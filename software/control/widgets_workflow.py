"""
Workflow Runner UI Components.

Dialog and widgets for configuring and running workflow sequences.
"""

import os
from typing import Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from control.workflow_runner import SequenceItem, SequenceType, Workflow

import squid.logging


def _confirm_missing_file(parent: QWidget, file_path: str, file_type: str) -> bool:
    """Ask user to confirm adding a non-existent file. Returns True if confirmed or file exists."""
    if not file_path or os.path.exists(file_path):
        return True
    reply = QMessageBox.question(
        parent,
        f"{file_type} Not Found",
        f"{file_type} '{file_path}' does not exist. Add anyway?",
        QMessageBox.Yes | QMessageBox.No,
    )
    return reply == QMessageBox.Yes


class AddSequenceDialog(QDialog):
    """Dialog for adding or editing a script sequence."""

    def __init__(self, parent=None, edit_data: dict = None):
        """
        Initialize dialog.

        Args:
            parent: Parent widget
            edit_data: If provided, pre-populate fields for editing. Keys: name, script_path,
                      arguments, python_path, conda_env
        """
        super().__init__(parent)
        self._edit_mode = edit_data is not None
        self.setWindowTitle("Edit Sequence" if self._edit_mode else "Add Sequence")
        self.setMinimumWidth(500)
        self._setup_ui()
        if edit_data:
            self._populate_from_data(edit_data)

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Name
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g., Liquid Handling, Robotic Arm, Fluidics Control")
        layout.addRow("Name:", self.edit_name)

        # Script path with browse button
        script_layout = QHBoxLayout()
        self.edit_script_path = QLineEdit()
        self.edit_script_path.setPlaceholderText("/home/user/scripts/fluidics_control.py")
        script_layout.addWidget(self.edit_script_path)

        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_script)
        script_layout.addWidget(self.btn_browse)
        layout.addRow("Script Path:", script_layout)

        # Arguments
        self.edit_arguments = QLineEdit()
        self.edit_arguments.setPlaceholderText("--wash --cycles 3 --volume 500")
        layout.addRow("Arguments:", self.edit_arguments)

        # Separator for environment options
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        layout.addRow(separator)

        env_label = QLabel("Python Environment (choose one):")
        env_label.setStyleSheet("font-weight: bold;")
        layout.addRow(env_label)

        # Python executable path (optional)
        python_layout = QHBoxLayout()
        self.edit_python_path = QLineEdit()
        self.edit_python_path.setPlaceholderText("/usr/bin/python3.10 or /home/user/venv/bin/python")
        python_layout.addWidget(self.edit_python_path)

        self.btn_browse_python = QPushButton("Browse...")
        self.btn_browse_python.clicked.connect(self._browse_python)
        python_layout.addWidget(self.btn_browse_python)
        layout.addRow("Python Path:", python_layout)

        # Conda environment (optional)
        self.edit_conda_env = QLineEdit()
        self.edit_conda_env.setPlaceholderText("fluidics_env, squid, base")
        layout.addRow("Conda Env:", self.edit_conda_env)

        # Help text
        help_text = QLabel(
            "<small><i>Leave both empty to use Squid's Python (recommended).<br>"
            "If Conda Env is set, Python Path is ignored.</i></small>"
        )
        help_text.setStyleSheet("color: gray;")
        layout.addRow(help_text)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Save" if self._edit_mode else "Add")
        self.btn_add.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addRow(btn_layout)

    def _populate_from_data(self, data: dict):
        """Pre-populate form fields from existing data."""
        field_mapping = {
            "name": self.edit_name,
            "script_path": self.edit_script_path,
            "arguments": self.edit_arguments,
            "python_path": self.edit_python_path,
            "conda_env": self.edit_conda_env,
        }
        for key, widget in field_mapping.items():
            if data.get(key):
                widget.setText(data[key])

    def _browse_script(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Script", "", "Python Scripts (*.py);;Shell Scripts (*.sh);;All Files (*)"
        )
        if file_path:
            self.edit_script_path.setText(file_path)

    def _browse_python(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Python Executable", "/usr/bin", "All Files (*)")
        if file_path:
            self.edit_python_path.setText(file_path)

    def _validate_and_accept(self):
        name = self.edit_name.text().strip()
        script_path = self.edit_script_path.text().strip()

        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return
        if name.lower() == "acquisition":
            QMessageBox.warning(self, "Validation Error", "'Acquisition' is reserved.")
            return
        if not script_path:
            QMessageBox.warning(self, "Validation Error", "Script path is required.")
            return
        if not _confirm_missing_file(self, script_path, "Script"):
            return
        if not _confirm_missing_file(self, self.edit_python_path.text().strip(), "Python executable"):
            return

        self.accept()

    def get_sequence_data(self) -> dict:
        return {
            "name": self.edit_name.text().strip(),
            "script_path": self.edit_script_path.text().strip(),
            "arguments": self.edit_arguments.text().strip() or None,
            "python_path": self.edit_python_path.text().strip() or None,
            "conda_env": self.edit_conda_env.text().strip() or None,
        }


class AddAcquisitionDialog(QDialog):
    """Dialog for adding or editing an acquisition sequence."""

    def __init__(self, parent=None, edit_data: dict = None):
        """
        Initialize dialog.

        Args:
            parent: Parent widget
            edit_data: If provided, pre-populate fields for editing. Keys: name, config_path
        """
        super().__init__(parent)
        self._edit_mode = edit_data is not None
        self.setWindowTitle("Edit Acquisition" if self._edit_mode else "Add Acquisition")
        self.setMinimumWidth(500)
        self._setup_ui()
        if edit_data:
            self._populate_from_data(edit_data)

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Name
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g., Acquisition, Pre-scan, Post-treatment scan")
        self.edit_name.setText("Acquisition")
        layout.addRow("Name:", self.edit_name)

        # Config path with browse button
        config_layout = QHBoxLayout()
        self.edit_config_path = QLineEdit()
        self.edit_config_path.setPlaceholderText("(Optional) /path/to/acquisition.yaml")
        config_layout.addWidget(self.edit_config_path)

        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_config)
        config_layout.addWidget(self.btn_browse)
        layout.addRow("Config File:", config_layout)

        # Help text
        help_text = QLabel(
            "<small><i>Leave empty to use current software settings.<br>"
            "If a YAML file is provided, acquisition settings will be<br>"
            "loaded from the file before running.</i></small>"
        )
        help_text.setStyleSheet("color: gray;")
        layout.addRow(help_text)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Save" if self._edit_mode else "Add")
        self.btn_add.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addRow(btn_layout)

    def _populate_from_data(self, data: dict):
        """Pre-populate form fields from existing data."""
        for key, widget in [("name", self.edit_name), ("config_path", self.edit_config_path)]:
            if data.get(key):
                widget.setText(data[key])

    def _browse_config(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Acquisition Config", "", "YAML Files (*.yaml *.yml);;All Files (*)"
        )
        if file_path:
            self.edit_config_path.setText(file_path)

    def _validate_and_accept(self):
        name = self.edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return
        if not _confirm_missing_file(self, self.edit_config_path.text().strip(), "Config file"):
            return
        self.accept()

    def get_sequence_data(self) -> dict:
        return {
            "name": self.edit_name.text().strip(),
            "config_path": self.edit_config_path.text().strip() or None,
        }


