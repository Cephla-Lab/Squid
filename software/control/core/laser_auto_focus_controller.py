import time
from typing import Optional, Tuple

import cv2
from datetime import datetime
import math
import numpy as np
from qtpy.QtCore import QObject, Signal

from control import utils
import control._def
from control.core.config import ConfigRepository
from control.core.live_controller import LiveController
from control.core.objective_store import ObjectiveStore
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from control.models import LaserAFConfig
from squid.abc import AbstractCamera, AbstractStage
import squid.logging


class LaserAutofocusController(QObject):
    image_to_display = Signal(np.ndarray)
    signal_displacement_um = Signal(float)
    signal_cross_correlation = Signal(float)
    signal_piezo_position_update = Signal()  # Signal to emit piezo position updates

    def __init__(
        self,
        microcontroller: Microcontroller,
        camera: AbstractCamera,
        liveController: LiveController,
        stage: AbstractStage,
        piezo: Optional[PiezoStage] = None,
        objectiveStore: Optional[ObjectiveStore] = None,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(__class__.__name__)
        self.microcontroller = microcontroller
        self.camera: AbstractCamera = camera
        self.liveController: LiveController = liveController
        self.stage = stage
        self.piezo = piezo
        self.objectiveStore = objectiveStore
        self.characterization_mode = control._def.LASER_AF_CHARACTERIZATION_MODE

        self.is_initialized = False

        self.laser_af_properties = LaserAFConfig()
        self.reference_crop = None

        self.spot_spacing_pixels = None  # spacing between the spots from the two interfaces (unit: pixel)

        self.image = None  # for saving the focus camera image for debugging when centroid cannot be found

        # Load configurations if available
        self.load_cached_configuration()

    @property
    def _config_repo(self) -> ConfigRepository:
        """Access ConfigRepository via LiveController's microscope."""
        return self.liveController.microscope.config_repo

    @property
    def _current_profile(self) -> Optional[str]:
        """Get current profile from ConfigRepository."""
        return self._config_repo.current_profile

    def initialize_manual(self, config: LaserAFConfig) -> None:
        """Initialize laser autofocus with manual parameters."""
        # x_reference needs adjustment only if set
        x_ref_adjusted = config.x_reference - config.x_offset if config.x_reference is not None else None
        adjusted_config = config.model_copy(
            update={
                "x_reference": x_ref_adjusted,  # self.x_reference is relative to the cropped region
                "x_offset": int((config.x_offset // 8) * 8),
                "y_offset": int((config.y_offset // 2) * 2),
                "width": int((config.width // 8) * 8),
                "height": int((config.height // 2) * 2),
            }
        )

        self.laser_af_properties = adjusted_config

        if self.laser_af_properties.has_reference:
            self.reference_crop = self.laser_af_properties.reference_image_cropped

        self.camera.set_region_of_interest(
            self.laser_af_properties.x_offset,
            self.laser_af_properties.y_offset,
            self.laser_af_properties.width,
            self.laser_af_properties.height,
        )

        self.is_initialized = True

        # Update cache if objective store and profile is available
        if self.objectiveStore and self._current_profile and self.objectiveStore.current_objective:
            updated_config = LaserAFConfig(**config.model_dump())
            self._config_repo.save_laser_af_config(
                self._current_profile, self.objectiveStore.current_objective, updated_config
            )

    def load_cached_configuration(self):
        """Load configuration from the cache if available."""
        if not self._current_profile:
            return

        current_objective = self.objectiveStore.current_objective if self.objectiveStore else None
        if not current_objective:
            return

        config = self._config_repo.get_laser_af_config(current_objective)
        if config is None:
            return

        # Update camera settings
        self.camera.set_exposure_time(config.focus_camera_exposure_time_ms)
        try:
            self.camera.set_analog_gain(config.focus_camera_analog_gain)
        except NotImplementedError:
            # Some camera drivers don't support analog gain; continue with existing gain
            self._log.debug(
                f"Focus camera does not support setting analog gain; "
                f"continuing with existing gain (requested: {config.focus_camera_analog_gain})"
            )

        # Initialize with loaded config
        self.initialize_manual(config)

    def initialize_auto(self) -> bool:
        """Automatically initialize laser autofocus by finding the spot and calibrating.

        This method:
        1. Finds the laser spot on full sensor
        2. Sets up ROI around the spot
        3. Calibrates pixel-to-um conversion using two z positions

        Returns:
            bool: True if initialization successful, False if any step fails
        """
        self.camera.set_region_of_interest(0, 0, 3088, 2064)

        # update camera settings
        self.camera.set_exposure_time(self.laser_af_properties.focus_camera_exposure_time_ms)
        try:
            self.camera.set_analog_gain(self.laser_af_properties.focus_camera_analog_gain)
        except NotImplementedError:
            pass

        # Find initial spot position
        self.microcontroller.turn_on_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

        result = self._get_laser_spot_centroid(
            remove_background=True,
            use_center_crop=(
                self.laser_af_properties.initialize_crop_width,
                self.laser_af_properties.initialize_crop_height,
            ),
        )
        if result is None:
            self._log.error("Failed to find laser spot during initialization")
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
            return False
        x, y = result

        self.microcontroller.turn_off_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

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
        self._log.info(f"Laser spot location on the full sensor is ({int(x)}, {int(y)})")

        self.initialize_manual(config)

        # Calibrate pixel-to-um conversion
        if not self._calibrate_pixel_to_um():
            self._log.error("Failed to calibrate pixel-to-um conversion")
            return False

        # Save configuration
        if self._current_profile:
            self._config_repo.save_laser_af_config(
                self._current_profile, self.objectiveStore.current_objective, self.laser_af_properties
            )

        return True

    def _calibrate_pixel_to_um(self) -> bool:
        """Calibrate pixel-to-um conversion.

        Returns:
            bool: True if calibration successful, False otherwise
        """
        # Calibrate pixel-to-um conversion
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Faield to turn on AF laser before pixel to um calibration, cannot continue!")
            return False

        # Move to first position and measure
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self.piezo is not None:
            time.sleep(control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 1)")
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Error turning off AF laser after spot calibration failure (position 1)")
                # Just fall through since we are already on a failure path.
            return False
        x0, y0 = result

        # Move to second position and measure
        self._move_z(self.laser_af_properties.pixel_to_um_calibration_distance)
        time.sleep(control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 2)")
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Error turning off AF laser after spot calibration failure (position 2)")
                # Just fall through since we are already on a failure path.
            return False
        x1, y1 = result

        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception(
                "Error turning off AF laser after spot calibration acquisition.  Continuing in unknown state"
            )

        # move back to initial position
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self.piezo is not None:
            time.sleep(control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000)

        # Calculate conversion factor
        if x1 - x0 == 0:
            pixel_to_um = 0.4  # Simulation value
            self._log.warning("Using simulation value for pixel_to_um conversion")
        else:
            pixel_to_um = self.laser_af_properties.pixel_to_um_calibration_distance / (x1 - x0)
        self._log.info(f"Pixel to um conversion factor is {pixel_to_um:.3f} um/pixel")
        calibration_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update config with new calibration values
        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={"pixel_to_um": pixel_to_um, "calibration_timestamp": calibration_timestamp}
        )

        # Update cache
        if self.objectiveStore and self._current_profile:
            self._config_repo.save_laser_af_config(
                self._current_profile, self.objectiveStore.current_objective, self.laser_af_properties
            )

        return True

    def set_laser_af_properties(self, updates: dict) -> None:
        """Update laser autofocus properties. Used for updating settings from GUI."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.is_initialized = False

    def update_threshold_properties(self, updates: dict) -> None:
        """Update threshold properties. Save settings without re-initializing."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        if self._current_profile and self.objectiveStore:
            self._config_repo.save_laser_af_config(
                self._current_profile, self.objectiveStore.current_objective, self.laser_af_properties
            )
        self._log.info("Updated threshold properties")

    def measure_displacement(self) -> float:
        """Measure the displacement of the laser spot from the reference position.

        Returns:
            float: Displacement in micrometers, or float('nan') if measurement fails
        """

        def finish_with(um: float) -> float:
            self.signal_displacement_um.emit(um)
            return um

        try:
            # turn on the laser
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Turning on AF laser timed out, failed to measure displacement.")
            return finish_with(float("nan"))

        # get laser spot location
        result = self._get_laser_spot_centroid()

        # turn off the laser
        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Turning off AF laser timed out!  We got a displacement but laser may still be on.")
            # Continue with the measurement, but we're essentially in an unknown / weird state here.  It's not clear
            # what we should do.

        if result is None:
            self._log.error("Failed to detect laser spot during displacement measurement")
            return finish_with(float("nan"))  # Signal invalid measurement

        if self.laser_af_properties.x_reference is None:
            self._log.warning("Cannot calculate displacement - reference position not set")
            return finish_with(float("nan"))

        x, y = result
        # calculate displacement
        displacement_um = (x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um
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
        self._log.info(f"Current laser AF displacement: {current_displacement_um:.1f} μm")

        if math.isnan(current_displacement_um):
            self._log.error("Cannot move to target: failed to measure current displacement")
            return False

        if abs(current_displacement_um) > self.laser_af_properties.laser_af_range:
            self._log.warning(
                f"Measured displacement ({current_displacement_um:.1f} μm) is unreasonably large, using previous z position"
            )
            return False

        um_to_move = target_um - current_displacement_um
        self._move_z(um_to_move)

        # Verify using cross-correlation that spot is in same location as reference
        cc_result, correlation = self._verify_spot_alignment()
        self.signal_cross_correlation.emit(correlation)
        if not cc_result:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            # move back to the current position
            self._move_z(-um_to_move)
            return False
        else:
            self._log.info("Cross correlation check passed - spots are well aligned")
            return True

    def _move_z(self, um_to_move: float) -> None:
        if self.piezo is not None:
            # TODO: check if um_to_move is in the range of the piezo
            self.piezo.move_relative(um_to_move)
            self.signal_piezo_position_update.emit()
        else:
            self.stage.move_z(um_to_move / 1000)

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
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for reference setting!")
            return False

        # get laser spot location and image
        result = self._get_laser_spot_centroid()
        reference_image = self.image

        # turn off the laser
        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn off AF laser after setting reference, laser is in an unknown state!")
            # Continue on since we got our reading, but the system is potentially in a weird state!

        if result is None or reference_image is None:
            self._log.error("Failed to detect laser spot while setting reference")
            return False

        x, y = result

        # Store cropped and normalized reference image
        center_y = int(reference_image.shape[0] / 2)
        x_start = max(0, int(x) - self.laser_af_properties.spot_crop_size // 2)
        x_end = min(reference_image.shape[1], int(x) + self.laser_af_properties.spot_crop_size // 2)
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(reference_image.shape[0], center_y + self.laser_af_properties.spot_crop_size // 2)

        reference_crop = reference_image[y_start:y_end, x_start:x_end].astype(np.float32)
        self.reference_crop = (reference_crop - np.mean(reference_crop)) / np.max(reference_crop)

        self.signal_displacement_um.emit(0)
        self._log.info(f"Set reference position to ({x:.1f}, {y:.1f})")

        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={"x_reference": x, "has_reference": True}
        )  # We don't keep reference_crop here to avoid serializing it

        # Update cached file. reference_crop needs to be saved.
        if self._current_profile and self.objectiveStore:
            # Create config for saving with reference image encoded
            save_config = self.laser_af_properties.model_copy(
                update={"x_reference": x + self.laser_af_properties.x_offset, "has_reference": True}
            )
            save_config.set_reference_image(self.reference_crop)
            self._config_repo.save_laser_af_config(
                self._current_profile, self.objectiveStore.current_objective, save_config
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

    def _verify_spot_alignment(self) -> Tuple[bool, np.array]:
        """Verify laser spot alignment using cross-correlation with reference image.

        Captures current laser spot image and compares it with the reference image
        using normalized cross-correlation. Images are cropped around the expected
        spot location and normalized by maximum intensity before comparison.

        Returns:
            bool: True if spots are well aligned (correlation > CORRELATION_THRESHOLD), False otherwise
        """
        failure_return_value = False, float("nan")

        # Get current spot image
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for verifying spot alignment.")
            return failure_return_value

        # TODO: create a function to get the current image (taking care of trigger mode checking and laser on/off switching)
        """
        self.camera.send_trigger()
        current_image = self.camera.read_frame()
        """
        self._get_laser_spot_centroid()
        current_image = self.image

        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn off AF laser after verifying spot alignment, laser in unknown state!")
            # Continue on because we got a reading, but the system is in a potentially weird and unknown state here.

        if self.reference_crop is None:
            self._log.warning("No reference crop stored")
            return failure_return_value

        if current_image is None:
            self._log.error("Failed to get images for cross-correlation check")
            return failure_return_value

        if self.laser_af_properties.x_reference is None:
            self._log.error("Cannot verify spot alignment - reference position not set")
            return failure_return_value

        # Crop and normalize current image
        center_x = int(self.laser_af_properties.x_reference)
        center_y = int(current_image.shape[0] / 2)

        x_start = max(0, center_x - self.laser_af_properties.spot_crop_size // 2)
        x_end = min(current_image.shape[1], center_x + self.laser_af_properties.spot_crop_size // 2)
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(current_image.shape[0], center_y + self.laser_af_properties.spot_crop_size // 2)

        current_crop = current_image[y_start:y_end, x_start:x_end].astype(np.float32)
        current_norm = (current_crop - np.mean(current_crop)) / np.max(current_crop)

        # Calculate normalized cross correlation
        correlation = np.corrcoef(current_norm.ravel(), self.reference_crop.ravel())[0, 1]

        self._log.info(f"Cross correlation with reference: {correlation:.3f}")

        # Check if correlation exceeds threshold
        if correlation < self.laser_af_properties.correlation_threshold:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            return False, correlation

        return True, correlation

    def get_new_frame(self):
        # IMPORTANT: This assumes that the autofocus laser is already on!
        self.camera.send_trigger(self.camera.get_exposure_time())
        return self.camera.read_frame()

    def _get_laser_spot_centroid(
        self, remove_background: bool = False, use_center_crop: Optional[Tuple[int, int]] = None
    ) -> Optional[Tuple[float, float]]:
        """Get the centroid location of the laser spot.

        Averages multiple measurements to improve accuracy. The number of measurements
        is controlled by LASER_AF_AVERAGING_N.

        Returns:
            Optional[Tuple[float, float]]: (x,y) coordinates of spot centroid, or None if detection fails
        """
        # disable camera callback
        self.camera.enable_callbacks(False)

        successful_detections = 0
        tmp_x = 0
        tmp_y = 0

        image = None
        for i in range(self.laser_af_properties.laser_af_averaging_n):
            try:
                image = self.get_new_frame()
                if image is None:
                    self._log.warning(f"Failed to read frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}")
                    continue

                self.image = image  # store for debugging # TODO: add to return instead of storing
                full_height, full_width = image.shape[:2]

                if use_center_crop is not None:
                    image = utils.crop_image(image, use_center_crop[0], use_center_crop[1])

                if remove_background:
                    # remove background using top hat filter
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (50, 50))  # TODO: tmp hard coded value
                    image = cv2.morphologyEx(image, cv2.MORPH_TOPHAT, kernel)

                # calculate centroid
                spot_detection_params = {
                    "y_window": self.laser_af_properties.y_window,
                    "x_window": self.laser_af_properties.x_window,
                    "peak_width": self.laser_af_properties.min_peak_width,
                    "peak_distance": self.laser_af_properties.min_peak_distance,
                    "peak_prominence": self.laser_af_properties.min_peak_prominence,
                    "spot_spacing": self.laser_af_properties.spot_spacing,
                }
                result = utils.find_spot_location(
                    image,
                    mode=self.laser_af_properties.get_spot_detection_mode(),
                    params=spot_detection_params,
                    filter_sigma=self.laser_af_properties.filter_sigma,
                )
                if result is None:
                    self._log.warning(
                        f"No spot detected in frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}"
                    )
                    continue

                if use_center_crop is not None:
                    x, y = (
                        result[0] + (full_width - use_center_crop[0]) // 2,
                        result[1] + (full_height - use_center_crop[1]) // 2,
                    )
                else:
                    x, y = result

                if (
                    self.laser_af_properties.has_reference
                    and self.laser_af_properties.x_reference is not None
                    and abs(x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um
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

        # optionally display the image
        if control._def.LASER_AF_DISPLAY_SPOT_IMAGE:
            self.image_to_display.emit(image)

        # Check if we got enough successful detections
        if successful_detections <= 0:
            self._log.error(f"No successful detections")
            return None

        # Calculate average position from successful detections
        x = tmp_x / successful_detections
        y = tmp_y / successful_detections

        self._log.debug(f"Spot centroid found at ({x:.1f}, {y:.1f}) from {successful_detections} detections")
        return (x, y)

    def get_image(self) -> Optional[np.ndarray]:
        """Capture and display a single image from the laser autofocus camera.

        Turns the laser on, captures an image, displays it, then turns the laser off.

        Returns:
            Optional[np.ndarray]: The captured image, or None if capture failed
        """
        # turn on the laser
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on laser AF laser before get_image, cannot get image.")
            return None

        try:
            # send trigger, grab image and display image
            self.camera.send_trigger()
            image = self.camera.read_frame()

            if image is None:
                self._log.error("Failed to read frame in get_image")
                return None

            self.image_to_display.emit(image)
            return image

        except Exception as e:
            self._log.error(f"Error capturing image: {str(e)}")
            return None

        finally:
            # turn off the laser
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Failed to turn off AF laser after get_image!")
