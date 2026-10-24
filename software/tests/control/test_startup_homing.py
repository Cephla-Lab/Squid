from unittest.mock import MagicMock

import pytest

import control._def
import squid.config
import squid.stage.cephla
import squid.stage.prior
import tests.squid.test_stage
from control.startup_homing import ObjectiveNotLoweredError
from tests.control.test_microcontroller import get_test_micro


@pytest.fixture
def piezo_only(monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", True)


def _spy_commands(micro):
    sent = []
    real = micro.send_command
    micro.send_command = lambda cmd: (sent.append(cmd[1]), real(cmd))[1]
    return sent


def test_xy_homes_refused_without_confirmation_on_piezo_only(piezo_only):
    micro = get_test_micro()
    sent = _spy_commands(micro)
    for home in (micro.home_x, micro.home_y, micro.home_xy):
        with pytest.raises(ObjectiveNotLoweredError, match="objective_lowered=true"):
            home()
    assert sent == []


def test_confirmed_xy_home_runs_on_piezo_only(piezo_only):
    micro = get_test_micro()
    micro.move_x_to_usteps(1000)
    micro.wait_till_operation_is_completed()
    micro.home_x(objective_lowered=True)
    micro.wait_till_operation_is_completed()
    assert micro.get_pos()[0] == 0


def test_z_home_needs_no_confirmation(piezo_only):
    micro = get_test_micro()
    micro.home_z()
    micro.wait_till_operation_is_completed()


def test_xy_homes_unchanged_off_piezo_only(monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", False)
    micro = get_test_micro()
    micro.home_xy()
    micro.wait_till_operation_is_completed()


def test_refused_cephla_home_sends_nothing_even_with_z(piezo_only):
    micro = get_test_micro()
    sent = _spy_commands(micro)
    stage = squid.stage.cephla.CephlaStage(micro, squid.config.get_stage_config())
    with pytest.raises(ObjectiveNotLoweredError):
        stage.home(x=True, y=False, z=True, theta=False)
    assert sent == []


def test_stage_confirmation_covers_one_call_only(piezo_only):
    stage = squid.stage.cephla.CephlaStage(get_test_micro(), squid.config.get_stage_config())
    stage.home(x=True, y=True, z=False, theta=False, objective_lowered=True)
    with pytest.raises(ObjectiveNotLoweredError):
        stage.home(x=False, y=True, z=False, theta=False)


def test_prior_home_always_needs_confirmation_on_piezo_only(piezo_only):
    stage = object.__new__(squid.stage.prior.PriorStage)
    stage._send_command = MagicMock()
    stage.wait_for_stop = MagicMock()
    with pytest.raises(ObjectiveNotLoweredError):
        stage.home(x=False, y=False, z=True, theta=False)
    stage._send_command.assert_not_called()
    stage.home(x=True, y=True, z=False, theta=False, objective_lowered=True)
    stage._send_command.assert_called_once_with("SIS")


def test_combined_stage_checks_before_z_or_lift(piezo_only):
    stage, _, _ = tests.squid.test_stage._sim_combined_stage()
    stage._z.home = MagicMock()
    stage._lower_lift_for_xy_homing = MagicMock()
    with pytest.raises(ObjectiveNotLoweredError):
        stage.home(x=True, y=False, z=True, theta=False)
    stage._z.home.assert_not_called()
    stage._lower_lift_for_xy_homing.assert_not_called()
    stage.home(x=True, y=False, z=False, theta=False, objective_lowered=True)
