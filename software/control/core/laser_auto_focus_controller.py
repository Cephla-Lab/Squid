import time
from typing import Optional, Tuple

import cv2
from datetime import datetime
import math
import numpy as np
from PyQt5.QtCore import QObject
from qtpy.QtCore import Signal

from control import utils
import control._def
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.core.live_controller import LiveController
from control.core.objective_store import ObjectiveStore
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from control.utils_config import LaserAFConfig
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
        laserAFSettingManager: Optional[LaserAFSettingManager] = None,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(__class__.__name__)
        self.microcontroller = microcontroller
        self.camera: AbstractCamera = camera
        self.liveController: LiveController = liveController
        self.stage = stage
        self.piezo = piezo
        self.objectiveStore = objectiveStore
        self.laserAFSettingManager = laserAFSettingManager
        self.characterization_mode = control._def.LASER_AF_CHARACTERIZATION_MODE

        self.is_initialized = False

        self.laser_af_properties = LaserAFConfig()
        self.reference_crop = None

        self.spot_spacing_pixels = None  # spacing between the spots from the two interfaces (unit: pixel)

        self.image = None  # for saving the focus camera image for debugging when centroid cannot be found

        # Load configurations if provided
        if self.laserAFSettingManager:
            self.load_cached_configuration()

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

            # Invalidate reference if crop image is missing
            if self.reference_crop is None:
                self._log.warning("Loaded laser AF profile is missing reference image. Please re-set reference.")
                self.laser_af_properties = self.laser_af_properties.model_copy(update={"has_reference": False})
                self.reference_crop = None

        self.camera.set_region_of_interest(
            self.laser_af_properties.x_offset,
            self.laser_af_properties.y_offset,
            self.laser_af_properties.width,
            self.laser_af_properties.height,
        )

        self.is_initialized = True

        # Update cache if objective store and laser_af_settings is available
        if self.objectiveStore and self.laserAFSettingManager and self.objectiveStore.current_objective:
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective, config.model_dump()
            )

    def load_cached_configuration(self):
        """Load configuration from the cache if available."""
        laser_af_settings = self.laserAFSettingManager.get_laser_af_settings()
        current_objective = self.objectiveStore.current_objective if self.objectiveStore else None
        if current_objective and current_objective in laser_af_settings:
            config = self.laserAFSettingManager.get_settings_for_objective(current_objective)

            # Update camera settings
            self.camera.set_exposure_time(config.focus_camera_exposure_time_ms)
            try:
                self.camera.set_analog_gain(config.focus_camera_analog_gain)
            except NotImplementedError:
                pass

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
            ignore_row_tolerance=True,  # Spot can be anywhere on full frame during init
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

        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)

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
        if self.objectiveStore and self.laserAFSettingManager:
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective, self.laser_af_properties.model_dump()
            )

        return True

    def set_laser_af_properties(self, updates: dict) -> None:
        """Update laser autofocus properties. Used for updating settings from GUI."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.is_initialized = False

    def update_threshold_properties(self, updates: dict) -> None:
        """Update threshold properties. Save settings without re-initializing."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.laserAFSettingManager.update_laser_af_settings(self.objectiveStore.current_objective, updates)
        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)
        self._log.info("Updated threshold properties")

    def _turn_on_laser(self) -> None:
        """Turn on AF laser. Raises TimeoutError on failure."""
        self.microcontroller.turn_on_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

    def _turn_off_laser(self) -> None:
        """Turn off AF laser. Raises TimeoutError on failure."""
        self.microcontroller.turn_off_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

    def _get_displacement_from_centroid(self, centroid: tuple) -> float:
        """Calculate displacement in um from centroid coordinates."""
        x, y = centroid
        return (x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um

    def measure_displacement(self, search_for_spot: bool = True) -> float:
        """Measure the displacement of the laser spot from the reference position.

        Args:
            search_for_spot: If True, search for spot if not found at current position

        Returns:
            float: Displacement in micrometers, or float('nan') if measurement fails
        """

        def finish_with(um: float) -> float:
            self.signal_displacement_um.emit(um)
            return um

        try:
            self._turn_on_laser()
        except TimeoutError:
            self._log.exception("Turning on AF laser timed out, failed to measure displacement.")
            return finish_with(float("nan"))

        # get laser spot location
        result = self._get_laser_spot_centroid()

        if result is not None:
            # Spot found on first try
            try:
                self._turn_off_laser()
            except TimeoutError:
                self._log.exception("Turning off AF laser timed out! Laser may still be on.")
            return finish_with(self._get_displacement_from_centroid(result))

        self._log.error("Failed to detect laser spot during displacement measurement")

        if not search_for_spot:
            try:
                self._turn_off_laser()
            except TimeoutError:
                self._log.exception("Turning off AF laser timed out! Laser may still be on.")
            return finish_with(float("nan"))

        # Search for spot by scanning through z range (laser stays on during search)
        search_step_um = 10  # Step size in micrometers

        # Get current z position in um (piezo or stage)
        if self.piezo is not None:
            current_z_um = self.piezo.position
            # For piezo, clamp bounds to valid piezo range (0 to range_um)
            lower_bound_um = max(0, current_z_um - self.laser_af_properties.laser_af_range)
            upper_bound_um = min(self.piezo.range_um, current_z_um + self.laser_af_properties.laser_af_range)
        else:
            current_z_um = self.stage.get_pos().z_mm * 1000
            lower_bound_um = current_z_um - self.laser_af_properties.laser_af_range
            upper_bound_um = current_z_um + self.laser_af_properties.laser_af_range

        # Generate positions going downward (from current to lower_bound)
        downward_positions = []
        pos = current_z_um - search_step_um
        while pos >= lower_bound_um:
            downward_positions.append(pos)
            pos -= search_step_um

        # Generate positions going upward (from current to upper_bound)
        upward_positions = []
        pos = current_z_um + search_step_um
        while pos <= upper_bound_um:
            upward_positions.append(pos)
            pos += search_step_um

        # Order positions based on search direction preference
        if control._def.LASER_AF_SEARCH_DOWN_FIRST:
            # Search downward first, then upward
            search_positions_um = downward_positions + [current_z_um] + upward_positions
        else:
            # Search upward first, then downward
            search_positions_um = upward_positions + [current_z_um] + downward_positions

        self._log.info(
            f"Starting spot search ({'downward' if control._def.LASER_AF_SEARCH_DOWN_FIRST else 'upward'} first): "
            f"positions {search_positions_um} um"
        )

        current_pos_um = current_z_um  # Track where we are

        for target_pos_um in search_positions_um:
            # Move to target position
            move_um = target_pos_um - current_pos_um
            if move_um != 0:
                self._log.info(f"Z search: moving to {target_pos_um:.1f} um (delta: {move_um:+.1f} um)")
                self._move_z(move_um)
                current_pos_um = target_pos_um
                # Wait for piezo to settle
                if self.piezo is not None:
                    time.sleep(control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000)
            else:
                self._log.info(f"Z search: checking current position {target_pos_um:.1f} um")

            # Attempt spot detection
            result = self._get_laser_spot_centroid()

            if result is None:
                self._log.info(f"Z search: no valid spot at {target_pos_um:.1f} um")
                continue

            displacement_um = self._get_displacement_from_centroid(result)
            if abs(displacement_um) > search_step_um + 4:
                self._log.info(
                    f"Z search: spot at {target_pos_um:.1f} um has displacement {displacement_um:.1f} um (out of range)"
                )
                continue

            self._log.info(f"Z search: spot found at {target_pos_um:.1f} um, displacement {displacement_um:.1f} um")
            try:
                self._turn_off_laser()
            except TimeoutError:
                self._log.exception("Turning off AF laser timed out! Laser may still be on.")
            return finish_with(displacement_um)

        # Spot not found - move back to original position
        self._restore_to_position(current_z_um)
        self._log.warning("Spot not found during z search")

        try:
            self._turn_off_laser()
        except TimeoutError:
            self._log.exception("Turning off AF laser timed out! Laser may still be on.")
        return finish_with(float("nan"))

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

        # Record original z position so we can restore it on failure
        if self.piezo is not None:
            original_z_um = self.piezo.position
        else:
            original_z_um = self.stage.get_pos().z_mm * 1000

        # Debug timestamp for characterization mode
        debug_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f") if self.characterization_mode else None

        current_displacement_um = self.measure_displacement()
        self._log.info(f"Current laser AF displacement: {current_displacement_um:.1f} μm")

        # Debug: save image after measurement (after search if triggered)
        if self.characterization_mode and self.image is not None:
            cv2.imwrite(f"/tmp/laser_af_{debug_timestamp}_1_measurement.bmp", self.image)

        if math.isnan(current_displacement_um):
            self._log.error("Cannot move to target: failed to measure current displacement")
            # measure_displacement already restores position on search failure
            return False

        if abs(current_displacement_um) > self.laser_af_properties.laser_af_range:
            self._log.warning(f"Measured displacement ({current_displacement_um:.1f} μm) is unreasonably large")
            self._restore_to_position(original_z_um)
            return False

        um_to_move = target_um - current_displacement_um
        self._move_z(um_to_move)

        # Verify using cross-correlation that spot is in same location as reference
        cc_result, correlation = self._verify_spot_alignment()

        # Debug: save image used for cross-correlation verification
        if self.characterization_mode and self.image is not None:
            cv2.imwrite(f"/tmp/laser_af_{debug_timestamp}_2_cc_verify.bmp", self.image)

        self.signal_cross_correlation.emit(correlation)
        if not cc_result:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            # Restore to original position (not just undo last move)
            self._restore_to_position(original_z_um)
            return False
        else:
            self._log.info("Cross correlation check passed - spots are well aligned")
            return True

    def _restore_to_position(self, target_z_um: float) -> None:
        """Restore z position to a specific absolute position."""
        if self.piezo is not None:
            current_z_um = self.piezo.position
        else:
            current_z_um = self.stage.get_pos().z_mm * 1000

        move_um = target_z_um - current_z_um
        if abs(move_um) > 0.01:  # Only move if difference is significant
            self._log.info(f"Restoring z position: moving {move_um:.1f} μm")
            self._move_z(move_um)

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
        x_end = min(
            reference_image.shape[1],
            int(x) + self.laser_af_properties.spot_crop_size // 2,
        )
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(
            reference_image.shape[0],
            center_y + self.laser_af_properties.spot_crop_size // 2,
        )

        reference_crop = reference_image[y_start:y_end, x_start:x_end].astype(np.float32)
        self.reference_crop = (reference_crop - np.mean(reference_crop)) / np.max(reference_crop)

        self._log.info(
            f"Reference crop updated: shape={self.reference_crop.shape}, "
            f"crop region=[{x_start}:{x_end}, {y_start}:{y_end}]"
        )

        self.signal_displacement_um.emit(0)
        self._log.info(f"Set reference position to ({x:.1f}, {y:.1f})")

        self.laser_af_properties = self.laser_af_properties.model_copy(update={"x_reference": x, "has_reference": True})
        # Update the reference image in laser_af_properties
        # so that self.laser_af_properties.reference_image_cropped stays in sync with self.reference_crop
        self.laser_af_properties.set_reference_image(self.reference_crop)

        # Update cached file
        self.laserAFSettingManager.update_laser_af_settings(
            self.objectiveStore.current_objective,
            {
                "x_reference": x + self.laser_af_properties.x_offset,
                "has_reference": True,
            },
            crop_image=self.reference_crop,
        )
        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)

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
        failure_return_value = False, np.array([0.0, 0.0])

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
        centroid_result = self._get_laser_spot_centroid()
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

        if centroid_result is None:
            self._log.error("Failed to detect spot centroid for cross-correlation check")
            return failure_return_value

        # Crop current image around the detected peak (not the reference position)
        current_peak_x, current_peak_y = centroid_result
        center_x = int(current_peak_x)
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

        if False:  # Set to True to enable debug plot
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(1, 3, figsize=(12, 4))

            # Reference crop
            axes[0].imshow(self.reference_crop, cmap="gray")
            axes[0].set_title(f"Reference Crop\n(x={self.laser_af_properties.x_reference:.1f})")
            axes[0].axis("off")

            # Current crop
            axes[1].imshow(current_norm, cmap="gray")
            axes[1].set_title(f"Current Crop\n(x={current_peak_x:.1f})")
            axes[1].axis("off")

            # Difference image
            diff = current_norm - self.reference_crop
            axes[2].imshow(diff, cmap="RdBu", vmin=-0.5, vmax=0.5)
            axes[2].set_title("Difference\n(Current - Reference)")
            axes[2].axis("off")

            passed = correlation >= self.laser_af_properties.correlation_threshold
            status = "PASS" if passed else "FAIL"
            color = "green" if passed else "red"
            peak_diff = current_peak_x - self.laser_af_properties.x_reference
            fig.suptitle(
                f"Cross-Correlation: {correlation:.3f} (threshold={self.laser_af_properties.correlation_threshold}) [{status}]\n"
                f"Peak shift: {peak_diff:.1f} pixels",
                fontsize=11,
                color=color,
            )

            plt.tight_layout()
            plt.show()

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
        self,
        remove_background: bool = False,
        use_center_crop: Optional[Tuple[int, int]] = None,
        ignore_row_tolerance: bool = False,
    ) -> Optional[Tuple[float, float]]:
        """Get the centroid location of the laser spot.

        Averages multiple measurements to improve accuracy. The number of measurements
        is controlled by LASER_AF_AVERAGING_N.

        Args:
            remove_background: Apply background removal using top-hat filter
            use_center_crop: (width, height) to crop around center before detection
            ignore_row_tolerance: If True, disable row tolerance filtering (for initialization)

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

                # calculate centroid using connected components parameters
                # Use large row_tolerance during initialization when spot location is unknown
                row_tolerance = image.shape[0] if ignore_row_tolerance else self.laser_af_properties.cc_row_tolerance
                spot_detection_params = {
                    "threshold": self.laser_af_properties.cc_threshold,
                    "min_area": self.laser_af_properties.cc_min_area,
                    "max_area": self.laser_af_properties.cc_max_area,
                    "row_tolerance": row_tolerance,
                }

                result = utils.find_spot_location(
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

                # Unpack result: (centroid_x, centroid_y)
                spot_x, spot_y = result

                if use_center_crop is not None:
                    x, y = (
                        spot_x + (full_width - use_center_crop[0]) // 2,
                        spot_y + (full_height - use_center_crop[1]) // 2,
                    )
                else:
                    x, y = spot_x, spot_y

                if (
                    self.laser_af_properties.has_reference
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
