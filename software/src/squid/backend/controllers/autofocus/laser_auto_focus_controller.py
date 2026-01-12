import time
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, TYPE_CHECKING, List
from datetime import datetime
import math
import numpy as np

import _def
import squid.core.utils.hardware_utils as utils
from squid.backend.controllers.autofocus.laser_af_settings_manager import LaserAFSettingManager
from squid.backend.managers import ObjectiveStore
from squid.backend.processing.laser_spot import (
    SpotDetectionResult,
    compute_correlation,
    compute_displacement,
    detect_spot,
    extract_spot_crop,
    is_spot_in_range,
    normalize_crop_for_reference,
)
from squid.core.config.feature_flags import get_feature_flags
from squid.core.utils.config_utils import LaserAFConfig
import squid.core.logging
from squid.backend.controllers.base import BaseController
from squid.core.events import (
    Event,
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
    LaserAFMoveCompleted,
    ObjectiveChanged,
    ProfileChanged,
    handles,
)

if TYPE_CHECKING:
    from squid.backend.services import CameraService, StageService, PeripheralService, PiezoService


@dataclass
class LaserAFResult:
    """Result of a laser autofocus displacement measurement."""

    displacement_um: float
    spot_intensity: float
    spot_snr: float
    correlation: Optional[float]
    spot_x_px: Optional[float]
    spot_y_px: Optional[float]
    timestamp: float
    image: Optional[np.ndarray] = None


@dataclass
class LaserSpotCentroid:
    """Spot centroid measurement with the captured frame."""

    x_px: float
    y_px: float
    image: np.ndarray


