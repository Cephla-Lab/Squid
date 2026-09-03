"""The Fluidics system panel (config row + Initialize, run off the GUI thread) and the device-status
group. Initialize blocks 3–30 s on hardware, so it runs on a daemon thread and reports back through
queued Qt signals."""

import threading
from typing import Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

import squid.logging
from control.widgets_fluidics import state


def _config_summary(config) -> str:
    from fluidics.control.config import available_ports  # importable once the service is up

    ports = len(available_ports(config))
    tec = config.temperature_controller.channels if config.temperature_controller is not None else None
    sensors = len(config.flow_sensors or [])
    return f"{config.application} · {ports} ports · TEC {tec if tec is not None else '—'} · sensors {sensors}"


class SystemPanel(QGroupBox):
    # Emitted from the bring-up thread (queued delivery); the panel's own UI slots are
    # connected first in __init__, so they run before any external subscriber.
    initialized = Signal()
    initialize_failed = Signal(str)

    _INITIALIZE_KWARGS: dict = {}  # tests override with {"instant": True}

    def __init__(self, service, parent=None):
        super().__init__("Fluidics system", parent)
        self._log = squid.logging.get_logger(__name__)
        self.service = service

        self.path_edit = QLineEdit(state.load_ui_state().get("config_path") or service.default_config_path)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        self.initialize_button = QPushButton("Initialize")
        self.initialize_button.clicked.connect(self._initialize)
        self.status_label = QLabel("Not initialized")
        self.status_label.setStyleSheet("color: gray;")

        row = QHBoxLayout()
        row.addWidget(QLabel("Config:"))
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)
        row.addWidget(self.initialize_button)
        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self.initialized.connect(self._on_initialized)
        self.initialize_failed.connect(self._on_failed)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Fluidics configuration", self.path_edit.text(), "Config files (*.yaml *.yml *.json)"
        )
        if path:
            self.path_edit.setText(path)

    def _initialize(self) -> None:
        if self.service.initialized:
            QMessageBox.information(self, "Fluidics", "The fluidics system is already initialized.")
            return
        config_path = self.path_edit.text().strip()
        self.initialize_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.status_label.setText("Initializing…")

        def bring_up():
            try:
                self.service.initialize(config_path=config_path, **self._INITIALIZE_KWARGS)
            except Exception as e:
                self.initialize_failed.emit(str(e))
                return
            self.initialized.emit()

        threading.Thread(target=bring_up, name="fluidics-initialize", daemon=True).start()

    def _on_initialized(self) -> None:
        self.initialize_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.path_edit.setEnabled(False)
        summary = _config_summary(self.service.config)
        if self.service.issues:
            summary += f" · {len(self.service.issues)} bring-up issue(s), see the log"
        self.status_label.setText(summary)
        state.save_ui_state(config_path=self.service.config_path)

    def _on_failed(self, message: str) -> None:
        self.initialize_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.status_label.setText("Initialize failed")
        QMessageBox.warning(self, "Initialize failed", message)


class DeviceStatusGroup(QGroupBox):
    """Reads only cached device state (no serial I/O from the GUI): the pump's held volume arrives by
    subscription, the valves/TEC expose cached attributes, and the run line comes from the runner."""

    def __init__(self, parent=None):
        super().__init__("Device status", parent)
        self._service = None
        self._held_ul: Optional[float] = None
        grid = QGridLayout()
        self.labels = {}
        for row, name in enumerate(("System", "Syringe pump", "Valves", "Temperature", "Run")):
            grid.addWidget(QLabel(f"{name}:"), row, 0)
            label = QLabel("—")
            grid.addWidget(label, row, 1)
            self.labels[name] = label
        grid.setColumnStretch(1, 1)
        self.setLayout(grid)

    def attach(self, service) -> None:
        self._service = service
        try:
            service.system.devices.syringe_pump.held_volume.subscribe(self._on_held_volume)
        except Exception:
            pass
        self.labels["System"].setText(f"initialized · {service.config_path}")

    def _on_held_volume(self, volume_ul) -> None:  # producer thread: store only
        self._held_ul = float(volume_ul)

    def refresh(self, run_line: str = "—") -> None:
        self.labels["Run"].setText(run_line)
        service = self._service
        if service is None or not service.initialized:
            return
        devices = service.system.devices
        held = f"{self._held_ul:.0f} µL held" if self._held_ul is not None else "idle"
        self.labels["Syringe pump"].setText(held)
        try:
            port = devices.selector_valves.get_current_port()
            reagent = devices.selector_valves.port_to_reagent(port)
            self.labels["Valves"].setText(f"port {port}" + (f" ({reagent})" if reagent else ""))
        except Exception:
            self.labels["Valves"].setText("—")
        tc = devices.temperature_controller
        if tc is None:
            self.labels["Temperature"].setText("no controller")
        else:
            parts = [
                f"ch{i + 1} {tc.actual_temperatures[i]:.1f}/{tc.target_temperatures[i]:.1f} °C"
                + (" ON" if tc.output_enabled[i] else " off")
                for i in range(tc.channels)
            ]
            self.labels["Temperature"].setText(" · ".join(parts))
