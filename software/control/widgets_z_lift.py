"""The Z lift of an upright V-308 system in the GUI (squid/stage/lift.py).

The lift's position is shown in the image display's status bar, next to the stage coordinates (core.ImageDisplayWindow),
and a LoadingPositionButton sits in the Stages tab. ZLiftWidget (Utils > Z Lift...) is the full window: the two limit
sensors, jog up /
down by a step, move to a height, lower, home, and the working height for each sample format, editable in a table and
saved to Z_LIFT_HEIGHTS_PATH. The lift rises to the current format's height after the startup homing and on leaving
the loading position.

Every move runs in a worker thread, one at a time. A LiftMotionGuard, shared by the window and every
loading-position button, keeps lift moves and the loading / scanning toggle (which lowers and raises the lift around
its XY travel) from overlapping: while either runs, every control that would start the other is disabled and refused.
"""

from typing import Callable, List, Optional

from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtGui import QFont
from qtpy.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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
from squid.stage.lift import LiftStage

log = squid.logging.get_logger(__name__)

_COL_FORMAT, _COL_HEIGHT = range(2)
_TRIGGERED_STYLE = "background-color: #FFB0B0; border: 1px solid #A00; padding: 2px 6px;"
_CLEAR_STYLE = "background-color: #C2FFC2; border: 1px solid #080; padding: 2px 6px;"


class LiftMotionGuard(QObject):
    """One lift motion at a time: a lift move from the GUI, the loading / scanning toggle, or an objective swap.

    The toggle runs in StageUtils' worker thread and lowers the lift, moves XY, and (back to scanning) raises it
    again; LiftStage's lock only covers each single lift move, so a GUI move in between would raise the sample
    while XY travels. The toggle is tracked from StageUtils' own signals, so it counts wherever it was started.

    At the loading position nothing may move the lift (a sample is being loaded): ``moves_blocked`` holds from the
    loading position being reached until the toggle back to scanning starts. The toggle itself stays allowed then,
    so ``busy`` (which the loading buttons follow) does not include it.
    """

    signal_busy_changed = Signal(bool)
    # (success, error message) of a move started with run(), delivered on the GUI thread
    signal_move_finished = Signal(bool, str)

    def __init__(self, stage_utils=None, parent=None):
        super().__init__(parent)
        self._stage_utils = stage_utils
        self._lift_move = False
        self._loading = False
        self._claimed = False  # held by an operation run elsewhere: an objective swap
        self._at_loading_position = False
        if stage_utils is not None:
            # signal_threaded_stage_move_started is emitted on the GUI thread before the toggle's thread starts,
            # so the guard is busy before any of its motion; the "reached" signals arrive from that thread.
            stage_utils.signal_threaded_stage_move_started.connect(self._on_loading_started)
            stage_utils.signal_loading_position_reached.connect(self._on_loading_position_reached)
            stage_utils.signal_scanning_position_reached.connect(self._on_scanning_position_reached)
        self.signal_move_finished.connect(self._end_lift_move)

    @property
    def busy(self) -> bool:
        """Something is moving the lift (or about to): the loading toggle must not start either."""
        return self._lift_move or self._loading or self._claimed

    @property
    def at_loading_position(self) -> bool:
        return self._at_loading_position

    @property
    def moves_blocked(self) -> bool:
        """No lift move and no objective swap may start: busy, or at the loading position."""
        return self.busy or self._at_loading_position

    def claim(self) -> bool:
        """Hold the lift for an operation that moves it on its own (an objective swap lowers it), from its start to
        its end. False (nothing held) while anything else is moving the lift, or at the loading position: the caller
        must not start then."""
        if self.moves_blocked:
            return False
        self._claimed = True
        self.signal_busy_changed.emit(True)
        return True

    def release(self):
        self._claimed = False
        self.signal_busy_changed.emit(self.busy)

    def run(self, operation: Callable, **kwargs) -> bool:
        """Run a lift move in a worker thread; False (nothing started) while anything else is moving the lift or at
        the loading position."""
        if self.moves_blocked:
            return False
        self._lift_move = True
        self.signal_busy_changed.emit(True)
        utils.threaded_operation_helper(
            operation, lambda ok, msg: self.signal_move_finished.emit(ok, msg or ""), **kwargs
        )
        return True

    def _end_lift_move(self, *_):
        self._lift_move = False
        self.signal_busy_changed.emit(self.busy)

    def _on_loading_started(self):
        self._loading = True
        if self._stage_utils is not None and self._stage_utils.slide_position == "loading":
            self._at_loading_position = False  # leaving it: the toggle back to scanning has started
        self.signal_busy_changed.emit(True)

    def _on_loading_position_reached(self):
        self._loading = False
        self._at_loading_position = True
        self.signal_busy_changed.emit(self.busy)

    def _on_scanning_position_reached(self):
        self._loading = False
        self._at_loading_position = False
        self.signal_busy_changed.emit(self.busy)


