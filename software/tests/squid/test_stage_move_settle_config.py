"""CephlaStage sends what the enabled loop IS (firmware >= 1.7): the TMC4361A's PID or move-and-settle.

The strategy is a state on the controller, so it is sent for "pid" too; the controller refuses a change
while a loop is requested, so the request is dropped first; and every move-and-settle field is literal on the
wire, so all four commands go out whenever move-and-settle is chosen.
"""

import logging
from unittest.mock import MagicMock

import pytest

import control._def as _def
import squid.config
from squid.config import AxisConfig, DirectionSign, PIDConfig
from squid.stage.cephla import CephlaStage


def _axis(pid: PIDConfig, sign=DirectionSign.DIRECTION_SIGN_NEGATIVE) -> AxisConfig:
    return AxisConfig(
        MOVEMENT_SIGN=sign,
        USE_ENCODER=False,
        ENCODER_SIGN=DirectionSign.DIRECTION_SIGN_POSITIVE,
        ENCODER_STEP_SIZE=100e-6,
        FULL_STEPS_PER_REV=200,
        SCREW_PITCH=0.3,
        MICROSTEPS_PER_STEP=16,
        MAX_SPEED=3.0,
        MAX_ACCELERATION=300,
        MIN_POSITION=0,
        MAX_POSITION=6,
        HAS_ENCODER=True,
        RAMP_PROFILE="trapezoid",
        PID=pid,
    )


def _stage(pid: PIDConfig, firmware=(1, 7), sign=DirectionSign.DIRECTION_SIGN_NEGATIVE):
    mc = MagicMock()
    mc.firmware_version = firmware
    mc.pid_fault_axes.return_value = set()
    cfg = squid.config.get_stage_config().model_copy(update={"Z_AXIS": _axis(pid, sign)})
    return CephlaStage(mc, cfg), mc


def _names(mc):
    return [c[0] for c in mc.method_calls]


def test_move_settle_is_configured_before_the_enable_and_after_dropping_the_request():
    pid = PIDConfig(
        ENABLED=True,
        P=65535,
        I=0,
        D=0,
        MAX_DEVIATION_UM=200,
        HOME_ZONE_UM=200,
        TOLERANCE_UM=0.2,
        STRATEGY="move_settle",
        MOVE_SETTLE_WINDOW_MS=9.0,
        MOVE_SETTLE_TRIM_GAIN=0.75,
        MOVE_SETTLE_APPROACH="positive",
        MOVE_SETTLE_LOST_MOTION_UM=0.8,
        MOVE_SETTLE_BIAS_UM=0.1,
        MOVE_SETTLE_SCALE_PPM=-1100,
        MOVE_SETTLE_FINISH_UM=50.0,
        MOVE_SETTLE_FINISH_FROM_UM=1000.0,
        MOVE_SETTLE_SHAPER_HALF_PERIOD_MS=4.42,
        MOVE_SETTLE_SHAPER_FIRST_SHARE=0.565,
        MOVE_SETTLE_SHAPER_MAX_MOVE_UM=10,
    )
    _, mc = _stage(pid)
    mc.set_loop_strategy.assert_called_once_with(_def.AXIS.Z, _def.LOOP_STRATEGY.MOVE_SETTLE)
    # the finishing leg goes out in um with THIS axis's usteps per um (16 x 200 / 0.3 mm = 10.667), for the
    # microcontroller layer to put on the wire in usteps
    mc.set_move_settle_finish.assert_called_once_with(
        _def.AXIS.Z, finish_um=50.0, from_um=1000.0, usteps_per_um=pytest.approx(16 * 200 / 300.0)
    )
    mc.set_move_settle_measure.assert_called_once_with(
        _def.AXIS.Z,
        window_ms=9.0,
        trim_gain=0.75,
        max_trims=6,
        max_reapproaches=1,
        # the ini says "positive" in the HOST's frame; this Z's movement sign is negative (+mm = the
        # counter's -), so the controller is told NEGATIVE
        approach=_def.MOVE_SETTLE_APPROACH.NEGATIVE,
        wait_ms=5.0,
    )
    mc.set_move_settle_feedforward.assert_called_once_with(_def.AXIS.Z, lost_motion_um=0.8, bias_um=0.1, backoff_um=3.0)
    mc.set_move_settle_model.assert_called_once_with(
        _def.AXIS.Z, carry_um=0.0, full_push_um=0.94, learn_gain=0.25, bias_sigma=0.5
    )
    mc.set_move_settle_scale.assert_called_once_with(_def.AXIS.Z, scale_ppm=-1100)
    mc.set_move_settle_shaper.assert_called_once_with(
        _def.AXIS.Z, half_period_ms=4.42, first_share=0.565, max_move_um=10
    )
    mc.set_move_settle_accept.assert_called_once_with(
        _def.AXIS.Z, overshoot_tolerance_um=0.28, quiet_pp_um=0.0, quiet_windows=0
    )
    order = _names(mc)
    assert order.index("turn_off_stage_pid") < order.index("set_loop_strategy") < order.index("set_move_settle_measure")
    assert order.index("set_move_settle_scale") < order.index("set_move_settle_finish") < order.index("turn_on_stage_pid")
    assert order.index("set_move_settle_accept") < order.index("turn_on_stage_pid")
    # what move-and-settle accepts and refuses to correct are the loop's tolerance and watchdog
    mc.set_pid_tolerance.assert_called_once_with(_def.AXIS.Z, 0.2, 0.2)


