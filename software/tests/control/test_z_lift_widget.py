"""The Z Lift window (control/widgets_z_lift.py): readout, moves off the GUI thread, heights per sample format, and the status-bar readout."""

import threading
from unittest.mock import patch

from qtpy.QtCore import QObject, Signal
from qtpy.QtWidgets import QMessageBox

from control.widgets_z_lift import LiftMotionGuard, LoadingPositionButton, ZLiftWidget
from squid.stage.lift import LiftHeights


class _FakeLift:
    min_mm, max_mm, lowered_mm = 0.0, 8.0, 0.0

    def __init__(self, tmp_path):
        self.height = 2.0
        self.calls = []
        self.threads = []
        self.heights = LiftHeights(str(tmp_path / "heights.json"))
        self.sample_format = "96 well plate"

    def clamp(self, mm):
        return min(max(mm, self.min_mm), self.max_mm)

    def get_position_mm(self):
        return self.height

    def limit_switches(self):
        return (self.height <= 0.0, False)

    def current_format(self):
        return self.sample_format

    def sample_height_mm(self):
        return self.heights.get(self.sample_format)

    def _record(self, name, mm=None):
        self.calls.append(name if mm is None else (name, round(mm, 6)))
        self.threads.append(threading.current_thread())

    def move_to(self, mm, blocking=True):
        self._record("move_to", mm)
        self.height = self.clamp(mm)

    def move_relative(self, delta_mm, blocking=True):
        self._record("move_relative", delta_mm)
        self.height = self.clamp(self.height + delta_mm)

    def lower(self):
        self._record("lower")
        before, self.height = self.height, 0.0
        return before

    def home(self):
        self._record("home")
        self.height = 0.0


_FORMATS = ["glass slide", "96 well plate", "384 well plate"]


def _widget(qtbot, tmp_path, stage_utils=None):
    lift = _FakeLift(tmp_path)
    widget = ZLiftWidget(lift, lambda: _FORMATS, stage_utils=stage_utils)
    qtbot.addWidget(widget)
    return widget, lift


def test_readout_shows_position_and_sensors(qtbot, tmp_path):
    widget, lift = _widget(qtbot, tmp_path)
    lift.height = 0.0
    widget._refresh_readout()
    assert widget.label_position.text().startswith("0.000 mm")
    assert "TRIGGERED" in widget.label_bottom.text()
    assert "clear" in widget.label_top.text()


def test_jog_runs_off_the_gui_thread_and_reenables(qtbot, tmp_path):
    widget, lift = _widget(qtbot, tmp_path)
    widget.spin_step.setValue(0.25)
    with qtbot.waitSignal(widget.motion_guard.signal_move_finished, timeout=3000):
        widget.btn_up.click()
    qtbot.waitUntil(lambda: widget.btn_up.isEnabled(), timeout=3000)
    assert lift.calls == [("move_relative", 0.25)]
    assert lift.threads[0] is not threading.main_thread()


def test_table_lists_every_format_and_marks_the_current_one(qtbot, tmp_path):
    widget, lift = _widget(qtbot, tmp_path)
    assert widget.table.rowCount() == 3
    assert widget.table.item(1, 0).text() == "96 well plate  (current)"
    assert widget.table.item(1, 0).font().bold()
    lift.sample_format = "glass slide"
    widget.refresh_formats()
    assert widget.table.item(0, 0).text() == "glass slide  (current)"


def test_set_go_to_and_clear_a_format_height_are_saved(qtbot, tmp_path):
    widget, lift = _widget(qtbot, tmp_path)
    lift.height = 3.25
    widget.table.selectRow(2)  # 384 well plate
    with patch.object(QMessageBox, "question", return_value=QMessageBox.Ok):
        widget.btn_height_set.click()
    assert LiftHeights(str(tmp_path / "heights.json")).by_format == {"384 well plate": 3.25}

    lift.height = 0.0
    widget.table.selectRow(2)
    with qtbot.waitSignal(widget.motion_guard.signal_move_finished, timeout=3000):
        widget.btn_height_go.click()
    assert lift.calls[-1] == ("move_to", 3.25)

    widget.table.selectRow(2)
    widget.btn_height_clear.click()
    assert LiftHeights(str(tmp_path / "heights.json")).by_format == {}


def test_editing_a_height_cell_validates(qtbot, tmp_path):
    widget, lift = _widget(qtbot, tmp_path)
    with patch.object(QMessageBox, "warning") as warning:
        widget.table.item(0, 1).setText("99")  # outside the travel: refused and reverted
    warning.assert_called_once()
    assert lift.heights.get("glass slide") is None and widget.table.item(0, 1).text() == ""
    widget.table.item(0, 1).setText("4.5")
    assert lift.heights.get("glass slide") == 4.5
    widget.table.item(0, 1).setText("")
    assert lift.heights.get("glass slide") is None


