"""
Acquisition planning and validation for multipoint acquisitions.

This module provides:
- AcquisitionPlanner: Estimates disk space, RAM, and validates acquisition settings

Extracted from MultiPointController to enable reuse by the orchestrator.
"""

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Set, TYPE_CHECKING

import squid.core.abc
import squid.core.logging
import squid.core.utils.hardware_utils as utils
from squid.backend.io import utils_acquisition

if TYPE_CHECKING:
    from squid.backend.managers import ObjectiveStore, ChannelConfigService, ScanCoordinates
    from squid.backend.services import CameraService

_log = squid.core.logging.get_logger(__name__)


@dataclass
class AcquisitionEstimate:
    """Estimated resource requirements for an acquisition."""

    image_count: int
    disk_bytes: int
    mosaic_ram_bytes: int
    duration_seconds: float  # Rough estimate

    @property
    def disk_gb(self) -> float:
        """Disk space in GB."""
        return self.disk_bytes / (1024 ** 3)

    @property
    def mosaic_ram_mb(self) -> float:
        """Mosaic RAM in MB."""
        return self.mosaic_ram_bytes / (1024 ** 2)


@dataclass
class ValidationResult:
    """Result of acquisition validation."""

    valid: bool
    errors: List[str]
    warnings: List[str]

    def __bool__(self) -> bool:
        return self.valid


