"""The motion self-test decides pass/fail and recommends ini values from a simulated Z stage."""

import pytest

import squid.config
from squid.config import AxisConfig, DirectionSign, PIDConfig
from squid.motion_selftest import ZMotionSelfTest


def _axis(pid=None, min_pos=0.05, flip=True, microsteps=16) -> AxisConfig:
    return AxisConfig(
        MOVEMENT_SIGN=DirectionSign.DIRECTION_SIGN_NEGATIVE,
        USE_ENCODER=False,
        ENCODER_SIGN=DirectionSign.DIRECTION_SIGN_POSITIVE,
        ENCODER_STEP_SIZE=100e-6,
        FULL_STEPS_PER_REV=200,
        SCREW_PITCH=0.3,
        MICROSTEPS_PER_STEP=microsteps,
        MAX_SPEED=3.0,
        MAX_ACCELERATION=300,
        MIN_POSITION=min_pos,
        MAX_POSITION=6.0,
        PID=pid,
        HAS_ENCODER=True,
        ENCODER_FLIP_DIR=flip,
    )


class FakeMcu:
    """Counter and encoder of a Z stage. gap_mm: the stage rests on its stop while the actuator is within
    gap_mm of home (encoder still); ratio: encoder counts per counter count (-1 = wrong sign)."""

    def __init__(self, axis: AxisConfig, gap_mm=0.0, ratio=1.0, max_dev_um=200.0, fault_after=None):
        self.axis = axis
        self.gap_mm = gap_mm
        self.ratio = ratio
        self.firmware_version = (1, 6)
        self.z_pos = 0
        self.enc_zero_mm = 0.0
        self.reporting = False
        self.pid_enabled = False
        self.pid_fault = False
        # Firmware ENABLE_STAGE_PID refuses when |ENC_POS - XACTUAL| exceeds the watchdog limit
        # (commands.cpp: "refuse up front instead of tripping a moment later").
        self.max_dev_um = max_dev_um
        # Inject a watchdog fault: after this many encoder reads with the loop engaged, the firmware
        # opens the loop and latches PID_FAULT (None = never).
        self.fault_after = fault_after
        self._reads_engaged = 0
        # In-flight model: a move keeps is_busy() True for this many polls (0 = instantaneous).
        # Lets a test cancel while Z is still moving, as on the real controller.
        self.busy_polls = 0
        self._busy_left = 0
        self.pid_on_calls = 0  # how many times the loop was switched on, restore included
        self.calls = []

    # -- model
    def _stage_mm(self, counter_mm):
        return max(counter_mm, self.gap_mm)

    def _enc_pos(self):
        mm = self.axis.convert_to_real_units(self.z_pos)
        enc_mm = self._stage_mm(mm) - self.enc_zero_mm
        return int(round(self.ratio * self.axis.convert_real_units_to_ustep(enc_mm)))

    # -- protocol
    def move_z_to_usteps(self, u):
        self.calls.append(("move", u))
        self.z_pos = int(u)
        self._busy_left = self.busy_polls

    def home_z(self):
        self.calls.append(("home",))
        self.z_pos = 0
        self.enc_zero_mm = self._stage_mm(0.0)  # homing zeroes both frames at the switch

    def is_busy(self):
        if self._busy_left > 0:
            self._busy_left -= 1
            return True
        return False

    def wait_till_operation_is_completed(self, timeout=5):
        pass

    def set_encoder_reporting(self, axis, mode):
        self.reporting = mode != 0

    def configure_stage_pid(self, axis, transitions_per_revolution, flip_direction=False):
        # Firmware >= 1.6: CONFIGURE_STAGE_PID re-aligns ENC_POS to XACTUAL at the current position
        # and clears a latched fault.
        self.calls.append(("configure_while_moving",) if self._busy_left > 0 else ("configure",))
        mm = self.axis.convert_to_real_units(self.z_pos)
        self.enc_zero_mm = self._stage_mm(mm) - mm
        self.pid_fault = False

    def turn_on_stage_pid(self, axis):
        self.pid_on_calls += 1
        dev_um = abs(self.axis.convert_to_real_units(self._enc_pos() - self.z_pos)) * 1000.0
        if dev_um > self.max_dev_um:
            self.calls.append(("enable_refused", round(dev_um)))
            return  # CMD_EXECUTION_ERROR on the real controller: the loop stays off
        self.pid_enabled = True
        self.pid_fault = False
        self._reads_engaged = 0

    def turn_off_stage_pid(self, axis):
        self.pid_enabled = False
        self.pid_fault = False  # DISABLE acknowledges a latched fault

    def get_encoder_state(self):
        if self.pid_enabled and self.fault_after is not None:
            self._reads_engaged += 1
            if self._reads_engaged >= self.fault_after:
                self.pid_enabled = False
                self.pid_fault = True
        enc = self._enc_pos()
        dev = 0 if self.pid_enabled else enc - self.z_pos
        dev = max(-32768, min(32767, dev))  # ENC_POS_DEV is an int16 in the status packet: it saturates
        return {
            "reporting": self.reporting,
            "pid_enabled": self.pid_enabled,
            "pid_fault": self.pid_fault,
            "pid_zone_hold": False,
            "axis": 2,
            "encoder_pos": enc,
            "deviation": dev,
            # full width, paired with the counter by the parser so a caller cannot straddle packets
            "dev32": enc - self.z_pos,
        }


