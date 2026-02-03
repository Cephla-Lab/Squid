"""Channel configuration editor dialogs.

Provides UI dialogs for editing:
- Illumination channels (hardware-level, machine_configs/illumination_channel_config.yaml)
- Acquisition channels (user-facing, user_profiles/{profile}/channel_configs/general.yaml)
- Filter wheel positions (machine_configs/filter_wheels.yaml)
- Controller port mappings (part of illumination config)

All dialogs receive a ConfigRepository and are opened from the Settings menu.
They emit Qt signals on save; the caller bridges to EventBus as needed.

Ported from upstream commit 171aed9b:software/control/widgets.py with architecture
adaptations for the arch_v2 codebase.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qtpy.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor

import squid.core.logging

logger = squid.core.logging.get_logger(__name__)


# =============================================================================
# Helper Widgets
# =============================================================================


class WavelengthWidget(QWidget):
    """Widget for wavelength field with checkbox to toggle between int and N/A."""

    def __init__(self, wavelength_nm=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("Check to set wavelength, uncheck for N/A")
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.checkbox)

        self.spinbox = QSpinBox()
        self.spinbox.setRange(200, 900)
        self.spinbox.setValue(405)
        layout.addWidget(self.spinbox)

        self.na_label = QLabel("N/A")
        self.na_label.setStyleSheet("color: gray;")
        layout.addWidget(self.na_label)

        # Set initial state
        if wavelength_nm is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(wavelength_nm)
            self.spinbox.setVisible(True)
            self.na_label.setVisible(False)
        else:
            self.checkbox.setChecked(False)
            self.spinbox.setVisible(False)
            self.na_label.setVisible(True)

    def _on_checkbox_changed(self, state):
        checked = state == Qt.Checked
        self.spinbox.setVisible(checked)
        self.na_label.setVisible(not checked)

    def get_wavelength(self):
        """Return wavelength value or None if N/A."""
        if self.checkbox.isChecked():
            return self.spinbox.value()
        return None

    def set_wavelength(self, wavelength_nm):
        """Set wavelength value or N/A."""
        if wavelength_nm is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(wavelength_nm)
        else:
            self.checkbox.setChecked(False)


class SourceCodeWidget(QWidget):
    """Widget for source code field with checkbox to toggle between int and N/A."""

    def __init__(self, source_code=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("Check to set source code, uncheck for N/A")
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.checkbox)

        self.spinbox = QSpinBox()
        self.spinbox.setRange(0, 30)
        self.spinbox.setValue(0)
        layout.addWidget(self.spinbox)

        self.na_label = QLabel("N/A")
        self.na_label.setStyleSheet("color: gray;")
        layout.addWidget(self.na_label)

        # Set initial state
        if source_code is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(source_code)
            self.spinbox.setVisible(True)
            self.na_label.setVisible(False)
        else:
            self.checkbox.setChecked(False)
            self.spinbox.setVisible(False)
            self.na_label.setVisible(True)

    def _on_checkbox_changed(self, state):
        checked = state == Qt.Checked
        self.spinbox.setVisible(checked)
        self.na_label.setVisible(not checked)

    def get_source_code(self):
        """Return source code value or None if N/A."""
        if self.checkbox.isChecked():
            return self.spinbox.value()
        return None

    def set_source_code(self, source_code):
        """Set source code value or N/A."""
        if source_code is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(source_code)
        else:
            self.checkbox.setChecked(False)


# =============================================================================
# Helper Functions
# =============================================================================


def _is_filter_wheel_enabled() -> bool:
    """Check if filter wheel is enabled in .ini configuration."""
    try:
        import _def

        return getattr(_def, "USE_EMISSION_FILTER_WHEEL", False)
    except ImportError:
        return False


def _populate_filter_positions_for_combo(
    combo: QComboBox,
    channel_wheel: Optional[str],
    config_repo,
    current_position: Optional[int] = None,
) -> None:
    """Populate filter position dropdown, auto-resolving single-wheel systems.

    Args:
        combo: The QComboBox to populate
        channel_wheel: Raw filter_wheel value from channel (None, "auto", or wheel name)
        config_repo: ConfigRepository instance
        current_position: Position to select (None for first position)
    """
    combo.clear()

    registry = config_repo.get_filter_wheel_registry()
    has_registry = registry and registry.filter_wheels

    # No filter wheel system at all
    if not has_registry and not _is_filter_wheel_enabled():
        combo.addItem("N/A", None)
        combo.setEnabled(False)
        return

    # Resolve wheel: explicit name, or auto-select if single wheel
    wheel = None
    if channel_wheel and channel_wheel not in ("(None)", "auto"):
        # Explicit wheel name specified
        wheel = registry.get_wheel_by_name(channel_wheel) if registry else None
        if not wheel and registry:
            logger.warning(f"Filter wheel '{channel_wheel}' not found in registry")
    elif has_registry and len(registry.filter_wheels) == 1:
        # Single wheel system - auto-select
        wheel = registry.get_first_wheel()

    if not wheel:
        # No wheel resolved - check if we should show default positions or N/A
        if has_registry or _is_filter_wheel_enabled():
            # Filter wheel exists but no selection - show default positions
            combo.setEnabled(True)
            for pos in range(1, 9):
                combo.addItem(f"Position {pos}", pos)
        else:
            combo.addItem("N/A", None)
            combo.setEnabled(False)
            return
    else:
        # Populate from wheel's actual positions
        combo.setEnabled(True)
        for pos, filter_name in sorted(wheel.positions.items()):
            combo.addItem(f"{pos}: {filter_name}", pos)

    # Select current position, or default to first
    if current_position is not None:
        for i in range(combo.count()):
            if combo.itemData(i) == current_position:
                combo.setCurrentIndex(i)
                return
    combo.setCurrentIndex(0)


# =============================================================================
# Illumination Channel Dialogs
# =============================================================================


class IlluminationChannelConfiguratorDialog(QDialog):
    """Dialog for editing illumination channel hardware configuration.

    This edits the machine_configs/illumination_channel_config.yaml file which defines
    the physical illumination hardware. User-facing acquisition settings (display color,
    enabled state, filter position) are configured separately in user profile configs.
    """

    signal_channels_updated = Signal()

    # Column indices for the channels table
    COL_NAME = 0
    COL_TYPE = 1
    COL_PORT = 2
    COL_WAVELENGTH = 3
    COL_CALIBRATION = 4

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.illumination_config = None
        self.setWindowTitle("Illumination Channel Configurator")
        self.setMinimumSize(900, 500)
        self._setup_ui()
        self._load_channels()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Warning label
        warning_label = QLabel(
            "Warning: Illumination channel configuration is hardware-specific. "
            "Modifying these settings may break existing acquisition configurations. "
            "Only change these settings when necessary."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: #CC0000; font-weight: bold;")
        layout.addWidget(warning_label)

        # Table for illumination channels
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Controller Port", "Wavelength (nm)", "Calibration File"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add Channel")
        self.btn_add.setAutoDefault(False)
        self.btn_add.setDefault(False)
        self.btn_add.clicked.connect(self._add_channel)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Remove Channel")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.setDefault(False)
        self.btn_remove.clicked.connect(self._remove_channel)
        button_layout.addWidget(self.btn_remove)

        self.btn_move_up = QPushButton("Move Up")
        self.btn_move_up.setAutoDefault(False)
        self.btn_move_up.clicked.connect(self._move_up)
        button_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("Move Down")
        self.btn_move_down.setAutoDefault(False)
        self.btn_move_down.clicked.connect(self._move_down)
        button_layout.addWidget(self.btn_move_down)

        self.btn_port_mapping = QPushButton("Port Mapping...")
        self.btn_port_mapping.setAutoDefault(False)
        self.btn_port_mapping.clicked.connect(self._open_port_mapping)
        button_layout.addWidget(self.btn_port_mapping)

        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.setAutoDefault(False)
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _get_calibration_full_path(self, filename):
        """Get full path for calibration file."""
        if not filename:
            return ""
        calib_dir = self.config_repo.machine_configs_path / "intensity_calibrations"
        return str(calib_dir / filename)

    def _load_channels(self):
        """Load illumination channels from YAML config into the table."""
        self.illumination_config = self.config_repo.get_illumination_config()
        if not self.illumination_config:
            return

        # Get available ports (only those with mappings)
        available_ports = self.illumination_config.get_available_ports()

        self.table.setRowCount(len(self.illumination_config.channels))

        for row, channel in enumerate(self.illumination_config.channels):
            # Name (editable)
            name_item = QTableWidgetItem(channel.name)
            self.table.setItem(row, self.COL_NAME, name_item)

            # Type (dropdown)
            type_combo = QComboBox()
            type_combo.addItems(["epi_illumination", "transillumination"])
            type_combo.setCurrentText(channel.type.value)
            type_combo.currentTextChanged.connect(lambda text, r=row: self._on_type_changed(r, text))
            self.table.setCellWidget(row, self.COL_TYPE, type_combo)

            # Controller Port (dropdown) - only ports with mappings
            port_combo = QComboBox()
            port_combo.addItems(available_ports)
            port_combo.setCurrentText(channel.controller_port)
            self.table.setCellWidget(row, self.COL_PORT, port_combo)

            # Wavelength (checkbox + spinbox, or N/A)
            wave_widget = WavelengthWidget(channel.wavelength_nm)
            self.table.setCellWidget(row, self.COL_WAVELENGTH, wave_widget)

            # Calibration file (full path)
            full_path = self._get_calibration_full_path(channel.intensity_calibration_file)
            calib_item = QTableWidgetItem(full_path)
            self.table.setItem(row, self.COL_CALIBRATION, calib_item)

    def _on_type_changed(self, row, new_type):
        """Handle type change - update wavelength default and controller port."""
        wave_widget = self.table.cellWidget(row, self.COL_WAVELENGTH)
        available_ports = self.illumination_config.get_available_ports()

        # Find first available USB and D ports
        first_usb = next((p for p in available_ports if p.startswith("USB")), None)
        first_d = next((p for p in available_ports if p.startswith("D")), None)

        if new_type == "epi_illumination":
            # Set wavelength to default 405nm for epi
            if isinstance(wave_widget, WavelengthWidget):
                wave_widget.set_wavelength(405)

            # Update controller port to first available laser port
            port_combo = self.table.cellWidget(row, self.COL_PORT)
            if port_combo and port_combo.currentText().startswith("USB") and first_d:
                port_combo.setCurrentText(first_d)
        else:
            # Set wavelength to N/A for transillumination
            if isinstance(wave_widget, WavelengthWidget):
                wave_widget.set_wavelength(None)

            # Update controller port to first available USB port
            port_combo = self.table.cellWidget(row, self.COL_PORT)
            if port_combo and port_combo.currentText().startswith("D") and first_usb:
                port_combo.setCurrentText(first_usb)

    def _add_channel(self):
        """Add a new illumination channel."""
        dialog = AddIlluminationChannelDialog(self.illumination_config, self)
        if dialog.exec_() == QDialog.Accepted:
            channel_data = dialog.get_channel_data()
            from squid.core.config.models.illumination_config import IlluminationChannel

            new_channel = IlluminationChannel(**channel_data)
            self.illumination_config.channels.append(new_channel)
            self._load_channels()

    def _remove_channel(self):
        """Remove selected channel."""
        current_row = self.table.currentRow()
        if current_row < 0:
            return

        name_item = self.table.item(current_row, 0)
        if name_item:
            reply = QMessageBox.question(
                self, "Confirm Removal", f"Remove channel '{name_item.text()}'?", QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                del self.illumination_config.channels[current_row]
                self._load_channels()

    def _move_up(self):
        """Move selected channel up."""
        current_row = self.table.currentRow()
        if current_row <= 0:
            return

        channels = self.illumination_config.channels
        channels[current_row], channels[current_row - 1] = channels[current_row - 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row - 1)

    def _move_down(self):
        """Move selected channel down."""
        current_row = self.table.currentRow()
        if not self.illumination_config or current_row < 0 or current_row >= len(self.illumination_config.channels) - 1:
            return

        channels = self.illumination_config.channels
        channels[current_row], channels[current_row + 1] = channels[current_row + 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row + 1)

    def _open_port_mapping(self):
        """Open the controller port mapping dialog."""
        dialog = ControllerPortMappingDialog(self.config_repo, self)
        dialog.signal_mappings_updated.connect(self._load_channels)
        dialog.exec_()

    def _save_changes(self):
        """Save all changes to illumination channel config."""
        if not self.illumination_config:
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Save",
            "Saving these changes will modify your hardware configuration.\n"
            "This may affect existing acquisition settings.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from squid.core.config.models.illumination_config import IlluminationType

        # Validate channel names before saving
        names = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                name = name_item.text().strip()
                if not name:
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        f"Channel name at row {row + 1} cannot be empty.",
                    )
                    return
                if name in names:
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        f"Duplicate channel name '{name}' found.",
                    )
                    return
                names.append(name)

        # Update channels from table
        for row in range(self.table.rowCount()):
            channel = self.illumination_config.channels[row]

            # Name
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                channel.name = name_item.text().strip()

            # Type
            type_widget = self.table.cellWidget(row, self.COL_TYPE)
            if isinstance(type_widget, QComboBox):
                channel.type = IlluminationType(type_widget.currentText())

            # Controller Port
            port_widget = self.table.cellWidget(row, self.COL_PORT)
            if isinstance(port_widget, QComboBox):
                channel.controller_port = port_widget.currentText()

            # Wavelength (checkbox + spinbox widget)
            wave_widget = self.table.cellWidget(row, self.COL_WAVELENGTH)
            if isinstance(wave_widget, WavelengthWidget):
                channel.wavelength_nm = wave_widget.get_wavelength()
            else:
                channel.wavelength_nm = None

            # Calibration file (extract filename from full path)
            calib_item = self.table.item(row, self.COL_CALIBRATION)
            if calib_item:
                calib_text = calib_item.text().strip()
                if calib_text:
                    # Extract just the filename from full path
                    channel.intensity_calibration_file = Path(calib_text).name
                else:
                    channel.intensity_calibration_file = None

        # Save to YAML file
        self.config_repo.save_illumination_config(self.illumination_config)
        self.signal_channels_updated.emit()
        self.accept()


class AddIlluminationChannelDialog(QDialog):
    """Dialog for adding a new illumination channel."""

    def __init__(self, illumination_config, parent=None):
        super().__init__(parent)
        self.illumination_config = illumination_config
        self.setWindowTitle("Add Illumination Channel")
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Channel type
        self.type_combo = QComboBox()
        self.type_combo.addItems(["epi_illumination", "transillumination"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addRow("Type:", self.type_combo)

        # Name
        self.name_edit = QLineEdit()
        layout.addRow("Name:", self.name_edit)

        # Controller port - only ports with mappings
        available_ports = self.illumination_config.get_available_ports() if self.illumination_config else []
        # Reorder: D ports first for epi_illumination default
        d_ports = [p for p in available_ports if p.startswith("D")]
        usb_ports = [p for p in available_ports if p.startswith("USB")]
        self.port_combo = QComboBox()
        self.port_combo.addItems(d_ports + usb_ports)
        layout.addRow("Controller Port:", self.port_combo)

        # Wavelength (for epi_illumination, optional for transillumination)
        self.wave_spin = QSpinBox()
        self.wave_spin.setRange(200, 900)
        self.wave_spin.setValue(405)
        self.wave_spin.setSpecialValueText("N/A")  # Show N/A when value is minimum
        self.wave_spin.setMinimum(0)  # Allow 0 to represent N/A
        layout.addRow("Wavelength (nm):", self.wave_spin)

        # Calibration file
        self.calib_edit = QLineEdit()
        self.calib_edit.setPlaceholderText("e.g., 405.csv")
        layout.addRow("Calibration File:", self.calib_edit)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Add")
        self.btn_ok.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addRow(button_layout)

    def _validate_and_accept(self):
        """Validate input before accepting."""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Channel name cannot be empty.")
            return

        # Check for duplicate names
        if self.illumination_config:
            existing_names = [ch.name for ch in self.illumination_config.channels]
            if name in existing_names:
                QMessageBox.warning(self, "Validation Error", f"Channel '{name}' already exists.")
                return

        self.accept()

    def _on_type_changed(self, type_str):
        is_epi = type_str == "epi_illumination"
        available_ports = self.illumination_config.get_available_ports() if self.illumination_config else []
        first_d = next((p for p in available_ports if p.startswith("D")), None)
        first_usb = next((p for p in available_ports if p.startswith("USB")), None)

        # Update port default based on type
        if is_epi:
            if first_d:
                self.port_combo.setCurrentText(first_d)
            self.wave_spin.setValue(405)
        else:
            if first_usb:
                self.port_combo.setCurrentText(first_usb)
            self.wave_spin.setValue(0)  # Shows as N/A

    def get_channel_data(self):
        from squid.core.config.models.illumination_config import IlluminationType

        channel_type = IlluminationType(self.type_combo.currentText())
        wavelength = self.wave_spin.value()
        data = {
            "name": self.name_edit.text().strip(),
            "type": channel_type,
            "controller_port": self.port_combo.currentText(),
            "wavelength_nm": wavelength if wavelength > 0 else None,
        }

        calib_text = self.calib_edit.text().strip()
        data["intensity_calibration_file"] = calib_text if calib_text else None

        return data


# =============================================================================
# Controller Port Mapping Dialog
# =============================================================================


class ControllerPortMappingDialog(QDialog):
    """Dialog for editing controller port to source code mappings.

    Shows all available controller ports (USB1-USB8 for LED matrix, D1-D8 for lasers)
    and their corresponding illumination source codes.
    """

    signal_mappings_updated = Signal()

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.illumination_config = None
        self.setWindowTitle("Controller Port Mapping")
        self.setMinimumSize(400, 450)
        self._setup_ui()
        self._load_mappings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            "Map controller ports to illumination source codes. "
            "USB ports are for LED matrix patterns, D ports are for lasers."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info_label)

        # Table for port mappings
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Controller Port", "Source Code"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _load_mappings(self):
        """Load current port mappings into the table."""
        from squid.core.config.models.illumination_config import IlluminationChannelConfig

        self.illumination_config = self.config_repo.get_illumination_config()
        if not self.illumination_config:
            return

        port_mapping = self.illumination_config.controller_port_mapping
        all_ports = IlluminationChannelConfig.ALL_PORTS

        self.table.setRowCount(len(all_ports))

        for row, port in enumerate(all_ports):
            # Controller port (read-only)
            port_item = QTableWidgetItem(port)
            port_item.setFlags(port_item.flags() & ~Qt.ItemIsEditable)
            port_item.setBackground(QColor(240, 240, 240))
            self.table.setItem(row, 0, port_item)

            # Source code (editable spinbox with N/A option)
            source_code = port_mapping.get(port)
            source_widget = SourceCodeWidget(source_code)
            self.table.setCellWidget(row, 1, source_widget)

    def _save_changes(self):
        """Save changes to port mappings."""
        if not self.illumination_config:
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Save",
            "Saving these changes will modify your controller port mappings.\n"
            "This may affect existing acquisition settings.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Update mappings from table
        new_mapping = {}
        for row in range(self.table.rowCount()):
            port_item = self.table.item(row, 0)
            if not port_item:
                continue

            port = port_item.text()
            source_widget = self.table.cellWidget(row, 1)

            if isinstance(source_widget, SourceCodeWidget):
                source_code = source_widget.get_source_code()
                if source_code is not None:
                    new_mapping[port] = source_code

        self.illumination_config.controller_port_mapping = new_mapping
        self.config_repo.save_illumination_config(self.illumination_config)
        self.signal_mappings_updated.emit()
        self.accept()


# =============================================================================
# Acquisition Channel Dialogs
# =============================================================================


class AcquisitionChannelConfiguratorDialog(QDialog):
    """Dialog for editing acquisition channel configurations.

    Edits user_profiles/{profile}/channel_configs/general.yaml.
    Unlike IlluminationChannelConfiguratorDialog (hardware), this edits
    user-facing channel settings like enabled state, display color, camera,
    and filter wheel assignments.
    """

    signal_channels_updated = Signal()

    # Column indices for the channels table
    COL_ENABLED = 0
    COL_NAME = 1
    COL_ILLUMINATION = 2
    COL_CAMERA = 3
    COL_FILTER_WHEEL = 4
    COL_FILTER_POSITION = 5
    COL_DISPLAY_COLOR = 6

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.general_config = None
        self.illumination_config = None
        self.setWindowTitle("Acquisition Channel Configuration")
        self.setMinimumSize(700, 400)
        self._setup_ui()
        self._load_channels()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            "Configure acquisition channels for the current profile. "
            "Changes affect how channels appear in the live view and acquisition panels."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Table for acquisition channels
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Enabled", "Name", "Illumination", "Camera", "Filter Wheel", "Filter", "Color"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DISPLAY_COLOR, QHeaderView.Fixed)
        self.table.setColumnWidth(self.COL_DISPLAY_COLOR, 60)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add Channel")
        self.btn_add.setAutoDefault(False)
        self.btn_add.setDefault(False)
        self.btn_add.clicked.connect(self._add_channel)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Remove Channel")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.setDefault(False)
        self.btn_remove.clicked.connect(self._remove_channel)
        button_layout.addWidget(self.btn_remove)

        self.btn_move_up = QPushButton("Move Up")
        self.btn_move_up.setAutoDefault(False)
        self.btn_move_up.clicked.connect(self._move_up)
        button_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("Move Down")
        self.btn_move_down.setAutoDefault(False)
        self.btn_move_down.clicked.connect(self._move_down)
        button_layout.addWidget(self.btn_move_down)

        button_layout.addSpacing(20)

        self.btn_export = QPushButton("Export...")
        self.btn_export.setAutoDefault(False)
        self.btn_export.clicked.connect(self._export_config)
        button_layout.addWidget(self.btn_export)

        self.btn_import = QPushButton("Import...")
        self.btn_import.setAutoDefault(False)
        self.btn_import.clicked.connect(self._import_config)
        button_layout.addWidget(self.btn_import)

        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.setAutoDefault(False)
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _set_buttons_enabled(self, enabled: bool):
        """Enable or disable action buttons based on config availability."""
        self.btn_add.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_move_up.setEnabled(enabled)
        self.btn_move_down.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        # Import is always enabled since it can create a new config
        # Cancel is always enabled

    def _load_channels(self):
        """Load acquisition channels from general.yaml into the table."""
        self.general_config = self.config_repo.get_general_config()
        self.illumination_config = self.config_repo.get_illumination_config()

        if not self.general_config:
            self._log.warning("No general config found for current profile")
            QMessageBox.warning(
                self,
                "No Configuration",
                "No channel configuration found for the current profile.\n"
                "Please ensure a profile is selected and has been initialized.",
            )
            # Disable buttons when no config is loaded
            self._set_buttons_enabled(False)
            return

        # Enable buttons when config is loaded
        self._set_buttons_enabled(True)

        # Determine column visibility
        camera_names = self.config_repo.get_camera_names()
        wheel_names = self.config_repo.get_filter_wheel_names()
        has_any_wheel = wheel_names or _is_filter_wheel_enabled()

        # Hide Camera column if single camera (0 or 1)
        if len(camera_names) <= 1:
            self.table.setColumnHidden(self.COL_CAMERA, True)

        # Hide Filter Wheel column if single wheel (auto-assigned)
        if len(wheel_names) <= 1:
            self.table.setColumnHidden(self.COL_FILTER_WHEEL, True)

        # Hide Filter Position column only if NO wheels at all
        if not has_any_wheel:
            self.table.setColumnHidden(self.COL_FILTER_POSITION, True)

        self.table.setRowCount(len(self.general_config.channels))

        for row, channel in enumerate(self.general_config.channels):
            self._populate_row(row, channel)

    def _populate_row(self, row: int, channel):
        """Populate a table row with channel data."""
        # Enabled checkbox
        checkbox_widget = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_widget)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox = QCheckBox()
        enabled = channel.enabled if hasattr(channel, "enabled") else True
        checkbox.setChecked(enabled)
        checkbox_layout.addWidget(checkbox)
        self.table.setCellWidget(row, self.COL_ENABLED, checkbox_widget)

        # Name (editable text)
        name_item = QTableWidgetItem(channel.name)
        self.table.setItem(row, self.COL_NAME, name_item)

        # Illumination dropdown
        illum_combo = QComboBox()
        if self.illumination_config:
            illum_names = [ch.name for ch in self.illumination_config.channels]
            illum_combo.addItems(illum_names)
            # Set current illumination
            current_illum = channel.illumination_settings.illumination_channel
            if current_illum and current_illum in illum_names:
                illum_combo.setCurrentText(current_illum)
        self.table.setCellWidget(row, self.COL_ILLUMINATION, illum_combo)

        # Camera dropdown — AcquisitionChannel.camera is Optional[int] (camera ID)
        # We populate combo with addItem(name, id) and use currentData() to read back
        camera_combo = QComboBox()
        camera_combo.addItem("(None)", None)
        camera_registry = self.config_repo.get_camera_registry()
        if camera_registry:
            for cam in camera_registry.cameras:
                if cam.name is not None:
                    camera_combo.addItem(cam.name, cam.id)
        # Select current camera by ID
        if channel.camera is not None:
            for i in range(camera_combo.count()):
                if camera_combo.itemData(i) == channel.camera:
                    camera_combo.setCurrentIndex(i)
                    break
        self.table.setCellWidget(row, self.COL_CAMERA, camera_combo)

        # Filter wheel dropdown
        wheel_combo = QComboBox()
        wheel_combo.addItem("(None)")
        wheel_names = self.config_repo.get_filter_wheel_names()
        wheel_combo.addItems(wheel_names)
        # Set selection if channel has explicit wheel name
        if channel.filter_wheel and channel.filter_wheel in wheel_names:
            wheel_combo.setCurrentText(channel.filter_wheel)
        wheel_combo.currentTextChanged.connect(lambda text, r=row: self._on_wheel_changed(r, text))
        self.table.setCellWidget(row, self.COL_FILTER_WHEEL, wheel_combo)

        # Filter position dropdown - function auto-resolves single-wheel systems
        position_combo = QComboBox()
        _populate_filter_positions_for_combo(
            position_combo, channel.filter_wheel, self.config_repo, channel.filter_position
        )
        self.table.setCellWidget(row, self.COL_FILTER_POSITION, position_combo)

        # Display color (color picker button - fills cell width)
        color = channel.display_color if hasattr(channel, "display_color") else "#FFFFFF"
        color_btn = QPushButton()
        color_btn.setStyleSheet(f"background-color: {color};")
        color_btn.setProperty("color", color)
        color_btn.clicked.connect(lambda _checked, r=row: self._pick_color(r))
        self.table.setCellWidget(row, self.COL_DISPLAY_COLOR, color_btn)

    def _on_wheel_changed(self, row: int, wheel_name: str):
        """Update filter position options when wheel selection changes."""
        position_combo = self.table.cellWidget(row, self.COL_FILTER_POSITION)
        if position_combo:
            _populate_filter_positions_for_combo(position_combo, wheel_name, self.config_repo)

    def _pick_color(self, row: int):
        """Open color picker for a row."""
        color_btn = self.table.cellWidget(row, self.COL_DISPLAY_COLOR)
        current_color = QColor(color_btn.property("color") if color_btn else "#FFFFFF")
        color = QColorDialog.getColor(current_color, self, "Select Display Color")
        if color.isValid():
            color_btn.setStyleSheet(f"background-color: {color.name()};")
            color_btn.setProperty("color", color.name())

    def _add_channel(self):
        """Add a new acquisition channel."""
        if self.general_config is None:
            QMessageBox.warning(self, "Error", "No configuration loaded. Cannot add channel.")
            return

        dialog = AddAcquisitionChannelDialog(self.config_repo, self)
        if dialog.exec_() == QDialog.Accepted:
            channel = dialog.get_channel()
            if channel:
                self.general_config.channels.append(channel)
                # Reload table
                self._load_channels()

    def _remove_channel(self):
        """Remove selected channel."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row < 0:
            return

        name_item = self.table.item(current_row, self.COL_NAME)
        if name_item:
            reply = QMessageBox.question(
                self,
                "Confirm Removal",
                f"Remove channel '{name_item.text()}'?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes and current_row < len(self.general_config.channels):
                del self.general_config.channels[current_row]
                self._load_channels()

    def _move_up(self):
        """Move selected channel up."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row <= 0:
            return

        channels = self.general_config.channels
        channels[current_row - 1], channels[current_row] = channels[current_row], channels[current_row - 1]
        self._load_channels()
        self.table.selectRow(current_row - 1)

    def _move_down(self):
        """Move selected channel down."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self.general_config.channels) - 1:
            return

        channels = self.general_config.channels
        channels[current_row], channels[current_row + 1] = channels[current_row + 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row + 1)

    def _save_changes(self):
        """Save changes to general.yaml."""
        if self.general_config is None:
            QMessageBox.warning(self, "Error", "No configuration loaded. Cannot save.")
            return

        # Sync table data to config object
        self._sync_table_to_config()

        # Validate filter wheel/position consistency
        warnings = []
        for channel in self.general_config.channels:
            if channel.filter_wheel is not None and channel.filter_position is None:
                warnings.append(f"Channel '{channel.name}' has filter wheel but no position selected")
                self._log.warning(warnings[-1])

        if warnings:
            reply = QMessageBox.warning(
                self,
                "Configuration Warning",
                "Some channels have incomplete filter settings:\n\n" + "\n".join(warnings) + "\n\nSave anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Save to YAML file
        try:
            self.config_repo.save_general_config(self.config_repo.current_profile, self.general_config)
        except (PermissionError, OSError) as e:
            self._log.error(f"Failed to save channel configuration: {e}")
            QMessageBox.critical(self, "Save Failed", f"Cannot write configuration file:\n{e}")
            return
        except Exception as e:
            self._log.error(f"Unexpected error saving channel configuration: {e}")
            QMessageBox.critical(self, "Save Failed", f"Failed to save configuration:\n{e}")
            return

        self.signal_channels_updated.emit()
        self.accept()

    def _export_config(self):
        """Export current channel configuration to a YAML file."""
        from squid.core.config.models.acquisition_config import GeneralChannelConfig
        import yaml

        # Get save file path
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Channel Configuration",
            "channel_config.yaml",
            "YAML Files (*.yaml *.yml);;All Files (*)",
        )
        if not file_path:
            return

        # Build current config from table (same logic as _save_changes but without saving)
        self._sync_table_to_config()

        if not self.general_config:
            QMessageBox.warning(self, "Export Failed", "No configuration loaded to export.")
            return

        # Export to YAML
        try:
            data = self.general_config.model_dump()
            with open(file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            QMessageBox.information(self, "Export Successful", f"Configuration exported to:\n{file_path}")
        except (PermissionError, OSError) as e:
            self._log.warning(f"Failed to write export file {file_path}: {e}")
            QMessageBox.critical(self, "Export Failed", f"Cannot write to file:\n{e}")
        except Exception as e:
            self._log.error(f"Unexpected error during export: {e}")
            QMessageBox.critical(self, "Export Failed", f"Unexpected error:\n{e}")

    def _import_config(self):
        """Import channel configuration from a YAML file."""
        from pydantic import ValidationError
        from squid.core.config.models.acquisition_config import GeneralChannelConfig
        import yaml

        # Get file path
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Channel Configuration",
            "",
            "YAML Files (*.yaml *.yml);;All Files (*)",
        )
        if not file_path:
            return

        # Load and validate
        try:
            with open(file_path, "r") as f:
                data = yaml.safe_load(f)
            if data is None:
                raise ValueError("File is empty or contains no valid YAML content")
            imported_config = GeneralChannelConfig.model_validate(data)
        except (PermissionError, FileNotFoundError) as e:
            self._log.warning(f"Cannot read import file {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"Cannot read file:\n{e}")
            return
        except yaml.YAMLError as e:
            self._log.warning(f"Invalid YAML in {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"File contains invalid YAML:\n{e}")
            return
        except (ValidationError, ValueError) as e:
            self._log.warning(f"Config validation failed for {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"Configuration format error:\n{e}")
            return

        # Replace current config
        self.general_config = imported_config

        # Refresh the table
        self.table.setRowCount(0)
        self._load_channels()

        QMessageBox.information(
            self, "Import Successful", f"Imported {len(imported_config.channels)} channels from:\n{file_path}"
        )

    def _sync_table_to_config(self):
        """Sync table data back to self.general_config without saving to disk."""
        if self.general_config is None:
            return

        # Use bounds checking to handle potential table/config mismatch
        num_rows = min(self.table.rowCount(), len(self.general_config.channels))
        for row in range(num_rows):
            channel = self.general_config.channels[row]

            # Enabled
            checkbox_widget = self.table.cellWidget(row, self.COL_ENABLED)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    channel.enabled = checkbox.isChecked()

            # Name
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                channel.name = name_item.text().strip()

            # Illumination
            illum_combo = self.table.cellWidget(row, self.COL_ILLUMINATION)
            if illum_combo and isinstance(illum_combo, QComboBox):
                channel.illumination_settings.illumination_channel = illum_combo.currentText()

            # Camera — read back the camera ID from itemData, not the display text
            camera_combo = self.table.cellWidget(row, self.COL_CAMERA)
            if camera_combo and isinstance(camera_combo, QComboBox):
                channel.camera = camera_combo.currentData()

            # Filter wheel: None = no selection, else explicit wheel name
            wheel_combo = self.table.cellWidget(row, self.COL_FILTER_WHEEL)
            if wheel_combo and isinstance(wheel_combo, QComboBox):
                wheel_text = wheel_combo.currentText()
                channel.filter_wheel = wheel_text if wheel_text != "(None)" else None

            # Filter position
            position_combo = self.table.cellWidget(row, self.COL_FILTER_POSITION)
            if position_combo and isinstance(position_combo, QComboBox):
                channel.filter_position = position_combo.currentData()

            # Display color
            color_btn = self.table.cellWidget(row, self.COL_DISPLAY_COLOR)
            if color_btn:
                channel.display_color = color_btn.property("color") or "#FFFFFF"


class AddAcquisitionChannelDialog(QDialog):
    """Dialog for adding a new acquisition channel."""

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self.config_repo = config_repo
        self._display_color = "#FFFFFF"
        self.setWindowTitle("Add Acquisition Channel")
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Name
        self.name_edit = QLineEdit()
        layout.addRow("Name:", self.name_edit)

        # Illumination source dropdown
        self.illumination_combo = QComboBox()
        illum_config = self.config_repo.get_illumination_config()
        if illum_config:
            self.illumination_combo.addItems([ch.name for ch in illum_config.channels])
        layout.addRow("Illumination:", self.illumination_combo)

        # Camera dropdown (hidden if single camera - 0 or 1 cameras)
        camera_registry = self.config_repo.get_camera_registry()
        camera_names = self.config_repo.get_camera_names()
        if len(camera_names) > 1:
            self.camera_combo = QComboBox()
            self.camera_combo.addItem("(None)", None)
            if camera_registry:
                for cam in camera_registry.cameras:
                    if cam.name is not None:
                        self.camera_combo.addItem(cam.name, cam.id)
            layout.addRow("Camera:", self.camera_combo)
        else:
            self.camera_combo = None

        # Filter wheel dropdown (hidden if single wheel - 0 or 1 wheels)
        wheel_names = self.config_repo.get_filter_wheel_names()
        has_any_wheel = wheel_names or _is_filter_wheel_enabled()

        # Show wheel dropdown only for multi-wheel systems
        if len(wheel_names) > 1:
            self.wheel_combo = QComboBox()
            self.wheel_combo.addItem("(None)")
            self.wheel_combo.addItems(wheel_names)
            self.wheel_combo.currentTextChanged.connect(self._on_wheel_changed)
            layout.addRow("Filter Wheel:", self.wheel_combo)
        else:
            self.wheel_combo = None

        # Filter position dropdown (shown if any filter wheels exist)
        if has_any_wheel:
            self.position_combo = QComboBox()
            # Populate positions - function auto-resolves single-wheel systems
            _populate_filter_positions_for_combo(self.position_combo, None, self.config_repo)
            layout.addRow("Filter Position:", self.position_combo)
        else:
            self.position_combo = None

        # Display color
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(60, 25)
        self.color_btn.setStyleSheet(f"background-color: {self._display_color}; border: 1px solid #888;")
        self.color_btn.clicked.connect(self._pick_color)
        layout.addRow("Display Color:", self.color_btn)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Add")
        self.btn_ok.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addRow(button_layout)

    def _on_wheel_changed(self, wheel_name: str):
        """Update filter position options when wheel selection changes."""
        if self.position_combo is not None:
            _populate_filter_positions_for_combo(self.position_combo, wheel_name, self.config_repo)

    def _pick_color(self):
        """Open color picker."""
        color = QColorDialog.getColor(QColor(self._display_color), self, "Select Display Color")
        if color.isValid():
            self._display_color = color.name()
            self.color_btn.setStyleSheet(f"background-color: {self._display_color}; border: 1px solid #888;")

    def _validate_and_accept(self):
        """Validate input before accepting."""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Channel name cannot be empty.")
            return

        # Check for duplicate names
        general_config = self.config_repo.get_general_config()
        if general_config:
            existing_names = [ch.name for ch in general_config.channels]
            if name in existing_names:
                QMessageBox.warning(self, "Validation Error", f"Channel '{name}' already exists.")
                return

        self.accept()

    def get_channel(self):
        """Build AcquisitionChannel from dialog inputs."""
        from squid.core.config.models.acquisition_config import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        name = self.name_edit.text().strip()
        illum_name = self.illumination_combo.currentText()

        # Camera — read back camera ID from itemData, not display text
        camera = None
        if self.camera_combo:
            camera = self.camera_combo.currentData()

        # Filter wheel and position
        filter_wheel = None
        if self.wheel_combo:
            wheel_text = self.wheel_combo.currentText()
            filter_wheel = wheel_text if wheel_text != "(None)" else None
        filter_position = self.position_combo.currentData() if self.position_combo else None

        # Bug fix from upstream: z_offset_um belongs on AcquisitionChannel, not IlluminationSettings
        return AcquisitionChannel(
            name=name,
            enabled=True,
            display_color=self._display_color,
            camera=camera,
            filter_wheel=filter_wheel,
            filter_position=filter_position,
            z_offset_um=0.0,
            illumination_settings=IlluminationSettings(
                illumination_channel=illum_name,
                intensity=20.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=20.0,
                gain_mode=10.0,
                pixel_format=None,
            ),
        )


# =============================================================================
# Filter Wheel Dialog
# =============================================================================


class FilterWheelConfiguratorDialog(QDialog):
    """Dialog for configuring filter wheel position names.

    Edits machine_configs/filter_wheels.yaml to define filter wheels
    and their position-to-name mappings.
    """

    signal_config_updated = Signal()

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.registry = None
        self.setWindowTitle("Filter Wheel Configuration")
        self.setMinimumSize(500, 400)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Configure filter wheel position names. Each position can have a descriptive name\n"
            "(e.g., 'DAPI emission', 'GFP emission') that will appear in channel configuration."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Wheel selector (hidden for single-wheel systems)
        self.wheel_layout = QHBoxLayout()
        self.wheel_label = QLabel("Filter Wheel:")
        self.wheel_layout.addWidget(self.wheel_label)
        self.wheel_combo = QComboBox()
        self.wheel_combo.currentIndexChanged.connect(self._on_wheel_selected)
        self.wheel_layout.addWidget(self.wheel_combo, 1)
        layout.addLayout(self.wheel_layout)

        # Positions table
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Position", "Filter Name"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Save/Cancel buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_config)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _load_config(self):
        """Load filter wheel registry from config."""
        from squid.core.config.models.filter_wheel_config import FilterWheelRegistryConfig, FilterWheelDefinition, FilterWheelType

        self.registry = self.config_repo.get_filter_wheel_registry()

        # Check if filter wheel is enabled in .ini
        filter_wheel_enabled = _is_filter_wheel_enabled()

        # If no registry exists but filter wheel is enabled, create one with an unnamed wheel
        if self.registry is None:
            if filter_wheel_enabled:
                # Create unnamed wheel with default positions
                default_positions = {i: f"Position {i}" for i in range(1, 9)}
                self.registry = FilterWheelRegistryConfig(
                    filter_wheels=[FilterWheelDefinition(type=FilterWheelType.EMISSION, positions=default_positions)]
                )
            else:
                self.registry = FilterWheelRegistryConfig(filter_wheels=[])

        # For single wheel systems: remove name if present (migrate from old "Emission" name)
        is_single_wheel = len(self.registry.filter_wheels) == 1
        if is_single_wheel:
            wheel = self.registry.filter_wheels[0]
            if wheel.name is not None or wheel.id is not None:
                self.registry.filter_wheels[0] = FilterWheelDefinition(type=wheel.type, positions=wheel.positions)

        # Hide wheel selector for single-wheel systems
        self.wheel_label.setVisible(not is_single_wheel)
        self.wheel_combo.setVisible(not is_single_wheel)

        # Populate wheel combo (for multi-wheel systems)
        self.wheel_combo.clear()
        for wheel in self.registry.filter_wheels:
            display_name = wheel.name or "(Unnamed)"
            self.wheel_combo.addItem(display_name, wheel)

        # Select first wheel and load its positions
        if self.wheel_combo.count() > 0:
            self.wheel_combo.setCurrentIndex(0)
            self._on_wheel_selected(0)
        else:
            self.table.setRowCount(0)

    def _on_wheel_selected(self, index):
        """Load positions for selected wheel into table."""
        self.table.setRowCount(0)

        if index < 0:
            return

        wheel = self.wheel_combo.itemData(index)
        if wheel is None:
            return

        # Populate table with positions
        for pos in sorted(wheel.positions.keys()):
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Position number (read-only)
            pos_item = QTableWidgetItem(str(pos))
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, pos_item)

            # Filter name (editable)
            name_item = QTableWidgetItem(wheel.positions[pos])
            self.table.setItem(row, 1, name_item)

    def _save_config(self):
        """Save filter wheel configuration to YAML file."""
        # Sync table data back to current wheel
        index = self.wheel_combo.currentIndex()
        if index >= 0:
            wheel = self.wheel_combo.itemData(index)
            if wheel:
                wheel.positions.clear()
                for row in range(self.table.rowCount()):
                    pos_item = self.table.item(row, 0)
                    name_item = self.table.item(row, 1)
                    if pos_item and name_item:
                        pos = int(pos_item.text())
                        name = name_item.text().strip() or f"Position {pos}"
                        wheel.positions[pos] = name

        # Save to file using repository (ensures consistent serialization)
        try:
            self.config_repo.save_filter_wheel_registry(self.registry)
            self.signal_config_updated.emit()
            QMessageBox.information(self, "Saved", "Filter wheel configuration saved.")
            self.accept()
        except (PermissionError, OSError) as e:
            self._log.error(f"Failed to save filter wheel config: {e}")
            QMessageBox.critical(self, "Error", f"Cannot write configuration file:\n{e}")
        except Exception as e:
            self._log.exception(f"Unexpected error saving filter wheel config: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save configuration:\n{e}")