class AcquisitionPlanner:
    """
    Plans and validates acquisition settings.

    Responsibilities:
    - Estimate disk space requirements
    - Estimate mosaic RAM requirements
    - Calculate image counts
    - Validate acquisition settings before starting

    Usage:
        planner = AcquisitionPlanner(
            objective_store=objective_store,
            channel_config_manager=channel_config_manager,
            camera_service=camera_service,
        )

        # Get estimates
        estimate = planner.estimate(
            scan_coordinates=scan_coords,
            configurations=selected_configs,
            n_z=10,
            n_t=1,
        )
        print(f"Disk: {estimate.disk_gb:.2f} GB")

        # Validate settings
        result = planner.validate(
            configurations=selected_configs,
            scan_coordinates=scan_coords,
            available_disk_bytes=1_000_000_000,
        )
        if not result:
            print(result.errors)
    """

    # Default time per FOV for duration estimation (seconds)
    DEFAULT_SECONDS_PER_FOV = 2.0

    # Target pixel size for mosaic view (microns)
    MOSAIC_TARGET_PIXEL_SIZE_UM = 10.0

    def __init__(
        self,
        objective_store: "ObjectiveStore",
        channel_config_manager: "ChannelConfigService",
        camera_service: "CameraService",
        *,
        merge_channels_enabled: bool = False,
        mosaic_display_enabled: bool = True,
    ):
        """
        Initialize the acquisition planner.

        Args:
            objective_store: ObjectiveStore for magnification info
            channel_config_manager: ChannelConfigService for config access
            camera_service: CameraService for camera info
            merge_channels_enabled: Whether merged channel images are saved
            mosaic_display_enabled: Whether mosaic display is enabled
        """
        self._objective_store = objective_store
        self._channel_config_manager = channel_config_manager
        self._camera_service = camera_service
        self._merge_channels = merge_channels_enabled
        self._mosaic_enabled = mosaic_display_enabled

    def estimate(
        self,
        scan_coordinates: "ScanCoordinates",
        configurations: List[Any],
        n_z: int = 1,
        n_t: int = 1,
    ) -> AcquisitionEstimate:
        """
        Estimate resource requirements for an acquisition.

        Args:
            scan_coordinates: ScanCoordinates with regions/FOVs defined
            configurations: List of selected channel configurations
            n_z: Number of z-planes
            n_t: Number of timepoints

        Returns:
            AcquisitionEstimate with image count, disk, and RAM estimates
        """
        image_count = self.calculate_image_count(
            scan_coordinates=scan_coordinates,
            n_configurations=len(configurations),
            n_z=n_z,
            n_t=n_t,
        )

        disk_bytes = self._estimate_disk_space(
            image_count=image_count,
            configurations=configurations,
        )

        mosaic_ram = self._estimate_mosaic_ram(
            scan_coordinates=scan_coordinates,
            n_configurations=len(configurations),
        )

        # Rough duration estimate
        total_fovs = sum(
            len(coords) for coords in scan_coordinates.region_fov_coordinates.values()
        )
        duration_seconds = total_fovs * n_z * n_t * self.DEFAULT_SECONDS_PER_FOV

        return AcquisitionEstimate(
            image_count=image_count,
            disk_bytes=disk_bytes,
            mosaic_ram_bytes=mosaic_ram,
            duration_seconds=duration_seconds,
        )

    def calculate_image_count(
        self,
        scan_coordinates: "ScanCoordinates",
        n_configurations: int,
        n_z: int = 1,
        n_t: int = 1,
    ) -> int:
        """
        Calculate total number of images for an acquisition.

        Args:
            scan_coordinates: ScanCoordinates with regions/FOVs defined
            n_configurations: Number of channel configurations
            n_z: Number of z-planes
            n_t: Number of timepoints

        Returns:
            Total image count
        """
        # Count FOVs across all regions
        total_fovs = sum(
            len(coords)
            for coords in scan_coordinates.region_fov_coordinates.values()
        )

        # Non-merged images: one per FOV × z × channel × timepoint
        non_merged = n_t * n_z * total_fovs * n_configurations

        # Merged images: one per FOV × z × timepoint (if enabled)
        merged = n_t * n_z * total_fovs if self._merge_channels else 0

        return non_merged + merged

    def validate(
        self,
        configurations: List[Any],
        scan_coordinates: "ScanCoordinates",
        *,
        available_disk_bytes: Optional[int] = None,
        available_ram_bytes: Optional[int] = None,
        n_z: int = 1,
        n_t: int = 1,
    ) -> ValidationResult:
        """
        Validate acquisition settings.

        Checks:
        - At least one configuration selected
        - At least one region/FOV defined
        - Sufficient disk space (if provided)
        - Sufficient RAM for mosaic (if provided)

        Args:
            configurations: Selected channel configurations
            scan_coordinates: ScanCoordinates with regions/FOVs
            available_disk_bytes: Available disk space (optional check)
            available_ram_bytes: Available RAM (optional check)
            n_z: Number of z-planes
            n_t: Number of timepoints

        Returns:
            ValidationResult with errors and warnings
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Check configurations
        if not configurations:
            errors.append("No channel configurations selected")

        # Check scan coordinates
        if not scan_coordinates.has_regions():
            errors.append("No scan regions defined")
        else:
            total_fovs = sum(
                len(coords)
                for coords in scan_coordinates.region_fov_coordinates.values()
            )
            if total_fovs == 0:
                errors.append("No FOVs defined in scan regions")

        # Get estimates for resource checks
        if configurations and scan_coordinates.has_regions():
            estimate = self.estimate(
                scan_coordinates=scan_coordinates,
                configurations=configurations,
                n_z=n_z,
                n_t=n_t,
            )

            # Check disk space
            if available_disk_bytes is not None:
                if estimate.disk_bytes > available_disk_bytes:
                    errors.append(
                        f"Insufficient disk space: need {estimate.disk_gb:.1f} GB, "
                        f"have {available_disk_bytes / (1024**3):.1f} GB"
                    )
                elif estimate.disk_bytes > available_disk_bytes * 0.9:
                    warnings.append(
                        f"Low disk space warning: need {estimate.disk_gb:.1f} GB, "
                        f"have {available_disk_bytes / (1024**3):.1f} GB"
                    )

            # Check RAM for mosaic
            if available_ram_bytes is not None and self._mosaic_enabled:
                if estimate.mosaic_ram_bytes > available_ram_bytes * 0.5:
                    warnings.append(
                        f"Mosaic view may consume significant RAM: "
                        f"{estimate.mosaic_ram_mb:.0f} MB"
                    )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def validate_channels_exist(
        self,
        channel_names: List[str],
        objective: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate that channel names exist in the configuration manager.

        Used by orchestrator to pre-validate protocol channel references.

        Args:
            channel_names: List of channel names to validate
            objective: Objective to check configurations for (uses current if None)

        Returns:
            ValidationResult with missing channel errors
        """
        errors: List[str] = []
        warnings: List[str] = []

        obj = objective or self._objective_store.current_objective
        available = self._channel_config_manager.get_configurations(obj)
        available_names = {c.name for c in available}

        for name in channel_names:
            if name not in available_names:
                errors.append(f"Unknown channel: '{name}'")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def get_available_channel_names(self, objective: Optional[str] = None) -> Set[str]:
        """Get names of all available channels for the given objective.

        Args:
            objective: Objective to get channels for (uses current if None)

        Returns:
            Set of available channel names
        """
        obj = objective or self._objective_store.current_objective
        available = self._channel_config_manager.get_configurations(obj)
        return {c.name for c in available}

    def _estimate_disk_space(
        self,
        image_count: int,
        configurations: List[Any],
    ) -> int:
        """Estimate disk space in bytes."""
        if image_count == 0 or not configurations:
            return 0

        # Try to get actual image size from camera
        try:
            size_per_image = self._measure_image_size(configurations[0])
        except Exception:
            _log.debug("Using fallback image size estimate")
            size_per_image = self._estimate_image_size()

        # Add overhead for non-image files (coordinates, configs, etc.)
        overhead = 100 * 1024  # 100 KB

        return size_per_image * image_count + overhead

    def _measure_image_size(self, config: Any) -> int:
        """Measure actual image size by saving a test image."""
        # Capture a test frame
        was_streaming = self._camera_service.get_is_streaming()
        callbacks_enabled = self._camera_service.get_callbacks_enabled()

        self._camera_service.enable_callbacks(False)
        if not was_streaming:
            self._camera_service.start_streaming()

        try:
            self._camera_service.send_trigger()
            frame = self._camera_service.read_camera_frame()
            if frame is None:
                raise RuntimeError("No frame captured")

            test_image = frame.frame
            is_color = frame.is_color()

        finally:
            self._camera_service.enable_callbacks(callbacks_enabled)
            if not was_streaming:
                self._camera_service.stop_streaming()

        # Save to temp file to measure actual size
        with tempfile.TemporaryDirectory() as temp_dir:
            size_before = utils.get_directory_disk_usage(Path(temp_dir))
            utils_acquisition.save_image(
                test_image, "test", temp_dir, config, is_color
            )
            size_after = utils.get_directory_disk_usage(Path(temp_dir))
            return size_after - size_before

    def _estimate_image_size(self) -> int:
        """Fallback image size estimate based on camera resolution."""
        width, height = self._camera_service.get_crop_size()
        if width is None or height is None:
            width, height = self._camera_service.get_resolution()
        if width is None or height is None:
            width, height = 2048, 2048  # Fallback

        # Assume 16-bit mono, compressed ~50%
        bytes_per_pixel = 2
        compression_factor = 0.5

        return int(width * height * bytes_per_pixel * compression_factor)

    def _estimate_mosaic_ram(
        self,
        scan_coordinates: "ScanCoordinates",
        n_configurations: int,
    ) -> int:
        """Estimate RAM for mosaic view in bytes."""
        if not self._mosaic_enabled or not scan_coordinates.has_regions():
            return 0

        bounds = scan_coordinates.get_scan_bounds()
        if not bounds:
            return 0

        # Calculate scan extents in mm
        width_mm = bounds["x"][1] - bounds["x"][0]
        height_mm = bounds["y"][1] - bounds["y"][0]

        # Get effective pixel size with downsampling
        pixel_size_um = (
            self._objective_store.get_pixel_size_factor()
            * self._camera_service.get_pixel_size_binned_um()
        )
        downsample_factor = max(1, int(self.MOSAIC_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        viewer_pixel_size_mm = (pixel_size_um * downsample_factor) / 1000

        # Calculate mosaic dimensions
        mosaic_width = int(math.ceil(width_mm / viewer_pixel_size_mm))
        mosaic_height = int(math.ceil(height_mm / viewer_pixel_size_mm))

        # Assume 16-bit per pixel, per channel
        bytes_per_pixel = 2

        # Check if camera is color
        try:
            pixel_format = self._camera_service.get_pixel_format()
            if pixel_format is not None and squid.core.abc.CameraPixelFormat.is_color_format(pixel_format):
                bytes_per_pixel *= 3
        except Exception:
            pass  # Assume mono

        return mosaic_width * mosaic_height * bytes_per_pixel * n_configurations
