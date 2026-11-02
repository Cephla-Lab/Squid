"""Utils > Filter Wheel Tuning...: when it refuses to start, what it always puts back, and what "Apply and save" does.

The tuning itself (what a level means, where the stall edge is, the margin, the ini writer) is pinned by
tests/test_filter_wheel_tuner_tune.py against the same engine; these tests are about the GUI's obligations:
nothing starts while the instrument is busy or without a wheel encoder to measure with, the machine is put back
however a run ends, the ini is written only on the explicit click, and Cancel stops the run.

No hardware: the microcontroller is a mock, and the session's tuning run is stubbed so the dialog's own behaviour
is what is being tested.
"""

from unittest.mock import MagicMock

import pytest

import control._def
import squid.filter_wheel_controller.cephla as cephla
from squid.config import SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel
from squid.filter_wheel_tuning import WheelTuningSession, refusal_reason

from control.widgets_filter_wheel_tuning import FilterWheelTuningDialog


INI = "[GENERAL]\n" "# motion\n" "microstepping_default_w = 64\n" "max_velocity_w_mm = 3.19\n" "use_something = True\n"

REC = {
    "pass": True,
    "microstepping_default_w": 8,
    "max_velocity_w_mm": 6.0,
    "max_acceleration_w_mm": 160.0,
    "ramp": "sshape",
    "stall_edge_found": True,
    "margin": 0.8,
    "moves_confirmed": 96,
    "by_distance_ms_median": {"1": 76.0, "4": 120.0},
    "adjacent_ms_median": 76.0,
    "date": "2026-09-20 12:00",
}


@pytest.fixture(autouse=True)
def _wheel_cache_in_tmp(tmp_path, monkeypatch):
    """Never write the real cache/filter_wheel_position.json from a test."""
    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(tmp_path / "filter_wheel_position.json"))


def _mcu(firmware=(1, 6), simulated=False):
    mcu = MagicMock()
    mcu.firmware_version = firmware
    mcu.is_simulated = simulated
    mcu.last_command_aborted_error = None
    return mcu


def _wheel(mcu, slot=3):
    config = SquidFilterWheelConfig(
        max_index=8, min_index=1, offset=0.008, motor_slot_index=3, transitions_per_revolution=4000
    )
    # skip_init with a record that matches this host: the construction touches no hardware (see cephla.py).
    cephla.cache_wheel_state({1: cephla.WheelRecord(slot, 0, cephla.host_motion_config())})
    wheel = SquidFilterWheel(mcu, config, skip_init=True)
    wheel.initialize([1])
    return wheel


def _dialog(qtbot, mcu=None, wheel=None, busy_reason=None):
    mcu = mcu if mcu is not None else _mcu()
    wheel = wheel if wheel is not None else _wheel(mcu)
    dialog = FilterWheelTuningDialog(mcu, wheel, busy_reason=busy_reason)
    qtbot.addWidget(dialog)
    return dialog


def _run_worker(qtbot, dialog, button):
    """Click a button and wait for the worker thread it starts to finish (the dialog clears .worker last)."""
    button.click()
    worker = dialog.worker
    assert worker is not None, "no run was started"
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=5000)


# ---------------------------------------------------------------- it refuses to start
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"firmware": (1, 5)}, "firmware"),
        ({"simulated": True}, "simulated"),
    ],
)
def test_the_hardware_has_to_be_able_to_answer(qtbot, kwargs, expected):
    mcu = _mcu(**kwargs)
    dialog = _dialog(qtbot, mcu=mcu, wheel=_wheel(_mcu()))
    dialog.button_verify.click()
    assert dialog.worker is None  # nothing was started
    assert expected in dialog.log_view.toPlainText().lower()


def test_a_wheel_that_is_not_a_squid_wheel_cannot_be_tuned(qtbot):
    other_wheel = MagicMock()  # a Zaber / Optospin controller, or none at all
    dialog = _dialog(qtbot, wheel=other_wheel)
    dialog.button_tune.click()
    assert dialog.worker is None
    assert "squid" in dialog.log_view.toPlainText().lower()


@pytest.mark.parametrize("reason", ["Live view is running.", "An acquisition is running."])
def test_it_will_not_share_the_instrument_with_live_view_or_an_acquisition(qtbot, reason):
    dialog = _dialog(qtbot, busy_reason=lambda: reason)
    dialog.button_verify.click()
    assert dialog.worker is None
    assert reason in dialog.log_view.toPlainText()


def test_refusal_reason_passes_a_healthy_machine(qtbot):
    mcu = _mcu()
    assert refusal_reason(_wheel(mcu), mcu) is None
    assert refusal_reason(_wheel(mcu), mcu, live=True) is not None
    assert refusal_reason(_wheel(mcu), mcu, acquiring=True) is not None


# ---------------------------------------------------------------- the machine is always put back
def _stub_run(dialog, summary=None, raises=None, record_calls=None):
    """Replace the tuning run itself; the session's restore stays real."""
    session = dialog.session

    def run(action, **overrides):
        slot = session.current_slot()
        if record_calls is not None:
            record_calls.append(("run", action, slot))
        try:
            if raises is not None:
                raise raises
            return dict(summary or {}, cancelled=False)
        finally:
            session.restore(slot)

    session.run = run
    return session


