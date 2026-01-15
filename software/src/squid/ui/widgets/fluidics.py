# Fluidics control widgets
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pandas as pd

import squid.core.logging
from qtpy.QtCore import Signal, QDateTime, QTimer, Qt
from qtpy.QtGui import QBrush, QColor, QFont
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTextEdit,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QProgressBar,
    QFrame,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
)

from squid.ui.widgets.base import PandasTableModel
from squid.core.events import (
    FluidicsOperationStarted,
    FluidicsOperationCompleted,
    FluidicsOperationProgress,
    FluidicsPhaseChanged,
    FluidicsIncubationStarted,
    FluidicsIncubationProgress,
    FluidicsIncubationCompleted,
    FluidicsStatusChanged,
    FluidicsSequenceStarted,
    FluidicsSequenceStepStarted,
    FluidicsSequenceCompleted,
)

if TYPE_CHECKING:
    from squid.backend.services.fluidics_service import FluidicsService
    from squid.ui.ui_event_bus import UIEventBus


class FluidicsWidget(QWidget):
    """Widget for manual fluidics control.

    Uses FluidicsService for all hardware operations, which enables:
    - Thread-safe operations
    - Shared state with orchestrator workflows
    - Event-driven status updates

    Can be created even if service is unavailable - provides UI to
    select config file and initialize.
    """

    log_message_signal = Signal(str)
    fluidics_initialized_signal = Signal()

    def __init__(
        self,
        fluidics_service: Optional["FluidicsService"],
        event_bus: "UIEventBus",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._service = fluidics_service
        self._event_bus = event_bus

        # Sequence data (loaded from CSV)
        self._sequence_df: Optional[pd.DataFrame] = None
        self._protocol_groups: dict[str, pd.DataFrame] = {}
        self._available_solutions: list[str] = []
        self._config_path: Optional[str] = None

        # Operation tracking
        self._operation_start_time: Optional[QDateTime] = None
        self._operation_est_duration: Optional[float] = None

        # Sequence execution tracking
        self._is_sequence_running: bool = False
        self._sequence_name: str = ""
        self._sequence_current_step: int = 0
        self._sequence_total_steps: int = 0

        # Skip step control
        self._skip_requested: bool = False
        self._empty_on_skip: bool = True

        # Get available solutions from service if available
        self._refresh_solutions()

        # Set up the UI
        self._setup_ui()
        self.log_message_signal.connect(self._log_status)

        # Subscribe to events for status updates
        self._subscribe_to_events()

        # Update UI state based on service availability
        self._update_service_status()

    @property
    def _is_available(self) -> bool:
        """Check if service is available and has a driver."""
        return self._service is not None and self._service.is_available

    def _refresh_solutions(self) -> None:
        """Refresh the list of available solutions from the service."""
        if self._service is not None:
            solutions = self._service.get_available_solutions()
            self._available_solutions = list(solutions.keys())
        else:
            self._available_solutions = []
        self._log.debug(f"Available solutions: {self._available_solutions}")

    def _subscribe_to_events(self) -> None:
        """Subscribe to fluidics events for status updates."""
        if self._event_bus is not None:
            self._event_bus.subscribe(FluidicsOperationStarted, self._on_operation_started)
            self._event_bus.subscribe(FluidicsOperationCompleted, self._on_operation_completed)
            self._event_bus.subscribe(FluidicsOperationProgress, self._on_operation_progress)
            self._event_bus.subscribe(FluidicsPhaseChanged, self._on_phase_changed)
            self._event_bus.subscribe(FluidicsIncubationStarted, self._on_incubation_started)
            self._event_bus.subscribe(FluidicsIncubationProgress, self._on_incubation_progress)
            self._event_bus.subscribe(FluidicsIncubationCompleted, self._on_incubation_completed)
            self._event_bus.subscribe(FluidicsStatusChanged, self._on_status_changed)
            # Sequence progress events
            self._event_bus.subscribe(FluidicsSequenceStarted, self._on_sequence_started)
            self._event_bus.subscribe(FluidicsSequenceStepStarted, self._on_sequence_step_started)
            self._event_bus.subscribe(FluidicsSequenceCompleted, self._on_sequence_completed)

    def _setup_ui(self) -> None:
        """Set up the widget UI."""
        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Configuration panel at top (always visible)
        self._setup_config_panel(main_layout)

        # System Status dashboard (real-time state display)
        self._setup_status_dashboard(main_layout)

        # Combined progress panels (always visible, side by side)
        self._setup_combined_progress_panel(main_layout)

        # Horizontal layout for controls and sequences
        content_layout = QHBoxLayout()
        main_layout.addLayout(content_layout)

        # Left side - Control panels
        left_panel = QVBoxLayout()
        self._setup_control_panels(left_panel)
        content_layout.addLayout(left_panel, 1)

        # Right side - Sequences panel
        right_panel = QVBoxLayout()
        self._setup_sequences_panel(right_panel)
        content_layout.addLayout(right_panel, 1)

    def _setup_config_panel(self, parent_layout: QVBoxLayout) -> None:
        """Set up the configuration panel."""
        config_group = QGroupBox("Configuration")
        config_layout = QHBoxLayout()

        # Status label
        self.lbl_status = QLabel("Status: Not configured")
        self.lbl_status.setStyleSheet("font-weight: bold;")
        config_layout.addWidget(self.lbl_status)

        config_layout.addStretch()

        # Config file path display
        config_layout.addWidget(QLabel("Config:"))
        self.txt_config_path = QLineEdit()
        self.txt_config_path.setReadOnly(True)
        self.txt_config_path.setMinimumWidth(300)
        self.txt_config_path.setPlaceholderText("No config file selected")
        config_layout.addWidget(self.txt_config_path)

        # Browse button
        self.btn_browse_config = QPushButton("Browse...")
        self.btn_browse_config.clicked.connect(self._browse_config)
        config_layout.addWidget(self.btn_browse_config)

        # Initialize button
        self.btn_initialize = QPushButton("Initialize")
        self.btn_initialize.clicked.connect(self._initialize_fluidics)
        config_layout.addWidget(self.btn_initialize)

        config_group.setLayout(config_layout)
        parent_layout.addWidget(config_group)

    def _setup_status_dashboard(self, parent_layout: QVBoxLayout) -> None:
        """Set up the real-time status dashboard panel."""
        self.status_dashboard_group = QGroupBox("System Status")
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setContentsMargins(12, 8, 12, 8)
        dashboard_layout.setSpacing(16)

        # State indicator group (LED + label)
        state_widget = QWidget()
        state_layout = QHBoxLayout(state_widget)
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(6)

        self.status_led = QLabel("●")
        self.status_led.setStyleSheet("color: gray; font-size: 24px;")
        state_layout.addWidget(self.status_led)

        self.lbl_state = QLabel("IDLE")
        self.lbl_state.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.lbl_state.setMinimumWidth(90)
        state_layout.addWidget(self.lbl_state)

        dashboard_layout.addWidget(state_widget)

        # Vertical separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFrameShadow(QFrame.Sunken)
        dashboard_layout.addWidget(sep1)

        # Port/Solution display
        port_widget = QWidget()
        port_layout = QHBoxLayout(port_widget)
        port_layout.setContentsMargins(0, 0, 0, 0)
        port_layout.setSpacing(4)

        port_label = QLabel("Port:")
        port_label.setStyleSheet("color: #888;")
        port_layout.addWidget(port_label)

        self.lbl_current_port = QLabel("--")
        self.lbl_current_port.setStyleSheet("font-weight: bold; font-size: 13px;")
        port_layout.addWidget(self.lbl_current_port)

        self.lbl_current_solution = QLabel("(none)")
        self.lbl_current_solution.setStyleSheet("color: #888;")
        port_layout.addWidget(self.lbl_current_solution)

        dashboard_layout.addWidget(port_widget)

        # Vertical separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        dashboard_layout.addWidget(sep2)

        # Syringe volume gauge
        syringe_widget = QWidget()
        syringe_layout = QHBoxLayout(syringe_widget)
        syringe_layout.setContentsMargins(0, 0, 0, 0)
        syringe_layout.setSpacing(8)

        syringe_label = QLabel("Syringe:")
        syringe_label.setStyleSheet("color: #888;")
        syringe_layout.addWidget(syringe_label)

        self.syringe_gauge = QProgressBar()
        self.syringe_gauge.setMinimum(0)
        self.syringe_gauge.setMaximum(5000)
        self.syringe_gauge.setValue(0)
        self.syringe_gauge.setFormat("%v / %m uL")
        self.syringe_gauge.setFixedWidth(180)
        self.syringe_gauge.setFixedHeight(20)
        syringe_layout.addWidget(self.syringe_gauge)

        dashboard_layout.addWidget(syringe_widget)
        dashboard_layout.addStretch()

        self.status_dashboard_group.setLayout(dashboard_layout)
        parent_layout.addWidget(self.status_dashboard_group)

    def _setup_combined_progress_panel(self, parent_layout: QVBoxLayout) -> None:
        """Set up combined progress panels (operation + sequence) side by side."""
        # Container for both progress panels
        progress_container = QHBoxLayout()
        progress_container.setSpacing(10)

        # Left: Operation progress
        self._setup_progress_panel(progress_container)

        # Right: Sequence progress
        self._setup_sequence_progress_panel(progress_container)

        parent_layout.addLayout(progress_container)

    def _setup_progress_panel(self, parent_layout: QHBoxLayout) -> None:
        """Set up the operation progress panel (always visible)."""
        self.progress_group = QGroupBox("Operation Progress")
        progress_layout = QVBoxLayout()

        # Operation description row
        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Operation:"))
        self.lbl_operation = QLabel("Idle")
        self.lbl_operation.setStyleSheet("font-weight: bold;")
        op_row.addWidget(self.lbl_operation)
        op_row.addStretch()

        # Flow details
        op_row.addWidget(QLabel("Flow:"))
        self.lbl_flow_details = QLabel("--")
        op_row.addWidget(self.lbl_flow_details)
        progress_layout.addLayout(op_row)

        # Progress bar row
        bar_row = QHBoxLayout()
        bar_row.addWidget(QLabel("Progress:"))
        self.operation_progress_bar = QProgressBar()
        self.operation_progress_bar.setMinimum(0)
        self.operation_progress_bar.setMaximum(100)
        self.operation_progress_bar.setValue(0)
        bar_row.addWidget(self.operation_progress_bar, 1)

        # Time remaining
        self.lbl_time_remaining = QLabel("--:--")
        self.lbl_time_remaining.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.lbl_time_remaining.setMinimumWidth(60)
        bar_row.addWidget(self.lbl_time_remaining)
        progress_layout.addLayout(bar_row)

        self.progress_group.setLayout(progress_layout)
        parent_layout.addWidget(self.progress_group, 1)  # Stretch factor 1 for equal sizing

    def _setup_sequence_progress_panel(self, parent_layout: QHBoxLayout) -> None:
        """Set up the sequence progress tracking panel (always visible)."""
        self.sequence_progress_group = QGroupBox("Sequence Progress")
        seq_layout = QVBoxLayout()
        seq_layout.setContentsMargins(10, 10, 10, 10)
        seq_layout.setSpacing(8)

        # Sequence name and step counter row
        header_row = QHBoxLayout()
        seq_label = QLabel("Sequence:")
        seq_label.setStyleSheet("color: #888;")
        header_row.addWidget(seq_label)

        self.lbl_sequence_name = QLabel("--")
        self.lbl_sequence_name.setStyleSheet("font-weight: bold; font-size: 12px;")
        header_row.addWidget(self.lbl_sequence_name)
        header_row.addStretch()

        # Step counter
        step_label = QLabel("Step:")
        step_label.setStyleSheet("color: #888;")
        header_row.addWidget(step_label)

        self.lbl_step_counter = QLabel("0 of 0")
        self.lbl_step_counter.setStyleSheet("font-weight: bold; font-size: 12px;")
        header_row.addWidget(self.lbl_step_counter)
        seq_layout.addLayout(header_row)

        # Sequence progress bar
        self.sequence_progress_bar = QProgressBar()
        self.sequence_progress_bar.setMinimum(0)
        self.sequence_progress_bar.setMaximum(100)
        self.sequence_progress_bar.setValue(0)
        self.sequence_progress_bar.setFormat("%p%")
        self.sequence_progress_bar.setFixedHeight(20)
        seq_layout.addWidget(self.sequence_progress_bar)

        # Current and next step descriptions (grid layout)
        steps_grid = QGridLayout()
        steps_grid.setHorizontalSpacing(8)

        curr_label = QLabel("Current:")
        curr_label.setStyleSheet("color: #888;")
        steps_grid.addWidget(curr_label, 0, 0)

        self.lbl_current_step = QLabel("--")
        self.lbl_current_step.setStyleSheet("font-weight: bold;")
        steps_grid.addWidget(self.lbl_current_step, 0, 1)

        next_label = QLabel("Next:")
        next_label.setStyleSheet("color: #888;")
        steps_grid.addWidget(next_label, 1, 0)

        self.lbl_next_step = QLabel("--")
        self.lbl_next_step.setStyleSheet("color: #666;")
        steps_grid.addWidget(self.lbl_next_step, 1, 1)

        steps_grid.setColumnStretch(1, 1)
        seq_layout.addLayout(steps_grid)

        # Skip controls row
        skip_layout = QHBoxLayout()
        self.chk_empty_on_skip = QCheckBox("Empty syringe")
        self.chk_empty_on_skip.setChecked(True)
        self.chk_empty_on_skip.setToolTip("Empty the syringe before skipping to next step")

        self.btn_skip_step = QPushButton("Skip to Next Step")
        self.btn_skip_step.setEnabled(False)  # Enabled during execution
        self.btn_skip_step.clicked.connect(self._skip_current_step)

        skip_layout.addWidget(self.chk_empty_on_skip)
        skip_layout.addStretch()
        skip_layout.addWidget(self.btn_skip_step)
        seq_layout.addLayout(skip_layout)

        self.sequence_progress_group.setLayout(seq_layout)
        parent_layout.addWidget(self.sequence_progress_group, 1)  # Stretch factor 1 for equal sizing

    def _setup_control_panels(self, parent_layout: QVBoxLayout) -> None:
        """Set up the fluidics control panels."""
        # Standard field widths for consistency
        FIELD_WIDTH = 100
        COMBO_MIN_WIDTH = 150

        # Prime Ports panel
        prime_group = QGroupBox("Prime Ports")
        prime_layout = QGridLayout()
        prime_layout.setHorizontalSpacing(12)
        prime_layout.setVerticalSpacing(8)

        # Row 0: Ports and Fill solution
        prime_layout.addWidget(QLabel("Ports:"), 0, 0)
        self.txt_prime_ports = QLineEdit()
        self.txt_prime_ports.setPlaceholderText("e.g., 1-5 or all")
        self.txt_prime_ports.setFixedWidth(FIELD_WIDTH)
        prime_layout.addWidget(self.txt_prime_ports, 0, 1)

        prime_layout.addWidget(QLabel("Fill with:"), 0, 2)
        self.prime_fill_combo = QComboBox()
        self.prime_fill_combo.setMinimumWidth(COMBO_MIN_WIDTH)
        self.prime_fill_combo.addItems(self._available_solutions)
        self._set_default_wash_buffer(self.prime_fill_combo)
        prime_layout.addWidget(self.prime_fill_combo, 0, 3)

        # Row 1: Volume, Flow rate, Start button
        prime_layout.addWidget(QLabel("Volume (uL):"), 1, 0)
        self.txt_prime_volume = QLineEdit()
        self.txt_prime_volume.setText("2000")
        self.txt_prime_volume.setFixedWidth(FIELD_WIDTH)
        prime_layout.addWidget(self.txt_prime_volume, 1, 1)

        prime_layout.addWidget(QLabel("Flow rate:"), 1, 2)
        self.txt_prime_flow_rate = QLineEdit()
        self.txt_prime_flow_rate.setText("5000")
        self.txt_prime_flow_rate.setFixedWidth(FIELD_WIDTH)
        prime_layout.addWidget(self.txt_prime_flow_rate, 1, 3)

        self.btn_prime_start = QPushButton("Prime")
        self.btn_prime_start.setFixedWidth(80)
        prime_layout.addWidget(self.btn_prime_start, 1, 4)

        prime_layout.setColumnStretch(5, 1)  # Stretch at end
        prime_group.setLayout(prime_layout)
        parent_layout.addWidget(prime_group)

        # Wash panel
        wash_group = QGroupBox("Wash")
        wash_layout = QGridLayout()
        wash_layout.setHorizontalSpacing(12)
        wash_layout.setVerticalSpacing(8)

        # Row 0: Solution and Repeats
        wash_layout.addWidget(QLabel("Solution:"), 0, 0)
        self.cleanup_solution_combo = QComboBox()
        self.cleanup_solution_combo.setMinimumWidth(COMBO_MIN_WIDTH)
        self.cleanup_solution_combo.addItems(self._available_solutions)
        self._set_default_wash_buffer(self.cleanup_solution_combo)
        wash_layout.addWidget(self.cleanup_solution_combo, 0, 1)

        wash_layout.addWidget(QLabel("Repeat:"), 0, 2)
        self.txt_cleanup_repeat = QLineEdit()
        self.txt_cleanup_repeat.setText("3")
        self.txt_cleanup_repeat.setFixedWidth(60)
        wash_layout.addWidget(self.txt_cleanup_repeat, 0, 3)

        # Row 1: Volume, Flow rate, Start button
        wash_layout.addWidget(QLabel("Volume (uL):"), 1, 0)
        self.txt_cleanup_volume = QLineEdit()
        self.txt_cleanup_volume.setText("2000")
        self.txt_cleanup_volume.setFixedWidth(FIELD_WIDTH)
        wash_layout.addWidget(self.txt_cleanup_volume, 1, 1)

        wash_layout.addWidget(QLabel("Flow rate:"), 1, 2)
        self.txt_cleanup_flow_rate = QLineEdit()
        self.txt_cleanup_flow_rate.setText("5000")
        self.txt_cleanup_flow_rate.setFixedWidth(FIELD_WIDTH)
        wash_layout.addWidget(self.txt_cleanup_flow_rate, 1, 3)

        self.btn_cleanup_start = QPushButton("Wash")
        self.btn_cleanup_start.setFixedWidth(80)
        wash_layout.addWidget(self.btn_cleanup_start, 1, 4)

        wash_layout.setColumnStretch(5, 1)
        wash_group.setLayout(wash_layout)
        parent_layout.addWidget(wash_group)

        # Manual Flow panel
        manual_group = QGroupBox("Manual Flow")
        manual_layout = QGridLayout()
        manual_layout.setHorizontalSpacing(12)
        manual_layout.setVerticalSpacing(8)

        # Row 0: Solution and Volume
        manual_layout.addWidget(QLabel("Solution:"), 0, 0)
        self.manual_solution_combo = QComboBox()
        self.manual_solution_combo.setMinimumWidth(COMBO_MIN_WIDTH)
        self.manual_solution_combo.addItems(self._available_solutions)
        manual_layout.addWidget(self.manual_solution_combo, 0, 1)

        manual_layout.addWidget(QLabel("Volume (uL):"), 0, 2)
        self.txt_manual_volume = QLineEdit()
        self.txt_manual_volume.setPlaceholderText("e.g., 500")
        self.txt_manual_volume.setFixedWidth(FIELD_WIDTH)
        manual_layout.addWidget(self.txt_manual_volume, 0, 3)

        # Row 1: Flow rate and buttons
        manual_layout.addWidget(QLabel("Flow rate:"), 1, 0)
        self.txt_manual_flow_rate = QLineEdit()
        self.txt_manual_flow_rate.setText("500")
        self.txt_manual_flow_rate.setFixedWidth(FIELD_WIDTH)
        manual_layout.addWidget(self.txt_manual_flow_rate, 1, 1)

        # Buttons
        self.btn_manual_flow = QPushButton("Flow")
        self.btn_manual_flow.setFixedWidth(80)
        manual_layout.addWidget(self.btn_manual_flow, 1, 2)

        self.btn_empty_syringe_pump = QPushButton("Empty Syringe")
        self.btn_empty_syringe_pump.setFixedWidth(110)
        manual_layout.addWidget(self.btn_empty_syringe_pump, 1, 3)

        manual_layout.setColumnStretch(5, 1)
        manual_group.setLayout(manual_layout)
        parent_layout.addWidget(manual_group)

        # Status log panel (compact)
        status_group = QGroupBox("Log")
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(4, 4, 4, 4)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(80)
        self.status_text.setStyleSheet("font-size: 11px;")
        status_layout.addWidget(self.status_text)

        status_group.setLayout(status_layout)
        parent_layout.addWidget(status_group)

        # Load Sequences button at bottom of left panel
        self.btn_load_sequences = QPushButton("Load Sequences from CSV...")
        parent_layout.addWidget(self.btn_load_sequences)

        # Add stretch to push controls to top
        parent_layout.addStretch()

        # Connect signals
        self.btn_load_sequences.clicked.connect(self._load_sequences)
        self.btn_prime_start.clicked.connect(self._start_prime)
        self.btn_cleanup_start.clicked.connect(self._start_wash)
        self.btn_manual_flow.clicked.connect(self._start_manual_flow)
        self.btn_empty_syringe_pump.clicked.connect(self._empty_syringe_pump)

    def _setup_sequences_panel(self, parent_layout: QVBoxLayout) -> None:
        """Set up the sequences panel with protocols list and steps view."""
        sequences_group = QGroupBox("Protocols")
        sequences_layout = QVBoxLayout()
        sequences_layout.setContentsMargins(8, 8, 8, 8)
        sequences_layout.setSpacing(8)

        # Two-panel layout: protocols on left, steps on right
        splitter = QSplitter(Qt.Horizontal)

        # Left panel: Protocol list
        protocols_widget = QWidget()
        protocols_layout = QVBoxLayout(protocols_widget)
        protocols_layout.setContentsMargins(0, 0, 0, 0)
        protocols_layout.setSpacing(4)

        protocols_label = QLabel("Protocols")
        protocols_label.setStyleSheet("font-weight: bold;")
        protocols_layout.addWidget(protocols_label)

        self.protocols_list = QListWidget()
        self.protocols_list.setAlternatingRowColors(True)
        self.protocols_list.currentItemChanged.connect(self._on_protocol_selected)
        protocols_layout.addWidget(self.protocols_list)

        splitter.addWidget(protocols_widget)

        # Right panel: Steps table for selected protocol
        steps_widget = QWidget()
        steps_layout = QVBoxLayout(steps_widget)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(4)

        steps_label = QLabel("Steps")
        steps_label.setStyleSheet("font-weight: bold;")
        steps_layout.addWidget(steps_label)

        self.steps_table = QTableWidget()
        self.steps_table.setColumnCount(5)
        self.steps_table.setHorizontalHeaderLabels(["#", "Operation", "Solution", "Volume", "Incubation"])
        self.steps_table.setAlternatingRowColors(True)
        self.steps_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.steps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.steps_table.verticalHeader().setVisible(False)

        # Set column sizing
        header = self.steps_table.horizontalHeader()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        steps_layout.addWidget(self.steps_table)

        splitter.addWidget(steps_widget)

        # Set initial splitter sizes (40% protocols, 60% steps)
        splitter.setSizes([150, 250])

        sequences_layout.addWidget(splitter, 1)

        # Execution buttons row
        exec_row = QHBoxLayout()
        exec_row.setSpacing(8)

        self.btn_execute_selected = QPushButton("Run Protocol")
        self.btn_execute_selected.setMinimumWidth(100)
        self.btn_execute_selected.clicked.connect(self._execute_selected_sequence)
        exec_row.addWidget(self.btn_execute_selected)

        self.btn_execute_all = QPushButton("Run All")
        self.btn_execute_all.setMinimumWidth(80)
        self.btn_execute_all.clicked.connect(self._execute_all_sequences)
        exec_row.addWidget(self.btn_execute_all)

        exec_row.addStretch()

        # Emergency Stop button - prominent
        self.btn_emergency_stop = QPushButton("STOP")
        self.btn_emergency_stop.setMinimumWidth(80)
        self.btn_emergency_stop.setMinimumHeight(32)
        self.btn_emergency_stop.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #e74c3c; }"
            "QPushButton:pressed { background-color: #a93226; }"
        )
        self.btn_emergency_stop.clicked.connect(self._emergency_stop)
        exec_row.addWidget(self.btn_emergency_stop)
        sequences_layout.addLayout(exec_row)

        sequences_group.setLayout(sequences_layout)
        parent_layout.addWidget(sequences_group)

    def _update_service_status(self) -> None:
        """Update UI based on service availability."""
        if self._is_available:
            self.lbl_status.setText("Status: Ready")
            self.lbl_status.setStyleSheet("font-weight: bold; color: green;")
            self.btn_initialize.setEnabled(False)
            self._enable_controls(True)

            # Update config path display if we have a service
            # (config was loaded at startup)
            if not self.txt_config_path.text():
                self.txt_config_path.setText("(loaded at startup)")
        else:
            self.lbl_status.setText("Status: Not initialized")
            self.lbl_status.setStyleSheet("font-weight: bold; color: orange;")
            self.btn_initialize.setEnabled(True)
            self._enable_controls(False)
            self._log_status("Fluidics not initialized. Select a config file and click Initialize.")

    def _set_default_wash_buffer(self, combo: QComboBox) -> None:
        """Set combo box to wash_buffer if available."""
        for i, name in enumerate(self._available_solutions):
            if name.lower() == "wash_buffer":
                combo.setCurrentIndex(i)
                return

    # ─────────────────────────────────────────────────────────────────────────
    # Configuration
    # ─────────────────────────────────────────────────────────────────────────

    def _browse_config(self) -> None:
        """Open file dialog to select fluidics config file."""
        start_dir = str(Path(__file__).parent.parent.parent.parent.parent / "configurations")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Fluidics Configuration",
            start_dir,
            "JSON Files (*.json);;All Files (*)",
        )
        if file_path:
            self._config_path = file_path
            self.txt_config_path.setText(file_path)
            self._log_status(f"Config file selected: {file_path}")

    def _initialize_fluidics(self) -> None:
        """Initialize fluidics with the selected config file."""
        if not self._config_path:
            QMessageBox.warning(
                self,
                "No Config Selected",
                "Please select a configuration file first.",
            )
            return

        self._log_status(f"Initializing fluidics with config: {self._config_path}")

        try:
            # Import here to avoid circular imports
            from squid.backend.drivers.fluidics import SimulatedFluidicsController
            from squid.backend.services.fluidics_service import FluidicsService
            from squid.core.events import event_bus

            # Create driver with timing simulation enabled
            driver = SimulatedFluidicsController(
                config_path=self._config_path,
                simulate_timing=True,
            )

            if not driver.initialize():
                self._log_status("ERROR: Failed to initialize fluidics driver")
                QMessageBox.critical(
                    self,
                    "Initialization Failed",
                    "Failed to initialize the fluidics driver. Check the config file.",
                )
                return

            # Create service
            self._service = FluidicsService(driver, event_bus)
            self._log_status("Fluidics initialized successfully!")

            # Refresh UI
            self._refresh_solutions()
            self._update_combo_boxes()
            self._update_service_status()

            self.fluidics_initialized_signal.emit()

        except Exception as e:
            self._log.exception(f"Error initializing fluidics: {e}")
            self._log_status(f"ERROR: {e}")
            QMessageBox.critical(
                self,
                "Initialization Error",
                f"Error initializing fluidics:\n{e}",
            )

    def _update_combo_boxes(self) -> None:
        """Update combo boxes with available solutions."""
        for combo in [
            self.prime_fill_combo,
            self.cleanup_solution_combo,
            self.manual_solution_combo,
        ]:
            combo.clear()
            combo.addItems(self._available_solutions)

        self._set_default_wash_buffer(self.prime_fill_combo)
        self._set_default_wash_buffer(self.cleanup_solution_combo)

    # ─────────────────────────────────────────────────────────────────────────
    # Event Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_operation_started(self, event: FluidicsOperationStarted) -> None:
        """Handle operation started events."""
        msg = f"Started {event.operation}"
        if event.solution:
            msg += f" ({event.solution})"
        if event.volume_ul:
            msg += f" - {event.volume_ul} uL"
        self.log_message_signal.emit(msg)
        self._enable_controls(False)

        # Show progress panel with operation details
        self.lbl_operation.setText(event.operation.upper())
        flow_details = ""
        if event.solution:
            flow_details = f"{event.solution}"
        if event.flow_rate_ul_per_min:
            flow_details += f" @ {event.flow_rate_ul_per_min:.0f} uL/min"
        self.lbl_flow_details.setText(flow_details or "--")

        # Estimate duration for progress tracking
        if event.volume_ul > 0 and event.flow_rate_ul_per_min > 0:
            est_duration = (event.volume_ul / event.flow_rate_ul_per_min) * 60
            self._operation_start_time = QDateTime.currentDateTime()
            self._operation_est_duration = est_duration
        else:
            self._operation_start_time = None
            self._operation_est_duration = None

        self.operation_progress_bar.setValue(0)
        self.lbl_time_remaining.setText("--:--")

    def _on_operation_completed(self, event: FluidicsOperationCompleted) -> None:
        """Handle operation completed events."""
        if event.success:
            msg = f"Completed {event.operation} in {event.duration_seconds:.1f}s"
        else:
            msg = f"Failed {event.operation}: {event.error_message}"
        self.log_message_signal.emit(msg)
        self._enable_controls(True)

        # Reset operation label
        self.lbl_operation.setText("Idle")

        # Update status LED to idle
        self._update_status_led("idle")
        self.lbl_state.setText("IDLE")

    def _on_operation_progress(self, event: FluidicsOperationProgress) -> None:
        """Handle operation progress events (called every ~1 second during flow operations)."""
        # Update progress bar
        self.operation_progress_bar.setValue(int(event.progress_percent))

        # Update time remaining
        if event.remaining_seconds is not None:
            mins = int(event.remaining_seconds // 60)
            secs = int(event.remaining_seconds % 60)
            self.lbl_time_remaining.setText(f"{mins}:{secs:02d}")

        # Update syringe volume if available
        if event.syringe_volume_ul is not None:
            self.syringe_gauge.setValue(int(event.syringe_volume_ul))

    def _on_phase_changed(self, event: FluidicsPhaseChanged) -> None:
        """Handle operation phase change events (aspirating/dispensing/valve)."""
        # Update state label with current phase
        phase_labels = {
            "aspirating": "ASPIRATING",
            "dispensing": "DISPENSING",
            "valve_switching": "SWITCHING",
        }
        state_text = phase_labels.get(event.phase, event.phase.upper())
        self.lbl_state.setText(state_text)

        # Update LED color based on phase
        phase_colors = {
            "aspirating": "#3498db",  # Blue
            "dispensing": "#f39c12",  # Orange
            "valve_switching": "#9b59b6",  # Purple
        }
        color = phase_colors.get(event.phase, "#f1c40f")  # Yellow default
        self.status_led.setStyleSheet(f"color: {color}; font-size: 20px;")

        # Update port/solution display
        if event.port is not None:
            self.lbl_current_port.setText(str(event.port))
            self.lbl_current_solution.setText(f"({event.solution or 'unknown'})")

        # Log phase change
        phase_msg = f"Phase: {state_text}"
        if event.port is not None:
            phase_msg += f" - Port {event.port}"
            if event.solution:
                phase_msg += f" ({event.solution})"
        self.log_message_signal.emit(phase_msg)

    def _on_incubation_started(self, event: FluidicsIncubationStarted) -> None:
        """Handle incubation started events."""
        self.lbl_operation.setText("INCUBATING")
        solution_info = f"with {event.solution}" if event.solution else ""
        self.lbl_flow_details.setText(solution_info)

        # Set up progress bar for incubation
        self.operation_progress_bar.setMaximum(100)
        self.operation_progress_bar.setValue(0)

        # Update status
        self._update_status_led("incubating")
        self.lbl_state.setText("INCUBATING")

        mins, secs = divmod(int(event.duration_seconds), 60)
        self.lbl_time_remaining.setText(f"{mins}:{secs:02d}")

        self.log_message_signal.emit(
            f"Incubating for {event.duration_seconds:.1f}s {solution_info}"
        )

    def _on_incubation_progress(self, event: FluidicsIncubationProgress) -> None:
        """Handle incubation progress events (called every ~1 second)."""
        # Update progress bar
        self.operation_progress_bar.setValue(int(event.progress_percent))

        # Update countdown timer
        mins, secs = divmod(int(event.remaining_seconds), 60)
        self.lbl_time_remaining.setText(f"{mins}:{secs:02d}")

    def _on_incubation_completed(self, event: FluidicsIncubationCompleted) -> None:
        """Handle incubation completed events."""
        if event.completed:
            self.log_message_signal.emit("Incubation completed")
        else:
            self.log_message_signal.emit("Incubation aborted")

        self.operation_progress_bar.setValue(100)
        self.lbl_time_remaining.setText("0:00")

    def _on_status_changed(self, event: FluidicsStatusChanged) -> None:
        """Handle status changed events."""
        if event.error_message:
            self.log_message_signal.emit(f"Error: {event.error_message}")

        # Update status LED based on status
        self._update_status_led(event.status)

        # Update state label
        state_labels = {
            "idle": "IDLE",
            "running": "RUNNING",
            "aspirating": "ASPIRATING",
            "dispensing": "DISPENSING",
            "valve_switching": "SWITCHING",
            "incubating": "INCUBATING",
            "completed": "COMPLETED",
            "error": "ERROR",
            "aborted": "ABORTED",
        }
        self.lbl_state.setText(state_labels.get(event.status, event.status.upper()))

        # Update port/solution display
        if event.current_port is not None:
            self.lbl_current_port.setText(str(event.current_port))
            self.lbl_current_solution.setText(f"({event.current_solution or 'unknown'})")

        if event.syringe_volume_ul is not None:
            self.syringe_gauge.setValue(int(event.syringe_volume_ul))

        # Enable/disable controls based on busy state
        self._enable_controls(not event.is_busy)

    def _update_status_led(self, status: str) -> None:
        """Update the status LED color based on current status."""
        status_colors = {
            "idle": "#27ae60",  # Green
            "running": "#f1c40f",  # Yellow
            "aspirating": "#3498db",  # Blue
            "dispensing": "#f39c12",  # Orange
            "valve_switching": "#9b59b6",  # Purple
            "incubating": "#3498db",  # Blue
            "completed": "#27ae60",  # Green
            "error": "#e74c3c",  # Red
            "aborted": "#e74c3c",  # Red
        }
        color = status_colors.get(status, "gray")
        self.status_led.setStyleSheet(f"color: {color}; font-size: 20px;")

    # ─────────────────────────────────────────────────────────────────────────
    # Sequence Event Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_sequence_started(self, event: FluidicsSequenceStarted) -> None:
        """Handle sequence started event."""
        self._is_sequence_running = True
        self._sequence_name = event.sequence_name
        self._sequence_current_step = 0
        self._sequence_total_steps = event.total_steps

        # Update UI
        self.lbl_sequence_name.setText(event.sequence_name)
        self.lbl_step_counter.setText(f"0 of {event.total_steps}")
        self.sequence_progress_bar.setValue(0)
        self.lbl_current_step.setText("Starting...")
        self.lbl_next_step.setText("--")

        # Enable skip button during sequence execution
        self.btn_skip_step.setEnabled(True)

        self.log_message_signal.emit(
            f"Starting sequence '{event.sequence_name}' with {event.total_steps} steps"
        )

    def _on_sequence_step_started(self, event: FluidicsSequenceStepStarted) -> None:
        """Handle sequence step started event."""
        self._sequence_current_step = event.step_index + 1  # Convert to 1-based for display

        # Update step counter
        self.lbl_step_counter.setText(f"{self._sequence_current_step} of {event.total_steps}")

        # Update progress bar
        if event.total_steps > 0:
            progress = int((event.step_index / event.total_steps) * 100)
            self.sequence_progress_bar.setValue(progress)

        # Highlight current step in tree view
        self._highlight_current_step(event.step_index)

        # Update step descriptions
        self.lbl_current_step.setText(event.step_description)
        self.lbl_next_step.setText(event.next_step_description or "End of sequence")

        self.log_message_signal.emit(
            f"Step {self._sequence_current_step}/{event.total_steps}: {event.step_description}"
        )

    def _on_sequence_completed(self, event: FluidicsSequenceCompleted) -> None:
        """Handle sequence completed event."""
        self._is_sequence_running = False

        # Disable skip button
        self.btn_skip_step.setEnabled(False)

        # Clear step highlighting in tree
        self._clear_step_highlights()

        # Update progress to 100% if successful
        if event.success:
            self.sequence_progress_bar.setValue(100)
            self.lbl_step_counter.setText(f"{event.steps_completed} of {event.total_steps}")
            self.lbl_current_step.setText("Sequence completed")
            self.log_message_signal.emit(
                f"Sequence '{event.sequence_name}' completed successfully"
            )
        else:
            self.lbl_current_step.setText("Sequence aborted")
            self.log_message_signal.emit(
                f"Sequence '{event.sequence_name}' aborted at step {event.steps_completed}/{event.total_steps}"
            )

        self.lbl_next_step.setText("--")

    # ─────────────────────────────────────────────────────────────────────────
    # User Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _load_sequences(self) -> None:
        """Open file dialog to load sequences from CSV."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Fluidics Sequences", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self._log_status(f"Loading sequences from {file_path}")
            try:
                self._sequence_df = pd.read_csv(file_path)
                # Remove 'include' column if present
                if "include" in self._sequence_df.columns:
                    self._sequence_df.drop("include", axis=1, inplace=True)

                # Populate protocols list
                self._populate_protocols_list()
                self._log_status(f"Loaded {len(self._sequence_df)} steps")

                # Store sequences on service for orchestrator access
                if self._service is not None:
                    self._service.set_sequences(self._sequence_df)

            except Exception as e:
                self._log_status(f"Error loading sequences: {str(e)}")
                self._log.exception(f"Error loading sequences: {e}")

    def _populate_protocols_list(self) -> None:
        """Populate the protocols list from loaded sequence data."""
        self.protocols_list.clear()
        self.steps_table.setRowCount(0)
        self._protocol_groups: dict[str, pd.DataFrame] = {}

        if self._sequence_df is None or len(self._sequence_df) == 0:
            return

        # Check if there's a 'protocol' or 'sequence' column for grouping
        group_col = None
        for col in ["protocol", "sequence", "group", "name"]:
            if col in self._sequence_df.columns:
                group_col = col
                break

        if group_col:
            # Group steps by protocol
            for protocol_name, group in self._sequence_df.groupby(group_col):
                name = str(protocol_name)
                self._protocol_groups[name] = group
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                self.protocols_list.addItem(item)
        else:
            # No grouping column - create a single "All Steps" protocol
            self._protocol_groups["All Steps"] = self._sequence_df
            item = QListWidgetItem("All Steps")
            item.setData(Qt.UserRole, "All Steps")
            self.protocols_list.addItem(item)

        # Select first protocol
        if self.protocols_list.count() > 0:
            self.protocols_list.setCurrentRow(0)

    def _on_protocol_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        """Handle protocol selection - show steps for selected protocol."""
        if current is None:
            self.steps_table.setRowCount(0)
            return

        protocol_name = current.data(Qt.UserRole)
        if protocol_name not in self._protocol_groups:
            return

        group = self._protocol_groups[protocol_name]
        self._populate_steps_table(group)

    def _populate_steps_table(self, group: pd.DataFrame) -> None:
        """Populate the steps table with steps from a protocol group."""
        self.steps_table.setRowCount(len(group))

        # Default text color for table items (light gray for dark theme compatibility)
        default_text_color = QColor("#e0e0e0")

        for row_num, (idx, row) in enumerate(group.iterrows()):
            # Column 0: Step number
            step_item = QTableWidgetItem(str(row_num + 1))
            step_item.setData(Qt.UserRole, idx)  # Store DataFrame index
            step_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 0, step_item)

            # Column 1: Operation
            operation = ""
            if "operation" in row.index and pd.notna(row["operation"]):
                operation = str(row["operation"])
            op_item = QTableWidgetItem(operation)
            op_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 1, op_item)

            # Column 2: Solution
            solution = ""
            if "solution" in row.index and pd.notna(row["solution"]):
                solution = str(row["solution"])
            sol_item = QTableWidgetItem(solution)
            sol_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 2, sol_item)

            # Column 3: Volume
            volume = ""
            if "volume_ul" in row.index and pd.notna(row["volume_ul"]):
                volume = f"{row['volume_ul']} uL"
            vol_item = QTableWidgetItem(volume)
            vol_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 3, vol_item)

            # Column 4: Incubation
            incubation = ""
            if "incubation_time_s" in row.index and pd.notna(row["incubation_time_s"]):
                time_s = row["incubation_time_s"]
                if time_s > 0:
                    if time_s >= 60:
                        incubation = f"{time_s / 60:.1f} min"
                    else:
                        incubation = f"{time_s} s"
            inc_item = QTableWidgetItem(incubation)
            inc_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 4, inc_item)

    def _highlight_current_step(self, step_index: int) -> None:
        """Highlight the currently executing step in the steps table."""
        # Default text color for non-highlighted rows (light gray for dark theme)
        default_text_color = QColor("#e0e0e0")
        # Transparent background to use table's alternating row colors
        default_bg_color = QColor(0, 0, 0, 0)

        for row in range(self.steps_table.rowCount()):
            for col in range(self.steps_table.columnCount()):
                item = self.steps_table.item(row, col)
                if item:
                    if row == step_index:
                        # Highlight current step
                        item.setBackground(QColor("#3498db"))
                        item.setForeground(QColor("white"))
                    else:
                        # Reset to default colors
                        item.setBackground(default_bg_color)
                        item.setForeground(default_text_color)

        # Scroll to make current step visible
        if step_index < self.steps_table.rowCount():
            self.steps_table.scrollToItem(self.steps_table.item(step_index, 0))

    def _clear_step_highlights(self) -> None:
        """Clear all step highlights in the steps table."""
        # Default text color (light gray for dark theme compatibility)
        default_text_color = QColor("#e0e0e0")
        # Transparent background to use table's alternating row colors
        default_bg_color = QColor(0, 0, 0, 0)

        for row in range(self.steps_table.rowCount()):
            for col in range(self.steps_table.columnCount()):
                item = self.steps_table.item(row, col)
                if item:
                    item.setBackground(default_bg_color)
                    item.setForeground(default_text_color)

    def _start_prime(self) -> None:
        """Start priming operation."""
        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        ports = self._get_port_list(self.txt_prime_ports.text())
        fill_solution = self.prime_fill_combo.currentText()

        try:
            volume = float(self.txt_prime_volume.text())
            flow_rate = float(self.txt_prime_flow_rate.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Volume and flow rate must be numbers")
            return

        if not ports:
            return

        # Get final port (port to fill tubing with)
        # Note: _is_available guarantees _service is not None
        assert self._service is not None
        final_port = self._service.get_port_for_solution(fill_solution)
        if final_port is None:
            QMessageBox.warning(self, "Invalid Input", f"Solution '{fill_solution}' not found")
            return

        self._log_status(
            f"Starting prime: Ports {ports}, Fill with {fill_solution}, "
            f"Volume {volume} uL, Flow rate {flow_rate} uL/min"
        )

        # Run in thread to avoid blocking UI
        def do_prime():
            try:
                # Re-check since we're in a different thread
                if self._service is None:
                    return
                self._service.prime(
                    ports=ports,
                    volume_ul=volume,
                    flow_rate_ul_per_min=flow_rate,
                    final_port=final_port,
                )
            except Exception as e:
                self.log_message_signal.emit(f"Prime error: {e}")

        threading.Thread(target=do_prime, daemon=True).start()

    def _start_wash(self) -> None:
        """Start wash operation."""
        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        wash_solution = self.cleanup_solution_combo.currentText()

        try:
            volume = float(self.txt_cleanup_volume.text())
            flow_rate = float(self.txt_cleanup_flow_rate.text())
            repeats = int(self.txt_cleanup_repeat.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Volume, flow rate, and repeats must be numbers")
            return

        self._log_status(
            f"Starting wash: Solution {wash_solution}, Volume {volume} uL, "
            f"Flow rate {flow_rate} uL/min, Repeat {repeats}x"
        )

        # Run in thread to avoid blocking UI
        def do_wash():
            try:
                if self._service is None:
                    return
                self._service.wash(
                    wash_solution=wash_solution,
                    volume_ul=volume,
                    flow_rate_ul_per_min=flow_rate,
                    repeats=repeats,
                )
            except Exception as e:
                self.log_message_signal.emit(f"Wash error: {e}")

        threading.Thread(target=do_wash, daemon=True).start()

    def _start_manual_flow(self) -> None:
        """Start manual flow operation."""
        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        solution = self.manual_solution_combo.currentText()

        try:
            flow_rate = float(self.txt_manual_flow_rate.text())
            volume = float(self.txt_manual_volume.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Flow rate and volume must be numbers")
            return

        if not solution:
            QMessageBox.warning(self, "Invalid Input", "Please select a solution")
            return

        self._log_status(
            f"Flow solution: {solution}, Flow rate {flow_rate} uL/min, Volume {volume} uL"
        )

        # Run in thread to avoid blocking UI
        def do_flow():
            try:
                if self._service is None:
                    return
                self._service.flow_solution_by_name(
                    solution_name=solution,
                    volume_ul=volume,
                    flow_rate_ul_per_min=flow_rate,
                )
            except Exception as e:
                self.log_message_signal.emit(f"Flow error: {e}")

        threading.Thread(target=do_flow, daemon=True).start()

    def _empty_syringe_pump(self) -> None:
        """Empty syringe pump to waste."""
        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        # Check if syringe has any volume to empty
        current_vol = self.syringe_gauge.value()
        if current_vol <= 0:
            self._log_status("Syringe already empty (0 µL)")
            return

        self._log_status(f"Emptying syringe pump ({current_vol} µL) to waste")

        # Run in thread to avoid blocking UI
        def do_empty():
            try:
                if self._service is None:
                    return
                self._service.empty_syringe()
            except Exception as e:
                self.log_message_signal.emit(f"Empty syringe error: {e}")

        threading.Thread(target=do_empty, daemon=True).start()

    def _emergency_stop(self) -> None:
        """Emergency stop all fluidics operations."""
        self._log_status("EMERGENCY STOP")
        if self._service is not None:
            self._service.abort()
        # Also stop any sequence in progress
        self._is_sequence_running = False
        self._skip_requested = False

        # Reset progress UI
        self._reset_progress_ui()

    def _skip_current_step(self) -> None:
        """Skip to the next step in the sequence."""
        if not self._is_sequence_running:
            self._log_status("No sequence running to skip")
            return

        current_step = self._sequence_current_step
        total_steps = self._sequence_total_steps
        self._log.info(f"Skip button clicked for step {current_step}/{total_steps}")
        self._log_status(f"Skip requested (step {current_step}/{total_steps})")

        # Set skip flag BEFORE calling abort
        self._skip_requested = True
        self._empty_on_skip = self.chk_empty_on_skip.isChecked()
        self._log.debug(f"Skip flags set: _skip_requested=True, _empty_on_skip={self._empty_on_skip}")

        # Abort current operation to unblock the background thread
        if self._service is not None:
            try:
                self._service.abort()
                self._log_status("Abort signal sent")
                self._log.debug("Abort signal sent to service")
            except Exception as e:
                self._log.warning(f"Error calling abort: {e}")
                self._log_status(f"Warning: abort error: {e}")

    def _reset_progress_ui(self) -> None:
        """Reset the progress UI to initial state."""
        # Clear step highlighting
        self._clear_step_highlights()

        # Reset progress panel
        self.lbl_sequence_name.setText("--")
        self.lbl_step_counter.setText("0 of 0")
        self.sequence_progress_bar.setValue(0)
        self.lbl_current_step.setText("--")
        self.lbl_next_step.setText("--")

        # Disable skip button
        self.btn_skip_step.setEnabled(False)

    def _execute_selected_sequence(self) -> None:
        """Execute the currently selected protocol."""
        self._log.info("_execute_selected_sequence called")
        self._log_status("Run Protocol clicked")

        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        if self._sequence_df is None or len(self._sequence_df) == 0:
            QMessageBox.warning(self, "No Sequences", "Please load sequences first")
            return

        # Get selected protocol
        current_item = self.protocols_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "No Selection", "Please select a protocol to run")
            return

        protocol_name = current_item.data(Qt.UserRole)
        if protocol_name not in self._protocol_groups:
            self._log.warning(f"Protocol '{protocol_name}' not found in groups: {list(self._protocol_groups.keys())}")
            QMessageBox.warning(self, "Error", f"Protocol '{protocol_name}' not found")
            return

        # Get row indices for this protocol
        group = self._protocol_groups[protocol_name]
        row_indexes = list(group.index)

        self._log_status(f"Executing protocol: {protocol_name} ({len(row_indexes)} steps)")
        self._execute_sequence_rows(row_indexes, protocol_name)

    def _execute_all_sequences(self) -> None:
        """Execute all loaded sequences in order."""
        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        if self._sequence_df is None or len(self._sequence_df) == 0:
            QMessageBox.warning(self, "No Sequences", "Please load sequences first")
            return

        row_indexes = list(range(len(self._sequence_df)))
        self._execute_sequence_rows(row_indexes, "All Protocols")

    def _execute_sequence_rows(self, row_indexes: list[int], sequence_name: Optional[str] = None) -> None:
        """Execute a list of sequence rows by index.

        This is the core sequence execution method that publishes
        sequence progress events for UI tracking.
        """
        if self._sequence_df is None or self._service is None:
            return

        # Extract sequence steps
        df = self._sequence_df.iloc[row_indexes].copy()
        total_steps = len(df)

        if total_steps == 0:
            self._log_status("No steps to execute")
            return

        # Generate sequence name if not provided
        if sequence_name is None:
            if len(row_indexes) == 1:
                sequence_name = f"Step {row_indexes[0] + 1}"
            elif len(row_indexes) == len(self._sequence_df):
                sequence_name = "All sequences"
            else:
                sequence_name = f"Steps {row_indexes[0] + 1}-{row_indexes[-1] + 1}"

        # Run in background thread
        def do_execute():
            try:
                if self._service is None or self._event_bus is None:
                    return

                # Publish sequence started
                self._event_bus.publish(FluidicsSequenceStarted(
                    sequence_name=sequence_name,
                    total_steps=total_steps,
                ))

                steps_completed = 0

                for i, (idx, row) in enumerate(df.iterrows()):
                    self._log.debug(f"Loop iteration {i + 1}/{total_steps}: _is_sequence_running={self._is_sequence_running}, _skip_requested={self._skip_requested}")

                    if not self._is_sequence_running:
                        # Aborted via emergency stop
                        self._log.info(f"Sequence aborted: _is_sequence_running=False at step {i + 1}")
                        break

                    # Build step description
                    step_desc = self._build_step_description(row)
                    next_desc = None
                    if i + 1 < total_steps:
                        next_row = df.iloc[i + 1]
                        next_desc = self._build_step_description(next_row)

                    # Publish step started
                    self._event_bus.publish(FluidicsSequenceStepStarted(
                        step_index=i,
                        total_steps=total_steps,
                        step_description=step_desc,
                        next_step_description=next_desc,
                    ))

                    # Execute the step
                    success = self._execute_single_step(row)
                    self._log.debug(f"Step {i + 1} execution returned: success={success}, skip_requested={self._skip_requested}")

                    # Check if skip was requested (abort called during step)
                    if self._skip_requested:
                        self._log.info(f"Skip handler triggered for step {i + 1}")
                        self._skip_requested = False
                        self.log_message_signal.emit(f"Skipping step {i + 1}")

                        # Reset abort flag FIRST so subsequent operations work
                        try:
                            if self._service is not None:
                                self._service.reset_abort()
                                self._log.debug("Abort flag reset successfully")
                        except Exception as e:
                            self._log.warning(f"Error resetting abort: {e}")
                            self.log_message_signal.emit(f"Warning: error resetting abort: {e}")

                        # Empty syringe if requested
                        if self._empty_on_skip and self._service is not None:
                            self.log_message_signal.emit("Emptying syringe before skip...")
                            try:
                                self._service.empty_syringe()
                                self._log.debug("Empty syringe completed")
                            except Exception as e:
                                self._log.warning(f"Empty syringe error: {e}")
                                self.log_message_signal.emit(f"Empty syringe error: {e}")

                        steps_completed = i + 1
                        self._log.info(f"Continuing to step {i + 2} after skip")
                        continue  # Skip to next step

                    # Only check success if skip was NOT requested
                    # (abort during step returns False but we still want to continue)
                    if not success:
                        self.log_message_signal.emit(f"Step {i + 1} failed, aborting sequence")
                        self._log.warning(f"Step {i + 1} failed with success=False, breaking loop")
                        break

                    steps_completed = i + 1
                    self._log.debug(f"Step {i + 1} completed normally")

                # Publish sequence completed
                self._event_bus.publish(FluidicsSequenceCompleted(
                    sequence_name=sequence_name,
                    success=steps_completed == total_steps and self._is_sequence_running,
                    steps_completed=steps_completed,
                    total_steps=total_steps,
                ))

            except Exception as e:
                self.log_message_signal.emit(f"Sequence execution error: {e}")
                if self._event_bus is not None:
                    self._event_bus.publish(FluidicsSequenceCompleted(
                        sequence_name=sequence_name,
                        success=False,
                        steps_completed=0,
                        total_steps=total_steps,
                    ))

        self._is_sequence_running = True
        threading.Thread(target=do_execute, daemon=True).start()

    def _build_step_description(self, row: pd.Series) -> str:
        """Build a human-readable description for a sequence step."""
        parts = []

        # Check for operation type
        if "operation" in row.index:
            val = row["operation"]
            if pd.notna(val):
                parts.append(str(val).capitalize())

        # Add solution info
        if "solution" in row.index:
            val = row["solution"]
            if pd.notna(val):
                parts.append(str(val))

        # Add volume info
        if "volume_ul" in row.index:
            val = row["volume_ul"]
            if pd.notna(val):
                parts.append(f"{val} uL")

        # Add flow rate info
        if "flow_rate_ul_per_min" in row.index:
            val = row["flow_rate_ul_per_min"]
            if pd.notna(val):
                parts.append(f"@ {val} uL/min")

        # Add incubation time
        if "incubation_time_s" in row.index:
            val = row["incubation_time_s"]
            if pd.notna(val) and float(val) > 0:
                parts.append(f"(incubate {val}s)")

        return " ".join(parts) if parts else "Unknown step"

    def _execute_single_step(self, row: pd.Series) -> bool:
        """Execute a single sequence step.

        Returns True if successful, False if failed or aborted.
        Note: When skip is requested, the loop should check _skip_requested
        flag rather than relying solely on this return value.
        """
        if self._service is None:
            return False

        try:
            # Get step parameters with safe defaults
            solution = ""
            if "solution" in row.index and pd.notna(row["solution"]):
                solution = str(row["solution"])

            volume_ul = 0.0
            if "volume_ul" in row.index and pd.notna(row["volume_ul"]):
                volume_ul = float(row["volume_ul"])

            flow_rate = 500.0
            if "flow_rate_ul_per_min" in row.index and pd.notna(row["flow_rate_ul_per_min"]):
                flow_rate = float(row["flow_rate_ul_per_min"])

            incubation_time_s = 0.0
            if "incubation_time_s" in row.index and pd.notna(row["incubation_time_s"]):
                incubation_time_s = float(row["incubation_time_s"])

            # Execute flow if volume specified
            if volume_ul > 0 and solution:
                self._log.debug(f"Calling flow_solution_by_name: {solution} {volume_ul}ul @ {flow_rate}ul/min")
                try:
                    success = self._service.flow_solution_by_name(
                        solution_name=solution,
                        volume_ul=volume_ul,
                        flow_rate_ul_per_min=flow_rate,
                    )
                except Exception as flow_err:
                    self._log.warning(f"Flow operation raised exception: {flow_err}")
                    self.log_message_signal.emit(f"Flow error: {flow_err}")
                    return False
                if not success:
                    self._log.debug("Flow operation returned False (possibly aborted)")
                    return False

            # Execute incubation if specified
            if incubation_time_s > 0:
                success = self._service.incubate(
                    duration_seconds=incubation_time_s,
                    solution=solution if solution else None,
                )
                if not success:
                    self._log.debug("Incubation returned False (possibly aborted)")
                    return False

            return True

        except Exception as e:
            self._log.exception(f"Error executing step: {e}")
            # Also emit to UI log so user can see the error
            self.log_message_signal.emit(f"Step error: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_port_list(self, text: str) -> list[int]:
        """Parse ports input string into a list of port numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"
        - Empty string returns all available ports

        Returns:
            List of port numbers, or empty list if invalid.
        """
        if not self._is_available:
            return []

        # Type narrowing: _is_available guarantees _service is not None
        assert self._service is not None

        try:
            ports_str = text.strip()
            if not ports_str:
                # Return all available ports
                return self._service.get_available_ports()

            port_list: list[int] = []
            available_ports = self._service.get_available_ports()
            max_port = max(available_ports) if available_ports else 28

            # Split by comma and process each part
            for part in ports_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > max_port or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and {max_port}, "
                            f"and start must be <= end"
                        )
                    port_list.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > max_port:
                        raise ValueError(
                            f"Invalid number {num}: Must be between 1 and {max_port}"
                        )
                    port_list.append(num)

            return port_list

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Please enter valid port numbers (e.g., '1-3,5,7-10')",
            )
            return []

    def _enable_controls(self, enabled: bool) -> None:
        """Enable or disable control buttons based on service availability."""
        # Only enable if service is available AND we want them enabled
        actually_enabled = enabled and self._is_available

        self.btn_load_sequences.setEnabled(enabled)  # Always allow loading sequences
        self.btn_prime_start.setEnabled(actually_enabled)
        self.btn_cleanup_start.setEnabled(actually_enabled)
        self.btn_manual_flow.setEnabled(actually_enabled)
        self.btn_empty_syringe_pump.setEnabled(actually_enabled)
        self.btn_execute_selected.setEnabled(actually_enabled)
        self.btn_execute_all.setEnabled(actually_enabled)

    def _log_status(self, message: str) -> None:
        """Log a status message to the status text area."""
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.status_text.append(f"[{current_time}] {message}")
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
        # Also log to console
        self._log.info(message)
