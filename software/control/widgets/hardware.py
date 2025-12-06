# Hardware control widgets (confocal, filter, laser AF, trigger, etc.)
import numpy as np
from typing import TYPE_CHECKING, Optional

import squid.logging
from squid.events import event_bus, DACValueChanged
from qtpy.QtCore import Signal, Qt

if TYPE_CHECKING:
    from squid.services import PeripheralService
from qtpy.QtWidgets import (
    QWidget,
    QFrame,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QSizePolicy,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
)
from qtpy.QtGui import QColor

from control._def import *
from control.core.live_controller import LiveController
from squid.abc import AbstractFilterWheelController


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
        self.exposure_spinbox.setKeyboardTracking(False)
        self.exposure_spinbox.setSingleStep(0.1)
        self.exposure_spinbox.setRange(*self.liveController.microscope.camera.get_exposure_limits())
        self.exposure_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_exposure_time_ms)
        exposure_layout.addWidget(self.exposure_spinbox)

        # Analog gain control
        analog_gain_layout = QHBoxLayout()
        analog_gain_layout.addWidget(QLabel("Focus Camera Analog Gain:"))
        self.analog_gain_spinbox = QDoubleSpinBox()
        self.analog_gain_spinbox.setKeyboardTracking(False)
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
        self._add_spinbox(spot_detection_layout, "Filter Sigma:", "filter_sigma", 0, 100, 1, allow_none=True)

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
        self,
        layout,
        label: str,
        property_name: str,
        min_val: float,
        max_val: float,
        decimals: int,
        step: float = 1,
        allow_none=False,
    ) -> None:
        """Helper method to add a labeled spinbox to the layout."""
        box_layout = QHBoxLayout()
        box_layout.addWidget(QLabel(label))

        spinbox = QDoubleSpinBox()
        spinbox.setKeyboardTracking(False)
        if allow_none:
            spinbox.setRange(min_val - step, max_val)
            spinbox.setSpecialValueText("None")
        else:
            spinbox.setRange(min_val, max_val)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(step)
        # Get initial value from laser_af_properties
        current_value = getattr(self.laserAutofocusController.laser_af_properties, property_name)
        if allow_none and current_value is None:
            spinbox.setValue(min_val - step)
        else:
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
            "filter_sigma": self.spinboxes["filter_sigma"].value(),
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
        self.liveController.microscope.low_level_drivers.microcontroller.turn_on_AF_laser()
        self.liveController.microscope.low_level_drivers.microcontroller.wait_till_operation_is_completed()
        self.liveController.trigger_acquisition()

        try:
            frame = self.liveController.camera.read_frame()
        finally:
            self.liveController.microscope.low_level_drivers.microcontroller.turn_off_AF_laser()
            self.liveController.microscope.low_level_drivers.microcontroller.wait_till_operation_is_completed()

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
        sigma = self.spinboxes["filter_sigma"].value()

        frame = self.illuminate_and_get_frame()
        if frame is not None:
            try:
                result = utils.find_spot_location(frame, mode=mode, params=params, filter_sigma=sigma, debug_plot=True)
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


