"""
AcquisitionService - Hardware orchestration primitives for acquisition.

This service consolidates duplicated illumination and configuration logic
from LiveController and MultiPointWorker into a single source of truth.

Used by:
- LiveController: For live view channel switching and illumination
- MultiPointWorker: For acquisition configuration and triggering
- (Future) ImagingExecutor: For orchestrated experiments
"""

from contextlib import contextmanager
from typing import Optional, TYPE_CHECKING

import squid.core.logging

from _def import TriggerMode

if TYPE_CHECKING:
    from squid.backend.services import (
        CameraService,
        IlluminationService,
        FilterWheelService,
        PeripheralService,
    )
    from squid.core.config.models import AcquisitionChannel


_log = squid.core.logging.get_logger(__name__)


class AcquisitionService:
    """
    Hardware orchestration primitives for acquisition.

    Provides a unified interface for:
    - Applying channel configurations (exposure, gain, illumination, filter)
    - Controlling illumination on/off
    - Managing illumination during software trigger captures

    Thread Safety:
        This service delegates to underlying services which handle their own
        locking. The service itself does not hold locks across operations.
    """

    def __init__(
        self,
        camera_service: "CameraService",
        peripheral_service: "PeripheralService",
        illumination_service: Optional["IlluminationService"] = None,
        filter_wheel_service: Optional["FilterWheelService"] = None,
    ):
        """
        Initialize the acquisition service.

        Args:
            camera_service: Camera service for exposure/gain control
            peripheral_service: Peripheral service for operation completion
            illumination_service: Optional illumination service for LED/laser control
            filter_wheel_service: Optional filter wheel service for emission filters
        """
        self._camera = camera_service
        self._peripheral = peripheral_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service

    def apply_configuration(
        self,
        config: "AcquisitionChannel",
        trigger_mode: str,
        enable_filter_switching: bool = True,
    ) -> None:
        """
        Apply a channel configuration to hardware.

        Sets camera exposure and gain, illumination power, and filter position.
        Does NOT turn on illumination - use turn_on_illumination() for that.

        Args:
            config: Channel configuration to apply
            trigger_mode: Current trigger mode (affects filter wheel delay)
            enable_filter_switching: Whether to switch emission filter

        Note:
            This consolidates logic from:
            - LiveController.set_microscope_mode() (exposure/gain)
            - LiveController.update_illumination() (power + filter)
            - MultiPointWorker._apply_channel_mode()
        """
        # Set camera exposure time
        exposure = getattr(config, "exposure_time", None)
        if exposure is not None:
            try:
                self._camera.set_exposure_time(exposure)
            except Exception:
                _log.exception("Failed to set exposure time")

        # Set camera analog gain
        gain = getattr(config, "analog_gain", None)
        if gain is not None:
            try:
                self._camera.set_analog_gain(gain)
            except Exception:
                _log.debug("Failed to set analog gain (may not be supported)")

        # Set illumination power (but don't turn on)
        self._set_illumination_power(config)

        # Set filter wheel position
        if enable_filter_switching:
            self._set_filter_position(config, trigger_mode)

    def turn_on_illumination(self, config: "AcquisitionChannel") -> bool:
        """
        Turn on illumination for the given configuration.

        Args:
            config: Channel configuration with illumination settings

        Returns:
            True if illumination was turned on, False otherwise

        Note:
            This consolidates logic from:
            - LiveController.turn_on_illumination()
            - MultiPointWorker._turn_on_illumination()
        """
        if self._illumination is None:
            return False

        source = getattr(config, "illumination_source", None)
        if source is None:
            _log.debug("Cannot turn on illumination: config missing illumination_source")
            return False

        try:
            channel = int(source)
            # Set power before turning on (in case it changed)
            intensity = getattr(config, "illumination_intensity", None)
            if intensity is not None:
                self._illumination.set_channel_power(channel, float(intensity))
            self._illumination.turn_on_channel(channel)
            return True
        except Exception:
            _log.exception("Failed to turn on illumination")
            return False

    def turn_off_illumination(self, config: "AcquisitionChannel") -> bool:
        """
        Turn off illumination for the given configuration.

        Args:
            config: Channel configuration with illumination settings

        Returns:
            True if illumination was turned off, False otherwise

        Note:
            This consolidates logic from:
            - LiveController.turn_off_illumination()
            - MultiPointWorker._turn_off_illumination()
        """
        if self._illumination is None:
            return False

        source = getattr(config, "illumination_source", None)
        if source is None:
            return False

        try:
            self._illumination.turn_off_channel(int(source))
            return True
        except Exception:
            _log.exception("Failed to turn off illumination")
            return False

    @contextmanager
    def illumination_context(self, config: "AcquisitionChannel", trigger_mode: str):
        """
        Context manager for illumination during software trigger capture.

        For software trigger mode, turns illumination on before yield and off after.
        For hardware trigger mode, illumination is controlled by strobe signal.

        Args:
            config: Channel configuration
            trigger_mode: Current trigger mode

        Usage:
            with acquisition_service.illumination_context(config, trigger_mode):
                camera_service.send_trigger()
                # wait for frame

        Note:
            This pattern is from MultiPointWorker.acquire_camera_image() lines 1370-1467.
        """
        if trigger_mode == TriggerMode.SOFTWARE:
            self.turn_on_illumination(config)
            try:
                yield
            finally:
                self.turn_off_illumination(config)
        else:
            # Hardware trigger - illumination controlled by strobe
            yield

    def wait_for_ready(self, timeout_s: float = 5.0) -> bool:
        """
        Wait for camera to be ready for next trigger.

        Args:
            timeout_s: Maximum time to wait in seconds

        Returns:
            True if camera is ready, False if timeout
        """
        import time

        start = time.time()
        while time.time() - start < timeout_s:
            if self._camera.get_ready_for_trigger():
                return True
            time.sleep(0.001)  # 1ms poll interval
        return False

    def get_strobe_time(self) -> float:
        """
        Get camera strobe time for filter wheel delay calculation.

        Returns:
            Strobe time in milliseconds, or 0 if not available
        """
        try:
            return self._camera.get_strobe_time()
        except Exception:
            return 0.0

    # === Private Helper Methods ===

    def _set_illumination_power(self, config: "AcquisitionChannel") -> None:
        """Set illumination power without turning on the channel."""
        if self._illumination is None:
            return

        source = getattr(config, "illumination_source", None)
        intensity = getattr(config, "illumination_intensity", None)

        if source is None or intensity is None:
            return

        try:
            self._illumination.set_channel_power(int(source), float(intensity))
        except Exception:
            _log.debug("Failed to set illumination power")

    def _set_filter_position(self, config: "AcquisitionChannel", trigger_mode: str) -> None:
        """Set emission filter position with appropriate delay offset."""
        if self._filter_wheel is None:
            return

        if not self._filter_wheel.is_available():
            return

        position = getattr(config, "emission_filter_position", None)
        if position is None:
            return

        try:
            # Set delay offset based on trigger mode
            delay = 0
            if trigger_mode == TriggerMode.HARDWARE:
                delay = -int(self.get_strobe_time())
            self._filter_wheel.set_delay_offset_ms(delay)
        except Exception:
            _log.debug("Failed to set filter wheel delay")

        try:
            self._filter_wheel.set_filter_wheel_position({1: int(position)})
        except Exception:
            _log.debug("Failed to set filter wheel position")

    # === Convenience Methods for Direct Service Access ===

    @property
    def camera(self) -> "CameraService":
        """Direct access to camera service for operations not covered by this service."""
        return self._camera

    @property
    def has_illumination(self) -> bool:
        """Check if illumination service is available."""
        return self._illumination is not None

    @property
    def has_filter_wheel(self) -> bool:
        """Check if filter wheel service is available."""
        return self._filter_wheel is not None and self._filter_wheel.is_available()
