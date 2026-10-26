import logging
import pathlib
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

import control._def
import control.microcontroller
import control.microscope
import squid.config
import squid.stage.cephla
import squid.stage.prior
import tests.squid.test_stage
from control._def import AXIS, CMD_SET, STARTUP_STATE_MIN_FIRMWARE, STARTUP_VERDICT_STATUS_BASE, StartupVerdict
from control.firmware_sim_serial import FirmwareSimSerial
from control.microcontroller import Microcontroller
from control.startup_homing import (
    CONFIG_SIGNATURE_FORMAT,
    ObjectiveNotLoweredError,
    StageHomingInProgressError,
    StageHomingRefused,
    StartupHoming,
    config_signature,
    config_signature_text,
    home_stage_confirmation_text,
    required_homed_axes_mask,
)
from tests.control.test_microcontroller import get_test_micro

SOFTWARE_DIR = pathlib.Path(__file__).resolve().parents[2]
NOT_STOPPED_ERROR = "Homing ended without the controller reporting the stage stopped"


@pytest.fixture
def piezo_only(monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", True)


def _spy_commands(micro):
    """The codes of the commands sent from now on, but the watchdog heartbeat's (its own thread, every 2.5 s)."""
    sent = []
    real = micro.send_command

    def spy(cmd):
        if cmd[1] != control._def.CMD_SET.HEARTBEAT:
            sent.append(cmd[1])
        return real(cmd)

    micro.send_command = spy
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


def test_timeout_keeps_the_claim_until_the_stage_stops(monkeypatch, caplog):
    scope = _scope()
    sim = scope.low_level_drivers.microcontroller._serial
    monkeypatch.setattr(squid.stage.cephla.CephlaStage, "_calc_move_timeout", staticmethod(lambda d, s: 0.3))
    scope.start_live()
    sim.hold_in_progress = True
    with caplog.at_level(logging.ERROR), pytest.raises(TimeoutError):
        scope.home_stage()
    # A real one: still logged, once, after the grace for a late report
    assert [r.levelno for r in caplog.records if NOT_STOPPED_ERROR in r.getMessage()] == [logging.ERROR]
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


def test_startup_homes_and_reports_homed():
    scope = control.microscope.Microscope.build_from_global_config(True)
    assert scope.startup_homing is StartupHoming.HOMED
    scope.close()


def test_skip_init_reports_not_run():
    scope = _scope()
    assert scope.startup_homing is StartupHoming.NOT_RUN
    scope.close()


def test_piezo_only_startup_with_no_one_to_ask_refuses(piezo_only):
    with pytest.raises(ObjectiveNotLoweredError, match="confirm_objective_lowered"):
        control.microscope.Microscope.build_from_global_config(True)


def test_piezo_only_cancel_declines_and_homes_nothing(piezo_only, monkeypatch):
    home = MagicMock()
    monkeypatch.setattr(control.microscope.Microscope, "home_xyz", home)
    scope = control.microscope.Microscope.build_from_global_config(True, confirm_objective_lowered=lambda: False)
    assert scope.startup_homing is StartupHoming.DECLINED
    home.assert_not_called()
    scope.close()


def test_piezo_only_confirm_asks_once_and_homes(piezo_only):
    asked = []
    scope = control.microscope.Microscope.build_from_global_config(
        True, confirm_objective_lowered=lambda: asked.append(1) or True
    )
    assert asked == [1] and scope.startup_homing is StartupHoming.HOMED
    scope.close()


# --- One exclusion for every claim (set_stage_homing_blocker) ---


def test_a_blocker_refuses_every_claim_with_its_reason_before_anything_moves():
    scope = _scope()
    events = []
    scope.add_stage_homing_listener(events.append)
    scope.set_stage_homing_blocker(lambda: "an objective change is in progress")
    sent = _spy_commands(scope.low_level_drivers.microcontroller)
    with pytest.raises(StageHomingRefused, match="^test refused: an objective change is in progress$") as refused:
        scope.claim_stage_homing("test")
    assert refused.value.reason == "an objective change is in progress"
    for home in (scope.home_xyz, scope.home_stage):
        with pytest.raises(StageHomingRefused, match="an objective change is in progress"):
            home()
    assert sent == [] and events == [] and not scope.stage_homing_in_progress  # no claim taken, nothing moved
    scope.set_stage_homing_blocker(lambda: None)
    scope.claim_stage_homing("test").release_when_stopped()
    scope.set_stage_homing_blocker(None)
    scope.claim_stage_homing("test").release_when_stopped()
    assert events == [True, False, True, False]
    scope.close()


# --- No false alarm from a release that the stage confirms a moment later ---


def test_startup_without_homing_releases_its_claim_with_no_false_alarm(monkeypatch, caplog):
    # The last startup command (SET_LIM) is not awaited: its echo can still be in flight when the claim is released.
    for name in ("HOMING_ENABLED_X", "HOMING_ENABLED_Y", "HOMING_ENABLED_Z"):
        monkeypatch.setattr(control._def, name, False)
    for _ in range(3):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            scope = control.microscope.Microscope.build_from_global_config(True)
        try:
            assert not scope.stage_homing_in_progress  # released by the time the constructor returns
            assert not [r for r in caplog.records if NOT_STOPPED_ERROR in r.getMessage()]
        finally:
            scope.close()


def test_a_refusal_release_logs_no_error(caplog):
    scope = _scope()
    sim = scope.low_level_drivers.microcontroller._serial
    sim.hold_in_progress = True
    scope.stage.move_x(0.5, blocking=False)  # a move still under way
    claim = scope.claim_stage_homing("Home X")
    with caplog.at_level(logging.INFO):
        claim.release_when_stopped(refusal="the stage is still moving")
    assert scope.stage_homing_in_progress  # still released only on the controller's report
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.levelno for r in caplog.records if "the stage is still moving" in r.getMessage()] == [logging.INFO]
    sim.hold_in_progress = False
    sim.send_status_packet()
    assert _wait(lambda: not scope.stage_homing_in_progress)
    scope.close()


