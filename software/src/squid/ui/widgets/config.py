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
        self._create_views_tab()
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
        try:
            from _def import FileSavingOption
            self.file_saving_combo.addItems([e.name for e in FileSavingOption])
        except ImportError:
            self.file_saving_combo.addItems(["OME_TIFF", "MULTI_PAGE_TIFF", "INDIVIDUAL_IMAGES", "ZARR_V3"])
        current_value = self._get_config_value("GENERAL", "file_saving_option", "OME_TIFF")
        self.file_saving_combo.setCurrentText(current_value)
        layout.addRow("File Saving Format:", self.file_saving_combo)

        # Zarr Compression
        self.zarr_compression_label = QLabel("Zarr Compression:")
        self.zarr_compression_combo = QComboBox()
        self.zarr_compression_combo.addItems(["none", "fast", "balanced", "best"])
        self.zarr_compression_combo.setCurrentText(
            self._get_config_value("GENERAL", "zarr_compression", "fast")
        )
        self.zarr_compression_combo.setToolTip(
            "none: No compression (fastest write, largest files)\n"
            "fast: LZ4/Zstd level 1 (~1000 MB/s encode, ~2:1 ratio)\n"
            "balanced: Zstd level 3 (~500 MB/s encode, ~3:1 ratio)\n"
            "best: Zstd level 9 (~100 MB/s encode, ~4:1 ratio)"
        )
        layout.addRow(self.zarr_compression_label, self.zarr_compression_combo)

        # Zarr Chunk Mode
        self.zarr_chunk_mode_label = QLabel("Zarr Chunk Mode:")
        self.zarr_chunk_mode_combo = QComboBox()
        self.zarr_chunk_mode_combo.addItems(["full_frame", "tiled_512", "tiled_256"])
        self.zarr_chunk_mode_combo.setCurrentText(
            self._get_config_value("GENERAL", "zarr_chunk_mode", "full_frame")
        )
        self.zarr_chunk_mode_combo.setToolTip(
            "full_frame: Each chunk is one full image frame (best for sequential access)\n"
            "tiled_512: 512x512 tiles (best for random spatial access)\n"
            "tiled_256: 256x256 tiles (smaller tiles, more overhead)"
        )
        layout.addRow(self.zarr_chunk_mode_label, self.zarr_chunk_mode_combo)

        # Zarr 6D FOV Dimension
        self.zarr_6d_fov_checkbox = QCheckBox()
        self.zarr_6d_fov_checkbox.setChecked(
            self._get_config_bool("GENERAL", "zarr_use_6d_fov_dimension", False)
        )
        self.zarr_6d_fov_checkbox.setToolTip(
            "When enabled, non-HCS acquisitions store all FOVs in a single 6D zarr array\n"
            "with shape (FOV, T, C, Z, Y, X) per region. This is non-standard but more compact.\n"
            "When disabled, each FOV gets its own 5D OME-NGFF compliant zarr store."
        )
        self.zarr_6d_fov_label = QLabel("Zarr 6D FOV Dimension:")
        layout.addRow(self.zarr_6d_fov_label, self.zarr_6d_fov_checkbox)

        # Connect file saving combo to update zarr options visibility
        self.file_saving_combo.currentTextChanged.connect(self._update_zarr_options_visibility)
        self._update_zarr_options_visibility()

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
        self.temperature_spinbox.setSuffix(" °C")
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

    def _create_views_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # Plate View section
        plate_group = CollapsibleGroupBox("Plate View")
        plate_layout = QFormLayout()

        # Save Downsampled Well Images
        self.generate_downsampled_checkbox = QCheckBox()
        self.generate_downsampled_checkbox.setChecked(
            self._get_config_bool("VIEWS", "save_downsampled_well_images", False)
        )
        plate_layout.addRow("Save Downsampled Well Images:", self.generate_downsampled_checkbox)

        # Display Plate View
        self.display_plate_view_checkbox = QCheckBox()
        self.display_plate_view_checkbox.setChecked(self._get_config_bool("VIEWS", "display_plate_view", False))
        plate_layout.addRow("Display Plate View:", self.display_plate_view_checkbox)

        # Well Resolutions (comma-separated)
        self.well_resolutions_edit = QLineEdit()
        default_resolutions = self._get_config_value("VIEWS", "downsampled_well_resolutions_um", "5.0, 10.0, 20.0")
        self.well_resolutions_edit.setText(default_resolutions)
        self.well_resolutions_edit.setToolTip(
            "Comma-separated list of resolution values in micrometers (e.g., 5.0, 10.0, 20.0)"
        )
        # Validator for comma-separated positive numbers
        from qtpy.QtCore import QRegularExpression
        from qtpy.QtGui import QRegularExpressionValidator

        well_res_pattern = QRegularExpression(r"^\s*\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*\s*$")
        self.well_resolutions_edit.setValidator(QRegularExpressionValidator(well_res_pattern))
        plate_layout.addRow("Well Resolutions (μm):", self.well_resolutions_edit)

        # Target Pixel Size (formerly Plate Resolution)
        self.plate_resolution_spinbox = QDoubleSpinBox()
        self.plate_resolution_spinbox.setRange(1.0, 100.0)
        self.plate_resolution_spinbox.setSingleStep(1.0)
        self.plate_resolution_spinbox.setValue(self._get_config_float("VIEWS", "downsampled_plate_resolution_um", 10.0))
        self.plate_resolution_spinbox.setSuffix(" μm")
        self.plate_resolution_spinbox.setToolTip("Target pixel size for plate view overview (um/pixel)")
        plate_layout.addRow("Target Pixel Size:", self.plate_resolution_spinbox)

        # Z-Projection Mode
        self.z_projection_combo = QComboBox()
        self.z_projection_combo.addItems(["mip", "middle"])
        current_projection = self._get_config_value("VIEWS", "downsampled_z_projection", "mip")
        self.z_projection_combo.setCurrentText(current_projection)
        self.z_projection_combo.setToolTip("MIP: Max intensity projection across z-stack. Middle: Use middle z-slice only.")
        plate_layout.addRow("Z-Projection Mode:", self.z_projection_combo)

        # Interpolation Method
        self.interpolation_method_combo = QComboBox()
        self.interpolation_method_combo.addItems(["inter_area_fast", "inter_linear", "inter_area"])
        current_method = self._get_config_value("VIEWS", "downsampled_interpolation_method", "inter_area_fast")
        self.interpolation_method_combo.setCurrentText(current_method)
        self.interpolation_method_combo.setToolTip(
            "inter_area_fast: Balanced speed/quality (~1ms). "
            "inter_linear: Fast (~0.05ms). "
            "inter_area: Highest quality (~18ms)."
        )
        plate_layout.addRow("Interpolation Method:", self.interpolation_method_combo)

        plate_group.content.addLayout(plate_layout)
        layout.addWidget(plate_group)

        # Mosaic View section
        mosaic_group = CollapsibleGroupBox("Mosaic View")
        mosaic_layout = QFormLayout()

        # Display Mosaic View
        self.display_mosaic_view_checkbox = QCheckBox()
        self.display_mosaic_view_checkbox.setChecked(self._get_config_bool("VIEWS", "display_mosaic_view", True))
        mosaic_layout.addRow("Display Mosaic View:", self.display_mosaic_view_checkbox)

        # Mosaic Target Pixel Size
        self.mosaic_pixel_size_spinbox = QDoubleSpinBox()
        self.mosaic_pixel_size_spinbox.setRange(0.5, 20.0)
        self.mosaic_pixel_size_spinbox.setSingleStep(0.5)
        self.mosaic_pixel_size_spinbox.setValue(
            self._get_config_float("VIEWS", "mosaic_view_target_pixel_size_um", 2.0)
        )
        self.mosaic_pixel_size_spinbox.setSuffix(" μm")
        mosaic_layout.addRow("Target Pixel Size:", self.mosaic_pixel_size_spinbox)

        mosaic_group.content.addLayout(mosaic_layout)
        layout.addWidget(mosaic_group)

        # NDViewer section
        ndviewer_group = CollapsibleGroupBox("NDViewer")
        ndviewer_layout = QFormLayout()

        # Enable NDViewer
        self.enable_ndviewer_checkbox = QCheckBox()
        self.enable_ndviewer_checkbox.setChecked(self._get_config_bool("VIEWS", "enable_ndviewer", False))
        self.enable_ndviewer_checkbox.setToolTip("Enable the NDViewer tab for viewing acquired datasets")
        ndviewer_layout.addRow("Enable NDViewer *:", self.enable_ndviewer_checkbox)

        ndviewer_group.content.addLayout(ndviewer_layout)
        layout.addWidget(ndviewer_group)

        layout.addStretch()
        self.tab_widget.addTab(tab, "Views")

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

        # Acquisition Throttling section
        throttle_group = CollapsibleGroupBox("Acquisition Throttling")
        throttle_layout = QFormLayout()

        self.throttling_enabled_checkbox = QCheckBox()
        self.throttling_enabled_checkbox.setChecked(
            self._get_config_bool("GENERAL", "acquisition_throttling_enabled", True)
        )
        self.throttling_enabled_checkbox.setToolTip(
            "When enabled, acquisition pauses when pending jobs or RAM usage exceeds limits.\n"
            "Prevents RAM exhaustion when acquisition speed exceeds disk write speed."
        )
        throttle_layout.addRow("Enable Throttling:", self.throttling_enabled_checkbox)

        self.max_pending_jobs_spinbox = QSpinBox()
        self.max_pending_jobs_spinbox.setRange(1, 100)
        self.max_pending_jobs_spinbox.setValue(
            self._get_config_int("GENERAL", "acquisition_max_pending_jobs", 10)
        )
        self.max_pending_jobs_spinbox.setToolTip(
            "Maximum number of jobs in flight before throttling.\n"
            "Higher values allow more parallelism but use more RAM."
        )
        throttle_layout.addRow("Max Pending Jobs:", self.max_pending_jobs_spinbox)

        self.max_pending_mb_spinbox = QDoubleSpinBox()
        self.max_pending_mb_spinbox.setRange(100.0, 10000.0)
        self.max_pending_mb_spinbox.setSingleStep(100.0)
        self.max_pending_mb_spinbox.setValue(
            self._get_config_float("GENERAL", "acquisition_max_pending_mb", 500.0)
        )
        self.max_pending_mb_spinbox.setSuffix(" MB")
        self.max_pending_mb_spinbox.setToolTip(
            "Maximum RAM usage (MB) for pending jobs before throttling.\n"
            "Higher values allow faster acquisition but risk RAM exhaustion."
        )
        throttle_layout.addRow("Max Pending RAM:", self.max_pending_mb_spinbox)

        self.throttle_timeout_spinbox = QDoubleSpinBox()
        self.throttle_timeout_spinbox.setRange(5.0, 300.0)
        self.throttle_timeout_spinbox.setSingleStep(5.0)
        self.throttle_timeout_spinbox.setValue(
            self._get_config_float("GENERAL", "acquisition_throttle_timeout_s", 30.0)
        )
        self.throttle_timeout_spinbox.setSuffix(" s")
        self.throttle_timeout_spinbox.setToolTip(
            "Maximum time to wait when throttled before reporting a warning.\n"
            "If disk I/O cannot keep up within this time, acquisition logs a warning."
        )
        throttle_layout.addRow("Throttle Timeout:", self.throttle_timeout_spinbox)

        throttle_group.content.addLayout(throttle_layout)
        layout.addWidget(throttle_group)

        # Use Simulated Hardware section
        hw_sim_group = CollapsibleGroupBox("Use Simulated Hardware *")
        hw_sim_layout = QFormLayout()

        sim_tooltip = (
            "Simulate this component (even without --simulation flag).\n"
            "With --simulation flag, ALL components are always simulated."
        )

        self.sim_camera_checkbox = QCheckBox()
        self.sim_camera_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_camera", False)
        )
        self.sim_camera_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Camera:", self.sim_camera_checkbox)

        self.sim_mcu_checkbox = QCheckBox()
        self.sim_mcu_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_microcontroller", False)
        )
        self.sim_mcu_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate MCU/Stage:", self.sim_mcu_checkbox)

        self.sim_spinning_disk_checkbox = QCheckBox()
        self.sim_spinning_disk_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_spinning_disk", False)
        )
        self.sim_spinning_disk_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Spinning Disk:", self.sim_spinning_disk_checkbox)

        self.sim_filter_wheel_checkbox = QCheckBox()
        self.sim_filter_wheel_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_filter_wheel", False)
        )
        self.sim_filter_wheel_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Filter Wheel:", self.sim_filter_wheel_checkbox)

        self.sim_objective_changer_checkbox = QCheckBox()
        self.sim_objective_changer_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_objective_changer", False)
        )
        self.sim_objective_changer_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Objective Changer:", self.sim_objective_changer_checkbox)

        self.sim_laser_af_camera_checkbox = QCheckBox()
        self.sim_laser_af_camera_checkbox.setChecked(
            self._get_config_bool("SIMULATION", "simulate_laser_af_camera", False)
        )
        self.sim_laser_af_camera_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Laser AF Camera:", self.sim_laser_af_camera_checkbox)

        hw_sim_group.content.addLayout(hw_sim_layout)
        layout.addWidget(hw_sim_group)

        # Development Settings section
        dev_group = CollapsibleGroupBox("Development Settings")
        dev_layout = QFormLayout()

        # Warning label for simulated disk I/O
        sim_warning_label = QLabel("Simulated disk I/O encodes images but does NOT save to disk!")
        sim_warning_label.setStyleSheet("color: #cc0000; font-weight: bold;")
        dev_layout.addRow(sim_warning_label)

        self.simulated_disk_io_checkbox = QCheckBox()
        self.simulated_disk_io_checkbox.setChecked(
            self._get_config_bool("DEVELOPMENT", "simulated_disk_io_enabled", False)
        )
        self.simulated_disk_io_checkbox.setToolTip(
            "When enabled, images are encoded to memory but NOT saved to disk.\n"
            "Use this mode for testing acquisition speed without wearing SSD.\n"
            "WARNING: No data will be saved during acquisitions!"
        )
        dev_layout.addRow("Enable Simulated Disk I/O:", self.simulated_disk_io_checkbox)

        self.simulated_speed_spinbox = QDoubleSpinBox()
        self.simulated_speed_spinbox.setRange(10.0, 3000.0)
        self.simulated_speed_spinbox.setSingleStep(50.0)
        self.simulated_speed_spinbox.setValue(
            self._get_config_float("DEVELOPMENT", "simulated_disk_io_speed_mb_s", 200.0)
        )
        self.simulated_speed_spinbox.setSuffix(" MB/s")
        self.simulated_speed_spinbox.setToolTip(
            "Simulated disk write speed in MB/s.\n"
            "HDD: 50-100 MB/s, SATA SSD: 200-500 MB/s, NVMe: 1000-3000 MB/s"
        )
        dev_layout.addRow("Simulated Write Speed:", self.simulated_speed_spinbox)

        self.simulated_compression_checkbox = QCheckBox()
        self.simulated_compression_checkbox.setChecked(
            self._get_config_bool("DEVELOPMENT", "simulated_disk_io_compression", True)
        )
        self.simulated_compression_checkbox.setToolTip(
            "When enabled, simulate compression to exercise CPU/RAM realistically.\n"
            "Disable for faster simulation with less CPU load."
        )
        dev_layout.addRow("Simulate Compression:", self.simulated_compression_checkbox)

        self.force_save_images_checkbox = QCheckBox()
        self.force_save_images_checkbox.setChecked(
            self._get_config_bool("DEVELOPMENT", "simulation_force_save_images", False)
        )
        self.force_save_images_checkbox.setToolTip(
            "When enabled, save images to disk even with Simulated Disk I/O enabled.\n"
            "Useful for testing file-based viewers (NDViewer) in simulation mode."
        )
        dev_layout.addRow("Force Save Images:", self.force_save_images_checkbox)

        dev_group.content.addLayout(dev_layout)
        layout.addWidget(dev_group)

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

    def _update_zarr_options_visibility(self) -> None:
        """Show/hide zarr-specific options based on file saving format."""
        is_zarr = self.file_saving_combo.currentText() == "ZARR_V3"
        self.zarr_compression_label.setVisible(is_zarr)
        self.zarr_compression_combo.setVisible(is_zarr)
        self.zarr_chunk_mode_label.setVisible(is_zarr)
        self.zarr_chunk_mode_combo.setVisible(is_zarr)
        self.zarr_6d_fov_label.setVisible(is_zarr)
        self.zarr_6d_fov_checkbox.setVisible(is_zarr)

    def _apply_settings(self) -> None:
        # Ensure all required sections exist
        for section in ["GENERAL", "CAMERA_CONFIG", "AF", "SOFTWARE_POS_LIMIT", "TRACKING", "VIEWS", "DEVELOPMENT"]:
            self._ensure_section(section)

        # General settings
        self.config.set("GENERAL", "file_saving_option", self.file_saving_combo.currentText())
        self.config.set("GENERAL", "default_saving_path", self.saving_path_edit.text())

        # Zarr settings
        self.config.set("GENERAL", "zarr_compression", self.zarr_compression_combo.currentText())
        self.config.set("GENERAL", "zarr_chunk_mode", self.zarr_chunk_mode_combo.currentText())
        self.config.set(
            "GENERAL",
            "zarr_use_6d_fov_dimension",
            "true" if self.zarr_6d_fov_checkbox.isChecked() else "false",
        )

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

        # Advanced - Acquisition Throttling
        self.config.set(
            "GENERAL",
            "acquisition_throttling_enabled",
            "true" if self.throttling_enabled_checkbox.isChecked() else "false",
        )
        self.config.set("GENERAL", "acquisition_max_pending_jobs", str(self.max_pending_jobs_spinbox.value()))
        self.config.set("GENERAL", "acquisition_max_pending_mb", str(self.max_pending_mb_spinbox.value()))
        self.config.set("GENERAL", "acquisition_throttle_timeout_s", str(self.throttle_timeout_spinbox.value()))

        # Views settings
        self.config.set(
            "VIEWS",
            "save_downsampled_well_images",
            "true" if self.generate_downsampled_checkbox.isChecked() else "false",
        )
        self.config.set(
            "VIEWS",
            "display_plate_view",
            "true" if self.display_plate_view_checkbox.isChecked() else "false",
        )
        self.config.set("VIEWS", "downsampled_well_resolutions_um", self.well_resolutions_edit.text())
        self.config.set("VIEWS", "downsampled_plate_resolution_um", str(self.plate_resolution_spinbox.value()))
        self.config.set("VIEWS", "downsampled_z_projection", self.z_projection_combo.currentText())
        self.config.set("VIEWS", "downsampled_interpolation_method", self.interpolation_method_combo.currentText())
        self.config.set(
            "VIEWS",
            "display_mosaic_view",
            "true" if self.display_mosaic_view_checkbox.isChecked() else "false",
        )
        self.config.set("VIEWS", "mosaic_view_target_pixel_size_um", str(self.mosaic_pixel_size_spinbox.value()))
        self.config.set(
            "VIEWS",
            "enable_ndviewer",
            "true" if self.enable_ndviewer_checkbox.isChecked() else "false",
        )

        # Development settings
        self.config.set(
            "DEVELOPMENT",
            "simulated_disk_io_enabled",
            "true" if self.simulated_disk_io_checkbox.isChecked() else "false",
        )
        self.config.set("DEVELOPMENT", "simulated_disk_io_speed_mb_s", str(self.simulated_speed_spinbox.value()))
        self.config.set(
            "DEVELOPMENT",
            "simulated_disk_io_compression",
            "true" if self.simulated_compression_checkbox.isChecked() else "false",
        )
        self.config.set(
            "DEVELOPMENT",
            "simulation_force_save_images",
            "true" if self.force_save_images_checkbox.isChecked() else "false",
        )

        # Hardware Simulation settings (in [SIMULATION] section)
        self._ensure_section("SIMULATION")
        self.config.set("SIMULATION", "simulate_camera", str(self.sim_camera_checkbox.isChecked()).lower())
        self.config.set("SIMULATION", "simulate_microcontroller", str(self.sim_mcu_checkbox.isChecked()).lower())
        self.config.set(
            "SIMULATION", "simulate_spinning_disk", str(self.sim_spinning_disk_checkbox.isChecked()).lower()
        )
        self.config.set("SIMULATION", "simulate_filter_wheel", str(self.sim_filter_wheel_checkbox.isChecked()).lower())
        self.config.set(
            "SIMULATION", "simulate_objective_changer", str(self.sim_objective_changer_checkbox.isChecked()).lower()
        )
        self.config.set(
            "SIMULATION", "simulate_laser_af_camera", str(self.sim_laser_af_camera_checkbox.isChecked()).lower()
        )

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

        # Zarr settings (takes effect on next acquisition)
        if hasattr(_def, "ZarrCompression") and hasattr(_def.ZarrCompression, "convert_to_enum"):
            _def.ZARR_COMPRESSION = _def.ZarrCompression.convert_to_enum(self.zarr_compression_combo.currentText())
        if hasattr(_def, "ZarrChunkMode") and hasattr(_def.ZarrChunkMode, "convert_to_enum"):
            _def.ZARR_CHUNK_MODE = _def.ZarrChunkMode.convert_to_enum(self.zarr_chunk_mode_combo.currentText())
        if hasattr(_def, "ZARR_USE_6D_FOV_DIMENSION"):
            _def.ZARR_USE_6D_FOV_DIMENSION = self.zarr_6d_fov_checkbox.isChecked()

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

        # Acquisition throttling settings (takes effect on next acquisition)
        if hasattr(_def, "ACQUISITION_THROTTLING_ENABLED"):
            _def.ACQUISITION_THROTTLING_ENABLED = self.throttling_enabled_checkbox.isChecked()
        if hasattr(_def, "ACQUISITION_MAX_PENDING_JOBS"):
            _def.ACQUISITION_MAX_PENDING_JOBS = self.max_pending_jobs_spinbox.value()
        if hasattr(_def, "ACQUISITION_MAX_PENDING_MB"):
            _def.ACQUISITION_MAX_PENDING_MB = self.max_pending_mb_spinbox.value()
        if hasattr(_def, "ACQUISITION_THROTTLE_TIMEOUT_S"):
            _def.ACQUISITION_THROTTLE_TIMEOUT_S = self.throttle_timeout_spinbox.value()

        # Views settings
        if hasattr(_def, "SAVE_DOWNSAMPLED_WELL_IMAGES"):
            _def.SAVE_DOWNSAMPLED_WELL_IMAGES = self.generate_downsampled_checkbox.isChecked()
        if hasattr(_def, "DISPLAY_PLATE_VIEW"):
            _def.DISPLAY_PLATE_VIEW = self.display_plate_view_checkbox.isChecked()
        # Parse comma-separated resolutions
        if hasattr(_def, "DOWNSAMPLED_WELL_RESOLUTIONS_UM"):
            resolutions_str = self.well_resolutions_edit.text()
            try:
                _def.DOWNSAMPLED_WELL_RESOLUTIONS_UM = [float(x.strip()) for x in resolutions_str.split(",") if x.strip()]
            except ValueError:
                self._log.warning(f"Invalid well resolutions format: {resolutions_str}")
        if hasattr(_def, "DOWNSAMPLED_PLATE_RESOLUTION_UM"):
            _def.DOWNSAMPLED_PLATE_RESOLUTION_UM = self.plate_resolution_spinbox.value()
        if hasattr(_def, "DOWNSAMPLED_Z_PROJECTION") and hasattr(_def, "ZProjectionMode"):
            _def.DOWNSAMPLED_Z_PROJECTION = _def.ZProjectionMode.convert_to_enum(self.z_projection_combo.currentText())
        if hasattr(_def, "DOWNSAMPLED_INTERPOLATION_METHOD") and hasattr(_def, "DownsamplingMethod"):
            _def.DOWNSAMPLED_INTERPOLATION_METHOD = _def.DownsamplingMethod.convert_to_enum(
                self.interpolation_method_combo.currentText()
            )
        if hasattr(_def, "USE_NAPARI_FOR_MOSAIC_DISPLAY"):
            _def.USE_NAPARI_FOR_MOSAIC_DISPLAY = self.display_mosaic_view_checkbox.isChecked()
        if hasattr(_def, "MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM"):
            _def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM = self.mosaic_pixel_size_spinbox.value()

        # Development settings (live update)
        if hasattr(_def, "SIMULATED_DISK_IO_ENABLED"):
            _def.SIMULATED_DISK_IO_ENABLED = self.simulated_disk_io_checkbox.isChecked()
        if hasattr(_def, "SIMULATED_DISK_IO_SPEED_MB_S"):
            _def.SIMULATED_DISK_IO_SPEED_MB_S = self.simulated_speed_spinbox.value()
        if hasattr(_def, "SIMULATED_DISK_IO_COMPRESSION"):
            _def.SIMULATED_DISK_IO_COMPRESSION = self.simulated_compression_checkbox.isChecked()
        if hasattr(_def, "SIMULATION_FORCE_SAVE_IMAGES"):
            _def.SIMULATION_FORCE_SAVE_IMAGES = self.force_save_images_checkbox.isChecked()

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

        # Zarr settings (live update)
        old_val = self._get_config_value("GENERAL", "zarr_compression", "fast")
        new_val = self.zarr_compression_combo.currentText()
        if old_val != new_val:
            changes.append(("Zarr Compression", old_val, new_val, False))

        old_val = self._get_config_value("GENERAL", "zarr_chunk_mode", "full_frame")
        new_val = self.zarr_chunk_mode_combo.currentText()
        if old_val != new_val:
            changes.append(("Zarr Chunk Mode", old_val, new_val, False))

        old_val_bool = self._get_config_bool("GENERAL", "zarr_use_6d_fov_dimension", False)
        new_val_bool = self.zarr_6d_fov_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Zarr 6D FOV Dimension", str(old_val_bool), str(new_val_bool), False))

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

        # Views settings (live update)
        old_val_bool = self._get_config_bool("VIEWS", "save_downsampled_well_images", False)
        new_val_bool = self.generate_downsampled_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Save Downsampled Well Images", str(old_val_bool), str(new_val_bool), False))

        old_val_bool = self._get_config_bool("VIEWS", "display_plate_view", False)
        new_val_bool = self.display_plate_view_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Display Plate View *", str(old_val_bool), str(new_val_bool), True))

        old_val = self._get_config_value("VIEWS", "downsampled_well_resolutions_um", "5.0, 10.0, 20.0")
        new_val = self.well_resolutions_edit.text()
        if old_val != new_val:
            changes.append(("Well Resolutions", old_val, new_val, False))

        old_float = self._get_config_float("VIEWS", "downsampled_plate_resolution_um", 10.0)
        new_float = self.plate_resolution_spinbox.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Target Pixel Size", f"{old_float} μm", f"{new_float} μm", False))

        old_val = self._get_config_value("VIEWS", "downsampled_z_projection", "mip")
        new_val = self.z_projection_combo.currentText()
        if old_val != new_val:
            changes.append(("Z-Projection Mode", old_val, new_val, False))

        old_val = self._get_config_value("VIEWS", "downsampled_interpolation_method", "inter_area_fast")
        new_val = self.interpolation_method_combo.currentText()
        if old_val != new_val:
            changes.append(("Interpolation Method", old_val, new_val, False))

        old_val_bool = self._get_config_bool("VIEWS", "display_mosaic_view", True)
        new_val_bool = self.display_mosaic_view_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Display Mosaic View *", str(old_val_bool), str(new_val_bool), True))

        old_float = self._get_config_float("VIEWS", "mosaic_view_target_pixel_size_um", 2.0)
        new_float = self.mosaic_pixel_size_spinbox.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Mosaic Target Pixel Size", f"{old_float} μm", f"{new_float} μm", False))

        old_val_bool = self._get_config_bool("VIEWS", "enable_ndviewer", False)
        new_val_bool = self.enable_ndviewer_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Enable NDViewer *", str(old_val_bool), str(new_val_bool), True))

        # Development settings (live update)
        old_val_bool = self._get_config_bool("DEVELOPMENT", "simulated_disk_io_enabled", False)
        new_val_bool = self.simulated_disk_io_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulated Disk I/O", str(old_val_bool), str(new_val_bool), False))

        old_float = self._get_config_float("DEVELOPMENT", "simulated_disk_io_speed_mb_s", 200.0)
        new_float = self.simulated_speed_spinbox.value()
        if not self._floats_equal(old_float, new_float):
            changes.append(("Simulated Write Speed", f"{old_float} MB/s", f"{new_float} MB/s", False))

        old_val_bool = self._get_config_bool("DEVELOPMENT", "simulated_disk_io_compression", True)
        new_val_bool = self.simulated_compression_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Compression", str(old_val_bool), str(new_val_bool), False))

        old_val_bool = self._get_config_bool("DEVELOPMENT", "simulation_force_save_images", False)
        new_val_bool = self.force_save_images_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Force Save Images", str(old_val_bool), str(new_val_bool), False))

        # Hardware Simulation settings (require restart)
        old_val_bool = self._get_config_bool("SIMULATION", "simulate_camera", False)
        new_val_bool = self.sim_camera_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Camera *", str(old_val_bool), str(new_val_bool), True))

        old_val_bool = self._get_config_bool("SIMULATION", "simulate_microcontroller", False)
        new_val_bool = self.sim_microcontroller_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Microcontroller *", str(old_val_bool), str(new_val_bool), True))

        old_val_bool = self._get_config_bool("SIMULATION", "simulate_spinning_disk", False)
        new_val_bool = self.sim_spinning_disk_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Spinning Disk *", str(old_val_bool), str(new_val_bool), True))

        old_val_bool = self._get_config_bool("SIMULATION", "simulate_filter_wheel", False)
        new_val_bool = self.sim_filter_wheel_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Filter Wheel *", str(old_val_bool), str(new_val_bool), True))

        old_val_bool = self._get_config_bool("SIMULATION", "simulate_objective_changer", False)
        new_val_bool = self.sim_objective_changer_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Objective Changer *", str(old_val_bool), str(new_val_bool), True))

        old_val_bool = self._get_config_bool("SIMULATION", "simulate_laser_af_camera", False)
        new_val_bool = self.sim_laser_af_camera_checkbox.isChecked()
        if old_val_bool != new_val_bool:
            changes.append(("Simulate Laser AF Camera *", str(old_val_bool), str(new_val_bool), True))

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
                self._prompt_restart()
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
            if requires_restart:
                self._prompt_restart()
            self.accept()

    def _prompt_restart(self) -> None:
        """Show restart dialog when settings require restart."""
        reply = QMessageBox.question(
            self,
            "Restart Required",
            "Some settings changed require a restart to take effect.\n\n"
            "Would you like to restart now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._restart_application()

    def _restart_application(self) -> None:
        """Restart the application with --skip-init flag."""
        import sys
        import os

        # Build restart command with --skip-init
        args = sys.argv.copy()
        if "--skip-init" not in args:
            args.append("--skip-init")

        # Close the preferences dialog first
        self.accept()

        # Schedule application restart after event loop processes
        from qtpy.QtCore import QTimer
        from qtpy.QtWidgets import QApplication

        def do_restart():
            # Quit application and spawn new process
            QApplication.instance().quit()
            os.execv(sys.executable, [sys.executable] + args)

        QTimer.singleShot(100, do_restart)
