import os
import logging
import sys
from typing import Optional

import squid.logging
from control.core.core import TrackingController, MultiPointController, LiveController
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
import control.utils as utils
from squid.abc import AbstractStage, AbstractCamera
from squid.config import CameraPixelFormat

# set QT_API environment variable
os.environ["QT_API"] = "pyqt5"

# qt libraries
import qtpy
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import pyqtgraph as pg
import pandas as pd
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
import re
import cv2
import math
import locale
import time
from datetime import datetime
import itertools
import numpy as np
from scipy.spatial import Delaunay
import shutil
from control._def import *
from PIL import Image, ImageDraw, ImageFont


def error_dialog(message: str, title: str = "Error"):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setText(message)
    msg.setWindowTitle(title)
    msg.setStandardButtons(QMessageBox.Ok)
    msg.setDefaultButton(QMessageBox.Ok)
    retval = msg.exec_()
    return


def check_space_available_with_error_dialog(
    multi_point_controller: MultiPointController, logger: logging.Logger, factor_of_safecty: float = 1.03
) -> bool:
    # To check how much disk space is required, we need to have the MultiPointController all configured.  That is
    # a precondition of this function.
    save_directory = multi_point_controller.base_path
    available_disk_space = utils.get_available_disk_space(save_directory)
    space_required = factor_of_safecty * multi_point_controller.get_estimated_acquisition_disk_storage()
    image_count = multi_point_controller.get_acquisition_image_count()

    logger.info(
        f"Checking space available: {space_required=}, {available_disk_space=}, {image_count=}, {save_directory=}"
    )
    if space_required > available_disk_space:
        megabytes_required = int(space_required / 1024 / 1024)
        megabytes_available = int(available_disk_space / 1024 / 1024)
        error_message = (
            f"This acquisition will capture {image_count:,} images, which will"
            f" require {megabytes_required:,} [MB], but '{save_directory}' only has {megabytes_available:,} [MB] available."
        )
        logger.error(error_message)
        error_dialog(error_message, title="Not Enough Disk Space")
        return False
    return True


class WrapperWindow(QMainWindow):
    def __init__(self, content_widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCentralWidget(content_widget)
        self.hide()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def closeForReal(self, event):
        super().closeEvent(event)


class CollapsibleGroupBox(QGroupBox):
    def __init__(self, title):
        super(CollapsibleGroupBox, self).__init__(title)
        self.setCheckable(True)
        self.setChecked(True)
        self.higher_layout = QVBoxLayout()
        self.content = QVBoxLayout()
        # self.content.setAlignment(Qt.AlignTop)
        self.content_widget = QWidget()
        self.content_widget.setLayout(self.content)
        self.higher_layout.addWidget(self.content_widget)
        self.setLayout(self.higher_layout)
        self.toggled.connect(self.toggle_content)

    def toggle_content(self, state):
        self.content_widget.setVisible(state)


"""
# Planning to replace this with a better design
class ConfigEditorForAcquisitions(QDialog):
    def __init__(self, configManager, only_z_offset=True):
        super().__init__()

        self.config = configManager

        self.only_z_offset = only_z_offset

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
        self.load_config_button.clicked.connect(lambda: self.load_config_from_file(None))

        layout = QVBoxLayout()
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.save_config_button)
        layout.addWidget(self.save_to_file_button)
        layout.addWidget(self.load_config_button)

        self.config_value_widgets = {}

        self.setLayout(layout)
        self.setWindowTitle("Configuration Editor")
        self.init_ui(only_z_offset)

    def init_ui(self, only_z_offset=None):
        if only_z_offset is None:
            only_z_offset = self.only_z_offset
        self.groups = {}
        for section in self.config.configurations:
            if not only_z_offset:
                group_box = CollapsibleGroupBox(section.name)
            else:
                group_box = QGroupBox(section.name)

            group_layout = QVBoxLayout()

            section_value_widgets = {}

            self.groups[str(section.id)] = group_box

            for option in section.__dict__.keys():
                if option.startswith("_") and option.endswith("_options"):
                    continue
                if option == "id":
                    continue
                if only_z_offset and option != "z_offset":
                    continue
                option_value = str(getattr(section, option))
                option_name = QLabel(option)
                option_layout = QHBoxLayout()
                option_layout.addWidget(option_name)
                if f"_{option}_options" in list(section.__dict__.keys()):
                    option_value_list = getattr(section, f"_{option}_options")
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

            self.config_value_widgets[str(section.id)] = section_value_widgets
            if not only_z_offset:
                group_box.content.addLayout(group_layout)
            else:
                group_box.setLayout(group_layout)

            self.scroll_area_layout.addWidget(group_box)

    def save_config(self):
        for section in self.config.configurations:
            for option in section.__dict__.keys():
                if option.startswith("_") and option.endswith("_options"):
                    continue
                old_val = getattr(section, option)
                if option == "id":
                    continue
                elif option == "camera_sn":
                    option_name_in_xml = "CameraSN"
                else:
                    option_name_in_xml = option.replace("_", " ").title().replace(" ", "")
                try:
                    widget = self.config_value_widgets[str(section.id)][option]
                except KeyError:
                    continue
                if type(widget) is QLineEdit:
                    self.config.update_configuration(section.id, option_name_in_xml, widget.text())
                else:
                    self.config.update_configuration(section.id, option_name_in_xml, widget.currentText())
        self.config.configurations = []
        self.config.read_configurations()

    def save_to_file(self):
        self.save_config()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Acquisition Config File", "", "XML Files (*.xml);;All Files (*)"
        )
        if file_path:
            if not self.config.write_configuration(file_path):
                QMessageBox.warning(
                    self, "Warning", f"Failed to write config to file '{file_path}'.  Check permissions!"
                )

    def load_config_from_file(self, only_z_offset=None):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Acquisition Config File", "", "XML Files (*.xml);;All Files (*)"
        )
        if file_path:
            self.config.config_filename = file_path
            self.config.configurations = []
            self.config.read_configurations()
            # Clear and re-initialize the UI
            self.scroll_area_widget.deleteLater()
            self.scroll_area_widget = QWidget()
            self.scroll_area_layout = QVBoxLayout()
            self.scroll_area_widget.setLayout(self.scroll_area_layout)
            self.scroll_area.setWidget(self.scroll_area_widget)
            self.init_ui(only_z_offset)
"""


class ConfigEditor(QDialog):
    def __init__(self, config):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
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

    def init_ui(self):
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

    def save_config(self):
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

    def save_to_filename(self, filename: str):
        try:
            with open(filename, "w") as configfile:
                self.config.write(configfile)
                return True
        except IOError:
            self._log.exception(f"Failed to write config file to '{filename}'")
            return False

    def save_to_file(self):
        self.save_config()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Config File", "", "INI Files (*.ini);;All Files (*)")
        if file_path:
            if not self.save_to_filename(file_path):
                QMessageBox.warning(
                    self, "Warning", f"Failed to write config file to '{file_path}'.  Check permissions!"
                )

    def load_config_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Config File", "", "INI Files (*.ini);;All Files (*)")
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
    def __init__(self, config, original_filepath, main_window):
        super().__init__(config)
        self.original_filepath = original_filepath
        self.main_window = main_window

        self.apply_exit_button = QPushButton("Apply and Exit")
        self.apply_exit_button.clicked.connect(self.apply_and_exit)

        self.layout().addWidget(self.apply_exit_button)

    def apply_and_exit(self):
        self.save_config()
        with open(self.original_filepath, "w") as configfile:
            self.config.write(configfile)
        try:
            self.main_window.close()
        except:
            pass
        self.close()


class LaserAutofocusSettingWidget(QWidget):

    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_apply_settings = Signal()
    signal_laser_spot_location = Signal(np.ndarray, float, float)

    def __init__(self, streamHandler, liveController: LiveController, laserAutofocusController, stretch=True):
        super().__init__()
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.laserAutofocusController = laserAutofocusController
        self.stretch = stretch
        self.liveController.set_trigger_fps(10)
        self.streamHandler.set_display_fps(10)

        # Enable background filling
        self.setAutoFillBackground(True)

        # Create and set background color
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(240, 240, 240))
        self.setPalette(palette)

        self.spinboxes = {}
        self.init_ui()
        self.update_calibration_label()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(9, 9, 9, 9)

        # Live control group
        live_group = QFrame()
        live_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        live_layout = QVBoxLayout()

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setStyleSheet("background-color: #C2C2FF")

        # Exposure time control
        exposure_layout = QHBoxLayout()
        exposure_layout.addWidget(QLabel("Focus Camera Exposure (ms):"))
        self.exposure_spinbox = QDoubleSpinBox()
        self.exposure_spinbox.setSingleStep(0.1)
        self.exposure_spinbox.setRange(*self.liveController.camera.get_exposure_limits())
        self.exposure_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_exposure_time_ms)
        exposure_layout.addWidget(self.exposure_spinbox)

        # Analog gain control
        analog_gain_layout = QHBoxLayout()
        analog_gain_layout.addWidget(QLabel("Focus Camera Analog Gain:"))
        self.analog_gain_spinbox = QDoubleSpinBox()
        self.analog_gain_spinbox.setRange(0, 24)
        self.analog_gain_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_analog_gain)
        analog_gain_layout.addWidget(self.analog_gain_spinbox)

        # Add to live group
        live_layout.addWidget(self.btn_live)
        live_layout.addLayout(exposure_layout)
        live_layout.addLayout(analog_gain_layout)
        live_group.setLayout(live_layout)

        # Non-threshold property group
        non_threshold_group = QFrame()
        non_threshold_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        non_threshold_layout = QVBoxLayout()

        # Add non-threshold property spinboxes
        self._add_spinbox(non_threshold_layout, "Spot Crop Size (pixels):", "spot_crop_size", 1, 500, 0)
        self._add_spinbox(
            non_threshold_layout, "Calibration Distance (μm):", "pixel_to_um_calibration_distance", 0.1, 20.0, 2
        )
        non_threshold_group.setLayout(non_threshold_layout)

        # Settings group
        settings_group = QFrame()
        settings_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        settings_layout = QVBoxLayout()

        # Add threshold property spinboxes
        self._add_spinbox(settings_layout, "Laser AF Averaging N:", "laser_af_averaging_n", 1, 100, 0)
        self._add_spinbox(
            settings_layout, "Displacement Success Window (μm):", "displacement_success_window_um", 0.1, 10.0, 2
        )
        self._add_spinbox(settings_layout, "Correlation Threshold:", "correlation_threshold", 0.1, 1.0, 2, 0.1)
        self._add_spinbox(settings_layout, "Laser AF Range (μm):", "laser_af_range", 1, 1000, 1)
        self.update_threshold_button = QPushButton("Apply without Re-initialization")
        settings_layout.addWidget(self.update_threshold_button)
        settings_group.setLayout(settings_layout)

        # Create spot detection group
        spot_detection_group = QFrame()
        spot_detection_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        spot_detection_layout = QVBoxLayout()

        # Add spot detection related spinboxes
        self._add_spinbox(spot_detection_layout, "Y Window (pixels):", "y_window", 1, 500, 0)
        self._add_spinbox(spot_detection_layout, "X Window (pixels):", "x_window", 1, 500, 0)
        self._add_spinbox(spot_detection_layout, "Min Peak Width:", "min_peak_width", 1, 100, 1)
        self._add_spinbox(spot_detection_layout, "Min Peak Distance:", "min_peak_distance", 1, 100, 1)
        self._add_spinbox(spot_detection_layout, "Min Peak Prominence:", "min_peak_prominence", 0.01, 1.0, 2, 0.1)
        self._add_spinbox(spot_detection_layout, "Spot Spacing (pixels):", "spot_spacing", 1, 1000, 1)

        # Spot detection mode combo box
        spot_mode_layout = QHBoxLayout()
        spot_mode_layout.addWidget(QLabel("Spot Detection Mode:"))
        self.spot_mode_combo = QComboBox()
        for mode in SpotDetectionMode:
            self.spot_mode_combo.addItem(mode.value, mode)
        current_index = self.spot_mode_combo.findData(
            self.laserAutofocusController.laser_af_properties.spot_detection_mode
        )
        self.spot_mode_combo.setCurrentIndex(current_index)
        spot_mode_layout.addWidget(self.spot_mode_combo)
        spot_detection_layout.addLayout(spot_mode_layout)

        # Add Run Spot Detection button
        self.run_spot_detection_button = QPushButton("Run Spot Detection")
        self.run_spot_detection_button.setEnabled(False)  # Disabled by default
        spot_detection_layout.addWidget(self.run_spot_detection_button)
        spot_detection_group.setLayout(spot_detection_layout)

        # Initialize button
        initialize_group = QFrame()
        initialize_layout = QVBoxLayout()
        self.initialize_button = QPushButton("Initialize")
        self.initialize_button.setStyleSheet("background-color: #C2C2FF")
        initialize_layout.addWidget(self.initialize_button)
        initialize_group.setLayout(initialize_layout)

        # Add Laser AF Characterization Mode checkbox
        characterization_group = QFrame()
        characterization_layout = QHBoxLayout()
        self.characterization_checkbox = QCheckBox("Laser AF Characterization Mode")
        self.characterization_checkbox.setChecked(self.laserAutofocusController.characterization_mode)
        characterization_layout.addWidget(self.characterization_checkbox)
        characterization_group.setLayout(characterization_layout)

        # Add to main layout
        layout.addWidget(live_group)
        layout.addWidget(non_threshold_group)
        layout.addWidget(settings_group)
        layout.addWidget(spot_detection_group)
        layout.addWidget(initialize_group)
        layout.addWidget(characterization_group)
        self.setLayout(layout)

        if not self.stretch:
            layout.addStretch()

        # Connect all signals to slots
        self.btn_live.clicked.connect(self.toggle_live)
        self.exposure_spinbox.valueChanged.connect(self.update_exposure_time)
        self.analog_gain_spinbox.valueChanged.connect(self.update_analog_gain)
        self.update_threshold_button.clicked.connect(self.update_threshold_settings)
        self.run_spot_detection_button.clicked.connect(self.run_spot_detection)
        self.initialize_button.clicked.connect(self.apply_and_initialize)
        self.characterization_checkbox.toggled.connect(self.toggle_characterization_mode)

    def _add_spinbox(
        self, layout, label: str, property_name: str, min_val: float, max_val: float, decimals: int, step: float = 1
    ) -> None:
        """Helper method to add a labeled spinbox to the layout."""
        box_layout = QHBoxLayout()
        box_layout.addWidget(QLabel(label))

        spinbox = QDoubleSpinBox()
        spinbox.setRange(min_val, max_val)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(step)
        # Get initial value from laser_af_properties
        current_value = getattr(self.laserAutofocusController.laser_af_properties, property_name)
        spinbox.setValue(current_value)

        box_layout.addWidget(spinbox)
        layout.addLayout(box_layout)

        # Store spinbox reference
        self.spinboxes[property_name] = spinbox

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
            self.run_spot_detection_button.setEnabled(False)
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")
            self.run_spot_detection_button.setEnabled(True)

    def stop_live(self):
        """Used for stopping live when switching to other tabs"""
        self.toggle_live(False)
        self.btn_live.setChecked(False)

    def toggle_characterization_mode(self, state):
        self.laserAutofocusController.characterization_mode = state

    def update_exposure_time(self, value):
        self.signal_newExposureTime.emit(value)

    def update_analog_gain(self, value):
        self.signal_newAnalogGain.emit(value)

    def update_values(self):
        """Update all widget values from the controller properties"""
        self.clear_labels()

        # Update spinboxes
        for prop_name, spinbox in self.spinboxes.items():
            current_value = getattr(self.laserAutofocusController.laser_af_properties, prop_name)
            spinbox.setValue(current_value)

        # Update exposure and gain
        self.exposure_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_exposure_time_ms)
        self.analog_gain_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_analog_gain)

        # Update spot detection mode
        current_mode = self.laserAutofocusController.laser_af_properties.spot_detection_mode
        index = self.spot_mode_combo.findData(current_mode)
        if index >= 0:
            self.spot_mode_combo.setCurrentIndex(index)

        self.update_threshold_button.setEnabled(self.laserAutofocusController.is_initialized)
        self.update_calibration_label()

    def apply_and_initialize(self):
        self.clear_labels()

        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes["displacement_success_window_um"].value(),
            "spot_crop_size": int(self.spinboxes["spot_crop_size"].value()),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "pixel_to_um_calibration_distance": self.spinboxes["pixel_to_um_calibration_distance"].value(),
            "laser_af_range": self.spinboxes["laser_af_range"].value(),
            "spot_detection_mode": self.spot_mode_combo.currentData(),
            "y_window": int(self.spinboxes["y_window"].value()),
            "x_window": int(self.spinboxes["x_window"].value()),
            "min_peak_width": self.spinboxes["min_peak_width"].value(),
            "min_peak_distance": self.spinboxes["min_peak_distance"].value(),
            "min_peak_prominence": self.spinboxes["min_peak_prominence"].value(),
            "spot_spacing": self.spinboxes["spot_spacing"].value(),
            "focus_camera_exposure_time_ms": self.exposure_spinbox.value(),
            "focus_camera_analog_gain": self.analog_gain_spinbox.value(),
            "has_reference": False,
        }
        self.laserAutofocusController.set_laser_af_properties(updates)
        self.laserAutofocusController.initialize_auto()
        self.signal_apply_settings.emit()
        self.update_threshold_button.setEnabled(True)
        self.update_calibration_label()

    def update_threshold_settings(self):
        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes["displacement_success_window_um"].value(),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "laser_af_range": self.spinboxes["laser_af_range"].value(),
        }
        self.laserAutofocusController.update_threshold_properties(updates)

    def update_calibration_label(self):
        # Show calibration result
        # Clear previous calibration label if it exists
        if hasattr(self, "calibration_label"):
            self.calibration_label.deleteLater()

        # Create and add new calibration label
        self.calibration_label = QLabel()
        self.calibration_label.setText(
            f"Calibration Result: {self.laserAutofocusController.laser_af_properties.pixel_to_um:.3f} pixels/um\nPerformed at {self.laserAutofocusController.laser_af_properties.calibration_timestamp}"
        )
        self.layout().addWidget(self.calibration_label)

    def illuminate_and_get_frame(self):
        # Get a frame from the live controller.  We need to reach deep into the liveController here which
        # is not ideal.
        self.liveController.microcontroller.turn_on_AF_laser()
        self.liveController.microcontroller.wait_till_operation_is_completed()
        self.liveController.trigger_acquisition()

        try:
            frame = self.liveController.camera.read_frame()
        finally:
            self.liveController.microcontroller.turn_off_AF_laser()
            self.liveController.microcontroller.wait_till_operation_is_completed()

        return frame

    def clear_labels(self):
        # Remove any existing error or correlation labels
        if hasattr(self, "spot_detection_error_label"):
            self.spot_detection_error_label.deleteLater()
            delattr(self, "spot_detection_error_label")

        if hasattr(self, "correlation_label"):
            self.correlation_label.deleteLater()
            delattr(self, "correlation_label")

    def run_spot_detection(self):
        """Run spot detection with current settings and emit results"""
        params = {
            "y_window": int(self.spinboxes["y_window"].value()),
            "x_window": int(self.spinboxes["x_window"].value()),
            "min_peak_width": self.spinboxes["min_peak_width"].value(),
            "min_peak_distance": self.spinboxes["min_peak_distance"].value(),
            "min_peak_prominence": self.spinboxes["min_peak_prominence"].value(),
            "spot_spacing": self.spinboxes["spot_spacing"].value(),
        }
        mode = self.spot_mode_combo.currentData()

        frame = self.illuminate_and_get_frame()
        if frame is not None:
            try:
                result = utils.find_spot_location(frame, mode=mode, params=params, debug_plot=True)
                if result is not None:
                    x, y = result
                    self.signal_laser_spot_location.emit(frame, x, y)
                else:
                    raise Exception("No spot detection result returned")
            except Exception:
                # Show error message
                # Clear previous error label if it exists
                if hasattr(self, "spot_detection_error_label"):
                    self.spot_detection_error_label.deleteLater()

                # Create and add new error label
                self.spot_detection_error_label = QLabel("Spot detection failed!")
                self.layout().addWidget(self.spot_detection_error_label)

    def show_cross_correlation_result(self, value):
        """Show cross-correlation value from validating laser af images"""
        # Clear previous correlation label if it exists
        if hasattr(self, "correlation_label"):
            self.correlation_label.deleteLater()

        # Create and add new correlation label
        self.correlation_label = QLabel()
        self.correlation_label.setText(f"Cross-correlation: {value:.3f}")
        self.layout().addWidget(self.correlation_label)


class SpinningDiskConfocalWidget(QWidget):

    signal_toggle_confocal_widefield = Signal(bool)

    def __init__(self, xlight):
        super(SpinningDiskConfocalWidget, self).__init__()

        self.xlight = xlight

        self.init_ui()

        self.dropdown_emission_filter.setCurrentText(str(self.xlight.get_emission_filter()))
        self.dropdown_dichroic.setCurrentText(str(self.xlight.get_dichroic()))

        self.dropdown_emission_filter.currentIndexChanged.connect(self.set_emission_filter)
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)

        self.disk_position_state = self.xlight.get_disk_position()

        self.signal_toggle_confocal_widefield.emit(self.disk_position_state)  # signal initial state

        if self.disk_position_state == 1:
            self.btn_toggle_widefield.setText("Switch to Widefield")

        self.btn_toggle_widefield.clicked.connect(self.toggle_disk_position)
        self.btn_toggle_motor.clicked.connect(self.toggle_motor)

        self.dropdown_filter_slider.valueChanged.connect(self.set_filter_slider)

        if self.xlight.has_illumination_iris_diaphragm:
            illumination_iris = self.xlight.illumination_iris
            self.slider_illumination_iris.setValue(illumination_iris)
            self.spinbox_illumination_iris.setValue(illumination_iris)

            self.slider_illumination_iris.sliderReleased.connect(lambda: self.update_illumination_iris(True))
            # Update spinbox values during sliding without sending to hardware
            self.slider_illumination_iris.valueChanged.connect(self.spinbox_illumination_iris.setValue)
            self.spinbox_illumination_iris.editingFinished.connect(lambda: self.update_illumination_iris(False))
        if self.xlight.has_emission_iris_diaphragm:
            emission_iris = self.xlight.emission_iris
            self.slider_emission_iris.setValue(emission_iris)
            self.spinbox_emission_iris.setValue(emission_iris)

            self.slider_emission_iris.sliderReleased.connect(lambda: self.update_emission_iris(True))
            # Update spinbox values during sliding without sending to hardware
            self.slider_emission_iris.valueChanged.connect(self.spinbox_emission_iris.setValue)
            self.spinbox_emission_iris.editingFinished.connect(lambda: self.update_emission_iris(False))

    def init_ui(self):

        emissionFilterLayout = QHBoxLayout()
        emissionFilterLayout.addWidget(QLabel("Emission Position"))
        self.dropdown_emission_filter = QComboBox(self)
        self.dropdown_emission_filter.addItems([str(i + 1) for i in range(8)])
        emissionFilterLayout.addWidget(self.dropdown_emission_filter)

        dichroicLayout = QHBoxLayout()
        dichroicLayout.addWidget(QLabel("Dichroic Position"))
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems([str(i + 1) for i in range(5)])
        dichroicLayout.addWidget(self.dropdown_dichroic)

        illuminationIrisLayout = QHBoxLayout()
        illuminationIrisLayout.addWidget(QLabel("Illumination Iris"))
        self.slider_illumination_iris = QSlider(Qt.Horizontal)
        self.slider_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris = QSpinBox()
        self.spinbox_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris.setKeyboardTracking(False)
        illuminationIrisLayout.addWidget(self.slider_illumination_iris)
        illuminationIrisLayout.addWidget(self.spinbox_illumination_iris)

        emissionIrisLayout = QHBoxLayout()
        emissionIrisLayout.addWidget(QLabel("Emission Iris"))
        self.slider_emission_iris = QSlider(Qt.Horizontal)
        self.slider_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris = QSpinBox()
        self.spinbox_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris.setKeyboardTracking(False)
        emissionIrisLayout.addWidget(self.slider_emission_iris)
        emissionIrisLayout.addWidget(self.spinbox_emission_iris)

        filterSliderLayout = QHBoxLayout()
        filterSliderLayout.addWidget(QLabel("Filter Slider"))
        # self.dropdown_filter_slider = QComboBox(self)
        # self.dropdown_filter_slider.addItems(["0", "1", "2", "3"])
        self.dropdown_filter_slider = QSlider(Qt.Horizontal)
        self.dropdown_filter_slider.setRange(0, 3)
        self.dropdown_filter_slider.setTickPosition(QSlider.TicksBelow)
        self.dropdown_filter_slider.setTickInterval(1)
        filterSliderLayout.addWidget(self.dropdown_filter_slider)

        self.btn_toggle_widefield = QPushButton("Switch to Confocal")

        self.btn_toggle_motor = QPushButton("Disk Motor On")
        self.btn_toggle_motor.setCheckable(True)

        layout = QGridLayout(self)

        # row 1
        if self.xlight.has_dichroic_filter_slider:
            layout.addLayout(filterSliderLayout, 0, 0, 1, 2)
        layout.addWidget(self.btn_toggle_motor, 0, 2)
        layout.addWidget(self.btn_toggle_widefield, 0, 3)

        # row 2
        if self.xlight.has_dichroic_filters_wheel:
            layout.addWidget(QLabel("Dichroic Filter Wheel"), 1, 0)
            layout.addWidget(self.dropdown_dichroic, 1, 1)
        if self.xlight.has_illumination_iris_diaphragm:
            layout.addLayout(illuminationIrisLayout, 1, 2, 1, 2)

        # row 3
        if self.xlight.has_emission_filters_wheel:
            layout.addWidget(QLabel("Emission Filter Wheel"), 2, 0)
            layout.addWidget(self.dropdown_emission_filter, 2, 1)
        if self.xlight.has_emission_iris_diaphragm:
            layout.addLayout(emissionIrisLayout, 2, 2, 1, 2)

        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)
        self.setLayout(layout)

    def enable_all_buttons(self, enable: bool):
        self.dropdown_emission_filter.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.btn_toggle_widefield.setEnabled(enable)
        self.btn_toggle_motor.setEnabled(enable)
        self.slider_illumination_iris.setEnabled(enable)
        self.spinbox_illumination_iris.setEnabled(enable)
        self.slider_emission_iris.setEnabled(enable)
        self.spinbox_emission_iris.setEnabled(enable)
        self.dropdown_filter_slider.setEnabled(enable)

    def block_iris_control_signals(self, block: bool):
        self.slider_illumination_iris.blockSignals(block)
        self.spinbox_illumination_iris.blockSignals(block)
        self.slider_emission_iris.blockSignals(block)
        self.spinbox_emission_iris.blockSignals(block)

    def toggle_disk_position(self):
        self.enable_all_buttons(False)
        if self.disk_position_state == 1:
            self.disk_position_state = self.xlight.set_disk_position(0)
            self.btn_toggle_widefield.setText("Switch to Confocal")
        else:
            self.disk_position_state = self.xlight.set_disk_position(1)
            self.btn_toggle_widefield.setText("Switch to Widefield")
        self.enable_all_buttons(True)
        self.signal_toggle_confocal_widefield.emit(self.disk_position_state)

    def toggle_motor(self):
        self.enable_all_buttons(False)
        if self.btn_toggle_motor.isChecked():
            self.xlight.set_disk_motor_state(True)
        else:
            self.xlight.set_disk_motor_state(False)
        self.enable_all_buttons(True)

    def set_emission_filter(self, index):
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_emission_filter.currentText()
        self.xlight.set_emission_filter(selected_pos)
        self.enable_all_buttons(True)

    def set_dichroic(self, index):
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_dichroic.currentText()
        self.xlight.set_dichroic(selected_pos)
        self.enable_all_buttons(True)

    def update_illumination_iris(self, from_slider: bool):
        self.block_iris_control_signals(True)  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_illumination_iris.value()
        else:
            value = self.spinbox_illumination_iris.value()
            self.slider_illumination_iris.setValue(value)
        self.xlight.set_illumination_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def update_emission_iris(self, from_slider: bool):
        self.block_iris_control_signals(True)  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_emission_iris.value()
        else:
            value = self.spinbox_emission_iris.value()
            self.slider_emission_iris.setValue(value)
        self.xlight.set_emission_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def set_filter_slider(self, index):
        self.enable_all_buttons(False)
        position = str(self.dropdown_filter_slider.value())
        self.xlight.set_filter_slider(position)
        self.enable_all_buttons(True)


class ObjectivesWidget(QWidget):
    signal_objective_changed = Signal()

    def __init__(self, objective_store, objective_changer=None):
        super(ObjectivesWidget, self).__init__()
        self.objectiveStore = objective_store
        self.objective_changer = objective_changer
        self.init_ui()
        self.dropdown.setCurrentText(self.objectiveStore.current_objective)

    def init_ui(self):
        self.dropdown = QComboBox(self)
        self.dropdown.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown.addItems(self.objectiveStore.objectives_dict.keys())
        self.dropdown.currentTextChanged.connect(self.on_objective_changed)

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Objective Lens"))
        layout.addWidget(self.dropdown)
        self.setLayout(layout)

    def on_objective_changed(self, objective_name):
        self.objectiveStore.set_current_objective(objective_name)
        if USE_XERYON:
            if objective_name in XERYON_OBJECTIVE_SWITCHER_POS_1 and self.objective_changer.currentPosition() != 1:
                self.objective_changer.moveToPosition1()
            elif objective_name in XERYON_OBJECTIVE_SWITCHER_POS_2 and self.objective_changer.currentPosition() != 2:
                self.objective_changer.moveToPosition2()
        self.signal_objective_changed.emit()


