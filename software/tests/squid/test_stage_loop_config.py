"""CephlaStage applies the encoder, closed-loop and ramp settings from the axis config to the controller.

These are the settings the Z bench work established (firmware 1.6): encoder direction flag, PID gains,
correction clamp and deviation watchdog, home exclusion zone, deadband, ramp profile. Before this the stage
config carried PID=None and never passed the encoder flip, so none of it reached the firmware.
"""

import logging
from unittest.mock import MagicMock

import control._def as _def
import squid.config
from squid.config import AxisConfig, PIDConfig, DirectionSign
from squid.stage.cephla import CephlaStage


def _axis(**overrides) -> AxisConfig:
    base = dict(
        MOVEMENT_SIGN=DirectionSign.DIRECTION_SIGN_NEGATIVE,
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
        PID=None,
    )
    base.update(overrides)
    return AxisConfig(**base)


def _stage(z_axis: AxisConfig, firmware=(1, 6)):
    mc = MagicMock()
    mc.firmware_version = firmware
    cfg = squid.config.get_stage_config().model_copy(update={"Z_AXIS": z_axis})
    # X and Y in the process-wide config have no encoder and PID disabled: they must stay silent.
    stage = CephlaStage(mc, cfg)
    return stage, mc


def test_full_bench_configuration_reaches_the_controller_in_order():
    z = _axis(
        HAS_ENCODER=True,
        ENCODER_FLIP_DIR=True,
        RAMP_PROFILE="trapezoid",
        PID=PIDConfig(
            ENABLED=True,
            P=65535,
            I=0,
            D=0,
            CORRECTION_VMAX=1.0,
            MAX_DEVIATION_UM=200,
            HOME_ZONE_UM=200,
            TOLERANCE_UM=0.2,
            OPEN_ABOVE_MM_S=3.5,
        ),
        COMPLETION_WINDOW_UM=0.3,
    )
    _, mc = _stage(z)
    # the window is a state and is sent for every stage axis (X and Y get 0); Z gets its value
    assert mc.set_completion_window.call_args_list[-1].args == (_def.AXIS.Z, 0.3 / 1000.0)
    mc.set_pid_open_above.assert_called_once_with(_def.AXIS.Z, 3.5)
    mc.set_ramp_profile.assert_called_once_with(_def.AXIS.Z, _def.RAMP_PROFILE.TRAPEZOID)
    mc.configure_stage_pid.assert_called_once_with(
        axis=_def.AXIS.Z, transitions_per_revolution=3000, flip_direction=True  # rounded, not 2999.999...
    )
    mc.set_pid_arguments.assert_called_once_with(_def.AXIS.Z, 65535, 0, 0)
    mc.set_pid_limits.assert_called_once_with(_def.AXIS.Z, 1.0, 200)
    mc.set_pid_home_zone.assert_called_once_with(_def.AXIS.Z, 200)
    mc.set_pid_tolerance.assert_called_once_with(_def.AXIS.Z, 0.2, 0.2)
    mc.turn_on_stage_pid.assert_called_once_with(_def.AXIS.Z)
    names = [c[0] for c in mc.method_calls if c[0] in ("configure_stage_pid", "set_pid_arguments", "turn_on_stage_pid")]
    assert names == ["configure_stage_pid", "set_pid_arguments", "turn_on_stage_pid"]


def test_encoder_without_loop_is_configured_but_not_enabled():
    z = _axis(HAS_ENCODER=True, ENCODER_FLIP_DIR=True, PID=PIDConfig(ENABLED=False, P=4096, I=0, D=0))
    _, mc = _stage(z)
    mc.configure_stage_pid.assert_called_once()
    assert mc.configure_stage_pid.call_args.kwargs["flip_direction"] is True
    mc.set_pid_arguments.assert_not_called()
    mc.turn_on_stage_pid.assert_not_called()
    mc.set_pid_limits.assert_not_called()


def test_no_encoder_sends_nothing_for_the_axis():
    _, mc = _stage(_axis())
    mc.configure_stage_pid.assert_not_called()
    mc.set_ramp_profile.assert_not_called()
    mc.turn_on_stage_pid.assert_not_called()


def test_use_encoder_alone_still_configures_the_encoder():
    _, mc = _stage(_axis(USE_ENCODER=True))
    mc.configure_stage_pid.assert_called_once()
    assert mc.configure_stage_pid.call_args.kwargs["flip_direction"] is False


