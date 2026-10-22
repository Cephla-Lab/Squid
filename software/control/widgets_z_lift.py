"""Utils > Z Lift...: read and move the Z lift of an upright V-308 system (squid/stage/lift.py).

A non-modal window: the lift's position and its two limit sensors, jog up / down by a step, move to a height, lower,
home, the loading position, and the named preset heights (editable here, saved to Z_LIFT_PRESETS_PATH). The preset
marked "After homing" is where the lift rises to after the startup homing; with none marked it stays lowered.

Every move runs in a worker thread, one at a time. A LiftMotionGuard, shared with every loading-position button, keeps
lift moves and the loading / scanning toggle (which lowers and raises the lift around its XY travel) from overlapping:
while either runs, every control that would start the other is disabled and refused.
"""

from typing import Optional

from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import control.utils as utils
import squid.logging
from squid.stage.lift import LiftPreset, LiftPresets, LiftStage

log = squid.logging.get_logger(__name__)

_COL_NAME, _COL_HEIGHT, _COL_STARTUP = range(3)
_TRIGGERED_STYLE = "background-color: #FFB0B0; border: 1px solid #A00; padding: 2px 6px;"
_CLEAR_STYLE = "background-color: #C2FFC2; border: 1px solid #080; padding: 2px 6px;"


class LiftMotionGuard(QObject):
    """One lift motion at a time: a move from the Z Lift window, or the loading / scanning toggle.

    The toggle runs in StageUtils' worker thread and lowers the lift, moves XY, and (back to scanning) raises it
    again; LiftStage's lock only covers each single lift move, so a window move in between would raise the sample
    while XY travels. The toggle is tracked from StageUtils' own signals, so it counts wherever it was started.
    """

    signal_busy_changed = Signal(bool)

    def __init__(self, stage_utils=None, parent=None):
        super().__init__(parent)
        self._lift_move = False
        self._loading = False
        if stage_utils is not None:
            # signal_threaded_stage_move_started is emitted on the GUI thread before the toggle's thread starts,
            # so the guard is busy before any of its motion; the "reached" signals arrive from that thread.
            stage_utils.signal_threaded_stage_move_started.connect(self._on_loading_started)
            stage_utils.signal_loading_position_reached.connect(self._on_loading_finished)
            stage_utils.signal_scanning_position_reached.connect(self._on_loading_finished)

    @property
    def busy(self) -> bool:
        return self._lift_move or self._loading

    def begin_lift_move(self) -> bool:
        """Claim the lift for a move; False (and nothing claimed) while anything else is moving it."""
        if self.busy:
            return False
        self._lift_move = True
        self.signal_busy_changed.emit(True)
        return True

    def end_lift_move(self):
        self._lift_move = False
        self.signal_busy_changed.emit(self.busy)

    def _on_loading_started(self):
        self._loading = True
        self.signal_busy_changed.emit(True)

    def _on_loading_finished(self):
        self._loading = False
        self.signal_busy_changed.emit(self.busy)


class LoadingPositionButton(QPushButton):
    """Toggles between the loading and scanning positions through StageUtils, which owns that state and the GUI's
    reactions to it; with a Z lift the move lowers it first and raises it back last (squid.stage.utils).
    Disabled, and refusing, while the shared LiftMotionGuard is busy."""

    def __init__(self, stage_utils, motion_guard: LiftMotionGuard, parent=None):
        super().__init__(parent)
        self.stage_utils = stage_utils
        self.motion_guard = motion_guard
        self.clicked.connect(self._toggle)
        motion_guard.signal_busy_changed.connect(self.refresh)
        stage_utils.signal_loading_position_reached.connect(self.refresh)
        stage_utils.signal_scanning_position_reached.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        at_loading = self.stage_utils.slide_position == "loading"
        self.setText("Move To Scanning Position" if at_loading else "Move To Loading Position")
        self.setStyleSheet("background-color: #C2FFC2" if at_loading else "background-color: #C2C2FF")
        self.setEnabled(not self.motion_guard.busy)

    def _toggle(self):
        if not self.motion_guard.busy:
            self.stage_utils.switch_position()