def test_a_finished_run_re_homes_through_the_controller_and_comes_back_to_the_slot(qtbot):
    mcu = _mcu()
    wheel = _wheel(mcu, slot=6)
    dialog = _dialog(qtbot, mcu=mcu, wheel=wheel)
    _stub_run(dialog, summary={"verify": {"pass": True, "moves": 96, "drift_usteps": 0, "drift_limit_usteps": 4}})
    _run_worker(qtbot, dialog, dialog.button_verify)

    mcu.configure_squidfilter.assert_called()  # the driver is put back under the host's configuration
    mcu.home_w.assert_called_once()  # ...and the wheel re-homed, so the position record is valid again
    assert wheel.get_filter_wheel_position() == {1: 6}  # back on the slot the user was on
    assert wheel.position_is_known(1) is True
    assert "PASS" in dialog.label_result.text()


def test_the_machine_is_put_back_even_when_the_run_raises(qtbot):
    mcu = _mcu()
    wheel = _wheel(mcu, slot=4)
    dialog = _dialog(qtbot, mcu=mcu, wheel=wheel)
    _stub_run(dialog, raises=RuntimeError("the encoder stopped reporting"))
    _run_worker(qtbot, dialog, dialog.button_tune)

    mcu.home_w.assert_called_once()
    assert wheel.get_filter_wheel_position() == {1: 4}
    assert "the encoder stopped reporting" in dialog.label_result.text()
    assert dialog.button_tune.isEnabled() and not dialog.button_cancel.isEnabled()


def test_cancel_reaches_the_run_and_the_result_says_so(qtbot):
    mcu = _mcu()
    dialog = _dialog(qtbot, mcu=mcu, wheel=_wheel(mcu, slot=2))
    session = dialog.session
    started, cancelled = [], []

    def run(action, **overrides):
        slot = session.current_slot()
        started.append(action)
        try:
            qtbot.waitUntil(lambda: session._cancelled, timeout=5000)  # the worker waits for the click
            cancelled.append(True)
            return {"cancelled": True}
        finally:
            session.restore(slot)

    session.run = run
    dialog.button_verify.click()
    worker = dialog.worker
    qtbot.waitUntil(lambda: bool(started), timeout=5000)
    assert dialog.button_cancel.isEnabled()
    dialog.button_cancel.click()
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=10000)
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=5000)
    assert cancelled == [True]
    assert "cancelled" in dialog.label_result.text().lower()
    mcu.home_w.assert_called_once()  # cancelling still puts the wheel back


def test_a_cancel_does_not_stick_to_the_next_run(qtbot):
    dialog = _dialog(qtbot)
    dialog.session.cancel()
    dialog.session.clear_cancel()
    assert dialog.session._cancelled is False


def test_the_runs_progress_reaches_the_log_view_from_the_worker_thread(qtbot):
    """The run writes its progress from the worker thread; a QTextEdit may only be touched from the GUI thread, so
    the line has to travel by signal."""
    dialog = _dialog(qtbot)
    session = dialog.session

    def run(action, **overrides):
        session.log("screen a50: 16 moves, encoder drift +0 usteps")
        return {"verify": {"pass": True, "moves": 16, "drift_usteps": 0, "drift_limit_usteps": 4}}

    session.run = run
    _run_worker(qtbot, dialog, dialog.button_verify)
    qtbot.waitUntil(lambda: "screen a50" in dialog.log_view.toPlainText(), timeout=5000)


# ---------------------------------------------------------------- a tune result, and only then a write
def test_a_successful_tune_offers_the_profile_and_writes_nothing_on_its_own(qtbot, tmp_path, monkeypatch):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text(INI)
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(ini))
    dialog = _dialog(qtbot)
    _stub_run(dialog, summary={"tune": REC})
    _run_worker(qtbot, dialog, dialog.button_tune)

    assert dialog.proposal_box.isVisible() or dialog.proposal is not None
    assert dialog.proposal == REC
    assert "8 usteps/FS" in dialog.label_proposed.text() and "160" in dialog.label_proposed.text()
    assert "76 ms" in dialog.label_proposed_timing.text()  # the measured time per slot
    assert "64 usteps/FS" in dialog.label_proposal_now.text()  # next to what the machine runs now
    assert ini.read_text() == INI  # nothing written without the click
    assert dialog.button_apply.isEnabled()


