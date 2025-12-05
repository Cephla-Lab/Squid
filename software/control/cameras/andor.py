import time
import numpy as np
import threading
import os
from typing import Optional, Callable, Sequence, Tuple, Dict
import pydantic

import pyAndorSDK3
from pyAndorSDK3 import AndorSDK3
from control._def import *
from squid.abc import AbstractCamera, CameraError
from squid.config import CameraConfig, CameraPixelFormat
from squid.abc import CameraFrame, CameraFrameFormat, CameraGainRange, CameraAcquisitionMode
import control.utils


# For using in Windows only
package_path = os.path.dirname(pyAndorSDK3.__file__)
library_path = os.path.join(package_path, "libs", "Windows", "64")
pyAndorSDK3.utils.add_library_path(library_path)


class AndorCapabilities(pydantic.BaseModel):
    binning_to_resolution: Dict[Tuple[int, int], Tuple[int, int]]


class AndorCamera(AbstractCamera):
    PIXEL_SIZE_UM: float = 6.5  # ZL 41 Cell

    @staticmethod
    def _open(index=None, sn=None) -> Tuple["pyAndorSDK3.Camera", AndorCapabilities]:
        if index is None and sn is None:
            raise ValueError("You must specify one of either index or sn.")
        elif index is not None and sn is not None:
            raise ValueError("You must specify only 1 of index or sn")

        sdk3 = AndorSDK3()

        if sn is not None:
            # TODO: Implement serial number lookup
            raise NotImplementedError("Serial number lookup not yet implemented for Andor cameras")

        if index is None:
            index = 0

        try:
            camera = sdk3.GetCamera(index)
            camera.open()
        except Exception as e:
            raise CameraError(f"Failed to open Andor camera with index={index}: {e}")

        # Get camera capabilities
        try:
            camera_model = camera.CameraModel
            log.info(f"Andor camera model: {camera_model}")
            # For now, we only support ZL41 Cell 4.2
            supported_resolutions = {
                (1, 1): (2048, 2048),
                (2, 2): (1024, 1024),
                (3, 3): (682, 682),
                (4, 4): (512, 512),
                (8, 8): (256, 256),
            }

            capabilities = AndorCapabilities(binning_to_resolution=supported_resolutions)

            return camera, capabilities
        except Exception as e:
            camera.close()
            raise CameraError(f"Failed to get camera capabilities: {e}")

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        self._read_thread_lock = threading.Lock()
        self._read_thread: Optional[threading.Thread] = None
        self._read_thread_keep_running = threading.Event()
        self._read_thread_keep_running.clear()
        self._read_thread_wait_period_s = 0.01
        self._read_thread_running = threading.Event()
        self._read_thread_running.clear()

        self._frame_lock = threading.Lock()
        self._current_frame: Optional[CameraFrame] = None
        self._last_trigger_timestamp = 0
        self._trigger_sent = threading.Event()

        camera, capabilities = AndorCamera._open(index=0)

        self._camera = camera
        self._capabilities: AndorCapabilities = capabilities
        self._is_streaming = threading.Event()

        # Andor specific properties
        self._strobe_delay_us: Optional[float] = None
        self._buffer_queue = []
        self._binning = self._config.default_binning

        # We store exposure time so we don't need to worry about backing out strobe time
        self._exposure_time_ms: float = 20

        # Initialize camera settings
        self._initialize_camera()
        self.set_exposure_time(self._exposure_time_ms)

    def _initialize_camera(self):
        if self._camera is None:
            return

        # Get exposure time limits
        try:
            self.EXPOSURE_TIME_MS_MIN = self._camera.min_ExposureTime * 1000  # convert to ms
            self.EXPOSURE_TIME_MS_MAX = self._camera.max_ExposureTime * 1000  # convert to ms
            self._log.info(f"Exposure limits: min={self.EXPOSURE_TIME_MS_MIN}ms, max={self.EXPOSURE_TIME_MS_MAX}ms")
        except Exception as e:
            self._log.error(f"Could not determine exposure time limits: {e}")
            self.EXPOSURE_TIME_MS_MIN = 0.01
            self.EXPOSURE_TIME_MS_MAX = 30000.0

        self.set_exposure_time(self._exposure_time_ms)
        self.set_pixel_format(CameraPixelFormat.MONO16)
        self.set_binning(*self._config.default_binning)

    def close(self):
        self._cleanup_read_thread()

        if self._is_streaming.is_set():
            self.stop_streaming()

        if self._camera is not None:
            self._camera.close()
            self._camera = None

    def _allocate_read_buffers(self, count=2):
        """Allocate buffers for the Andor camera"""
        try:
            img_size = self._camera.ImageSizeBytes
            self._buffer_queue = []
            for _ in range(count):
                buf = np.empty((img_size,), dtype="B")
                self._camera.queue(buf, img_size)
                self._buffer_queue.append(buf)  # Keep reference to avoid GC
            return True
        except Exception as e:
            self._log.error(f"Failed to allocate buffers: {e}")
            return False

    def _read_frames_when_available(self):
        self._log.info("Starting Andor read thread.")
        self._read_thread_running.set()

        while self._read_thread_keep_running.is_set():
            try:
                try:
                    # Wait for frame with timeout
                    acq = self._camera.wait_buffer(1000)  # 1000ms timeout

                    # Queue a new buffer
                    self._camera.queue(acq._np_data, self._camera.ImageSizeBytes)

                    # Process the frame
                    raw = np.asarray(acq._np_data, dtype=np.uint8)
                    img = raw.view("<u2")  # Andor typically returns 16-bit data

                    # We reshape the image based on the full resolution now. After we implement ROI, we should
                    # use AOIWidth, AOIHeight, and AOIStride instead.
                    h, w = self._capabilities.binning_to_resolution[self._binning]
                    img = img.reshape(h, w)

                    self._trigger_sent.clear()

                    processed_frame = self._process_raw_frame(img)

                    with self._frame_lock:
                        camera_frame = CameraFrame(
                            frame_id=self._current_frame.frame_id + 1 if self._current_frame else 1,
                            timestamp=time.time(),
                            frame=processed_frame,
                            frame_format=self.get_frame_format(),
                            frame_pixel_format=self.get_pixel_format(),
                        )
                        self._current_frame = camera_frame

                    # Send frame to callbacks
                    self._propogate_frame(camera_frame)

                except Exception as e:
                    # Timeout is normal, don't log
                    if "timeout" not in str(e).lower():
                        self._log.debug(f"Frame read error: {e}")
                    time.sleep(0.001)

            except Exception as e:
                self._log.exception("Exception in read loop, ignoring and trying to continue.")
                time.sleep(0.01)

        self._read_thread_running.clear()

    def set_exposure_time(self, exposure_time_ms: float):
        camera_exposure_time_s = exposure_time_ms / 1000.0

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            strobe_time_ms = self.get_strobe_time()
            camera_exposure_time_s += strobe_time_ms / 1000.0
            if self._hw_set_strobe_delay_ms_fn:
                self._log.debug(f"Setting hw strobe time to {strobe_time_ms} [ms]")
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)

        try:
            self._camera.ExposureTime = camera_exposure_time_s
            self._exposure_time_ms = exposure_time_ms
            self._trigger_sent.clear()
            return True
        except Exception as e:
            raise CameraError(f"Failed to set exposure time to {exposure_time_ms}ms: {e}")

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        return self.EXPOSURE_TIME_MS_MIN, self.EXPOSURE_TIME_MS_MAX

    def get_strobe_time(self) -> float:
        if self._strobe_delay_us is None:
            return 0.0
        return self._strobe_delay_us / 1000.0  # Convert to ms

    def _calculate_strobe_delay(self):
        try:
            self._line_rate_us = 1 / self._camera.LineScanSpeed * 1000000
            self._log.info(f"Line rate: {self._line_rate_us} us")
        except Exception as e:
            self._log.error(f"Could not determine line rate: {e}")
            self._line_rate_us = None

        if self._line_rate_us is not None:
            resolution = self.get_resolution()
            self._strobe_delay_us = int(self._line_rate_us * resolution[1])

    def set_frame_format(self, frame_format: CameraFrameFormat):
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by this camera.")
        return True

    def get_frame_format(self) -> CameraFrameFormat:
        return CameraFrameFormat.RAW

    _PIXEL_FORMAT_TO_GAIN_MODE = {
        CameraPixelFormat.MONO12: "12-bit (low noise)",
        CameraPixelFormat.MONO16: "16-bit (low noise & high well capacity)",
    }

    _PIXEL_FORMAT_TO_ANDOR_FORMAT = {
        CameraPixelFormat.MONO12: "Mono12",
        CameraPixelFormat.MONO16: "Mono16",
    }

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        if isinstance(pixel_format, str):
            try:
                pixel_format = CameraPixelFormat(pixel_format)
            except ValueError:
                raise ValueError(f"Unknown or unsupported pixel format={pixel_format}")

        if pixel_format not in self._PIXEL_FORMAT_TO_GAIN_MODE:
            raise ValueError(f"Pixel format {pixel_format} is not supported by this camera.")

        with self._pause_streaming():
            try:
                self._camera.SimplePreAmpGainControl = self._PIXEL_FORMAT_TO_GAIN_MODE[pixel_format]
                # PixelEncoding will be set automatically based on SimplePreAmpGainControl, but to make sure we are
                # using the "Mono12" instead of "Mono12Packed" or other formats, it may be safer to set it explicitly
                self._camera.PixelEncoding = self._PIXEL_FORMAT_TO_ANDOR_FORMAT[pixel_format]

                self._calculate_strobe_delay()

                # Update exposure time if in hardware trigger mode
                if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
                    self.set_exposure_time(self._exposure_time_ms)

            except Exception as e:
                raise CameraError(f"Failed to set pixel format to {pixel_format}: {e}")

    def get_pixel_format(self) -> CameraPixelFormat:
        try:
            andor_format = self._camera.PixelEncoding
            _andor_to_pixel = {v: k for (k, v) in self._PIXEL_FORMAT_TO_ANDOR_FORMAT.items()}

            return _andor_to_pixel[andor_format]
        except Exception:
            raise CameraError("Could not determine pixel format")

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return list(self._PIXEL_FORMAT_TO_ANDOR_FORMAT.keys())

    def get_resolution(self) -> Tuple[int, int]:
        return self._capabilities.binning_to_resolution[self.get_binning()]

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        if (binning_factor_x, binning_factor_y) not in self._capabilities.binning_to_resolution:
            raise ValueError(f"Binning {binning_factor_x}x{binning_factor_y} is not supported by this camera.")
        with self._pause_streaming():
            try:
                self._camera.AOIBinning = f"{binning_factor_x}x{binning_factor_y}"
                self._calculate_strobe_delay()
                self._binning = (binning_factor_x, binning_factor_y)
            except Exception as e:
                raise CameraError(f"Failed to set binning to {binning_factor_x}x{binning_factor_y}: {e}")

    def get_binning(self) -> Tuple[int, int]:
        try:
            binning = self._camera.AOIBinning
            binning_x, binning_y = binning.split("x")
            return (int(binning_x), int(binning_y))
        except Exception:
            raise CameraError("Could not determine binning")

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return self._capabilities.binning_to_resolution.keys()

    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return self.PIXEL_SIZE_UM * self.get_binning()[0]

    def set_analog_gain(self, analog_gain: float):
        # Andor cameras typically don't have analog gain
        self._log.warning("Analog gain is not supported for Andor cameras")

    def get_analog_gain(self) -> float:
        return 0.0

    def get_gain_range(self) -> CameraGainRange:
        raise NotImplementedError("Analog gain is not supported for Andor cameras")

    def _ensure_read_thread_running(self):
        with self._read_thread_lock:
            if self._read_thread is not None and self._read_thread_running.is_set():
                self._log.debug("Read thread exists and thread is marked as running.")
                return True

            elif self._read_thread is not None:
                self._log.warning("Read thread already exists, but not marked as running. Still attempting start.")

            self._read_thread = threading.Thread(target=self._read_frames_when_available, daemon=True)
            self._read_thread_keep_running.set()
            self._read_thread.start()

    def start_streaming(self):
        if self._is_streaming.is_set():
            self._log.debug("Already streaming, start_streaming is noop")
            return True

        try:
            if not self._allocate_read_buffers():
                raise CameraError("Failed to allocate read buffers")

            self._camera.AcquisitionStart()
            self._trigger_sent.clear()
            self._is_streaming.set()
            self._ensure_read_thread_running()
            self._log.info("Andor camera started streaming")
            return True
        except Exception as e:
            self._log.error(f"Failed to start streaming: {e}")
            self._is_streaming.clear()
            return False

    def _cleanup_read_thread(self):
        self._log.debug("Cleaning up read thread.")
        with self._read_thread_lock:
            if self._read_thread is None:
                self._log.warning("No read thread, already not running?")
                return True

            self._read_thread_keep_running.clear()
            self._read_thread.join(1.1 * self._read_thread_wait_period_s)

            success = not self._read_thread.is_alive()
            if not success:
                self._log.warning("Read thread refused to exit!")

            self._read_thread = None
            self._read_thread_running.clear()

    def stop_streaming(self):
        if not self._is_streaming.is_set():
            self._log.debug("Already stopped, stop_streaming is noop")
            return
        try:
            self._camera.AcquisitionStop()
            self._camera.flush()
            self._buffer_queue = []
            self._trigger_sent.clear()
            self._is_streaming.clear()
            self._log.info("Andor camera stopped streaming")
        except Exception as e:
            self._log.error(f"Error stopping streaming: {e}")

    def get_is_streaming(self):
        return self._is_streaming.is_set()

    def read_camera_frame(self) -> Optional[CameraFrame]:
        if not self.get_is_streaming():
            self._log.error("Cannot read camera frame when not streaming.")
            return None

        if not self._read_thread_running.is_set():
            self._log.error("Fatal camera error: read thread not running!")
            return None

        starting_id = self.get_frame_id()
        timeout_s = (1.04 * self.get_total_frame_time() + 1000) / 1000.0
        timeout_time_s = time.time() + timeout_s

        while self.get_frame_id() == starting_id:
            if time.time() > timeout_time_s:
                self._log.warning(f"Timed out after waiting {timeout_s}s for frame (starting_id={starting_id})")
                return None
            time.sleep(0.001)

        with self._frame_lock:
            return self._current_frame

    def get_frame_id(self) -> int:
        with self._frame_lock:
            return self._current_frame.frame_id if self._current_frame else -1

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Andor driver.")

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        raise NotImplementedError("White Balance Gains not implemented for the Andor driver.")

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Andor driver.")

    def set_black_level(self, black_level: float):
        raise NotImplementedError("Black levels are not implemented for the Andor driver.")

    def get_black_level(self) -> float:
        raise NotImplementedError("Black levels are not implemented for the Andor driver.")

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        with self._pause_streaming():
            try:
                if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                    self._camera.CycleMode = "Continuous"
                    self._camera.TriggerMode = "Software"
                elif acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                    self._camera.CycleMode = "Continuous"
                    self._camera.TriggerMode = "Internal"
                elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                    self._camera.CycleMode = "Continuous"
                    self._camera.TriggerMode = "External"
                    self._frame_ID_offset_hardware_trigger = None
                else:
                    raise ValueError(f"Unhandled acquisition_mode={acquisition_mode}")

                self.set_exposure_time(self._exposure_time_ms)

            except Exception as e:
                self._log.error(f"Failed to set acquisition mode to {acquisition_mode}: {e}")
                return False
        return True

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        try:
            trigger_mode = self._camera.TriggerMode

            if trigger_mode == "External":
                return CameraAcquisitionMode.HARDWARE_TRIGGER
            elif trigger_mode == "Software":
                return CameraAcquisitionMode.SOFTWARE_TRIGGER
            elif trigger_mode == "Internal":
                return CameraAcquisitionMode.CONTINUOUS
            else:
                raise ValueError(f"Unknown Andor trigger mode: {trigger_mode}")
        except Exception:
            # Default to continuous if we can't determine
            return CameraAcquisitionMode.CONTINUOUS

    def send_trigger(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise CameraError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_is_streaming():
            raise CameraError("Camera is not streaming, cannot send trigger.")

        if not self.get_ready_for_trigger():
            raise CameraError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp}s ago)"
            )

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            try:
                self._camera.SoftwareTrigger()
                self._last_trigger_timestamp = time.time()
                self._trigger_sent.set()
                self._log.debug("trigger sent")
            except Exception as e:
                raise CameraError(f"Failed to send software trigger: {e}")

    def get_ready_for_trigger(self) -> bool:
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        # Andor cameras have limited ROI support
        self._log.warning("ROI is not fully implemented for Andor cameras")

        # Store ROI parameters for potential future use
        self.ROI_offset_x = offset_x
        self.ROI_offset_y = offset_y
        self.ROI_width = width
        self.ROI_height = height

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return (0, 0, self._camera.AOIWidth, self._camera.AOIHeight)

    def set_temperature(self, temperature_deg_c: Optional[float]):
        raise NotImplementedError("Temperature control is not implemented for this Andor camera model.")

    def get_temperature(self) -> float:
        try:
            return self._camera.SensorTemperature
        except Exception:
            raise NotImplementedError("Temperature reading is not available for this Andor camera model.")

    def set_temperature_reading_callback(self, func) -> Callable[[float], None]:
        raise NotImplementedError("Temperature reading callback is not supported for this camera.")