PID = PIDConfig(ENABLED=True, P=65535, I=0, D=0, CORRECTION_VMAX=1.0, MAX_DEVIATION_UM=200, HOME_ZONE_UM=200)


def _run(mcu, axis, **kw):
    log = []
    t = ZMotionSelfTest(mcu, axis, log=log.append, hold_s=0.05, settle_scale=0.0, **kw)
    return t.run(), log


def test_good_stage_passes_every_check():
    axis = _axis(pid=PID)
    report, log = _run(FakeMcu(axis), axis)
    names = [r.name for r in report.results]
    assert names == [
        "preflight",
        "homing",
        "encoder scale and sign",
        "gap above home",
        "lost steps (open loop)",
        "closed loop",
    ]
    assert report.passed, report.text()
    assert report.recommendations == {}
    assert "OVERALL: PASS" in report.text()


def test_reversed_encoder_fails_and_recommends_the_flip():
    axis = _axis(pid=PID, flip=True)
    report, _ = _run(FakeMcu(axis, ratio=-1.0), axis)
    enc = next(r for r in report.results if r.name == "encoder scale and sign")
    assert enc.passed is False
    assert report.recommendations["encoder_flip_dir_z"] == "False"
    # the loop is never closed on a backwards encoder
    closed = next(r for r in report.results if r.name == "closed loop")
    assert closed.passed is False
    assert not report.passed


def test_reversed_encoder_leaves_the_loop_off():
    """A loop engaged on an encoder that failed validation regulates against a bad measurement.

    The encoder is the loop's only feedback, so if its scale or sign is wrong the correction is
    wrong too - on Z that ends in the stall the operator calls non-recoverable. Restore must
    leave the loop off and say so, even though the ini asks for it.
    """
    axis = _axis(pid=PID, flip=True)
    mcu = FakeMcu(axis, ratio=-1.0)
    report, log = _run(mcu, axis)

    assert mcu.pid_on_calls == 0
    assert mcu.pid_enabled is False
    assert any("left OFF" in line for line in log), log


def test_gap_stage_is_measured_and_the_floor_is_recommended():
    axis = _axis(pid=PID, min_pos=0.05)  # floor inside the gap: must fail
    report, _ = _run(FakeMcu(axis, gap_mm=0.64), axis)
    gap = next(r for r in report.results if r.name == "gap above home")
    assert gap.passed is False
    assert 0.6 <= gap.values["gap_mm"] <= 0.7
    assert 0.6 <= float(report.recommendations["z_home_gap_mm"]) <= 0.7
    assert report.recommendations["z_negative (SOFTWARE_POS_LIMIT)"] in ("0.75", "0.80")
    assert report.recommendations["z_park_at_min_after_homing"] == "True"


