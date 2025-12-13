import time
from typing import Optional, Tuple, Dict, Any, TYPE_CHECKING

import cv2
from datetime import datetime
import math
import numpy as np

import squid.core.utils.hardware_utils as utils
import _def
from squid.mcs.controllers.autofocus.laser_af_settings_manager import LaserAFSettingManager
from squid.ops.navigation import ObjectiveStore
from squid.core.utils.config_utils import LaserAFConfig
import squid.core.logging
from squid.core.events import (
    EventBus,
    SetLaserAFPropertiesCommand,
    InitializeLaserAFCommand,
    SetLaserAFCharacterizationModeCommand,
    UpdateLaserAFThresholdCommand,
    MoveToLaserAFTargetCommand,
    SetLaserAFReferenceCommand,
    MeasureLaserAFDisplacementCommand,
    CaptureLaserAFFrameCommand,
    LaserAFPropertiesChanged,
    LaserAFInitialized,
    LaserAFReferenceSet,
    LaserAFDisplacementMeasured,
    LaserAFFrameCaptured,
    LaserAFCrossCorrelationMeasured,
    LaserAFSpotCentroidMeasured,
    ObjectiveChanged,
    ProfileChanged,
)

if TYPE_CHECKING:
    from squid.mcs.services import CameraService, StageService, PeripheralService, PiezoService


