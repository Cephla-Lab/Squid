# Laser autofocus widgets
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

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

from _def import SpotDetectionMode
import squid.core.utils.hardware_utils as utils
from squid.core.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    SetTriggerFPSCommand,
    SetLaserAFPropertiesCommand,
    InitializeLaserAFCommand,
    SetLaserAFCharacterizationModeCommand,
    UpdateLaserAFThresholdCommand,
    MoveToLaserAFTargetCommand,
    SetLaserAFReferenceCommand,
    MeasureLaserAFDisplacementCommand,
    CaptureLaserAFFrameCommand,
    LaserAFInitialized,
    LaserAFReferenceSet,
    LaserAFCrossCorrelationMeasured,
    LaserAFSpotCentroidMeasured,
    LaserAFPropertiesChanged,
    LaserAFDisplacementMeasured,
    LaserAFMoveCompleted,
    ProfileChanged,
    ObjectiveChanged,
)

if TYPE_CHECKING:
    from squid.storage.stream_handler import StreamHandler
    from squid.ui.widgets.display.image_display import ImageDisplayWindow


class LaserAutofocusSettingWidget(QWidget):
    """Widget for configuring laser autofocus settings.

    Subscribes to LaserAFPropertiesChanged, LaserAFInitialized, and LiveStateChanged events.
    No direct controller access - pure event-driven architecture.
    """

    signal_newExposureTime: Signal = Signal(float)
    signal_newAnalogGain: Signal = Signal(float)
    signal_apply_settings: Signal = Signal()
    # Note: spot marking is handled internally via ImageDisplayWindow.set_image_display_window().

    def __init__(
        self,
        streamHandler: "StreamHandler",
        event_bus: EventBus,
        initial_properties: Dict[str, Any],
        initial_is_initialized: bool = False,
        initial_characterization_mode: bool = False,
        exposure_limits: Tuple[float, float] = (0.1, 1000.0),
        stretch: bool = True,
    ) -> None:
        """Initialize the laser autofocus settings widget.

        Args:
            streamHandler: Stream handler for display FPS control
            event_bus: EventBus for publishing commands and subscribing to state
            initial_properties: Dict with initial laser AF properties (from LaserAFConfig)
            initial_is_initialized: Whether laser AF is already initialized
            initial_characterization_mode: Whether characterization mode is enabled
            exposure_limits: Tuple of (min, max) exposure limits in ms
            stretch: Whether to add stretch to layout
        """
        super().__init__()
        self.streamHandler: "StreamHandler" = streamHandler
        self._event_bus = event_bus
        self._exposure_limits = exposure_limits
        self.stretch: bool = stretch

        # Cached state from events (initialized with provided initial values)
        self._laser_af_properties = dict(initial_properties)  # Make a copy
        self._is_initialized = initial_is_initialized
        self._characterization_mode = initial_characterization_mode

        self.spinboxes: Dict[str, QDoubleSpinBox] = {}
        self.btn_live: QPushButton
        self.exposure_spinbox: QDoubleSpinBox
        self.analog_gain_spinbox: QDoubleSpinBox
        self.update_threshold_button: QPushButton
        self.run_spot_detection_button: QPushButton
        self.initialize_button: QPushButton
        self.characterization_checkbox: QCheckBox
        self.spot_mode_combo: QComboBox
        self._image_display_window: Optional["ImageDisplayWindow"] = None
        self._is_live = False
        self._last_frame: Optional[np.ndarray] = None

        # Set initial trigger/display FPS via EventBus
        self._event_bus.publish(SetTriggerFPSCommand(camera="focus", fps=10))
        self.streamHandler.set_display_fps(10)

        # Enable background filling
        self.setAutoFillBackground(True)

        # Create and set background color
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(240, 240, 240))
        self.setPalette(palette)

        self.init_ui()
        self.update_calibration_label()

        # Subscribe to state events
        self._event_bus.subscribe(LiveStateChanged, self._on_live_state_changed)
        self._event_bus.subscribe(LaserAFInitialized, self._on_laser_af_initialized)
        self._event_bus.subscribe(LaserAFSpotCentroidMeasured, self._on_spot_centroid_measured)
        self._event_bus.subscribe(LaserAFCrossCorrelationMeasured, self._on_cross_correlation_measured)
        self._event_bus.subscribe(LaserAFPropertiesChanged, self._on_properties_changed)
        self._event_bus.subscribe(ProfileChanged, self._on_profile_or_objective_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_profile_or_objective_changed)

        # Keep the most recent displayed focus-camera frame for spot marking.
        if hasattr(self.streamHandler, "image_to_display"):
            try:
                self.streamHandler.image_to_display.connect(self._on_new_frame)  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                pass

    def _on_properties_changed(self, event: LaserAFPropertiesChanged) -> None:
        """Handle laser AF properties change from EventBus."""
        # Update cached properties with changes
        self._laser_af_properties.update(event.properties)
        # Update calibration label if pixel_to_um or calibration_timestamp changed
        if "pixel_to_um" in event.properties or "calibration_timestamp" in event.properties:
            self.update_calibration_label()

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Handle live state changes from EventBus."""
        if getattr(event, "camera", "main") != "focus":
            return
        self._is_live = event.is_live
        if event.is_live:
            self.btn_live.setText("Stop Live")
            self.btn_live.setChecked(True)
            self.run_spot_detection_button.setEnabled(False)
        else:
            self.btn_live.setText("Start Live")
            self.btn_live.setChecked(False)
            self.run_spot_detection_button.setEnabled(True)
            # Disable spot tracking on display window
            if self._image_display_window is not None:
                self._image_display_window.set_spot_tracking(enabled=False)

    def _on_laser_af_initialized(self, event: LaserAFInitialized) -> None:
        """Handle laser AF initialization from EventBus."""
        # Update cached state
        self._is_initialized = event.is_initialized
        self.update_threshold_button.setEnabled(event.success)
        self.update_calibration_label()
        if event.success:
            # Enable spot tracking if live mode is running
            if self._image_display_window is not None and self._is_live:
                mode = self.spot_mode_combo.currentData()
                params = self._get_spot_tracking_params()
                sigma = self.spinboxes["filter_sigma"].value()
                self._image_display_window.set_spot_tracking(
                    enabled=True,
                    mode=mode,
                    params=params,
                    filter_sigma=int(sigma) if sigma and sigma > 0 else None,
                )
        else:
            # Show error to user - initialization failed
            from qtpy.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Laser AF Initialization Failed",
                "Failed to initialize laser autofocus. Check the log for details.\n\n"
                "Common issues:\n"
                "- Laser spot not detected\n"
                "- Spot too close to image edge\n"
                "- Calibration failed"
            )

    def _on_new_frame(self, frame: np.ndarray) -> None:
        self._last_frame = frame

    def _on_spot_centroid_measured(self, event: LaserAFSpotCentroidMeasured) -> None:
        if not event.success or event.x_px is None or event.y_px is None:
            self._show_spot_detection_error()
            return
        # Use the image from the event if provided, otherwise fall back to _last_frame
        image = getattr(event, 'image', None)
        if image is None:
            image = self._last_frame
        if image is None:
            self._show_spot_detection_error()
            return
        if self._image_display_window is not None:
            self._image_display_window.mark_spot(image, event.x_px, event.y_px)

    def _on_cross_correlation_measured(self, event: LaserAFCrossCorrelationMeasured) -> None:
        self.show_cross_correlation_result(event.correlation)

    def _on_profile_or_objective_changed(self, event) -> None:
        """Handle profile or objective changes - refresh widget values."""
        self.update_values()

    def init_ui(self) -> None:
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
        self.exposure_spinbox.setRange(*self._exposure_limits)
        self.exposure_spinbox.setValue(
            self._laser_af_properties.get("focus_camera_exposure_time_ms", 1.0)
        )
        exposure_layout.addWidget(self.exposure_spinbox)

        # Analog gain control
        analog_gain_layout = QHBoxLayout()
        analog_gain_layout.addWidget(QLabel("Focus Camera Analog Gain:"))
        self.analog_gain_spinbox = QDoubleSpinBox()
        self.analog_gain_spinbox.setKeyboardTracking(False)
        self.analog_gain_spinbox.setRange(0, 24)
        self.analog_gain_spinbox.setValue(
            self._laser_af_properties.get("focus_camera_analog_gain", 0.0)
        )
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
        self._add_spinbox(
            non_threshold_layout,
            "Spot Crop Size (pixels):",
            "spot_crop_size",
            1,
            500,
            0,
        )
        self._add_spinbox(
            non_threshold_layout,
            "Calibration Distance (μm):",
            "pixel_to_um_calibration_distance",
            0.1,
            20.0,
            2,
        )
        non_threshold_group.setLayout(non_threshold_layout)

        # Settings group
        settings_group = QFrame()
        settings_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        settings_layout = QVBoxLayout()

        # Add threshold property spinboxes
        self._add_spinbox(
            settings_layout, "Laser AF Averaging N:", "laser_af_averaging_n", 1, 100, 0
        )
        self._add_spinbox(
            settings_layout,
            "Displacement Success Window (μm):",
            "displacement_success_window_um",
            0.1,
            10.0,
            2,
        )
        self._add_spinbox(
            settings_layout,
            "Correlation Threshold:",
            "correlation_threshold",
            0.1,
            1.0,
            2,
            0.1,
        )
        self._add_spinbox(
            settings_layout, "Laser AF Range (μm):", "laser_af_range", 1, 1000, 1
        )
        self.update_threshold_button = QPushButton("Apply without Re-initialization")
        settings_layout.addWidget(self.update_threshold_button)
        settings_group.setLayout(settings_layout)

        # Create spot detection group
        spot_detection_group = QFrame()
        spot_detection_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        spot_detection_layout = QVBoxLayout()

        # Add spot detection related spinboxes
        self._add_spinbox(
            spot_detection_layout, "Y Window (pixels):", "y_window", 1, 500, 0
        )
        self._add_spinbox(
            spot_detection_layout, "X Window (pixels):", "x_window", 1, 500, 0
        )
        self._add_spinbox(
            spot_detection_layout, "Min Peak Width:", "min_peak_width", 1, 100, 1
        )
        self._add_spinbox(
            spot_detection_layout, "Min Peak Distance:", "min_peak_distance", 1, 100, 1
        )
        self._add_spinbox(
            spot_detection_layout,
            "Min Peak Prominence:",
            "min_peak_prominence",
            0.01,
            1.0,
            2,
            0.1,
        )
        self._add_spinbox(
            spot_detection_layout, "Spot Spacing (pixels):", "spot_spacing", 1, 1000, 1
        )
        self._add_spinbox(
            spot_detection_layout,
            "Filter Sigma:",
            "filter_sigma",
            0,
            100,
            1,
            allow_none=True,
        )

        # Spot detection mode combo box
        spot_mode_layout = QHBoxLayout()
        spot_mode_layout.addWidget(QLabel("Spot Detection Mode:"))
        self.spot_mode_combo = QComboBox()
        for mode in SpotDetectionMode:
            self.spot_mode_combo.addItem(mode.value, mode)
        spot_mode = self._laser_af_properties.get("spot_detection_mode", SpotDetectionMode.SINGLE)
        # Accept string or enum and normalize to enum
        if isinstance(spot_mode, str):
            try:
                spot_mode = SpotDetectionMode(spot_mode)
            except Exception:
                self._log.debug("Unknown spot_detection_mode '%s', defaulting to SINGLE", spot_mode)
                spot_mode = SpotDetectionMode.SINGLE
        current_index = self.spot_mode_combo.findData(spot_mode)
        self.spot_mode_combo.setCurrentIndex(current_index if current_index >= 0 else 0)
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
        self.characterization_checkbox.setChecked(self._characterization_mode)
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
        self.characterization_checkbox.toggled.connect(
            self.toggle_characterization_mode
        )

    def _add_spinbox(
        self,
        layout: QVBoxLayout,
        label: str,
        property_name: str,
        min_val: float,
        max_val: float,
        decimals: int,
        step: float = 1,
        allow_none: bool = False,
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
        # Get initial value from cached properties
        current_value = self._laser_af_properties.get(property_name)
        if allow_none and current_value is None:
            spinbox.setValue(min_val - step)
        else:
            spinbox.setValue(current_value)

        box_layout.addWidget(spinbox)
        layout.addLayout(box_layout)

        # Store spinbox reference
        self.spinboxes[property_name] = spinbox

    def set_image_display_window(self, window: "ImageDisplayWindow") -> None:
        """Set the image display window for spot tracking overlay."""
        self._image_display_window = window

    def _get_spot_tracking_params(self) -> dict:
        """Get current spot tracking parameters from spinboxes."""
        return {
            "y_window": int(self.spinboxes["y_window"].value()),
            "x_window": int(self.spinboxes["x_window"].value()),
            "min_peak_width": self.spinboxes["min_peak_width"].value(),
            "min_peak_distance": self.spinboxes["min_peak_distance"].value(),
            "min_peak_prominence": self.spinboxes["min_peak_prominence"].value(),
            "spot_spacing": self.spinboxes["spot_spacing"].value(),
        }

    def toggle_live(self, pressed: bool) -> None:
        if pressed:
            self._event_bus.publish(StartLiveCommand(camera="focus"))
            # Enable spot tracking only if laser AF is initialized
            if (
                self._image_display_window is not None
                and self._is_initialized
            ):
                mode = self.spot_mode_combo.currentData()
                params = self._get_spot_tracking_params()
                sigma = self.spinboxes["filter_sigma"].value()
                self._image_display_window.set_spot_tracking(
                    enabled=True,
                    mode=mode,
                    params=params,
                    filter_sigma=int(sigma) if sigma and sigma > 0 else None,
                )
        else:
            self._event_bus.publish(StopLiveCommand(camera="focus"))

    def stop_live(self) -> None:
        """Used for stopping live when switching to other tabs"""
        self.toggle_live(False)
        self.btn_live.setChecked(False)

    def toggle_characterization_mode(self, state: bool) -> None:
        self._event_bus.publish(SetLaserAFCharacterizationModeCommand(enabled=state))

    def update_exposure_time(self, value: float) -> None:
        self.signal_newExposureTime.emit(value)

    def update_analog_gain(self, value: float) -> None:
        self.signal_newAnalogGain.emit(value)

    def update_values(self) -> None:
        """Update all widget values from cached properties."""
        self.clear_labels()

        # Update spinboxes from cached properties
        for prop_name, spinbox in self.spinboxes.items():
            current_value = self._laser_af_properties.get(prop_name)
            if current_value is not None:
                spinbox.setValue(current_value)

        # Update exposure and gain
        self.exposure_spinbox.setValue(
            self._laser_af_properties.get("focus_camera_exposure_time_ms", 1.0)
        )
        self.analog_gain_spinbox.setValue(
            self._laser_af_properties.get("focus_camera_analog_gain", 0.0)
        )

        # Update spot detection mode
        current_mode = self._laser_af_properties.get("spot_detection_mode")
        if current_mode is not None:
            index = self.spot_mode_combo.findData(current_mode)
            if index >= 0:
                self.spot_mode_combo.setCurrentIndex(index)

        self.update_threshold_button.setEnabled(self._is_initialized)
        self.update_calibration_label()

    def apply_and_initialize(self) -> None:
        self.clear_labels()

        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes[
                "displacement_success_window_um"
            ].value(),
            "spot_crop_size": int(self.spinboxes["spot_crop_size"].value()),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "pixel_to_um_calibration_distance": self.spinboxes[
                "pixel_to_um_calibration_distance"
            ].value(),
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
        # Publish commands via EventBus - controller will handle and publish result
        self._event_bus.publish(SetLaserAFPropertiesCommand(properties=updates))
        self._event_bus.publish(InitializeLaserAFCommand())
        self.signal_apply_settings.emit()

    def update_threshold_settings(self) -> None:
        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes[
                "displacement_success_window_um"
            ].value(),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "laser_af_range": self.spinboxes["laser_af_range"].value(),
        }
        self._event_bus.publish(UpdateLaserAFThresholdCommand(updates=updates))

    def update_calibration_label(self) -> None:
        # Show calibration result
        # Clear previous calibration label if it exists
        if hasattr(self, "calibration_label") and self.calibration_label is not None:
            self.calibration_label.deleteLater()

        # Create and add new calibration label using cached properties
        pixel_to_um = self._laser_af_properties.get("pixel_to_um", 1.0)
        calibration_timestamp = self._laser_af_properties.get("calibration_timestamp", "")
        self.calibration_label: QLabel = QLabel()
        self.calibration_label.setText(
            f"Calibration Result: {pixel_to_um:.3f} pixels/um\nPerformed at {calibration_timestamp}"
        )
        layout = self.layout()
        if layout is not None:
            layout.addWidget(self.calibration_label)

    def clear_labels(self) -> None:
        # Remove any existing error or correlation labels
        if (
            hasattr(self, "spot_detection_error_label")
            and self.spot_detection_error_label is not None
        ):
            self.spot_detection_error_label.deleteLater()
            delattr(self, "spot_detection_error_label")

        if hasattr(self, "correlation_label") and self.correlation_label is not None:
            self.correlation_label.deleteLater()
            delattr(self, "correlation_label")

    def run_spot_detection(self) -> None:
        """Run spot detection with current settings.

        This is async - backend captures frame + computes centroid.
        """
        self._event_bus.publish(CaptureLaserAFFrameCommand())

    def _show_spot_detection_error(self) -> None:
        """Show spot detection error label."""
        # Clear previous error label if it exists
        if (
            hasattr(self, "spot_detection_error_label")
            and self.spot_detection_error_label is not None
        ):
            self.spot_detection_error_label.deleteLater()

        # Create and add new error label
        self.spot_detection_error_label: QLabel = QLabel("Spot detection failed!")
        layout = self.layout()
        if layout is not None:
            layout.addWidget(self.spot_detection_error_label)

    def show_cross_correlation_result(self, value: float) -> None:
        """Show cross-correlation value from validating laser af images"""
        # Clear previous correlation label if it exists
        if hasattr(self, "correlation_label") and self.correlation_label is not None:
            self.correlation_label.deleteLater()

        # Create and add new correlation label
        self.correlation_label: QLabel = QLabel()
        self.correlation_label.setText(f"Cross-correlation: {value:.3f}")
        layout = self.layout()
        if layout is not None:
            layout.addWidget(self.correlation_label)


class LaserAutofocusControlWidget(QFrame):
    """Widget for controlling laser autofocus operations.

    Subscribes to LaserAFInitialized, LaserAFReferenceSet, LaserAFDisplacementMeasured,
    and LiveStateChanged events. No direct controller access - pure event-driven architecture.
    """

    def __init__(
        self,
        event_bus: EventBus,
        initial_is_initialized: bool = False,
        initial_has_reference: bool = False,
        main: Optional[QWidget] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Initialize the laser autofocus control widget.

        Args:
            event_bus: EventBus for publishing commands and subscribing to state
            initial_is_initialized: Whether laser AF is already initialized
            initial_has_reference: Whether a reference has been set
            main: Parent widget
        """
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self._is_live = False

        # Cached state from events
        self._is_initialized = initial_is_initialized
        self._has_reference = initial_has_reference

        self.btn_set_reference: QPushButton
        self.label_displacement: QLabel
        self.btn_measure_displacement: QPushButton
        self.entry_target: QDoubleSpinBox
        self.btn_move_to_target: QPushButton
        self.add_components()
        self.update_init_state()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to state events
        self._event_bus.subscribe(LiveStateChanged, self._on_live_state_changed)
        self._event_bus.subscribe(LaserAFReferenceSet, self._on_reference_set)
        self._event_bus.subscribe(LaserAFInitialized, self._on_initialized)
        self._event_bus.subscribe(LaserAFDisplacementMeasured, self._on_displacement_measured)
        self._event_bus.subscribe(LaserAFMoveCompleted, self._on_move_completed)
        self._event_bus.subscribe(ProfileChanged, self._on_profile_or_objective_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_profile_or_objective_changed)

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Track live state for stop/start around operations."""
        if getattr(event, "camera", "main") != "focus":
            return
        self._is_live = event.is_live

    def _on_reference_set(self, event: LaserAFReferenceSet) -> None:
        """Handle reference set event."""
        if event.success:
            self._has_reference = True
            self.btn_measure_displacement.setEnabled(True)
            self.btn_move_to_target.setEnabled(True)

    def _on_initialized(self, event: LaserAFInitialized) -> None:
        """Handle initialization event."""
        self._is_initialized = event.is_initialized
        self.update_init_state()

    def _on_displacement_measured(self, event: LaserAFDisplacementMeasured) -> None:
        """Handle displacement measurement event - replaces Qt signal connection."""
        if event.success and event.displacement_um is not None:
            self.label_displacement.setNum(event.displacement_um)

    def _on_move_completed(self, event: LaserAFMoveCompleted) -> None:
        """Handle move to target completion event."""
        if event.success:
            # Update displacement label with final displacement
            if event.final_displacement_um is not None:
                self.label_displacement.setNum(event.final_displacement_um)
        else:
            # Show error to user
            from qtpy.QtWidgets import QMessageBox
            error_msg = event.error or "Unknown error"
            QMessageBox.warning(
                self,
                "Move to Target Failed",
                f"Failed to move to target {event.target_um:.2f} μm:\n{error_msg}"
            )

    def _on_profile_or_objective_changed(self, event) -> None:
        """Handle profile or objective changes - refresh init state."""
        self.update_init_state()

    def add_components(self) -> None:
        self.btn_set_reference = QPushButton(" Set Reference ")
        self.btn_set_reference.setCheckable(False)
        self.btn_set_reference.setChecked(False)
        self.btn_set_reference.setDefault(False)
        # Will be enabled/disabled by update_init_state based on cached state

        self.label_displacement = QLabel()
        self.label_displacement.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.btn_measure_displacement = QPushButton("Measure Displacement")
        self.btn_measure_displacement.setCheckable(False)
        self.btn_measure_displacement.setChecked(False)
        self.btn_measure_displacement.setDefault(False)
        # Will be enabled/disabled by update_init_state based on cached state

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
        # Will be enabled/disabled by update_init_state based on cached state

        self.grid = QGridLayout()

        self.grid.addWidget(self.btn_set_reference, 0, 0, 1, 4)

        self.grid.addWidget(QLabel("Displacement (um)"), 1, 0)
        self.grid.addWidget(self.label_displacement, 1, 1)
        self.grid.addWidget(self.btn_measure_displacement, 1, 2, 1, 2)

        self.grid.addWidget(QLabel("Target (um)"), 2, 0)
        self.grid.addWidget(self.entry_target, 2, 1)
        self.grid.addWidget(self.btn_move_to_target, 2, 2, 1, 2)
        self.setLayout(self.grid)

        # make connections - all via EventBus, no direct controller signals
        self.btn_set_reference.clicked.connect(self.on_set_reference_clicked)
        self.btn_measure_displacement.clicked.connect(
            self.on_measure_displacement_clicked
        )
        self.btn_move_to_target.clicked.connect(self.move_to_target)
        # Displacement updates via LaserAFDisplacementMeasured event subscription

    def update_init_state(self) -> None:
        """Update button enabled states based on cached initialization state."""
        # Set Reference requires initialization
        self.btn_set_reference.setEnabled(self._is_initialized)
        # Measure/Move require both initialization AND a reference
        self.btn_measure_displacement.setEnabled(self._is_initialized and self._has_reference)
        self.btn_move_to_target.setEnabled(self._is_initialized and self._has_reference)

    def move_to_target(self) -> None:
        was_live = self._is_live
        if was_live:
            self._event_bus.publish(StopLiveCommand(camera="focus"))
        self._event_bus.publish(MoveToLaserAFTargetCommand(displacement_um=self.entry_target.value()))
        if was_live:
            self._event_bus.publish(StartLiveCommand(camera="focus"))

    def on_set_reference_clicked(self) -> None:
        """Handle set reference button click"""
        was_live = self._is_live
        if was_live:
            self._event_bus.publish(StopLiveCommand(camera="focus"))
        self._event_bus.publish(SetLaserAFReferenceCommand())
        if was_live:
            self._event_bus.publish(StartLiveCommand(camera="focus"))

    def on_measure_displacement_clicked(self) -> None:
        was_live = self._is_live
        if was_live:
            self._event_bus.publish(StopLiveCommand(camera="focus"))
        self._event_bus.publish(MeasureLaserAFDisplacementCommand())
        if was_live:
            self._event_bus.publish(StartLiveCommand(camera="focus"))
