from pyvcam import pvc
from pyvcam.camera import Camera as PVCam
from typing import Callable, Optional, Tuple, Sequence
import numpy as np
import threading
import time

import squid.logging
from squid.config import CameraConfig, CameraPixelFormat
from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraFrameFormat,
    CameraFrame,
    CameraGainRange,
    CameraError,
)
from control._def import *


class PhotometricsCamera(AbstractCamera):
    PIXEL_SIZE_UM = 6.5  # Kinetix camera

    @staticmethod
    def _open(index: Optional[int] = None) -> PVCam:
        """Open a Photometrics camera and return the camera object."""
        log = squid.logging.get_logger("PhotometricsCamera._open")

        pvc.init_pvcam()

        try:
            if index is not None:
                # Open by index (not commonly used for Photometrics)
                cameras = list(PVCam.detect_camera())
                if index >= len(cameras):
                    raise CameraError(f"Camera index {index} out of range. Found {len(cameras)} cameras.")
                cam = cameras[index]
            else:
                # Open first available camera
                cam = next(PVCam.detect_camera())

            cam.open()
            log.info("Photometrics camera opened successfully")
            return cam

        except Exception as e:
            pvc.uninit_pvcam()
            raise CameraError(f"Failed to open Photometrics camera: {e}")

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        # Threading for frame reading
        self._read_thread_lock = threading.Lock()
        self._read_thread: Optional[threading.Thread] = None
        self._read_thread_keep_running = threading.Event()
        self._read_thread_keep_running.clear()
        self._read_thread_wait_period_s = 1.0
        self._read_thread_running = threading.Event()
        self._read_thread_running.clear()

        # Frame management
        self._frame_lock = threading.Lock()
        self._current_frame: Optional[CameraFrame] = None
        self._last_trigger_timestamp = 0
        self._trigger_sent = threading.Event()
        self._is_streaming = threading.Event()

        # Open camera
        self._camera = PhotometricsCamera._open()

        # Camera configuration
        self._exposure_time_ms = 20  # set it to some default value

        self._pixel_format = CameraPixelFormat.MONO16  # Initialize pixel format to 16bit (Dynamic Range Mode)
        # Initialize ROI values to full frame, or we may get None values if default ROI is not set
        self._crop_roi = (0, 0, 3200, 3200)
        self._configure_camera()

        """
        # TODO: need to confirm if we can get temperature during live mode
        # Temperature monitoring
        self.temperature_reading_callback = None
        self._terminate_temperature_event = threading.Event()
        self.temperature_reading_thread = threading.Thread(target=self._check_temperature, daemon=True)
        self.temperature_reading_thread.start()
        """

    def _configure_camera(self):
        """Configure camera with default settings."""
        self._camera.exp_res = 0  # Exposure resolution in milliseconds
        self._camera.speed_table_index = 0
        self._camera.exp_out_mode = 3  # Rolling shutter mode
        try:
            self.set_region_of_interest(*self._config.default_roi)  # 25mm FOV ROI: 240, 240, 2720, 2720
        except Exception as e:
            self._log.error(f"Failed to set crop ROI: {e}")
        self._log.info(f"Cropped area: {self._camera.shape(0)}")
        self.set_pixel_format(self._config.default_pixel_format)
        self.set_temperature(self._config.default_temperature)
        self._calculate_strobe_delay()

    def start_streaming(self):
        if self._is_streaming.is_set():
            self._log.debug("Already streaming, start_streaming is noop")
            return

        try:
            self._camera.start_live()
            self._ensure_read_thread_running()
            self._trigger_sent.clear()
            self._is_streaming.set()
            self._log.info("Photometrics camera starts streaming")
        except Exception as e:
            raise CameraError(f"Failed to start streaming: {e}")

    def stop_streaming(self):
        if not self._is_streaming.is_set():
            self._log.debug("Already stopped, stop_streaming is noop")
            return

        try:
            self._cleanup_read_thread()
            self._camera.finish()
            self._trigger_sent.clear()
            self._is_streaming.clear()
            self._log.info("Photometrics camera streaming stopped")
        except Exception as e:
            raise CameraError(f"Failed to stop streaming: {e}")

    def get_is_streaming(self):
        return self._is_streaming.is_set()

    def close(self):
        try:
            self._camera.close()
        except Exception as e:
            raise CameraError(f"Failed to close camera: {e}")
        pvc.uninit_pvcam()

    def _ensure_read_thread_running(self):
        with self._read_thread_lock:
            if self._read_thread is not None and self._read_thread_running.is_set():
                self._log.debug("Read thread exists and thread is marked as running.")
                return True

            elif self._read_thread is not None:
                self._log.warning("Read thread already exists, but not marked as running. Still attempting start.")

            self._read_thread = threading.Thread(target=self._wait_for_frame, daemon=True)
            self._read_thread_keep_running.set()
            self._read_thread.start()

    def _cleanup_read_thread(self):
        self._log.debug("Cleaning up read thread.")
        with self._read_thread_lock:
            if self._read_thread is None:
                self._log.warning("No read thread, already not running?")
                return True

            self._read_thread_keep_running.clear()

            try:
                self._camera.abort()
            except Exception as e:
                self._log.warning(f"Failed to abort camera: {e}")

            self._read_thread.join(1.1 * self._read_thread_wait_period_s)

            success = not self._read_thread.is_alive()
            if not success:
                self._log.warning("Read thread refused to exit!")

            self._read_thread = None
            self._read_thread_running.clear()

    def _wait_for_frame(self):
        """Thread function to wait for and process frames."""
        self._log.info("Starting Photometrics read thread.")
        self._read_thread_running.set()

        while self._read_thread_keep_running.is_set():
            try:
                wait_time = int(self._read_thread_wait_period_s * 1000)
                frame, _, _ = self._camera.poll_frame(timeout_ms=wait_time)
                if frame is None:
                    time.sleep(0.001)
                    continue

                raw_data = frame["pixel_data"]
                processed_frame = self._process_raw_frame(raw_data)

                with self._frame_lock:
                    camera_frame = CameraFrame(
                        frame_id=self._current_frame.frame_id + 1 if self._current_frame else 1,
                        timestamp=time.time(),
                        frame=processed_frame,
                        frame_format=self.get_frame_format(),
                        frame_pixel_format=self.get_pixel_format(),
                    )
                    self._current_frame = camera_frame

                self._propogate_frame(camera_frame)
                self._trigger_sent.clear()

                time.sleep(0.001)

            except Exception as e:
                self._log.debug(f"Exception in read loop: {e}, continuing...")
                time.sleep(0.001)

        self._read_thread_running.clear()

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
                self._log.warning(
                    f"Timed out after waiting {timeout_s=}[s] for frame ({starting_id=}), total_frame_time={self.get_total_frame_time()}."
                )
                return None
            time.sleep(0.001)

        with self._frame_lock:
            return self._current_frame

    def get_frame_id(self) -> int:
        with self._frame_lock:
            return self._current_frame.frame_id if self._current_frame else -1

    def set_exposure_time(self, exposure_time_ms: float):
        # Kinetix camera set_exposure_time is slow, so we don't want to call it unnecessarily.
        if exposure_time_ms == self._exposure_time_ms:
            return
        self._set_exposure_time_imp(exposure_time_ms)

    def _set_exposure_time_imp(self, exposure_time_ms: float):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            strobe_time_ms = self.get_strobe_time()
            adjusted_exposure_time = exposure_time_ms + strobe_time_ms
            if self._hw_set_strobe_delay_ms_fn:
                self._log.debug(f"Setting hw strobe time to {strobe_time_ms} [ms]")
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)
        else:
            adjusted_exposure_time = exposure_time_ms

        with self._pause_streaming():
            try:
                self._camera.exp_time = int(adjusted_exposure_time)
                self._exposure_time_ms = exposure_time_ms
                self._trigger_sent.clear()
            except Exception as e:
                raise CameraError(f"Failed to set exposure time: {e}")

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        return 0.0, 10000.0  # From Kinetix manual

    def get_strobe_time(self) -> float:
        return self._strobe_delay_ms

    def set_frame_format(self, frame_format: CameraFrameFormat):
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by this camera.")

    def get_frame_format(self) -> CameraFrameFormat:
        return CameraFrameFormat.RAW

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        """
        port_speed_gain_table:
        {'Sensitivity': {'port_value': 0, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 12, 'gain_range': [1], 'Standard': {'gain_index': 1}}},
        'Speed': {'port_value': 1, 'Speed_0': {'speed_index': 0, 'pixel_time': 5, 'bit_depth': 8, 'gain_range': [1, 2], 'Sensitivity': {'gain_index': 1}, 'Full Well': {'gain_index': 2}}},
        'Dynamic Range': {'port_value': 2, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 16, 'gain_range': [1], 'Standard': {'gain_index': 1}}},
        'Sub-Electron': {'port_value': 3, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 16, 'gain_range': [1], 'Standard': {'gain_index': 1}}}}
        """
        with self._pause_streaming():
            try:
                if pixel_format == CameraPixelFormat.MONO8:
                    self._camera.readout_port = 1
                elif pixel_format == CameraPixelFormat.MONO12:
                    self._camera.readout_port = 0
                elif pixel_format == CameraPixelFormat.MONO16:
                    self._camera.readout_port = 2
                else:
                    raise ValueError(f"Unsupported pixel format: {pixel_format}")

                self._pixel_format = pixel_format
                self._calculate_strobe_delay()

            except Exception as e:
                raise CameraError(f"Failed to set pixel format: {e}")

    def get_pixel_format(self) -> CameraPixelFormat:
        # We are not able to query camera for pixel format during live mode, so we need to return the pixel format we set.
        return self._pixel_format

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return [CameraPixelFormat.MONO8, CameraPixelFormat.MONO12, CameraPixelFormat.MONO16]

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        if binning_factor_x != 1 or binning_factor_y != 1:
            raise ValueError("Kinetix camera does not support binning")

    def get_binning(self) -> Tuple[int, int]:
        return (1, 1)

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return [(1, 1)]

    def get_resolution(self) -> Tuple[int, int]:
        return (3200, 3200)

    def get_pixel_size_unbinned_um(self) -> float:
        return PhotometricsCamera.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return PhotometricsCamera.PIXEL_SIZE_UM  # No binning supported

    def set_analog_gain(self, analog_gain: float):
        raise NotImplementedError("Analog gain is not supported by this camera.")

    def get_analog_gain(self) -> float:
        raise NotImplementedError("Analog gain is not supported by this camera.")

    def get_gain_range(self) -> CameraGainRange:
        raise NotImplementedError("Analog gain is not supported by this camera.")

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White balance gains are not supported by this camera.")

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        raise NotImplementedError("White balance gains are not supported by this camera.")

    def set_auto_white_balance_gains(self, on: bool):
        raise NotImplementedError("Auto white balance gains are not supported by this camera.")

    def set_black_level(self, black_level: float):
        raise NotImplementedError("Black level adjustment is not supported by this camera.")

    def get_black_level(self) -> float:
        raise NotImplementedError("Black level adjustment is not supported by this camera.")

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        with self._pause_streaming():
            try:
                if acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                    self._camera.exp_mode = "Internal Trigger"
                elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                    self._camera.exp_mode = "Software Trigger Edge"
                elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                    self._camera.exp_mode = "Edge Trigger"
                else:
                    raise ValueError(f"Unsupported acquisition mode: {acquisition_mode}")

                self._acquisition_mode = acquisition_mode
                self._set_exposure_time_imp(self._exposure_time_ms)

            except Exception as e:
                raise CameraError(f"Failed to set acquisition mode: {e}")

    _TRIGGER_CODE_MAPPING_KINETIX = {
        1792: "Internal Trigger",
        2304: "Edge Trigger",
        2048: "Trigger First",
        2560: "Level Trigger",
        3328: "Level Trigger Overlap",
        3072: "Software Trigger Edge",
        2816: "Software Trigger First",
    }

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        if PhotometricsCamera._TRIGGER_CODE_MAPPING_KINETIX[self._camera.exp_mode] == "Internal Trigger":
            return CameraAcquisitionMode.CONTINUOUS
        elif PhotometricsCamera._TRIGGER_CODE_MAPPING_KINETIX[self._camera.exp_mode] == "Software Trigger Edge":
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif PhotometricsCamera._TRIGGER_CODE_MAPPING_KINETIX[self._camera.exp_mode] == "Edge Trigger":
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            raise ValueError(
                f"Unknown acquisition mode: {PhotometricsCamera._TRIGGER_CODE_MAPPING_KINETIX[self._camera.exp_mode]}"
            )

    def send_trigger(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise CameraError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_is_streaming():
            raise CameraError("Camera is not streaming, cannot send trigger.")

        if not self.get_ready_for_trigger():
            raise CameraError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            try:
                self._camera.sw_trigger()
                self._last_trigger_timestamp = time.time()
                self._trigger_sent.set()
            except Exception as e:
                raise CameraError(f"Failed to send software trigger: {e}")

    def get_ready_for_trigger(self) -> bool:
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        with self._pause_streaming():
            try:
                self._camera.set_roi(offset_x, offset_y, width, height)
                self._crop_roi = (offset_x, offset_y, width, height)
                self._calculate_strobe_delay()
            except Exception as e:
                raise CameraError(f"Failed to set ROI: {e}")

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        # There's no way to query ROI in their SDK, so we need to return the ROI we set.
        return self._crop_roi

    def set_temperature(self, temperature_deg_c: Optional[float]):
        # Kinetix camera temperature range: -15 to 15 C.
        # Right now we need to pause streaming to set temperature. Not sure if this is the same with new cameras.
        with self._pause_streaming():
            try:
                if temperature_deg_c < -15 or temperature_deg_c > 15:
                    raise ValueError(f"Temperature must be between -15 and 15 C, got {temperature_deg_c} C")
                self._camera.temp_setpoint = int(temperature_deg_c)
            except Exception as e:
                raise CameraError(f"Failed to set temperature: {e}")

    def get_temperature(self) -> float:
        # Right now we need to pause streaming to get temperature. This is very slow, so we will not update real-time temperature in gui.
        with self._pause_streaming():
            try:
                return self._camera.temp
            except Exception as e:
                raise CameraError(f"Failed to get temperature: {e}")

    def set_temperature_reading_callback(self, callback: Callable):
        raise NotImplementedError("Temperature reading callback is not supported by this camera.")

    def _calculate_strobe_delay(self):
        """Calculate strobe delay based on pixel format and ROI."""
        # Line time (us) from the manual:
        # Dynamic Range Mode (16bit): 3.75; Speed Mode (8bit): 0.625; Sensitivity Mode (12bit): 3.53125; Sub-Electron Mode (16bit): 60.1
        _, height = self._camera.shape(0)

        if self._pixel_format == CameraPixelFormat.MONO8:
            line_time_us = 0.625
        elif self._pixel_format == CameraPixelFormat.MONO12:
            line_time_us = 3.53125
        elif self._pixel_format == CameraPixelFormat.MONO16:
            line_time_us = 3.75
        else:
            line_time_us = 3.53125  # Default

        self._strobe_delay_ms = (line_time_us * height) / 1000.0
