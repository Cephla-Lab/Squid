"""The Z Lift window (control/widgets_z_lift.py): readout, moves off the GUI thread, editable presets."""

import threading
from unittest.mock import patch

from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtWidgets import QInputDialog, QMessageBox

from control.widgets_z_lift import LoadingPositionButton, ZLiftWidget
from squid.stage.lift import LiftPreset, LiftPresets


class _FakeLift:
    min_mm, max_mm, lowered_mm = 0.0, 8.0, 0.0

    def __init__(self):
        self.height = 2.0
        self.calls = []
        self.threads = []

    def clamp(self, mm):
        return min(max(mm, self.min_mm), self.max_mm)

    def get_position_mm(self):
        return self.height

    def limit_switches(self):
        return (self.height <= 0.0, False)

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


def _widget(qtbot, tmp_path, presets=None):
    lift = _FakeLift()
    store = LiftPresets(str(tmp_path / "presets.json"))
    store.presets = list(presets or [])
    widget = ZLiftWidget(lift, store)
    qtbot.addWidget(widget)
    return widget, lift, store


def test_readout_shows_position_and_sensors(qtbot, tmp_path):
    widget, lift, _ = _widget(qtbot, tmp_path)
    lift.height = 0.0
    widget._refresh_readout()
    assert widget.label_position.text().startswith("0.000 mm")
    assert "TRIGGERED" in widget.label_bottom.text()
    assert "clear" in widget.label_top.text()


def test_jog_runs_off_the_gui_thread_and_reenables(qtbot, tmp_path):
    widget, lift, _ = _widget(qtbot, tmp_path)
    widget.spin_step.setValue(0.25)
    with qtbot.waitSignal(widget._signal_move_finished, timeout=3000):
        widget.btn_up.click()
    qtbot.waitUntil(lambda: widget.btn_up.isEnabled(), timeout=3000)
    assert lift.calls == [("move_relative", 0.25)]
    assert lift.threads[0] is not threading.main_thread()


def test_add_set_and_go_to_preset_are_saved(qtbot, tmp_path):
    widget, lift, store = _widget(qtbot, tmp_path)
    with patch.object(QInputDialog, "getText", return_value=("40x", True)):
        widget.btn_preset_add.click()
    assert [(p.name, p.height_mm) for p in LiftPresets(store._path).presets] == [("40x", 2.0)]

    lift.height = 3.25
    widget.table.selectRow(0)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.Ok):
        widget.btn_preset_set.click()
    assert LiftPresets(store._path).presets[0].height_mm == 3.25

    lift.height = 0.0
    widget.table.selectRow(0)
    with qtbot.waitSignal(widget._signal_move_finished, timeout=3000):
        widget.btn_preset_go.click()
    assert lift.calls[-1] == ("move_to", 3.25)


def test_editing_cells_validates_and_startup_is_unique(qtbot, tmp_path):
    widget, _, store = _widget(qtbot, tmp_path, [LiftPreset("a", 1.0), LiftPreset("b", 2.0)])
    widget._fill_presets_table()

    with patch.object(QMessageBox, "warning") as warning:
        widget.table.item(0, 1).setText("99")  # outside the travel: refused and reverted
    warning.assert_called_once()
    assert store.presets[0].height_mm == 1.0 and widget.table.item(0, 1).text() == "1.000"

    widget.table.item(1, 0).setText("b2")
    assert store.presets[1].name == "b2"

    widget.table.item(0, 2).setCheckState(Qt.Checked)
    widget.table.item(1, 2).setCheckState(Qt.Checked)
    assert store.startup == "b2"
    assert widget.table.item(0, 2).checkState() == Qt.Unchecked
    assert LiftPresets(store._path).startup == "b2"


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
    a, b = LoadingPositionButton(utils), LoadingPositionButton(utils)
    qtbot.addWidget(a)
    qtbot.addWidget(b)
    a.click()
    assert utils.switches == 1
    assert not a.isEnabled() and not b.isEnabled()  # the other button saw it start
    b.click()
    assert utils.switches == 1
    utils.slide_position = "loading"
    utils.signal_loading_position_reached.emit()
    assert a.isEnabled() and b.text() == "Move To Scanning Position"
