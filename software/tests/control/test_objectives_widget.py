"""Tests for ObjectivesWidget's non-blocking objective switching.

An objective switch blocks for seconds (Z retract, changer motion, Z restore); run
synchronously in the dropdown's Qt slot it froze the GUI event loop long enough for
the desktop to declare the app unresponsive. The widget must run the changer through
threaded_operation_helper and only touch Qt state back on the GUI thread.
"""

import threading
import time
from unittest.mock import MagicMock

import control.widgets as widgets_module
from control.widgets import ObjectivesWidget


class FakeStore:
    def __init__(self, objectives=("4x", "10x", "20x"), current="4x"):
        self.objectives_dict = {name: {} for name in objectives}
        self.current_objective = current

    def set_current_objective(self, name):
        self.current_objective = name


class SlowChanger:
    """Blocks in move_to_objective long enough to detect GUI-thread blocking."""

    def __init__(self, move_s=0.3, fail_with=None):
        self.move_s = move_s
        self.fail_with = fail_with
        self.done = threading.Event()
        self.calls = []

    def move_to_objective(self, objective_name):
        self.calls.append(objective_name)
        time.sleep(self.move_s)
        self.done.set()
        if self.fail_with is not None:
            raise self.fail_with


def test_switch_runs_off_the_gui_thread(qtbot):
    # The dropdown handler must return while the changer is still moving, with the
    # dropdown disabled as a re-entry guard; store update and signal follow on the
    # GUI thread once the move completes.
    store = FakeStore()
    changer = SlowChanger()
    widget = ObjectivesWidget(store, changer)
    qtbot.addWidget(widget)

    with qtbot.waitSignal(widget.signal_objective_changed, timeout=3000):
        widget.dropdown.setCurrentText("10x")
        assert not changer.done.is_set()  # handler returned while the move still runs
        assert not widget.dropdown.isEnabled()  # re-entry guard during the move

    assert changer.calls == ["10x"]
    assert store.current_objective == "10x"
    assert widget.dropdown.isEnabled()


def test_failed_switch_warns_reverts_and_reenables(qtbot, monkeypatch):
    # Any changer failure (e.g. a turret move timeout, not just KeyError) must end
    # in a warning dialog, an untouched store, and the dropdown reverted + usable.
    message_box = MagicMock()
    monkeypatch.setattr(widgets_module, "QMessageBox", message_box)
    store = FakeStore(current="4x")
    changer = SlowChanger(move_s=0.05, fail_with=RuntimeError("Motion did not finish within 30.0s"))
    widget = ObjectivesWidget(store, changer)
    qtbot.addWidget(widget)

    widget.dropdown.setCurrentText("10x")
    qtbot.waitUntil(lambda: widget.dropdown.isEnabled(), timeout=3000)

    assert store.current_objective == "4x"  # store unchanged on failure
    assert widget.dropdown.currentText() == "4x"  # dropdown reverted
    message_box.warning.assert_called_once()