class DragonflyConfocalWidget(QWidget):

    signal_toggle_confocal_widefield = Signal(bool)

    def __init__(self, dragonfly):
        super(DragonflyConfocalWidget, self).__init__()

        self.dragonfly = dragonfly

        self.init_ui()

        # Initialize current states from hardware
        try:
            current_modality = self.dragonfly.get_modality()
            self.confocal_mode = current_modality == "CONFOCAL" if current_modality else False

            current_dichroic = self.dragonfly.get_port_selection_dichroic()
            if current_dichroic is not None:
                self.dropdown_dichroic.setCurrentText(str(current_dichroic))

            current_port1_filter = self.dragonfly.get_emission_filter(1)
            if current_port1_filter is not None:
                self.dropdown_port1_emission_filter.setCurrentText(str(current_port1_filter))

            current_port2_filter = self.dragonfly.get_emission_filter(2)
            if current_port2_filter is not None:
                self.dropdown_port2_emission_filter.setCurrentText(str(current_port2_filter))

            current_field_aperture = self.dragonfly.get_field_aperture_wheel_position()
            if current_field_aperture is not None:
                self.dropdown_field_aperture.setCurrentText(str(current_field_aperture))

            motor_state = self.dragonfly.get_disk_motor_state()
            if motor_state is not None:
                self.btn_disk_motor.setChecked(motor_state)

        except Exception as e:
            print(f"Error initializing widget state: {e}")

        # Set initial button text
        if self.confocal_mode:
            self.btn_toggle_confocal.setText("Switch to Widefield")
        else:
            self.btn_toggle_confocal.setText("Switch to Confocal")

        # Connect signals
        self.btn_toggle_confocal.clicked.connect(self.toggle_confocal_mode)
        self.btn_disk_motor.clicked.connect(self.toggle_disk_motor)
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)
        self.dropdown_port1_emission_filter.currentIndexChanged.connect(self.set_port1_emission_filter)
        self.dropdown_port2_emission_filter.currentIndexChanged.connect(self.set_port2_emission_filter)
        self.dropdown_field_aperture.currentIndexChanged.connect(self.set_field_aperture)

        # Emit initial state
        self.signal_toggle_confocal_widefield.emit(self.confocal_mode)

    def init_ui(self):
        main_layout = QVBoxLayout()

        layout_confocal = QHBoxLayout()
        # Row 1: Switch to Confocal button, Disk Motor button, Dichroic dropdown
        self.btn_toggle_confocal = QPushButton("Switch to Confocal")
        self.btn_disk_motor = QPushButton("Disk Motor On")
        self.btn_disk_motor.setCheckable(True)

        dichroic_label = QLabel("Port Selection")
        dichroic_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems(self.dragonfly.get_port_selection_dichroic_info())

        layout_confocal.addWidget(self.btn_toggle_confocal)
        layout_confocal.addWidget(self.btn_disk_motor)
        layout_confocal.addWidget(dichroic_label)
        layout_confocal.addWidget(self.dropdown_dichroic)

        layout_wheels = QGridLayout()
        # Row 2: Camera Port 1 Emission Filter and Field Aperture
        port1_emission_label = QLabel("Port 1 Emission Filter")
        self.dropdown_port1_emission_filter = QComboBox(self)
        self.dropdown_port1_emission_filter.addItems(self.dragonfly.get_emission_filter_info(1))

        port1_aperture_label = QLabel("Field Aperture")
        self.dropdown_field_aperture = QComboBox(self)
        self.dropdown_field_aperture.addItems(self.dragonfly.get_field_aperture_info())

        layout_wheels.addWidget(port1_emission_label, 0, 0)
        layout_wheels.addWidget(self.dropdown_port1_emission_filter, 0, 1)
        layout_wheels.addWidget(port1_aperture_label, 0, 2)
        layout_wheels.addWidget(self.dropdown_field_aperture, 0, 3)

        # Row 3: Camera Port 2 Emission Filter and Field Aperture
        port2_emission_label = QLabel("Port 2 Emission Filter")
        self.dropdown_port2_emission_filter = QComboBox(self)
        self.dropdown_port2_emission_filter.addItems(self.dragonfly.get_emission_filter_info(2))

        layout_wheels.addWidget(port2_emission_label, 1, 0)
        layout_wheels.addWidget(self.dropdown_port2_emission_filter, 1, 1)

        main_layout.addLayout(layout_confocal)
        main_layout.addLayout(layout_wheels)

        self.setLayout(main_layout)

    def enable_all_buttons(self, enable: bool):
        """Enable or disable all controls"""
        self.btn_toggle_confocal.setEnabled(enable)
        self.btn_disk_motor.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.dropdown_port1_emission_filter.setEnabled(enable)
        self.dropdown_port2_emission_filter.setEnabled(enable)
        self.dropdown_field_aperture.setEnabled(enable)

    def toggle_confocal_mode(self):
        """Toggle between confocal and widefield modes"""
        self.enable_all_buttons(False)
        try:
            if self.confocal_mode:
                # Switch to widefield
                self.dragonfly.set_modality("BF")  # or whatever widefield mode string is
                self.confocal_mode = False
                self.btn_toggle_confocal.setText("Switch to Confocal")
            else:
                # Switch to confocal
                self.dragonfly.set_modality("CONFOCAL")
                self.confocal_mode = True
                self.btn_toggle_confocal.setText("Switch to Widefield")

            self.signal_toggle_confocal_widefield.emit(self.confocal_mode)
        except Exception as e:
            print(f"Error toggling confocal mode: {e}")
        finally:
            self.enable_all_buttons(True)

    def toggle_disk_motor(self):
        """Toggle disk motor on/off"""
        self.enable_all_buttons(False)
        try:
            if self.btn_disk_motor.isChecked():
                self.dragonfly.set_disk_motor_state(True)
            else:
                self.dragonfly.set_disk_motor_state(False)
        except Exception as e:
            print(f"Error toggling disk motor: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_dichroic(self, index):
        """Set dichroic position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_dichroic.currentIndex()
            self.dragonfly.set_port_selection_dichroic(selected_pos + 1)
        except Exception as e:
            print(f"Error setting dichroic: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port1_emission_filter(self, index):
        """Set port 1 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port1_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(1, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port2_emission_filter(self, index):
        """Set port 2 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port2_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(2, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 2 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_field_aperture(self, index):
        """Set port 1 field aperture position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_field_aperture.currentIndex()
            self.dragonfly.set_field_aperture_wheel_position(selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 field aperture: {e}")
        finally:
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


class DACControWidget(QFrame):
    def __init__(
        self,
        peripheral_service: "PeripheralService",
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)

        self._service = peripheral_service

        # Subscribe to state updates
        event_bus.subscribe(DACValueChanged, self._on_dac_changed)

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
        """Set DAC0 output (0-100%)."""
        self._service.set_dac(channel=0, percentage=value)

    def set_DAC1(self, value):
        """Set DAC1 output (0-100%)."""
        self._service.set_dac(channel=1, percentage=value)

    def _on_dac_changed(self, event: DACValueChanged):
        """Handle DAC value changed event."""
        # Update UI without triggering signal loops
        if event.channel == 0:
            self.entry_DAC0.blockSignals(True)
            self.slider_DAC0.blockSignals(True)
            self.entry_DAC0.setValue(event.value)
            self.slider_DAC0.setValue(int(event.value))
            self.entry_DAC0.blockSignals(False)
            self.slider_DAC0.blockSignals(False)
        elif event.channel == 1:
            self.entry_DAC1.blockSignals(True)
            self.slider_DAC1.blockSignals(True)
            self.entry_DAC1.setValue(event.value)
            self.slider_DAC1.setValue(int(event.value))
            self.entry_DAC1.blockSignals(False)
            self.slider_DAC1.blockSignals(False)


class FilterControllerWidget(QFrame):
    def __init__(
        self,
        filterController: AbstractFilterWheelController,
        liveController: LiveController,
        main=None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.filterController: AbstractFilterWheelController = filterController
        self.liveController = liveController
        self.wheel_index = 1  # Control the first filter wheel
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        # Get filter wheel info to populate combo box
        try:
            wheel_info = self.filterController.get_filter_wheel_info(self.wheel_index)
            num_positions = wheel_info.number_of_slots
        except:
            # Fallback to 7 positions if we can't get info
            num_positions = 7

        self.comboBox = QComboBox()
        for i in range(1, num_positions + 1):
            self.comboBox.addItem(f"Position {i}")

        self.checkBox = QCheckBox("Disable filter wheel movement on changing Microscope Configuration", self)

        # Create buttons
        self.get_position_btn = QPushButton("Get Position")
        self.home_btn = QPushButton("Home")
        self.next_btn = QPushButton("Next")
        self.previous_btn = QPushButton("Previous")

        layout = QGridLayout()
        layout.addWidget(QLabel("Filter wheel position:"), 0, 0)
        layout.addWidget(self.comboBox, 0, 1)
        layout.addWidget(self.get_position_btn, 0, 2)
        layout.addWidget(self.checkBox, 2, 0, 1, 3)  # Span across 3 columns
        layout.addWidget(self.home_btn, 3, 0)
        layout.addWidget(self.next_btn, 3, 1)
        layout.addWidget(self.previous_btn, 3, 2)
        layout.addWidget(
            QLabel("For acquisition, filter wheel positions need to be set in channel configurations."), 4, 0, 1, 3
        )

        self.setLayout(layout)

        # Connect signals
        self.comboBox.currentIndexChanged.connect(self.on_selection_change)
        self.checkBox.stateChanged.connect(self.disable_movement_by_switching_channels)
        self.get_position_btn.clicked.connect(self.update_position_from_controller)
        self.home_btn.clicked.connect(self.home)
        self.next_btn.clicked.connect(self.go_to_next_position)
        self.previous_btn.clicked.connect(self.go_to_previous_position)

    def home(self):
        """Home the filter wheel."""
        self.filterController.home(self.wheel_index)

    def update_position_from_controller(self):
        """Poll the current position from the controller and update the dropdown."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)
            # Block signals temporarily to avoid triggering position change
            self.comboBox.blockSignals(True)
            self.comboBox.setCurrentIndex(current_pos - 1)  # Convert 1-indexed to 0-indexed
            self.comboBox.blockSignals(False)
            print(f"Filter wheel position updated: {current_pos}")
        except Exception as e:
            print(f"Error getting filter wheel position: {e}")

    def on_selection_change(self, index):
        """Handle position selection from combo box."""
        if index >= 0:
            position = index + 1  # Combo box is 0-indexed, positions are 1-indexed
            self.filterController.set_filter_wheel_position({self.wheel_index: position})

    def go_to_next_position(self):
        """Move to the next position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)
            wheel_info = self.filterController.get_filter_wheel_info(self.wheel_index)
            max_pos = wheel_info.number_of_slots

            if current_pos < max_pos:
                new_pos = current_pos + 1
                self.filterController.set_filter_wheel_position({self.wheel_index: new_pos})
                self.comboBox.setCurrentIndex(new_pos - 1)  # Update combo box
        except Exception as e:
            print(f"Error moving to next position: {e}")

    def go_to_previous_position(self):
        """Move to the previous position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(self.wheel_index, 1)

            if current_pos > 1:
                new_pos = current_pos - 1
                self.filterController.set_filter_wheel_position({self.wheel_index: new_pos})
                self.comboBox.setCurrentIndex(new_pos - 1)  # Update combo box
        except Exception as e:
            print(f"Error moving to previous position: {e}")

    def disable_movement_by_switching_channels(self, state):
        """Enable/disable automatic filter wheel movement when changing channels."""
        if state:
            self.liveController.enable_channel_auto_filter_switching = False
        else:
            self.liveController.enable_channel_auto_filter_switching = True


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
        self.entry_triggerFPS.setKeyboardTracking(False)
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


class LaserAutofocusControlWidget(QFrame):
    def __init__(self, laserAutofocusController, liveController: LiveController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.laserAutofocusController = laserAutofocusController
        self.liveController: LiveController = liveController
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
        self.btn_measure_displacement.clicked.connect(self.on_measure_displacement_clicked)
        self.btn_move_to_target.clicked.connect(self.move_to_target)
        self.laserAutofocusController.signal_displacement_um.connect(self.label_displacement.setNum)

    def update_init_state(self):
        self.btn_set_reference.setEnabled(self.laserAutofocusController.is_initialized)
        self.btn_measure_displacement.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)
        self.btn_move_to_target.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)

    def move_to_target(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        self.laserAutofocusController.move_to_target(self.entry_target.value())
        if was_live:
            self.liveController.start_live()

    def on_set_reference_clicked(self):
        """Handle set reference button click"""
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        success = self.laserAutofocusController.set_reference()
        if success:
            self.btn_measure_displacement.setEnabled(True)
            self.btn_move_to_target.setEnabled(True)
        if was_live:
            self.liveController.start_live()

    def on_measure_displacement_clicked(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        self.laserAutofocusController.measure_displacement()
        if was_live:
            self.liveController.start_live()


class LedMatrixSettingsDialog(QDialog):
    def __init__(self, led_array):
        self.led_array = led_array
        super().__init__()
        self.setWindowTitle("LED Matrix Settings")

        self.layout = QVBoxLayout()

        # Add QDoubleSpinBox for LED intensity (0-1)
        self.NA_spinbox = QDoubleSpinBox()
        self.NA_spinbox.setKeyboardTracking(False)
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