class WorkflowRunnerDialog(QDialog):
    """Dialog for configuring and running workflow sequences."""

    signal_run_workflow = Signal(object)  # Emitted when Run is clicked, passes Workflow
    signal_pause_workflow = Signal()  # Emitted when Pause is clicked
    signal_resume_workflow = Signal()  # Emitted when Resume is clicked
    signal_stop_workflow = Signal()  # Emitted when Stop is clicked

    # Column indices
    COL_INCLUDE = 0
    COL_NAME = 1
    COL_COMMAND = 2
    COL_CYCLE_ARG = 3
    COL_CYCLE_VALUES = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._workflow = Workflow.create_default()
        self._is_running = False
        self._is_paused = False
        self._setup_ui()
        self._load_workflow_to_table()

    def _setup_ui(self):
        self.setWindowTitle("Workflow Runner")
        self.setMinimumSize(750, 550)
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            "Define sequences to run. 'Acquisition' runs the built-in acquisition "
            "with current settings. Other sequences run external scripts."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Table
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._setup_table_columns()
        layout.addWidget(self.table)

        # Cycles section
        cycles_layout = QHBoxLayout()
        cycles_label = QLabel("Cycles:")
        cycles_layout.addWidget(cycles_label)
        self.spinbox_cycles = QSpinBox()
        self.spinbox_cycles.setMinimum(1)
        self.spinbox_cycles.setMaximum(1000)
        self.spinbox_cycles.setValue(1)
        self.spinbox_cycles.valueChanged.connect(self._on_cycles_changed)
        cycles_layout.addWidget(self.spinbox_cycles)
        cycles_layout.addStretch()
        layout.addLayout(cycles_layout)

        # All buttons in one row
        btn_layout = QHBoxLayout()

        self.btn_insert_above = QPushButton("Insert Above")
        self.btn_insert_above.clicked.connect(lambda: self._insert_sequence(above=True))
        btn_layout.addWidget(self.btn_insert_above)

        self.btn_insert_below = QPushButton("Insert Below")
        self.btn_insert_below.clicked.connect(lambda: self._insert_sequence(above=False))
        btn_layout.addWidget(self.btn_insert_below)

        self.btn_edit = QPushButton("Edit")
        self.btn_edit.clicked.connect(self._edit_sequence)
        btn_layout.addWidget(self.btn_edit)

        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove_sequence)
        btn_layout.addWidget(self.btn_remove)

        self.btn_save = QPushButton("Save...")
        self.btn_save.clicked.connect(self._save_workflow)
        btn_layout.addWidget(self.btn_save)

        self.btn_load = QPushButton("Load...")
        self.btn_load.clicked.connect(self._load_workflow)
        btn_layout.addWidget(self.btn_load)

        btn_layout.addStretch()

        self.btn_run = QPushButton("Run")
        self.btn_run.setStyleSheet("background-color: #C2C2FF; font-weight: bold;")
        self.btn_run.clicked.connect(self._run_workflow)
        btn_layout.addWidget(self.btn_run)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._pause_workflow)
        self.btn_pause.setEnabled(False)
        btn_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._stop_workflow)
        self.btn_stop.setEnabled(False)
        btn_layout.addWidget(self.btn_stop)

        layout.addLayout(btn_layout)

        # Status label
        self.label_status = QLabel("")
        layout.addWidget(self.label_status)

        # Script output area
        output_header_layout = QHBoxLayout()
        output_label = QLabel("Log:")
        output_header_layout.addWidget(output_label)
        output_header_layout.addStretch()
        self.btn_save_log = QPushButton("Save Log...")
        self.btn_save_log.clicked.connect(self._save_log)
        output_header_layout.addWidget(self.btn_save_log)
        layout.addLayout(output_header_layout)

        self.text_output = QTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setMaximumHeight(150)
        self.text_output.setStyleSheet("font-family: monospace; font-size: 10pt;")
        layout.addWidget(self.text_output)

    def _setup_table_columns(self):
        """Configure table columns."""
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Include", "Name", "Command/Path", "Cycle Arg", "Cycle Arg Values"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # Command column stretches
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

    def _on_cycles_changed(self, value):
        """Handle cycles spinbox value change."""
        self._workflow.num_cycles = value

    def _load_workflow_to_table(self):
        """Populate table from workflow data."""
        self.table.setRowCount(len(self._workflow.sequences))

        for row, seq in enumerate(self._workflow.sequences):
            self._populate_table_row(row, seq)

    def _populate_table_row(self, row: int, seq: SequenceItem):
        """Populate a single table row with sequence data."""
        is_acq = seq.is_acquisition()

        # Include checkbox
        checkbox = QCheckBox()
        checkbox.setChecked(seq.included)
        checkbox.toggled.connect(lambda checked, r=row: self._on_include_toggled(r, checked))
        cell_widget = QWidget()
        cell_layout = QHBoxLayout(cell_widget)
        cell_layout.addWidget(checkbox)
        cell_layout.setAlignment(Qt.AlignCenter)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(row, self.COL_INCLUDE, cell_widget)

        # Name
        name_item = QTableWidgetItem(seq.name)
        self._apply_acquisition_styling(name_item, is_acq)
        self.table.setItem(row, self.COL_NAME, name_item)

        # Command
        cmd_item = self._create_command_item(seq)
        self.table.setItem(row, self.COL_COMMAND, cmd_item)

        # Cycle Arg name
        cycle_arg_item = QTableWidgetItem(seq.cycle_arg_name or "")
        self._apply_acquisition_styling(cycle_arg_item, is_acq, include_foreground=True)
        self.table.setItem(row, self.COL_CYCLE_ARG, cycle_arg_item)

        # Cycle Values
        cycle_values_item = QTableWidgetItem(seq.cycle_arg_values or "")
        self._apply_acquisition_styling(cycle_values_item, is_acq, include_foreground=True)
        self.table.setItem(row, self.COL_CYCLE_VALUES, cycle_values_item)

    def _create_command_item(self, seq: SequenceItem) -> QTableWidgetItem:
        """Create the command column item for a sequence."""
        if seq.is_acquisition():
            if seq.config_path:
                cmd_text = f"Config: {os.path.basename(seq.config_path)}"
            else:
                cmd_text = "(Current Settings)"
            item = QTableWidgetItem(cmd_text)
            self._apply_acquisition_styling(item, is_acquisition=True, include_foreground=True)
            return item

        cmd_text = seq.script_path or ""
        if seq.arguments:
            cmd_text += f" {seq.arguments}"
        if seq.conda_env:
            cmd_text = f"[{seq.conda_env}] {cmd_text}"
        elif seq.python_path:
            cmd_text = f"[{os.path.basename(seq.python_path)}] {cmd_text}"
        return QTableWidgetItem(cmd_text)

    def _apply_acquisition_styling(
        self, item: QTableWidgetItem, is_acquisition: bool, include_foreground: bool = False
    ):
        """Apply read-only styling for acquisition sequence items."""
        if not is_acquisition:
            return
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setBackground(QColor(240, 240, 255))  # Light blue
        if include_foreground:
            item.setForeground(QColor(128, 128, 128))  # Gray text

    def _on_include_toggled(self, row: int, checked: bool):
        """Handle include checkbox toggle."""
        if row < len(self._workflow.sequences):
            self._workflow.sequences[row].included = checked

    def _prompt_sequence_type(self) -> Optional[str]:
        """Prompt user to choose between script and acquisition. Returns 'script', 'acquisition', or None."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Sequence Type")
        msg_box.setText("What type of sequence do you want to add?")
        btn_script = msg_box.addButton("Script", QMessageBox.ActionRole)
        btn_acquisition = msg_box.addButton("Acquisition", QMessageBox.ActionRole)
        msg_box.addButton("Cancel", QMessageBox.RejectRole)
        msg_box.exec_()

        clicked = msg_box.clickedButton()
        if clicked == btn_script:
            return "script"
        elif clicked == btn_acquisition:
            return "acquisition"
        return None

    def _create_sequence_from_dialog(self, sequence_type: str) -> Optional[SequenceItem]:
        """Show dialog and create SequenceItem. Returns None if cancelled."""
        if sequence_type == "acquisition":
            dialog = AddAcquisitionDialog(self)
            if dialog.exec_() != QDialog.Accepted:
                return None
            seq_data = dialog.get_sequence_data()
            return SequenceItem(
                name=seq_data["name"],
                sequence_type=SequenceType.ACQUISITION,
                config_path=seq_data["config_path"],
                included=True,
            )
        else:
            dialog = AddSequenceDialog(self)
            if dialog.exec_() != QDialog.Accepted:
                return None
            seq_data = dialog.get_sequence_data()
            return SequenceItem(
                name=seq_data["name"],
                sequence_type=SequenceType.SCRIPT,
                script_path=seq_data["script_path"],
                arguments=seq_data["arguments"],
                python_path=seq_data["python_path"],
                conda_env=seq_data["conda_env"],
                included=True,
            )

    def _insert_sequence(self, above: bool, sequence_type: str = None):
        """Insert a new sequence above or below current selection."""
        if sequence_type is None:
            sequence_type = self._prompt_sequence_type()
            if sequence_type is None:
                return

        new_seq = self._create_sequence_from_dialog(sequence_type)
        if new_seq is None:
            return

        current_row = self.table.currentRow()
        if current_row < 0:
            insert_idx = 0 if above else len(self._workflow.sequences)
        else:
            insert_idx = current_row if above else current_row + 1

        self._workflow.sequences.insert(insert_idx, new_seq)
        self._load_workflow_to_table()
        self.table.selectRow(insert_idx)
        self.label_status.setText(f"Added sequence '{new_seq.name}'")

    def _edit_sequence(self):
        """Edit the selected sequence."""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a sequence to edit.")
            return

        seq = self._workflow.sequences[current_row]

        if seq.is_acquisition():
            edit_data = {"name": seq.name, "config_path": seq.config_path}
            dialog = AddAcquisitionDialog(self, edit_data=edit_data)
        else:
            edit_data = {
                "name": seq.name,
                "script_path": seq.script_path,
                "arguments": seq.arguments,
                "python_path": seq.python_path,
                "conda_env": seq.conda_env,
            }
            dialog = AddSequenceDialog(self, edit_data=edit_data)

        if dialog.exec_() != QDialog.Accepted:
            return

        seq_data = dialog.get_sequence_data()
        seq.name = seq_data["name"]
        if seq.is_acquisition():
            seq.config_path = seq_data["config_path"]
        else:
            seq.script_path = seq_data["script_path"]
            seq.arguments = seq_data["arguments"]
            seq.python_path = seq_data["python_path"]
            seq.conda_env = seq_data["conda_env"]

        self._load_workflow_to_table()
        self.table.selectRow(current_row)
        self.label_status.setText("Changes saved")

    def _remove_sequence(self):
        """Remove selected sequence."""
        current_row = self.table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "No Selection", "Please select a sequence to remove.")
            return

        seq = self._workflow.sequences[current_row]
        seq_type = "acquisition" if seq.is_acquisition() else "sequence"

        reply = QMessageBox.question(
            self, "Confirm Remove", f"Remove {seq_type} '{seq.name}'?", QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            del self._workflow.sequences[current_row]
            self._load_workflow_to_table()
            self.label_status.setText(f"Removed {seq_type} '{seq.name}'")

    def _save_workflow(self):
        """Save workflow to YAML file."""
        self._sync_table_to_workflow()

        file_path, _ = QFileDialog.getSaveFileName(self, "Save Workflow", "", "YAML Files (*.yaml *.yml)")
        if not file_path:
            return
        if not file_path.endswith((".yaml", ".yml")):
            file_path += ".yaml"

        try:
            self._workflow.save_to_file(file_path)
            self._set_status(f"Saved to {os.path.basename(file_path)}", "green")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save workflow: {e}")
            self._set_status(f"Save failed: {e}", "red")

    def _load_workflow(self):
        """Load workflow from YAML file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Workflow", "", "YAML Files (*.yaml *.yml)")
        if not file_path:
            return

        try:
            self._workflow = Workflow.load_from_file(file_path)
            self.spinbox_cycles.setValue(self._workflow.num_cycles)
            self._load_workflow_to_table()
            self._set_status(f"Loaded {os.path.basename(file_path)}", "green")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load workflow: {e}")
            self._set_status(f"Load failed: {e}", "red")

    def _run_workflow(self):
        """Validate and emit signal to run workflow."""
        self._sync_table_to_workflow()

        # Validate cycle args if any sequence has them
        errors = self._workflow.validate_cycle_args()
        if errors:
            QMessageBox.warning(self, "Validation Error", "\n".join(errors))
            return

        # Check at least one sequence is included
        included = self._workflow.get_included_sequences()
        if not included:
            QMessageBox.warning(self, "No Sequences", "Please include at least one sequence to run.")
            return

        # Confirmation
        seq_names = [s.name for s in included]
        num_cycles = self._workflow.num_cycles
        msg = f"Run workflow with {len(included)} sequences?\n\n" + "\n".join(
            f"  {i+1}. {name}" for i, name in enumerate(seq_names)
        )

        if num_cycles > 1:
            msg += f"\n\nThis will repeat for {num_cycles} cycles."

        reply = QMessageBox.question(self, "Confirm Run", msg, QMessageBox.Ok | QMessageBox.Cancel)
        if reply != QMessageBox.Ok:
            return

        self._log.info(f"Starting workflow with sequences: {seq_names}, cycles: {num_cycles}")
        self.signal_run_workflow.emit(self._workflow)

    def _pause_workflow(self):
        """Pause or resume the workflow."""
        if self._is_paused:
            self._log.info("Resuming workflow")
            self.signal_resume_workflow.emit()
        else:
            self._log.info("Pausing workflow")
            self.signal_pause_workflow.emit()

    def _stop_workflow(self):
        """Stop the workflow after current sequence."""
        self._log.info("Stopping workflow")
        self.signal_stop_workflow.emit()

    def _get_table_text(self, row: int, col: int) -> Optional[str]:
        """Get text from table cell, or None if empty."""
        item = self.table.item(row, col)
        return item.text().strip() or None if item else None

    def _sync_table_to_workflow(self):
        """Sync table edits back to workflow data."""
        self._workflow.num_cycles = self.spinbox_cycles.value()

        for row, seq in enumerate(self._workflow.sequences):
            # Get include state from checkbox
            cell_widget = self.table.cellWidget(row, self.COL_INCLUDE)
            if cell_widget:
                checkbox = cell_widget.findChild(QCheckBox)
                if checkbox:
                    seq.included = checkbox.isChecked()

            # Skip Acquisition - it's not editable via table
            if seq.is_acquisition():
                continue

            # Update name (but not to "acquisition")
            new_name = self._get_table_text(row, self.COL_NAME)
            if new_name and new_name.lower() != "acquisition":
                seq.name = new_name

            # Cycle args
            seq.cycle_arg_name = self._get_table_text(row, self.COL_CYCLE_ARG)
            seq.cycle_arg_values = self._get_table_text(row, self.COL_CYCLE_VALUES)

    def highlight_sequence(self, index: int):
        """Highlight the currently running sequence."""
        for row in range(self.table.rowCount()):
            background = self._get_row_background_color(row, is_running=(row == index))
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item:
                    item.setBackground(background)

    def _get_row_background_color(self, row: int, is_running: bool = False) -> QColor:
        """Get the appropriate background color for a table row."""
        if is_running:
            return QColor(200, 255, 200)  # Light green for running
        seq = self._workflow.sequences[row] if row < len(self._workflow.sequences) else None
        if seq and seq.is_acquisition():
            return QColor(240, 240, 255)  # Light blue for acquisition
        return QColor(255, 255, 255)  # White for scripts

    def clear_highlight(self):
        """Clear all row highlights."""
        self.highlight_sequence(-1)

    def _set_status(self, text: str, color: str = "black"):
        """Set status label text and color."""
        self.label_status.setText(text)
        self.label_status.setStyleSheet(f"color: {color};")

    def set_running_state(self, running: bool):
        """Update UI based on running state."""
        self._is_running = running
        self._is_paused = False

        # Enable/disable editing controls (inverse of running state)
        for widget in [
            self.btn_run,
            self.btn_insert_above,
            self.btn_insert_below,
            self.btn_edit,
            self.btn_remove,
            self.btn_save,
            self.btn_load,
            self.spinbox_cycles,
        ]:
            widget.setEnabled(not running)

        # Pause and Stop buttons enabled when running
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setText("Pause")

        if running:
            self._set_status("Workflow running...", "blue")
            self.text_output.clear()
        else:
            self.clear_highlight()
            if "Running:" in self.label_status.text():
                self._set_status("Ready")

    def on_workflow_paused(self):
        """Handle workflow paused."""
        self._is_paused = True
        self.btn_pause.setText("Resume")
        self._set_status("Workflow paused - click Resume to continue", "orange")

    def on_workflow_resumed(self):
        """Handle workflow resumed."""
        self._is_paused = False
        self.btn_pause.setText("Pause")
        self._set_status("Workflow running...", "blue")

    def on_workflow_finished(self, success: bool):
        """Handle workflow completion."""
        self.set_running_state(False)
        if success:
            self._set_status("Workflow completed successfully", "green")
        else:
            self._set_status("Workflow stopped", "red")

    def on_sequence_started(self, index: int, name: str):
        """Handle sequence start."""
        self.highlight_sequence(index)
        self._set_status(f"Running: {name}", "blue")

    def on_error(self, error_msg: str):
        """Handle error from workflow runner."""
        self._set_status(f"Error: {error_msg}", "red")

    def on_script_output(self, line: str):
        """Append script output line."""
        self.text_output.append(line)
        # Auto-scroll to bottom
        scrollbar = self.text_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _save_log(self):
        """Save the log output to a text file."""
        from datetime import datetime

        default_name = f"workflow_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Log", default_name, "Text Files (*.txt);;All Files (*)")
        if not file_path:
            return

        try:
            with open(file_path, "w") as f:
                f.write(self.text_output.toPlainText())
            self._set_status(f"Log saved to {os.path.basename(file_path)}", "green")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save log: {e}")
            self._set_status(f"Save failed: {e}", "red")

    def closeEvent(self, event):
        """Handle dialog close - warn if workflow is running."""
        if self._is_running:
            reply = QMessageBox.question(
                self,
                "Workflow Running",
                "A workflow is currently running. Stop it and close?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.signal_stop_workflow.emit()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
