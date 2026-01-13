"""
Protocol Loader Dialog for selecting and validating experiment protocols.
"""

from pathlib import Path
from typing import Optional, List

from PyQt5.QtCore import Qt, pyqtSignal
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

    protocol_selected = pyqtSignal(object, str, str)  # protocol, base_path, experiment_id

    def __init__(
        self,
        available_channels: Optional[List[str]] = None,
        default_path: str = "",
        parent: Optional[QDialog] = None,
    ):
        super().__init__(parent)
        self._loader = ProtocolLoader()
        self._available_channels = available_channels or []
        self._default_path = default_path
        self._current_protocol: Optional[ExperimentProtocol] = None

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

        p = self._current_protocol
        lines = [
            f"Name: {p.name}",
            f"Version: {p.version}",
            f"Description: {p.description}" if p.description else "",
            f"Author: {p.author}" if p.author else "",
            "",
            f"Total Rounds: {len(p.rounds)}",
            f"Imaging Rounds: {p.total_imaging_rounds()}",
            "",
            "Rounds:",
        ]

        for i, r in enumerate(p.rounds):
            has_imaging = "+" if r.imaging else "-"
            has_fluidics = "+" if r.fluidics else "-"
            intervention = " [!]" if r.requires_intervention else ""
            lines.append(
                f"  {i + 1}. {r.name} [{r.type.value}] "
                f"F:{has_fluidics} I:{has_imaging}{intervention}"
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
        """Validate all inputs and enable/disable start button."""
        output_path = self._output_edit.text().strip()
        valid = (
            self._current_protocol is not None
            and bool(output_path)
            and Path(output_path).exists()
        )
        self._start_btn.setEnabled(valid)

    def _on_start_clicked(self) -> None:
        """Handle start button click."""
        if self._current_protocol is None:
            return

        experiment_id = (
            self._id_edit.text().strip()
            or self._current_protocol.name
        )

        self.protocol_selected.emit(
            self._current_protocol,
            self._output_edit.text(),
            experiment_id,
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
