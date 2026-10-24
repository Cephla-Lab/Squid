import threading
import time
from unittest.mock import MagicMock

import pytest

import control._def
import control.microscope
import squid.config
import squid.stage.cephla
import squid.stage.prior
import tests.squid.test_stage
from control.startup_homing import ObjectiveNotLoweredError, StageHomingInProgressError
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


def _scope(**kw):
    return control.microscope.Microscope.build_from_global_config(True, skip_init=True, **kw)


def _wait(predicate, timeout_s=3.0):
    end = time.time() + timeout_s
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_home_stage_finishes_at_the_safe_position(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    scope = _scope()
    scope.stage.move_x_to(40)
    scope.stage.move_y_to(25)
    scope.home_stage()
    pos = scope.stage.get_pos()
    # Where home_xyz ends, not 40/25; within 1 um, as a mm target lands on the nearest microstep.
    assert pos.x_mm == pytest.approx(50, abs=1e-3) and pos.y_mm == pytest.approx(0, abs=1e-3)
    assert pos.z_mm == pytest.approx(int(control._def.Z_HOME_SAFETY_POINT) / 1000.0, abs=1e-3)
    assert not scope.stage_homing_in_progress
    scope.close()


def test_unconfirmed_home_moves_nothing(piezo_only):
    scope = _scope()
    sent = _spy_commands(scope.low_level_drivers.microcontroller)
    for home in (scope.home_xyz, scope.home_stage):
        with pytest.raises(ObjectiveNotLoweredError):
            home()
    assert sent == [] and not scope.stage_homing_in_progress
    scope.close()


def test_claim_refuses_other_motion():
    scope = _scope()
    claim = scope.claim_stage_homing("test")
    for refused in (
        scope.home_xyz,
        scope.home_stage,
        lambda: scope.move_x_to(1.0),
        lambda: scope.move_y(1.0),
        lambda: scope.move_z(0.1),
        lambda: scope.move_z_to(1.0),
        lambda: scope.set_objective("any"),
    ):
        with pytest.raises(StageHomingInProgressError):
            refused()
    claim.release_when_stopped()
    assert not scope.stage_homing_in_progress
    scope.close()


def test_release_is_idempotent():
    scope = _scope()
    events = []
    scope.add_stage_homing_listener(events.append)
    claim = scope.claim_stage_homing("test")
    claim.release_when_stopped()
    claim.release_when_stopped()
    assert events == [True, False] and not scope.stage_homing_in_progress
    scope.close()


def test_a_failed_live_pause_gives_the_claim_back(monkeypatch):
    scope = _scope()
    scope.start_live()
    monkeypatch.setattr(scope, "stop_live", MagicMock(side_effect=RuntimeError("camera gone")))
    with pytest.raises(RuntimeError, match="camera gone"):
        scope.claim_stage_homing("test")
    assert not scope.stage_homing_in_progress
    monkeypatch.undo()
    scope.close()


def test_a_claim_taken_the_instant_a_release_ends_sees_live_resumed(monkeypatch):
    scope = _scope()
    monkeypatch.setattr(scope.stage, "reports_stopped", lambda: True)  # ordering under test, not stop detection
    events = []
    scope.add_stage_homing_listener(events.append)
    scope.start_live()
    claim_a = scope.claim_stage_homing("A")
    real_clear = scope._clear_stage_homing_claim
    taken = []

    def clear_then_claim(claim):
        real_clear(claim)
        if claim is claim_a:  # another client wins the race the moment A's claim is cleared
            taken.append(scope.claim_stage_homing("B"))

    monkeypatch.setattr(scope, "_clear_stage_homing_claim", clear_then_claim)
    claim_a.release_when_stopped()
    (claim_b,) = taken
    assert events == [True, False, True]  # False(A) before True(B)
    assert claim_b.was_live  # A resumed live before B could claim, so B paused it and will resume it
    assert scope.stage_homing_in_progress and not scope.live_controller.is_live  # no live during B's homing
    claim_b.release_when_stopped()
    assert events == [True, False, True, False] and scope.live_controller.is_live
    scope.close()


def test_a_claim_from_a_listener_during_the_release_is_refused_without_deadlock(monkeypatch):
    scope = _scope()
    monkeypatch.setattr(scope.stage, "reports_stopped", lambda: True)
    scope.start_live()
    claim_a = scope.claim_stage_homing("A")
    outcomes = []

    def claim_on_release(active):
        if not active:
            try:
                scope.claim_stage_homing("B")
                outcomes.append("claimed")
            except StageHomingInProgressError:
                outcomes.append("refused")

    scope.add_stage_homing_listener(claim_on_release)
    release = threading.Thread(target=claim_a.release_when_stopped, daemon=True)
    release.start()
    release.join(5)
    assert not release.is_alive()  # no lock is held while listeners run
    assert outcomes == ["refused"]  # A's claim is held until its listeners and the live resume are done
    assert not scope.stage_homing_in_progress and scope.live_controller.is_live
    scope.close()


def test_homing_pauses_live_and_resumes_only_when_stopped():
    scope = _scope()
    events = []
    scope.add_stage_homing_listener(events.append)
    scope.start_live()
    scope.home_stage()
    assert events == [True, False] and scope.live_controller.is_live
    scope.stop_live()
    scope.home_stage()
    assert not scope.live_controller.is_live  # was not running: not resumed
    scope.close()


def test_timeout_keeps_the_claim_until_the_stage_stops(monkeypatch):
    scope = _scope()
    sim = scope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    scope.start_live()
    sim.hold_in_progress = True
    with pytest.raises(TimeoutError):
        scope.home_stage()
    assert scope.stage_homing_in_progress and not scope.live_controller.is_live
    with pytest.raises(StageHomingInProgressError):
        scope.move_x_to(1.0)
    sim.hold_in_progress = False
    sim.send_status_packet()  # the hardware reports it stopped
    assert _wait(lambda: not scope.stage_homing_in_progress)
    assert _wait(lambda: scope.live_controller.is_live)
    scope.close()


def test_host_abort_keeps_the_claim(monkeypatch):
    scope = _scope()
    micro = scope.low_level_drivers.microcontroller
    sim = micro._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    scope.start_live()
    sim.hold_in_progress = True
    with pytest.raises(TimeoutError):
        scope.home_stage()
    micro.abort_current_command("test: host gives up waiting")
    assert not scope.stage.get_state().busy  # the host flag now says idle: not evidence
    sim.send_status_packet()  # the firmware still reports IN_PROGRESS
    time.sleep(3 * control.microscope.STAGE_STOP_POLL_S)
    assert scope.stage_homing_in_progress and not scope.live_controller.is_live
    with pytest.raises(StageHomingInProgressError):
        scope.move_x_to(1.0)
    sim.hold_in_progress = False
    sim.send_status_packet()
    assert _wait(lambda: not scope.stage_homing_in_progress) and _wait(lambda: scope.live_controller.is_live)
    scope.close()


def test_idle_needs_a_fresh_controller_report():
    micro = get_test_micro()
    sim = micro._serial
    micro.move_x_usteps(10)
    micro.wait_till_operation_is_completed()
    assert micro.reported_idle_since_last_command()  # COMPLETED, received after the send
    sim.drop_responses = True
    micro.move_x_usteps(10)  # lost link: the old COMPLETED is stale
    assert not micro.reported_idle_since_last_command()
    sim.drop_responses = False
    sim.hold_in_progress = True
    micro.home_z()
    time.sleep(0.05)
    assert not micro.reported_idle_since_last_command()  # IN_PROGRESS
    sim.hold_in_progress = False
    sim.send_status_packet()
    assert _wait(micro.reported_idle_since_last_command)


def test_prior_and_pi_report_stopped_only_on_a_fresh_query():
    prior = object.__new__(squid.stage.prior.PriorStage)
    prior._send_command = MagicMock(return_value="0")
    assert prior.reports_stopped()
    prior._send_command = MagicMock(return_value="1")
    assert not prior.reports_stopped()
    prior._send_command = MagicMock(side_effect=OSError("link lost"))
    assert not prior.reports_stopped()
    pi = tests.squid.test_stage._sim_pi_stage()
    pi._c414.is_moving = MagicMock(side_effect=OSError("link lost"))
    assert not pi.reports_stopped()
