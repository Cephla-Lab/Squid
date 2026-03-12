"""
Orchestrator Widget components for multi-round experiment control.

Provides dockable widgets for the orchestrator:
- OrchestratorControlPanel: Status, buttons, progress, intervention
- OrchestratorWorkflowTree: Hierarchical workflow display
"""

import os
import time as _time
from collections import deque

from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING

from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QGroupBox,
    QFrame,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QMenu,
    QAction,
    QSizePolicy,
    QMessageBox,
)
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QPen, QPainterPath
import pyqtgraph as pg

from squid.core.events import handles, LoadScanCoordinatesCommand
from squid.ui.widgets.base import EventBusWidget
from squid.backend.controllers.orchestrator import (
    OrchestratorState,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorStepStarted,
    OrchestratorStepCompleted,
    OrchestratorAttemptUpdate,
    OrchestratorInterventionRequired,
    OrchestratorError,
    OrchestratorTimingSnapshot,
    RunStateUpdated,
    ResolveInterventionCommand,
    ValidateProtocolCommand,
    ProtocolValidationComplete,
)
from squid.backend.controllers.multipoint import (
    FovTaskStarted,
    FovTaskCompleted,
    JumpToFovCommand,
    SkipFovCommand,
    RequeueFovCommand,
)
from squid.backend.controllers.orchestrator.validation import ValidationSummary
from squid.ui.widgets.orchestrator.validation_dialog import ValidationResultDialog

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus
    from squid.backend.controllers.orchestrator import OrchestratorController
    from squid.ui.widgets.orchestrator.parameter_panel import ParameterInspectionPanel

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds into a compact human-readable string."""
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"


# ============================================================================
# Shared theme constants
# ============================================================================

STATUS_COLORS = {
    "pending": QColor("#888888"),
    "running": QColor("#42A5F5"),
    "retrying": QColor("#29B6F6"),
    "completed": QColor("#66BB6A"),
    "failed": QColor("#EF5350"),
    "skipped": QColor("#FFA726"),
}

# Muted dark-theme button styles
_BTN_BASE = (
    "QPushButton {{ "
    "  min-height: 30px; padding: 4px 12px; border-radius: 4px; "
    "  font-weight: 500; border: 1px solid {border}; "
    "  background-color: {bg}; color: {fg}; "
    "}} "
    "QPushButton:hover {{ background-color: {hover}; }} "
    "QPushButton:disabled {{ background-color: #3a3a3a; color: #666; border-color: #444; }}"
)

BTN_STYLES = {
    "primary":     _BTN_BASE.format(bg="#2E7D32", fg="#fff", border="#388E3C", hover="#388E3C"),
    "destructive": _BTN_BASE.format(bg="#C62828", fg="#fff", border="#D32F2F", hover="#D32F2F"),
    "secondary":   _BTN_BASE.format(bg="#424242", fg="#ddd", border="#555", hover="#515151"),
    "pause":       _BTN_BASE.format(bg="#E65100", fg="#fff", border="#EF6C00", hover="#EF6C00"),
    "resume":      _BTN_BASE.format(bg="#1565C0", fg="#fff", border="#1976D2", hover="#1976D2"),
    "acknowledge": _BTN_BASE.format(bg="#F9A825", fg="#3E2723", border="#FBC02D", hover="#FDD835"),
}

# Current-step indicator highlight (applied to column 0 background)
_CURRENT_STEP_BG = QColor("#1A3A5C")  # Subtle blue tint for dark theme
_RUNNING_BG = QColor("#1A3A2C")  # Subtle green tint


class TimeAxisItem(pg.AxisItem):
    """X-axis that shows elapsed time as human-readable labels."""

    def tickStrings(self, values, scale, spacing):
        result = []
        for v in values:
            seconds = int(v)
            if seconds < 60:
                result.append(f"{seconds}s")
            elif seconds < 3600:
                result.append(f"{seconds // 60}m")
            else:
                h = seconds // 3600
                m = (seconds % 3600) // 60
                result.append(f"{h}h{m:02d}m" if m else f"{h}h")
        return result


class AccumulatingPlot(QWidget):
    """A pyqtgraph plot that accumulates data over the full experiment run."""

    def __init__(self, title: str, y_label: str, line_color: str = "#4FC3F7",
                 y_range: tuple = None, parent=None):
        super().__init__(parent)
        self._x_data: list = []
        self._y_data: list = []
        self._run_start: float = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        time_axis = TimeAxisItem(orientation="bottom")
        self._plot_widget = pg.PlotWidget(axisItems={"bottom": time_axis})
        self._plot_widget.setBackground("#171b21")
        self._plot_widget.setTitle(title, color="#b8c1cc", size="9pt")
        self._plot_widget.setLabel("left", y_label, color="#7f8b99", **{"font-size": "8pt"})
        self._plot_widget.setLabel("bottom", "Time", color="#7f8b99", **{"font-size": "8pt"})
        self._plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self._plot_widget.setMinimumHeight(140)
        # Disable SI prefix scaling on Y-axis (prevents ×0.001 display)
        self._plot_widget.getAxis("left").enableAutoSIPrefix(False)

        if y_range is not None:
            self._plot_widget.setYRange(y_range[0], y_range[1])

        pen = pg.mkPen(color=line_color, width=2)
        self._curve = self._plot_widget.plot(pen=pen)

        self._marker = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen(None), brush=pg.mkBrush(line_color)
        )
        self._plot_widget.addItem(self._marker)

        self._value_text = pg.TextItem(color=line_color, anchor=(1, 1))
        self._plot_widget.addItem(self._value_text)

        layout.addWidget(self._plot_widget)

    def set_run_start(self, t: float):
        self._run_start = t
        self._x_data.clear()
        self._y_data.clear()

    def append(self, timestamp: float, value: float):
        elapsed = timestamp - self._run_start if self._run_start > 0 else 0.0
        self._x_data.append(elapsed)
        self._y_data.append(value)
        self._curve.setData(self._x_data, self._y_data)
        if self._x_data:
            self._marker.setData([self._x_data[-1]], [self._y_data[-1]])
            self._value_text.setPos(self._x_data[-1], self._y_data[-1])
            self._value_text.setText(f"{value:.1f}")

    def add_horizontal_line(self, y: float, color: str = "#ff6b6b", label: str = ""):
        pen = pg.mkPen(color=color, width=1, style=Qt.DashLine)
        line = pg.InfiniteLine(pos=y, angle=0, pen=pen, label=label,
                               labelOpts={"color": color, "position": 0.95})
        self._plot_widget.addItem(line)

    def clear_data(self):
        self._x_data.clear()
        self._y_data.clear()
        self._curve.setData([], [])
        self._marker.setData([], [])


class SubsystemBreakdownWidget(QFrame):
    """Simple stacked bar showing accumulated subsystem time split."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._values: Dict[str, float] = {}
        self.setMinimumHeight(90)
        self.setStyleSheet(
            "QFrame { background-color: #20252b; border: 1px solid #303841; border-radius: 6px; }"
        )

    def set_values(self, values: Dict[str, float]) -> None:
        self._values = {key: float(value) for key, value in values.items() if float(value) > 0.0}
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(10, 10, -10, -10)
        painter.fillRect(rect, QColor("#20252b"))

        total = sum(self._values.values())
        bar_rect = rect.adjusted(0, 4, 0, -24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#15181d"))
        painter.drawRoundedRect(bar_rect, 5, 5)

        if total <= 0:
            painter.setPen(QPen(QColor("#5f6b7a"), 1))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No subsystem timing yet")
            return

        palette = {
            "imaging": QColor("#4FC3F7"),
            "fluidics": QColor("#81C784"),
            "intervention": QColor("#FFB74D"),
            "paused": QColor("#BA68C8"),
            "waiting": QColor("#90A4AE"),
        }
        x = float(bar_rect.left())
        for name, value in sorted(self._values.items()):
            width = bar_rect.width() * (value / total)
            color = palette.get(name, QColor("#90A4AE"))
            painter.setBrush(color)
            painter.drawRoundedRect(int(x), bar_rect.top(), max(2, int(width)), bar_rect.height(), 4, 4)
            x += width

        # Legend — wrap to multiple rows if needed
        max_item_width = 100
        items_per_row = max(1, rect.width() // max_item_width)
        legend_y = bar_rect.bottom() + 14
        for i, (name, secs) in enumerate(self._values.items()):
            col = i % items_per_row
            row = i // items_per_row
            lx = rect.left() + col * max_item_width
            ly = legend_y + row * 16
            color = palette.get(name, QColor("#90A4AE"))
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(lx, ly, 8, 8)
            painter.setPen(QPen(QColor("#b8c1cc"), 1))
            mins = int(secs) // 60
            sec = int(secs) % 60
            painter.drawText(lx + 12, ly + 8, f"{name}: {mins}m{sec:02d}s")


class OrchestratorControlPanel(EventBusWidget):
    """Control panel for experiment orchestration.

    Contains:
    - Status display
    - Control buttons (Load, Start, Pause, Resume, Abort)
    - Progress tracking
    - Intervention acknowledgment
    """

    # Signals for thread-safe UI updates
    state_changed = pyqtSignal(str, str)  # old_state, new_state
    progress_updated = pyqtSignal(object)  # OrchestratorProgress
    intervention_required = pyqtSignal(object)  # OrchestratorInterventionRequired
    timing_snapshot = pyqtSignal(object)  # OrchestratorTimingSnapshot
    run_state_updated = pyqtSignal(object)  # RunStateUpdated
    error_occurred = pyqtSignal(str, str)  # type, message
    fov_positions_changed = pyqtSignal(dict)  # FOV positions dict
    protocol_loaded = pyqtSignal(dict)  # protocol data
    validation_complete = pyqtSignal(object)  # ValidationSummary

    def __init__(
        self,
        event_bus: "UIEventBus",
        orchestrator: Optional["OrchestratorController"] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(event_bus, parent)
        self._orchestrator = orchestrator
        self._protocol_data: Optional[Dict[str, Any]] = None
        self._protocol_path: Optional[str] = None
        self._base_path: Optional[str] = None
        self._experiment_id: Optional[str] = None
        self._fov_positions: Dict[str, List[Tuple[float, float, float]]] = {}
        self._validated: bool = False  # True after successful validation
        # Start-from position (set by tree navigation)
        self._start_round_index: int = 0
        self._start_step_index: int = 0
        self._start_fov_index: int = 0
        self._run_single_round: bool = False
        self._last_progress: Optional[OrchestratorProgress] = None
        self._history: Dict[str, deque[float]] = {
            "progress": deque(maxlen=120),
            "eta": deque(maxlen=120),
            "overhead": deque(maxlen=120),
        }

        self._setup_ui()
        self._connect_signals()
        self._update_button_states(OrchestratorState.IDLE)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        overview_group = self._create_status_section()
        layout.addWidget(overview_group)

        controls = self._create_controls_section()
        layout.addWidget(controls)

        progress_group = self._create_progress_section()
        layout.addWidget(progress_group)

        metrics_group = self._create_metrics_section()
        layout.addWidget(metrics_group)

        self._intervention_frame = self._create_intervention_section()
        self._intervention_frame.setVisible(False)
        layout.addWidget(self._intervention_frame)

        layout.addStretch()

    def _create_status_section(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("background-color: #20252b; border-radius: 6px;")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        # Experiment label (shown above the health strip)
        self._experiment_label = QLabel("No protocol loaded")
        self._experiment_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._experiment_label.setWordWrap(True)
        self._experiment_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        outer.addWidget(self._experiment_label)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # -- State Zone --
        state_zone = QVBoxLayout()
        state_zone.setSpacing(0)
        self._status_label = QLabel("IDLE")
        self._status_label.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #edf2f7;"
        )
        self._time_label = QLabel("")
        self._time_label.setStyleSheet("font-size: 9px; color: #7f8b99;")
        state_zone.addWidget(self._status_label)
        state_zone.addWidget(self._time_label)
        layout.addLayout(state_zone, 2)

        # -- Position Zone --
        pos_zone = QVBoxLayout()
        pos_zone.setSpacing(0)
        self._round_label = QLabel("Round: -")
        self._round_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        self._step_label = QLabel("Step: -")
        self._step_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        self._fov_label = QLabel("FOV: -")
        self._fov_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        self._attempt_label = QLabel("")
        self._attempt_label.setStyleSheet("font-size: 9px; color: #FFB74D;")
        self._attempt_label.hide()
        pos_zone.addWidget(self._round_label)
        pos_zone.addWidget(self._step_label)
        pos_zone.addWidget(self._fov_label)
        pos_zone.addWidget(self._attempt_label)
        layout.addLayout(pos_zone, 3)

        # -- Health Zone --
        health_zone = QVBoxLayout()
        health_zone.setSpacing(0)

        focus_row = QHBoxLayout()
        self._focus_dot = QLabel("\u25cf")
        self._focus_dot.setStyleSheet("font-size: 10px; color: #888888;")
        self._focus_label = QLabel("Focus: -")
        self._focus_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        focus_row.addWidget(self._focus_dot)
        focus_row.addWidget(self._focus_label)
        focus_row.addStretch()

        self._throughput_label = QLabel("Throughput: -")
        self._throughput_label.setStyleSheet("font-size: 10px; color: #b8c1cc;")
        self._warnings_label = QLabel("")
        self._warnings_label.setStyleSheet("font-size: 9px; color: #7f8b99;")

        health_zone.addLayout(focus_row)
        health_zone.addWidget(self._throughput_label)
        health_zone.addWidget(self._warnings_label)
        layout.addLayout(health_zone, 2)

        outer.addLayout(layout)
        return frame

    def _create_controls_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Row 1: Load, Validate, Start
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self._load_btn = QPushButton("Load Protocol")
        self._load_btn.setStyleSheet(BTN_STYLES["secondary"])
        self._load_btn.clicked.connect(self._on_load_clicked)
        row1.addWidget(self._load_btn)

        self._validate_btn = QPushButton("Validate")
        self._validate_btn.setStyleSheet(BTN_STYLES["secondary"])
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        self._validate_btn.setEnabled(False)
        row1.addWidget(self._validate_btn)

        self._start_btn = QPushButton("Start Acquisition")
        self._start_btn.setStyleSheet(BTN_STYLES["primary"])
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._start_btn.setEnabled(False)
        row1.addWidget(self._start_btn)

        layout.addLayout(row1)

        # Row 2: Pause, Resume, Abort
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setStyleSheet(BTN_STYLES["pause"])
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        row2.addWidget(self._pause_btn)

        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setStyleSheet(BTN_STYLES["resume"])
        self._resume_btn.clicked.connect(self._on_resume_clicked)
        row2.addWidget(self._resume_btn)

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setStyleSheet(BTN_STYLES["destructive"])
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        row2.addWidget(self._abort_btn)
        layout.addLayout(row2)

        return widget

    def _create_progress_section(self) -> QGroupBox:
        group = QGroupBox("Progress")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Round progress
        round_layout = QHBoxLayout()
        round_layout.addWidget(QLabel("Round:"))
        self._round_label = QLabel("- / -")
        self._round_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        round_layout.addWidget(self._round_label)
        layout.addLayout(round_layout)

        # Current round name + step type
        self._round_name_label = QLabel("")
        self._round_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._round_name_label.setStyleSheet("font-style: italic; color: #aaa;")
        layout.addWidget(self._round_name_label)

        # Overall progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; }"
            "QProgressBar::chunk { background-color: #43A047; }"
        )
        layout.addWidget(self._progress_bar)

        # Time remaining
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Time remaining:"))
        self._time_remaining_label = QLabel("--")
        self._time_remaining_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._time_remaining_label.setStyleSheet("color: #aaa;")
        time_layout.addWidget(self._time_remaining_label)
        layout.addLayout(time_layout)

        overhead_layout = QHBoxLayout()
        overhead_layout.addWidget(QLabel("Paused:"))
        self._paused_time_label = QLabel("0s")
        self._paused_time_label.setStyleSheet("color: #aaa;")
        overhead_layout.addWidget(self._paused_time_label)
        overhead_layout.addStretch()
        overhead_layout.addWidget(QLabel("Retry overhead:"))
        self._retry_time_label = QLabel("0s")
        self._retry_time_label.setStyleSheet("color: #aaa;")
        overhead_layout.addWidget(self._retry_time_label)
        layout.addLayout(overhead_layout)

        return group

    def _create_metrics_section(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("background-color: #20252b; border-radius: 6px;")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._progress_plot = AccumulatingPlot(
            "Progress", "Complete %", line_color="#4FC3F7", y_range=(0, 100)
        )
        self._throughput_plot = AccumulatingPlot(
            "Throughput", "FOVs / min", line_color="#81C784"
        )
        self._focus_plot = AccumulatingPlot(
            "Focus Error", "Error (\u00b5m)", line_color="#FFB74D"
        )

        self._subsystem_breakdown = SubsystemBreakdownWidget()

        layout.addWidget(self._progress_plot)
        layout.addWidget(self._throughput_plot)
        layout.addWidget(self._focus_plot)
        layout.addWidget(self._subsystem_breakdown)

        return frame

    def _create_intervention_section(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #1f2329; border: 1px solid #57442e; border-left: 5px solid #F9A825; border-radius: 8px; }"
        )

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self._intervention_badge = QLabel("INTERVENTION")
        self._intervention_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        self._intervention_badge.setFont(font)
        self._intervention_badge.setStyleSheet(
            "background-color: #5d4037; color: #ffd54f; padding: 4px 8px; border-radius: 10px;"
        )
        header_row.addWidget(self._intervention_badge, 0, Qt.AlignmentFlag.AlignLeft)

        self._intervention_title = QLabel("Waiting for operator action")
        self._intervention_title.setStyleSheet(
            "color: #f7fafc; font-size: 14px; font-weight: 600; border: none;"
        )
        header_row.addWidget(self._intervention_title, 1)
        header_row.addStretch()
        info_layout.addLayout(header_row)

        self._intervention_message = QLabel("")
        self._intervention_message.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._intervention_message.setWordWrap(True)
        self._intervention_message.setStyleSheet("color: #edf2f7; border: none; font-size: 13px;")
        info_layout.addWidget(self._intervention_message)

        self._intervention_context = QLabel("")
        self._intervention_context.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._intervention_context.setWordWrap(True)
        self._intervention_context.setStyleSheet("color: #b8c1cc; border: none;")
        info_layout.addWidget(self._intervention_context)
        layout.addLayout(info_layout, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)

        self._acknowledge_btn = QPushButton("Continue")
        self._acknowledge_btn.setMinimumHeight(36)
        self._acknowledge_btn.setStyleSheet(BTN_STYLES["acknowledge"])
        self._acknowledge_btn.clicked.connect(self._on_acknowledge_clicked)
        button_row.addWidget(self._acknowledge_btn)

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setMinimumHeight(36)
        self._retry_btn.setStyleSheet(BTN_STYLES["resume"])
        self._retry_btn.clicked.connect(self._on_retry_clicked)
        button_row.addWidget(self._retry_btn)

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setMinimumHeight(36)
        self._skip_btn.setStyleSheet(BTN_STYLES["secondary"])
        self._skip_btn.clicked.connect(self._on_skip_clicked)
        button_row.addWidget(self._skip_btn)

        self._abort_intervention_btn = QPushButton("Abort")
        self._abort_intervention_btn.setMinimumHeight(36)
        self._abort_intervention_btn.setStyleSheet(BTN_STYLES["destructive"])
        self._abort_intervention_btn.clicked.connect(self._on_intervention_abort_clicked)
        button_row.addWidget(self._abort_intervention_btn)

        layout.addLayout(button_row, 0)

        return frame

    def _connect_signals(self) -> None:
        self.state_changed.connect(self._on_state_changed_ui)
        self.progress_updated.connect(self._on_progress_updated_ui)
        self.intervention_required.connect(self._on_intervention_required_ui)
        self.timing_snapshot.connect(self._on_timing_snapshot_ui)
        self.run_state_updated.connect(self._on_run_state_ui)
        self.error_occurred.connect(self._on_error_occurred_ui)
        self.validation_complete.connect(self._on_validation_complete_ui)

    # ========================================================================
    # Event Handlers
    # ========================================================================

    @handles(OrchestratorStateChanged)
    def _on_state_changed(self, event: OrchestratorStateChanged) -> None:
        self.state_changed.emit(event.old_state, event.new_state)

    @handles(OrchestratorProgress)
    def _on_progress(self, event: OrchestratorProgress) -> None:
        self.progress_updated.emit(event)

    @handles(OrchestratorInterventionRequired)
    def _on_intervention(self, event: OrchestratorInterventionRequired) -> None:
        self.intervention_required.emit(event)

    @handles(OrchestratorTimingSnapshot)
    def _on_timing_snapshot(self, event: OrchestratorTimingSnapshot) -> None:
        self.timing_snapshot.emit(event)

    @handles(OrchestratorError)
    def _on_error(self, event: OrchestratorError) -> None:
        self.error_occurred.emit(event.error_type, event.message)

    @handles(RunStateUpdated)
    def _on_run_state(self, event: RunStateUpdated) -> None:
        self.run_state_updated.emit(event.run_state)

    @handles(ProtocolValidationComplete)
    def _on_validation_complete(self, event: ProtocolValidationComplete) -> None:
        summary = ValidationSummary(
            protocol_name=event.protocol_name,
            total_rounds=event.total_rounds,
            total_estimated_seconds=event.estimated_seconds,
            total_disk_bytes=event.estimated_disk_bytes,
            operation_estimates=event.operation_estimates,
            errors=tuple(event.errors),
            warnings=tuple(event.warnings),
            valid=event.valid,
        )
        self.validation_complete.emit(summary)

    # ========================================================================
    # UI Slots
    # ========================================================================

    @pyqtSlot(str, str)
    def _on_state_changed_ui(self, old_state: str, new_state: str) -> None:
        del old_state
        self._status_label.setText(new_state)
        self._update_status_style(new_state)
        try:
            state = OrchestratorState[new_state]
            self._update_button_states(state)
        except KeyError:
            pass
        if new_state != "WAITING_INTERVENTION":
            self._intervention_frame.setVisible(False)

        # Reset time remaining on terminal states
        if new_state == "COMPLETED":
            self._time_remaining_label.setText("Complete")
            self._time_remaining_label.setStyleSheet("color: #66BB6A;")
            self._time_label.setText("Complete")
        elif new_state in ("FAILED", "ABORTED", "IDLE"):
            self._time_remaining_label.setText("--")
            self._time_remaining_label.setStyleSheet("color: #aaa;")
            self._time_label.setText("")

        # Initialize plots on new run
        if new_state == "RUNNING":
            now = _time.monotonic()
            self._progress_plot.set_run_start(now)
            self._throughput_plot.set_run_start(now)
            self._focus_plot.set_run_start(now)
            self._progress_plot.clear_data()
            self._throughput_plot.clear_data()
            self._focus_plot.clear_data()

    @pyqtSlot(object)
    def _on_progress_updated_ui(self, event: OrchestratorProgress) -> None:
        self._last_progress = event
        self._history["progress"].append(float(event.progress_percent))
        # Update progress section widgets (round label, progress bar, time remaining)
        self._round_label.setText(
            f"Round: {event.current_round} / {event.total_rounds}  ({event.current_round_name})"
        )
        self._round_name_label.setText(event.current_round_name)
        self._progress_bar.setValue(int(event.progress_percent))
        self._paused_time_label.setText(self._format_time_remaining(event.paused_seconds))
        self._retry_time_label.setText(
            self._format_time_remaining(event.retry_overhead_seconds)
        )

        # Update time remaining display
        if event.eta_seconds is not None and isinstance(event.eta_seconds, (int, float)) and event.eta_seconds > 0:
            eta_text = self._format_time_remaining(float(event.eta_seconds))
            self._time_remaining_label.setText(eta_text)
            self._time_remaining_label.setStyleSheet("color: #ddd;")
        elif event.progress_percent >= 100.0:
            self._time_remaining_label.setText("Complete")
            self._time_remaining_label.setStyleSheet("color: #66BB6A;")
        else:
            self._time_remaining_label.setText("--")
            self._time_remaining_label.setStyleSheet("color: #aaa;")

    @pyqtSlot(object)
    def _on_intervention_required_ui(self, event: OrchestratorInterventionRequired) -> None:
        self._intervention_message.setText(event.message)
        context_bits = []
        if event.round_name:
            context_bits.append(event.round_name)
        if event.current_step_name:
            context_bits.append(event.current_step_name)
        if event.current_fov_label:
            context_bits.append(event.current_fov_label)
        if event.attempt > 0:
            context_bits.append(f"attempt {event.attempt}")
        self._intervention_context.setText(" | ".join(context_bits))
        if event.kind == "failure":
            self._intervention_badge.setText("RECOVERY REQUIRED")
            self._intervention_badge.setStyleSheet(
                "background-color: #5d1f1f; color: #ffb4ab; padding: 4px 8px; border-radius: 10px;"
            )
            self._intervention_title.setText("Run needs operator recovery")
        else:
            self._intervention_badge.setText("INTERVENTION")
            self._intervention_badge.setStyleSheet(
                "background-color: #5d4037; color: #ffd54f; padding: 4px 8px; border-radius: 10px;"
            )
            self._intervention_title.setText("Waiting for operator action")
        allowed = set(event.allowed_actions)
        self._acknowledge_btn.setVisible("acknowledge" in allowed)
        self._retry_btn.setVisible("retry" in allowed)
        self._skip_btn.setVisible("skip" in allowed)
        self._abort_intervention_btn.setVisible("abort" in allowed)
        self._intervention_frame.setVisible(True)

    @pyqtSlot(object)
    def _on_timing_snapshot_ui(self, event: OrchestratorTimingSnapshot) -> None:
        if event.eta_seconds is not None and event.eta_seconds >= 0:
            self._history["eta"].append(float(event.eta_seconds))
        total_overhead = (
            float(event.paused_seconds)
            + float(event.retry_overhead_seconds)
            + float(event.intervention_overhead_seconds)
        )
        self._history["overhead"].append(total_overhead)
        self._subsystem_breakdown.set_values(event.subsystem_seconds)

    @pyqtSlot(object)
    def _on_run_state_ui(self, rs) -> None:
        """Update plots and health strip from RunState snapshot."""
        now = _time.monotonic()

        # Feed plots
        self._progress_plot.append(now, rs.progress_percent)

        if rs.throughput_fov_per_min is not None:
            self._throughput_plot.append(now, rs.throughput_fov_per_min)

        if rs.focus_error_um is not None:
            self._focus_plot.append(now, rs.focus_error_um)

        # Update subsystem breakdown
        if rs.subsystem_seconds:
            self._subsystem_breakdown.set_values(rs.subsystem_seconds)

        # -- Health strip updates --
        elapsed_str = _format_duration(rs.elapsed_s)
        eta_str = f"~{_format_duration(rs.eta_s)} remaining" if rs.eta_s else ""
        self._time_label.setText(f"{elapsed_str} elapsed  {eta_str}")

        # Position
        self._round_label.setText(
            f"Round: {rs.round_index + 1} / {rs.total_rounds}  ({rs.round_name})"
        )
        self._step_label.setText(
            f"Step: {rs.step_index + 1} / {rs.total_steps}  ({rs.step_type}: {rs.step_label})"
        )
        self._fov_label.setText(
            f"FOV: {rs.fov_index + 1} / {rs.total_fovs}" if rs.total_fovs > 0 else "FOV: -"
        )

        if rs.attempt > 1:
            self._attempt_label.setText(f"Attempt {rs.attempt} (retry)")
            self._attempt_label.show()
        else:
            self._attempt_label.hide()

        # Health
        focus_colors = {"locked": "#66BB6A", "searching": "#FFA726", "lost": "#EF5350"}
        if rs.focus_status:
            color = focus_colors.get(rs.focus_status, "#888888")
            self._focus_dot.setStyleSheet(f"font-size: 14px; color: {color};")
            err = f" ({rs.focus_error_um:.2f} \u00b5m)" if rs.focus_error_um is not None else ""
            self._focus_label.setText(f"Focus: {rs.focus_status}{err}")
        else:
            self._focus_dot.setStyleSheet("font-size: 14px; color: #888888;")
            self._focus_label.setText("Focus: -")

        if rs.throughput_fov_per_min is not None:
            self._throughput_label.setText(f"Throughput: {rs.throughput_fov_per_min:.1f} FOVs/min")
        else:
            self._throughput_label.setText("Throughput: -")

    @pyqtSlot(str, str)
    def _on_error_occurred_ui(self, error_type: str, message: str) -> None:
        _log.error(f"Orchestrator error [{error_type}]: {message}")

    @pyqtSlot(object)
    def _on_validation_complete_ui(self, summary: ValidationSummary) -> None:
        dialog = ValidationResultDialog(summary, parent=self)
        dialog.exec_()
        if summary.valid:
            self._validated = True
            self._start_btn.setEnabled(True)

    # ========================================================================
    # Button Handlers
    # ========================================================================

    def _on_load_clicked(self) -> None:
        from squid.ui.widgets.orchestrator.protocol_loader_dialog import ProtocolLoaderDialog

        dialog = ProtocolLoaderDialog(parent=self)
        if dialog.exec_():
            self._protocol_path = dialog.get_protocol_path()
            self._base_path = dialog.get_output_path()
            self._experiment_id = dialog.get_experiment_id()
            self._fov_positions = dialog.get_fov_positions()

            if self._protocol_path and self._base_path:
                self._load_protocol(self._protocol_path)

    def _load_protocol(self, protocol_path: str) -> None:
        from pathlib import Path
        from squid.core.protocol import ProtocolLoader

        try:
            # Parse with ProtocolLoader so UI workflow matches execution semantics
            # (repeat expansion, resource resolution, step normalization).
            protocol_obj = ProtocolLoader().load(protocol_path)
            protocol_data = protocol_obj.model_dump(mode="json", exclude_none=True)

            self._protocol_data = protocol_data
            protocol_name = protocol_data.get("name", Path(protocol_path).stem)

            rounds = protocol_data.get("rounds", [])
            has_imaging = False
            default_fovs_required = False
            for round_def in rounds:
                for step in round_def.get("steps", []):
                    if step.get("step_type") != "imaging":
                        continue
                    has_imaging = True
                    if step.get("fovs", "current") in ("current", "default"):
                        default_fovs_required = True

            fov_count = sum(len(coords) for coords in self._fov_positions.values())
            if has_imaging and default_fovs_required:
                if fov_count > 0:
                    fov_status = f"({fov_count} FOVs loaded)"
                else:
                    fov_status = "(FOVs required)"
            elif has_imaging:
                fov_status = "(no FOVs required)"
            else:
                fov_status = "(no imaging)"

            self._experiment_label.setText(
                f"Protocol: {protocol_name}\n"
                f"Experiment: {self._experiment_id or 'Unnamed'} {fov_status}"
            )

            self.fov_positions_changed.emit(self._fov_positions)
            self.protocol_loaded.emit(protocol_data)

            # Protocol must be validated before Start is enabled
            self._validated = False
            self._start_round_index = 0
            self._start_step_index = 0
            self._start_fov_index = 0
            self._run_single_round = False
            self._start_btn.setEnabled(False)
            self._validate_btn.setEnabled(True)
            _log.info(f"Protocol loaded: {protocol_path}")

            # Add focus threshold reference lines if protocol uses focus lock
            for config in protocol_obj.imaging_protocols.values():
                fg = getattr(config, "focus_gate", None)
                if fg is not None:
                    fl = getattr(fg, "focus_lock", None)
                    if fl is not None:
                        acquire = getattr(fl, "acquire_threshold_um", None)
                        maintain = getattr(fl, "maintain_threshold_um", None)
                        if acquire is not None:
                            self._focus_plot.add_horizontal_line(
                                acquire, "#66BB6A", "acquire"
                            )
                        if maintain is not None:
                            self._focus_plot.add_horizontal_line(
                                maintain, "#FFA726", "maintain"
                            )
                        break

        except Exception as e:
            _log.error(f"Failed to load protocol: {e}")
            self._experiment_label.setText(f"Error: {e}")
            self._protocol_data = None
            self._validated = False
            self._start_round_index = 0
            self._start_step_index = 0
            self._start_fov_index = 0
            self._run_single_round = False
            self._start_btn.setEnabled(False)
            self._validate_btn.setEnabled(False)

    def _on_start_clicked(self) -> None:
        if self._orchestrator is None:
            _log.warning("No orchestrator configured")
            return
        if self._protocol_path is None or self._base_path is None:
            _log.warning("No protocol loaded")
            return
        if not self._validated:
            _log.warning("Protocol must be validated before starting")
            return

        fov_count = sum(len(coords) for coords in self._fov_positions.values())
        if fov_count == 0:
            QMessageBox.warning(
                self,
                "No FOVs Loaded",
                "Start Acquisition requires FOV positions.\n"
                "Load FOVs in the protocol loader, or use 'Run Current' to acquire at the current stage position.",
            )
            return

        if self._fov_positions:
            region_fov_coords: Dict[str, Tuple[Tuple[float, ...], ...]] = {}
            for region_id, coords in self._fov_positions.items():
                region_fov_coords[region_id] = tuple(tuple(c) for c in coords)

            self._publish(
                LoadScanCoordinatesCommand(
                    region_fov_coordinates=region_fov_coords,
                )
            )
            _log.info(f"Loaded {sum(len(c) for c in self._fov_positions.values())} FOV positions")

        success = self._orchestrator.start_experiment(
            protocol_path=self._protocol_path,
            base_path=self._base_path,
            experiment_id=self._experiment_id or None,
            start_from_round=self._start_round_index,
            start_from_step=self._start_step_index,
            start_from_fov=self._start_fov_index,
            run_single_round=self._run_single_round,
        )
        if success:
            # Reset start position and single-round flag only after successful launch.
            self._run_single_round = False
            self._start_round_index = 0
            self._start_step_index = 0
            self._start_fov_index = 0
        else:
            _log.error("Failed to start experiment")

    def _on_validate_clicked(self) -> None:
        if self._protocol_path is None or self._base_path is None:
            _log.warning("No protocol loaded")
            return
        fov_count = sum(len(coords) for coords in self._fov_positions.values())
        self._publish(
            ValidateProtocolCommand(
                protocol_path=self._protocol_path,
                base_path=self._base_path,
                fov_count=fov_count,
            )
        )

    def _on_pause_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.pause()

    def _on_resume_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.resume()

    def _on_abort_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.abort()

    def _on_acknowledge_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.acknowledge_intervention()
        else:
            self._publish(ResolveInterventionCommand(action="acknowledge"))

    def _on_retry_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.resolve_intervention("retry")
        else:
            self._publish(ResolveInterventionCommand(action="retry"))

    def _on_skip_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.resolve_intervention("skip")
        else:
            self._publish(ResolveInterventionCommand(action="skip"))

    def _on_intervention_abort_clicked(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.resolve_intervention("abort")
        else:
            self._publish(ResolveInterventionCommand(action="abort"))

    # ========================================================================
    # Public: start position (set by tree navigation)
    # ========================================================================

    def set_start_position(self, round_index: int, step_index: int, fov_index: int = 0) -> None:
        """Set start position for next experiment run."""
        self._start_round_index = round_index
        self._start_step_index = step_index
        self._start_fov_index = max(0, fov_index)
        self._run_single_round = False

    def start_from_round(self, round_index: int, step_index: int = 0, fov_index: int = 0) -> None:
        """Set start position and immediately start."""
        self.set_start_position(round_index, step_index, fov_index)
        self._on_start_clicked()

    def run_single_round(self, round_index: int, step_index: int = 0, fov_index: int = 0) -> None:
        """Set to run a single round and immediately start."""
        self._start_round_index = round_index
        self._start_step_index = step_index
        self._start_fov_index = max(0, fov_index)
        self._run_single_round = True
        self._on_start_clicked()

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _update_button_states(self, state: OrchestratorState) -> None:
        is_idle = state == OrchestratorState.IDLE
        is_running = state in (
            OrchestratorState.RUNNING,
            OrchestratorState.WAITING_INTERVENTION,
        )
        is_paused = state == OrchestratorState.PAUSED
        is_terminal = state in (
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
            OrchestratorState.ABORTED,
        )

        self._load_btn.setEnabled(bool(is_idle or is_terminal))
        can_start = (is_idle or is_terminal) and self._protocol_data is not None and self._validated
        self._start_btn.setEnabled(bool(can_start))
        self._validate_btn.setEnabled(bool((is_idle or is_terminal) and self._protocol_data is not None))
        self._pause_btn.setEnabled(bool(is_running))
        self._resume_btn.setEnabled(bool(is_paused))
        self._abort_btn.setEnabled(bool(is_running or is_paused))

    @staticmethod
    def _format_time_remaining(seconds: float) -> str:
        """Format seconds into a human-readable time remaining string."""
        return _format_duration(seconds)

    def _update_status_style(self, state: str) -> None:
        colors = {
            "IDLE": "#888",
            "RUNNING": "#66BB6A",
            "WAITING_INTERVENTION": "#FFA726",
            "PAUSED": "#FFA726",
            "COMPLETED": "#66BB6A",
            "FAILED": "#EF5350",
            "ABORTED": "#EF5350",
        }
        color = colors.get(state, "#888")
        self._status_label.setStyleSheet(f"color: {color};")

    def shutdown(self) -> None:
        self._cleanup_subscriptions()


class OrchestratorWorkflowTree(EventBusWidget):
    """Hierarchical workflow display for experiment orchestration.

    Shows rounds, operations, and FOVs in a collapsible tree structure.
    Supports status tracking, current-step indicator, context menus,
    and double-click navigation for step selection.
    """

    # Signal emitted when user requests to jump to a FOV
    jump_to_fov = pyqtSignal(str, int, int)  # fov_id, round_index, time_point

    # Signal emitted when user requests to skip a FOV
    skip_fov = pyqtSignal(str, int, int)  # fov_id, round_index, time_point

    # Signal emitted when user requests to requeue a FOV
    requeue_fov = pyqtSignal(str, int, int, bool)  # fov_id, round_index, time_point, before_current

    # Signal for thread-safe UI updates
    fov_started = pyqtSignal(str, int, int, int, float, float)  # fov_id, fov_index, round_index, time_point, x_mm, y_mm
    fov_completed = pyqtSignal(str, int, int, str, str)  # fov_id, round_index, time_point, status_name, error_message

    # Signals for round/step status (thread-safe bridge from EventBus)
    _round_started_sig = pyqtSignal(int, str)  # round_index, round_name
    _round_completed_sig = pyqtSignal(int, str, bool, str)  # round_index, round_name, success, error
    _step_started_sig = pyqtSignal(int, int, str, float, object)  # round_index, step_index, step_type, estimated_seconds, imaging_protocol
    _step_completed_sig = pyqtSignal(int, int, str, bool, str, float)  # round_index, step_index, step_type, success, error, duration_seconds
    _attempt_update_sig = pyqtSignal(int, int, int, str, str)  # round_index, step_index, attempt, phase, message
    _state_changed_sig = pyqtSignal(str, str)  # old_state, new_state
    _validation_complete_sig = pyqtSignal(object)  # ValidationSummary

    # Signal to control panel: user selected a start position
    start_position_changed = pyqtSignal(int, int, int)  # round_index, step_index, fov_index

    # Signal: user wants to start from a specific position (context menu / double-click)
    start_from_requested = pyqtSignal(int, int, int)  # round_index, step_index, fov_index

    # Signal: user wants to run only a single round (context menu)
    run_single_round_requested = pyqtSignal(int, int, int)  # round_index, step_index, fov_index

    def __init__(
        self,
        event_bus: "UIEventBus",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(event_bus, parent)
        self._tree_items: Dict[tuple, QTreeWidgetItem] = {}
        self._fov_items: Dict[Tuple[int, str], List[QTreeWidgetItem]] = {}  # (round_index, fov_id) -> items
        self._current_highlight_item: Optional[QTreeWidgetItem] = None
        self._current_round_index: int = 0
        self._current_time_point: int = 0
        self._fov_positions: Dict[str, List[Tuple[float, float, float]]] = {}

        # Current-step tracking
        self._current_step_key: Optional[tuple] = None
        self._is_running: bool = False
        self._round_start_times: Dict[int, float] = {}
        self._param_panel: Optional["ParameterInspectionPanel"] = None

        self._setup_ui()
        self._connect_signals()

    def set_param_panel(self, panel: "ParameterInspectionPanel") -> None:
        self._param_panel = panel

    def set_fov_positions(self, positions: Dict[str, List[Tuple[float, float, float]]]) -> None:
        self._fov_positions = positions

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Operation", "Status", "Est. Time", "Details"])
        self._tree.setAlternatingRowColors(False)
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)

        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        placeholder = QTreeWidgetItem(["Load a protocol to see workflow"])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self._tree.addTopLevelItem(placeholder)

        layout.addWidget(self._tree)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        expand_btn = QPushButton("Expand All")
        expand_btn.setStyleSheet(BTN_STYLES["secondary"])
        expand_btn.clicked.connect(self._tree.expandAll)
        btn_layout.addWidget(expand_btn)

        collapse_btn = QPushButton("Collapse All")
        collapse_btn.setStyleSheet(BTN_STYLES["secondary"])
        collapse_btn.clicked.connect(self._tree.collapseAll)
        btn_layout.addWidget(collapse_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        self.fov_started.connect(self._handle_fov_started_ui)
        self.fov_completed.connect(self._handle_fov_completed_ui)
        self._round_started_sig.connect(self._handle_round_started_ui)
        self._round_completed_sig.connect(self._handle_round_completed_ui)
        self._step_started_sig.connect(self._handle_step_started_ui)
        self._step_completed_sig.connect(self._handle_step_completed_ui)
        self._attempt_update_sig.connect(self._handle_attempt_update_ui)
        self._state_changed_sig.connect(self._handle_state_changed_ui)
        self._validation_complete_sig.connect(self._handle_validation_complete_ui)

    @property
    def tree(self) -> QTreeWidget:
        return self._tree

    # ========================================================================
    # Populate Tree
    # ========================================================================

    @pyqtSlot(dict)
    def populate_from_protocol(self, protocol: Dict[str, Any]) -> None:
        self._tree.clear()
        self._tree_items.clear()
        self._fov_items.clear()
        self._current_highlight_item = None
        self._current_step_key = None

        rounds = protocol.get("rounds", [])
        if not rounds:
            item = QTreeWidgetItem(["No rounds defined"])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(item)
            return

        for round_idx, round_data in enumerate(rounds):
            round_name = round_data.get("name", f"Round {round_idx + 1}")
            if round_name.lower().startswith("round"):
                display_name = round_name
            else:
                display_name = f"Round {round_idx + 1}: {round_name}"
            round_item = QTreeWidgetItem([
                display_name,
                "pending",
                "",
                ""
            ])
            round_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
            round_item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "round",
                "round_index": round_idx,
                "round_data": round_data,
            })
            self._tree.addTopLevelItem(round_item)
            self._tree_items[(round_idx,)] = round_item

            operations = round_data.get("operations", [])
            if not operations:
                operations = self._build_operations_from_schema(round_data)

            for op_idx, operation in enumerate(operations):
                op_type = operation.get("type", "unknown")
                op_name = operation.get("name", op_type)

                op_item = QTreeWidgetItem([
                    f"{op_type.title()}: {op_name}",
                    "pending",
                    "",
                    self._get_operation_details(operation)
                ])
                op_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
                op_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "operation",
                    "round_data": round_data,
                    "operation_data": operation,
                    "op_index": op_idx,
                })
                round_item.addChild(op_item)
                self._tree_items[(round_idx, op_idx)] = op_item

                if op_type == "imaging":
                    self._add_imaging_fovs(op_item, round_idx, op_idx, operation)

        self._tree.expandToDepth(0)

        # Set initial current-step indicator on first round
        if (0,) in self._tree_items:
            self._update_current_step_indicator((0,))

    def _build_operations_from_schema(self, round_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        operations: List[Dict[str, Any]] = []

        steps = round_data.get("steps", [])
        if steps:
            for i, step in enumerate(steps):
                step_type = step.get("step_type", "unknown")
                op: Dict[str, Any] = {
                    "type": step_type,
                    "name": step.get("name", f"{step_type.title()} {i + 1}"),
                }
                if isinstance(step, dict):
                    op.update(step)
                operations.append(op)
            return operations

        fluidics = round_data.get("fluidics", [])
        for i, step in enumerate(fluidics):
            op = {
                "type": "fluidics",
                "name": f"Fluidics {i + 1}",
            }
            if isinstance(step, dict):
                op.update(step)
            operations.append(op)

        imaging = round_data.get("imaging")
        if imaging is not None:
            op = {
                "type": "imaging",
                "name": "Imaging",
            }
            if isinstance(imaging, dict):
                op.update(imaging)
            operations.append(op)

        round_type = round_data.get("type", "")
        if round_type == "intervention" or round_data.get("requires_intervention"):
            if not any(op.get("type") == "intervention" for op in operations):
                operations.insert(0, {
                    "type": "intervention",
                    "name": "Intervention",
                    "message": round_data.get("intervention_message", ""),
                })

        return operations

    def _add_imaging_fovs(
        self,
        parent_item: QTreeWidgetItem,
        round_idx: int,
        op_idx: int,
        operation: Dict[str, Any]
    ) -> None:
        if self._fov_positions:
            fov_idx = 0
            for region_id, coords in self._fov_positions.items():
                for coord_idx, (x_mm, y_mm, z_mm) in enumerate(coords):
                    fov_id = f"{region_id}_{coord_idx:04d}"
                    pos_str = f"({x_mm:.3f}, {y_mm:.3f})"
                    fov_item = QTreeWidgetItem([
                        f"{region_id} - FOV {coord_idx + 1}",
                        "pending",
                        "",
                        pos_str
                    ])
                    fov_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
                    fov_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "fov",
                        "fov_id": fov_id,
                        "region_id": region_id,
                        "round_index": round_idx,
                        "step_index": op_idx,
                        "fov_index": fov_idx,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "z_mm": z_mm,
                        "status": "PENDING",
                    })
                    parent_item.addChild(fov_item)
                    self._tree_items[(round_idx, op_idx, fov_idx)] = fov_item
                    self._fov_items.setdefault((round_idx, fov_id), []).append(fov_item)
                    fov_idx += 1
            return

        fov_config = operation.get("fov_config", {})
        num_fovs = fov_config.get("num_fovs", 0)

        if num_fovs == 0:
            fov_item = QTreeWidgetItem(["No FOV positions loaded", "", "", ""])
            fov_item.setFlags(Qt.ItemFlag.NoItemFlags)
            fov_item.setForeground(0, QBrush(QColor("#EF5350")))
            parent_item.addChild(fov_item)
            return

        for fov_idx in range(num_fovs):
            fov_id = f"FOV_{fov_idx + 1}"
            fov_item = QTreeWidgetItem([f"FOV {fov_idx + 1}", "pending", "", ""])
            fov_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
            fov_item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "fov",
                "fov_id": fov_id,
                "region_id": "",
                "round_index": round_idx,
                "step_index": op_idx,
                "fov_index": fov_idx,
                "x_mm": 0.0,
                "y_mm": 0.0,
                "z_mm": 0.0,
                "status": "PENDING",
            })
            parent_item.addChild(fov_item)
            self._tree_items[(round_idx, op_idx, fov_idx)] = fov_item
            self._fov_items.setdefault((round_idx, fov_id), []).append(fov_item)

    def add_fov_item(
        self,
        fov_id: str,
        region_id: str,
        fov_index: int,
        parent_key: tuple,
        x_mm: float = 0.0,
        y_mm: float = 0.0,
        z_mm: float = 0.0,
    ) -> None:
        parent_item = self._tree_items.get(parent_key)
        if parent_item is None:
            return

        if parent_item.childCount() == 1:
            first_child = parent_item.child(0)
            if first_child and "runtime" in first_child.text(0).lower():
                parent_item.removeChild(first_child)

        pos_str = f"({x_mm:.3f}, {y_mm:.3f})"
        fov_item = QTreeWidgetItem([
            f"{region_id} - FOV {fov_index + 1}",
            "pending",
            "",
            pos_str
        ])
        fov_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
        fov_item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": "fov",
            "fov_id": fov_id,
            "region_id": region_id,
            "round_index": parent_key[0] if len(parent_key) >= 1 else 0,
            "step_index": parent_key[1] if len(parent_key) >= 2 else 0,
            "fov_index": fov_index,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "z_mm": z_mm,
            "status": "PENDING",
        })
        parent_item.addChild(fov_item)
        round_idx = parent_key[0] if len(parent_key) >= 1 else 0
        self._fov_items.setdefault((round_idx, fov_id), []).append(fov_item)

    def populate_fovs_from_coordinates(
        self,
        region_fov_coordinates: Dict[str, List[tuple]],
        parent_key: tuple = (0, 0),
    ) -> None:
        for region_id, coords in region_fov_coordinates.items():
            for fov_index, coord in enumerate(coords):
                x_mm = coord[0] if len(coord) > 0 else 0.0
                y_mm = coord[1] if len(coord) > 1 else 0.0
                z_mm = coord[2] if len(coord) > 2 else 0.0
                fov_id = f"{region_id}_{fov_index:04d}"

                self.add_fov_item(
                    fov_id=fov_id,
                    region_id=region_id,
                    fov_index=fov_index,
                    parent_key=parent_key,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=z_mm,
                )

    def _get_operation_details(self, operation: Dict[str, Any]) -> str:
        op_type = operation.get("type", "")
        if op_type == "imaging":
            config = operation.get("protocol") or operation.get("config")
            if config:
                fovs = operation.get("fovs", "current")
                return f"Protocol: {config}" + (f", FOVs: {fovs}" if fovs not in ("current", "default") else "")
            channels = operation.get("channels", [])
            return f"Channels: {', '.join(channels)}" if channels else ""
        elif op_type == "fluidics":
            protocol = operation.get("protocol")
            if protocol:
                return f"Protocol: {protocol}"
            action = operation.get("action", "") or operation.get("command", "")
            reagent = operation.get("reagent", "") or operation.get("solution", "")
            return f"{action}: {reagent}" if reagent else str(action)
        elif op_type == "wait":
            duration = operation.get("duration_seconds", 0) or operation.get("duration_s", 0)
            return f"Duration: {duration}s"
        elif op_type == "intervention":
            msg = operation.get("message", "")
            return msg[:50] if msg else ""
        return ""

    def update_item_status(
        self,
        key: tuple,
        status: str,
        details: Optional[str] = None
    ) -> None:
        item = self._tree_items.get(key)
        if item is None:
            return
        item.setText(1, status)
        color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
        item.setForeground(1, QBrush(color))
        if details is not None:
            item.setText(3, details)
        if status == "running":
            self._tree.scrollToItem(item)

    def set_time_estimate(self, key: tuple, time_str: str) -> None:
        item = self._tree_items.get(key)
        if item is not None:
            item.setText(2, time_str)

    # ========================================================================
    # Current-Step Indicator
    # ========================================================================

    def _update_current_step_indicator(self, key: Optional[tuple]) -> None:
        """Update the current-step visual indicator.

        Clears the old indicator and applies bold font + subtle background
        highlight to the new current item.
        """
        # Clear previous indicator
        if self._current_step_key is not None:
            prev_item = self._tree_items.get(self._current_step_key)
            if prev_item is not None:
                font = prev_item.font(0)
                font.setBold(False)
                prev_item.setFont(0, font)
                for col in range(4):
                    prev_item.setBackground(col, QBrush(QColor(0, 0, 0, 0)))

        self._current_step_key = key

        if key is not None:
            item = self._tree_items.get(key)
            if item is not None:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                for col in range(4):
                    item.setBackground(col, QBrush(_CURRENT_STEP_BG))
                self._tree.scrollToItem(item)

        # Emit signal for control panel (only when not running, to avoid
        # overwriting the user's start position during execution)
        if key is not None and not self._is_running:
            round_idx = key[0] if len(key) >= 1 else 0
            step_idx = key[1] if len(key) >= 2 else 0
            fov_idx = key[2] if len(key) >= 3 else 0
            self.start_position_changed.emit(round_idx, step_idx, fov_idx)

    def _reset_all_items_to_pending(self) -> None:
        """Reset all tree items to 'pending' status."""
        for key, item in self._tree_items.items():
            if len(key) <= 2:  # rounds and operations
                item.setText(1, "pending")
                item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
            elif len(key) == 3:  # FOVs
                item.setText(1, "pending")
                item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
                item.setBackground(0, QBrush(QColor(0, 0, 0, 0)))
                item_data = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(item_data, dict):
                    item_data["status"] = "PENDING"
                    item.setData(0, Qt.ItemDataRole.UserRole, item_data)
        self._current_highlight_item = None

    # ========================================================================
    # Round/Step Event Handlers (EventBus → signal → UI thread)
    # ========================================================================

    @handles(OrchestratorRoundStarted)
    def _on_round_started(self, event: OrchestratorRoundStarted) -> None:
        self._round_started_sig.emit(event.round_index, event.round_name)
        self._round_start_times[event.round_index] = _time.monotonic()

    @handles(OrchestratorRoundCompleted)
    def _on_round_completed(self, event: OrchestratorRoundCompleted) -> None:
        self._round_completed_sig.emit(
            event.round_index, event.round_name, event.success, event.error or ""
        )

    @handles(OrchestratorStepStarted)
    def _on_step_started(self, event: OrchestratorStepStarted) -> None:
        self._step_started_sig.emit(
            event.round_index, event.step_index, event.step_type,
            event.estimated_seconds, event.imaging_protocol,
        )

    @handles(OrchestratorStepCompleted)
    def _on_step_completed(self, event: OrchestratorStepCompleted) -> None:
        self._step_completed_sig.emit(
            event.round_index, event.step_index, event.step_type,
            event.success, event.error or "", event.duration_seconds,
        )

    @handles(OrchestratorAttemptUpdate)
    def _on_attempt_update(self, event: OrchestratorAttemptUpdate) -> None:
        self._attempt_update_sig.emit(
            event.round_index,
            event.step_index,
            event.attempt,
            event.phase,
            event.message,
        )

    @handles(OrchestratorStateChanged)
    def _on_state_changed(self, event: OrchestratorStateChanged) -> None:
        self._state_changed_sig.emit(event.old_state, event.new_state)

    @handles(ProtocolValidationComplete)
    def _on_validation_complete(self, event: ProtocolValidationComplete) -> None:
        summary = ValidationSummary(
            protocol_name=event.protocol_name,
            total_rounds=event.total_rounds,
            total_estimated_seconds=event.estimated_seconds,
            total_disk_bytes=event.estimated_disk_bytes,
            operation_estimates=event.operation_estimates,
            errors=tuple(event.errors),
            warnings=tuple(event.warnings),
            valid=event.valid,
        )
        self._validation_complete_sig.emit(summary)

    # ========================================================================
    # UI Slots for Round/Step
    # ========================================================================

    @pyqtSlot(int, str)
    def _handle_round_started_ui(self, round_index: int, round_name: str) -> None:
        _ = round_name
        key = (round_index,)
        self.update_item_status(key, "running")
        self._update_current_step_indicator(key)

    @pyqtSlot(int, str, bool, str)
    def _handle_round_completed_ui(
        self, round_index: int, round_name: str, success: bool, error: str
    ) -> None:
        _ = round_name
        key = (round_index,)
        if error == "skipped":
            self.update_item_status(key, "skipped")
        elif success:
            self.update_item_status(key, "completed")
        else:
            self.update_item_status(key, "failed", details=error[:50] if error else None)
        # Show actual round duration
        start = self._round_start_times.pop(round_index, None)
        if start is not None:
            duration = _time.monotonic() - start
            self.set_time_estimate(key, _format_duration(duration))

    @pyqtSlot(int, int, str, float, object)
    def _handle_step_started_ui(
        self, round_index: int, step_index: int, step_type: str,
        estimated_seconds: float, imaging_protocol: object,
    ) -> None:
        _ = step_type
        key = (round_index, step_index)
        self.update_item_status(key, "running")
        self._update_current_step_indicator(key)
        # Show estimated time while running
        if estimated_seconds > 0:
            self.set_time_estimate(key, f"~{_format_duration(estimated_seconds)}")
        # Show imaging protocol in parameter panel if available
        if imaging_protocol is not None and self._param_panel is not None:
            proto_name = str(key)
            item = self._tree_items.get(key)
            if item is not None:
                proto_name = item.text(0)
            self._param_panel.show_imaging_protocol(proto_name, imaging_protocol)

    @pyqtSlot(int, int, str, bool, str, float)
    def _handle_step_completed_ui(
        self, round_index: int, step_index: int, step_type: str,
        success: bool, error: str, duration_seconds: float,
    ) -> None:
        _ = step_type
        key = (round_index, step_index)
        if success:
            self.update_item_status(key, "completed")
        else:
            self.update_item_status(key, "failed", details=error[:50] if error else None)
        # Show actual duration after completion
        if duration_seconds > 0:
            self.set_time_estimate(key, _format_duration(duration_seconds))

    @pyqtSlot(int, int, int, str, str)
    def _handle_attempt_update_ui(
        self,
        round_index: int,
        step_index: int,
        attempt: int,
        phase: str,
        message: str,
    ) -> None:
        key = (round_index, step_index)
        item = self._tree_items.get(key)
        if item is None:
            return
        if phase in ("retry_scheduled", "failed") and attempt > 1:
            self.update_item_status(key, "retrying", details=f"Attempt {attempt}: {message[:50]}".strip())
        elif phase == "started" and attempt > 1:
            self.update_item_status(key, "retrying", details=f"Retry attempt {attempt}")

    @pyqtSlot(str, str)
    def _handle_state_changed_ui(self, old_state: str, new_state: str) -> None:
        _ = old_state
        self._is_running = new_state in ("RUNNING", "WAITING_INTERVENTION", "PAUSED")

        if new_state == "ABORTED":
            # Reset all items to pending after abort
            self._reset_all_items_to_pending()
            if (0,) in self._tree_items:
                self._update_current_step_indicator((0,))

        elif new_state in ("COMPLETED", "FAILED"):
            # Keep status as-is but move indicator to first round for re-run
            if (0,) in self._tree_items:
                self._update_current_step_indicator((0,))

        elif new_state == "RUNNING" and old_state in ("IDLE", "COMPLETED", "FAILED", "ABORTED"):
            # Starting a new run — reset all to pending
            self._reset_all_items_to_pending()

    @pyqtSlot(object)
    def _handle_validation_complete_ui(self, summary: ValidationSummary) -> None:
        """Populate Est. Time column from validation estimates."""
        # Per-step estimates
        round_totals: Dict[int, float] = {}
        for op in summary.operation_estimates:
            if op.step_index >= 0:
                key = (op.round_index, op.step_index)
                self.set_time_estimate(key, _format_duration(op.estimated_seconds))
                round_totals[op.round_index] = round_totals.get(op.round_index, 0.0) + op.estimated_seconds
        # Per-round totals
        for round_idx, total_secs in round_totals.items():
            key = (round_idx,)
            self.set_time_estimate(key, _format_duration(total_secs))

    # ========================================================================
    # FOV Event Handlers
    # ========================================================================

    @handles(FovTaskStarted)
    def _on_fov_started(self, event: FovTaskStarted) -> None:
        self._current_round_index = event.round_index
        self._current_time_point = event.time_point
        self.fov_started.emit(
            event.fov_id,
            event.fov_index,
            event.round_index,
            event.time_point,
            event.x_mm,
            event.y_mm,
        )

    @handles(FovTaskCompleted)
    def _on_fov_completed(self, event: FovTaskCompleted) -> None:
        status_name = event.status.name if hasattr(event.status, 'name') else str(event.status)
        error_msg = event.error_message or ""
        self.fov_completed.emit(
            event.fov_id,
            event.round_index,
            event.time_point,
            status_name,
            error_msg,
        )

    def _resolve_fov_item_for_event(self, round_index: int, fov_id: str) -> Optional[QTreeWidgetItem]:
        """Resolve the target FOV item for a runtime event.

        FOV identifiers are reused across rounds, so lookups must be scoped by
        round index to avoid updating the wrong tree node.
        """
        items = self._fov_items.get((round_index, fov_id), [])
        if not items:
            return None
        if len(items) == 1:
            return items[0]

        active_step_idx: Optional[int] = None
        if (
            self._current_step_key is not None
            and len(self._current_step_key) >= 2
            and self._current_step_key[0] == round_index
        ):
            active_step_idx = self._current_step_key[1]

        if active_step_idx is not None:
            for item in items:
                item_data = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(item_data, dict) and item_data.get("step_index") == active_step_idx:
                    return item

        for item in items:
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and item_data.get("status") in ("PENDING", "DEFERRED", "EXECUTING"):
                return item

        return items[0]

    @pyqtSlot(str, int, int, int, float, float)
    def _handle_fov_started_ui(
        self,
        fov_id: str,
        fov_index: int,
        round_index: int,
        time_point: int,
        x_mm: float,
        y_mm: float,
    ) -> None:
        _ = fov_index
        _ = time_point

        if self._current_highlight_item is not None:
            prev_item = self._current_highlight_item
            prev_item.setBackground(0, QBrush(QColor(0, 0, 0, 0)))

        item = self._resolve_fov_item_for_event(round_index, fov_id)
        if item is None:
            return

        item.setText(1, "running")
        item.setForeground(1, QBrush(STATUS_COLORS["running"]))
        item.setBackground(0, QBrush(_RUNNING_BG))
        self._tree.scrollToItem(item)
        self._current_highlight_item = item

        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict):
            item_data["x_mm"] = x_mm
            item_data["y_mm"] = y_mm
            item_data["status"] = "EXECUTING"
            item.setData(0, Qt.ItemDataRole.UserRole, item_data)

    @pyqtSlot(str, int, int, str, str)
    def _handle_fov_completed_ui(
        self,
        fov_id: str,
        round_index: int,
        time_point: int,
        status_name: str,
        error_msg: str,
    ) -> None:
        _ = time_point
        item = self._resolve_fov_item_for_event(round_index, fov_id)
        if item is None:
            return

        status_map = {
            "COMPLETED": "completed",
            "FAILED": "failed",
            "SKIPPED": "skipped",
            "DEFERRED": "pending",
        }
        display_status = status_map.get(status_name, "completed")

        item.setText(1, display_status)
        color = STATUS_COLORS.get(display_status, STATUS_COLORS["pending"])
        item.setForeground(1, QBrush(color))

        item.setBackground(0, QBrush(QColor(0, 0, 0, 0)))

        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict):
            item_data["status"] = status_name
            if error_msg:
                item_data["error_message"] = error_msg
            item.setData(0, Qt.ItemDataRole.UserRole, item_data)

        if error_msg:
            item.setText(3, error_msg[:50])

    # ========================================================================
    # Context Menu
    # ========================================================================

    def _show_context_menu(self, position) -> None:
        item = self._tree.itemAt(position)
        if item is None:
            return

        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not item_data or not isinstance(item_data, dict):
            return

        item_type = item_data.get("type")
        menu = QMenu(self)

        if item_type == "round":
            round_idx = item_data["round_index"]
            self._build_round_context_menu(menu, round_idx)
        elif item_type == "operation":
            round_data = item_data.get("round_data", {})
            round_idx = round_data.get("_round_index", 0)
            # Find round index from tree structure
            parent = item.parent()
            if parent is not None:
                parent_data = parent.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(parent_data, dict):
                    round_idx = parent_data.get("round_index", 0)
            op_idx = item_data.get("op_index", 0)
            self._build_operation_context_menu(menu, round_idx, op_idx)
        elif item_type == "fov":
            fov_id = item_data.get("fov_id")
            if fov_id:
                round_idx = item_data.get("round_index", 0)
                step_idx = item_data.get("step_index", 0)
                fov_idx = item_data.get("fov_index", 0)
                parent = item.parent()
                if parent is not None:
                    parent_data = parent.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(parent_data, dict):
                        step_idx = parent_data.get("op_index", step_idx)
                    grandparent = parent.parent()
                    if grandparent is not None:
                        grandparent_data = grandparent.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(grandparent_data, dict):
                            round_idx = grandparent_data.get("round_index", round_idx)
                self._build_fov_context_menu(menu, fov_id, round_idx, step_idx, fov_idx)
        else:
            return

        viewport = self._tree.viewport()
        if viewport is not None:
            menu.exec_(viewport.mapToGlobal(position))

    def _build_round_context_menu(self, menu: QMenu, round_idx: int) -> None:
        if not self._is_running:
            set_start = QAction("Set as start position", self)
            set_start.triggered.connect(lambda: self._set_start_position(round_idx, 0))
            menu.addAction(set_start)

            start_from = QAction("Start from this round", self)
            start_from.triggered.connect(lambda: self._start_from(round_idx, 0))
            menu.addAction(start_from)

            run_only = QAction("Run this round only", self)
            run_only.triggered.connect(lambda: self._run_only(round_idx, 0))
            menu.addAction(run_only)

    def _build_operation_context_menu(self, menu: QMenu, round_idx: int, op_idx: int) -> None:
        if not self._is_running:
            set_start = QAction("Set as start position", self)
            set_start.triggered.connect(lambda: self._set_start_position(round_idx, op_idx))
            menu.addAction(set_start)

            start_from = QAction("Start from this step", self)
            start_from.triggered.connect(lambda: self._start_from(round_idx, op_idx))
            menu.addAction(start_from)

    def _build_fov_context_menu(
        self,
        menu: QMenu,
        fov_id: str,
        round_idx: int,
        step_idx: int,
        fov_idx: int,
    ) -> None:
        if not self._is_running:
            set_start = QAction("Set as start position", self)
            set_start.triggered.connect(
                lambda: self._set_start_position(round_idx, step_idx, fov_idx)
            )
            menu.addAction(set_start)

            start_from = QAction("Start from this FOV", self)
            start_from.triggered.connect(
                lambda: self._start_from(round_idx, step_idx, fov_idx)
            )
            menu.addAction(start_from)
            return

        jump_action = QAction("Jump to this FOV", self)
        jump_action.triggered.connect(lambda: self._emit_jump(fov_id))
        menu.addAction(jump_action)

        skip_action = QAction("Skip this FOV", self)
        skip_action.triggered.connect(lambda: self._emit_skip(fov_id))
        menu.addAction(skip_action)

        menu.addSeparator()

        requeue_action = QAction("Requeue this FOV", self)
        requeue_action.triggered.connect(lambda: self._emit_requeue(fov_id, before_current=False))
        menu.addAction(requeue_action)

        requeue_before_action = QAction("Requeue before current", self)
        requeue_before_action.triggered.connect(lambda: self._emit_requeue(fov_id, before_current=True))
        menu.addAction(requeue_before_action)

    # ========================================================================
    # Navigation Actions
    # ========================================================================

    def _set_start_position(
        self,
        round_idx: int,
        step_idx: int,
        fov_idx: Optional[int] = None,
    ) -> None:
        """Set the start position indicator without starting."""
        if fov_idx is not None:
            key = (round_idx, step_idx, fov_idx)
            if key not in self._tree_items:
                key = (round_idx, step_idx)
        elif step_idx > 0:
            key = (round_idx, step_idx)
        else:
            key = (round_idx,)
        self._update_current_step_indicator(key)

    def _start_from(self, round_idx: int, step_idx: int, fov_idx: Optional[int] = None) -> None:
        """Set start position and signal to start from that position."""
        self._set_start_position(round_idx, step_idx, fov_idx)
        self.start_from_requested.emit(round_idx, step_idx, fov_idx or 0)

    def _run_only(self, round_idx: int, step_idx: int, fov_idx: Optional[int] = None) -> None:
        """Signal to run a single round from the tree."""
        self._set_start_position(round_idx, step_idx, fov_idx)
        self.run_single_round_requested.emit(round_idx, step_idx, fov_idx or 0)

    def _extract_start_indices(
        self,
        item: QTreeWidgetItem,
    ) -> Optional[Tuple[int, int, Optional[int]]]:
        """Return (round_idx, step_idx, fov_idx?) for tree item selection."""
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not item_data or not isinstance(item_data, dict):
            return None

        item_type = item_data.get("type")
        if item_type == "round":
            return item_data.get("round_index", 0), 0, None
        if item_type == "operation":
            round_idx = 0
            parent = item.parent()
            if parent is not None:
                parent_data = parent.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(parent_data, dict):
                    round_idx = parent_data.get("round_index", 0)
            return round_idx, item_data.get("op_index", 0), None
        if item_type == "fov":
            round_idx = item_data.get("round_index", 0)
            step_idx = item_data.get("step_index", 0)
            fov_idx = item_data.get("fov_index", 0)
            parent = item.parent()
            if parent is not None:
                parent_data = parent.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(parent_data, dict):
                    step_idx = parent_data.get("op_index", step_idx)
                grandparent = parent.parent()
                if grandparent is not None:
                    grandparent_data = grandparent.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(grandparent_data, dict):
                        round_idx = grandparent_data.get("round_index", round_idx)
            return round_idx, step_idx, fov_idx
        return None

    def _emit_jump(self, fov_id: str) -> None:
        self.jump_to_fov.emit(fov_id, self._current_round_index, self._current_time_point)
        self._publish(JumpToFovCommand(
            fov_id=fov_id,
            round_index=self._current_round_index,
            time_point=self._current_time_point,
        ))

    def _emit_skip(self, fov_id: str) -> None:
        self.skip_fov.emit(fov_id, self._current_round_index, self._current_time_point)
        self._publish(SkipFovCommand(
            fov_id=fov_id,
            round_index=self._current_round_index,
            time_point=self._current_time_point,
        ))

    def _emit_requeue(self, fov_id: str, before_current: bool) -> None:
        self.requeue_fov.emit(fov_id, self._current_round_index, self._current_time_point, before_current)
        self._publish(RequeueFovCommand(
            fov_id=fov_id,
            round_index=self._current_round_index,
            time_point=self._current_time_point,
            before_current=before_current,
        ))

    # ========================================================================
    # Double-Click Navigation
    # ========================================================================

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Single-click selects start position when not running."""
        _ = column
        if self._is_running:
            return

        indices = self._extract_start_indices(item)
        if indices is None:
            return

        round_idx, step_idx, fov_idx = indices
        self._set_start_position(round_idx, step_idx, fov_idx)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        _ = column
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not item_data or not isinstance(item_data, dict):
            return

        item_type = item_data.get("type")
        if item_type == "fov":
            fov_id = item_data.get("fov_id")
            if fov_id and self._is_running:
                self._emit_jump(fov_id)
            elif not self._is_running:
                indices = self._extract_start_indices(item)
                if indices is None:
                    return
                round_idx, step_idx, fov_idx = indices
                self._start_from(round_idx, step_idx, fov_idx)
        elif item_type in ("round", "operation") and not self._is_running:
            indices = self._extract_start_indices(item)
            if indices is None:
                return
            round_idx, step_idx, fov_idx = indices
            self._start_from(round_idx, step_idx, fov_idx)

    # ========================================================================
    # Cleanup
    # ========================================================================

    def cleanup(self) -> None:
        self._cleanup_subscriptions()


# Backwards compatibility alias
OrchestratorWidget = OrchestratorControlPanel