class CameraSettingsWidget(QFrame):

    signal_binning_changed = Signal()

    def __init__(
        self,
        camera: AbstractCamera,
        include_gain_exposure_time=False,
        include_camera_temperature_setting=False,
        include_camera_auto_wb_setting=False,
        main=None,
        *args,
        **kwargs,
    ):

        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.camera: AbstractCamera = camera
        self.add_components(
            include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
        )
        # set frame style
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(
        self, include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
    ):

        # add buttons and input fields
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setMinimum(self.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        self.entry_exposureTime.setValue(20)
        self.camera.set_exposure_time(20)

        self.entry_analogGain = QDoubleSpinBox()
        try:
            gain_range = self.camera.get_gain_range()
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(gain_range.min_gain)
            self.camera.set_analog_gain(gain_range.min_gain)
        except NotImplementedError:
            self._log.info("Camera does not support analog gain, disabling analog gain control.")
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.dropdown_pixelFormat = QComboBox()
        self.dropdown_pixelFormat.addItems(["MONO8", "MONO12", "MONO14", "MONO16", "BAYER_RG8", "BAYER_RG12"])
        if self.camera.get_pixel_format() is not None:
            self.dropdown_pixelFormat.setCurrentText(self.camera.get_pixel_format().name)
        else:
            print("setting camera's default pixel format")
            self.camera.set_pixel_format(CameraPixelFormat.from_string(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT))
            self.dropdown_pixelFormat.setCurrentText(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT)
        self.dropdown_pixelFormat.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))
        # to do: load and save pixel format in configurations

        self.entry_ROI_offset_x = QSpinBox()
        roi_info = self.camera.get_region_of_interest()
        (max_x, max_y) = self.camera.get_resolution()
        self.entry_ROI_offset_x.setValue(roi_info[0])
        self.entry_ROI_offset_x.setSingleStep(8)
        self.entry_ROI_offset_x.setFixedWidth(60)
        self.entry_ROI_offset_x.setMinimum(0)
        self.entry_ROI_offset_x.setMaximum(max_x)
        self.entry_ROI_offset_x.setKeyboardTracking(False)
        self.entry_ROI_offset_y = QSpinBox()
        self.entry_ROI_offset_y.setValue(roi_info[1])
        self.entry_ROI_offset_y.setSingleStep(8)
        self.entry_ROI_offset_y.setFixedWidth(60)
        self.entry_ROI_offset_y.setMinimum(0)
        self.entry_ROI_offset_y.setMaximum(max_y)
        self.entry_ROI_offset_y.setKeyboardTracking(False)
        self.entry_ROI_width = QSpinBox()
        self.entry_ROI_width.setMinimum(16)
        self.entry_ROI_width.setMaximum(max_x)
        self.entry_ROI_width.setValue(roi_info[2])
        self.entry_ROI_width.setSingleStep(8)
        self.entry_ROI_width.setFixedWidth(60)
        self.entry_ROI_width.setKeyboardTracking(False)
        self.entry_ROI_height = QSpinBox()
        self.entry_ROI_height.setSingleStep(8)
        self.entry_ROI_height.setMinimum(16)
        self.entry_ROI_height.setMaximum(max_y)
        self.entry_ROI_height.setValue(roi_info[3])
        self.entry_ROI_height.setFixedWidth(60)
        self.entry_ROI_height.setKeyboardTracking(False)
        self.entry_temperature = QDoubleSpinBox()
        self.entry_temperature.setMaximum(25)
        self.entry_temperature.setMinimum(-50)
        self.entry_temperature.setDecimals(1)
        self.label_temperature_measured = QLabel()
        # self.label_temperature_measured.setNum(0)
        self.label_temperature_measured.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # connection
        self.entry_exposureTime.valueChanged.connect(self.camera.set_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.set_analog_gain_if_supported)
        self.dropdown_pixelFormat.currentTextChanged.connect(
            lambda s: self.camera.set_pixel_format(CameraPixelFormat.from_string(s))
        )
        self.entry_ROI_offset_x.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_offset_y.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_height.valueChanged.connect(self.set_Height)
        self.entry_ROI_width.valueChanged.connect(self.set_Width)

        # layout
        self.camera_layout = QVBoxLayout()
        if include_gain_exposure_time:
            exposure_line = QHBoxLayout()
            exposure_line.addWidget(QLabel("Exposure Time (ms)"))
            exposure_line.addWidget(self.entry_exposureTime)
            self.camera_layout.addLayout(exposure_line)
            gain_line = QHBoxLayout()
            gain_line.addWidget(QLabel("Analog Gain"))
            gain_line.addWidget(self.entry_analogGain)
            self.camera_layout.addLayout(gain_line)

        format_line = QHBoxLayout()
        format_line.addWidget(QLabel("Pixel Format"))
        format_line.addWidget(self.dropdown_pixelFormat)
        try:
            current_binning = self.camera.get_binning()
            current_binning_string = "x".join([str(current_binning[0]), str(current_binning[1])])
            binning_options = [f"{binning[0]}x{binning[1]}" for binning in self.camera.get_binning_options()]
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.addItems(binning_options)
            self.dropdown_binning.setCurrentText(current_binning_string)

            self.dropdown_binning.currentTextChanged.connect(self.set_binning)
        except AttributeError as ae:
            print(ae)
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.setEnabled(False)
            pass
        format_line.addWidget(QLabel("Binning"))
        format_line.addWidget(self.dropdown_binning)
        self.camera_layout.addLayout(format_line)

        if include_camera_temperature_setting:
            temp_line = QHBoxLayout()
            temp_line.addWidget(QLabel("Set Temperature (C)"))
            temp_line.addWidget(self.entry_temperature)
            temp_line.addWidget(QLabel("Actual Temperature (C)"))
            temp_line.addWidget(self.label_temperature_measured)
            try:
                self.entry_temperature.valueChanged.connect(self.set_temperature)
                self.camera.set_temperature_reading_callback(self.update_measured_temperature)
            except AttributeError:
                pass
            self.camera_layout.addLayout(temp_line)

        roi_line = QHBoxLayout()
        roi_line.addWidget(QLabel("Height"))
        roi_line.addWidget(self.entry_ROI_height)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Y-offset"))
        roi_line.addWidget(self.entry_ROI_offset_y)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Width"))
        roi_line.addWidget(self.entry_ROI_width)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("X-offset"))
        roi_line.addWidget(self.entry_ROI_offset_x)
        self.camera_layout.addLayout(roi_line)

        if DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS is True:
            blacklevel_line = QHBoxLayout()
            blacklevel_line.addWidget(QLabel("Black Level"))

            self.label_blackLevel = QSpinBox()
            self.label_blackLevel.setMinimum(0)
            self.label_blackLevel.setMaximum(31)
            self.label_blackLevel.valueChanged.connect(self.update_blacklevel)
            self.label_blackLevel.setSuffix(" ")

            blacklevel_line.addWidget(self.label_blackLevel)

            self.camera_layout.addLayout(blacklevel_line)

        if include_camera_auto_wb_setting and CameraPixelFormat.is_color_format(self.camera.get_pixel_format()):
            # auto white balance
            self.btn_auto_wb = QPushButton("Auto White Balance")
            self.btn_auto_wb.setCheckable(True)
            self.btn_auto_wb.setChecked(False)
            self.btn_auto_wb.clicked.connect(self.toggle_auto_wb)

            self.camera_layout.addWidget(self.btn_auto_wb)

        self.setLayout(self.camera_layout)

    def set_analog_gain_if_supported(self, gain):
        try:
            self.camera.set_analog_gain(gain)
        except NotImplementedError:
            self._log.warning(f"Cannot set gain to {gain}, gain not supported.")

    def toggle_auto_wb(self, pressed):
        # 0: OFF  1:CONTINUOUS  2:ONCE
        if pressed:
            # Run auto white balance once, then uncheck
            self.camera.set_auto_white_balance_gains(on=True)
        else:
            self.camera.set_auto_white_balance_gains(on=False)
            r, g, b = self.camera.get_white_balance_gains()
            self.camera.set_white_balance_gains(r, g, b)

    def set_exposure_time(self, exposure_time):
        self.entry_exposureTime.setValue(exposure_time)

    def set_analog_gain(self, analog_gain):
        self.entry_analogGain.setValue(analog_gain)

    def set_Width(self):
        width = int(self.entry_ROI_width.value() // 8) * 8
        self.entry_ROI_width.blockSignals(True)
        self.entry_ROI_width.setValue(width)
        self.entry_ROI_width.blockSignals(False)
        offset_x = (self.camera.get_resolution()[0] - self.entry_ROI_width.value()) / 2
        offset_x = int(offset_x // 8) * 8
        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_x.setValue(offset_x)
        self.entry_ROI_offset_x.blockSignals(False)
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_Height(self):
        height = int(self.entry_ROI_height.value() // 8) * 8
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_height.setValue(height)
        self.entry_ROI_height.blockSignals(False)
        offset_y = (self.camera.get_resolution()[1] - self.entry_ROI_height.value()) / 2
        offset_y = int(offset_y // 8) * 8
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_offset_y.setValue(offset_y)
        self.entry_ROI_offset_y.blockSignals(False)
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_ROI_offset(self):
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_temperature(self):
        try:
            self.camera.set_temperature(float(self.entry_temperature.value()))
        except AttributeError:
            self._log.warning("Cannot set temperature - not supported.")

    def update_measured_temperature(self, temperature):
        self.label_temperature_measured.setNum(temperature)

    def set_binning(self, binning_text):
        binning_parts = binning_text.split("x")
        binning_x = int(binning_parts[0])
        binning_y = int(binning_parts[1])

        self.camera.set_binning(binning_x, binning_y)

        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_width.blockSignals(True)

        # TODO: move these calculations to camera class as they can be different for different cameras
        def round_to_8(val):
            return int(8 * val // 8)

        (x_offset, y_offset, width, height) = self.camera.get_region_of_interest()
        (x_max, y_max) = self.camera.get_resolution()
        self.entry_ROI_height.setMaximum(y_max)
        self.entry_ROI_width.setMaximum(x_max)

        self.entry_ROI_offset_x.setMaximum(x_max)
        self.entry_ROI_offset_y.setMaximum(y_max)

        self.entry_ROI_offset_x.setValue(round_to_8(x_offset))
        self.entry_ROI_offset_y.setValue(round_to_8(y_offset))
        self.entry_ROI_height.setValue(round_to_8(height))
        self.entry_ROI_width.setValue(round_to_8(width))

        self.entry_ROI_offset_x.blockSignals(False)
        self.entry_ROI_offset_y.blockSignals(False)
        self.entry_ROI_height.blockSignals(False)
        self.entry_ROI_width.blockSignals(False)

        self.signal_binning_changed.emit()

    def update_blacklevel(self, blacklevel):
        try:
            self.camera.set_black_level(blacklevel)
        except AttributeError:
            self._log.warning("Cannot set black level - not supported.")


class ProfileWidget(QFrame):

    signal_profile_changed = Signal()

    def __init__(self, configurationManager, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configurationManager = configurationManager

        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.setup_ui()

    def setup_ui(self):
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

    def load_profile(self):
        """Load the selected profile."""
        profile_name = self.dropdown_profiles.currentText()
        # Load the profile
        self.configurationManager.load_profile(profile_name)
        self.signal_profile_changed.emit()

    def create_new_profile(self):
        """Create a new profile with current configurations."""
        dialog = QInputDialog()
        profile_name, ok = dialog.getText(self, "New Profile", "Enter new profile name:", QLineEdit.Normal, "")

        if ok and profile_name:
            try:
                self.configurationManager.create_new_profile(profile_name)
                # Update profile dropdown
                self.dropdown_profiles.addItem(profile_name)
                self.dropdown_profiles.setCurrentText(profile_name)
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))

    def get_current_profile(self):
        """Return the currently selected profile name."""
        return self.dropdown_profiles.currentText()


class LiveControlWidget(QFrame):

    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)
    signal_live_configuration = Signal(object)
    signal_start_live = Signal()

    def __init__(
        self,
        streamHandler,
        liveController,
        objectiveStore,
        channelConfigurationManager,
        show_trigger_options=True,
        show_display_options=False,
        show_autolevel=False,
        autolevel=False,
        stretch=True,
        main=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController: LiveController = liveController
        self.streamHandler = streamHandler
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.fps_trigger = 10
        self.fps_display = 10
        self.liveController.set_trigger_fps(self.fps_trigger)
        self.streamHandler.set_display_fps(self.fps_display)

        self.currentConfiguration = self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        )[0]

        self.add_components(show_trigger_options, show_display_options, show_autolevel, autolevel, stretch)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.update_microscope_mode_by_name(self.currentConfiguration.name)

        self.is_switching_mode = False  # flag used to prevent from settings being set by twice - from both mode change slot and value change slot; another way is to use blockSignals(True)

    def add_components(self, show_trigger_options, show_display_options, show_autolevel, autolevel, stretch):
        # line 0: trigger mode
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS])
        self.dropdown_triggerManu.setCurrentText(self.liveController.camera.get_acquisition_mode().value)
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_triggerManu.setSizePolicy(sizePolicy)

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        self.entry_triggerFPS.setDecimals(0)

        # line 2: choose microscope mode / toggle live mode
        self.dropdown_modeSelection = QComboBox()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItems([microscope_configuration.name])
        self.dropdown_modeSelection.setCurrentText(self.currentConfiguration.name)
        self.dropdown_modeSelection.setSizePolicy(sizePolicy)

        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)
        self.btn_live.setStyleSheet("background-color: #C2C2FF")
        self.btn_live.setSizePolicy(sizePolicy)

        # line 3: exposure time and analog gain associated with the current mode
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setMinimum(self.liveController.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.liveController.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.setValue(0)
        self.entry_exposureTime.setSizePolicy(sizePolicy)

        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setMinimum(0)
        self.entry_analogGain.setMaximum(24)
        # self.entry_analogGain.setSuffix('x')
        self.entry_analogGain.setSingleStep(0.1)
        self.entry_analogGain.setValue(0)
        self.entry_analogGain.setSizePolicy(sizePolicy)
        # Not all cameras support analog gain, so attempt to get the gain
        # to check this
        try:
            self.liveController.camera.get_analog_gain()
        except NotImplementedError:
            self._log.info("Analog gain not supported, disabling it in live control widget.")
            self.entry_analogGain.setEnabled(False)

        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setMinimum(0)
        self.slider_illuminationIntensity.setMaximum(100)
        self.slider_illuminationIntensity.setValue(100)
        self.slider_illuminationIntensity.setSingleStep(2)

        self.entry_illuminationIntensity = QDoubleSpinBox()
        self.entry_illuminationIntensity.setMinimum(0)
        self.entry_illuminationIntensity.setMaximum(100)
        self.entry_illuminationIntensity.setSingleStep(1)
        self.entry_illuminationIntensity.setSuffix("%")
        self.entry_illuminationIntensity.setValue(100)

        # line 4: display fps and resolution scaling
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setMinimum(1)
        self.entry_displayFPS.setMaximum(240)
        self.entry_displayFPS.setSingleStep(1)
        self.entry_displayFPS.setDecimals(0)
        self.entry_displayFPS.setValue(self.fps_display)

        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setMinimum(10)
        self.slider_resolutionScaling.setMaximum(100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setSingleStep(10)

        self.label_resolutionScaling = QSpinBox()
        self.label_resolutionScaling.setMinimum(10)
        self.label_resolutionScaling.setMaximum(100)
        self.label_resolutionScaling.setValue(self.slider_resolutionScaling.value())
        self.label_resolutionScaling.setSuffix(" %")
        self.slider_resolutionScaling.setSingleStep(5)

        self.slider_resolutionScaling.valueChanged.connect(lambda v: self.label_resolutionScaling.setValue(round(v)))
        self.label_resolutionScaling.valueChanged.connect(lambda v: self.slider_resolutionScaling.setValue(round(v)))

        # autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)

        # Determine the maximum width needed
        self.entry_illuminationIntensity.setMinimumWidth(self.btn_live.sizeHint().width())
        self.btn_autolevel.setMinimumWidth(self.btn_autolevel.sizeHint().width())

        max_width = max(self.btn_autolevel.minimumWidth(), self.entry_illuminationIntensity.minimumWidth())

        # Set the fixed width for all three widgets
        self.entry_illuminationIntensity.setFixedWidth(max_width)
        self.btn_autolevel.setFixedWidth(max_width)

        # connections
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)
        self.slider_resolutionScaling.valueChanged.connect(self.streamHandler.set_display_resolution_scaling)
        self.slider_resolutionScaling.valueChanged.connect(self.liveController.set_display_resolution_scaling)
        self.dropdown_modeSelection.currentTextChanged.connect(self.update_microscope_mode_by_name)
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)
        self.btn_live.clicked.connect(self.toggle_live)
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)
        self.entry_illuminationIntensity.valueChanged.connect(self.update_config_illumination_intensity)
        self.entry_illuminationIntensity.valueChanged.connect(
            lambda x: self.slider_illuminationIntensity.setValue(int(x))
        )
        self.slider_illuminationIntensity.valueChanged.connect(self.entry_illuminationIntensity.setValue)
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)

        # layout
        grid_line1 = QHBoxLayout()
        grid_line1.addWidget(QLabel("Live Configuration"))
        grid_line1.addWidget(self.dropdown_modeSelection, 2)
        grid_line1.addWidget(self.btn_live, 1)

        grid_line2 = QHBoxLayout()
        grid_line2.addWidget(QLabel("Exposure Time"))
        grid_line2.addWidget(self.entry_exposureTime)
        gain_label = QLabel(" Analog Gain")
        gain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid_line2.addWidget(gain_label)
        grid_line2.addWidget(self.entry_analogGain)
        if show_autolevel:
            grid_line2.addWidget(self.btn_autolevel)

        grid_line4 = QHBoxLayout()
        grid_line4.addWidget(QLabel("Illumination"))
        grid_line4.addWidget(self.slider_illuminationIntensity)
        grid_line4.addWidget(self.entry_illuminationIntensity)

        grid_line0 = QHBoxLayout()
        if show_trigger_options:
            grid_line0.addWidget(QLabel("Trigger Mode"))
            grid_line0.addWidget(self.dropdown_triggerManu)
            grid_line0.addWidget(QLabel("Trigger FPS"))
            grid_line0.addWidget(self.entry_triggerFPS)

        grid_line05 = QHBoxLayout()
        show_dislpay_fps = False
        if show_display_options:
            resolution_label = QLabel("Display Resolution")
            resolution_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid_line05.addWidget(resolution_label)
            grid_line05.addWidget(self.slider_resolutionScaling)
            if show_dislpay_fps:
                grid_line05.addWidget(QLabel("Display FPS"))
                grid_line05.addWidget(self.entry_displayFPS)
            else:
                grid_line05.addWidget(self.label_resolutionScaling)

        self.grid = QVBoxLayout()
        if show_trigger_options:
            self.grid.addLayout(grid_line0)
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line2)
        self.grid.addLayout(grid_line4)
        if show_display_options:
            self.grid.addLayout(grid_line05)
        if not stretch:
            self.grid.addStretch()
        self.setLayout(self.grid)

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
            self.signal_start_live.emit()
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def toggle_autolevel(self, autolevel_on):
        self.btn_autolevel.setChecked(autolevel_on)

    def update_camera_settings(self):
        self.signal_newAnalogGain.emit(self.entry_analogGain.value())
        self.signal_newExposureTime.emit(self.entry_exposureTime.value())

    def refresh_mode_list(self):
        # Update the mode selection dropdown
        self.dropdown_modeSelection.blockSignals(True)
        self.dropdown_modeSelection.clear()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(microscope_configuration.name)
        self.dropdown_modeSelection.blockSignals(False)

        # Update to first configuration
        if self.dropdown_modeSelection.count() > 0:
            self.update_microscope_mode_by_name(self.dropdown_modeSelection.currentText())

    def update_microscope_mode_by_name(self, current_microscope_mode_name):
        self.is_switching_mode = True
        # identify the mode selected (note that this references the object in self.channelConfigurationManager.get_channel_configurations_for_objective(self.objectiveStore.current_objective))
        self.currentConfiguration = self.channelConfigurationManager.get_channel_configuration_by_name(
            self.objectiveStore.current_objective, current_microscope_mode_name
        )
        self.signal_live_configuration.emit(self.currentConfiguration)
        # update the microscope to the current configuration
        self.liveController.set_microscope_mode(self.currentConfiguration)
        # update the exposure time and analog gain settings according to the selected configuration
        self.entry_exposureTime.setValue(self.currentConfiguration.exposure_time)
        self.entry_analogGain.setValue(self.currentConfiguration.analog_gain)
        self.entry_illuminationIntensity.setValue(self.currentConfiguration.illumination_intensity)
        self.is_switching_mode = False

    def update_trigger_mode(self):
        self.liveController.set_trigger_mode(self.dropdown_triggerManu.currentText())

    def update_config_exposure_time(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.exposure_time = new_value
            self.channelConfigurationManager.update_configuration(
                self.objectiveStore.current_objective, self.currentConfiguration.id, "ExposureTime", new_value
            )
            self.signal_newExposureTime.emit(new_value)

    def update_config_analog_gain(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.analog_gain = new_value
            self.channelConfigurationManager.update_configuration(
                self.objectiveStore.current_objective, self.currentConfiguration.id, "AnalogGain", new_value
            )
            self.signal_newAnalogGain.emit(new_value)

    def update_config_illumination_intensity(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.illumination_intensity = new_value
            self.channelConfigurationManager.update_configuration(
                self.objectiveStore.current_objective, self.currentConfiguration.id, "IlluminationIntensity", new_value
            )
            self.liveController.update_illumination()

    def set_microscope_mode(self, config):
        # self.liveController.set_microscope_mode(config)
        self.dropdown_modeSelection.setCurrentText(config.name)

    def set_trigger_mode(self, trigger_mode):
        self.dropdown_triggerManu.setCurrentText(trigger_mode)
        self.liveController.set_trigger_mode(self.dropdown_triggerManu.currentText())


class PiezoWidget(QFrame):
    def __init__(self, piezo: PiezoStage, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.piezo = piezo
        self.piezo_displacement_um = 0.00
        self.add_components()

    def add_components(self):
        # Row 1: Slider and Double Spin Box for direct control
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setMinimum(0)
        self.slider.setMaximum(int(self.piezo.range_um * 100))  # Multiplied by 100 for 0.01 precision
        self.slider.setValue(int(self.piezo._home_position_um * 100))

        self.spinBox = QDoubleSpinBox(self)
        self.spinBox.setRange(0.0, self.piezo.range_um)
        self.spinBox.setDecimals(2)
        self.spinBox.setSingleStep(1)
        self.spinBox.setSuffix(" μm")
        self.spinBox.setKeyboardTracking(False)
        self.spinBox.setValue(self.piezo._home_position_um)

        # Row 3: Home Button
        self.home_btn = QPushButton(f" Set to {self.piezo._home_position_um} μm ", self)

        hbox1 = QHBoxLayout()
        hbox1.addWidget(self.home_btn)
        hbox1.addWidget(self.slider)
        hbox1.addWidget(self.spinBox)

        # Row 2: Increment Double Spin Box, Move Up and Move Down Buttons
        self.increment_spinBox = QDoubleSpinBox(self)
        self.increment_spinBox.setRange(0.0, 100.0)
        self.increment_spinBox.setDecimals(2)
        self.increment_spinBox.setSingleStep(1)
        self.increment_spinBox.setValue(1.00)
        self.increment_spinBox.setSuffix(" μm")
        self.move_up_btn = QPushButton("Move Up", self)
        self.move_down_btn = QPushButton("Move Down", self)

        hbox2 = QHBoxLayout()
        hbox2.addWidget(self.increment_spinBox)
        hbox2.addWidget(self.move_up_btn)
        hbox2.addWidget(self.move_down_btn)

        # Vertical Layout to include all HBoxes
        vbox = QVBoxLayout()
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)

        self.setLayout(vbox)

        # Connect signals and slots
        self.slider.valueChanged.connect(self.update_from_slider)
        self.spinBox.valueChanged.connect(self.update_from_spinBox)
        self.move_up_btn.clicked.connect(lambda: self.adjust_position(True))
        self.move_down_btn.clicked.connect(lambda: self.adjust_position(False))
        self.home_btn.clicked.connect(self.home)

    def update_from_slider(self, value):
        self.piezo_displacement_um = value / 100  # Convert back to float with two decimal places
        self.update_spinBox()
        self.update_piezo_position()

    def update_from_spinBox(self, value):
        self.piezo_displacement_um = value
        self.update_slider()
        self.update_piezo_position()

    def update_spinBox(self):
        self.spinBox.blockSignals(True)
        self.spinBox.setValue(self.piezo_displacement_um)
        self.spinBox.blockSignals(False)

    def update_slider(self):
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.piezo_displacement_um * 100))
        self.slider.blockSignals(False)

    def update_piezo_position(self):
        self.piezo.move_to(self.piezo_displacement_um)

    def adjust_position(self, up):
        increment = self.increment_spinBox.value()
        if up:
            self.piezo_displacement_um = min(self.piezo.range_um, self.spinBox.value() + increment)
        else:
            self.piezo_displacement_um = max(0, self.spinBox.value() - increment)
        self.update_spinBox()
        self.update_slider()
        self.update_piezo_position()

    def home(self):
        self.piezo.home()
        self.piezo_displacement_um = self.piezo._home_position_um
        self.update_spinBox()
        self.update_slider()

    def update_displacement_um_display(self, displacement=None):
        if displacement is None:
            displacement = self.piezo.position
        self.piezo_displacement_um = round(displacement, 2)
        self.update_spinBox()
        self.update_slider()


class RecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.imageSaver.set_base_path(DEFAULT_SAVING_PATH)

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        self.streamHandler.set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line2)
        self.grid.addLayout(grid_line3)
        self.grid.addWidget(self.btn_record)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        self.entry_saveFPS.valueChanged.connect(self.streamHandler.set_save_fps)
        self.entry_timeLimit.valueChanged.connect(self.imageSaver.set_recording_time_limit)
        self.imageSaver.stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.imageSaver.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            self.imageSaver.start_new_experiment(self.lineEdit_experimentID.text())
            self.streamHandler.start_recording()
        else:
            self.streamHandler.stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        self.streamHandler.stop_recording()
        self.btn_setSavingDir.setEnabled(True)