def test_zero_limits_are_not_sent():
    z = _axis(HAS_ENCODER=True, PID=PIDConfig(ENABLED=True, P=16384, I=0, D=0))
    _, mc = _stage(z)
    mc.set_pid_limits.assert_not_called()
    mc.set_pid_home_zone.assert_not_called()
    mc.set_pid_tolerance.assert_not_called()
    # loop mode and completion window are states: 0 (rest-only / exact target) is sent explicitly
    mc.set_pid_open_above.assert_called_once_with(_def.AXIS.Z, 0.0)
    assert mc.set_completion_window.call_args_list[-1].args == (_def.AXIS.Z, 0.0)
    mc.turn_on_stage_pid.assert_called_once()


def test_old_firmware_enables_the_loop_but_skips_the_new_commands():
    z = _axis(
        HAS_ENCODER=True,
        RAMP_PROFILE="trapezoid",
        PID=PIDConfig(
            ENABLED=True,
            P=16384,
            I=0,
            D=0,
            CORRECTION_VMAX=1.0,
            MAX_DEVIATION_UM=200,
            HOME_ZONE_UM=200,
            OPEN_ABOVE_MM_S=1.0,
        ),
        COMPLETION_WINDOW_UM=0.3,
    )
    _, mc = _stage(z, firmware=(1, 5))
    mc.set_ramp_profile.assert_not_called()
    mc.set_pid_open_above.assert_not_called()
    mc.set_completion_window.assert_not_called()
    mc.set_pid_limits.assert_not_called()
    mc.set_pid_home_zone.assert_not_called()
    mc.configure_stage_pid.assert_called_once()
    mc.turn_on_stage_pid.assert_called_once()


def test_process_config_carries_the_machine_constants():
    cfg = squid.config.get_stage_config()
    assert cfg.Z_AXIS.PID is not None
    assert cfg.Z_AXIS.PID.ENABLED == bool(_def.ENABLE_PID_Z)
    assert cfg.Z_AXIS.PID.P == _def.PID_P_Z
    assert cfg.Z_AXIS.HAS_ENCODER == bool(_def.HAS_ENCODER_Z)
    assert cfg.Z_AXIS.ENCODER_FLIP_DIR == bool(_def.ENCODER_FLIP_DIR_Z)
    assert cfg.Z_AXIS.RAMP_PROFILE in ("sshape", "trapezoid")
    assert cfg.Z_AXIS.PID.OPEN_ABOVE_MM_S == float(getattr(_def, "PID_OPEN_ABOVE_Z_mm", 0.0))
    assert cfg.Z_AXIS.COMPLETION_WINDOW_UM == float(getattr(_def, "COMPLETION_WINDOW_Z_UM", 0.0))


def test_a_loop_kept_engaged_in_flight_is_flagged_as_unqualified(caplog):
    """pid_open_above > 0 leaves the loop closed for the whole of any move slower than the threshold. That
    mode limit-cycled and stalled the motor on both bench stages once a move cruised beyond ~0.1 s
    (2026-09-08), so an ini that asks for it has to say so at startup, not through a stalled Z."""
    z = _axis(
        HAS_ENCODER=True,
        PID=PIDConfig(ENABLED=True, P=65535, I=0, D=0, CORRECTION_VMAX=1.0, MAX_DEVIATION_UM=200, OPEN_ABOVE_MM_S=1.0),
    )
    with caplog.at_level(logging.WARNING, logger="squid"):
        _stage(z)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "UNQUALIFIED" in r.getMessage()]
    assert warned, [r.getMessage() for r in caplog.records]
    assert "pid_open_above_z_mm = 1.0" in warned[0].getMessage()


def test_rest_only_is_the_qualified_mode_and_warns_about_nothing(caplog):
    z = _axis(
        HAS_ENCODER=True,
        PID=PIDConfig(ENABLED=True, P=65535, I=0, D=0, CORRECTION_VMAX=1.0, MAX_DEVIATION_UM=200, OPEN_ABOVE_MM_S=0.0),
    )
    with caplog.at_level(logging.WARNING, logger="squid"):
        _stage(z)
    assert not [r for r in caplog.records if "UNQUALIFIED" in r.getMessage()], [r.getMessage() for r in caplog.records]
