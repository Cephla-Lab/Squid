"""
Image capture context and executor for multipoint acquisitions.

This module provides:
- CaptureContext: Dataclass holding capture parameters
- build_capture_info: Factory function for building CaptureInfo
- ImageCaptureExecutor: Class for executing image captures

These provide a clean parameter passing pattern for image capture.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Union, TYPE_CHECKING
import time

import squid.core.abc
import squid.core.logging

if TYPE_CHECKING:
    from squid.core.utils.config_utils import ChannelMode
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo
    from squid.backend.services import CameraService, IlluminationService, FilterWheelService

_log = squid.core.logging.get_logger(__name__)


@dataclass
class CaptureContext:
    """
    Context for a single image capture.

    Holds all parameters needed for one capture operation, making it easy
    to pass capture context between methods.

    Note: region_id accepts both str and int for compatibility with existing
    code patterns. The str form (e.g. "region_0") is used in scan_region_fov_coords_mm
    while the int form is used in CaptureInfo.
    """

    config: "ChannelMode"
    file_id: str
    save_directory: str
    z_index: int
    region_id: Union[str, int]
    fov: int
    config_idx: int
    time_point: int
    z_piezo_um: Optional[float] = None
    pixel_size_um: Optional[float] = None
    fov_id: Optional[str] = None  # Stable FOV identifier (e.g., "A1_0001")


def build_capture_info(
    context: CaptureContext,
    position: squid.core.abc.Pos,
    capture_time: Optional[float] = None,
) -> "CaptureInfo":
    """
    Build a CaptureInfo from a CaptureContext and position.

    This factory function consolidates CaptureInfo construction that was
    previously duplicated across acquire_camera_image and acquire_rgb_image.

    Args:
        context: Capture context with configuration and identifiers
        position: Current stage position
        capture_time: Capture timestamp (defaults to current time)

    Returns:
        CaptureInfo ready for job processing
    """
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo

    # Note: CaptureInfo.region_id is typed as int but actually accepts strings
    # in the existing codebase. Using type: ignore for compatibility.
    return CaptureInfo(
        position=position,
        z_index=context.z_index,
        capture_time=capture_time if capture_time is not None else time.time(),
        z_piezo_um=context.z_piezo_um,
        configuration=context.config,
        save_directory=context.save_directory,
        file_id=context.file_id,
        region_id=context.region_id,  # type: ignore[arg-type]
        fov=context.fov,
        configuration_idx=context.config_idx,
        time_point=context.time_point,
        pixel_size_um=context.pixel_size_um,
        fov_id=context.fov_id,
    )


class ImageCaptureExecutor:
    """
    Executes image captures for multipoint and orchestrator acquisitions.

    Wraps camera triggering, illumination control, and frame capture
    into a clean interface. Provides both single-image and batch capture
    methods.

    Usage:
        executor = ImageCaptureExecutor(
            camera_service=camera_service,
            illumination_service=illumination_service,
            filter_wheel_service=filter_wheel_service,
        )

        # Single image capture
        frame = executor.capture_single_image(
            configuration=channel_config,
        )

        # Capture with context for saving
        info = executor.capture_with_context(
            context=capture_context,
            position=stage_position,
        )
    """

    # Default timeout waiting for frame (seconds)
    DEFAULT_FRAME_TIMEOUT_S = 10.0

    def __init__(
        self,
        camera_service: "CameraService",
        illumination_service: Optional["IlluminationService"] = None,
        filter_wheel_service: Optional["FilterWheelService"] = None,
        *,
        enable_auto_filter_switching: bool = True,
    ):
        """
        Initialize the image capture executor.

        Args:
            camera_service: CameraService for camera operations
            illumination_service: IlluminationService for illumination control
            filter_wheel_service: FilterWheelService for filter control
            enable_auto_filter_switching: Whether to auto-switch filters per config
        """
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._auto_filter = enable_auto_filter_switching

    def apply_configuration(self, config: "ChannelMode") -> None:
        """
        Apply a channel configuration (illumination, filters).

        Args:
            config: Channel configuration to apply
        """
        # Apply illumination
        if self._illumination is not None:
            try:
                # Get illumination settings from config
                intensity = getattr(config, "illumination_intensity", None)
                channel = getattr(config, "illumination_source", None)
                if intensity is not None and channel is not None:
                    self._illumination.set_channel_power(channel, intensity)
                    self._illumination.turn_on_channel(channel)
            except Exception as e:
                _log.warning(f"Failed to apply illumination: {e}")

        # Apply filter wheel position
        if self._auto_filter and self._filter_wheel is not None:
            try:
                filter_pos = getattr(config, "emission_filter_position", None)
                if filter_pos is not None:
                    self._filter_wheel.set_position(filter_pos)
            except Exception as e:
                _log.warning(f"Failed to set filter position: {e}")

        # Set camera exposure and gain from config
        try:
            exposure_ms = getattr(config, "exposure_time_ms", None)
            if exposure_ms is not None:
                self._camera.set_exposure_time(exposure_ms)

            gain = getattr(config, "analog_gain", None)
            if gain is not None:
                self._camera.set_analog_gain(gain)
        except Exception as e:
            _log.warning(f"Failed to set camera parameters: {e}")

    def capture_single_image(
        self,
        configuration: Optional["ChannelMode"] = None,
    ) -> Optional[squid.core.abc.CameraFrame]:
        """
        Capture a single image.

        Args:
            configuration: Optional channel configuration to apply first

        Returns:
            CameraFrame if successful, None otherwise
        """
        if configuration is not None:
            self.apply_configuration(configuration)

        # Send trigger and wait for frame
        try:
            self._camera.send_trigger()
            frame = self._camera.read_camera_frame()
            return frame
        except Exception as e:
            _log.error(f"Failed to capture image: {e}")
            return None

    def capture_with_context(
        self,
        context: CaptureContext,
        position: squid.core.abc.Pos,
    ) -> Optional["CaptureInfo"]:
        """
        Capture an image and build CaptureInfo for job processing.

        Args:
            context: Capture context with file IDs and config
            position: Current stage position

        Returns:
            CaptureInfo if successful, None otherwise
        """
        # Apply configuration
        self.apply_configuration(context.config)

        # Capture frame
        capture_time = time.time()
        frame = self.capture_single_image(
            configuration=None,  # Already applied above
        )

        if frame is None:
            return None

        # Build CaptureInfo
        return build_capture_info(context, position, capture_time)

    def turn_off_illumination(self, channel: Optional[int] = None) -> None:
        """Turn off illumination channel(s).

        Args:
            channel: Specific channel to turn off, or None for last used channel
        """
        if self._illumination is not None:
            try:
                if channel is not None:
                    self._illumination.turn_off_channel(channel)
            except Exception as e:
                _log.warning(f"Failed to turn off illumination: {e}")

    def capture_z_stack(
        self,
        configuration: "ChannelMode",
        z_positions_mm: List[float],
        move_z_callback: Callable[[float], None],
    ) -> List[Optional[squid.core.abc.CameraFrame]]:
        """
        Capture a z-stack at the current XY position.

        Args:
            configuration: Channel configuration to use
            z_positions_mm: List of z positions in mm
            move_z_callback: Callback to move z stage

        Returns:
            List of CameraFrames (None entries for failed captures)
        """
        # Apply configuration once
        self.apply_configuration(configuration)

        frames: List[Optional[squid.core.abc.CameraFrame]] = []

        for z_mm in z_positions_mm:
            # Move to z position
            move_z_callback(z_mm)

            # Capture frame
            frame = self.capture_single_image(
                configuration=None,  # Already applied
            )
            frames.append(frame)

        return frames