class NavigationWidget(QFrame):
    def __init__(
        self,
        stage: AbstractStage,
        slidePositionController=None,
        main=None,
        widget_configuration="full",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.slidePositionController = slidePositionController
        self.widget_configuration = widget_configuration
        self.slide_position = None
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.position_update_timer = QTimer()
        self.position_update_timer.setInterval(100)
        self.position_update_timer.timeout.connect(self._update_position)
        self.position_update_timer.start()

    def _update_position(self):
        pos = self.stage.get_pos()
        self.label_Xpos.setNum(pos.x_mm)
        self.label_Ypos.setNum(pos.y_mm)
        # NOTE: The z label is in um
        self.label_Zpos.setNum(pos.z_mm * 1000)

    def add_components(self):
        x_label = QLabel("X :")
        x_label.setFixedWidth(20)
        self.label_Xpos = QLabel()
        self.label_Xpos.setNum(0)
        self.label_Xpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dX = QDoubleSpinBox()
        self.entry_dX.setMinimum(0)
        self.entry_dX.setMaximum(25)
        self.entry_dX.setSingleStep(0.2)
        self.entry_dX.setValue(0)
        self.entry_dX.setDecimals(3)
        self.entry_dX.setSuffix(" mm")
        self.entry_dX.setKeyboardTracking(False)
        self.btn_moveX_forward = QPushButton("Forward")
        self.btn_moveX_forward.setDefault(False)
        self.btn_moveX_backward = QPushButton("Backward")
        self.btn_moveX_backward.setDefault(False)

        self.btn_home_X = QPushButton("Home X")
        self.btn_home_X.setDefault(False)
        self.btn_home_X.setEnabled(HOMING_ENABLED_X)
        self.btn_zero_X = QPushButton("Zero X")
        self.btn_zero_X.setDefault(False)

        self.checkbox_clickToMove = QCheckBox("Click to Move")
        self.checkbox_clickToMove.setChecked(False)
        self.checkbox_clickToMove.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))

        y_label = QLabel("Y :")
        y_label.setFixedWidth(20)
        self.label_Ypos = QLabel()
        self.label_Ypos.setNum(0)
        self.label_Ypos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dY = QDoubleSpinBox()
        self.entry_dY.setMinimum(0)
        self.entry_dY.setMaximum(25)
        self.entry_dY.setSingleStep(0.2)
        self.entry_dY.setValue(0)
        self.entry_dY.setDecimals(3)
        self.entry_dY.setSuffix(" mm")

        self.entry_dY.setKeyboardTracking(False)
        self.btn_moveY_forward = QPushButton("Forward")
        self.btn_moveY_forward.setDefault(False)
        self.btn_moveY_backward = QPushButton("Backward")
        self.btn_moveY_backward.setDefault(False)

        self.btn_home_Y = QPushButton("Home Y")
        self.btn_home_Y.setDefault(False)
        self.btn_home_Y.setEnabled(HOMING_ENABLED_Y)
        self.btn_zero_Y = QPushButton("Zero Y")
        self.btn_zero_Y.setDefault(False)

        z_label = QLabel("Z :")
        z_label.setFixedWidth(20)
        self.label_Zpos = QLabel()
        self.label_Zpos.setNum(0)
        self.label_Zpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dZ = QDoubleSpinBox()
        self.entry_dZ.setMinimum(0)
        self.entry_dZ.setMaximum(1000)
        self.entry_dZ.setSingleStep(0.2)
        self.entry_dZ.setValue(0)
        self.entry_dZ.setDecimals(3)
        self.entry_dZ.setSuffix(" μm")
        self.entry_dZ.setKeyboardTracking(False)
        self.btn_moveZ_forward = QPushButton("Forward")
        self.btn_moveZ_forward.setDefault(False)
        self.btn_moveZ_backward = QPushButton("Backward")
        self.btn_moveZ_backward.setDefault(False)

        self.btn_home_Z = QPushButton("Home Z")
        self.btn_home_Z.setDefault(False)
        self.btn_home_Z.setEnabled(HOMING_ENABLED_Z)
        self.btn_zero_Z = QPushButton("Zero Z")
        self.btn_zero_Z.setDefault(False)

        self.btn_load_slide = QPushButton("Move To Loading Position")
        self.btn_load_slide.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        grid_line0 = QGridLayout()
        grid_line0.addWidget(x_label, 0, 0)
        grid_line0.addWidget(self.label_Xpos, 0, 1)
        grid_line0.addWidget(self.entry_dX, 0, 2)
        grid_line0.addWidget(self.btn_moveX_forward, 0, 3)
        grid_line0.addWidget(self.btn_moveX_backward, 0, 4)

        grid_line0.addWidget(y_label, 1, 0)
        grid_line0.addWidget(self.label_Ypos, 1, 1)
        grid_line0.addWidget(self.entry_dY, 1, 2)
        grid_line0.addWidget(self.btn_moveY_forward, 1, 3)
        grid_line0.addWidget(self.btn_moveY_backward, 1, 4)

        grid_line0.addWidget(z_label, 2, 0)
        grid_line0.addWidget(self.label_Zpos, 2, 1)
        grid_line0.addWidget(self.entry_dZ, 2, 2)
        grid_line0.addWidget(self.btn_moveZ_forward, 2, 3)
        grid_line0.addWidget(self.btn_moveZ_backward, 2, 4)

        grid_line3 = QHBoxLayout()

        if self.widget_configuration == "full":
            grid_line3.addWidget(self.btn_home_X)
            grid_line3.addWidget(self.btn_home_Y)
            grid_line3.addWidget(self.btn_home_Z)
            grid_line3.addWidget(self.btn_zero_X)
            grid_line3.addWidget(self.btn_zero_Y)
            grid_line3.addWidget(self.btn_zero_Z)
        else:
            grid_line3.addWidget(self.btn_load_slide, 1)
            grid_line3.addWidget(self.btn_home_Z, 1)
            grid_line3.addWidget(self.btn_zero_Z, 1)

        self.set_click_to_move(ENABLE_CLICK_TO_MOVE_BY_DEFAULT)
        if not ENABLE_CLICK_TO_MOVE_BY_DEFAULT:
            grid_line3.addWidget(self.checkbox_clickToMove, 1)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        self.grid.addLayout(grid_line3)
        self.setLayout(self.grid)

        self.entry_dX.valueChanged.connect(self.set_deltaX)
        self.entry_dY.valueChanged.connect(self.set_deltaY)
        self.entry_dZ.valueChanged.connect(self.set_deltaZ)

        self.btn_moveX_forward.clicked.connect(self.move_x_forward)
        self.btn_moveX_backward.clicked.connect(self.move_x_backward)
        self.btn_moveY_forward.clicked.connect(self.move_y_forward)
        self.btn_moveY_backward.clicked.connect(self.move_y_backward)
        self.btn_moveZ_forward.clicked.connect(self.move_z_forward)
        self.btn_moveZ_backward.clicked.connect(self.move_z_backward)

        self.btn_home_X.clicked.connect(self.home_x)
        self.btn_home_Y.clicked.connect(self.home_y)
        self.btn_home_Z.clicked.connect(self.home_z)
        self.btn_zero_X.clicked.connect(self.zero_x)
        self.btn_zero_Y.clicked.connect(self.zero_y)
        self.btn_zero_Z.clicked.connect(self.zero_z)

        self.btn_load_slide.clicked.connect(self.switch_position)
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")

    def set_click_to_move(self, enabled):
        self.log.info(f"Click to move enabled={enabled}")
        self.setEnabled_all(enabled)
        self.checkbox_clickToMove.setChecked(enabled)

    def get_click_to_move_enabled(self):
        return self.checkbox_clickToMove.isChecked()

    def setEnabled_all(self, enabled):
        self.checkbox_clickToMove.setEnabled(enabled)
        self.btn_home_X.setEnabled(enabled)
        self.btn_zero_X.setEnabled(enabled)
        self.btn_moveX_forward.setEnabled(enabled)
        self.btn_moveX_backward.setEnabled(enabled)
        self.btn_home_Y.setEnabled(enabled)
        self.btn_zero_Y.setEnabled(enabled)
        self.btn_moveY_forward.setEnabled(enabled)
        self.btn_moveY_backward.setEnabled(enabled)
        self.btn_home_Z.setEnabled(enabled)
        self.btn_zero_Z.setEnabled(enabled)
        self.btn_moveZ_forward.setEnabled(enabled)
        self.btn_moveZ_backward.setEnabled(enabled)
        self.btn_load_slide.setEnabled(enabled)

    def move_x_forward(self):
        self.stage.move_x(self.entry_dX.value())

    def move_x_backward(self):
        self.stage.move_x(-self.entry_dX.value())

    def move_y_forward(self):
        self.stage.move_y(self.entry_dY.value())

    def move_y_backward(self):
        self.stage.move_y(-self.entry_dY.value())

    def move_z_forward(self):
        self.stage.move_z(self.entry_dZ.value() / 1000)

    def move_z_backward(self):
        self.stage.move_z(-self.entry_dZ.value() / 1000)

    def set_deltaX(self, value):
        mm_per_ustep = 1.0 / self.stage.x_mm_to_usteps(1.0)
        deltaX = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dX.setValue(deltaX)

    def set_deltaY(self, value):
        mm_per_ustep = 1.0 / self.stage.y_mm_to_usteps(1.0)
        deltaY = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dY.setValue(deltaY)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.z_mm_to_usteps(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_dZ.setValue(deltaZ)

    def home_x(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.stage.home(x=True, y=False, z=False, theta=False)

    def home_y(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.stage.home(x=False, y=True, z=False, theta=False)

    def home_z(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.stage.home(x=False, y=False, z=True, theta=False)

    def zero_x(self):
        self.stage.zero(x=True, y=False, z=False, theta=False)

    def zero_y(self):
        self.stage.zero(x=False, y=True, z=False, theta=False)

    def zero_z(self):
        self.stage.zero(x=False, y=False, z=True, theta=False)

    def slot_slide_loading_position_reached(self):
        self.slide_position = "loading"
        self.btn_load_slide.setStyleSheet("background-color: #C2FFC2")
        self.btn_load_slide.setText("Move to Scanning Position")
        self.btn_moveX_forward.setEnabled(False)
        self.btn_moveX_backward.setEnabled(False)
        self.btn_moveY_forward.setEnabled(False)
        self.btn_moveY_backward.setEnabled(False)
        self.btn_moveZ_forward.setEnabled(False)
        self.btn_moveZ_backward.setEnabled(False)
        self.btn_load_slide.setEnabled(True)

    def slot_slide_scanning_position_reached(self):
        self.slide_position = "scanning"
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")
        self.btn_load_slide.setText("Move to Loading Position")
        self.btn_moveX_forward.setEnabled(True)
        self.btn_moveX_backward.setEnabled(True)
        self.btn_moveY_forward.setEnabled(True)
        self.btn_moveY_backward.setEnabled(True)
        self.btn_moveZ_forward.setEnabled(True)
        self.btn_moveZ_backward.setEnabled(True)
        self.btn_load_slide.setEnabled(True)

    def switch_position(self):
        if self.slide_position != "loading":
            self.slidePositionController.move_to_slide_loading_position()
        else:
            self.slidePositionController.move_to_slide_scanning_position()
        self.btn_load_slide.setEnabled(False)

    def replace_slide_controller(self, slidePositionController):
        self.slidePositionController = slidePositionController
        self.slidePositionController.signal_slide_loading_position_reached.connect(
            self.slot_slide_loading_position_reached
        )
        self.slidePositionController.signal_slide_scanning_position_reached.connect(
            self.slot_slide_scanning_position_reached
        )


class DACControWidget(QFrame):
    def __init__(self, microcontroller, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.microcontroller = microcontroller
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.slider_DAC0 = QSlider(Qt.Horizontal)
        self.slider_DAC0.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC0.setMinimum(0)
        self.slider_DAC0.setMaximum(100)
        self.slider_DAC0.setSingleStep(1)
        self.slider_DAC0.setValue(0)

        self.entry_DAC0 = QDoubleSpinBox()
        self.entry_DAC0.setMinimum(0)
        self.entry_DAC0.setMaximum(100)
        self.entry_DAC0.setSingleStep(0.1)
        self.entry_DAC0.setValue(0)
        self.entry_DAC0.setKeyboardTracking(False)

        self.slider_DAC1 = QSlider(Qt.Horizontal)
        self.slider_DAC1.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC1.setMinimum(0)
        self.slider_DAC1.setMaximum(100)
        self.slider_DAC1.setValue(0)
        self.slider_DAC1.setSingleStep(1)

        self.entry_DAC1 = QDoubleSpinBox()
        self.entry_DAC1.setMinimum(0)
        self.entry_DAC1.setMaximum(100)
        self.entry_DAC1.setSingleStep(0.1)
        self.entry_DAC1.setValue(0)
        self.entry_DAC1.setKeyboardTracking(False)

        # connections
        self.entry_DAC0.valueChanged.connect(self.set_DAC0)
        self.entry_DAC0.valueChanged.connect(self.slider_DAC0.setValue)
        self.slider_DAC0.valueChanged.connect(self.entry_DAC0.setValue)
        self.entry_DAC1.valueChanged.connect(self.set_DAC1)
        self.entry_DAC1.valueChanged.connect(self.slider_DAC1.setValue)
        self.slider_DAC1.valueChanged.connect(self.entry_DAC1.setValue)

        # layout
        grid_line1 = QHBoxLayout()
        grid_line1.addWidget(QLabel("DAC0"))
        grid_line1.addWidget(self.slider_DAC0)
        grid_line1.addWidget(self.entry_DAC0)
        grid_line1.addWidget(QLabel("DAC1"))
        grid_line1.addWidget(self.slider_DAC1)
        grid_line1.addWidget(self.entry_DAC1)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

    def set_DAC0(self, value):
        self.microcontroller.analog_write_onboard_DAC(0, round(value * 65535 / 100))

    def set_DAC1(self, value):
        self.microcontroller.analog_write_onboard_DAC(1, round(value * 65535 / 100))


class AutoFocusWidget(QFrame):
    signal_autoLevelSetting = Signal(bool)

    def __init__(self, autofocusController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autofocusController = autofocusController
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.stage = self.autofocusController.stage

    def add_components(self):
        self.entry_delta = QDoubleSpinBox()
        self.entry_delta.setMinimum(0)
        self.entry_delta.setMaximum(20)
        self.entry_delta.setSingleStep(0.2)
        self.entry_delta.setDecimals(3)
        self.entry_delta.setSuffix(" μm")
        self.entry_delta.setValue(1.524)
        self.entry_delta.setKeyboardTracking(False)
        self.entry_delta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_deltaZ(1.524)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(3)
        self.entry_N.setMaximum(10000)
        self.entry_N.setFixedWidth(self.entry_N.sizeHint().width())
        self.entry_N.setMaximum(20)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(10)
        self.entry_N.setKeyboardTracking(False)
        self.entry_N.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_N(10)

        self.btn_autofocus = QPushButton("Autofocus")
        self.btn_autofocus.setDefault(False)
        self.btn_autofocus.setCheckable(True)
        self.btn_autofocus.setChecked(False)

        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(False)
        self.btn_autolevel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # layout
        self.grid = QVBoxLayout()
        grid_line0 = QHBoxLayout()
        grid_line0.addWidget(QLabel("\u0394 Z"))
        grid_line0.addWidget(self.entry_delta)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(QLabel("# of Z-Planes"))
        grid_line0.addWidget(self.entry_N)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(self.btn_autolevel)

        self.grid.addLayout(grid_line0)
        self.grid.addWidget(self.btn_autofocus)
        self.setLayout(self.grid)

        # connections
        self.btn_autofocus.toggled.connect(lambda: self.autofocusController.autofocus(False))
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.entry_delta.valueChanged.connect(self.set_deltaZ)
        self.entry_N.valueChanged.connect(self.autofocusController.set_N)
        self.autofocusController.autofocusFinished.connect(self.autofocus_is_finished)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.log.debug(f"{deltaZ=}")

        self.entry_delta.setValue(deltaZ)
        self.autofocusController.set_deltaZ(deltaZ)

    def autofocus_is_finished(self):
        self.btn_autofocus.setChecked(False)


class FilterControllerWidget(QFrame):
    def __init__(self, filterController, liveController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filterController = filterController
        self.liveController = liveController
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.comboBox = QComboBox()
        for i in range(1, 8):  # Assuming 7 filter positions
            self.comboBox.addItem(f"Position {i}")
        self.checkBox = QCheckBox("Disable filter wheel movement on changing Microscope Configuration", self)

        layout = QGridLayout()
        layout.addWidget(QLabel("Filter wheel position:"), 0, 0)
        layout.addWidget(self.comboBox, 0, 1)
        layout.addWidget(self.checkBox, 2, 0)

        self.setLayout(layout)

        self.comboBox.currentIndexChanged.connect(self.on_selection_change)  # Connecting to selection change
        self.checkBox.stateChanged.connect(self.disable_movement_by_switching_channels)

    def on_selection_change(self, index):
        # The 'index' parameter is the new index of the combo box
        if index >= 0 and index <= 7:  # Making sure the index is valid
            self.filterController.set_emission_filter(index + 1)

    def disable_movement_by_switching_channels(self, state):
        if state:
            self.liveController.enable_channel_auto_filter_switching = False
        else:
            self.liveController.enable_channel_auto_filter_switching = True


class StatsDisplayWidget(QFrame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initUI()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def initUI(self):
        self.layout = QVBoxLayout()
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(2)
        self.table_widget.verticalHeader().hide()
        self.table_widget.horizontalHeader().hide()
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.layout.addWidget(self.table_widget)
        self.setLayout(self.layout)

    def display_stats(self, stats):
        print("displaying parasite stats")
        locale.setlocale(locale.LC_ALL, "")
        self.table_widget.setRowCount(len(stats))
        row = 0
        for key, value in stats.items():
            key_item = QTableWidgetItem(str(key))
            value_item = None
            try:
                value_item = QTableWidgetItem(f"{value:n}")
            except:
                value_item = QTableWidgetItem(str(value))
            self.table_widget.setItem(row, 0, key_item)
            self.table_widget.setItem(row, 1, value_item)
            row += 1


class FlexibleMultiPointWidget(QFrame):

    signal_acquisition_started = Signal(bool)  # true = started, false = finished
    signal_acquisition_channels = Signal(list)  # list channels
    signal_acquisition_shape = Signal(int, float)  # Nz, dz
    signal_stitcher_z_levels = Signal(int)  # live Nz
    signal_stitcher_widget = Signal(bool)  # signal start stitcher

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        focusMapWidget,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.acquisition_start_time = None
        self.last_used_locations = None
        self.last_used_location_ids = None
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        self.base_path_is_set = False
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.use_overlap = USE_OVERLAP_FOR_FLEXIBLE
        self.add_components()
        self.setup_layout()
        self.setup_connections()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.is_current_acquisition_widget = False
        self.acquisition_in_place = False
        self.parent = self.multipointController.parent

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()
        self.lineEdit_experimentID.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lineEdit_experimentID.setFixedWidth(96)

        self.dropdown_location_list = QComboBox()
        self.dropdown_location_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        self.btn_previous = QPushButton("Previous")
        self.btn_next = QPushButton("Next")
        self.btn_clear = QPushButton("Clear")

        self.btn_load_last_executed = QPushButton("Prev Used Locations")

        self.btn_export_locations = QPushButton("Export Location List")
        self.btn_import_locations = QPushButton("Import Location List")

        # editable points table
        self.table_location_list = QTableWidget()
        self.table_location_list.setColumnCount(4)
        header_labels = ["x", "y", "z", "ID"]
        self.table_location_list.setHorizontalHeaderLabels(header_labels)
        self.btn_show_table_location_list = QPushButton("Edit")  # Open / Edit

        self.entry_deltaX = QDoubleSpinBox()
        self.entry_deltaX.setMinimum(0)
        self.entry_deltaX.setMaximum(5)
        self.entry_deltaX.setSingleStep(0.1)
        self.entry_deltaX.setValue(Acquisition.DX)
        self.entry_deltaX.setDecimals(3)
        self.entry_deltaX.setSuffix(" mm")
        self.entry_deltaX.setKeyboardTracking(False)

        self.entry_NX = QSpinBox()
        self.entry_NX.setMinimum(1)
        self.entry_NX.setMaximum(1000)
        self.entry_NX.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NX.setMaximum(50)
        self.entry_NX.setSingleStep(1)
        self.entry_NX.setValue(1)
        self.entry_NX.setKeyboardTracking(False)
        # self.entry_NX.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_deltaY = QDoubleSpinBox()
        self.entry_deltaY.setMinimum(0)
        self.entry_deltaY.setMaximum(5)
        self.entry_deltaY.setSingleStep(0.1)
        self.entry_deltaY.setValue(Acquisition.DX)
        self.entry_deltaY.setDecimals(3)
        self.entry_deltaY.setSuffix(" mm")
        self.entry_deltaY.setKeyboardTracking(False)

        self.entry_NY = QSpinBox()
        self.entry_NY.setMinimum(1)
        self.entry_NY.setMaximum(1000)
        self.entry_NY.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NY.setMaximum(50)
        self.entry_NY.setSingleStep(1)
        self.entry_NY.setValue(1)
        self.entry_NY.setKeyboardTracking(False)
        # self.entry_NY.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setDecimals(1)
        self.entry_overlap.setSuffix(" %")
        self.entry_overlap.setValue(10)
        self.entry_overlap.setKeyboardTracking(False)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setKeyboardTracking(False)
        # self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setKeyboardTracking(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(12 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setKeyboardTracking(False)
        # self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(10000)  # @@@ to be changed
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)
        self.entry_Nt.setKeyboardTracking(False)

        # Calculate a consistent width
        max_delta_width = max(
            self.entry_deltaZ.sizeHint().width(),
            self.entry_dt.sizeHint().width(),
            self.entry_deltaX.sizeHint().width(),
            self.entry_deltaY.sizeHint().width(),
        )
        self.entry_deltaZ.setFixedWidth(max_delta_width)
        self.entry_dt.setFixedWidth(max_delta_width)
        self.entry_deltaX.setFixedWidth(max_delta_width)
        self.entry_deltaY.setFixedWidth(max_delta_width)

        max_num_width = max(
            self.entry_NX.sizeHint().width(),
            self.entry_NY.sizeHint().width(),
            self.entry_NZ.sizeHint().width(),
            self.entry_Nt.sizeHint().width(),
        )
        self.entry_NX.setFixedWidth(max_num_width)
        self.entry_NY.setFixedWidth(max_num_width)
        self.entry_NZ.setFixedWidth(max_num_width)
        self.entry_Nt.setFixedWidth(max_num_width)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.checkbox_set_z_range = QCheckBox("Set Z-range")
        self.checkbox_set_z_range.toggled.connect(self.toggle_z_range_controls)

        # Add new components for Z-range
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_minZ_button = QPushButton("Set")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_maxZ_button = QPushButton("Set")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # layout
        self.grid_line0 = QHBoxLayout()
        self.grid_line0.addWidget(QLabel("Saving Path"))
        self.grid_line0.addWidget(self.lineEdit_savingDir)
        self.grid_line0.addWidget(self.btn_setSavingDir)
        self.grid_line0.addWidget(QLabel("ID"))
        self.grid_line0.addWidget(self.lineEdit_experimentID)

        self.grid_location_list_line1 = QGridLayout()
        temp3 = QHBoxLayout()
        temp3.addWidget(QLabel("Location List"))
        temp3.addWidget(self.dropdown_location_list)
        self.grid_location_list_line1.addLayout(temp3, 0, 0, 1, 6)  # Span across all columns except the last
        self.grid_location_list_line1.addWidget(
            self.btn_show_table_location_list, 0, 6, 1, 2
        )  # Align with other buttons

        self.grid_location_list_line2 = QGridLayout()
        # Make all buttons span 2 columns for consistent width
        self.grid_location_list_line2.addWidget(self.btn_add, 1, 0, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_remove, 1, 2, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_next, 1, 4, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_clear, 1, 6, 1, 2)

        self.grid_location_list_line3 = QGridLayout()
        self.grid_location_list_line3.addWidget(self.btn_import_locations, 2, 0, 1, 4)
        self.grid_location_list_line3.addWidget(self.btn_export_locations, 2, 4, 1, 4)

        # Create spacer items
        EDGE_SPACING = 4  # Adjust this value as needed
        edge_spacer = QSpacerItem(EDGE_SPACING, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)

        # Create first row layouts
        if self.use_overlap:
            xy_half = QHBoxLayout()
            xy_half.addWidget(QLabel("Nx"))
            xy_half.addWidget(self.entry_NX)
            xy_half.addStretch(1)
            xy_half.addWidget(QLabel("Ny"))
            xy_half.addWidget(self.entry_NY)
            xy_half.addSpacerItem(edge_spacer)

            overlap_half = QHBoxLayout()
            overlap_half.addSpacerItem(edge_spacer)
            overlap_half.addWidget(QLabel("FOV Overlap"), alignment=Qt.AlignRight)
            overlap_half.addWidget(self.entry_overlap)
        else:
            # Create alternate first row layouts (dx, dy) instead of (overlap %)
            x_half = QHBoxLayout()
            x_half.addWidget(QLabel("dx"))
            x_half.addWidget(self.entry_deltaX)
            x_half.addStretch(1)
            x_half.addWidget(QLabel("Nx"))
            x_half.addWidget(self.entry_NX)
            x_half.addSpacerItem(edge_spacer)

            y_half = QHBoxLayout()
            y_half.addSpacerItem(edge_spacer)
            y_half.addWidget(QLabel("dy"))
            y_half.addWidget(self.entry_deltaY)
            y_half.addStretch(1)
            y_half.addWidget(QLabel("Ny"))
            y_half.addWidget(self.entry_NY)

        # Create second row layouts
        dz_half = QHBoxLayout()
        dz_half.addWidget(QLabel("dz"))
        dz_half.addWidget(self.entry_deltaZ)
        dz_half.addStretch(1)
        dz_half.addWidget(QLabel("Nz"))
        dz_half.addWidget(self.entry_NZ)
        dz_half.addSpacerItem(edge_spacer)

        dt_half = QHBoxLayout()
        dt_half.addSpacerItem(edge_spacer)
        dt_half.addWidget(QLabel("dt"))
        dt_half.addWidget(self.entry_dt)
        dt_half.addStretch(1)
        dt_half.addWidget(QLabel("Nt"))
        dt_half.addWidget(self.entry_Nt)

        self.grid_acquisition = QGridLayout()
        # Add the layouts to grid_line1
        if self.use_overlap:
            self.grid_acquisition.addLayout(xy_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(overlap_half, 3, 4, 1, 4)
        else:
            self.grid_acquisition.addLayout(x_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(y_half, 3, 4, 1, 4)
        self.grid_acquisition.addLayout(dz_half, 4, 0, 1, 4)
        self.grid_acquisition.addLayout(dt_half, 4, 4, 1, 4)

        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.set_minZ_button)
        self.z_min_layout.addWidget(QLabel("Z-min"), Qt.AlignRight)
        self.z_min_layout.addWidget(self.entry_minZ)
        self.z_min_layout.addSpacerItem(edge_spacer)

        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addSpacerItem(edge_spacer)
        self.z_max_layout.addWidget(self.set_maxZ_button)
        self.z_max_layout.addWidget(QLabel("Z-max"), Qt.AlignRight)
        self.z_max_layout.addWidget(self.entry_maxZ)

        self.grid_acquisition.addLayout(self.z_min_layout, 5, 0, 1, 4)  # hide this in toggle
        self.grid_acquisition.addLayout(self.z_max_layout, 5, 4, 1, 4)  # hide this in toggle

        grid_af = QVBoxLayout()
        grid_af.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            grid_af.addWidget(self.checkbox_withReflectionAutofocus)
        # grid_af.addWidget(self.checkbox_genAFMap)  # we are not using auto-focus map for now
        grid_af.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            grid_af.addWidget(self.checkbox_usePiezo)
        grid_af.addWidget(self.checkbox_set_z_range)
        if ENABLE_STITCHER:
            grid_af.addWidget(self.checkbox_stitchOutput)

        grid_config = QHBoxLayout()
        grid_config.addWidget(self.list_configurations)
        grid_config.addSpacerItem(edge_spacer)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        grid_acquisition = QHBoxLayout()
        grid_acquisition.addSpacerItem(edge_spacer)
        grid_acquisition.addLayout(grid_af)
        grid_acquisition.addLayout(button_layout)

        self.grid_acquisition.addLayout(grid_config, 6, 0, 3, 4)
        self.grid_acquisition.addLayout(grid_acquisition, 6, 4, 3, 4)

        # Columns 0-3: Combined stretch factor = 4
        # Columns 4-7: Combined stretch factor = 4
        for i in range(4):
            self.grid_location_list_line1.setColumnStretch(i, 1)
            self.grid_location_list_line2.setColumnStretch(i, 1)
            self.grid_location_list_line3.setColumnStretch(i, 1)
            self.grid_acquisition.setColumnStretch(i, 1)

            self.grid_location_list_line1.setColumnStretch(i + 4, 1)
            self.grid_location_list_line2.setColumnStretch(i + 4, 1)
            self.grid_location_list_line3.setColumnStretch(i + 4, 1)
            self.grid_acquisition.setColumnStretch(i + 4, 1)

        self.grid_location_list_line1.setRowStretch(0, 0)  # Location list row
        self.grid_location_list_line2.setRowStretch(1, 0)  # Button row
        self.grid_location_list_line3.setRowStretch(2, 0)  # Import/Export buttons
        self.grid_acquisition.setRowStretch(0, 0)  # Nx/Ny and overlap row
        self.grid_acquisition.setRowStretch(1, 0)  # dz/Nz and dt/Nt row
        self.grid_acquisition.setRowStretch(2, 0)  # Z-range row
        self.grid_acquisition.setRowStretch(3, 1)  # Configuration/AF row - allow this to stretch
        self.grid_acquisition.setRowStretch(4, 0)  # Last row

        # Row : Progress Bar
        self.row_progress_layout = QHBoxLayout()
        self.row_progress_layout.addWidget(self.progress_label)
        self.row_progress_layout.addWidget(self.progress_bar)
        self.row_progress_layout.addWidget(self.eta_label)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

    def setup_connections(self):
        # connections
        if self.use_overlap:
            self.entry_overlap.valueChanged.connect(self.update_fov_positions)
        else:
            self.entry_deltaX.valueChanged.connect(self.update_fov_positions)
            self.entry_deltaY.valueChanged.connect(self.update_fov_positions)
        self.entry_NX.valueChanged.connect(self.update_fov_positions)
        self.entry_NY.valueChanged.connect(self.update_fov_positions)
        # self.btn_add.clicked.connect(self.update_fov_positions) #TODO: this is handled in the add_location method - to be removed
        # self.btn_remove.clicked.connect(self.update_fov_positions) #TODO: this is handled in the remove_location method - to be removed
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_NX.valueChanged.connect(self.multipointController.set_NX)
        self.entry_NY.valueChanged.connect(self.multipointController.set_NY)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_NZ.valueChanged.connect(self.signal_stitcher_z_levels.emit)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.checkbox_stitchOutput.toggled.connect(self.display_stitcher_widget)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        # self.combobox_z_stack.currentIndexChanged.connect(self.signal_z_stacking.emit)

        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

        self.btn_add.clicked.connect(self.add_location)
        self.btn_remove.clicked.connect(self.remove_location)
        self.btn_previous.clicked.connect(self.previous)
        self.btn_next.clicked.connect(self.next)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_load_last_executed.clicked.connect(self.load_last_used_locations)
        self.btn_export_locations.clicked.connect(self.export_location_list)
        self.btn_import_locations.clicked.connect(self.import_location_list)

        self.table_location_list.cellClicked.connect(self.cell_was_clicked)
        self.table_location_list.cellChanged.connect(self.cell_was_changed)
        self.btn_show_table_location_list.clicked.connect(self.table_location_list.show)
        self.dropdown_location_list.currentIndexChanged.connect(self.go_to)

        self.shortcut = QShortcut(QKeySequence(";"), self)
        self.shortcut.activated.connect(self.btn_add.click)

        self.toggle_z_range_controls(False)
        self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())

    def setup_layout(self):
        self.grid = QVBoxLayout()
        self.grid.addLayout(self.grid_line0)
        self.grid.addLayout(self.grid_location_list_line1)
        self.grid.addLayout(self.grid_location_list_line2)
        self.grid.addLayout(self.grid_location_list_line3)
        self.grid.addLayout(self.grid_acquisition)
        self.grid.addLayout(self.row_progress_layout)
        self.setLayout(self.grid)

    def toggle_z_range_controls(self, state):
        is_visible = bool(state)

        # Hide/show widgets in z_min_layout
        for i in range(self.z_min_layout.count()):
            widget = self.z_min_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)
            widget = self.z_max_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)

        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)

        if not is_visible:
            try:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
            except:
                pass
            # When Z-range is not specified, set Z-min and Z-max to current Z position
            current_z = self.stage.get_pos().z_mm * 1000
            self.entry_minZ.setValue(current_z)
            self.entry_maxZ.setValue(current_z)
        else:
            self.entry_minZ.valueChanged.connect(self.update_z_max)
            self.entry_maxZ.valueChanged.connect(self.update_z_min)
            self.entry_minZ.valueChanged.connect(self.update_Nz)
            self.entry_maxZ.valueChanged.connect(self.update_Nz)
            self.entry_deltaZ.valueChanged.connect(self.update_Nz)

        # Update the layout
        self.grid.update()
        self.updateGeometry()
        self.update()

    def init_z(self, z_pos_mm=None):
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        print("init z-level flexible:", self.entry_minZ.value())

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def update_region_progress(self, current_fov, num_fovs):
        self._log.debug(f"Updating region progress for {current_fov=}, {num_fovs=}")
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self._log.debug(
            f"updating acquisition progress for {current_region=}, {num_regions=}, {current_time_point=}..."
        )
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")

        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def update_fov_positions(self):
        if self.parent.recordTabWidget.currentWidget() != self:
            return
        if self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

        for i, (x, y, z) in enumerate(self.location_list):
            region_id = self.location_ids[i]
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def display_stitcher_widget(self, checked):
        self.signal_stitcher_widget.emit(checked)

    def toggle_acquisition(self, pressed):
        self._log.debug(f"FlexibleMultiPointWidget.toggle_acquisition, {pressed=}")
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please choose base saving directory first")
            return
        if not self.list_configurations.selectedItems():  # no channel selected
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please select at least one imaging channel first")
            return
        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            # add the current location to the location list if the list is empty
            if len(self.location_list) == 0:
                self.add_location()
                self.acquisition_in_place = True

            if self.checkbox_set_z_range.isChecked():
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000
                maxZ = self.entry_maxZ.value() / 1000
                self.multipointController.set_z_range(minZ, maxZ)
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz / 1000 * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
            else:
                self.multipointController.set_focus_map(None)

            # Set acquisition parameters
            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_base_path(self.lineEdit_savingDir.text())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if not check_space_available_with_error_dialog(self.multipointController, self._log):
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                self.btn_startAcquisition.setChecked(False)
                return

            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.is_current_acquisition_widget = True  # keep track of what widget started the acquisition
            self.setEnabled_all(False)

            # emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start coordinate-based acquisition
            self.multipointController.run_acquisition()
        else:
            # This must eventually propagate through and call out acquisition_finished.
            self.multipointController.request_abort_aquisition()

    def load_last_used_locations(self):
        if self.last_used_locations is None or len(self.last_used_locations) == 0:
            return
        self.clear_only_location_list()

        for row, row_ind in zip(self.last_used_locations, self.last_used_location_ids):
            x = row[0]
            y = row[1]
            z = row[2]
            name = row_ind[0]
            if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                location_str = (
                    "x:" + str(round(x, 3)) + "mm  y:" + str(round(y, 3)) + "mm  z:" + str(round(1000 * z, 1)) + "μm"
                )
                self.dropdown_location_list.addItem(location_str)
                self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                self.location_ids = np.append(self.location_ids, name)
                self.table_location_list.insertRow(self.table_location_list.rowCount())
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(z * 1000, 1)))
                )
                self.table_location_list.setItem(self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(name))
                index = self.dropdown_location_list.count() - 1
                self.dropdown_location_list.setCurrentIndex(index)
                print(self.location_list)
            else:
                print("Duplicate values not added based on x and y.")
                # to-do: update z coordinate

    def add_location(self):
        # Get raw positions without rounding
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm
        z = pos.z_mm
        region_id = f"R{len(self.location_ids)}"

        # Check for duplicates using rounded values for comparison
        if not np.any(np.all(self.location_list[:, :2] == [round(x, 3), round(y, 3)], axis=1)):
            # Block signals to prevent triggering cell_was_changed
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Store actual values in location_list
            self.location_list = np.vstack((self.location_list, [[x, y, z]]))
            self.location_ids = np.append(self.location_ids, region_id)

            # Update both UI elements at the same time
            location_str = f"x:{round(x,3)} mm  y:{round(y,3)} mm  z:{round(z*1000,1)} μm"
            self.dropdown_location_list.addItem(location_str)
            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(row, 2, QTableWidgetItem(str(round(z * 1000, 1))))
            self.table_location_list.setItem(row, 3, QTableWidgetItem(region_id))

            # Store actual values in region coordinates
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

            # Set the current index to the newly added location
            self.dropdown_location_list.setCurrentIndex(len(self.location_ids) - 1)
            self.table_location_list.selectRow(row)

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            print(f"Added Region: {region_id} - x={x}, y={y}, z={z}")
        else:
            print("Invalid Region: Duplicate Location")

    def remove_location(self):
        index = self.dropdown_location_list.currentIndex()
        if index >= 0:
            # Remove region ID and associated data
            region_id = self.location_ids[index]
            print(f"Removing region: {region_id}")

            # Block signals to prevent unintended UI updates
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Remove from data structures
            self.location_list = np.delete(self.location_list, index, axis=0)
            self.location_ids = np.delete(self.location_ids, index)

            # Remove from both UI elements
            self.dropdown_location_list.removeItem(index)
            self.table_location_list.removeRow(index)

            # Remove scanCoordinates dictionaries and remove region overlay
            self.scanCoordinates.region_centers.pop(region_id, None)
            for coord in self.scanCoordinates.region_fov_coordinates.pop(region_id, []):
                self.navigationViewer.deregister_fov_to_image(coord[0], coord[1])

            # Reindex remaining regions and update UI
            for i in range(index, len(self.location_ids)):
                old_id = self.location_ids[i]
                new_id = f"R{i}"
                self.location_ids[i] = new_id

                # Update dictionaries
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(old_id, None)
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    old_id, []
                )

                # Update UI with new ID and coordinates
                x, y, z = self.location_list[i]
                location_str = f"x:{round(x, 3)} mm  y:{round(y, 3)} mm  z:{round(z * 1000, 1)} μm"
                self.dropdown_location_list.setItemText(i, location_str)
                self.table_location_list.setItem(i, 3, QTableWidgetItem(new_id))

            # Clear overlay if no locations remain
            if len(self.location_list) == 0:
                self.navigationViewer.clear_overlay()

            print(f"Remaining location IDs: {self.location_ids}")
            for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                for coord in fov_coords:
                    self.navigationViewer.register_fov_to_image(coord[0], coord[1])

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)

    # def create_point_id(self):
    #     self.scanCoordinates.get_selected_wells()
    #     if len(self.scanCoordinates.region_centers.keys()) == 0:
    #         print('Select a well first.')
    #         return None

    #     name = self.scanCoordinates.region_centers.keys()[0]
    #     location_split_names = [int(x.split('-')[1]) for x in self.location_ids if x.split('-')[0] == name]
    #     if len(location_split_names) > 0:
    #         new_id = f'{name}-{np.max(location_split_names)+1}'
    #     else:
    #         new_id = f'{name}-0'
    #     return new_id

    def next(self):
        index = self.dropdown_location_list.currentIndex()
        # max_index = self.dropdown_location_list.count() - 1
        # index = min(index + 1, max_index)
        num_regions = self.dropdown_location_list.count()
        if num_regions <= 0:
            self._log.error("Cannot move to next location, because there are no locations in the list")
            return

        index = (index + 1) % num_regions
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)

    def previous(self):
        index = self.dropdown_location_list.currentIndex()
        index = max(index - 1, 0)
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)

    def clear(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.scanCoordinates.clear_regions()
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)
        self.navigationViewer.clear_overlay()

        self._log.info("Cleared all locations and overlays.")

    def clear_only_location_list(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype=str)
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)

    def go_to(self, index):
        if index != -1:
            if index < len(self.location_list):  # to avoid giving errors when adding new points
                x = self.location_list[index, 0]
                y = self.location_list[index, 1]
                z = self.location_list[index, 2]
                self.stage.move_x_to(x)
                self.stage.move_y_to(y)
                self.stage.move_z_to(z)
                self.table_location_list.selectRow(index)

    def cell_was_clicked(self, row, column):
        self.dropdown_location_list.setCurrentIndex(row)

    def cell_was_changed(self, row, column):
        # Get region ID
        region_id = self.location_ids[row]

        # Clear all FOVs for this region
        if region_id in self.scanCoordinates.region_fov_coordinates.keys():
            for coord in self.scanCoordinates.region_fov_coordinates[region_id]:
                self.navigationViewer.deregister_fov_to_image(coord[0], coord[1])

        # Handle the changed value
        val_edit = self.table_location_list.item(row, column).text()

        if column < 2:  # X or Y coordinate changed
            self.location_list[row, column] = float(val_edit)
            x, y, z = self.location_list[row]

            # Update region coordinates and FOVs for new position
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

        elif column == 2:  # Z coordinate changed
            z = float(val_edit) / 1000
            self.location_list[row, 2] = z
            self.scanCoordinates.region_centers[region_id][2] = z
        else:  # ID changed
            new_id = val_edit
            self.location_ids[row] = new_id
            # Update dictionary keys
            if region_id in self.scanCoordinates.region_centers:
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(region_id)
            if region_id in self.scanCoordinates.region_fov_coordinates:
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    region_id
                )

        # Update UI
        location_str = f"x:{round(self.location_list[row,0],3)} mm  y:{round(self.location_list[row,1],3)} mm  z:{round(1000*self.location_list[row,2],3)} μm"
        self.dropdown_location_list.setItemText(row, location_str)
        self.go_to(row)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_A and event.modifiers() == Qt.ControlModifier:
            self.add_location()
        else:
            super().keyPressEvent(event)

    def update_location_z_level(self, index, z_mm):
        self.table_location_list.blockSignals(True)
        self.dropdown_location_list.blockSignals(True)

        self.location_list[index, 2] = z_mm
        location_str = (
            "x:"
            + str(round(self.location_list[index, 0], 3))
            + "mm  y:"
            + str(round(self.location_list[index, 1], 3))
            + "mm  z:"
            + str(round(1000 * z_mm, 1))
            + "μm"
        )
        self.dropdown_location_list.setItemText(index, location_str)
        if self.table_location_list.rowCount() > index:
            self.table_location_list.setItem(index, 2, QTableWidgetItem(str(round(1000 * z_mm, 1))))

        self.table_location_list.blockSignals(False)
        self.dropdown_location_list.blockSignals(False)

    def export_location_list(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.DataFrame(self.location_list, columns=["x (mm)", "y (mm)", "z (um)"])
            location_list_df["ID"] = self.location_ids
            location_list_df["i"] = 0
            location_list_df["j"] = 0
            location_list_df["k"] = 0
            location_list_df.to_csv(file_path, index=False, header=True)

    def import_location_list(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.read_csv(file_path)
            location_list_df_relevant = None
            try:
                location_list_df_relevant = location_list_df[["x (mm)", "y (mm)", "z (um)"]]
            except KeyError:
                self._log.error("Improperly formatted location list being imported")
                return
            if "ID" in location_list_df.columns:
                location_list_df_relevant["ID"] = location_list_df["ID"].astype(str)
            else:
                location_list_df_relevant["ID"] = "None"
            self.clear_only_location_list()
            for index, row in location_list_df_relevant.iterrows():
                x = row["x (mm)"]
                y = row["y (mm)"]
                z = row["z (um)"]
                region_id = row["ID"]
                if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                    location_str = (
                        "x:"
                        + str(round(x, 3))
                        + "mm  y:"
                        + str(round(y, 3))
                        + "mm  z:"
                        + str(round(1000 * z, 1))
                        + "μm"
                    )
                    self.dropdown_location_list.addItem(location_str)
                    index = self.dropdown_location_list.count() - 1
                    self.dropdown_location_list.setCurrentIndex(index)
                    self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                    self.location_ids = np.append(self.location_ids, region_id)
                    self.table_location_list.insertRow(self.table_location_list.rowCount())
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(1000 * z, 1)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(region_id)
                    )
                    if self.use_overlap:
                        self.scanCoordinates.add_flexible_region(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            overlap_percent=self.entry_overlap.value(),
                        )
                    else:
                        self.scanCoordinates.add_flexible_region_with_step_size(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            self.entry_deltaX.value(),
                            self.entry_deltaY.value(),
                        )
                else:
                    self._log.warning("Duplicate values not added based on x and y.")
            self._log.debug(self.location_list)

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)

        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def acquisition_is_finished(self):
        self._log.debug(
            f"In FlexibleMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )

        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        if not self.acquisition_in_place:
            self.last_used_locations = self.location_list.copy()
            self.last_used_location_ids = self.location_ids.copy()
        else:
            self.clear_only_location_list()
            self.acquisition_in_place = False

        self.signal_acquisition_started.emit(False)
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)
        self.is_current_acquisition_widget = False

    def setEnabled_all(self, enabled, exclude_btn_startAcquisition=True):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.entry_NX.setEnabled(enabled)
        self.entry_NY.setEnabled(enabled)
        self.entry_deltaZ.setEnabled(enabled)
        self.entry_NZ.setEnabled(enabled)
        self.entry_dt.setEnabled(enabled)
        self.entry_Nt.setEnabled(enabled)
        if not self.use_overlap:
            self.entry_deltaX.setEnabled(enabled)
            self.entry_deltaY.setEnabled(enabled)
        else:
            self.entry_overlap.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_genAFMap.setEnabled(enabled)
        self.checkbox_useFocusMap.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        self.checkbox_stitchOutput.setEnabled(enabled)
        self.checkbox_set_z_range.setEnabled(enabled)

        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)


