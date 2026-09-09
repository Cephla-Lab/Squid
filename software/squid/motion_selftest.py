"""Motion self-test for the Z axis: one routine for the GUI dialog and the command line.

What an instrument needs after a Z stage or objectives change is verification, not tuning (the closed-loop
gains are the same on every stage measured so far: P 65535, I 0, D 0, clamp 1 mm/s, rest-only). The routine
runs, in order, on the controller connection it is given:

1. preflight   - firmware version, encoder present, loop settings the host configured
2. home        - home Z, park at the working depth
3. encoder     - open-loop 0.5 mm move: encoder-vs-counter scale and sign; frame offset after homing
4. gap map     - open loop, 50 um steps from 1 mm down to the switch and back: where the stage stops following
                 the actuator (stages whose actuator homes below the stage's stop); floor / zone / park values
5. lost steps  - open loop at the configured velocity and acceleration: 5 x 100 um out and back, 2 x 2 mm
6. closed loop - if the ini enables it: engage, 20 x 1 um stack (ack time, error), 100 um step, 5 s hold
7. restore     - loop back to its configured state, encoder reporting off, Z at the working depth

Every check yields a CheckResult; the report carries pass/fail per check and the ini values the measurements
recommend. All motion is open-loop except step 6, and the deviation watchdog the host configured stays armed.
Distances are in the stage's own frame (mm from home, positive away from the switch); the encoder error is
ENC_POS - XACTUAL in microsteps as the firmware reports it, converted to um with the axis scale.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from control._def import AXIS, ENCODER_REPORTING
from squid.config import AxisConfig


@dataclass
class CheckResult:
    name: str
    passed: Optional[bool]  # None: not applicable on this instrument
    summary: str
    values: Dict[str, float] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else ("FAIL" if self.passed is False else "n/a ")


@dataclass
class SelfTestReport:
    results: List[CheckResult] = field(default_factory=list)
    recommendations: Dict[str, str] = field(default_factory=dict)  # ini key -> value, from the measurements
    aborted: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.aborted is None and all(r.passed is not False for r in self.results)

    def text(self) -> str:
        lines = []
        for r in self.results:
            lines.append(f"{r.verdict}  {r.name}: {r.summary}")
        if self.aborted:
            lines.append(f"ABORTED: {self.aborted}")
        if self.recommendations:
            lines.append("")
            lines.append("ini values from these measurements:")
            for k, v in self.recommendations.items():
                lines.append(f"  {k} = {v}")
        lines.append("")
        lines.append("OVERALL: " + ("PASS" if self.passed else "FAIL"))
        return "\n".join(lines)


class SelfTestCancelled(Exception):
    pass


class ZMotionSelfTest:
    """Runs the checks against a connected Microcontroller for the Z axis described by `axis`.

    `log` receives one line per event; `cancel` is polled between moves and returns True to stop.
    The caller owns the connection and the stage configuration (the GUI has already sent it).
    """

    # A stepper loses sync in whole full steps (usually four at once); the open-loop residual and its
    # hysteresis are a few tenths of a micron. One full step (pitch / steps per rev) separates the two.
    # The loop holds the error inside its two-count deadband, but a single sample can catch a transient count
    # or two before the next correction (0.28 um once in 20 steps on the bench). 0.5 um separates a working
    # loop from a failing one without flagging that; open loop leaves 3-5 um on the same stage.
    CLOSED_ERROR_LIMIT_UM = 0.5
    CLOSED_ACK_LIMIT_MS = 200.0
    SCALE_TOLERANCE = 0.05

    def __init__(
        self,
        mcu,
        axis: AxisConfig,
        log: Callable[[str], None] = print,
        stage=None,
        cancel: Optional[Callable[[], bool]] = None,
        working_depth_mm: Optional[float] = None,
        gap_scan_from_mm: float = 1.0,
        gap_step_um: float = 50.0,
        stack_n: int = 20,
        hold_s: float = 5.0,
        settle_scale: float = 1.0,
    ):
        self.mcu = mcu
        self.axis = axis
        self.log = log
        # The GUI sends the ini's travel limits to the firmware at startup, and the TMC4361A hard-stops at
        # them: a move below the Z floor never reaches its target and the command stays in progress. The gap
        # map has to visit the switch (the gap lies below the floor by policy), so with a stage it opens the
        # floor for that check and restores it afterwards. Without a stage the limits are left as they are.
        self.stage = stage
        self._floor_opened = False
        self.cancel = cancel or (lambda: False)
        self.usteps_per_mm = abs(float(axis.convert_real_units_to_ustep(1.0)))
        lo, hi = float(axis.MIN_POSITION), float(axis.MAX_POSITION)
        if working_depth_mm is None:
            working_depth_mm = min(2.5, hi - 0.6)
        self.depth = max(lo + 0.5, min(working_depth_mm, hi - 0.6))
        self.gap_scan_from = min(gap_scan_from_mm, self.depth)
        self.gap_step_mm = gap_step_um / 1000.0
        self.stack_n = stack_n
        self.hold_s = hold_s
        self.settle_scale = settle_scale  # tests run the sequence without the physical settling waits
        self.pid = axis.PID
        self.loop_configured = bool(self.pid and self.pid.ENABLED)
        self.max_dev_um = float(self.pid.MAX_DEVIATION_UM) if self.pid and self.pid.MAX_DEVIATION_UM else 250.0
        self.lost_step_limit_um = float(axis.SCREW_PITCH) / float(axis.FULL_STEPS_PER_REV) * 1000.0
        self.report = SelfTestReport()
        self.encoder_ok = False
        self._last_ack_s = float("nan")

    # ------------------------------------------------------------------ helpers
    def _mm(self, usteps: float) -> float:
        return float(self.axis.convert_to_real_units(usteps))

    def _usteps(self, mm: float) -> int:
        return int(round(self.axis.convert_real_units_to_ustep(mm)))

    def _dev_um(self, dev_usteps: float) -> float:
        return dev_usteps / self.usteps_per_mm * 1000.0

    def _pos_mm(self) -> float:
        return self._mm(self.mcu.z_pos)

    def _enc(self):
        return self.mcu.get_encoder_state()

    def _wait(self, timeout_s: float = 30.0):
        self.mcu.wait_till_operation_is_completed(timeout_s)

    def _check_cancel(self):
        if self.cancel():
            raise SelfTestCancelled()

    def _guard(self, check_cancel: bool = True):
        if check_cancel:
            self._check_cancel()
        if not self.encoder_ok:
            return
        st = self._enc()
        if st["pid_fault"]:
            raise RuntimeError("the firmware watchdog opened the loop (PID_FAULT): the deviation exceeded the limit")
        if st["pid_enabled"] and abs(self._dev_um(st["deviation"])) > self.max_dev_um:
            self.mcu.turn_off_stage_pid(AXIS.Z)
            raise RuntimeError(f"loop error {self._dev_um(st['deviation']):+.0f} um exceeded {self.max_dev_um:.0f} um; loop opened")

    def _move(self, mm: float, timeout_s: float = 30.0, allow_below_floor: bool = False, check_cancel: bool = True) -> float:
        lo = 0.0 if allow_below_floor else float(self.axis.MIN_POSITION)
        if mm < lo - 1e-9 or mm > float(self.axis.MAX_POSITION) + 1e-9:
            raise RuntimeError(f"refusing Z target {mm:.3f} mm: outside {lo:.2f}..{self.axis.MAX_POSITION:.2f} mm")
        if check_cancel:
            self._check_cancel()
        t0 = time.time()
        self.mcu.move_z_to_usteps(self._usteps(mm))
        while self.mcu.is_busy():
            self._guard(check_cancel)
            if time.time() - t0 > timeout_s:
                raise TimeoutError("Z move did not complete")
            time.sleep(0.002)
        # the controller clears 'busy' on an abort too (execution error, ack timeout): that is not a completed move
        err = getattr(self.mcu, "last_command_aborted_error", None)
        if err is not None:
            raise RuntimeError(f"Z move aborted by the controller: {err}")
        self._last_ack_s = time.time() - t0
        return self._last_ack_s

    def _settle(self, seconds: float):
        seconds *= self.settle_scale
        t0 = time.time()
        self._guard()
        while time.time() - t0 < seconds:
            self._guard()
            time.sleep(0.01)

    def _add(self, name: str, passed: Optional[bool], summary: str, **values) -> CheckResult:
        r = CheckResult(name, passed, summary, values)
        self.report.results.append(r)
        self.log(f"{r.verdict}  {name}: {summary}")
        return r

    # ------------------------------------------------------------------ checks
    def preflight(self):
        fw = tuple(self.mcu.firmware_version) if getattr(self.mcu, "firmware_version", None) else (0, 0)
        has_encoder = bool(self.axis.HAS_ENCODER or self.axis.USE_ENCODER)
        new_fw = fw >= (1, 6)
        pid = self.pid
        loop = "off"
        if self.loop_configured:
            mode = "rest-only" if not pid.OPEN_ABOVE_MM_S else f"engaged below {pid.OPEN_ABOVE_MM_S} mm/s"
            loop = (f"P {pid.P:.0f} I {pid.I:.0f} D {pid.D:.0f}, clamp {pid.CORRECTION_VMAX} mm/s, watchdog "
                    f"{pid.MAX_DEVIATION_UM:.0f} um, zone {pid.HOME_ZONE_UM:.0f} um, {mode}")
        self._add(
            "preflight",
            True if (has_encoder and new_fw) or not has_encoder else False,
            f"firmware {fw[0]}.{fw[1]}, encoder {'present' if has_encoder else 'absent'}, loop {loop}; "
            f"{self.axis.MICROSTEPS_PER_STEP} usteps/FS, {self.axis.MAX_SPEED} mm/s, {self.axis.MAX_ACCELERATION} mm/s2, "
            f"travel {self.axis.MIN_POSITION}..{self.axis.MAX_POSITION} mm, working depth {self.depth:.2f} mm",
            firmware=fw[0] + fw[1] / 10.0,
        )
        if has_encoder and new_fw:
            # open-loop checks first: the loop must be off, reporting on
            if self.loop_configured:
                self.mcu.turn_off_stage_pid(AXIS.Z)
                self._wait()
            self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.ENC_IN_THETA)
            self._wait()
            time.sleep(0.2 * self.settle_scale)
            if self._enc()["reporting"]:
                self.encoder_ok = True
            else:
                self._add("encoder reporting", False, "the firmware did not start reporting the encoder")

    def home_and_park(self):
        self.log("homing Z")
        t0 = time.time()
        self.mcu.home_z()
        self._wait(60)
        t_home = time.time() - t0
        self._move(self.depth, allow_below_floor=True)
        self._add("homing", True, f"homed in {t_home:.2f} s, parked at {self.depth:.2f} mm", homing_s=t_home)

    def encoder_check(self):
        if not self.encoder_ok:
            self._add("encoder scale and sign", None, "no encoder on this axis")
            return
        self._settle(0.5)
        z0, e0 = self.mcu.z_pos, self._enc()["encoder_pos"]
        self._move(self.depth + 0.5)
        self._settle(0.5)
        z1, e1 = self.mcu.z_pos, self._enc()["encoder_pos"]
        self._move(self.depth)
        self._settle(0.5)
        dz, de = z1 - z0, e1 - e0
        ratio = de / dz if dz else float("nan")
        offset_um = self._dev_um(self._enc()["deviation"])
        ok_scale = abs(abs(ratio) - 1.0) <= self.SCALE_TOLERANCE
        ok_sign = ratio > 0
        if not ok_sign and ok_scale:
            self.report.recommendations["encoder_flip_dir_z"] = str(not self.axis.ENCODER_FLIP_DIR)
        summary = f"encoder/counter ratio {ratio:+.4f} over 0.5 mm; frame offset after homing {offset_um:+.1f} um"
        if not ok_scale:
            summary += (f" - SCALE OFF by {abs(ratio) - 1:+.1%}: check encoder_step_size_z_mm / screw_pitch_z_mm")
        elif not ok_sign:
            summary += " - encoder runs backwards: set encoder_flip_dir_z as recommended"
        self._add("encoder scale and sign", ok_scale and ok_sign, summary, ratio=ratio, frame_offset_um=offset_um)
        self.encoder_ok = ok_scale and ok_sign

    def _open_floor(self):
        if self.stage is not None and not self._floor_opened and float(self.axis.MIN_POSITION) > 0:
            self.stage.set_limits(z_neg_mm=0.0)
            self._floor_opened = True

    def _close_floor(self):
        if self._floor_opened:
            self.stage.set_limits(z_neg_mm=float(self.axis.MIN_POSITION))
            self._floor_opened = False

    def gap_map(self):
        if not self.encoder_ok:
            self._add("gap above home", None, "skipped (encoder not usable)")
            return
        self._open_floor()
        pts = []

        def sample(tag):
            self._settle(0.35)
            st = self._enc()
            pts.append((tag, self._mm(self.mcu.z_pos), self._mm(st["encoder_pos"])))

        z = self.gap_scan_from
        while z > -1e-9:
            self._move(max(z, 0.0), allow_below_floor=True)
            sample("down")
            z -= self.gap_step_mm
        z = 0.0
        while z <= self.gap_scan_from + 1e-9:
            self._move(z, allow_below_floor=True)
            sample("up")
            z += self.gap_step_mm
        self._move(self.depth)
        self._close_floor()
        down = [(x, e) for t, x, e in pts if t == "down"]
        up = [(x, e) for t, x, e in pts if t == "up"]
        # Edge of the gap from the first partial step: the stage travelled (encoder delta) part of the step
        # before it stopped following (down) or after it started again (up); the edge sits there.
        decouple = recouple = None
        for k in range(1, len(down)):
            dx, de = down[k][0] - down[k - 1][0], down[k][1] - down[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) < 0.5:
                travelled = max(0.0, min(abs(de), abs(dx)))
                decouple = round(down[k - 1][0] - travelled, 3)
                break
        for k in range(1, len(up)):
            dx, de = up[k][0] - up[k - 1][0], up[k][1] - up[k - 1][1]
            if abs(dx) > 1e-6 and abs(de / dx) > 0.5:
                # the previous (partial or zero) step already contained the edge
                prev_de = (up[k - 1][1] - up[k - 2][1]) if k >= 2 else 0.0
                travelled = max(0.0, min(abs(prev_de), abs(dx)))
                recouple = round(up[k - 1][0] - travelled, 3)
                break
        gap = max(decouple or 0.0, recouple or 0.0)
        floor_cfg = float(self.axis.MIN_POSITION)
        zone_um = float(self.pid.HOME_ZONE_UM) if self.pid else 0.0
        if gap <= self.gap_step_mm + 1e-9:
            self._add("gap above home", True, f"the stage follows the actuator down to the switch (no gap)", gap_mm=0.0)
            return
        floor = math.ceil((gap + 0.1) * 20) / 20.0
        problems = []
        if floor_cfg < gap + 0.05:
            problems.append(f"[SOFTWARE_POS_LIMIT] z_negative {floor_cfg} is inside the gap")
        if self.loop_configured and zone_um < gap * 1000:
            problems.append(f"pid_home_zone_z_um {zone_um:.0f} is smaller than the gap")
        self.report.recommendations.update({
            "z_home_gap_mm": f"{gap:.2f}",
            "z_negative (SOFTWARE_POS_LIMIT)": f"{floor:.2f}",
            "z_park_at_min_after_homing": "True",
            "pid_home_zone_z_um": f"<= {floor * 1000:.0f}",
        })
        self._add(
            "gap above home",
            not problems,
            f"encoder stops following below {gap:.2f} mm (down: {decouple}, up: {recouple}); "
            + ("; ".join(problems) if problems else "floor and zone are consistent with it"),
            gap_mm=gap,
        )

    def lost_steps(self):
        if not self.encoder_ok:
            self._add("lost steps (open loop)", None, "skipped (encoder not usable)")
            return
        # one warm-up pair so the reference is taken with the same direction history as the measurement
        # (the open-loop offset has a few tenths of a micron of direction hysteresis)
        self._move(self.depth + 0.1)
        self._settle(0.15)
        self._move(self.depth)
        self._settle(0.5)
        dev0 = self._enc()["deviation"]
        acks = []
        for _ in range(5):
            acks.append(self._move(self.depth + 0.1))
            self._settle(0.15)
            acks.append(self._move(self.depth))
            self._settle(0.15)
        self._settle(0.5)
        lost_short = self._dev_um(self._enc()["deviation"] - dev0)
        far = min(self.depth + 2.0, float(self.axis.MAX_POSITION) - 0.1)
        dev1 = self._enc()["deviation"]
        long_acks = []
        for _ in range(2):
            long_acks.append(self._move(far))
            self._settle(0.2)
            long_acks.append(self._move(self.depth))
            self._settle(0.2)
        self._settle(0.5)
        lost_long = self._dev_um(self._enc()["deviation"] - dev1)
        ok = abs(lost_short) <= self.lost_step_limit_um and abs(lost_long) <= self.lost_step_limit_um
        self._add(
            "lost steps (open loop)",
            ok,
            f"10 x 100 um: offset change {lost_short:+.2f} um, ack median {statistics.median(acks) * 1000:.0f} ms; "
            f"4 x {far - self.depth:.1f} mm at {self.axis.MAX_SPEED} mm/s: {lost_long:+.2f} um, ack median "
            f"{statistics.median(long_acks) * 1000:.0f} ms (limit one full step, {self.lost_step_limit_um:.1f} um)",
            lost_short_um=lost_short, lost_long_um=lost_long, ack_100um_ms=statistics.median(acks) * 1000,
        )

    def closed_loop(self):
        if not self.loop_configured:
            self._add("closed loop", None, "loop not enabled in the configuration")
            return
        if not self.encoder_ok:
            self._add("closed loop", False, "encoder not usable, loop left off")
            return
        self.mcu.turn_on_stage_pid(AXIS.Z)
        self._wait(5)
        self._settle(0.3)
        st = self._enc()
        if not st["pid_enabled"]:
            why = "held open (home zone or offset)" if st["pid_zone_hold"] else "refused by the firmware"
            self._add("closed loop", False, f"the loop did not engage at {self.depth:.2f} mm: {why}")
            return
        acks, errs = [], []
        step = 0.001
        for k in range(1, self.stack_n + 1):
            acks.append(self._move(self.depth + k * step))
            self._settle(0.15)
            errs.append(self._dev_um(self._enc()["deviation"]))
        ack_100 = self._move(self.depth)
        self._settle(0.3)
        # hold
        t0 = time.time()
        hold_errs = []
        last_sign = 0
        crossings = 0
        while time.time() - t0 < self.hold_s:
            self._guard()
            e = self._dev_um(self._enc()["deviation"])
            hold_errs.append(e)
            s = (e > 0) - (e < 0)
            if s and last_sign and s != last_sign:
                crossings += 1
            if s:
                last_sign = s
            time.sleep(0.01)
        acks_ms = sorted(a * 1000 for a in acks)
        err_max = max(abs(e) for e in errs)
        hold_max = max(abs(e) for e in hold_errs) if hold_errs else 0.0
        fault = self._enc()["pid_fault"]
        ok = (not fault and err_max <= self.CLOSED_ERROR_LIMIT_UM and hold_max <= self.CLOSED_ERROR_LIMIT_UM
              and acks_ms[-1] <= self.CLOSED_ACK_LIMIT_MS)
        self._add(
            "closed loop",
            ok,
            f"{self.stack_n} x 1 um: ack median {acks_ms[len(acks_ms) // 2]:.0f} ms (max {acks_ms[-1]:.0f}), "
            f"error max {err_max:.2f} um; {self.stack_n * step * 1000:.0f} um return {ack_100 * 1000:.0f} ms; "
            f"{self.hold_s:.0f} s hold: max |error| {hold_max:.2f} um, {crossings / self.hold_s:.1f} crossings/s"
            + ("; WATCHDOG FAULT" if fault else ""),
            ack_median_ms=acks_ms[len(acks_ms) // 2], ack_max_ms=acks_ms[-1], err_max_um=err_max, hold_max_um=hold_max,
        )

    def restore(self):
        # Every step here runs regardless of how the run ended (cancel included) and regardless of the
        # others failing: floor back, Z back to the working depth, loop back to its configured state,
        # encoder reporting off.
        for step in (self._close_floor, self._restore_position, self._restore_loop, self._restore_reporting):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - restoring must not mask the report
                self.log(f"restore ({step.__name__.lstrip('_')}): {e}")

    def _restore_position(self):
        if self.encoder_ok and abs(self._pos_mm() - self.depth) > 0.01 and not self.mcu.is_busy():
            self._move(self.depth, check_cancel=False)

    def _restore_loop(self):
        if self.loop_configured:
            st = self._enc() if self.encoder_ok else {"pid_enabled": False}
            if not st["pid_enabled"]:
                self.mcu.turn_on_stage_pid(AXIS.Z)
                self._wait(5)

    def _restore_reporting(self):
        if self.encoder_ok or self.loop_configured:
            self.mcu.set_encoder_reporting(AXIS.Z, ENCODER_REPORTING.OFF)
            self._wait()

    # ------------------------------------------------------------------ driver
    def run(self) -> SelfTestReport:
        steps = [self.preflight, self.home_and_park, self.encoder_check, self.gap_map, self.lost_steps, self.closed_loop]
        try:
            for step in steps:
                self._check_cancel()
                step()
        except SelfTestCancelled:
            self.report.aborted = "cancelled by the operator"
            self.log("cancelled")
        except Exception as e:  # noqa: BLE001 - the report carries the failure
            self.report.aborted = str(e)
            self.log(f"aborted: {e}")
        finally:
            self.restore()
        # the per-check lines were logged as they happened; close with the recommendations and the verdict
        self.log("")
        tail = self.report.text().splitlines()
        for line in tail[len(self.report.results):]:
            self.log(line)
        return self.report