def test_apply_and_save_writes_the_ini_uses_it_now_and_reconfigures_the_wheel(qtbot, tmp_path, monkeypatch):
    ini = tmp_path / "configuration_test.ini"
    ini.write_text(INI)
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(ini))
    monkeypatch.setattr(control._def, "MICROSTEPPING_DEFAULT_W", 64)
    monkeypatch.setattr(control._def, "MAX_VELOCITY_W_mm", 3.19)
    monkeypatch.setattr(control._def, "MAX_ACCELERATION_W_mm", 300)
    mcu = _mcu()
    wheel = _wheel(mcu, slot=5)
    dialog = _dialog(qtbot, mcu=mcu, wheel=wheel)
    _stub_run(dialog, summary={"tune": REC})
    _run_worker(qtbot, dialog, dialog.button_tune)
    mcu.reset_mock()

    _run_worker(qtbot, dialog, dialog.button_apply)

    text = ini.read_text()
    assert "microstepping_default_w = 8" in text and "max_acceleration_w_mm = 160" in text
    assert "use_something = True" in text  # the rest of the file is untouched
    assert len(list(tmp_path.glob("configuration_test.ini.bak-*"))) == 1
    # the running software uses it at once, host and driver together
    assert control._def.MICROSTEPPING_DEFAULT_W == 8 and control._def.MAX_ACCELERATION_W_mm == 160.0
    mcu.configure_squidfilter.assert_called()
    mcu.home_w.assert_called_once()  # a microstep is a different length now: the wheel must be re-anchored
    assert wheel.get_filter_wheel_position() == {1: 5}  # and the user is back on their slot
    # the position record now says the wheel runs the new profile, so the next --skip-init restart is safe
    assert wheel._configured[1]["microstepping"] == 8
    assert "Applied and saved" in dialog.label_result.text()
    assert dialog.proposal is None


def test_apply_without_an_ini_is_reported_and_changes_nothing(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "CACHED_CONFIG_FILE_PATH", str(tmp_path / "does_not_exist.ini"))
    before = int(control._def.MICROSTEPPING_DEFAULT_W)
    dialog = _dialog(qtbot)
    _stub_run(dialog, summary={"tune": REC})
    _run_worker(qtbot, dialog, dialog.button_tune)
    _run_worker(qtbot, dialog, dialog.button_apply)
    assert "not found" in dialog.label_result.text()
    assert int(control._def.MICROSTEPPING_DEFAULT_W) == before


def test_a_failed_verify_says_so(qtbot):
    dialog = _dialog(qtbot)
    _stub_run(
        dialog,
        summary={
            "verify": {
                "pass": False,
                "moves": 96,
                "drift_usteps": 40,
                "drift_limit_usteps": 4,
                "worst_move_usteps": -32,
            }
        },
    )
    _run_worker(qtbot, dialog, dialog.button_verify)
    assert "FAIL" in dialog.label_result.text() and "Tune" in dialog.label_result.text()


# ---------------------------------------------------------------- the session's restore, without the dialog
def test_the_session_restore_never_raises_and_leaves_the_position_unknown_on_a_failure(qtbot):
    mcu = _mcu()
    wheel = _wheel(mcu, slot=7)
    mcu.home_w.side_effect = TimeoutError("W never acked")
    logged = []
    session = WheelTuningSession(mcu, wheel, log_fn=logged.append)
    session.restore(7)  # no raise
    assert wheel.position_is_known(1) is False
    assert any("RESTORE FAILED" in line for line in logged)


class _FakeTuner:
    """Stands in for the engine: records what it was handed and what was asked of it."""

    made = []

    def __init__(self, params, mcu=None, log_fn=None):
        self.params, self.mcu, self.log_fn = params, mcu, log_fn
        self.cancelled = False
        self.ran = False
        self.summary = {"verify": {"pass": True, "moves": 96}}
        _FakeTuner.made.append(self)

    def cancel(self):
        self.cancelled = True

    def run(self):
        self.ran = True


def test_the_session_runs_the_engine_on_the_existing_controller_and_then_restores(monkeypatch):
    import squid.filter_wheel_tuning as tuning

    _FakeTuner.made.clear()
    monkeypatch.setattr(tuning, "WheelTuner", _FakeTuner)
    mcu = _mcu()
    wheel = _wheel(mcu, slot=6)
    summary = WheelTuningSession(mcu, wheel).run("verify")

    tuner = _FakeTuner.made[-1]
    assert tuner.ran and tuner.mcu is mcu  # the application's own controller, handed over as it is
    mcu.reset.assert_not_called()  # never reset or re-initialised out from under the running GUI
    assert tuner.params.action == "verify" and tuner.params.current_ma is None  # no reduced current from the GUI
    assert tuner.params.leave_enabled is True  # the machine goes on using the wheel afterwards
    mcu.home_w.assert_called_once()
    assert wheel.get_filter_wheel_position() == {1: 6} and summary["verify"]["pass"] is True


def test_a_cancel_asked_for_before_the_run_starts_still_reaches_the_engine(monkeypatch):
    import squid.filter_wheel_tuning as tuning

    _FakeTuner.made.clear()
    monkeypatch.setattr(tuning, "WheelTuner", _FakeTuner)
    mcu = _mcu()
    session = WheelTuningSession(mcu, _wheel(mcu))
    session.cancel()
    session.run("tune")
    assert _FakeTuner.made[-1].cancelled is True


def test_the_session_knows_when_there_is_no_slot_to_come_back_to(qtbot):
    mcu = _mcu()
    wheel = _wheel(mcu, slot=7)
    wheel._position_known[1] = False
    session = WheelTuningSession(mcu, wheel)
    assert session.current_slot() is None