# --- Wording ---


def test_each_home_refuses_under_its_own_name(piezo_only):
    scope = _scope()
    with pytest.raises(ObjectiveNotLoweredError, match="^Home XYZ refused"):
        scope.home_xyz()
    with pytest.raises(ObjectiveNotLoweredError, match="^Home stage refused"):
        scope.home_stage()
    scope.close()


@pytest.mark.parametrize(
    "pi_focus, z_lift, expected",
    [
        (False, False, "X and Y move to their limit switches."),
        (True, False, "The objective retracts, then X and Y move to their limit switches."),
        (True, True, "The Z lift lowers and the objective retracts, then X and Y move to their limit switches."),
    ],
)
def test_home_stage_confirmation_says_what_moves_first(monkeypatch, pi_focus, z_lift, expected):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", pi_focus)
    assert home_stage_confirmation_text(has_z_lift=z_lift) == (
        f"{expected} The stage will not return to where it is now."
    )


def test_home_stage_confirmation_with_a_stepper_z(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", True)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", False)
    assert home_stage_confirmation_text(has_z_lift=False) == (
        "X, Y and Z move to their limit switches. The stage will not return to where it is now."
    )


# Startup state protocol (firmware 1.9): STORE_CONFIG_SIGNATURE / QUERY_STARTUP_STATE, verdict in the status byte.


