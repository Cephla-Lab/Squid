"""
Workflow Runner UI Components.

Dialog and widgets for configuring and running workflow sequences.
"""

import json
import os
from typing import Callable, List, Optional, Sequence

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from control.workflow_runner import (
    LAST_TARGETS_PLACEHOLDER,
    SequenceItem,
    SequenceType,
    Workflow,
    parse_well_list,
)

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


def _browse_yaml(parent: QWidget, line_edit: QLineEdit, title: str) -> None:
    file_path, _ = QFileDialog.getOpenFileName(parent, title, "", "YAML Files (*.yaml *.yml);;All Files (*)")
    if file_path:
        line_edit.setText(file_path)


def _browse_dir(parent: QWidget, line_edit: QLineEdit, title: str) -> None:
    path = QFileDialog.getExistingDirectory(parent, title, line_edit.text() or "")
    if path:
        line_edit.setText(path)


def _offset_spinbox(maximum: float, decimals: int = 4, suffix: str = " mm") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(-maximum, maximum)
    box.setSingleStep(0.001)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


def parse_param_lines(text: str) -> dict:
    """``key = value`` lines -> dict, values JSON-coerced (3 -> int, 1.5 -> float, true -> bool)."""
    params = {}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"expected KEY=VALUE, got {raw.strip()!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ValueError(f"missing key in {raw.strip()!r}")
        try:
            params[key] = json.loads(value)
        except ValueError:
            params[key] = value
    return params


