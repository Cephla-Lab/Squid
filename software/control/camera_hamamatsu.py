import time
from typing import Optional, Callable, Sequence, Tuple, Dict
import threading

import pydantic

from squid.abc import AbstractCamera, CameraError
from squid.config import CameraConfig, CameraPixelFormat
from squid.abc import CameraFrame, CameraFrameFormat, CameraGainRange, CameraAcquisitionMode
from control.dcam import Dcam, Dcamapi
from control.dcamapi4 import *
import control.utils


class HamamatsuCapabilities(pydantic.BaseModel):
    binning_to_resolution: Dict[Tuple[int, int], Tuple[int, int]]


class HamamatsuCamera(AbstractCamera):
    PIXEL_SIZE_UM: float = 6.5

    @staticmethod
    def _open(index=None, sn=None) -> Tuple[Dcam, HamamatsuCapabilities]:
        if index is None and sn is None:
            raise ValueError("You must specify one of either index or sn.")
        elif index is not None and sn is not None:
            raise ValueError("You must specify only 1 of index or sn")

        dcam_init_result = Dcamapi.init()
        if isinstance(dcam_init_result, bool):
            if not dcam_init_result:
                raise CameraError(
                    f"Dcam api initialization failed: {HamamatsuCamera._last_dcam_error_string_direct(Dcamapi.lasterr())}"
                )
            else:
                raise CameraError("Dcam api init gave true, which is not valid.")
        elif isinstance(dcam_init_result, tuple):
            dcam_init, cam_count = dcam_init_result
        else:
            raise CameraError("Dcam api init result is invalid.")

        if cam_count < 1:
            raise ValueError("No Dcam api cameras available - is the hardware plugged in?")

        if sn is not None:
            for idx in range(cam_count):
                cam = Dcam(idx)
                if sn == cam.dev_getstring(DCAM_IDSTR.CAMERAID):
                    index = idx
                    break

            if index is None:
                raise ValueError(f"Could not find camera with serial number: '{sn}'")

        if index is None:
            raise ValueError("Index is none after all checks, something is wrong!")

        camera = Dcam(index)

        if not camera.dev_open(index):
            raise CameraError(f"Failed to open camera with index={index}")

        supported_resolutions = {
            # C15440-20UP (ORCA-Fusion BT)
            (1, 1): (2304, 2304),
            (2, 2): (1152, 1152),
            (4, 4): (576, 576),
        }

        capabilities = HamamatsuCapabilities(binning_to_resolution=supported_resolutions)

        return camera, capabilities

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
        self._read_thread_wait_period_s = 1.0
        self._read_thread_running = threading.Event()
        self._read_thread_running.clear()

        self._frame_lock = threading.Lock()
        self._current_frame: Optional[CameraFrame] = None
        self._last_trigger_timestamp = 0
        self._trigger_sent = threading.Event()

        camera, capabilities = HamamatsuCamera._open(index=0)

        self._camera: Dcam = camera
        self._capabilities: HamamatsuCapabilities = capabilities
        self._is_streaming = threading.Event()

        # We store exposure time so we don't need to worry about backing out strobe time from the
        # time stored on the camera.
        #
        # We just set it to some sane value to start.
        self._exposure_time_ms: int = 20
        self.set_exposure_time(self._exposure_time_ms)

    def close(self):
        self._cleanup_read_thread()

    def _set_prop(self, dcam_prop, prop_value):
        if not self._camera.prop_setvalue(dcam_prop, prop_value):
            self._log.error(f"Failed to set property {dcam_prop}={prop_value}: {self._camera.lasterr()}")
            return False
        return True

    def _allocate_read_buffers(self, count=5):
        # NOTE: The caller must hold the camera lock!
        if not self._camera.buf_alloc(count):
            self._log.error(f"Failed to allocate {count} buffers.")
            return False
        return True

    def _read_frames_when_available(self):
        self._log.info("Starting Hamamatsu read thread.")
        self._read_thread_running.set()
        while self._read_thread_keep_running.is_set():
            # We really, really, do not want this thread to die prematurely, so catch all exceptions and try
            # to continue.
            try:
                wait_time = int(round(self._read_thread_wait_period_s * 1000.0))
                frame_ready = self._camera.wait_event(DCAMWAIT_CAPEVENT.FRAMEREADY, wait_time)

                if frame_ready:
                    # The dcam driver handles setting the correct width and height, so we can use the
                    # np frame directly.
                    raw_frame = self._camera.buf_getlastframedata()
                    self._trigger_sent.clear()

                    if isinstance(raw_frame, bool):
                        self._log.error("Frame read resulted in boolean, must be an error.")
                        continue

                    processed_frame = self._process_raw_frame(raw_frame)
                    with self._frame_lock:
                        camera_frame = CameraFrame(
                            frame_id=self._current_frame.frame_id + 1 if self._current_frame else 1,
                            timestamp=time.time(),
                            frame=processed_frame,
                            frame_format=self.get_frame_format(),
                            frame_pixel_format=self.get_pixel_format(),
                        )

                        self._current_frame = camera_frame

                    # Send the local copy of the frame to all the callbacks so we are sure they get this frame
                    self._propogate_frame(camera_frame)

                # NOTE(imo): I'm not sure if self._camera.wait_event actually yields to the python
                # interpreter.
                time.sleep(0.001)

            except Exception as e:
                self._log.exception("Exception in read loop, ignoring and trying to continue.")
        self._read_thread_running.clear()

    @staticmethod
    def _last_dcam_error_string_direct(last_error: DCAMERR):

        reverse_error_map = {enum_entry.value: enum_name for (enum_name, enum_entry) in DCAMERR.__members__.items()}

        if last_error not in reverse_error_map:
            return f"{last_error}:{reverse_error_map[last_error]}"
        else:
            return f"{last_error}:UNKNOWN_ERROR"

    def _last_dcam_error_string(self):
        return HamamatsuCamera._last_dcam_error_string_direct(self._camera.lasterr())

    def set_exposure_time(self, exposure_time_ms: float):
        camera_exposure_time_s = exposure_time_ms / 1000.0
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            strobe_time_ms = self.get_strobe_time()
            camera_exposure_time_s += strobe_time_ms / 1000.0
            if self._hw_set_strobe_delay_ms_fn:
                self._log.debug(f"Setting hw strobe time to {strobe_time_ms} [ms]")
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)

        if not self._set_prop(DCAM_IDPROP.EXPOSURETIME, camera_exposure_time_s):
            raise CameraError(f"Failed to set exposure time to {exposure_time_ms=} ({camera_exposure_time_s=} [s])")

        self._exposure_time_ms = exposure_time_ms
        self._trigger_sent.clear()
        return True

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        exposure_attr = self._camera.prop_getattr(DCAM_IDPROP.EXPOSURETIME)
        return exposure_attr.valuemin * 1000.0, exposure_attr.valuemax * 1000.0  # in ms

    def get_strobe_time(self) -> float:
        resolution = self.get_resolution()
        line_interval_s = self._camera.prop_getvalue(DCAM_IDPROP.INTERNAL_LINEINTERVAL) * resolution[1]
        trigger_delay_s = self._camera.prop_getvalue(DCAM_IDPROP.TRIGGERDELAY)

        if isinstance(line_interval_s, bool) or isinstance(trigger_delay_s, bool):
            raise CameraError("Failed to get strobe delay properties from camera")

        return (line_interval_s + trigger_delay_s) * 1000.0

    def set_frame_format(self, frame_format: CameraFrameFormat):
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by this camera.")
        return True

    def get_frame_format(self) -> CameraFrameFormat:
        return CameraFrameFormat.RAW

    _PIXEL_FORMAT_TO_DCAM_FORMAT = {
        CameraPixelFormat.MONO8: DCAM_PIXELTYPE.MONO8,
        CameraPixelFormat.MONO16: DCAM_PIXELTYPE.MONO16,
    }

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        # For historical, allow passing in strings as long as they are valid pixel
        # formats.
        if isinstance(pixel_format, str):
            try:
                pixel_format = CameraPixelFormat(pixel_format)
            except ValueError:
                raise ValueError(f"Unknown or unsupported pixel format={pixel_format}")
        if pixel_format not in self._PIXEL_FORMAT_TO_DCAM_FORMAT:
            raise ValueError(f"Pixel format {pixel_format} is not supported by this camera.")

        with self._pause_streaming():
            if not self._set_prop(DCAM_IDPROP.IMAGE_PIXELTYPE, self._PIXEL_FORMAT_TO_DCAM_FORMAT[pixel_format]):
                raise CameraError(f"Failed to set pixel format to {pixel_format}")

    def get_pixel_format(self) -> CameraPixelFormat:
        raw_dcam_pixel_format = self._camera.prop_getvalue(DCAM_IDPROP.IMAGE_PIXELTYPE)

        if isinstance(raw_dcam_pixel_format, bool):
            raise CameraError("Failed to get pixel format from camera.")

        dcam_pixel_format = int(raw_dcam_pixel_format)
        _dcam_to_pixel = {v: k for (k, v) in self._PIXEL_FORMAT_TO_DCAM_FORMAT.items()}

        if dcam_pixel_format not in _dcam_to_pixel:
            raise ValueError(f"Camera returned unknown pixel format code: {dcam_pixel_format}")

        return _dcam_to_pixel[dcam_pixel_format]

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return list(self._PIXEL_FORMAT_TO_DCAM_FORMAT.keys())

    def get_resolution(self) -> Tuple[int, int]:
        return self._capabilities.binning_to_resolution[self.get_binning()]

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        # TODO: We only support 1x1 binning for now. More may be added later.
        if binning_factor_x != 1 or binning_factor_y != 1:
            raise ValueError("Binning has not been implemented for this camera yet.")

    def get_binning(self) -> Tuple[int, int]:
        return (1, 1)

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return [(1, 1)]

    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return self.PIXEL_SIZE_UM * self.get_binning()[0]

    def set_analog_gain(self, analog_gain: float):
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_analog_gain(self) -> float:
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_gain_range(self) -> CameraGainRange:
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def _ensure_read_thread_running(self):
        with self._read_thread_lock:
            if self._read_thread is not None and self._read_thread_running.is_set():
                self._log.debug("Read thread exists and thread is marked as running.")
                return True

            elif self._read_thread is not None:
                self._log.warning("Read thread already exists, but not marked as running.  Still attempting start.")

            self._read_thread = threading.Thread(target=self._read_frames_when_available, daemon=True)
            self._read_thread_keep_running.set()
            self._read_thread.start()

    def start_streaming(self):
        self._ensure_read_thread_running()

        if self._is_streaming.is_set():
            self._log.debug("Already streaming, start_streaming is noop")
            return True

        if not self._allocate_read_buffers():
            self._log.error(f"Couldn't allocate read buffers for streaming: {self._last_dcam_error_string()}")
            return False
        if not self._camera.cap_start():
            self._log.error(f"Failed to start streaming: {self._last_dcam_error_string()}")
            return False

        self._trigger_sent.clear()
        self._is_streaming.set()
        return True

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
        self._log.debug("Stopping Hamamatsu streaming.")
        success = True
        if not self._camera.cap_stop():
            self._log.error(f"Failed to stop camera streaming: {self._last_dcam_error_string()}")
            success = False

        if not self._camera.buf_release():
            self._log.error(f"Failed to release camera buffers: {self._last_dcam_error_string()}")
            success = False

        self._log.debug(f"Stopped with {success=}")
        self._trigger_sent.clear()
        self._is_streaming.clear()
        return success

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

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_black_level(self, black_level: float):
        raise NotImplementedError("Black levels are not implemented for the Hamamatsu driver.")

    def get_black_level(self) -> float:
        raise NotImplementedError("Black levels are not implemented for the Hamamatsu driver.")

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        with self._pause_streaming():
            if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.SOFTWARE
            elif acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.INTERNAL
            elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.EXTERNAL
                if not self._set_prop(DCAM_IDPROP.TRIGGERPOLARITY, DCAMPROP.TRIGGERPOLARITY.POSITIVE):
                    self._log.error(f"Failed to set positive trigger polarity for hardware trigger.")
                    return False
            else:
                raise ValueError(f"Unhandled {acquisition_mode=}")

            if not self._set_prop(DCAM_IDPROP.TRIGGERSOURCE, dcam_trigger_source):
                self._log.error(f"Failed to set acquisition mode to {acquisition_mode=}")
                return False
            self.set_exposure_time(self._exposure_time_ms)
        return True

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        dcam_mode_raw = self._camera.prop_getvalue(DCAM_IDPROP.TRIGGERSOURCE)

        if isinstance(dcam_mode_raw, bool):
            raise CameraError("Failed to get camera trigger source prop.")

        dcam_mode = int(dcam_mode_raw)

        if dcam_mode == DCAMPROP.TRIGGERSOURCE.EXTERNAL:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        elif dcam_mode == DCAMPROP.TRIGGERSOURCE.SOFTWARE:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif dcam_mode == DCAMPROP.TRIGGERSOURCE.INTERNAL:
            return CameraAcquisitionMode.CONTINUOUS
        else:
            raise ValueError(f"Unknown dcam trigger source mode {dcam_mode=}")

    def send_trigger(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise CameraError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_is_streaming():
            raise CameraError(f"Camera is not streaming, cannot send trigger.")

        if not self.get_ready_for_trigger():
            raise CameraError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            if not self._camera.cap_firetrigger():
                raise CameraError(f"Failed to send software trigger: {self._last_dcam_error_string()}")

            self._last_trigger_timestamp = time.time()
            self._trigger_sent.set()

    def get_ready_for_trigger(self) -> bool:
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        # Numbers are in unbinned pixels. Supports C15440-20UP (ORCA-Fusion BT) only.
        with self._pause_streaming():
            roi_mode_on = self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYMODE, DCAMPROP.MODE.ON)

            def fail(msg):
                """
                This is a helper for turning off roi mode if any of the sets below fail.
                """
                self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYMODE, DCAMPROP.MODE.OFF)
                raise ValueError(msg)

            if not roi_mode_on:
                raise CameraError("Failed to turn on roi mode on camera, cannot set roi.")

            offset_x = control.utils.truncate_to_interval(offset_x, 4)
            if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYHPOS, int(offset_x)):
                fail("Could not set roi x offset.")

            width = control.utils.truncate_to_interval(width, 4)
            if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYHSIZE, int(width)):
                fail("Could not set roi width.")

            offset_y = control.utils.truncate_to_interval(offset_y, 4)
            if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYVPOS, int(offset_y)):
                fail("Could not set roi y offset.")

            height = control.utils.truncate_to_interval(height, 4)
            if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYVSIZE, int(height)):
                fail("Could not set roi height.")

        # Force exposure + strobe delay recalculation if needed
        self.set_exposure_time(self.get_exposure_time())

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return (
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYHPOS)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYVPOS)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYHSIZE)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYVSIZE)),
        )

    def set_temperature(self, temperature_deg_c: Optional[float]):
        # Commented out since setting temperature is not supported in Model C15440-20UP (ORCA-Fusion BT)
        # self._camera.prop_setvalue(DCAM_IDPROP.SENSORTEMPERATURETARGET, temperature_deg_c)
        raise NotImplementedError("Setting temperature is not supported for this camera.")

    def get_temperature(self) -> float:
        return self._camera.prop_getvalue(DCAM_IDPROP.SENSORTEMPERATURE)

    def set_temperature_reading_callback(self, func) -> Callable[[float], None]:
        raise NotImplementedError("Setting temperature reading callback is not supported for this camera.")
