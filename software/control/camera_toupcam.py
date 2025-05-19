import math
import time
from typing import Optional, Tuple, Sequence, Dict

import numpy as np
import pydantic

import control.utils
import squid.logging
from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraGainRange,
    CameraFrameFormat,
    CameraPixelFormat,
    CameraFrame,
)
import squid.config
from control._def import *

import threading
import control.toupcam as toupcam
from control.toupcam_exceptions import hresult_checker

log = squid.logging.get_logger(__name__)


class ToupCamCapabilities(pydantic.BaseModel):
    binning_to_resolution: Dict[Tuple[int, int], Tuple[int, int]]
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
    TOUPCAM_OPTION_RAW_RAW_VAL = 1
    TOUPCAM_OPTION_RAW_RGB_VAL = 0
    PIXEL_SIZE_UM = 3.76

    @staticmethod
    def _event_callback(event_number, camera):
        if event_number == toupcam.TOUPCAM_EVENT_IMAGE:
            camera._on_frame_callback()

    @staticmethod
    def _tdib_width_bytes(w):
        return (w * 24 + 31) // 32 * 4

    @staticmethod
    def _calculate_strobe_delay_us(
        camera: toupcam.Toupcam, pixel_size: int, capabilities: ToupCamCapabilities
    ) -> float:
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
            log.error(f"get max_framerate fail --> {control.toupcam_exceptions.explain(ex)}")
            raise

        # need reset value, because the default value is only 90% of setting value
        try:
            camera.put_Option(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, max_framerate_tenths_fps)
        except toupcam.HRESULTException as ex:
            log.exception(f"put max_framerate fail --> {control.toupcam_exceptions.explain(ex)}")
            raise

        max_framerate_fps = max_framerate_tenths_fps / 10.0

        vheight = 72000000 / (max_framerate_fps * line_length)
        if vheight < roi_height + 56:
            vheight = roi_height + 56

        strobe_time = int(vheight * row_time)

        return strobe_time

    @staticmethod
    def _open(index=None, sn=None) -> Tuple[toupcam.Toupcam, ToupCamCapabilities]:
        log = squid.logging.get_logger("ToupcamCamera._open")
        log.info(f"Opening toupcam with {index=}, {sn=}")
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

        for idx, device in enumerate(devices):
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

        resolution_list = []
        for r in devices[index].model.res:
            resolution_list.append((r.width, r.height))
        if len(resolution_list) == 0:
            raise ValueError("No resolutions found for camera")
        resolution_list.sort(key=lambda x: x[0] * x[1], reverse=True)

        highest_res = resolution_list[0]

        binning_res = {}
        for res in resolution_list:
            x_binning = int(highest_res[0] / res[0])
            y_binning = int(highest_res[1] / res[1])
            binning_res[(x_binning, y_binning)] = res

        camera = toupcam.Toupcam.Open(devices[index].id)
        capabilities = ToupCamCapabilities(
            binning_to_resolution=binning_res,
            has_fan=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_FAN) > 0,
            has_TEC=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_TEC_ONOFF) > 0,
            has_low_noise_mode=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_LOW_NOISE) > 0,
            has_black_level=(devices[index].model.flag & toupcam.TOUPCAM_FLAG_BLACKLEVEL) > 0,
        )

        return camera, capabilities

    def __init__(self, config: squid.config.CameraConfig, hw_trigger_fn, hw_set_strobe_delay_ms_fn):
        super().__init__(config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        self._current_frame: Optional[CameraFrame] = None
        self._camera: Optional[toupcam.Toupcam] = None

        # These are used only in both software and hw trigger mode.  We use them to make sure we don't send a trigger
        # when a frame is already in progress.  The send_trigger method should be the only one setting this to True
        # (and setting the timestamp), and the raw frame callback can set the _trigger_sent to False when
        # it receives a frame.
        self._trigger_sent = False
        self._last_trigger_timestamp = 0

        # _raw_camera_stream_started keeps track of the ToupcamCamera <-> hardware stream. This should always be running,
        # because it is how we get notified by the camera that new frames are available.  Our _on_frame_callback
        # is what the camera driver calls when a new frame is available.
        self._raw_camera_stream_started = False
        self._raw_frame_callback_lock = threading.Lock()
        (self._camera, self._capabilities) = ToupcamCamera._open(index=0)
        self._pixel_format = self._config.default_pixel_format
        self._binning = self._config.default_binning

        # Since we need to set the on-camera exposure time different depending on our trigger mode
        # (eg: sometimes we compensate for a strobe delay when hardware triggering), we can't back
        # out our users' exposure time easily from the camera value.  To get around this, we need
        # to store the exposure time they give to us.
        #
        # Because it is better than nothing, we initialize our stored value to whatever is on the
        # camera at startup (but then set_exposure_time will modify it when a user sets exposure time)
        self._exposure_time = self._get_raw_exposure_time()

        # toupcam temperature
        self.temperature_reading_callback = None
        self.terminate_read_temperature_thread = False
        self.thread_read_temperature = threading.Thread(target=self._check_temperature, daemon=True)
        self.thread_read_temperature.start()

        self._configure_camera()
        self._start_raw_camera_stream()
        self._update_internal_settings()

    def _start_raw_camera_stream(self):
        """
        Make sure the camera is setup to tell us when frames are available.
        """
        try:
            self._log.debug("Starting raw stream in PullModeWithCallback.")
            self._camera.StartPullModeWithCallback(self._event_callback, self)
            self._raw_camera_stream_started = True
        except toupcam.HRESULTException as ex:
            self._raw_camera_stream_started = False
            self._log.exception("failed to start camera, hr=0x{:x}".format(ex.hr))
            raise ex

    def _on_frame_callback(self):
        """
        This is the callback that we have the toupcam software call when a frame is ready.  It should always be running.
        """
        with self._raw_frame_callback_lock:
            # Since we are receiving a frame callback, we know things are setup properly.
            self._raw_camera_stream_started = True

            # Make sure that if this was triggered by a software trigger, or we switched to software triggering
            # while waiting for this frame, that we allow subsequent software triggers.
            self._trigger_sent = False

            # get the image from the camera
            try:
                self._camera.PullImageV2(
                    self._internal_read_buffer, self._get_pixel_size_in_bytes() * 8, None
                )  # the second camera is number of bits per pixel - ignored in RAW mode
            except toupcam.HRESULTException as ex:
                # TODO(imo): Propagate error in some way and handle
                self._log.error("pull image failed, hr=0x{:x}".format(ex.hr))

            this_frame_id = (self._current_frame.frame_id if self._current_frame else 0) + 1
            this_timestamp = time.time()
            this_frame_format = self.get_frame_format()
            this_pixel_format = self.get_pixel_format()

            if this_frame_format != CameraFrameFormat.RAW:
                self._log.error("Only RAW CameraFrameFormat are supported, cannot handle frame.")
                return

            (x_offset, y_offset, width, height) = self.get_region_of_interest()
            if self._get_pixel_size_in_bytes() == 1:
                raw_image = np.frombuffer(self._internal_read_buffer, dtype="uint8")
            elif self._get_pixel_size_in_bytes() == 2:
                raw_image = np.frombuffer(self._internal_read_buffer, dtype="uint16")
            current_raw_image = raw_image.reshape(height, width)

            current_frame = CameraFrame(
                frame_id=this_frame_id,
                timestamp=this_timestamp,
                frame=self._process_raw_frame(current_raw_image),
                frame_format=this_frame_format,
                frame_pixel_format=this_pixel_format,
            )

            # Before releasing the lock, set the new current fram with the incremented frame id so other methods can
            # see we have a new frame. This should be the only place we modify _current_frame outside of init, and
            # since we hold a lock this whole time, we know that the frame id is still correct.
            self._current_frame = current_frame

        # Propagate the local copy so we are sure it's the correct frame that goes out.
        self._propogate_frame(current_frame)

    def _update_internal_settings(self, send_exposure=True):
        """
        This needs to be called when a camera side setting changes that needs a:
          * read buffer size update
          * strobe delay recalc

        It might be called in a performance sensitive context, so you should make sure any updates here
        are as fast as they can be.
        """
        # resize the buffer
        _, _, width, height = self._camera.get_Roi()

        # calculate buffer size
        pixel_size = self._get_pixel_size_in_bytes()
        if self.get_frame_format() == CameraFrameFormat.RGB and pixel_size != 4:
            buffer_size = ToupcamCamera._tdib_width_bytes(width * pixel_size * 8) * height
        else:
            buffer_size = width * pixel_size * height
        self._log.info(f"image size: {width=} x {height=}, {buffer_size=}")
        # create the buffer
        self._internal_read_buffer = bytes(buffer_size)

        self._strobe_delay_us = ToupcamCamera._calculate_strobe_delay_us(
            self._camera, self._get_pixel_size_in_bytes(), self._capabilities
        )
        if self._hw_set_strobe_delay_ms_fn:
            self._hw_set_strobe_delay_ms_fn(self._strobe_delay_us / 1000.0)

        if send_exposure:
            self.set_exposure_time(self.get_exposure_time())

    def _check_temperature(self):
        while not self.terminate_read_temperature_thread:
            time.sleep(2)
            temperature = self.get_temperature()
            if self.temperature_reading_callback is not None:
                try:
                    self.temperature_reading_callback(temperature)
                except TypeError as ex:
                    self._log.error("Temperature read callback failed due to error: " + repr(ex))
                    pass

    def _configure_camera(self):
        """
        Run our initial configuration to get the camera into a know and safe starting state.
        """
        if self._capabilities.has_low_noise_mode:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE, 0)

        # set temperature
        self._set_fan_speed(self._config.default_fan_speed)
        self.set_temperature(self._config.default_temperature)

        self._raw_set_frame_format(CameraFrameFormat.RAW)
        self._raw_set_pixel_format(self._pixel_format)  # 'MONO8'
        try:
            self.set_black_level(self._config.default_black_level)
        except NotImplementedError:
            self._log.warning("Black level is not supported by this toupcam model, ignoring default black level value")

        # We can't trigger update_internal_settings yet, because the strobe calc will fail.  So set the res
        # using the raw helper.
        (width, height) = self._capabilities.binning_to_resolution[self._binning]
        self._raw_set_resolution(width, height)

        # TODO: Do hardware cropping here (set ROI)

    def _set_temperature_reading_callback(self, func):
        self.temperature_reading_callback = func

    def _get_raw_exposure_time(self) -> float:
        return self._camera.get_ExpoTime() / 1000.0  # microseconds -> milliseconds

    def _close(self):
        self.terminate_read_temperature_thread = True
        self.thread_read_temperature.join()
        self._set_fan_speed(0)
        self._camera.Close()
        self._camera = None

    def start_streaming(self):
        self._log.info("start streaming requested")
        if not self._raw_camera_stream_started:
            self._start_raw_camera_stream()

    def stop_streaming(self):
        self._camera.Stop()
        self._raw_camera_stream_started = False

    def get_is_streaming(self):
        return self._raw_camera_stream_started

    def set_exposure_time(self, exposure_time):
        # Since we have to set the on-camera exposure time differently depending on the trigger mode
        # and the calculated strobe delay, it is tricky to get the exposure time from the
        # camera.  To get around this, we store it.
        self._exposure_time = exposure_time

        # In the calls below, we need to make sure we convert to microseconds.
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._camera.put_ExpoTime(int(exposure_time * 1000) + int(self._strobe_delay_us))
        else:
            self._camera.put_ExpoTime(int(exposure_time * 1000))

    def get_exposure_time(self) -> float:
        return self._exposure_time

    def get_exposure_limits(self) -> Tuple[float, float]:
        (min_exposure, max_exposure, default_exposure) = self._camera.get_ExpTimeRange()
        return min_exposure / 1000.0, max_exposure / 1000.0  # us -> ms

    @staticmethod
    def _user_gain_to_toupcam(user_gain):
        """
        0-40 is the valid user range.  This must map to 100-10000 in toupcam
        """
        return int(100 * (10 ** (user_gain / 20)))

    @staticmethod
    def _toupcam_gain_to_user(toupcam_gain):
        return 20 * math.log10(toupcam_gain / 100)

    def set_analog_gain(self, analog_gain):
        gain_range = self.get_gain_range()

        clamped_gain = max(gain_range.min_gain, min(analog_gain, gain_range.max_gain))

        if clamped_gain != analog_gain:
            self._log.warning(
                f"Requested {analog_gain=} is outside the range {gain_range.min_gain} to {gain_range.max_gain}"
            )

        # for touptek cameras gain is 100-10000 (for 1x - 100x)
        self._log.info(f"Trying to set analog gain = {clamped_gain}")
        self._camera.put_ExpoAGain(self._user_gain_to_toupcam(clamped_gain))

    def _raw_set_pixel_format(self, pixel_format: CameraPixelFormat):
        if self.get_frame_format() == CameraFrameFormat.RAW:
            if pixel_format == CameraPixelFormat.MONO8:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
            elif pixel_format == CameraPixelFormat.MONO12:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO14:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO16:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            else:
                raise ValueError(f"Unsupported pixel format: {pixel_format=}")
        else:
            # RGB data format
            if pixel_format == CameraPixelFormat.MONO8:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 3)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO12:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO14:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO16:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.RGB24:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 0)
            elif pixel_format == CameraPixelFormat.RGB32:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 2)
            elif pixel_format == CameraPixelFormat.RGB48:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 1)
            else:
                raise ValueError(f"Unsupported pixel format: {pixel_format=}")

        # NOTE(imo): Ideally we'd query pixel_format from the device instead of storing the state here, but it's
        # impossible to do so - the settings for a particular depth are not unique.  EG MONO12 and MONO14 both
        # have the same settings.  I'm not sure how this works?  But just store the pixel format here...
        self._pixel_format = pixel_format

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        with self._pause_streaming():
            self._raw_set_pixel_format(pixel_format)
            self.set_black_level(self._config.default_black_level)
        self._update_internal_settings()

    def get_pixel_format(self) -> CameraPixelFormat:
        return self._pixel_format

    def set_auto_exposure(self, enabled: bool):
        try:
            self._camera.put_AutoExpoEnable(enabled)
        except toupcam.HRESULTException as ex:
            self._log.exception("Unable to set auto exposure: " + repr(ex))
            raise

    def _raw_set_frame_format(self, data_format: CameraFrameFormat):
        if data_format == CameraFrameFormat.RGB:
            self._camera.put_Option(
                toupcam.TOUPCAM_OPTION_RAW, ToupcamCamera.TOUPCAM_OPTION_RAW_RGB_VAL
            )  # 0 is RGB mode, 1 is RAW mode
        elif data_format == CameraFrameFormat.RAW:
            self._camera.put_Option(
                toupcam.TOUPCAM_OPTION_RAW, ToupcamCamera.TOUPCAM_OPTION_RAW_RAW_VAL
            )  # 1 is RAW mode, 0 is RGB mode

    def set_frame_format(self, data_format: CameraFrameFormat):
        with self._pause_streaming():
            self._raw_set_frame_format(data_format)
        self._update_internal_settings()

    def get_frame_format(self) -> CameraFrameFormat:
        camera_val = self._camera.get_Option(toupcam.TOUPCAM_OPTION_RAW)

        if camera_val == ToupcamCamera.TOUPCAM_OPTION_RAW_RAW_VAL:
            return CameraFrameFormat.RAW
        elif camera_val == ToupcamCamera.TOUPCAM_OPTION_RAW_RGB_VAL:
            return CameraFrameFormat.RGB
        else:
            raise ValueError(f"Camera returned unknown frame format: value={camera_val}")

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        with self._pause_streaming():
            if (binning_factor_x, binning_factor_y) not in self._capabilities.binning_to_resolution:
                raise ValueError(f"Binning ({binning_factor_x},{binning_factor_y}) not supported by camera")
            width, height = self._capabilities.binning_to_resolution[(binning_factor_x, binning_factor_y)]
            self._raw_set_resolution(width, height)
            self._binning = (binning_factor_x, binning_factor_y)
            self._log.debug(f"Setting binning to {binning_factor_x},{binning_factor_y} -> {width},{height}")

            # We will disable hardware cropping until hardware trigger issue is resolved.
            # old_binning = self._binning
            # self._binning = (binning_factor_x, binning_factor_y)
            # old_roi = self.get_region_of_interest()

        # new_roi = AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi, self._binning)
        # self._log.debug(f"Changing roi from {old_roi=} to {new_roi=} to keep FOV the same after resolution change.")
        # self.set_region_of_interest(*new_roi)

        self._update_internal_settings()

    def _raw_set_resolution(self, width, height):
        try:
            self._camera.put_Size(width, height)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_INVALIDARG", "E_BUSY", "E_ACCESDENIED", "E_UNEXPECTED")
            if err_type == "E_INVALIDARG":
                self._log.exception(f"Resolution ({width},{height}) not supported by camera")
            else:
                self._log.exception(f"Resolution cannot be set due to error: " + err_type)
            raise

    def get_temperature(self):
        try:
            return self._camera.get_Temperature() / 10
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Could not get temperature, error: " + error_type)
            raise

    def set_temperature(self, temperature):
        try:
            self._camera.put_Temperature(int(temperature * 10))
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Unable to set temperature: " + error_type)
            raise

    def _set_fan_speed(self, speed):
        try:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_FAN, speed)
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Unable to set fan speed: " + error_type)
            raise

    def _set_trigger_width_mode(self):
        self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1)  # set PWM source to GPIO0
        self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4)  # trigger source to PWM

    def _set_gain_mode(self, mode):
        if mode == "LCG":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 0)
        elif mode == "HCG":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 1)
        elif mode == "HDR":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 2)

    def send_trigger(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise RuntimeError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_ready_for_trigger():
            raise RuntimeError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._log.debug(f"Sending hardware trigger with {illumination_time=}")
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._log.debug("Sending software trigger..")
            self._camera.Trigger(1)

        self._last_trigger_timestamp = time.time()
        self._trigger_sent = True

    def get_ready_for_trigger(self) -> bool:
        # TODO(imo): Should we pass in the timeout?  This might be fine since it's calculated based on the exposure time.
        trigger_timeout_s = 1.5 * self._get_raw_exposure_time() / 1000 * 1.02 + 4
        trigger_age = time.time() - self._last_trigger_timestamp
        trigger_too_old = trigger_age > trigger_timeout_s
        trigger_sent = self._trigger_sent
        if trigger_sent and trigger_too_old:
            self._log.warning(
                f"Previous software trigger timed out after {trigger_timeout_s} [s]. Assuming it failed and allowing re-trigger."
            )
            self._trigger_sent = False
        elif trigger_sent:
            return False
        return True

    def _stop_exposure(self):
        if self.get_is_streaming() and self._trigger_sent == True:
            self._camera.Trigger(0)
            self._trigger_sent = False
        else:
            pass

    def get_strobe_time(self) -> float:
        return self._strobe_delay_us / 1000.0

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        roi_offset_x = control.utils.truncate_to_interval(offset_x, 2)
        roi_offset_y = control.utils.truncate_to_interval(offset_y, 2)
        roi_width = control.utils.truncate_to_interval(width, 2)
        roi_height = control.utils.truncate_to_interval(height, 2)
        with self._pause_streaming():
            try:
                self._camera.put_Roi(roi_offset_x, roi_offset_y, roi_width, roi_height)
            except toupcam.HRESULTException as ex:
                self._log.exception("ROI bounds invalid, not changing ROI.")

        self._update_internal_settings()

    def get_binning(self) -> Tuple[int, int]:
        return self._binning

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return self._capabilities.binning_to_resolution.keys()

    def get_resolution(self) -> Tuple[int, int]:
        return self._capabilities.binning_to_resolution[self._binning]

    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return (
            self.PIXEL_SIZE_UM * self.get_binning()[0]
        )  # We will use the same binning factor in width and height for now

    def get_analog_gain(self) -> float:
        return self._toupcam_gain_to_user(self._camera.get_ExpoAGain())

    def get_gain_range(self) -> CameraGainRange:
        (min_gain, max_gain, default_gain) = self._camera.get_ExpoAGainRange()
        return CameraGainRange(
            min_gain=self._toupcam_gain_to_user(min_gain), max_gain=self._toupcam_gain_to_user(max_gain), gain_step=0.01
        )

    def read_camera_frame(self):
        # TODO(imo): Seems like the timeout should be something passed in, not hard coded.
        timeout_s = (self.get_exposure_time() / 1000) * 1.02 + 4
        timeout_end_time_s = time.time() + timeout_s
        starting_frame_id = self.get_frame_id()

        while time.time() < timeout_end_time_s:
            if self.get_frame_id() != starting_frame_id:
                return self._current_frame
            time.sleep(0.001)

        self._log.error(f"Timed out after {timeout_s} [s] waiting for a frame.")

        return None

    def get_frame_id(self) -> int:
        return self._current_frame.frame_id if self._current_frame else -1

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        return self._camera.get_WhiteBalanceGain()

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        self._camera.put_WhiteBalanceGain((red_gain, green_gain, blue_gain))

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        self._camera.AwbInit()
        return self.get_white_balance_gains()

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
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB32): 1,  # Bit depth of 8 -> same as MONO8
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 256,  # Bit depth of 16 -> same as MONO16
    }

    def _get_black_level_factor(self):
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
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 6,
    }

    def _get_pixel_size_in_bytes(self):
        frame_and_format = (self.get_frame_format(), self.get_pixel_format())
        if frame_and_format not in ToupcamCamera._PIXEL_SIZE_MAPPING:
            raise ValueError(f"Unknown combo for pixel size: {frame_and_format=}")

        return ToupcamCamera._PIXEL_SIZE_MAPPING[frame_and_format]

    def get_black_level(self) -> float:
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")

        raw_black_level = self._camera.get_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL)

        return raw_black_level / self._get_black_level_factor()

    def set_black_level(self, black_level: float):
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")
        raw_black_level = black_level * self._get_black_level_factor()

        try:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL, raw_black_level)
        except toupcam.HRESULTException as ex:
            print("put blacklevel fail, hr=0x{:x}".format(ex.hr))

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        if acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            trigger_option_value = 0
        elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            trigger_option_value = 1
        elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            trigger_option_value = 2
        else:
            raise ValueError(f"Do not know how to handle {acquisition_mode=}")
        self._camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, trigger_option_value)

        if acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            # select trigger source to GPIO0
            try:
                self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 1)
            except toupcam.HRESULTException as ex:
                error_type = hresult_checker(ex)
                self._log.exception("Unable to select trigger source: " + error_type)
                raise
            # set GPIO1 to trigger wait
            try:
                self._camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE, 0)
                self._camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER, 0)
            except toupcam.HRESULTException as ex:
                error_type = hresult_checker(ex)
                self._log.exception("Unable to set GPIO1 for trigger ready: " + error_type)
                raise
            # Re-set exposure time to force strobe to get set to the remote.
            self.set_exposure_time(self.get_exposure_time())

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        trigger_option_value = self._camera.get_Option(toupcam.TOUPCAM_OPTION_TRIGGER)
        if trigger_option_value == 0:
            return CameraAcquisitionMode.CONTINUOUS
        elif trigger_option_value == 1:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif trigger_option_value == 2:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            raise ValueError(f"Received unknown trigger option from toupcam: {trigger_option_value}")

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self._camera.get_Roi()
