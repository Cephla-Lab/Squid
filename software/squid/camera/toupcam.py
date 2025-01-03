from typing import Optional, Tuple

import numpy as np

from squid.abc import AbstractCamera, CameraConfig, CameraAcquisitionMode, CameraFrameFormat
import squid.logging
import squid.config
from squid.config import CameraPixelFormat

import control.toupcam as toupcam_api
import control.toupcam_exceptions as toupcam_api_exceptions

_log = squid.logging.get_logger(__name__)


def get_sn_by_model(model_name):
    try:
        device_list = toupcam_api.Toupcam.EnumV2()
    except:
        _log.error("Problem generating Toupcam device list")
        return None
    for dev in device_list:
        if dev.displayname == model_name:
            return dev.id
    return None  # return None if no device with the specified model_name is connected


class ToupcamCamera(AbstractCamera):
    def __init__(self, camera_config: CameraConfig, serial_number=None):
        super().__init__(camera_config)
        self._buf = bytes(0)
        self._pixel_format: CameraPixelFormat = camera_config.default_pixel_format

        # If we aren't given a serial number to look for, just open the first Toupcam Camera we find.
        devices = toupcam_api.Toupcam.EnumV2()
        cam_idx = 0
        if serial_number:
            cam_idx = [d.id for d in devices].index(serial_number)

        self._toupcam_device = devices[cam_idx]
        self._camera: Optional[toupcam_api.Toupcam] = None
        self._open(self._toupcam_device)

    def _open(self, cam_device: toupcam_api.ToupcamDeviceV2):
        self._camera = toupcam_api.Toupcam.Open(cam_device)

        valid_resolutions = tuple((r.width, r.height) for r in cam_device.model.res)
        # The maximum resolution must be one of the tuples, so we can't just look for max width and height.  Instead,
        # use the maxiumum number of pixels by multiplying the two.
        max_resolution = max(valid_resolutions, key=lambda r: r[0] * r[1])

        if self._config.default_resolution is not None and self._config.default_resolution not in valid_resolutions:
            return ValueError(f"The default resolution, {self._config.default_resolution}, is not in the list of valid resolutions.")

        starting_resolution = self._config.default_resolution if self._config.default_resolution else max_resolution
        self.set_resolution(*starting_resolution)

        self._has_low_noise_mode = (cam_device.model.flag & toupcam_api.TOUPCAM_FLAG_LOW_NOISE) > 0
        if self._has_low_noise_mode:
            self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_LOW_NOISE, 0)

        if self._has_fan():
            self._set_fan_speed(1)

        self.set_temperature(20)

        self.set_frame_format(CameraFrameFormat.RAW)
        self.set_pixel_format(self._config.default_pixel_format)
        self.set_auto_exposure(False)
        self.set_black_level(self._config.default_black_level)

    def _calculate_hardware_trigger_arguments(self):
        """
        use camera arguments such as resolution, ROI, exposure time, set max FPS, bandwidth to calculate the trigger delay time
        """
        pixel_bits = self.pixel_size_byte * 8

        line_length = 0
        low_noise = 0

        (resolution_width, resolution_height) = self.get_resolution()
        xoffset, yoffset, roi_width, roi_height = self.get_region_of_interest()

        try:
            bandwidth = self._camera.get_Option(toupcam_api.TOUPCAM_OPTION_BANDWIDTH)
        except toupcam_api.HRESULTException as ex:
            self._log.error("get bandwidth fail, hr=0x{:x}".format(ex.hr))
            raise

        if self._has_low_noise_mode:
            try:
                low_noise = self._camera.get_Option(toupcam_api.TOUPCAM_OPTION_LOW_NOISE)
            except toupcam_api.HRESULTException as ex:
                self._log.error("get low_noise fail, hr=0x{:x}".format(ex.hr))
                raise

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
            max_framerate = self._camera.get_Option(toupcam_api.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE)
        except toupcam_api.HRESULTException as ex:
            self._log.error("get max_framerate fail, hr=0x{:x}".format(ex.hr))
            raise

        # need reset value, because the default value is only 90% of setting value
        try:
            self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_PRECISE_FRAMERATE, max_framerate)
        except toupcam_api.HRESULTException as ex:
            self._log.error("put max_framerate fail, hr=0x{:x}".format(ex.hr))
            raise

        max_framerate = max_framerate / 10.0

        vheight = 72000000 / (max_framerate * line_length)
        if vheight < roi_height + 56:
            vheight = roi_height + 56

        exp_length = 72 * self.get_exposure_time() * 1000 / line_length

        frame_time = int(vheight * row_time)

        self._strobe_delay_ms = frame_time / 1000.0

    def _has_fan(self):
        return self._toupcam_device.model.flag & toupcam_api.TOUPCAM_FLAG_FAN

    def _has_tec(self):
        return self._toupcam_device.model.flag & toupcam_api.TOUPCAM_FLAG_TEC_ONOFF

    def _set_fan_speed(self, speed_toupcam_units: int):
        if not self._has_fan():
            raise NotImplementedError(f"This toupcam sn={self._toupcam_device.id} does not have a fan, cannot set fan speed.")

        try:
            self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_FAN, speed_toupcam_units)
        except toupcam_api.HRESULTException as ex:
            error_type = toupcam_api_exceptions.hresult_checker(ex)
            self._log.error("Unable to set fan speed: " + error_type)


    def _update_buffer_settings(self):
        # resize the buffer
        xoffset, yoffset, width, height = self.get_region_of_interest()

        def TDIBWIDTHBYTES(w):
            return (w * 24 + 31) // 32 * 4

        # calculate buffer size
        if self.get_frame_format() == CameraFrameFormat.RGB and self.pixel_size_byte != 4:
            bufsize = TDIBWIDTHBYTES(width * self.pixel_size_byte * 8) * height
        else:
            bufsize = width * self.pixel_size_byte * height
        self.log.info("image size: {} x {}, bufsize = {}".format(width, height, bufsize))
        # create the buffer
        self.buf = bytes(bufsize)

    def set_exposure_time(self, exposure_time_ms: float):
        pass

    def get_exposure_time(self) -> float:
        pass

    def set_frame_format(self, frame_format: CameraFrameFormat):
        pass

    def get_frame_format(self) -> CameraFrameFormat:
        pass

    def set_pixel_format(self, pixel_format: squid.config.CameraPixelFormat):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        data_format = self.get_frame_format()
        self._pixel_format = pixel_format
        if data_format == CameraFrameFormat.RAW:
            if pixel_format == CameraPixelFormat.MONO8:
                self.pixel_size_byte = 1
                self.blacklevel_factor = 1
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 0)
            elif pixel_format == CameraPixelFormat.MONO12:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 16
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO14:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 64
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO16:
                self.pixel_size_byte = 2
                self.blacklevel_factor = 256
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
        else:
            # RGB data format
            if pixel_format == CameraPixelFormat.MONO8:
                self.pixel_size_byte = 1
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 3)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO12:
                self.pixel_size_byte = 2
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO14:
                self.pixel_size_byte = 2
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.MONO16:
                self.pixel_size_byte = 2
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            if pixel_format == CameraPixelFormat.RGB24:
                self.pixel_size_byte = 3
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 0)
            if pixel_format == CameraPixelFormat.RGB32:
                self.pixel_size_byte = 4
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 2)
            if pixel_format == CameraPixelFormat.RGB48:
                self.pixel_size_byte = 6
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam_api.TOUPCAM_OPTION_RGB, 1)

        self._update_buffer_settings()

        if was_streaming:
            self.start_streaming()

    def get_pixel_format(self) -> squid.config.CameraPixelFormat:
        pass

    def set_resolution(self, width: int, height: int):
        pass

    def get_resolution(self) -> Tuple[int, int]:
        pass

    def set_analog_gain(self, analog_gain: float):
        pass

    def get_analog_gain(self) -> float:
        pass

    def start_streaming(self):
        pass

    def stop_streaming(self):
        pass

    def get_frame(self) -> np.ndarray:
        pass

    def get_frame_id(self) -> int:
        pass

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        pass

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        pass

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        pass

    def set_black_level(self, black_level: float):
        pass

    def get_black_level(self) -> float:
        pass

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        pass

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        pass

    def send_trigger(self):
        pass

    def cancel_exposure(self):
        pass

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        pass

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        pass

    def set_temperature(self, temperature_deg_c: Optional[float]):
        pass

    def get_temperature(self) -> float:
        pass