@pytest.fixture
def new_firmware_micro():
    sim = FirmwareSimSerial(strict=True, firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    micro = Microcontroller(sim, reset_and_initialize=False)
    yield micro, sim
    micro.close()


def test_startup_state_needs_its_firmware():
    sim = FirmwareSimSerial(strict=True, firmware_version=(1, 8))
    micro = Microcontroller(sim, reset_and_initialize=False)
    try:
        assert not micro.supports_startup_state()
        sim.command_log.clear()
        with pytest.raises(RuntimeError, match="1.8"):
            micro.store_config_signature(0x12345678, 1)
        with pytest.raises(RuntimeError, match="1.8"):
            micro.query_startup_state(0x12345678, 1, 0b011)
        assert sim.command_log == []
    finally:
        micro.close()


@pytest.mark.parametrize(
    "signature, signature_format, mask",
    [(-1, 1, 0), (1 << 32, 1, 0), (0, -1, 0), (0, 16, 0), (0, 1, -1), (0, 1, 0b1000)],
)
def test_startup_state_arguments_must_fit_the_wire(new_firmware_micro, signature, signature_format, mask):
    micro, sim = new_firmware_micro
    sim.command_log.clear()
    with pytest.raises(ValueError):
        micro.query_startup_state(signature, signature_format, mask)
    if mask == 0:
        with pytest.raises(ValueError):
            micro.store_config_signature(signature, signature_format)
    assert sim.command_log == []


def test_store_home_query_is_warm(new_firmware_micro):
    micro, sim = new_firmware_micro
    assert micro.supports_startup_state()
    micro.store_config_signature(0x12345678, 1)
    assert sim.command_log[-1] == CMD_SET.STORE_CONFIG_SIGNATURE and not micro.is_busy()  # waited for completion
    assert micro.query_startup_state(0x12345678, 1, 0b011) is StartupVerdict.NOT_HOMED
    micro.home_xy()
    micro.wait_till_operation_is_completed()
    assert micro.query_startup_state(0x12345678, 1, 0b011) is StartupVerdict.WARM
    assert micro.query_startup_state(0x12345679, 1, 0b011) is StartupVerdict.SIGNATURE_MISMATCH
    assert micro.query_startup_state(0x12345678, 2, 0b011) is StartupVerdict.SIGNATURE_MISMATCH
    assert micro.query_startup_state(0x12345678, 1, 0b111) is StartupVerdict.NOT_HOMED


def test_query_never_takes_an_earlier_verdict():
    micro = Microcontroller(FirmwareSimSerial(firmware_version=(1, 8)), reset_and_initialize=False)
    try:
        micro.firmware_version = STARTUP_STATE_MIN_FIRMWARE  # host believes it; the sim sends no verdict
        micro._startup_verdict_packet = (
            (micro._cmd_id + 1) % 256,
            int(StartupVerdict.WARM),
        )  # stale; same id as the next send
        with pytest.raises(RuntimeError, match="no startup verdict"):
            micro.query_startup_state(0x12345678, 1, 0b011)
    finally:
        micro.close()


def test_host_query_returns_during_genuine_in_progress(new_firmware_micro):
    micro, sim = new_firmware_micro  # constructed first: its tail legitimately waits for completion
    sim.in_progress = True
    sim.moving_axes.add(AXIS.X)
    t0 = time.time()
    assert micro.query_startup_state(0x12345678, 1, 0b011) is StartupVerdict.NO_SIGNATURE
    assert time.time() - t0 < 1.0 and micro.is_busy()  # answered without waiting for completion


@pytest.mark.parametrize("verdict", list(StartupVerdict))
def test_a_verdict_status_never_completes_aborts_resends_or_counts_as_idle(new_firmware_micro, verdict):
    micro, sim = new_firmware_micro
    micro.LAST_COMMAND_ACK_TIMEOUT = 0.01  # the ack-timeout resend would fire on the next packet
    assert micro.query_startup_state(0x12345678, 1, 0b011) is StartupVerdict.NO_SIGNATURE
    sent = list(sim.command_log)
    time.sleep(0.05)
    for _ in range(3):  # the firmware repeats the verdict in every packet until the next command
        parsed = threading.Event()
        micro.set_callback(lambda m: parsed.set())
        with sim._update_lock:
            sim.response_buffer.extend(sim._build_response(micro._cmd_id, STARTUP_VERDICT_STATUS_BASE + verdict))
            sim._update_internal_state()
        assert parsed.wait(2)
        assert micro._startup_verdict_packet == (micro._cmd_id, verdict)
        assert micro.is_busy() and micro.last_command_aborted_error is None
        assert not micro.reported_idle_since_last_command()
    assert sim.command_log == sent  # nothing resent


def test_signature_is_the_same_in_another_process():
    out = subprocess.run(
        [sys.executable, "-c", "import control.startup_homing as s; print(s.config_signature())"],
        cwd=SOFTWARE_DIR,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(out) == config_signature()


def test_signature_text_starts_with_the_format_and_covers_every_axis():
    lines = config_signature_text().split("\n")
    assert lines[0] == "format=1"
    assert len(lines) == 1 + 3 * 13
    assert all("=" in line for line in lines)


@pytest.mark.parametrize(
    "name",
    [
        "SCREW_PITCH_X_MM",
        "MICROSTEPPING_DEFAULT_Y",
        "Z_MOTOR_RMS_CURRENT_mA",
        "X_MOTOR_I_HOLD",
        "MAX_VELOCITY_Y_mm",
        "MAX_ACCELERATION_Z_mm",
        "X_HOME_SAFETY_MARGIN_UM",
    ],
)
def test_each_def_setting_changes_the_signature(monkeypatch, name):
    before = config_signature()
    monkeypatch.setattr(control._def, name, getattr(control._def, name) * 2 + 1)
    assert config_signature() != before


@pytest.mark.parametrize(
    "field, value",
    [
        ("MOVEMENT_SIGN", "flip"),
        ("USE_ENCODER", "flip"),
        ("ENCODER_SIGN", "flip"),
        ("ENCODER_STEP_SIZE", 0.123),
        ("FULL_STEPS_PER_REV", 400.0),
    ],
)
def test_each_host_only_setting_changes_the_signature(monkeypatch, field, value):
    before = config_signature()
    config = squid.config.get_stage_config()
    axis = config.Y_AXIS
    if value == "flip":
        current = getattr(axis, field)
        value = (not current) if isinstance(current, bool) else type(current)(-current.value)
    new_axis = axis.model_copy(update={field: value})
    monkeypatch.setattr(squid.config, "_stage_config", config.model_copy(update={"Y_AXIS": new_axis}))
    assert config_signature() != before


def test_limit_switch_config_changes_the_signature(monkeypatch):
    before = config_signature()
    real = control._def.limit_switch_config_byte
    monkeypatch.setattr(control._def, "limit_switch_config_byte", lambda a: real(a) ^ 0x01 if a == "z" else real(a))
    assert config_signature() != before


def test_equal_values_written_differently_sign_the_same(monkeypatch):
    monkeypatch.setattr(control._def, "SCREW_PITCH_X_MM", 2.54)
    a = config_signature()
    monkeypatch.setattr(control._def, "SCREW_PITCH_X_MM", float("2.540"))
    assert config_signature() == a
    monkeypatch.setattr(control._def, "X_MOTOR_I_HOLD", 0.5)
    b = config_signature()
    monkeypatch.setattr(control._def, "X_MOTOR_I_HOLD", 0.50)
    assert config_signature() == b


def test_format_version_changes_the_signature(monkeypatch):
    import control.startup_homing

    before = config_signature()
    monkeypatch.setattr(control.startup_homing, "CONFIG_SIGNATURE_FORMAT", 2)
    assert config_signature() != before


@pytest.mark.parametrize(
    "z, pi, lift, mask",
    [(True, False, False, 0b111), (False, False, False, 0b011), (True, True, False, 0b011), (False, True, True, 0b111)],
)
def test_required_axes(monkeypatch, z, pi, lift, mask):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", True)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", z)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", pi)
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", lift)
    assert required_homed_axes_mask() == mask


def test_required_axes_needs_both_x_and_y_homing(monkeypatch):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", True)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", False)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", False)
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", False)
    assert required_homed_axes_mask() == 0