def test_status_bar_shows_the_lift_next_to_the_stage(qtbot):
    from unittest.mock import MagicMock

    from control.core.core import ImageDisplayWindow
    from squid.abc import Pos

    live = MagicMock()
    live.microscope.stage.get_pos.return_value = Pos(x_mm=1.0, y_mm=2.0, z_mm=0.3, theta_rad=None)
    live.microscope.addons.piezo_stage = None
    live.microscope.z_lift.get_position_mm.return_value = 6.25
    window = ImageDisplayWindow(live)
    qtbot.addWidget(window)
    window.update_stage_piezo_positions()
    assert window.lift_position_label.text() == "Lift: 6.250 mm"
    assert not window.lift_position_label.isHidden()

    live.microscope.z_lift = None  # a system without a lift shows nothing extra
    window.update_stage_piezo_positions()
    assert window.lift_position_label.isHidden() and window.lift_separator.isHidden()


class _FakeStageUtils(QObject):
    signal_threaded_stage_move_started = Signal()
    signal_loading_position_reached = Signal()
    signal_scanning_position_reached = Signal()

    def __init__(self):
        super().__init__()
        self.slide_position = None
        self.switches = 0

    def switch_position(self):
        self.switches += 1
        self.signal_threaded_stage_move_started.emit()


def test_loading_buttons_share_one_toggle(qtbot):
    utils = _FakeStageUtils()
    guard = LiftMotionGuard(utils)
    a, b = LoadingPositionButton(utils, guard), LoadingPositionButton(utils, guard)
    qtbot.addWidget(a)
    qtbot.addWidget(b)
    a.click()
    assert utils.switches == 1
    assert not a.isEnabled() and not b.isEnabled()  # the other button saw it start
    b._toggle()  # refused even if a click got through
    assert utils.switches == 1
    utils.slide_position = "loading"
    utils.signal_loading_position_reached.emit()
    assert a.isEnabled() and b.text() == "Move To Scanning Position"


def test_loading_toggle_blocks_every_lift_move(qtbot, tmp_path):
    utils = _FakeStageUtils()
    widget, lift = _widget(qtbot, tmp_path, stage_utils=utils)
    row_button = LoadingPositionButton(utils, widget.motion_guard)  # the Stages tab's button
    qtbot.addWidget(row_button)

    row_button.click()
    assert utils.switches == 1
    assert not widget.btn_up.isEnabled() and not widget.btn_height_go.isEnabled()
    assert not widget.btn_loading.isEnabled()
    widget._jog(+1)  # refused even if a click got through
    assert lift.calls == []

    # At the loading position the lift stays locked; only the toggle back to scanning is allowed.
    utils.slide_position = "loading"
    utils.signal_loading_position_reached.emit()
    assert not widget.btn_up.isEnabled() and not widget.btn_height_go.isEnabled()
    assert row_button.isEnabled() and widget.btn_loading.isEnabled()
    widget._jog(+1)
    assert lift.calls == []
    assert not widget.motion_guard.claim()  # no objective swap either

    row_button.click()  # back to scanning: the loading position is being left
    assert utils.switches == 2
    assert not widget.motion_guard.at_loading_position
    utils.slide_position = "scanning"
    utils.signal_scanning_position_reached.emit()
    assert widget.btn_up.isEnabled() and row_button.isEnabled()
    widget._jog(+1)
    qtbot.waitUntil(lambda: lift.calls == [("move_relative", 0.1)], timeout=3000)


def test_lift_move_blocks_the_loading_toggle(qtbot, tmp_path):
    utils = _FakeStageUtils()
    widget, lift = _widget(qtbot, tmp_path, stage_utils=utils)
    release = threading.Event()
    real_move = lift.move_relative

    def slow_move_relative(delta_mm, blocking=True):
        release.wait(5)
        real_move(delta_mm)

    lift.move_relative = slow_move_relative
    row_button = LoadingPositionButton(utils, widget.motion_guard)
    qtbot.addWidget(row_button)

    widget.btn_up.click()
    assert not row_button.isEnabled() and not widget.btn_loading.isEnabled()
    row_button._toggle()
    assert utils.switches == 0
    with qtbot.waitSignal(widget.motion_guard.signal_move_finished, timeout=3000):
        release.set()
    qtbot.waitUntil(lambda: row_button.isEnabled(), timeout=3000)


