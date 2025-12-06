# squid/services/camera_service.py
"""Service for camera operations."""
from typing import Optional, Sequence, Tuple, TYPE_CHECKING, Callable

from squid.services.base import BaseService
from squid.config import CameraPixelFormat
from squid.events import (
    EventBus,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    ExposureTimeChanged,
    AnalogGainChanged,
    ROIChanged,
    BinningChanged,
    PixelFormatChanged,
)

if TYPE_CHECKING:
    from squid.abc import AbstractCamera, CameraGainRange, CameraAcquisitionMode


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

    def set_exposure_time(self, exposure_time_ms: float) -> None:
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

    def set_analog_gain(self, gain: float) -> None:
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

    # ============================================================
    # Task 1.1: ROI methods
    # ============================================================

    def set_region_of_interest(self, x_offset: int, y_offset: int, width: int, height: int) -> None:
        """Set camera region of interest."""
        self._log.debug(f"Setting ROI: offset=({x_offset}, {y_offset}), size=({width}, {height})")
        self._camera.set_region_of_interest(x_offset, y_offset, width, height)
        self.publish(ROIChanged(x_offset=x_offset, y_offset=y_offset, width=width, height=height))

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        """Get current ROI as (x_offset, y_offset, width, height)."""
        return self._camera.get_region_of_interest()

    def get_resolution(self) -> Tuple[int, int]:
        """Get camera resolution as (width, height)."""
        return self._camera.get_resolution()

    # ============================================================
    # Task 1.2: Binning methods
    # ============================================================

    def set_binning(self, binning_x: int, binning_y: int) -> None:
        """Set camera binning."""
        self._log.debug(f"Setting binning: {binning_x}x{binning_y}")
        self._camera.set_binning(binning_x, binning_y)
        self.publish(BinningChanged(binning_x=binning_x, binning_y=binning_y))

    def get_binning(self) -> Tuple[int, int]:
        """Get current binning as (x, y)."""
        return self._camera.get_binning()

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        """Get available binning options."""
        return self._camera.get_binning_options()

    # ============================================================
    # Task 1.3: Pixel format methods
    # ============================================================

    def set_pixel_format(self, pixel_format: CameraPixelFormat) -> None:
        """Set camera pixel format."""
        self._log.debug(f"Setting pixel format: {pixel_format}")
        self._camera.set_pixel_format(pixel_format)
        self.publish(PixelFormatChanged(pixel_format=pixel_format))

    def get_pixel_format(self) -> Optional[CameraPixelFormat]:
        """Get current pixel format."""
        return self._camera.get_pixel_format()

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        """Get available pixel formats."""
        return self._camera.get_available_pixel_formats()

    # ============================================================
    # Task 1.4: Temperature methods
    # ============================================================

    def set_temperature(self, temperature: float) -> None:
        """Set camera target temperature."""
        self._log.debug(f"Setting temperature: {temperature}°C")
        self._camera.set_temperature(temperature)

    def set_temperature_reading_callback(self, callback: Callable) -> None:
        """Set callback for temperature readings."""
        self._camera.set_temperature_reading_callback(callback)

    # ============================================================
    # Task 1.5: White balance methods
    # ============================================================

    def set_white_balance_gains(self, r: float, g: float, b: float) -> None:
        """Set white balance gains."""
        self._camera.set_white_balance_gains(r, g, b)

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        """Get white balance gains as (r, g, b)."""
        return self._camera.get_white_balance_gains()

    def set_auto_white_balance(self, enabled: bool) -> None:
        """Enable/disable auto white balance."""
        self._camera.set_auto_white_balance_gains(on=enabled)

    # ============================================================
    # Task 1.6: Black level method
    # ============================================================

    def set_black_level(self, level: float) -> None:
        """Set camera black level."""
        self._log.debug(f"Setting black level: {level}")
        self._camera.set_black_level(level)

    # ============================================================
    # Read-only camera properties
    # ============================================================

    def get_gain_range(self) -> "CameraGainRange":
        """Get camera gain range."""
        return self._camera.get_gain_range()

    def get_acquisition_mode(self) -> "CameraAcquisitionMode":
        """Get current acquisition mode."""
        return self._camera.get_acquisition_mode()

    def get_pixel_size_binned_um(self) -> float:
        """Get pixel size after binning in microns."""
        return self._camera.get_pixel_size_binned_um()