# --- The startup decision (B5): warm only when the controller vouches for the homing under this configuration ---

COVERED_SETTINGS = (
    CMD_SET.SET_LEAD_SCREW_PITCH,
    CMD_SET.CONFIGURE_STEPPER_DRIVER,
    CMD_SET.SET_MAX_VELOCITY_ACCELERATION,
    CMD_SET.SET_HOME_SAFETY_MERGIN,
    CMD_SET.SET_LIMIT_SWITCH_CONFIG,
)
OLDER_FIRMWARE = (STARTUP_STATE_MIN_FIRMWARE[0], STARTUP_STATE_MIN_FIRMWARE[1] - 1)


def _reopen(micro, sim):
    """End a session the way a relaunch finds it: the host closes, the controller stays powered."""
    micro.close()
    sim.reconnect(1)
    sim.command_log.clear()


def _homed(sim=None, before_close=None):
    """A controller that an earlier session started normally (storing the signature) and homed in X, Y and Z."""
    sim = sim or FirmwareSimSerial(firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    micro = Microcontroller(serial_device=sim)
    micro.home_xy(objective_lowered=True)
    micro.wait_till_operation_is_completed()
    micro.home_z()
    micro.wait_till_operation_is_completed()
    if before_close:
        before_close(micro)
    _reopen(micro, sim)
    return sim


def test_first_launch_is_normal_and_stores_the_signature_last():
    sim = FirmwareSimSerial(firmware_version=STARTUP_STATE_MIN_FIRMWARE)
    micro = Microcontroller(serial_device=sim)
    log = sim.command_log
    assert micro.startup_verdict is StartupVerdict.NO_SIGNATURE and not micro.startup_was_warm
    # Nothing but the version detection before the query, and RESET right after it
    assert log[:3] == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.QUERY_STARTUP_STATE, CMD_SET.RESET]
    store = log.index(CMD_SET.STORE_CONFIG_SIGNATURE)
    assert log.index(CMD_SET.INITIALIZE) < log.index(CMD_SET.CONFIGURE_STEPPER_DRIVER) < store
    assert max(i for i, c in enumerate(log) if c == CMD_SET.SET_LIMIT_SWITCH_CONFIG) < store
    assert max(i for i, c in enumerate(log) if c in COVERED_SETTINGS) < store  # the last covered step
    assert log[store + 1 :] == [CMD_SET.SET_FOCUS_WHEEL_MODE, CMD_SET.SET_PANEL_LOCK]
    assert sim.homing_validity.signature == (config_signature(), CONFIG_SIGNATURE_FORMAT)


