"""
Orchestrator Widget components for multi-round experiment control.

Provides dockable widgets for the orchestrator:
- OrchestratorControlPanel: Status, buttons, progress, intervention
- OrchestratorWorkflowTree: Hierarchical workflow display
"""

import time as _time

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
)
from PyQt5.QtGui import QFont, QColor, QBrush

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
    OrchestratorInterventionRequired,
    OrchestratorError,
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
    progress_updated = pyqtSignal(int, int, float, str, object)  # current, total, percent, name, eta_seconds
    intervention_required = pyqtSignal(str)  # message
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

        self._setup_ui()
        self._connect_signals()
        self._update_button_states(OrchestratorState.IDLE)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        status_group = self._create_status_section()
        layout.addWidget(status_group)

        controls = self._create_controls_section()
        layout.addWidget(controls)

        progress_group = self._create_progress_section()
        layout.addWidget(progress_group)

        self._intervention_frame = self._create_intervention_section()
        self._intervention_frame.setVisible(False)
        layout.addWidget(self._intervention_frame)

        layout.addStretch()

    def _create_status_section(self) -> QGroupBox:
        group = QGroupBox("Experiment Status")
        layout = QVBoxLayout(group)

        self._status_label = QLabel("IDLE")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        self._status_label.setFont(font)
        self._status_label.setStyleSheet("color: #888;")
        layout.addWidget(self._status_label)

        self._experiment_label = QLabel("No protocol loaded")
        self._experiment_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._experiment_label.setWordWrap(True)
        layout.addWidget(self._experiment_label)

        return group

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

        self._start_btn = QPushButton("Start")
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

        return group

    def _create_intervention_section(self) -> QFrame:
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #3E2723; border: 2px solid #F9A825; border-radius: 4px; }"
        )

        layout = QVBoxLayout(frame)

        header = QLabel("INTERVENTION REQUIRED")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(12)
        header.setFont(font)
        header.setStyleSheet("color: #FDD835; border: none;")
        layout.addWidget(header)

        self._intervention_message = QLabel("")
        self._intervention_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._intervention_message.setWordWrap(True)
        self._intervention_message.setStyleSheet("color: #FFE082; border: none;")
        layout.addWidget(self._intervention_message)

        self._acknowledge_btn = QPushButton("ACKNOWLEDGE")
        self._acknowledge_btn.setMinimumHeight(36)
        self._acknowledge_btn.setStyleSheet(BTN_STYLES["acknowledge"])
        self._acknowledge_btn.clicked.connect(self._on_acknowledge_clicked)
        layout.addWidget(self._acknowledge_btn)

        return frame

    def _connect_signals(self) -> None:
        self.state_changed.connect(self._on_state_changed_ui)
        self.progress_updated.connect(self._on_progress_updated_ui)
        self.intervention_required.connect(self._on_intervention_required_ui)
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
        self.progress_updated.emit(
            event.current_round,
            event.total_rounds,
            event.progress_percent,
            event.current_round_name,
            event.eta_seconds,
        )

    @handles(OrchestratorInterventionRequired)
    def _on_intervention(self, event: OrchestratorInterventionRequired) -> None:
        self.intervention_required.emit(event.message)

    @handles(OrchestratorError)
    def _on_error(self, event: OrchestratorError) -> None:
        self.error_occurred.emit(event.error_type, event.message)

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
        elif new_state in ("FAILED", "ABORTED", "IDLE"):
            self._time_remaining_label.setText("--")
            self._time_remaining_label.setStyleSheet("color: #aaa;")

    @pyqtSlot(int, int, float, str, object)
    def _on_progress_updated_ui(
        self, current: int, total: int, percent: float, name: str, eta_seconds: object
    ) -> None:
        self._round_label.setText(f"{current} / {total}")
        self._round_name_label.setText(name)
        self._progress_bar.setValue(int(percent))

        # Update time remaining display
        if eta_seconds is not None and isinstance(eta_seconds, (int, float)) and eta_seconds > 0:
            self._time_remaining_label.setText(self._format_time_remaining(float(eta_seconds)))
            self._time_remaining_label.setStyleSheet("color: #ddd;")
        elif percent >= 100.0:
            self._time_remaining_label.setText("Complete")
            self._time_remaining_label.setStyleSheet("color: #66BB6A;")
        else:
            self._time_remaining_label.setText("--")
            self._time_remaining_label.setStyleSheet("color: #aaa;")

    @pyqtSlot(str)
    def _on_intervention_required_ui(self, message: str) -> None:
        self._intervention_message.setText(message)
        self._intervention_frame.setVisible(True)

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
    _step_started_sig = pyqtSignal(int, int, str, float)  # round_index, step_index, step_type, estimated_seconds
    _step_completed_sig = pyqtSignal(int, int, str, bool, str, float)  # round_index, step_index, step_type, success, error, duration_seconds
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

        self._setup_ui()
        self._connect_signals()

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
            event.estimated_seconds,
        )

    @handles(OrchestratorStepCompleted)
    def _on_step_completed(self, event: OrchestratorStepCompleted) -> None:
        self._step_completed_sig.emit(
            event.round_index, event.step_index, event.step_type,
            event.success, event.error or "", event.duration_seconds,
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

    @pyqtSlot(int, int, str, float)
    def _handle_step_started_ui(
        self, round_index: int, step_index: int, step_type: str, estimated_seconds: float
    ) -> None:
        _ = step_type
        key = (round_index, step_index)
        self.update_item_status(key, "running")
        self._update_current_step_indicator(key)
        # Show estimated time while running
        if estimated_seconds > 0:
            self.set_time_estimate(key, f"~{_format_duration(estimated_seconds)}")

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
