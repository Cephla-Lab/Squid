"""StageUtils' moves to and from the loading position run in a worker thread. How each move ends (the button, live
view, an error dialog, the "reached" signals) has to happen on the GUI thread: Qt widgets may only be used there,
and a dialog opened from another thread can crash the app."""

import threading
from unittest.mock import MagicMock, patch

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QMessageBox

from control.widgets import StageUtils
from squid.abc import Pos
from squid.stage.reservation import StageMotionReservation


def _stage_utils(qtbot, live_was_on=False, y_fails=False):
    stage = MagicMock(spec=["move_x_to", "move_y_to", "move_z_to", "get_pos"])
    stage.get_pos.return_value = Pos(x_mm=10.0, y_mm=10.0, z_mm=1.0, theta_rad=None)
    if y_fails:
        stage.move_y_to.side_effect = TimeoutError("y stalled")
    live = MagicMock()
    live.is_live = live_was_on
    dialog = StageUtils(stage, live, is_wellplate=False, stage_motion=StageMotionReservation())
    qtbot.addWidget(dialog)
    return dialog


def _record_thread(threads, name):
    return lambda *args, **kwargs: threads.setdefault(name, threading.current_thread())


def test_a_move_ends_on_the_gui_thread(qtbot):
    dialog = _stage_utils(qtbot, live_was_on=True)  # so the end of the move turns live back on
    threads = {}
    dialog.live_controller.start_live.side_effect = _record_thread(threads, "start_live")
    dialog.signal_loading_position_reached.connect(_record_thread(threads, "reached"), Qt.DirectConnection)

    with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
        dialog.switch_position()

    assert threads == {"start_live": threading.main_thread(), "reached": threading.main_thread()}
    assert dialog.slide_position == "loading"
    assert dialog.btn_load_slide.isEnabled()

    threads.clear()
    dialog.signal_scanning_position_reached.connect(_record_thread(threads, "reached"), Qt.DirectConnection)
    with qtbot.waitSignal(dialog.signal_scanning_position_reached, timeout=10000):
        dialog.switch_position()

    assert threads == {"start_live": threading.main_thread(), "reached": threading.main_thread()}
    assert dialog.slide_position == "scanning"


def test_a_failed_move_shows_its_error_on_the_gui_thread(qtbot):
    dialog = _stage_utils(qtbot, y_fails=True)
    threads = {}

    with patch.object(QMessageBox, "warning", side_effect=_record_thread(threads, "dialog")) as warning:
        with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
            dialog.switch_position()

    assert threads == {"dialog": threading.main_thread()}
    assert "y stalled" in warning.call_args.args[2]


def test_a_camera_that_will_not_restart_live_still_ends_the_move(qtbot, caplog):
    from control.widgets_z_lift import LiftMotionGuard

    dialog = _stage_utils(qtbot, live_was_on=True)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    dialog.live_controller.start_live.side_effect = RuntimeError("camera gone")

    with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
        dialog.switch_position()
    with qtbot.waitSignal(dialog.signal_scanning_position_reached, timeout=10000):
        dialog.switch_position()

    assert dialog.slide_position == "scanning"
    assert dialog.btn_load_slide.isEnabled()
    assert not guard.busy and not guard.at_loading_position  # nothing left refusing the next move
    assert "camera gone" in caplog.text
