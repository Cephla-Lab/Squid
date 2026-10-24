"""The Fluidics system panel (config row + Initialize, run off the GUI thread) and the device-status
group. Initialize blocks 3–30 s on hardware, so it runs on a daemon thread and reports back through
queued Qt signals."""

import re
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


# How a drive fault is worded in an error. The stage's own checks: "axis Y2: drive fault, error code
# 0x7306", "axis X1: drive fault during homing", "axis Z3: drive is not enabled". The servo driver's,
# when Home or Reset fault meets a fault that will not clear, wrapped by the stage's adapter: "axis Z3:
# homing failed: Motor fault: error code 0x7306" (also "fault reset failed", "enable failed").
_DRIVE_FAULT = re.compile(
    r"axis (\S+): (?:drive (?:fault(?:, error code 0x[0-9A-Fa-f]+| during homing)?|is not enabled)"
    r"|[\w ]+ failed: Motor fault: error code 0x[0-9A-Fa-f]+)"
)

_ROBOT_FAULT_TIP = (
    "The last drive fault a robot move stopped on (a Sipper move, Prime/Clean or a run), not a live "
    "reading: the drives are read only by the moves themselves, on their one serial line. Reset fault "
    "in the Sipper group, or a Home that finishes, clears it."
)


def drive_fault(message: Optional[str]) -> Optional[str]:
    """The drive fault an error names ('axis Y2: drive fault, error code 0x7306'), or None."""
    match = _DRIVE_FAULT.search(message or "")
    return match.group(0) if match else None


NOT_RETRACTED = "not known to be retracted"


def tip_location(robot) -> str:
    """Where the sipper's tip is, from the robot's cached state (no I/O): in a well, retracted,
    or NOT_RETRACTED. The Sipper group and Device status both say it this way."""
    key = robot.current_well_key()
    if key is not None:
        return f"in {key}"
    return "retracted" if robot.is_retracted else NOT_RETRACTED


class DeviceStatusGroup(QGroupBox):
    """Reads only cached device state (no serial I/O from the GUI): the pump's held volume and the
    robot's position arrive by subscription, the valves/TEC expose cached attributes, and the run line
    comes from the runner. The robot's fault is the last one an error named (see drive_fault)."""

    _ROBOT_ROWS = ("Robot", "Robot fault")

    def __init__(self, parent=None):
        super().__init__("Device status", parent)
        self._service = None
        self._held_ul: Optional[float] = None
        self._robot = None
        self._robot_position = None
        self._robot_fault: Optional[str] = None
        grid = QGridLayout()
        self.labels = {}
        self._robot_row_widgets = []  # shown on a rig with a sipper
        rows = ("System", "Syringe pump", "Valves", "Temperature") + self._ROBOT_ROWS + ("Run",)
        for row, name in enumerate(rows):
            caption, label = QLabel(f"{name}:"), QLabel("—")
            grid.addWidget(caption, row, 0)
            grid.addWidget(label, row, 1)
            self.labels[name] = label
            if name in self._ROBOT_ROWS:
                self._robot_row_widgets += [caption, label]
        self.labels["Robot fault"].setToolTip(_ROBOT_FAULT_TIP)
        self._show_robot_rows(False)  # until a rig with a sipper is attached
        grid.setColumnStretch(1, 1)
        self.setLayout(grid)

    def _show_robot_rows(self, shown: bool) -> None:
        for widget in self._robot_row_widgets:
            widget.setVisible(shown)

    def attach(self, service) -> None:
        self._service = service
        try:
            service.system.devices.syringe_pump.held_volume.subscribe(self._on_held_volume)
        except Exception:
            pass
        self.labels["System"].setText(f"initialized · {service.config_path}")
        self._robot = service.system.devices.robot
        self._robot_fault = None
        self._show_robot_rows(self._robot is not None)
        if self._robot is not None:
            self._robot_position = self._robot.position()
            self._robot.moves.subscribe(self._on_robot_move)
            service.system.session.events.subscribe(self._on_run_event)

    def _on_held_volume(self, volume_ul) -> None:  # producer thread: store only
        self._held_ul = float(volume_ul)

    def _on_robot_move(self, position) -> None:  # the job's thread: store only
        self._robot_position = position

    def _on_run_event(self, event) -> None:  # the job's thread: store only
        from fluidics.events import RunEnded

        if isinstance(event, RunEnded) and event.outcome == "failed":
            self.note_robot_error(event.message)

    def note_robot_error(self, message: Optional[str]) -> None:
        """A job failed with `message`; a drive fault in it shows until it is cleared. Any thread."""
        fault = drive_fault(message)
        if fault is not None:
            self._robot_fault = fault

    def note_robot_recovered(self) -> None:
        """Reset fault or Home finished: the drives answered, the fault is cleared."""
        self._robot_fault = None

    def _paint_robot(self) -> None:
        """While a job runs, the last position the stage published (its own is unknown mid-move).
        Once the rig is idle, the stage's own cached position and state (no I/O): a Home or a fault
        reset that failed can drop the position without publishing it."""
        robot = self._robot
        idle = not self._service.system.busy
        position = robot.position() if idle else self._robot_position
        if position is None:
            where = "position unknown (Home the robot)"
        else:
            x, y, z = position
            moving = not idle and robot.current_well_key() is None
            where = f"x {x:.2f}  y {y:.2f}  z {z:.2f} mm · " + ("moving or in use" if moving else tip_location(robot))
        self.labels["Robot"].setText(where)
        fault = self._robot_fault
        label = self.labels["Robot fault"]
        label.setText(f"{fault} (Reset fault in the Sipper group)" if fault else "none")
        label.setStyleSheet("color: #b00020; font-weight: bold;" if fault else "")

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
        if self._robot is not None:
            self._paint_robot()