def test_gap_stage_with_a_consistent_floor_passes():
    pid = PIDConfig(ENABLED=True, P=65535, I=0, D=0, CORRECTION_VMAX=1.0, MAX_DEVIATION_UM=200, HOME_ZONE_UM=700)
    axis = _axis(pid=pid, min_pos=0.75)
    mcu = FakeMcu(axis, gap_mm=0.64)
    report, log = _run(mcu, axis)
    gap = next(r for r in report.results if r.name == "gap above home")
    assert gap.passed is True, gap.summary
    # The run homes with the loop OFF, so the firmware never arms its post-homing realignment and an
    # explicit ENABLE would be refused on this stage (640 um frame offset > 200 um watchdog). The
    # self-test must realign the frames itself before enabling, as the firmware does on the first
    # engage after a homing with the loop requested.
    closed = next(r for r in report.results if r.name == "closed loop")
    assert closed.passed is True, closed.summary
    assert ("enable_refused", 640) not in mcu.calls, mcu.calls
    assert ("configure",) in mcu.calls
    assert report.passed, report.text()


def test_watchdog_fault_during_the_run_leaves_the_loop_off():
    # The firmware opens the loop and latches PID_FAULT partway through the closed-loop stack. The
    # report must say so, and the cleanup must NOT switch the loop back on: a fresh ENABLE would
    # pass the firmware's deviation check once the stage settled and hide the fault.
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis, fault_after=3)
    report, log = _run(mcu, axis)
    assert not report.passed
    assert mcu.pid_enabled is False
    assert mcu.pid_on_calls == 1, mcu.calls  # the run's own enable; none from restore
    assert any("left OFF" in line and "fault" in line.lower() for line in log), log


def test_open_loop_instrument_skips_the_loop_check():
    axis = _axis(pid=None)
    report, _ = _run(FakeMcu(axis), axis)
    closed = next(r for r in report.results if r.name == "closed loop")
    assert closed.passed is None
    assert report.passed


class FakeStage:
    def __init__(self):
        self.limits = []

    def set_limits(self, **kw):
        self.limits.append(kw)


def test_gap_map_opens_the_floor_and_restores_it():
    axis = _axis(pid=PID, min_pos=0.75)
    mcu = FakeMcu(axis, gap_mm=0.64)
    stage = FakeStage()
    t = ZMotionSelfTest(mcu, axis, log=lambda s: None, hold_s=0.05, settle_scale=0.0, stage=stage)
    report = t.run()
    gap = next(r for r in report.results if r.name == "gap above home")
    assert 0.6 <= gap.values["gap_mm"] <= 0.7  # the switch was reached, the gap measured
    assert stage.limits[0] == {"z_neg_mm": 0.0}  # floor opened for the gap map
    assert stage.limits[-1] == {"z_neg_mm": 0.75}  # and put back


def test_cancel_away_from_depth_still_restores_everything():
    axis = _axis(pid=PID, min_pos=0.75)
    mcu = FakeMcu(axis, gap_mm=0.64)
    stage = FakeStage()
    state = {"moves": 0}
    orig = mcu.move_z_to_usteps

    def counting_move(u):
        state["moves"] += 1
        orig(u)

    mcu.move_z_to_usteps = counting_move
    # cancel in the middle of the gap map, with Z near the switch and the floor opened
    t = ZMotionSelfTest(
        mcu, axis, log=lambda s: None, cancel=lambda: state["moves"] >= 12, hold_s=0.05, settle_scale=0.0, stage=stage
    )
    report = t.run()
    assert report.aborted == "cancelled by the operator"
    assert stage.limits[-1] == {"z_neg_mm": 0.75}  # floor put back
    assert (
        abs(axis.convert_to_real_units(mcu.z_pos) - t.depth) < 1e-3
    )  # Z back at the working depth (one microstep is 94 nm)
    assert mcu.pid_enabled is True  # loop back on
    assert mcu.reporting is False  # reporting off


