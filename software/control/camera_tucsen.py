from ctypes import *
import numpy as np
import threading
import time
from typing import Optional, Callable, Sequence, Tuple, Dict

import pydantic

from squid.abc import AbstractCamera, CameraError
from squid.config import CameraConfig, CameraPixelFormat, TucsenCameraModel
from squid.abc import CameraFrame, CameraFrameFormat, CameraAcquisitionMode, CameraGainRange
import squid.logging
from control.TUCam import *
import control.utils
from control._def import *


class Mode400BSIV3(Enum):
    """
    HDR is the default gain mode of 400BSI V3 camera.
    Store setting values for (TUCIDC_IMGMODESELECT, TUCIDP_GLOBALGAIN) here
    Other combinations of image mode and gain mode are possible, but we don't support them yet.
    """

    HDR = (2, 0)  # 16bit
    CMS = (1, 0)  # 12bit
    HIGH_SPEED = (3, 1)  # 11bit


class ModeFL26BW(Enum):
    # TODO: Add support for FL26BW model
    """
    FL26BW modes values are a combination of image mode and binning.
    Store setting values for (TUCIDC_IMGMODESELECT, TUIDC_RESOLUTION) here
    """
    STANDARD = (0, 0)
    LOW_NOISE = (1, 0)
    SENBIN = (0, 1)


