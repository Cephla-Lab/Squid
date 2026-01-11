"""
AcquisitionPlanner - Estimation and validation logic for acquisitions.

This module provides pure calculation logic for:
- Image count estimation
- Disk storage estimation
- RAM usage estimation for mosaic display
- Acquisition settings validation

All methods are designed to be stateless and easily testable.
"""

import math
import pathlib
import tempfile
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

import squid.core.logging
import squid.core.utils.hardware_utils as utils
from squid.backend.io import utils_acquisition
import squid.core.abc
import _def

if TYPE_CHECKING:
    from squid.backend.managers import ScanCoordinates, ObjectiveStore
    from squid.backend.services import CameraService
    from squid.backend.controllers.autofocus import LaserAutofocusController
    from squid.core.utils.config_utils import ChannelMode


_log = squid.core.logging.get_logger(__name__)


@dataclass
class AcquisitionEstimate:
    """Estimated resource requirements for an acquisition."""
    image_count: int
    disk_bytes: int
    mosaic_ram_bytes: int


@dataclass
class ValidationResult:
    """Result of acquisition settings validation."""
    is_valid: bool
    errors: List[str]

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(is_valid=True, errors=[])

    @classmethod
    def failure(cls, errors: List[str]) -> "ValidationResult":
        return cls(is_valid=False, errors=errors)


