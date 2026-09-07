"""Z-axis encoder check and closed-loop (TMC4361A PID) tuning tool.

Talks to the controller through control.microcontroller (the same path the GUI uses), so
whatever this tool establishes is what the software will see. Requires firmware >= 1.6
(SET_ENCODER_REPORTING / SET_PID_LIMITS). Close the Squid GUI first: it holds the port.

Safety model - Z must never be driven into a travel end:
  * All motion stays inside a depth window below the top (home) switch, default 1.0 .. 4.5 mm,
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
Common options: --depth-mm 2.5 --step-um 100 --vmax 1.0 --corr-vmax 0.3 --max-dev-um 200 --out z_tune
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
from control._def import AXIS, ENCODER_REPORTING  # noqa: E402
from control.microcontroller import Microcontroller, get_microcontroller_serial_device  # noqa: E402

FULLSTEPS_PER_REV = 200
MICROSTEPS = 256            # the tool sets this explicitly, so usteps/mm is known regardless of GUI history
PITCH_MM = _def.SCREW_PITCH_Z_MM
ENC_STEP_MM = _def.ENCODER_STEP_SIZE_Z_MM
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
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def run(self):
        last = None
        t0 = time.time()
        while not self._stop.is_set():
            st = self.mcu.get_encoder_state()
            row = (time.time() - t0, self.mcu.z_pos, st["encoder_pos"], st["deviation"], self.mcu.encoder_flags)
            if last is None or row[1:] != last[1:]:
                with self._lock:
                    self.rows.append(row)
                last = row
            time.sleep(0.004)

    def stop(self):
        self._stop.set()
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
        self.log(f"Z configured: {MICROSTEPS} usteps/FS, pitch {PITCH_MM} mm, {_def.Z_MOTOR_RMS_CURRENT_mA} mA, "
                 f"vmax {self.a.vmax} mm/s, accel {self.a.accel} mm/s2 ({USTEPS_PER_MM:.0f} usteps/mm)")

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
        if self.mcu is None:
            return
        self.loop_off()
        try:
            self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.OFF); self.wait(5)
        except Exception as e:  # noqa: BLE001
            self.log(f"could not disable reporting: {e}")
        self.restore_velocity()
        if self.sampler:
            self.sampler.stop()
        with open(os.path.join(self.out, "summary.json"), "w") as f:
            json.dump(self.summary, f, indent=2, default=str)
        self.log(f"summary written to {os.path.join(self.out, 'summary.json')}; loop off, reporting off, velocity restored")
        self.mcu.close()

    # ---------------------------------------------------------------- safety
    def check_depth(self, depth_mm):
        lo, hi = self.a.depth_min, min(self.a.depth_max, HARD_CAP_DEPTH_MM)
        if not (lo <= depth_mm <= hi):
            raise RuntimeError(f"refusing Z target {depth_mm:.3f} mm: outside the allowed window {lo}..{hi} mm")

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
        if not (self.a.depth_min - 0.5 <= d <= min(self.a.depth_max, HARD_CAP_DEPTH_MM) + 0.5):
            self.loop_off()
            raise RuntimeError(f"host guard: Z at {d:.3f} mm left the window")

    def move_to_depth(self, depth_mm, timeout=30.0):
        self.check_depth(depth_mm)
        self.mcu.move_z_to_usteps(depth_to_usteps(depth_mm))
        t0 = time.time()
        while self.mcu.is_busy():
            self.guard()
            if time.time() - t0 > timeout:
                self.loop_off()
                raise TimeoutError("Z move did not complete")
            time.sleep(0.01)

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
        self.log(f"Z homed; moving to {self.a.depth_mm} mm depth")
        self.move_to_depth(self.a.depth_mm)

    def configure_encoder(self, flip):
        m = self.mcu
        m.set_pid_limits(AXIS.Z, self.a.corr_vmax, self.a.max_dev_um); self.wait()
        m.configure_stage_pid(AXIS.Z, TRANSITIONS_PER_REV, flip_direction=flip); self.wait()
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d); self.wait()
        m.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.ENC_IN_THETA); self.wait()
        time.sleep(0.2)
        st = m.get_encoder_state()
        if not st["reporting"]:
            raise RuntimeError("firmware did not start encoder reporting - is it >= 1.6? (SET_ENCODER_REPORTING ignored)")
        self.log(f"encoder configured: {TRANSITIONS_PER_REV} transitions/rev, flip={flip}, "
                 f"correction vmax {self.a.corr_vmax} mm/s, watchdog {self.a.max_dev_um} um")

    def encoder_check(self):
        """Open loop: encoder must follow XACTUAL with ratio +1 (in usteps). Fix the sign if it reads -1."""
        for attempt in range(2):
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
                return
            if attempt == 0:
                self.flip = not self.flip
                self.log(f"encoder runs backwards: reconfiguring with flip={self.flip}")
        raise RuntimeError("encoder sign still wrong after flipping - not closing the loop")

    def record(self, label, depth_from, depth_to, dwell=1.0):
        """One excursion: dwell, move, dwell, move back, dwell. Returns metrics from the sampler."""
        self.sampler.clear()
        self.settle(dwell)
        t_move = self.sampler.rows[-1][0] if self.sampler.rows else 0.0
        self.move_to_depth(depth_to)
        self.settle(dwell)
        self.move_to_depth(depth_from)
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
        rest_rms = math.sqrt(sum(d * d for d in rest) / len(rest)) if rest else float("nan")
        # settling: first time after the last move where |dev| stays within tol for 0.2 s
        tol = self.a.settle_tol_um * USTEPS_PER_MM / 1000.0
        settle_s = float("nan")
        if rows:
            t_end = rows[-1][0]
            inside_since = None
            for t, _, _, d, _ in rows:
                if abs(d) <= tol:
                    inside_since = t if inside_since is None else inside_since
                    if t - inside_since >= 0.2:
                        settle_s = inside_since
                        break
                else:
                    inside_since = None
            settle_s = t_end - settle_s if not math.isnan(settle_s) else float("nan")
        # oscillation: sign changes per second in the deviation while at rest at the end
        tail = [r[3] for r in rows if r[0] > rows[-1][0] - dwell] if rows else []
        crossings = sum(1 for a, b in zip(tail, tail[1:]) if (a < 0) != (b < 0))
        metrics = {"label": label, "samples": len(rows), "peak_dev_um": peak / USTEPS_PER_MM * 1000,
                   "rest_rms_um": rest_rms / USTEPS_PER_MM * 1000, "tail_zero_crossings_per_s": crossings / dwell,
                   "final_dev_um": (rows[-1][3] / USTEPS_PER_MM * 1000) if rows else float("nan"),
                   "csv": path}
        self.log(f"{label}: peak |dev| {metrics['peak_dev_um']:.1f} um, rest rms {metrics['rest_rms_um']:.2f} um, "
                 f"final {metrics['final_dev_um']:+.2f} um, tail crossings {metrics['tail_zero_crossings_per_s']:.1f}/s")
        return metrics

    def baseline(self):
        step = self.a.step_um / 1000.0
        m = self.record("baseline_openloop", self.a.depth_mm, self.a.depth_mm + step)
        m["phase"] = "baseline"
        self.summary["results"].append(m)

    def closed_loop_step(self, p, i, d):
        m = self.mcu
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

    # ---------------------------------------------------------------- main
    def run(self):
        try:
            self.connect()
            self.configure_z()
            self.home()
            self.encoder_check()
            if self.a.action == "check":
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
    ap.add_argument("action", choices=["check", "baseline", "step", "sweep"])
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
    ap.add_argument("--out", default="z_tune")
    args = ap.parse_args()
    if args.depth_max > HARD_CAP_DEPTH_MM:
        print(f"--depth-max capped at {HARD_CAP_DEPTH_MM} mm")
    ZTuner(args).run()


if __name__ == "__main__":
    main()