class TucsenCamera(AbstractCamera):

    _MODE_TO_LINE_RATE_US = {
        Mode400BSIV3.HDR: 11.2,
        Mode400BSIV3.CMS: 11.2,
        Mode400BSIV3.HIGH_SPEED: 7.2,
        ModeFL26BW.STANDARD: 34.67,
        ModeFL26BW.LOW_NOISE: 69.3,
        ModeFL26BW.SENBIN: 12.58,
    }

    _MODEL_TO_PIXEL_SIZE_UM = {
        TucsenCameraModel.DHYANA_400BSI_V3: 6.5,
        TucsenCameraModel.FL26_BW: 3.76,
    }

    @staticmethod
    def _get_sn_by_model(camera_model: TucsenCameraModel) -> str:
        TUCAMINIT = TUCAM_INIT(0, "./".encode("utf-8"))
        TUCAM_Api_Init(pointer(TUCAMINIT))

        for i in range(TUCAMINIT.uiCamCount):
            TUCAMOPEN = TUCAM_OPEN(i, 0)
            TUCAM_Dev_Open(pointer(TUCAMOPEN))
            TUCAMVALUEINFO = TUCAM_VALUE_INFO(TUCAM_IDINFO.TUIDI_CAMERA_MODEL.value, 0, 0, 0)
            TUCAM_Dev_GetInfo(TUCAMOPEN.hIdxTUCam, pointer(TUCAMVALUEINFO))
            if TUCAMVALUEINFO.pText == camera_model.value:
                sn = TucsenCamera._read_camera_sn(TUCAMOPEN.hIdxTUCam)
                TUCAM_Dev_Close(TUCAMOPEN.hIdxTUCam)
                TUCAM_Api_Uninit()
                return sn

            TUCAM_Dev_Close(TUCAMOPEN.hIdxTUCam)

        TUCAM_Api_Uninit()
        return None

    @staticmethod
    def _read_camera_sn(camera_handle: c_void_p) -> str:
        cSN = (c_char * 64)()
        pSN = cast(cSN, c_char_p)
        TUCAMREGRW = TUCAM_REG_RW(1, pSN, 64)
        TUSDKdll.TUCAM_Reg_Read(camera_handle, TUCAMREGRW)
        sn = string_at(pSN).decode("utf-8")
        return sn

    @staticmethod
    def _open(index: Optional[int] = None, sn: Optional[str] = None) -> c_void_p:
        log = squid.logging.get_logger("TucsenCamera._open")

        if index is None and sn is None:
            raise ValueError("You must specify one of either index or sn.")
        elif index is not None and sn is not None:
            raise ValueError("You must specify only 1 of index or sn")

        TUCAMINIT = TUCAM_INIT(0, "./control".encode("utf-8"))
        TUCAM_Api_Init(pointer(TUCAMINIT))
        log.info(f"Connect {TUCAMINIT.uiCamCount} camera(s)")

        if index >= TUCAMINIT.uiCamCount:
            raise CameraError("Camera index out of range. Is the camera connected?")

        if sn is not None:
            for i in range(TUCAMINIT.uiCamCount):
                # We have to open each camera to read the serial number
                TUCAMOPEN = TUCAM_OPEN(i, 0)
                TUCAM_Dev_Open(pointer(TUCAMOPEN))

                if TucsenCamera._read_camera_sn(TUCAMOPEN.hIdxTUCam) == sn:
                    index = i
                    break
                else:
                    TUCAM_Dev_Close(TUCAMOPEN.hIdxTUCam)
            TUCAM_Api_Uninit()
            raise CameraError(f"Camera with serial number {sn} not found")
        else:
            TUCAMOPEN = TUCAM_OPEN(index, 0)
            TUCAM_Dev_Open(pointer(TUCAMOPEN))

        if TUCAMOPEN.hIdxTUCam == 0:
            raise CameraError("Open Tucsen camera failure!")
        else:
            log.info("Open Tucsen camera success!")

        return TUCAMOPEN.hIdxTUCam  # TODO: return model specific properties after we support more models

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        # TODO: Open camera by model (We don't need it for Tucsen camera right now)

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
        self._is_streaming = threading.Event()

        self._camera = TucsenCamera._open(index=0)
        self._binning = self._config.default_binning
        if self._config.camera_model == TucsenCameraModel.FL26_BW:
            self._camera_mode = ModeFL26BW.STANDARD if self._config.default_binning == (1, 1) else ModeFL26BW.SENBIN
            # Low noise mode is not supported for FL26BW model yet.
        else:
            self._camera_mode = Mode400BSIV3.HDR  # HDR as default

        self._m_frame = None  # image buffer
        # We need to keep trigger attribute for starting and stopping streaming
        self._trigger_attr = TUCAM_TRIGGER_ATTR()

        self._configure_camera()

        # We store exposure time so we don't need to worry about backing out strobe time from the
        # time stored on the camera.
        #
        # We just set it to some sane value to start.
        self._exposure_time_ms: int = 20

        self.temperature_reading_callback = None
        self._terminate_temperature_event = threading.Event()
        self.temperature_reading_thread = threading.Thread(target=self._check_temperature, daemon=True)
        self.temperature_reading_thread.start()

    def _configure_camera(self):
        # TODO: Add support for FL26BW model
        # TODO: For 400BSI V3, we use the default HDR mode for now.
        self.set_temperature(self._config.default_temperature)
        self.set_binning(*self._config.default_binning)
        # TODO: Set default roi

    def start_streaming(self):
        if self._is_streaming.is_set():
            self._log.debug("Already streaming, start_streaming is noop")
            return

        if self._m_frame is None:
            self._allocate_buffer()

        if TUCAM_Cap_Start(self._camera, self._trigger_attr.nTgrMode) != TUCAMRET.TUCAMRET_SUCCESS:
            TUCAM_Buf_Release(self._camera)
            raise CameraError("Failed to start streaming")

        self._ensure_read_thread_running()

        self._trigger_sent.clear()
        self._is_streaming.set()
        self._log.info("TUCam Camera starts streaming")

    def _allocate_buffer(self):
        self._m_frame = TUCAM_FRAME()
        self._m_frame.pBuffer = 0
        self._m_frame.ucFormatGet = TUFRM_FORMATS.TUFRM_FMT_USUAl.value
        self._m_frame.uiRsdSize = 1

        if TUCAM_Buf_Alloc(self._camera, pointer(self._m_frame)) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to allocate buffer")

    def stop_streaming(self):
        if not self._is_streaming.is_set():
            self._log.debug("Already stopped, stop_streaming is noop")
            return

        self._cleanup_read_thread()

        if TUCAM_Cap_Stop(self._camera) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to stop streaming")

        self._trigger_sent.clear()
        self._is_streaming.clear()
        self._log.info("TUCam Camera streaming stopped")

    def get_is_streaming(self):
        return self._is_streaming.is_set()

    def close(self):
        if self.temperature_reading_thread is not None:
            self._terminate_temperature_event.set()
            self.temperature_reading_thread.join()
        if TUCAM_Dev_Close(self._camera) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to close camera")
        TUCAM_Api_Uninit()
        self._log.info("Close Tucsen camera success")

    def _ensure_read_thread_running(self):
        with self._read_thread_lock:
            if self._read_thread is not None and self._read_thread_running.is_set():
                self._log.debug("Read thread exists and thread is marked as running.")
                return True

            elif self._read_thread is not None:
                self._log.warning("Read thread already exists, but not marked as running.  Still attempting start.")

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

            if TUCAM_Buf_AbortWait(self._camera) != TUCAMRET.TUCAMRET_SUCCESS:
                self._log.error("Failed to abort wait for frame")

            self._read_thread.join(1.1 * self._read_thread_wait_period_s)

            success = not self._read_thread.is_alive()
            if not success:
                self._log.warning("Read thread refused to exit!")

            self._read_thread = None
            self._read_thread_running.clear()

    def _wait_for_frame(self):
        self._log.info("Starting Tucsen read thread.")
        self._read_thread_running.set()
        while self._read_thread_keep_running.is_set():
            try:
                wait_time_ms = int(self._read_thread_wait_period_s * 1000)  # ms, convert to int
                try:
                    TUCAM_Buf_WaitForFrame(self._camera, pointer(self._m_frame), c_int32(wait_time_ms))
                except Exception:
                    pass

                if self._m_frame is None or self._m_frame.pBuffer is None or self._m_frame.pBuffer == 0:
                    self._log.error("Invalid frame buffer")
                    continue

                np_image = self._convert_frame_to_numpy(self._m_frame)

                processed_frame = self._process_raw_frame(np_image)
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
                self._log.exception(f"Exception: {e} in read loop, ignoring and trying to continue.")
        self._read_thread_running.clear()

    def _convert_frame_to_numpy(self, frame: TUCAM_FRAME) -> np.ndarray:
        # TODO: In the latest version of 400BSI V3, the readout data will match the actual bit depth.
        # We are not able to tell the firmware version from SN yet. Need to figure out if it's safe to assume
        # all users have the latest firmware. We use 16-bit buffer for the old demo units for now.
        buf = create_string_buffer(frame.uiImgSize)
        pointer_data = c_void_p(frame.pBuffer + frame.usHeader)
        memmove(buf, pointer_data, frame.uiImgSize)

        data = bytes(buf)
        image_np = np.frombuffer(data, dtype=np.uint16)
        image_np = image_np.reshape((frame.usHeight, frame.usWidth))

        return image_np

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
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            strobe_time_ms = self.get_strobe_time()
            adjusted_exposure_time = exposure_time_ms + strobe_time_ms
            if self._hw_set_strobe_delay_ms_fn:
                self._log.debug(f"Setting hw strobe time to {strobe_time_ms} [ms]")
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)
        else:
            adjusted_exposure_time = exposure_time_ms

        if (
            TUCAM_Prop_SetValue(self._camera, TUCAM_IDPROP.TUIDP_EXPOSURETM.value, c_double(adjusted_exposure_time), 0)
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            raise CameraError("Failed to set exposure time")

        self._exposure_time_ms = exposure_time_ms
        self._trigger_sent.clear()

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        prop = TUCAM_PROP_ATTR()
        prop.idProp = TUCAM_IDPROP.TUIDP_EXPOSURETM.value
        prop.nIdxChn = 0
        if TUCAM_Prop_GetAttr(self._camera, pointer(prop)) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to get exposure time limits")
        self._log.info(f"Exposure limits: {prop.dbValMin}, {prop.dbValMax}")
        return prop.dbValMin, prop.dbValMax

    def _calculate_strobe_delay(self):
        # Line rate: FL 26BW: 34.67 us for standard resolution; 69.3 us for low noise; 12.58 us for SenBin
        #            400BSI V3: 7.2 us for high speed; 11.2 us for other gain modes
        # Right now we are only using 400BSI V3's HDR mode.
        # TODO: Support more modes.
        _, _, _, height = self.get_region_of_interest()
        readout_time_ms = TucsenCamera._MODE_TO_LINE_RATE_US[self._camera_mode] * height * self._binning[1] / 1000.0

        trigger_attr = TUCAM_TRIGGER_ATTR()
        if TUCAM_Cap_GetTrigger(self._camera, pointer(trigger_attr)) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to get trigger delay")
        trigger_delay_ms = trigger_attr.nDelayTm

        self._strobe_delay_ms = readout_time_ms + trigger_delay_ms

    def get_strobe_time(self) -> float:
        return self._strobe_delay_ms

    def set_frame_format(self, frame_format: CameraFrameFormat):
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by this camera.")

    def get_frame_format(self) -> CameraFrameFormat:
        return CameraFrameFormat.RAW

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        # TODO: This is temporary before we move to support the new version of 400BSI V3 hardware and FL26BW model.
        if pixel_format != CameraPixelFormat.MONO16:
            raise ValueError(f"Pixel format {pixel_format} is not supported by this camera.")

    def get_pixel_format(self) -> CameraPixelFormat:
        # TODO: This is temporary before we move to support the new version of 400BSI V3 hardware and FL26BW model.
        return CameraPixelFormat.MONO16

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return [CameraPixelFormat.MONO16]

    def _update_internal_settings(self):
        if TUCAM_Buf_Release(self._camera) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to release buffer")
        self._allocate_buffer()
        self._calculate_strobe_delay()

    _BINNING_TO_BINNING_CODE_400BSIV3 = {
        (1, 1): 0,
        (2, 2): 2,
        (4, 4): 3,
    }

    _BINNING_CODE_TO_RESOLUTION_400BSIV3 = {
        0: (2048, 2048),
        # 1: (2048, 2048),  # Code 1 is enhance mode, which will modify pixel values. We don't use it.
        2: (1024, 1024),
        3: (512, 512),
    }

    def _raw_set_resolution(self, bin_value: int):
        with self._pause_streaming():
            if (
                TUCAM_Capa_SetValue(self._camera, TUCAM_IDCAPA.TUIDC_RESOLUTION.value, c_int(bin_value))
                != TUCAMRET.TUCAMRET_SUCCESS
            ):
                raise CameraError("Cannot set camera binning.")
            if self._config.camera_model == TucsenCameraModel.FL26_BW:
                self._camera_mode = ModeFL26BW.STANDARD if bin_value == 0 else ModeFL26BW.SENBIN
            self._update_internal_settings()

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        # TODO: Add support for FL26BW model
        if not (binning_factor_x, binning_factor_y) in TucsenCamera._BINNING_TO_BINNING_CODE_400BSIV3:
            raise CameraError(f"No binning option exists for {binning_factor_x}x{binning_factor_y}")
        self._raw_set_resolution(TucsenCamera._BINNING_TO_BINNING_CODE_400BSIV3[(binning_factor_x, binning_factor_y)])
        self._binning = (binning_factor_x, binning_factor_y)

    def get_binning(self) -> Tuple[int, int]:
        return self._binning

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        # TODO: Add support for FL26BW model
        return TucsenCamera._BINNING_TO_BINNING_CODE_400BSIV3.keys()

    def get_resolution(self) -> Tuple[int, int]:
        # TODO: Add support for FL26BW model
        idx = c_int(0)
        if (
            TUCAM_Capa_GetValue(self._camera, TUCAM_IDCAPA.TUIDC_RESOLUTION.value, pointer(idx))
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            raise CameraError("Failed to get resolution")
        return TucsenCamera._BINNING_CODE_TO_RESOLUTION_400BSIV3[idx.value]

    def get_pixel_size_unbinned_um(self) -> float:
        model = self._config.camera_model
        return TucsenCamera._MODEL_TO_PIXEL_SIZE_UM[model]

    def get_pixel_size_binned_um(self) -> float:
        return self.get_pixel_size_unbinned_um() * self.get_binning()[0]

    def set_analog_gain(self, analog_gain: float):
        if self._config.camera_model == TucsenCameraModel.FL26_BW:
            self._raw_set_analog_gain_fl26bw(analog_gain)
        else:
            raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_analog_gain(self) -> float:
        if self._config.camera_model == TucsenCameraModel.FL26_BW:
            return self._raw_get_analog_gain_fl26bw()
        else:
            raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_gain_range(self) -> CameraGainRange:
        if self._config.camera_model == TucsenCameraModel.FL26_BW:
            # These values are not accurate gain values. They are for selecting gain mode for FL26BW model.
            return CameraGainRange(min_gain=0, max_gain=3, gain_step=1)
        else:
            raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Tucsen driver.")

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        raise NotImplementedError("White Balance Gains not implemented for the Tucsen driver.")

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Tucsen driver.")

    def set_black_level(self, black_level: float):
        raise NotImplementedError("Black levels are not implemented for the Tucsen driver.")

    def get_black_level(self) -> float:
        raise NotImplementedError("Black levels are not implemented for the Tucsen driver.")

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        roi_attr = TUCAM_ROI_ATTR()
        roi_attr.bEnable = 1
        # These values must be a multiple of 4. When using 11bit mode, they must be a multiple of 32 (not supported yet).
        roi_attr.nHOffset = control.utils.truncate_to_interval(offset_x, 4)
        roi_attr.nVOffset = control.utils.truncate_to_interval(offset_y, 4)
        roi_attr.nWidth = control.utils.truncate_to_interval(width, 4)
        roi_attr.nHeight = control.utils.truncate_to_interval(height, 4)

        with self._pause_streaming():
            if TUCAM_Cap_SetROI(self._camera, roi_attr) != TUCAMRET.TUCAMRET_SUCCESS:
                raise CameraError(
                    f"Failed to set ROI: {roi_attr.nHOffset}, {roi_attr.nVOffset}, {roi_attr.nWidth}, {roi_attr.nHeight}"
                )
            self._update_internal_settings()

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        roi_attr = TUCAM_ROI_ATTR()
        if TUCAM_Cap_GetROI(self._camera, pointer(roi_attr)) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to get ROI")
        return (roi_attr.nHOffset, roi_attr.nVOffset, roi_attr.nWidth, roi_attr.nHeight)

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        self._log.debug(f"Setting acquisition mode to {acquisition_mode}")
        with self._pause_streaming():
            if TUCAM_Cap_GetTrigger(self._camera, pointer(self._trigger_attr)) != TUCAMRET.TUCAMRET_SUCCESS:
                raise CameraError("Failed to get trigger attributes")
            if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                self._trigger_attr.nTgrMode = TUCAM_CAPTURE_MODES.TUCCM_TRIGGER_SOFTWARE.value
            elif acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                self._trigger_attr.nTgrMode = TUCAM_CAPTURE_MODES.TUCCM_SEQUENCE.value
            elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                self._trigger_attr.nTgrMode = TUCAM_CAPTURE_MODES.TUCCM_TRIGGER_STANDARD.value
            else:
                raise ValueError(f"Unhandled {acquisition_mode=}")
            self._trigger_attr.nBufFrames = 1
            if TUCAM_Cap_SetTrigger(self._camera, self._trigger_attr) != TUCAMRET.TUCAMRET_SUCCESS:
                raise CameraError("Failed to set acquisition mode")
            self._update_internal_settings()
            self.set_exposure_time(self._exposure_time_ms)

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        trigger_attr = TUCAM_TRIGGER_ATTR()
        if TUCAM_Cap_GetTrigger(self._camera, pointer(trigger_attr)) != TUCAMRET.TUCAMRET_SUCCESS:
            raise CameraError("Failed to get acquisition mode")
        if trigger_attr.nTgrMode == TUCAM_CAPTURE_MODES.TUCCM_TRIGGER_SOFTWARE.value:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif trigger_attr.nTgrMode == TUCAM_CAPTURE_MODES.TUCCM_SEQUENCE.value:
            return CameraAcquisitionMode.CONTINUOUS
        elif trigger_attr.nTgrMode == TUCAM_CAPTURE_MODES.TUCCM_TRIGGER_STANDARD.value:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            raise ValueError(f"Unknown tucsen trigger source mode {trigger_attr.nTgrMode=}")

    def set_temperature_reading_callback(self, func: Callable):
        self.temperature_reading_callback = func

    def set_temperature(self, temperature: float):
        t = temperature * 10 + 500
        if (
            TUCAM_Prop_SetValue(self._camera, TUCAM_IDPROP.TUIDP_TEMPERATURE.value, c_double(t), 0)
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            self._log.exception(f"Failed to set temperature to {temperature}C")
            raise

    def get_temperature(self) -> float:
        t = c_double(0)
        if (
            TUCAM_Prop_GetValue(self._camera, TUCAM_IDPROP.TUIDP_TEMPERATURE.value, pointer(t), 0)
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            self._log.exception("Failed to get temperature")
            raise
        return t.value

    def _check_temperature(self):
        while not self._terminate_temperature_event.is_set():
            time.sleep(2)
            try:
                temperature = self.get_temperature()
                if self.temperature_reading_callback is not None:
                    try:
                        self.temperature_reading_callback(temperature)
                    except Exception as ex:
                        self._log.exception(f"Temperature read callback failed: {ex}")
                        pass
            except Exception as e:
                self._log.exception(f"Failed to read temperature in callback: {e}")
                pass

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
            TUCAM_Cap_DoSoftwareTrigger(self._camera)
            self._last_trigger_timestamp = time.time()
            self._trigger_sent.set()

    def get_ready_for_trigger(self) -> bool:
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def set_auto_exposure(self, enable=False):
        value = 1 if enable else 0
        TUCAM_Capa_SetValue(self._camera, TUCAM_IDCAPA.TUIDC_ATEXPOSURE.value, value)

        if enable:
            self.log.info("Auto exposure enabled")
        else:
            self.log.info("Auto exposure disabled")

    def _raw_set_analog_gain_fl26bw(self, gain: float):
        # For FL26BW model
        # Gain0: System Gain (DN/e-): 1.28; Full Well Capacity (e-): 49000; Readout Noise (e-): 2.7(Median), 3.3(RMS)
        # Gain1: System Gain (DN/e-): 3.98; Full Well Capacity (e-): 15700; Readout Noise (e-): 1.0(Median), 1.3(RMS)
        # Gain2: System Gain (DN/e-): 8.0; Full Well Capacity (e-): 7800; Readout Noise (e-): 0.95(Median), 1.2(RMS)
        # Gain3: System Gain (DN/e-): 20; Full Well Capacity (e-): 3000; Readout Noise (e-): 0.85(Median), 1.0(RMS)
        if (
            TUCAM_Prop_SetValue(self._camera, TUCAM_IDPROP.TUIDP_GLOBALGAIN.value, c_double(gain), 0)
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            raise CameraError("Failed to set analog gain")

    def _raw_get_analog_gain_fl26bw(self) -> float:
        # For FL26BW model
        gain_value = c_double(0)
        if (
            TUCAM_Prop_GetValue(self._camera, TUCAM_IDPROP.TUIDP_GLOBALGAIN.value, pointer(gain_value), 0)
            != TUCAMRET.TUCAMRET_SUCCESS
        ):
            raise CameraError("Failed to get analog gain")

        return gain_value.value