def test_relaunch_is_warm_and_reconfigures_nothing(caplog):
    sim = _homed(before_close=lambda m: (m.move_x_to_usteps(1000), m.wait_till_operation_is_completed()))
    with caplog.at_level(logging.INFO):
        micro = Microcontroller(serial_device=sim)
    assert micro.startup_verdict is StartupVerdict.WARM and micro.startup_was_warm
    assert "same configuration: startup homing skipped" in caplog.text
    assert sim.command_log[:3] == [CMD_SET.TURN_OFF_ALL_PORTS, CMD_SET.QUERY_STARTUP_STATE, CMD_SET.RESET]
    for code in (
        CMD_SET.INITIALIZE,
        CMD_SET.CONFIGURE_STEPPER_DRIVER,
        CMD_SET.SET_LEAD_SCREW_PITCH,
        CMD_SET.SET_MAX_VELOCITY_ACCELERATION,
        CMD_SET.SET_HOME_SAFETY_MERGIN,
        CMD_SET.STORE_CONFIG_SIGNATURE,
    ):
        assert code not in sim.command_log
    assert sim.x == 1000 and micro.get_pos()[0] == 1000  # the stage kept its position
    # What the warm start re-sent (the limit-switch config, unchanged) kept the signature: the next relaunch is warm
    _reopen(micro, sim)
    assert Microcontroller(serial_device=sim).startup_was_warm


@pytest.mark.parametrize(
    "before_close, after, verdict",
    [
        (lambda m: m.zero_x(), None, StartupVerdict.NOT_HOMED),
        (
            lambda m: (m.set_axis_enable_disable(AXIS.Y, 0), m.set_axis_enable_disable(AXIS.Y, 1)),
            None,
            StartupVerdict.NOT_HOMED,
        ),
        (
            lambda m: m.configure_motor_driver(
                AXIS.X,
                2 * control._def.MICROSTEPPING_DEFAULT_X,
                control._def.X_MOTOR_RMS_CURRENT_mA,
                control._def.X_MOTOR_I_HOLD,
            ),
            None,
            StartupVerdict.NO_SIGNATURE,
        ),
        (None, lambda sim, mp: sim.set_power_good(False), StartupVerdict.UNHEALTHY),
        (None, lambda sim, mp: setattr(sim, "tmc_config_ok", False), StartupVerdict.UNHEALTHY),
        (None, lambda sim, mp: sim.moving_axes.add(AXIS.Y), StartupVerdict.MOVING),
        (
            None,
            lambda sim, mp: mp.setattr(control._def, "MAX_VELOCITY_X_mm", control._def.MAX_VELOCITY_X_mm + 1),
            StartupVerdict.SIGNATURE_MISMATCH,
        ),
    ],
)
def test_anything_else_is_a_normal_startup(monkeypatch, caplog, before_close, after, verdict):
    sim = _homed(before_close=before_close)
    if after:
        after(sim, monkeypatch)
    with caplog.at_level(logging.INFO):
        micro = Microcontroller(serial_device=sim)
    assert micro.startup_verdict is verdict and not micro.startup_was_warm
    assert CMD_SET.INITIALIZE in sim.command_log
    assert control.microcontroller._STARTUP_VERDICT_LOG[verdict] in caplog.text  # the reason, one line per verdict


