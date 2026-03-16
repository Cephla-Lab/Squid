"""
Protocol Loader Dialog for selecting and validating experiment protocols.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QGroupBox,
    QTextEdit,
    QMessageBox,
)

from squid.core.events import ClearScanCoordinatesCommand, LoadScanCoordinatesCommand
from squid.core.protocol import ExperimentProtocol, ProtocolLoader, ProtocolValidationError

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class ProtocolLoaderDialog(QDialog):
    """Dialog for selecting and previewing experiment protocols.

    Features:
    - Browse for protocol files
    - Recently used protocols list
    - Protocol preview (name, description, rounds)
    - Channel validation
    - Output path selection
    """

    def __init__(
        self,
        available_channels: Optional[List[str]] = None,
        default_path: str = "",
        parent: Optional[QDialog] = None,
        event_bus: Optional[object] = None,
    ):
        super().__init__(parent)
        self._loader = ProtocolLoader()
        self._available_channels = available_channels or []
        self._default_path = default_path
        self._current_protocol: Optional[ExperimentProtocol] = None
        self._fov_positions: Dict[str, List[Tuple[float, float, float]]] = {}
        self._fov_path: Optional[str] = None
        self._event_bus = event_bus if event_bus is not None else getattr(parent, "_bus", None)

        self.setWindowTitle("Load Experiment Protocol")
        self.setMinimumSize(600, 500)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)

        # Protocol file selection
        file_group = QGroupBox("Protocol File")
        file_layout = QHBoxLayout(file_group)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Select protocol file...")
        file_layout.addWidget(self._path_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._on_browse_clicked)
        file_layout.addWidget(browse_btn)

        layout.addWidget(file_group)

        # Protocol preview
        preview_group = QGroupBox("Protocol Preview")
        preview_layout = QVBoxLayout(preview_group)

        self._preview_text = QTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_text.setPlaceholderText("Load a protocol to see preview...")
        preview_layout.addWidget(self._preview_text)

        layout.addWidget(preview_group)

        # Output path selection
        output_group = QGroupBox("Output Directory")
        output_layout = QHBoxLayout(output_group)

        self._output_edit = QLineEdit()
        self._output_edit.setText(self._default_path)
        self._output_edit.setPlaceholderText("Select output directory...")
        output_layout.addWidget(self._output_edit)

        output_browse_btn = QPushButton("Browse...")
        output_browse_btn.clicked.connect(self._on_output_browse_clicked)
        output_layout.addWidget(output_browse_btn)

        layout.addWidget(output_group)

        # FOV Positions section
        fov_group = QGroupBox("FOV Positions")
        fov_layout = QVBoxLayout(fov_group)

        fov_file_layout = QHBoxLayout()
        self._fov_path_edit = QLineEdit()
        self._fov_path_edit.setPlaceholderText("Select FOV positions file (CSV)...")
        self._fov_path_edit.setReadOnly(True)
        fov_file_layout.addWidget(self._fov_path_edit)

        fov_browse_btn = QPushButton("Browse...")
        fov_browse_btn.clicked.connect(self._on_fov_browse_clicked)
        fov_file_layout.addWidget(fov_browse_btn)
        fov_layout.addLayout(fov_file_layout)

        self._fov_status_label = QLabel("No FOV positions loaded")
        self._fov_status_label.setStyleSheet("color: #888;")
        fov_layout.addWidget(self._fov_status_label)

        layout.addWidget(fov_group)

        # Experiment ID
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel("Experiment ID:"))
        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("(auto-generated from protocol name)")
        id_layout.addWidget(self._id_edit)
        layout.addLayout(id_layout)

        # Validation status
        self._validation_label = QLabel("")
        self._validation_label.setWordWrap(True)
        layout.addWidget(self._validation_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self._start_btn = QPushButton("Start Experiment")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self._start_btn)

        layout.addLayout(button_layout)

    def _on_browse_clicked(self) -> None:
        """Handle browse button click."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Protocol File",
            "",
            "YAML Files (*.yaml *.yml);;All Files (*.*)",
        )
        if path:
            self._path_edit.setText(path)
            self._load_protocol(path)

    def _on_output_browse_clicked(self) -> None:
        """Handle output directory browse."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self._output_edit.text() or "",
        )
        if path:
            self._output_edit.setText(path)
            self._validate_inputs()

    def _on_fov_browse_clicked(self) -> None:
        """Handle FOV positions file browse."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select FOV Positions File",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if path:
            self._load_fov_positions(path)

    def _publish_fov_preview(
        self,
        positions: Dict[str, List[Tuple[float, float, float]]],
    ) -> None:
        """Publish preview FOVs so the navigation window mirrors the loaded protocol."""
        if self._event_bus is None or not hasattr(self._event_bus, "publish"):
            return

        self._event_bus.publish(ClearScanCoordinatesCommand(clear_displayed_fovs=True))
        if not positions:
            return

        region_fov_coordinates = {
            region_id: tuple(tuple(float(v) for v in coord) for coord in coords)
            for region_id, coords in positions.items()
        }
        region_centers = {
            region_id: (
                float(sum(coord[0] for coord in coords) / len(coords)),
                float(sum(coord[1] for coord in coords) / len(coords)),
                float(sum(coord[2] for coord in coords) / len(coords)),
            )
            for region_id, coords in positions.items()
            if coords
        }
        self._event_bus.publish(
            LoadScanCoordinatesCommand(
                region_fov_coordinates=region_fov_coordinates,
                region_centers=region_centers,
            )
        )

    def _load_fov_positions(self, path: str) -> None:
        """Load FOV positions from a CSV file.

        CSV format: region_id/x_mm/y_mm(/z_mm) or region/x (mm)/y (mm).
        First row is header (required).

        Args:
            path: Path to the CSV file
        """
        import csv

        try:
            positions: Dict[str, List[Tuple[float, float, float]]] = {}

            with open(path, "r", newline="") as f:
                reader = csv.reader(f)
                # Skip header row
                header = next(reader, None)
                if header is None:
                    raise ValueError("Empty CSV file")

                column_map = {}
                for idx, col in enumerate(header):
                    col_lower = col.strip().lower()
                    if "region" in col_lower:
                        column_map["region"] = idx
                    elif col_lower in ("x", "x_mm", "x (mm)") or ("x" in col_lower and "mm" in col_lower):
                        column_map["x"] = idx
                    elif col_lower in ("y", "y_mm", "y (mm)") or ("y" in col_lower and "mm" in col_lower):
                        column_map["y"] = idx
                    elif col_lower in ("z", "z_mm", "z (mm)") or ("z" in col_lower and "mm" in col_lower):
                        column_map["z"] = idx

                if not all(k in column_map for k in ("region", "x", "y")):
                    raise ValueError(
                        f"CSV must have region, x, y columns. Found: {header}"
                    )

                for row_num, row in enumerate(reader, start=2):
                    if len(row) <= max(column_map.values()):
                        _log.warning(
                            f"Row {row_num}: Expected columns {header}, got {len(row)} value(s)"
                        )
                        continue

                    region_id = row[column_map["region"]].strip()
                    try:
                        x = float(row[column_map["x"]])
                        y = float(row[column_map["y"]])
                        z = float(row[column_map["z"]]) if "z" in column_map else 0.0
                    except ValueError as e:
                        _log.warning(f"Row {row_num}: Invalid numeric value: {e}")
                        continue

                    if region_id not in positions:
                        positions[region_id] = []
                    positions[region_id].append((x, y, z))

            total_fovs = sum(len(coords) for coords in positions.values())
            num_regions = len(positions)

            if total_fovs == 0:
                self._fov_status_label.setText("No valid FOV positions found in file")
                self._fov_status_label.setStyleSheet("color: #f44336;")
                self._fov_positions = {}
                self._fov_path = None
                self._fov_path_edit.clear()
                self._publish_fov_preview({})
            else:
                self._fov_positions = positions
                self._fov_path = path
                self._fov_path_edit.setText(path)
                self._fov_status_label.setText(
                    f"Loaded {total_fovs} FOVs in {num_regions} region(s)"
                )
                self._fov_status_label.setStyleSheet("color: #4CAF50;")
                self._publish_fov_preview(positions)

            self._validate_inputs()

        except Exception as e:
            _log.error(f"Failed to load FOV positions: {e}")
            self._fov_status_label.setText(f"Error: {e}")
            self._fov_status_label.setStyleSheet("color: #f44336;")
            self._fov_positions = {}
            self._fov_path = None
            self._publish_fov_preview({})

    def _load_protocol(self, path: str) -> None:
        """Load and preview a protocol."""
        try:
            self._current_protocol = self._loader.load(path)
            self._show_preview()
            self._validate_channels()

            # Auto-fill output directory from protocol if specified
            if hasattr(self._current_protocol, 'output_directory') and self._current_protocol.output_directory:
                output_dir = str(self._current_protocol.output_directory)
                # Expand ~ to home directory
                output_dir = str(Path(output_dir).expanduser())
                if not self._output_edit.text():
                    self._output_edit.setText(output_dir)

            # Auto-load the run-level FOV file for preview if specified.
            if self._current_protocol.fov_file:
                fov_file = self._current_protocol.fov_file
                if Path(fov_file).exists():
                    self._load_fov_positions(fov_file)
                else:
                    self._fov_status_label.setText(f"FOV file not found: {fov_file}")
                    self._fov_status_label.setStyleSheet("color: #f44336;")

            self._validate_inputs()

            # Auto-fill experiment ID
            if not self._id_edit.text():
                self._id_edit.setText(self._current_protocol.name)

        except ProtocolValidationError as e:
            self._current_protocol = None
            self._preview_text.setPlainText(f"Error loading protocol:\n{e}")
            self._validation_label.setText(
                f'<span style="color: red;">Validation failed</span>'
            )
            self._start_btn.setEnabled(False)

        except Exception as e:
            self._current_protocol = None
            self._preview_text.setPlainText(f"Error: {e}")
            self._start_btn.setEnabled(False)

    def _show_preview(self) -> None:
        """Show protocol preview."""
        if self._current_protocol is None:
            return

        from squid.core.protocol import FluidicsStep, ImagingStep, InterventionStep

        p = self._current_protocol
        author = getattr(p, "author", "")
        lines = [
            f"Name: {p.name}",
            f"Version: {p.version}",
            f"Description: {p.description}" if p.description else "",
            f"Author: {author}" if author else "",
            "",
            f"Total Rounds: {len(p.rounds)}",
            f"Imaging Steps: {p.total_imaging_steps()}",
            "",
            "Rounds:",
        ]

        for i, r in enumerate(p.rounds):
            # Check step types in V2 format
            has_imaging = any(isinstance(s, ImagingStep) for s in r.steps)
            has_fluidics = any(isinstance(s, FluidicsStep) for s in r.steps)
            has_intervention = any(isinstance(s, InterventionStep) for s in r.steps)

            imaging_flag = "+" if has_imaging else "-"
            fluidics_flag = "+" if has_fluidics else "-"
            intervention_marker = " [!]" if has_intervention else ""
            lines.append(
                f"  {i + 1}. {r.name} "
                f"F:{fluidics_flag} I:{imaging_flag}{intervention_marker}"
            )

        self._preview_text.setPlainText("\n".join(lines))

    def _validate_channels(self) -> None:
        """Validate protocol channels against available channels."""
        if self._current_protocol is None or not self._available_channels:
            return

        errors = self._loader.validate_channels(
            self._current_protocol,
            self._available_channels,
        )

        if errors:
            self._validation_label.setText(
                f'<span style="color: orange;">Warning: {len(errors)} channel(s) not found</span>'
            )
            _log.warning(f"Channel validation: {errors}")
        else:
            self._validation_label.setText(
                '<span style="color: green;">All channels valid</span>'
            )

    def _validate_inputs(self) -> None:
        """Validate all inputs and enable/disable start button.

        Requirements for starting:
        1. Protocol must be loaded
        2. Output path must be valid
        3. If protocol has imaging rounds, the protocol must define a run-level
           FOV file and it must be previewed successfully
        """
        output_path = self._output_edit.text().strip()
        output_dir = Path(output_path).expanduser() if output_path else None
        output_valid = False
        if output_dir is not None:
            if output_dir.exists():
                output_valid = output_dir.is_dir()
            else:
                parent_dir = output_dir.parent
                output_valid = parent_dir.exists() and parent_dir.is_dir()
        valid = (
            self._current_protocol is not None
            and bool(output_path)
            and output_valid
        )

        # Check FOV requirement for imaging protocols
        fov_required = False
        fov_loaded = bool(self._fov_positions)

        if self._current_protocol is not None:
            from squid.core.protocol import ImagingStep

            fov_required = any(
                isinstance(step, ImagingStep)
                for round_ in self._current_protocol.rounds
                for step in round_.steps
            )

        if fov_required and not self._current_protocol.fov_file:
            self._validation_label.setText(
                '<span style="color: orange;">Protocol imaging rounds require resources.fov_file</span>'
            )
            valid = False
        elif fov_required and not fov_loaded:
            self._validation_label.setText(
                '<span style="color: orange;">Protocol FOV file must load successfully for imaging rounds</span>'
            )
            valid = False
        elif valid and fov_required and fov_loaded:
            total_fovs = sum(len(coords) for coords in self._fov_positions.values())
            self._validation_label.setText(
                f'<span style="color: green;">Ready: {total_fovs} FOVs loaded</span>'
            )
        elif valid and not fov_required:
            self._validation_label.setText(
                '<span style="color: green;">Ready: FOVs not required</span>'
            )

        self._start_btn.setEnabled(valid)

    def _on_start_clicked(self) -> None:
        """Handle start button click."""
        if self._current_protocol is None:
            return

        output_path = Path(self._output_edit.text().strip()).expanduser()
        try:
            output_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Output Directory Error",
                f"Could not create output directory:\n{output_path}\n\n{exc}",
            )
            return

        experiment_id = (
            self._id_edit.text().strip()
            or self._current_protocol.name
        )

        self.accept()

    def get_protocol(self) -> Optional[ExperimentProtocol]:
        """Get the loaded protocol."""
        return self._current_protocol

    def get_protocol_path(self) -> str:
        """Get the selected protocol path."""
        return self._path_edit.text()

    def get_output_path(self) -> str:
        """Get the selected output path."""
        return self._output_edit.text()

    def get_experiment_id(self) -> str:
        """Get the experiment ID."""
        return self._id_edit.text() or (
            self._current_protocol.name if self._current_protocol else ""
        )

    # Properties for convenience
    @property
    def protocol_path(self) -> str:
        """Protocol file path."""
        return self.get_protocol_path()

    @property
    def base_path(self) -> str:
        """Output base path."""
        return self.get_output_path()

    @property
    def experiment_id(self) -> str:
        """Experiment ID."""
        return self.get_experiment_id()

    def get_fov_positions(self) -> Dict[str, List[Tuple[float, float, float]]]:
        """Get the loaded FOV positions.

        Returns:
            Dict mapping region_id to list of (x_mm, y_mm, z_mm) tuples
        """
        return self._fov_positions.copy()

    def get_fov_path(self) -> Optional[str]:
        """Get the path to the loaded FOV positions file."""
        return self._fov_path

    @property
    def fov_positions(self) -> Dict[str, List[Tuple[float, float, float]]]:
        """FOV positions dict."""
        return self.get_fov_positions()
