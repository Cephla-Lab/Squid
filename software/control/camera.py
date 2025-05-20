import threading
from typing import Optional, Tuple, Sequence, Callable
import time
import pydantic

from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraFrame,
    CameraGainRange,
    CameraFrameFormat,
    CameraError,
)
from squid.config import CameraConfig, CameraPixelFormat, GxipyCameraModel, CameraSensor
from control._def import CAMERA_PIXEL_SIZE_UM

try:
    import control.gxipy as gx
except:
    print("gxipy import error")


class DefaultCameraCapabilities(pydantic.BaseModel):
    is_color: bool
    gettable_pixel_format: bool
    settable_pixel_format: bool
    settable_roi: bool
    black_level: bool
    white_balance: bool
    auto_white_balance: bool


def get_sn_by_model(camera_model: GxipyCameraModel):
    try:
        device_manager = gx.DeviceManager()
        device_num, device_info_list = device_manager.update_device_list()
    except:
        device_num = 0
    if device_num > 0:
        for i in range(device_num):
            if device_info_list[i]["model_name"] == camera_model.value:
                return device_info_list[i]["sn"]
    return None  # return None if no device with the specified model_name is connected


class DefaultCamera(AbstractCamera):
    @staticmethod
    def _open(device_manager: gx.DeviceManager, sn=None, index=None):
        if sn is None and index is None:
            raise ValueError("You must specify a serial number or index of camera to open.")

        device_num, device_info_list = device_manager.update_device_list()

        if device_num < 1:
            raise CameraError("No gxipy compatible cameras available.")

        if sn:
            camera = device_manager.open_device_by_sn(sn)
        else:
            # The device manager uses 1 index starting, but our convention is 0 index starting.
            camera = device_manager.open_device_by_index(index + 1)

        capabilities = DefaultCameraCapabilities(
            is_color=camera.PixelColorFilter.is_implemented(),
            gettable_pixel_format=camera.PixelFormat.is_readable(),
            settable_pixel_format=camera.PixelFormat.is_writable(),
            settable_roi=(
                camera.Width.is_writable()
                and camera.Height.is_writable()
                and camera.OffsetX.is_writable()
                and camera.OffsetY.is_writable()
            ),
            black_level=(camera.BlackLevel.is_implemented() and camera.BlackLevel.is_writable()),
            white_balance=(
                camera.BalanceRatio.is_implemented()
                and camera.BalanceRatio.is_writable()
                and camera.BalanceRatioSelector.is_implemented()
                and camera.BalanceRatioSelector.is_writable()
            ),
            auto_white_balance=(camera.BalanceWhiteAuto.is_implemented() and camera.BalanceWhiteAuto.is_writable()),
        )

        # NOTE(imo): In our previous driver, we did all these as defaults/prep to make things down the line work.
        # We do the same here, although we can probably remove some of them.
        camera.AcquisitionFrameRate.set(1000)
        camera.AcquisitionFrameRateMode.set(gx.GxSwitchEntry.ON)
        camera.DeviceLinkThroughputLimitMode.set(gx.GxSwitchEntry.OFF)

        return (camera, capabilities)

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        # If there are multiple Daheng cameras (default camera) connected, open the camera by model given in the config
        if self._config.camera_model is not None:
            sn = get_sn_by_model(self._config.camera_model)
            if sn is None:
                raise CameraError(f"Camera with model {self._config.camera_model} not found.")
            else:
                # We need to keep the device manager instance around because it also manages the gx library initialization
                # and de-initialization.  So we capture it here, but then never use it past the _open call.
                self._gx_device_manager = gx.DeviceManager()
                (self._camera, self._capabilities) = DefaultCamera._open(self._gx_device_manager, sn=sn)
        else:
            # If there is only one camera connected, open it by index
            self._gx_device_manager = gx.DeviceManager()
            (self._camera, self._capabilities) = DefaultCamera._open(self._gx_device_manager, index=0)

        # TODO/NOTE(imo): Need to test if self as user_param is correct here, of it sends self for us.
        self._camera.register_capture_callback(None, self._frame_callback)

        if self._config.default_white_balance_gains is not None and self._capabilities.white_balance:
            default_wb = self._config.default_white_balance_gains
            self.set_white_balance_gains(default_wb.r, default_wb.g, default_wb.b)

        # Since we might need to use a strobe delay, the value stored in the camera's driver can't be
        # used to back out the requested exposure time easily.  So we keep track of it ourselves.
        self._exposure_time_ms = 0
        self._strobe_delay_us = 0

        # Querying is slow on these devices, so we cache some properties.
        self._pixel_format: Optional[CameraPixelFormat] = None

        self._in_trigger = False
        self._last_trigger_timestamp = 0

        # To modify the current frame, you must hold the frame lock.
        self._frame_lock = threading.Lock()
        self._current_frame: Optional[CameraFrame] = None

    def __del__(self):
        try:
            if self._camera:
                self._camera.close_device()
        except AttributeError:
            # If init fails before we create the camera, we'll get here.  That's fine - just move along.
            pass

    def _frame_callback(self, unused_user_param, raw_image: gx.RawImage):
        with self._frame_lock:
            this_frame_id = (self._current_frame.frame_id if self._current_frame else 0) + 1
            this_timestamp = time.time()
            this_frame_format = self.get_frame_format()
            this_pixel_format = self.get_pixel_format()

            self._in_trigger = False
            if CameraPixelFormat.is_color_format(this_pixel_format):
                rgb_image = raw_image.convert("RGB")
                numpy_image = rgb_image.get_numpy_array()
                if this_pixel_format == CameraPixelFormat.BAYER_RG12:
                    numpy_image = numpy_image << 4
            else:
                numpy_image = raw_image.get_numpy_array()
                if this_pixel_format == CameraPixelFormat.MONO12:
                    numpy_image = numpy_image << 4

            processed_image = self._process_raw_frame(numpy_image)

            current_frame = CameraFrame(
                frame_id=this_frame_id,
                timestamp=this_timestamp,
                frame=processed_image,
                frame_format=this_frame_format,
                frame_pixel_format=this_pixel_format,
            )
            self._current_frame = current_frame

        # Propagate the local copy so we are sure it's the correct frame that goes out.
        self._propogate_frame(current_frame)

    @staticmethod
    def _get_pixel_size_bytes(pixel_format: CameraPixelFormat) -> int:
        if pixel_format == CameraPixelFormat.MONO8:
            return 1
        elif pixel_format == CameraPixelFormat.MONO12:
            return 2
        elif pixel_format == CameraPixelFormat.MONO14:
            return 2
        elif pixel_format == CameraPixelFormat.MONO16:
            return 2
        elif pixel_format == CameraPixelFormat.BAYER_RG8:
            return 1
        elif pixel_format == CameraPixelFormat.BAYER_RG12:
            return 2
        else:
            raise ValueError(f"No pixel byte size for format: {pixel_format=}")

    def _update_strobe_time(self):
        # NOTE(imo): This is just using defaults for the IMX226 (MER2-1220-32U3M) from the original camera
        # driver.  It should instead be configurable!
        exposure_delay_us_8bit = 650
        pixel_size_bytes = self._get_pixel_size_bytes(self.get_pixel_format())
        exposure_delay_us = pixel_size_bytes * exposure_delay_us_8bit
        exposure_time_us = 1000.0 * self._exposure_time_ms
        row_count = self.get_resolution()[1]  # TODO: this should be the row count after setting ROI
        row_period_us = 10

        self._strobe_delay_us = (
            exposure_delay_us + exposure_time_us + row_period_us * pixel_size_bytes * (row_count - 1) + 500
        )

        if self._hw_set_strobe_delay_ms_fn:
            self._hw_set_strobe_delay_ms_fn(self._strobe_delay_us / 1000.0)

    def set_exposure_time(self, exposure_time_ms: float):
        exposure_time_calculated_us = 1000.0 * exposure_time_ms
        if (
            self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER
            and not self._capabilities.is_global_shutter
        ):
            self._update_strobe_time()
            exposure_time_calculated_us += self._strobe_delay_us
        self._log.debug(
            f"Setting exposure time {exposure_time_calculated_us} [us] for exposure_time={exposure_time_ms * 1000} [us] and strobe={self._strobe_delay_us} [us]"
        )
        self._camera.ExposureTime.set(exposure_time_calculated_us)
        self._exposure_time_ms = exposure_time_ms

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        range_dict = self._camera.ExposureTime.get_range()
        return range_dict["min"] / 1000, range_dict["max"] / 1000

    def get_strobe_time(self) -> float:
        return self._strobe_delay_us / 1000.0

    _PIXEL_FORMAT_TO_FRAME_FORMAT = {
        CameraPixelFormat.MONO8: CameraFrameFormat.RAW,
        CameraPixelFormat.MONO10: CameraFrameFormat.RAW,
        CameraPixelFormat.MONO12: CameraFrameFormat.RAW,
        CameraPixelFormat.MONO16: CameraFrameFormat.RAW,
        CameraPixelFormat.BAYER_RG8: CameraFrameFormat.RGB,
        CameraPixelFormat.BAYER_RG12: CameraFrameFormat.RGB,
    }

    def set_frame_format(self, frame_format: CameraFrameFormat):
        current_pixel_format = self.get_pixel_format()
        if current_pixel_format not in DefaultCamera._PIXEL_FORMAT_TO_FRAME_FORMAT:
            raise ValueError(
                f"Something is really wrong, current pixel format is not mapped to a frame format: {current_pixel_format=}"
            )

        if frame_format != DefaultCamera._PIXEL_FORMAT_TO_FRAME_FORMAT[current_pixel_format]:
            raise ValueError(
                f"Frame format {frame_format=} not compatible with current pixel format {current_pixel_format=}"
            )

        # NOTE(imo): This is a weird one - we use an implied frame format for pixel formats in our default camera
        # implementation, so setting frame format really isn't a thing here.  But we let it pass as long as what
        # the caller is asking for matches the pixel format.

    def get_frame_format(self) -> CameraFrameFormat:
        current_pixel_format = self.get_pixel_format()
        if current_pixel_format not in DefaultCamera._PIXEL_FORMAT_TO_FRAME_FORMAT:
            raise ValueError(
                f"Something is really wrong, current pixel format {current_pixel_format=} does not have a frame format."
            )

        return DefaultCamera._PIXEL_FORMAT_TO_FRAME_FORMAT[current_pixel_format]

    _PIXEL_FORMAT_TO_GX_FORMAT = {
        CameraPixelFormat.MONO8: gx.GxPixelFormatEntry.MONO8,
        CameraPixelFormat.MONO10: gx.GxPixelFormatEntry.MONO10,
        CameraPixelFormat.MONO12: gx.GxPixelFormatEntry.MONO12,
        CameraPixelFormat.MONO14: gx.GxPixelFormatEntry.MONO14,
        CameraPixelFormat.MONO16: gx.GxPixelFormatEntry.MONO16,
        CameraPixelFormat.BAYER_RG8: gx.GxPixelFormatEntry.BAYER_RG8,
        CameraPixelFormat.BAYER_RG12: gx.GxPixelFormatEntry.BAYER_RG12,
    }

    @staticmethod
    def _gx_pixel_format_for(pixel_format: CameraPixelFormat):
        if pixel_format not in DefaultCamera._PIXEL_FORMAT_TO_GX_FORMAT:
            raise ValueError(f"No gx pixel format for {pixel_format=}")

        return DefaultCamera._PIXEL_FORMAT_TO_GX_FORMAT[pixel_format]

    @staticmethod
    def _pixel_format_for_gx_pixel(gx_pixel) -> CameraPixelFormat:
        for px, gx_for_px in DefaultCamera._PIXEL_FORMAT_TO_GX_FORMAT.items():
            if gx_for_px == gx_pixel:
                return px
        raise NotImplementedError(f"No pixel format for gx format {gx_pixel=}")

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        with self._pause_streaming():
            if not self._capabilities.settable_pixel_format:
                raise NotImplementedError("The camera does not support setting pixel format.")
            self._camera.PixelFormat.set(self._gx_pixel_format_for(pixel_format))
            self._pixel_format = pixel_format

        self._update_strobe_time()
        # For re-setting exposure time just in case the strobe changed.
        self.set_exposure_time(self.get_exposure_time())

    def get_pixel_format(self) -> CameraPixelFormat:
        if not self._capabilities.gettable_pixel_format:
            raise NotImplementedError("The camera does not support getting pixel format.")

        if self._pixel_format is None:
            (pixel_format_val, _) = self._camera.PixelFormat.get()
            self._pixel_format = self._pixel_format_for_gx_pixel(pixel_format_val)

        return self._pixel_format

    def get_resolution(self) -> Tuple[int, int]:
        return self._camera.WidthMax.get(), self._camera.HeightMax.get()

    def get_binning(self) -> Tuple[int, int]:
        return (1, 1)

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return [(1, 1)]

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        raise NotImplementedError("DefaultCameras do not support binning")

    _MODEL_TO_SENSOR = {
        GxipyCameraModel.MER2_1220_32U3M: CameraSensor.IMX226,
        GxipyCameraModel.MER2_630_60U3M: CameraSensor.IMX178,
    }

    def get_pixel_size_unbinned_um(self) -> float:
        if self._config.camera_model in DefaultCamera._MODEL_TO_SENSOR:
            return CAMERA_PIXEL_SIZE_UM[DefaultCamera._MODEL_TO_SENSOR[self._config.camera_model].value]
        else:
            raise NotImplementedError(f"No pixel size for {self._config.camera_model=}")

    def get_pixel_size_binned_um(self) -> float:
        # Right now binning for these cameras will always be 1x1
        if self._config.camera_model in DefaultCamera._MODEL_TO_SENSOR:
            return CAMERA_PIXEL_SIZE_UM[DefaultCamera._MODEL_TO_SENSOR[self._config.camera_model].value]
        else:
            raise NotImplementedError(f"No pixel size for {self._config.camera_model=}")

    def set_analog_gain(self, analog_gain: float):
        self._camera.Gain.set(analog_gain)

    def get_analog_gain(self) -> float:
        return self._camera.Gain.get()

    def get_gain_range(self) -> CameraGainRange:
        gain_range = self._camera.Gain.get_range()

        return CameraGainRange(min_gain=gain_range["min"], max_gain=gain_range["max"], gain_step=gain_range["inc"])

    def start_streaming(self):
        self._camera.stream_on()

    def stop_streaming(self):
        self._camera.stream_off()

    def get_is_streaming(self):
        # The gx camera implementation sets:
        #   self.data_stream[0].acquisition_flag = True
        # via the stream_on() and stream_off() calls, so we can check that (if it exists)
        if len(self._camera.data_stream) < 1:
            return False

        return self._camera.data_stream[0].acquisition_flag

    def read_camera_frame(self) -> Optional[CameraFrame]:
        self._log.debug("Entering read_camera_frame.")
        starting_frame_id = self.get_frame_id()
        if not self.get_is_streaming():
            self._log.warning("Cannot read frame if not streaming.")
            return None

        total_exposure_time_ms = self._exposure_time_ms + self._strobe_delay_us / 1000.0

        # If the last frame we got was from <exposure time ago, use it.
        if self._current_frame and time.time() - self._current_frame.timestamp <= total_exposure_time_ms / 1000.0:
            return self._current_frame

        # The camera api isn't really fast, so it is easy to time out waiting for a frame and its processing.  So
        # for the timeout, we add a flat 100 ms to account for that.
        timeout_period_s = (4 * total_exposure_time_ms + 100) / 1000.0
        timeout_time_s = time.time() + timeout_period_s

        while time.time() < timeout_time_s:
            if self.get_frame_id() != starting_frame_id:
                break
            time.sleep(0.001)

        with self._frame_lock:
            if self.get_frame_id() != starting_frame_id:
                return self._current_frame
            else:
                self._log.warning("Timed out waiting for frame")
                return None

    def get_frame_id(self) -> int:
        return self._current_frame.frame_id if self._current_frame else -1

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        if not self._capabilities.white_balance:
            raise NotImplementedError("Camera does not support white balance!")

        rgb_vals = []
        for idx in (0, 1, 2):  # r, g, b
            self._camera.BalanceRatioSelector.set(idx)
            rgb_vals.append(self._camera.BalanceRatio.get())

        return rgb_vals[0], rgb_vals[1], rgb_vals[2]

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        rgb_vals = (red_gain, green_gain, blue_gain)
        for idx in (0, 1, 2):  # r, g, b
            self._camera.BalanceRatioSelector.set(idx)
            self._camera.BalanceRatio.set(rgb_vals[idx])

    def set_auto_white_balance_gains(self, on: bool):
        if on:
            self._camera.BalanceWhiteAuto.set(gx.GxAutoEntry.CONTINUOUS)
        else:
            self._camera.BalanceWhiteAuto.set(gx.GxAutoEntry.OFF)

    def set_black_level(self, black_level: float):
        if not self._capabilities.black_level:
            raise NotImplementedError("Camera does not support black level")

        self._camera.BlackLevel.set(black_level)

    def get_black_level(self) -> float:
        if not self._capabilities.black_level:
            raise NotImplementedError("Camera does not support black level")

        return self._camera.BlackLevel.get()

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        if acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._camera.TriggerMode.set(gx.GxSwitchEntry.ON)
            self._camera.TriggerSource.set(gx.GxTriggerSourceEntry.LINE2)  # LINE0 requires 7 mA min
        elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._camera.TriggerMode.set(gx.GxSwitchEntry.ON)
            self._camera.TriggerSource.set(gx.GxTriggerSourceEntry.SOFTWARE)
        elif acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            self._camera.TriggerMode.set(gx.GxSwitchEntry.OFF)

        # Force re-calc of exposure time to account for strobe, etc.
        self.set_exposure_time(self.get_exposure_time())

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        (trigger_mode_val, _) = self._camera.TriggerMode.get()
        if trigger_mode_val == gx.GxSwitchEntry.ON:
            (trigger_source_val, _) = self._camera.TriggerSource.get()
            if trigger_source_val == gx.GxTriggerSourceEntry.SOFTWARE:
                return CameraAcquisitionMode.SOFTWARE_TRIGGER
            else:
                return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            return CameraAcquisitionMode.CONTINUOUS

    def send_trigger(self, illumination_time: Optional[float] = None):
        if not self.get_is_streaming():
            self._log.warning("Trigger requested, but not streaming. Skipping.")
            return

        current_acquisition_mode = self.get_acquisition_mode()
        self._last_trigger_timestamp = time.time()
        if current_acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_trigger_fn(illumination_time)
        elif current_acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._camera.TriggerSoftware.send_command()
        else:
            self._log.warning(f"Current acquisition mode {current_acquisition_mode=} not triggerable.")

    def get_ready_for_trigger(self) -> bool:
        time_since_last_s = time.time() - self._last_trigger_timestamp
        timeout_period_s = (4 * self._exposure_time_ms + 5) / 1000.0  # Arbitrary - how do we do somethigng smart here?
        if time_since_last_s > timeout_period_s and self._in_trigger:
            self._log.warning(f"It has been {time_since_last_s=}[s] since last trigger, timing it out.")
            self._in_trigger = False

        return not self._in_trigger

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        if not self._capabilities.settable_roi:
            raise NotImplementedError("Camera does not implement settable region of interest.")

        # NOTE: The camera restricts offsets/widths/etc based on what the other settings currently are, so you
        # can't just blindly set them.  If the offset is growing, you need to set the width first.  If the
        # offset is decreasing, you need to set the offset first.
        (existing_offset_x, existing_offset_y, existing_width, existing_height) = self.get_region_of_interest()

        with self._pause_streaming():
            if existing_offset_x < offset_x:
                self._camera.Width.set(width)
                self._camera.OffsetX.set(offset_x)
            else:
                self._camera.OffsetX.set(offset_x)
                self._camera.Width.set(width)

            if existing_offset_y < offset_y:
                self._camera.Height.set(height)
                self._camera.OffsetY.set(offset_y)
            else:
                self._camera.OffsetY.set(offset_y)
                self._camera.Height.set(height)

        updated_roi = self.get_region_of_interest()

        requested_roi = (offset_x, offset_y, width, height)

        if updated_roi != requested_roi:
            raise CameraError(
                f"After request to update roi to {requested_roi=}, new roi is {updated_roi=} instead.  Existing was {(existing_offset_x, existing_offset_y, existing_width, existing_height)}"
            )

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return (
            self._camera.OffsetX.get(),
            self._camera.OffsetY.get(),
            self._camera.Width.get(),
            self._camera.Height.get(),
        )

    def set_temperature(self, temperature_deg_c: Optional[float]):
        raise NotImplementedError("DefaultCameras do not support temperature control.")

    def get_temperature(self) -> float:
        raise NotImplementedError("DefaultCameras do not support getting current temperature")

    def set_temperature_reading_callback(self, callback: Callable):
        raise NotImplementedError("DefaultCameras do not support getting current temperature")
