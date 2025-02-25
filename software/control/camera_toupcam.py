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
        pass

    def get_exposure_limits(self) -> Tuple[float, float]:
        pass

    def get_strobe_time(self) -> float:
        pass

    def set_frame_format(self, frame_format: CameraFrameFormat):
        pass

    def get_frame_format(self) -> CameraFrameFormat:
        pass

    def get_pixel_format(self) -> squid.config.CameraPixelFormat:
        pass

    def get_resolution(self) -> Tuple[int, int]:
        pass

    def get_resolutions(self) -> Sequence[Tuple[int, int]]:
        pass

    def get_analog_gain(self) -> float:
        pass

    def get_gain_range(self) -> CameraGainRange:
        pass

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
        pass

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        pass

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        pass

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        pass

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

        self.exposure_time = 1  # unit: ms
        self.analog_gain = 0
        self.frame_ID = -1
        self.timestamp = 0

        self.pixel_size_byte = 1

        # below are values for IMX226 (MER2-1220-32U3M) - to make configurable
        self.row_period_us = 10
        self.row_numbers = 3036
        self.exposure_delay_us_8bit = 650
        self.exposure_delay_us = self.exposure_delay_us_8bit * self.pixel_size_byte

        # just setting a default value
        # it would be re-calculate with function calculate_hardware_trigger_arguments
        self.strobe_delay_us = self.exposure_delay_us + self.row_period_us * self.pixel_size_byte * (
            self.row_numbers - 1
        )

        # toupcam
        self.data_format: CameraFrameFormat = CameraFrameFormat.RAW
        self.image_is_ready = False
        self._toupcam_pullmode_started = False
        self._software_trigger_sent = False
        self._last_software_trigger_timestamp = None
        self.resolution = None
        # the balcklevel factor
        # 8 bits: 1
        # 10 bits: 4
        # 12 bits: 16
        # 14 bits: 64
        # 16 bits: 256
        self.blacklevel_factor = 1
        self._capabilities = ToupCamCapabilities(resolutions=[], has_fan=False, has_tec=False, has_low_noise_mode=False)

        # toupcam temperature
        self.temperature_reading_callback = None
        self.terminate_read_temperature_thread = False
        self.thread_read_temperature = threading.Thread(target=self.check_temperature, daemon=True)

        self.brand = "ToupTek"

        self._valid_resolutions = []

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

    def _open(self, index=0):
        devices = toupcam.Toupcam.EnumV2()
        if len(devices) <= 0:
            raise ValueError("There are no Toupcam V2 devices.  Is the camera connected and powered on?")

        for (idx, device) in devices:
            self._log.info(
                "{}: flag = {:#x}, preview = {}, still = {}".format(
                    self.devices[0].displayname,
                    self.devices[0].model.flag,
                    self.devices[0].model.preview,
                    self.devices[0].model.still,
                )
            )
        for r in self.devices[index].model.res:
            self._log.info("\t = [{} x {}]".format(r.width, r.height))
        if self.sn is not None:
            index = [idx for idx in range(len(self.devices)) if self.devices[idx].id == self.sn][0]
        highest_res = (0, 0)
        self._valid_resolutions = []
        for r in self.devices[index].model.res:
            self._valid_resolutions.append((r.width, r.height))
            if r.width > highest_res[0] or r.height > highest_res[1]:
                highest_res = (r.width, r.height)
        self.camera = toupcam.Toupcam.Open(self.devices[index].id)
        self.has_fan = (self.devices[index].model.flag & toupcam.TOUPCAM_FLAG_FAN) > 0
        self.has_TEC = (self.devices[index].model.flag & toupcam.TOUPCAM_FLAG_TEC_ONOFF) > 0
        self.has_low_noise_mode = (self.devices[index].model.flag & toupcam.TOUPCAM_FLAG_LOW_NOISE) > 0
        if self.has_low_noise_mode:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE, 0)

        # RGB format: The output of every pixel contains 3 componants which stand for R/G/B value respectively. This output is a processed output from the internal color processing engine.
        # RAW format: In this format, the output is the raw data directly output from the sensor. The RAW format is for the users that want to skip the internal color processing and obtain the raw data for user-specific purpose. With the raw format output enabled, the functions that are related to the internal color processing will not work, such as Toupcam_put_Hue or Toupcam_AwbOnce function and so on

        # set temperature
        self.set_fan_speed(1)
        self.set_temperature(20)

        self.set_data_format(CameraFrameFormat.RAW)
        self.set_pixel_format("MONO16")  # 'MONO8'
        self.set_auto_exposure(False)
        self.set_blacklevel(DEFAULT_BLACKLEVEL_VALUE)

        # set resolution to full if resolution is not specified or not in the list of supported resolutions
        if self.resolution is None:
            self.resolution = highest_res
        elif self.resolution not in self.res_list:
            self.resolution = highest_res

        # set camera resolution
        self.set_resolution(self.resolution[0], self.resolution[1])  # buffer created when setting resolution
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
    else:
        self._log.error("no camera found")

    self.thread_read_temperature.start()

    def set_temperature_reading_callback(self, func):
        self.temperature_reading_callback = func

    def close(self):
        self.terminate_read_temperature_thread = True
        self.thread_read_temperature.join()
        self.set_fan_speed(0)
        self.camera.Close()
        self.camera = None
        self.buf = None
        self.last_raw_image = None
        self.last_converted_image = None
        self.last_numpy_image = None

    def set_exposure_time(self, exposure_time):
        # use_strobe = (self.trigger_mode == TriggerMode.HARDWARE) # true if using hardware trigger
        # if use_strobe == False or self.is_global_shutter:
        #     self.exposure_time = exposure_time
        #     self.camera.ExposureTime.set(exposure_time * 1000)
        # else:
        #     # set the camera exposure time such that the active exposure time (illumination on time) is the desired value
        #     self.exposure_time = exposure_time
        #     # add an additional 500 us so that the illumination can fully turn off before rows start to end exposure
        #     camera_exposure_time = self.exposure_delay_us + self.exposure_time*1000 + self.row_period_us*self.pixel_size_byte*(self.row_numbers-1) + 500 # add an additional 500 us so that the illumination can fully turn off before rows start to end exposure
        #     self.camera.ExposureTime.set(camera_exposure_time)
        self.exposure_time = exposure_time

        # exposure time in ms
        if self.trigger_mode == TriggerMode.HARDWARE:
            self.camera.put_ExpoTime(int(exposure_time * 1000) + int(self.strobe_delay_us))
        else:
            self.camera.put_ExpoTime(int(exposure_time * 1000))

    def update_camera_exposure_time(self):
        pass
        # use_strobe = (self.trigger_mode == TriggerMode.HARDWARE) # true if using hardware trigger
        # if use_strobe == False or self.is_global_shutter:
        #     self.camera.ExposureTime.set(self.exposure_time * 1000)
        # else:
        #     camera_exposure_time = self.exposure_delay_us + self.exposure_time*1000 + self.row_period_us*self.pixel_size_byte*(self.row_numbers-1) + 500 # add an additional 500 us so that the illumination can fully turn off before rows start to end exposure
        #     self.camera.ExposureTime.set(camera_exposure_time)

    def set_analog_gain(self, analog_gain):
        analog_gain = min(self.GAIN_MAX, analog_gain)
        analog_gain = max(self.GAIN_MIN, analog_gain)
        self.analog_gain = analog_gain
        # gain_min, gain_max, gain_default = self.camera.get_ExpoAGainRange() # remove from set_analog_gain
        # for touptek cameras gain is 100-10000 (for 1x - 100x)
        self.camera.put_ExpoAGain(int(100 * (10 ** (analog_gain / 20))))
        # self.camera.Gain.set(analog_gain)

    def get_awb_ratios(self):
        try:
            self.camera.AwbInit()
            return self.camera.get_WhiteBalanceGain()
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_NOTIMPL")
            self._log.warning("AWB not implemented")
            return (0, 0, 0)

    def set_wb_ratios(self, wb_r=None, wb_g=None, wb_b=None):
        try:
            camera.put_WhiteBalanceGain(wb_r, wb_g, wb_b)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_NOTIMPL")
            self._log.warning("White balance not implemented")

    def set_reverse_x(self, value):
        pass

    def set_reverse_y(self, value):
        pass

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

    def set_pixel_format(self, pixel_format: CameraPixelFormat):

        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        self.pixel_format = pixel_format
        if self.data_format == CameraFrameFormat.RAW:
            if pixel_format == CameraPixelFormat.MONO8:
                self.pixel_size_byte = 1
                self.blacklevel_factor = 1
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
            elif pixel_format == CameraPixelFormat.MONO12:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 16
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO14:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 64
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO16:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 256
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
        else:
            # RGB data format
            if pixel_format == CameraPixelFormat.MONO8:
                self.pixel_size_byte = 1
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 3)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO12:
                self.pixel_size_byte = 2
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO14:
                self.pixel_size_byte = 2
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO16:
                self.pixel_size_byte = 2
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.RGB24:
                self.pixel_size_byte = 3
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 0)
            if pixel_format == CameraPixelFormat.RGB32:
                self.pixel_size_byte = 4
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 2)
            if pixel_format == CameraPixelFormat.RGB48:
                self.pixel_size_byte = 6
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 1)

        self._update_buffer_settings()

        if was_streaming:
            self.start_streaming()

        self._update_internal_settings()

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

    def _update_buffer_settings(self):
        # resize the buffer
        _, _, width, height = self.camera.get_Roi()

        # calculate buffer size
        if self.data_format == "RGB" and self.pixel_size_byte != 4:
            bufsize = Camera._tdib_width_bytes(width * self.pixel_size_byte * 8) * height
        else:
            bufsize = width * self.pixel_size_byte * height
        self._log.info(f"image size: {width=} x {height=}, {bufsize=}")
        # create the buffer
        self.buf = bytes(bufsize)

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

    def set_fan_speed(self, speed):
        if self.has_fan:
            try:
                self.camera.put_Option(toupcam.TOUPCAM_OPTION_FAN, speed)
            except toupcam.HRESULTException as ex:
                error_type = hresult_checker(ex)
                # TODO(imo): Propagate error in some way and handle
                self._log.error("Unable to set fan speed: " + error_type)
        else:
            pass

    def set_continuous_acquisition(self):
        self.camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 0)
        self.trigger_mode = TriggerMode.CONTINUOUS
        # self.update_camera_exposure_time()

    def set_software_triggered_acquisition(self):
        self.camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 1)
        self.trigger_mode = TriggerMode.SOFTWARE
        # self.update_camera_exposure_time()

    def set_hardware_triggered_acquisition(self):
        self.camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 2)
        self.frame_ID_offset_hardware_trigger = None
        self.trigger_mode = TriggerMode.HARDWARE

        # select trigger source to GPIO0
        try:
            self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 1)
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            # TODO(imo): Propagate error in some way and handle
            self._log.error("Unable to select trigger source: " + error_type)
        # set GPIO1 to trigger wait
        try:
            self.camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE, 0)
            self.camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER, 0)
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            # TODO(imo): Propagate error in some way and handle
            self._log.error("Unable to set GPIO1 for trigger ready: " + error_type)

    def set_trigger_width_mode(self):
        self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1)  # set PWM source to GPIO0
        self.camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4)  # trigger source to PWM

    def set_gain_mode(self, mode):
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

        if self.reset_strobe_delay is not None:
            self.reset_strobe_delay()

    def _calculate_hardware_trigger_arguments(self):
        # use camera arguments such as resolutuon, ROI, exposure time, set max FPS, bandwidth to calculate the trigger delay time
        resolution_width = 0
        resolution_height = 0

        pixel_bits = self.pixel_size_byte * 8

        line_length = 0
        low_noise = 0

        try:
            resolution_width, resolution_height = self.camera.get_Size()
        except toupcam.HRESULTException as ex:
            # TODO(imo): Propagate error in some way and handle
            self._log.error("get resolution fail, hr=0x{:x}".format(ex.hr))

        xoffset, yoffset, roi_width, roi_height = self.camera.get_Roi()

        try:
            bandwidth = self.camera.get_Option(toupcam.TOUPCAM_OPTION_BANDWIDTH)
        except toupcam.HRESULTException as ex:
            # TODO(imo): Propagate error in some way and handle
            self._log.error("get badwidth fail, hr=0x{:x}".format(ex.hr))

        if self.has_low_noise_mode:
            try:
                low_noise = self.camera.get_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE)
            except toupcam.HRESULTException as ex:
                # TODO(imo): Propagate error in some way and handle
                self._log.error("get low_noise fail, hr=0x{:x}".format(ex.hr))

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
            max_framerate_tenths_fps = self.camera.get_Option(toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE)
        except toupcam.HRESULTException as ex:
            # TODO(imo): Propagate error in some way and handle
            self._log.error("get max_framerate fail, hr=0x{:x}".format(ex.hr))

        # need reset value, because the default value is only 90% of setting value
        try:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, max_framerate_tenths_fps)
        except toupcam.HRESULTException as ex:
            # TODO(imo): Propagate error in some way and handle
            self._log.error("put max_framerate fail, hr=0x{:x}".format(ex.hr))

        max_framerate_fps = max_framerate_tenths_fps / 10.0

        vheight = 72000000 / (max_framerate_fps * line_length)
        if vheight < roi_height + 56:
            vheight = roi_height + 56

        frame_time = int(vheight * row_time)

        self.strobe_delay_us = frame_time

    def set_black_level(self, blacklevel):
        try:
            current_blacklevel = self.camera.get_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_NOTIMPL")
            print("blacklevel not implemented")
            return

        _blacklevel = blacklevel * self.blacklevel_factor

        try:
            self.camera.put_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL, _blacklevel)
        except toupcam.HRESULTException as ex:
            print("put blacklevel fail, hr=0x{:x}".format(ex.hr))
