# squid/services/camera_service.py
"""Service for camera operations."""
from __future__ import annotations
import threading
from typing import Optional, Sequence, Tuple, Callable

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
    # New camera settings commands
    SetROICommand,
    SetBinningCommand,
    SetPixelFormatCommand,
    SetCameraTemperatureCommand,
    SetBlackLevelCommand,
    SetAutoWhiteBalanceCommand,
    # New state events
    CameraTemperatureChanged,
    BlackLevelChanged,
    AutoWhiteBalanceChanged,
)

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
        self._lock = threading.RLock()

        # Subscribe to commands
        self.subscribe(SetExposureTimeCommand, self._on_set_exposure_command)
        self.subscribe(SetAnalogGainCommand, self._on_set_gain_command)
        self.subscribe(SetROICommand, self._on_set_roi_command)
        self.subscribe(SetBinningCommand, self._on_set_binning_command)
        self.subscribe(SetPixelFormatCommand, self._on_set_pixel_format_command)
        self.subscribe(SetCameraTemperatureCommand, self._on_set_temperature_command)
        self.subscribe(SetBlackLevelCommand, self._on_set_black_level_command)
        self.subscribe(SetAutoWhiteBalanceCommand, self._on_set_auto_wb_command)

    def _on_set_exposure_command(self, event: SetExposureTimeCommand):
        """Handle SetExposureTimeCommand event."""
        self.set_exposure_time(event.exposure_time_ms)

    def _on_set_gain_command(self, event: SetAnalogGainCommand):
        """Handle SetAnalogGainCommand event."""
        self.set_analog_gain(event.gain)

    def _on_set_roi_command(self, event: SetROICommand):
        """Handle SetROICommand event."""
        self.set_region_of_interest(
            event.x_offset, event.y_offset, event.width, event.height
        )

    def _on_set_binning_command(self, event: SetBinningCommand):
        """Handle SetBinningCommand event."""
        self.set_binning(event.binning_x, event.binning_y)

    def _on_set_pixel_format_command(self, event: SetPixelFormatCommand):
        """Handle SetPixelFormatCommand event."""
        pixel_format = CameraPixelFormat.from_string(event.pixel_format)
        self.set_pixel_format(pixel_format)

    def _on_set_temperature_command(self, event: SetCameraTemperatureCommand):
        """Handle SetCameraTemperatureCommand event."""
        self.set_temperature(event.temperature_celsius)

    def _on_set_black_level_command(self, event: SetBlackLevelCommand):
        """Handle SetBlackLevelCommand event."""
        self.set_black_level(event.level)

    def _on_set_auto_wb_command(self, event: SetAutoWhiteBalanceCommand):
        """Handle SetAutoWhiteBalanceCommand event."""
        self.set_auto_white_balance(event.enabled)

    def set_exposure_time(self, exposure_time_ms: float) -> None:
        """
        Set camera exposure time.

        Args:
            exposure_time_ms: Exposure time in milliseconds
        """
        with self._lock:
            # Get limits and clamp
            limits = self._camera.get_exposure_limits()
            exposure_time_ms = max(limits[0], min(limits[1], exposure_time_ms))

            self._log.debug(f"Setting exposure time to {exposure_time_ms} ms")
            self._camera.set_exposure_time(exposure_time_ms)

        self.publish(ExposureTimeChanged(exposure_time_ms=exposure_time_ms))

    def get_exposure_time(self) -> float:
        """Get current exposure time in milliseconds."""
        with self._lock:
            return self._camera.get_exposure_time()

    def get_exposure_limits(self) -> Tuple[float, float]:
        """Get exposure time limits (min, max) in milliseconds."""
        with self._lock:
            return self._camera.get_exposure_limits()

    def set_analog_gain(self, gain: float) -> None:
        """
        Set camera analog gain.

        Args:
            gain: Analog gain value
        """
        try:
            with self._lock:
                gain_range = self._camera.get_gain_range()
                gain = max(gain_range.min_gain, min(gain_range.max_gain, gain))

                self._log.debug(f"Setting analog gain to {gain}")
                self._camera.set_analog_gain(gain)

            self.publish(AnalogGainChanged(gain=gain))
        except NotImplementedError:
            self._log.warning("Camera does not support analog gain")

    def get_analog_gain(self) -> float:
        """Get current analog gain."""
        with self._lock:
            return self._camera.get_analog_gain()

    # ============================================================
    # Task 1.1: ROI methods
    # ============================================================

    def set_region_of_interest(
        self, x_offset: int, y_offset: int, width: int, height: int
    ) -> None:
        """Set camera region of interest."""
        self._log.debug(
            f"Setting ROI: offset=({x_offset}, {y_offset}), size=({width}, {height})"
        )
        with self._lock:
            self._camera.set_region_of_interest(x_offset, y_offset, width, height)
        self.publish(
            ROIChanged(x_offset=x_offset, y_offset=y_offset, width=width, height=height)
        )

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        """Get current ROI as (x_offset, y_offset, width, height)."""
        with self._lock:
            return self._camera.get_region_of_interest()

    def get_resolution(self) -> Tuple[int, int]:
        """Get camera resolution as (width, height)."""
        with self._lock:
            return self._camera.get_resolution()

    # ============================================================
    # Task 1.2: Binning methods
    # ============================================================

    def set_binning(self, binning_x: int, binning_y: int) -> None:
        """Set camera binning."""
        self._log.debug(f"Setting binning: {binning_x}x{binning_y}")
        with self._lock:
            self._camera.set_binning(binning_x, binning_y)
        self.publish(BinningChanged(binning_x=binning_x, binning_y=binning_y))

    def get_binning(self) -> Tuple[int, int]:
        """Get current binning as (x, y)."""
        with self._lock:
            return self._camera.get_binning()

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        """Get available binning options."""
        with self._lock:
            return self._camera.get_binning_options()

    # ============================================================
    # Task 1.3: Pixel format methods
    # ============================================================

    def set_pixel_format(self, pixel_format: CameraPixelFormat) -> None:
        """Set camera pixel format."""
        self._log.debug(f"Setting pixel format: {pixel_format}")
        with self._lock:
            self._camera.set_pixel_format(pixel_format)
        self.publish(PixelFormatChanged(pixel_format=pixel_format))

    def get_pixel_format(self) -> Optional[CameraPixelFormat]:
        """Get current pixel format."""
        with self._lock:
            return self._camera.get_pixel_format()

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        """Get available pixel formats."""
        with self._lock:
            return self._camera.get_available_pixel_formats()

    # ============================================================
    # Task 1.4: Temperature methods
    # ============================================================

    def set_temperature(self, temperature: float) -> None:
        """Set camera target temperature."""
        self._log.debug(f"Setting temperature: {temperature}°C")
        with self._lock:
            self._camera.set_temperature(temperature)
        self.publish(CameraTemperatureChanged(set_temperature_celsius=temperature))

    def set_temperature_reading_callback(self, callback: Callable) -> None:
        """Set callback for temperature readings."""
        with self._lock:
            self._camera.set_temperature_reading_callback(callback)

    # ============================================================
    # Task 1.5: White balance methods
    # ============================================================

    def set_white_balance_gains(self, r: float, g: float, b: float) -> None:
        """Set white balance gains."""
        with self._lock:
            self._camera.set_white_balance_gains(r, g, b)

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        """Get white balance gains as (r, g, b)."""
        with self._lock:
            return self._camera.get_white_balance_gains()

    def set_auto_white_balance(self, enabled: bool) -> None:
        """Enable/disable auto white balance."""
        with self._lock:
            self._camera.set_auto_white_balance_gains(on=enabled)
        self.publish(AutoWhiteBalanceChanged(enabled=enabled))

    # ============================================================
    # Task 1.6: Black level method
    # ============================================================

    def set_black_level(self, level: int) -> None:
        """Set camera black level."""
        self._log.debug(f"Setting black level: {level}")
        with self._lock:
            self._camera.set_black_level(level)
        self.publish(BlackLevelChanged(level=level))

    # ============================================================
    # Read-only camera properties
    # ============================================================

    def get_gain_range(self) -> "CameraGainRange":
        """Get camera gain range."""
        with self._lock:
            return self._camera.get_gain_range()

    def get_acquisition_mode(self) -> "CameraAcquisitionMode":
        """Get current acquisition mode."""
        with self._lock:
            return self._camera.get_acquisition_mode()

    def set_acquisition_mode(self, mode: "CameraAcquisitionMode") -> None:
        """Set camera acquisition mode."""
        with self._lock:
            self._camera.set_acquisition_mode(mode)

    def get_pixel_size_binned_um(self) -> float:
        """Get pixel size after binning in microns."""
        with self._lock:
            return self._camera.get_pixel_size_binned_um()

    # ============================================================
    # Streaming and trigger methods (for acquisition)
    # ============================================================

    def start_streaming(self) -> None:
        """Start camera streaming."""
        self._log.debug("Starting camera streaming")
        with self._lock:
            self._camera.start_streaming()

    def stop_streaming(self) -> None:
        """Stop camera streaming."""
        self._log.debug("Stopping camera streaming")
        with self._lock:
            self._camera.stop_streaming()

    def get_is_streaming(self) -> bool:
        """Check if camera is streaming."""
        with self._lock:
            return self._camera.get_is_streaming()

    def send_trigger(self, illumination_time: Optional[float] = None) -> None:
        """Send trigger to camera."""
        with self._lock:
            self._camera.send_trigger(illumination_time=illumination_time)

    def get_ready_for_trigger(self) -> bool:
        """Check if camera is ready for trigger."""
        with self._lock:
            return self._camera.get_ready_for_trigger()

    def get_total_frame_time(self) -> float:
        """Get total frame time in milliseconds."""
        with self._lock:
            return self._camera.get_total_frame_time()

    def read_frame(self):
        """Read a frame from the camera (blocking)."""
        with self._lock:
            return self._camera.read_frame()

    def read_camera_frame(self):
        """Read a CameraFrame from the camera (blocking)."""
        with self._lock:
            return self._camera.read_camera_frame()

    # ============================================================
    # Callback management
    # ============================================================

    def add_frame_callback(self, callback: Callable) -> str:
        """Add a frame callback and return callback ID."""
        with self._lock:
            return self._camera.add_frame_callback(callback)

    def remove_frame_callback(self, callback_id: str) -> None:
        """Remove a frame callback by ID."""
        with self._lock:
            self._camera.remove_frame_callback(callback_id)

    def enable_callbacks(self, enabled: bool) -> None:
        """Enable or disable frame callbacks."""
        with self._lock:
            self._camera.enable_callbacks(enabled)

    def get_callbacks_enabled(self) -> bool:
        """Check if callbacks are enabled."""
        with self._lock:
            return self._camera.get_callbacks_enabled()
