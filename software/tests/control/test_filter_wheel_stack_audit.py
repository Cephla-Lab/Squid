"""Regression tests from the independent audit of the filter-wheel stack (#657 -> #662 -> #664) at 02b7d6b0,
2026-09-20. The audit's own tests are kept where the fix makes them pass as written; where the fix refuses
loudly instead of staying silent, the test below states the fixed behaviour. No hardware: a MagicMock controller.

One audit finding is deliberately NOT here: "a watchdog heartbeat ends the wait for a wheel move". It reproduces
against SimSerial, which answers HEARTBEAT with COMPLETED at once; the firmware reports IN_PROGRESS for as long as
any move runs, whatever command id the packet carries. Checked on hardware instead (bench log 2026-09-20).
"""

import builtins
import threading
import time
from unittest.mock import MagicMock

import pytest

import control._def as defs
import control.microcontroller
import squid.filter_wheel_controller.cephla as cephla
import squid.filter_wheel_tuning as tuning
from control.widgets_filter_wheel_tuning import FilterWheelTuningDialog
from squid.config import SquidFilterWheelConfig

PROFILE = dict(
    microstepping_default_w=8,
    max_velocity_w_mm=6.0,
    max_acceleration_w_mm=250.0,
    stall_edge_found=False,
    moves_confirmed=96,
)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A two-wheel SquidFilterWheel restarted with --skip-init onto a valid record: both wheels on slot 5,
    recorded with exactly the configuration this host would send, so the restart trusts them."""
    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(tmp_path / "wheel.json"))
    for key, value in [
        ("MICROSTEPPING_DEFAULT_W", 64),
        ("MAX_VELOCITY_W_mm", 3.19),
        ("MAX_ACCELERATION_W_mm", 300),
        ("SQUID_FILTERWHEEL_COMPLETION_WINDOW_DEG", 0.0),
    ]:
        monkeypatch.setattr(defs, key, value)
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    mc.is_simulated = False
    mc.last_command_aborted_error = None
    configs = {
        i: SquidFilterWheelConfig(
            max_index=8, min_index=1, offset=0.008, motor_slot_index=i + 2, transitions_per_revolution=4000
        )
        for i in (1, 2)
    }
    cephla.cache_wheel_state({i: cephla.WheelRecord(5, 0, cephla.host_motion_config()) for i in configs})
    wheel = cephla.SquidFilterWheel(mc, configs, skip_init=True)
    wheel.initialize([1, 2])
    mc.reset_mock()
    return mc, wheel


# ------------------------------------------------------------------ 1 (P1): withdrawing the record can fail silently
def test_a_record_that_could_not_be_withdrawn_is_not_believed_by_a_restart(rig, monkeypatch):
    """cache_wheel_state() never raises - it only warns - so every "the record is withdrawn now" in the design
    (_set_unknown, _home_wheel, release_for_direct_control) is a hope, not a fact. With the cache directory
    unwritable (full disk, read-only cache, cache/ removed under a running process) a tuning run withdraws
    nothing: the file still says slot 5 with the configuration this host runs, so a --skip-init start after an
    interrupted run believes it and never homes, while the tuner left the wheel wherever it stopped."""
    mc, wheel = rig
    assert wheel.position_is_known(1)

    real_open = builtins.open
    refuse = {"writes": False}

    def guarded_open(path, mode="r", *args, **kwargs):
        if refuse["writes"] and any(c in str(mode) for c in "wax"):
            raise OSError(28, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    refuse["writes"] = True
    # exactly what WheelTuningSession.run() does before handing the axis to the tuner
    wheel.release_for_direct_control(1)
    refuse["writes"] = False

    restarted = cephla.SquidFilterWheel(mc, wheel._configs, skip_init=True)
    assert not restarted.position_is_known(1), (
        "the record still claims slot 5 after the wheel was released for direct control: a --skip-init restart "
        "stays on a slot the wheel is no longer on, and never homes"
    )


# ------------------------------------------------------------------ 2 (P2): the record does not track the geometry


# ------------------------------------------------------------------ 3 (P2): the watchdog heartbeat shares the mcu
class _HoldingSerial(control.microcontroller.SimSerial):
    """A SimSerial that withholds the reply to MOVETO_W, the way a real wheel takes ~80 ms to reach a slot.
    Every other command is answered at once, as the simulator always does."""

    def __init__(self):
        super().__init__()
        self._held = None

    def write(self, data, reconnect_tries: int = 0) -> int:
        if data[1] == defs.CMD_SET.MOVETO_W:
            self._held = bytes(data)
            return len(data)
        return super().write(data, reconnect_tries)

    def release(self):
        held, self._held = self._held, None
        if held is not None:
            super().write(bytearray(held))


# ------------------------------------------------------------------ 4 (P2): release_for_direct_control locks nothing

# ------------------------------------------------------------------ 5 (P3): the tuner is wired to AXIS.W only


# ------------------------------------------------------------------ 6 (P2): a restored wheel is never re-initialised
def test_a_restored_wheel_is_re_initialised_when_its_first_move_is_refused(rig):
    """cephla.py's header says a controller power-cycled between the two processes "is caught by the firmware
    rejecting the first move, which re-homes". It is not. The skip_init restore sets _configured[wheel] from the
    RECORD - a claim about a controller this process has never spoken to - so _home_wheel() finds the driver
    configuration "known" and skips _configure_wheel(), i.e. never sends INITFILTERWHEEL. On a controller that
    has not had INITFILTERWHEEL this session, enable_filterwheel is false: every MOVETO_W is refused, and
    callback_home_or_zero's AXIS_W branch (stage_commands.cpp ~766) does nothing at all while
    process_serial_message has already defaulted the status to COMPLETED_WITHOUT_ERRORS - a home that silently
    succeeds without moving. The wheel stays unusable for the whole session."""
    mc, wheel = rig
    pending = {"cmd": None}

    def note(name):
        def remember(*args, **kwargs):
            pending["cmd"] = name

        return remember

    mc.move_w_to_usteps.side_effect = note("move")
    mc.home_w.side_effect = note("home")  # the firmware's no-op home, reported COMPLETED

    def wait(*args, **kwargs):
        cmd, pending["cmd"] = pending["cmd"], None
        if cmd == "move":  # dispatch_filterwheel_move -> report_move_error when enable_filterwheel is false
            raise cephla.CommandAborted(command_id=1, reason="firmware reported CMD_EXECUTION_ERROR", recoverable=True)

    mc.wait_till_operation_is_completed.side_effect = wait

    with pytest.raises(cephla.CommandAborted):
        wheel.set_filter_wheel_position({1: 3})

    names = [call[0] for call in mc.method_calls]
    assert "init_filter_wheel" in names, (
        f"the refused move never re-initialised the wheel axis ({names}): the documented recovery cannot work on a "
        f"controller that was power-cycled between the two processes"
    )


# ------------------------------------------------------------------ 7 (P3): Cancel is offered where it does nothing
def test_cancel_is_not_offered_during_apply(rig, qtbot):
    """_apply() enables Cancel through _set_running(True), but apply() never looks at the cancel flag and must
    not be interrupted half way (the ini is already written). Worse, session.cancel() then calls cancel() on the
    PREVIOUS run's tuner. The operator is shown a button that says "stop after the move that is running" while
    the wheel is being re-homed and returned, and it stops nothing."""
    mc, wheel = rig
    dialog = FilterWheelTuningDialog(mc, wheel)
    qtbot.addWidget(dialog)
    dialog.proposal = dict(PROFILE)
    started, release = threading.Event(), threading.Event()

    def slow_apply(rec, ini_path=None):
        started.set()
        release.wait(10)
        return {"path": "machine.ini", "backup": "machine.ini.bak"}

    dialog.session.apply = slow_apply
    dialog.show()
    dialog.button_apply.click()
    worker = dialog.worker
    try:
        qtbot.waitUntil(started.is_set)
        assert not dialog.button_cancel.isEnabled(), "Cancel is offered during Apply, where it cancels nothing"
    finally:
        release.set()
        worker.wait(5000)
        qtbot.waitUntil(lambda: dialog.worker is None)


# ------------------------------------------------------------------ the fixed behaviour, where it differs from silence
def test_a_record_that_can_neither_be_rewritten_nor_deleted_stops_the_release(rig, monkeypatch):
    mc, wheel = rig
    monkeypatch.setattr(cephla, "cache_wheel_state", lambda *a, **k: False)

    def no_delete(path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(cephla.os, "remove", no_delete)
    with pytest.raises(cephla.WheelRecordError):
        wheel.release_for_direct_control(1)
    assert 1 not in wheel._direct_control  # the tuner never gets the axis: the session's run() propagates this


def test_a_changed_pitch_or_sign_is_noticed_by_a_skip_init_restart(rig, monkeypatch):
    mc, wheel = rig
    for key, value in (("SCREW_PITCH_W_MM", 2.0), ("STAGE_MOVEMENT_SIGN_W", -1), ("FULLSTEPS_PER_REV_W", 400)):
        with monkeypatch.context() as m:
            before = cephla.SquidFilterWheel._target_pos_to_usteps(wheel._configs[1], 5)
            m.setattr(defs, key, value)
            assert cephla.SquidFilterWheel._target_pos_to_usteps(wheel._configs[1], 5) != before  # the math follows it
            cephla.cache_wheel_state({1: cephla.WheelRecord(5, 0, wheel._configured[1])})
            mc.reset_mock()
            restarted = cephla.SquidFilterWheel(mc, {1: wheel._configs[1]}, skip_init=True)
            names = [c[0] for c in mc.method_calls]
            assert "configure_squidfilter" in names and "home_w" in names, f"{key}: not re-anchored ({names})"
            assert restarted.get_filter_wheel_position()[1] == 5  # and back on the recorded slot


def test_a_released_wheel_refuses_moves_and_homes_until_it_is_handed_back(rig):
    mc, wheel = rig
    wheel.release_for_direct_control(1)
    mc.reset_mock()
    with pytest.raises(cephla.WheelUnderDirectControl):
        wheel.set_filter_wheel_position({1: 3})
    with pytest.raises(cephla.WheelUnderDirectControl):
        wheel.home(1)
    names = [c[0] for c in mc.method_calls]
    assert "home_w" not in names and "move_w_to_usteps" not in names and "init_filter_wheel" not in names
    wheel.set_filter_wheel_position({2: 3})  # the other wheel is not affected
    wheel.reconfigure_driver(1, return_to_slot=5)  # the hand-back
    wheel.set_filter_wheel_position({1: 3})
    assert wheel.get_filter_wheel_position()[1] == 3 and wheel.position_is_known(1)


def test_the_tuner_refuses_a_wheel_that_is_not_on_the_w_axis(rig):
    mc, wheel = rig
    assert "W axis" in tuning.refusal_reason(wheel, mc, wheel_id=2)
    assert tuning.refusal_reason(wheel, mc, wheel_id=1) is None
    with pytest.raises(ValueError, match="W axis"):
        tuning.WheelTuningSession(mc, wheel, wheel_id=2).run("verify")
    assert wheel.position_is_known(2) and 2 in cephla.load_cached_wheel_state()  # nothing was released
    assert wheel.position_is_known(1)


def test_a_home_on_a_restored_wheel_sends_the_configuration_once(rig):
    # the configuration came from the record: the first home verifies it by sending it; later homes do not repeat it
    mc, wheel = rig
    wheel.home(1)
    assert [c[0] for c in mc.method_calls].count("init_filter_wheel") == 1
    mc.reset_mock()
    wheel.home(1)
    assert "init_filter_wheel" not in [c[0] for c in mc.method_calls]


def test_wrap_setting_accepts_the_integers_an_ini_yields():
    parse = cephla.SquidFilterWheel._parse_wrap
    assert parse(1) is True and parse(0) is False and parse("Auto") == "auto"
    with pytest.raises(ValueError):
        parse(2)
