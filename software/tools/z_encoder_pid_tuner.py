"""Z-axis encoder check and closed-loop (TMC4361A PID) tuning tool.

Talks to the controller through control.microcontroller (the same path the GUI uses), so
whatever this tool establishes is what the software will see. Requires firmware >= 1.6
(SET_ENCODER_REPORTING / SET_PID_LIMITS). Close the Squid GUI first: it holds the port.

Geometry: home (XACTUAL = 0) is the actuator fully retracted with the stage resting on its stop; positive
mm in the software is the actuator extending and the stage moving up. Near home the stage can rest on its
stop while the actuator keeps retracting, so the encoder stops following: firmware >= 1.6 holds the loop
open inside a configurable home zone (--zone-um) and `zonemap` measures where that happens.

Safety model - Z must never be driven into a travel end:
  * All motion stays inside a window of extension from home, default 1.0 .. 4.5 mm,
    hard-capped at 5.5 mm; every target is checked before it is sent and every sample after.
  * Z velocity is lowered to --vmax (default 1.0 mm/s) for the whole session and restored at exit.
  * Before the loop is ever closed, the encoder is read open-loop across a known move and must
    track the step counter in sign and scale; otherwise the tool stops and tells you what it saw
    (typically: flip the encoder direction).
  * The closed-loop correction velocity is clamped (SET_PID_LIMITS) and the firmware watchdog
    disables the loop if the error exceeds --max-dev-um. The tool also watches the error and the
    position from the host side and turns the loop off on the first anomaly.
  * The loop is turned off, reporting disabled and velocities restored in a finally block.

Usage (from software/, with the project venv):
  python tools/z_encoder_pid_tuner.py check                  # connect, home, encoder sign/scale
  python tools/z_encoder_pid_tuner.py baseline               # open-loop following error and rest noise
  python tools/z_encoder_pid_tuner.py step --p 4096 --i 0 --d 1
  python tools/z_encoder_pid_tuner.py sweep --p-list 1024 2048 4096 8192 16384 --d 1
  python tools/z_encoder_pid_tuner.py zonemap                # open-loop ENC_POS vs XACTUAL from 2 mm down to home and back
Common options: --depth-mm 2.5 (extension from home) --step-um 100 --vmax 1.0 --corr-vmax 0.3 --max-dev-um 200
                --zone-um 0 (home exclusion zone sent to firmware) --out z_tune
"""
import argparse
import csv
import json
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import control._def as _def  # noqa: E402  (loads the machine configuration)
from control._def import AXIS, ENCODER_REPORTING, RAMP_PROFILE  # noqa: E402
from control.microcontroller import Microcontroller, get_microcontroller_serial_device  # noqa: E402

FULLSTEPS_PER_REV = 200
# --microsteps sets these before anything runs (the tool configures the driver explicitly, so usteps/mm is
# known regardless of GUI history). Defaults to the ini's MICROSTEPPING_DEFAULT_Z, which is what the GUI uses.
MICROSTEPS = int(_def.MICROSTEPPING_DEFAULT_Z)
PITCH_MM = _def.SCREW_PITCH_Z_MM
ENC_STEP_MM = _def.ENCODER_STEP_SIZE_Z_MM
USTEPS_PER_MM = MICROSTEPS * FULLSTEPS_PER_REV / PITCH_MM


def set_microsteps(n):
    global MICROSTEPS, USTEPS_PER_MM
    MICROSTEPS = int(n)
    USTEPS_PER_MM = MICROSTEPS * FULLSTEPS_PER_REV / PITCH_MM
TRANSITIONS_PER_REV = int(round(PITCH_MM / ENC_STEP_MM))
SIGN = _def.STAGE_MOVEMENT_SIGN_Z           # -1 on the Squid+: "down" (positive depth) is negative usteps
HARD_CAP_DEPTH_MM = 5.5                     # never command below this, whatever the arguments say
FW_MIN = (1, 6)


def depth_to_usteps(depth_mm):
    return int(round(SIGN * depth_mm * USTEPS_PER_MM))


def usteps_to_depth(usteps):
    return SIGN * usteps / USTEPS_PER_MM


class Sampler(threading.Thread):
    """Polls the Microcontroller's packet fields at ~200 Hz and records changes with timestamps."""

    def __init__(self, mcu):
        super().__init__(daemon=True)
        self.mcu = mcu
        self.rows = []
        self._stop_evt = threading.Event()  # not "_stop": Thread uses that name internally
        self._lock = threading.Lock()

    def now(self):
        return time.time() - self.t0

    def run(self):
        last = None
        t0 = self.t0 = time.time()
        while not self._stop_evt.is_set():
            st = self.mcu.get_encoder_state()
            row = (time.time() - t0, self.mcu.z_pos, st["encoder_pos"], st["deviation"], self.mcu.encoder_flags)
            # record on change, and at least every 50 ms so rest periods are represented
            if last is None or row[1:] != last[1:] or row[0] - last[0] >= 0.05:
                with self._lock:
                    self.rows.append(row)
                last = row
            time.sleep(0.004)

    def stop(self):
        self._stop_evt.set()
        self.join(timeout=1.0)

    def snapshot(self):
        with self._lock:
            return list(self.rows)

    def clear(self):
        with self._lock:
            self.rows.clear()