def test_approach_side_is_translated_from_the_host_frame_to_the_counter_frame():
    def sent(approach, sign):
        _, mc = _stage(
            PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY="move_settle", MOVE_SETTLE_APPROACH=approach), sign=sign
        )
        return mc.set_move_settle_measure.call_args.kwargs["approach"]

    neg, pos = DirectionSign.DIRECTION_SIGN_NEGATIVE, DirectionSign.DIRECTION_SIGN_POSITIVE
    assert sent("positive", pos) == _def.MOVE_SETTLE_APPROACH.POSITIVE
    assert sent("negative", pos) == _def.MOVE_SETTLE_APPROACH.NEGATIVE
    assert sent("positive", neg) == _def.MOVE_SETTLE_APPROACH.NEGATIVE
    assert sent("negative", neg) == _def.MOVE_SETTLE_APPROACH.POSITIVE
    assert sent("move", neg) == _def.MOVE_SETTLE_APPROACH.MOVE_DIRECTION  # no fixed side: nothing to translate


def test_the_chip_pid_strategy_is_sent_too_so_a_tools_setting_cannot_survive():
    _, mc = _stage(PIDConfig(ENABLED=True, P=65535, I=0, D=0))
    mc.set_loop_strategy.assert_called_once_with(_def.AXIS.Z, _def.LOOP_STRATEGY.CHIP_PID)
    mc.set_move_settle_measure.assert_not_called()
    mc.turn_on_stage_pid.assert_called_once_with(_def.AXIS.Z)


def test_firmware_1_6_with_the_chip_pid_is_configured_exactly_as_before():
    _, mc = _stage(PIDConfig(ENABLED=True, P=65535, I=0, D=0), firmware=(1, 6))
    mc.set_loop_strategy.assert_not_called()
    mc.turn_off_stage_pid.assert_not_called()
    mc.turn_on_stage_pid.assert_called_once_with(_def.AXIS.Z)


def test_move_settle_on_old_firmware_leaves_the_axis_open_loop_and_says_why(caplog):
    """The chip's PID is a different loop with different settings, not a fallback for move-and-settle."""
    with caplog.at_level(logging.ERROR):
        _, mc = _stage(PIDConfig(ENABLED=True, P=65535, I=0, D=0, STRATEGY="move_settle"), firmware=(1, 6))
    mc.set_loop_strategy.assert_not_called()
    mc.turn_on_stage_pid.assert_not_called()
    # names the key the operator has in the ini: Z's is z_encoder_control, there is no strategy key for Z
    assert any("z_encoder_control = 'move_settle' needs firmware >= 1.7" in r.message for r in caplog.records)


def _settle_stage(z_usteps=-10000):  # this Z counts down going up: about +0.94 mm
    stage, mc = _stage(PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY="move_settle"))
    mc.get_pos.return_value = (0, 0, z_usteps, 0)
    mc.reset_mock()
    mc.get_pos.return_value = (0, 0, z_usteps, 0)
    return stage, mc


def test_move_settle_z_goes_straight_down_without_the_host_backlash_move():
    """The controller deals with the lost motion; 5 um past and back up would be two settled moves."""
    stage, mc = _settle_stage()
    stage.move_z(-0.001)
    assert mc.move_z_usteps.call_count == 1
    assert mc.move_z_usteps.call_args.args[0] == stage.z_mm_to_usteps(-0.001)
    z_now_mm = stage.get_pos().z_mm
    stage.move_z_to(z_now_mm - 0.002)
    mc.move_z_to_usteps.assert_called_once_with(stage.z_mm_to_usteps(z_now_mm - 0.002))


def test_a_measured_correction_starts_from_where_the_stage_is_only_in_move_settle():
    stage, mc = _settle_stage()
    stage.move_z_from_measured(-0.0005)
    mc.move_z_usteps_from_measured.assert_called_once_with(stage.z_mm_to_usteps(-0.0005))
    mc.move_z_usteps.assert_not_called()
    # any other Z: the ordinary relative move (with the host's backlash move on the way down)
    stage, mc = _stage(PIDConfig(ENABLED=True, P=65535, I=0, D=0))
    mc.reset_mock()
    stage.move_z_from_measured(-0.0005)
    mc.move_z_usteps_from_measured.assert_not_called()
    assert mc.move_z_usteps.call_count == 2