def test_aborted_move_is_reported_not_counted():
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    mcu.last_command_aborted_error = None
    orig = mcu.move_z_to_usteps

    def failing_move(u):
        orig(u)
        mcu.last_command_aborted_error = "CMD_EXECUTION_ERROR"

    mcu.move_z_to_usteps = failing_move
    report, _ = _run(mcu, axis)
    assert report.aborted and "aborted by the controller" in report.aborted


def test_cancel_stops_early_and_restores():
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    t = ZMotionSelfTest(mcu, axis, log=lambda s: None, cancel=cancel, hold_s=0.05, settle_scale=0.0)
    report = t.run()
    assert report.aborted == "cancelled by the operator"
    assert not report.passed
    assert mcu.reporting is False  # reporting switched back off
    assert mcu.pid_enabled is True  # loop back to its configured state


def test_refuses_to_run_when_z_homing_is_disabled(monkeypatch):
    """home_and_park() calls home_z() unconditionally, so a configuration that disables Z
    homing cannot run the self-test: two shipped inis set homing_enabled_z = False, and on
    those machines the axis is deliberately never driven onto its switch."""
    import control._def

    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)

    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    report, log = _run(mcu, axis)

    assert not report.passed, report.text()
    preflight = next(r for r in report.results if r.name == "preflight")
    assert preflight.passed is False
    assert "homing_enabled_z" in preflight.summary
    assert ("home",) not in mcu.calls
    # the run stops at preflight: none of the motion checks were attempted
    assert [r.name for r in report.results] == ["preflight"]


def test_cancel_mid_move_realigns_only_after_z_has_stopped_at_the_working_depth():
    # Cancellation raises inside _move() while the controller is still busy. The cleanup must
    # wait for the move to finish, bring Z back to the working depth, and only then re-align the
    # encoder frame and re-enable: CONFIGURE_STAGE_PID during motion would capture a wrong offset.
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    mcu.busy_polls = 6
    state = {"moves": 0}
    orig = mcu.move_z_to_usteps

    def counting_move(u):
        state["moves"] += 1
        orig(u)

    mcu.move_z_to_usteps = counting_move
    t = ZMotionSelfTest(
        mcu, axis, log=lambda s: None, cancel=lambda: state["moves"] >= 3, hold_s=0.05, settle_scale=0.0
    )
    report = t.run()
    assert report.aborted == "cancelled by the operator"
    assert ("configure_while_moving",) not in mcu.calls, mcu.calls
    assert ("configure",) in mcu.calls
    last_move = max(i for i, c in enumerate(mcu.calls) if c[0] == "move")
    assert mcu.calls.index(("configure",)) > last_move  # re-align after the restore move, not before
    assert abs(axis.convert_to_real_units(mcu.z_pos) - t.depth) < 1e-3
    assert mcu.pid_enabled is True


def test_cancel_with_z_never_stopping_leaves_the_loop_off():
    # If the controller never reports idle, nothing about Z's resting position is known: skip the
    # re-align, leave the loop off, and say so - never CONFIGURE a moving axis.
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    state = {"moves": 0}
    orig = mcu.move_z_to_usteps

    def counting_move(u):
        state["moves"] += 1
        if state["moves"] >= 3:
            mcu.busy_polls = 10**9  # from the cancelled move on, the controller never reports idle
        orig(u)

    mcu.move_z_to_usteps = counting_move
    log = []
    t = ZMotionSelfTest(
        mcu,
        axis,
        log=log.append,
        cancel=lambda: state["moves"] >= 3,
        hold_s=0.05,
        settle_scale=0.0,
        idle_timeout_s=0.05,
    )
    report = t.run()
    assert report.aborted == "cancelled by the operator"
    assert ("configure_while_moving",) not in mcu.calls, mcu.calls
    assert ("configure",) not in mcu.calls
    assert mcu.pid_enabled is False
    assert any("left OFF" in line and "still moving" in line for line in log), log


