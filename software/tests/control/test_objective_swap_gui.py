"""The manual V-308 objective swap blocks every other stage / focus / acquisition control in the full GUI.

Kept out of test_HighContentScreeningGui.py and run in its own CI process: full-application GUI
tests leave Qt/thread state behind that segfaults a later GUI test spinning the event loop in the
same process (see .github/workflows/main.yml).
"""

import threading

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QMessageBox

import control.gui_hcs
import control.microscope
import squid.abc


class _GatedFocusServo:
    """A V-308 stand-in whose retract and restore block until the test opens their gates."""

    def __init__(self):
        self.retract_entered = threading.Event()
        self.retract_gate = threading.Event()
        self.restore_entered = threading.Event()
        self.restore_gate = threading.Event()

    def get_pos(self):
        return squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=2.0, theta_rad=None)

    def home(self, x, y, z, theta, blocking=True):
        self.retract_entered.set()
        assert self.retract_gate.wait(timeout=10)

    def set_servo(self, enabled):
        pass

    def move_z_to(self, abs_mm, blocking=True):
        self.restore_entered.set()
        assert self.restore_gate.wait(timeout=10)


def test_objective_swap_blocks_stage_focus_and_acquisition_controls(qtbot, monkeypatch):
    """While a manual V-308 swap is retracting or restoring Z off the GUI thread, autofocus,
    acquisition starts and manual stage moves must all be blocked, and restored afterwards."""

    def question(parent, title, text, *args, **kwargs):
        return QMessageBox.Ok if title == "Change Objective" else QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", question)

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)
    servo = _GatedFocusServo()
    win.objectivesWidget.focus_servo = servo
    blocked = [
        getattr(win, name) for name in win._OBJECTIVE_SWAP_BLOCKED_WIDGETS if getattr(win, name, None) is not None
    ]
    enabled_before = [w.isEnabled() for w in blocked]
    assert win.autofocusWidget.isEnabled() and win.recordTabWidget.isEnabled()
    start_pos = win.stage.get_pos()
    # What the GUI's position timer does at runtime; the objective-changed redraw needs a current location.
    win.navigationViewer.draw_fov_current_location(start_pos)

    def attempt_everything():
        assert not any(w.isEnabled() for w in blocked)
        # Disabled buttons ignore clicks: neither autofocus nor an acquisition can start.
        qtbot.mouseClick(win.autofocusWidget.btn_autofocus, Qt.LeftButton)
        assert not win.autofocusController.autofocus_in_progress
        start_widget = win.recordTabWidget.currentWidget()
        if hasattr(start_widget, "btn_startAcquisition"):
            qtbot.mouseClick(start_widget.btn_startAcquisition, Qt.LeftButton)
            assert not start_widget.btn_startAcquisition.isChecked()
        assert not win.multipointController.acquisition_in_progress()
        # Click-to-move, well-selection moves and scroll-Z go through these handlers.
        win.move_to_mm(start_pos.x_mm + 1.0, start_pos.y_mm + 1.0)
        win.move_from_click_mm(start_pos.x_mm + 1.0, start_pos.y_mm + 1.0)
        win.move_z_from_scroll(5.0)
        pos = win.stage.get_pos()
        assert (pos.x_mm, pos.y_mm, pos.z_mm) == pytest.approx((start_pos.x_mm, start_pos.y_mm, start_pos.z_mm))

    new_objective = next(o for o in win.objectiveStore.objectives_dict if o != win.objectiveStore.current_objective)
    win.objectivesWidget.dropdown.setCurrentText(new_objective)

    assert servo.retract_entered.wait(timeout=5)  # phase 1 is blocked mid-retract
    attempt_everything()
    servo.retract_gate.set()

    qtbot.waitUntil(servo.restore_entered.is_set, timeout=5000)  # dialog answered; phase 2 blocked mid-restore
    attempt_everything()
    servo.restore_gate.set()

    qtbot.waitUntil(win.objectivesWidget.dropdown.isEnabled, timeout=5000)
    assert [w.isEnabled() for w in blocked] == enabled_before
    assert not win._objective_swap_active
    assert win.objectiveStore.current_objective == new_objective
