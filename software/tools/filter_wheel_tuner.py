"""Filter-wheel (W axis) speed tuning, with the wheel's encoder as the lost-step detector.

Talks to the controller through control.microcontroller (the GUI's path). Needs firmware >= 1.6:
SET_ENCODER_REPORTING streams the wheel's ENC_POS and the loop error ENC_POS - XACTUAL (the wheel has
no position field in the status packet, so this is the only way to see where it is), SET_RAMP_PROFILE
selects trapezoid vs S-shape. Close the Squid GUI first: it holds the port.

Wheel model (host convention, squid/filter_wheel_controller/cephla.py): SCREW_PITCH_W_MM = 1 so one
"mm" is one motor revolution = one wheel revolution; slot k sits at offset + (k - 1) / slots revolutions
from the home flag. The wheel is rotary with no end stop, so nothing here is a travel hazard; a stall is
only noise and a re-home.

Lost steps: the encoder does not move with lost steps, the step counter does, so the change of the rest
deviation (XACTUAL - ENC_POS) across a level is the number of microsteps lost (or gained) in that level.

Usage (from software/, with the project venv):
  python tools/filter_wheel_tuner.py check                       # init, configure, encoder sign/scale, home
  python tools/filter_wheel_tuner.py pattern --ramp sshape        # the shipping profile, as a baseline
  python tools/filter_wheel_tuner.py accelsweep --accel-list 50 100 150 200 250 300 --vmax 3.19
  python tools/filter_wheel_tuner.py velsweep --vel-list 3.19 4 5 6 --accel 150
Options: --vmax rev/s  --accel rev/s^2  --ramp trapezoid|sshape  --microsteps 64  --flip auto|0|1
         --lost-usteps 32 (stop a sweep when a level drifts by more than this)  --out wheel_tune
"""

import argparse
import csv
import json
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import control._def as _def  # noqa: E402
from control._def import AXIS, ENCODER_REPORTING, RAMP_PROFILE  # noqa: E402
from control.microcontroller import Microcontroller, get_microcontroller_serial_device  # noqa: E402

FULLSTEPS = int(_def.FULLSTEPS_PER_REV_W)
MICROSTEPS = int(_def.MICROSTEPPING_DEFAULT_W)
USTEPS_PER_REV = MICROSTEPS * FULLSTEPS  # pitch is 1 "mm" per rev
SLOTS = int(_def.SQUID_FILTERWHEEL_MAX_INDEX)
MIN_INDEX = int(_def.SQUID_FILTERWHEEL_MIN_INDEX)
OFFSET_REV = float(_def.SQUID_FILTERWHEEL_OFFSET)
TRANSITIONS = int(_def.SQUID_FILTERWHEEL_TRANSITIONS_PER_REVOLUTION)
SIGN = int(_def.STAGE_MOVEMENT_SIGN_W)
FW_MIN = (1, 6)
DEFAULT_PATTERN = [2, 3, 4, 5, 6, 7, 8, 1, 5, 2, 8, 4, 7, 3, 6, 1]  # 7 adjacent, one 7-slot return, 8 jumps


def set_microsteps(n):
    global MICROSTEPS, USTEPS_PER_REV
    MICROSTEPS = int(n)
    USTEPS_PER_REV = MICROSTEPS * FULLSTEPS


def slot_usteps(slot):
    # same truncation as the host's _delta_to_usteps
    return int(SIGN * (OFFSET_REV + (slot - MIN_INDEX) / SLOTS) / (1.0 / USTEPS_PER_REV))


def model_move_s(d_rev, v, a, overhead_s=0.007):
    """Trapezoid move time for a distance d at vmax v, accel a, plus the fixed reporting overhead."""
    if d_rev <= v * v / a:
        return 2.0 * (d_rev / a) ** 0.5 + overhead_s
    return d_rev / v + v / a + overhead_s