def test_return_move_timing_out_near_depth_leaves_the_loop_off():
    # The cancelled move stops, but the cleanup's own return-to-depth move never completes while the
    # counter already reads within 0.01 mm of the target (stopped at 2.495 for a 2.500 mm target, or
    # a controller that stays busy). _at_rest must not survive from the earlier wait: no re-align,
    # loop off.
    axis = _axis(pid=PID)
    mcu = FakeMcu(axis)
    state = {"moves": 0}
    orig = mcu.move_z_to_usteps

    def counting_move(u):
        state["moves"] += 1
        if state["moves"] == 5:
            mcu.busy_polls = 6  # the cancelled move (into the gap map, away from depth): stops after a few polls
        elif state["moves"] >= 6:
            mcu.busy_polls = 10**9  # the restore's return move: never reports idle
        orig(u)

    mcu.move_z_to_usteps = counting_move
    log = []
    t = ZMotionSelfTest(
        mcu,
        axis,
        log=log.append,
        cancel=lambda: state["moves"] >= 5,
        hold_s=0.05,
        settle_scale=0.0,
        idle_timeout_s=0.05,
    )
    report = t.run()
    assert report.aborted == "cancelled by the operator"
    assert state["moves"] >= 6, mcu.calls  # the return move was attempted
    assert ("configure_while_moving",) not in mcu.calls, mcu.calls
    assert ("configure",) not in mcu.calls, mcu.calls
    assert mcu.pid_enabled is False
    assert any("left OFF" in line for line in log), log


def test_an_offset_past_the_int16_clip_is_measured_and_lost_steps_still_fail():
    """ENC_POS_DEV is an int16: on a 256 usteps/FS Z it saturates at +-192 um, which is smaller than the
    things the checks have to see. This stage has a 640 um frame offset after homing (its actuator homes
    below the stage's stop) and loses half a millimetre partway through the lost-step check; both are past
    the clip, so both are invisible unless the checks work from encoder_pos - z_pos (32-bit, same packet).
    """
    pid = PIDConfig(ENABLED=True, P=65535, I=0, D=0, CORRECTION_VMAX=1.0, MAX_DEVIATION_UM=200, HOME_ZONE_UM=700)
    axis = _axis(pid=pid, min_pos=0.75, microsteps=256)
    assert abs(axis.convert_real_units_to_ustep(0.64)) > 32767  # the clip sits inside the offset being measured
    mcu = FakeMcu(axis, gap_mm=0.64)

    log = []
    t = ZMotionSelfTest(mcu, axis, log=log.append, hold_s=0.05, settle_scale=0.0)
    # lost steps: the axis slips 0.5 mm on the third of the six 100 um moves the check makes, after it has
    # taken its reference. The counter keeps counting; the encoder does not follow.
    trigger = axis.convert_real_units_to_ustep(t.depth + 0.1)
    moves = {"n": 0}
    orig = mcu.move_z_to_usteps

    def slipping_move(u):
        orig(u)
        if u == trigger:
            moves["n"] += 1
            if moves["n"] == 3:
                mcu.enc_zero_mm += 0.5

    mcu.move_z_to_usteps = slipping_move
    report = t.run()

    enc = next(r for r in report.results if r.name == "encoder scale and sign")
    assert enc.values["frame_offset_um"] == pytest.approx(640, abs=5)  # the true offset, not the ~192 um clip
    lost = next(r for r in report.results if r.name == "lost steps (open loop)")
    assert lost.passed is False, lost.summary  # a saturated field would have reported no change at all
    assert abs(lost.values["lost_short_um"]) == pytest.approx(500, abs=5)
    assert not report.passed
