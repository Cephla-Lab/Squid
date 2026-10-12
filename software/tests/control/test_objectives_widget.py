"""ObjectivesWidget runs the objective changer off the GUI thread (see on_objective_changed)."""

import threading
from unittest.mock import patch

from qtpy.QtWidgets import QMessageBox

import tests.control.test_stubs as ts
from control.widgets import ObjectivesWidget


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


class _ForgetfulChanger(_BlockingChanger):
    """Like the turret after a rotation that failed partway: it no longer knows which objective is in place."""

    def __init__(self, fail_with=None):
        super().__init__(fail_with)
        self.current_objective = "20x"

    def move_to_objective(self, objective_name):
        self.current_objective = None
        super().move_to_objective(objective_name)
        self.current_objective = objective_name


def test_a_failed_switch_that_leaves_the_objective_unknown_shows_none_so_reselecting_it_moves(qtbot):
    store = ts.get_test_objective_store()
    shown = store.current_objective
    changer = _ForgetfulChanger(fail_with=TimeoutError("Move to 6050 pulses timed out"))
    changer.release.set()
    widget = ObjectivesWidget(store, changer)
    qtbot.addWidget(widget)
    other = next(name for name in store.objectives_dict if name != shown)

    with patch.object(QMessageBox, "warning") as warning:
        widget.dropdown.setCurrentText(other)
        qtbot.waitUntil(widget.dropdown.isEnabled, timeout=3000)

    assert changer.current_objective is None
    assert widget.dropdown.currentIndex() == -1  # not the old objective: the changer may be between slots
    assert "unknown" in warning.call_args[0][2]
    changer.fail_with = None
    with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
        widget.dropdown.setCurrentText(shown)  # the objective shown before the failure
    assert changer.calls == [other, shown]
    assert store.current_objective == shown == changer.current_objective