class Sampler(threading.Thread):
    """Polls the packet fields at ~250 Hz: (t, ENC_POS, deviation). XACTUAL = ENC_POS - deviation."""

    def __init__(self, mcu):
        super().__init__(daemon=True)
        self.mcu = mcu
        self.rows = []
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self.t0 = time.time()

    def run(self):
        last = None
        while not self._stop_evt.is_set():
            st = self.mcu.get_encoder_state()
            row = (time.time(), st["encoder_pos"], st["deviation"])
            if last is None or row[1:] != last[1:] or row[0] - last[0] >= 0.05:
                with self._lock:
                    self.rows.append(row)
                last = row
            time.sleep(0.003)

    def stop(self):
        self._stop_evt.set()
        self.join(timeout=1.0)

    def since(self, t_wall):
        with self._lock:
            return [r for r in self.rows if r[0] >= t_wall]

    def clear(self):
        with self._lock:
            self.rows.clear()


class WheelTuner:
    def __init__(self, args):
        self.a = args
        self.out = args.out
        os.makedirs(self.out, exist_ok=True)
        self.mcu = None
        self.sampler = None
        self.flip = None
        self.transitions = TRANSITIONS if args.transitions == "auto" else int(args.transitions)
        self.slot = None  # last commanded slot
        self.last_cmd_to_ack_s = float("nan")
        self.summary = {
            "config": vars(args),
            "usteps_per_rev": USTEPS_PER_REV,
            "slots": SLOTS,
            "offset_rev": OFFSET_REV,
            "transitions_per_rev": TRANSITIONS,
            "results": [],
        }

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def wait(self, timeout=30.0):
        self.mcu.wait_till_operation_is_completed(timeout)

    # ---------------------------------------------------------------- setup
    def connect(self):
        dev = get_microcontroller_serial_device(version=_def.CONTROLLER_VERSION, sn=_def.CONTROLLER_SN)
        self.mcu = Microcontroller(dev, reset_and_initialize=True)
        time.sleep(0.5)
        fw = tuple(self.mcu.firmware_version)
        self.log(f"connected; controller reset and initialised; firmware {fw[0]}.{fw[1]}")
        if fw < FW_MIN:
            raise RuntimeError(f"firmware {fw[0]}.{fw[1]} lacks the encoder interface; need >= {FW_MIN[0]}.{FW_MIN[1]}")

    def setup(self, flip):
        m = self.mcu
        m.init_filter_wheel(AXIS.W)
        self.wait(10)
        time.sleep(0.3)
        m.configure_squidfilter(AXIS.W)  # pitch 1, ini microstepping/current, ini v/a
        if MICROSTEPS != int(_def.MICROSTEPPING_DEFAULT_W):
            m.configure_motor_driver(AXIS.W, MICROSTEPS, _def.W_MOTOR_RMS_CURRENT_mA, _def.W_MOTOR_I_HOLD)
            self.wait()
        self.set_motion(self.a.vmax, self.a.accel, self.a.ramp)
        if self.a.window_deg > 0:
            m.set_completion_window(AXIS.W, self.a.window_deg / 360.0)
            self.wait()
            self.log(
                f"completion window {self.a.window_deg:g} deg ({self.a.window_deg / 360 * USTEPS_PER_REV:.1f} usteps): "
                f"COMPLETED is sent while the last degrees are travelled"
            )
        m.configure_stage_pid(AXIS.W, self.transitions, bool(flip))
        self.wait()
        m.set_encoder_reporting(AXIS.W, ENCODER_REPORTING.ENC_IN_THETA)
        self.wait()
        self.flip = bool(flip)
        time.sleep(0.2)
        self.sampler = Sampler(m)
        self.sampler.start()
        self.log(
            f"W configured: {MICROSTEPS} usteps/FS ({USTEPS_PER_REV} usteps/rev), {_def.W_MOTOR_RMS_CURRENT_mA} mA, "
            f"encoder {self.transitions} transitions/rev (flip={self.flip}), reporting on"
        )

    def reconfigure_encoder(self, transitions, flip):
        self.mcu.configure_stage_pid(AXIS.W, int(transitions), bool(flip))
        self.wait()
        self.transitions, self.flip = int(transitions), bool(flip)
        self.summary["transitions_per_rev"] = self.transitions
        self.summary["flip"] = self.flip
        time.sleep(0.2)

    def set_motion(self, vmax, accel, ramp):
        m = self.mcu
        m.set_max_velocity_acceleration(AXIS.W, vmax, accel)
        self.wait()
        prof = RAMP_PROFILE.TRAPEZOID if ramp == "trapezoid" else RAMP_PROFILE.SSHAPE
        m.set_ramp_profile(AXIS.W, prof)
        self.wait()
        amax_cap = (2**22 - 1) / USTEPS_PER_REV
        note = (
            f"  (AMAX register ceiling {amax_cap:.0f} rev/s2 at {MICROSTEPS} usteps: CLAMPED)"
            if accel > amax_cap
            else ""
        )
        self.log(f"W motion: vmax {vmax} rev/s, accel {accel} rev/s2, ramp {ramp}{note}")

    # ---------------------------------------------------------------- primitives
    def state(self):
        # The TMC4361A's ENC_POS_DEV is ENC_POS - XACTUAL (verified on the bench: slot 2 commanded 1702,
        # ENC_POS 678, deviation -1024).
        st = self.mcu.get_encoder_state()
        return {"enc": st["encoder_pos"], "dev": st["deviation"], "xactual": st["encoder_pos"] - st["deviation"]}

    def check_abort(self):
        err = self.mcu.last_command_aborted_error
        if err is not None:
            self.mcu.acknowledge_aborted_command()
            raise RuntimeError(f"controller rejected the command: {err}")

    def move_to_usteps(self, target, timeout=30.0):
        t0 = time.time()
        self.mcu.move_w_to_usteps(int(target))
        while self.mcu.is_busy():
            if time.time() - t0 > timeout:
                raise TimeoutError("W move did not complete")
            time.sleep(0.002)
        self.check_abort()
        self.last_cmd_to_ack_s = time.time() - t0
        self.state_at_ack = self.state()  # where the wheel was when COMPLETED arrived
        self.state_at_ack["target"] = int(target)

    def move_to_slot(self, slot):
        self.move_to_usteps(slot_usteps(slot))
        self.slot = slot

    def home(self):
        self.log("homing W (to the index flag)")
        t0 = time.time()
        self.mcu.home_w()
        self.wait(40)
        self.check_abort()
        dt = time.time() - t0
        time.sleep(0.3)
        st = self.state()
        self.log(f"homed in {dt:.2f} s; ENC_POS {st['enc']}, XACTUAL {st['xactual']} (both should be 0)")
        self.slot = None
        return dt

    # ---------------------------------------------------------------- phases
    def check(self):
        """Encoder sign and scale from a known open-loop move (home -> slot 2, 0.133 rev)."""
        self.home()
        self.move_to_slot(2)
        time.sleep(0.4)
        st = self.state()
        target = slot_usteps(2)
        ratio = st["enc"] / target if target else float("nan")
        self.log(
            f"slot 2: commanded {target} usteps, XACTUAL {st['xactual']}, ENC_POS {st['enc']} -> ratio {ratio:+.4f}; "
            f"cmd->ack {self.last_cmd_to_ack_s * 1000:.0f} ms"
        )
        res = {
            "phase": "check",
            "target_usteps": target,
            "enc_pos": st["enc"],
            "xactual": st["xactual"],
            "ratio": ratio,
            "flip": self.flip,
            "cmd_to_ack_s": self.last_cmd_to_ack_s,
        }
        self.summary["results"].append(res)
        return ratio

    def measure_scale(self):
        """Encoder transitions per revolution from one full turn: slot 1 -> slot 1 + 1 rev (12800 usteps),
        driven gently (1 rev/s, 50 rev/s2, S-shape) so lost steps cannot masquerade as encoder scale."""
        self.set_motion(1.0, 50, "sshape")
        self.move_to_slot(1)
        time.sleep(0.4)
        st0 = self.state()
        self.move_to_usteps(slot_usteps(1) + USTEPS_PER_REV)
        time.sleep(0.5)
        st1 = self.state()
        self.set_motion(self.a.vmax, self.a.accel, self.a.ramp)
        d_x = st1["xactual"] - st0["xactual"]
        d_e = st1["enc"] - st0["enc"]
        ratio = d_e / d_x if d_x else float("nan")
        est = self.transitions * ratio
        self.log(
            f"one revolution: XACTUAL +{d_x}, ENC_POS {d_e:+d} usteps -> ratio {ratio:+.4f}; "
            f"encoder = {est:.1f} transitions/rev (configured {self.transitions})"
        )
        self.summary["results"].append(
            {
                "phase": "scale",
                "d_xactual": d_x,
                "d_enc": d_e,
                "ratio": ratio,
                "transitions_configured": self.transitions,
                "transitions_measured": est,
            }
        )
        self.move_to_usteps(slot_usteps(1))
        time.sleep(0.3)
        return ratio, est

    def ensure_encoder(self):
        """Verify sign and scale of the encoder against the step counter; fix the flip and the transitions/rev
        when allowed (--flip auto, --transitions auto), re-home and re-verify."""
        ratio = self.check()
        if ratio < 0:
            if self.a.flip != "auto":
                raise RuntimeError("encoder is inverted with the requested flip; use --flip auto or the other value")
            self.log("encoder inverted: re-configuring with the opposite flip")
            self.reconfigure_encoder(self.transitions, not self.flip)
            ratio = self.check()
            if ratio < 0:
                raise RuntimeError(f"encoder still inverted after flipping (ratio {ratio:+.3f}); check wiring")
        if not 0.98 <= ratio <= 1.02:
            _, est = self.measure_scale()
            if self.a.transitions != "auto":
                raise RuntimeError(f"encoder scale is {est:.0f} transitions/rev, not the requested {self.transitions}")
            new = int(round(est))
            self.log(f"re-configuring the encoder with {new} transitions/rev and re-homing")
            self.reconfigure_encoder(new, self.flip)
            ratio = self.check()
            if not 0.98 <= ratio <= 1.02:
                raise RuntimeError(f"encoder still off scale after reconfiguring (ratio {ratio:+.4f})")
        self.log(
            f"encoder OK: ratio {ratio:+.4f}, {self.transitions} transitions/rev, flip={self.flip} "
            f"(host should use SQUID_FILTERWHEEL_TRANSITIONS_PER_REVOLUTION = {self.transitions}, ENCODER_FLIP_DIR_W = {self.flip})"
        )

    def pattern(self, label, slots=None, settle_s=0.12):
        """Run the move pattern once at the current motion settings. Per move: slot distance, cmd->ack,
        deviation at rest afterwards. Returns the level summary (also appended to the results)."""
        slots = slots or self.a.pattern
        if self.slot is None:
            self.move_to_slot(1)
            time.sleep(0.3)
        time.sleep(0.3)
        st0 = self.state()
        wall0 = time.time()
        rows = []
        for target in slots:
            frm = self.slot
            dist = abs(target - frm)
            dev_before = self.state()["dev"]
            t_move = time.time()
            self.move_to_usteps(slot_usteps(target))
            self.slot = target
            time.sleep(settle_s)
            st = self.state()
            # peak encoder speed during the move, from the sampler trace (rev/s)
            tr = self.sampler.since(t_move) if self.sampler else []
            vpk = 0.0
            for (ta, ea, _), (tb, eb, _) in zip(tr, tr[1:]):
                if tb - ta >= 0.008:
                    vpk = max(vpk, abs(eb - ea) / (tb - ta) / USTEPS_PER_REV)
            ack = self.state_at_ack
            rows.append(
                {
                    "from": frm,
                    "to": target,
                    "slots": dist,
                    "cmd_to_ack_ms": self.last_cmd_to_ack_s * 1000,
                    "dev_usteps": st["dev"],
                    "lost_this_move": st["dev"] - dev_before,
                    "enc_peak_rev_s": vpk,
                    "enc": st["enc"],
                    "xactual": st["xactual"],
                    "t": time.time() - wall0,
                    "enc_err_at_ack_deg": (ack["enc"] - ack["target"]) / USTEPS_PER_REV * 360.0,
                    "xactual_err_at_ack_deg": (ack["xactual"] - ack["target"]) / USTEPS_PER_REV * 360.0,
                }
            )
        time.sleep(0.3)
        st1 = self.state()
        drift = st1["dev"] - st0["dev"]
        if self.sampler:
            tpath = os.path.join(self.out, f"trace_{label.replace(' ', '_').replace('/', '-')}.csv")
            with open(tpath, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t_s", "enc_usteps", "dev_usteps", "xactual_usteps"])
                for t, e, d in self.sampler.since(wall0 - 0.3):
                    w.writerow([f"{t - wall0:.4f}", e, d, e - d])
        by_dist = {}
        for r in rows:
            by_dist.setdefault(r["slots"], []).append(r["cmd_to_ack_ms"])
        adj = by_dist.get(1, [])
        res = {
            "phase": "level",
            "label": label,
            "vmax": self.a.vmax,
            "accel": self.a.accel,
            "ramp": self.a.ramp,
            "microsteps": MICROSTEPS,
            "n_moves": len(rows),
            "dev_start": st0["dev"],
            "dev_end": st1["dev"],
            "drift_usteps": drift,
            "max_abs_dev_change": max(abs(r["dev_usteps"] - st0["dev"]) for r in rows),
            "adjacent_ms_median": statistics.median(adj) if adj else float("nan"),
            "adjacent_ms_max": max(adj) if adj else float("nan"),
            "by_distance_ms_median": {str(k): statistics.median(v) for k, v in sorted(by_dist.items())},
            "wall_start": wall0,
            "wall_end": time.time(),
        }
        path = os.path.join(self.out, f"moves_{label.replace(' ', '_').replace('/', '-')}.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        res["csv"] = path
        self.summary["results"].append(res)
        model = (
            model_move_s(1.0 / SLOTS, self.a.vmax, self.a.accel) * 1000 if self.a.ramp == "trapezoid" else float("nan")
        )
        dist_txt = ", ".join(
            f"{k} slot{'s' if int(k) > 1 else ''} {v:.0f} ms" for k, v in res["by_distance_ms_median"].items()
        )
        worst = max(rows, key=lambda r: abs(r["lost_this_move"]))
        res["worst_move"] = {k: worst[k] for k in ("from", "to", "slots", "lost_this_move", "enc_peak_rev_s")}
        res["enc_peak_rev_s_max"] = max(r["enc_peak_rev_s"] for r in rows)
        res["enc_err_at_ack_deg_median_abs"] = statistics.median(abs(r["enc_err_at_ack_deg"]) for r in rows)
        res["enc_err_at_ack_deg_max_abs"] = max(abs(r["enc_err_at_ack_deg"]) for r in rows)
        self.log(
            f"{label}: {len(rows)} moves; adjacent cmd->ack median {res['adjacent_ms_median']:.0f} ms (max {res['adjacent_ms_max']:.0f}"
            f"{f', trapezoid model {model:.0f}' if model == model else ''}); {dist_txt}; "
            f"encoder drift {drift:+d} usteps ({drift / USTEPS_PER_REV * 360:+.2f} deg) over the level; "
            f"worst single move {worst['from']}->{worst['to']} lost {worst['lost_this_move']:+d} usteps; "
            f"peak encoder speed {res['enc_peak_rev_s_max']:.2f} rev/s; encoder still {res['enc_err_at_ack_deg_median_abs']:.2f} deg "
            f"(max {res['enc_err_at_ack_deg_max_abs']:.2f}) from the slot when COMPLETED arrived"
        )
        return res

    def wrap(self):
        """Cross the index flag in both directions. Forward: slot 1 -> slot 1 + one turn. Backward: slot 1 ->
        slot 8 the short way (one slot back, across the flag) and back, --wrap-n times, then a full backward
        turn. On firmware that still has the flag armed as a hard stop the backward crossing never completes:
        the move times out after --wrap-timeout s and the tool reports it (and resets the controller)."""
        self.move_to_slot(1)
        time.sleep(0.3)
        st0 = self.state()
        wall0 = time.time()
        results = []

        def attempt(label, target):
            t0 = time.time()
            try:
                self.move_to_usteps(target, timeout=self.a.wrap_timeout)
                time.sleep(0.15)
                st = self.state()
                r = {
                    "label": label,
                    "target": target,
                    "ok": True,
                    "cmd_to_ack_ms": self.last_cmd_to_ack_s * 1000,
                    "xactual": st["xactual"],
                    "enc": st["enc"],
                    "dev": st["dev"],
                }
                self.log(
                    f"{label}: OK, cmd->ack {r['cmd_to_ack_ms']:.0f} ms, XACTUAL {st['xactual']}, ENC {st['enc']}, dev {st['dev']}"
                )
            except TimeoutError:
                st = self.state()
                r = {
                    "label": label,
                    "target": target,
                    "ok": False,
                    "after_s": time.time() - t0,
                    "xactual": st["xactual"],
                    "enc": st["enc"],
                    "dev": st["dev"],
                }
                self.log(
                    f"{label}: NOT COMPLETED after {r['after_s']:.1f} s (XACTUAL {st['xactual']}, target {target}): "
                    f"the flag stopped the wheel. Resetting the controller."
                )
                self.mcu.reset()
                time.sleep(0.5)
                self.mcu.initialize_drivers()
                time.sleep(0.5)
                raise
            results.append(r)
            return r

        try:
            attempt("forward across the flag (+1 turn)", slot_usteps(1) + USTEPS_PER_REV)
            attempt("back to slot 1 (-1 turn)", slot_usteps(1))
            for k in range(self.a.wrap_n):
                attempt(f"slot 1 -> 8 short way (across the flag) #{k + 1}", slot_usteps(1) - USTEPS_PER_REV // SLOTS)
                attempt(f"slot 8 -> 1 short way #{k + 1}", slot_usteps(1))
            attempt("backward full turn across the flag", slot_usteps(1) - USTEPS_PER_REV)
            attempt("back to slot 1", slot_usteps(1))
        finally:
            st1 = self.state()
            res = {
                "phase": "wrap",
                "label": "wrap",
                "results": results,
                "dev_start": st0["dev"],
                "dev_end": st1["dev"],
                "drift_usteps": st1["dev"] - st0["dev"],
                "wall_start": wall0,
                "wall_end": time.time(),
            }
            self.summary["results"].append(res)
            self.log(
                f"wrap: {sum(1 for r in results if r['ok'])}/{len(results)} crossings completed; encoder drift {res['drift_usteps']:+d} usteps"
            )

    def sweep(self, kind, values):
        for v in values:
            if kind == "accel":
                self.a.accel = v
            else:
                self.a.vmax = v
            self.set_motion(self.a.vmax, self.a.accel, self.a.ramp)
            label = f"{kind} {v:g}"
            try:
                res = self.pattern(label)
            except Exception as e:  # noqa: BLE001
                self.log(f"{label}: aborted: {e}")
                self.summary["results"].append({"phase": "level", "label": label, "error": str(e)})
                break
            if abs(res["drift_usteps"]) > self.a.lost_usteps:
                self.log(
                    f"STOP: {res['drift_usteps']:+d} usteps of position lost at {label} (limit {self.a.lost_usteps})"
                )
                break
        good = [
            r
            for r in self.summary["results"]
            if r.get("phase") == "level"
            and "error" not in r
            and abs(r["drift_usteps"]) <= self.a.lost_usteps
            and r["label"].startswith(kind)
        ]
        if good:
            best = good[-1]
            self.log(
                f"highest {kind} with no lost steps: {best['label']} (adjacent slot {best['adjacent_ms_median']:.0f} ms)"
            )

    # ---------------------------------------------------------------- main
    def run(self):
        try:
            self.connect()
            flip = {"auto": bool(_def.ENCODER_FLIP_DIR_W), "0": False, "1": True}[self.a.flip]
            # sweeps start at their gentlest level, so the encoder check itself cannot stall the wheel
            if self.a.action == "accelsweep":
                self.a.accel = self.a.accel_list[0]
            elif self.a.action == "velsweep":
                self.a.vmax = self.a.vel_list[0]
            self.setup(flip)
            self.ensure_encoder()
            if self.a.action == "check":
                self.move_to_slot(1)
                return
            if self.a.action == "pattern":
                self.pattern(f"{self.a.ramp} v{self.a.vmax:g} a{self.a.accel:g}")
                return
            if self.a.action == "accelsweep":
                self.sweep("accel", self.a.accel_list)
                return
            if self.a.action == "velsweep":
                self.sweep("vmax", self.a.vel_list)
                return
            if self.a.action == "wrap":
                self.wrap()
                return
        finally:
            self.shutdown()

    def shutdown(self):
        if self.mcu is None:
            return
        steps = [
            ("reporting off", lambda: (self.mcu.set_encoder_reporting(AXIS.W, ENCODER_REPORTING.OFF), self.wait(5))),
            (
                "ini velocity restored",
                lambda: (
                    self.mcu.set_max_velocity_acceleration(AXIS.W, _def.MAX_VELOCITY_W_mm, _def.MAX_ACCELERATION_W_mm),
                    self.wait(5),
                ),
            ),
            (
                "ramp restored to S-shape",
                lambda: (self.mcu.set_ramp_profile(AXIS.W, RAMP_PROFILE.SSHAPE), self.wait(5)),
            ),
        ]
        if not self.a.leave_enabled:
            steps.append(("W driver disabled", lambda: (self.mcu.set_axis_enable_disable(AXIS.W, 0), self.wait(5))))
        steps.append(("sampler stopped", lambda: self.sampler.stop() if self.sampler else None))
        done = []
        for name, fn in steps:
            try:
                fn()
                done.append(name)
            except Exception as e:  # noqa: BLE001
                self.log(f"shutdown step '{name}' failed: {e}")
        try:
            with open(os.path.join(self.out, "summary.json"), "w") as f:
                json.dump(self.summary, f, indent=2, default=str)
            self.log(f"summary written to {os.path.join(self.out, 'summary.json')}; {', '.join(done)}")
        finally:
            self.mcu.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "pattern", "accelsweep", "velsweep", "wrap"])
    ap.add_argument("--wrap-n", type=int, default=5, help="wrap: number of 1<->8 short-way round trips across the flag")
    ap.add_argument(
        "--wrap-timeout", type=float, default=5.0, help="wrap: seconds before a crossing is declared stopped"
    )
    ap.add_argument("--vmax", type=float, default=float(_def.MAX_VELOCITY_W_mm), help="rev/s")
    ap.add_argument("--accel", type=float, default=float(_def.MAX_ACCELERATION_W_mm), help="rev/s^2")
    ap.add_argument("--ramp", choices=["trapezoid", "sshape"], default="trapezoid")
    ap.add_argument("--microsteps", type=int, default=int(_def.MICROSTEPPING_DEFAULT_W))
    ap.add_argument("--flip", choices=["auto", "0", "1"], default="auto")
    ap.add_argument(
        "--window-deg",
        type=float,
        default=0.0,
        help="SET_COMPLETION_WINDOW for W in degrees (0 = exact-target completion)",
    )
    ap.add_argument(
        "--transitions",
        default="auto",
        help="encoder transitions per revolution, or auto = measure over one turn and use it",
    )
    ap.add_argument("--accel-list", type=float, nargs="+", default=[50, 100, 150, 200, 250, 300])
    ap.add_argument("--vel-list", type=float, nargs="+", default=[3.19, 4, 5, 6])
    ap.add_argument("--pattern", type=int, nargs="+", default=DEFAULT_PATTERN)
    ap.add_argument(
        "--lost-usteps",
        type=int,
        default=32,
        help="level drift that counts as lost steps (64 usteps = 1 full step at 64 usteps/FS)",
    )
    ap.add_argument("--leave-enabled", action="store_true", help="leave the W driver energised at exit")
    ap.add_argument("--out", default="wheel_tune")
    a = ap.parse_args()
    set_microsteps(a.microsteps)
    for s in a.pattern:
        if not (MIN_INDEX <= s <= MIN_INDEX + SLOTS - 1):
            ap.error(f"slot {s} out of range")
    WheelTuner(a).run()


if __name__ == "__main__":
    main()