class LoadingPositionButton(QPushButton):
    """Toggles between the loading and scanning positions through StageUtils, which owns that state and the GUI's
    reactions to it; with a Z lift the move lowers it first and raises it last (squid.stage.utils).
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


def _set_sensor_label(label: QLabel, name: str, triggered: bool):
    label.setText(f"{name}: {'TRIGGERED' if triggered else 'clear'}")
    label.setStyleSheet(_TRIGGERED_STYLE if triggered else _CLEAR_STYLE)


class ZLiftWidget(QDialog):
    """Utils > Z Lift...: move the lift, and edit the working height for each sample format."""

    def __init__(self, lift: LiftStage, sample_formats_fn: Callable[[], List[str]], stage_utils=None, parent=None):
        super().__init__(parent)
        self.lift = lift
        # Read on every refresh: "calibrate format..." can add formats while the software runs.
        self.sample_formats_fn = sample_formats_fn
        self.sample_formats: List[str] = []
        # StageUtils owns the loading / scanning toggle (and the GUI's reactions to it); None hides the button.
        self.stage_utils = stage_utils
        # Shared with every loading button (the Stages tab's takes it from here).
        self.motion_guard = LiftMotionGuard(stage_utils, parent=self)
        self.motion_guard.signal_busy_changed.connect(self._on_busy_changed)
        self.motion_guard.signal_move_finished.connect(self._on_move_finished)

        self.setWindowTitle("Z Lift")
        self.setModal(False)
        self._build_ui()
        self.refresh_formats()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_readout)
        self._timer.start(200)
        self._refresh_readout()

    # --- layout -----------------------------------------------------------------------------------------------
    def _build_ui(self):
        self.label_position = QLabel()
        self.label_position.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self.label_bottom = QLabel()
        self.label_top = QLabel()
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
        self.btn_go.clicked.connect(lambda: self.motion_guard.run(self.lift.move_to, mm=self.spin_target.value()))

        self.btn_lower = QPushButton("Lower")
        self.btn_lower.setToolTip(f"Move down to the lowered position ({self.lift.lowered_mm:g} mm)")
        self.btn_lower.clicked.connect(lambda: self.motion_guard.run(self.lift.lower))
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

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Sample format", "Height (mm)"])
        self.table.horizontalHeader().setSectionResizeMode(_COL_FORMAT, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_height_item_changed)
        self.btn_height_go = QPushButton("Go To")
        self.btn_height_set = QPushButton("Set To Current")
        self.btn_height_clear = QPushButton("Clear")
        self.btn_height_go.clicked.connect(self._go_to_selected_height)
        self.btn_height_set.clicked.connect(self._set_selected_height_to_current)
        self.btn_height_clear.clicked.connect(self._clear_selected_height)
        height_buttons = QHBoxLayout()
        for b in (self.btn_height_go, self.btn_height_set, self.btn_height_clear):
            height_buttons.addWidget(b)
        heights_box = QGroupBox("Working height per sample format")
        heights_layout = QVBoxLayout(heights_box)
        heights_layout.addWidget(
            QLabel("The lift rises to the current format's height after homing and on leaving the loading position.")
        )
        heights_layout.addWidget(self.table)
        heights_layout.addLayout(height_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(readout)
        layout.addWidget(jog)
        layout.addWidget(heights_box)

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
            self.btn_height_go,
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
        _set_sensor_label(self.label_bottom, "Bottom sensor", bottom)
        _set_sensor_label(self.label_top, "Top sensor", top)

    # --- moves ------------------------------------------------------------------------------------------------
    def _on_busy_changed(self, busy: bool):
        blocked = self.motion_guard.moves_blocked  # busy, or at the loading position
        for b in self._move_controls:
            b.setEnabled(not blocked)

    def _on_move_finished(self, success: bool, error_msg: str):
        self._refresh_readout()
        if not success:
            QMessageBox.warning(self, "Z Lift", f"The lift move failed:\n{error_msg}")

    def _jog(self, direction: int):
        self.motion_guard.run(self.lift.move_relative, delta_mm=direction * self.spin_step.value())

    def _home(self):
        reply = QMessageBox.question(
            self,
            "Home Z Lift",
            "The lift drives down to its bottom sensor, which becomes 0 mm. Continue?",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Ok:
            self.motion_guard.run(self.lift.home)

    # --- heights per sample format ----------------------------------------------------------------------------
    def refresh_formats(self, *_):
        """Refill the table; the current sample format's row is bold. Connected to the format selector."""
        current = self.lift.current_format()
        self.sample_formats = list(self.sample_formats_fn())
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for sample_format in self.sample_formats:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(sample_format + ("  (current)" if sample_format == current else ""))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name.setData(Qt.UserRole, sample_format)
            height = self.lift.heights.get(sample_format)
            height_item = QTableWidgetItem("" if height is None else f"{height:.3f}")
            if sample_format == current:
                font = QFont()
                font.setBold(True)
                name.setFont(font)
                height_item.setFont(font)
            self.table.setItem(row, _COL_FORMAT, name)
            self.table.setItem(row, _COL_HEIGHT, height_item)
        self.table.blockSignals(False)

    def _selected_format(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), _COL_FORMAT).data(Qt.UserRole) if rows else None

    def _select_format(self, sample_format: str):
        if sample_format in self.sample_formats:
            self.table.selectRow(self.sample_formats.index(sample_format))

    def _save_height(self, sample_format: str, height_mm: Optional[float]):
        self.lift.heights.set(sample_format, height_mm)
        try:
            self.lift.heights.save()
        except OSError as e:
            QMessageBox.warning(self, "Z Lift", f"Could not save the lift heights:\n{e}")
        self.refresh_formats()
        self._select_format(sample_format)

    def _on_height_item_changed(self, item: QTableWidgetItem):
        sample_format = self.table.item(item.row(), _COL_FORMAT).data(Qt.UserRole)
        text = item.text().strip()
        if not text:
            self._save_height(sample_format, None)
            return
        try:
            height = float(text)
        except ValueError:
            height = None
        if height is None or self.lift.clamp(height) != height:
            QMessageBox.warning(
                self,
                "Z Lift",
                f"Heights must be numbers from {self.lift.min_mm:g} to {self.lift.max_mm:g} mm: {text!r}.",
            )
            self.refresh_formats()  # revert the refused edit
            return
        self._save_height(sample_format, height)

    def _set_selected_height_to_current(self):
        sample_format = self._selected_format()
        if sample_format is None:
            return
        height = round(self.lift.get_position_mm(), 4)
        old = self.lift.heights.get(sample_format)
        old_text = "none" if old is None else f"{old:.3f} mm"
        reply = QMessageBox.question(
            self,
            "Set Lift Height",
            f"Set the {sample_format} height from {old_text} to the current {height:.3f} mm?",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Ok:
            self._save_height(sample_format, height)

    def _clear_selected_height(self):
        sample_format = self._selected_format()
        if sample_format is not None:
            self._save_height(sample_format, None)

    def _go_to_selected_height(self):
        sample_format = self._selected_format()
        height = self.lift.heights.get(sample_format)
        if height is not None:
            self.motion_guard.run(self.lift.move_to, mm=height)

    def hideEvent(self, event):
        # A hidden window keeps no timer running; showing it again restarts the readout.
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        self._timer.start(200)
        self.refresh_formats()
        if self.btn_loading is not None:
            self.btn_loading.refresh()
        super().showEvent(event)
