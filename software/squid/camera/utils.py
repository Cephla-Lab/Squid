import functools
import threading
import time
from typing import Optional, Tuple, Sequence, Callable

import numpy as np

import squid.logging
from squid.config import CameraConfig, CameraPixelFormat, CameraVariant
from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraFrameFormat,
    CameraFrame,
    CameraGainRange,
    CameraError,
)

_log = squid.logging.get_logger("squid.camera.utils")


def get_camera(
    config: CameraConfig,
    simulated: bool = False,
    hw_trigger_fn: Optional[Callable[[Optional[float]], bool]] = None,
    hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]] = None,
) -> AbstractCamera:
    """
    Try to import, and then build, the requested camera.  We import on a case-by-case basis
    because some cameras require system level installations, and so in many cases camera
    driver imports will fail.

    If you're using a camera implementation with hardware trigger mode, you'll need to provide the functions for
    sending a hardware trigger and setting the strobe delay.

    NOTE(imo): While we transition to AbstractCamera, we need to do some hacks here to make the non-transitioned
    drivers still work.  Hence the embedded helpers here.
    """

    def open_if_needed(camera):
        try:
            camera.open()
        except AttributeError:
            pass

    if simulated:
        return SimulatedCamera(config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn)

    try:
        if config.camera_type == CameraVariant.TOUPCAM:
            import control.camera_toupcam

            camera = control.camera_toupcam.ToupcamCamera(
                config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
            )
        elif config.camera_type == CameraVariant.FLIR:
            import control.camera_flir

            camera = control.camera_flir.Camera(config)
        elif config.camera_type == CameraVariant.HAMAMATSU:
            import control.camera_hamamatsu

            camera = control.camera_hamamatsu.HamamatsuCamera(
                config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
            )
        elif config.camera_type == CameraVariant.IDS:
            import control.camera_ids

            camera = control.camera_ids.Camera(config)
        elif config.camera_type == CameraVariant.TUCSEN:
            import control.camera_tucsen

            camera = control.camera_ids.Camera(config)
        elif config.camera_type == CameraVariant.TIS:
            import control.camera_TIS

            camera = control.camera_TIS.Camera(config)
        else:
            import control.camera

            camera = control.camera.DefaultCamera(
                config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
            )

        # NOTE(imo): All of these things are hacks before complete migration to AbstractCamera impls.  They can
        # be removed once all the cameras conform to the AbstractCamera interface.
        open_if_needed(camera)

        return camera
    except ImportError as e:
        _log.warning(f"Camera of type: '{config.camera_type}' failed to import.  Falling back to default camera impl.")
        _log.warning(e)

        import control.camera

        return control.camera.DefaultCamera(
            config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_set_strobe_delay_ms_fn
        )