class LaserAutofocusController:

    def __init__(
        self,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        piezo_service: Optional["PiezoService"] = None,
        objectiveStore: Optional[ObjectiveStore] = None,
        laserAFSettingManager: Optional[LaserAFSettingManager] = None,
        stream_handler: Optional[object] = None,
    ):
        self._log = squid.core.logging.get_logger(__class__.__name__)

        self.objectiveStore: Optional[ObjectiveStore] = objectiveStore
        self.laserAFSettingManager: Optional[LaserAFSettingManager] = (
            laserAFSettingManager
        )

        self._camera_service: "CameraService" = camera_service
        self._stage_service: "StageService" = stage_service
        self._peripheral_service: "PeripheralService" = peripheral_service
        self._piezo_service: Optional["PiezoService"] = piezo_service
        self._event_bus: "EventBus" = event_bus
        self._stream_handler = stream_handler

        self.characterization_mode: bool = _def.LASER_AF_CHARACTERIZATION_MODE
        self.is_initialized: bool = False

        self.laser_af_properties: LaserAFConfig = LaserAFConfig()
        self.reference_crop: Optional[np.ndarray] = None

        self.spot_spacing_pixels: Optional[float] = (
            None  # spacing between the spots from the two interfaces (unit: pixel)
        )

        self.image: Optional[np.ndarray] = (
            None  # for saving the focus camera image for debugging when centroid cannot be found
        )

        # Load configurations if provided
        if self.laserAFSettingManager:
            self.load_cached_configuration()

        # Subscribe to EventBus commands
        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.subscribe(SetLaserAFPropertiesCommand, self._on_set_properties)
        self._event_bus.subscribe(InitializeLaserAFCommand, self._on_initialize)
        self._event_bus.subscribe(SetLaserAFCharacterizationModeCommand, self._on_set_characterization_mode)
        self._event_bus.subscribe(UpdateLaserAFThresholdCommand, self._on_update_threshold)
        self._event_bus.subscribe(MoveToLaserAFTargetCommand, self._on_move_to_target)
        self._event_bus.subscribe(SetLaserAFReferenceCommand, self._on_set_reference)
        self._event_bus.subscribe(MeasureLaserAFDisplacementCommand, self._on_measure_displacement)
        self._event_bus.subscribe(CaptureLaserAFFrameCommand, self._on_capture_frame)
        self._event_bus.subscribe(ObjectiveChanged, lambda _e: self.on_settings_changed())
        self._event_bus.subscribe(ProfileChanged, lambda _e: self.on_settings_changed())

    def _stream_image(self, image: np.ndarray) -> None:
        if self._stream_handler is None:
            return
        try:
            self._stream_handler.on_new_image(image)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            self._log.exception("Failed to stream laser AF image")

    def _publish_displacement(self, displacement_um: float) -> None:
        if self._event_bus is None:
            return
        success = not math.isnan(displacement_um)
        self._event_bus.publish(
            LaserAFDisplacementMeasured(
                displacement_um=displacement_um if success else None,
                success=success,
            )
        )

    # =========================================================================
    # Service helper methods (services-only)
    # =========================================================================

    def _set_camera_roi(self, x: int, y: int, width: int, height: int) -> None:
        """Set camera region of interest."""
        self._camera_service.set_region_of_interest(x, y, width, height)

    def _set_camera_exposure(self, exposure_ms: float) -> None:
        """Set camera exposure time."""
        self._camera_service.set_exposure_time(exposure_ms)

    def _get_camera_exposure(self) -> float:
        """Get camera exposure time."""
        return self._camera_service.get_exposure_time()

    def _set_camera_analog_gain(self, gain: float) -> None:
        """Set camera analog gain."""
        self._camera_service.set_analog_gain(gain)

    def _send_camera_trigger(self) -> None:
        """Send camera trigger."""
        self._camera_service.send_trigger()

    def _read_camera_frame(self) -> Optional[np.ndarray]:
        """Read frame from camera."""
        return self._camera_service.read_frame()

    def _enable_camera_callbacks(self, enabled: bool) -> None:
        """Enable/disable camera callbacks."""
        self._camera_service.enable_callbacks(enabled)

    def _turn_on_af_laser(self, wait: bool = True) -> None:
        """Turn on autofocus laser."""
        self._peripheral_service.turn_on_af_laser(wait_for_completion=wait)

    def _turn_off_af_laser(self, wait: bool = True) -> None:
        """Turn off autofocus laser."""
        self._peripheral_service.turn_off_af_laser(wait_for_completion=wait)

    def _move_stage_z(self, distance_mm: float) -> None:
        """Move stage Z by relative distance."""
        self._stage_service.move_z(distance_mm)

    def _move_piezo(self, position_um: float) -> None:
        """Move piezo to absolute position."""
        if self._piezo_service is None:
            raise RuntimeError("PiezoService required for piezo move")
        self._piezo_service.move_to(position_um)

    def _get_piezo_position(self) -> float:
        """Get current piezo position."""
        if self._piezo_service is None:
            raise RuntimeError("PiezoService required for piezo position")
        return self._piezo_service.get_position()

    # =========================================================================

    def initialize_manual(self, config: LaserAFConfig) -> None:
        """Initialize laser autofocus with manual parameters."""
        adjusted_config = config.model_copy(
            update={
                "x_reference": config.x_reference
                - config.x_offset,  # self.x_reference is relative to the cropped region
                "x_offset": int((config.x_offset // 8) * 8),
                "y_offset": int((config.y_offset // 2) * 2),
                "width": int((config.width // 8) * 8),
                "height": int((config.height // 2) * 2),
            }
        )

        self.laser_af_properties = adjusted_config

        if self.laser_af_properties.has_reference:
            self.reference_crop = self.laser_af_properties.reference_image_cropped

        self._set_camera_roi(
            self.laser_af_properties.x_offset,
            self.laser_af_properties.y_offset,
            self.laser_af_properties.width,
            self.laser_af_properties.height,
        )

        self.is_initialized = True

        # Update cache if objective store and laser_af_settings is available
        if (
            self.objectiveStore
            and self.laserAFSettingManager
            and self.objectiveStore.current_objective
        ):
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective, config.model_dump()
            )

    def load_cached_configuration(self) -> None:
        """Load configuration from the cache if available.

        Note: This only loads settings, it does NOT initialize the hardware.
        The user must click Initialize to actually set up the laser AF.
        """
        laser_af_settings: Dict[str, Any] = (
            self.laserAFSettingManager.get_laser_af_settings()
        )
        current_objective: Optional[str] = (
            self.objectiveStore.current_objective if self.objectiveStore else None
        )
        if current_objective and current_objective in laser_af_settings:
            config = self.laserAFSettingManager.get_settings_for_objective(
                current_objective
            )

            # Update camera settings
            self._set_camera_exposure(config.focus_camera_exposure_time_ms)
            try:
                self._set_camera_analog_gain(config.focus_camera_analog_gain)
            except NotImplementedError:
                pass

            # Load the config settings but do NOT mark as initialized
            # The user must click Initialize to actually set up the hardware
            self.laser_af_properties = config
            if config.has_reference:
                self.reference_crop = config.reference_image_cropped

    def initialize_auto(self) -> bool:
        """Automatically initialize laser autofocus by finding the spot and calibrating.

        This method:
        1. Finds the laser spot on full sensor
        2. Sets up ROI around the spot
        3. Calibrates pixel-to-um conversion using two z positions

        Returns:
            bool: True if initialization successful, False if any step fails
        """
        self._set_camera_roi(0, 0, 3088, 2064)

        # update camera settings
        self._set_camera_exposure(
            self.laser_af_properties.focus_camera_exposure_time_ms
        )
        try:
            self._set_camera_analog_gain(
                self.laser_af_properties.focus_camera_analog_gain
            )
        except NotImplementedError:
            pass

        # Find initial spot position
        self._turn_on_af_laser()

        result = self._get_laser_spot_centroid(
            remove_background=True,
            use_center_crop=(
                self.laser_af_properties.initialize_crop_width,
                self.laser_af_properties.initialize_crop_height,
            ),
        )
        if result is None:
            self._log.error("Failed to find laser spot during initialization")
            self._turn_off_af_laser()
            return False
        x, y = result

        self._turn_off_af_laser()

        # Set up ROI around spot and clear reference
        config = self.laser_af_properties.model_copy(
            update={
                "x_offset": x - self.laser_af_properties.width / 2,
                "y_offset": y - self.laser_af_properties.height / 2,
                "has_reference": False,
            }
        )
        self.reference_crop = None
        config.set_reference_image(None)
        self._log.info(
            f"Laser spot location on the full sensor is ({int(x)}, {int(y)})"
        )

        self.initialize_manual(config)

        # Calibrate pixel-to-um conversion
        if not self._calibrate_pixel_to_um():
            self._log.error("Failed to calibrate pixel-to-um conversion")
            return False

        self.laserAFSettingManager.save_configurations(
            self.objectiveStore.current_objective
        )

        return True

    def _calibrate_pixel_to_um(self) -> bool:
        """Calibrate pixel-to-um conversion.

        Returns:
            bool: True if calibration successful, False otherwise
        """
        # Calibrate pixel-to-um conversion
        try:
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception(
                "Faield to turn on AF laser before pixel to um calibration, cannot continue!"
            )
            return False

        # Move to first position and measure
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self._piezo_service is not None:
            time.sleep(_def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 1)")
            try:
                self._turn_off_af_laser()
            except TimeoutError:
                self._log.exception(
                    "Error turning off AF laser after spot calibration failure (position 1)"
                )
                # Just fall through since we are already on a failure path.
            return False
        x0, y0 = result

        # Move to second position and measure
        self._move_z(self.laser_af_properties.pixel_to_um_calibration_distance)
        time.sleep(_def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 2)")
            try:
                self._turn_off_af_laser()
            except TimeoutError:
                self._log.exception(
                    "Error turning off AF laser after spot calibration failure (position 2)"
                )
                # Just fall through since we are already on a failure path.
            return False
        x1, y1 = result

        try:
            self._turn_off_af_laser()
        except TimeoutError:
            self._log.exception(
                "Error turning off AF laser after spot calibration acquisition.  Continuing in unknown state"
            )

        # move back to initial position
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self._piezo_service is not None:
            time.sleep(_def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        # Calculate conversion factor
        if x1 - x0 == 0:
            pixel_to_um = 0.4  # Simulation value
            self._log.warning("Using simulation value for pixel_to_um conversion")
        else:
            pixel_to_um = self.laser_af_properties.pixel_to_um_calibration_distance / (
                x1 - x0
            )
        self._log.info(f"Pixel to um conversion factor is {pixel_to_um:.3f} um/pixel")
        calibration_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update config with new calibration values
        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={
                "pixel_to_um": pixel_to_um,
                "calibration_timestamp": calibration_timestamp,
            }
        )

        # Update cache
        if self.objectiveStore and self.laserAFSettingManager:
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective,
                self.laser_af_properties.model_dump(),
            )

        return True

    def set_laser_af_properties(self, updates: dict) -> None:
        """Update laser autofocus properties. Used for updating settings from GUI."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.is_initialized = False

    def update_threshold_properties(self, updates: dict) -> None:
        """Update threshold properties. Save settings without re-initializing."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.laserAFSettingManager.update_laser_af_settings(
            self.objectiveStore.current_objective, updates
        )
        self.laserAFSettingManager.save_configurations(
            self.objectiveStore.current_objective
        )
        self._log.info("Updated threshold properties")

    def measure_displacement(self) -> float:
        """Measure the displacement of the laser spot from the reference position.

        Returns:
            float: Displacement in micrometers, or float('nan') if measurement fails
        """

        def finish_with(um: float) -> float:
            self._publish_displacement(um)
            return um

        try:
            # turn on the laser
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception(
                "Turning on AF laser timed out, failed to measure displacement."
            )
            return finish_with(float("nan"))

        # get laser spot location
        result = self._get_laser_spot_centroid()

        # turn off the laser
        try:
            self._turn_off_af_laser()
        except TimeoutError:
            self._log.exception(
                "Turning off AF laser timed out!  We got a displacement but laser may still be on."
            )
            # Continue with the measurement, but we're essentially in an unknown / weird state here.  It's not clear
            # what we should do.

        if result is None:
            self._log.error(
                "Failed to detect laser spot during displacement measurement"
            )
            return finish_with(float("nan"))  # Signal invalid measurement

        x, y = result
        # calculate displacement
        displacement_um = (
            x - self.laser_af_properties.x_reference
        ) * self.laser_af_properties.pixel_to_um
        return finish_with(displacement_um)

    def move_to_target(self, target_um: float) -> bool:
        """Move the stage to reach a target displacement from reference position.

        Args:
            target_um: Target displacement in micrometers

        Returns:
            bool: True if move was successful, False if measurement failed or displacement was out of range
        """
        if not self.laser_af_properties.has_reference:
            self._log.warning("Cannot move to target - reference not set")
            return False

        current_displacement_um = self.measure_displacement()
        self._log.info(
            f"Current laser AF displacement: {current_displacement_um:.1f} μm"
        )

        if math.isnan(current_displacement_um):
            self._log.error(
                "Cannot move to target: failed to measure current displacement"
            )
            return False

        if abs(current_displacement_um) > self.laser_af_properties.laser_af_range:
            self._log.warning(
                f"Measured displacement ({current_displacement_um:.1f} μm) is unreasonably large, using previous z position"
            )
            return False

        um_to_move = target_um - current_displacement_um
        self._move_z(um_to_move)

        # Verify using cross-correlation only when returning to reference (target ~= 0)
        # For other targets, the spot won't match the reference and that's expected
        if abs(target_um) < 1.0:  # Only verify when target is near zero
            cc_result, correlation = self._verify_spot_alignment()
            if self._event_bus is not None:
                self._event_bus.publish(LaserAFCrossCorrelationMeasured(correlation=correlation))
            if not cc_result:
                self._log.warning("Cross correlation check failed - spots not well aligned")
                # move back to the current position
                self._move_z(-um_to_move)
                return False
            else:
                self._log.info("Cross correlation check passed - spots are well aligned")

        return True

    def _move_z(self, um_to_move: float) -> None:
        if self._piezo_service is not None:
            self._piezo_service.move_relative(um_to_move)
            return
        self._move_stage_z(um_to_move / 1000)

    def set_reference(self) -> bool:
        """Set the current spot position as the reference position.

        Captures and stores both the spot position and a cropped reference image
        around the spot for later alignment verification.

        Returns:
            bool: True if reference was set successfully, False if spot detection failed
        """
        if not self.is_initialized:
            self._log.error("Laser autofocus is not initialized, cannot set reference")
            return False

        # turn on the laser
        try:
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for reference setting!")
            return False

        # get laser spot location and image
        result = self._get_laser_spot_centroid()
        reference_image = self.image

        # turn off the laser
        try:
            self._turn_off_af_laser()
        except TimeoutError:
            self._log.exception(
                "Failed to turn off AF laser after setting reference, laser is in an unknown state!"
            )
            # Continue on since we got our reading, but the system is potentially in a weird state!

        if result is None or reference_image is None:
            self._log.error("Failed to detect laser spot while setting reference")
            return False

        x, y = result

        # Store cropped and normalized reference image
        center_y = int(reference_image.shape[0] / 2)
        x_start = max(0, int(x) - self.laser_af_properties.spot_crop_size // 2)
        x_end = min(
            reference_image.shape[1],
            int(x) + self.laser_af_properties.spot_crop_size // 2,
        )
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(
            reference_image.shape[0],
            center_y + self.laser_af_properties.spot_crop_size // 2,
        )

        reference_crop = reference_image[y_start:y_end, x_start:x_end].astype(
            np.float32
        )
        self.reference_crop = (reference_crop - np.mean(reference_crop)) / np.max(
            reference_crop
        )

        self._publish_displacement(0)
        self._log.info(f"Set reference position to ({x:.1f}, {y:.1f})")

        # Tell simulated camera to use current Z as reference (for spot position simulation)
        try:
            self._camera_service.set_reference_position()  # type: ignore[attr-defined]
        except Exception:
            pass

        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={"x_reference": x, "has_reference": True}
        )  # We don't keep reference_crop here to avoid serializing it

        # Update cached file. reference_crop needs to be saved.
        self.laserAFSettingManager.update_laser_af_settings(
            self.objectiveStore.current_objective,
            {
                "x_reference": x + self.laser_af_properties.x_offset,
                "has_reference": True,
            },
            crop_image=self.reference_crop,
        )
        self.laserAFSettingManager.save_configurations(
            self.objectiveStore.current_objective
        )

        self._log.info("Reference spot position set")

        return True

    def on_settings_changed(self) -> None:
        """Handle objective change or profile load event.

        This method is called when the objective changes. It resets the initialization
        status and loads the cached configuration for the new objective.
        """
        self.is_initialized = False
        self.load_cached_configuration()

    def _verify_spot_alignment(self) -> Tuple[bool, float]:
        """Verify laser spot alignment using cross-correlation with reference image.

        Captures current laser spot image and compares it with the reference image
        using normalized cross-correlation. Images are cropped around the expected
        spot location and normalized by maximum intensity before comparison.

        Returns:
            Tuple[bool, float]: (alignment_ok, correlation) - True if spots are well aligned (correlation > CORRELATION_THRESHOLD), False otherwise
        """
        failure_return_value: Tuple[bool, float] = False, 0.0

        # Get current spot image
        try:
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception(
                "Failed to turn on AF laser for verifying spot alignment."
            )
            return failure_return_value

        # TODO: create a function to get the current image (taking care of trigger mode checking and laser on/off switching)
        """
        self.camera.send_trigger()
        current_image = self.camera.read_frame()
        """
        self._get_laser_spot_centroid()
        current_image: Optional[np.ndarray] = self.image

        try:
            self._turn_off_af_laser()
        except TimeoutError:
            self._log.exception(
                "Failed to turn off AF laser after verifying spot alignment, laser in unknown state!"
            )
            # Continue on because we got a reading, but the system is in a potentially weird and unknown state here.

        if self.reference_crop is None:
            self._log.warning("No reference crop stored")
            return failure_return_value

        if current_image is None:
            self._log.error("Failed to get images for cross-correlation check")
            return failure_return_value

        # Crop and normalize current image
        center_x: int = int(self.laser_af_properties.x_reference)
        center_y: int = int(current_image.shape[0] / 2)

        x_start: int = max(0, center_x - self.laser_af_properties.spot_crop_size // 2)
        x_end: int = min(
            current_image.shape[1],
            center_x + self.laser_af_properties.spot_crop_size // 2,
        )
        y_start: int = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end: int = min(
            current_image.shape[0],
            center_y + self.laser_af_properties.spot_crop_size // 2,
        )

        current_crop: np.ndarray = current_image[y_start:y_end, x_start:x_end].astype(
            np.float32
        )
        current_norm: np.ndarray = (current_crop - np.mean(current_crop)) / np.max(
            current_crop
        )

        # Calculate normalized cross correlation
        correlation: float = np.corrcoef(
            current_norm.ravel(), self.reference_crop.ravel()
        )[0, 1]

        self._log.info(f"Cross correlation with reference: {correlation:.3f}")

        # Check if correlation exceeds threshold
        if correlation < self.laser_af_properties.correlation_threshold:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            return False, correlation

        return True, correlation

    def get_new_frame(self) -> Optional[np.ndarray]:
        # IMPORTANT: This assumes that the autofocus laser is already on!
        self._camera_service.send_trigger(
            illumination_time=self._get_camera_exposure()
        )
        return self._read_camera_frame()

    def _get_laser_spot_centroid(
        self,
        remove_background: bool = False,
        use_center_crop: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[float, float]]:
        """Get the centroid location of the laser spot.

        Averages multiple measurements to improve accuracy. The number of measurements
        is controlled by LASER_AF_AVERAGING_N.

        Returns:
            Optional[Tuple[float, float]]: (x,y) coordinates of spot centroid, or None if detection fails
        """
        # disable camera callback
        self._enable_camera_callbacks(False)

        successful_detections: int = 0
        tmp_x: float = 0
        tmp_y: float = 0

        image: Optional[np.ndarray] = None
        for i in range(self.laser_af_properties.laser_af_averaging_n):
            try:
                image = self.get_new_frame()
                if image is None:
                    self._log.warning(
                        f"Failed to read frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}"
                    )
                    continue

                self.image = image  # store for debugging # TODO: add to return instead of storing
                full_height: int
                full_width: int
                full_height, full_width = image.shape[:2]

                if use_center_crop is not None:
                    image = utils.crop_image(
                        image, use_center_crop[0], use_center_crop[1]
                    )

                if remove_background:
                    # remove background using top hat filter
                    kernel = cv2.getStructuringElement(
                        cv2.MORPH_ELLIPSE, (50, 50)
                    )  # TODO: tmp hard coded value
                    image = cv2.morphologyEx(image, cv2.MORPH_TOPHAT, kernel)

                # calculate centroid
                spot_detection_params: Dict[str, Any] = {
                    "y_window": self.laser_af_properties.y_window,
                    "x_window": self.laser_af_properties.x_window,
                    "peak_width": self.laser_af_properties.min_peak_width,
                    "peak_distance": self.laser_af_properties.min_peak_distance,
                    "peak_prominence": self.laser_af_properties.min_peak_prominence,
                    "spot_spacing": self.laser_af_properties.spot_spacing,
                }
                result: Optional[Tuple[float, float]] = utils.find_spot_location(
                    image,
                    mode=self.laser_af_properties.spot_detection_mode,
                    params=spot_detection_params,
                    filter_sigma=self.laser_af_properties.filter_sigma,
                )
                if result is None:
                    self._log.warning(
                        f"No spot detected in frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}"
                    )
                    continue

                x: float
                y: float
                if use_center_crop is not None:
                    x, y = (
                        result[0] + (full_width - use_center_crop[0]) // 2,
                        result[1] + (full_height - use_center_crop[1]) // 2,
                    )
                else:
                    x, y = result

                if (
                    self.laser_af_properties.has_reference
                    and abs(x - self.laser_af_properties.x_reference)
                    * self.laser_af_properties.pixel_to_um
                    > self.laser_af_properties.laser_af_range
                ):
                    self._log.warning(
                        f"Spot detected at ({x:.1f}, {y:.1f}) is out of range ({self.laser_af_properties.laser_af_range:.1f} μm), skipping it."
                    )
                    continue

                tmp_x += x
                tmp_y += y
                successful_detections += 1

            except Exception as e:
                self._log.error(
                    f"Error processing frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}: {str(e)}"
                )
                continue

        # Re-enable camera callbacks
        self._enable_camera_callbacks(True)

        # optionally display the image
        if _def.LASER_AF_DISPLAY_SPOT_IMAGE and image is not None:
            self._stream_image(image)

        # Check if we got enough successful detections
        if successful_detections <= 0:
            self._log.error("No successful detections")
            return None

        # Calculate average position from successful detections
        x: float = tmp_x / successful_detections
        y: float = tmp_y / successful_detections

        self._log.debug(
            f"Spot centroid found at ({x:.1f}, {y:.1f}) from {successful_detections} detections"
        )
        return (x, y)

    def get_image(self) -> Optional[np.ndarray]:
        """Capture and display a single image from the laser autofocus camera.

        Turns the laser on, captures an image, displays it, then turns the laser off.

        Returns:
            Optional[np.ndarray]: The captured image, or None if capture failed
        """
        # turn on the laser
        try:
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception(
                "Failed to turn on laser AF laser before get_image, cannot get image."
            )
            return None

        try:
            # send trigger, grab image and display image
            self._send_camera_trigger()
            image = self._read_camera_frame()

            if image is None:
                self._log.error("Failed to read frame in get_image")
                return None

            self._stream_image(image)
            return image

        except Exception as e:
            self._log.error(f"Error capturing image: {str(e)}")
            return None

        finally:
            # turn off the laser
            try:
                self._turn_off_af_laser()
            except TimeoutError:
                self._log.exception("Failed to turn off AF laser after get_image!")

    # =========================================================================
    # EventBus Command Handlers
    # =========================================================================

    def _on_set_properties(self, cmd: SetLaserAFPropertiesCommand) -> None:
        """Handle SetLaserAFPropertiesCommand from EventBus."""
        self.set_laser_af_properties(cmd.properties)
        if self._event_bus:
            self._event_bus.publish(LaserAFPropertiesChanged(properties=cmd.properties))

    def _on_initialize(self, cmd: InitializeLaserAFCommand) -> None:
        """Handle InitializeLaserAFCommand from EventBus."""
        success = self.initialize_auto()
        if self._event_bus:
            self._event_bus.publish(LaserAFInitialized(is_initialized=self.is_initialized, success=success))

    def _on_set_characterization_mode(self, cmd: SetLaserAFCharacterizationModeCommand) -> None:
        """Handle SetLaserAFCharacterizationModeCommand from EventBus."""
        self.characterization_mode = cmd.enabled

    def _on_update_threshold(self, cmd: UpdateLaserAFThresholdCommand) -> None:
        """Handle UpdateLaserAFThresholdCommand from EventBus."""
        self.update_threshold_properties(cmd.updates)

    def _on_move_to_target(self, cmd: MoveToLaserAFTargetCommand) -> None:
        """Handle MoveToLaserAFTargetCommand from EventBus."""
        if cmd.displacement_um is not None:
            self.move_to_target(cmd.displacement_um)

    def _on_set_reference(self, cmd: SetLaserAFReferenceCommand) -> None:
        """Handle SetLaserAFReferenceCommand from EventBus."""
        success = self.set_reference()
        if self._event_bus:
            self._event_bus.publish(LaserAFReferenceSet(success=success))

    def _on_measure_displacement(self, cmd: MeasureLaserAFDisplacementCommand) -> None:
        """Handle MeasureLaserAFDisplacementCommand from EventBus."""
        self.measure_displacement()

    def _on_capture_frame(self, cmd: CaptureLaserAFFrameCommand) -> None:
        """Handle CaptureLaserAFFrameCommand from EventBus."""
        if self._event_bus is None:
            return

        try:
            self._turn_on_af_laser()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for frame capture")
            self._event_bus.publish(LaserAFFrameCaptured(success=False))
            self._event_bus.publish(
                LaserAFSpotCentroidMeasured(success=False, error="Failed to turn on AF laser")
            )
            return

        try:
            result = self._get_laser_spot_centroid()
            image = self.image
            if image is not None:
                self._stream_image(image)
            if result is None:
                self._event_bus.publish(LaserAFFrameCaptured(success=False))
                self._event_bus.publish(
                    LaserAFSpotCentroidMeasured(
                        success=False, error="Spot detection failed"
                    )
                )
            else:
                x, y = result
                self._event_bus.publish(LaserAFFrameCaptured(success=True))
                self._event_bus.publish(
                    LaserAFSpotCentroidMeasured(success=True, x_px=x, y_px=y)
                )
        finally:
            try:
                self._turn_off_af_laser()
            except TimeoutError:
                self._log.exception("Failed to turn off AF laser after frame capture")