def test_the_chip_pid_and_open_loop_z_keep_the_host_backlash_move():
    stage, mc = _stage(PIDConfig(ENABLED=True, P=65535, I=0, D=0))
    mc.reset_mock()
    stage.move_z(-0.001)
    assert mc.move_z_usteps.call_count == 2  # past the mark, then back up


def test_a_missed_move_is_sent_once_more_as_the_same_absolute_target():
    from control.microcontroller import CommandAborted

    missed = CommandAborted(reason="firmware reported CMD_EXECUTION_ERROR", command_id=1, recoverable=True)
    stage, mc = _settle_stage(z_usteps=-10000)
    mc.wait_till_operation_is_completed.side_effect = [missed, None]
    rel = stage.z_mm_to_usteps(0.001)
    stage.move_z(0.001)
    mc.acknowledge_aborted_command.assert_called_once()
    mc.move_z_to_usteps.assert_called_once_with(-10000 + rel)  # where the relative move was going

    # twice in a row is the caller's to see
    stage, mc = _settle_stage()
    mc.wait_till_operation_is_completed.side_effect = [missed, missed]
    with pytest.raises(CommandAborted):
        stage.move_z_to(stage.get_pos().z_mm + 0.001)
    assert mc.move_z_to_usteps.call_count == 2

    # a latched fault (or a lost acknowledgement) is not a missed move: nothing is resent
    stage, mc = _settle_stage()
    mc.wait_till_operation_is_completed.side_effect = [
        CommandAborted(reason="Z closed-loop fault latched", command_id=1, recoverable=False)
    ]
    with pytest.raises(CommandAborted):
        stage.move_z(0.001)
    mc.acknowledge_aborted_command.assert_not_called()
    mc.move_z_to_usteps.assert_not_called()


def test_a_missed_from_measured_move_is_retried_as_the_last_firmware_target_not_nominal_plus_correction():
    """The controller resolved the target as (encoder + correction); the host knows only the counter, off the
    encoder by up to the accepted band at rest. So the retry asks the controller for that target, and what it
    carries is only an estimate, relative to where the stage now is (the counter, after a miss)."""
    from control.microcontroller import CommandAborted

    missed = CommandAborted(reason="firmware reported CMD_EXECUTION_ERROR", command_id=1, recoverable=True)
    stage, mc = _settle_stage(z_usteps=-10000)
    rel = stage.z_mm_to_usteps(-0.0005)
    nominal = -10000
    measured = nominal + 8  # where the stage IS, inside the band: the controller's target is measured + rel
    counter_after_miss = measured + rel + 5  # a miss puts the counter where the stage stopped
    mc.get_pos.side_effect = [(0, 0, nominal, 0), (0, 0, counter_after_miss, 0)]
    mc.wait_till_operation_is_completed.side_effect = [missed, None]
    stage.move_z_from_measured(-0.0005)
    mc.move_z_usteps_from_measured.assert_called_once_with(rel)
    mc.acknowledge_aborted_command.assert_called_once()
    mc.move_z_usteps_retry_last.assert_called_once_with((nominal + rel) - counter_after_miss)
    mc.move_z_to_usteps.assert_not_called()  # nominal + correction is not the plane that was asked for
    mc.move_z_usteps.assert_not_called()
    order = _names(mc)
    assert order.index("acknowledge_aborted_command") < order.index("move_z_usteps_retry_last")
    assert mc.wait_till_operation_is_completed.call_count == 2


def test_the_ordinary_z_retry_is_still_the_same_absolute_target():
    """move_z / move_z_to were planned from the last target, which the counter IS at rest: nominal is the plane."""
    from control.microcontroller import CommandAborted

    missed = CommandAborted(reason="firmware reported CMD_EXECUTION_ERROR", command_id=1, recoverable=True)
    stage, mc = _settle_stage(z_usteps=-10000)
    mc.wait_till_operation_is_completed.side_effect = [missed, None]
    stage.move_z(0.001)
    mc.move_z_to_usteps.assert_called_once_with(-10000 + stage.z_mm_to_usteps(0.001))
    mc.move_z_usteps_retry_last.assert_not_called()

    stage, mc = _settle_stage(z_usteps=-10000)
    mc.wait_till_operation_is_completed.side_effect = [missed, None]
    stage.move_z_to(stage.get_pos().z_mm + 0.001)
    assert mc.move_z_to_usteps.call_count == 2
    assert mc.move_z_to_usteps.call_args_list[0] == mc.move_z_to_usteps.call_args_list[1]
    mc.move_z_usteps_retry_last.assert_not_called()


def test_unknown_strategy_and_approach_are_rejected_by_the_config():
    with pytest.raises(ValueError):
        PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY="servo")
    with pytest.raises(ValueError):
        PIDConfig(ENABLED=True, P=1, I=0, D=0, MOVE_SETTLE_APPROACH="up")
    assert PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY=" Move-and-settle ").STRATEGY == "move_settle"
    assert PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY="move_settle").STRATEGY == "move_settle"
    assert PIDConfig(ENABLED=True, P=1, I=0, D=0, STRATEGY="PID").STRATEGY == "pid"
