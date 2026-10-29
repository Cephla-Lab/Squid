"""The stop sign before XY homing on a piezo-only system (control/widgets_homing.py): when it is asked, what each
button and key does, and Stage Utils' Home X / Home Y going through it."""

from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QApplication, QDialog, QMessageBox

import control._def
from control.widgets_homing import (
    NosepieceLoweredDialog,
    StopSign,
    confirm_nosepiece_lowered,
    startup_needs_nosepiece_check,
)


@pytest.mark.parametrize(
    "piezo_only, homing_x, homing_y, skip_init, expected",
    [
        (True, True, True, False, True),
        (True, True, True, True, False),  # Restart (--skip-init) does not home
        (False, True, True, False, False),  # the software lowers the objective itself
        (True, False, True, False, False),  # startup moves XY only when both axes home
        (True, True, False, False, False),
    ],
)
def test_startup_needs_the_check_only_when_a_piezo_only_system_homes_xy(
    monkeypatch, piezo_only, homing_x, homing_y, skip_init, expected
):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", piezo_only)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_X", homing_x)
    monkeypatch.setattr(control._def, "HOMING_ENABLED_Y", homing_y)
    assert startup_needs_nosepiece_check(skip_init) is expected


def _shown_dialog(qtbot, decline_text="Quit Squid"):
    dialog = NosepieceLoweredDialog(None, decline_text=decline_text)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


def test_home_button_accepts(qtbot):
    dialog = _shown_dialog(qtbot)
    dialog.home_button.click()
    assert not dialog.isVisible()
    assert dialog.result() == QDialog.Accepted


def test_decline_button_rejects_and_carries_the_given_label(qtbot):
    dialog = _shown_dialog(qtbot, decline_text="Cancel")
    assert dialog.decline_button.text() == "Cancel"
    dialog.decline_button.click()
    assert not dialog.isVisible()
    assert dialog.result() == QDialog.Rejected


@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape])
def test_a_stray_key_declines_even_with_focus_on_home(qtbot, key):
    dialog = _shown_dialog(qtbot)
    dialog.home_button.setFocus()
    qtbot.keyClick(dialog.home_button, key)
    assert not dialog.isVisible()
    assert dialog.result() == QDialog.Rejected


def test_stop_sign_is_a_red_octagon(qtbot):
    sign = StopSign(96)
    qtbot.addWidget(sign)
    image = sign.grab().toImage()

    def color_at(fx, fy):
        return QColor(image.pixel(int(fx * (image.width() - 1)), int(fy * (image.height() - 1))))

    def is_red(c):
        return c.red() > 150 and c.green() < 80 and c.blue() < 80

    assert is_red(color_at(0.5, 0.02))  # top edge, outside the white rim
    assert is_red(color_at(0.02, 0.5))  # left edge
    assert not is_red(color_at(0.02, 0.02))  # the corners are cut: an octagon, not a square
    assert not is_red(color_at(0.98, 0.98))


def _answer_when_shown(qtbot, accept: bool):
    def answer():
        dialogs = [w for w in QApplication.topLevelWidgets() if isinstance(w, NosepieceLoweredDialog) and w.isVisible()]
        assert len(dialogs) == 1
        (dialogs[0].home_button if accept else dialogs[0].decline_button).click()

    QTimer.singleShot(0, answer)


@pytest.mark.parametrize("accept", [True, False])
def test_confirm_returns_true_only_for_home(qtbot, accept):
    _answer_when_shown(qtbot, accept)
    assert confirm_nosepiece_lowered(None, decline_text="Quit Squid") is accept


def _stage_utils(qtbot):
    from control.widgets import StageUtils

    live = MagicMock()
    live.is_live = False
    stage = MagicMock()
    dialog = StageUtils(stage, live, is_wellplate=False)
    qtbot.addWidget(dialog)
    return dialog, stage


@pytest.mark.parametrize("button, axes", [("home_x", dict(x=True, y=False)), ("home_y", dict(x=False, y=True))])
@pytest.mark.parametrize("confirmed", [True, False])
def test_piezo_only_home_x_y_asks_at_the_stop_sign(qtbot, monkeypatch, button, axes, confirmed):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", True)
    dialog, stage = _stage_utils(qtbot)
    with patch("control.widgets.confirm_nosepiece_lowered", return_value=confirmed) as confirm, patch.object(
        QMessageBox, "exec_", side_effect=AssertionError("the generic confirmation must not be shown")
    ):
        getattr(dialog, button)()
    confirm.assert_called_once_with(dialog, decline_text="Cancel")
    if confirmed:
        stage.home.assert_called_once_with(z=False, theta=False, **axes)
    else:
        stage.home.assert_not_called()


def test_other_systems_keep_the_generic_confirmation(qtbot, monkeypatch):
    monkeypatch.setattr(control._def, "IS_PIEZO_ONLY", False)
    dialog, stage = _stage_utils(qtbot)
    with patch("control.widgets.confirm_nosepiece_lowered") as confirm, patch.object(
        QMessageBox, "exec_", return_value=QMessageBox.Ok
    ):
        dialog.home_x()
    confirm.assert_not_called()
    stage.home.assert_called_once_with(x=True, y=False, z=False, theta=False)
