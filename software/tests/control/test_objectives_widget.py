"""ObjectivesWidget runs the objective changer off the GUI thread (see on_objective_changed)."""

import threading
from unittest.mock import patch

import pytest
from qtpy.QtWidgets import QMessageBox

import tests.control.test_stubs as ts
from control.widgets import ObjectivesWidget
from squid.abc import Pos


class _BlockingChanger:
    """Blocks in move_to_objective until the test sets `release`, so nothing races a sleep.
    (A real method, not a MagicMock: threaded_operation_helper reads operation.__name__.)"""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.release = threading.Event()
        self.done = threading.Event()
        self.calls = []

    def move_to_objective(self, objective_name):
        self.calls.append(objective_name)
        self.release.wait(timeout=5)
        self.done.set()
        if self.fail_with is not None:
            raise self.fail_with


def test_switch_runs_off_the_gui_thread(qtbot):
    store = ts.get_test_objective_store()
    changer = _BlockingChanger()
    widget = ObjectivesWidget(store, changer)
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
        widget.dropdown.setCurrentText("10x")
        assert not changer.done.is_set()
        assert not widget.dropdown.isEnabled()
        changer.release.set()

    assert changer.calls == ["10x"]
    assert store.current_objective == "10x"
    assert widget.dropdown.isEnabled()


def test_failed_switch_warns_reverts_and_reenables(qtbot):
    store = ts.get_test_objective_store()
    changer = _BlockingChanger(fail_with=RuntimeError("Motion did not finish within 30.0s"))
    changer.release.set()
    widget = ObjectivesWidget(store, changer)
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "warning") as warning:
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert store.current_objective == store.default_objective
    assert widget.dropdown.currentText() == store.default_objective
    warning.assert_called_once()


class _RecordingServo:
    """A Z stage that records retract / servo / restore calls into a shared event list; optionally
    fails one step ("retract", "restore", or the servo direction True/False)."""

    def __init__(self, events, fail_on=None, z_mm=2.5):
        self.events = events
        self.fail_on = fail_on
        self.z_mm = z_mm

    def get_pos(self):
        return Pos(x_mm=0.0, y_mm=0.0, z_mm=self.z_mm, theta_rad=None)

    def home(self, x, y, z, theta, blocking=True):
        if self.fail_on == "retract":
            raise RuntimeError("GCS error")
        self.events.append("retract")
        self.retract_thread = threading.current_thread()
        self.z_mm = 0.1

    def set_servo(self, enabled):
        if enabled is self.fail_on:
            raise RuntimeError("GCS error")
        self.events.append(f"servo_{'on' if enabled else 'off'}")

    def move_z_to(self, abs_mm, blocking=True):
        if self.fail_on == "restore":
            raise RuntimeError("GCS error")
        self.events.append(f"restore_{abs_mm}")
        self.z_mm = abs_mm


def _dialog_answering(events, answer):
    def question(*args, **kwargs):
        events.append("dialog")
        return answer

    return question


def test_servo_swap_disengages_confirms_then_reengages(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events))
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, QMessageBox.Ok)):
        with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
            widget.dropdown.setCurrentText("10x")

    assert events == ["retract", "servo_off", "dialog", "servo_on", "restore_2.5"]
    assert store.current_objective == "10x"
    assert widget.dropdown.isEnabled()


def test_servo_swap_cancel_reengages_and_keeps_previous(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events))
    qtbot.addWidget(widget)
    emitted = []
    widget.signal_objective_changed.connect(lambda: emitted.append(True))

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, QMessageBox.Cancel)):
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert events == ["retract", "servo_off", "dialog", "servo_on", "restore_2.5"]
    assert store.current_objective == store.default_objective
    assert widget.dropdown.currentText() == store.default_objective
    assert not emitted
    assert widget.dropdown.isEnabled()


def test_servo_swap_aborts_without_dialog_when_disengage_fails(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events, fail_on=False))
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question") as question, patch.object(QMessageBox, "warning") as warning:
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    question.assert_not_called()
    warning.assert_called_once()
    assert store.current_objective == store.default_objective
    assert widget.dropdown.currentText() == store.default_objective
    assert widget.dropdown.isEnabled()


