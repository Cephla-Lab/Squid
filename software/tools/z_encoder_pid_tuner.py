"""Z-axis encoder check and closed-loop (TMC4361A PID) tuning tool.

Talks to the controller through control.microcontroller (the same path the GUI uses), so
whatever this tool establishes is what the software will see. Requires firmware >= 1.6
(SET_ENCODER_REPORTING / SET_PID_LIMITS). Close the Squid GUI first: it holds the port.

Geometry: home (XACTUAL = 0) is the actuator fully retracted with the stage resting on its stop; positive
mm in the software is the actuator extending and the stage moving up. Near home the stage can rest on its
stop while the actuator keeps retracting, so the encoder stops following: firmware >= 1.6 holds the loop
open inside a configurable home zone (--zone-um) and `zonemap` measures where that happens.

The encoder error everywhere in this tool - every log line, every CSV column still named deviation_usteps
or dev_um, every summary field - is ENC_POS - XACTUAL at full width, as the reader thread computed it from
the encoder position and the step counter of a single status packet. The firmware also reports that difference itself, as ENC_POS_DEV, but clipped to
an int16: +-32767 usteps is +-192 um on a 256 usteps/FS Z, below the 200 um --max-dev-um default, so a host
guard reading that field could never trip at the default watchdog and any error past +-192 um was reported
wrong. The clipped field is only used where the firmware's own behaviour is the subject.

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
  python tools/z_encoder_pid_tuner.py ackprobe --ack-steps-um 1 10 100 --ack-reps 20 --exposure-ms 100

ackprobe answers the question an acquisition asks - "may the camera start now?" - rather than the one an ack
answers - "did the controller reply?". For every size in --ack-steps-um it reads the encoder error and the loop
state at the acknowledgment itself, with no settle in between, and then every 5 ms through an --exposure-ms
window: error at the ack, whether the loop was engaged there and throughout, how long until the error is inside
the configured tolerance. The closed-loop and open-loop ladders alternate which one runs first (odd reps closed
first) so a drift cannot favour either. The error is ENC_POS - XACTUAL computed from the 32-bit fields, not the
int16 ENC_POS_DEV the firmware reports, which saturates at +-192 um on a 256 usteps/FS Z. The host sees the
controller through the 10 ms status stream: behaviour faster than that is not resolvable from here.
If the firmware opens the loop during a ladder, that move is the result: it is written to the CSV with its
fault cause before the ladder stops. The cause is only on the wire while encoder reporting is off, so it is
read by dropping reporting for ~30 ms - and before any DISABLE, which clears it along with the fault.

Common options: --depth-mm 2.5 (extension from home) --step-um 100 --vmax 1.0 --corr-vmax 0.3 --max-dev-um 200
                --zone-um 0 (home exclusion zone sent to firmware) --out z_tune
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import control._def as _def  # noqa: E402  (loads the machine configuration)
from control._def import AXIS, ENCODER_REPORTING, PID_FAULT_CAUSE, RAMP_PROFILE  # noqa: E402
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
SIGN = _def.STAGE_MOVEMENT_SIGN_Z  # -1 on the Squid+: "down" (positive depth) is negative usteps
HARD_CAP_DEPTH_MM = 5.5  # never command below this, whatever the arguments say
FW_MIN = (1, 6)


def depth_to_usteps(depth_mm):
    return int(round(SIGN * depth_mm * USTEPS_PER_MM))


def usteps_to_depth(usteps):
    return SIGN * usteps / USTEPS_PER_MM


def host_git_hash():
    """Short hash of the checkout this tool ran from, so a result set can be tied to the host code. '' if unknown."""
    try:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - provenance is best effort, never a reason to fail a bench run
        return ""


def pct(values, q):
    """q-th percentile (q in 0..1) by nearest rank; NaN for an empty list."""
    if not values:
        return float("nan")
    v = sorted(values)
    return v[max(0, min(len(v) - 1, int(math.ceil(q * len(v))) - 1))]


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
            z = self.mcu.z_pos
            if st["dev32"] is None:
                # No encoder reading in this packet: reporting is off, either before the tool turned it
                # on, while _read_z_fault_cause() has it down for a few packets, or after shutdown
                # dropped it. The last reading is not a measurement of now - the stage has moved since -
                # so a row built from it would put a fabricated pair in the trace.
                time.sleep(0.004)
                continue
            # column 3 is ENC_POS - XACTUAL at full width, paired by the reader thread inside one packet,
            # not the firmware's int16 ENC_POS_DEV: that field clips at +-32767 usteps and hides exactly
            # the excursions a trace is recorded for
            row = (time.time() - t0, z, st["encoder_pos"], st["dev32"], self.mcu.encoder_flags)
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
        # PID_FAULT_CAUSE read at the last fault, so an abort raised deep in a polling loop still
        # carries the reason back out to whatever is recording the run.
        self.last_fault_cause = PID_FAULT_CAUSE.NONE
        self.last_cmd_to_ack_s = float("nan")
        self.cmd_to_ack_log = []
        self.summary = {
            "config": vars(args),
            "pitch_mm": PITCH_MM,
            "usteps_per_mm": USTEPS_PER_MM,
            "transitions_per_rev": TRANSITIONS_PER_REV,
            "results": [],
        }

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
        m.configure_motor_driver(AXIS.Z, MICROSTEPS, _def.Z_MOTOR_RMS_CURRENT_mA, _def.Z_MOTOR_I_HOLD)
        self.wait()
        m.set_leadscrew_pitch(AXIS.Z, PITCH_MM)
        self.wait()
        m.set_max_velocity_acceleration(AXIS.Z, self.a.vmax, self.a.accel)
        self.wait()
        prof = RAMP_PROFILE.TRAPEZOID if self.a.ramp == "trapezoid" else RAMP_PROFILE.SSHAPE
        m.set_ramp_profile(AXIS.Z, prof)
        self.wait()
        self.log(
            f"Z configured: {MICROSTEPS} usteps/FS, pitch {PITCH_MM} mm, {_def.Z_MOTOR_RMS_CURRENT_mA} mA, "
            f"vmax {self.a.vmax} mm/s, accel {self.a.accel} mm/s2, ramp {self.a.ramp} ({USTEPS_PER_MM:.0f} usteps/mm)"
        )

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
            raise RuntimeError(
                f"refusing Z target {depth_mm:.3f} mm extension: outside the allowed window {lo}..{hi} mm"
            )

    def current_depth(self):
        return usteps_to_depth(self.mcu.z_pos)

    def guard(self):
        """Called during waits: aborts (loop off) on fault flag, excess error, or leaving the window."""
        st = self.mcu.get_encoder_state()
        d = self.current_depth()
        if st["pid_fault"]:
            # before loop_off(): DISABLE acknowledges the fault, and the cause goes with it
            cause = self._read_z_fault_cause()
            self.loop_off()
            raise RuntimeError(
                f"firmware opened the loop (PID_FAULT): {self._cause_text(cause) or 'cause not reported'}"
            )
        dev = self._dev32_usteps(st)
        if st["pid_enabled"] and abs(dev) > self.a.max_dev_um * USTEPS_PER_MM / 1000.0:
            # only while the firmware reports the loop ENGAGED: while it is held open (home zone, homing,
            # or the gap above home on a stage whose actuator homes below its stop) the deviation is
            # expected to be large and means nothing
            self.loop_off()
            raise RuntimeError(
                f"host guard: loop error {dev} usteps ({dev / USTEPS_PER_MM * 1000:+.1f} um) exceeded "
                f"{self.a.max_dev_um} um"
            )
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
        # host-visible latency: command sent -> ack seen (includes the 10 ms packet cadence). Timed
        # here, before the cause read below spends ~100 ms of it on the wire.
        self.last_cmd_to_ack_s = time.time() - t0
        # The controller clears 'busy' on an abort as well as on a completed move, and the
        # CMD_EXECUTION_ERROR that aborts one is carried by the same packet as the fault bit. Read as
        # an ack, the faulted move was recorded as a clean one - and the abort was left uncleared for
        # the next send_command to warn about.
        err = self.mcu.last_command_aborted_error
        if err is not None:
            # before the abort is acknowledged: DISABLE / ENABLE / CONFIGURE_STAGE_PID clear the
            # cause along with the fault, so this is the only moment the firmware will say why
            cause = self._read_z_fault_cause()
            self.mcu.acknowledge_aborted_command()
            raise RuntimeError(
                f"Z move aborted by the controller: {err}; fault cause: {self._cause_text(cause) or 'none'}"
            )
        self.cmd_to_ack_log.append(self.last_cmd_to_ack_s)

    def settle(self, seconds):
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.guard()
            time.sleep(0.01)

    def _move_and_read(self, depth_mm, settle_s=0.0):
        """Move to `depth_mm` and read the encoder state; returns (command-to-ack seconds, state).

        settle_s = 0 reads at the acknowledgment with nothing in between, which is what ackprobe needs (the
        error an exposure started on the ack would see); stack() passes its settle and gets the error at rest.
        """
        self.move_to_depth(depth_mm)
        ack_s = self.last_cmd_to_ack_s
        if settle_s > 0:
            self.settle(settle_s)
        return ack_s, self.mcu.get_encoder_state()

    def _dev32_usteps(self, st):
        """Encoder minus counter, full width, from one status packet.

        st["deviation"] is the firmware's ENC_POS_DEV clipped to int16 (+-32767 usteps, +-192 um on a
        256 usteps/FS Z), which saturates exactly where a lost-motion number matters. st["dev32"] is the
        same difference at full width, and the reader thread pairs ENC_POS with the step counter of the
        packet it arrived in - pairing them here instead would straddle packets (~10 um at 1 mm/s).
        """
        return int(st["dev32"])

    # ---------------------------------------------------------------- phases
    def home(self):
        self.log("homing Z (toward the top switch)")
        self.mcu.home_z()
        self.wait(60)
        self.log(f"Z homed; moving to {self.a.depth_mm} mm extension from home")
        self.move_to_depth(self.a.depth_mm)

    def configure_encoder(self, flip):
        m = self.mcu
        m.set_pid_limits(AXIS.Z, self.a.corr_vmax, self.a.max_dev_um)
        self.wait()
        m.set_pid_home_zone(AXIS.Z, self.a.zone_um)
        self.wait()
        if self.a.tol_um > 0:
            m.set_pid_tolerance(AXIS.Z, self.a.tol_um, self.a.tol_um)
            self.wait()
        # Loop mode (SET_PID_OPEN_ABOVE: opened above this ramp velocity, re-engaged below; 0 = rest-only) and
        # completion window (0 = exact target) are states, not 'keep the current value': always send them. On
        # firmware before 2026-09-08 a threshold left by an earlier run survived the controller reset and a
        # 'rest-only' run of this tool ran engaged in flight.
        m.set_pid_open_above(AXIS.Z, self.a.open_above)
        self.wait()
        m.set_completion_window(AXIS.Z, self.a.window_um / 1000.0)
        self.wait()
        m.configure_stage_pid(AXIS.Z, TRANSITIONS_PER_REV, flip_direction=flip)
        self.wait()
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d)
        self.wait()
        m.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.ENC_IN_THETA)
        self.wait()
        time.sleep(0.2)
        st = m.get_encoder_state()
        if not st["reporting"]:
            raise RuntimeError(
                "firmware did not start encoder reporting - is it >= 1.6? (SET_ENCODER_REPORTING ignored)"
            )
        self.log(
            f"encoder configured: {TRANSITIONS_PER_REV} transitions/rev, flip={flip}, "
            f"correction vmax {self.a.corr_vmax} mm/s, watchdog {self.a.max_dev_um} um, home zone {self.a.zone_um} um"
        )

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
            self.summary["results"].append(
                {"phase": "encoder_check", "attempt": attempt, "flip": self.flip, "dz": dz, "de": de, "ratio": ratio}
            )
            if abs(abs(ratio) - 1.0) > 0.05:
                implied = ENC_STEP_MM * abs(ratio)
                raise RuntimeError(
                    f"encoder scale off by {abs(ratio) - 1:+.1%}: transitions/rev {TRANSITIONS_PER_REV} "
                    f"implies an encoder step of {implied * 1000:.4f} um instead of {ENC_STEP_MM * 1000:.4f}. "
                    "Fix ENCODER_STEP_SIZE_Z_MM / SCREW_PITCH_Z_MM before closing the loop."
                )
            if ratio > 0:
                self.log("encoder sign OK")
                # The loop nulls XACTUAL - ENC_POS in absolute terms, so the two frames must agree
                # before it is ever closed. Firmware >= 1.6 zeroes ENC_POS with XACTUAL at homing/zero.
                if self.a.align_after_home:
                    # Stages whose actuator homes below the stage's stop have a decoupled gap above home
                    # (zonemap measures it: 0.64 mm on the second bench Z). Re-align the encoder frame to
                    # XACTUAL here, at a coupled position, so the loop only corrects deviations that arise
                    # while coupled. The home zone (--zone-um) must cover the gap.
                    self.mcu.configure_stage_pid(AXIS.Z, TRANSITIONS_PER_REV, flip_direction=self.flip)
                    self.wait()
                    self.settle(0.3)
                    self.log(
                        f"encoder frame re-aligned to XACTUAL at {self.a.depth_mm} mm extension (--align-after-home)"
                    )
                self.settle(0.3)
                dev = self._dev32_usteps(self.mcu.get_encoder_state())
                dev_um = dev / USTEPS_PER_MM * 1000
                self.log(f"encoder frame offset after homing: {dev_um:+.1f} um")
                if abs(dev_um) > self.a.max_dev_um / 4:
                    if self.a.action == "zonemap":
                        # the zone map never closes the loop: it is the tool that measures exactly this
                        # offset (a stage that rests on its stop while the actuator homes below it)
                        self.log("frame offset exceeds the gate; continuing because zonemap is open-loop only")
                        return
                    raise RuntimeError(
                        f"encoder frame is offset from XACTUAL by {dev_um:+.1f} um; "
                        "the loop would slew by that amount on enable. Refusing to continue. "
                        f"(Past {32767 / USTEPS_PER_MM * 1000:.0f} um the status packet's ENC_POS_DEV field, "
                        "an int16, can no longer carry the number, so host readings taken from it - including "
                        "older readings from this tool - were wrong. The firmware is not affected: its ENABLE "
                        "gate and its watchdog read the full 32-bit deviation register.) "
                        "Run `zonemap` to see where the encoder decouples from the counter."
                    )
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
            self.log(
                "WARNING: loop error ran past the range of the status packet's int16 ENC_POS_DEV field (>=192 um "
                "at 256 usteps/FS), so any reading taken from that field past this point is wrong - this trace "
                "uses the 32-bit difference instead. The firmware still sees the whole error: its watchdog and "
                "its enable check read the full 32-bit deviation register. What the size itself means is that "
                "the encoder frame is offset from XACTUAL; firmware must zero ENC_POS at homing; do not close "
                "the loop in this state."
            )
        # settling time: from the end of the last commanded move (last change of XACTUAL) until |dev|
        # stays within tol for 0.2 s
        tol = self.a.settle_tol_um * USTEPS_PER_MM / 1000.0
        settle_s = float("nan")
        if rows:
            t_last_move = max(
                (rows[k][0] for k in range(1, len(rows)) if rows[k][1] != rows[k - 1][1]), default=rows[0][0]
            )
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
        metrics = {
            "label": label,
            "samples": len(rows),
            "peak_dev_um": peak / USTEPS_PER_MM * 1000,
            "rest_mean_um": rest_mean / USTEPS_PER_MM * 1000,
            "rest_rms_um": rest_rms / USTEPS_PER_MM * 1000,
            "tail_zero_crossings_per_s": crossings / dwell,
            "final_dev_um": (rows[-1][3] / USTEPS_PER_MM * 1000) if rows else float("nan"),
            "settle_after_last_move_s": settle_s,
            "cmd_to_ack_out_s": ack_out,
            "cmd_to_ack_back_s": ack_back,
            "wall_start": wall_start,
            "wall_end": time.time(),
            "csv": path,
        }
        self.log(
            f"{label}: cmd->ack {ack_out * 1000:.0f} / {ack_back * 1000:.0f} ms; peak |dev| {metrics['peak_dev_um']:.1f} um, "
            f"rest rms {metrics['rest_rms_um']:.2f} um, final {metrics['final_dev_um']:+.2f} um, "
            f"encoder settled {settle_s * 1000:.0f} ms after the ramp ended, tail crossings {metrics['tail_zero_crossings_per_s']:.1f}/s"
        )
        return metrics

    def baseline(self):
        step = self.a.step_um / 1000.0
        m = self.record("baseline_openloop", self.a.depth_mm, self.a.depth_mm + step)
        m["phase"] = "baseline"
        self.summary["results"].append(m)

    def closed_loop_step(self, p, i, d):
        m = self.mcu
        self.settle(0.3)
        dev0 = self._dev32_usteps(m.get_encoder_state()) / USTEPS_PER_MM * 1000
        if abs(dev0) > self.a.max_dev_um / 4:
            raise RuntimeError(f"not closing the loop: error already {dev0:+.1f} um before enable")
        m.set_pid_arguments(AXIS.Z, p, i, d)
        self.wait()
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
                self.summary["results"].append(
                    {"phase": "closed_loop", "p": p, "i": self.a.i, "d": self.a.d, "error": str(e)}
                )
                self.loop_off()
                self.settle(0.5)
                # re-centre before the next gain
                self.move_to_depth(self.a.depth_mm)
        print("\nP        peak_dev_um  rest_rms_um  final_um  crossings/s")
        for r in table:
            print(
                f"{r['p']:<8} {r['peak_dev_um']:11.1f}  {r['rest_rms_um']:11.2f}  {r['final_dev_um']:+8.2f}  {r['tail_zero_crossings_per_s']:10.1f}"
            )
        good = [r for r in table if r["tail_zero_crossings_per_s"] < self.a.max_crossings and not r.get("fault")]
        if good:
            best = min(good, key=lambda r: (r["peak_dev_um"], r["rest_rms_um"]))
            self.log(
                f"recommendation: P={best['p']} (lowest peak error without oscillation); I={self.a.i}, D={self.a.d}"
            )
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
            z = self.mcu.z_pos
            if st["dev32"] is None:
                # encoder reporting is not on this packet; the previous reading belongs to a previous
                # position, and the map exists to say where the stage stopped following the actuator
                self.log(f"zonemap: no encoder reading at {usteps_to_depth(z):.3f} mm - point skipped")
                return
            pts.append((tag, z, st["encoder_pos"], st["dev32"]))

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
            w.writerow(
                [
                    "direction",
                    "xactual_usteps",
                    "enc_pos_usteps",
                    "deviation_usteps",
                    "extension_mm",
                    "enc_mm",
                    "dev_um",
                ]
            )
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
            dx = down[k][0] - down[k - 1][0]
            de = down[k][1] - down[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) < 0.5:
                decouple = down[k - 1][0]
                break
        recouple = None
        for k in range(1, len(up)):
            dx = up[k][0] - up[k - 1][0]
            de = up[k][1] - up[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) > 0.5:
                recouple = up[k][0]
                break
        self.log(
            f"zonemap: encoder stops following below {decouple} mm on the way down; follows again above {recouple} mm on the way up"
        )
        if decouple is not None and recouple is not None:
            gap = max(float(decouple), float(recouple))
            floor = math.ceil((gap + 0.1) * 20) / 20.0  # gap + 0.1 mm margin, rounded up to 0.05 mm
            self.log(
                f"zonemap: ini values for this stage -> z_home_gap_mm = {gap:.2f}; [SOFTWARE_POS_LIMIT] z_negative = {floor:.2f} "
                f"(floor for every move); z_park_at_min_after_homing = True; pid_home_zone_z_um <= {floor * 1000:.0f}"
            )
        else:
            self.log("zonemap: no decoupled region above home on this stage (z_home_gap_mm = 0)")
        self.summary["results"].append(
            {"phase": "zonemap", "decouple_mm": decouple, "recouple_mm": recouple, "csv": path}
        )

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
            time.sleep(0.3)  # plain sleep: a fault flag is a result here, not a reason to abort
            st = self.mcu.get_encoder_state()
            self.log(
                f"{tag}: z={self.current_depth():.3f} mm  loop_engaged={st['pid_enabled']}  zone_hold={st['pid_zone_hold']}  "
                f"fault={st['pid_fault']}  err={self._dev32_usteps(st) / USTEPS_PER_MM * 1000:+.1f} um"
            )
            return st

        def transitions(rows):
            out = []
            last = None
            for t, x, e, d, f in rows:
                eng = bool(f & (1 << _def.ENC_FLAG.PID_ENABLED))
                hold = bool(f & (1 << _def.ENC_FLAG.PID_ZONE))
                key = (eng, hold)
                if key != last:
                    out.append((round(usteps_to_depth(x), 3), "engaged" if eng else ("held" if hold else "off")))
                    last = key
            return out

        def dump(tag, rows):
            """Save the sampled trace of a phase (extension, encoder, deviation, flags) - kept on failure too."""
            path = os.path.join(self.out, f"zonetest_{tag}.csv")
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t_s", "extension_mm", "enc_mm", "dev_um", "engaged", "zone_hold", "fault"])
                for t, x, e, d, fl in rows:
                    w.writerow(
                        [
                            f"{t:.4f}",
                            f"{usteps_to_depth(x):.5f}",
                            f"{usteps_to_depth(e):.5f}",
                            f"{d / USTEPS_PER_MM * 1000:.2f}",
                            int(bool(fl & (1 << _def.ENC_FLAG.PID_ENABLED))),
                            int(bool(fl & (1 << _def.ENC_FLAG.PID_ZONE))),
                            int(bool(fl & (1 << _def.ENC_FLAG.PID_FAULT))),
                        ]
                    )
            return path

        m = self.mcu
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d)
        self.wait()
        m.turn_on_stage_pid(AXIS.Z)
        self.wait(5)
        self.loop_on = True
        st = flags("A. enable at working extension")
        if not st["pid_enabled"]:
            raise RuntimeError("loop did not engage outside the zone")

        verdict = {
            "B_drop_in_zone": False,
            "C_reengage_out": False,
            "D_open_during_homing": False,
            "E_reengage_after_homing": False,
        }
        phases = [
            (
                "B",
                "B_drop_in_zone",
                lambda: self.move_to_depth(inside),
                f"B. after move into the zone ({inside:.3f} mm)",
                lambda st: (not st["pid_enabled"]) and st["pid_zone_hold"] and not st["pid_fault"],
            ),
            (
                "C",
                "C_reengage_out",
                lambda: self.move_to_depth(self.a.depth_mm),
                "C. after move back out",
                lambda st: st["pid_enabled"] and not st["pid_zone_hold"] and not st["pid_fault"],
            ),
            (
                "D",
                "D_open_during_homing",
                lambda: (m.home_z(), self.wait(60)),
                "D. after homing",
                lambda st: (not st["pid_enabled"]) and st["pid_zone_hold"] and not st["pid_fault"],
            ),
            (
                "E",
                "E_reengage_after_homing",
                lambda: self.move_to_depth(self.a.depth_mm),
                "E. after moving out again",
                lambda st: st["pid_enabled"] and not st["pid_zone_hold"] and not st["pid_fault"],
            ),
        ]
        for tag, key, action, label, judge in phases:
            self.sampler.clear()
            failed = None
            try:
                action()
            except RuntimeError as e:  # the guard raises on a firmware fault or a host-side deviation trip
                failed = str(e)
            rows = self.sampler.snapshot()
            path = dump(tag, rows)
            st = flags(label)
            self.log(f"   transitions during the phase: {transitions(rows)}  (trace {os.path.basename(path)})")
            if failed:
                # where did the deviation run away? the last 12 samples before the fault flag tell
                fault_rows = [r for r in rows if r[4] & (1 << _def.ENC_FLAG.PID_FAULT)]
                if fault_rows:
                    k = rows.index(fault_rows[0])
                    for t, x, e, d, fl in rows[max(0, k - 12) : k + 2]:
                        self.log(
                            f"      t={t:7.3f}s  z={usteps_to_depth(x):.3f} mm  enc={usteps_to_depth(e):.3f} mm  "
                            f"dev={d / USTEPS_PER_MM * 1000:+.1f} um  engaged={int(bool(fl & 2))} hold={int(bool(fl & 8))} fault={int(bool(fl & 4))}"
                        )
                self.log(f"   phase {tag} ABORTED: {failed}")
                verdict[key] = False
                break
            verdict[key] = bool(judge(st))

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
        amax_cap = (2**22 - 1) / USTEPS_PER_MM
        self.log(f"AMAX register ceiling at {MICROSTEPS} usteps/FS: {amax_cap:.0f} mm/s2")
        m = self.mcu
        levels = []
        for accel in self.a.accel_list:
            if accel > amax_cap:
                self.log(f"skipping {accel} mm/s2: above the register ceiling ({amax_cap:.0f})")
                continue
            m.set_max_velocity_acceleration(AXIS.Z, self.a.vmax, accel)
            self.wait()
            self.settle(0.5)
            st0 = m.get_encoder_state()
            off0 = self._dev32_usteps(st0)
            wall0 = time.time()
            acks_100 = []
            acks_1000 = []
            try:
                for _ in range(self.a.accel_reps):
                    self.move_to_depth(self.a.depth_mm + 0.1)
                    acks_100.append(self.last_cmd_to_ack_s)
                    self.settle(0.15)
                    self.move_to_depth(self.a.depth_mm)
                    acks_100.append(self.last_cmd_to_ack_s)
                    self.settle(0.15)
                for _ in range(2):
                    self.move_to_depth(self.a.depth_mm + 1.0)
                    acks_1000.append(self.last_cmd_to_ack_s)
                    self.settle(0.2)
                    self.move_to_depth(self.a.depth_mm)
                    acks_1000.append(self.last_cmd_to_ack_s)
                    self.settle(0.2)
            except Exception as e:  # noqa: BLE001
                self.log(f"accel {accel}: aborted: {e}")
                levels.append({"accel": accel, "error": str(e)})
                break
            self.settle(0.5)
            st1 = m.get_encoder_state()
            off1 = self._dev32_usteps(st1)
            lost_um = (off1 - off0) / USTEPS_PER_MM * 1000.0
            t100 = sorted(acks_100)[len(acks_100) // 2] if acks_100 else float("nan")
            t1000 = sorted(acks_1000)[len(acks_1000) // 2] if acks_1000 else float("nan")
            # Trapezoid timing after subtracting the fixed command+report overhead (--ack-overhead-ms):
            # acceleration-limited (a*d <= v^2): t = 2*sqrt(d/a)  ->  a = 4d/t^2
            # velocity-limited  (a*d  > v^2): t = d/v + v/a     ->  a = v/(t - d/v)
            d_mm = 0.1
            v = self.a.vmax
            t_mv = t100 - self.a.ack_overhead_ms / 1000.0
            if t_mv <= 0:
                a_eff = float("nan")
            elif accel * d_mm <= v * v:
                a_eff = 4 * d_mm / (t_mv**2)
            else:
                a_eff = v / (t_mv - d_mm / v) if t_mv > d_mm / v else float("nan")
            row = {
                "accel": accel,
                "ack_100um_median_s": t100,
                "ack_1mm_median_s": t1000,
                "implied_accel_mm_s2": a_eff,
                "lost_um": lost_um,
                "off_before": off0,
                "off_after": off1,
                "wall_start": wall0,
                "wall_end": time.time(),
            }
            levels.append(row)
            self.log(
                f"accel {accel:4.0f} mm/s2: 100 um ack {t100 * 1000:5.1f} ms (implied {a_eff:5.0f} mm/s2), "
                f"1 mm ack {t1000 * 1000:5.1f} ms, encoder offset change {lost_um:+.2f} um"
            )
            if abs(lost_um) > self.a.lost_step_um:
                self.log(f"STOP: {lost_um:+.2f} um of position lost at {accel} mm/s2 (limit {self.a.lost_step_um} um)")
                break
        self.summary["results"].append(
            {
                "phase": "accelsweep",
                "vmax": self.a.vmax,
                "microsteps": MICROSTEPS,
                "ramp": self.a.ramp,
                "amax_register_cap": amax_cap,
                "levels": levels,
            }
        )
        good = [l for l in levels if "error" not in l and abs(l["lost_um"]) <= self.a.lost_step_um]
        if good:
            self.log(
                f"highest acceleration with no lost steps: {good[-1]['accel']:.0f} mm/s2 "
                f"(100 um in {good[-1]['ack_100um_median_s'] * 1000:.0f} ms command-to-ack)"
            )
        self.restore_velocity()

    def velsweep(self):
        """Open loop: for each velocity in --vel-list (at --accel), --vel-reps excursions of --excursion-mm up
        from --depth-mm and back. Per level: lost steps (change of ENC_POS - XACTUAL at rest), command-to-ack,
        and from the 250 Hz trace the in-motion encoder lag and the cruise-phase encoder velocity ripple:
        a stage or motor resonance shows as a speed band where the ripple grows, the lag jumps, or the
        encoder stops while the counter runs (stall samples). Wall-clock stamps per level for the mic."""
        m = self.mcu
        levels = []
        d_mm = self.a.excursion_mm
        if self.a.closed:
            self.engage_loop()
            self.log("velsweep with the loop ENGAGED during the moves (a fault at a level ends the sweep)")
        for v in self.a.vel_list:
            m.set_max_velocity_acceleration(AXIS.Z, v, self.a.accel)
            self.wait()
            self.settle(0.5)
            off0 = self._dev32_usteps(m.get_encoder_state())
            wall0 = time.time()
            acks = []
            lag_max = 0.0
            ripple = []
            stall_samples = 0
            cruise_samples = 0
            lag_cruise = []
            all_rows = []
            try:
                for _ in range(self.a.vel_reps):
                    for target in (self.a.depth_mm + d_mm, self.a.depth_mm):
                        self.sampler.clear()
                        try:
                            self.move_to_depth(target)
                            acks.append(self.last_cmd_to_ack_s)
                        finally:
                            all_rows.extend(self.sampler.snapshot())
                            self._write_rows(f"velsweep_v{v:g}.csv", all_rows)
                        rows = all_rows[-len(self.sampler.snapshot()) :] if False else self.sampler.snapshot()
                        # counter and encoder velocities between successive samples (>= 8 ms apart)
                        for (ta, xa, ea, da, _), (tb, xb, eb, db, _) in zip(rows, rows[1:]):
                            dt = tb - ta
                            if dt < 0.008:
                                continue
                            vx = abs(xb - xa) / dt / USTEPS_PER_MM
                            ve = abs(eb - ea) / dt / USTEPS_PER_MM
                            lag_max = max(lag_max, abs(db) / USTEPS_PER_MM * 1000.0)
                            if vx > 0.8 * v:  # cruise
                                cruise_samples += 1
                                ripple.append(ve - vx)
                                lag_cruise.append(db / USTEPS_PER_MM * 1000.0)
                                if ve < 0.2 * vx:
                                    stall_samples += 1
                        self.settle(0.25)
            except Exception as e:  # noqa: BLE001
                self.log(f"vmax {v}: aborted: {e}")
                levels.append({"vmax": v, "error": str(e), "wall_start": wall0, "wall_end": time.time()})
                break
            self.settle(0.5)
            off1 = self._dev32_usteps(m.get_encoder_state())
            lost_um = (off1 - off0) / USTEPS_PER_MM * 1000.0
            import statistics

            row = {
                "vmax": v,
                "accel": self.a.accel,
                "excursion_mm": d_mm,
                "reps": self.a.vel_reps,
                "ack_median_s": statistics.median(acks) if acks else float("nan"),
                "model_s": model_move_s(d_mm, v, self.a.accel) if "model_move_s" in globals() else float("nan"),
                "lost_um": lost_um,
                "lag_max_um": lag_max,
                "lag_cruise_mean_um": statistics.mean(lag_cruise) if lag_cruise else float("nan"),
                "cruise_ripple_std_mm_s": statistics.pstdev(ripple) if len(ripple) > 1 else float("nan"),
                "cruise_samples": cruise_samples,
                "stall_samples": stall_samples,
                "wall_start": wall0,
                "wall_end": time.time(),
            }
            levels.append(row)
            self.log(
                f"vmax {v:4.1f} mm/s: {d_mm:g} mm ack {row['ack_median_s'] * 1000:5.0f} ms; lost {lost_um:+.2f} um; "
                f"in-motion lag mean {row['lag_cruise_mean_um']:+.1f} um (max |{lag_max:.1f}|); cruise ripple std {row['cruise_ripple_std_mm_s']:.2f} mm/s "
                f"over {cruise_samples} samples; stall samples {stall_samples}"
            )
            if abs(lost_um) > self.a.lost_step_um:
                self.log(f"STOP: {lost_um:+.2f} um lost at {v} mm/s (limit {self.a.lost_step_um} um)")
                break
        self.summary["results"].append(
            {
                "phase": "velsweep",
                "accel": self.a.accel,
                "microsteps": MICROSTEPS,
                "ramp": self.a.ramp,
                "levels": levels,
            }
        )
        good = [l for l in levels if "error" not in l and abs(l["lost_um"]) <= self.a.lost_step_um]
        if good:
            self.log(f"highest velocity with no lost steps: {good[-1]['vmax']:.1f} mm/s")
        self.restore_velocity()

    def engageprobe(self):
        """Is the loop engaged while the axis moves slowly? Engage, then move --excursion-mm up and back at each
        velocity in --vel-list (set VMAX below and above --open-above to see both regimes) and count the status
        samples taken in motion that carry the PID_ENABLED flag. Firmware with SET_PID_OPEN_ABOVE keeps the
        loop engaged below the threshold and opens it above; rest-only firmware opens it for every move."""
        m = self.mcu
        self.engage_loop()
        try:
            for v in self.a.vel_list:
                m.set_max_velocity_acceleration(AXIS.Z, v, self.a.accel)
                self.wait()
                self.settle(0.3)
                moving_eng = moving_tot = 0
                trans = []
                last = None
                for target in (self.a.depth_mm + self.a.excursion_mm, self.a.depth_mm):
                    self.sampler.clear()
                    self.move_to_depth(target)
                    rows = self.sampler.snapshot()
                    for (ta, xa, ea, da, fa), (tb, xb, eb, db, fb) in zip(rows, rows[1:]):
                        if xb == xa:
                            continue  # at rest: not counted
                        moving_tot += 1
                        eng = bool(fb & (1 << _def.ENC_FLAG.PID_ENABLED))
                        moving_eng += eng
                        key = (eng, bool(fb & (1 << _def.ENC_FLAG.PID_ZONE)))
                        if key != last:
                            trans.append(
                                (round(usteps_to_depth(xb), 3), "engaged" if eng else ("held" if key[1] else "off"))
                            )
                            last = key
                    self.settle(0.3)
                frac = moving_eng / moving_tot if moving_tot else float("nan")
                verdict = "ENGAGED in flight" if frac > 0.9 else ("OPEN in flight" if frac < 0.1 else "mixed")
                self.log(
                    f"vmax {v:4.2f} mm/s: {moving_eng}/{moving_tot} in-motion samples with the loop engaged -> {verdict}; "
                    f"transitions {trans[:8]}"
                )
                self.summary["results"].append(
                    {
                        "phase": "engageprobe",
                        "vmax": v,
                        "moving_samples": moving_tot,
                        "engaged_samples": moving_eng,
                        "transitions": trans[:20],
                    }
                )
        finally:
            self.loop_off()
            self.restore_velocity()

    def _write_rows(self, name, rows):
        """Sampler rows (t, XACTUAL, ENC_POS, deviation, flags) as CSV in the output folder."""
        import csv

        with open(os.path.join(self.a.out, name), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "extension_mm", "enc_mm", "dev_um", "engaged", "zone_hold", "fault"])
            for t, x, e, d, fl in rows:
                w.writerow(
                    [
                        f"{t:.3f}",
                        f"{usteps_to_depth(x):.5f}",
                        f"{usteps_to_depth(e):.5f}",
                        f"{d / USTEPS_PER_MM * 1000:.2f}",
                        int(bool(fl & (1 << _def.ENC_FLAG.PID_ENABLED))),
                        int(bool(fl & (1 << _def.ENC_FLAG.PID_ZONE))),
                        int(bool(fl & (1 << _def.ENC_FLAG.PID_FAULT))),
                    ]
                )

    def residual(self):
        """Open-loop residual per direction: ENC_POS - XACTUAL at rest after a move, in um, for each step size in
        --residual-steps-um, --residual-reps moves up (deeper) then the same number back down. Reports mean and std per
        direction and size, in the firmware's sign convention (positive direction = counter increasing). The loop
        stays off. Measured 2026-09-08: the offset is a static frame offset, the per-move change is at the noise floor.
        """
        m = self.mcu
        out = []
        for step_um in self.a.residual_steps_um:
            d = step_um / 1000.0
            per_dir = {"+": [], "-": []}
            delta = {"+": [], "-": []}  # change of the offset caused by the move itself
            self.settle(0.3)
            prev = self._dev32_usteps(m.get_encoder_state()) / USTEPS_PER_MM * 1000.0
            for k in range(self.a.residual_reps):
                self.move_to_depth(self.a.depth_mm + (k + 1) * d)
                self.settle(0.15)
                st = m.get_encoder_state()
                # deeper = counter decreasing on this Z (sign -1): direction "-" in counter terms
                cur = self._dev32_usteps(st) / USTEPS_PER_MM * 1000.0
                per_dir["-"].append(cur)
                delta["-"].append(cur - prev)
                prev = cur
            for k in range(self.a.residual_reps - 1, -1, -1):
                self.move_to_depth(self.a.depth_mm + k * d)
                self.settle(0.15)
                st = m.get_encoder_state()
                cur = self._dev32_usteps(st) / USTEPS_PER_MM * 1000.0
                per_dir["+"].append(cur)
                delta["+"].append(cur - prev)
                prev = cur
            import statistics

            row = {"phase": "residual", "step_um": step_um, "reps": self.a.residual_reps}
            for key, name in (("-", "neg"), ("+", "pos")):
                v = per_dir[key]
                row[f"{name}_mean_um"] = statistics.mean(v)
                row[f"{name}_std_um"] = statistics.pstdev(v) if len(v) > 1 else 0.0
                row[f"{name}_min_um"] = min(v)
                row[f"{name}_max_um"] = max(v)
                dv = delta[key]
                row[f"{name}_delta_mean_um"] = statistics.mean(dv)
                row[f"{name}_delta_std_um"] = statistics.pstdev(dv) if len(dv) > 1 else 0.0
                row[f"{name}_values_um"] = [round(x, 3) for x in v]
            out.append(row)
            self.log(
                f"  per-move change of the offset (what a pre-compensation could remove): deeper {row['neg_delta_mean_um']:+.2f} um std {row['neg_delta_std_um']:.2f}; "
                f"shallower {row['pos_delta_mean_um']:+.2f} um std {row['pos_delta_std_um']:.2f}; the rest of the offset is the static frame offset"
            )
            self.log(
                f"residual after {step_um:g} um moves (ENC - XACTUAL at rest, counter sign): "
                f"counter-decreasing (deeper) {row['neg_mean_um']:+.2f} um std {row['neg_std_um']:.2f} [{row['neg_min_um']:+.2f}, {row['neg_max_um']:+.2f}]; "
                f"counter-increasing (shallower) {row['pos_mean_um']:+.2f} um std {row['pos_std_um']:.2f} [{row['pos_min_um']:+.2f}, {row['pos_max_um']:+.2f}]"
            )
        self.summary["results"].extend(out)
        if out:
            self.log(
                "direction asymmetry of the offset (half the pos-neg difference, um): "
                f"{statistics.mean((r['pos_mean_um'] - r['neg_mean_um']) / 2 for r in out):+.2f}  "
                "(the common part is the static frame offset the loop nulls once at rest)"
            )

    def engage_loop(self):
        m = self.mcu
        self.settle(0.3)
        dev0 = self._dev32_usteps(m.get_encoder_state()) / USTEPS_PER_MM * 1000
        if abs(dev0) > self.a.max_dev_um / 4:
            raise RuntimeError(f"not closing the loop: error already {dev0:+.1f} um before enable")
        m.set_pid_arguments(AXIS.Z, self.a.p, self.a.i, self.a.d)
        self.wait()
        m.turn_on_stage_pid(AXIS.Z)
        self.wait(5)
        self.loop_on = True
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
        acks = []
        errs = []
        encs = []
        try:
            for k in range(1, n + 1):
                ack_s, st = self._move_and_read(self.a.depth_mm + k * du, settle_s=0.15)
                acks.append(ack_s)
                errs.append(self._dev32_usteps(st) / USTEPS_PER_MM * 1000)
                encs.append(usteps_to_depth(st["encoder_pos"]))
            self.move_to_depth(self.a.depth_mm)
            self.settle(0.3)
        finally:
            if closed:
                self.loop_off()
        # step-to-step encoder increments vs commanded
        inc = [(encs[i] - encs[i - 1]) * 1000 for i in range(1, len(encs))]
        acks_ms = sorted(a_ * 1000 for a_ in acks)
        res = {
            "phase": "stack",
            "label": label,
            "closed": closed,
            "n": n,
            "step_um": self.a.stack_um,
            "ack_ms_median": acks_ms[len(acks_ms) // 2],
            "ack_ms_max": acks_ms[-1],
            "err_um_mean": sum(errs) / len(errs),
            "err_um_max_abs": max(abs(e) for e in errs),
            "enc_increment_um_mean": (sum(inc) / len(inc)) if inc else float("nan"),
            "enc_increment_um_min": min(inc) if inc else float("nan"),
            "enc_increment_um_max": max(inc) if inc else float("nan"),
            "wall_start": wall0,
            "wall_end": time.time(),
        }
        self.summary["results"].append(res)
        self.log(
            f"{label}: ack median {res['ack_ms_median']:.0f} ms (max {res['ack_ms_max']:.0f}); encoder error mean {res['err_um_mean']:+.2f} um, "
            f"max |err| {res['err_um_max_abs']:.2f} um; encoder step increments mean {res['enc_increment_um_mean']:.3f} um "
            f"(min {res['enc_increment_um_min']:.3f}, max {res['enc_increment_um_max']:.3f}) for commanded {self.a.stack_um:g} um"
        )
        return res

    # ---------------------------------------------------------------- ackprobe
    def _ack_tolerance_um(self):
        """The tolerance an 'is it there yet' question is asked against: --tol-um, or the firmware default
        (two encoder counts) when the tool did not send one."""
        return self.a.tol_um if self.a.tol_um > 0 else 2 * ENC_STEP_MM * 1000.0

    def _read_z_fault_cause(self, restore_reporting=True):
        """Why the firmware opened the Z loop, as a PID_FAULT_CAUSE, or NONE if it will not say.

        The causes ride in status bytes 19-21 only while encoder reporting is OFF; with it on (which is
        how this tool runs) those bytes carry the reported axis's flags and clipped deviation instead.
        So drop reporting for a few status packets, read, and put it back.

        Must be called before anything acknowledges the fault: DISABLE_STAGE_PID, ENABLE_STAGE_PID and
        CONFIGURE_STAGE_PID all clear the cause together with the fault bit, so loop_off() erases the
        answer. Best effort - a failure here must not replace the fault as the reported problem.
        """
        try:
            self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.OFF)
            self.wait(5)
            time.sleep(0.03)  # ~3 packets at the 10 ms status cadence
            self.last_fault_cause = self.mcu.pid_fault_cause(AXIS.Z)
            return self.last_fault_cause
        except Exception as e:  # noqa: BLE001
            self.log(f"could not read the fault cause: {e}")
            return PID_FAULT_CAUSE.NONE
        finally:
            if restore_reporting:
                try:
                    self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.ENC_IN_THETA)
                    self.wait(5)
                except Exception as e:  # noqa: BLE001
                    self.log(f"could not restore encoder reporting: {e}")

    def _ackprobe_step(self, rep, closed, step_um, tol_usteps):
        """One measured move: error and loop state AT the acknowledgment, then every 5 ms through the
        exposure window. The at-ack read is the window's first sample, at t = 0.

        Returns (row, abort). A fault is the event this action exists to capture, so it ends the
        sampling but not the row: the caller gets the filled-in row first and raises `abort` after it
        has been written. Raising from in here would have dropped exactly the move that tripped, and
        left pid_fault_at_ack and faults_in_window unable to be anything but 0 in the CSV. A move the
        controller aborted is the same event arriving as an exception instead of a flag, and is kept
        the same way.
        """
        t_cmd = time.time()
        abort = None
        self.last_fault_cause = PID_FAULT_CAUSE.NONE
        cause = PID_FAULT_CAUSE.NONE
        try:
            ack_s, st = self._move_and_read(self.a.depth_mm + step_um / 1000.0)  # no settle: read follows the ack
        except RuntimeError as e:
            # A CMD_EXECUTION_ERROR rides on the same packet as the fault bit, so a move the
            # controller aborted IS a faulted move, not a lost sample. move_to_depth timed the error
            # ack and read the cause before acknowledging the abort (which clears it); everything
            # below then fills in the row exactly as a fault flagged at the ack does, and the window
            # loop is skipped because `abort` is already set.
            abort = e
            ack_s = self.last_cmd_to_ack_s
            cause = self.last_fault_cause
            st = self.mcu.get_encoder_state()
        t_ack = time.time()
        # Reading the cause drops encoder reporting for a few packets and restoring it is best
        # effort, so an aborted move can come back with no reading at all. nan says "not measured"
        # instead of carrying the previous move's error into this row.
        dev = self._dev32_usteps(st) if st["dev32"] is not None else float("nan")
        enabled_at_ack = bool(st["pid_enabled"])
        fault_at_ack = bool(st["pid_fault"]) or cause != PID_FAULT_CAUSE.NONE
        engaged_throughout = enabled_at_ack
        faults = int(fault_at_ack)
        max_abs = abs(dev)
        t_tol = 0.0 if abs(dev) <= tol_usteps else float("nan")
        samples = 1
        if abort is None and fault_at_ack:
            cause = self._read_z_fault_cause()
            self.loop_off()
            abort = RuntimeError(f"firmware opened the loop (PID_FAULT) at the acknowledgment of a {step_um:g} um move")
        while abort is None and time.time() - t_ack < self.a.exposure_ms / 1000.0:
            time.sleep(0.005)  # twice the status cadence; the stream itself is 10 ms
            sn = self.mcu.get_encoder_state()
            d = self._dev32_usteps(sn)
            samples += 1
            max_abs = max(max_abs, abs(d))
            engaged_throughout = engaged_throughout and bool(sn["pid_enabled"])
            faults += int(bool(sn["pid_fault"]))
            if math.isnan(t_tol) and abs(d) <= tol_usteps:
                t_tol = (time.time() - t_ack) * 1000.0
            if sn["pid_fault"]:
                # read the cause before loop_off(): DISABLE acknowledges the fault and clears it
                cause = self._read_z_fault_cause()
                self.loop_off()
                abort = RuntimeError(
                    f"firmware opened the loop (PID_FAULT) {(time.time() - t_ack) * 1000.0:.0f} ms into the "
                    f"exposure window of a {step_um:g} um move"
                )
                break
            try:
                self.guard()  # same host-side abort the other actions use; the sample above is recorded
            except RuntimeError as e:
                abort = e
                # guard reads the state again, so it can be the one that catches a fault; it stashes
                # the cause on the way past because its own loop_off() erases it
                cause = self.last_fault_cause
        if abort is not None:
            self.log(f"ackprobe: {abort} - keeping the row; cause: {self._cause_text(cause)}")
        row = {
            "rep": rep,
            "mode": "closed" if closed else "open",
            "step_um": step_um,
            "t_cmd": t_cmd,
            "ack_ms": ack_s * 1000.0,
            "dev32_at_ack_usteps": dev,
            "dev32_at_ack_um": dev / USTEPS_PER_MM * 1000.0,
            "pid_enabled_at_ack": enabled_at_ack,
            "pid_fault_at_ack": fault_at_ack,
            "max_abs_dev_in_window_um": max_abs / USTEPS_PER_MM * 1000.0,
            "time_to_within_tolerance_ms": t_tol,
            "engaged_throughout": engaged_throughout,
            "window_samples": samples,
            "faults_in_window": faults,
            "fault_cause": cause,
            "fault_cause_name": self._cause_text(cause),
        }
        return row, abort

    @staticmethod
    def _cause_text(cause):
        if cause == PID_FAULT_CAUSE.NONE:
            return ""
        return PID_FAULT_CAUSE.NAMES.get(cause, f"unknown cause {cause}")

    def ackprobe(self):
        """Readiness for an exposure at the acknowledgment, not just the acknowledgment.

        For every size in --ack-steps-um, --ack-reps moves up from the working extension, closed loop and
        open loop: the 32-bit encoder-minus-counter error and the loop state read at the ack itself, then
        every 5 ms for --exposure-ms. The two ladders alternate which runs first (odd reps closed first).
        The return to the working extension between sizes is not measured.

        The host only sees the controller through the 10 ms status stream; sub-10 ms behaviour is not
        resolvable from here, whatever the ack numbers look like.

        A fault ends the run, but the move it happened on is kept: it is appended with its cause and the
        CSV and summary are written before the exception leaves.
        """
        tol_um = self._ack_tolerance_um()
        tol_usteps = tol_um * USTEPS_PER_MM / 1000.0
        self.log(
            f"ackprobe: {', '.join(f'{v:g}' for v in self.a.ack_steps_um)} um x {self.a.ack_reps} reps, closed and "
            f"open, {self.a.exposure_ms:g} ms window sampled every 5 ms, tolerance {tol_um:.2f} um"
        )
        rows = []
        try:
            for rep in range(1, self.a.ack_reps + 1):
                for closed in (True, False) if rep % 2 else (False, True):
                    if closed:
                        self.engage_loop()
                    try:
                        for step_um in self.a.ack_steps_um:
                            row, abort = self._ackprobe_step(rep, closed, step_um, tol_usteps)
                            rows.append(row)  # the faulted move is a result, not a lost sample
                            if abort is not None:
                                raise abort
                            self.move_to_depth(self.a.depth_mm)
                            self.settle(0.15)
                    finally:
                        if closed:
                            self.loop_off()
        finally:
            # a guard abort mid-run still leaves the rows that were taken, and they are the interesting ones
            if rows:
                self._ackprobe_report(rows, tol_um)

    def _ackprobe_preamble(self, tol_um):
        """What a result set has to carry to be re-readable later: when, which host code, which firmware,
        which loop settings were actually in force, and what the host can and cannot see."""
        fw = tuple(self.mcu.firmware_version) if self.mcu is not None else (0, 0)
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host_git": host_git_hash(),
            "firmware": f"{fw[0]}.{fw[1]}",
            "pid_p": self.a.p,
            "pid_i": self.a.i,
            "pid_d": self.a.d,
            "correction_vmax_mm_s": self.a.corr_vmax,
            "watchdog_um": self.a.max_dev_um,
            "home_zone_um": self.a.zone_um,
            "tolerance_um": tol_um,
            "open_above_mm_s": self.a.open_above,
            "completion_window_um": self.a.window_um,
            "depth_mm": self.a.depth_mm,
            "vmax_mm_s": self.a.vmax,
            "accel_mm_s2": self.a.accel,
            "ramp": self.a.ramp,
            "microsteps_per_full_step": MICROSTEPS,
            "usteps_per_mm": round(USTEPS_PER_MM, 3),
            "steps_um": list(self.a.ack_steps_um),
            "reps": self.a.ack_reps,
            "exposure_ms": self.a.exposure_ms,
            "window_sample_interval_ms": 5.0,
            "error_field": "ENC_POS - XACTUAL from the 32-bit fields (not the int16 ENC_POS_DEV, which clips at +-192 um)",
            "telemetry": "10 ms status stream; sub-10 ms behaviour is not resolvable from the host",
            "fault_cause_field": (
                "PID_FAULT_* from status bytes 19-21, read by dropping encoder reporting for ~30 ms at the "
                "fault and before any DISABLE, which would clear it"
            ),
        }

    def _ackprobe_summary(self, rows):
        out = []
        for step_um in self.a.ack_steps_um:
            for mode in ("closed", "open"):
                sel = [r for r in rows if r["step_um"] == step_um and r["mode"] == mode]
                if not sel:
                    continue
                acks = [r["ack_ms"] for r in sel]
                at_ack = [abs(r["dev32_at_ack_um"]) for r in sel]
                in_win = [r["max_abs_dev_in_window_um"] for r in sel]
                reached = [
                    r["time_to_within_tolerance_ms"] for r in sel if not math.isnan(r["time_to_within_tolerance_ms"])
                ]
                out.append(
                    {
                        "phase": "ackprobe",
                        "step_um": step_um,
                        "mode": mode,
                        "n": len(sel),
                        "acks_with_loop_open": sum(1 for r in sel if not r["pid_enabled_at_ack"]),
                        "err_at_ack_um_median": pct(at_ack, 0.5),
                        "err_at_ack_um_p95": pct(at_ack, 0.95),
                        "err_at_ack_um_max": max(at_ack),
                        "window_max_err_um_median": pct(in_win, 0.5),
                        "window_max_err_um_p95": pct(in_win, 0.95),
                        "window_max_err_um_max": max(in_win),
                        "ack_ms_p50": pct(acks, 0.5),
                        "ack_ms_p95": pct(acks, 0.95),
                        "ack_ms_max": max(acks),
                        "faults": sum(r["faults_in_window"] for r in sel),
                        "fault_causes": sorted({r["fault_cause_name"] for r in sel if r["fault_cause"]}),
                        "time_to_tolerance_ms_median": pct(reached, 0.5),
                        "never_within_tolerance": len(sel) - len(reached),
                    }
                )
        return out

    _ACKPROBE_COLUMNS = [
        "rep",
        "mode",
        "step_um",
        "t_cmd_epoch_s",
        "ack_ms",
        "dev32_at_ack_usteps",
        "dev32_at_ack_um",
        "pid_enabled_at_ack",
        "pid_fault_at_ack",
        "max_abs_dev_in_window_um",
        "time_to_within_tolerance_ms",
        "engaged_throughout",
        "window_samples",
        "faults_in_window",
        "fault_cause",
        "fault_cause_name",
    ]

    def _ackprobe_report(self, rows, tol_um):
        pre = self._ackprobe_preamble(tol_um)
        path = os.path.join(self.out, "ackprobe.csv")
        with open(path, "w", newline="") as f:
            for k, v in pre.items():
                f.write(f"# {k}: {v}\n")
            w = csv.writer(f)
            w.writerow(self._ACKPROBE_COLUMNS)
            for r in rows:
                w.writerow(
                    [
                        r["rep"],
                        r["mode"],
                        f"{r['step_um']:g}",
                        f"{r['t_cmd']:.6f}",
                        f"{r['ack_ms']:.1f}",
                        r["dev32_at_ack_usteps"],
                        f"{r['dev32_at_ack_um']:.3f}",
                        int(r["pid_enabled_at_ack"]),
                        int(r["pid_fault_at_ack"]),
                        f"{r['max_abs_dev_in_window_um']:.3f}",
                        f"{r['time_to_within_tolerance_ms']:.1f}",
                        int(r["engaged_throughout"]),
                        r["window_samples"],
                        r["faults_in_window"],
                        r["fault_cause"],
                        r["fault_cause_name"],
                    ]
                )
        summary = self._ackprobe_summary(rows)
        self.summary["results"].extend(summary)
        json_path = os.path.join(self.out, "ackprobe_summary.json")
        with open(json_path, "w") as f:
            json.dump({"preamble": pre, "summary": summary}, f, indent=2, default=str)
        self.log(f"ackprobe: {len(rows)} moves -> {path}, summary -> {json_path}")
        self.log(
            f"tolerance {tol_um:.2f} um; telemetry: 10 ms status stream; sub-10 ms behaviour is not resolvable from the host"
        )
        self.log(
            f"{'step um':>8} {'mode':>6} {'n':>4} {'open@ack':>9} {'|err|@ack med/p95/max um':>26} "
            f"{'in-window max med/p95/max um':>30} {'ack ms p50/p95/max':>20} {'faults':>7} {'to tol ms':>10}"
        )
        for r in summary:
            self.log(
                f"{r['step_um']:>8g} {r['mode']:>6} {r['n']:>4} {r['acks_with_loop_open']:>9} "
                f"{r['err_at_ack_um_median']:>8.2f} {r['err_at_ack_um_p95']:>8.2f} {r['err_at_ack_um_max']:>8.2f} "
                f"{r['window_max_err_um_median']:>9.2f} {r['window_max_err_um_p95']:>9.2f} {r['window_max_err_um_max']:>9.2f} "
                f"{r['ack_ms_p50']:>6.0f} {r['ack_ms_p95']:>6.0f} {r['ack_ms_max']:>6.0f} {r['faults']:>7} "
                f"{r['time_to_tolerance_ms_median']:>7.0f}"
                + (f" ({r['never_within_tolerance']} never inside)" if r["never_within_tolerance"] else "")
            )

    def hold(self):
        """Closed-loop hold at the working extension for --hold-s seconds (mic records hunting), then loop off.
        With --hold-open-s N an open-loop hold of N s at the same position is recorded first, so the
        microphone has an ambient control window inside the same recording (the only thing that changes
        at the boundary is the loop engaging)."""
        if self.a.hold_open_s > 0:
            wall_o = time.time()
            devs_o = []
            t0 = time.time()
            while time.time() - t0 < self.a.hold_open_s:
                self.guard()
                devs_o.append(self._dev32_usteps(self.mcu.get_encoder_state()))
                time.sleep(0.01)
            d_o = [d / USTEPS_PER_MM * 1000 for d in devs_o]
            self.summary["results"].append(
                {
                    "phase": "hold_open",
                    "label": f"hold_open_{self.a.hold_open_s:g}s",
                    "seconds": self.a.hold_open_s,
                    "err_um_mean": sum(d_o) / len(d_o),
                    "err_um_max_abs": max(abs(v) for v in d_o),
                    "wall_start": wall_o,
                    "wall_end": time.time(),
                }
            )
            self.log(f"hold {self.a.hold_open_s:g} s OPEN loop (control): error mean {sum(d_o) / len(d_o):+.3f} um")
        self.engage_loop()
        wall0 = time.time()
        devs = []
        try:
            t0 = time.time()
            while time.time() - t0 < self.a.hold_s:
                self.guard()
                devs.append(self._dev32_usteps(self.mcu.get_encoder_state()))
                time.sleep(0.01)
        finally:
            self.loop_off()
        import statistics

        d_um = [d / USTEPS_PER_MM * 1000 for d in devs]
        cross = sum(1 for a_, b_ in zip(d_um, d_um[1:]) if (a_ < 0) != (b_ < 0))
        res = {
            "phase": "hold",
            "label": f"hold_closed_{self.a.hold_s:g}s",
            "seconds": self.a.hold_s,
            "err_um_mean": statistics.mean(d_um),
            "err_um_std": statistics.pstdev(d_um),
            "err_um_max_abs": max(abs(v) for v in d_um),
            "zero_crossings_per_s": cross / self.a.hold_s,
            "wall_start": wall0,
            "wall_end": time.time(),
        }
        self.summary["results"].append(res)
        self.log(
            f"hold {self.a.hold_s:g} s closed loop: error mean {res['err_um_mean']:+.3f} um, std {res['err_um_std']:.3f} um, "
            f"max |err| {res['err_um_max_abs']:.2f} um, zero crossings {res['zero_crossings_per_s']:.1f}/s"
        )

    # ---------------------------------------------------------------- main
    def run(self):
        try:
            self.connect()
            self.configure_z()
            self.configure_encoder(
                self.flip
            )  # before homing: the homing zero must be taken under the final encoder scale
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
            if self.a.action == "velsweep":
                self.velsweep()
                return
            if self.a.action == "engageprobe":
                self.engageprobe()
                return
            if self.a.action == "residual":
                self.residual()
                return
            if self.a.action == "stack":
                self.stack(closed=False)
                self.stack(closed=True)
                return
            if self.a.action == "ackprobe":
                self.ackprobe()
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
    ap.add_argument(
        "action",
        choices=[
            "check",
            "baseline",
            "step",
            "sweep",
            "zonemap",
            "zonetest",
            "accelsweep",
            "velsweep",
            "engageprobe",
            "residual",
            "stack",
            "ackprobe",
            "hold",
        ],
    )
    ap.add_argument("--residual-steps-um", type=float, nargs="+", default=[1.0, 10.0, 100.0])
    ap.add_argument("--residual-reps", type=int, default=10)
    ap.add_argument("--vel-list", type=float, nargs="+", default=[1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
    ap.add_argument("--vel-reps", type=int, default=3)
    ap.add_argument("--excursion-mm", type=float, default=2.0, help="velsweep excursion up from --depth-mm")
    ap.add_argument("--closed", action="store_true", help="velsweep: keep the closed loop engaged during the moves")
    ap.add_argument("--depth-mm", type=float, default=2.5, help="working depth below the top switch")
    ap.add_argument("--depth-min", type=float, default=1.0)
    ap.add_argument("--depth-max", type=float, default=4.5)
    ap.add_argument("--step-um", type=float, default=100.0, help="excursion for baseline/step tests")
    ap.add_argument("--vmax", type=float, default=1.0, help="Z max velocity during the session, mm/s")
    ap.add_argument("--accel", type=float, default=20.0, help="Z acceleration during the session, mm/s2")
    ap.add_argument("--corr-vmax", type=float, default=0.3, help="closed-loop correction velocity clamp, mm/s")
    ap.add_argument("--max-dev-um", type=float, default=200.0, help="watchdog / guard limit on loop error")
    ap.add_argument("--settle-tol-um", type=float, default=0.5)
    ap.add_argument(
        "--max-crossings",
        type=float,
        default=5.0,
        help="tail zero-crossings/s above which a gain counts as oscillating",
    )
    ap.add_argument("--p", type=int, default=_def.PID_P_Z)
    ap.add_argument("--i", type=int, default=_def.PID_I_Z)
    ap.add_argument("--d", type=int, default=_def.PID_D_Z)
    ap.add_argument("--p-list", type=int, nargs="+", default=[1024, 2048, 4096, 8192, 16384])
    ap.add_argument("--zone-um", type=float, default=0.0, help="home exclusion zone sent to firmware (0 = none)")
    ap.add_argument(
        "--tol-um",
        type=float,
        default=0.0,
        help="closed-loop deadband and target-reached tolerance in um (0 = firmware default: 2 encoder counts)",
    )
    ap.add_argument(
        "--window-um", type=float, default=0.0, help="completion window sent to firmware in um (0 = exact target)"
    )
    ap.add_argument(
        "--open-above",
        type=float,
        default=0.0,
        help="ramp velocity (mm/s) above which the loop is opened during moves; 0 = rest-only, >= vmax = in-flight",
    )
    ap.add_argument(
        "--align-after-home",
        action="store_true",
        help="re-align the encoder frame to XACTUAL at --depth-mm after homing (stages with a decoupled gap above home)",
    )
    ap.add_argument("--accel-list", type=float, nargs="+", default=[100, 150, 200, 250, 300, 350, 390])
    ap.add_argument(
        "--ack-steps-um",
        type=float,
        nargs="+",
        default=[1.0, 10.0, 100.0],
        help="ackprobe step sizes, um",
    )
    ap.add_argument("--ack-reps", type=int, default=20, help="ackprobe repetitions per step size and mode")
    ap.add_argument(
        "--exposure-ms",
        type=float,
        default=100.0,
        help="ackprobe: window sampled after each ack, standing in for an exposure",
    )
    ap.add_argument("--stack-n", type=int, default=20)
    ap.add_argument("--stack-um", type=float, default=1.0)
    ap.add_argument("--hold-s", type=float, default=20.0)
    ap.add_argument(
        "--hold-open-s", type=float, default=0.0, help="open-loop control hold recorded before the closed-loop hold"
    )
    ap.add_argument("--accel-reps", type=int, default=5, help="100 um out-and-back repetitions per level")
    ap.add_argument(
        "--lost-step-um", type=float, default=1.0, help="encoder-vs-counter offset change that counts as lost steps"
    )
    ap.add_argument(
        "--ack-overhead-ms",
        type=float,
        default=7.0,
        help="fixed command+report overhead subtracted when inferring acceleration",
    )
    ap.add_argument("--zonemap-from", type=float, default=2.0, help="zonemap start extension, mm")
    ap.add_argument("--zonemap-step-um", type=float, default=50.0)
    ap.add_argument(
        "--microsteps",
        type=int,
        default=int(_def.MICROSTEPPING_DEFAULT_Z),
        help="Z microsteps per full step to configure (ini default %d; 256 was used for the first sessions)"
        % int(_def.MICROSTEPPING_DEFAULT_Z),
    )
    ap.add_argument(
        "--ramp",
        choices=["sshape", "trapezoid"],
        default="sshape",
        help="TMC4361A ramp profile for Z during the session",
    )
    ap.add_argument("--out", default="z_tune")
    args = ap.parse_args()
    set_microsteps(args.microsteps)
    if args.depth_max > HARD_CAP_DEPTH_MM:
        print(f"--depth-max capped at {HARD_CAP_DEPTH_MM} mm")
    ZTuner(args).run()


if __name__ == "__main__":
    main()