def test_stage_utils_dialog_toggle_follows_the_lift_guard(qtbot):
    from unittest.mock import MagicMock

    from control.widgets import StageUtils

    live = MagicMock()
    live.is_live = False
    dialog = StageUtils(MagicMock(), live, is_wellplate=False)
    qtbot.addWidget(dialog)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)

    assert guard.claim()  # e.g. an objective swap is running
    assert not dialog.btn_load_slide.isEnabled()
    with patch("control.widgets.move_to_loading_position") as move:
        dialog.switch_position()  # refused even if a click got through
    move.assert_not_called()

    guard.release()
    assert dialog.btn_load_slide.isEnabled()
    with patch("control.widgets.move_to_loading_position") as move:
        dialog.switch_position()
    move.assert_called_once()
    assert guard.busy  # its own toggle holds the guard until it reports back


def test_a_failed_return_from_loading_stays_at_loading(qtbot):
    from unittest.mock import MagicMock

    from control.widgets import StageUtils

    live = MagicMock()
    live.is_live = False
    dialog = StageUtils(MagicMock(), live, is_wellplate=False)
    qtbot.addWidget(dialog)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    dialog._was_live = False
    dialog.slide_position = "loading"
    dialog.signal_loading_position_reached.emit()
    assert guard.at_loading_position

    with patch("control.widgets.move_to_scanning_position"):
        dialog.switch_position()  # the return starts...
    assert not guard.at_loading_position
    reached = []
    dialog.signal_scanning_position_reached.connect(lambda: reached.append("scanning"))
    dialog.signal_loading_position_reached.connect(lambda: reached.append("loading"))
    with patch.object(QMessageBox, "warning") as warning:
        dialog._callback_scanning_position_reached(False, "timeout")  # ...and fails
    warning.assert_called_once()
    assert reached == ["loading"]  # still at loading: everything stays locked
    assert dialog.slide_position == "loading" and guard.at_loading_position
    assert dialog.btn_load_slide.text() == "Move to Scanning Position"  # retry offered


def _stage_utils_dialog(qtbot, is_live, keep_live_off_at_loading):
    from unittest.mock import MagicMock

    from control.widgets import StageUtils

    live = MagicMock()
    live.is_live = is_live
    live.stop_live.side_effect = lambda: setattr(live, "is_live", False)
    live.start_live.side_effect = lambda: setattr(live, "is_live", True)
    dialog = StageUtils(MagicMock(), live, is_wellplate=False)
    qtbot.addWidget(dialog)
    dialog.keep_live_off_at_loading = keep_live_off_at_loading
    return dialog, live


def _toggle(dialog, success=True):
    with patch("control.widgets.move_to_loading_position"), patch("control.widgets.move_to_scanning_position"):
        going_to_loading = dialog.slide_position != "loading"
        dialog.switch_position()
    if going_to_loading:
        dialog._callback_loading_position_reached(True, None)
    else:
        with patch.object(QMessageBox, "warning"):
            dialog._callback_scanning_position_reached(success, None if success else "timeout")


def test_a_lift_system_keeps_live_off_at_loading_and_resumes_it_at_scanning(qtbot):
    dialog, live = _stage_utils_dialog(qtbot, is_live=True, keep_live_off_at_loading=True)
    _toggle(dialog)
    assert dialog.slide_position == "loading" and not live.is_live  # its controls are locked there
    _toggle(dialog, success=False)  # a failed return: still at loading, live still off
    assert dialog.slide_position == "loading" and not live.is_live
    _toggle(dialog)
    assert dialog.slide_position == "scanning" and live.is_live


def test_a_lift_system_does_not_start_live_that_was_off(qtbot):
    dialog, live = _stage_utils_dialog(qtbot, is_live=False, keep_live_off_at_loading=True)
    _toggle(dialog)
    _toggle(dialog)
    live.start_live.assert_not_called()


def test_without_a_lift_live_comes_back_at_the_loading_position_as_before(qtbot):
    dialog, live = _stage_utils_dialog(qtbot, is_live=True, keep_live_off_at_loading=False)
    _toggle(dialog)
    assert dialog.slide_position == "loading" and live.is_live


def test_set_to_current_is_blocked_at_the_loading_position(qtbot, tmp_path):
    utils = _FakeStageUtils()
    widget, lift = _widget(qtbot, tmp_path, stage_utils=utils)
    assert widget.btn_height_set.isEnabled()
    widget.btn_loading.click()
    utils.slide_position = "loading"
    utils.signal_loading_position_reached.emit()
    assert not widget.btn_height_set.isEnabled()
    widget._select_format("96 well plate")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.Ok) as question:
        widget._set_selected_height_to_current()  # refused even if a click got through
    question.assert_not_called()
    assert lift.heights.get("96 well plate") is None
