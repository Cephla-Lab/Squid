"""
Image capture execution for multipoint acquisitions.

This module provides:
- CaptureContext: Dataclass holding capture parameters
- build_capture_info: Factory function for building CaptureInfo
- ImageCaptureExecutor: Orchestrates single image capture with illumination

These classes work with AcquisitionService to provide a clean interface
for image capture during multipoint acquisitions.
"""

from dataclasses import dataclass
from typing import Optional, Union, TYPE_CHECKING
import time

import squid.core.abc
import squid.core.logging
from _def import TriggerMode

if TYPE_CHECKING:
    from squid.backend.services import CameraService, AcquisitionService
    from squid.core.utils.config_utils import ChannelMode
    from squid.backend.controllers.multipoint.job_processing import CaptureInfo


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
    )


class ImageCaptureExecutor:
    """
    Executes single image captures with proper illumination control.

    This class coordinates camera triggering with illumination, using
    AcquisitionService for hardware control. It handles:
    - Configuration application (exposure, gain, illumination)
    - Trigger mode-aware illumination control
    - Camera triggering and frame waiting

    The actual frame callback processing remains in MultiPointWorker since
    it requires tight integration with the worker's threading model.

    Usage:
        executor = ImageCaptureExecutor(
            camera_service=camera,
            acquisition_service=acquisition,
            trigger_mode=TriggerMode.HARDWARE,
        )

        # Apply configuration
        executor.apply_config(config)

        # Execute capture (illumination handled automatically)
        executor.execute_capture(config, wait_for_frame=True)
    """

    def __init__(
        self,
        camera_service: "CameraService",
        acquisition_service: Optional["AcquisitionService"] = None,
        trigger_mode: str = TriggerMode.SOFTWARE,
        frame_timeout_multiplier: float = 5.0,
        frame_timeout_base_s: float = 2.0,
    ):
        """
        Initialize the image capture executor.

        Args:
            camera_service: Camera service for exposure/trigger control
            acquisition_service: Optional acquisition service for illumination
            trigger_mode: Trigger mode (SOFTWARE or HARDWARE)
            frame_timeout_multiplier: Multiplier for frame wait timeout
            frame_timeout_base_s: Base timeout for frame wait in seconds
        """
        self._camera = camera_service
        self._acquisition = acquisition_service
        self._trigger_mode = trigger_mode
        self._timeout_multiplier = frame_timeout_multiplier
        self._timeout_base_s = frame_timeout_base_s

    @property
    def trigger_mode(self) -> str:
        """Get current trigger mode."""
        return self._trigger_mode

    @trigger_mode.setter
    def trigger_mode(self, mode: str) -> None:
        """Set trigger mode."""
        self._trigger_mode = mode

    def apply_config(self, config: "ChannelMode") -> None:
        """
        Apply channel configuration to camera and illumination.

        Args:
            config: Channel configuration to apply
        """
        if self._acquisition is not None:
            self._acquisition.apply_configuration(
                config,
                self._trigger_mode,
                enable_filter_switching=True,
            )
        else:
            # Fallback: set exposure/gain directly
            exposure = getattr(config, "exposure_time", None)
            if exposure is not None:
                self._camera.set_exposure_time(exposure)

            gain = getattr(config, "analog_gain", None)
            if gain is not None:
                try:
                    self._camera.set_analog_gain(gain)
                except Exception:
                    _log.debug("Failed to set analog gain")

    def get_illumination_time(self) -> Optional[float]:
        """
        Get camera illumination time for hardware trigger mode.

        Returns:
            Illumination time in ms for hardware trigger, None for software
        """
        if self._trigger_mode == TriggerMode.HARDWARE:
            return self._camera.get_exposure_time()
        return None

    def turn_on_illumination(self, config: "ChannelMode") -> None:
        """
        Turn on illumination for software trigger mode.

        Args:
            config: Channel configuration with illumination settings
        """
        if self._acquisition is not None:
            self._acquisition.turn_on_illumination(config)

    def turn_off_illumination(self, config: "ChannelMode") -> None:
        """
        Turn off illumination after software trigger capture.

        Args:
            config: Channel configuration with illumination settings
        """
        if self._acquisition is not None:
            self._acquisition.turn_off_illumination(config)

    def send_trigger(self, illumination_time: Optional[float] = None) -> None:
        """
        Send camera trigger.

        Args:
            illumination_time: Optional illumination time for hardware trigger
        """
        # The camera service handles illumination time in its send_trigger
        if illumination_time is not None:
            self._camera.send_trigger(illumination_time=illumination_time)
        else:
            self._camera.send_trigger()

    def get_frame_wait_timeout(self) -> float:
        """
        Calculate timeout for waiting for frame.

        Returns:
            Timeout in seconds based on exposure time
        """
        total_frame_time_ms = self._camera.get_total_frame_time()
        return self._timeout_multiplier * total_frame_time_ms / 1000.0 + self._timeout_base_s

    def wait_for_exposure(self) -> None:
        """
        Wait for exposure to complete in hardware trigger mode.

        This ensures we don't move the stage before exposure is done.
        """
        if self._trigger_mode == TriggerMode.HARDWARE:
            total_frame_time_ms = self._camera.get_total_frame_time()
            exposure_time_s = total_frame_time_ms / 1000.0
            time.sleep(max(0.0, exposure_time_s))

    def execute_software_capture(
        self,
        config: "ChannelMode",
        send_trigger: bool = True,
    ) -> None:
        """
        Execute a software trigger capture with illumination control.

        Turns illumination on before trigger and off after exposure.

        Args:
            config: Channel configuration
            send_trigger: Whether to send camera trigger
        """
        self.turn_on_illumination(config)

        if send_trigger:
            self.send_trigger(illumination_time=None)

    def execute_hardware_capture(
        self,
        config: "ChannelMode",  # noqa: ARG002 - kept for API consistency
        send_trigger: bool = True,
        wait_for_exposure: bool = True,
    ) -> None:
        """
        Execute a hardware trigger capture.

        Illumination is controlled by strobe signal in hardware mode.
        The config parameter is unused here but kept for API consistency
        with execute_software_capture.

        Args:
            config: Channel configuration (unused, for API consistency)
            send_trigger: Whether to send camera trigger
            wait_for_exposure: Whether to wait for exposure to complete
        """
        # Note: config is not used because hardware mode controls illumination via strobe
        _ = config  # Explicitly mark as intentionally unused
        illumination_time = self.get_illumination_time()

        if send_trigger:
            self.send_trigger(illumination_time=illumination_time)

        if wait_for_exposure:
            self.wait_for_exposure()

    def finalize_capture(self, config: "ChannelMode") -> None:
        """
        Finalize capture by turning off illumination if needed.

        Should be called after frame is received.

        Args:
            config: Channel configuration
        """
        if self._trigger_mode == TriggerMode.SOFTWARE:
            self.turn_off_illumination(config)