class ZLiftWidget(QDialog):
    # (success, error message) from the worker thread, delivered on the GUI thread
    _signal_move_finished = Signal(bool, str)

    def __init__(self, lift: LiftStage, presets: LiftPresets, stage_utils=None, parent=None):
        super().__init__(parent)
        self.lift = lift
        self.presets = presets
        # StageUtils owns the loading / scanning toggle (and the GUI's reactions to it); None hides the button.
        self.stage_utils = stage_utils
        # Shared with every loading button (ZLiftControls takes it from here).
        self.motion_guard = LiftMotionGuard(stage_utils, parent=self)
        self.motion_guard.signal_busy_changed.connect(self._on_busy_changed)

        self.setWindowTitle("Z Lift")
        self.setModal(False)
        self._signal_move_finished.connect(self._on_move_finished)
        self._build_ui()
        self._fill_presets_table()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_readout)
        self._timer.start(200)
        self._refresh_readout()

    # --- layout -----------------------------------------------------------------------------------------------
    def _build_ui(self):
        self.label_position = QLabel()
        self.label_position.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self.label_bottom = QLabel("Bottom sensor")
        self.label_top = QLabel("Top sensor")
        readout = QGroupBox("Position")
        readout_layout = QGridLayout(readout)
        readout_layout.addWidget(self.label_position, 0, 0, 1, 2)
        readout_layout.addWidget(self.label_bottom, 1, 0)
        readout_layout.addWidget(self.label_top, 1, 1)
        readout_layout.addWidget(QLabel(f"Travel {self.lift.min_mm:g} to {self.lift.max_mm:g} mm"), 2, 0, 1, 2)

        self.spin_step = QDoubleSpinBox()
        self.spin_step.setDecimals(3)
        self.spin_step.setRange(0.001, max(0.001, self.lift.max_mm - self.lift.min_mm))
        self.spin_step.setSingleStep(0.05)
        self.spin_step.setValue(0.1)
        self.spin_step.setSuffix(" mm")
        self.btn_up = QPushButton("Up")
        self.btn_down = QPushButton("Down")
        self.btn_up.clicked.connect(lambda: self._jog(+1))
        self.btn_down.clicked.connect(lambda: self._jog(-1))

        self.spin_target = QDoubleSpinBox()
        self.spin_target.setDecimals(3)
        self.spin_target.setRange(self.lift.min_mm, self.lift.max_mm)
        self.spin_target.setSuffix(" mm")
        self.btn_go = QPushButton("Move To")
        self.btn_go.clicked.connect(lambda: self._run_move(self.lift.move_to, mm=self.spin_target.value()))

        self.btn_lower = QPushButton("Lower")
        self.btn_lower.setToolTip(f"Move down to the lowered position ({self.lift.lowered_mm:g} mm)")
        self.btn_lower.clicked.connect(lambda: self._run_move(self.lift.lower))
        self.btn_home = QPushButton("Home")
        self.btn_home.setToolTip("Drive down to the bottom sensor and make it 0 mm")
        self.btn_home.clicked.connect(self._home)

        jog = QGroupBox("Move")
        jog_layout = QGridLayout(jog)
        jog_layout.addWidget(QLabel("Step"), 0, 0)
        jog_layout.addWidget(self.spin_step, 0, 1)
        jog_layout.addWidget(self.btn_up, 0, 2)
        jog_layout.addWidget(self.btn_down, 0, 3)
        jog_layout.addWidget(QLabel("Height"), 1, 0)
        jog_layout.addWidget(self.spin_target, 1, 1)
        jog_layout.addWidget(self.btn_go, 1, 2, 1, 2)
        jog_layout.addWidget(self.btn_lower, 2, 0, 1, 2)
        jog_layout.addWidget(self.btn_home, 2, 2, 1, 2)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Height (mm)", "After homing"])
        self.table.horizontalHeader().setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_preset_item_changed)
        self.btn_preset_go = QPushButton("Go To")
        self.btn_preset_add = QPushButton("Add Current")
        self.btn_preset_set = QPushButton("Set To Current")
        self.btn_preset_remove = QPushButton("Remove")
        self.btn_preset_go.clicked.connect(self._go_to_selected_preset)
        self.btn_preset_add.clicked.connect(self._add_preset)
        self.btn_preset_set.clicked.connect(self._set_selected_preset_to_current)
        self.btn_preset_remove.clicked.connect(self._remove_selected_preset)
        preset_buttons = QHBoxLayout()
        for b in (self.btn_preset_go, self.btn_preset_add, self.btn_preset_set, self.btn_preset_remove):
            preset_buttons.addWidget(b)
        presets_box = QGroupBox("Presets")
        presets_layout = QVBoxLayout(presets_box)
        presets_layout.addWidget(self.table)
        presets_layout.addLayout(preset_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(readout)
        layout.addWidget(jog)
        layout.addWidget(presets_box)

        self.btn_loading = None
        if self.stage_utils is not None:
            self.btn_loading = LoadingPositionButton(self.stage_utils, self.motion_guard)
            layout.addWidget(self.btn_loading)

        self._move_controls = [
            self.btn_up,
            self.btn_down,
            self.btn_go,
            self.btn_lower,
            self.btn_home,
            self.btn_preset_go,
        ]

    # --- readout ----------------------------------------------------------------------------------------------
    def _refresh_readout(self):
        try:
            position = self.lift.get_position_mm()
            bottom, top = self.lift.limit_switches()
        except Exception as e:  # never let a readout error kill the timer
            self.label_position.setText(f"Unavailable ({e})")
            return
        self.label_position.setText(f"{position:.3f} mm" + ("   (moving)" if self.motion_guard.busy else ""))
        for label, name, triggered in ((self.label_bottom, "Bottom", bottom), (self.label_top, "Top", top)):
            label.setText(f"{name} sensor: {'TRIGGERED' if triggered else 'clear'}")
            label.setStyleSheet(_TRIGGERED_STYLE if triggered else _CLEAR_STYLE)

    # --- moves ------------------------------------------------------------------------------------------------
    def _on_busy_changed(self, busy: bool):
        for b in self._move_controls:
            b.setEnabled(not busy)

    def _run_move(self, operation, **kwargs):
        if not self.motion_guard.begin_lift_move():
            return
        utils.threaded_operation_helper(
            operation, lambda ok, msg: self._signal_move_finished.emit(ok, msg or ""), **kwargs
        )

    def _on_move_finished(self, success: bool, error_msg: str):
        self.motion_guard.end_lift_move()
        self._refresh_readout()
        if not success:
            QMessageBox.warning(self, "Z Lift", f"The lift move failed:\n{error_msg}")

    def _jog(self, direction: int):
        self._run_move(self.lift.move_relative, delta_mm=direction * self.spin_step.value())

    def _home(self):
        reply = QMessageBox.question(
            self,
            "Home Z Lift",
            "The lift drives down to its bottom sensor, which becomes 0 mm. Continue?",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Ok:
            self._run_move(self.lift.home)

    # --- presets ----------------------------------------------------------------------------------------------
    def _fill_presets_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for preset in self.presets.presets:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, _COL_NAME, QTableWidgetItem(preset.name))
            self.table.setItem(row, _COL_HEIGHT, QTableWidgetItem(f"{preset.height_mm:.3f}"))
            startup = QTableWidgetItem()
            startup.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            startup.setCheckState(Qt.Checked if preset.name == self.presets.startup else Qt.Unchecked)
            self.table.setItem(row, _COL_STARTUP, startup)
        self.table.blockSignals(False)

    def _selected_row(self) -> Optional[int]:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _save_presets(self):
        try:
            self.presets.save()
        except OSError as e:
            QMessageBox.warning(self, "Z Lift", f"Could not save the presets:\n{e}")

    def _unique_name(self, name: str, ignore_row: Optional[int] = None) -> bool:
        return not any(p.name == name for i, p in enumerate(self.presets.presets) if i != ignore_row)

    def _on_preset_item_changed(self, item: QTableWidgetItem):
        row, col = item.row(), item.column()
        preset = self.presets.presets[row]
        if col == _COL_NAME:
            name = item.text().strip()
            if not name or not self._unique_name(name, ignore_row=row):
                QMessageBox.warning(self, "Z Lift", f"Preset names must be unique and not empty: {name!r}.")
            else:
                if self.presets.startup == preset.name:
                    self.presets.startup = name
                preset.name = name
        elif col == _COL_HEIGHT:
            try:
                height = float(item.text())
            except ValueError:
                height = None
            if height is None or self.lift.clamp(height) != height:
                QMessageBox.warning(
                    self,
                    "Z Lift",
                    f"Heights must be numbers from {self.lift.min_mm:g} to {self.lift.max_mm:g} mm: {item.text()!r}.",
                )
            else:
                preset.height_mm = height
        elif col == _COL_STARTUP:
            self.presets.startup = preset.name if item.checkState() == Qt.Checked else None
        self._save_presets()
        self._fill_presets_table()  # reverts a refused edit and keeps the "After homing" check unique
        self.table.selectRow(row)

    def _add_preset(self):
        name, ok = QInputDialog.getText(self, "Add Preset", "Name for the current lift height:")
        name = name.strip()
        if not ok or not name:
            return
        if not self._unique_name(name):
            QMessageBox.warning(self, "Z Lift", f"There is already a preset named {name!r}.")
            return
        self.presets.presets.append(LiftPreset(name, round(self.lift.get_position_mm(), 4)))
        self._save_presets()
        self._fill_presets_table()
        self.table.selectRow(self.table.rowCount() - 1)

    def _set_selected_preset_to_current(self):
        row = self._selected_row()
        if row is None:
            return
        preset = self.presets.presets[row]
        height = round(self.lift.get_position_mm(), 4)
        reply = QMessageBox.question(
            self,
            "Set Preset",
            f"Set '{preset.name}' from {preset.height_mm:.3f} mm to the current {height:.3f} mm?",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if reply != QMessageBox.Ok:
            return
        preset.height_mm = height
        self._save_presets()
        self._fill_presets_table()
        self.table.selectRow(row)

    def _remove_selected_preset(self):
        row = self._selected_row()
        if row is None:
            return
        preset = self.presets.presets.pop(row)
        if self.presets.startup == preset.name:
            self.presets.startup = None
        self._save_presets()
        self._fill_presets_table()

    def _go_to_selected_preset(self):
        row = self._selected_row()
        if row is None:
            return
        self._run_move(self.lift.move_to, mm=self.presets.presets[row].height_mm)

    def hideEvent(self, event):
        # A hidden window keeps no timer running; showing it again restarts the readout.
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._timer.start(200)
        if self.btn_loading is not None:
            self.btn_loading.refresh()
        super().showEvent(event)


class ZLiftControls(QWidget):
    """The control panel's row for a lift system: the loading position toggle (sharing the window's motion guard),
    and the button opening the window."""

    def __init__(self, lift_window: ZLiftWidget, stage_utils, parent=None):
        super().__init__(parent)
        self.btn_loading = LoadingPositionButton(stage_utils, lift_window.motion_guard)
        self.btn_lift = QPushButton("Z Lift...")
        self.btn_lift.clicked.connect(lambda: (lift_window.show(), lift_window.raise_(), lift_window.activateWindow()))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.btn_loading)
        layout.addWidget(self.btn_lift)
