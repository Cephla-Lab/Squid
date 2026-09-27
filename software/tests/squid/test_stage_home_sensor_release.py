"""After a Z homing CephlaStage takes the stage past the home sensor's release point and back to the floor.

The home sensor is a Hall-effect sensor with hysteresis: asserted at home, it stays asserted until the stage is
_def.Z_HOME_SENSOR_HYSTERESIS_MM further out, and the controller refuses every move toward home while it is
(bench 2026-09-26). Going out is always allowed; once released the sensor trips again only at home itself.
"""

import logging
from unittest.mock import MagicMock

import pytest

import control._def as _def
import squid.config
from squid.config import AxisConfig, DirectionSign, PIDConfig
from squid.stage.cephla import CephlaStage


def _stage(floor_mm=0.05, top_mm=6.0, pid=None):
    axis = AxisConfig(
        MOVEMENT_SIGN=DirectionSign.DIRECTION_SIGN_NEGATIVE,
        USE_ENCODER=False,
        ENCODER_SIGN=DirectionSign.DIRECTION_SIGN_POSITIVE,
        ENCODER_STEP_SIZE=100e-6,
        FULL_STEPS_PER_REV=200,
        SCREW_PITCH=0.3,
        MICROSTEPS_PER_STEP=16,
        MAX_SPEED=3.0,
        MAX_ACCELERATION=300,
        MIN_POSITION=floor_mm,
        MAX_POSITION=top_mm,
        HAS_ENCODER=True,
        RAMP_PROFILE="trapezoid",
        PID=pid or PIDConfig(ENABLED=False, P=1, I=0, D=0),
    )
    mc = MagicMock()
    mc.firmware_version = (1, 7)
    mc.pid_fault_axes.return_value = set()
    mc.get_pos.return_value = (0, 0, 0, 0)
    stage = CephlaStage(mc, squid.config.get_stage_config().model_copy(update={"Z_AXIS": axis}))
    mc.reset_mock()
    mc.get_pos.return_value = (0, 0, 0, 0)
    return stage, mc


def _z_targets_mm(stage, mc):
    """the absolute Z targets sent after the homing, in mm"""
    per_mm = stage.z_mm_to_usteps(1.0)
    return [round(c.args[0] / per_mm, 4) for c in mc.move_z_to_usteps.call_args_list]


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 0.0, raising=False)
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_RELEASE_MARGIN_MM", 0.2, raising=False)
    monkeypatch.setattr(_def, "Z_PARK_AT_MIN_AFTER_HOMING", False, raising=False)


def test_without_a_hysteresis_a_homing_moves_nothing_more():
    stage, mc = _stage()
    stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    mc.home_z.assert_called_once()
    mc.move_z_to_usteps.assert_not_called()
    mc.move_z_usteps.assert_not_called()


def test_the_release_goes_out_past_the_band_and_comes_back_to_the_floor(monkeypatch):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    stage, mc = _stage(floor_mm=0.05)
    stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    names = [c[0] for c in mc.method_calls]
    assert names.index("home_z") < names.index("move_z_to_usteps")
    targets = _z_targets_mm(stage, mc)
    assert targets[0] == pytest.approx(1.2)          # hysteresis + margin: the sensor lets go on the way
    assert targets[-1] == pytest.approx(0.05, abs=1e-3)   # the floor; an open-loop Z may go past it and back first
    assert all(t > 0 for t in targets)               # never to home itself: that asserts the sensor again


def test_the_park_at_the_floor_is_not_done_twice(monkeypatch):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    monkeypatch.setattr(_def, "Z_PARK_AT_MIN_AFTER_HOMING", True)
    stage, mc = _stage(floor_mm=0.75)
    stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    targets = _z_targets_mm(stage, mc)
    assert targets[0] == pytest.approx(1.2)
    assert sum(1 for t in targets if t == pytest.approx(0.75, abs=1e-3)) == 1


def test_only_z_is_released(monkeypatch):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    stage, mc = _stage()
    stage.home(x=True, y=True, z=False, theta=False, blocking=True)
    mc.move_z_to_usteps.assert_not_called()


def test_a_non_blocking_homing_cannot_release_and_says_so(monkeypatch, caplog):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    stage, mc = _stage()
    with caplog.at_level(logging.WARNING):
        stage.home(x=False, y=False, z=True, theta=False, blocking=False)
    mc.move_z_to_usteps.assert_not_called()
    assert "home sensor is still asserted" in caplog.text


def test_a_release_point_beyond_the_travel_is_refused_with_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    stage, mc = _stage(top_mm=1.1)
    with caplog.at_level(logging.WARNING):
        stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    mc.move_z_to_usteps.assert_not_called()
    assert "beyond the Z travel" in caplog.text


def test_without_a_floor_above_home_the_stage_stays_out(monkeypatch):
    monkeypatch.setattr(_def, "Z_HOME_SENSOR_HYSTERESIS_MM", 1.0)
    stage, mc = _stage(floor_mm=0.0)
    stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    assert _z_targets_mm(stage, mc) == [pytest.approx(1.2)]
