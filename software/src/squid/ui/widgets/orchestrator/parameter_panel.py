"""
Parameter Inspection Panel for orchestrator.

Shows detailed parameters for selected operations and FOVs in the workflow tree.
"""

from typing import Optional, Dict, Any, TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QGroupBox,
)
from PyQt5.QtGui import QFont

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint.fov_task import FovTask

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class ParameterInspectionPanel(QWidget):
    """Panel displaying detailed parameters for selected workflow items.

    Shows key-value pairs for:
    - Round configuration
    - Operation parameters
    - FOV task details
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Header label
        self._header_label = QLabel("No item selected")
        self._header_label.setFont(QFont("", -1, QFont.Bold))
        self._header_label.setWordWrap(True)
        layout.addWidget(self._header_label)

        # Parameters table
        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

    def clear(self) -> None:
        """Clear the panel."""
        self._header_label.setText("No item selected")
        self._table.setRowCount(0)

    def show_round(self, round_index: int, round_data: Dict[str, Any]) -> None:
        """Show round parameters.

        Args:
            round_index: Index of the round (0-based)
            round_data: Round configuration dictionary (V2 or legacy format)
        """
        round_name = round_data.get("name", f"Round {round_index + 1}")
        # Avoid redundant "Round X: Round X" display
        if round_name.lower().startswith("round"):
            display_name = round_name
        else:
            display_name = f"Round {round_index + 1}: {round_name}"
        self._header_label.setText(display_name)

        self._table.setRowCount(0)

        # Check for V2 format (steps list) first
        steps = round_data.get("steps", [])
        if steps:
            self._show_v2_round_params(round_data, steps)
        else:
            self._show_legacy_round_params(round_data)

    def _show_v2_round_params(self, round_data: Dict[str, Any], steps: list) -> None:
        """Show V2 format round parameters.

        Args:
            round_data: Round configuration dictionary
            steps: List of step dictionaries
        """
        # Count step types
        fluidics_steps = [s for s in steps if s.get("step_type") == "fluidics"]
        imaging_steps = [s for s in steps if s.get("step_type") == "imaging"]
        intervention_steps = [s for s in steps if s.get("step_type") == "intervention"]

        # Determine round type from steps
        if intervention_steps:
            round_type = "intervention"
        elif imaging_steps and fluidics_steps:
            round_type = "fluidics + imaging"
        elif imaging_steps:
            round_type = "imaging"
        elif fluidics_steps:
            round_type = "fluidics"
        else:
            round_type = "unknown"

        self._add_row("Type", round_type)

        # Check for intervention requirement
        has_intervention = len(intervention_steps) > 0
        self._add_row("Requires Intervention", str(has_intervention))

        if intervention_steps:
            msg = intervention_steps[0].get("message", "")
            if msg:
                self._add_row("Intervention Message", msg)

        # Fluidics summary
        if fluidics_steps:
            self._add_row("Fluidics Steps", str(len(fluidics_steps)))
            # Show protocol references
            protocols = [s.get("protocol", "unknown") for s in fluidics_steps]
            self._add_row("Fluidics Protocols", ", ".join(protocols))

        # Imaging summary
        if imaging_steps:
            self._add_row("Imaging Steps", str(len(imaging_steps)))
            # Show config references
            configs = [s.get("config", "unknown") for s in imaging_steps]
            self._add_row("Imaging Configs", ", ".join(set(configs)))
            # Show FOV set references
            fov_sets = [s.get("fovs", "default") for s in imaging_steps]
            unique_fov_sets = set(fov_sets)
            if unique_fov_sets != {"default"}:
                self._add_row("FOV Sets", ", ".join(unique_fov_sets))

    def _show_legacy_round_params(self, round_data: Dict[str, Any]) -> None:
        """Show legacy format round parameters.

        Args:
            round_data: Round configuration dictionary
        """
        # Add round parameters
        self._add_row("Type", round_data.get("type", "imaging"))
        self._add_row("Requires Intervention", str(round_data.get("requires_intervention", False)))

        if round_data.get("intervention_message"):
            self._add_row("Intervention Message", round_data.get("intervention_message"))

        # Fluidics summary
        fluidics = round_data.get("fluidics", [])
        if fluidics:
            self._add_row("Fluidics Steps", str(len(fluidics)))

        # Imaging summary
        imaging = round_data.get("imaging")
        if imaging:
            channels = imaging.get("channels", [])
            self._add_row("Imaging Channels", ", ".join(channels) if channels else "default")

            # Z-stack details
            z_planes = imaging.get("z_planes", 1)
            z_step_um = imaging.get("z_step_um", 0.5)
            self._add_row("Z Planes", str(z_planes))
            self._add_row("Z Step (µm)", f"{z_step_um:.2f}")
            if z_planes > 1:
                total_z_range = (z_planes - 1) * z_step_um
                self._add_row("Total Z Range (µm)", f"{total_z_range:.2f}")

            self._add_row("Use Autofocus", str(imaging.get("use_autofocus", False)))

    def show_operation(
        self,
        round_data: Dict[str, Any],
        operation_data: Dict[str, Any],
        op_index: int
    ) -> None:
        """Show operation parameters.

        Args:
            round_data: Parent round data
            operation_data: Operation configuration dictionary
            op_index: Operation index within round
        """
        op_type = operation_data.get("type", "unknown")
        op_name = operation_data.get("name", op_type)
        round_name = round_data.get("name", "Round")
        self._header_label.setText(f"{round_name} - {op_type.title()}: {op_name}")

        self._table.setRowCount(0)

        # Common fields
        self._add_row("Type", op_type)
        self._add_row("Index", str(op_index + 1))

        if op_type == "imaging":
            self._show_imaging_params(operation_data)
        elif op_type == "fluidics":
            self._show_fluidics_params(operation_data)
        elif op_type == "wait":
            self._show_wait_params(operation_data)
        elif op_type == "intervention":
            self._show_intervention_params(operation_data)

    def _show_imaging_params(self, data: Dict[str, Any]) -> None:
        """Show imaging operation parameters.

        Handles both V2 format (config reference) and legacy format (inline params).
        """
        # V2 format: check for config reference
        config_ref = data.get("config")
        if config_ref:
            self._add_row("Config", config_ref)

        # V2 format: check for FOV set reference
        fovs_ref = data.get("fovs")
        if fovs_ref and fovs_ref != "default":
            self._add_row("FOV Set", fovs_ref)

        # Channels (may be inline or from config)
        channels = data.get("channels", [])
        if channels:
            self._add_row("Channels", ", ".join(channels))
        elif config_ref:
            self._add_row("Channels", "(from config)")

        # Z-stack parameters section (may be inline or from config)
        z_planes = data.get("z_planes")
        z_step_um = data.get("z_step_um")

        if z_planes is not None:
            self._add_row("Z Planes", str(z_planes))
        if z_step_um is not None:
            self._add_row("Z Step (µm)", f"{z_step_um:.2f}")

        # Calculate and show total z-range
        if z_planes is not None and z_step_um is not None and z_planes > 1:
            total_z_range = (z_planes - 1) * z_step_um
            self._add_row("Total Z Range (µm)", f"{total_z_range:.2f}")

        # Z-stack mode if specified
        z_mode = data.get("z_mode", data.get("z_stack_mode"))
        if z_mode:
            self._add_row("Z-Stack Mode", str(z_mode))

        # Focus settings (show if explicitly set)
        if "use_autofocus" in data:
            self._add_row("Use Autofocus", str(data.get("use_autofocus", False)))
        if "use_focus_lock" in data:
            self._add_row("Use Focus Lock", str(data.get("use_focus_lock", True)))

        # Exposure settings
        if data.get("exposure_time_ms"):
            self._add_row("Exposure (ms)", str(data.get("exposure_time_ms")))

        # Per-channel exposures if specified
        channel_exposures = data.get("channel_exposures", {})
        if channel_exposures:
            for ch_name, exp_ms in channel_exposures.items():
                self._add_row(f"  {ch_name} Exposure (ms)", str(exp_ms))

        if "skip_saving" in data:
            self._add_row("Skip Saving", str(data.get("skip_saving", False)))

    def _show_fluidics_params(self, data: Dict[str, Any]) -> None:
        """Show fluidics operation parameters.

        Handles both V2 format (protocol reference) and legacy format (inline params).
        """
        # V2 format: check for protocol reference
        protocol_ref = data.get("protocol")
        if protocol_ref:
            self._add_row("Protocol", protocol_ref)

        # Legacy/inline format parameters
        command = data.get("command", "")
        if command:
            self._add_row("Command", str(command))
        if data.get("solution"):
            self._add_row("Solution", str(data.get("solution")))
        if data.get("volume_ul"):
            self._add_row("Volume (µL)", str(data.get("volume_ul")))
        if data.get("flow_rate_ul_per_min"):
            self._add_row("Flow Rate (µL/min)", str(data.get("flow_rate_ul_per_min")))
        if data.get("duration_s"):
            self._add_row("Duration (s)", str(data.get("duration_s")))

    def _show_wait_params(self, data: Dict[str, Any]) -> None:
        """Show wait operation parameters."""
        self._add_row("Duration (s)", str(data.get("duration_seconds", 0)))
        if data.get("message"):
            self._add_row("Message", str(data.get("message")))

    def _show_intervention_params(self, data: Dict[str, Any]) -> None:
        """Show intervention operation parameters."""
        self._add_row("Message", data.get("message", ""))

    def show_fov(self, fov_task: "FovTask") -> None:
        """Show FOV task parameters.

        Args:
            fov_task: FOV task object
        """
        self._header_label.setText(f"FOV: {fov_task.fov_id}")

        self._table.setRowCount(0)

        self._add_row("FOV ID", fov_task.fov_id)
        self._add_row("Region", fov_task.region_id)
        self._add_row("Index", str(fov_task.fov_index))
        self._add_row("X (mm)", f"{fov_task.x_mm:.4f}")
        self._add_row("Y (mm)", f"{fov_task.y_mm:.4f}")
        self._add_row("Z (mm)", f"{fov_task.z_mm:.4f}")
        self._add_row("Status", fov_task.status.name)
        self._add_row("Attempt", str(fov_task.attempt))

        if fov_task.error_message:
            self._add_row("Error", fov_task.error_message)

        # Show metadata if any
        if fov_task.metadata:
            for key, value in fov_task.metadata.items():
                self._add_row(f"[meta] {key}", str(value))

    def show_fov_summary(
        self,
        fov_id: str,
        region_id: str,
        fov_index: int,
        x_mm: float,
        y_mm: float,
        status: str,
        z_mm: float = 0.0,
    ) -> None:
        """Show FOV summary without full FovTask object.

        Args:
            fov_id: FOV identifier
            region_id: Region identifier
            fov_index: FOV index
            x_mm: X position in mm
            y_mm: Y position in mm
            status: Status string
            z_mm: Z position in mm
        """
        self._header_label.setText(f"FOV: {fov_id}")

        self._table.setRowCount(0)

        self._add_row("FOV ID", fov_id)
        self._add_row("Region", region_id)
        self._add_row("Index", str(fov_index))
        self._add_row("X (mm)", f"{x_mm:.4f}")
        self._add_row("Y (mm)", f"{y_mm:.4f}")
        self._add_row("Z (mm)", f"{z_mm:.4f}")
        self._add_row("Status", status)

    def _add_row(self, key: str, value: str) -> None:
        """Add a row to the table.

        Args:
            key: Parameter name
            value: Parameter value
        """
        row = self._table.rowCount()
        self._table.insertRow(row)

        key_item = QTableWidgetItem(key)
        key_item.setFont(QFont("", -1, QFont.Bold))
        self._table.setItem(row, 0, key_item)

        value_item = QTableWidgetItem(value)
        self._table.setItem(row, 1, value_item)