class AcquisitionPlanner:
    """
    Provides estimation and validation logic for acquisitions.

    All methods are designed to be pure functions or have minimal side effects,
    making them easy to test in isolation.

    Usage:
        planner = AcquisitionPlanner()

        # Calculate image count
        count = planner.calculate_image_count(
            scan_coordinates=scan_coords,
            num_timepoints=10,
            num_z_levels=5,
            num_configurations=3,
        )

        # Estimate disk usage
        disk_bytes = planner.estimate_disk_storage_bytes(
            image_count=count,
            camera_service=camera,
            channel_config_manager=config_manager,
            objective_store=objectives,
        )

        # Validate settings
        result = planner.validate_settings(
            do_reflection_af=True,
            laser_af_controller=laser_af,
        )
        if not result.is_valid:
            print(f"Validation errors: {result.errors}")
    """

    def calculate_image_count(
        self,
        scan_coordinates: "ScanCoordinates",
        num_timepoints: int,
        num_z_levels: int,
        num_configurations: int,
        merge_channels: bool = False,
    ) -> int:
        """
        Calculate total number of images for an acquisition.

        Args:
            scan_coordinates: ScanCoordinates with region/FOV information
            num_timepoints: Number of timepoints (Nt)
            num_z_levels: Number of Z levels (NZ)
            num_configurations: Number of channel configurations
            merge_channels: Whether merged channel images are created

        Returns:
            Total number of images to be captured

        Raises:
            ValueError: If scan_coordinates is not properly configured
        """
        try:
            # Count coordinates across all regions
            coords_per_region = [
                len(region_coords)
                for region_id, region_coords in scan_coordinates.region_fov_coordinates.items()
            ]
            total_coords = sum(coords_per_region)

            # Standard images: timepoints * z_levels * coords * configurations
            standard_images = (
                num_timepoints * num_z_levels * total_coords * num_configurations
            )

            # Merged images: one per FOV per z-level per timepoint
            merged_images = 0
            if merge_channels:
                merged_images = num_timepoints * num_z_levels * total_coords

            return standard_images + merged_images

        except AttributeError as e:
            raise ValueError(
                f"scan_coordinates not properly configured: {e}"
            )

    def estimate_disk_storage_bytes(
        self,
        image_count: int,
        camera_service: "CameraService",
        channel_config_manager: Any,
        objective_store: "ObjectiveStore",
        sample_image: Optional[np.ndarray] = None,
    ) -> int:
        """
        Estimate disk storage required for an acquisition.

        This method attempts to capture a sample image to get an accurate
        size estimate. If that fails, it uses a worst-case estimate.

        Args:
            image_count: Total number of images (from calculate_image_count)
            camera_service: Camera service for capturing sample
            channel_config_manager: Manager with channel configurations
            objective_store: ObjectiveStore for configurations
            sample_image: Optional pre-captured sample image

        Returns:
            Estimated disk usage in bytes
        """
        # Get first configuration for test save
        configurations = channel_config_manager.get_configurations(
            objective_store.current_objective
        )
        if not configurations:
            raise ValueError(
                "Cannot calculate disk space requirements without any valid configurations."
            )
        first_config = configurations[0]

        # Get or create sample image
        test_image = sample_image
        is_color = True

        if test_image is None:
            test_image, is_color = self._get_sample_image(camera_service)

        if test_image is None:
            # Create synthetic worst-case image
            pixel_format = camera_service.get_pixel_format()
            is_color = (
                pixel_format is not None
                and squid.core.abc.CameraPixelFormat.is_color_format(pixel_format)
            )
            width, height = camera_service.get_crop_size()
            test_image = np.random.randint(
                2**16 - 1,
                size=(height, width, 3 if is_color else 1),
                dtype=np.uint16,
            )

        # Measure actual saved size
        size_per_image = self._measure_saved_image_size(
            test_image, first_config, is_color
        )

        # Add overhead for metadata files (~100KB)
        non_image_overhead = 100 * 1024

        return size_per_image * image_count + non_image_overhead

    def estimate_mosaic_ram_bytes(
        self,
        scan_coordinates: "ScanCoordinates",
        objective_store: "ObjectiveStore",
        camera_service: "CameraService",
        num_configurations: int,
    ) -> int:
        """
        Estimate RAM required for mosaic display.

        The mosaic view holds a downsampled composite image of all FOVs.
        This method estimates the memory required based on scan bounds
        and pixel size.

        Args:
            scan_coordinates: ScanCoordinates with region bounds
            objective_store: ObjectiveStore for magnification factor
            camera_service: Camera service for pixel size
            num_configurations: Number of channel configurations

        Returns:
            Estimated RAM usage in bytes, or 0 if mosaic is disabled
        """
        if not _def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
            return 0

        if not scan_coordinates or not scan_coordinates.has_regions():
            return 0

        bounds = scan_coordinates.get_scan_bounds()
        if not bounds:
            return 0

        # Calculate scan extents in mm
        width_mm = bounds["x"][1] - bounds["x"][0]
        height_mm = bounds["y"][1] - bounds["y"][0]

        # Get effective pixel size with downsampling
        pixel_size_um = (
            objective_store.get_pixel_size_factor()
            * camera_service.get_pixel_size_binned_um()
        )
        downsample_factor = max(
            1, int(_def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um)
        )
        viewer_pixel_size_mm = (pixel_size_um * downsample_factor) / 1000

        # Calculate mosaic dimensions
        mosaic_width = int(math.ceil(width_mm / viewer_pixel_size_mm))
        mosaic_height = int(math.ceil(height_mm / viewer_pixel_size_mm))

        # Memory calculation (2 bytes per pixel for uint16)
        bytes_per_pixel = 2

        # Check for color camera
        try:
            is_color_attr = getattr(camera_service, "is_color", None)
            if callable(is_color_attr):
                if is_color_attr():
                    bytes_per_pixel *= 3
            elif isinstance(is_color_attr, bool) and is_color_attr:
                bytes_per_pixel *= 3
        except Exception:
            pass

        if num_configurations == 0:
            _log.warning(
                "Estimated mosaic RAM is 0 because no channel configurations are selected."
            )
            return 0

        return mosaic_width * mosaic_height * bytes_per_pixel * num_configurations

    def validate_settings(
        self,
        do_reflection_af: bool,
        laser_af_controller: Optional["LaserAutofocusController"],
    ) -> ValidationResult:
        """
        Validate acquisition settings before starting.

        Args:
            do_reflection_af: Whether laser reflection autofocus is enabled
            laser_af_controller: Optional laser autofocus controller

        Returns:
            ValidationResult indicating success or failure with error messages
        """
        errors = []

        if do_reflection_af:
            if laser_af_controller is None:
                errors.append(
                    "Laser Autofocus Not Ready - Laser AF controller not configured."
                )
            else:
                laser_props = getattr(laser_af_controller, "laser_af_properties", None)
                if laser_props is None or not getattr(laser_props, "has_reference", False):
                    errors.append(
                        "Laser Autofocus Not Ready - Please set the laser autofocus "
                        "reference position before starting acquisition with laser AF enabled."
                    )

        if errors:
            return ValidationResult.failure(errors)
        return ValidationResult.success()

    def get_full_estimate(
        self,
        scan_coordinates: "ScanCoordinates",
        num_timepoints: int,
        num_z_levels: int,
        configurations: List["ChannelMode"],
        camera_service: "CameraService",
        channel_config_manager: Any,
        objective_store: "ObjectiveStore",
        merge_channels: bool = False,
    ) -> AcquisitionEstimate:
        """
        Get a complete estimate of resource requirements.

        This is a convenience method that calls all estimation methods.

        Args:
            scan_coordinates: ScanCoordinates with region/FOV information
            num_timepoints: Number of timepoints (Nt)
            num_z_levels: Number of Z levels (NZ)
            configurations: List of channel configurations
            camera_service: Camera service
            channel_config_manager: Channel configuration manager
            objective_store: ObjectiveStore
            merge_channels: Whether merged channel images are created

        Returns:
            AcquisitionEstimate with all resource estimates
        """
        num_configs = len(configurations)

        image_count = self.calculate_image_count(
            scan_coordinates=scan_coordinates,
            num_timepoints=num_timepoints,
            num_z_levels=num_z_levels,
            num_configurations=num_configs,
            merge_channels=merge_channels,
        )

        disk_bytes = self.estimate_disk_storage_bytes(
            image_count=image_count,
            camera_service=camera_service,
            channel_config_manager=channel_config_manager,
            objective_store=objective_store,
        )

        mosaic_ram = self.estimate_mosaic_ram_bytes(
            scan_coordinates=scan_coordinates,
            objective_store=objective_store,
            camera_service=camera_service,
            num_configurations=num_configs,
        )

        return AcquisitionEstimate(
            image_count=image_count,
            disk_bytes=disk_bytes,
            mosaic_ram_bytes=mosaic_ram,
        )

    # === Private Helper Methods ===

    def _get_sample_image(
        self,
        camera_service: "CameraService",
    ) -> Tuple[Optional[np.ndarray], bool]:
        """
        Attempt to capture a sample image from the camera.

        Returns:
            Tuple of (image, is_color) or (None, True) if capture failed
        """
        was_streaming = camera_service.get_is_streaming()
        callbacks_were_enabled = camera_service.get_callbacks_enabled()

        try:
            camera_service.enable_callbacks(False)
            if not was_streaming:
                camera_service.start_streaming()

            # Send trigger and read frame
            camera_service.send_trigger()
            frame = camera_service.read_frame()

            if frame is not None:
                # read_frame returns ndarray directly
                is_color = len(frame.shape) == 3 and frame.shape[2] == 3
                return frame, is_color

        except Exception:
            _log.debug("Failed to capture sample image for size estimate")
        finally:
            camera_service.enable_callbacks(callbacks_were_enabled)
            if not was_streaming:
                camera_service.stop_streaming()

        return None, True

    def _measure_saved_image_size(
        self,
        image: np.ndarray,
        config: "ChannelMode",
        is_color: bool,
    ) -> int:
        """
        Measure the actual disk size of a saved image.

        This accounts for compression and file format overhead.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            file_id = "test_id"
            size_before = utils.get_directory_disk_usage(pathlib.Path(temp_dir))

            utils_acquisition.save_image(
                image, file_id, temp_dir, config, is_color
            )

            size_after = utils.get_directory_disk_usage(pathlib.Path(temp_dir))
            return size_after - size_before