@pytest.mark.parametrize("moving, verdict", [({AXIS.Y}, StartupVerdict.MOVING), (set(), StartupVerdict.WARM)])
def test_in_progress_controller_startup(moving, verdict):
    sim = _homed()
    sim.in_progress = True  # a crashed session's command still in progress
    sim.moving_axes |= moving
    micro = Microcontroller(serial_device=sim)  # must not time out
    assert micro.startup_verdict is verdict
    assert (CMD_SET.INITIALIZE in sim.command_log) == (verdict is not StartupVerdict.WARM)
    assert not sim.in_progress and not micro.is_busy()  # RESET cleared it on both paths


def test_no_verdict_is_a_normal_startup(monkeypatch, caplog):
    """Ruling B-R4: a query that gets no verdict (lost to another sender, a timeout) is a normal startup."""

    class NoVerdictSim(FirmwareSimSerial):
        def _query_startup_state(self, cmd):
            pass  # answers the query as an ordinary command: COMPLETED, no verdict

    real_query = Microcontroller.query_startup_state
    monkeypatch.setattr(
        Microcontroller,
        "query_startup_state",
        lambda self, *args, **kwargs: real_query(self, *args, **{**kwargs, "timeout_s": 0.2}),
    )
    sim = _homed(NoVerdictSim(firmware_version=STARTUP_STATE_MIN_FIRMWARE))
    with caplog.at_level(logging.INFO):
        micro = Microcontroller(serial_device=sim)
    assert micro.startup_verdict is None and not micro.startup_was_warm
    assert CMD_SET.QUERY_STARTUP_STATE in sim.command_log and CMD_SET.INITIALIZE in sim.command_log
    assert CMD_SET.STORE_CONFIG_SIGNATURE in sim.command_log
    assert "no startup verdict" in caplog.text and "normal startup" in caplog.text


def test_reverting_settings_never_restores(monkeypatch):
    sim = _homed()  # homed under settings A
    with monkeypatch.context() as m:
        m.setattr(control._def, "MAX_VELOCITY_X_mm", control._def.MAX_VELOCITY_X_mm + 1)
        under_b = Microcontroller(serial_device=sim)  # a normal startup: INITIALIZE clears the flags, B is stored
        assert under_b.startup_verdict is StartupVerdict.SIGNATURE_MISMATCH
    _reopen(under_b, sim)
    under_a = Microcontroller(serial_device=sim)  # back under A: a mismatch with B again, so a normal startup again
    assert under_a.startup_verdict is StartupVerdict.SIGNATURE_MISMATCH and not under_a.startup_was_warm
    assert CMD_SET.INITIALIZE in sim.command_log
    _reopen(under_a, sim)
    # A is stored once more, but the homing done under A before is gone for good
    assert Microcontroller(serial_device=sim).startup_verdict is StartupVerdict.NOT_HOMED