class WellplateMultiPointWidget(QFrame):

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz
    signal_stitcher_z_levels = Signal(int)  # live Nz
    signal_stitcher_widget = Signal(bool)  # start stitching
    signal_manual_shape_mode = Signal(bool)  # enable manual shape layer on mosaic display

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        focusMapWidget=None,
        napariMosaicWidget=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        if napariMosaicWidget is None:
            self.performance_mode = True
        else:
            self.napariMosaicWidget = napariMosaicWidget
            self.performance_mode = False
        self.base_path_is_set = False
        self.well_selected = False
        self.num_regions = 0
        self.acquisition_start_time = None
        self.manual_shape = None
        self.eta_seconds = 0
        self.is_current_acquisition_widget = False
        self.parent = self.multipointController.parent

        # TODO (hl): these along with update_live_coordinates need to move out of this class
        self._last_update_time = 0
        self._last_x_mm = None
        self._last_y_mm = None

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.set_default_scan_size()

        # Add state tracking for coordinates
        self.has_loaded_coordinates = False

    def add_components(self):
        self.entry_well_coverage = QDoubleSpinBox()
        self.entry_well_coverage.setRange(1, 999.99)
        self.entry_well_coverage.setValue(100)
        self.entry_well_coverage.setSuffix("%")
        self.entry_well_coverage.setDecimals(0)
        btn_width = self.entry_well_coverage.sizeHint().width()

        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.btn_setSavingDir.setFixedWidth(btn_width)

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Update scan size entry
        self.entry_scan_size = QDoubleSpinBox()
        self.entry_scan_size.setRange(0.1, 100)
        self.entry_scan_size.setValue(1)
        self.entry_scan_size.setSuffix(" mm")

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setValue(10)
        self.entry_overlap.setSuffix("%")
        self.entry_overlap.setFixedWidth(btn_width)

        # Add z-min and z-max entries
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to minimum
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_minZ_button = QPushButton("Set")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to maximum
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_maxZ_button = QPushButton("Set")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        # self.entry_deltaZ.setEnabled(False)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setEnabled(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(24 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(5000)
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])
        self.combobox_z_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Add a combo box for shape selection
        self.combobox_shape = QComboBox()
        if self.performance_mode:
            self.combobox_shape.addItems(["Square", "Circle", "Rectangle"])
        else:
            self.combobox_shape.addItems(["Square", "Circle", "Rectangle", "Manual"])
            self.combobox_shape.model().item(3).setEnabled(False)
        self.combobox_shape.setFixedWidth(btn_width)
        # self.combobox_shape.currentTextChanged.connect(self.on_shape_changed)

        self.btn_save_scan_coordinates = QPushButton("Save Coordinates")
        self.btn_load_scan_coordinates = QPushButton("Load Coordinates")

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_set_z_range = QCheckBox("Set Z-range")
        self.checkbox_set_z_range.toggled.connect(self.toggle_z_range_controls)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        #  Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        row_1_layout = QHBoxLayout()
        row_1_layout.addWidget(QLabel("Experiment ID"))
        row_1_layout.addWidget(self.lineEdit_experimentID)
        main_layout.addLayout(row_1_layout)

        # Scan Shape, FOV overlap, and Save / Load Scan Coordinates
        row_2_layout = QGridLayout()
        row_2_layout.addWidget(QLabel("Scan Shape"), 0, 0)
        row_2_layout.addWidget(self.combobox_shape, 0, 1)
        row_2_layout.addWidget(QLabel("Scan Size"), 0, 2)
        row_2_layout.addWidget(self.entry_scan_size, 0, 3)
        row_2_layout.addWidget(QLabel("Coverage"), 0, 4)
        row_2_layout.addWidget(self.entry_well_coverage, 0, 5)
        row_2_layout.addWidget(QLabel("FOV Overlap"), 1, 0)
        row_2_layout.addWidget(self.entry_overlap, 1, 1)
        row_2_layout.addWidget(self.btn_save_scan_coordinates, 1, 2, 1, 2)
        row_2_layout.addWidget(self.btn_load_scan_coordinates, 1, 4, 1, 2)
        main_layout.addLayout(row_2_layout)

        grid = QGridLayout()

        # dz and Nz
        dz_layout = QHBoxLayout()
        dz_layout.addWidget(QLabel("dz"))
        dz_layout.addWidget(self.entry_deltaZ)
        dz_layout.addWidget(QLabel("Nz"))
        dz_layout.addWidget(self.entry_NZ)
        grid.addLayout(dz_layout, 0, 0)

        # dt and Nt
        dt_layout = QHBoxLayout()
        dt_layout.addWidget(QLabel("dt"))
        dt_layout.addWidget(self.entry_dt)
        dt_layout.addWidget(QLabel("Nt"))
        dt_layout.addWidget(self.entry_Nt)
        grid.addLayout(dt_layout, 0, 2)

        # Z-min
        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.set_minZ_button)
        min_label = QLabel("Z-min")
        min_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        self.z_min_layout.addWidget(min_label)
        self.z_min_layout.addWidget(self.entry_minZ)
        grid.addLayout(self.z_min_layout, 1, 0)

        # Z-max
        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addWidget(self.set_maxZ_button)
        max_label = QLabel("Z-max")
        max_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        self.z_max_layout.addWidget(max_label)
        self.z_max_layout.addWidget(self.entry_maxZ)
        grid.addLayout(self.z_max_layout, 1, 2)

        w = max(min_label.sizeHint().width(), max_label.sizeHint().width())
        min_label.setFixedWidth(w)
        max_label.setFixedWidth(w)

        # Configuration list
        grid.addWidget(self.list_configurations, 2, 0)

        # Options and Start button
        options_layout = QVBoxLayout()
        options_layout.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        # options_layout.addWidget(self.checkbox_genAFMap)  # We are not using AF map now
        options_layout.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)
        options_layout.addWidget(self.checkbox_set_z_range)
        if ENABLE_STITCHER:
            options_layout.addWidget(self.checkbox_stitchOutput)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        bottom_right = QHBoxLayout()
        bottom_right.addLayout(options_layout)
        bottom_right.addSpacing(2)
        bottom_right.addLayout(button_layout)

        grid.addLayout(bottom_right, 2, 2)
        spacer_widget = QWidget()
        spacer_widget.setFixedWidth(2)
        grid.addWidget(spacer_widget, 0, 1)

        # Set column stretches
        grid.setColumnStretch(0, 1)  # Middle spacer
        grid.setColumnStretch(1, 0)  # Middle spacer
        grid.setColumnStretch(2, 1)  # Middle spacer

        main_layout.addLayout(grid)
        # Row 5: Progress Bar
        row_progress_layout = QHBoxLayout()
        row_progress_layout.addWidget(self.progress_label)
        row_progress_layout.addWidget(self.progress_bar)
        row_progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(row_progress_layout)
        self.toggle_z_range_controls(self.checkbox_set_z_range.isChecked())

        # Connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.entry_overlap.valueChanged.connect(self.update_coordinates)
        self.entry_scan_size.valueChanged.connect(self.update_coordinates)
        self.entry_scan_size.valueChanged.connect(self.update_coverage_from_scan_size)
        self.entry_well_coverage.valueChanged.connect(self.update_scan_size_from_coverage)
        self.combobox_shape.currentTextChanged.connect(self.reset_coordinates)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_useFocusMap.toggled.connect(self.multipointController.set_manual_focus_map_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.checkbox_stitchOutput.toggled.connect(self.display_stitcher_widget)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)
        if not self.performance_mode:
            self.napariMosaicWidget.signal_layers_initialized.connect(self.enable_manual_ROI)
        self.entry_NZ.valueChanged.connect(self.signal_stitcher_z_levels.emit)
        # self.combobox_z_stack.currentIndexChanged.connect(self.signal_z_stacking.emit)

        # Connect save/clear coordinates button
        self.btn_save_scan_coordinates.clicked.connect(self.on_save_or_clear_coordinates_clicked)
        self.btn_load_scan_coordinates.clicked.connect(self.on_load_coordinates_clicked)

    def enable_manual_ROI(self, enable):
        self.combobox_shape.model().item(3).setEnabled(enable)
        if not enable:
            self.set_default_shape()

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def toggle_z_range_controls(self, is_visible):
        # Efficiently set visibility for all widgets in both layouts
        for layout in (self.z_min_layout, self.z_max_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(is_visible)

        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)
        current_z = self.stage.get_pos().z_mm * 1000
        self.entry_minZ.setValue(current_z)
        self.entry_maxZ.setValue(current_z)

        # Safely connect or disconnect signals
        try:
            if is_visible:
                self.entry_minZ.valueChanged.connect(self.update_z_max)
                self.entry_maxZ.valueChanged.connect(self.update_z_min)
                self.entry_minZ.valueChanged.connect(self.update_Nz)
                self.entry_maxZ.valueChanged.connect(self.update_Nz)
                self.entry_deltaZ.valueChanged.connect(self.update_Nz)
            else:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
        except TypeError:
            # Handle case where signals might not be connected/disconnected
            pass

        # Update the layout
        self.updateGeometry()
        self.update()

    def set_default_scan_size(self):
        print("Sample Format:", self.navigationViewer.sample)
        self.combobox_shape.blockSignals(True)
        self.entry_well_coverage.blockSignals(True)
        self.entry_scan_size.blockSignals(True)

        self.set_default_shape()

        if "glass slide" in self.navigationViewer.sample:
            self.entry_scan_size.setValue(
                0.1
            )  # init to 0.1mm when switching to 'glass slide' (for imaging a single FOV by default)
            self.entry_scan_size.setEnabled(True)
            self.entry_well_coverage.setEnabled(False)
        else:
            self.entry_well_coverage.setEnabled(True)
            # entry_well_coverage.valueChanged signal will not emit coverage = 100 already
            self.entry_well_coverage.setValue(100)
            self.update_scan_size_from_coverage()

        self.update_coordinates()

        self.combobox_shape.blockSignals(False)
        self.entry_well_coverage.blockSignals(False)
        self.entry_scan_size.blockSignals(False)

    def set_default_shape(self):
        if self.scanCoordinates.format in ["384 well plate", "1536 well plate"]:
            self.combobox_shape.setCurrentText("Square")
        # elif self.scanCoordinates.format in ["4 slide"]:
        #     self.combobox_shape.setCurrentText("Rectangle")
        elif self.scanCoordinates.format != 0:
            self.combobox_shape.setCurrentText("Circle")

    def get_effective_well_size(self):
        well_size = self.scanCoordinates.well_size_mm
        if self.combobox_shape.currentText() == "Circle":
            pixel_size_um = (
                self.objectiveStore.get_pixel_size_factor() * self.navigationViewer.camera_sensor_pixel_size_um
            )
            # TODO: In the future software cropping size may be changed when program is running,
            # so we may want to use the crop_width from the camera object here.
            fov_size_mm = (pixel_size_um / 1000) * CAMERA_CONFIG.CROP_WIDTH_UNBINNED
            return well_size + fov_size_mm * (1 + math.sqrt(2))
        return well_size

    def reset_coordinates(self):
        shape = self.combobox_shape.currentText()
        if shape == "Manual":
            self.signal_manual_shape_mode.emit(True)
        else:
            self.signal_manual_shape_mode.emit(False)
            self.update_coverage_from_scan_size()
            self.update_coordinates()

    def update_manual_shape(self, shapes_data_mm):
        if self.parent.recordTabWidget.currentWidget() != self:
            return

        if shapes_data_mm and len(shapes_data_mm) > 0:
            self.shapes_mm = shapes_data_mm
            print(f"Manual ROIs updated with {len(self.shapes_mm)} shapes")
        else:
            self.shapes_mm = None
            print("No valid shapes found, cleared manual ROIs")
        self.update_coordinates()

    def convert_pixel_to_mm(self, pixel_coords):
        # Convert pixel coordinates to millimeter coordinates
        mm_coords = pixel_coords * self.napariMosaicWidget.viewer_pixel_size_mm
        mm_coords += np.array(
            [self.napariMosaicWidget.top_left_coordinate[1], self.napariMosaicWidget.top_left_coordinate[0]]
        )
        return mm_coords

    def update_coverage_from_scan_size(self):
        if "glass slide" not in self.navigationViewer.sample:
            effective_well_size = self.get_effective_well_size()
            scan_size = self.entry_scan_size.value()
            coverage = round((scan_size / effective_well_size) * 100, 2)
            self.entry_well_coverage.blockSignals(True)
            self.entry_well_coverage.setValue(coverage)
            self.entry_well_coverage.blockSignals(False)
            print("COVERAGE", coverage)

    def update_scan_size_from_coverage(self):
        effective_well_size = self.get_effective_well_size()
        coverage = self.entry_well_coverage.value()
        scan_size = round((coverage / 100) * effective_well_size, 3)
        self.entry_scan_size.setValue(scan_size)
        print("SIZE", scan_size)

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def init_z(self, z_pos_mm=None):
        # sets initial z range form the current z position used after startup of the GUI
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        print("init z-level wellplate:", self.entry_minZ.value())

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def update_coordinates(self):
        if hasattr(self.parent, "recordTabWidget") and self.parent.recordTabWidget.currentWidget() != self:
            return
        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()

        if shape == "Manual":
            self.scanCoordinates.set_manual_coordinates(self.shapes_mm, overlap_percent)

        elif "glass slide" in self.navigationViewer.sample:
            pos = self.stage.get_pos()
            self.scanCoordinates.set_live_scan_coordinates(pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape)
        else:
            if self.scanCoordinates.has_regions():
                self.scanCoordinates.clear_regions()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)

    def update_well_coordinates(self, selected):
        if self.parent.recordTabWidget.currentWidget() != self:
            return
        if selected:
            scan_size_mm = self.entry_scan_size.value()
            overlap_percent = self.entry_overlap.value()
            shape = self.combobox_shape.currentText()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)
        elif self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

    def update_live_coordinates(self, pos: squid.abc.Pos):
        if hasattr(self.parent, "recordTabWidget") and self.parent.recordTabWidget.currentWidget() != self:
            return
        # Don't update scan coordinates if we're navigating focus points. A temporary fix for focus map with glass slide.
        # This disables updating scanning grid when focus map is checked
        if self.focusMapWidget is not None and self.focusMapWidget.enabled:
            return
        x_mm = pos.x_mm
        y_mm = pos.y_mm
        # Check if x_mm or y_mm has changed
        position_changed = (x_mm != self._last_x_mm) or (y_mm != self._last_y_mm)
        if not position_changed or time.time() - self._last_update_time < 0.5:
            return
        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()
        self.scanCoordinates.set_live_scan_coordinates(x_mm, y_mm, scan_size_mm, overlap_percent, shape)
        self._last_update_time = time.time()
        self._last_x_mm = x_mm
        self._last_y_mm = y_mm

    def toggle_acquisition(self, pressed):
        self._log.debug(f"WellplateMultiPointWidget.toggle_acquisition, {pressed=}")
        if not self.base_path_is_set:
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
            return

        if not self.list_configurations.selectedItems():
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            scan_size_mm = self.entry_scan_size.value()
            overlap_percent = self.entry_overlap.value()
            shape = self.combobox_shape.currentText()

            self.scanCoordinates.sort_coordinates()
            if len(self.scanCoordinates.region_centers) == 0:
                # Use current location if no regions added #TODO FIX
                pos = self.stage.get_pos()
                x = pos.x_mm
                y = pos.y_mm
                z = pos.z_mm
                self.scanCoordinates.add_region("current", x, y, scan_size_mm, overlap_percent, shape)

            # Calculate total number of positions for signal emission # not needed ever
            total_positions = sum(len(coords) for coords in self.scanCoordinates.region_fov_coordinates.values())
            Nx = Ny = int(math.sqrt(total_positions))
            dx_mm = dy_mm = scan_size_mm / (Nx - 1) if Nx > 1 else scan_size_mm

            if self.checkbox_set_z_range.isChecked():
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000  # Convert from μm to mm
                maxZ = self.entry_maxZ.value() / 1000  # Convert from μm to mm
                self.multipointController.set_z_range(minZ, maxZ)
                print("set z-range", (minZ, maxZ))
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                # Try to fit the surface
                if self.focusMapWidget.fit_surface():
                    # If fit successful, set the surface fitter in controller
                    self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                else:
                    QMessageBox.warning(self, "Warning", "Failed to fit focus surface")
                    self.btn_startAcquisition.setChecked(False)
                    return
            else:
                # If checkbox not checked, set surface fitter to None
                self.multipointController.set_focus_map(None)

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if not check_space_available_with_error_dialog(self.multipointController, self._log):
                self.btn_startAcquisition.setChecked(False)
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()

        else:
            # This must eventually propagate through and call our aquisition_is_finished, or else we'll be left
            # in an odd state.
            self.multipointController.request_abort_aquisition()

    def acquisition_is_finished(self):
        self._log.debug(
            f"In WellMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.disable_updating_focus_points_on_signal()
        self.reset_coordinates()
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.update_focus_point_display()
            self.focusMapWidget.enable_updating_focus_points_on_signal()
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled):
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

            if self.scanCoordinates.format == "glass slide":
                self.entry_well_coverage.setEnabled(False)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)
        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def display_stitcher_widget(self, checked):
        self.signal_stitcher_widget.emit(checked)

    def toggle_coordinate_controls(self, has_coordinates: bool):
        """Toggle button text and control states based on whether coordinates are loaded"""
        if has_coordinates:
            self.btn_save_scan_coordinates.setText("Clear Coordinates")
            # Disable scan controls when coordinates are loaded
            self.combobox_shape.setEnabled(False)
            self.entry_scan_size.setEnabled(False)
            self.entry_well_coverage.setEnabled(False)
            self.entry_overlap.setEnabled(False)
            # Disable well selector
            self.parent.wellSelectionWidget.setEnabled(False)
        else:
            self.btn_save_scan_coordinates.setText("Save Coordinates")
            # Re-enable scan controls when coordinates are cleared
            self.combobox_shape.setEnabled(True)
            self.entry_scan_size.setEnabled(True)
            if "glass slide" in self.navigationViewer.sample:
                self.entry_well_coverage.setEnabled(False)
            else:
                self.entry_well_coverage.setEnabled(True)
            self.entry_overlap.setEnabled(True)
            # Re-enable well selector
            self.parent.wellSelectionWidget.setEnabled(True)

        self.has_loaded_coordinates = has_coordinates

    def on_save_or_clear_coordinates_clicked(self):
        """Handle save/clear coordinates button click"""
        if self.has_loaded_coordinates:
            # Clear coordinates
            self.scanCoordinates.clear_regions()
            self.toggle_coordinate_controls(has_coordinates=False)
            # Update display/coordinates as needed
            self.update_coordinates()
        else:
            # Save coordinates (existing save functionality)
            self.save_coordinates()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"  # Default directory
        )

        if file_path:
            print("loading coordinates from", file_path)
            self.load_coordinates(file_path)

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV

            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["Region", "X_mm", "Y_mm"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'Region', 'X_mm', and 'Y_mm' columns")

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["Region"].unique():
                region_points = df[df["Region"] == region_id]
                coords = list(zip(region_points["X_mm"], region_points["Y_mm"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["X_mm"].mean()
                center_y = region_points["Y_mm"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                for x, y in coords:
                    self.navigationViewer.register_fov_to_image(x, y)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

            # Update UI state
            self.toggle_coordinate_controls(has_coordinates=True)

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def save_coordinates(self):
        """Save scan coordinates to a CSV file.

        Opens a file dialog for the user to choose save location and filename.
        Coordinates are saved in CSV format with headers.
        """
        # Open file dialog for user to select save location and filename
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"  # Default directory
        )

        if file_path:
            base_path, extension = os.path.splitext(file_path)
            if not extension:
                extension = ".csv"

            current_objective = self.objectiveStore.current_objective

            def _helper_save_coordinates(self, file_path: str):
                # Get coordinates from scanCoordinates
                coordinates = []
                for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                    for x, y in fov_coords:
                        coordinates.append([region_id, x, y])

                # Save to CSV with headers

                df = pd.DataFrame(coordinates, columns=["Region", "X_mm", "Y_mm"])
                df.to_csv(file_path, index=False)

                self._log.info(f"Saved scan coordinates to {file_path}")

            try:
                for objective_name in self.objectiveStore.objectives_dict.keys():
                    if objective_name == current_objective:
                        continue
                    else:
                        self.objectiveStore.set_current_objective(objective_name)
                        self.update_coordinates()
                        obj_file_path = f"{base_path}_{objective_name}{extension}"
                        _helper_save_coordinates(self, obj_file_path)

                self.objectiveStore.set_current_objective(current_objective)
                self.update_coordinates()
                obj_file_path = f"{base_path}_{current_objective}{extension}"
                _helper_save_coordinates(self, obj_file_path)

            except Exception as e:
                self._log.error(f"Failed to save coordinates: {str(e)}")
                QMessageBox.warning(self, "Save Error", f"Failed to save coordinates to {file_path}\nError: {str(e)}")


class MultiPointWithFluidicsWidget(QFrame):
    """A simplified version of WellplateMultiPointWidget for use with fluidics"""

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        napariMosaicWidget=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        if napariMosaicWidget is None:
            self.performance_mode = True
        else:
            self.napariMosaicWidget = napariMosaicWidget
            self.performance_mode = False

        self.base_path_is_set = False
        self.acquisition_start_time = None
        self.eta_seconds = 0
        self.nRound = 0
        self.is_current_acquisition_widget = False
        self.parent = self.multipointController.parent

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Z-stack controls
        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)

        # Channel configurations
        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Reflection AF checkbox
        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        # Piezo checkbox
        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        # Start acquisition button
        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setEnabled(False)

        # Progress indicators
        self.progress_label = QLabel("Round -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Layout setup
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        exp_id_layout = QHBoxLayout()
        exp_id_layout.addWidget(QLabel("Experiment ID"))
        exp_id_layout.addWidget(self.lineEdit_experimentID)

        self.btn_load_coordinates = QPushButton("Load Coordinates")
        exp_id_layout.addWidget(self.btn_load_coordinates)

        self.btn_init_fluidics = QPushButton("Init Fluidics")
        # exp_id_layout.addWidget(self.btn_init_fluidics)

        main_layout.addLayout(exp_id_layout)

        # Z-stack controls
        z_stack_layout = QHBoxLayout()
        z_stack_layout.addWidget(QLabel("dz"))
        z_stack_layout.addWidget(self.entry_deltaZ)
        z_stack_layout.addWidget(QLabel("Nz"))
        z_stack_layout.addWidget(self.entry_NZ)

        # Rounds input
        z_stack_layout.addWidget(QLabel("Fluidics Rounds:"))
        self.entry_rounds = QLineEdit()
        z_stack_layout.addWidget(self.entry_rounds)

        main_layout.addLayout(z_stack_layout)

        # Grid layout for channel list and options
        grid = QGridLayout()

        # Channel configurations on left
        grid.addWidget(self.list_configurations, 0, 0)

        # Options layout
        options_layout = QVBoxLayout()
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)

        grid.addLayout(options_layout, 0, 2)

        # Start button on far right
        grid.addWidget(self.btn_startAcquisition, 0, 4)

        # Add spacers between columns
        spacer_widget1 = QWidget()
        spacer_widget1.setFixedWidth(2)
        grid.addWidget(spacer_widget1, 0, 1)

        spacer_widget2 = QWidget()
        spacer_widget2.setFixedWidth(2)
        grid.addWidget(spacer_widget2, 0, 3)

        # Set column stretches
        grid.setColumnStretch(0, 2)  # Channel list - half width
        grid.setColumnStretch(1, 0)  # First spacer
        grid.setColumnStretch(2, 1)  # Options
        grid.setColumnStretch(3, 0)  # Second spacer
        grid.setColumnStretch(4, 1)  # Start button

        main_layout.addLayout(grid)

        # Progress bar layout
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(progress_layout)

        # Connect signals
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.btn_load_coordinates.clicked.connect(self.on_load_coordinates_clicked)
        # self.btn_init_fluidics.clicked.connect(self.init_fluidics)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

    # The following methods are copied from WellplateMultiPointWidget with minimal modifications
    def toggle_acquisition(self, pressed):
        rounds = self.get_rounds()
        if pressed:
            if not self.base_path_is_set:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
                return

            if not self.list_configurations.selectedItems():
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
                return

            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            if not rounds:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please enter valid round numbers (1-24)")
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_use_fluidics(True)  # may be set to False from other widgets
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.set_Nt(len(rounds))
            self.multipointController.fluidics.set_rounds(rounds)
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()
        else:
            self.multipointController.request_abort_aquisition()

    def set_saving_dir(self):
        """Open dialog to set saving directory"""
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_deltaZ(self, value):
        """Set Z-stack step size, adjusting for piezo if needed"""
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        """Emit signal with list of selected channel names"""
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def acquisition_is_finished(self):
        """Handle acquisition completion"""
        self._log.debug(
            f"In MultiPointWithFluidicsWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled):
        """Enable/disable all widget controls"""
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.nRound

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = remaining_fovs / fov_per_second if fov_per_second > 0 else 0
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.nRound > 1:
            progress_parts.append(f"Round {current_time_point + 1}/{self.nRound}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        """Update the estimated time remaining display"""
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        """Show/hide progress tracking widgets"""
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Round 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            print("loading coordinates from", file_path)
            self.load_coordinates(file_path)

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV
            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["Region", "X_mm", "Y_mm"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'Region', 'X_mm', and 'Y_mm' columns")

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["Region"].unique():
                region_points = df[df["Region"] == region_id]
                coords = list(zip(region_points["X_mm"], region_points["Y_mm"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["X_mm"].mean()
                center_y = region_points["Y_mm"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                for x, y in coords:
                    self.navigationViewer.register_fov_to_image(x, y)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def init_fluidics(self):
        """Initialize the fluidics system"""
        # self.multipointController.fluidics.initialize()
        self.btn_startAcquisition.setEnabled(True)

    def get_rounds(self) -> list:
        """Parse rounds input string into a list of round numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            rounds_str = self.entry_rounds.text().strip()
            if not rounds_str:
                return []

            rounds = []

            # Split by comma and process each part
            for part in rounds_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 24 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 24, and start must be <= end"
                        )
                    rounds.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 24:
                        raise ValueError(f"Invalid number {num}: Must be between 1 and 24")
                    rounds.append(num)

            self.nRound = len(rounds)

            return rounds

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception as e:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid round numbers (e.g., '1-3,5,7-10')")
            return []


class FluidicsWidget(QWidget):

    log_message_signal = Signal(str)
    fluidics_initialized_signal = Signal()

    def __init__(self, fluidics, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)

        # Initialize data structures
        self.fluidics = fluidics
        self.fluidics.log_callback = self.log_message_signal.emit
        self.set_sequence_callbacks()

        # Set up the UI
        self.setup_ui()
        self.log_message_signal.connect(self.log_status)

    def setup_ui(self):
        # Main layout
        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # Left side - Control panels
        left_panel = QVBoxLayout()

        # Fluidics Control panel
        fluidics_control_group = QGroupBox("Fluidics Control")
        fluidics_control_layout = QVBoxLayout()

        # First row - Initialize and Load Sequences
        init_row = QHBoxLayout()
        self.btn_initialize = QPushButton("Initialize")
        self.btn_load_sequences = QPushButton("Load Sequences")
        init_row.addWidget(self.btn_initialize)
        init_row.addWidget(self.btn_load_sequences)
        fluidics_control_layout.addLayout(init_row)

        # Second row - Prime Ports
        prime_row = QHBoxLayout()
        prime_row.addWidget(QLabel("Prime Ports:"))
        prime_row.addWidget(QLabel("Ports"))
        self.txt_prime_ports = QLineEdit()
        prime_row.addWidget(self.txt_prime_ports)
        prime_row.addWidget(QLabel("Fill Tubing With"))
        self.prime_fill_combo = QComboBox()
        self.prime_fill_combo.addItems(self.fluidics.available_port_names)
        self.prime_fill_combo.setCurrentIndex(25 - 1)  # Usually Port 25 should be the common wash buffer port
        prime_row.addWidget(self.prime_fill_combo)
        prime_row.addWidget(QLabel("Volume (µL)"))
        self.txt_prime_volume = QLineEdit()
        self.txt_prime_volume.setText("2000")
        prime_row.addWidget(self.txt_prime_volume)
        self.btn_prime_start = QPushButton("Start")
        prime_row.addWidget(self.btn_prime_start)
        fluidics_control_layout.addLayout(prime_row)

        # Third row - Clean Up
        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(QLabel("Clean Up:"))
        cleanup_row.addWidget(QLabel("Ports"))
        self.txt_cleanup_ports = QLineEdit()
        cleanup_row.addWidget(self.txt_cleanup_ports)
        cleanup_row.addWidget(QLabel("Fill Tubing With"))
        self.cleanup_fill_combo = QComboBox()
        self.cleanup_fill_combo.addItems(self.fluidics.available_port_names)
        self.cleanup_fill_combo.setCurrentIndex(25 - 1)
        cleanup_row.addWidget(self.cleanup_fill_combo)
        cleanup_row.addWidget(QLabel("Volume (µL)"))
        self.txt_cleanup_volume = QLineEdit()
        self.txt_cleanup_volume.setText("2000")
        cleanup_row.addWidget(self.txt_cleanup_volume)
        cleanup_row.addWidget(QLabel("Repeat"))
        self.txt_cleanup_repeat = QLineEdit()
        self.txt_cleanup_repeat.setText("3")
        cleanup_row.addWidget(self.txt_cleanup_repeat)
        self.btn_cleanup_start = QPushButton("Start")
        cleanup_row.addWidget(self.btn_cleanup_start)
        fluidics_control_layout.addLayout(cleanup_row)

        fluidics_control_group.setLayout(fluidics_control_layout)
        left_panel.addWidget(fluidics_control_group)

        # Manual Control panel
        manual_control_group = QGroupBox("Manual Control")
        manual_control_layout = QVBoxLayout()

        # First row - Port, Flow Rate, Volume, Flow button
        manual_row1 = QHBoxLayout()
        manual_row1.addWidget(QLabel("Port"))
        self.manual_port_combo = QComboBox()
        self.manual_port_combo.addItems(self.fluidics.available_port_names)
        manual_row1.addWidget(self.manual_port_combo)
        manual_row1.addWidget(QLabel("Flow Rate (µL/min)"))
        self.txt_manual_flow_rate = QLineEdit()
        self.txt_manual_flow_rate.setText("500")
        manual_row1.addWidget(self.txt_manual_flow_rate)
        manual_row1.addWidget(QLabel("Volume (µL)"))
        self.txt_manual_volume = QLineEdit()
        manual_row1.addWidget(self.txt_manual_volume)
        self.btn_manual_flow = QPushButton("Flow")
        manual_row1.addWidget(self.btn_manual_flow)
        manual_control_layout.addLayout(manual_row1)

        # Second row - Empty Syringe Pump button
        manual_row2 = QHBoxLayout()
        self.btn_empty_syringe_pump = QPushButton("Empty Syringe Pump To Waste")
        manual_row2.addWidget(self.btn_empty_syringe_pump)
        manual_control_layout.addLayout(manual_row2)

        manual_control_group.setLayout(manual_control_layout)
        left_panel.addWidget(manual_control_group)

        # Status panel
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        status_layout.addWidget(self.status_text)

        status_group.setLayout(status_layout)
        left_panel.addWidget(status_group)

        # Add left panel to main layout
        main_layout.addLayout(left_panel, 1)

        # Right side - Sequences panel
        right_panel = QVBoxLayout()

        sequences_group = QGroupBox("Sequences")
        sequences_layout = QVBoxLayout()

        # Table for sequences
        self.sequences_table = QTableView()
        sequences_layout.addWidget(self.sequences_table)

        # Emergency Stop button
        self.btn_emergency_stop = QPushButton("Emergency Stop")
        self.btn_emergency_stop.setStyleSheet("background-color: red; color: white; font-weight: bold;")
        sequences_layout.addWidget(self.btn_emergency_stop)

        sequences_group.setLayout(sequences_layout)
        right_panel.addWidget(sequences_group)

        # Add right panel to main layout
        main_layout.addLayout(right_panel, 1)

        # Connect signals
        self.btn_initialize.clicked.connect(self.initialize_fluidics)
        self.btn_load_sequences.clicked.connect(self.load_sequences)
        self.btn_prime_start.clicked.connect(self.start_prime)
        self.btn_cleanup_start.clicked.connect(self.start_cleanup)
        self.btn_manual_flow.clicked.connect(self.start_manual_flow)
        self.btn_empty_syringe_pump.clicked.connect(self.empty_syringe_pump)
        self.btn_emergency_stop.clicked.connect(self.emergency_stop)

        self.enable_controls(False)
        self.btn_emergency_stop.setEnabled(False)

    def initialize_fluidics(self):
        """Initialize the fluidics system"""
        self.log_status("Initializing fluidics system...")
        self.fluidics.initialize()
        self.btn_initialize.setEnabled(False)
        self.enable_controls(True)
        self.btn_emergency_stop.setEnabled(True)
        self.fluidics_initialized_signal.emit()

    def set_sequence_callbacks(self):
        callbacks = {
            "on_finished": self.on_finish,
            "on_error": self.on_finish,
            "on_estimate": self.on_estimate,
            "update_progress": self.update_progress,
        }
        self.fluidics.worker_callbacks = callbacks

    def set_manual_control_callbacks(self):
        # TODO: use better logging description
        callbacks = {
            "on_finished": lambda: self.on_finish("Operation completed"),
            "on_error": self.on_finish,
            "on_estimate": None,
            "update_progress": None,
        }
        self.fluidics.worker_callbacks = callbacks

    def load_sequences(self):
        """Open file dialog to load sequences from CSV"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Fluidics Sequences", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self.log_status(f"Loading sequences from {file_path}")
            try:
                self.sequence_df = self.fluidics.load_sequences(file_path)
                self.sequence_df.drop("include", axis=1, inplace=True)
                model = PandasTableModel(self.sequence_df, self.fluidics.available_port_names)
                self.sequences_table.setModel(model)
                self.sequences_table.resizeColumnsToContents()
                self.sequences_table.horizontalHeader().setStretchLastSection(True)
                self.log_status(f"Loaded {len(self.sequence_df)} sequences")
            except Exception as e:
                self.log_status(f"Error loading sequences: {str(e)}")

    def start_prime(self):
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_prime_ports.text())
        fill_port = self.prime_fill_combo.currentIndex() + 1
        volume = int(self.txt_prime_volume.text())

        if not ports or not fill_port or not volume:
            return

        self.log_status(f"Starting prime: Ports {ports}, Fill with {fill_port}, Volume {volume}µL")
        self.fluidics.priming(ports, fill_port, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_cleanup(self):
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_cleanup_ports.text())
        fill_port = self.cleanup_fill_combo.currentIndex() + 1
        volume = int(self.txt_cleanup_volume.text())
        repeat = int(self.txt_cleanup_repeat.text())

        if not ports or not fill_port or not volume or not repeat:
            return

        self.log_status(f"Starting cleanup: Ports {ports}, Fill with {fill_port}, Volume {volume}µL, Repeat {repeat}x")
        self.fluidics.clean_up(ports, fill_port, volume, repeat)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_manual_flow(self):
        self.set_manual_control_callbacks()
        port = self.manual_port_combo.currentIndex() + 1
        flow_rate = int(self.txt_manual_flow_rate.text())
        volume = int(self.txt_manual_volume.text())

        if not port or not flow_rate or not volume:
            return

        self.log_status(f"Flow reagent: Port {port}, Flow rate {flow_rate}µL/min, Volume {volume}µL")
        self.fluidics.manual_flow(port, flow_rate, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def empty_syringe_pump(self):
        self.log_status("Empty syringe pump to waste")
        self.enable_controls(False)
        self.fluidics.empty_syringe_pump()
        self.log_status("Operation completed")
        self.enable_controls(True)

    def emergency_stop(self):
        self.fluidics.emergency_stop()

    def get_port_list(self, text: str) -> list:
        """Parse ports input string into a list of numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            ports_str = text.strip()
            if not ports_str:
                return [i for i in range(1, len(self.fluidics.available_port_names) + 1)]

            port_list = []

            # Split by comma and process each part
            for part in ports_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 28 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 28, and start must be <= end"
                        )
                    port_list.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 28:
                        raise ValueError(f"Invalid number {num}: Must be between 1 and 28")
                    port_list.append(num)

            return port_list

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception as e:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid port numbers (e.g., '1-3,5,7-10')")
            return []

    def update_progress(self, idx, seq_num, status):
        self.sequences_table.model().set_current_row(idx)
        self.log_message_signal.emit(f"Sequence {self.sequence_df.iloc[idx]['sequence_name']} {status}")

    def on_finish(self, status=None):
        self.enable_controls(True)
        try:
            self.sequences_table.model().set_current_row(-1)
        except:
            pass
        if status is None:
            status = "Sequence section completed"
        self.fluidics.reset_abort()
        self.log_message_signal.emit(status)

    def on_estimate(self, time, n):
        self.log_message_signal.emit(f"Estimated time: {time}s, Sequences: {n}")

    def enable_controls(self, enabled: bool):
        self.btn_load_sequences.setEnabled(enabled)
        self.btn_prime_start.setEnabled(enabled)
        self.btn_cleanup_start.setEnabled(enabled)
        self.btn_manual_flow.setEnabled(enabled)
        self.btn_empty_syringe_pump.setEnabled(enabled)

    def log_status(self, message):
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.status_text.append(f"[{current_time}] {message}")
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # Also log to console
        self._log.info(message)


class PandasTableModel(QAbstractTableModel):
    """Model for displaying pandas DataFrame in a QTableView"""

    def __init__(self, data, port_names=None):
        super().__init__()
        self._data = data
        self._current_row = -1
        self._port_names = port_names or []
        self._column_name_map = {
            "sequence_name": "Sequence Name",
            "fluidic_port": "Fluidic Port",
            "fill_tubing_with": "Fill Tubing With",
            "flow_rate": "Flow Rate (µL/min)",
            "volume": "Volume (µL)",
            "incubation_time": "Incubation (min)",
            "repeat": "Repeat",
        }

    def rowCount(self, parent=None):
        return len(self._data)

    def columnCount(self, parent=None):
        return len(self._data.columns)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            value = self._data.iloc[index.row(), index.column()]
            if pd.isna(value):
                return ""

            # Map port numbers to names for specific columns
            column_name = self._data.columns[index.column()]
            if column_name in ["fluidic_port", "fill_tubing_with"] and self._port_names:
                try:
                    # Convert value to integer and get corresponding name
                    port_num = int(value)
                    if 1 <= port_num <= len(self._port_names):
                        return self._port_names[port_num - 1]
                except (ValueError, TypeError):
                    pass

            return str(value)

        elif role == Qt.BackgroundRole:
            # Highlight the current row
            if index.row() == self._current_row:
                return QBrush(QColor(173, 216, 230))  # Light blue
            else:
                return QBrush(QColor(255, 255, 255))  # White
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            original_name = str(self._data.columns[section])
            return self._column_name_map.get(original_name, original_name)
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None

    def set_current_row(self, row_index):
        self._current_row = row_index
        self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))


class FocusMapWidget(QFrame):
    """Widget for managing focus map points and surface fitting"""

    def __init__(self, stage: AbstractStage, navigationViewer, scanCoordinates, focusMap):
        super().__init__()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Store controllers
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.scanCoordinates = scanCoordinates
        self.focusMap = focusMap

        # Store focus points in widget
        self.focus_points = []  # list of (region_id, x, y, z) tuples
        self.enabled = False  # toggled when focus map enabled for next acquisition

        self.setup_ui()
        self.make_connections()
        self.setEnabled(False)
        self.add_margin = True  # margin for focus grid makes it smaller, but will avoid points at the borders

    def setup_ui(self):
        """Create and arrange UI components"""
        self.layout = QVBoxLayout(self)

        # Point combo and Z control
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Focus Point:"))
        self.point_combo = QComboBox()
        controls_layout.addWidget(self.point_combo, stretch=1)
        self.update_z_btn = QPushButton("Update Z")
        controls_layout.addWidget(self.update_z_btn)
        self.layout.addLayout(controls_layout)

        # Point control buttons - line 1
        point_controls = QHBoxLayout()
        self.add_point_btn = QPushButton("Add")
        self.remove_point_btn = QPushButton("Remove")
        self.next_point_btn = QPushButton("Next")
        self.edit_point_btn = QPushButton("Edit")
        point_controls.addWidget(self.add_point_btn)
        point_controls.addWidget(self.remove_point_btn)
        point_controls.addWidget(self.next_point_btn)
        point_controls.addWidget(self.edit_point_btn)
        self.layout.addLayout(point_controls)

        # Point control buttons - line 2
        point_controls_2 = QHBoxLayout()
        point_controls_2.addWidget(QLabel("Focus Grid:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 10)
        self.rows_spin.setValue(4)
        point_controls_2.addWidget(self.rows_spin)
        x_label = QLabel("×")
        x_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        point_controls_2.addWidget(x_label)
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 10)
        self.cols_spin.setValue(4)
        point_controls_2.addWidget(self.cols_spin)
        self.export_btn = QPushButton("Export")
        self.export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_btn = QPushButton("Import")
        self.import_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        point_controls_2.addWidget(self.export_btn)
        point_controls_2.addWidget(self.import_btn)
        self.layout.addLayout(point_controls_2)

        # Surface fitting controls
        settings_layout = QHBoxLayout()
        settings_layout.addWidget(QLabel("Fitting Method:"))
        self.fit_method_combo = QComboBox()
        self.fit_method_combo.addItems(["spline", "rbf", "constant"])
        settings_layout.addWidget(self.fit_method_combo)
        settings_layout.addWidget(QLabel("Smoothing:"))
        self.smoothing_spin = QDoubleSpinBox()
        self.smoothing_spin.setRange(0.01, 1.0)
        self.smoothing_spin.setValue(0.1)
        self.smoothing_spin.setSingleStep(0.05)
        settings_layout.addWidget(self.smoothing_spin)
        self.by_region_checkbox = QCheckBox("Fit by Region")
        self.by_region_checkbox.setChecked(False)
        settings_layout.addWidget(self.by_region_checkbox)
        self.layout.addLayout(settings_layout)

        # Status label - reserve space even when hidden
        self.status_label = QLabel()
        self.status_label.setText(" ")  # Empty text to keep space
        self.layout.addWidget(self.status_label)

    def make_connections(self):
        # Auto-navigate when point selection changes
        self.point_combo.currentIndexChanged.connect(self.goto_selected_point)

        # Update Z for current point
        self.update_z_btn.clicked.connect(self.update_current_z)

        # Connect grid size changes
        self.rows_spin.valueChanged.connect(self.regenerate_grid)
        self.cols_spin.valueChanged.connect(self.regenerate_grid)

        # Connect point control buttons
        self.add_point_btn.clicked.connect(self.add_current_point)
        self.remove_point_btn.clicked.connect(self.remove_current_point)
        self.next_point_btn.clicked.connect(self.goto_next_point)
        self.edit_point_btn.clicked.connect(self.edit_current_point)
        self.export_btn.clicked.connect(self.export_focus_points)
        self.import_btn.clicked.connect(self.import_focus_points)

        # Connect fitting method change
        self.fit_method_combo.currentTextChanged.connect(self._match_by_region_box)

        # Connect to scan coordinates changes
        self.scanCoordinates.signal_scan_coordinates_updated.connect(self.on_regions_updated)

    def update_point_list(self):
        """Update point selection combo showing grid coordinates for points"""
        self.point_combo.blockSignals(True)
        curr_focus_point = self.point_combo.currentIndex()
        self.point_combo.clear()
        for idx, (region_id, x, y, z) in enumerate(self.focus_points):
            point_text = (
                f"{region_id}: "
                + "x:"
                + str(round(x, 3))
                + "mm  y:"
                + str(round(y, 3))
                + "mm  z:"
                + str(round(1000 * z, 2))
                + "μm"
            )
            self.point_combo.addItem(point_text)
        self.point_combo.setCurrentIndex(max(0, min(curr_focus_point, len(self.focus_points) - 1)))
        self.point_combo.blockSignals(False)

    def edit_current_point(self):
        """Edit coordinates of current point in a popup dialog"""
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            region_id, x, y, z = self.focus_points[index]

            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Edit Focus Point")
            layout = QFormLayout()

            # Add coordinate spinboxes with good precision
            x_spin = QDoubleSpinBox()
            x_spin.setRange(SOFTWARE_POS_LIMIT.X_NEGATIVE, SOFTWARE_POS_LIMIT.X_POSITIVE)
            x_spin.setDecimals(3)
            x_spin.setValue(x)
            x_spin.setSuffix(" mm")

            y_spin = QDoubleSpinBox()
            y_spin.setRange(SOFTWARE_POS_LIMIT.Y_NEGATIVE, SOFTWARE_POS_LIMIT.Y_POSITIVE)
            y_spin.setDecimals(3)
            y_spin.setValue(y)
            y_spin.setSuffix(" mm")

            z_spin = QDoubleSpinBox()
            z_spin.setRange(
                SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000, SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000
            )  # Convert mm limits to μm
            z_spin.setDecimals(2)
            z_spin.setValue(z * 1000)  # Convert mm to μm
            z_spin.setSuffix(" μm")

            layout.addRow("X:", x_spin)
            layout.addRow("Y:", y_spin)
            layout.addRow("Z:", z_spin)

            # Add OK/Cancel buttons
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)
            dialog.setLayout(layout)

            # Show dialog and handle result
            if dialog.exec_() == QDialog.Accepted:
                new_x = x_spin.value()
                new_y = y_spin.value()
                new_z = z_spin.value() / 1000  # Convert μm back to mm for storage
                self.focus_points[index] = (region_id, new_x, new_y, new_z)
                self.update_point_list()
                self.update_focus_point_display()

    def update_focus_point_display(self):
        """Update all focus points on navigation viewer"""
        self.navigationViewer.clear_focus_points()
        for _, x, y, _ in self.focus_points:
            self.navigationViewer.register_focus_point(x, y)

    def generate_grid(self, rows=4, cols=4):
        """Generate focus point grid that spans scan bounds"""
        if self.enabled:
            self.point_combo.blockSignals(True)
            self.focus_points.clear()
            self.navigationViewer.clear_focus_points()
            self.status_label.setText(" ")
            current_z = self.stage.get_pos().z_mm

            # Use FocusMap to generate coordinates
            coordinates = self.focusMap.generate_grid_coordinates(
                self.scanCoordinates, rows=rows, cols=cols, add_margin=self.add_margin
            )

            # Add points with current z coordinate
            for region_id, coords_list in coordinates.items():
                for coords in coords_list:
                    self.focus_points.append((region_id, coords[0], coords[1], current_z))
                    self.navigationViewer.register_focus_point(coords[0], coords[1])

            self.update_point_list()
            self.point_combo.blockSignals(False)

    def regenerate_grid(self):
        """Generate focus point grid given updated dims"""
        self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def add_current_point(self):
        # Check if any scan regions exist
        if not self.scanCoordinates.has_regions():
            QMessageBox.warning(self, "No Regions Defined", "Please define scan regions before adding focus points.")
            return

        pos = self.stage.get_pos()
        region_id = None

        # If by_region checkbox is checked, ask for region ID
        if self.by_region_checkbox.isChecked():
            region_ids = list(self.scanCoordinates.region_centers.keys())
            if not region_ids:
                QMessageBox.warning(
                    self, "No Regions Defined", "Please define scan regions before adding focus points."
                )
                return

            region_id, ok = QInputDialog.getItem(
                self, "Select Region", "Choose a region:", [str(r) for r in region_ids], 0, False
            )
            if not ok or not region_id:
                return
            region_id = str(region_id)  # Ensure string format
        else:
            # Find the closest region to current position
            closest_region = None
            min_distance = float("inf")
            for rid, center in self.scanCoordinates.region_centers.items():
                dx = center[0] - pos.x_mm
                dy = center[1] - pos.y_mm
                distance = dx * dx + dy * dy
                if distance < min_distance:
                    min_distance = distance
                    closest_region = rid
            region_id = closest_region

        if region_id is not None:
            self.focus_points.append((region_id, pos.x_mm, pos.y_mm, pos.z_mm))
            self.update_point_list()
            self.navigationViewer.register_focus_point(pos.x_mm, pos.y_mm)
        else:
            QMessageBox.warning(self, "Region Error", "Could not determine a valid region for this focus point.")

    def remove_current_point(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            self.focus_points.pop(index)
            self.update_point_list()
            self.update_focus_point_display()

    def goto_next_point(self):
        if not self.focus_points:
            return
        current = self.point_combo.currentIndex()
        next_index = (current + 1) % len(self.focus_points)
        self.point_combo.setCurrentIndex(next_index)
        self.goto_selected_point()

    def goto_selected_point(self):
        if self.enabled:
            index = self.point_combo.currentIndex()
            if 0 <= index < len(self.focus_points):
                _, x, y, z = self.focus_points[index]
                self.stage.move_x_to(x)
                self.stage.move_y_to(y)
                self.stage.move_z_to(z)

    def update_current_z(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            new_z = self.stage.get_pos().z_mm
            region_id, x, y, _ = self.focus_points[index]
            self.focus_points[index] = (region_id, x, y, new_z)
            self.update_point_list()

    def get_region_points_dict(self):
        points_dict = {}
        for region_id, x, y, z in self.focus_points:
            if region_id not in points_dict:
                points_dict[region_id] = []
            points_dict[region_id].append((x, y, z))
        return points_dict

    def fit_surface(self):
        try:
            method = self.fit_method_combo.currentText()
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            by_region = self.by_region_checkbox.isChecked()

            # Validate settings
            if by_region:
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in self.focus_points)
                if focus_regions != scan_regions:
                    QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        "The focus points region IDs do not match the scan regions. Please uncheck 'By Region' or select the correct regions.",
                    )
                    return False

            if method == "constant" and (rows != 1 or cols != 1):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For 'constant' method, grid size should be 1×1.\nUse 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            if method != "constant" and (rows < 2 or cols < 2):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For surface fitting methods ('spline' or 'rbf'), a grid size of at least 2×2 is recommended.\nAlternatively, use 1x1 grid and 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            self.focusMap.set_method(method)
            self.focusMap.set_fit_by_region(by_region)
            self.focusMap.smoothing_factor = self.smoothing_spin.value()

            mean_error, std_error = self.focusMap.fit(self.get_region_points_dict())

            self.status_label.setText(f"Surface fit: {mean_error:.3f} mm mean error")
            return True

        except Exception as e:
            self.status_label.setText(f"Fitting failed: {str(e)}")
            return False

    def _match_by_region_box(self):
        if self.fit_method_combo.currentText() == "constant":
            self.by_region_checkbox.setChecked(True)

    def export_focus_points(self):
        """Export focus points to a CSV file"""
        if not self.focus_points:
            QMessageBox.warning(self, "No Focus Points", "There are no focus points to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Focus Points", "", "CSV Files (*.csv);;All Files (*)")
        if not file_path:
            return
        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        try:
            with open(file_path, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                # Write header
                writer.writerow(["Region_ID", "X_mm", "Y_mm", "Z_um"])

                # Write data
                for region_id, x, y, z in self.focus_points:
                    writer.writerow([region_id, x, y, z])

            self.status_label.setText(f"Exported {len(self.focus_points)} points to {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export focus points: {str(e)}")

    def import_focus_points(self):
        """Import focus points from a CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Focus Points", "", "CSV Files (*.csv);;All Files (*)")

        if not file_path:
            return

        try:
            # Read the CSV file
            imported_points = []
            with open(file_path, "r", newline="") as csvfile:
                reader = csv.reader(csvfile)
                header = next(reader)  # Skip header row

                # Validate header
                required_columns = ["Region_ID", "X_mm", "Y_mm", "Z_um"]
                if not all(col in header for col in required_columns):
                    QMessageBox.warning(
                        self, "Invalid Format", f"CSV file must contain columns: {', '.join(required_columns)}"
                    )
                    return

                # Get column indices
                region_idx = header.index("Region_ID")
                x_idx = header.index("X_mm")
                y_idx = header.index("Y_mm")
                z_idx = header.index("Z_um")

                # Read data
                for row in reader:
                    if len(row) >= 4:
                        try:
                            region_id = str(row[region_idx])
                            x = float(row[x_idx])
                            y = float(row[y_idx])
                            z = float(row[z_idx])
                            imported_points.append((region_id, x, y, z))
                        except (ValueError, IndexError):
                            continue

            # If by_region is checked, validate regions
            if self.by_region_checkbox.isChecked():
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in imported_points)

                if not focus_regions == scan_regions:
                    response = QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        f"The imported focus points have regions: {', '.join(sorted(focus_regions))}\n\n"
                        f"Current scan has regions: {', '.join(sorted(scan_regions))}\n\n"
                        "Import anyway (disable 'By Region') or cancel?",
                        QMessageBox.Ok | QMessageBox.Cancel,
                        QMessageBox.Cancel,
                    )

                    if response == QMessageBox.Cancel:
                        return
                    else:
                        # User chose to continue, uncheck by_region
                        self.by_region_checkbox.setChecked(False)

            # Clear existing points and add imported ones
            self.focus_points = imported_points
            self.update_point_list()
            self.update_focus_point_display()

            self.status_label.setText(f"Imported {len(imported_points)} focus points")

        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import focus points: {str(e)}")

    def on_regions_updated(self):
        if self.scanCoordinates.has_regions():
            self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def disable_updating_focus_points_on_signal(self):
        self.scanCoordinates.signal_scan_coordinates_updated.disconnect(self.on_regions_updated)

    def enable_updating_focus_points_on_signal(self):
        self.scanCoordinates.signal_scan_coordinates_updated.connect(self.on_regions_updated)

    def setEnabled(self, enabled):
        self.enabled = enabled
        super().setEnabled(enabled)
        self.navigationViewer.focus_point_overlay_item.setVisible(enabled)
        self.on_regions_updated()

    def resizeEvent(self, event):
        """Handle resize events to maintain button sizing"""
        super().resizeEvent(event)
        self.update_z_btn.setFixedWidth(self.edit_point_btn.width())


class StitcherWidget(QFrame):

    def __init__(self, objectiveStore, channelConfigurationManager, contrastManager, *args, **kwargs):
        super(StitcherWidget, self).__init__(*args, **kwargs)
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.contrastManager = contrastManager
        self.stitcherThread = None
        self.output_path = ""
        self.initUI()

    def initUI(self):
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)  # Set frame style
        self.layout = QVBoxLayout(self)
        self.rowLayout1 = QHBoxLayout()
        self.rowLayout2 = QHBoxLayout()

        # Use registration checkbox
        self.useRegistrationCheck = QCheckBox("Registration")
        self.useRegistrationCheck.toggled.connect(self.onRegistrationCheck)
        self.rowLayout1.addWidget(self.useRegistrationCheck)
        self.rowLayout1.addStretch()

        # Apply flatfield correction checkbox
        self.applyFlatfieldCheck = QCheckBox("Flatfield Correction")
        self.rowLayout1.addWidget(self.applyFlatfieldCheck)
        self.rowLayout1.addStretch()

        # Output format dropdown
        self.outputFormatLabel = QLabel("Output Format", self)
        self.outputFormatCombo = QComboBox(self)
        self.outputFormatCombo.addItem("OME-ZARR")
        self.outputFormatCombo.addItem("OME-TIFF")
        self.rowLayout1.addWidget(self.outputFormatLabel)
        self.rowLayout1.addWidget(self.outputFormatCombo)

        # Select registration channel
        self.registrationChannelLabel = QLabel("Registration Configuration", self)
        self.registrationChannelLabel.setVisible(False)
        self.rowLayout2.addWidget(self.registrationChannelLabel)
        self.registrationChannelCombo = QComboBox(self)
        self.registrationChannelLabel.setVisible(False)
        self.registrationChannelCombo.setVisible(False)
        self.registrationChannelCombo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rowLayout2.addWidget(self.registrationChannelCombo)

        # Select registration cz-level
        self.registrationZLabel = QLabel(" Z-Level", self)
        self.registrationZLabel.setVisible(False)
        self.rowLayout2.addWidget(self.registrationZLabel)
        self.registrationZCombo = QSpinBox(self)
        self.registrationZCombo.setSingleStep(1)
        self.registrationZCombo.setMinimum(0)
        self.registrationZCombo.setMaximum(0)
        self.registrationZCombo.setValue(0)
        self.registrationZLabel.setVisible(False)
        self.registrationZCombo.setVisible(False)
        self.rowLayout2.addWidget(self.registrationZCombo)

        self.layout.addLayout(self.rowLayout1)
        self.layout.addLayout(self.rowLayout2)
        self.setLayout(self.layout)

        # Button to view output in Napari
        self.viewOutputButton = QPushButton("View Output in Napari")
        self.viewOutputButton.setEnabled(False)  # Initially disabled
        self.viewOutputButton.setVisible(False)
        self.viewOutputButton.clicked.connect(self.viewOutputNapari)
        self.layout.addWidget(self.viewOutputButton)

        # Progress bar
        progress_row = QHBoxLayout()

        # Status label
        self.statusLabel = QLabel("Status: Image Acquisition")
        progress_row.addWidget(self.statusLabel)
        self.statusLabel.setVisible(False)

        self.progressBar = QProgressBar()
        progress_row.addWidget(self.progressBar)
        self.progressBar.setVisible(False)  # Initially hidden
        self.layout.addLayout(progress_row)

    def setStitcherThread(self, thread):
        self.stitcherThread = thread

    def onRegistrationCheck(self, checked):
        self.registrationChannelLabel.setVisible(checked)
        self.registrationChannelCombo.setVisible(checked)
        self.registrationZLabel.setVisible(checked)
        self.registrationZCombo.setVisible(checked)

    def updateRegistrationChannels(self, selected_channels):
        self.registrationChannelCombo.clear()  # Clear existing items
        self.registrationChannelCombo.addItems(selected_channels)

    def updateRegistrationZLevels(self, Nz):
        self.registrationZCombo.setMinimum(0)
        self.registrationZCombo.setMaximum(Nz - 1)

    def gettingFlatfields(self):
        self.statusLabel.setText("Status: Calculating Flatfields")
        self.viewOutputButton.setVisible(False)
        self.viewOutputButton.setStyleSheet("")
        self.progressBar.setValue(0)
        self.statusLabel.setVisible(True)
        self.progressBar.setVisible(True)

    def startingStitching(self):
        self.statusLabel.setText("Status: Stitching Scans")
        self.viewOutputButton.setVisible(False)
        self.progressBar.setValue(0)
        self.statusLabel.setVisible(True)
        self.progressBar.setVisible(True)

    def updateProgressBar(self, value, total):
        self.progressBar.setMaximum(total)
        self.progressBar.setValue(value)
        self.progressBar.setVisible(True)

    def startingSaving(self, stitch_complete=False):
        if stitch_complete:
            self.statusLabel.setText("Status: Saving Stitched Acquisition")
        else:
            self.statusLabel.setText("Status: Saving Stitched Region")
        self.statusLabel.setVisible(True)
        self.progressBar.setRange(0, 0)  # indeterminate mode.
        self.progressBar.setVisible(True)

    def finishedSaving(self, output_path, dtype):
        if self.stitcherThread is not None:
            self.stitcherThread.quit()
            self.stitcherThread.deleteLater()
        self.statusLabel.setVisible(False)
        self.progressBar.setVisible(False)
        self.viewOutputButton.setVisible(True)
        self.viewOutputButton.setStyleSheet("background-color: #C2C2FF")
        self.viewOutputButton.setEnabled(True)
        try:
            self.viewOutputButton.clicked.disconnect()
        except TypeError:
            pass
        self.viewOutputButton.clicked.connect(self.viewOutputNapari)

        self.output_path = output_path

    def extractWavelength(self, name):
        # TODO: Use the 'color' attribute of the ChannelMode object
        # Split the string and find the wavelength number immediately after "Fluorescence"
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]  # Assuming '488 nm Ex' and taking '488'
        for color in ["R", "G", "B"]:
            if color in parts or "full_" + color in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Convert a HEX value to a normalized RGB tuple."""
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,  # Normalize the Red component
            ((channel_info["hex"] >> 8) & 0xFF) / 255,  # Normalize the Green component
            (channel_info["hex"] & 0xFF) / 255,
        )  # Normalize the Blue component
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updateContrastLimits(self, channel, min_val, max_val):
        self.contrastManager.update_limits(channel, min_val, max_val)

    def viewOutputNapari(self):
        try:
            napari_viewer = napari.Viewer()
            if ".ome.zarr" in self.output_path:
                napari_viewer.open(self.output_path, plugin="napari-ome-zarr")
            else:
                napari_viewer.open(self.output_path)

            for layer in napari_viewer.layers:
                layer_name = layer.name.replace("_", " ").replace("full ", "full_")
                channel_info = CHANNEL_COLORS_MAP.get(
                    self.extractWavelength(layer_name), {"hex": 0xFFFFFF, "name": "gray"}
                )

                if channel_info["name"] in AVAILABLE_COLORMAPS:
                    layer.colormap = AVAILABLE_COLORMAPS[channel_info["name"]]
                else:
                    layer.colormap = self.generateColormap(channel_info)

                min_val, max_val = self.contrastManager.get_limits(layer_name)
                layer.contrast_limits = (min_val, max_val)

        except Exception as e:
            QMessageBox.critical(self, "Error Opening in Napari", str(e))
            print(f"An error occurred while opening output in Napari: {e}")

    def resetUI(self):
        self.output_path = ""

        # Reset UI components to their default states
        self.applyFlatfieldCheck.setChecked(False)
        self.outputFormatCombo.setCurrentIndex(0)  # Assuming the first index is the default
        self.useRegistrationCheck.setChecked(False)
        self.registrationChannelCombo.clear()  # Clear existing items
        self.registrationChannelLabel.setVisible(False)
        self.registrationChannelCombo.setVisible(False)

        # Reset the visibility and state of buttons and labels
        self.viewOutputButton.setEnabled(False)
        self.viewOutputButton.setVisible(False)
        self.progressBar.setValue(0)
        self.progressBar.setVisible(False)
        self.statusLabel.setText("Status: Image Acquisition")
        self.statusLabel.setVisible(False)

    def closeEvent(self, event):
        if self.stitcherThread is not None:
            self.stitcherThread.quit()
            self.stitcherThread.wait()
            self.stitcherThread.deleteLater()
            self.stitcherThread = None
        super().closeEvent(event)


class NapariLiveWidget(QWidget):
    signal_coordinates_clicked = Signal(int, int, int, int)
    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)

    def __init__(
        self,
        streamHandler,
        liveController,
        stage: AbstractStage,
        objectiveStore,
        channelConfigurationManager,
        contrastManager,
        wellSelectionWidget=None,
        show_trigger_options=True,
        show_display_options=True,
        show_autolevel=False,
        autolevel=False,
        parent=None,
    ):
        super().__init__(parent)
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.stage = stage
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.wellSelectionWidget = wellSelectionWidget
        self.live_configuration = self.liveController.currentConfiguration
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.init_live = False
        self.init_live_rgb = False
        self.init_scale = False
        self.previous_scale = None
        self.previous_center = None
        self.last_was_autofocus = False
        self.fps_trigger = 10
        self.fps_display = 10
        self.contrastManager = contrastManager

        self.initNapariViewer()
        self.addNapariGrayclipColormap()
        self.initControlWidgets(show_trigger_options, show_display_options, show_autolevel, autolevel)
        self.update_microscope_mode_by_name(self.live_configuration.name)

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        self.viewerWidget = self.viewer.window._qt_window
        self.viewer.dims.axis_labels = ["Y-axis", "X-axis"]
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def updateHistogram(self, layer):
        if self.histogram_widget is not None and layer.data is not None:
            self.pg_image_item.setImage(layer.data, autoLevels=False)
            self.histogram_widget.setLevels(*layer.contrast_limits)
            self.histogram_widget.setHistogramRange(layer.data.min(), layer.data.max())

            # Set the histogram widget's region to match the layer's contrast limits
            self.histogram_widget.region.setRegion(layer.contrast_limits)

            # Update colormap only if it has changed
            if hasattr(self, "last_colormap") and self.last_colormap != layer.colormap.name:
                self.histogram_widget.gradient.setColorMap(self.createColorMap(layer.colormap))
            self.last_colormap = layer.colormap.name

    def createColorMap(self, colormap):
        colors = colormap.colors
        positions = np.linspace(0, 1, len(colors))
        return pg.ColorMap(positions, colors)

    def initControlWidgets(self, show_trigger_options, show_display_options, show_autolevel, autolevel):
        # Initialize histogram widget
        self.pg_image_item = pg.ImageItem()
        self.histogram_widget = pg.HistogramLUTWidget(image=self.pg_image_item)
        self.histogram_widget.setFixedWidth(100)
        self.histogram_dock = self.viewer.window.add_dock_widget(self.histogram_widget, area="right", name="hist")
        self.histogram_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.histogram_dock.setTitleBarWidget(QWidget())
        self.histogram_widget.region.sigRegionChanged.connect(self.on_histogram_region_changed)
        self.histogram_widget.region.sigRegionChangeFinished.connect(self.on_histogram_region_changed)

        # Microscope Configuration
        self.dropdown_modeSelection = QComboBox()
        for config in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(config.name)
        self.dropdown_modeSelection.setCurrentText(self.live_configuration.name)
        self.dropdown_modeSelection.currentTextChanged.connect(self.update_microscope_mode_by_name)

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        gradient_style = """
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #D6D6FF, stop:1 #C2C2FF);
                border-radius: 5px;
                color: black;
                border: 1px solid #A0A0A0;
            }
            QPushButton:checked {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #FFD6D6, stop:1 #FFC2C2);
                border: 1px solid #A0A0A0;
            }
            QPushButton:hover {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #E0E0FF, stop:1 #D0D0FF);
            }
            QPushButton:pressed {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #9090C0, stop:1 #8080B0);
            }
        """
        self.btn_live.setStyleSheet(gradient_style)
        # self.btn_live.setStyleSheet("font-weight: bold; background-color: #7676F7") #6666D3
        current_height = self.btn_live.sizeHint().height()
        self.btn_live.setFixedHeight(int(current_height * 1.5))
        self.btn_live.clicked.connect(self.toggle_live)

        # Exposure Time
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(*self.liveController.camera.get_exposure_limits())
        self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)

        # Analog Gain
        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setRange(0, 24)
        self.entry_analogGain.setSingleStep(0.1)
        self.entry_analogGain.setValue(self.live_configuration.analog_gain)
        # self.entry_analogGain.setSuffix('x')
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)

        # Illumination Intensity
        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setRange(0, 100)
        self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setTickInterval(10)
        self.slider_illuminationIntensity.valueChanged.connect(self.update_config_illumination_intensity)
        self.label_illuminationIntensity = QLabel(str(self.slider_illuminationIntensity.value()) + "%")
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda v: self.label_illuminationIntensity.setText(str(v) + "%")
        )

        # Trigger mode
        self.dropdown_triggerMode = QComboBox()
        trigger_modes = [
            ("Software", TriggerMode.SOFTWARE),
            ("Hardware", TriggerMode.HARDWARE),
            ("Continuous", TriggerMode.CONTINUOUS),
        ]
        for display_name, mode in trigger_modes:
            self.dropdown_triggerMode.addItem(display_name, mode)
        self.dropdown_triggerMode.currentIndexChanged.connect(self.on_trigger_mode_changed)
        # self.dropdown_triggerMode = QComboBox()
        # self.dropdown_triggerMode.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS])
        # self.dropdown_triggerMode.currentTextChanged.connect(self.liveController.set_trigger_mode)

        # Trigger FPS
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setRange(0.02, 1000)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        # self.entry_triggerFPS.setSuffix(" fps")
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)

        # Display FPS
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setRange(1, 240)
        self.entry_displayFPS.setValue(self.fps_display)
        # self.entry_displayFPS.setSuffix(" fps")
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)

        # Resolution Scaling
        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setRange(10, 100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setTickInterval(10)
        self.slider_resolutionScaling.valueChanged.connect(self.update_resolution_scaling)
        self.label_resolutionScaling = QLabel(str(self.slider_resolutionScaling.value()) + "%")
        self.slider_resolutionScaling.valueChanged.connect(lambda v: self.label_resolutionScaling.setText(str(v) + "%"))

        # Autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)
        self.btn_autolevel.clicked.connect(self.signal_autoLevelSetting.emit)

        def make_row(label_widget, entry_widget, value_label=None):
            row = QHBoxLayout()
            row.addWidget(label_widget)
            row.addWidget(entry_widget)
            if value_label:
                row.addWidget(value_label)
            return row

        control_layout = QVBoxLayout()

        # Add widgets to layout
        control_layout.addWidget(self.dropdown_modeSelection)
        control_layout.addWidget(self.btn_live)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        row1 = make_row(QLabel("Exposure Time"), self.entry_exposureTime)
        control_layout.addLayout(row1)

        row2 = make_row(QLabel("Illumination"), self.slider_illuminationIntensity, self.label_illuminationIntensity)
        control_layout.addLayout(row2)

        row3 = make_row((QLabel("Analog Gain")), self.entry_analogGain)
        control_layout.addLayout(row3)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_trigger_options:
            row0 = make_row(QLabel("Trigger Mode"), self.dropdown_triggerMode)
            control_layout.addLayout(row0)
            row00 = make_row(QLabel("Trigger FPS"), self.entry_triggerFPS)
            control_layout.addLayout(row00)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_display_options:
            row4 = make_row((QLabel("Display FPS")), self.entry_displayFPS)
            control_layout.addLayout(row4)
            row5 = make_row(QLabel("Display Resolution"), self.slider_resolutionScaling, self.label_resolutionScaling)
            control_layout.addLayout(row5)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_autolevel:
            control_layout.addWidget(self.btn_autolevel)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        control_layout.addStretch(1)

        add_live_controls = False
        if USE_NAPARI_FOR_LIVE_CONTROL or add_live_controls:
            live_controls_widget = QWidget()
            live_controls_widget.setLayout(control_layout)
            # layer_list_widget.setFixedWidth(270)

            layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
            layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

            self.viewer.window._qt_viewer.layerButtons.hide()
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)

            # Add the actual dock widgets
            self.dock_layer_controls = self.viewer.window.add_dock_widget(
                layer_controls_widget, area="left", name="layer controls", tabify=True
            )
            self.dock_layer_list = self.viewer.window.add_dock_widget(
                layer_list_widget, area="left", name="layer list", tabify=True
            )
            self.dock_live_controls = self.viewer.window.add_dock_widget(
                live_controls_widget, area="left", name="live controls", tabify=True
            )

            self.viewer.window.window_menu.addAction(self.dock_live_controls.toggleViewAction())

        if USE_NAPARI_WELL_SELECTION:
            well_selector_layout = QVBoxLayout()
            # title_label = QLabel("Well Selector")
            # title_label.setAlignment(Qt.AlignCenter)  # Center the title
            # title_label.setStyleSheet("font-weight: bold;")  # Optional: style the title
            # well_selector_layout.addWidget(title_label)

            well_selector_row = QHBoxLayout()
            well_selector_row.addStretch(1)
            well_selector_row.addWidget(self.wellSelectionWidget)
            well_selector_row.addStretch(1)
            well_selector_layout.addLayout(well_selector_row)
            well_selector_layout.addStretch()

            well_selector_dock_widget = QWidget()
            well_selector_dock_widget.setLayout(well_selector_layout)
            self.dock_well_selector = self.viewer.window.add_dock_widget(
                well_selector_dock_widget, area="bottom", name="well selector"
            )
            self.dock_well_selector.setFixedHeight(self.dock_well_selector.minimumSizeHint().height())

        layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
        layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

        self.viewer.window._qt_viewer.layerButtons.hide()
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)
        self.print_window_menu_items()

    def print_window_menu_items(self):
        print("Items in window_menu:")
        for action in self.viewer.window.window_menu.actions():
            print(action.text())

    def on_histogram_region_changed(self):
        if self.live_configuration.name:
            min_val, max_val = self.histogram_widget.region.getRegion()
            self.updateContrastLimits(self.live_configuration.name, min_val, max_val)

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def toggle_live_controls(self, show):
        if show:
            self.dock_live_controls.show()
        else:
            self.dock_live_controls.hide()

    def toggle_well_selector(self, show):
        if show:
            self.dock_well_selector.show()
        else:
            self.dock_well_selector.hide()

    def replace_well_selector(self, wellSelector):
        self.viewer.window.remove_dock_widget(self.dock_well_selector)
        self.wellSelectionWidget = wellSelector
        well_selector_layout = QHBoxLayout()
        well_selector_layout.addStretch(1)  # Add stretch on the left
        well_selector_layout.addWidget(self.wellSelectionWidget)
        well_selector_layout.addStretch(1)  # Add stretch on the right
        well_selector_dock_widget = QWidget()
        well_selector_dock_widget.setLayout(well_selector_layout)
        self.dock_well_selector = self.viewer.window.add_dock_widget(
            well_selector_dock_widget, area="bottom", name="well selector", tabify=True
        )

    def set_microscope_mode(self, config):
        self.dropdown_modeSelection.setCurrentText(config.name)

    def update_microscope_mode_by_name(self, current_microscope_mode_name):
        self.live_configuration = self.channelConfigurationManager.get_channel_configuration_by_name(
            self.objectiveStore.current_objective, current_microscope_mode_name
        )
        if self.live_configuration:
            self.liveController.set_microscope_mode(self.live_configuration)
            self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
            self.entry_analogGain.setValue(self.live_configuration.analog_gain)
            self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))

    def update_config_exposure_time(self, new_value):
        self.live_configuration.exposure_time = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "ExposureTime", new_value
        )
        self.signal_newExposureTime.emit(new_value)

    def update_config_analog_gain(self, new_value):
        self.live_configuration.analog_gain = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "AnalogGain", new_value
        )
        self.signal_newAnalogGain.emit(new_value)

    def update_config_illumination_intensity(self, new_value):
        self.live_configuration.illumination_intensity = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "IlluminationIntensity", new_value
        )
        self.liveController.update_illumination()

    def update_resolution_scaling(self, value):
        self.streamHandler.set_display_resolution_scaling(value)
        self.liveController.set_display_resolution_scaling(value)

    def on_trigger_mode_changed(self, index):
        # Get the actual value using user data
        actual_value = self.dropdown_triggerMode.itemData(index)
        print(f"Selected: {self.dropdown_triggerMode.currentText()} (actual value: {actual_value})")

    def addNapariGrayclipColormap(self):
        if hasattr(napari.utils.colormaps.AVAILABLE_COLORMAPS, "grayclip"):
            return
        grayclip = []
        for i in range(255):
            grayclip.append([i / 255, i / 255, i / 255])
        grayclip.append([1, 0, 0])
        napari.utils.colormaps.AVAILABLE_COLORMAPS["grayclip"] = napari.utils.Colormap(name="grayclip", colors=grayclip)

    def initLiveLayer(self, channel, image_height, image_width, image_dtype, rgb=False):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        self.viewer.layers.clear()
        self.image_width = image_width
        self.image_height = image_height
        if self.dtype != np.dtype(image_dtype):

            self.contrastManager.scale_contrast_limits(
                np.dtype(image_dtype)
            )  # Fix This to scale existing contrast limits to new dtype range
            self.dtype = image_dtype

        self.channels.add(channel)
        self.live_configuration.name = channel

        if rgb:
            canvas = np.zeros((image_height, image_width, 3), dtype=self.dtype)
        else:
            canvas = np.zeros((image_height, image_width), dtype=self.dtype)
        limits = self.getContrastLimits(self.dtype)
        layer = self.viewer.add_image(
            canvas,
            name="Live View",
            visible=True,
            rgb=rgb,
            colormap="grayclip",
            contrast_limits=limits,
            blending="additive",
        )
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name, self.dtype)
        layer.mouse_double_click_callbacks.append(self.onDoubleClick)
        layer.events.contrast_limits.connect(self.signalContrastLimits)
        self.updateHistogram(layer)

        if not self.init_scale:
            self.resetView()
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
        else:
            self.viewer.camera.zoom = self.previous_scale
            self.viewer.camera.center = self.previous_center

    def updateLiveLayer(self, image, from_autofocus=False):
        """Updates the canvas with the new image data."""
        if self.dtype != np.dtype(image.dtype):
            self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            self.dtype = np.dtype(image.dtype)
            self.init_live = False
            self.init_live_rgb = False

        if not self.live_configuration.name:
            self.live_configuration.name = self.liveController.currentConfiguration.name
        rgb = len(image.shape) >= 3

        if not rgb and not self.init_live or "Live View" not in self.viewer.layers:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live = True
            self.init_live_rgb = False
            print("init live")
        elif rgb and not self.init_live_rgb:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live_rgb = True
            self.init_live = False
            print("init live rgb")

        layer = self.viewer.layers["Live View"]
        layer.data = image
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name)
        self.updateHistogram(layer)

        if from_autofocus:
            # save viewer scale
            if not self.last_was_autofocus:
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
            # resize to cropped view
            self.resetView()
            self.last_was_autofocus = True
        else:
            if not self.init_scale:
                # init viewer scale
                self.resetView()
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
                self.init_scale = True
            elif self.last_was_autofocus:
                # return to to original view
                self.viewer.camera.zoom = self.previous_scale
                self.viewer.camera.center = self.previous_center
            # save viewer scale
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
            self.last_was_autofocus = False
        layer.refresh()

    def onDoubleClick(self, layer, event):
        """Handle double-click events and emit centered coordinates if within the data range."""
        coords = layer.world_to_data(event.position)
        layer_shape = layer.data.shape[0:2] if len(layer.data.shape) >= 3 else layer.data.shape

        if coords is not None and (0 <= int(coords[-1]) < layer_shape[-1] and (0 <= int(coords[-2]) < layer_shape[-2])):
            x_centered = int(coords[-1] - layer_shape[-1] / 2)
            y_centered = int(coords[-2] - layer_shape[-2] / 2)
            # Emit the centered coordinates and dimensions of the layer's data array
            self.signal_coordinates_clicked.emit(x_centered, y_centered, layer_shape[-1], layer_shape[-2])

    def set_live_configuration(self, live_configuration):
        self.live_configuration = live_configuration

    def updateContrastLimits(self, channel, min_val, max_val):
        self.contrastManager.update_limits(channel, min_val, max_val)
        if "Live View" in self.viewer.layers:
            self.viewer.layers["Live View"].contrast_limits = (min_val, max_val)

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(self.live_configuration.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()

    def activate(self):
        print("ACTIVATING NAPARI LIVE WIDGET")
        self.viewer.window.activate()


class NapariMultiChannelWidget(QWidget):

    def __init__(self, objectiveStore, camera, contrastManager, grid_enabled=False, parent=None):
        super().__init__(parent)
        # Initialize placeholders for the acquisition parameters
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.pixel_size_um = 1
        self.dz_um = 1
        self.Nz = 1
        self.layers_initialized = False
        self.acquisition_initialized = False
        self.viewer_scale_initialized = False
        self.update_layer_count = 0
        self.grid_enabled = grid_enabled

        # Initialize a napari Viewer without showing its standalone window.
        self.initNapariViewer()

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        if self.grid_enabled:
            self.viewer.grid.enabled = True
        self.viewer.dims.axis_labels = ["Z-axis", "Y-axis", "X-axis"]
        self.viewerWidget = self.viewer.window._qt_window
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def initLayersShape(self, Nz, dz):
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        if self.Nz != Nz or self.dz_um != dz or self.pixel_size_um != pixel_size_um:
            self.acquisition_initialized = False
            self.Nz = Nz
            self.dz_um = dz if Nz > 1 and dz != 0 else 1.0
            self.pixel_size_um = pixel_size_um

    def initChannels(self, channels):
        self.channels = set(channels)

    def extractWavelength(self, name):
        # Split the string and find the wavelength number immediately after "Fluorescence"
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]  # Assuming '488 nm Ex' and taking '488'
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Convert a HEX value to a normalized RGB tuple."""
        positions = [0, 1]
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,  # Normalize the Red component
            ((channel_info["hex"] >> 8) & 0xFF) / 255,  # Normalize the Green component
            (channel_info["hex"] & 0xFF) / 255,
        )  # Normalize the Blue component
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def initLayers(self, image_height, image_width, image_dtype):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        if self.acquisition_initialized:
            for layer in list(self.viewer.layers):
                if layer.name not in self.channels:
                    self.viewer.layers.remove(layer)
        else:
            self.viewer.layers.clear()
            self.acquisition_initialized = True
            if self.dtype != np.dtype(image_dtype) and not USE_NAPARI_FOR_LIVE_VIEW:
                self.contrastManager.scale_contrast_limits(image_dtype)

        self.image_width = image_width
        self.image_height = image_height
        self.dtype = np.dtype(image_dtype)
        self.layers_initialized = True
        self.update_layer_count = 0

    def updateLayers(self, image, x, y, k, channel_name):
        """Updates the appropriate slice of the canvas with the new image data."""
        rgb = len(image.shape) == 3

        # Check if the layer exists and has a different dtype
        if self.dtype != np.dtype(image.dtype):  # or self.viewer.layers[channel_name].data.dtype != image.dtype:
            # Remove the existing layer
            self.layers_initialized = False
            self.acquisition_initialized = False

        if not self.layers_initialized:
            self.initLayers(image.shape[0], image.shape[1], image.dtype)

        if channel_name not in self.viewer.layers:
            self.channels.add(channel_name)
            if rgb:
                color = None  # RGB images do not need a colormap
                canvas = np.zeros((self.Nz, self.image_height, self.image_width, 3), dtype=self.dtype)
            else:
                channel_info = CHANNEL_COLORS_MAP.get(
                    self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
                )
                if channel_info["name"] in AVAILABLE_COLORMAPS:
                    color = AVAILABLE_COLORMAPS[channel_info["name"]]
                else:
                    color = self.generateColormap(channel_info)
                canvas = np.zeros((self.Nz, self.image_height, self.image_width), dtype=self.dtype)

            limits = self.getContrastLimits(self.dtype)
            layer = self.viewer.add_image(
                canvas,
                name=channel_name,
                visible=True,
                rgb=rgb,
                colormap=color,
                contrast_limits=limits,
                blending="additive",
                scale=(self.dz_um, self.pixel_size_um, self.pixel_size_um),
            )

            # print(f"multi channel - dz_um:{self.dz_um}, pixel_y_um:{self.pixel_size_um}, pixel_x_um:{self.pixel_size_um}")
            layer.contrast_limits = self.contrastManager.get_limits(channel_name)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

            if not self.viewer_scale_initialized:
                self.resetView()
                self.viewer_scale_initialized = True
            else:
                layer.refresh()

        layer = self.viewer.layers[channel_name]
        layer.data[k] = image
        layer.contrast_limits = self.contrastManager.get_limits(channel_name)
        self.update_layer_count += 1
        if self.update_layer_count % len(self.channels) == 0:
            if self.Nz > 1:
                self.viewer.dims.set_point(0, k * self.dz_um)
            for layer in self.viewer.layers:
                layer.refresh()

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def activate(self):
        print("ACTIVATING NAPARI MULTICHANNEL WIDGET")
        self.viewer.window.activate()


class NapariMosaicDisplayWidget(QWidget):

    signal_coordinates_clicked = Signal(float, float)  # x, y in mm
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal(bool)
    signal_shape_drawn = Signal(list)

    def __init__(self, objectiveStore, camera, contrastManager, parent=None):
        super().__init__(parent)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewer.window._qt_window)
        self.layers_initialized = False
        self.shape_layer = None
        self.shapes_mm = []
        self.is_drawing_shape = False

        # add clear button
        self.clear_button = QPushButton("Clear Mosaic View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        self.layout.addWidget(self.clear_button)

        self.setLayout(self.layout)
        self.customizeViewer()
        self.viewer_pixel_size_mm = 1
        self.dz_um = None
        self.Nz = None
        self.channels = set()
        self.viewer_extents = []  # [min_y, max_y, min_x, max_x]
        self.top_left_coordinate = None  # [y, x] in mm
        self.mosaic_dtype = None

    def customizeViewer(self):
        # # hide status bar
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()
        self.viewer.bind_key("D", self.toggle_draw_mode)

    def toggle_draw_mode(self, viewer):
        self.is_drawing_shape = not self.is_drawing_shape

        if "Manual ROI" not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name="Manual ROI", edge_width=40, edge_color="red", face_color="transparent"
            )
            self.shape_layer.events.data.connect(self.on_shape_change)
        else:
            self.shape_layer = self.viewer.layers["Manual ROI"]

        if self.is_drawing_shape:
            # if there are existing shapes, switch to vertex select mode
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                # if no shapes exist, switch to add polygon mode
                # start drawing a new polygon on click, add vertices with additional clicks, finish/close polygon with double-click
                self.shape_layer.mode = "add_polygon"
        else:
            # if no shapes exist, switch to pan/zoom mode
            self.shape_layer.mode = "pan_zoom"

        self.on_shape_change()

    def enable_shape_drawing(self, enable):
        if enable:
            self.toggle_draw_mode(self.viewer)
        else:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.mode = "pan_zoom"

    def on_shape_change(self, event=None):
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # convert shapes to mm coordinates
            self.shapes_mm = [self.convert_shape_to_mm(shape) for shape in self.shape_layer.data]
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

    def convert_shape_to_mm(self, shape_data):
        shape_data_mm = []
        for point in shape_data:
            coords = self.viewer.layers[0].world_to_data(point)
            x_mm = self.top_left_coordinate[1] + coords[1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[0] * self.viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        return np.array(shape_data_mm)

    def convert_mm_to_viewer_shapes(self, shapes_mm):
        viewer_shapes = []
        for shape_mm in shapes_mm:
            viewer_shape = []
            for point_mm in shape_mm:
                x_data = (point_mm[0] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm
                y_data = (point_mm[1] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm
                world_coords = self.viewer.layers[0].data_to_world([y_data, x_data])
                viewer_shape.append(world_coords)
            viewer_shapes.append(viewer_shape)
        return viewer_shapes

    def update_shape_layer_position(self, prev_top_left, new_top_left):
        if self.shape_layer is None or len(self.shapes_mm) == 0:
            return
        try:
            # update top_left_coordinate
            self.top_left_coordinate = new_top_left

            # convert mm coordinates to viewer coordinates
            new_shapes = self.convert_mm_to_viewer_shapes(self.shapes_mm)

            # update shape layer data
            self.shape_layer.data = new_shapes
        except Exception as e:
            print(f"Error updating shape layer position: {e}")
            import traceback

            traceback.print_exc()

    def initChannels(self, channels):
        self.channels = set(channels)

    def initLayersShape(self, Nz, dz):
        self.Nz = 1
        self.dz_um = dz

    def extractWavelength(self, name):
        # extract wavelength from channel name
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        # generate colormap from hex value
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updateMosaic(self, image, x_mm, y_mm, k, channel_name):
        # calculate pixel size
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        downsample_factor = max(1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        image_pixel_size_um = pixel_size_um * downsample_factor
        image_pixel_size_mm = image_pixel_size_um / 1000
        image_dtype = image.dtype

        # downsample image
        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (image.shape[1] // downsample_factor, image.shape[0] // downsample_factor),
                interpolation=cv2.INTER_AREA,
            )

        # adjust image position
        x_mm -= (image.shape[1] * image_pixel_size_mm) / 2
        y_mm -= (image.shape[0] * image_pixel_size_mm) / 2

        if not self.viewer.layers:
            # initialize first layer
            self.layers_initialized = True
            self.signal_layers_initialized.emit(self.layers_initialized)
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.viewer_extents = [
                y_mm,
                y_mm + image.shape[0] * image_pixel_size_mm,
                x_mm,
                x_mm + image.shape[1] * image_pixel_size_mm,
            ]
            self.top_left_coordinate = [y_mm, x_mm]
            self.mosaic_dtype = image_dtype
        else:
            # convert image dtype and scale if necessary
            image = self.convertImageDtype(image, self.mosaic_dtype)
            if image_pixel_size_mm != self.viewer_pixel_size_mm:
                scale_factor = image_pixel_size_mm / self.viewer_pixel_size_mm
                image = cv2.resize(
                    image,
                    (int(image.shape[1] * scale_factor), int(image.shape[0] * scale_factor)),
                    interpolation=cv2.INTER_LINEAR,
                )

        if channel_name not in self.viewer.layers:
            # create new layer for channel
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            layer = self.viewer.add_image(
                np.zeros_like(image),
                name=channel_name,
                rgb=len(image.shape) == 3,
                colormap=color,
                visible=True,
                blending="additive",
                scale=(self.viewer_pixel_size_mm * 1000, self.viewer_pixel_size_mm * 1000),
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

        # get layer for channel
        layer = self.viewer.layers[channel_name]

        # update extents
        self.viewer_extents[0] = min(self.viewer_extents[0], y_mm)
        self.viewer_extents[1] = max(self.viewer_extents[1], y_mm + image.shape[0] * self.viewer_pixel_size_mm)
        self.viewer_extents[2] = min(self.viewer_extents[2], x_mm)
        self.viewer_extents[3] = max(self.viewer_extents[3], x_mm + image.shape[1] * self.viewer_pixel_size_mm)

        # store previous top-left coordinate
        prev_top_left = self.top_left_coordinate.copy() if self.top_left_coordinate else None
        self.top_left_coordinate = [self.viewer_extents[0], self.viewer_extents[2]]

        # update layer
        self.updateLayer(layer, image, x_mm, y_mm, k, prev_top_left)

        # update contrast limits
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        scaled_min = self.convertValue(min_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        scaled_max = self.convertValue(max_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        layer.contrast_limits = (scaled_min, scaled_max)
        layer.refresh()

    def updateLayer(self, layer, image, x_mm, y_mm, k, prev_top_left):
        # calculate new mosaic size and position
        mosaic_height = int(math.ceil((self.viewer_extents[1] - self.viewer_extents[0]) / self.viewer_pixel_size_mm))
        mosaic_width = int(math.ceil((self.viewer_extents[3] - self.viewer_extents[2]) / self.viewer_pixel_size_mm))

        is_rgb = len(image.shape) == 3 and image.shape[2] == 3
        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            # calculate offsets for existing data
            y_offset = int(math.floor((prev_top_left[0] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
            x_offset = int(math.floor((prev_top_left[1] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

            for mosaic in self.viewer.layers:
                if mosaic.name != "Manual ROI":
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data = np.zeros((mosaic_height, mosaic_width, 3), dtype=mosaic.data.dtype)
                    else:
                        new_data = np.zeros((mosaic_height, mosaic_width), dtype=mosaic.data.dtype)

                    # ensure offsets don't exceed bounds
                    y_end = min(y_offset + mosaic.data.shape[0], new_data.shape[0])
                    x_end = min(x_offset + mosaic.data.shape[1], new_data.shape[1])

                    # shift existing data
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data[y_offset:y_end, x_offset:x_end, :] = mosaic.data[
                            : y_end - y_offset, : x_end - x_offset, :
                        ]
                    else:
                        new_data[y_offset:y_end, x_offset:x_end] = mosaic.data[: y_end - y_offset, : x_end - x_offset]
                    mosaic.data = new_data

            if "Manual ROI" in self.viewer.layers:
                self.update_shape_layer_position(prev_top_left, self.top_left_coordinate)

            self.resetView()

        # insert new image
        y_pos = int(math.floor((y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
        x_pos = int(math.floor((x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

        # ensure indices are within bounds
        y_end = min(y_pos + image.shape[0], layer.data.shape[0])
        x_end = min(x_pos + image.shape[1], layer.data.shape[1])

        # insert image data
        if is_rgb:
            layer.data[y_pos:y_end, x_pos:x_end, :] = image[: y_end - y_pos, : x_end - x_pos, :]
        else:
            layer.data[y_pos:y_end, x_pos:x_end] = image[: y_end - y_pos, : x_end - x_pos]
        layer.refresh()

    def convertImageDtype(self, image, target_dtype):
        # convert image to target dtype
        if image.dtype == target_dtype:
            return image

        # get full range of values for both dtypes
        if np.issubdtype(image.dtype, np.integer):
            input_info = np.iinfo(image.dtype)
            input_min, input_max = input_info.min, input_info.max
        else:
            input_min, input_max = np.min(image), np.max(image)

        if np.issubdtype(target_dtype, np.integer):
            output_info = np.iinfo(target_dtype)
            output_min, output_max = output_info.min, output_info.max
        else:
            output_min, output_max = 0.0, 1.0

        # normalize and scale image
        image_normalized = (image.astype(np.float64) - input_min) / (input_max - input_min)
        image_scaled = image_normalized * (output_max - output_min) + output_min

        return image_scaled.astype(target_dtype)

    def convertValue(self, value, from_dtype, to_dtype):
        # Convert value from one dtype range to another
        from_info = np.iinfo(from_dtype)
        to_info = np.iinfo(to_dtype)

        # Normalize the value to [0, 1] range
        normalized = (value - from_info.min) / (from_info.max - from_info.min)

        # Scale to the target dtype range
        return normalized * (to_info.max - to_info.min) + to_info.min

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)

        # Convert the new limits from mosaic_dtype to acquisition_dtype
        acquisition_min = self.convertValue(min_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)
        acquisition_max = self.convertValue(max_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)

        # Update the ContrastManager with the new limits
        self.contrastManager.update_limits(layer.name, acquisition_min, acquisition_max)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def onDoubleClick(self, layer, event):
        coords = layer.world_to_data(event.position)
        if coords is not None:
            x_mm = self.top_left_coordinate[1] + coords[-1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[-2] * self.viewer_pixel_size_mm
            print(f"move from click: ({x_mm:.6f}, {y_mm:.6f})")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clear_shape(self):
        if self.shape_layer is not None:
            self.viewer.layers.remove(self.shape_layer)
            self.shape_layer = None
            self.is_drawing_shape = False
            self.signal_shape_drawn.emit([])

    def clearAllLayers(self):
        # Keep the Manual ROI layer and clear the content of all other layers
        for layer in self.viewer.layers:
            if layer.name == "Manual ROI":
                continue

            if hasattr(layer, "data") and hasattr(layer.data, "shape"):
                # Create an empty array matching the layer's dimensions
                if len(layer.data.shape) == 3 and layer.data.shape[2] == 3:  # RGB
                    empty_data = np.zeros((layer.data.shape[0], layer.data.shape[1], 3), dtype=layer.data.dtype)
                else:  # Grayscale
                    empty_data = np.zeros((layer.data.shape[0], layer.data.shape[1]), dtype=layer.data.dtype)

                layer.data = empty_data

        self.channels = set()

        for layer in self.viewer.layers:
            layer.refresh()

        self.signal_clear_viewer.emit()

    def activate(self):
        print("ACTIVATING NAPARI MOSAIC WIDGET")
        self.viewer.window.activate()


class TrackingControllerWidget(QFrame):
    def __init__(
        self,
        trackingController: TrackingController,
        objectiveStore,
        channelConfigurationManager,
        show_configurations=True,
        main=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.trackingController = trackingController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.base_path_is_set = False
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.trackingController.microcontroller.add_joystick_button_listener(
            lambda button_pressed: self.handle_button_state(button_pressed)
        )

    def add_components(self, show_configurations):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.trackingController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # self.dropdown_objective = QComboBox()
        # self.dropdown_objective.addItems(list(OBJECTIVES.keys()))
        # self.dropdown_objective.setCurrentText(DEFAULT_OBJECTIVE)
        self.objectivesWidget = ObjectivesWidget(self.objectiveStore)

        self.dropdown_tracker = QComboBox()
        self.dropdown_tracker.addItems(TRACKERS)
        self.dropdown_tracker.setCurrentText(DEFAULT_TRACKER)

        self.entry_tracking_interval = QDoubleSpinBox()
        self.entry_tracking_interval.setMinimum(0)
        self.entry_tracking_interval.setMaximum(30)
        self.entry_tracking_interval.setSingleStep(0.5)
        self.entry_tracking_interval.setValue(0)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
        self.checkbox_saveImages = QCheckBox("Save Images")
        self.btn_track = QPushButton("Start Tracking")
        self.btn_track.setCheckable(True)
        self.btn_track.setChecked(False)

        self.checkbox_enable_stage_tracking = QCheckBox(" Enable Stage Tracking")
        self.checkbox_enable_stage_tracking.setChecked(True)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 0, 0)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1, 1, 2)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 3)
        tmp = QLabel("Experiment ID")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 1, 0)
        grid_line0.addWidget(self.lineEdit_experimentID, 1, 1, 1, 1)
        tmp = QLabel("Objective")
        tmp.setFixedWidth(90)
        # grid_line0.addWidget(tmp,1,2)
        # grid_line0.addWidget(self.dropdown_objective, 1,3)
        grid_line0.addWidget(tmp, 1, 2)
        grid_line0.addWidget(self.objectivesWidget, 1, 3)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)

        grid_line1 = QHBoxLayout()
        tmp = QLabel("Tracker")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.dropdown_tracker)
        tmp = QLabel("Tracking Interval (s)")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.entry_tracking_interval)
        grid_line1.addWidget(self.checkbox_withAutofocus)
        grid_line1.addWidget(self.checkbox_saveImages)

        grid_line4 = QGridLayout()
        grid_line4.addWidget(self.btn_track, 0, 0, 1, 3)
        grid_line4.addWidget(self.checkbox_enable_stage_tracking, 0, 4)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        if show_configurations:
            self.grid.addLayout(grid_line3)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line4)
        self.grid.addStretch()
        self.setLayout(self.grid)

        # connections - buttons, checkboxes, entries
        self.checkbox_enable_stage_tracking.stateChanged.connect(self.trackingController.toggle_stage_tracking)
        self.checkbox_withAutofocus.stateChanged.connect(self.trackingController.toggel_enable_af)
        self.checkbox_saveImages.stateChanged.connect(self.trackingController.toggel_save_images)
        self.entry_tracking_interval.valueChanged.connect(self.trackingController.set_tracking_time_interval)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_track.clicked.connect(self.toggle_acquisition)
        # connections - selections and entries
        self.dropdown_tracker.currentIndexChanged.connect(self.update_tracker)
        # self.dropdown_objective.currentIndexChanged.connect(self.update_pixel_size)
        self.objectivesWidget.dropdown.currentIndexChanged.connect(self.update_pixel_size)
        # controller to widget
        self.trackingController.signal_tracking_stopped.connect(self.slot_tracking_stopped)

        # run initialization functions
        self.update_pixel_size()
        self.trackingController.update_image_resizing_factor(1)  # to add: image resizing slider

    # TODO(imo): This needs testing!
    def handle_button_pressed(self, button_state):
        QMetaObject.invokeMethod(self, "slot_joystick_button_pressed", Qt.AutoConnection, button_state)

    def slot_joystick_button_pressed(self, button_state):
        self.btn_track.setChecked(button_state)
        if self.btn_track.isChecked():
            if self.base_path_is_set == False:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            self.setEnabled_all(False)
            self.trackingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.trackingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def slot_tracking_stopped(self):
        self.btn_track.setChecked(False)
        self.setEnabled_all(True)
        print("tracking stopped")

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.trackingController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed):
        if pressed:
            if self.base_path_is_set == False:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self.trackingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.trackingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def setEnabled_all(self, enabled):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        # self.dropdown_tracker
        # self.dropdown_objective
        self.list_configurations.setEnabled(enabled)

    def update_tracker(self, index):
        self.trackingController.update_tracker_selection(self.dropdown_tracker.currentText())

    def update_pixel_size(self):
        objective = self.dropdown_objective.currentText()
        self.trackingController.objective = objective
        # self.internal_state.data['Objective'] = self.objective
        # TODO: these pixel size code needs to be updated.
        pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR] / (
            TUBE_LENS_MM / (OBJECTIVES[objective]["tube_lens_f_mm"] / OBJECTIVES[objective]["magnification"])
        )
        self.trackingController.update_pixel_size(pixel_size_um)
        print("pixel size is " + str(pixel_size_um) + " μm")

    def update_pixel_size(self):
        objective = self.objectiveStore.current_objective
        self.trackingController.objective = objective
        objective_info = self.objectiveStore.objectives_dict[objective]
        magnification = objective_info["magnification"]
        objective_tube_lens_mm = objective_info["tube_lens_f_mm"]
        tube_lens_mm = TUBE_LENS_MM
        # TODO: these pixel size code needs to be updated.
        pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR]
        pixel_size_xy = pixel_size_um / (magnification / (objective_tube_lens_mm / tube_lens_mm))
        self.trackingController.update_pixel_size(pixel_size_xy)
        print(f"pixel size is {pixel_size_xy:.2f} μm")

    """
        # connections
        self.checkbox_withAutofocus.stateChanged.connect(self.trackingController.set_af_flag)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.trackingController.trackingStopped.connect(self.acquisition_is_finished)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.plateReadingController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self,pressed):
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self.trackingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.trackingController.set_selected_configurations((item.text() for item in self.list_configurations.selectedItems()))
            self.trackingController.set_selected_columns(list(map(int,[item.text() for item in self.list_columns.selectedItems()])))
            self.trackingController.run_acquisition()
        else:
            self.trackingController.stop_acquisition() # to implement
            pass

    def acquisition_is_finished(self):
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)

    def setEnabled_all(self,enabled,exclude_btn_startAcquisition=False):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.list_columns.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)
    """


class PlateReaderAcquisitionWidget(QFrame):
    def __init__(
        self, plateReadingController, configurationManager=None, show_configurations=True, main=None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.plateReadingController = plateReadingController
        self.configurationManager = configurationManager
        self.base_path_is_set = False
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self, show_configurations):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.plateReadingController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        self.list_columns = QListWidget()
        for i in range(PLATE_READER.NUMBER_OF_COLUMNS):
            self.list_columns.addItems([str(i + 1)])
        self.list_columns.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.list_configurations = QListWidget()
        for microscope_configuration in self.configurationManager.configurations:
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
        self.btn_startAcquisition = QPushButton("Start Acquisition")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)

        self.btn_startAcquisition.setEnabled(False)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line1 = QGridLayout()
        tmp = QLabel("Sample ID")
        tmp.setFixedWidth(90)
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line2 = QGridLayout()
        tmp = QLabel("Columns")
        tmp.setFixedWidth(90)
        grid_line2.addWidget(tmp)
        grid_line2.addWidget(self.list_columns, 0, 1)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)
        # grid_line3.addWidget(self.checkbox_withAutofocus)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.grid.addLayout(grid_line2, 2, 0)
        if show_configurations:
            self.grid.addLayout(grid_line3, 3, 0)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addWidget(self.btn_startAcquisition, 4, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.checkbox_withAutofocus.stateChanged.connect(self.plateReadingController.set_af_flag)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.plateReadingController.acquisitionFinished.connect(self.acquisition_is_finished)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.plateReadingController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed):
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self.plateReadingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.plateReadingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.plateReadingController.set_selected_columns(
                list(map(int, [item.text() for item in self.list_columns.selectedItems()]))
            )
            self.plateReadingController.run_acquisition()
        else:
            self.plateReadingController.stop_acquisition()  # to implement
            pass

    def acquisition_is_finished(self):
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled, exclude_btn_startAcquisition=False):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.list_columns.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def slot_homing_complete(self):
        self.btn_startAcquisition.setEnabled(True)


class PlateReaderNavigationWidget(QFrame):
    def __init__(self, plateReaderNavigationController, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.plateReaderNavigationController = plateReaderNavigationController

    def add_components(self):
        self.dropdown_column = QComboBox()
        self.dropdown_column.addItems([""])
        self.dropdown_column.addItems([str(i + 1) for i in range(PLATE_READER.NUMBER_OF_COLUMNS)])
        self.dropdown_row = QComboBox()
        self.dropdown_row.addItems([""])
        self.dropdown_row.addItems([chr(i) for i in range(ord("A"), ord("A") + PLATE_READER.NUMBER_OF_ROWS)])
        self.btn_moveto = QPushButton("Move To")
        self.btn_home = QPushButton("Home")
        self.label_current_location = QLabel()
        self.label_current_location.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.label_current_location.setFixedWidth(50)

        self.dropdown_column.setEnabled(False)
        self.dropdown_row.setEnabled(False)
        self.btn_moveto.setEnabled(False)

        # layout
        grid_line0 = QHBoxLayout()
        # tmp = QLabel('Saving Path')
        # tmp.setFixedWidth(90)
        grid_line0.addWidget(self.btn_home)
        grid_line0.addWidget(QLabel("Column"))
        grid_line0.addWidget(self.dropdown_column)
        grid_line0.addWidget(QLabel("Row"))
        grid_line0.addWidget(self.dropdown_row)
        grid_line0.addWidget(self.btn_moveto)
        grid_line0.addStretch()
        grid_line0.addWidget(self.label_current_location)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.setLayout(self.grid)

        self.btn_home.clicked.connect(self.home)
        self.btn_moveto.clicked.connect(self.move)

    def home(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.plateReaderNavigationController.home()

    def move(self):
        self.plateReaderNavigationController.moveto(self.dropdown_column.currentText(), self.dropdown_row.currentText())

    def slot_homing_complete(self):
        self.dropdown_column.setEnabled(True)
        self.dropdown_row.setEnabled(True)
        self.btn_moveto.setEnabled(True)

    def update_current_location(self, location_str):
        self.label_current_location.setText(location_str)
        row = location_str[0]
        column = location_str[1:]
        self.dropdown_row.setCurrentText(row)
        self.dropdown_column.setCurrentText(column)


class TriggerControlWidget(QFrame):
    # for synchronized trigger
    signal_toggle_live = Signal(bool)
    signal_trigger_mode = Signal(str)
    signal_trigger_fps = Signal(float)

    def __init__(self, microcontroller2):
        super().__init__()
        self.fps_trigger = 10
        self.fps_display = 10
        self.microcontroller2 = microcontroller2
        self.triggerMode = TriggerMode.SOFTWARE
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        # line 0: trigger mode
        self.triggerMode = None
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE])

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)

        self.btn_live = QPushButton("Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)

        # connections
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)
        self.btn_live.clicked.connect(self.toggle_live)
        self.entry_triggerFPS.valueChanged.connect(self.update_trigger_fps)

        # inititialization
        self.microcontroller2.set_camera_trigger_frequency(self.fps_trigger)

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("Trigger Mode"), 0, 0)
        grid_line0.addWidget(self.dropdown_triggerManu, 0, 1)
        grid_line0.addWidget(QLabel("Trigger FPS"), 0, 2)
        grid_line0.addWidget(self.entry_triggerFPS, 0, 3)
        grid_line0.addWidget(self.btn_live, 1, 0, 1, 4)
        self.setLayout(grid_line0)

    def toggle_live(self, pressed):
        self.signal_toggle_live.emit(pressed)
        if pressed:
            self.microcontroller2.start_camera_trigger()
        else:
            self.microcontroller2.stop_camera_trigger()

    def update_trigger_mode(self):
        self.signal_trigger_mode.emit(self.dropdown_triggerManu.currentText())

    def update_trigger_fps(self, fps):
        self.fps_trigger = fps
        self.signal_trigger_fps.emit(fps)
        self.microcontroller2.set_camera_trigger_frequency(self.fps_trigger)


class MultiCameraRecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, channels, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.channels = channels
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        for channel in self.channels:
            self.streamHandler[channel].set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)
        grid_line3.addWidget(self.btn_record, 0, 4)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 0, 0)
        self.grid.addLayout(grid_line2, 1, 0)
        self.grid.addLayout(grid_line3, 2, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        for channel in self.channels:
            self.entry_saveFPS.valueChanged.connect(self.streamHandler[channel].set_save_fps)
            self.entry_timeLimit.valueChanged.connect(self.imageSaver[channel].set_recording_time_limit)
            self.imageSaver[channel].stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        for channel in self.channels:
            self.imageSaver[channel].set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.save_dir_base = save_dir_base
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            experiment_ID = self.lineEdit_experimentID.text()
            experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
            utils.ensure_directory_exists(os.path.join(self.save_dir_base, experiment_ID))
            for channel in self.channels:
                self.imageSaver[channel].start_new_experiment(os.path.join(experiment_ID, channel), add_timestamp=False)
                self.streamHandler[channel].start_recording()
        else:
            for channel in self.channels:
                self.streamHandler[channel].stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        for channel in self.channels:
            self.streamHandler[channel].stop_recording()
        self.btn_setSavingDir.setEnabled(True)


class WaveformDisplay(QFrame):

    def __init__(self, N=1000, include_x=True, include_y=True, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.N = N
        self.include_x = include_x
        self.include_y = include_y
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.plotWidget = {}
        self.plotWidget["X"] = PlotWidget("X", N=self.N, add_legend=True)
        self.plotWidget["Y"] = PlotWidget("X", N=self.N, add_legend=True)

        layout = QGridLayout()  # layout = QStackedLayout()
        if self.include_x:
            layout.addWidget(self.plotWidget["X"], 0, 0)
        if self.include_y:
            layout.addWidget(self.plotWidget["Y"], 1, 0)
        self.setLayout(layout)

    def plot(self, time, data):
        if self.include_x:
            self.plotWidget["X"].plot(time, data[0, :], "X", color=(255, 255, 255), clear=True)
        if self.include_y:
            self.plotWidget["Y"].plot(time, data[1, :], "Y", color=(255, 255, 255), clear=True)

    def update_N(self, N):
        self.N = N
        self.plotWidget["X"].update_N(N)
        self.plotWidget["Y"].update_N(N)


class PlotWidget(pg.GraphicsLayoutWidget):

    def __init__(self, title="", N=1000, parent=None, add_legend=False):
        super().__init__(parent)
        self.plotWidget = self.addPlot(title="", axisItems={"bottom": pg.DateAxisItem()})
        if add_legend:
            self.plotWidget.addLegend()
        self.N = N

    def plot(self, x, y, label, color, clear=False):
        self.plotWidget.plot(x[-self.N :], y[-self.N :], pen=pg.mkPen(color=color, width=4), name=label, clear=clear)

    def update_N(self, N):
        self.N = N


class DisplacementMeasurementWidget(QFrame):
    def __init__(self, displacementMeasurementController, waveformDisplay, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.displacementMeasurementController = displacementMeasurementController
        self.waveformDisplay = waveformDisplay
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.entry_x_offset = QDoubleSpinBox()
        self.entry_x_offset.setMinimum(0)
        self.entry_x_offset.setMaximum(3000)
        self.entry_x_offset.setSingleStep(0.2)
        self.entry_x_offset.setDecimals(3)
        self.entry_x_offset.setValue(0)
        self.entry_x_offset.setKeyboardTracking(False)

        self.entry_y_offset = QDoubleSpinBox()
        self.entry_y_offset.setMinimum(0)
        self.entry_y_offset.setMaximum(3000)
        self.entry_y_offset.setSingleStep(0.2)
        self.entry_y_offset.setDecimals(3)
        self.entry_y_offset.setValue(0)
        self.entry_y_offset.setKeyboardTracking(False)

        self.entry_x_scaling = QDoubleSpinBox()
        self.entry_x_scaling.setMinimum(-100)
        self.entry_x_scaling.setMaximum(100)
        self.entry_x_scaling.setSingleStep(0.1)
        self.entry_x_scaling.setDecimals(3)
        self.entry_x_scaling.setValue(1)
        self.entry_x_scaling.setKeyboardTracking(False)

        self.entry_y_scaling = QDoubleSpinBox()
        self.entry_y_scaling.setMinimum(-100)
        self.entry_y_scaling.setMaximum(100)
        self.entry_y_scaling.setSingleStep(0.1)
        self.entry_y_scaling.setDecimals(3)
        self.entry_y_scaling.setValue(1)
        self.entry_y_scaling.setKeyboardTracking(False)

        self.entry_N_average = QSpinBox()
        self.entry_N_average.setMinimum(1)
        self.entry_N_average.setMaximum(25)
        self.entry_N_average.setSingleStep(1)
        self.entry_N_average.setValue(1)
        self.entry_N_average.setKeyboardTracking(False)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(1)
        self.entry_N.setMaximum(5000)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(1000)
        self.entry_N.setKeyboardTracking(False)

        self.reading_x = QLabel()
        self.reading_x.setNum(0)
        self.reading_x.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.reading_y = QLabel()
        self.reading_y.setNum(0)
        self.reading_y.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("x offset"), 0, 0)
        grid_line0.addWidget(self.entry_x_offset, 0, 1)
        grid_line0.addWidget(QLabel("x scaling"), 0, 2)
        grid_line0.addWidget(self.entry_x_scaling, 0, 3)
        grid_line0.addWidget(QLabel("y offset"), 0, 4)
        grid_line0.addWidget(self.entry_y_offset, 0, 5)
        grid_line0.addWidget(QLabel("y scaling"), 0, 6)
        grid_line0.addWidget(self.entry_y_scaling, 0, 7)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("d from x"), 0, 0)
        grid_line1.addWidget(self.reading_x, 0, 1)
        grid_line1.addWidget(QLabel("d from y"), 0, 2)
        grid_line1.addWidget(self.reading_y, 0, 3)
        grid_line1.addWidget(QLabel("N average"), 0, 4)
        grid_line1.addWidget(self.entry_N_average, 0, 5)
        grid_line1.addWidget(QLabel("N"), 0, 6)
        grid_line1.addWidget(self.entry_N, 0, 7)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

        # connections
        self.entry_x_offset.valueChanged.connect(self.update_settings)
        self.entry_y_offset.valueChanged.connect(self.update_settings)
        self.entry_x_scaling.valueChanged.connect(self.update_settings)
        self.entry_y_scaling.valueChanged.connect(self.update_settings)
        self.entry_N_average.valueChanged.connect(self.update_settings)
        self.entry_N.valueChanged.connect(self.update_settings)
        self.entry_N.valueChanged.connect(self.update_waveformDisplay_N)

    def update_settings(self, new_value):
        print("update settings")
        self.displacementMeasurementController.update_settings(
            self.entry_x_offset.value(),
            self.entry_y_offset.value(),
            self.entry_x_scaling.value(),
            self.entry_y_scaling.value(),
            self.entry_N_average.value(),
            self.entry_N.value(),
        )

    def update_waveformDisplay_N(self, N):
        self.waveformDisplay.update_N(N)

    def display_readings(self, readings):
        self.reading_x.setText("{:.2f}".format(readings[0]))
        self.reading_y.setText("{:.2f}".format(readings[1]))


class LaserAutofocusControlWidget(QFrame):
    def __init__(self, laserAutofocusController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.laserAutofocusController = laserAutofocusController
        self.add_components()
        self.update_init_state()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_set_reference = QPushButton(" Set Reference ")
        self.btn_set_reference.setCheckable(False)
        self.btn_set_reference.setChecked(False)
        self.btn_set_reference.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_set_reference.setEnabled(False)

        self.label_displacement = QLabel()
        self.label_displacement.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.btn_measure_displacement = QPushButton("Measure Displacement")
        self.btn_measure_displacement.setCheckable(False)
        self.btn_measure_displacement.setChecked(False)
        self.btn_measure_displacement.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_measure_displacement.setEnabled(False)

        self.entry_target = QDoubleSpinBox()
        self.entry_target.setMinimum(-100)
        self.entry_target.setMaximum(100)
        self.entry_target.setSingleStep(0.01)
        self.entry_target.setDecimals(2)
        self.entry_target.setValue(0)
        self.entry_target.setKeyboardTracking(False)

        self.btn_move_to_target = QPushButton("Move to Target")
        self.btn_move_to_target.setCheckable(False)
        self.btn_move_to_target.setChecked(False)
        self.btn_move_to_target.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_move_to_target.setEnabled(False)

        self.grid = QGridLayout()

        self.grid.addWidget(self.btn_set_reference, 0, 0, 1, 4)

        self.grid.addWidget(QLabel("Displacement (um)"), 1, 0)
        self.grid.addWidget(self.label_displacement, 1, 1)
        self.grid.addWidget(self.btn_measure_displacement, 1, 2, 1, 2)

        self.grid.addWidget(QLabel("Target (um)"), 2, 0)
        self.grid.addWidget(self.entry_target, 2, 1)
        self.grid.addWidget(self.btn_move_to_target, 2, 2, 1, 2)
        self.setLayout(self.grid)

        # make connections
        self.btn_set_reference.clicked.connect(self.on_set_reference_clicked)
        self.btn_measure_displacement.clicked.connect(self.laserAutofocusController.measure_displacement)
        self.btn_move_to_target.clicked.connect(self.move_to_target)
        self.laserAutofocusController.signal_displacement_um.connect(self.label_displacement.setNum)

    def update_init_state(self):
        self.btn_set_reference.setEnabled(self.laserAutofocusController.is_initialized)
        self.btn_measure_displacement.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)
        self.btn_move_to_target.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)

    def move_to_target(self, target_um):
        self.laserAutofocusController.move_to_target(self.entry_target.value())

    def on_set_reference_clicked(self):
        """Handle set reference button click"""
        success = self.laserAutofocusController.set_reference()
        if success:
            self.btn_measure_displacement.setEnabled(True)
            self.btn_move_to_target.setEnabled(True)


class WellplateFormatWidget(QWidget):

    signalWellplateSettings = Signal(QVariant, float, float, int, int, float, float, int, int, int)

    def __init__(self, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.wellplate_format = WELLPLATE_FORMAT
        self.csv_path = SAMPLE_FORMATS_CSV_PATH  # 'sample_formats.csv'
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        self.label = QLabel("Sample Format", self)
        self.comboBox = QComboBox(self)
        self.populate_combo_box()
        self.comboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.label)
        layout.addWidget(self.comboBox)
        self.comboBox.currentIndexChanged.connect(self.wellplateChanged)
        index = self.comboBox.findData(self.wellplate_format)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)

    def populate_combo_box(self):
        self.comboBox.clear()
        for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
            self.comboBox.addItem(format_, format_)

        # Add custom item and set its font to italic
        self.comboBox.addItem("calibrate format...", "custom")
        index = self.comboBox.count() - 1  # Get the index of the last item
        font = QFont()
        font.setItalic(True)
        self.comboBox.setItemData(index, font, Qt.FontRole)

    def wellplateChanged(self, index):
        self.wellplate_format = self.comboBox.itemData(index)
        if self.wellplate_format == "custom":
            calibration_dialog = WellplateCalibration(
                self, self.stage, self.navigationViewer, self.streamHandler, self.liveController
            )
            result = calibration_dialog.exec_()
            if result == QDialog.Rejected:
                # If the dialog was closed without adding a new format, revert to the previous selection
                prev_index = self.comboBox.findData(self.wellplate_format)
                self.comboBox.setCurrentIndex(prev_index)
        else:
            self.setWellplateSettings(self.wellplate_format)

    def setWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            self.signalWellplateSettings.emit(QVariant("glass slide"), 0, 0, 0, 0, 0, 0, 0, 1, 1)
            return
        else:
            print(f"Wellplate format {wellplate_format} not recognized")
            return

        self.signalWellplateSettings.emit(
            QVariant(wellplate_format),
            settings["a1_x_mm"],
            settings["a1_y_mm"],
            settings["a1_x_pixel"],
            settings["a1_y_pixel"],
            settings["well_size_mm"],
            settings["well_spacing_mm"],
            settings["number_of_skip"],
            settings["rows"],
            settings["cols"],
        )

    def getWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            settings = {
                "format": "glass slide",
                "a1_x_mm": 0,
                "a1_y_mm": 0,
                "a1_x_pixel": 0,
                "a1_y_pixel": 0,
                "well_size_mm": 0,
                "well_spacing_mm": 0,
                "number_of_skip": 0,
                "rows": 1,
                "cols": 1,
            }
        else:
            return None
        return settings

    def add_custom_format(self, name, settings):
        WELLPLATE_FORMAT_SETTINGS[name] = settings
        self.populate_combo_box()
        index = self.comboBox.findData(name)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)
        self.wellplateChanged(index)

    def save_formats_to_csv(self):
        cache_path = os.path.join("cache", self.csv_path)
        os.makedirs("cache", exist_ok=True)

        fieldnames = [
            "format",
            "a1_x_mm",
            "a1_y_mm",
            "a1_x_pixel",
            "a1_y_pixel",
            "well_size_mm",
            "well_spacing_mm",
            "number_of_skip",
            "rows",
            "cols",
        ]
        with open(cache_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
                writer.writerow({**{"format": format_}, **settings})

    @staticmethod
    def parse_csv_row(row):
        return {
            "a1_x_mm": float(row["a1_x_mm"]),
            "a1_y_mm": float(row["a1_y_mm"]),
            "a1_x_pixel": int(row["a1_x_pixel"]),
            "a1_y_pixel": int(row["a1_y_pixel"]),
            "well_size_mm": float(row["well_size_mm"]),
            "well_spacing_mm": float(row["well_spacing_mm"]),
            "number_of_skip": int(row["number_of_skip"]),
            "rows": int(row["rows"]),
            "cols": int(row["cols"]),
        }


class WellplateCalibration(QDialog):

    def __init__(self, wellplateFormatWidget, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.setWindowTitle("Well Plate Calibration")
        self.wellplateFormatWidget = wellplateFormatWidget
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.was_live = self.liveController.is_live
        self.corners = [None, None, None]
        self.show_virtual_joystick = True  # FLAG
        self.initUI()
        # Initially allow click-to-move and hide the joystick controls
        self.clickToMoveCheckbox.setChecked(True)
        self.toggleVirtualJoystick(False)

    def initUI(self):
        layout = QHBoxLayout(self)  # Change to QHBoxLayout to have two columns

        # Left column for existing controls
        left_layout = QVBoxLayout()

        # Add radio buttons for selecting mode
        self.mode_group = QButtonGroup(self)
        self.new_format_radio = QRadioButton("Add New Format")
        self.calibrate_format_radio = QRadioButton("Calibrate Existing Format")
        self.mode_group.addButton(self.new_format_radio)
        self.mode_group.addButton(self.calibrate_format_radio)
        self.new_format_radio.setChecked(True)

        left_layout.addWidget(self.new_format_radio)
        left_layout.addWidget(self.calibrate_format_radio)

        # Existing format selection (initially hidden)
        self.existing_format_combo = QComboBox(self)
        self.populate_existing_formats()
        self.existing_format_combo.hide()
        left_layout.addWidget(self.existing_format_combo)

        # Connect radio buttons to toggle visibility
        self.new_format_radio.toggled.connect(self.toggle_input_mode)
        self.calibrate_format_radio.toggled.connect(self.toggle_input_mode)

        self.form_layout = QFormLayout()

        self.nameInput = QLineEdit(self)
        self.nameInput.setPlaceholderText("custom well plate")
        self.form_layout.addRow("Sample Name:", self.nameInput)

        self.rowsInput = QSpinBox(self)
        self.rowsInput.setRange(1, 100)
        self.rowsInput.setValue(8)
        self.form_layout.addRow("# Rows:", self.rowsInput)

        self.colsInput = QSpinBox(self)
        self.colsInput.setRange(1, 100)
        self.colsInput.setValue(12)
        self.form_layout.addRow("# Columns:", self.colsInput)

        # Add new inputs for plate dimensions
        self.plateWidthInput = QDoubleSpinBox(self)
        self.plateWidthInput.setRange(10, 500)  # Adjust range as needed
        self.plateWidthInput.setValue(127.76)  # Default value for a standard 96-well plate
        self.plateWidthInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Width:", self.plateWidthInput)

        self.plateHeightInput = QDoubleSpinBox(self)
        self.plateHeightInput.setRange(10, 500)  # Adjust range as needed
        self.plateHeightInput.setValue(85.48)  # Default value for a standard 96-well plate
        self.plateHeightInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Height:", self.plateHeightInput)

        self.wellSpacingInput = QDoubleSpinBox(self)
        self.wellSpacingInput.setRange(0.1, 100)
        self.wellSpacingInput.setValue(9)
        self.wellSpacingInput.setSingleStep(0.1)
        self.wellSpacingInput.setDecimals(2)
        self.wellSpacingInput.setSuffix(" mm")
        self.form_layout.addRow("Well Spacing:", self.wellSpacingInput)

        left_layout.addLayout(self.form_layout)

        points_layout = QGridLayout()
        self.cornerLabels = []
        self.setPointButtons = []
        navigate_label = QLabel("Navigate to and Select\n3 Points on the Edge of Well A1")
        navigate_label.setAlignment(Qt.AlignCenter)
        # navigate_label.setStyleSheet("font-weight: bold;")
        points_layout.addWidget(navigate_label, 0, 0, 1, 2)
        for i in range(1, 4):
            label = QLabel(f"Point {i}: N/A")
            button = QPushButton("Set Point")
            button.setFixedWidth(button.sizeHint().width())
            button.clicked.connect(lambda checked, index=i - 1: self.setCorner(index))
            points_layout.addWidget(label, i, 0)
            points_layout.addWidget(button, i, 1)
            self.cornerLabels.append(label)
            self.setPointButtons.append(button)

        points_layout.setColumnStretch(0, 1)
        left_layout.addLayout(points_layout)

        # Add 'Click to Move' checkbox
        self.clickToMoveCheckbox = QCheckBox("Click to Move")
        self.clickToMoveCheckbox.stateChanged.connect(self.toggleClickToMove)
        left_layout.addWidget(self.clickToMoveCheckbox)

        # Add 'Show Virtual Joystick' checkbox
        self.showJoystickCheckbox = QCheckBox("Virtual Joystick")
        self.showJoystickCheckbox.stateChanged.connect(self.toggleVirtualJoystick)
        left_layout.addWidget(self.showJoystickCheckbox)

        self.calibrateButton = QPushButton("Calibrate")
        self.calibrateButton.clicked.connect(self.calibrate)
        self.calibrateButton.setEnabled(False)
        left_layout.addWidget(self.calibrateButton)

        # Add left column to main layout
        layout.addLayout(left_layout)

        self.live_viewer = CalibrationLiveViewer(parent=self)
        self.streamHandler.image_to_display.connect(self.live_viewer.display_image)

        if not self.was_live:
            self.liveController.start_live()

        # when the dialog closes i want to # self.liveController.stop_live() if live was stopped before. . . if it was on before, leave it on
        layout.addWidget(self.live_viewer)

        # Right column for joystick and sensitivity controls
        self.right_layout = QVBoxLayout()
        self.right_layout.addStretch(1)

        self.joystick = Joystick(self)
        self.joystick.joystickMoved.connect(self.moveStage)
        self.right_layout.addWidget(self.joystick, 0, Qt.AlignTop | Qt.AlignHCenter)

        self.right_layout.addStretch(1)

        # Create a container widget for sensitivity label and slider
        sensitivity_layout = QVBoxLayout()

        sensitivityLabel = QLabel("Joystick Sensitivity")
        sensitivityLabel.setAlignment(Qt.AlignCenter)
        sensitivity_layout.addWidget(sensitivityLabel)

        self.sensitivitySlider = QSlider(Qt.Horizontal)
        self.sensitivitySlider.setMinimum(1)
        self.sensitivitySlider.setMaximum(100)
        self.sensitivitySlider.setValue(50)
        self.sensitivitySlider.setTickPosition(QSlider.TicksBelow)
        self.sensitivitySlider.setTickInterval(10)

        label_width = sensitivityLabel.sizeHint().width()
        self.sensitivitySlider.setFixedWidth(label_width)

        sensitivity_layout.addWidget(self.sensitivitySlider, 0, Qt.AlignHCenter)

        self.right_layout.addLayout(sensitivity_layout)

        layout.addLayout(self.right_layout)

        if not self.was_live:
            self.liveController.start_live()

    def toggleVirtualJoystick(self, state):
        if state:
            self.joystick.show()
            self.sensitivitySlider.show()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().show()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().show()  # Show sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().show()  # Show sensitivity slider
        else:
            self.joystick.hide()
            self.sensitivitySlider.hide()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().hide()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().hide()  # Hide sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().hide()  # Hide sensitivity slider

    def moveStage(self, x, y):
        sensitivity = self.sensitivitySlider.value() / 50.0  # Normalize to 0-2 range
        max_speed = 0.1 * sensitivity
        exponent = 2

        dx = math.copysign(max_speed * abs(x) ** exponent, x)
        dy = math.copysign(max_speed * abs(y) ** exponent, y)

        self.stage.move_x(dx)
        self.stage.move_y(dy)

    def toggleClickToMove(self, state):
        # TODO(imo): I broke click to move with the navigation controller rip out
        # if state == Qt.Checked:
        #     self.navigationController.set_flag_click_to_move(True)
        #     self.live_viewer.signal_calibration_viewer_click.connect(self.viewerClicked)
        # else:
        #     self.live_viewer.signal_calibration_viewer_click.disconnect(self.viewerClicked)
        #     self.navigationController.set_flag_click_to_move(False)
        pass

    def viewerClicked(self, x, y, width, height):
        # TODO(imo): I broke click to move with the navigation controller rip out
        # if self.clickToMoveCheckbox.isChecked():
        #     self.navigationController.move_from_click(x, y, width, height)
        pass

    def setCorner(self, index):
        if self.corners[index] is None:
            pos = self.stage.get_pos()
            x = pos.x_mm
            y = pos.y_mm

            # Check if the new point is different from existing points
            if any(corner is not None and np.allclose([x, y], corner) for corner in self.corners):
                QMessageBox.warning(
                    self,
                    "Duplicate Point",
                    "This point is too close to an existing point. Please choose a different location.",
                )
                return

            self.corners[index] = (x, y)
            self.cornerLabels[index].setText(f"Point {index+1}: ({x:.2f}, {y:.2f})")
            self.setPointButtons[index].setText("Clear Point")
        else:
            self.corners[index] = None
            self.cornerLabels[index].setText(f"Point {index+1}: Not set")
            self.setPointButtons[index].setText("Set Point")

        self.calibrateButton.setEnabled(all(corner is not None for corner in self.corners))

    def populate_existing_formats(self):
        self.existing_format_combo.clear()
        for format_ in WELLPLATE_FORMAT_SETTINGS:
            self.existing_format_combo.addItem(f"{format_} well plate", format_)

    def toggle_input_mode(self):
        if self.new_format_radio.isChecked():
            self.existing_format_combo.hide()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().show()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().show()
        else:
            self.existing_format_combo.show()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().hide()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().hide()

    def calibrate(self):
        try:
            if self.new_format_radio.isChecked():
                if not self.nameInput.text() or not all(self.corners):
                    QMessageBox.warning(
                        self,
                        "Incomplete Information",
                        "Please fill in all fields and set 3 corner points before calibrating.",
                    )
                    return

                name = self.nameInput.text()
                rows = self.rowsInput.value()
                cols = self.colsInput.value()
                well_spacing_mm = self.wellSpacingInput.value()
                plate_width_mm = self.plateWidthInput.value()
                plate_height_mm = self.plateHeightInput.value()

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center
                scale = 1 / 0.084665
                a1_x_pixel = round(a1_x_mm * scale)
                a1_y_pixel = round(a1_y_mm * scale)

                new_format = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "a1_x_pixel": a1_x_pixel,
                    "a1_y_pixel": a1_y_pixel,
                    "well_size_mm": well_size_mm,
                    "well_spacing_mm": well_spacing_mm,
                    "number_of_skip": 0,
                    "rows": rows,
                    "cols": cols,
                }

                self.wellplateFormatWidget.add_custom_format(name, new_format)
                self.wellplateFormatWidget.save_formats_to_csv()
                self.create_wellplate_image(name, new_format, plate_width_mm, plate_height_mm)
                self.wellplateFormatWidget.setWellplateSettings(name)
                success_message = f"New format '{name}' has been successfully created and calibrated."

            else:
                selected_format = self.existing_format_combo.currentData()
                if not all(self.corners):
                    QMessageBox.warning(
                        self, "Incomplete Information", "Please set 3 corner points before calibrating."
                    )
                    return

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center

                # Get the existing format settings
                existing_settings = WELLPLATE_FORMAT_SETTINGS[selected_format]

                print(f"Updating existing format {selected_format} well plate")
                print(
                    f"OLD: 'a1_x_mm': {existing_settings['a1_x_mm']}, 'a1_y_mm': {existing_settings['a1_y_mm']}, 'well_size_mm': {existing_settings['well_size_mm']}"
                )
                print(f"NEW: 'a1_x_mm': {a1_x_mm}, 'a1_y_mm': {a1_y_mm}, 'well_size_mm': {well_size_mm}")

                updated_settings = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "well_size_mm": well_size_mm,
                }

                WELLPLATE_FORMAT_SETTINGS[selected_format].update(updated_settings)

                self.wellplateFormatWidget.save_formats_to_csv()
                self.wellplateFormatWidget.setWellplateSettings(selected_format)
                success_message = f"Format '{selected_format} well plate' has been successfully recalibrated."

            # Update the WellplateFormatWidget's combo box to reflect the newly calibrated format
            self.wellplateFormatWidget.populate_combo_box()
            index = self.wellplateFormatWidget.comboBox.findData(
                selected_format if self.calibrate_format_radio.isChecked() else name
            )
            if index >= 0:
                self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

            # Display success message
            QMessageBox.information(self, "Calibration Successful", success_message)
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Calibration Error", f"An error occurred during calibration: {str(e)}")

    def create_wellplate_image(self, name, format_data, plate_width_mm, plate_height_mm):

        scale = 1 / 0.084665

        def mm_to_px(mm):
            return round(mm * scale)

        width = mm_to_px(plate_width_mm)
        height = mm_to_px(plate_height_mm)
        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        rows, cols = format_data["rows"], format_data["cols"]
        well_spacing_mm = format_data["well_spacing_mm"]
        well_size_mm = format_data["well_size_mm"]
        a1_x_mm, a1_y_mm = format_data["a1_x_mm"], format_data["a1_y_mm"]

        def draw_left_slanted_rectangle(draw, xy, slant, width=4, outline="black", fill=None):
            x1, y1, x2, y2 = xy

            # Define the polygon points
            points = [
                (x1 + slant, y1),  # Top-left after slant
                (x2, y1),  # Top-right
                (x2, y2),  # Bottom-right
                (x1 + slant, y2),  # Bottom-left after slant
                (x1, y2 - slant),  # Bottom of left slant
                (x1, y1 + slant),  # Top of left slant
            ]

            # Draw the filled polygon with outline
            draw.polygon(points, fill=fill, outline=outline, width=width)

        # Draw the outer rectangle with rounded corners
        corner_radius = 20
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=corner_radius, outline="black", width=4, fill="grey"
        )

        # Draw the inner rectangle with left slanted corners
        margin = 20
        slant = 40
        draw_left_slanted_rectangle(
            draw, [margin, margin, width - margin, height - margin], slant, width=4, outline="black", fill="lightgrey"
        )

        # Function to draw a circle
        def draw_circle(x, y, diameter):
            radius = diameter / 2
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline="black", width=4, fill="white")

        # Draw the wells
        for row in range(rows):
            for col in range(cols):
                x = mm_to_px(a1_x_mm + col * well_spacing_mm)
                y = mm_to_px(a1_y_mm + row * well_spacing_mm)
                draw_circle(x, y, mm_to_px(well_size_mm))

        # Load a default font
        font_size = 30
        font = ImageFont.load_default().font_variant(size=font_size)

        # Add column labels
        for col in range(cols):
            label = str(col + 1)
            x = mm_to_px(a1_x_mm + col * well_spacing_mm)
            y = mm_to_px((a1_y_mm - well_size_mm / 2) / 2)
            bbox = font.getbbox(label)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.text((x - text_width / 2, y), label, fill="black", font=font)

        # Add row labels
        for row in range(rows):
            label = chr(65 + row) if row < 26 else chr(65 + row // 26 - 1) + chr(65 + row % 26)
            x = mm_to_px((a1_x_mm - well_size_mm / 2) / 2)
            y = mm_to_px(a1_y_mm + row * well_spacing_mm)
            bbox = font.getbbox(label)
            text_height = bbox[3] - bbox[1]
            text_width = bbox[2] - bbox[0]
            draw.text((x + 20 - text_width / 2, y - text_height + 1), label, fill="black", font=font)

        image_path = os.path.join("images", f'{name.replace(" ", "_")}.png')
        image.save(image_path)
        print(f"Wellplate image saved as {image_path}")
        return image_path

    @staticmethod
    def calculate_circle(points):
        # Convert points to numpy array
        points = np.array(points)

        # Calculate the center and radius of the circle
        A = np.array([points[1] - points[0], points[2] - points[0]])
        b = np.sum(A * (points[1:3] + points[0]) / 2, axis=1)
        center = np.linalg.solve(A, b)

        # Calculate the radius
        radius = np.mean(np.linalg.norm(points - center, axis=1))

        return center, radius

    def closeEvent(self, event):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().closeEvent(event)

    def accept(self):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().accept()

    def reject(self):
        # This method is called when the dialog is closed without accepting
        if not self.was_live:
            self.liveController.stop_live()
        sample = self.navigationViewer.sample

        # Convert sample string to format int
        if "glass slide" in sample:
            sample_format = "glass slide"
        else:
            try:
                sample_format = int(sample.split()[0])
            except (ValueError, IndexError):
                print(f"Unable to parse sample format from '{sample}'. Defaulting to 0.")
                sample_format = "glass slide"

        # Set dropdown to the current sample format
        index = self.wellplateFormatWidget.comboBox.findData(sample_format)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

        # Update wellplate settings
        self.wellplateFormatWidget.setWellplateSettings(sample_format)

        super().reject()


class CalibrationLiveViewer(QWidget):

    signal_calibration_viewer_click = Signal(int, int, int, int)
    signal_mouse_moved = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.initial_zoom_set = False
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = pg.GraphicsLayoutWidget()
        self.viewbox = self.view.addViewBox()
        self.viewbox.setAspectLocked(True)
        self.viewbox.invertY(True)

        # Set appropriate panning limits based on the acquisition image or plate size
        xmax = int(CAMERA_CONFIG.CROP_WIDTH_UNBINNED)
        ymax = int(CAMERA_CONFIG.CROP_HEIGHT_UNBINNED)
        self.viewbox.setLimits(xMin=0, xMax=xmax, yMin=0, yMax=ymax)

        self.img_item = pg.ImageItem()
        self.viewbox.addItem(self.img_item)

        # Add fixed crosshair
        pen = QPen(QColor(255, 0, 0))  # Red color
        pen.setWidth(4)

        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.viewbox.addItem(self.crosshair_h)
        self.viewbox.addItem(self.crosshair_v)

        layout.addWidget(self.view)

        # Connect double-click event
        self.view.scene().sigMouseClicked.connect(self.onMouseClicked)

        # Set fixed size for the viewer
        self.setFixedSize(500, 500)

        # Initialize with a blank image
        self.display_image(np.zeros((xmax, ymax)))

    def setCrosshairPosition(self):
        center = self.viewbox.viewRect().center()
        self.crosshair_h.setPos(center.y())
        self.crosshair_v.setPos(center.x())

    def display_image(self, image):
        # Step 1: Update the image
        self.img_item.setImage(image)

        # Step 2: Get the image dimensions
        image_width = image.shape[1]
        image_height = image.shape[0]

        # Step 3: Calculate the center of the image
        image_center_x = image_width / 2
        image_center_y = image_height / 2

        # Step 4: Calculate the current view range
        current_view_range = self.viewbox.viewRect()

        # Step 5: If it's the first image or initial zoom hasn't been set, center the image
        if not self.initial_zoom_set:
            self.viewbox.setRange(xRange=(0, image_width), yRange=(0, image_height), padding=0)
            self.initial_zoom_set = True  # Mark initial zoom as set

        # Step 6: Always center the view around the image center (for seamless transitions)
        else:
            self.viewbox.setRange(
                xRange=(
                    image_center_x - current_view_range.width() / 2,
                    image_center_x + current_view_range.width() / 2,
                ),
                yRange=(
                    image_center_y - current_view_range.height() / 2,
                    image_center_y + current_view_range.height() / 2,
                ),
                padding=0,
            )

        # Step 7: Ensure the crosshair is updated
        self.setCrosshairPosition()

    # def mouseMoveEvent(self, event):
    #     self.signal_mouse_moved.emit(event.x(), event.y())

    def onMouseClicked(self, event):
        # Map the scene position to view position
        if event.double():  # double click to move
            pos = event.pos()
            scene_pos = self.viewbox.mapSceneToView(pos)

            # Get the x, y coordinates
            x, y = int(scene_pos.x()), int(scene_pos.y())
            # Ensure the coordinates are within the image boundaries
            image_shape = self.img_item.image.shape
            if 0 <= x < image_shape[1] and 0 <= y < image_shape[0]:
                # Adjust the coordinates to be relative to the center of the image
                x_centered = x - image_shape[1] // 2
                y_centered = y - image_shape[0] // 2
                # Emit the signal with the clicked coordinates and image size
                self.signal_calibration_viewer_click.emit(x_centered, y_centered, image_shape[1], image_shape[0])
            else:
                print("click was outside the image bounds.")
        else:
            print("single click only detected")

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            scale_factor = 0.9
        else:
            scale_factor = 1.1

        # Get the center of the viewbox
        center = self.viewbox.viewRect().center()

        # Scale the view
        self.viewbox.scaleBy((scale_factor, scale_factor), center)

        # Update crosshair position after scaling
        self.setCrosshairPosition()

        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setCrosshairPosition()


class Joystick(QWidget):
    joystickMoved = Signal(float, float)  # Emits x and y values between -1 and 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.inner_radius = 40
        self.max_distance = self.width() // 2 - self.inner_radius
        self.outer_radius = int(self.width() * 3 / 8)
        self.current_x = 0
        self.current_y = 0
        self.is_pressed = False
        self.timer = QTimer(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate the painting area
        paint_rect = QRectF(0, 0, 200, 200)

        # Draw outer circle
        painter.setBrush(QColor(230, 230, 230))  # Light grey fill
        painter.setPen(QPen(QColor(100, 100, 100), 2))  # Dark grey outline
        painter.drawEllipse(paint_rect.center(), self.outer_radius, self.outer_radius)

        # Draw inner circle (joystick position)
        painter.setBrush(QColor(100, 100, 100))
        painter.setPen(Qt.NoPen)
        joystick_x = paint_rect.center().x() + self.current_x * self.max_distance
        joystick_y = paint_rect.center().y() + self.current_y * self.max_distance
        painter.drawEllipse(QPointF(joystick_x, joystick_y), self.inner_radius, self.inner_radius)

    def mousePressEvent(self, event):
        if QRectF(0, 0, 200, 200).contains(event.pos()):
            self.is_pressed = True
            self.updateJoystickPosition(event.pos())
            self.timer.timeout.connect(self.update_position)
            self.timer.start(10)

    def mouseMoveEvent(self, event):
        if self.is_pressed and QRectF(0, 0, 200, 200).contains(event.pos()):
            self.updateJoystickPosition(event.pos())

    def mouseReleaseEvent(self, event):
        self.is_pressed = False
        self.updateJoystickPosition(QPointF(100, 100))  # Center position
        self.timer.timeout.disconnect(self.update_position)
        self.joystickMoved.emit(0, 0)

    def update_position(self):
        if self.is_pressed:
            self.joystickMoved.emit(self.current_x, -self.current_y)

    def updateJoystickPosition(self, pos):
        center = QPointF(100, 100)
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        distance = math.sqrt(dx**2 + dy**2)

        if distance > self.max_distance:
            dx = dx * self.max_distance / distance
            dy = dy * self.max_distance / distance

        self.current_x = dx / self.max_distance
        self.current_y = dy / self.max_distance
        self.update()


class WellSelectionWidget(QTableWidget):

    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self, format_, wellplateFormatWidget, *args, **kwargs):
        super(WellSelectionWidget, self).__init__(*args, **kwargs)
        self.wellplateFormatWidget = wellplateFormatWidget
        self.cellDoubleClicked.connect(self.onDoubleClick)
        self.itemSelectionChanged.connect(self.onSelectionChanged)
        self.fixed_height = 400
        self.setFormat(format_)

    def setFormat(self, format_):
        self.format = format_
        settings = self.wellplateFormatWidget.getWellplateSettings(self.format)
        self.rows = settings["rows"]
        self.columns = settings["cols"]
        self.spacing_mm = settings["well_spacing_mm"]
        self.number_of_skip = settings["number_of_skip"]
        self.a1_x_mm = settings["a1_x_mm"]
        self.a1_y_mm = settings["a1_y_mm"]
        self.a1_x_pixel = settings["a1_x_pixel"]
        self.a1_y_pixel = settings["a1_y_pixel"]
        self.well_size_mm = settings["well_size_mm"]

        self.setRowCount(self.rows)
        self.setColumnCount(self.columns)
        self.initUI()
        self.setData()

    def initUI(self):
        # Disable editing, scrollbars, and other interactions
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setDisabled(True)
        self.horizontalScrollBar().setDisabled(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTabKeyNavigation(False)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropOverwriteMode(False)
        self.setMouseTracking(False)

        if self.format == "1536 well plate":
            font = QFont()
            font.setPointSize(6)  # You can adjust this value as needed
        else:
            font = QFont()
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)

        self.setLayout()

    def setLayout(self):
        # Calculate available space and cell size
        header_height = self.horizontalHeader().height()
        available_height = self.fixed_height - header_height  # Fixed height of 408 pixels

        # Calculate cell size based on the minimum of available height and width
        cell_size = available_height // self.rowCount()

        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(cell_size)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setDefaultSectionSize(cell_size)

        # Ensure sections do not resize
        self.verticalHeader().setMinimumSectionSize(cell_size)
        self.verticalHeader().setMaximumSectionSize(cell_size)
        self.horizontalHeader().setMinimumSectionSize(cell_size)
        self.horizontalHeader().setMaximumSectionSize(cell_size)

        row_header_width = self.verticalHeader().width()

        # Calculate total width and height
        total_height = (self.rowCount() * cell_size) + header_height
        total_width = (self.columnCount() * cell_size) + row_header_width

        # Set the widget's fixed size
        self.setFixedHeight(total_height)
        self.setFixedWidth(total_width)

        # Force the widget to update its layout
        self.updateGeometry()
        self.viewport().update()

    def onWellplateChanged(self):
        self.setFormat(self.wellplateFormatWidget.wellplate_format)

    def setData(self):
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                item = self.item(i, j)
                if not item:  # Create a new item if none exists
                    item = QTableWidgetItem()
                    self.setItem(i, j, item)
                # Reset to selectable by default
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        if self.number_of_skip > 0 and self.format != 0:
            for i in range(self.number_of_skip):
                for j in range(self.columns):  # Apply to rows
                    self.item(i, j).setFlags(self.item(i, j).flags() & ~Qt.ItemIsSelectable)
                    self.item(self.rows - 1 - i, j).setFlags(
                        self.item(self.rows - 1 - i, j).flags() & ~Qt.ItemIsSelectable
                    )
                for k in range(self.rows):  # Apply to columns
                    self.item(k, i).setFlags(self.item(k, i).flags() & ~Qt.ItemIsSelectable)
                    self.item(k, self.columns - 1 - i).setFlags(
                        self.item(k, self.columns - 1 - i).flags() & ~Qt.ItemIsSelectable
                    )

        # Update row headers
        row_headers = []
        for i in range(self.rows):
            if i < 26:
                label = chr(ord("A") + i)
            else:
                first_letter = chr(ord("A") + (i // 26) - 1)
                second_letter = chr(ord("A") + (i % 26))
                label = first_letter + second_letter
            row_headers.append(label)
        self.setVerticalHeaderLabels(row_headers)

        # Adjust vertical header width after setting labels
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def onDoubleClick(self, row, col):
        print("double click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)
            print("well location:", (x_mm, y_mm))
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSingleClick(self, row, col):
        print("single click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSelectionChanged(self):
        # Check if there are any selected indexes before proceeding
        if self.format != "glass slide":
            has_selection = bool(self.selectedIndexes())
            self.signal_wellSelected.emit(has_selection)

    def get_selected_cells(self):
        list_of_selected_cells = []
        print("getting selected cells...")
        if self.format == "glass slide":
            return list_of_selected_cells
        for index in self.selectedIndexes():
            row, col = index.row(), index.column()
            # Check if the cell is within the allowed bounds
            if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
                col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
            ):
                list_of_selected_cells.append((row, col))
        if list_of_selected_cells:
            print("cells:", list_of_selected_cells)
        else:
            print("no cells")
        return list_of_selected_cells

    def resizeEvent(self, event):
        self.initUI()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        # Ignore wheel events to prevent scrolling
        event.ignore()

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        pass

    def set_white_boundaries_style(self):
        style = """
        QTableWidget {
            gridline-color: white;
            border: 1px solid white;
        }
        QHeaderView::section {
            color: white;
        }
        """
        # QTableWidget::item {
        #     border: 1px solid white;
        # }
        self.setStyleSheet(style)


class Well1536SelectionWidget(QWidget):

    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.format = "1536 well plate"
        self.selected_cells = {}  # Dictionary to keep track of selected cells and their colors
        self.current_cell = None  # To track the current (green) cell
        self.rows = 32
        self.columns = 48
        self.spacing_mm = 2.25
        self.number_of_skip = 0
        self.well_size_mm = 1.5
        self.a1_x_mm = 11.0  # measured stage position - to update
        self.a1_y_mm = 7.86  # measured stage position - to update
        self.a1_x_pixel = 144  # coordinate on the png - to update
        self.a1_y_pixel = 108  # coordinate on the png - to update
        self.initUI()

    def initUI(self):
        self.setWindowTitle("1536 Well Plate")
        self.setGeometry(100, 100, 750, 400)  # Increased width to accommodate controls

        self.a = 11
        image_width = 48 * self.a
        image_height = 32 * self.a

        self.image = QPixmap(image_width, image_height)
        self.image.fill(QColor("white"))
        self.label = QLabel()
        self.label.setPixmap(self.image)
        self.label.setFixedSize(image_width, image_height)
        self.label.setAlignment(Qt.AlignCenter)

        self.cell_input = QLineEdit(self)
        self.cell_input.setPlaceholderText("e.g. AE12 or B4")
        go_button = QPushButton("Go to well", self)
        go_button.clicked.connect(self.go_to_cell)
        self.selection_input = QLineEdit(self)
        self.selection_input.setPlaceholderText("e.g. A1:E48, X1, AC24, Z2:AF6, ...")
        self.selection_input.editingFinished.connect(self.select_cells)

        # Create navigation buttons
        up_button = QPushButton("↑", self)
        left_button = QPushButton("←", self)
        right_button = QPushButton("→", self)
        down_button = QPushButton("↓", self)
        add_button = QPushButton("Select", self)

        # Connect navigation buttons to their respective functions
        up_button.clicked.connect(self.move_up)
        left_button.clicked.connect(self.move_left)
        right_button.clicked.connect(self.move_right)
        down_button.clicked.connect(self.move_down)
        add_button.clicked.connect(self.add_current_well)

        layout = QHBoxLayout()
        layout.addWidget(self.label)

        layout_controls = QVBoxLayout()
        layout_controls.addStretch(2)

        # Add navigation buttons in a + sign layout
        layout_move = QGridLayout()
        layout_move.addWidget(up_button, 0, 2)
        layout_move.addWidget(left_button, 1, 1)
        layout_move.addWidget(add_button, 1, 2)
        layout_move.addWidget(right_button, 1, 3)
        layout_move.addWidget(down_button, 2, 2)
        layout_move.setColumnStretch(0, 1)
        layout_move.setColumnStretch(4, 1)
        layout_controls.addLayout(layout_move)

        layout_controls.addStretch(1)

        layout_input = QGridLayout()
        layout_input.addWidget(QLabel("Well Navigation"), 0, 0)
        layout_input.addWidget(self.cell_input, 0, 1)
        layout_input.addWidget(go_button, 0, 2)
        layout_input.addWidget(QLabel("Well Selection"), 1, 0)
        layout_input.addWidget(self.selection_input, 1, 1, 1, 2)
        layout_controls.addLayout(layout_input)

        control_widget = QWidget()
        control_widget.setLayout(layout_controls)
        control_widget.setFixedHeight(image_height)  # Set the height of controls to match the image

        layout.addWidget(control_widget)
        self.setLayout(layout)

    def move_up(self):
        if self.current_cell:
            row, col = self.current_cell
            if row > 0:
                self.current_cell = (row - 1, col)
                self.update_current_cell()

    def move_left(self):
        if self.current_cell:
            row, col = self.current_cell
            if col > 0:
                self.current_cell = (row, col - 1)
                self.update_current_cell()

    def move_right(self):
        if self.current_cell:
            row, col = self.current_cell
            if col < self.columns - 1:
                self.current_cell = (row, col + 1)
                self.update_current_cell()

    def move_down(self):
        if self.current_cell:
            row, col = self.current_cell
            if row < self.rows - 1:
                self.current_cell = (row + 1, col)
                self.update_current_cell()

    def add_current_well(self):
        if self.current_cell:
            row, col = self.current_cell
            cell_name = f"{chr(65 + row)}{col + 1}"

            if (row, col) in self.selected_cells:
                # If the well is already selected, remove it
                del self.selected_cells[(row, col)]
                self.remove_well_from_selection_input(cell_name)
                print(f"Removed well {cell_name}")
            else:
                # If the well is not selected, add it
                self.selected_cells[(row, col)] = "#1f77b4"  # Add to selected cells with blue color
                self.add_well_to_selection_input(cell_name)
                print(f"Added well {cell_name}")

            self.redraw_wells()
            self.signal_wellSelected.emit(bool(self.selected_cells))

    def add_well_to_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        if current_selection:
            self.selection_input.setText(f"{current_selection}, {cell_name}")
        else:
            self.selection_input.setText(cell_name)

    def remove_well_from_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        cells = [cell.strip() for cell in current_selection.split(",")]
        if cell_name in cells:
            cells.remove(cell_name)
            self.selection_input.setText(", ".join(cells))

    def update_current_cell(self):
        self.redraw_wells()
        row, col = self.current_cell
        if row < 26:
            row_label = chr(65 + row)
        else:
            row_label = chr(64 + (row // 26)) + chr(65 + (row % 26))
        # Update cell_input with the correct label (e.g., A1, B2, AA1, etc.)
        self.cell_input.setText(f"{row_label}{col + 1}")

        x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
        y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
        self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def redraw_wells(self):
        self.image.fill(QColor("white"))  # Clear the pixmap first
        painter = QPainter(self.image)
        painter.setPen(QColor("white"))
        # Draw selected cells in red
        for (row, col), color in self.selected_cells.items():
            painter.setBrush(QColor(color))
            painter.drawRect(col * self.a, row * self.a, self.a, self.a)
        # Draw current cell in green
        if self.current_cell:
            painter.setBrush(Qt.NoBrush)  # No fill
            painter.setPen(QPen(QColor("red"), 2))  # Red outline, 2 pixels wide
            row, col = self.current_cell
            painter.drawRect(col * self.a + 2, row * self.a + 2, self.a - 3, self.a - 3)
        painter.end()
        self.label.setPixmap(self.image)

    def go_to_cell(self):
        cell_desc = self.cell_input.text().strip()
        match = re.match(r"([A-Za-z]+)(\d+)", cell_desc)
        if match:
            row_part, col_part = match.groups()
            row_index = self.row_to_index(row_part)
            col_index = int(col_part) - 1
            self.current_cell = (row_index, col_index)  # Update the current cell
            self.redraw_wells()  # Redraw with the new current cell
            x_mm = col_index * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row_index * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def select_cells(self):
        # first clear selection
        self.selected_cells = {}

        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        cell_descriptions = self.selection_input.text().split(",")
        for desc in cell_descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = self.row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = self.row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(min(start_row_index, end_row_index), max(start_row_index, end_row_index) + 1):
                        for col in range(min(start_col_index, end_col_index), max(start_col_index, end_col_index) + 1):
                            self.selected_cells[(row, col)] = "#1f77b4"
                else:  # It's a single cell
                    self.selected_cells[(start_row_index, start_col_index)] = "#1f77b4"
        self.redraw_wells()
        if self.selected_cells:
            self.signal_wellSelected.emit(True)

    def row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def onSelectionChanged(self):
        self.get_selected_cells()

    def onWellplateChanged(self):
        """A placeholder to match the method in WellSelectionWidget"""
        pass

    def get_selected_cells(self):
        list_of_selected_cells = list(self.selected_cells.keys())
        return list_of_selected_cells


class LedMatrixSettingsDialog(QDialog):
    def __init__(self, led_array):
        self.led_array = led_array
        super().__init__()
        self.setWindowTitle("LED Matrix Settings")

        self.layout = QVBoxLayout()

        # Add QDoubleSpinBox for LED intensity (0-1)
        self.NA_spinbox = QDoubleSpinBox()
        self.NA_spinbox.setRange(0, 1)
        self.NA_spinbox.setSingleStep(0.01)
        self.NA_spinbox.setValue(self.led_array.NA)

        NA_layout = QHBoxLayout()
        NA_layout.addWidget(QLabel("NA"))
        NA_layout.addWidget(self.NA_spinbox)

        self.layout.addLayout(NA_layout)
        self.setLayout(self.layout)

        # add ok/cancel buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

        self.button_box.accepted.connect(self.update_NA)

    def update_NA(self):
        self.led_array.set_NA(self.NA_spinbox.value())


class SampleSettingsWidget(QFrame):
    def __init__(self, ObjectivesWidget, WellplateFormatWidget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objectivesWidget = ObjectivesWidget
        self.wellplateFormatWidget = WellplateFormatWidget

        # Set up the layout
        top_row_layout = QGridLayout()
        top_row_layout.setSpacing(2)
        top_row_layout.setContentsMargins(0, 2, 0, 2)
        top_row_layout.addWidget(self.objectivesWidget, 0, 0)
        top_row_layout.addWidget(self.wellplateFormatWidget, 0, 1)
        self.setLayout(top_row_layout)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Connect signals for saving settings
        self.objectivesWidget.signal_objective_changed.connect(self.save_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(lambda *args: self.save_settings())

    def save_settings(self):
        """Save current objective and wellplate format to cache"""
        os.makedirs("cache", exist_ok=True)
        data = {
            "objective": self.objectivesWidget.dropdown.currentText(),
            "wellplate_format": self.wellplateFormatWidget.wellplate_format,
        }

        with open("cache/objective_and_sample_format.txt", "w") as f:
            json.dump(data, f)


class SquidFilterWidget(QFrame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.microscope = parent
        self.add_components()

    def add_components(self):
        # Layout for the position label
        self.position_label = QLabel(f"Position: {SQUID_FILTERWHEEL_MIN_INDEX}", self)
        position_layout = QHBoxLayout()
        position_layout.addWidget(self.position_label)

        # Layout for the status label
        self.status_label = QLabel("Status: Ready", self)
        status_layout = QHBoxLayout()
        status_layout.addWidget(self.status_label)

        # Layout for the editText, label, and button
        self.edit_text = QLineEdit(self)
        self.edit_text.setMaxLength(1)  # Restrict to one character
        self.edit_text.setText(f"{SQUID_FILTERWHEEL_MIN_INDEX}")
        move_to_pos_label = QLabel("move to pos.", self)
        self.move_spin_btn = QPushButton("Move To", self)

        move_to_pos_layout = QHBoxLayout()
        move_to_pos_layout.addWidget(move_to_pos_label)
        move_to_pos_layout.addWidget(self.edit_text)
        move_to_pos_layout.addWidget(self.move_spin_btn)

        # Buttons for controlling the filter spin
        self.previous_btn = QPushButton("Previous", self)
        self.next_btn = QPushButton("Next", self)
        self.home_btn = QPushButton("Homing", self)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.previous_btn)
        buttons_layout.addWidget(self.next_btn)
        buttons_layout.addWidget(self.home_btn)

        # Main vertical layout
        main_layout = QVBoxLayout()
        main_layout.addLayout(position_layout)
        main_layout.addLayout(status_layout)
        main_layout.addLayout(move_to_pos_layout)  # Layout with editText, label, and button
        main_layout.addLayout(buttons_layout)

        self.setLayout(main_layout)

        # Connect signals and slots
        self.previous_btn.clicked.connect(self.decrement_position)
        self.next_btn.clicked.connect(self.increment_position)
        self.home_btn.clicked.connect(self.home_position)
        self.move_spin_btn.clicked.connect(self.move_to_position)

    def update_position(self, position):
        self.position_label.setText(f"Position: {position}")

    def decrement_position(self):
        current_position = int(self.position_label.text().split(": ")[1])
        new_position = max(SQUID_FILTERWHEEL_MIN_INDEX, current_position - 1)  # Ensure position doesn't go below 0
        if current_position != new_position:
            self.microscope.squid_filter_wheel.previous_position()
            self.update_position(new_position)

    def increment_position(self):
        current_position = int(self.position_label.text().split(": ")[1])
        new_position = min(
            SQUID_FILTERWHEEL_MAX_INDEX, current_position + 1
        )  # Ensure position doesn't go above SQUID_FILTERWHEEL_MAX_INDEX
        if current_position != new_position:
            self.microscope.squid_filter_wheel.next_position()
            self.update_position(new_position)

    def home_position(self):
        self.update_position(SQUID_FILTERWHEEL_MIN_INDEX)
        self.status_label.setText("Status: Homed")
        self.microscope.squid_filter_wheel.homing()

    def move_to_position(self):
        try:
            position = int(self.edit_text.text())
            if position in range(SQUID_FILTERWHEEL_MIN_INDEX, SQUID_FILTERWHEEL_MAX_INDEX + 1):
                if position != int(self.position_label.text().split(": ")[1]):
                    self.microscope.squid_filter_wheel.set_emission(position)
                self.update_position(position)
        except ValueError:
            self.status_label.setText("Status: Invalid input")
            self.position_label.setText("Position: Invalid")


from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
from scipy.interpolate import griddata


class SurfacePlotWidget(QWidget):
    """
    A widget that displays a 3D surface plot of the coordinates.
    """

    signal_point_clicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Setup canvas and figure
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")

        layout = QVBoxLayout()
        layout.addWidget(self.canvas)
        self.setLayout(layout)

        self.selected_index = None
        self.plot_populated = False

        # Connect events
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("button_press_event", self.on_click)

    def plot(self, x: np.array, y: np.array, z: np.array, region: np.array) -> None:
        """
        Plot both surface and scatter points in 3D.

        Args:
            x (np.array): X coordinates (1D array)
            y (np.array): Y coordinates (1D array)
            z (np.array): Z coordinates (1D array)
        """
        # Store the original coordinates
        self.x = x
        self.y = y
        self.z = z

        # Clear previous plot
        self.ax.clear()

        # plot surface by region
        for r in np.unique(region):
            mask = region == r
            grid_x, grid_y = np.mgrid[min(x[mask]) : max(x[mask]) : 10j, min(y[mask]) : max(y[mask]) : 10j]
            grid_z = griddata((x[mask], y[mask]), z[mask], (grid_x, grid_y), method="cubic")
            self.ax.plot_surface(grid_x, grid_y, grid_z, cmap="viridis", edgecolor="none")
            # self.ax.plot_trisurf(x[mask], y[mask], z[mask], cmap='viridis', edgecolor='none')

        # Create scatter plot using original coordinates
        self.colors = ["r"] * len(self.x)
        self.scatter = self.ax.scatter(self.x, self.y, self.z, c=self.colors, s=30)

        # Set labels
        self.ax.set_xlabel("X (mm)")
        self.ax.set_ylabel("Y (mm)")
        self.ax.set_zlabel("Z (um)")
        self.ax.set_title("Double-click a point to go to that position")

        # Force x and y to have same scale
        max_range = max(np.ptp(self.x), np.ptp(self.y))
        center_x = np.mean(self.x)
        center_y = np.mean(self.y)

        self.ax.set_xlim(center_x - max_range / 2, center_x + max_range / 2)
        self.ax.set_ylim(center_y - max_range / 2, center_y + max_range / 2)

        self.canvas.draw()
        self.plot_populated = True

    def on_scroll(self, event):
        scale = 1.1 if event.button == "up" else 0.9

        def zoom(lim):
            center = (lim[0] + lim[1]) / 2
            half_range = (lim[1] - lim[0]) / 2 * scale
            return center - half_range, center + half_range

        self.ax.set_xlim(zoom(self.ax.get_xlim()))
        self.ax.set_ylim(zoom(self.ax.get_ylim()))
        self.ax.set_zlim(zoom(self.ax.get_zlim()))
        self.canvas.draw()

    def on_click(self, event):
        if not self.plot_populated:
            return
        if not event.dblclick or event.inaxes != self.ax:
            return

        # Cancel drag mode after double-click
        self.canvas.button_pressed = None  # FIX: Avoids AttributeError

        # Project 3D points to 2D screen space
        x2d, y2d, _ = proj3d.proj_transform(self.x, self.y, self.z, self.ax.get_proj())
        dists = np.hypot(x2d - event.xdata, y2d - event.ydata)
        idx = np.argmin(dists)

        # Threshold in data coordinates
        display_thresh = 0.05 * max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0], self.ax.get_ylim()[1] - self.ax.get_ylim()[0]
        )
        if dists[idx] > display_thresh:
            return

        # Change point color
        self.colors = ["r"] * len(self.x)
        self.colors[idx] = "g"
        self.scatter.remove()
        self.scatter = self.ax.scatter(self.x, self.y, self.z, c=self.colors, s=30)

        print(f"Clicked Point: x={self.x[idx]:.3f}, y={self.y[idx]:.3f}, z={self.z[idx]:.3f}")
        self.canvas.draw()
        self.signal_point_clicked.emit(self.x[idx], self.y[idx])
