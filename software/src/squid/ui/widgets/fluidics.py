# Fluidics control widgets
from __future__ import annotations

import html
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

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
from squid.core.events import (
    RunFluidicsProtocolCommand,
    LoadFluidicsProtocolsCommand,
    StopFluidicsCommand,
    SkipFluidicsStepCommand,
    FluidicsOperationStarted,
    FluidicsOperationCompleted,
    FluidicsOperationProgress,
    FluidicsPhaseChanged,
    FluidicsIncubationStarted,
    FluidicsIncubationProgress,
    FluidicsIncubationCompleted,
    FluidicsStatusChanged,
    FluidicsControllerStateChanged,
    FluidicsProtocolStarted,
    FluidicsProtocolStepStarted,
    FluidicsProtocolCompleted,
    FluidicsProtocolsLoaded,
    FluidicsProtocolsLoadFailed,
)
from squid.core.protocol import FluidicsProtocol, FluidicsProtocolFile

if TYPE_CHECKING:
    from squid.backend.services.fluidics_service import FluidicsService
    from squid.backend.services import ServiceRegistry
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
        service_registry: Optional["ServiceRegistry"] = None,
        is_simulation: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._service = fluidics_service
        self._event_bus = event_bus
        self._service_registry = service_registry
        self._is_simulation = is_simulation

        # Protocol data (loaded from YAML)
        self._protocols: dict[str, FluidicsProtocol] = {}
        self._protocols_path: Optional[str] = None
        self._available_solutions: list[str] = []
        self._config_path: Optional[str] = None

        # Operation tracking
        self._operation_start_time: Optional[QDateTime] = None
        self._operation_est_duration: Optional[float] = None

        # Protocol execution tracking
        self._is_protocol_running: bool = False
        self._protocol_name: str = ""
        self._protocol_current_step: int = 0
        self._protocol_total_steps: int = 0

        # Get available solutions from service if available
        self._refresh_solutions()

        # Set up the UI
        self._setup_ui()
        self.log_message_signal.connect(self._log_status)

        # Subscribe to events for status updates
        self._subscribe_to_events()

        # Update UI state based on service availability
        self._update_service_status()
        self._load_protocols_from_config()

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
            # Protocol execution events
            self._event_bus.subscribe(
                FluidicsControllerStateChanged, self._on_controller_state_changed
            )
            self._event_bus.subscribe(FluidicsProtocolStarted, self._on_protocol_started)
            self._event_bus.subscribe(
                FluidicsProtocolStepStarted, self._on_protocol_step_started
            )
            self._event_bus.subscribe(
                FluidicsProtocolCompleted, self._on_protocol_completed
            )
            self._event_bus.subscribe(FluidicsProtocolsLoaded, self._on_protocols_loaded)
            self._event_bus.subscribe(
                FluidicsProtocolsLoadFailed, self._on_protocols_load_failed
            )

    def _setup_ui(self) -> None:
        """Set up the widget UI."""
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)
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
        """Set up the configuration panel with hardware config and protocol loading."""
        config_group = QGroupBox("Configuration")
        config_outer = QVBoxLayout()
        config_outer.setContentsMargins(4, 4, 4, 4)
        config_outer.setSpacing(4)

        # Row 1: Hardware config
        hw_row = QHBoxLayout()
        hw_row.setSpacing(8)

        self.lbl_status = QLabel("Status: Not configured")
        self.lbl_status.setStyleSheet("font-weight: bold;")
        hw_row.addWidget(self.lbl_status)

        hw_row.addStretch()

        hw_row.addWidget(QLabel("Config:"))
        self.txt_config_path = QLineEdit()
        self.txt_config_path.setReadOnly(True)
        self.txt_config_path.setMinimumWidth(250)
        self.txt_config_path.setPlaceholderText("No config file selected")
        hw_row.addWidget(self.txt_config_path)

        self.btn_browse_config = QPushButton("Browse...")
        self.btn_browse_config.clicked.connect(self._browse_config)
        hw_row.addWidget(self.btn_browse_config)

        self.btn_initialize = QPushButton("Initialize")
        self.btn_initialize.clicked.connect(self._initialize_fluidics)
        hw_row.addWidget(self.btn_initialize)

        config_outer.addLayout(hw_row)

        # Row 2: Protocol loading
        proto_row = QHBoxLayout()
        proto_row.setSpacing(8)

        proto_row.addWidget(QLabel("Protocols:"))
        self.lbl_protocols_path = QLabel("No protocols loaded")
        self.lbl_protocols_path.setStyleSheet("color: #888;")
        proto_row.addWidget(self.lbl_protocols_path)

        proto_row.addStretch()

        self.btn_load_sequences = QPushButton("Load Protocols...")
        self.btn_load_sequences.setMinimumWidth(120)
        self.btn_load_sequences.clicked.connect(self._load_protocols)
        proto_row.addWidget(self.btn_load_sequences)

        config_outer.addLayout(proto_row)

        config_group.setLayout(config_outer)
        parent_layout.addWidget(config_group)

    def _setup_status_dashboard(self, parent_layout: QVBoxLayout) -> None:
        """Set up the real-time status dashboard panel."""
        self.status_dashboard_group = QGroupBox("System Status")
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setContentsMargins(6, 2, 6, 2)
        dashboard_layout.setSpacing(8)

        # State indicator (LED + label)
        self.status_led = QLabel("●")
        self.status_led.setStyleSheet("color: gray; font-size: 18px;")
        dashboard_layout.addWidget(self.status_led)

        self.lbl_state = QLabel("IDLE")
        self.lbl_state.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.lbl_state.setMinimumWidth(80)
        dashboard_layout.addWidget(self.lbl_state)

        # Vertical separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setFrameShadow(QFrame.Sunken)
        dashboard_layout.addWidget(sep1)

        # Port/Solution display
        port_label = QLabel("Port:")
        port_label.setStyleSheet("color: #888;")
        dashboard_layout.addWidget(port_label)

        self.lbl_current_port = QLabel("--")
        self.lbl_current_port.setStyleSheet("font-weight: bold; font-size: 13px;")
        dashboard_layout.addWidget(self.lbl_current_port)

        self.lbl_current_solution = QLabel("(none)")
        self.lbl_current_solution.setStyleSheet("color: #888;")
        dashboard_layout.addWidget(self.lbl_current_solution)

        # Vertical separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        dashboard_layout.addWidget(sep2)

        # Syringe volume gauge - prominent, stretches to fill
        syringe_label = QLabel("Syringe:")
        syringe_label.setStyleSheet("color: #888;")
        dashboard_layout.addWidget(syringe_label)

        self.syringe_gauge = QProgressBar()
        self.syringe_gauge.setMinimum(0)
        self.syringe_gauge.setMaximum(5000)
        self.syringe_gauge.setValue(0)
        self.syringe_gauge.setFormat("%v / %m uL")
        self.syringe_gauge.setMinimumWidth(200)
        self.syringe_gauge.setFixedHeight(22)
        self.syringe_gauge.setStyleSheet(
            "QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; "
            "font-weight: bold; font-size: 12px; background-color: #2a2a2a; }"
            "QProgressBar::chunk { background-color: #3498db; border-radius: 2px; }"
        )
        dashboard_layout.addWidget(self.syringe_gauge, 1)  # stretch factor

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
        progress_layout.setContentsMargins(6, 4, 6, 4)
        progress_layout.setSpacing(2)

        # Operation description row
        op_row = QHBoxLayout()
        op_row.setSpacing(4)
        self.lbl_operation = QLabel("Idle")
        self.lbl_operation.setStyleSheet("font-weight: bold;")
        op_row.addWidget(self.lbl_operation)
        op_row.addStretch()
        self.lbl_flow_details = QLabel("--")
        self.lbl_flow_details.setStyleSheet("color: #aaa;")
        op_row.addWidget(self.lbl_flow_details)
        progress_layout.addLayout(op_row)

        # Progress bar row
        bar_row = QHBoxLayout()
        bar_row.setSpacing(4)
        self.operation_progress_bar = QProgressBar()
        self.operation_progress_bar.setMinimum(0)
        self.operation_progress_bar.setMaximum(100)
        self.operation_progress_bar.setValue(0)
        self.operation_progress_bar.setFixedHeight(18)
        bar_row.addWidget(self.operation_progress_bar, 1)

        self.lbl_time_remaining = QLabel("--:--")
        self.lbl_time_remaining.setStyleSheet("font-weight: bold;")
        self.lbl_time_remaining.setMinimumWidth(50)
        bar_row.addWidget(self.lbl_time_remaining)
        progress_layout.addLayout(bar_row)

        self.progress_group.setLayout(progress_layout)
        parent_layout.addWidget(self.progress_group, 1)  # Stretch factor 1 for equal sizing

    def _setup_sequence_progress_panel(self, parent_layout: QHBoxLayout) -> None:
        """Set up the protocol progress tracking panel (always visible)."""
        self.sequence_progress_group = QGroupBox("Protocol Progress")
        seq_layout = QVBoxLayout()
        seq_layout.setContentsMargins(6, 4, 6, 4)
        seq_layout.setSpacing(2)

        # Header: protocol name + step counter
        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        self.lbl_sequence_name = QLabel("--")
        self.lbl_sequence_name.setStyleSheet("font-weight: bold;")
        header_row.addWidget(self.lbl_sequence_name)
        header_row.addStretch()
        self.lbl_step_counter = QLabel("0 of 0")
        self.lbl_step_counter.setStyleSheet("font-weight: bold;")
        header_row.addWidget(self.lbl_step_counter)
        seq_layout.addLayout(header_row)

        # Progress bar
        self.sequence_progress_bar = QProgressBar()
        self.sequence_progress_bar.setMinimum(0)
        self.sequence_progress_bar.setMaximum(100)
        self.sequence_progress_bar.setValue(0)
        self.sequence_progress_bar.setFormat("%p%")
        self.sequence_progress_bar.setFixedHeight(18)
        seq_layout.addWidget(self.sequence_progress_bar)

        # Current/next step in compact form
        steps_grid = QGridLayout()
        steps_grid.setHorizontalSpacing(4)
        steps_grid.setVerticalSpacing(0)

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

        # Prime Ports panel (flat group box)
        prime_group = QGroupBox("Prime Ports")
        prime_layout = QGridLayout()
        prime_layout.setHorizontalSpacing(8)
        prime_layout.setVerticalSpacing(4)

        # Row 0: Ports
        prime_layout.addWidget(QLabel("Ports:"), 0, 0)
        self.txt_prime_ports = QLineEdit()
        self.txt_prime_ports.setPlaceholderText("e.g., 1-5, 23-24 or all")
        self.txt_prime_ports.setFixedWidth(FIELD_WIDTH)
        self.txt_prime_ports.textChanged.connect(self._on_prime_ports_changed)
        prime_layout.addWidget(self.txt_prime_ports, 0, 1)

        # Port info label (shows parsed ports with solution names)
        self.lbl_prime_port_info = QLabel("")
        self.lbl_prime_port_info.setStyleSheet("color: #888; font-size: 11px;")
        self.lbl_prime_port_info.setWordWrap(True)
        prime_layout.addWidget(self.lbl_prime_port_info, 0, 2, 1, 3)

        # Row 1: Volume, Flow rate, Start button
        prime_layout.addWidget(QLabel("Volume (uL):"), 1, 0)
        self.txt_prime_volume = QLineEdit()
        self.txt_prime_volume.setText("2000")
        self.txt_prime_volume.setFixedWidth(FIELD_WIDTH)
        prime_layout.addWidget(self.txt_prime_volume, 1, 1)

        prime_layout.addWidget(QLabel("Flow rate (uL/min):"), 1, 2)
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

        # Manual Flow panel (flat group box)
        manual_group = QGroupBox("Manual Flow")
        manual_layout = QGridLayout()
        manual_layout.setHorizontalSpacing(8)
        manual_layout.setVerticalSpacing(4)

        # Row 0: Solution, Port info, Volume
        manual_layout.addWidget(QLabel("Solution:"), 0, 0)
        self.manual_solution_combo = QComboBox()
        self.manual_solution_combo.setMinimumWidth(COMBO_MIN_WIDTH)
        self.manual_solution_combo.addItems(self._available_solutions)
        self.manual_solution_combo.currentTextChanged.connect(self._on_manual_solution_changed)
        manual_layout.addWidget(self.manual_solution_combo, 0, 1)

        self.lbl_manual_port = QLabel("")
        self.lbl_manual_port.setStyleSheet("color: #888;")
        manual_layout.addWidget(self.lbl_manual_port, 0, 2)

        manual_layout.addWidget(QLabel("Volume (uL):"), 0, 3)
        self.txt_manual_volume = QLineEdit()
        self.txt_manual_volume.setPlaceholderText("e.g., 500")
        self.txt_manual_volume.setFixedWidth(FIELD_WIDTH)
        manual_layout.addWidget(self.txt_manual_volume, 0, 4)

        # Row 1: Flow rate, Buttons
        manual_layout.addWidget(QLabel("Flow rate (uL/min):"), 1, 0)
        self.txt_manual_flow_rate = QLineEdit()
        self.txt_manual_flow_rate.setText("500")
        self.txt_manual_flow_rate.setFixedWidth(FIELD_WIDTH)
        manual_layout.addWidget(self.txt_manual_flow_rate, 1, 1)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_manual_flow = QPushButton("Flow")
        self.btn_manual_flow.setFixedWidth(80)
        btn_row.addWidget(self.btn_manual_flow)

        self.btn_empty_syringe_pump = QPushButton("Empty Syringe")
        self.btn_empty_syringe_pump.setFixedWidth(110)
        btn_row.addWidget(self.btn_empty_syringe_pump)
        btn_row.addStretch()
        manual_layout.addLayout(btn_row, 1, 2, 1, 3)

        manual_layout.setColumnStretch(5, 1)
        manual_group.setLayout(manual_layout)
        parent_layout.addWidget(manual_group)

        # Status log panel
        status_group = QGroupBox("Log")
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(4, 4, 4, 4)

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMinimumHeight(150)
        self.status_text.setStyleSheet(
            "font-family: 'Consolas', 'Courier New', monospace; font-size: 13px;"
        )
        status_layout.addWidget(self.status_text)

        self.btn_save_log = QPushButton("Save Log")
        status_layout.addWidget(self.btn_save_log)

        status_group.setLayout(status_layout)
        parent_layout.addWidget(status_group)

        # Connect signals
        self.btn_prime_start.clicked.connect(self._start_prime)
        self.btn_manual_flow.clicked.connect(self._start_manual_flow)
        self.btn_empty_syringe_pump.clicked.connect(self._empty_syringe_pump)
        self.btn_save_log.clicked.connect(self._save_log)

        # Initialize port info display
        self._on_manual_solution_changed(self.manual_solution_combo.currentText())

    def _setup_sequences_panel(self, parent_layout: QVBoxLayout) -> None:
        """Set up the sequences panel with protocols list and steps view."""
        sequences_group = QGroupBox("Protocols")
        sequences_layout = QVBoxLayout()
        sequences_layout.setContentsMargins(4, 4, 4, 4)
        sequences_layout.setSpacing(4)

        # Two-panel layout: protocols on left, steps on right
        splitter = QSplitter(Qt.Horizontal)

        # Left panel: Protocol list
        protocols_widget = QWidget()
        protocols_layout = QVBoxLayout(protocols_widget)
        protocols_layout.setContentsMargins(0, 0, 0, 0)
        protocols_layout.setSpacing(4)

        protocols_label = QLabel("Protocols")
        protocols_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        protocols_layout.addWidget(protocols_label)

        self.protocols_list = QListWidget()
        self.protocols_list.setAlternatingRowColors(True)
        self.protocols_list.setStyleSheet("font-size: 13px;")
        self.protocols_list.currentItemChanged.connect(self._on_protocol_selected)
        protocols_layout.addWidget(self.protocols_list)

        splitter.addWidget(protocols_widget)

        # Right panel: Steps table for selected protocol
        steps_widget = QWidget()
        steps_layout = QVBoxLayout(steps_widget)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(4)

        steps_header_layout = QHBoxLayout()
        steps_label = QLabel("Steps")
        steps_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        steps_header_layout.addWidget(steps_label)

        self.lbl_estimated_duration = QLabel("")
        self.lbl_estimated_duration.setStyleSheet("color: #888; font-style: italic;")
        steps_header_layout.addWidget(self.lbl_estimated_duration)
        steps_header_layout.addStretch()
        steps_layout.addLayout(steps_header_layout)

        self.steps_table = QTableWidget()
        self.steps_table.setStyleSheet("font-size: 13px;")
        self.steps_table.setColumnCount(6)
        self.steps_table.setHorizontalHeaderLabels(
            ["#", "Operation", "Solution", "Volume", "Rate", "Incubation"]
        )
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
            header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        steps_layout.addWidget(self.steps_table)

        splitter.addWidget(steps_widget)

        # Set initial splitter sizes (40% protocols, 60% steps)
        splitter.setSizes([150, 250])

        sequences_layout.addWidget(splitter, 1)

        # Execution buttons row
        exec_row = QHBoxLayout()
        exec_row.setSpacing(12)

        self.btn_execute_selected = QPushButton("Start Protocol")
        self.btn_execute_selected.setMinimumWidth(140)
        self.btn_execute_selected.setMinimumHeight(36)
        self.btn_execute_selected.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
            "QPushButton:pressed { background-color: #1e8449; }"
            "QPushButton:disabled { background-color: #555; color: #999; }"
        )
        self.btn_execute_selected.clicked.connect(self._run_selected_protocol)
        exec_row.addWidget(self.btn_execute_selected)

        # Emergency Stop button - prominent (adjacent to Start)
        self.btn_emergency_stop = QPushButton("STOP")
        self.btn_emergency_stop.setMinimumWidth(80)
        self.btn_emergency_stop.setMinimumHeight(36)
        self.btn_emergency_stop.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-weight: bold; "
            "font-size: 13px; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #e74c3c; }"
            "QPushButton:pressed { background-color: #a93226; }"
        )
        self.btn_emergency_stop.clicked.connect(self._emergency_stop)
        exec_row.addWidget(self.btn_emergency_stop)
        exec_row.addStretch()
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

            # Update syringe gauge max from actual hardware capacity
            assert self._service is not None
            capacity = self._service.get_syringe_capacity_ul()
            self.syringe_gauge.setMaximum(int(capacity))

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
            self.btn_initialize.setEnabled(True)
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
            import sys
            from squid.backend.drivers.fluidics import (
                MERFISHFluidicsDriver,
                SimulatedFluidicsController,
            )
            from squid.backend.services.fluidics_service import FluidicsService
            from squid.core.events import event_bus

            # Prefer registry event bus if available to keep UI in sync.
            core_bus = event_bus
            if self._service_registry is not None:
                core_bus = getattr(self._service_registry, "_event_bus", event_bus)

            driver = None
            if self._is_simulation:
                driver = SimulatedFluidicsController(
                    config_path=self._config_path,
                    simulate_timing=True,
                )
            else:
                # Ensure fluidics_v2 is importable for real hardware.
                software_dir = Path(__file__).parent.parent.parent.parent.parent
                fluidics_v2_path = software_dir / "fluidics_v2" / "software"
                if fluidics_v2_path.exists() and str(fluidics_v2_path) not in sys.path:
                    sys.path.insert(0, str(fluidics_v2_path))
                driver = MERFISHFluidicsDriver(
                    config_path=self._config_path,
                    simulation=False,
                )

            if driver is None or not driver.initialize():
                mode = "simulation" if self._is_simulation else "hardware"
                self._log_status(f"ERROR: Failed to initialize fluidics ({mode})")
                QMessageBox.critical(
                    self,
                    "Initialization Failed",
                    "Failed to initialize the fluidics driver. Check the config file and hardware.",
                )
                return

            # Create service
            self._service = FluidicsService(driver, core_bus)
            if self._service_registry is not None:
                existing = self._service_registry.get("fluidics")
                if existing is not None:
                    existing.shutdown()
                self._service_registry.register("fluidics", self._service)
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
        self.manual_solution_combo.clear()
        self.manual_solution_combo.addItems(self._available_solutions)

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

        # Update port/solution display if provided
        if event.port is not None:
            self.lbl_current_port.setText(str(event.port))
        if event.solution:
            self.lbl_current_solution.setText(f"({event.solution})")

        # Show progress panel with operation details
        self.lbl_operation.setText(event.operation.upper())
        flow_details = ""
        if event.solution:
            flow_details = f"{event.solution}"
        if event.flow_rate_ul_per_min:
            flow_details += f" @ {event.flow_rate_ul_per_min:.0f} uL/min"
        self.lbl_flow_details.setText(flow_details or "--")

        # Update status LED to running
        self._update_status_led("running")
        self.lbl_state.setText("RUNNING")

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
    # Protocol Event Handlers
    # ─────────────────────────────────────────────────────────────────────────

    def _on_controller_state_changed(
        self, event: FluidicsControllerStateChanged
    ) -> None:
        """Handle controller state transitions."""
        self.log_message_signal.emit(
            f"Protocol state: {event.old_state} -> {event.new_state}"
        )

    def _on_protocol_started(self, event: FluidicsProtocolStarted) -> None:
        """Handle protocol started event."""
        self._is_protocol_running = True
        self._protocol_name = event.protocol_name
        self._protocol_current_step = 0
        self._protocol_total_steps = event.total_steps

        # Update UI
        self.lbl_sequence_name.setText(event.protocol_name)
        self.lbl_step_counter.setText(f"0 of {event.total_steps}")
        self.sequence_progress_bar.setValue(0)
        self.lbl_current_step.setText("Starting...")
        self.lbl_next_step.setText("--")

        # Auto-select protocol in list so steps table shows its steps
        # (handles protocols started from orchestrator, not just GUI)
        for i in range(self.protocols_list.count()):
            item = self.protocols_list.item(i)
            if item and item.data(Qt.UserRole) == event.protocol_name:
                self.protocols_list.setCurrentItem(item)
                break

        # Enable skip button during protocol execution
        self.btn_skip_step.setEnabled(True)
        self._enable_controls(False)

        self.log_message_signal.emit(
            f"Starting protocol '{event.protocol_name}' with {event.total_steps} steps"
        )

    def _on_protocol_step_started(self, event: FluidicsProtocolStepStarted) -> None:
        """Handle protocol step started event."""
        self._protocol_current_step = event.step_index + 1  # Convert to 1-based for display

        # Update step counter
        self.lbl_step_counter.setText(f"{self._protocol_current_step} of {event.total_steps}")

        # Update progress bar
        if event.total_steps > 0:
            progress = int((event.step_index / event.total_steps) * 100)
            self.sequence_progress_bar.setValue(progress)

        # Highlight current step in table
        self._highlight_current_step(event.step_index)

        # Update step descriptions
        self.lbl_current_step.setText(event.step_description)
        self.lbl_next_step.setText(event.next_step_description or "End of protocol")

        self.log_message_signal.emit(
            f"Step {self._protocol_current_step}/{event.total_steps}: {event.step_description}"
        )

    def _on_protocol_completed(self, event: FluidicsProtocolCompleted) -> None:
        """Handle protocol completed event."""
        self._is_protocol_running = False

        # Disable skip button
        self.btn_skip_step.setEnabled(False)

        # Clear step highlighting
        self._clear_step_highlights()

        # Reset progress bar (protocol is done, not "in progress")
        if event.success:
            self.sequence_progress_bar.setValue(0)
            self.lbl_step_counter.setText(f"{event.steps_completed} of {event.total_steps}")
            self.lbl_current_step.setText("Protocol completed")
            self.log_message_signal.emit(
                f"Protocol '{event.protocol_name}' completed successfully"
            )
        else:
            self.lbl_current_step.setText("Protocol aborted")
            msg = f"Protocol '{event.protocol_name}' aborted at step {event.steps_completed}/{event.total_steps}"
            if event.error_message:
                msg += f": {event.error_message}"
            self.log_message_signal.emit(msg)

        self.lbl_next_step.setText("--")

        # Reset operation progress panel to idle state
        self.lbl_operation.setText("Idle")
        self.lbl_flow_details.setText("--")
        self.operation_progress_bar.setValue(0)
        self.lbl_time_remaining.setText("--:--")

        # Reset status dashboard
        self._update_status_led("idle")
        self.lbl_state.setText("IDLE")

        self._enable_controls(True)

    def _on_protocols_loaded(self, event: FluidicsProtocolsLoaded) -> None:
        """Handle protocol load events."""
        self._protocols = event.protocols
        self._protocols_path = event.path
        self._populate_protocols_list()
        self._enable_controls(True)
        self.lbl_protocols_path.setText(
            f"{len(event.protocols)} protocols from {Path(event.path).name}"
        )
        self.lbl_protocols_path.setStyleSheet("color: #27ae60; font-weight: bold;")
        self._log_status(
            f"Loaded {len(event.protocols)} protocols from {event.path}"
        )

        # Display solution validation warnings
        if event.validation_warnings:
            for proto_name, missing in event.validation_warnings.items():
                self._log_status(
                    f"Warning: '{proto_name}' uses unknown solutions: {', '.join(missing)}"
                )

    def _on_protocols_load_failed(self, event: FluidicsProtocolsLoadFailed) -> None:
        """Handle protocol load failures."""
        self._log_status(
            f"Error loading protocols from {event.path}: {event.error_message}"
        )
        QMessageBox.warning(
            self,
            "Protocol Load Failed",
            f"Failed to load protocols from {event.path}:\n{event.error_message}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # User Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _load_protocols(self) -> None:
        """Open file dialog to load protocols from YAML."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Fluidics Protocols", "", "YAML Files (*.yaml *.yml);;All Files (*)"
        )

        if file_path:
            self._log_status(f"Loading protocols from {file_path}")
            try:
                self._load_protocols_from_path(file_path, publish=True)

            except Exception as e:
                self._log_status(f"Error loading protocols: {str(e)}")
                self._log.exception(f"Error loading protocols: {e}")

    def _load_protocols_from_config(self) -> None:
        """Load protocols from configured path, if provided."""
        try:
            import _def as _config
        except Exception:
            return

        protocols_path = getattr(_config, "FLUIDICS_PROTOCOLS_PATH", None)
        if not protocols_path:
            return

        path = Path(protocols_path)
        if not path.is_absolute():
            base_dir = getattr(_config, "PROJECT_ROOT", None)
            if base_dir is not None:
                path = (base_dir / path).resolve()
            else:
                path = path.resolve()

        if not path.exists():
            self._log_status(f"Fluidics protocols not found: {path}")
            return

        try:
            self._load_protocols_from_path(str(path), publish=True)
        except Exception as exc:
            self._log_status(f"Error loading protocols: {exc}")
            self._log.exception("Error loading protocols: %s", exc)

    def _load_protocols_from_path(self, path: str, publish: bool = True) -> None:
        """Load protocols from YAML and update UI."""
        self._protocols_path = path
        protocol_file = FluidicsProtocolFile.load_from_yaml(path)
        self._protocols = protocol_file.protocols
        self._populate_protocols_list()
        self._enable_controls(True)
        self.lbl_protocols_path.setText(
            f"{len(self._protocols)} protocols from {Path(path).name}"
        )
        self.lbl_protocols_path.setStyleSheet("color: #27ae60; font-weight: bold;")
        self._log_status(f"Loaded {len(self._protocols)} protocols")

        if publish and self._event_bus is not None:
            self._event_bus.publish(LoadFluidicsProtocolsCommand(path=path))

    def _populate_protocols_list(self) -> None:
        """Populate the protocols list from loaded protocol data."""
        self.protocols_list.clear()
        self.steps_table.setRowCount(0)

        if not self._protocols:
            return

        for protocol_name in self._protocols:
            item = QListWidgetItem(protocol_name)
            item.setData(Qt.UserRole, protocol_name)
            self.protocols_list.addItem(item)

        # Select first protocol
        if self.protocols_list.count() > 0:
            self.protocols_list.setCurrentRow(0)

    def _on_protocol_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        """Handle protocol selection - show steps for selected protocol."""
        if current is None:
            self.steps_table.setRowCount(0)
            self.lbl_estimated_duration.setText("")
            return

        protocol_name = current.data(Qt.UserRole)
        if protocol_name not in self._protocols:
            return

        protocol = self._protocols[protocol_name]
        self._populate_steps_table(protocol)

        # Show estimated duration
        duration_s = protocol.estimated_duration_s()
        if duration_s >= 3600:
            hours = int(duration_s // 3600)
            mins = int((duration_s % 3600) // 60)
            self.lbl_estimated_duration.setText(f"~{hours}h {mins}m")
        elif duration_s >= 60:
            mins = int(duration_s // 60)
            secs = int(duration_s % 60)
            self.lbl_estimated_duration.setText(f"~{mins}m {secs}s")
        else:
            self.lbl_estimated_duration.setText(f"~{duration_s:.0f}s")

    def _populate_steps_table(self, protocol: FluidicsProtocol) -> None:
        """Populate the steps table with steps from a protocol."""
        self.steps_table.setRowCount(len(protocol.steps))

        # Default text color for table items (light gray for dark theme compatibility)
        default_text_color = QColor("#e0e0e0")

        for row_num, step in enumerate(protocol.steps):
            # Column 0: Step number
            step_item = QTableWidgetItem(str(row_num + 1))
            step_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 0, step_item)

            # Column 1: Operation
            op_item = QTableWidgetItem(step.operation.value)
            op_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 1, op_item)

            # Column 2: Solution (with availability warning)
            solution = step.solution or ""
            sol_item = QTableWidgetItem(solution)
            if solution and self._available_solutions and solution not in self._available_solutions:
                # Case-insensitive check
                if not any(s.lower() == solution.lower() for s in self._available_solutions):
                    sol_item.setForeground(QColor("#e67e22"))
                    sol_item.setToolTip(f"Solution '{solution}' not found in available solutions")
                else:
                    sol_item.setForeground(default_text_color)
            else:
                sol_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 2, sol_item)

            # Column 3: Volume
            volume = ""
            if step.volume_ul is not None:
                volume = f"{step.volume_ul} uL"
            vol_item = QTableWidgetItem(volume)
            vol_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 3, vol_item)

            # Column 4: Flow Rate
            flow_rate = ""
            if step.flow_rate_ul_per_min is not None:
                flow_rate = f"{step.flow_rate_ul_per_min:.0f} uL/min"
            fr_item = QTableWidgetItem(flow_rate)
            fr_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 4, fr_item)

            # Column 5: Incubation
            incubation = ""
            if step.duration_s is not None and step.duration_s > 0:
                if step.duration_s >= 60:
                    incubation = f"{step.duration_s / 60:.1f} min"
                else:
                    incubation = f"{step.duration_s} s"
            inc_item = QTableWidgetItem(incubation)
            inc_item.setForeground(default_text_color)
            self.steps_table.setItem(row_num, 5, inc_item)

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

        try:
            volume = float(self.txt_prime_volume.text())
            flow_rate = float(self.txt_prime_flow_rate.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Volume and flow rate must be numbers")
            return

        if not ports:
            return

        final_port = ports[0]

        self._log_status(
            f"Starting prime: Ports {ports}, "
            f"Volume {volume} uL, Flow rate {flow_rate} uL/min"
        )

        # Run in thread to avoid blocking UI
        def do_prime():
            try:
                # Re-check since we're in a different thread
                if self._service is None:
                    return
                self._service.reset_abort()
                self._service.prime(
                    ports=ports,
                    volume_ul=volume,
                    flow_rate_ul_per_min=flow_rate,
                    final_port=final_port,
                )
            except Exception as e:
                self.log_message_signal.emit(f"Prime error: {e}")

        threading.Thread(target=do_prime, daemon=True).start()

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

        self._log_status(f"Flow {solution}: {volume} uL @ {flow_rate} uL/min")

        # Run in thread to avoid blocking UI
        def do_flow():
            try:
                if self._service is None:
                    return
                self._service.reset_abort()
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

        self._log_status("Emptying syringe pump to waste")

        # Run in thread to avoid blocking UI
        def do_empty():
            try:
                if self._service is None:
                    return
                self._service.reset_abort()
                self._service.empty_syringe()
            except Exception as e:
                self.log_message_signal.emit(f"Empty syringe error: {e}")

        threading.Thread(target=do_empty, daemon=True).start()

    def _emergency_stop(self) -> None:
        """Emergency stop all fluidics operations."""
        self._log_status("EMERGENCY STOP")
        if self._service is not None:
            self._service.abort()
            self._service.reset_abort()
        if self._event_bus is not None:
            self._event_bus.publish(StopFluidicsCommand())
        self._is_protocol_running = False

        # Reset progress UI
        self._reset_progress_ui()

    def _skip_current_step(self) -> None:
        """Skip to the next step in the protocol."""
        if not self._is_protocol_running:
            self._log_status("No protocol running to skip")
            return

        current_step = self._protocol_current_step
        total_steps = self._protocol_total_steps
        empty_on_skip = self.chk_empty_on_skip.isChecked()
        self._log.info(f"Skip button clicked for step {current_step}/{total_steps}")
        self._log_status(f"Skip requested (step {current_step}/{total_steps})")

        if self._event_bus is not None:
            self._event_bus.publish(
                SkipFluidicsStepCommand(empty_syringe=empty_on_skip)
            )

    def _reset_progress_ui(self) -> None:
        """Reset the progress UI to initial state."""
        # Clear step highlighting
        self._clear_step_highlights()

        # Reset protocol progress panel
        self.lbl_sequence_name.setText("--")
        self.lbl_step_counter.setText("0 of 0")
        self.sequence_progress_bar.setValue(0)
        self.lbl_current_step.setText("--")
        self.lbl_next_step.setText("--")
        self._protocol_current_step = 0
        self._protocol_total_steps = 0

        # Reset operation progress panel
        self.lbl_operation.setText("Idle")
        self.lbl_flow_details.setText("--")
        self.operation_progress_bar.setValue(0)
        self.lbl_time_remaining.setText("--:--")

        # Reset status dashboard
        self._update_status_led("idle")
        self.lbl_state.setText("IDLE")

        # Disable skip button
        self.btn_skip_step.setEnabled(False)

    def _run_selected_protocol(self) -> None:
        """Run the currently selected protocol."""
        self._log.info("_run_selected_protocol called")
        self._log_status("Run Protocol clicked")

        if not self._is_available:
            QMessageBox.warning(self, "Not Available", "Fluidics not initialized")
            return

        if not self._protocols:
            QMessageBox.warning(self, "No Protocols", "Please load protocols first")
            return

        # Get selected protocol
        current_item = self.protocols_list.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "No Selection", "Please select a protocol to run")
            return

        protocol_name = current_item.data(Qt.UserRole)
        if protocol_name not in self._protocols:
            self._log.warning(
                "Protocol '%s' not found in list: %s",
                protocol_name,
                list(self._protocols.keys()),
            )
            QMessageBox.warning(self, "Error", f"Protocol '{protocol_name}' not found")
            return

        self._log_status(f"Starting protocol: {protocol_name}")
        if self._event_bus is not None:
            self._event_bus.publish(
                RunFluidicsProtocolCommand(protocol_name=protocol_name)
            )


    # ─────────────────────────────────────────────────────────────────────────
    # Port Info Slots
    # ─────────────────────────────────────────────────────────────────────────

    def _on_prime_ports_changed(self, text: str) -> None:
        """Update port info label when prime ports text changes."""
        if not self._is_available or not self._service:
            self.lbl_prime_port_info.setText("")
            return

        ports_str = text.strip()
        if not ports_str or ports_str.lower() == "all":
            ports = self._service.get_available_ports()
        else:
            # Parse without showing error dialogs (just for preview)
            try:
                ports = []
                for part in ports_str.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "-" in part:
                        start, end = map(int, part.split("-"))
                        ports.extend(range(start, end + 1))
                    else:
                        ports.append(int(part))
            except (ValueError, TypeError):
                self.lbl_prime_port_info.setText("")
                return

        if not ports:
            self.lbl_prime_port_info.setText("")
            return

        # Build display with solution names
        parts = []
        for p in ports:
            name = self._service.get_port_name(p)
            if name:
                parts.append(f"{p}: {name}")
            else:
                parts.append(str(p))
        self.lbl_prime_port_info.setText(", ".join(parts))

    def _on_manual_solution_changed(self, solution: str) -> None:
        """Update port info label when manual solution changes."""
        if not self._is_available or not self._service or not solution:
            self.lbl_manual_port.setText("")
            return

        port = self._service.get_port_for_solution(solution)
        if port is not None:
            self.lbl_manual_port.setText(f"Port: {port}")
        else:
            self.lbl_manual_port.setText("")

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
            if not ports_str or ports_str.lower() == "all":
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
        has_protocols = bool(self._protocols)

        self.btn_load_sequences.setEnabled(enabled)  # Always allow loading protocols
        self.btn_prime_start.setEnabled(actually_enabled)
        self.btn_manual_flow.setEnabled(actually_enabled)
        self.btn_empty_syringe_pump.setEnabled(actually_enabled)
        self.btn_execute_selected.setEnabled(actually_enabled and has_protocols)

    def _log_status(self, message: str) -> None:
        """Log a status message to the status text area with color coding."""
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        escaped = html.escape(message)
        msg_lower = message.lower()
        if "error" in msg_lower or "failed" in msg_lower:
            color = "#e74c3c"
        elif "warning" in msg_lower:
            color = "#e67e22"
        else:
            color = "#e0e0e0"
        self.status_text.append(
            f'<span style="color:{color}">[{current_time}] {escaped}</span>'
        )
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
        # Also log to console
        self._log.info(message)

    def _save_log(self) -> None:
        """Save the log content to a file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Fluidics Log", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, "w") as f:
                    f.write(self.status_text.toPlainText())
                self._log_status(f"Log saved to {file_path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save log: {str(e)}")
