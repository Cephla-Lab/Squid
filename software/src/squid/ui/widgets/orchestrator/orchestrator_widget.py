"""
Orchestrator Widget components for multi-round experiment control.

Provides dockable widgets for the orchestrator:
- OrchestratorControlPanel: Status, buttons, progress, intervention
- OrchestratorWorkflowTree: Hierarchical workflow display
"""

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
    OrchestratorInterventionRequired,
    OrchestratorError,
    ValidateProtocolCommand,
    ProtocolValidationStarted,
    ProtocolValidationComplete,
)
from squid.backend.controllers.multipoint import (
    FovTaskStarted,
    FovTaskCompleted,
    FovTaskListChanged,
    JumpToFovCommand,
    SkipFovCommand,
    RequeueFovCommand,
    FovStatus,
)
from squid.backend.controllers.orchestrator.validation import ValidationSummary
from squid.ui.widgets.orchestrator.validation_dialog import ValidationResultDialog

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.controllers.orchestrator import OrchestratorController

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


# Status colors shared between components
STATUS_COLORS = {
    "pending": QColor("#888888"),
    "running": QColor("#2196F3"),
    "completed": QColor("#4CAF50"),
    "failed": QColor("#f44336"),
    "skipped": QColor("#FF9800"),
}


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
    progress_updated = pyqtSignal(int, int, float, str)  # current, total, percent, name
    intervention_required = pyqtSignal(str)  # message
    error_occurred = pyqtSignal(str, str)  # type, message
    fov_positions_changed = pyqtSignal(dict)  # FOV positions dict
    protocol_loaded = pyqtSignal(dict)  # protocol data
    validation_complete = pyqtSignal(object)  # ValidationSummary

    def __init__(
        self,
        event_bus: "EventBus",
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

        self._setup_ui()
        self._connect_signals()
        self._update_button_states(OrchestratorState.IDLE)

    def _setup_ui(self) -> None:
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Status section
        status_group = self._create_status_section()
        layout.addWidget(status_group)

        # Control buttons
        controls = self._create_controls_section()
        layout.addWidget(controls)

        # Progress section
        progress_group = self._create_progress_section()
        layout.addWidget(progress_group)

        # Intervention section (hidden by default)
        self._intervention_frame = self._create_intervention_section()
        self._intervention_frame.setVisible(False)
        layout.addWidget(self._intervention_frame)

        layout.addStretch()

    def _create_status_section(self) -> QGroupBox:
        """Create the status display section."""
        group = QGroupBox("Experiment Status")
        layout = QVBoxLayout(group)

        # Status label (large text)
        self._status_label = QLabel("IDLE")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        self._status_label.setFont(font)
        self._status_label.setStyleSheet("color: #666;")
        layout.addWidget(self._status_label)

        # Experiment info
        self._experiment_label = QLabel("No protocol loaded")
        self._experiment_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._experiment_label.setWordWrap(True)
        layout.addWidget(self._experiment_label)

        return group

    def _create_controls_section(self) -> QWidget:
        """Create the control buttons section."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Row 1: Load, Validate, Start
        row1 = QHBoxLayout()
        self._load_btn = QPushButton("Load Protocol")
        self._load_btn.setMinimumHeight(35)
        self._load_btn.setStyleSheet("background-color: #607D8B; color: white;")
        self._load_btn.clicked.connect(self._on_load_clicked)
        row1.addWidget(self._load_btn)

        self._validate_btn = QPushButton("Validate")
        self._validate_btn.setMinimumHeight(35)
        self._validate_btn.setStyleSheet("background-color: #795548; color: white;")
        self._validate_btn.clicked.connect(self._on_validate_clicked)
        self._validate_btn.setEnabled(False)
        row1.addWidget(self._validate_btn)

        self._start_btn = QPushButton("Start")
        self._start_btn.setMinimumHeight(35)
        self._start_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self._start_btn.clicked.connect(self._on_start_clicked)
        self._start_btn.setEnabled(False)
        row1.addWidget(self._start_btn)
        layout.addLayout(row1)

        # Row 2: Pause, Resume, Abort
        row2 = QHBoxLayout()
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setMinimumHeight(35)
        self._pause_btn.setStyleSheet("background-color: #FF9800; color: white;")
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        row2.addWidget(self._pause_btn)

        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setMinimumHeight(35)
        self._resume_btn.setStyleSheet("background-color: #2196F3; color: white;")
        self._resume_btn.clicked.connect(self._on_resume_clicked)
        row2.addWidget(self._resume_btn)

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setMinimumHeight(35)
        self._abort_btn.setStyleSheet("background-color: #f44336; color: white;")
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        row2.addWidget(self._abort_btn)
        layout.addLayout(row2)

        return widget

    def _create_progress_section(self) -> QGroupBox:
        """Create the progress tracking section."""
        group = QGroupBox("Progress")
        layout = QVBoxLayout(group)

        # Round progress
        round_layout = QHBoxLayout()
        round_layout.addWidget(QLabel("Round:"))
        self._round_label = QLabel("- / -")
        self._round_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        round_layout.addWidget(self._round_label)
        layout.addLayout(round_layout)

        # Current round name
        self._round_name_label = QLabel("")
        self._round_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._round_name_label.setStyleSheet("font-style: italic;")
        layout.addWidget(self._round_name_label)

        # Overall progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        return group

    def _create_intervention_section(self) -> QFrame:
        """Create the intervention acknowledgment section."""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("background-color: #FFF3CD; border: 2px solid #FFC107;")

        layout = QVBoxLayout(frame)

        header = QLabel("INTERVENTION REQUIRED")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(12)
        header.setFont(font)
        header.setStyleSheet("color: #856404;")
        layout.addWidget(header)

        self._intervention_message = QLabel("")
        self._intervention_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._intervention_message.setWordWrap(True)
        self._intervention_message.setStyleSheet("color: #856404;")
        layout.addWidget(self._intervention_message)

        self._acknowledge_btn = QPushButton("ACKNOWLEDGE")
        self._acknowledge_btn.setMinimumHeight(40)
        self._acknowledge_btn.setStyleSheet(
            "background-color: #FFC107; color: #856404; font-weight: bold;"
        )
        self._acknowledge_btn.clicked.connect(self._on_acknowledge_clicked)
        layout.addWidget(self._acknowledge_btn)

        return frame

    def _connect_signals(self) -> None:
        """Connect signals to slots."""
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
        del old_state  # Unused
        self._status_label.setText(new_state)
        self._update_status_style(new_state)
        try:
            state = OrchestratorState[new_state]
            self._update_button_states(state)
        except KeyError:
            pass
        if new_state != "WAITING_INTERVENTION":
            self._intervention_frame.setVisible(False)

    @pyqtSlot(int, int, float, str)
    def _on_progress_updated_ui(
        self, current: int, total: int, percent: float, name: str
    ) -> None:
        self._round_label.setText(f"{current} / {total}")
        self._round_name_label.setText(name)
        self._progress_bar.setValue(int(percent))

    @pyqtSlot(str)
    def _on_intervention_required_ui(self, message: str) -> None:
        self._intervention_message.setText(message)
        self._intervention_frame.setVisible(True)

    @pyqtSlot(str, str)
    def _on_error_occurred_ui(self, error_type: str, message: str) -> None:
        _log.error(f"Orchestrator error [{error_type}]: {message}")

    @pyqtSlot(object)
    def _on_validation_complete_ui(self, summary: ValidationSummary) -> None:
        """Show validation results dialog."""
        dialog = ValidationResultDialog(summary, parent=self)
        dialog.exec_()

    # ========================================================================
    # Button Handlers
    # ========================================================================

    def _on_load_clicked(self) -> None:
        """Handle load button click."""
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
        """Load and display a protocol file."""
        import yaml
        from pathlib import Path

        try:
            with open(protocol_path, 'r') as f:
                protocol_data: Dict[str, Any] = yaml.safe_load(f)

            self._protocol_data = protocol_data
            protocol_name = protocol_data.get("name", Path(protocol_path).stem)

            # Check imaging steps and whether they use default FOVs
            rounds = protocol_data.get("rounds", [])
            has_imaging = False
            default_fovs_required = False
            for round_def in rounds:
                for step in round_def.get("steps", []):
                    if step.get("step_type") != "imaging":
                        continue
                    has_imaging = True
                    if step.get("fovs", "default") == "default":
                        default_fovs_required = True

            # Update status label
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

            # Emit FOV positions first, then protocol (so tree has positions when populating)
            self.fov_positions_changed.emit(self._fov_positions)
            self.protocol_loaded.emit(protocol_data)

            # Enable Start only if default FOVs are required and loaded
            can_start = not default_fovs_required or fov_count > 0
            self._start_btn.setEnabled(can_start)
            self._validate_btn.setEnabled(True)
            _log.info(f"Protocol loaded: {protocol_path}, can_start={can_start}")

        except Exception as e:
            _log.error(f"Failed to load protocol: {e}")
            self._experiment_label.setText(f"Error: {e}")
            self._protocol_data = None
            self._start_btn.setEnabled(False)
            self._validate_btn.setEnabled(False)

    def _on_start_clicked(self) -> None:
        if self._orchestrator is None:
            _log.warning("No orchestrator configured")
            return
        if self._protocol_path is None or self._base_path is None:
            _log.warning("No protocol loaded")
            return

        # Load FOV positions into ScanCoordinates before starting
        if self._fov_positions:
            # Convert to the format expected by LoadScanCoordinatesCommand
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
        )
        if not success:
            _log.error("Failed to start experiment")

    def _on_validate_clicked(self) -> None:
        """Handle validate button click."""
        if self._protocol_path is None or self._base_path is None:
            _log.warning("No protocol loaded")
            return
        # Calculate FOV count from loaded positions
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
        can_start = (is_idle or is_terminal) and self._protocol_data is not None
        self._start_btn.setEnabled(bool(can_start))
        # Allow validation from idle or terminal states (after abort/complete/fail)
        self._validate_btn.setEnabled(bool((is_idle or is_terminal) and self._protocol_data is not None))
        self._pause_btn.setEnabled(bool(is_running))
        self._resume_btn.setEnabled(bool(is_paused))
        self._abort_btn.setEnabled(bool(is_running or is_paused))

    def _update_status_style(self, state: str) -> None:
        colors = {
            "IDLE": "#666",
            "RUNNING": "#4CAF50",
            "WAITING_INTERVENTION": "#FF9800",
            "PAUSED": "#FF9800",
            "COMPLETED": "#4CAF50",
            "FAILED": "#f44336",
            "ABORTED": "#f44336",
        }
        color = colors.get(state, "#666")
        self._status_label.setStyleSheet(f"color: {color};")

    def shutdown(self) -> None:
        self._cleanup_subscriptions()


class OrchestratorWorkflowTree(EventBusWidget):
    """Hierarchical workflow display for experiment orchestration.

    Shows rounds, operations, and FOVs in a collapsible tree structure.
    Supports FOV highlighting, context menus, and double-click navigation.
    """

    # Signal emitted when user requests to jump to a FOV
    jump_to_fov = pyqtSignal(str, int, int)  # fov_id, round_index, time_point

    # Signal emitted when user requests to skip a FOV
    skip_fov = pyqtSignal(str, int, int)  # fov_id, round_index, time_point

    # Signal emitted when user requests to requeue a FOV
    requeue_fov = pyqtSignal(str, int, int, bool)  # fov_id, round_index, time_point, before_current

    # Signal for thread-safe UI updates
    fov_started = pyqtSignal(str, int, float, float)  # fov_id, fov_index, x_mm, y_mm
    fov_completed = pyqtSignal(str, str, str)  # fov_id, status_name, error_message

    def __init__(
        self,
        event_bus: "EventBus",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(event_bus, parent)
        self._tree_items: Dict[tuple, QTreeWidgetItem] = {}
        self._fov_items: Dict[str, QTreeWidgetItem] = {}  # fov_id -> item
        self._current_highlight: Optional[str] = None  # Currently highlighted fov_id
        self._current_round_index: int = 0
        self._current_time_point: int = 0
        self._fov_positions: Dict[str, List[Tuple[float, float, float]]] = {}

        self._setup_ui()
        self._connect_signals()

    def set_fov_positions(self, positions: Dict[str, List[Tuple[float, float, float]]]) -> None:
        """Set FOV positions to display in the workflow tree.

        Args:
            positions: Dict mapping region_id to list of (x_mm, y_mm, z_mm) tuples
        """
        self._fov_positions = positions

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Tree widget with 4 columns: Operation, Status, Est. Time, Details
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Operation", "Status", "Est. Time", "Details"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)

        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        # Enable context menu
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click navigation
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Placeholder
        placeholder = QTreeWidgetItem(["Load a protocol to see workflow"])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self._tree.addTopLevelItem(placeholder)

        layout.addWidget(self._tree)

        # Expand/collapse buttons
        btn_layout = QHBoxLayout()
        expand_btn = QPushButton("Expand All")
        expand_btn.clicked.connect(self._tree.expandAll)
        btn_layout.addWidget(expand_btn)

        collapse_btn = QPushButton("Collapse All")
        collapse_btn.clicked.connect(self._tree.collapseAll)
        btn_layout.addWidget(collapse_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        """Connect internal signals for thread-safe UI updates."""
        self.fov_started.connect(self._handle_fov_started_ui)
        self.fov_completed.connect(self._handle_fov_completed_ui)

    @property
    def tree(self) -> QTreeWidget:
        """Get the internal tree widget for external connections."""
        return self._tree

    @pyqtSlot(dict)
    def populate_from_protocol(self, protocol: Dict[str, Any]) -> None:
        """Populate the tree from protocol data."""
        self._tree.clear()
        self._tree_items.clear()
        self._fov_items.clear()
        self._current_highlight = None

        rounds = protocol.get("rounds", [])
        if not rounds:
            item = QTreeWidgetItem(["No rounds defined"])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tree.addTopLevelItem(item)
            return

        for round_idx, round_data in enumerate(rounds):
            round_name = round_data.get("name", f"Round {round_idx + 1}")
            # Avoid redundant "Round X: Round X" display
            if round_name.lower().startswith("round"):
                display_name = round_name
            else:
                display_name = f"Round {round_idx + 1}: {round_name}"
            round_item = QTreeWidgetItem([
                display_name,
                "pending",
                "",  # Time estimate (populated if available)
                ""   # Details
            ])
            round_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
            # Set UserRole data for parameter panel
            round_item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "round",
                "round_index": round_idx,
                "round_data": round_data,
            })
            self._tree.addTopLevelItem(round_item)
            self._tree_items[(round_idx,)] = round_item

            # Handle both formats: "operations" list or schema format (fluidics + imaging)
            operations = round_data.get("operations", [])

            # If no operations, build from schema format (fluidics/imaging keys)
            if not operations:
                operations = self._build_operations_from_schema(round_data)

            for op_idx, operation in enumerate(operations):
                op_type = operation.get("type", "unknown")
                op_name = operation.get("name", op_type)

                op_item = QTreeWidgetItem([
                    f"{op_type.title()}: {op_name}",
                    "pending",
                    "",  # Time estimate
                    self._get_operation_details(operation)
                ])
                op_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
                # Set UserRole data for parameter panel
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

    def _build_operations_from_schema(self, round_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build operations list from schema format.

        Handles both V2 format (steps with step_type) and legacy format
        (separate fluidics/imaging keys).

        Args:
            round_data: Round data dictionary from protocol

        Returns:
            List of operation dictionaries with 'type' and relevant params
        """
        operations: List[Dict[str, Any]] = []

        # V2 format: steps list with step_type field
        steps = round_data.get("steps", [])
        if steps:
            for i, step in enumerate(steps):
                step_type = step.get("step_type", "unknown")
                op: Dict[str, Any] = {
                    "type": step_type,
                    "name": step.get("name", f"{step_type.title()} {i + 1}"),
                }
                # Copy over step-specific fields
                if isinstance(step, dict):
                    op.update(step)
                operations.append(op)
            return operations

        # Legacy format: separate fluidics/imaging keys
        fluidics = round_data.get("fluidics", [])
        for i, step in enumerate(fluidics):
            op = {
                "type": "fluidics",
                "name": f"Fluidics {i + 1}",
            }
            # Copy over fluidics-specific fields
            if isinstance(step, dict):
                op.update(step)
            operations.append(op)

        # Add imaging operation if present
        imaging = round_data.get("imaging")
        if imaging is not None:
            op = {
                "type": "imaging",
                "name": "Imaging",
            }
            # Copy over imaging-specific fields
            if isinstance(imaging, dict):
                op.update(imaging)
            operations.append(op)

        # Handle intervention/wait types
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
        # Use loaded FOV positions if available
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
                        "fov_index": fov_idx,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "z_mm": z_mm,
                        "status": "PENDING",
                    })
                    parent_item.addChild(fov_item)
                    self._tree_items[(round_idx, op_idx, fov_idx)] = fov_item
                    self._fov_items[fov_id] = fov_item
                    fov_idx += 1
            return

        # Fall back to fov_config if no positions loaded
        fov_config = operation.get("fov_config", {})
        num_fovs = fov_config.get("num_fovs", 0)

        if num_fovs == 0:
            fov_item = QTreeWidgetItem(["No FOV positions loaded", "", "", ""])
            fov_item.setFlags(Qt.ItemFlag.NoItemFlags)
            fov_item.setForeground(0, QBrush(QColor("#f44336")))  # Red to indicate error
            parent_item.addChild(fov_item)
            return

        for fov_idx in range(num_fovs):
            fov_id = f"FOV_{fov_idx + 1}"
            fov_item = QTreeWidgetItem([f"FOV {fov_idx + 1}", "pending", "", ""])
            fov_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
            # Set UserRole data for parameter panel (positions will be updated at runtime)
            fov_item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "fov",
                "fov_id": fov_id,
                "region_id": "",
                "fov_index": fov_idx,
                "x_mm": 0.0,
                "y_mm": 0.0,
                "z_mm": 0.0,
                "status": "PENDING",
            })
            parent_item.addChild(fov_item)
            self._tree_items[(round_idx, op_idx, fov_idx)] = fov_item
            self._fov_items[fov_id] = fov_item

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
        """Add a FOV item to the tree dynamically.

        Called when FOV list is populated at runtime.

        Args:
            fov_id: Stable FOV identifier (e.g., "A1_0001")
            region_id: Region identifier (e.g., "A1")
            fov_index: FOV index within region
            parent_key: Parent item key tuple (round_idx, op_idx)
            x_mm: X position in mm
            y_mm: Y position in mm
            z_mm: Z position in mm
        """
        parent_item = self._tree_items.get(parent_key)
        if parent_item is None:
            return

        # Check if first child is placeholder
        if parent_item.childCount() == 1:
            first_child = parent_item.child(0)
            if first_child and "runtime" in first_child.text(0).lower():
                parent_item.removeChild(first_child)

        # Show position in details column
        pos_str = f"({x_mm:.3f}, {y_mm:.3f})"
        fov_item = QTreeWidgetItem([
            f"{region_id} - FOV {fov_index + 1}",
            "pending",
            "",
            pos_str
        ])
        fov_item.setForeground(1, QBrush(STATUS_COLORS["pending"]))
        # Set UserRole data for parameter panel
        fov_item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": "fov",
            "fov_id": fov_id,
            "region_id": region_id,
            "fov_index": fov_index,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "z_mm": z_mm,
            "status": "PENDING",
        })
        parent_item.addChild(fov_item)
        self._fov_items[fov_id] = fov_item

    def populate_fovs_from_coordinates(
        self,
        region_fov_coordinates: Dict[str, List[tuple]],
        parent_key: tuple = (0, 0),
    ) -> None:
        """Populate FOV items from scan coordinates.

        Args:
            region_fov_coordinates: Dict mapping region_id to list of (x, y, z) tuples
            parent_key: Parent item key tuple (round_idx, op_idx) for the imaging operation
        """
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
        """Get details string for an operation.

        Handles both V2 format (config/protocol references) and legacy format.
        """
        op_type = operation.get("type", "")
        if op_type == "imaging":
            # V2 format: config reference
            config = operation.get("config")
            if config:
                fovs = operation.get("fovs", "default")
                return f"Config: {config}" + (f", FOVs: {fovs}" if fovs != "default" else "")
            # Legacy format: inline channels
            channels = operation.get("channels", [])
            return f"Channels: {', '.join(channels)}" if channels else ""
        elif op_type == "fluidics":
            # V2 format: protocol reference
            protocol = operation.get("protocol")
            if protocol:
                return f"Protocol: {protocol}"
            # Legacy formats: action/reagent (old) and command/solution (schema)
            action = operation.get("action", "") or operation.get("command", "")
            reagent = operation.get("reagent", "") or operation.get("solution", "")
            return f"{action}: {reagent}" if reagent else str(action)
        elif op_type == "wait":
            # Handle both formats: duration_seconds (old) and duration_s (schema)
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
        """Update status of a tree item."""
        item = self._tree_items.get(key)
        if item is None:
            return
        item.setText(1, status)
        color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
        item.setForeground(1, QBrush(color))
        if details is not None:
            item.setText(3, details)  # Details in column 3 now
        if status == "running":
            self._tree.scrollToItem(item)

    def set_time_estimate(self, key: tuple, time_str: str) -> None:
        """Set time estimate for a tree item.

        Args:
            key: Item key tuple
            time_str: Formatted time string (e.g., "2h 30m")
        """
        item = self._tree_items.get(key)
        if item is not None:
            item.setText(2, time_str)

    # ========================================================================
    # FOV Event Handlers
    # ========================================================================

    @handles(FovTaskStarted)
    def _on_fov_started(self, event: FovTaskStarted) -> None:
        """Handle FOV task started event."""
        self._current_round_index = event.round_index
        self._current_time_point = event.time_point
        # Thread-safe: emit signal for UI update
        self.fov_started.emit(event.fov_id, event.fov_index, event.x_mm, event.y_mm)

    @handles(FovTaskCompleted)
    def _on_fov_completed(self, event: FovTaskCompleted) -> None:
        """Handle FOV task completed event."""
        status_name = event.status.name if hasattr(event.status, 'name') else str(event.status)
        error_msg = event.error_message or ""
        self.fov_completed.emit(event.fov_id, status_name, error_msg)

    @pyqtSlot(str, int, float, float)
    def _handle_fov_started_ui(self, fov_id: str, fov_index: int, x_mm: float, y_mm: float) -> None:
        """Update UI for FOV started (main thread)."""
        _ = fov_index  # Unused but kept for signal compatibility

        # Remove highlight from previous
        if self._current_highlight and self._current_highlight in self._fov_items:
            prev_item = self._fov_items[self._current_highlight]
            prev_item.setBackground(0, QBrush(QColor(0, 0, 0, 0)))  # Transparent

        # Highlight current FOV
        if fov_id in self._fov_items:
            item = self._fov_items[fov_id]
            item.setText(1, "running")
            item.setForeground(1, QBrush(STATUS_COLORS["running"]))
            item.setBackground(0, QBrush(QColor("#E3F2FD")))  # Light blue highlight
            self._tree.scrollToItem(item)
            self._current_highlight = fov_id

            # Update item data with actual coordinates
            item_data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict):
                item_data["x_mm"] = x_mm
                item_data["y_mm"] = y_mm
                item_data["status"] = "EXECUTING"
                item.setData(0, Qt.ItemDataRole.UserRole, item_data)

    @pyqtSlot(str, str, str)
    def _handle_fov_completed_ui(self, fov_id: str, status_name: str, error_msg: str) -> None:
        """Update UI for FOV completed (main thread)."""
        if fov_id not in self._fov_items:
            return

        item = self._fov_items[fov_id]

        # Map status name to display status
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

        # Remove running highlight
        item.setBackground(0, QBrush(QColor(0, 0, 0, 0)))

        # Update item data with final status
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item_data, dict):
            item_data["status"] = status_name
            if error_msg:
                item_data["error_message"] = error_msg
            item.setData(0, Qt.ItemDataRole.UserRole, item_data)

        # Show error in details if failed
        if error_msg:
            item.setText(3, error_msg[:50])

    # ========================================================================
    # Context Menu
    # ========================================================================

    def _show_context_menu(self, position) -> None:
        """Show context menu for FOV items."""
        item = self._tree.itemAt(position)
        if item is None:
            return

        # Get item data and extract fov_id
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not item_data or not isinstance(item_data, dict):
            return

        # Only show context menu for FOV items
        if item_data.get("type") != "fov":
            return

        fov_id = item_data.get("fov_id")
        if not fov_id:
            return

        menu = QMenu(self)

        # Jump to this FOV
        jump_action = QAction("Jump to this FOV", self)
        jump_action.triggered.connect(
            lambda: self._emit_jump(fov_id)
        )
        menu.addAction(jump_action)

        # Skip this FOV
        skip_action = QAction("Skip this FOV", self)
        skip_action.triggered.connect(
            lambda: self._emit_skip(fov_id)
        )
        menu.addAction(skip_action)

        menu.addSeparator()

        # Requeue this FOV
        requeue_action = QAction("Requeue this FOV", self)
        requeue_action.triggered.connect(
            lambda: self._emit_requeue(fov_id, before_current=False)
        )
        menu.addAction(requeue_action)

        # Requeue before current
        requeue_before_action = QAction("Requeue before current", self)
        requeue_before_action.triggered.connect(
            lambda: self._emit_requeue(fov_id, before_current=True)
        )
        menu.addAction(requeue_before_action)

        viewport = self._tree.viewport()
        if viewport is not None:
            menu.exec_(viewport.mapToGlobal(position))

    def _emit_jump(self, fov_id: str) -> None:
        """Emit jump command."""
        self.jump_to_fov.emit(fov_id, self._current_round_index, self._current_time_point)
        self._publish(JumpToFovCommand(
            fov_id=fov_id,
            round_index=self._current_round_index,
            time_point=self._current_time_point,
        ))

    def _emit_skip(self, fov_id: str) -> None:
        """Emit skip command."""
        self.skip_fov.emit(fov_id, self._current_round_index, self._current_time_point)
        self._publish(SkipFovCommand(
            fov_id=fov_id,
            round_index=self._current_round_index,
            time_point=self._current_time_point,
        ))

    def _emit_requeue(self, fov_id: str, before_current: bool) -> None:
        """Emit requeue command."""
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

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle double-click on tree item."""
        _ = column  # Unused
        item_data = item.data(0, Qt.ItemDataRole.UserRole)
        if item_data and isinstance(item_data, dict):
            # Only handle FOV items
            if item_data.get("type") == "fov":
                fov_id = item_data.get("fov_id")
                if fov_id:
                    self._emit_jump(fov_id)

    # ========================================================================
    # Cleanup
    # ========================================================================

    def cleanup(self) -> None:
        """Cleanup resources."""
        self._cleanup_subscriptions()


# Backwards compatibility alias
OrchestratorWidget = OrchestratorControlPanel