def format_param_lines(params: Optional[dict]) -> str:
    return "\n".join(f"{key} = {json.dumps(value)}" for key, value in (params or {}).items())


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

        self.checkbox_last_targets = QCheckBox("Use the targets written by the Analyze step before this one")
        self.checkbox_last_targets.toggled.connect(self._on_last_targets_toggled)
        layout.addRow(self.checkbox_last_targets)

        self.edit_wells = QLineEdit()
        self.edit_wells.setPlaceholderText("(Optional) e.g. C5, C6, D5 - wellplate config files only")
        layout.addRow("Wells:", self.edit_wells)

        save_dir_layout = QHBoxLayout()
        self.edit_save_dir = QLineEdit()
        self.edit_save_dir.setPlaceholderText("(Optional) default: the acquisition tab's saving folder")
        save_dir_layout.addWidget(self.edit_save_dir)
        btn_save_dir = QPushButton("Browse...")
        btn_save_dir.clicked.connect(lambda: _browse_dir(self, self.edit_save_dir, "Select Save Folder"))
        save_dir_layout.addWidget(btn_save_dir)
        layout.addRow("Save folder:", save_dir_layout)

        # Help text
        help_text = QLabel(
            "<small><i>Leave the config empty to use the current settings of the tab that matches the file.<br>"
            "A config file picks its own tab (wellplate, flexible or Time at XY) when it runs.<br>"
            "A well list replaces the wells stored in a wellplate config.</i></small>"
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
        for key, widget in [("name", self.edit_name), ("wells", self.edit_wells), ("save_dir", self.edit_save_dir)]:
            if data.get(key):
                widget.setText(data[key])
        config_path = data.get("config_path") or ""
        if config_path.strip() == LAST_TARGETS_PLACEHOLDER:
            self.checkbox_last_targets.setChecked(True)
        elif config_path:
            self.edit_config_path.setText(config_path)

    def _on_last_targets_toggled(self, checked: bool):
        self.edit_config_path.setEnabled(not checked)
        self.btn_browse.setEnabled(not checked)
        self.edit_wells.setEnabled(not checked)
        if checked:
            self.edit_config_path.setText(LAST_TARGETS_PLACEHOLDER)
        elif self.edit_config_path.text().strip() == LAST_TARGETS_PLACEHOLDER:
            self.edit_config_path.clear()

    def _browse_config(self):
        _browse_yaml(self, self.edit_config_path, "Select Acquisition Config")

    def _validate_and_accept(self):
        name = self.edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return
        config_path = self.edit_config_path.text().strip()
        wells = self.edit_wells.text().strip()
        if self.checkbox_last_targets.isChecked():
            config_path = LAST_TARGETS_PLACEHOLDER
        elif not _confirm_missing_file(self, config_path, "Config file"):
            return
        if wells:
            try:
                parse_well_list(wells)
            except ValueError as e:
                QMessageBox.warning(self, "Validation Error", str(e))
                return
            if not config_path or config_path == LAST_TARGETS_PLACEHOLDER:
                QMessageBox.warning(self, "Validation Error", "A well list needs a wellplate config file.")
                return
        self.accept()

    def get_sequence_data(self) -> dict:
        config_path = self.edit_config_path.text().strip()
        if self.checkbox_last_targets.isChecked():
            config_path = LAST_TARGETS_PLACEHOLDER
        return {
            "name": self.edit_name.text().strip(),
            "config_path": config_path or None,
            "wells": self.edit_wells.text().strip() or None,
            "save_dir": self.edit_save_dir.text().strip() or None,
        }


class AddObjectiveDialog(QDialog):
    """Dialog for adding or editing an objective-switch sequence."""

    def __init__(
        self,
        parent=None,
        edit_data: dict = None,
        objective_names: Optional[List[str]] = None,
        laser_af_reference_status: Optional[Callable[[str], Optional[bool]]] = None,
    ):
        super().__init__(parent)
        self._edit_mode = edit_data is not None
        self._objective_names = list(objective_names or [])
        self._reference_status = laser_af_reference_status
        self.setWindowTitle("Edit Objective Step" if self._edit_mode else "Add Objective Step")
        self.setMinimumWidth(460)
        self._setup_ui()
        if edit_data:
            self._populate_from_data(edit_data)
        self._update_status()

    def _setup_ui(self):
        layout = QFormLayout(self)
        self.edit_name = QLineEdit()
        self.edit_name.setText("Objective")
        layout.addRow("Name:", self.edit_name)

        self.combo_objective = QComboBox()
        self.combo_objective.setEditable(not self._objective_names)
        self.combo_objective.addItems(self._objective_names)
        self.combo_objective.currentTextChanged.connect(self._update_status)
        layout.addRow("Objective:", self.combo_objective)

        self.checkbox_reference = QCheckBox("Require a laser-AF reference for this objective")
        self.checkbox_reference.setChecked(True)
        self.checkbox_reference.toggled.connect(self._update_status)
        layout.addRow(self.checkbox_reference)

        self.label_status = QLabel("")
        self.label_status.setWordWrap(True)
        layout.addRow(self.label_status)

        help_text = QLabel(
            "<small><i>Switches the objective (turning the turret when there is one) and reloads<br>"
            "that objective's stored laser-AF reference. The workflow stops if this step fails.</i></small>"
        )
        help_text.setStyleSheet("color: gray;")
        layout.addRow(help_text)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Save" if self._edit_mode else "Add")
        self.btn_add.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addRow(btn_layout)

    def _populate_from_data(self, data: dict):
        if data.get("name"):
            self.edit_name.setText(data["name"])
        objective = data.get("objective") or ""
        if objective and self.combo_objective.findText(objective) < 0:
            self.combo_objective.addItem(objective)
        self.combo_objective.setCurrentText(objective)
        self.checkbox_reference.setChecked(bool(data.get("require_laser_af_reference", True)))

    def _update_status(self, *_):
        name = self.combo_objective.currentText().strip()
        if not name or self._reference_status is None:
            self.label_status.setText("")
            return
        status = self._reference_status(name)
        if status is None:
            text, color = "Laser AF is not available on this system.", "gray"
        elif status:
            text, color = f"Laser-AF reference for {name}: present.", "green"
        else:
            text, color = f"Laser-AF reference for {name}: NOT set (teach it in the Laser-Based Focus tab).", "red"
        self.label_status.setText(text)
        self.label_status.setStyleSheet(f"color: {color};")

    def _validate_and_accept(self):
        if not self.edit_name.text().strip():
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return
        if not self.combo_objective.currentText().strip():
            QMessageBox.warning(self, "Validation Error", "Choose an objective.")
            return
        self.accept()

    def get_sequence_data(self) -> dict:
        return {
            "name": self.edit_name.text().strip(),
            "objective": self.combo_objective.currentText().strip(),
            "require_laser_af_reference": self.checkbox_reference.isChecked(),
        }


class AddAnalyzeDialog(QDialog):
    """Dialog for adding or editing an analyze (smart acquisition) sequence."""

    def __init__(self, parent=None, edit_data: dict = None):
        super().__init__(parent)
        self._edit_mode = edit_data is not None
        self.setWindowTitle("Edit Analyze Step" if self._edit_mode else "Add Analyze Step")
        self.setMinimumWidth(620)
        self._setup_ui()
        if edit_data:
            self._populate_from_data(edit_data)
        else:
            self._fill_default_params()

    @staticmethod
    def _analyzer_names() -> List[str]:
        try:
            from control.core.smart_acquisition import analyzers

            return list(analyzers.available())
        except Exception:
            return ["ring_grid"]

    def _setup_ui(self):
        from control.core.smart_acquisition.emit import MAX_XY_OFFSET_MM, MAX_Z_OFFSET_MM

        layout = QFormLayout(self)
        self.edit_name = QLineEdit()
        self.edit_name.setText("Find targets")
        layout.addRow("Name:", self.edit_name)

        self.combo_analyzer = QComboBox()
        self.combo_analyzer.addItems(self._analyzer_names())
        if self.combo_analyzer.findText("ring_grid") >= 0:
            self.combo_analyzer.setCurrentText("ring_grid")
        self.combo_analyzer.currentTextChanged.connect(lambda _t: self._fill_default_params())
        layout.addRow("Analyzer:", self.combo_analyzer)

        self.edit_params = QPlainTextEdit()
        self.edit_params.setPlaceholderText("key = value, one per line")
        self.edit_params.setMaximumHeight(90)
        layout.addRow("Parameters:", self.edit_params)

        template_layout = QHBoxLayout()
        self.edit_template = QLineEdit()
        self.edit_template.setPlaceholderText(
            "acquisition.yaml of the follow-up run (channels, recording, time series)"
        )
        template_layout.addWidget(self.edit_template)
        self.btn_browse_template = QPushButton("Browse...")
        self.btn_browse_template.clicked.connect(lambda: _browse_yaml(self, self.edit_template, "Select Template"))
        template_layout.addWidget(self.btn_browse_template)
        layout.addRow("Template:", template_layout)

        input_layout = QHBoxLayout()
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText("(Optional) survey folder; default: the previous acquisition of this run")
        input_layout.addWidget(self.edit_input)
        self.btn_browse_input = QPushButton("Browse...")
        self.btn_browse_input.clicked.connect(lambda: _browse_dir(self, self.edit_input, "Select Survey Folder"))
        input_layout.addWidget(self.btn_browse_input)
        layout.addRow("Survey:", input_layout)

        output_layout = QHBoxLayout()
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText("(Optional) default: <survey>/smart_targets")
        output_layout.addWidget(self.edit_output)
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(lambda: _browse_dir(self, self.edit_output, "Select Output Folder"))
        output_layout.addWidget(self.btn_browse_output)
        layout.addRow("Output:", output_layout)

        offsets_layout = QHBoxLayout()
        self.spin_dx = _offset_spinbox(MAX_XY_OFFSET_MM)
        self.spin_dy = _offset_spinbox(MAX_XY_OFFSET_MM)
        self.spin_dz = _offset_spinbox(MAX_Z_OFFSET_MM)
        for label, spin in (("dx", self.spin_dx), ("dy", self.spin_dy), ("dz", self.spin_dz)):
            offsets_layout.addWidget(QLabel(label))
            offsets_layout.addWidget(spin)
        offsets_layout.addStretch()
        layout.addRow("Objective offset:", offsets_layout)

        flags_layout = QHBoxLayout()
        self.checkbox_carry_z = QCheckBox("Carry survey Z (+ dz)")
        self.checkbox_carry_z.setChecked(True)
        self.checkbox_sweep_z = QCheckBox("Sweep z-stack for best focus")
        self.checkbox_overlay = QCheckBox("Write overlays")
        self.checkbox_overlay.setChecked(True)
        for box in (self.checkbox_carry_z, self.checkbox_sweep_z, self.checkbox_overlay):
            flags_layout.addWidget(box)
        flags_layout.addStretch()
        layout.addRow(flags_layout)

        help_text = QLabel(
            "<small><i>Offset convention: position under the follow-up objective = position under the survey<br>"
            "objective + offset. Measure it by centring one feature under each objective and subtracting.<br>"
            "The workflow stops if no targets are found.</i></small>"
        )
        help_text.setStyleSheet("color: gray;")
        layout.addRow(help_text)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Save" if self._edit_mode else "Add")
        self.btn_add.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addRow(btn_layout)

    def _fill_default_params(self):
        try:
            from control.core.smart_acquisition import analyzers

            name = self.combo_analyzer.currentText()
            defaults = analyzers.defaults_for(name)
            primary = analyzers.primary_params_for(name) or list(defaults)
            self.edit_params.setPlainText(format_param_lines({k: defaults[k] for k in primary if k in defaults}))
        except Exception:
            self.edit_params.setPlainText("")

    def _populate_from_data(self, data: dict):
        if data.get("name"):
            self.edit_name.setText(data["name"])
        analyzer = data.get("analyzer") or "ring_grid"
        if self.combo_analyzer.findText(analyzer) < 0:
            self.combo_analyzer.addItem(analyzer)
        self.combo_analyzer.blockSignals(True)
        self.combo_analyzer.setCurrentText(analyzer)
        self.combo_analyzer.blockSignals(False)
        self.edit_params.setPlainText(format_param_lines(data.get("analyzer_params")))
        self.edit_template.setText(data.get("template_path") or "")
        self.edit_input.setText(data.get("input_path") or "")
        self.edit_output.setText(data.get("output_dir") or "")
        xy = data.get("xy_offset_mm") or [0.0, 0.0]
        self.spin_dx.setValue(float(xy[0]))
        self.spin_dy.setValue(float(xy[1]))
        self.spin_dz.setValue(float(data.get("z_offset_mm") or 0.0))
        self.checkbox_carry_z.setChecked(bool(data.get("carry_z", True)))
        self.checkbox_sweep_z.setChecked(bool(data.get("sweep_z", False)))
        self.checkbox_overlay.setChecked(bool(data.get("overlay", True)))

    def _validate_and_accept(self):
        if not self.edit_name.text().strip():
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            return
        template = self.edit_template.text().strip()
        if not template:
            QMessageBox.warning(self, "Validation Error", "A template acquisition.yaml is required.")
            return
        if not _confirm_missing_file(self, template, "Template"):
            return
        try:
            parse_param_lines(self.edit_params.toPlainText())
        except ValueError as e:
            QMessageBox.warning(self, "Validation Error", f"Parameters: {e}")
            return
        self.accept()

    def get_sequence_data(self) -> dict:
        dx, dy, dz = self.spin_dx.value(), self.spin_dy.value(), self.spin_dz.value()
        return {
            "name": self.edit_name.text().strip(),
            "analyzer": self.combo_analyzer.currentText().strip(),
            "analyzer_params": parse_param_lines(self.edit_params.toPlainText()) or None,
            "template_path": self.edit_template.text().strip() or None,
            "input_path": self.edit_input.text().strip() or None,
            "output_dir": self.edit_output.text().strip() or None,
            "xy_offset_mm": [dx, dy] if (dx or dy) else None,
            "z_offset_mm": dz if dz else None,
            "carry_z": self.checkbox_carry_z.isChecked(),
            "sweep_z": self.checkbox_sweep_z.isChecked(),
            "overlay": self.checkbox_overlay.isChecked(),
        }


class SmartRingWorkflowDialog(QDialog):
    """The inputs of the smart-ring recipe -> a ready-to-run workflow (see control.smart_workflow).

    The wells are not typed here: they are the ones selected in the survey acquisition.yaml
    (Wellplate tab, plate map, Save Settings...), shown read-only under the survey row.
    The objective offset is remembered per objective pair, and the recording folder (where the
    follow-up step saves) is remembered too; both are filled in again next time.
    """

    def __init__(
        self,
        parent=None,
        objective_names: Optional[List[str]] = None,
        laser_af_reference_status: Optional[Callable[[str], Optional[bool]]] = None,
        camera_binning: Optional[Callable[[], Optional[Sequence[int]]]] = None,
        camera_image_size: Optional[Callable[[], Optional[Sequence[int]]]] = None,
    ):
        super().__init__(parent)
        self._objective_names = list(objective_names or [])
        self._reference_status = laser_af_reference_status
        self._camera_binning = camera_binning
        self._camera_image_size = camera_image_size
        self.workflow: Optional[Workflow] = None
        self.setWindowTitle("New Smart Ring Workflow")
        self.setMinimumWidth(640)
        self._setup_ui()
        self._update_status()

    def _objective_combo(self, preferred: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(not self._objective_names)
        combo.addItems(self._objective_names)
        if combo.findText(preferred) >= 0:
            combo.setCurrentText(preferred)
        combo.currentTextChanged.connect(self._update_status)
        return combo

    def _setup_ui(self):
        from control.core.smart_acquisition.emit import MAX_XY_OFFSET_MM, MAX_Z_OFFSET_MM
        from control.smart_workflow import load_recording_folder

        layout = QFormLayout(self)
        intro = QLabel(
            "Survey the wells at low magnification, find the ring structures, switch objective and "
            "image every ring with the follow-up acquisition (Time at XY or multipoint)."
        )
        intro.setWordWrap(True)
        layout.addRow(intro)

        survey_layout = QHBoxLayout()
        self.edit_survey = QLineEdit()
        self.edit_survey.setPlaceholderText(
            "acquisition.yaml from the Wellplate tab (wells selected on the plate map, Save Settings...)"
        )
        survey_layout.addWidget(self.edit_survey)
        btn = QPushButton("Browse...")
        btn.clicked.connect(lambda: _browse_yaml(self, self.edit_survey, "Select Survey acquisition.yaml"))
        survey_layout.addWidget(btn)
        layout.addRow("1. Survey settings:", survey_layout)

        self.label_wells = QLabel("")
        self.label_wells.setWordWrap(True)
        self.label_wells.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addRow("Wells:", self.label_wells)
        self.edit_survey.textChanged.connect(self._update_wells)
        self._update_wells()

        self.combo_low = self._objective_combo("4x")
        layout.addRow("2. Survey objective:", self.combo_low)

        template_layout = QHBoxLayout()
        self.edit_template = QLineEdit()
        self.edit_template.setPlaceholderText(
            "acquisition.yaml of the follow-up run (Time at XY: channel rows with Zi/R/Zf, FPS/Dur, z planes, Nt/dt)"
        )
        template_layout.addWidget(self.edit_template)
        btn = QPushButton("Browse...")
        btn.clicked.connect(lambda: _browse_yaml(self, self.edit_template, "Select Follow-up acquisition.yaml"))
        template_layout.addWidget(btn)
        layout.addRow("3. Follow-up settings:", template_layout)

        self.combo_high = self._objective_combo("20x")
        layout.addRow("4. Follow-up objective:", self.combo_high)

        offsets_layout = QHBoxLayout()
        self.spin_dx = _offset_spinbox(MAX_XY_OFFSET_MM)
        self.spin_dy = _offset_spinbox(MAX_XY_OFFSET_MM)
        self.spin_dz = _offset_spinbox(MAX_Z_OFFSET_MM)
        for label, spin in (("dx", self.spin_dx), ("dy", self.spin_dy), ("dz", self.spin_dz)):
            offsets_layout.addWidget(QLabel(label))
            offsets_layout.addWidget(spin)
        offsets_layout.addStretch()
        layout.addRow("5. Objective offset:", offsets_layout)
        offset_help = QLabel(
            "<small><i>follow-up position = survey position + offset. Centre one feature under each objective "
            "and subtract the stage readings (dz is the parfocal difference).</i></small>"
        )
        offset_help.setStyleSheet("color: gray;")
        offset_help.setWordWrap(True)
        layout.addRow(offset_help)
        # The offset is remembered per objective pair (cache/smart_ring_wizard.yaml).
        self.label_offset_saved = QLabel("")
        self.label_offset_saved.setStyleSheet("color: gray;")
        layout.addRow(self.label_offset_saved)
        self.combo_low.currentTextChanged.connect(self._load_offset)
        self.combo_high.currentTextChanged.connect(self._load_offset)
        self._load_offset()

        # Where the follow-up (Image targets) step saves its recordings; remembered like the offset.
        recording_layout = QHBoxLayout()
        self.edit_recording = QLineEdit()
        self.edit_recording.setPlaceholderText("(Optional) default: the Time at XY tab's saving folder")
        self.edit_recording.setText(load_recording_folder() or "")
        recording_layout.addWidget(self.edit_recording)
        btn = QPushButton("Browse...")
        btn.clicked.connect(lambda: _browse_dir(self, self.edit_recording, "Select Recording Folder"))
        recording_layout.addWidget(btn)
        layout.addRow("6. Recording folder:", recording_layout)

        ring_layout = QHBoxLayout()
        self.spin_diameter = QDoubleSpinBox()
        self.spin_diameter.setRange(1.0, 100000.0)
        self.spin_diameter.setSuffix(" µm")
        self.spin_pitch = QDoubleSpinBox()
        self.spin_pitch.setRange(1.0, 100000.0)
        self.spin_pitch.setSuffix(" µm")
        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 50)
        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 50)
        try:
            from control.core.smart_acquisition import analyzers

            defaults = analyzers.defaults_for("ring_grid")
        except Exception:
            defaults = {}
        self.spin_diameter.setValue(float(defaults.get("ring_diameter_um", 320.0)))
        self.spin_pitch.setValue(float(defaults.get("grid_pitch_um", 1000.0)))
        self.spin_rows.setValue(int(defaults.get("grid_rows", 3)))
        self.spin_cols.setValue(int(defaults.get("grid_cols", 3)))
        for label, widget in (
            ("diameter", self.spin_diameter),
            ("pitch", self.spin_pitch),
            ("rows", self.spin_rows),
            ("cols", self.spin_cols),
        ):
            ring_layout.addWidget(QLabel(label))
            ring_layout.addWidget(widget)
        ring_layout.addStretch()
        layout.addRow("Rings:", ring_layout)

        output_layout = QHBoxLayout()
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText("(Optional) default: <survey>/smart_targets")
        output_layout.addWidget(self.edit_output)
        btn = QPushButton("Browse...")
        btn.clicked.connect(lambda: _browse_dir(self, self.edit_output, "Select Output Folder"))
        output_layout.addWidget(btn)
        layout.addRow("Targets folder:", output_layout)

        self.checkbox_carry_z = QCheckBox("Carry the survey Z (+ dz) to each target")
        self.checkbox_carry_z.setChecked(True)
        self.checkbox_reference = QCheckBox("Require laser-AF references for both objectives")
        self.checkbox_reference.setChecked(True)
        self.checkbox_reference.toggled.connect(self._update_status)
        layout.addRow(self.checkbox_carry_z)
        layout.addRow(self.checkbox_reference)

        self.label_status = QLabel("")
        self.label_status.setWordWrap(True)
        layout.addRow(self.label_status)

        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Create Workflow")
        self.btn_ok.clicked.connect(self._build)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addRow(btn_layout)

    def _objective_pair(self):
        low, high = self.combo_low.currentText().strip(), self.combo_high.currentText().strip()
        return (low, high) if low and high and low != high else None

    def _load_offset(self, *_):
        """Fill dx/dy/dz with the offset remembered for this objective pair, or 0 (another pair's would be wrong)."""
        from control.smart_workflow import load_objective_offset

        pair = self._objective_pair()
        if pair is None:
            self.label_offset_saved.setText("")
            return
        stored = load_objective_offset(*pair)
        for spin, value in zip((self.spin_dx, self.spin_dy, self.spin_dz), stored or (0.0, 0.0, 0.0)):
            spin.setValue(value)
        pair_label = f"{pair[0]} \u2192 {pair[1]}"
        self.label_offset_saved.setText(
            f"<small>Saved offset for {pair_label}</small>"
            if stored is not None
            else f"<small>No saved offset for {pair_label} yet; it is saved when you create the workflow</small>"
        )

    def _update_wells(self, *_):
        """Show the wells the survey file selects, or why it cannot be used."""
        from control.smart_workflow import survey_wells

        path = self.edit_survey.text().strip()
        if not path:
            self.label_wells.setText("<i>from the survey settings file</i>")
            self.label_wells.setStyleSheet("color: gray;")
            return
        try:
            wells = survey_wells(path)
        except ValueError as e:
            self.label_wells.setText(str(e))
            self.label_wells.setStyleSheet("color: red;")
            return
        self.label_wells.setText(f"{', '.join(wells)} ({len(wells)})")
        self.label_wells.setStyleSheet("")

    def _update_status(self, *_):
        if self._reference_status is None:
            self.label_status.setText("")
            return
        parts, missing = [], False
        for combo in (self.combo_low, self.combo_high):
            name = combo.currentText().strip()
            if not name:
                continue
            status = self._reference_status(name)
            if status is None:
                parts.append(f"{name}: laser AF unavailable")
            elif status:
                parts.append(f"{name}: reference present")
            else:
                parts.append(f"{name}: NO reference")
                missing = True
        self.label_status.setText("Laser-AF references - " + "; ".join(parts))
        self.label_status.setStyleSheet("color: red;" if missing and self.checkbox_reference.isChecked() else "")

    def recipe(self):
        from control.smart_workflow import SmartRingRecipe

        dx, dy, dz = self.spin_dx.value(), self.spin_dy.value(), self.spin_dz.value()
        return SmartRingRecipe(
            survey_yaml=self.edit_survey.text().strip(),
            objective_low=self.combo_low.currentText().strip(),
            template_yaml=self.edit_template.text().strip(),
            objective_high=self.combo_high.currentText().strip(),
            xy_offset_mm=[dx, dy] if (dx or dy) else None,
            z_offset_mm=dz if dz else None,
            analyzer="ring_grid",
            analyzer_params={
                "ring_diameter_um": self.spin_diameter.value(),
                "grid_pitch_um": self.spin_pitch.value(),
                "grid_rows": self.spin_rows.value(),
                "grid_cols": self.spin_cols.value(),
            },
            carry_z=self.checkbox_carry_z.isChecked(),
            output_dir=self.edit_output.text().strip() or None,
            require_laser_af_reference=self.checkbox_reference.isChecked(),
            recording_dir=self.edit_recording.text().strip() or None,
        )

    def _build(self):
        from control.smart_workflow import (
            build_smart_ring_workflow,
            recipe_warnings,
            save_objective_offset,
            save_recording_folder,
            validate_recipe,
        )

        recipe = self.recipe()
        errors = validate_recipe(recipe)
        if errors:
            QMessageBox.warning(self, "Recipe Incomplete", "\n".join(errors))
            return
        live_binning = self._camera_binning() if self._camera_binning is not None else None
        live_image_size = self._camera_image_size() if self._camera_image_size is not None else None
        warnings = recipe_warnings(recipe, live_binning=live_binning, live_image_size=live_image_size)
        if warnings:
            reply = QMessageBox.question(
                self,
                "Check Before Running",
                "\n\n".join(warnings) + "\n\nCreate the workflow anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
        self.workflow = build_smart_ring_workflow(recipe)
        pair = self._objective_pair()
        if pair is not None:
            save_objective_offset(*pair, self.spin_dx.value(), self.spin_dy.value(), self.spin_dz.value())
        save_recording_folder(recipe.recording_dir)
        self.accept()


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

    def __init__(
        self,
        parent=None,
        objective_names: Optional[List[str]] = None,
        laser_af_reference_status: Optional[Callable[[str], Optional[bool]]] = None,
        camera_binning: Optional[Callable[[], Optional[Sequence[int]]]] = None,
        camera_image_size: Optional[Callable[[], Optional[Sequence[int]]]] = None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._objective_names = list(objective_names or [])
        self._reference_status = laser_af_reference_status
        self._camera_binning = camera_binning
        self._camera_image_size = camera_image_size
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
            "Define sequences to run. 'Acquisition' runs the built-in acquisition (a config file picks its "
            "tab), 'Objective' switches objective, 'Analyze' turns a survey into targets, and scripts run "
            "externally."
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
        self.checkbox_stop_on_failure = QCheckBox("Stop at the first failed step")
        self.checkbox_stop_on_failure.setToolTip(
            "Objective and Analyze steps always stop the workflow when they fail; "
            "this also stops it after a failed script or acquisition."
        )
        self.checkbox_stop_on_failure.toggled.connect(self._on_stop_on_failure_toggled)
        cycles_layout.addSpacing(20)
        cycles_layout.addWidget(self.checkbox_stop_on_failure)
        cycles_layout.addStretch()
        self.btn_smart_ring = QPushButton("New Smart Ring Workflow...")
        self.btn_smart_ring.setToolTip("Survey wells, find rings, switch objective, image every ring")
        self.btn_smart_ring.clicked.connect(self._new_smart_ring_workflow)
        cycles_layout.addWidget(self.btn_smart_ring)
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

    def _on_stop_on_failure_toggled(self, checked: bool):
        self._workflow.stop_on_failure = bool(checked)

    def _set_workflow(self, workflow: Workflow):
        """Replace the edited workflow and refresh every control that mirrors it."""
        self._workflow = workflow
        self.spinbox_cycles.setValue(self._workflow.num_cycles)
        self.checkbox_stop_on_failure.setChecked(bool(self._workflow.stop_on_failure))
        self._load_workflow_to_table()

    def _new_smart_ring_workflow(self):
        dialog = SmartRingWorkflowDialog(
            self,
            objective_names=self._objective_names,
            laser_af_reference_status=self._reference_status,
            camera_binning=self._camera_binning,
            camera_image_size=self._camera_image_size,
        )
        if dialog.exec_() != QDialog.Accepted or dialog.workflow is None:
            return
        self._set_workflow(dialog.workflow)
        self._set_status("Smart ring workflow created - Save it, then Run", "green")

    def _load_workflow_to_table(self):
        """Populate table from workflow data."""
        self.table.setRowCount(len(self._workflow.sequences))

        for row, seq in enumerate(self._workflow.sequences):
            self._populate_table_row(row, seq)

    def _populate_table_row(self, row: int, seq: SequenceItem):
        """Populate a single table row with sequence data."""
        is_acq = not seq.is_script()  # every built-in step is read-only in the table

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
        self._apply_acquisition_styling(name_item, is_acq, seq=seq)
        self.table.setItem(row, self.COL_NAME, name_item)

        # Command
        cmd_item = self._create_command_item(seq)
        self.table.setItem(row, self.COL_COMMAND, cmd_item)

        # Cycle Arg name
        cycle_arg_item = QTableWidgetItem(seq.cycle_arg_name or "")
        self._apply_acquisition_styling(cycle_arg_item, is_acq, include_foreground=True, seq=seq)
        self.table.setItem(row, self.COL_CYCLE_ARG, cycle_arg_item)

        # Cycle Values
        cycle_values_item = QTableWidgetItem(seq.cycle_arg_values or "")
        self._apply_acquisition_styling(cycle_values_item, is_acq, include_foreground=True, seq=seq)
        self.table.setItem(row, self.COL_CYCLE_VALUES, cycle_values_item)

    @staticmethod
    def _type_color(seq: Optional[SequenceItem]) -> QColor:
        """Row colour per step type (white for scripts)."""
        if seq is None or seq.is_script():
            return QColor(255, 255, 255)
        if seq.is_objective():
            return QColor(255, 245, 220)  # amber
        if seq.is_analyze():
            return QColor(232, 248, 232)  # green
        return QColor(240, 240, 255)  # light blue: acquisition

    def _create_command_item(self, seq: SequenceItem) -> QTableWidgetItem:
        """Create the command column item for a sequence."""
        if not seq.is_script():
            item = QTableWidgetItem(seq.summary())
            self._apply_acquisition_styling(item, is_acquisition=True, include_foreground=True, seq=seq)
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
        self,
        item: QTableWidgetItem,
        is_acquisition: bool,
        include_foreground: bool = False,
        seq: Optional[SequenceItem] = None,
    ):
        """Apply read-only styling for built-in (non-script) sequence items."""
        if not is_acquisition:
            return
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setBackground(self._type_color(seq) if seq is not None else QColor(240, 240, 255))
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
        btn_objective = msg_box.addButton("Objective", QMessageBox.ActionRole)
        btn_analyze = msg_box.addButton("Analyze", QMessageBox.ActionRole)
        msg_box.addButton("Cancel", QMessageBox.RejectRole)
        msg_box.exec_()

        clicked = msg_box.clickedButton()
        if clicked == btn_script:
            return "script"
        elif clicked == btn_acquisition:
            return "acquisition"
        elif clicked == btn_objective:
            return "objective"
        elif clicked == btn_analyze:
            return "analyze"
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
                wells=seq_data["wells"],
                save_dir=seq_data["save_dir"],
                included=True,
            )
        elif sequence_type == "objective":
            dialog = AddObjectiveDialog(
                self, objective_names=self._objective_names, laser_af_reference_status=self._reference_status
            )
            if dialog.exec_() != QDialog.Accepted:
                return None
            seq_data = dialog.get_sequence_data()
            return SequenceItem(name=seq_data.pop("name"), sequence_type=SequenceType.OBJECTIVE, **seq_data)
        elif sequence_type == "analyze":
            dialog = AddAnalyzeDialog(self)
            if dialog.exec_() != QDialog.Accepted:
                return None
            seq_data = dialog.get_sequence_data()
            return SequenceItem(name=seq_data.pop("name"), sequence_type=SequenceType.ANALYZE, **seq_data)
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
            edit_data = {"name": seq.name, "config_path": seq.config_path, "wells": seq.wells, "save_dir": seq.save_dir}
            dialog = AddAcquisitionDialog(self, edit_data=edit_data)
        elif seq.is_objective():
            edit_data = {
                "name": seq.name,
                "objective": seq.objective,
                "require_laser_af_reference": seq.require_laser_af_reference,
            }
            dialog = AddObjectiveDialog(
                self,
                edit_data=edit_data,
                objective_names=self._objective_names,
                laser_af_reference_status=self._reference_status,
            )
        elif seq.is_analyze():
            edit_data = {
                "name": seq.name,
                "analyzer": seq.analyzer,
                "analyzer_params": seq.analyzer_params,
                "template_path": seq.template_path,
                "input_path": seq.input_path,
                "output_dir": seq.output_dir,
                "xy_offset_mm": seq.xy_offset_mm,
                "z_offset_mm": seq.z_offset_mm,
                "carry_z": seq.carry_z,
                "sweep_z": seq.sweep_z,
                "overlay": seq.overlay,
            }
            dialog = AddAnalyzeDialog(self, edit_data=edit_data)
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
            seq.wells = seq_data["wells"]
        elif seq.is_objective() or seq.is_analyze():
            for key, value in seq_data.items():
                if key != "name":
                    setattr(seq, key, value)
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
        seq_type = seq.sequence_type.value if not seq.is_script() else "sequence"

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
            self._set_workflow(Workflow.load_from_file(file_path))
            self._set_status(f"Loaded {os.path.basename(file_path)}", "green")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load workflow: {e}")
            self._set_status(f"Load failed: {e}", "red")

    def _run_workflow(self):
        """Validate and emit signal to run workflow."""
        self._sync_table_to_workflow()

        # Everything that can be checked without hardware: cycle args, files, offsets, {last_targets}...
        errors = self._workflow.validate()
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
        self._workflow.stop_on_failure = self.checkbox_stop_on_failure.isChecked()

        for row, seq in enumerate(self._workflow.sequences):
            # Get include state from checkbox
            cell_widget = self.table.cellWidget(row, self.COL_INCLUDE)
            if cell_widget:
                checkbox = cell_widget.findChild(QCheckBox)
                if checkbox:
                    seq.included = checkbox.isChecked()

            # Only scripts are editable in the table; the other step types have dialogs.
            if not seq.is_script():
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
        return self._type_color(seq)

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
            self.btn_smart_ring,
            self.spinbox_cycles,
            self.checkbox_stop_on_failure,
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
