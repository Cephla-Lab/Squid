# Configuration editor widgets
import configparser
import os
import squid.core.logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TYPE_CHECKING
from configparser import ConfigParser
from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QFrame,
    QSizePolicy,
    QInputDialog,
)

from squid.ui.widgets.base import CollapsibleGroupBox
from squid.core.events import ProfileChanged

if TYPE_CHECKING:
    from squid.backend.managers import ConfigurationManager
    from squid.ui.ui_event_bus import UIEventBus


class ConfigEditor(QDialog):
    config: ConfigParser
    scroll_area: QScrollArea
    scroll_area_widget: QWidget
    scroll_area_layout: QVBoxLayout
    save_config_button: QPushButton
    save_to_file_button: QPushButton
    load_config_button: QPushButton
    config_value_widgets: Dict[str, Dict[str, Any]]
    groups: Dict[str, CollapsibleGroupBox]

    def __init__(self, config: ConfigParser) -> None:
        super().__init__()
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config = config

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area_widget = QWidget()
        self.scroll_area_layout = QVBoxLayout()
        self.scroll_area_widget.setLayout(self.scroll_area_layout)
        self.scroll_area.setWidget(self.scroll_area_widget)

        self.save_config_button = QPushButton("Save Config")
        self.save_config_button.clicked.connect(self.save_config)
        self.save_to_file_button = QPushButton("Save to File")
        self.save_to_file_button.clicked.connect(self.save_to_file)
        self.load_config_button = QPushButton("Load Config from File")
        self.load_config_button.clicked.connect(self.load_config_from_file)

        layout = QVBoxLayout()
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.save_config_button)
        layout.addWidget(self.save_to_file_button)
        layout.addWidget(self.load_config_button)

        self.config_value_widgets = {}

        self.setLayout(layout)
        self.setWindowTitle("Configuration Editor")
        self.init_ui()

    def init_ui(self) -> None:
        self.groups = {}
        for section in self.config.sections():
            group_box = CollapsibleGroupBox(section)
            group_layout = QVBoxLayout()

            section_value_widgets = {}

            self.groups[section] = group_box

            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                option_value = self.config.get(section, option)
                option_name = QLabel(option)
                option_layout = QHBoxLayout()
                option_layout.addWidget(option_name)
                if f"_{option}_options" in self.config.options(section):
                    option_value_list = self.config.get(section, f"_{option}_options")
                    values = option_value_list.strip("[]").split(",")
                    for i in range(len(values)):
                        values[i] = values[i].strip()
                    if option_value not in values:
                        values.append(option_value)
                    combo_box = QComboBox()
                    combo_box.addItems(values)
                    combo_box.setCurrentText(option_value)
                    option_layout.addWidget(combo_box)
                    section_value_widgets[option] = combo_box
                else:
                    option_input = QLineEdit(option_value)
                    option_layout.addWidget(option_input)
                    section_value_widgets[option] = option_input
                group_layout.addLayout(option_layout)

            self.config_value_widgets[section] = section_value_widgets
            group_box.content.addLayout(group_layout)
            self.scroll_area_layout.addWidget(group_box)

    def save_config(self) -> None:
        for section in self.config.sections():
            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                old_val = self.config.get(section, option)
                widget = self.config_value_widgets[section][option]
                if type(widget) is QLineEdit:
                    self.config.set(section, option, widget.text())
                else:
                    self.config.set(section, option, widget.currentText())
                if old_val != self.config.get(section, option):
                    print(self.config.get(section, option))

    def save_to_filename(self, filename: str) -> bool:
        try:
            with open(filename, "w") as configfile:
                self.config.write(configfile)
                return True
        except IOError:
            self._log.exception(f"Failed to write config file to '{filename}'")
            return False

    def save_to_file(self) -> None:
        self.save_config()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Config File", "", "INI Files (*.ini);;All Files (*)"
        )
        if file_path:
            if not self.save_to_filename(file_path):
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"Failed to write config file to '{file_path}'.  Check permissions!",
                )

    def load_config_from_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Config File", "", "INI Files (*.ini);;All Files (*)"
        )
        if file_path:
            self.config.read(file_path)
            # Clear and re-initialize the UI
            self.scroll_area_widget.deleteLater()
            self.scroll_area_widget = QWidget()
            self.scroll_area_layout = QVBoxLayout()
            self.scroll_area_widget.setLayout(self.scroll_area_layout)
            self.scroll_area.setWidget(self.scroll_area_widget)
            self.init_ui()


class ConfigEditorBackwardsCompatible(ConfigEditor):
    original_filepath: str
    main_window: QWidget
    apply_exit_button: QPushButton

    def __init__(
        self, config: ConfigParser, original_filepath: str, main_window: QWidget
    ) -> None:
        super().__init__(config)
        self.original_filepath = original_filepath
        self.main_window = main_window

        self.apply_exit_button = QPushButton("Apply and Exit")
        self.apply_exit_button.clicked.connect(self.apply_and_exit)

        layout = self.layout()
        if layout is not None:
            layout.addWidget(self.apply_exit_button)

    def apply_and_exit(self) -> None:
        self.save_config()
        with open(self.original_filepath, "w") as configfile:
            self.config.write(configfile)
        try:
            self.main_window.close()
        except Exception:
            pass
        self.close()