class SimulatedCamera(AbstractCamera):
    @staticmethod
    def debug_log(method):
        import inspect

        @functools.wraps(method)
        def _logged_method(self, *args, **kwargs):
            kwargs_pairs = tuple(f"{k}={v}" for (k, v) in kwargs.items())
            args_str = tuple(str(a) for a in args)
            current_frame = inspect.currentframe()
            self._log.debug(
                f"{inspect.getouterframes(current_frame)[1][3]} -> {method.__name__}({','.join(args_str + kwargs_pairs)})"
            )
            return method(self, *args, **kwargs)

        return _logged_method

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._frame_id = 0
        self._current_raw_frame = None
        self._current_frame = None

        self._exposure_time_ms = None
        self.set_exposure_time(20)  # Just some random sane default since that isn't specified in our config.
        self._frame_format = CameraFrameFormat.RAW
        self._pixel_format = None
        self.set_pixel_format(self._config.default_pixel_format)
        self._resolution = None
        self.set_resolution(self._config.default_resolution[0], self._config.default_resolution[1])
        self._analog_gain = None
        self.set_analog_gain(0)
        self._white_balance_gains = None
        self.set_white_balance_gains(1.0, 1.0, 1.0)
        self._black_level = None
        self.set_black_level(0)
        self._acquisition_mode = None
        self.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        self._roi = (0, 0, self.get_resolution()[0], self.get_resolution()[1])
        self._temperature_setpoint = None
        self._continue_streaming = False
        self._streaming_thread: Optional[threading.Thread] = None
        self._last_trigger_timestamp = 0

        # This is for the migration to AbstractCamera.  It helps us find methods/properties that
        # some cameras had in the pre-AbstractCamera days.
        self._missing_methods = {}

    class MissingAttribImpl:
        name_to_val = {}

        def __init__(self, name):
            self._log = squid.logging.get_logger(f"MissingAttribImpl({name})")
            self._val = self.name_to_val.get(name, None)

        def __get__(self, instance, owner):
            self._log.debug("Get")
            return self._val

        def __set__(self, instance, value):
            self._log.debug(f"Set={value}")
            self._val = value

        def __call__(self, *args, **kwargs):
            kwarg_pairs = [f"{k}={v}" for (k, v) in kwargs.items()]
            args_str = [str(a) for a in args]
            self._log.debug(f"Called(*args, **kwargs) -> Called({','.join(args_str)}, {','.join(kwarg_pairs)}")
            return self._val

    def __getattr__(self, item):
        self._log.warning(f"Creating placeholder missing method: {item}")
        return self._missing_methods.get(item, SimulatedCamera.MissingAttribImpl(item))

    @debug_log
    def set_exposure_time(self, exposure_time_ms: float):
        self._exposure_time_ms = exposure_time_ms

    @debug_log
    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    @debug_log
    def get_strobe_time(self):
        return 3  # Just some arbitrary non-zero number so we test code that relies on this.

    @debug_log
    def get_exposure_limits(self) -> Tuple[float, float]:
        return 1, 1000

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
    def get_resolutions(self) -> Sequence[Tuple[int, int]]:
        return [(1920, 1080), (2000, 2000), (3000, 2000)]

    @debug_log
    def set_analog_gain(self, analog_gain: float):
        valid_range = self.get_gain_range()
        if analog_gain > valid_range.max_gain or analog_gain < valid_range.min_gain:
            raise ValueError("Gain outside valid range.")

        self._analog_gain = analog_gain

    @debug_log
    def get_analog_gain(self) -> float:
        return self._analog_gain

    @debug_log
    def get_gain_range(self) -> CameraGainRange:
        # Arbitrary, just something to test with
        return CameraGainRange(min_gain=0.0, max_gain=100.0, gain_step=2.0)

    def _start_streaming_thread(self):
        def stream_fn():
            self._log.info("Starting streaming thread...")
            last_frame_time = time.time()
            while self._continue_streaming:
                time_since = time.time() - last_frame_time
                # use self._exposure_time and _acquisition_mode so as not to spam the logs,
                # but this could case issues if subclassed for testing.
                if (
                    self._exposure_time_ms / 1000.0
                ) - time_since <= 0 and self._acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                    self._next_frame()
                    last_frame_time = time.time()
                time.sleep(0.001)
            self._log.info("Stopping streaming...")

        self._streaming_thread = threading.Thread(target=stream_fn, daemon=True)
        self._streaming_thread.start()

    @debug_log
    def start_streaming(self):
        if self._streaming_thread:
            if self._streaming_thread.is_alive() and self._continue_streaming:
                self._log.info("Already streaming, not starting again.")
                return
            elif self._streaming_thread.is_alive() and not self._continue_streaming:
                self._log.info("Looks like streaming is shutting down, waiting before restarting.")
                timeout_time = time.time() + 1
                while self._streaming_thread.is_alive() and timeout_time < time.time():
                    time.sleep(0.001)
                if self._streaming_thread.is_alive():
                    raise CameraError("Cannot start streaming, camera is inconsisten state")

        self._continue_streaming = True
        self._start_streaming_thread()

    @debug_log
    def stop_streaming(self):
        self._continue_streaming = False
        if self._streaming_thread:
            self._streaming_thread.join()

    @debug_log
    def get_is_streaming(self):
        return self._streaming_thread and self._streaming_thread.is_alive()

    @debug_log
    def read_camera_frame(self) -> CameraFrame:
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
    def send_trigger(self, illumination_time: Optional[float] = None):
        if self._acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            self._log.warning("Sending triggers in continuous acquisition mode is not allowed.")
            return
        self._next_frame()

    @debug_log
    def _next_frame(self):
        (width, height) = self.get_resolution()
        if self.get_frame_id() == 0:
            if self.get_pixel_format() == CameraPixelFormat.MONO8:
                self._current_raw_frame = np.random.randint(255, size=(height, width), dtype=np.uint8)
                self._current_raw_frame[height // 2 - 99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = 200
            elif self.get_pixel_format() == CameraPixelFormat.MONO12:
                self._current_raw_frame = np.random.randint(4095, size=(height, width), dtype=np.uint16)
                self._current_raw_frame[height // 2 - 99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = (
                    200 * 16
                )
                self._current_raw_frame = self._current_raw_frame << 4
            elif self.get_pixel_format() == CameraPixelFormat.MONO16:
                self._current_raw_frame = np.random.randint(65535, size=(height, width), dtype=np.uint16)
                self._current_raw_frame[height // 2 - 99 : height // 2 + 100, width // 2 - 99 : width // 2 + 100] = (
                    200 * 256
                )
            else:
                raise NotImplementedError(f"Simulated camera does not support pixel_format={self.get_pixel_format()}")
        else:
            self._current_raw_frame = np.roll(self._current_raw_frame, 10, axis=0)

        self._frame_id += 1

        self._current_frame = CameraFrame(
            frame_id=self._frame_id,
            timestamp=time.time(),
            frame=self._process_raw_frame(self._current_raw_frame),
            frame_format=self.get_frame_format(),
            frame_pixel_format=self.get_pixel_format(),
        )

        self._propogate_frame(self._current_frame)

    @debug_log
    def get_ready_for_trigger(self) -> bool:
        return time.time() - self._last_trigger_timestamp > self.get_exposure_time()

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
