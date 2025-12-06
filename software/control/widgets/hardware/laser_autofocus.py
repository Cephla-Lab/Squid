# Laser autofocus widgets
import numpy as np

from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
)
from qtpy.QtGui import QColor

from control._def import SpotDetectionMode
from control.core.display import LiveController
from control import utils


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
