import time
from typing import Optional, Tuple, Sequence

import numpy as np
import pydantic

import control.utils
import squid.logging
from squid.abc import AbstractCamera, CameraAcquisitionMode, CameraGainRange, CameraFrameFormat, CameraFrame, CameraPixelFormat
import squid.config
from control._def import *

import threading
import control.toupcam as toupcam
from control.toupcam_exceptions import hresult_checker

log = squid.logging.get_logger(__name__)

class ToupCamCapabilities(pydantic.BaseModel):
    resolutions: Sequence[Tuple[int, int]]
    has_fan: bool
    has_TEC: bool
    has_low_noise_mode: bool
    has_black_level: bool


def get_sn_by_model(model_name):
    try:
        device_list = toupcam.Toupcam.EnumV2()
    except:
        log.error("Problem generating Toupcam device list")
        return None
    for dev in device_list:
        if dev.displayname == model_name:
            return dev.id
    return None  # return None if no device with the specified model_name is connected



class ToupcamCamera(AbstractCamera):

    def get_exposure_time(self) -> float:
        return self.camera.get_ExpoTime() / 1000.0  # microseconds -> milliseconds

    def get_exposure_limits(self) -> Tuple[float, float]:
        (min_exposure, max_exposure, default_exposure) = self.camera.get_ExpTimeRange()
        return min_exposure, max_exposure

    def get_strobe_time(self) -> float:
        return self._strobe_delay_us / 1000.0

    def set_frame_format(self, frame_format: CameraFrameFormat):
        pass

    def get_frame_format(self) -> CameraFrameFormat:
        pass

    def get_pixel_format(self) -> squid.config.CameraPixelFormat:
        pass

    def get_resolution(self) -> Tuple[int, int]:
        # TODO(imo): Should this be FinalSize to account for ROI?
        return self.camera.get_Size()

    def get_resolutions(self) -> Sequence[Tuple[int, int]]:
        return self._capabilities.resolutions

    def get_analog_gain(self) -> float:
        return self.camera.get_ExpoAGain()

    def get_gain_range(self) -> CameraGainRange:
        (min_gain, max_gain, default_gain) = self.camera.get_ExpoAGainRange()
        return min_gain, max_gain

    def _get_frame(self):
        pass

    def get_frame_id(self) -> int:
        pass

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        pass

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        pass

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        pass

    def get_black_level(self) -> float:
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")

        raw_black_level = self.camera.get_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL)

        return raw_black_level / self._get_black_level_factor()

    def set_black_level(self, black_level: float):
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")
        raw_black_level = black_level * self._get_black_level_factor()

        try:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL, raw_black_level)
        except toupcam.HRESULTException as ex:
            print("put blacklevel fail, hr=0x{:x}".format(ex.hr))

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        if acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            trigger_option_value = 0
        elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            trigger_option_value = 1
        elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            trigger_option_value =2
        else:
            raise ValueError(f"Do not know how to handle {acquisition_mode=}")
        self.camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, trigger_option_value)

        if acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            # select trigger source to GPIO0
            try:
                self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 1)
            except toupcam.HRESULTException as ex:
                error_type = hresult_checker(ex)
                self._log.exception("Unable to select trigger source: " + error_type)
                raise
            # set GPIO1 to trigger wait
            try:
                self.camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE, 0)
                self.camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER, 0)
            except toupcam.HRESULTException as ex:
                error_type = hresult_checker(ex)
                self._log.exception("Unable to set GPIO1 for trigger ready: " + error_type)
                raise

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        trigger_option_value = self.camera.get_Option(toupcam.TOUPCAM_OPTION_TRIGGER)
        if trigger_option_value == 0:
            return CameraAcquisitionMode.CONTINUOUS
        elif trigger_option_value == 1:
            raise CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif trigger_option_value == 2:
            raise CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            raise ValueError(f"Received unknown trigger option from toupcam: {trigger_option_value}")

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self.camera.get_Roi()

    @staticmethod
    def _event_callback(event_number, camera: ToupcamCamera):
        if event_number == toupcam.TOUPCAM_EVENT_IMAGE:
                camera._on_frame_callback()

    def _on_frame_callback(self):
        # If we're not streaming, ignore the image
        if not self.get_is_streaming():

        # get the image from the camera
        try:
            self.camera.PullImageV2(
                self.buf, self.pixel_size_byte * 8, None
            )  # the second camera is number of bits per pixel - ignored in RAW mode
        except toupcam.HRESULTException as ex:
            # TODO(imo): Propagate error in some way and handle
            self._log.error("pull image failed, hr=0x{:x}".format(ex.hr))

        # increament frame ID
        self.frame_ID += 1
        this_frame_id = self.frame_ID
        this_timestamp = time.time()

        # right now support the raw format only
        if self.data_format == "RGB":
            if self.pixel_format == "RGB24":
                # TODO(imo): Propagate error in some way and handle
                self._log.error("convert buffer to image not yet implemented for the RGB format")
            return
        else:
            if self.pixel_size_byte == 1:
                raw_image = np.frombuffer(self.buf, dtype="uint8")
            elif self.pixel_size_byte == 2:
                raw_image = np.frombuffer(self.buf, dtype="uint16")
            self.camera
            self.current_frame = raw_image.reshape()

        self.image_is_ready = True

        if self.callback_is_enabled == True:
            self.new_image_callback_external(self)

    @staticmethod
    def _tdib_width_bytes(w):
        return (w * 24 + 31) // 32 * 4

    def __init__(self, config: squid.config.CameraConfig, hw_trigger_fn, hw_set_strobe_delay_ms_fn):
        super().__init__(config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        # many to be purged
        self.camera: Optional[toupcam.Toupcam] = None

        self.analog_gain = 0
        self.frame_ID = -1
        self.timestamp = 0

        self._pixel_format = CameraPixelFormat.MONO8

        # below are values for IMX226 (MER2-1220-32U3M) - to make configurable
        self.row_period_us = 10
        self.row_numbers = 3036
        self.exposure_delay_us_8bit = 650
        self.exposure_delay_us = self.exposure_delay_us_8bit * self._pixel_size()

        # just setting a default value
        # it would be re-calculate with function calculate_hardware_trigger_arguments
        self._strobe_delay_us = self.exposure_delay_us + self.row_period_us * self._pixel_size() * (
            self.row_numbers - 1
        )

        self._toupcam_pullmode_started = False
        (self._camera, self._capabilities) = ToupcamCamera._open(index=0)

        # toupcam temperature
        self.temperature_reading_callback = None
        self.terminate_read_temperature_thread = False
        self.thread_read_temperature = threading.Thread(target=self.check_temperature, daemon=True)
        self.thread_read_temperature.start()

    def check_temperature(self):
        while not self.terminate_read_temperature_thread:
            time.sleep(2)
            temperature = self.get_temperature()
            if self.temperature_reading_callback is not None:
                try:
                    self.temperature_reading_callback(temperature)
                except TypeError as ex:
                    self._log.error("Temperature read callback failed due to error: " + repr(ex))
                    pass

    @staticmethod
    def _open(index=None, sn=None) -> Tuple[toupcam.ToupcamDeviceV2, ToupCamCapabilities]:
        log = squid.logging.get_logger("ToupcamCamera._open")
        devices = toupcam.Toupcam.EnumV2()
        if len(devices) <= 0:
            raise ValueError("There are no Toupcam V2 devices.  Is the camera connected and powered on?")

        if index is not None and sn is not None:
            raise ValueError("You specified both a device index and a sn, this is not allowed.")

        if sn is not None:
            sn_matches = [idx for idx in range(len(devices)) if devices[idx].id == sn]
            if not len(sn_matches):
                all_sn = [d.id for d in devices]
                raise ValueError(f"Could not find camera with SN={sn}, options are: {','.join(all_sn)}")

        for (idx, device) in enumerate(devices):
            log.info(
                "Camera {}: {}: flag = {:#x}, preview = {}, still = {}".format(
                    idx,
                    device.displayname,
                    device.model.flag,
                    device.model.preview,
                    device.model.still,
                )
            )

        for r in devices[index].model.res:
            log.info("\t = [{} x {}]".format(r.width, r.height))

        valid_resolutions = []
        for r in devices[index].model.res:
            valid_resolutions.append((r.width, r.height))

        camera = toupcam.Toupcam.Open(devices[index].id)
        capabilities = ToupCamCapabilities(
            resolutions=valid_resolutions,
            has_fan=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_FAN) > 0,
            has_TEC=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_TEC_ONOFF) > 0,
            has_low_noise_mode=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_LOW_NOISE) > 0,
            has_black_level=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_BLACKLEVEL) > 0)


        return camera, capabilities

    def _configure_camera(self):
        """
        Run our initial configuration to get the camera into a know and safe starting state.
        """
        if self._capabilities.has_low_noise_mode:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE, 0)

        # set temperature
        self._set_fan_speed(1)
        self.set_temperature(20)

        self.set_data_format(CameraFrameFormat.RAW)
        self.set_pixel_format(CameraPixelFormat.MONO16)  # 'MONO8'
        self.set_black_level(DEFAULT_BLACKLEVEL_VALUE)

        # set camera resolution
        self._update_buffer_settings()

        if self.camera:
            if self.buf:
                try:
                    self.camera.StartPullModeWithCallback(self._event_callback, self)
                except toupcam.HRESULTException as ex:
                    self._log.exception("failed to start camera, hr=0x{:x}".format(ex.hr))
                    raise ex
            self._toupcam_pullmode_started = True
        else:
            self._log.error("failed to open camera")
            raise RuntimeError("Couldn't open camera")
    def set_temperature_reading_callback(self, func):
        self.temperature_reading_callback = func

    def close(self):
        self.terminate_read_temperature_thread = True
        self.thread_read_temperature.join()
        self._set_fan_speed(0)
        self.camera.Close()
        self.camera = None
        self.buf = None
        self.last_raw_image = None
        self.last_converted_image = None
        self.last_numpy_image = None

    def set_exposure_time(self, exposure_time):
        # In the calls below, we need to make sure we convert to microseconds.
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self.camera.put_ExpoTime(int(exposure_time * 1000) + int(self._strobe_delay_us))
        else:
            self.camera.put_ExpoTime(int(exposure_time * 1000))

    def set_analog_gain(self, analog_gain):
        analog_gain = min(self.GAIN_MAX, analog_gain)
        analog_gain = max(self.GAIN_MIN, analog_gain)
        self.analog_gain = analog_gain
        # gain_min, gain_max, gain_default = self.camera.get_ExpoAGainRange() # remove from set_analog_gain
        # for touptek cameras gain is 100-10000 (for 1x - 100x)
        self.camera.put_ExpoAGain(int(100 * (10 ** (analog_gain / 20))))
        # self.camera.Gain.set(analog_gain)

    def get_auto_white_balance_gains(self):
        try:
            self.camera.AwbInit()
            return self.camera.get_WhiteBalanceGain()
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_NOTIMPL")
            self._log.warning("AWB not implemented")
            return (0, 0, 0)

    def set_white_balance_gains(self, wb_r=None, wb_g=None, wb_b=None):
        try:
            camera.put_WhiteBalanceGain(wb_r, wb_g, wb_b)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_NOTIMPL")
            self._log.warning("White balance not implemented")

    def start_streaming(self):
        if self.buf and (self._toupcam_pullmode_started == False):
            try:
                self.camera.StartPullModeWithCallback(self._event_callback, self)
                self._toupcam_pullmode_started = True
            except toupcam.HRESULTException as ex:
                self._log.exception("failed to start camera, hr: " + hresult_checker(ex))
                self.close()
                raise ex
        self._log.info("start streaming")
        self.is_streaming = True

    def stop_streaming(self):
        self.camera.Stop()
        self.is_streaming = False
        self._toupcam_pullmode_started = False

    def get_is_streaming(self):
        return self._toupcam_pullmode_started

    _BLACK_LEVEL_MAPPING = {
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO12): 16,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO14): 64,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO16): 256,
        # TODO(imo): We didn't set a black level factor if outside of 1 of the 4 options above, but still used the factor.  Is the mapping below correct, or is black level ignored for RGB?
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO12): 16,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO14): 64,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO16): 256,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB24): 1,  # Bit depth of 8 -> same as MONO8
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB32): 1, # Bit depth of 8 -> same as MONO8
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 256 # Bit depth of 16 -> same as MONO16
    }
    def _black_level_factor(self):
        frame_and_format = (self.get_frame_format(), self.get_pixel_format())
        if frame_and_format not in ToupcamCamera._BLACK_LEVEL_MAPPING:
            raise ValueError(f"Unknown combo for black level: {frame_and_format=}")

        return ToupcamCamera._BLACK_LEVEL_MAPPING[frame_and_format]

    _PIXEL_SIZE_MAPPING = {
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO12): 2,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO14): 2,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO16): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO12): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO14): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO16): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB24): 3,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB32): 4,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 6
    }
    def _pixel_size(self):
        frame_and_format = (self.get_frame_format(), self.get_pixel_format())
        if frame_and_format not in ToupcamCamera._PIXEL_SIZE_MAPPING:
            raise ValueError(f"Unknown combo for pixel size: {frame_and_format=}")

        return ToupcamCamera._PIXEL_SIZE_MAPPING[frame_and_format]

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        if self.get_frame_format() == CameraFrameFormat.RAW:
            if pixel_format == CameraPixelFormat.MONO8:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
            elif pixel_format == CameraPixelFormat.MONO12:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO14:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO16:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
        else:
            # RGB data format
            if pixel_format == CameraPixelFormat.MONO8:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 3)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO12:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO14:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO16:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.RGB24:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 0)
            if pixel_format == CameraPixelFormat.RGB32:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 2)
            if pixel_format == CameraPixelFormat.RGB48:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 1)

        # NOTE(imo): Ideally we'd query pixel_format from the device instead of storing the state here, but it's
        # impossible to do so - the settings for a particular depth are not unique.  EG MONO12 and MONO14 both
        # have the same settings.  I'm not sure how this works?  But just store the pixel format here...
        self._pixel_format = pixel_format

        self._update_internal_settings()

        if was_streaming:
            self.start_streaming()

    def _update_internal_settings(self):
        """
        This needs to be called when a camera side setting changes that needs a:
          * read buffer size update
          * strobe delay recalc
        """
        # resize the buffer
        _, _, width, height = self.camera.get_Roi()

        # calculate buffer size
        if self.data_format == "RGB" and self.pixel_size_byte != 4:
            bufsize = ToupcamCamera._tdib_width_bytes(width * self.pixel_size_byte * 8) * height
        else:
            bufsize = width * self.pixel_size_byte * height
        self._log.info(f"image size: {width=} x {height=}, {bufsize=}")
        # create the buffer
        self.buf = bytes(bufsize)

        self._strobe_delay_us = ToupcamCamera._calculate_strobe_delay_us(self.camera, self._pixel_size(), self._capabilities)

    def get_pixel_format(self) -> CameraPixelFormat:
        return self._pixel_format

    def set_auto_exposure(self, enabled):
        try:
            self.camera.put_AutoExpoEnable(enabled)
        except toupcam.HRESULTException as ex:
            self._log.error("Unable to set auto exposure: " + repr(ex))
            # TODO(imo): Propagate error in some way and handle

    def set_data_format(self, data_format: CameraFrameFormat):
        self.data_format = data_format
        if data_format == "RGB":
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_RAW, 0)  # 0 is RGB mode, 1 is RAW mode
        elif data_format == "RAW":
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_RAW, 1)  # 1 is RAW mode, 0 is RGB mode

    def set_resolution(self, width, height):
        was_streaming = False
        if self.is_streaming:
            self.stop_streaming()
            was_streaming = True
        try:
            self.camera.put_Size(width, height)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_INVALIDARG", "E_BUSY", "E_ACCESDENIED", "E_UNEXPECTED")
            if err_type == "E_INVALIDARG":
                self._log.error(f"Resolution ({width},{height}) not supported by camera")
            else:
                self._log.error(f"Resolution cannot be set due to error: " + err_type)
                # TODO(imo): Propagate error in some way and handle
        self._update_buffer_settings()
        if was_streaming:
            self.start_streaming()

        if self.reset_strobe_delay is not None:
            self.reset_strobe_delay()

    def get_temperature(self):
        try:
            return self.camera.get_Temperature() / 10
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.debug("Could not get temperature, error: " + error_type)
            # TODO(imo): Returning 0 temp here seems dangerous - probably indicate instead that we don't know the temp
            return 0

    def set_temperature(self, temperature):
        try:
            self.camera.put_Temperature(int(temperature * 10))
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            # TODO(imo): Propagate error in some way and handle
            self._log.error("Unable to set temperature: " + error_type)

    def _set_fan_speed(self, speed):
        try:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_FAN, speed)
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            # TODO(imo): Propagate error in some way and handle
            self._log.exception("Unable to set fan speed: " + error_type)
            raise

    def _set_trigger_width_mode(self):
        self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1)  # set PWM source to GPIO0
        self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4)  # trigger source to PWM

    def _set_gain_mode(self, mode):
        if mode == "LCG":
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 0)
        elif mode == "HCG":
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 1)
        elif mode == "HDR":
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 2)

    def send_trigger(self):
        if self._last_software_trigger_timestamp != None:
            if (time.time() - self._last_software_trigger_timestamp) > (1.5 * self.exposure_time / 1000 * 1.02 + 4):
                self._log.warning("last software trigger timed out")
                self._software_trigger_sent = False
        if self.is_streaming and (self._software_trigger_sent == False):
            self.camera.Trigger(1)
            self._software_trigger_sent = True
            self._last_software_trigger_timestamp = time.time()
            self._log.debug(">>> trigger sent")
        else:
            # TODO(imo): Propagate these errors in some way and handle
            if self.is_streaming == False:
                self._logger.error("trigger not sent - camera is not streaming")
            else:
                pass

    def _stop_exposure(self):
        if self.is_streaming and self._software_trigger_sent == True:
            self.camera.Trigger(0)
            self._software_trigger_sent = False
        else:
            pass

    def read_frame(self, reset_image_ready_flag=True):
        # set reset_image_ready_flag to True when read_frame() is called immediately after triggering the acquisition
        if reset_image_ready_flag:
            self.image_is_ready = False
        timestamp_t0 = time.time()
        while (time.time() - timestamp_t0) <= (self.exposure_time / 1000) * 1.02 + 4:
            time.sleep(0.005)
            if self.image_is_ready:
                self.image_is_ready = False
                return self.current_frame
        self._log.error("read frame timed out")
        return None

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        roi_offset_x = control.utils.truncate_to_interval(offset_x, 2)
        roi_offset_y = control.utils.truncate_to_interval(offset_y, 2)
        roi_width = control.utils.truncate_to_interval(width, 2)
        roi_height = control.utils.truncate_to_interval(height, 2)

        was_streaming = False
        if self.is_streaming:
            self.stop_streaming()
            was_streaming = True

        try:
            self.camera.put_Roi(roi_offset_x, roi_offset_y, roi_width, roi_height)
        except toupcam.HRESULTException as ex:
            self._log.exception("ROI bounds invalid, not changing ROI.")

        self._update_buffer_settings()
        if was_streaming:
            self.start_streaming()

    @staticmethod
    def _calculate_strobe_delay_us(camera: toupcam.Toupcam, pixel_size: int, capabilities: ToupCamCapabilities) -> float:
        log = squid.logging.get_logger("ToupcamCamera._calculate_strobe_delay")
        # use camera arguments such as resolutuon, ROI, exposure time, set max FPS, bandwidth to calculate the trigger delay time

        pixel_bits = pixel_size * 8
        line_length = 0
        low_noise = 0

        try:
            resolution_width, resolution_height = camera.get_Size()
        except toupcam.HRESULTException as ex:
            log.exception("get resolution fail, hr=0x{:x}".format(ex.hr))
            raise

        xoffset, yoffset, roi_width, roi_height = camera.get_Roi()

        try:
            bandwidth = camera.get_Option(toupcam.TOUPCAM_OPTION_BANDWIDTH)
        except toupcam.HRESULTException as ex:
            log.exception("get badwidth fail, hr=0x{:x}".format(ex.hr))
            raise

        if capabilities.has_low_noise_mode:
            try:
                low_noise = camera.get_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE)
            except toupcam.HRESULTException as ex:
                log.exception("get low_noise fail, hr=0x{:x}".format(ex.hr))

        if resolution_width == 6224 and resolution_height == 4168:
            if pixel_bits == 8:
                line_length = 1200 * (roi_width / 6224)
                if line_length < 450:
                    line_length = 450
            elif pixel_bits == 16:
                if low_noise == 1:
                    line_length = 5000
                elif low_noise == 0:
                    line_length = 2500
        elif resolution_width == 3104 and resolution_height == 2084:
            if pixel_bits == 8:
                line_length = 906
            elif pixel_bits == 16:
                line_length = 1200
        elif resolution_width == 2064 and resolution_height == 1386:
            if pixel_bits == 8:
                line_length = 454
            elif pixel_bits == 16:
                line_length = 790

        line_length = int(line_length / (bandwidth / 100.0))
        row_time = line_length / 72

        try:
            max_framerate_tenths_fps = camera.get_Option(toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE)
        except toupcam.HRESULTException as ex:
            log.error("get max_framerate fail, hr=0x{:x}".format(ex.hr))
            raise

        # need reset value, because the default value is only 90% of setting value
        try:
            camera.put_Option(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, max_framerate_tenths_fps)
        except toupcam.HRESULTException as ex:
            log.error("put max_framerate fail, hr=0x{:x}".format(ex.hr))
            raise

        max_framerate_fps = max_framerate_tenths_fps / 10.0

        vheight = 72000000 / (max_framerate_fps * line_length)
        if vheight < roi_height + 56:
            vheight = roi_height + 56

        strobe_time = int(vheight * row_time)

        return strobe_time
