# squid/services/camera_service.py
"""Service for camera operations."""
from typing import Tuple, TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    ExposureTimeChanged,
    AnalogGainChanged,
)

if TYPE_CHECKING:
    from squid.abc import AbstractCamera


class CameraService(BaseService):
    """
    Service layer for camera operations.

    Handles exposure, gain, binning, ROI, etc.
    Widgets should use this service instead of calling camera directly.
    """

    def __init__(self, camera: "AbstractCamera", event_bus: EventBus):
        """
        Initialize camera service.

        Args:
            camera: AbstractCamera implementation
            event_bus: EventBus for communication
        """
        super().__init__(event_bus)
        self._camera = camera

        # Subscribe to commands
        self.subscribe(SetExposureTimeCommand, self._on_set_exposure_command)
        self.subscribe(SetAnalogGainCommand, self._on_set_gain_command)

    def _on_set_exposure_command(self, event: SetExposureTimeCommand):
        """Handle SetExposureTimeCommand event."""
        self.set_exposure_time(event.exposure_time_ms)

    def _on_set_gain_command(self, event: SetAnalogGainCommand):
        """Handle SetAnalogGainCommand event."""
        self.set_analog_gain(event.gain)

    def set_exposure_time(self, exposure_time_ms: float):
        """
        Set camera exposure time.

        Args:
            exposure_time_ms: Exposure time in milliseconds
        """
        # Get limits and clamp
        limits = self._camera.get_exposure_limits()
        exposure_time_ms = max(limits[0], min(limits[1], exposure_time_ms))

        self._log.debug(f"Setting exposure time to {exposure_time_ms} ms")
        self._camera.set_exposure_time(exposure_time_ms)

        self.publish(ExposureTimeChanged(exposure_time_ms=exposure_time_ms))

    def get_exposure_time(self) -> float:
        """Get current exposure time in milliseconds."""
        return self._camera.get_exposure_time()

    def get_exposure_limits(self) -> Tuple[float, float]:
        """Get exposure time limits (min, max) in milliseconds."""
        return self._camera.get_exposure_limits()

    def set_analog_gain(self, gain: float):
        """
        Set camera analog gain.

        Args:
            gain: Analog gain value
        """
        try:
            gain_range = self._camera.get_gain_range()
            gain = max(gain_range.min_gain, min(gain_range.max_gain, gain))

            self._log.debug(f"Setting analog gain to {gain}")
            self._camera.set_analog_gain(gain)

            self.publish(AnalogGainChanged(gain=gain))
        except NotImplementedError:
            self._log.warning("Camera does not support analog gain")

    def get_analog_gain(self) -> float:
        """Get current analog gain."""
        return self._camera.get_analog_gain()
