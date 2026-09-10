"""The wide Fluidics display tab: instrument on the left (Initialize, manual control, device status,
Log | Temperature | Reagents), the Protocol editor on the right."""

from typing import Callable, Optional, Tuple

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.widgets_fluidics.log_view import FluidicsLogView, ReagentsTable
from control.widgets_fluidics.protocol_tab import ProtocolTab
from control.widgets_fluidics.system_panel import DeviceStatusGroup, SystemPanel


class FluidicsDisplayTab(QWidget):
    system_ready = Signal()

    def __init__(
        self,
        service,
        current_source: Optional[Callable[[], Tuple[Optional[str], dict, dict]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)
        self.service = service
        self.fluidics_port = None
        self.temperature_tab = None
        self.run_line_provider: Callable[[], str] = lambda: "—"

        self.system_panel = SystemPanel(service)
        self.system_panel.initialized.connect(self._on_initialized)

        self.manual_group = QGroupBox("Manual control")
        manual_layout = QVBoxLayout()
        self._manual_placeholder = QLabel("Initialize the fluidics system to enable manual control.")
        self._manual_placeholder.setStyleSheet("color: gray;")
        manual_layout.addWidget(self._manual_placeholder)

        # One-off priming/cleaning as an inline row (no pop-ups), the old widget's fields:
        # which ports to prime (their tubing filled with the config's per-port amounts), the
        # wash port the final volume is drawn from, that volume, the flow rate, and a repeat.
        self.ports_edit = QLineEdit()
        self.ports_edit.setPlaceholderText("ports to prime, e.g. 1-4, 25")
        self.wash_port_spin = QSpinBox()
        self.wash_port_spin.setRange(1, 100000)
        self.wash_port_spin.setValue(1)
        self.wash_port_spin.setPrefix("wash ")
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(1, 100000)
        self.volume_spin.setValue(200)
        self.volume_spin.setSuffix(" µL")
        self.flow_spin = QSpinBox()
        self.flow_spin.setRange(1, 100000)
        self.flow_spin.setValue(2000)
        self.flow_spin.setSuffix(" µL/min")
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99)
        self.repeat_spin.setValue(1)
        self.repeat_spin.setPrefix("×")
        self.prime_button = QPushButton("Prime")
        self.prime_button.clicked.connect(lambda: self._quick_op("priming"))
        self.clean_button = QPushButton("Clean")
        self.clean_button.clicked.connect(lambda: self._quick_op("clean_up"))
        self.stop_quick_button = QPushButton("Stop")
        self.stop_quick_button.clicked.connect(self._stop_quick_op)
        self.stop_quick_button.hide()
        self._quick_widgets = [
            self.ports_edit,
            self.wash_port_spin,
            self.volume_spin,
            self.flow_spin,
            self.repeat_spin,
            self.prime_button,
            self.clean_button,
        ]
        for widget in self._quick_widgets:
            widget.setEnabled(False)  # need the initialized system

        quick_row = QHBoxLayout()
        quick_row.addWidget(QLabel("Prime / Clean:"))
        quick_row.addWidget(self.ports_edit, 1)
        quick_row.addWidget(self.wash_port_spin)
        quick_row.addWidget(self.volume_spin)
        quick_row.addWidget(self.flow_spin)
        quick_row.addWidget(self.repeat_spin)
        quick_row.addWidget(self.prime_button)
        quick_row.addWidget(self.clean_button)
        quick_row.addWidget(self.stop_quick_button)
        manual_layout.addLayout(quick_row)
        self.manual_group.setLayout(manual_layout)
        self._quick_running = False

        self.device_status = DeviceStatusGroup()

        self.log_view = FluidicsLogView()
        self.log_view.connect_logging()
        self.reagents_table = ReagentsTable()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.log_view, "Log")
        self.tabs.addTab(self.reagents_table, "Reagents")

        left = QWidget()
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.system_panel)
        left_layout.addWidget(self.manual_group)
        left_layout.addWidget(self.device_status)
        left_layout.addWidget(self.tabs, 1)
        left.setLayout(left_layout)

        self.protocol_tab = ProtocolTab(service, current_source=current_source)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.protocol_tab)
        # Content-driven so the instrument column (the temperature plots need real width)
        # is never clipped; the divider is draggable and both panes share resize space.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(1000)

    def _on_initialized(self) -> None:
        try:
            from control.core.fluidics_protocol.library_port import LibraryFluidicsPort

            self.fluidics_port = LibraryFluidicsPort(self.service.system)
        except Exception:
            self._log.exception("Could not build the fluidics port")
        self.device_status.attach(self.service)

        try:
            from fluidics.qt.manual_control import ManualControlWidget

            manual = ManualControlWidget(self.service.config, self.service.system)
            self._manual_placeholder.hide()
            self.manual_group.layout().addWidget(manual)
            self.manual_widget = manual
        except ImportError:
            self._manual_placeholder.setText("Manual control needs the updated fluidics library (fluidics.qt).")
        except Exception:
            self._log.exception("Could not build the manual-control widget")

        tc = self.service.system.devices.temperature_controller
        if tc is not None:
            try:
                from fluidics.qt.sensor_plots import TemperatureControlWidget

                self.temperature_tab = TemperatureControlWidget(tc)
                self.tabs.insertTab(1, self.temperature_tab, "Temperature")
            except Exception:
                self._log.exception("Could not build the Temperature tab")
        for widget in self._quick_widgets:
            widget.setEnabled(True)
        self.system_ready.emit()

    def shutdown(self) -> None:
        """Exit/restart path: detach logging and close the plot widgets' open CSV
        recordings (an embedded tab gets no closeEvent, so the host must ask; see
        SensorTabWidget.close_recordings)."""
        self.log_view.disconnect_logging()
        if self.temperature_tab is not None:
            self.temperature_tab.close_recordings()

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the instrument: manual control and TEC setpoints go
        dead (the plots and their recording stay live)."""
        self.manual_group.setEnabled(not active)
        if self.temperature_tab is not None:
            self.temperature_tab.setControlsEnabled(not active)

    def quick_op_active(self) -> bool:
        return self._quick_running

    def _quick_op(self, op: str) -> None:
        # The old widget's behavior: prime the tubing for use_ports (config per-port amounts),
        # then draw `volume` from the wash port. Driven as a manual verb so use_ports survives
        # (the sequence model dropped it); Clean just repeats.
        if self.fluidics_port is None or self._quick_running:
            return
        from control.models.fluidics_protocol import parse_port_list

        use_ports = parse_port_list(self.ports_edit.text())
        if not use_ports:
            self._log.error("Prime/Clean: name at least one port to prime (e.g. 1-4, 25)")
            return
        verb_name = "prime" if op == "priming" else "clean"
        wash_port = int(self.wash_port_spin.value())
        flow_rate = int(self.flow_spin.value())
        volume = int(self.volume_spin.value())
        repeat = int(self.repeat_spin.value()) if op == "clean_up" else 1
        operations = self.service.system.operations

        def verb():
            for _ in range(repeat):
                operations.priming_or_clean_up(wash_port, flow_rate, volume, use_ports=use_ports)

        try:
            self.service.system.run_manual(
                verb,
                callbacks={
                    "on_error": lambda message: self._log.error(f"Manual {verb_name} failed: {message}"),
                    "on_stopped": lambda: self._log.info(f"Manual {verb_name} stopped"),
                    "on_finished": lambda: self._log.info(f"Manual {verb_name} finished"),
                },
            )
        except Exception as e:  # RuntimeError while another job holds the session
            self._log.error(f"Prime/Clean could not start: {e}")
            return
        self._set_quick_running(True)
        self._log.info(f"Manual {verb_name} started on {len(use_ports)} port(s)")

    def _stop_quick_op(self) -> None:
        if self._quick_running:
            self.service.system.abort()

    def _set_quick_running(self, running: bool) -> None:
        """Flip the inline Prime/Clean row between running (fields locked, Stop shown) and idle."""
        self._quick_running = running
        for widget in self._quick_widgets:
            widget.setEnabled(not running)
        self.stop_quick_button.setVisible(running)

    def _poll_quick_op(self) -> None:
        # The manual verb runs on the library's job thread; when the session reads free the
        # inline row comes back (the callbacks already logged the outcome).
        if not self._quick_running or self.service.system.busy:
            return
        self._set_quick_running(False)

    def _refresh_status(self) -> None:
        try:
            self._poll_quick_op()
            self.device_status.refresh(self.run_line_provider())
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Device status refresh failed: {e}")