class LaserAutofocusController(BaseController):

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
        super().__init__(event_bus)

        self.objectiveStore: Optional[ObjectiveStore] = objectiveStore
        self.laserAFSettingManager: Optional[LaserAFSettingManager] = (
            laserAFSettingManager
        )

        self._camera_service: "CameraService" = camera_service
        self._stage_service: "StageService" = stage_service
        self._peripheral_service: "PeripheralService" = peripheral_service
        self._piezo_service: Optional["PiezoService"] = piezo_service
        self._stream_handler = stream_handler

        self._feature_flags = get_feature_flags()
        self.characterization_mode: bool = self._feature_flags.is_enabled(
            "LASER_AF_CHARACTERIZATION_MODE"
        )
        self.is_initialized: bool = False

        self.laser_af_properties: LaserAFConfig = LaserAFConfig()
        self.reference_crop: Optional[np.ndarray] = None

        self.spot_spacing_pixels: Optional[float] = (
            None  # spacing between the spots from the two interfaces (unit: pixel)
        )
        self._last_crop: Optional[np.ndarray] = None
        self._last_crop_bounds: Optional[Tuple[int, int, int, int]] = None
        self._last_spot_metrics: Optional[Tuple[float, float, float]] = None
        self._last_frame: Optional[np.ndarray] = None
        self._measurement_lock = threading.Lock()

        # Load configurations if provided
        if self.laserAFSettingManager:
            self.load_cached_configuration()

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

    def _capture_frame(self, *, illumination_time: Optional[float] = None) -> Optional[np.ndarray]:
        """Capture a frame, sending a trigger when required."""
        from squid.core.abc import CameraAcquisitionMode

        acquisition_mode = self._camera_service.get_acquisition_mode()
        if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._camera_service.send_trigger(
                illumination_time=illumination_time or self._get_camera_exposure()
            )
        frame = self._read_camera_frame()
        if frame is not None:
            self._last_frame = frame
        return frame

    def get_last_frame(self) -> Optional[np.ndarray]:
        """Return the most recently captured autofocus frame, if available."""
        return self._last_frame

    def _enable_camera_callbacks(self, enabled: bool) -> None:
        """Enable/disable camera callbacks."""
        self._camera_service.enable_callbacks(enabled)

    def _turn_on_af_laser(self, wait: bool = True) -> None:
        """Turn on autofocus laser."""
        self._peripheral_service.turn_on_af_laser(wait_for_completion=wait)

    def _turn_off_af_laser(self, wait: bool = True) -> None:
        """Turn off autofocus laser."""
        self._peripheral_service.turn_off_af_laser(wait_for_completion=wait)

    def turn_on_laser(self, bypass_mode_gate: bool = False) -> None:
        """Turn on AF laser for continuous lock."""
        try:
            self._peripheral_service.turn_on_af_laser(
                wait_for_completion=True, bypass_mode_gate=bypass_mode_gate
            )
        except TypeError:
            self._peripheral_service.turn_on_af_laser(wait_for_completion=True)

    def turn_off_laser(self, bypass_mode_gate: bool = False) -> None:
        """Turn off AF laser after continuous lock."""
        try:
            self._peripheral_service.turn_off_af_laser(
                wait_for_completion=True, bypass_mode_gate=bypass_mode_gate
            )
        except TypeError:
            self._peripheral_service.turn_off_af_laser(wait_for_completion=True)

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
        x = result.x_px
        y = result.y_px

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
        x0 = result.x_px
        y0 = result.y_px

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
        x1 = result.x_px
        y1 = result.y_px

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

        if not self._measurement_lock.acquire(timeout=0.1):
            self._log.warning("Measurement blocked - continuous lock is running")
            return float("nan")

        def finish_with(um: float) -> float:
            self._publish_displacement(um)
            return um

        try:
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

            # calculate displacement
            displacement_um = compute_displacement(
                result.x_px,
                self.laser_af_properties.x_reference,
                self.laser_af_properties.pixel_to_um,
            )
            return finish_with(displacement_um)
        finally:
            self._measurement_lock.release()

    def measure_displacement_continuous(self) -> LaserAFResult:
        """Measure displacement assuming laser is already on."""
        if not self._measurement_lock.acquire(timeout=0.1):
            self._log.warning("Measurement blocked - continuous lock is running")
            return LaserAFResult(
                displacement_um=float("nan"),
                spot_intensity=0.0,
                spot_snr=0.0,
                correlation=None,
                spot_x_px=None,
                spot_y_px=None,
                timestamp=time.monotonic(),
            )
        try:
            frame = self._capture_frame(illumination_time=self._get_camera_exposure())
            if frame is None:
                return LaserAFResult(
                    displacement_um=float("nan"),
                    spot_intensity=0.0,
                    spot_snr=0.0,
                    correlation=None,
                    spot_x_px=None,
                    spot_y_px=None,
                    timestamp=time.monotonic(),
                )

            result = self._detect_spot_and_compute_displacement(frame)
            if result is None:
                return LaserAFResult(
                    displacement_um=float("nan"),
                    spot_intensity=0.0,
                    spot_snr=0.0,
                    correlation=None,
                    spot_x_px=None,
                    spot_y_px=None,
                    timestamp=time.monotonic(),
                    image=frame,
                )

            displacement_um, spot_x, spot_y, snr, intensity, correlation = result
            return LaserAFResult(
                displacement_um=displacement_um,
                spot_intensity=intensity,
                spot_snr=snr,
                correlation=correlation,
                spot_x_px=spot_x,
                spot_y_px=spot_y,
                timestamp=time.monotonic(),
                image=frame,
            )
        finally:
            self._measurement_lock.release()

    def move_to_target(self, target_um: float, publish_result: bool = True) -> bool:
        """Move the stage to reach a target displacement from reference position.

        Args:
            target_um: Target displacement in micrometers
            publish_result: Whether to publish LaserAFMoveCompleted event (default True)

        Returns:
            bool: True if move was successful, False if measurement failed or displacement was out of range
        """
        if not self.laser_af_properties.has_reference:
            self._log.warning("Cannot move to target - reference not set")
            if publish_result and self._event_bus is not None:
                self._event_bus.publish(LaserAFMoveCompleted(
                    success=False, target_um=target_um, error="Reference not set"
                ))
            return False

        current_displacement_um = self.measure_displacement()
        self._log.info(
            f"Current laser AF displacement: {current_displacement_um:.1f} μm"
        )

        if math.isnan(current_displacement_um):
            self._log.error(
                "Cannot move to target: failed to measure current displacement"
            )
            if publish_result and self._event_bus is not None:
                self._event_bus.publish(LaserAFMoveCompleted(
                    success=False, target_um=target_um, error="Failed to measure current displacement"
                ))
            return False

        if abs(current_displacement_um) > self.laser_af_properties.laser_af_range:
            self._log.warning(
                f"Measured displacement ({current_displacement_um:.1f} μm) is unreasonably large, using previous z position"
            )
            if publish_result and self._event_bus is not None:
                self._event_bus.publish(LaserAFMoveCompleted(
                    success=False, target_um=target_um,
                    error=f"Displacement {current_displacement_um:.1f} μm out of range"
                ))
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
                if publish_result and self._event_bus is not None:
                    self._event_bus.publish(LaserAFMoveCompleted(
                        success=False, target_um=target_um, error="Cross-correlation check failed"
                    ))
                return False
            else:
                self._log.info("Cross correlation check passed - spots are well aligned")

        # Measure final displacement after move to confirm
        final_displacement = self.measure_displacement()
        self._log.info(f"Final displacement after move: {final_displacement:.1f} μm (target: {target_um:.1f} μm)")

        if publish_result and self._event_bus is not None:
            self._event_bus.publish(LaserAFMoveCompleted(
                success=True, target_um=target_um, final_displacement_um=final_displacement
            ))

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

        # turn off the laser
        try:
            self._turn_off_af_laser()
        except TimeoutError:
            self._log.exception(
                "Failed to turn off AF laser after setting reference, laser is in an unknown state!"
            )
            # Continue on since we got our reading, but the system is potentially in a weird state!

        if result is None:
            self._log.error("Failed to detect laser spot while setting reference")
            return False

        reference_image = result.image
        x = result.x_px
        y = result.y_px

        crop_size = max(1, int(self.laser_af_properties.spot_crop_size))
        center_y = reference_image.shape[0] / 2
        reference_crop, _ = extract_spot_crop(
            reference_image, x, center_y, crop_size
        )
        self.reference_crop = normalize_crop_for_reference(reference_crop)
        if self.reference_crop is None:
            self._log.error("Failed to normalize reference crop")
            return False

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

    @handles(ObjectiveChanged, ProfileChanged)
    def _on_profile_or_objective_changed(self, _event: Event) -> None:
        self.on_settings_changed()

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

        current_image = self._capture_frame(illumination_time=self._get_camera_exposure())

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
        if self.laser_af_properties.x_reference is None:
            self._log.warning("No reference position stored")
            return failure_return_value

        if current_image is None:
            self._log.error("Failed to get images for cross-correlation check")
            return failure_return_value

        crop_size = max(1, int(self.laser_af_properties.spot_crop_size))
        current_crop, _bounds = extract_spot_crop(
            current_image,
            self.laser_af_properties.x_reference,
            current_image.shape[0] / 2,
            crop_size,
        )
        correlation = compute_correlation(current_crop, self.reference_crop)
        if correlation is None:
            self._log.warning("Cross correlation check failed - invalid crops")
            return False, 0.0

        self._log.info(f"Cross correlation with reference: {correlation:.3f}")

        # Check if correlation exceeds threshold
        if correlation < self.laser_af_properties.correlation_threshold:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            return False, correlation

        return True, correlation

    def get_new_frame(self) -> Optional[np.ndarray]:
        # IMPORTANT: This assumes that the autofocus laser is already on!
        return self._capture_frame(illumination_time=self._get_camera_exposure())

    def _get_laser_spot_centroid(
        self,
        remove_background: bool = False,
        use_center_crop: Optional[Tuple[int, int]] = None,
    ) -> Optional[LaserSpotCentroid]:
        """Get the centroid location of the laser spot.

        Averages multiple measurements to improve accuracy. The number of measurements
        is controlled by LASER_AF_AVERAGING_N.

        Returns:
            LaserSpotCentroid containing centroid and image, or None if detection fails
        """
        # disable camera callback
        self._enable_camera_callbacks(False)

        # Wait for camera to settle after disabling callbacks
        # This is necessary because if live mode just stopped, the camera
        # might still be processing frames or in a transitional state
        time.sleep(0.05)  # 50ms settle time

        # Flush any stale frames in the buffer by doing a dummy read
        try:
            self._camera_service.send_trigger(illumination_time=self._get_camera_exposure())
            _ = self._read_camera_frame()  # Discard this frame
        except Exception:
            pass  # Ignore errors during flush

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

                result = self._detect_spot_in_frame(
                    image,
                    remove_background=remove_background,
                    use_center_crop=use_center_crop,
                )
                if result is None:
                    self._log.warning(
                        f"No spot detected in frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}"
                    )
                    continue

                tmp_x += result.x
                tmp_y += result.y
                successful_detections += 1

            except Exception as e:
                self._log.error(
                    f"Error processing frame {i + 1}/{self.laser_af_properties.laser_af_averaging_n}: {str(e)}"
                )
                continue

        # Re-enable camera callbacks
        self._enable_camera_callbacks(True)

        # optionally display the image
        if self._feature_flags.is_enabled("LASER_AF_DISPLAY_SPOT_IMAGE") and image is not None:
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
        if image is not None:
            self._update_last_crop_and_metrics(image, x, y)
            return LaserSpotCentroid(x_px=x, y_px=y, image=image)
        return None

    def _update_last_crop_and_metrics(
        self, image: np.ndarray, spot_x: float, spot_y: float
    ) -> None:
        crop_size = max(1, int(self.laser_af_properties.spot_crop_size))
        x_center = int(round(spot_x))
        y_center = int(round(spot_y))
        x_start = max(0, x_center - crop_size // 2)
        x_end = min(image.shape[1], x_center + crop_size // 2)
        y_start = max(0, y_center - crop_size // 2)
        y_end = min(image.shape[0], y_center + crop_size // 2)

        crop = image[y_start:y_end, x_start:x_end]
        self._last_crop = crop
        self._last_crop_bounds = (x_start, y_start, x_end, y_end)

        local_x = spot_x - x_start
        local_y = spot_y - y_start
        self._last_spot_metrics = utils.extract_spot_metrics(crop, local_x, local_y)

    def _detect_spot_in_frame(
        self,
        frame: np.ndarray,
        *,
        remove_background: bool = False,
        use_center_crop: Optional[Tuple[int, int]] = None,
    ) -> Optional[SpotDetectionResult]:
        spot_detection_params: Dict[str, Any] = {
            "y_window": self.laser_af_properties.y_window,
            "x_window": self.laser_af_properties.x_window,
            "min_peak_width": self.laser_af_properties.min_peak_width,
            "min_peak_distance": self.laser_af_properties.min_peak_distance,
            "min_peak_prominence": self.laser_af_properties.min_peak_prominence,
            "spot_spacing": self.laser_af_properties.spot_spacing,
        }
        spot = detect_spot(
            frame,
            params=spot_detection_params,
            mode=self.laser_af_properties.spot_detection_mode,
            filter_sigma=self.laser_af_properties.filter_sigma,
            remove_bg=remove_background,
            center_crop=use_center_crop,
        )
        if spot is None:
            return None

        x_reference = self.laser_af_properties.x_reference
        if (
            self.laser_af_properties.has_reference
            and x_reference is not None
            and not is_spot_in_range(
                spot_x=spot.x,
                reference_x=x_reference,
                pixel_to_um=self.laser_af_properties.pixel_to_um,
                max_range_um=self.laser_af_properties.laser_af_range,
            )
        ):
            self._log.warning(
                "Spot detected at (%.1f, %.1f) is out of range (%.1f um), skipping it.",
                spot.x,
                spot.y,
                self.laser_af_properties.laser_af_range,
            )
            return None

        return spot

    def _detect_spot_and_compute_displacement(
        self, frame: np.ndarray
    ) -> Optional[Tuple[float, float, float, float, float, Optional[float]]]:
        spot = self._detect_spot_in_frame(frame)
        if spot is None:
            return None

        self._update_last_crop_and_metrics(frame, spot.x, spot.y)
        if self._last_spot_metrics is None:
            snr = 0.0
            intensity = 0.0
        else:
            snr, intensity, _background = self._last_spot_metrics

        x_reference = self.laser_af_properties.x_reference or 0.0
        displacement_um = compute_displacement(
            spot.x, x_reference, self.laser_af_properties.pixel_to_um
        )

        correlation: Optional[float] = None
        if self.laser_af_properties.has_reference and self.reference_crop is not None:
            crop_size = max(1, int(self.laser_af_properties.spot_crop_size))
            current_crop, _bounds = extract_spot_crop(
                frame,
                x_reference,
                frame.shape[0] / 2,
                crop_size,
            )
            correlation = compute_correlation(current_crop, self.reference_crop)

        return (displacement_um, spot.x, spot.y, snr, intensity, correlation)

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

    @handles(SetLaserAFPropertiesCommand)
    def _on_set_properties(self, cmd: SetLaserAFPropertiesCommand) -> None:
        """Handle SetLaserAFPropertiesCommand from EventBus."""
        self.set_laser_af_properties(cmd.properties)
        if self._event_bus:
            self._event_bus.publish(LaserAFPropertiesChanged(properties=cmd.properties))

    @handles(InitializeLaserAFCommand)
    def _on_initialize(self, cmd: InitializeLaserAFCommand) -> None:
        """Handle InitializeLaserAFCommand from EventBus."""
        success = self.initialize_auto()
        if self._event_bus:
            self._event_bus.publish(LaserAFInitialized(is_initialized=self.is_initialized, success=success))

    @handles(SetLaserAFCharacterizationModeCommand)
    def _on_set_characterization_mode(self, cmd: SetLaserAFCharacterizationModeCommand) -> None:
        """Handle SetLaserAFCharacterizationModeCommand from EventBus."""
        self.characterization_mode = cmd.enabled

    @handles(UpdateLaserAFThresholdCommand)
    def _on_update_threshold(self, cmd: UpdateLaserAFThresholdCommand) -> None:
        """Handle UpdateLaserAFThresholdCommand from EventBus."""
        self.update_threshold_properties(cmd.updates)

    @handles(MoveToLaserAFTargetCommand)
    def _on_move_to_target(self, cmd: MoveToLaserAFTargetCommand) -> None:
        """Handle MoveToLaserAFTargetCommand from EventBus."""
        if cmd.displacement_um is not None:
            self.move_to_target(cmd.displacement_um)

    @handles(SetLaserAFReferenceCommand)
    def _on_set_reference(self, cmd: SetLaserAFReferenceCommand) -> None:
        """Handle SetLaserAFReferenceCommand from EventBus."""
        success = self.set_reference()
        if self._event_bus:
            self._event_bus.publish(LaserAFReferenceSet(success=success))

    @handles(MeasureLaserAFDisplacementCommand)
    def _on_measure_displacement(self, cmd: MeasureLaserAFDisplacementCommand) -> None:
        """Handle MeasureLaserAFDisplacementCommand from EventBus."""
        self.measure_displacement()

    @handles(CaptureLaserAFFrameCommand)
    def _on_capture_frame(self, cmd: CaptureLaserAFFrameCommand) -> None:
        """Handle CaptureLaserAFFrameCommand from EventBus.

        This captures a single frame and runs spot detection.
        """
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
            # Capture a single frame for spot detection
            image = self.get_new_frame()
            if image is None:
                self._log.error("Failed to capture frame for spot detection")
                self._event_bus.publish(LaserAFFrameCaptured(success=False))
                self._event_bus.publish(
                    LaserAFSpotCentroidMeasured(success=False, error="Failed to capture frame")
                )
                return

            result = self._detect_spot_in_frame(image)

            # Stream the image to the display first
            if image is not None:
                self._stream_image(image)

            if result is None:
                self._event_bus.publish(LaserAFFrameCaptured(success=False))
                self._event_bus.publish(
                    LaserAFSpotCentroidMeasured(
                        success=False, error="Spot detection failed", image=image
                    )
                )
            else:
                x = result.x
                y = result.y
                self._log.info(f"Spot detected at ({x:.1f}, {y:.1f})")
                self._event_bus.publish(LaserAFFrameCaptured(success=True))
                # Include the image in the event so the widget can display it with the crosshair
                self._event_bus.publish(
                    LaserAFSpotCentroidMeasured(success=True, x_px=x, y_px=y, image=image)
                )
        finally:
            try:
                self._turn_off_af_laser()
            except TimeoutError:
                self._log.exception("Failed to turn off AF laser after frame capture")