class CaptureSequenceBuilder:
    """
    Builds capture sequences for z-stacks and multi-channel acquisitions.

    Helper class for generating CaptureContext instances for a sequence
    of captures at a single FOV position.

    Usage:
        builder = CaptureSequenceBuilder(
            save_directory="/path/to/data",
            region_id="region_0",
            fov=1,
            time_point=0,
        )

        # Generate contexts for a z-stack with multiple channels
        contexts = builder.build_zstack_sequence(
            configurations=[bf_config, fluor_config],
            num_z_levels=5,
            z_piezo_um=50.0,
            pixel_size_um=0.325,
        )
    """

    def __init__(
        self,
        save_directory: str,
        region_id: str,
        fov: int,
        time_point: int,
    ):
        """
        Initialize the capture sequence builder.

        Args:
            save_directory: Directory for saving images
            region_id: Region identifier
            fov: FOV index
            time_point: Current timepoint
        """
        self._save_directory = save_directory
        self._region_id = region_id
        self._fov = fov
        self._time_point = time_point

    def build_context(
        self,
        config: "ChannelMode",
        config_idx: int,
        z_index: int,
        file_id: str,
        z_piezo_um: Optional[float] = None,
        pixel_size_um: Optional[float] = None,
    ) -> CaptureContext:
        """
        Build a single CaptureContext.

        Args:
            config: Channel configuration
            config_idx: Index in configuration list
            z_index: Z-level index
            file_id: File identifier
            z_piezo_um: Optional piezo z position
            pixel_size_um: Optional pixel size

        Returns:
            CaptureContext for this capture
        """
        return CaptureContext(
            config=config,
            file_id=file_id,
            save_directory=self._save_directory,
            z_index=z_index,
            region_id=self._region_id,
            fov=self._fov,
            config_idx=config_idx,
            time_point=self._time_point,
            z_piezo_um=z_piezo_um,
            pixel_size_um=pixel_size_um,
        )

    def generate_file_id(
        self,
        region_id: str,
        fov: int,
        time_point: int,
        z_index: int,
        config_name: str,
    ) -> str:
        """
        Generate a standard file ID for an image.

        Args:
            region_id: Region identifier
            fov: FOV index
            time_point: Timepoint index
            z_index: Z-level index
            config_name: Configuration name

        Returns:
            Formatted file ID string
        """
        return f"{region_id}_{fov}_{time_point}_{z_index}_{config_name}"