def test_old_firmware_and_skip_init_never_query(caplog):
    old = FirmwareSimSerial(firmware_version=OLDER_FIRMWARE)
    with caplog.at_level(logging.INFO):
        micro = Microcontroller(serial_device=old)
    assert micro.startup_verdict is None and not micro.startup_was_warm
    assert CMD_SET.INITIALIZE in old.command_log
    assert CMD_SET.QUERY_STARTUP_STATE not in old.command_log
    assert CMD_SET.STORE_CONFIG_SIGNATURE not in old.command_log
    assert f"Firmware {OLDER_FIRMWARE[0]}.{OLDER_FIRMWARE[1]} cannot report homing state: normal startup" in caplog.text

    sim = _homed()
    micro = Microcontroller(serial_device=sim, reset_and_initialize=False)  # --skip-init
    assert micro.startup_verdict is None and not micro.startup_was_warm
    for code in (CMD_SET.QUERY_STARTUP_STATE, CMD_SET.STORE_CONFIG_SIGNATURE, CMD_SET.RESET, CMD_SET.INITIALIZE):
        assert code not in sim.command_log


def test_a_startup_that_homes_no_stage_axis_is_never_warm(monkeypatch, caplog):
    """With no axis to find homed, the homed check would pass on any controller: such a startup is a normal one,
    without the query, and still stores the signature as every normal startup on new firmware does."""
    sim = _homed()  # signature stored, X/Y/Z homed: asked, this controller would vouch for the stage
    for name in ("HOMING_ENABLED_X", "HOMING_ENABLED_Y", "HOMING_ENABLED_Z", "USE_Z_LIFT_STAGE"):
        monkeypatch.setattr(control._def, name, False)
    assert required_homed_axes_mask() == 0
    with caplog.at_level(logging.INFO):
        micro = Microcontroller(serial_device=sim)
    assert micro.startup_verdict is None and not micro.startup_was_warm
    assert CMD_SET.QUERY_STARTUP_STATE not in sim.command_log
    assert CMD_SET.INITIALIZE in sim.command_log and CMD_SET.CONFIGURE_STEPPER_DRIVER in sim.command_log
    assert sim.command_log[-3:] == [
        CMD_SET.STORE_CONFIG_SIGNATURE,
        CMD_SET.SET_FOCUS_WHEEL_MODE,
        CMD_SET.SET_PANEL_LOCK,
    ]
    assert sim.homing_validity.signature == (config_signature(), CONFIG_SIGNATURE_FORMAT)
    assert "Startup homes no stage axis: normal startup" in caplog.text


@pytest.mark.parametrize("z_homes, verdict", [(False, StartupVerdict.WARM), (True, StartupVerdict.NOT_HOMED)])
def test_z_not_required_when_z_does_not_home(monkeypatch, z_homes, verdict):
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Z", z_homes)
    monkeypatch.setattr(control._def, "USE_PI_FOCUS_STAGE", False)
    monkeypatch.setattr(control._def, "USE_Z_LIFT_STAGE", False)
    sim = _homed(before_close=lambda m: m.zero_z())  # only X and Y homed
    assert Microcontroller(serial_device=sim).startup_verdict is verdict


def _scope_sim():
    """A 1.9 controller for a whole Microscope, which also sends commands the sim's protocol whitelist lacks
    (SET_WATCHDOG_TIMEOUT, HEARTBEAT): those are answered, not refused."""
    return FirmwareSimSerial(strict=False, firmware_version=STARTUP_STATE_MIN_FIRMWARE)


def test_microscope_warm_start_skips_homing_and_prompt(monkeypatch, piezo_only):
    sim = _homed(_scope_sim())
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    home = MagicMock()
    monkeypatch.setattr(control.microscope.Microscope, "home_xyz", home)
    asked = []
    scope = control.microscope.Microscope.build_from_global_config(
        True, confirm_objective_lowered=lambda: asked.append(1)
    )
    assert scope.startup_homing is StartupHoming.ALREADY_HOMED and not asked
    home.assert_not_called()
    scope.close()


def test_a_microscope_relaunch_is_warm(monkeypatch):
    """End to end: nothing a normal Microscope startup sends after the store (stage, addons, homing) empties the
    signature, so the next launch on the same controller is warm."""
    sim = _scope_sim()
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    first = control.microscope.Microscope.build_from_global_config(True)
    assert first.startup_homing is StartupHoming.HOMED
    first.close()
    sim.reconnect(1)
    assert control.microscope.Microscope.build_from_global_config(True).startup_homing is StartupHoming.ALREADY_HOMED