class ProfileWidget(QFrame):
    signal_profile_changed: Signal = Signal()

    configurationManager: "ConfigurationManager"
    dropdown_profiles: QComboBox
    btn_newProfile: QPushButton

    def __init__(
        self,
        configurationManager: "ConfigurationManager",
        event_bus: Optional["UIEventBus"] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.configurationManager = configurationManager
        self._event_bus = event_bus
        self._subscriptions: List[Tuple[Type, Callable]] = []

        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.setup_ui()

    def setup_ui(self) -> None:
        # Create widgets
        self.dropdown_profiles = QComboBox()
        self.dropdown_profiles.addItems(self.configurationManager.available_profiles)
        self.dropdown_profiles.setCurrentText(self.configurationManager.current_profile)
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_profiles.setSizePolicy(sizePolicy)

        self.btn_newProfile = QPushButton("Save As")

        # Connect signals
        self.dropdown_profiles.currentTextChanged.connect(self.load_profile)
        self.btn_newProfile.clicked.connect(self.create_new_profile)

        # Layout
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Configuration Profile"))
        layout.addWidget(self.dropdown_profiles, 2)
        layout.addWidget(self.btn_newProfile)

        self.setLayout(layout)

    def load_profile(self) -> None:
        """Load the selected profile."""
        profile_name = self.dropdown_profiles.currentText()
        # Load the profile
        self.configurationManager.load_profile(profile_name)
        self.signal_profile_changed.emit()
        if self._event_bus is not None:
            self._event_bus.publish(ProfileChanged(profile_name=profile_name))

    def create_new_profile(self) -> None:
        """Create a new profile with current configurations."""
        dialog = QInputDialog()
        profile_name, ok = dialog.getText(
            self, "New Profile", "Enter new profile name:", QLineEdit.Normal, ""
        )

        if ok and profile_name:
            try:
                self.configurationManager.create_new_profile(profile_name)
                # Update profile dropdown
                self.dropdown_profiles.addItem(profile_name)
                self.dropdown_profiles.setCurrentText(profile_name)
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))

    def get_current_profile(self) -> str:
        """Return the currently selected profile name."""
        return self.dropdown_profiles.currentText()