def test_servo_swap_reports_failed_reengage_but_keeps_confirmed_objective(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events, fail_on=True))
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, QMessageBox.Ok)), patch.object(
        QMessageBox, "critical"
    ) as critical:
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert events == ["retract", "servo_off", "dialog"]  # no restore without the servo
    critical.assert_called_once()
    assert store.current_objective == "10x"
    assert widget.dropdown.isEnabled()


def test_servo_swap_aborts_with_servo_on_when_retract_fails(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events, fail_on="retract"))
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question") as question, patch.object(QMessageBox, "warning") as warning:
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert events == []  # the servo was never released
    question.assert_not_called()
    warning.assert_called_once()
    assert store.current_objective == store.default_objective
    assert widget.dropdown.isEnabled()


def test_servo_swap_warns_when_restore_fails_but_keeps_confirmed_objective(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events, fail_on="restore"))
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, QMessageBox.Ok)), patch.object(
        QMessageBox, "warning"
    ) as warning:
        widget.dropdown.setCurrentText("10x")
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert events == ["retract", "servo_off", "dialog", "servo_on"]
    warning.assert_called_once()
    assert store.current_objective == "10x"
    assert widget.dropdown.isEnabled()


def test_servo_swap_runs_stage_calls_off_the_gui_thread(qtbot):
    store = ts.get_test_objective_store()
    events = []
    servo = _RecordingServo(events)
    widget = ObjectivesWidget(store, focus_servo=servo)
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, QMessageBox.Ok)):
        with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
            widget.dropdown.setCurrentText("10x")
            assert not widget.dropdown.isEnabled()

    assert servo.retract_thread is not threading.main_thread()
    assert widget.dropdown.isEnabled()


def test_servo_swap_refused_while_z_is_in_use(qtbot):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events), z_in_use=lambda: True)
    qtbot.addWidget(widget)

    with patch.object(QMessageBox, "question") as question, patch.object(QMessageBox, "warning") as warning:
        widget.dropdown.setCurrentText("10x")

    assert events == []  # Z never retracted, servo never released
    question.assert_not_called()
    warning.assert_called_once()
    assert store.current_objective == store.default_objective
    assert widget.dropdown.currentText() == store.default_objective
    assert widget.dropdown.isEnabled()


def _swap_active_log(widget):
    log = []
    widget.signal_swap_active.connect(log.append)
    return log


@pytest.mark.parametrize(
    "fail_on, answer",
    [
        (None, QMessageBox.Ok),
        (None, QMessageBox.Cancel),
        ("retract", QMessageBox.Ok),
        (False, QMessageBox.Ok),  # servo disengage fails
        (True, QMessageBox.Ok),  # servo re-engage fails
        ("restore", QMessageBox.Ok),
    ],
)
def test_servo_swap_holds_swap_active_until_every_end(qtbot, fail_on, answer):
    store = ts.get_test_objective_store()
    events = []
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo(events, fail_on=fail_on))
    qtbot.addWidget(widget)
    swap_active = _swap_active_log(widget)

    with patch.object(QMessageBox, "question", side_effect=_dialog_answering(events, answer)), patch.object(
        QMessageBox, "warning"
    ), patch.object(QMessageBox, "critical"):
        widget.dropdown.setCurrentText("10x")
        assert swap_active == [True]
        qtbot.waitUntil(lambda: swap_active == [True, False], timeout=3000)

    assert widget.dropdown.isEnabled()


def test_refused_swap_never_reports_swap_active(qtbot):
    store = ts.get_test_objective_store()
    widget = ObjectivesWidget(store, focus_servo=_RecordingServo([]), z_in_use=lambda: True)
    qtbot.addWidget(widget)
    swap_active = _swap_active_log(widget)

    with patch.object(QMessageBox, "warning"):
        widget.dropdown.setCurrentText("10x")

    assert swap_active == []
