"""The wide Fluidics display tab: instrument on the left (Initialize, manual control, device status,
Log | Temperature | Reagents), the Protocol editor on the right."""

from typing import Callable, Optional, Tuple

from qtpy.QtCore import QTimer, Signal
from qtpy.QtWidgets import QGroupBox, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget
from qtpy.QtCore import Qt

import squid.logging
from control.core.fluidics_protocol.sensor_recorder import SensorRecorder
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
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.service = service
        self.fluidics_port = None
        self.recorder = SensorRecorder()
        self.temperature_tab = None
        self.run_line_provider: Callable[[], str] = lambda: "—"

        self.system_panel = SystemPanel(service)
        self.system_panel.initialized.connect(self._on_initialized)

        self.manual_group = QGroupBox("Manual control")
        manual_layout = QVBoxLayout()
        self._manual_placeholder = QLabel("Initialize the fluidics system to enable manual control.")
        self._manual_placeholder.setStyleSheet("color: gray;")
        manual_layout.addWidget(self._manual_placeholder)
        self.manual_group.setLayout(manual_layout)

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
                from control.widgets_fluidics.sensor_plots import TemperatureTab

                self.temperature_tab = TemperatureTab(tc, self.recorder)
                self.tabs.insertTab(1, self.temperature_tab, "Temperature")
            except Exception:
                self._log.exception("Could not build the Temperature tab")
        self.system_ready.emit()

    def set_run_active(self, active: bool) -> None:
        """A running protocol owns the instrument: manual control and TEC setpoints go dead."""
        self.manual_group.setEnabled(not active)
        if self.temperature_tab is not None:
            self.temperature_tab.set_run_active(active)

    def _refresh_status(self) -> None:
        try:
            self.device_status.refresh(self.run_line_provider())
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Device status refresh failed: {e}")