class PreferencesDialog(QDialog):
    """User-friendly preferences dialog with tabbed interface for common settings.

    This dialog provides a graphical interface for editing configuration settings
    without manually editing INI files. Settings are organized into tabs:
    - General: File saving format, default paths
    - Acquisition: Autofocus channel, multipoint settings
    - Camera: Binning, flip, temperature, ROI defaults
    - Advanced: Stage motion, autofocus, hardware, position limits, tracking

    Settings marked with (*) require a software restart to take effect.
    """

    signal_config_changed: Signal = Signal()

    def __init__(
        self,
        config: ConfigParser,
        config_filepath: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.config = config
        self.config_filepath = config_filepath
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Tab widget
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)

        # Create tabs
        self._create_general_tab()
        self._create_acquisition_tab()
        self._create_camera_tab()
        self._create_advanced_tab()

        # Buttons
        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save_and_close)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

    def _create_general_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # File Saving Format
        self.file_saving_combo = QComboBox()
        self.file_saving_combo.addItems(["OME_TIFF", "MULTI_PAGE_TIFF", "INDIVIDUAL_IMAGES"])
        current_value = self._get_config_value("GENERAL", "file_saving_option", "OME_TIFF")
        self.file_saving_combo.setCurrentText(current_value)
        layout.addRow("File Saving Format:", self.file_saving_combo)

        # Default Saving Path
        path_widget = QWidget()
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.saving_path_edit = QLineEdit()
        self.saving_path_edit.setText(
            self._get_config_value("GENERAL", "default_saving_path", str(Path.home() / "Downloads"))
        )
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_saving_path)
        path_layout.addWidget(self.saving_path_edit)
        path_layout.addWidget(browse_button)
        layout.addRow("Default Saving Path:", path_widget)

        self.tab_widget.addTab(tab, "General")

    def _create_acquisition_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # Autofocus Channel
        self.autofocus_channel_edit = QLineEdit()
        self.autofocus_channel_edit.setText(
            self._get_config_value("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
        )
        layout.addRow("Autofocus Channel:", self.autofocus_channel_edit)

        # Enable Flexible Multipoint
        self.flexible_multipoint_checkbox = QCheckBox()
        self.flexible_multipoint_checkbox.setChecked(
            self._get_config_bool("GENERAL", "enable_flexible_multipoint", True)
        )
        layout.addRow("Enable Flexible Multipoint:", self.flexible_multipoint_checkbox)

        self.tab_widget.addTab(tab, "Acquisition")

    def _create_camera_tab(self) -> None:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # Restart warning label
        restart_label = QLabel("Note: Camera settings require software restart to take effect.")
        restart_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addRow(restart_label)

        # Default Binning Factor
        self.binning_spinbox = QSpinBox()
        self.binning_spinbox.setRange(1, 4)
        self.binning_spinbox.setValue(self._get_config_int("CAMERA_CONFIG", "binning_factor_default", 2))
        layout.addRow("Default Binning Factor:", self.binning_spinbox)

        # Image Flip
        self.flip_combo = QComboBox()
        self.flip_combo.addItems(["None", "Vertical", "Horizontal", "Both"])
        current_flip = self._get_config_value("CAMERA_CONFIG", "flip_image", "None")
        self.flip_combo.setCurrentText(current_flip)
        layout.addRow("Image Flip:", self.flip_combo)

        # Temperature Default
        self.temperature_spinbox = QSpinBox()
        self.temperature_spinbox.setRange(-20, 40)
        self.temperature_spinbox.setValue(self._get_config_int("CAMERA_CONFIG", "temperature_default", 20))
        self.temperature_spinbox.setSuffix(" C")
        layout.addRow("Temperature Default:", self.temperature_spinbox)

        # ROI Width
        self.roi_width_spinbox = QSpinBox()
        self.roi_width_spinbox.setRange(0, 10000)
        self.roi_width_spinbox.setSpecialValueText("Auto")
        roi_width = self._get_config_value("CAMERA_CONFIG", "roi_width_default", "None")
        if roi_width == "None":
            self.roi_width_spinbox.setValue(0)
        else:
            try:
                self.roi_width_spinbox.setValue(int(roi_width))
            except ValueError:
                self._log.warning(f"Invalid roi_width_default value '{roi_width}', using Auto")
                self.roi_width_spinbox.setValue(0)
        layout.addRow("ROI Width:", self.roi_width_spinbox)

        # ROI Height
        self.roi_height_spinbox = QSpinBox()
        self.roi_height_spinbox.setRange(0, 10000)
        self.roi_height_spinbox.setSpecialValueText("Auto")
        roi_height = self._get_config_value("CAMERA_CONFIG", "roi_height_default", "None")
        if roi_height == "None":
            self.roi_height_spinbox.setValue(0)
        else:
            try:
                self.roi_height_spinbox.setValue(int(roi_height))
            except ValueError:
                self._log.warning(f"Invalid roi_height_default value '{roi_height}', using Auto")
                self.roi_height_spinbox.setValue(0)
        layout.addRow("ROI Height:", self.roi_height_spinbox)

        self.tab_widget.addTab(tab, "Camera")

    def _create_advanced_tab(self) -> None:
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        # Stage & Motion section (requires restart)
        stage_group = CollapsibleGroupBox("Stage && Motion *")
        stage_layout = QFormLayout()

        self.max_vel_x = QDoubleSpinBox()
        self.max_vel_x.setRange(0.1, 100)
        self.max_vel_x.setValue(self._get_config_float("GENERAL", "max_velocity_x_mm", 30))
        self.max_vel_x.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity X:", self.max_vel_x)

        self.max_vel_y = QDoubleSpinBox()
        self.max_vel_y.setRange(0.1, 100)
        self.max_vel_y.setValue(self._get_config_float("GENERAL", "max_velocity_y_mm", 30))
        self.max_vel_y.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity Y:", self.max_vel_y)

        self.max_vel_z = QDoubleSpinBox()
        self.max_vel_z.setRange(0.1, 20)
        self.max_vel_z.setValue(self._get_config_float("GENERAL", "max_velocity_z_mm", 3.8))
        self.max_vel_z.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity Z:", self.max_vel_z)

        self.max_accel_x = QDoubleSpinBox()
        self.max_accel_x.setRange(1, 2000)
        self.max_accel_x.setValue(self._get_config_float("GENERAL", "max_acceleration_x_mm", 500))
        self.max_accel_x.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration X:", self.max_accel_x)

        self.max_accel_y = QDoubleSpinBox()
        self.max_accel_y.setRange(1, 2000)
        self.max_accel_y.setValue(self._get_config_float("GENERAL", "max_acceleration_y_mm", 500))
        self.max_accel_y.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration Y:", self.max_accel_y)

        self.max_accel_z = QDoubleSpinBox()
        self.max_accel_z.setRange(1, 500)
        self.max_accel_z.setValue(self._get_config_float("GENERAL", "max_acceleration_z_mm", 100))
        self.max_accel_z.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration Z:", self.max_accel_z)

        self.scan_stab_x = QSpinBox()
        self.scan_stab_x.setRange(0, 1000)
        self.scan_stab_x.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_x", 25))
        self.scan_stab_x.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization X:", self.scan_stab_x)

        self.scan_stab_y = QSpinBox()
        self.scan_stab_y.setRange(0, 1000)
        self.scan_stab_y.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_y", 25))
        self.scan_stab_y.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization Y:", self.scan_stab_y)

        self.scan_stab_z = QSpinBox()
        self.scan_stab_z.setRange(0, 1000)
        self.scan_stab_z.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_z", 20))
        self.scan_stab_z.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization Z:", self.scan_stab_z)

        stage_group.content.addLayout(stage_layout)
        layout.addWidget(stage_group)

        # Contrast Autofocus section
        af_group = CollapsibleGroupBox("Contrast Autofocus")
        af_layout = QFormLayout()

        self.af_stop_threshold = QDoubleSpinBox()
        self.af_stop_threshold.setRange(0.1, 1.0)
        self.af_stop_threshold.setSingleStep(0.05)
        self.af_stop_threshold.setValue(self._get_config_float("AF", "stop_threshold", 0.85))
        af_layout.addRow("Stop Threshold:", self.af_stop_threshold)

        self.af_crop_width = QSpinBox()
        self.af_crop_width.setRange(100, 4000)
        self.af_crop_width.setValue(self._get_config_int("AF", "crop_width", 800))
        self.af_crop_width.setSuffix(" px")
        af_layout.addRow("Crop Width:", self.af_crop_width)

        self.af_crop_height = QSpinBox()
        self.af_crop_height.setRange(100, 4000)
        self.af_crop_height.setValue(self._get_config_int("AF", "crop_height", 800))
        self.af_crop_height.setSuffix(" px")
        af_layout.addRow("Crop Height:", self.af_crop_height)

        af_group.content.addLayout(af_layout)
        layout.addWidget(af_group)

        # Hardware Configuration section
        hw_group = CollapsibleGroupBox("Hardware Configuration")
        hw_layout = QFormLayout()

        self.z_motor_combo = QComboBox()
        self.z_motor_combo.addItems(["STEPPER", "STEPPER + PIEZO", "PIEZO", "LINEAR"])
        self.z_motor_combo.setCurrentText(self._get_config_value("GENERAL", "z_motor_config", "STEPPER"))
        hw_layout.addRow("Z Motor Config *:", self.z_motor_combo)

        self.spinning_disk_checkbox = QCheckBox()
        self.spinning_disk_checkbox.setChecked(self._get_config_bool("GENERAL", "enable_spinning_disk_confocal", False))
        hw_layout.addRow("Enable Spinning Disk *:", self.spinning_disk_checkbox)

        self.led_r_factor = QDoubleSpinBox()
        self.led_r_factor.setRange(0.0, 1.0)
        self.led_r_factor.setSingleStep(0.1)
        self.led_r_factor.setValue(self._get_config_float("GENERAL", "led_matrix_r_factor", 1.0))
        hw_layout.addRow("LED Matrix R Factor:", self.led_r_factor)

        self.led_g_factor = QDoubleSpinBox()
        self.led_g_factor.setRange(0.0, 1.0)
        self.led_g_factor.setSingleStep(0.1)
        self.led_g_factor.setValue(self._get_config_float("GENERAL", "led_matrix_g_factor", 1.0))
        hw_layout.addRow("LED Matrix G Factor:", self.led_g_factor)

        self.led_b_factor = QDoubleSpinBox()
        self.led_b_factor.setRange(0.0, 1.0)
        self.led_b_factor.setSingleStep(0.1)
        self.led_b_factor.setValue(self._get_config_float("GENERAL", "led_matrix_b_factor", 1.0))
        hw_layout.addRow("LED Matrix B Factor:", self.led_b_factor)

        self.illumination_factor = QDoubleSpinBox()
        self.illumination_factor.setRange(0.0, 1.0)
        self.illumination_factor.setSingleStep(0.1)
        self.illumination_factor.setValue(self._get_config_float("GENERAL", "illumination_intensity_factor", 0.6))
        hw_layout.addRow("Illumination Intensity Factor:", self.illumination_factor)

        hw_group.content.addLayout(hw_layout)
        layout.addWidget(hw_group)

        # Software Position Limits section
        limits_group = CollapsibleGroupBox("Software Position Limits")
        limits_layout = QFormLayout()

        self.limit_x_pos = QDoubleSpinBox()
        self.limit_x_pos.setRange(0, 500)
        self.limit_x_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "x_positive", 115))
        self.limit_x_pos.setSuffix(" mm")
        limits_layout.addRow("X Positive:", self.limit_x_pos)

        self.limit_x_neg = QDoubleSpinBox()
        self.limit_x_neg.setRange(0, 500)
        self.limit_x_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "x_negative", 5))
        self.limit_x_neg.setSuffix(" mm")
        limits_layout.addRow("X Negative:", self.limit_x_neg)

        self.limit_y_pos = QDoubleSpinBox()
        self.limit_y_pos.setRange(0, 500)
        self.limit_y_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "y_positive", 76))
        self.limit_y_pos.setSuffix(" mm")
        limits_layout.addRow("Y Positive:", self.limit_y_pos)

        self.limit_y_neg = QDoubleSpinBox()
        self.limit_y_neg.setRange(0, 500)
        self.limit_y_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "y_negative", 4))
        self.limit_y_neg.setSuffix(" mm")
        limits_layout.addRow("Y Negative:", self.limit_y_neg)

        self.limit_z_pos = QDoubleSpinBox()
        self.limit_z_pos.setRange(0, 50)
        self.limit_z_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "z_positive", 6))
        self.limit_z_pos.setSuffix(" mm")
        limits_layout.addRow("Z Positive:", self.limit_z_pos)

        self.limit_z_neg = QDoubleSpinBox()
        self.limit_z_neg.setRange(0, 50)
        self.limit_z_neg.setDecimals(3)
        self.limit_z_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "z_negative", 0.05))
        self.limit_z_neg.setSuffix(" mm")
        limits_layout.addRow("Z Negative:", self.limit_z_neg)

        limits_group.content.addLayout(limits_layout)
        layout.addWidget(limits_group)

        # Tracking section
        tracking_group = CollapsibleGroupBox("Tracking")
        tracking_layout = QFormLayout()

        self.enable_tracking_checkbox = QCheckBox()
        self.enable_tracking_checkbox.setChecked(self._get_config_bool("GENERAL", "enable_tracking", False))
        tracking_layout.addRow("Enable Tracking:", self.enable_tracking_checkbox)

        self.default_tracker_combo = QComboBox()
        self.default_tracker_combo.addItems(["csrt", "kcf", "mil", "tld", "medianflow", "mosse", "daSiamRPN"])
        self.default_tracker_combo.setCurrentText(self._get_config_value("TRACKING", "default_tracker", "csrt"))
        tracking_layout.addRow("Default Tracker:", self.default_tracker_combo)

        self.search_area_ratio = QSpinBox()
        self.search_area_ratio.setRange(1, 50)
        self.search_area_ratio.setValue(self._get_config_int("TRACKING", "search_area_ratio", 10))
        tracking_layout.addRow("Search Area Ratio:", self.search_area_ratio)

        tracking_group.content.addLayout(tracking_layout)
        layout.addWidget(tracking_group)

        # Legend for restart indicator
        legend_label = QLabel("* Requires software restart to take effect")
        legend_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(legend_label)

        layout.addStretch()
        scroll.setWidget(scroll_content)

        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)
        self.tab_widget.addTab(tab, "Advanced")

    def _get_config_value(self, section: str, option: str, default: str = "") -> str:
        try:
            return self.config.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def _get_config_bool(self, section: str, option: str, default: bool = False) -> bool:
        try:
            val = self.config.get(section, option)
            return str(val).strip().lower() in ("true", "1", "yes", "on")
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def _get_config_int(self, section: str, option: str, default: int = 0) -> int:
        try:
            return int(self.config.get(section, option))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def _get_config_float(self, section: str, option: str, default: float = 0.0) -> float:
        try:
            return float(self.config.get(section, option))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def _floats_equal(self, a: float, b: float, epsilon: float = 1e-4) -> bool:
        """Compare two floats with epsilon tolerance to avoid precision issues."""
        return abs(a - b) < epsilon

    def _browse_saving_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Default Saving Path", self.saving_path_edit.text())
        if path:
            if os.access(path, os.W_OK):
                self.saving_path_edit.setText(path)
            else:
                QMessageBox.warning(self, "Invalid Path", f"The selected directory is not writable:\n{path}")

    def _ensure_section(self, section: str) -> None:
        """Ensure a config section exists, creating it if necessary."""
        if not self.config.has_section(section):
            self.config.add_section(section)

    def _apply_settings(self) -> None:
        # Ensure all required sections exist
        for section in ["GENERAL", "CAMERA_CONFIG", "AF", "SOFTWARE_POS_LIMIT", "TRACKING"]:
            self._ensure_section(section)

        # General settings
        self.config.set("GENERAL", "file_saving_option", self.file_saving_combo.currentText())
        self.config.set("GENERAL", "default_saving_path", self.saving_path_edit.text())

        # Acquisition settings
        self.config.set("GENERAL", "multipoint_autofocus_channel", self.autofocus_channel_edit.text())
        self.config.set(
            "GENERAL",
            "enable_flexible_multipoint",
            "true" if self.flexible_multipoint_checkbox.isChecked() else "false",
        )

        # Camera settings
        self.config.set("CAMERA_CONFIG", "binning_factor_default", str(self.binning_spinbox.value()))
        self.config.set("CAMERA_CONFIG", "flip_image", self.flip_combo.currentText())
        self.config.set("CAMERA_CONFIG", "temperature_default", str(self.temperature_spinbox.value()))
        roi_width = "None" if self.roi_width_spinbox.value() == 0 else str(self.roi_width_spinbox.value())
        roi_height = "None" if self.roi_height_spinbox.value() == 0 else str(self.roi_height_spinbox.value())
        self.config.set("CAMERA_CONFIG", "roi_width_default", roi_width)
        self.config.set("CAMERA_CONFIG", "roi_height_default", roi_height)

        # Advanced - Stage & Motion
        self.config.set("GENERAL", "max_velocity_x_mm", str(self.max_vel_x.value()))
        self.config.set("GENERAL", "max_velocity_y_mm", str(self.max_vel_y.value()))
        self.config.set("GENERAL", "max_velocity_z_mm", str(self.max_vel_z.value()))
        self.config.set("GENERAL", "max_acceleration_x_mm", str(self.max_accel_x.value()))
        self.config.set("GENERAL", "max_acceleration_y_mm", str(self.max_accel_y.value()))
        self.config.set("GENERAL", "max_acceleration_z_mm", str(self.max_accel_z.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_x", str(self.scan_stab_x.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_y", str(self.scan_stab_y.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_z", str(self.scan_stab_z.value()))

        # Advanced - Autofocus
        self.config.set("AF", "stop_threshold", str(self.af_stop_threshold.value()))
        self.config.set("AF", "crop_width", str(self.af_crop_width.value()))
        self.config.set("AF", "crop_height", str(self.af_crop_height.value()))

        # Advanced - Hardware
        self.config.set("GENERAL", "z_motor_config", self.z_motor_combo.currentText())
        self.config.set(
            "GENERAL",
            "enable_spinning_disk_confocal",
            "true" if self.spinning_disk_checkbox.isChecked() else "false",
        )
        self.config.set("GENERAL", "led_matrix_r_factor", str(self.led_r_factor.value()))
        self.config.set("GENERAL", "led_matrix_g_factor", str(self.led_g_factor.value()))
        self.config.set("GENERAL", "led_matrix_b_factor", str(self.led_b_factor.value()))
        self.config.set("GENERAL", "illumination_intensity_factor", str(self.illumination_factor.value()))

        # Advanced - Position Limits
        self.config.set("SOFTWARE_POS_LIMIT", "x_positive", str(self.limit_x_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "x_negative", str(self.limit_x_neg.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "y_positive", str(self.limit_y_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "y_negative", str(self.limit_y_neg.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "z_positive", str(self.limit_z_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "z_negative", str(self.limit_z_neg.value()))

        # Advanced - Tracking
        self.config.set("GENERAL", "enable_tracking", "true" if self.enable_tracking_checkbox.isChecked() else "false")
        self.config.set("TRACKING", "default_tracker", self.default_tracker_combo.currentText())
        self.config.set("TRACKING", "search_area_ratio", str(self.search_area_ratio.value()))

        # Save to file
        try:
            with open(self.config_filepath, "w") as f:
                self.config.write(f)
            self._log.info(f"Configuration saved to {self.config_filepath}")
        except OSError as e:
            self._log.exception("Failed to save configuration")
            QMessageBox.warning(
                self,
                "Error",
                (
                    f"Failed to save configuration to:\n"
                    f"{self.config_filepath}\n\n"
                    "Please check that:\n"
                    "- You have write permission to this location.\n"
                    "- The file is not open in another application.\n"
                    "- The disk is not full or write-protected.\n\n"
                    f"System error: {e}"
                ),
            )
            return

        # Update runtime values for settings that can be applied live
        try:
            self._apply_live_settings()
        except Exception:
            self._log.exception("Failed to apply live settings")

        self.signal_config_changed.emit()

    def _apply_live_settings(self) -> None:
        """Apply settings that can take effect without restart."""
        # Local import to get the module reference for updating runtime values
        try:
            import _def
        except ImportError:
            self._log.warning("Cannot import _def module for live settings update")
            return

        # File saving option
        if hasattr(_def, "FileSavingOption") and hasattr(_def.FileSavingOption, "convert_to_enum"):
            _def.FILE_SAVING_OPTION = _def.FileSavingOption.convert_to_enum(self.file_saving_combo.currentText())

        # Default saving path
        if hasattr(_def, "DEFAULT_SAVING_PATH"):
            _def.DEFAULT_SAVING_PATH = self.saving_path_edit.text()

        # Autofocus channel
        if hasattr(_def, "MULTIPOINT_AUTOFOCUS_CHANNEL"):
            _def.MULTIPOINT_AUTOFOCUS_CHANNEL = self.autofocus_channel_edit.text()

        # Flexible multipoint
        if hasattr(_def, "ENABLE_FLEXIBLE_MULTIPOINT"):
            _def.ENABLE_FLEXIBLE_MULTIPOINT = self.flexible_multipoint_checkbox.isChecked()

        # AF settings
        if hasattr(_def, "AF"):
            _def.AF.STOP_THRESHOLD = self.af_stop_threshold.value()
            _def.AF.CROP_WIDTH = self.af_crop_width.value()
            _def.AF.CROP_HEIGHT = self.af_crop_height.value()

        # LED matrix factors
        if hasattr(_def, "LED_MATRIX_R_FACTOR"):
            _def.LED_MATRIX_R_FACTOR = self.led_r_factor.value()
        if hasattr(_def, "LED_MATRIX_G_FACTOR"):
            _def.LED_MATRIX_G_FACTOR = self.led_g_factor.value()
        if hasattr(_def, "LED_MATRIX_B_FACTOR"):
            _def.LED_MATRIX_B_FACTOR = self.led_b_factor.value()

        # Illumination intensity factor
        if hasattr(_def, "ILLUMINATION_INTENSITY_FACTOR"):
            _def.ILLUMINATION_INTENSITY_FACTOR = self.illumination_factor.value()

        # Software position limits
        if hasattr(_def, "SOFTWARE_POS_LIMIT"):
            _def.SOFTWARE_POS_LIMIT.X_POSITIVE = self.limit_x_pos.value()
            _def.SOFTWARE_POS_LIMIT.X_NEGATIVE = self.limit_x_neg.value()
            _def.SOFTWARE_POS_LIMIT.Y_POSITIVE = self.limit_y_pos.value()
            _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE = self.limit_y_neg.value()
            _def.SOFTWARE_POS_LIMIT.Z_POSITIVE = self.limit_z_pos.value()
            _def.SOFTWARE_POS_LIMIT.Z_NEGATIVE = self.limit_z_neg.value()

        # Tracking settings
        if hasattr(_def, "ENABLE_TRACKING"):
            _def.ENABLE_TRACKING = self.enable_tracking_checkbox.isChecked()
        if hasattr(_def, "Tracking"):
            _def.Tracking.DEFAULT_TRACKER = self.default_tracker_combo.currentText()
            _def.Tracking.SEARCH_AREA_RATIO = self.search_area_ratio.value()

    def _get_changes(self) -> List[Tuple[str, str, str, bool]]:
        """Get list of settings that have changed from current config.

        Returns list of (name, old, new, requires_restart) tuples.
        """
        changes: List[Tuple[str, str, str, bool]] = []

        # General settings (live update)
        old_val = self._get_config_value("GENERAL", "file_saving_option", "OME_TIFF")
        new_val = self.file_saving_combo.currentText()
        if old_val != new_val:
            changes.append(("File Saving Format", old_val, new_val, False))

        old_val = self._get_config_value("GENERAL", "default_saving_path", str(Path.home() / "Downloads"))
        new_val = self.saving_path_edit.text()
        if old_val != new_val:
            changes.append(("Default Saving Path", old_val, new_val, False))

        # Acquisition settings (live update)
        old_val = self._get_config_value("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
        new_val = self.autofocus_channel_edit.text()
        if old_val != new_val:
            changes.append(("Autofocus Channel", old_val, new_val, False))

        old_bool = self._get_config_bool("GENERAL", "enable_flexible_multipoint", True)
        new_bool = self.flexible_multipoint_checkbox.isChecked()
        if old_bool != new_bool:
            changes.append(("Enable Flexible Multipoint", str(old_bool), str(new_bool), False))

        # Camera settings (require restart)
        old_int = self._get_config_int("CAMERA_CONFIG", "binning_factor_default", 2)
        new_int = self.binning_spinbox.value()
        if old_int != new_int:
            changes.append(("Default Binning Factor", str(old_int), str(new_int), True))

        old_val = self._get_config_value("CAMERA_CONFIG", "flip_image", "None")
        new_val = self.flip_combo.currentText()
        if old_val != new_val:
            changes.append(("Image Flip", old_val, new_val, True))

        old_int = self._get_config_int("CAMERA_CONFIG", "temperature_default", 20)
        new_int = self.temperature_spinbox.value()
        if old_int != new_int:
            changes.append(("Temperature Default", f"{old_int} C", f"{new_int} C", True))

        old_val = self._get_config_value("CAMERA_CONFIG", "roi_width_default", "None")
        new_val = "None" if self.roi_width_spinbox.value() == 0 else str(self.roi_width_spinbox.value())
        if old_val != new_val:
            changes.append(("ROI Width", old_val, new_val, True))

        old_val = self._get_config_value("CAMERA_CONFIG", "roi_height_default", "None")
        new_val = "None" if self.roi_height_spinbox.value() == 0 else str(self.roi_height_spinbox.value())
        if old_val != new_val:
            changes.append(("ROI Height", old_val, new_val, True))

        # Advanced - Stage & Motion (require restart)
        old_float = self._get_config_float("GENERAL", "max_velocity_x_mm", 30)
        new_float = self.max_vel_x.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Velocity X", f"{old_float} mm/s", f"{new_float} mm/s", True))

        old_float = self._get_config_float("GENERAL", "max_velocity_y_mm", 30)
        new_float = self.max_vel_y.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Velocity Y", f"{old_float} mm/s", f"{new_float} mm/s", True))

        old_float = self._get_config_float("GENERAL", "max_velocity_z_mm", 3.8)
        new_float = self.max_vel_z.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Velocity Z", f"{old_float} mm/s", f"{new_float} mm/s", True))

        old_float = self._get_config_float("GENERAL", "max_acceleration_x_mm", 500)
        new_float = self.max_accel_x.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Acceleration X", f"{old_float} mm/s2", f"{new_float} mm/s2", True))

        old_float = self._get_config_float("GENERAL", "max_acceleration_y_mm", 500)
        new_float = self.max_accel_y.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Acceleration Y", f"{old_float} mm/s2", f"{new_float} mm/s2", True))

        old_float = self._get_config_float("GENERAL", "max_acceleration_z_mm", 100)
        new_float = self.max_accel_z.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Max Acceleration Z", f"{old_float} mm/s2", f"{new_float} mm/s2", True))

        old_int = self._get_config_int("GENERAL", "scan_stabilization_time_ms_x", 25)
        new_int = self.scan_stab_x.value()
        if old_int != new_int:
            changes.append(("Scan Stabilization X", f"{old_int} ms", f"{new_int} ms", True))

        old_int = self._get_config_int("GENERAL", "scan_stabilization_time_ms_y", 25)
        new_int = self.scan_stab_y.value()
        if old_int != new_int:
            changes.append(("Scan Stabilization Y", f"{old_int} ms", f"{new_int} ms", True))

        old_int = self._get_config_int("GENERAL", "scan_stabilization_time_ms_z", 20)
        new_int = self.scan_stab_z.value()
        if old_int != new_int:
            changes.append(("Scan Stabilization Z", f"{old_int} ms", f"{new_int} ms", True))

        # Advanced - Autofocus (live update)
        old_float = self._get_config_float("AF", "stop_threshold", 0.85)
        new_float = self.af_stop_threshold.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("AF Stop Threshold", str(old_float), str(new_float), False))

        old_int = self._get_config_int("AF", "crop_width", 800)
        new_int = self.af_crop_width.value()
        if old_int != new_int:
            changes.append(("AF Crop Width", f"{old_int} px", f"{new_int} px", False))

        old_int = self._get_config_int("AF", "crop_height", 800)
        new_int = self.af_crop_height.value()
        if old_int != new_int:
            changes.append(("AF Crop Height", f"{old_int} px", f"{new_int} px", False))

        # Advanced - Hardware (mixed)
        old_val = self._get_config_value("GENERAL", "z_motor_config", "STEPPER")
        new_val = self.z_motor_combo.currentText()
        if old_val != new_val:
            changes.append(("Z Motor Config", old_val, new_val, True))

        old_bool = self._get_config_bool("GENERAL", "enable_spinning_disk_confocal", False)
        new_bool = self.spinning_disk_checkbox.isChecked()
        if old_bool != new_bool:
            changes.append(("Enable Spinning Disk", str(old_bool), str(new_bool), True))

        old_float = self._get_config_float("GENERAL", "led_matrix_r_factor", 1.0)
        new_float = self.led_r_factor.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("LED Matrix R Factor", str(old_float), str(new_float), False))

        old_float = self._get_config_float("GENERAL", "led_matrix_g_factor", 1.0)
        new_float = self.led_g_factor.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("LED Matrix G Factor", str(old_float), str(new_float), False))

        old_float = self._get_config_float("GENERAL", "led_matrix_b_factor", 1.0)
        new_float = self.led_b_factor.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("LED Matrix B Factor", str(old_float), str(new_float), False))

        old_float = self._get_config_float("GENERAL", "illumination_intensity_factor", 0.6)
        new_float = self.illumination_factor.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Illumination Intensity Factor", str(old_float), str(new_float), False))

        # Advanced - Position Limits (live update)
        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "x_positive", 115)
        new_float = self.limit_x_pos.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("X Positive Limit", f"{old_float} mm", f"{new_float} mm", False))

        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "x_negative", 5)
        new_float = self.limit_x_neg.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("X Negative Limit", f"{old_float} mm", f"{new_float} mm", False))

        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "y_positive", 76)
        new_float = self.limit_y_pos.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Y Positive Limit", f"{old_float} mm", f"{new_float} mm", False))

        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "y_negative", 4)
        new_float = self.limit_y_neg.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Y Negative Limit", f"{old_float} mm", f"{new_float} mm", False))

        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "z_positive", 6)
        new_float = self.limit_z_pos.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Z Positive Limit", f"{old_float} mm", f"{new_float} mm", False))

        old_float = self._get_config_float("SOFTWARE_POS_LIMIT", "z_negative", 0.05)
        new_float = self.limit_z_neg.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Z Negative Limit", f"{old_float} mm", f"{new_float} mm", False))

        # Advanced - Tracking (live update)
        old_bool = self._get_config_bool("GENERAL", "enable_tracking", False)
        new_bool = self.enable_tracking_checkbox.isChecked()
        if old_bool != new_bool:
            changes.append(("Enable Tracking", str(old_bool), str(new_bool), False))

        old_val = self._get_config_value("TRACKING", "default_tracker", "csrt")
        new_val = self.default_tracker_combo.currentText()
        if old_val != new_val:
            changes.append(("Default Tracker", old_val, new_val, False))

        old_int = self._get_config_int("TRACKING", "search_area_ratio", 10)
        new_int = self.search_area_ratio.value()
        if old_int != new_int:
            changes.append(("Search Area Ratio", str(old_int), str(new_int), False))

        return changes

    def _save_and_close(self) -> None:
        changes = self._get_changes()

        if not changes:
            self.accept()
            return

        # Check if any changes require restart
        requires_restart = any(change[3] for change in changes)

        # For single change, save directly without confirmation
        if len(changes) == 1:
            self._apply_settings()
            if requires_restart:
                QMessageBox.information(
                    self, "Settings Saved", "Settings have been saved. This change requires a restart to take effect."
                )
            self.accept()
            return

        # For multiple changes, show confirmation dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Changes")
        dialog.setMinimumWidth(450)
        if self.isModal():
            dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        label = QLabel("The following settings will be changed:")
        layout.addWidget(label)

        # Create text showing before/after for each change
        changes_text = QTextEdit()
        changes_text.setReadOnly(True)
        changes_lines = []
        for name, old_val, new_val, needs_restart in changes:
            restart_note = " [restart required]" if needs_restart else ""
            changes_lines.append(f"{name}{restart_note}:\n  Before: {old_val}\n  After:  {new_val}")
        changes_text.setPlainText("\n\n".join(changes_lines))
        changes_text.setMinimumHeight(200)
        layout.addWidget(changes_text)

        # Only show restart warning if at least one change requires restart
        if requires_restart:
            note_label = QLabel(
                "Note: Settings marked [restart required] will only take effect after restarting the software."
            )
            note_label.setStyleSheet("color: #666; font-style: italic;")
            note_label.setWordWrap(True)
            layout.addWidget(note_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        if dialog.exec_() == QDialog.Accepted:
            self._apply_settings()
            self.accept()
