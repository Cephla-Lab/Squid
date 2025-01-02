import functools
from typing import Optional, Tuple

import numpy as np

from squid.config import CameraConfig
from squid.abc import AbstractCamera, CameraAcquisitionMode, CameraPixelFormat, CameraFrameFormat


def get_camera(config: CameraConfig, simulated: bool = False) -> AbstractCamera:
    """
    Try to import, and then build, the requested camera.  We import on a case-by-case basis
    because some cameras require system level installations, and so in many cases camera
    driver imports will fail.
    """
    if simulated:
        return SimulatedCamera(config)

    raise NotImplementedError(f"Camera of type={config.camera_type} not yet supported.")

class SimulatedCamera(AbstractCamera):
    @staticmethod
    def debug_log(method):
        import inspect
        @functools.wraps
        def _logged_method(self, *args, **kwargs):
            kwargs_pairs = tuple(f"{k}={v}" for (k, v) in kwargs.items())
            self._log.debug(f"{inspect.currentframe().f_code.co_name}({','.join(args + kwargs_pairs)})")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_id = 1
        self._current_frame = None

        self._exposure_time = None
        self._frame_format = None
        self._pixel_format = None
        self._resolution = None
        self.set_resolution(self._config.default_resolution[0], self._config.default_resolution[1])
        self._analog_gain = None
        self._white_balance_gains = None
        self._black_level = None
        self._acquisition_mode = None
        self._roi = (0, 0, self.get_resolution()[0], self.get_resolution[1])
        self._temperature_setpoint = None


    @debug_log
    def set_exposure_time(self, exposure_time_ms: float):
        self._exposure_time = exposure_time_ms

    @debug_log
    def get_exposure_time(self) -> float:
        return self._exposure_time

    @debug_log
    def set_frame_format(self, frame_format: CameraFrameFormat):
        self._frame_format = frame_format

    @debug_log
    def get_frame_format(self) -> CameraFrameFormat:
        return self._frame_format

    @debug_log
    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        self._pixel_format = pixel_format

    @debug_log
    def get_pixel_format(self) -> CameraPixelFormat:
        return self._pixel_format

    @debug_log
    def set_resolution(self, width: int, height: int):
        self._resolution = (width, height)

    @debug_log
    def get_resolution(self) -> Tuple[int, int]:
        return self._resolution

    @debug_log
    def set_analog_gain(self, analog_gain: float):
        self._analog_gain = analog_gain

    @debug_log
    def get_analog_gain(self) -> float:
        return self._analog_gain

    @debug_log
    def start_streaming(self):
        raise NotImplementedError("Streaming is not implemented on the sim camera yet")

    @debug_log
    def stop_streaming(self):
        pass

    @debug_log
    def get_frame(self) -> np.ndarray:
        self.send_trigger()
        return self._current_frame

    @debug_log
    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        return self._white_balance_gains

    @debug_log
    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        self._white_balance_gains = (red_gain, green_gain, blue_gain)

    @debug_log
    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        self.set_white_balance_gains(1.0, 1.0, 1.0)

        return self.get_white_balance_gains()

    @debug_log
    def set_black_level(self, black_level: float):
        self._black_level = black_level

    @debug_log
    def get_black_level(self) -> float:
        return self._black_level

    @debug_log
    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        self._acquisition_mode = acquisition_mode

    @debug_log
    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        return self._acquisition_mode

    @debug_log
    def send_trigger(self):
        (height, width) = self.get_resolution()
        if self.get_frame_id() == 1:
            if self.get_pixel_format() == 'MONO8':
                self._current_frame = np.random.randint(255, size=(height, width), dtype=np.uint8)
                self._current_frame[height // 2-99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = 200
            elif self.get_pixel_format() == 'MONO12':
                self._current_frame = np.random.randint(4095, size=(height, width), dtype=np.uint16)
                self._current_frame[height // 2 - 99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = 200 * 16
                self._current_frame = self._current_frame << 4
            elif self.get_pixel_format() == 'MONO16':
                self._current_frame = np.random.randint(65535, size=(height, width), dtype=np.uint16)
                self._current_frame[height // 2 - 99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = 200 * 256
        else:
            self._current_frame = np.roll(self._current_frame,10, axis=0)

        self._frame_id += 1
        self._propogate_frame(self._current_frame)

    @debug_log
    def cancel_exposure(self):
        pass

    @debug_log
    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        self._roi = (offset_x, offset_y, width, height)

    @debug_log
    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self._roi

    @debug_log
    def set_temperature(self, temperature_deg_c: Optional[float]):
        self._temperature_setpoint = temperature_deg_c

    @debug_log
    def get_temperature(self) -> float:
        return self._temperature_setpoint

    def get_frame_id(self) -> int:
        return self._frame_id
