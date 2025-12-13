# Fluidics control widgets
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import squid.core.logging

if TYPE_CHECKING:
    from squid.mcs.drivers.fluidics.fluidics import Fluidics
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTextEdit,
    QTableView,
    QFileDialog,
    QMessageBox,
    QGroupBox,
)

from _def import *
from squid.ui.widgets.base import PandasTableModel
from squid.core.events import EventBus, FluidicsInitialized


class FluidicsWidget(QWidget):
    log_message_signal = Signal(str)
    fluidics_initialized_signal = Signal()

    def __init__(
        self, fluidics: Fluidics, event_bus: Optional[EventBus] = None, parent=None
    ) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus

        # Initialize data structures
        self.fluidics = fluidics
        self.fluidics.log_callback = self.log_message_signal.emit
        self.set_sequence_callbacks()

        # Set up the UI
        self.setup_ui()
        self.log_message_signal.connect(self.log_status)

    def setup_ui(self) -> None:
        # Main layout
        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # Left side - Control panels
        left_panel = QVBoxLayout()

        # Fluidics Control panel
        fluidics_control_group = QGroupBox("Fluidics Control")
        fluidics_control_layout = QVBoxLayout()

        # First row - Initialize and Load Sequences
        init_row = QHBoxLayout()
        self.btn_initialize = QPushButton("Initialize")
        self.btn_load_sequences = QPushButton("Load Sequences")
        init_row.addWidget(self.btn_initialize)
        init_row.addWidget(self.btn_load_sequences)
        fluidics_control_layout.addLayout(init_row)

        # Second row - Prime Ports
        prime_row = QHBoxLayout()
        prime_row.addWidget(QLabel("Prime Ports:"))
        prime_row.addWidget(QLabel("Ports"))
        self.txt_prime_ports = QLineEdit()
        prime_row.addWidget(self.txt_prime_ports)
        prime_row.addWidget(QLabel("Fill Tubing With"))
        self.prime_fill_combo = QComboBox()
        self.prime_fill_combo.addItems(self.fluidics.available_port_names)
        self.prime_fill_combo.setCurrentIndex(
            25 - 1
        )  # Usually Port 25 should be the common wash buffer port
        prime_row.addWidget(self.prime_fill_combo)
        prime_row.addWidget(QLabel("Volume (µL)"))
        self.txt_prime_volume = QLineEdit()
        self.txt_prime_volume.setText("2000")
        prime_row.addWidget(self.txt_prime_volume)
        self.btn_prime_start = QPushButton("Start")
        prime_row.addWidget(self.btn_prime_start)
        fluidics_control_layout.addLayout(prime_row)

        # Third row - Clean Up
        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(QLabel("Clean Up:"))
        cleanup_row.addWidget(QLabel("Ports"))
        self.txt_cleanup_ports = QLineEdit()
        cleanup_row.addWidget(self.txt_cleanup_ports)
        cleanup_row.addWidget(QLabel("Fill Tubing With"))
        self.cleanup_fill_combo = QComboBox()
        self.cleanup_fill_combo.addItems(self.fluidics.available_port_names)
        self.cleanup_fill_combo.setCurrentIndex(25 - 1)
        cleanup_row.addWidget(self.cleanup_fill_combo)
        cleanup_row.addWidget(QLabel("Volume (µL)"))
        self.txt_cleanup_volume = QLineEdit()
        self.txt_cleanup_volume.setText("2000")
        cleanup_row.addWidget(self.txt_cleanup_volume)
        cleanup_row.addWidget(QLabel("Repeat"))
        self.txt_cleanup_repeat = QLineEdit()
        self.txt_cleanup_repeat.setText("3")
        cleanup_row.addWidget(self.txt_cleanup_repeat)
        self.btn_cleanup_start = QPushButton("Start")
        cleanup_row.addWidget(self.btn_cleanup_start)
        fluidics_control_layout.addLayout(cleanup_row)

        fluidics_control_group.setLayout(fluidics_control_layout)
        left_panel.addWidget(fluidics_control_group)

        # Manual Control panel
        manual_control_group = QGroupBox("Manual Control")
        manual_control_layout = QVBoxLayout()

        # First row - Port, Flow Rate, Volume, Flow button
        manual_row1 = QHBoxLayout()
        manual_row1.addWidget(QLabel("Port"))
        self.manual_port_combo = QComboBox()
        self.manual_port_combo.addItems(self.fluidics.available_port_names)
        manual_row1.addWidget(self.manual_port_combo)
        manual_row1.addWidget(QLabel("Flow Rate (µL/min)"))
        self.txt_manual_flow_rate = QLineEdit()
        self.txt_manual_flow_rate.setText("500")
        manual_row1.addWidget(self.txt_manual_flow_rate)
        manual_row1.addWidget(QLabel("Volume (µL)"))
        self.txt_manual_volume = QLineEdit()
        manual_row1.addWidget(self.txt_manual_volume)
        self.btn_manual_flow = QPushButton("Flow")
        manual_row1.addWidget(self.btn_manual_flow)
        manual_control_layout.addLayout(manual_row1)

        # Second row - Empty Syringe Pump button
        manual_row2 = QHBoxLayout()
        self.btn_empty_syringe_pump = QPushButton("Empty Syringe Pump To Waste")
        manual_row2.addWidget(self.btn_empty_syringe_pump)
        manual_control_layout.addLayout(manual_row2)

        manual_control_group.setLayout(manual_control_layout)
        left_panel.addWidget(manual_control_group)

        # Status panel
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        status_layout.addWidget(self.status_text)

        status_group.setLayout(status_layout)
        left_panel.addWidget(status_group)

        # Add left panel to main layout
        main_layout.addLayout(left_panel, 1)

        # Right side - Sequences panel
        right_panel = QVBoxLayout()

        sequences_group = QGroupBox("Sequences")
        sequences_layout = QVBoxLayout()

        # Table for sequences
        self.sequences_table = QTableView()
        sequences_layout.addWidget(self.sequences_table)

        # Emergency Stop button
        self.btn_emergency_stop = QPushButton("Emergency Stop")
        self.btn_emergency_stop.setStyleSheet(
            "background-color: red; color: white; font-weight: bold;"
        )
        sequences_layout.addWidget(self.btn_emergency_stop)

        sequences_group.setLayout(sequences_layout)
        right_panel.addWidget(sequences_group)

        # Add right panel to main layout
        main_layout.addLayout(right_panel, 1)

        # Connect signals
        self.btn_initialize.clicked.connect(self.initialize_fluidics)
        self.btn_load_sequences.clicked.connect(self.load_sequences)
        self.btn_prime_start.clicked.connect(self.start_prime)
        self.btn_cleanup_start.clicked.connect(self.start_cleanup)
        self.btn_manual_flow.clicked.connect(self.start_manual_flow)
        self.btn_empty_syringe_pump.clicked.connect(self.empty_syringe_pump)
        self.btn_emergency_stop.clicked.connect(self.emergency_stop)

        self.enable_controls(False)
        self.btn_emergency_stop.setEnabled(False)

    def initialize_fluidics(self) -> None:
        """Initialize the fluidics system"""
        self.log_status("Initializing fluidics system...")
        self.fluidics.initialize()
        self.btn_initialize.setEnabled(False)
        self.enable_controls(True)
        self.btn_emergency_stop.setEnabled(True)
        self.fluidics_initialized_signal.emit()
        if self._event_bus is not None:
            self._event_bus.publish(FluidicsInitialized())

    def set_sequence_callbacks(self) -> None:
        callbacks = {
            "on_finished": self.on_finish,
            "on_error": self.on_finish,
            "on_estimate": self.on_estimate,
            "update_progress": self.update_progress,
        }
        self.fluidics.worker_callbacks = callbacks

    def set_manual_control_callbacks(self) -> None:
        # TODO: use better logging description
        callbacks = {
            "on_finished": lambda: self.on_finish("Operation completed"),
            "on_error": self.on_finish,
            "on_estimate": None,
            "update_progress": None,
        }
        self.fluidics.worker_callbacks = callbacks

    def load_sequences(self) -> None:
        """Open file dialog to load sequences from CSV"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Fluidics Sequences", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self.log_status(f"Loading sequences from {file_path}")
            try:
                self.sequence_df = self.fluidics.load_sequences(file_path)
                self.sequence_df.drop("include", axis=1, inplace=True)
                model = PandasTableModel(
                    self.sequence_df, self.fluidics.available_port_names
                )
                self.sequences_table.setModel(model)
                self.sequences_table.resizeColumnsToContents()
                self.sequences_table.horizontalHeader().setStretchLastSection(True)
                self.log_status(f"Loaded {len(self.sequence_df)} sequences")
            except Exception as e:
                self.log_status(f"Error loading sequences: {str(e)}")

    def start_prime(self) -> None:
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_prime_ports.text())
        fill_port = self.prime_fill_combo.currentIndex() + 1
        volume = int(self.txt_prime_volume.text())

        if not ports or not fill_port or not volume:
            return

        self.log_status(
            f"Starting prime: Ports {ports}, Fill with {fill_port}, Volume {volume}µL"
        )
        self.fluidics.priming(ports, fill_port, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_cleanup(self) -> None:
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_cleanup_ports.text())
        fill_port = self.cleanup_fill_combo.currentIndex() + 1
        volume = int(self.txt_cleanup_volume.text())
        repeat = int(self.txt_cleanup_repeat.text())

        if not ports or not fill_port or not volume or not repeat:
            return

        self.log_status(
            f"Starting cleanup: Ports {ports}, Fill with {fill_port}, Volume {volume}µL, Repeat {repeat}x"
        )
        self.fluidics.clean_up(ports, fill_port, volume, repeat)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_manual_flow(self) -> None:
        self.set_manual_control_callbacks()
        port = self.manual_port_combo.currentIndex() + 1
        flow_rate = int(self.txt_manual_flow_rate.text())
        volume = int(self.txt_manual_volume.text())

        if not port or not flow_rate or not volume:
            return

        self.log_status(
            f"Flow reagent: Port {port}, Flow rate {flow_rate}µL/min, Volume {volume}µL"
        )
        self.fluidics.manual_flow(port, flow_rate, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def empty_syringe_pump(self) -> None:
        self.log_status("Empty syringe pump to waste")
        self.enable_controls(False)
        self.fluidics.empty_syringe_pump()
        self.log_status("Operation completed")
        self.enable_controls(True)

    def emergency_stop(self) -> None:
        self.fluidics.emergency_stop()

    def get_port_list(self, text: str) -> list:
        """Parse ports input string into a list of numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            ports_str = text.strip()
            if not ports_str:
                return [
                    i for i in range(1, len(self.fluidics.available_port_names) + 1)
                ]

            port_list: list[int] = []

            # Split by comma and process each part
            for part in ports_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 28 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 28, and start must be <= end"
                        )
                    port_list.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 28:
                        raise ValueError(
                            f"Invalid number {num}: Must be between 1 and 28"
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

    def update_progress(self, idx: int, seq_num: int, status: str) -> None:
        self.sequences_table.model().set_current_row(idx)
        self.log_message_signal.emit(
            f"Sequence {self.sequence_df.iloc[idx]['sequence_name']} {status}"
        )

    def on_finish(self, status: str = None) -> None:
        self.enable_controls(True)
        try:
            self.sequences_table.model().set_current_row(-1)
        except Exception:
            pass
        if status is None:
            status = "Sequence section completed"
        self.fluidics.reset_abort()
        self.log_message_signal.emit(status)

    def on_estimate(self, time: float, n: int) -> None:
        self.log_message_signal.emit(f"Estimated time: {time}s, Sequences: {n}")

    def enable_controls(self, enabled: bool):
        self.btn_load_sequences.setEnabled(enabled)
        self.btn_prime_start.setEnabled(enabled)
        self.btn_cleanup_start.setEnabled(enabled)
        self.btn_manual_flow.setEnabled(enabled)
        self.btn_empty_syringe_pump.setEnabled(enabled)

    def log_status(self, message: str) -> None:
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.status_text.append(f"[{current_time}] {message}")
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # Also log to console
        self._log.info(message)
