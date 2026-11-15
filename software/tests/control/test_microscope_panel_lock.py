"""The control panel is locked while anything holds the stage (Microscope.stage_motion) and unlocked when nothing does.

The joystick and the focus wheel are read by the controller firmware on its own: without the lock they still move the
stage during an acquisition (between commanded moves), a home, or the stay at the loading position. Every one of those
owners takes the stage-motion reservation, so the Microscope locks the panel from the reservation, in one place.
"""

import logging
import threading

import pytest

import control.microcontroller
import control.microscope
from control._def import CMD_SET, FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE
from control.firmware_sim_serial import FirmwareSimSerial

OLD_FIRMWARE_WARNING = "cannot lock the control panel"


def _scope(monkeypatch, firmware_version):
    # strict=False: a whole Microscope also sends commands the sim's protocol whitelist lacks (watchdog, heartbeat)
    sim = FirmwareSimSerial(strict=False, firmware_version=firmware_version)
    monkeypatch.setattr(control.microcontroller, "get_microcontroller_serial_device", lambda **kw: sim)
    scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    return scope, sim


@pytest.fixture
def scope(monkeypatch):
    scope, sim = _scope(monkeypatch, FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE)
    yield scope, sim
    scope.close()


@pytest.fixture
def old_scope(monkeypatch):
    old = (FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE[0], FOCUS_WHEEL_TO_HOST_MIN_FIRMWARE[1] - 1)
    scope, sim = _scope(monkeypatch, old)
    yield scope, sim
    scope.close()


def _panel_commands(scope):
    """The SET_PANEL_LOCK commands sent from now on, as True / False."""
    mcu = scope.low_level_drivers.microcontroller
    sent = []
    real_send = mcu.send_command

    def send(cmd):
        if cmd[1] == CMD_SET.SET_PANEL_LOCK:
            sent.append(bool(cmd[2]))
        real_send(cmd)

    mcu.send_command = send
    return sent


def test_taking_the_stage_locks_the_panel_and_releasing_it_unlocks(scope):
    scope, sim = scope
    assert not sim.panel_locked

    hold = scope.stage_motion.take("an acquisition is running")
    assert sim.panel_locked  # the controller has it before take() returns: before the owner sends a move
    assert not scope.low_level_drivers.microcontroller.is_busy()
    hold.release()

    assert not sim.panel_locked


def test_a_refused_take_and_a_stale_release_leave_the_lock_alone(scope):
    scope, sim = scope
    first = scope.stage_motion.take("an acquisition is running")
    first.release()
    second = scope.stage_motion.take("a move to the loading position is running")
    sent = _panel_commands(scope)

    with pytest.raises(control.microscope.StageMotionRefused):
        scope.stage_motion.take("a stage move from the control server is running")
    first.release()

    assert sent == [] and sim.panel_locked
    second.release()
    assert sent == [False] and not sim.panel_locked


def test_a_home_locks_the_panel_until_its_claim_is_released(scope):
    scope, sim = scope
    seen = []
    scope.add_stage_homing_listener(lambda active: seen.append((active, sim.panel_locked)))

    scope.claim_stage_homing("TCP home").release_when_stopped()

    assert seen == [(True, True), (False, True)]  # the panel unlocks after the claim's False, at its release
    assert not sim.panel_locked


def test_a_refused_lock_command_is_logged_not_raised(scope, caplog):
    scope, sim = scope
    mcu = scope.low_level_drivers.microcontroller

    def refused(locked):
        raise TimeoutError("the controller did not answer")

    mcu.set_panel_lock = refused
    with caplog.at_level(logging.ERROR):
        hold = scope.stage_motion.take("an acquisition is running")  # the owner still gets the stage
        hold.release()

    assert scope.stage_motion.holder is None
    assert len([r for r in caplog.records if "control panel" in r.getMessage()]) == 2


def test_close_unlocks_and_a_later_release_sends_nothing(scope):
    """Closed at the loading position: Microscope.close() unlocks the panel, and the stay's release afterwards (the GUI
    tearing down) does not write to a closed controller."""
    scope, sim = scope
    hold = scope.stage_motion.take("the stage is at the loading position (return from it first)")
    assert sim.panel_locked

    scope.close()
    assert not sim.panel_locked
    sent = _panel_commands(scope)
    hold.release()

    assert sent == []


def test_old_firmware_warns_once_and_never_sends_the_lock(old_scope, caplog):
    scope, sim = old_scope
    assert not scope.low_level_drivers.microcontroller.supports_panel_lock()
    sent = _panel_commands(scope)

    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            scope.claim_stage_homing("TCP home").release_when_stopped()
            assert not scope.stage_homing_in_progress
        scope.stage_motion.take("an acquisition is running").release()

    assert sent == []
    warnings = [r.getMessage() for r in caplog.records if OLD_FIRMWARE_WARNING in r.getMessage()]
    assert warnings == [
        "This controller firmware cannot lock the control panel: its joystick still moves XY during acquisitions"
    ]


def test_close_waits_for_a_lock_under_way_then_unlocks_last(scope):
    """An owner taking the stage while Squid closes: its lock command, already past the closed check, must not land
    after close()'s unlock and leave the joystick dead without Squid."""
    scope, sim = scope
    mcu = scope.low_level_drivers.microcontroller
    real_set = mcu.set_panel_lock
    in_lock, go_on = threading.Event(), threading.Event()
    sent = []

    def slow_set(locked):
        if locked:
            in_lock.set()
            go_on.wait(5)
        sent.append(locked)
        real_set(locked)

    mcu.set_panel_lock = slow_set
    owner = threading.Thread(target=lambda: scope.stage_motion.take("a stage move from the control server is running"))
    owner.start()
    assert in_lock.wait(5)
    closer = threading.Thread(target=scope.close)
    closer.start()
    closer.join(0.3)
    assert closer.is_alive()  # close() waits for the lock under way
    go_on.set()
    closer.join(10)
    owner.join(10)

    assert sent == [True, False]  # close()'s unlock comes last
    assert not sim.panel_locked