class ZTuner:
    def __init__(self, args):
        self.a = args
        self.out = args.out
        os.makedirs(self.out, exist_ok=True)
        self.mcu = None
        self.sampler = None
        self.flip = bool(_def.ENCODER_FLIP_DIR_Z)
        self.loop_on = False
        self.last_cmd_to_ack_s = float("nan")
        self.cmd_to_ack_log = []
        self.summary = {"config": vars(args), "pitch_mm": PITCH_MM, "usteps_per_mm": USTEPS_PER_MM,
                        "transitions_per_rev": TRANSITIONS_PER_REV, "results": []}

    # ---------------------------------------------------------------- plumbing
    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def wait(self, timeout=30.0):
        self.mcu.wait_till_operation_is_completed(timeout)

    def connect(self):
        dev = get_microcontroller_serial_device(version=_def.CONTROLLER_VERSION, sn=_def.CONTROLLER_SN)
        self.mcu = Microcontroller(dev, reset_and_initialize=True)
        time.sleep(0.5)
        fw = tuple(self.mcu.firmware_version)
        self.log(f"connected; controller reset and initialised; firmware {fw[0]}.{fw[1]}")
        if fw < FW_MIN:
            raise RuntimeError(f"firmware {fw[0]}.{fw[1]} lacks the encoder interface; need >= {FW_MIN[0]}.{FW_MIN[1]}")
        self.sampler = Sampler(self.mcu)
        self.sampler.start()

    def configure_z(self):
        m = self.mcu
        m.configure_motor_driver(AXIS.Z, MICROSTEPS, _def.Z_MOTOR_RMS_CURRENT_mA, _def.Z_MOTOR_I_HOLD); self.wait()
        m.set_leadscrew_pitch(AXIS.Z, PITCH_MM); self.wait()
        m.set_max_velocity_acceleration(AXIS.Z, self.a.vmax, self.a.accel); self.wait()
        prof = RAMP_PROFILE.TRAPEZOID if self.a.ramp == "trapezoid" else RAMP_PROFILE.SSHAPE
        m.set_ramp_profile(AXIS.Z, prof); self.wait()
        self.log(f"Z configured: {MICROSTEPS} usteps/FS, pitch {PITCH_MM} mm, {_def.Z_MOTOR_RMS_CURRENT_mA} mA, "
                 f"vmax {self.a.vmax} mm/s, accel {self.a.accel} mm/s2, ramp {self.a.ramp} ({USTEPS_PER_MM:.0f} usteps/mm)")

    def restore_velocity(self):
        try:
            self.mcu.set_max_velocity_acceleration(AXIS.Z, _def.MAX_VELOCITY_Z_mm, _def.MAX_ACCELERATION_Z_mm)
            self.wait(5)
        except Exception as e:  # noqa: BLE001
            self.log(f"could not restore Z velocity: {e}")

    def loop_off(self):
        if self.mcu is None:
            return
        try:
            self.mcu.turn_off_stage_pid(AXIS.Z)
            self.wait(5)
        except Exception as e:  # noqa: BLE001
            self.log(f"turn_off_stage_pid failed: {e}")
        self.loop_on = False

    def shutdown(self):
        """Every step is independent: a failure in one must not skip the ones that make the board safe."""
        if self.mcu is None:
            return
        steps = [
            ("loop off", self.loop_off),
            ("reporting off", lambda: (self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.OFF), self.wait(5))),
            ("velocity restored", self.restore_velocity),
            ("sampler stopped", lambda: self.sampler.stop() if self.sampler else None),
        ]
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

    # ---------------------------------------------------------------- safety
    def check_depth(self, depth_mm):
        lo, hi = self.a.depth_min, min(self.a.depth_max, HARD_CAP_DEPTH_MM)
        if not (lo <= depth_mm <= hi):
            raise RuntimeError(f"refusing Z target {depth_mm:.3f} mm extension: outside the allowed window {lo}..{hi} mm")

    def current_depth(self):
        return usteps_to_depth(self.mcu.z_pos)

    def guard(self):
        """Called during waits: aborts (loop off) on fault flag, excess error, or leaving the window."""
        st = self.mcu.get_encoder_state()
        d = self.current_depth()
        if st["pid_fault"]:
            self.loop_off()
            raise RuntimeError("firmware watchdog disabled the loop (PID_FAULT) - deviation exceeded the limit")
        if self.loop_on and abs(st["deviation"]) > self.a.max_dev_um * USTEPS_PER_MM / 1000.0:
            self.loop_off()
            raise RuntimeError(f"host guard: loop error {st['deviation']} usteps exceeded {self.a.max_dev_um} um")
        # Anything between the top switch (depth 0, where homing leaves us) and a little past the
        # working window is legitimate transit; only going deeper than the window, or above home,
        # is an anomaly. The bottom of travel is the stall the operator called non-recoverable.
        if d < -0.1 or d > min(self.a.depth_max, HARD_CAP_DEPTH_MM) + 0.5:
            self.loop_off()
            raise RuntimeError(f"host guard: Z at {d:.3f} mm left the allowed range")

    def move_to_depth(self, depth_mm, timeout=30.0):
        self.check_depth(depth_mm)
        t0 = time.time()
        self.mcu.move_z_to_usteps(depth_to_usteps(depth_mm))
        while self.mcu.is_busy():
            self.guard()
            if time.time() - t0 > timeout:
                self.loop_off()
                raise TimeoutError("Z move did not complete")
            time.sleep(0.002)
        # host-visible latency: command sent -> COMPLETED ack seen (includes the 10 ms packet cadence)
        self.last_cmd_to_ack_s = time.time() - t0
        self.cmd_to_ack_log.append(self.last_cmd_to_ack_s)

    def settle(self, seconds):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.guard()
            time.sleep(0.01)

    # ---------------------------------------------------------------- phases
    def home(self):
        self.log("homing Z (toward the top switch)")
        self.mcu.home_z()
        self.wait(60)
        self.log(f"Z homed; moving to {self.a.depth_mm} mm extension from home")
        self.move_to_depth(self.a.depth_mm)

    def configure_encoder(self, flip):
        m = self.mcu
        m.set_pid_limits(AXIS.Z, self.a.corr_vmax, self.a.max_dev_um); self.wait()
        m.set_pid_home_zone(AXIS.Z, self.a.zone_um); self.wait()
        if self.a.tol_um > 0:
            m.set_pid_tolerance(AXIS.Z, self.a.tol_um, self.a.tol_um); self.wait()
        m.configure_stage_pid(AXIS.Z, TRANSITIONS_PER_REV, flip_direction=flip); self.wait()
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d); self.wait()
        m.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.ENC_IN_THETA); self.wait()
        time.sleep(0.2)
        st = m.get_encoder_state()
        if not st["reporting"]:
            raise RuntimeError("firmware did not start encoder reporting - is it >= 1.6? (SET_ENCODER_REPORTING ignored)")
        self.log(f"encoder configured: {TRANSITIONS_PER_REV} transitions/rev, flip={flip}, "
                 f"correction vmax {self.a.corr_vmax} mm/s, watchdog {self.a.max_dev_um} um, home zone {self.a.zone_um} um")

    def encoder_check(self):
        """Open loop: encoder must follow XACTUAL with ratio +1 (in usteps). Fix the sign if it reads -1.

        The encoder is configured (scale, direction, limits, reporting) BEFORE homing in run(), so the
        zero written at homing is taken under the final scale; firmware >= 1.6 also re-aligns ENC_POS
        to XACTUAL inside CONFIGURE_STAGE_PID, so a re-configuration here (sign retry) stays aligned.
        """
        for attempt in range(2):
            if attempt > 0:
                self.configure_encoder(self.flip)
            self.settle(0.5)
            z0, e0 = self.mcu.z_pos, self.mcu.get_encoder_state()["encoder_pos"]
            self.move_to_depth(self.a.depth_mm + 0.5)
            self.settle(0.5)
            z1, e1 = self.mcu.z_pos, self.mcu.get_encoder_state()["encoder_pos"]
            self.move_to_depth(self.a.depth_mm)
            self.settle(0.5)
            dz, de = z1 - z0, e1 - e0
            ratio = de / dz if dz else float("nan")
            self.log(f"encoder check: XACTUAL moved {dz} usteps, ENC_POS moved {de} usteps, ratio {ratio:+.4f}")
            self.summary["results"].append({"phase": "encoder_check", "attempt": attempt, "flip": self.flip,
                                            "dz": dz, "de": de, "ratio": ratio})
            if abs(abs(ratio) - 1.0) > 0.05:
                implied = ENC_STEP_MM * abs(ratio)
                raise RuntimeError(f"encoder scale off by {abs(ratio) - 1:+.1%}: transitions/rev {TRANSITIONS_PER_REV} "
                                   f"implies an encoder step of {implied * 1000:.4f} um instead of {ENC_STEP_MM * 1000:.4f}. "
                                   "Fix ENCODER_STEP_SIZE_Z_MM / SCREW_PITCH_Z_MM before closing the loop.")
            if ratio > 0:
                self.log("encoder sign OK")
                # The loop nulls XACTUAL - ENC_POS in absolute terms, so the two frames must agree
                # before it is ever closed. Firmware >= 1.6 zeroes ENC_POS with XACTUAL at homing/zero.
                self.settle(0.3)
                dev = self.mcu.get_encoder_state()["deviation"]
                dev_um = dev / USTEPS_PER_MM * 1000
                self.log(f"encoder frame offset after homing: {dev_um:+.1f} um")
                if abs(dev) >= 32767 or abs(dev_um) > self.a.max_dev_um / 4:
                    raise RuntimeError(f"encoder frame is offset from XACTUAL by {dev_um:+.1f} um (clipped at 192 um); "
                                       "the loop would slew by that amount on enable. Refusing to continue.")
                return
            if attempt == 0:
                self.flip = not self.flip
                self.log(f"encoder runs backwards: reconfiguring with flip={self.flip}")
        raise RuntimeError("encoder sign still wrong after flipping - not closing the loop")

    def record(self, label, depth_from, depth_to, dwell=1.0):
        """One excursion: dwell, move, dwell, move back, dwell. Returns metrics from the sampler."""
        wall_start = time.time()
        self.sampler.clear()
        self.settle(dwell)
        t_move = self.sampler.now()
        self.move_to_depth(depth_to)
        ack_out = self.last_cmd_to_ack_s
        self.settle(dwell)
        self.move_to_depth(depth_from)
        ack_back = self.last_cmd_to_ack_s
        self.settle(dwell)
        rows = self.sampler.snapshot()
        path = os.path.join(self.out, f"{label}.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "xactual_usteps", "enc_pos_usteps", "deviation_usteps", "flags"])
            w.writerows(rows)
        devs = [r[3] for r in rows]
        rest = [r[3] for r in rows if r[0] < t_move]
        peak = max((abs(d) for d in devs), default=0)
        rest_mean = (sum(rest) / len(rest)) if rest else float("nan")
        rest_rms = math.sqrt(sum((d - rest_mean) ** 2 for d in rest) / len(rest)) if rest else float("nan")
        if peak >= 32767:
            self.log("WARNING: loop error is pinned at the int16 clip (>=192 um at 256 usteps/FS): the encoder frame is "
                     "offset from XACTUAL. Firmware must zero ENC_POS at homing; do not close the loop in this state.")
        # settling time: from the end of the last commanded move (last change of XACTUAL) until |dev|
        # stays within tol for 0.2 s
        tol = self.a.settle_tol_um * USTEPS_PER_MM / 1000.0
        settle_s = float("nan")
        if rows:
            t_last_move = max((rows[k][0] for k in range(1, len(rows)) if rows[k][1] != rows[k - 1][1]), default=rows[0][0])
            inside_since = None
            for t, _, _, d, _ in rows:
                if t < t_last_move:
                    continue
                if abs(d) <= tol:
                    inside_since = t if inside_since is None else inside_since
                    if t - inside_since >= 0.2:
                        settle_s = inside_since - t_last_move
                        break
                else:
                    inside_since = None
        # oscillation: sign changes per second in the deviation while at rest at the end
        tail = [r[3] for r in rows if r[0] > rows[-1][0] - dwell] if rows else []
        crossings = sum(1 for a, b in zip(tail, tail[1:]) if (a < 0) != (b < 0))
        metrics = {"label": label, "samples": len(rows), "peak_dev_um": peak / USTEPS_PER_MM * 1000,
                   "rest_mean_um": rest_mean / USTEPS_PER_MM * 1000,
                   "rest_rms_um": rest_rms / USTEPS_PER_MM * 1000, "tail_zero_crossings_per_s": crossings / dwell,
                   "final_dev_um": (rows[-1][3] / USTEPS_PER_MM * 1000) if rows else float("nan"),
                   "settle_after_last_move_s": settle_s,
                   "cmd_to_ack_out_s": ack_out, "cmd_to_ack_back_s": ack_back,
                   "wall_start": wall_start, "wall_end": time.time(),
                   "csv": path}
        self.log(f"{label}: cmd->ack {ack_out * 1000:.0f} / {ack_back * 1000:.0f} ms; peak |dev| {metrics['peak_dev_um']:.1f} um, "
                 f"rest rms {metrics['rest_rms_um']:.2f} um, final {metrics['final_dev_um']:+.2f} um, "
                 f"encoder settled {settle_s * 1000:.0f} ms after the ramp ended, tail crossings {metrics['tail_zero_crossings_per_s']:.1f}/s")
        return metrics

    def baseline(self):
        step = self.a.step_um / 1000.0
        m = self.record("baseline_openloop", self.a.depth_mm, self.a.depth_mm + step)
        m["phase"] = "baseline"
        self.summary["results"].append(m)

    def closed_loop_step(self, p, i, d):
        m = self.mcu
        self.settle(0.3)
        dev0 = m.get_encoder_state()["deviation"] / USTEPS_PER_MM * 1000
        if abs(dev0) > self.a.max_dev_um / 4:
            raise RuntimeError(f"not closing the loop: error already {dev0:+.1f} um before enable")
        m.set_pid_arguments(AXIS.Z, p, i, d); self.wait()
        m.turn_on_stage_pid(AXIS.Z)
        self.wait(5)
        self.loop_on = True
        self.settle(0.3)
        st = m.get_encoder_state()
        if not st["pid_enabled"]:
            self.loop_on = False
            raise RuntimeError("ENABLE_STAGE_PID did not take (status shows loop off) - encoder not configured?")
        try:
            step = self.a.step_um / 1000.0
            res = self.record(f"step_p{p}_i{i}_d{d}", self.a.depth_mm, self.a.depth_mm + step)
        finally:
            self.loop_off()
        res.update({"phase": "closed_loop", "p": p, "i": i, "d": d, "fault": self.mcu.get_encoder_state()["pid_fault"]})
        self.summary["results"].append(res)
        return res

    def sweep(self):
        table = []
        for p in self.a.p_list:
            try:
                r = self.closed_loop_step(p, self.a.i, self.a.d)
                table.append(r)
            except Exception as e:  # noqa: BLE001
                self.log(f"P={p}: aborted: {e}")
                self.summary["results"].append({"phase": "closed_loop", "p": p, "i": self.a.i, "d": self.a.d, "error": str(e)})
                self.loop_off()
                self.settle(0.5)
                # re-centre before the next gain
                self.move_to_depth(self.a.depth_mm)
        print("\nP        peak_dev_um  rest_rms_um  final_um  crossings/s")
        for r in table:
            print(f"{r['p']:<8} {r['peak_dev_um']:11.1f}  {r['rest_rms_um']:11.2f}  {r['final_dev_um']:+8.2f}  {r['tail_zero_crossings_per_s']:10.1f}")
        good = [r for r in table if r["tail_zero_crossings_per_s"] < self.a.max_crossings and not r.get("fault")]
        if good:
            best = min(good, key=lambda r: (r["peak_dev_um"], r["rest_rms_um"]))
            self.log(f"recommendation: P={best['p']} (lowest peak error without oscillation); I={self.a.i}, D={self.a.d}")
            self.summary["recommendation"] = {"p": best["p"], "i": self.a.i, "d": self.a.d}
        else:
            self.log("no gain in the list met the oscillation criterion; lower the list or raise --max-crossings")

    def zonemap(self):
        """Open loop: step from --zonemap-from mm toward home in --zonemap-step um increments and back,
        recording ENC_POS vs XACTUAL at rest, to find where the stage stops following the actuator."""
        step_mm = self.a.zonemap_step_um / 1000.0
        top = self.a.zonemap_from
        pts = []

        def sample(tag):
            self.settle(0.4)
            st = self.mcu.get_encoder_state()
            pts.append((tag, self.mcu.z_pos, st["encoder_pos"], st["deviation"]))

        # descend to home (allowed: zonemap deliberately visits the home region open-loop)
        self.a.depth_min = 0.0
        z = top
        while z > -1e-9:
            self.move_to_depth(max(z, 0.0))
            sample("down")
            z -= step_mm
        z = 0.0
        while z <= top + 1e-9:
            self.move_to_depth(z)
            sample("up")
            z += step_mm
        self.move_to_depth(self.a.depth_mm)
        path = os.path.join(self.out, "zonemap.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["direction", "xactual_usteps", "enc_pos_usteps", "deviation_usteps", "extension_mm", "enc_mm", "dev_um"])
            for tag, x, e, d in pts:
                w.writerow([tag, x, e, d, usteps_to_depth(x), usteps_to_depth(e), d / USTEPS_PER_MM * 1000])
        # where does the encoder stop following on the way down?
        down = [(usteps_to_depth(x), usteps_to_depth(e)) for tag, x, e, d in pts if tag == "down"]
        up = [(usteps_to_depth(x), usteps_to_depth(e)) for tag, x, e, d in pts if tag == "up"]
        print("\nextension_mm  enc_mm   dev_um   (down)")
        for (xm, em), (tag, x, e, d) in zip(down, [p for p in pts if p[0] == "down"]):
            print(f"{xm:11.3f} {em:8.3f} {d / USTEPS_PER_MM * 1000:8.1f}")
        decouple = None
        for k in range(1, len(down)):
            dx = down[k][0] - down[k - 1][0]; de = down[k][1] - down[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) < 0.5:
                decouple = down[k - 1][0]; break
        recouple = None
        for k in range(1, len(up)):
            dx = up[k][0] - up[k - 1][0]; de = up[k][1] - up[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) > 0.5:
                recouple = up[k][0]; break
        self.log(f"zonemap: encoder stops following below {decouple} mm on the way down; follows again above {recouple} mm on the way up")
        self.summary["results"].append({"phase": "zonemap", "decouple_mm": decouple, "recouple_mm": recouple, "csv": path})

    def zonetest(self):
        """Verify the firmware home zone: loop engaged at the working extension, a move into the zone must
        drop it to open loop (PID_ZONE flag), a move back out must re-engage it; homing with the loop
        requested must run open-loop and re-engage after moving out."""
        zone_mm = self.a.zone_um / 1000.0
        if zone_mm <= 0:
            raise RuntimeError("zonetest needs --zone-um > 0")
        inside = zone_mm / 2.0
        self.a.depth_min = 0.0  # this test deliberately visits the zone

        def flags(tag):
            self.settle(0.3)
            st = self.mcu.get_encoder_state()
            self.log(f"{tag}: z={self.current_depth():.3f} mm  loop_engaged={st['pid_enabled']}  zone_hold={st['pid_zone_hold']}  "
                     f"fault={st['pid_fault']}  err={st['deviation'] / USTEPS_PER_MM * 1000:+.1f} um")
            return st

        def transitions(rows):
            out = []; last = None
            for t, x, e, d, f in rows:
                eng = bool(f & (1 << _def.ENC_FLAG.PID_ENABLED)); hold = bool(f & (1 << _def.ENC_FLAG.PID_ZONE))
                key = (eng, hold)
                if key != last:
                    out.append((round(usteps_to_depth(x), 3), "engaged" if eng else ("held" if hold else "off")))
                    last = key
            return out

        m = self.mcu
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d); self.wait()
        m.turn_on_stage_pid(AXIS.Z); self.wait(5); self.loop_on = True
        st = flags("A. enable at working extension")
        if not st["pid_enabled"]:
            raise RuntimeError("loop did not engage outside the zone")

        self.sampler.clear()
        self.move_to_depth(inside)
        st = flags(f"B. after move into the zone ({inside:.3f} mm)")
        self.log(f"   transitions during the move: {transitions(self.sampler.snapshot())}")
        ok_b = (not st["pid_enabled"]) and st["pid_zone_hold"] and not st["pid_fault"]

        self.sampler.clear()
        self.move_to_depth(self.a.depth_mm)
        st = flags("C. after move back out")
        self.log(f"   transitions during the move: {transitions(self.sampler.snapshot())}")
        ok_c = st["pid_enabled"] and not st["pid_zone_hold"] and not st["pid_fault"]

        self.log("D. homing with the loop requested")
        self.sampler.clear()
        m.home_z(); self.wait(60)
        st = flags("D. after homing")
        self.log(f"   transitions during homing: {transitions(self.sampler.snapshot())}")
        ok_d = (not st["pid_enabled"]) and st["pid_zone_hold"] and not st["pid_fault"]

        self.sampler.clear()
        self.move_to_depth(self.a.depth_mm)
        st = flags("E. after moving out again")
        self.log(f"   transitions during the move: {transitions(self.sampler.snapshot())}")
        ok_e = st["pid_enabled"] and not st["pid_zone_hold"] and not st["pid_fault"]

        verdict = {"B_drop_in_zone": ok_b, "C_reengage_out": ok_c, "D_open_during_homing": ok_d, "E_reengage_after_homing": ok_e}
        self.summary["results"].append({"phase": "zonetest", "zone_um": self.a.zone_um, **verdict})
        self.log(f"zonetest verdict: {verdict}  -> {'PASS' if all(verdict.values()) else 'FAIL'}")

    def accelsweep(self):
        """Open loop: for each acceleration in --accel-list, do N x 100 um and 2 x 1 mm excursions at --vmax
        and judge (a) lost steps from the change of ENC_POS - XACTUAL at rest across the level, (b) the
        effective acceleration from the command-to-ack time of the 100 um moves, (c) the mic (recorded by
        the caller; wall-clock stamps are written to the summary). Stops at the first level that loses
        steps or trips the guard. Never goes near the travel ends: everything happens at --depth-mm +- 1 mm.

        Register ceiling: AMAX is a 22-bit value in usteps/s^2 on the TMC4361A. Above
        (2^22 - 1) / usteps_per_mm the firmware silently clamps, so levels beyond it are skipped.
        """
        amax_cap = (2 ** 22 - 1) / USTEPS_PER_MM
        self.log(f"AMAX register ceiling at {MICROSTEPS} usteps/FS: {amax_cap:.0f} mm/s2")
        m = self.mcu
        levels = []
        for accel in self.a.accel_list:
            if accel > amax_cap:
                self.log(f"skipping {accel} mm/s2: above the register ceiling ({amax_cap:.0f})")
                continue
            m.set_max_velocity_acceleration(AXIS.Z, self.a.vmax, accel); self.wait()
            self.settle(0.5)
            st0 = m.get_encoder_state(); off0 = st0["deviation"]
            wall0 = time.time()
            acks_100 = []; acks_1000 = []
            try:
                for _ in range(self.a.accel_reps):
                    self.move_to_depth(self.a.depth_mm + 0.1); acks_100.append(self.last_cmd_to_ack_s)
                    self.settle(0.15)
                    self.move_to_depth(self.a.depth_mm); acks_100.append(self.last_cmd_to_ack_s)
                    self.settle(0.15)
                for _ in range(2):
                    self.move_to_depth(self.a.depth_mm + 1.0); acks_1000.append(self.last_cmd_to_ack_s)
                    self.settle(0.2)
                    self.move_to_depth(self.a.depth_mm); acks_1000.append(self.last_cmd_to_ack_s)
                    self.settle(0.2)
            except Exception as e:  # noqa: BLE001
                self.log(f"accel {accel}: aborted: {e}")
                levels.append({"accel": accel, "error": str(e)})
                break
            self.settle(0.5)
            st1 = m.get_encoder_state(); off1 = st1["deviation"]
            lost_um = (off1 - off0) / USTEPS_PER_MM * 1000.0
            t100 = sorted(acks_100)[len(acks_100) // 2] if acks_100 else float("nan")
            t1000 = sorted(acks_1000)[len(acks_1000) // 2] if acks_1000 else float("nan")
            # Trapezoid timing after subtracting the fixed command+report overhead (--ack-overhead-ms):
            # acceleration-limited (a*d <= v^2): t = 2*sqrt(d/a)  ->  a = 4d/t^2
            # velocity-limited  (a*d  > v^2): t = d/v + v/a     ->  a = v/(t - d/v)
            d_mm = 0.1; v = self.a.vmax
            t_mv = t100 - self.a.ack_overhead_ms / 1000.0
            if t_mv <= 0:
                a_eff = float("nan")
            elif accel * d_mm <= v * v:
                a_eff = 4 * d_mm / (t_mv ** 2)
            else:
                a_eff = v / (t_mv - d_mm / v) if t_mv > d_mm / v else float("nan")
            row = {"accel": accel, "ack_100um_median_s": t100, "ack_1mm_median_s": t1000,
                   "implied_accel_mm_s2": a_eff, "lost_um": lost_um, "off_before": off0, "off_after": off1,
                   "wall_start": wall0, "wall_end": time.time()}
            levels.append(row)
            self.log(f"accel {accel:4.0f} mm/s2: 100 um ack {t100 * 1000:5.1f} ms (implied {a_eff:5.0f} mm/s2), "
                     f"1 mm ack {t1000 * 1000:5.1f} ms, encoder offset change {lost_um:+.2f} um")
            if abs(lost_um) > self.a.lost_step_um:
                self.log(f"STOP: {lost_um:+.2f} um of position lost at {accel} mm/s2 (limit {self.a.lost_step_um} um)")
                break
        self.summary["results"].append({"phase": "accelsweep", "vmax": self.a.vmax, "microsteps": MICROSTEPS,
                                        "ramp": self.a.ramp, "amax_register_cap": amax_cap, "levels": levels})
        good = [l for l in levels if "error" not in l and abs(l["lost_um"]) <= self.a.lost_step_um]
        if good:
            self.log(f"highest acceleration with no lost steps: {good[-1]['accel']:.0f} mm/s2 "
                     f"(100 um in {good[-1]['ack_100um_median_s'] * 1000:.0f} ms command-to-ack)")
        self.restore_velocity()

    def engage_loop(self):
        m = self.mcu
        self.settle(0.3)
        dev0 = m.get_encoder_state()["deviation"] / USTEPS_PER_MM * 1000
        if abs(dev0) > self.a.max_dev_um / 4:
            raise RuntimeError(f"not closing the loop: error already {dev0:+.1f} um before enable")
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d); self.wait()
        m.turn_on_stage_pid(AXIS.Z); self.wait(5); self.loop_on = True
        self.settle(0.3)
        if not m.get_encoder_state()["pid_enabled"]:
            self.loop_on = False
            raise RuntimeError("ENABLE_STAGE_PID did not take")

    def stack(self, closed):
        """Focus-stack pattern: --stack-n steps of --stack-um up from the working extension, then back down
        in one move. Per step: command-to-ack time and the encoder error at rest (0.15 s after the ack).
        Open loop (closed=False) or closed loop (closed=True)."""
        n, du = self.a.stack_n, self.a.stack_um / 1000.0
        label = f"stack_{'closed' if closed else 'open'}_{n}x{self.a.stack_um:g}um"
        if closed:
            self.engage_loop()
        wall0 = time.time()
        acks = []; errs = []; encs = []
        try:
            for k in range(1, n + 1):
                self.move_to_depth(self.a.depth_mm + k * du)
                acks.append(self.last_cmd_to_ack_s)
                self.settle(0.15)
                st = self.mcu.get_encoder_state()
                errs.append(st["deviation"] / USTEPS_PER_MM * 1000)
                encs.append(usteps_to_depth(st["encoder_pos"]))
            self.move_to_depth(self.a.depth_mm)
            self.settle(0.3)
        finally:
            if closed:
                self.loop_off()
        # step-to-step encoder increments vs commanded
        inc = [(encs[i] - encs[i - 1]) * 1000 for i in range(1, len(encs))]
        acks_ms = sorted(a_ * 1000 for a_ in acks)
        res = {"phase": "stack", "label": label, "closed": closed, "n": n, "step_um": self.a.stack_um,
               "ack_ms_median": acks_ms[len(acks_ms) // 2], "ack_ms_max": acks_ms[-1],
               "err_um_mean": sum(errs) / len(errs), "err_um_max_abs": max(abs(e) for e in errs),
               "enc_increment_um_mean": (sum(inc) / len(inc)) if inc else float("nan"),
               "enc_increment_um_min": min(inc) if inc else float("nan"), "enc_increment_um_max": max(inc) if inc else float("nan"),
               "wall_start": wall0, "wall_end": time.time()}
        self.summary["results"].append(res)
        self.log(f"{label}: ack median {res['ack_ms_median']:.0f} ms (max {res['ack_ms_max']:.0f}); encoder error mean {res['err_um_mean']:+.2f} um, "
                 f"max |err| {res['err_um_max_abs']:.2f} um; encoder step increments mean {res['enc_increment_um_mean']:.3f} um "
                 f"(min {res['enc_increment_um_min']:.3f}, max {res['enc_increment_um_max']:.3f}) for commanded {self.a.stack_um:g} um")
        return res

    def hold(self):
        """Closed-loop hold at the working extension for --hold-s seconds (mic records hunting), then loop off.
        With --hold-open-s N an open-loop hold of N s at the same position is recorded first, so the
        microphone has an ambient control window inside the same recording (the only thing that changes
        at the boundary is the loop engaging)."""
        if self.a.hold_open_s > 0:
            wall_o = time.time(); devs_o = []
            t0 = time.time()
            while time.time() - t0 < self.a.hold_open_s:
                self.guard()
                devs_o.append(self.mcu.get_encoder_state()["deviation"])
                time.sleep(0.01)
            d_o = [d / USTEPS_PER_MM * 1000 for d in devs_o]
            self.summary["results"].append({"phase": "hold_open", "label": f"hold_open_{self.a.hold_open_s:g}s",
                                            "seconds": self.a.hold_open_s, "err_um_mean": sum(d_o) / len(d_o),
                                            "err_um_max_abs": max(abs(v) for v in d_o),
                                            "wall_start": wall_o, "wall_end": time.time()})
            self.log(f"hold {self.a.hold_open_s:g} s OPEN loop (control): error mean {sum(d_o) / len(d_o):+.3f} um")
        self.engage_loop()
        wall0 = time.time(); devs = []
        try:
            t0 = time.time()
            while time.time() - t0 < self.a.hold_s:
                self.guard()
                devs.append(self.mcu.get_encoder_state()["deviation"])
                time.sleep(0.01)
        finally:
            self.loop_off()
        import statistics
        d_um = [d / USTEPS_PER_MM * 1000 for d in devs]
        cross = sum(1 for a_, b_ in zip(d_um, d_um[1:]) if (a_ < 0) != (b_ < 0))
        res = {"phase": "hold", "label": f"hold_closed_{self.a.hold_s:g}s", "seconds": self.a.hold_s,
               "err_um_mean": statistics.mean(d_um), "err_um_std": statistics.pstdev(d_um), "err_um_max_abs": max(abs(v) for v in d_um),
               "zero_crossings_per_s": cross / self.a.hold_s, "wall_start": wall0, "wall_end": time.time()}
        self.summary["results"].append(res)
        self.log(f"hold {self.a.hold_s:g} s closed loop: error mean {res['err_um_mean']:+.3f} um, std {res['err_um_std']:.3f} um, "
                 f"max |err| {res['err_um_max_abs']:.2f} um, zero crossings {res['zero_crossings_per_s']:.1f}/s")

    # ---------------------------------------------------------------- main
    def run(self):
        try:
            self.connect()
            self.configure_z()
            self.configure_encoder(self.flip)   # before homing: the homing zero must be taken under the final encoder scale
            self.home()
            self.encoder_check()
            if self.a.action == "check":
                return
            if self.a.action == "zonemap":
                self.zonemap()
                return
            if self.a.action == "zonetest":
                self.zonetest()
                return
            if self.a.action == "accelsweep":
                self.accelsweep()
                return
            if self.a.action == "stack":
                self.stack(closed=False)
                self.stack(closed=True)
                return
            if self.a.action == "hold":
                self.hold()
                return
            if self.a.action in ("baseline", "step", "sweep"):
                self.baseline()
            if self.a.action == "step":
                self.closed_loop_step(self.a.p, self.a.i, self.a.d)
            elif self.a.action == "sweep":
                self.sweep()
        finally:
            self.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "baseline", "step", "sweep", "zonemap", "zonetest", "accelsweep", "stack", "hold"])
    ap.add_argument("--depth-mm", type=float, default=2.5, help="working depth below the top switch")
    ap.add_argument("--depth-min", type=float, default=1.0)
    ap.add_argument("--depth-max", type=float, default=4.5)
    ap.add_argument("--step-um", type=float, default=100.0, help="excursion for baseline/step tests")
    ap.add_argument("--vmax", type=float, default=1.0, help="Z max velocity during the session, mm/s")
    ap.add_argument("--accel", type=float, default=20.0, help="Z acceleration during the session, mm/s2")
    ap.add_argument("--corr-vmax", type=float, default=0.3, help="closed-loop correction velocity clamp, mm/s")
    ap.add_argument("--max-dev-um", type=float, default=200.0, help="watchdog / guard limit on loop error")
    ap.add_argument("--settle-tol-um", type=float, default=0.5)
    ap.add_argument("--max-crossings", type=float, default=5.0, help="tail zero-crossings/s above which a gain counts as oscillating")
    ap.add_argument("--p", type=int, default=_def.PID_P_Z)
    ap.add_argument("--i", type=int, default=_def.PID_I_Z)
    ap.add_argument("--d", type=int, default=_def.PID_D_Z)
    ap.add_argument("--p-list", type=int, nargs="+", default=[1024, 2048, 4096, 8192, 16384])
    ap.add_argument("--zone-um", type=float, default=0.0, help="home exclusion zone sent to firmware (0 = none)")
    ap.add_argument("--tol-um", type=float, default=0.0, help="closed-loop deadband and target-reached tolerance in um (0 = firmware default: 2 encoder counts)")
    ap.add_argument("--accel-list", type=float, nargs="+", default=[100, 150, 200, 250, 300, 350, 390])
    ap.add_argument("--stack-n", type=int, default=20)
    ap.add_argument("--stack-um", type=float, default=1.0)
    ap.add_argument("--hold-s", type=float, default=20.0)
    ap.add_argument("--hold-open-s", type=float, default=0.0, help="open-loop control hold recorded before the closed-loop hold")
    ap.add_argument("--accel-reps", type=int, default=5, help="100 um out-and-back repetitions per level")
    ap.add_argument("--lost-step-um", type=float, default=1.0, help="encoder-vs-counter offset change that counts as lost steps")
    ap.add_argument("--ack-overhead-ms", type=float, default=7.0, help="fixed command+report overhead subtracted when inferring acceleration")
    ap.add_argument("--zonemap-from", type=float, default=2.0, help="zonemap start extension, mm")
    ap.add_argument("--zonemap-step-um", type=float, default=50.0)
    ap.add_argument("--microsteps", type=int, default=int(_def.MICROSTEPPING_DEFAULT_Z),
                    help="Z microsteps per full step to configure (ini default %d; 256 was used for the first sessions)" % int(_def.MICROSTEPPING_DEFAULT_Z))
    ap.add_argument("--ramp", choices=["sshape", "trapezoid"], default="sshape", help="TMC4361A ramp profile for Z during the session")
    ap.add_argument("--out", default="z_tune")
    args = ap.parse_args()
    set_microsteps(args.microsteps)
    if args.depth_max > HARD_CAP_DEPTH_MM:
        print(f"--depth-max capped at {HARD_CAP_DEPTH_MM} mm")
    ZTuner(args).run()


if __name__ == "__main__":
    main()
