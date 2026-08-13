"""ActiveCameraFacade: a single AbstractCamera-shaped object that delegates to whichever
concrete camera is currently active.

Why: ~15 components across the app take the camera in their constructor and cache it
forever (LiveController, MultiPointWorker, NavigationViewer, ScanCoordinates, ...).
Storing this facade at microscope.camera lets all of them keep working across camera
switches without modification. Identity-sensitive code (per-camera settings widgets,
settings cache) must use the concrete cameras via get_concrete_camera()/all_cameras().
"""

import threading
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import squid.logging
from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraFrame,
    CameraFrameFormat,
    CameraGainRange,
)
from squid.config import CameraPixelFormat


class ActiveCameraFacade(AbstractCamera):
    def __init__(self, cameras: Dict[int, AbstractCamera], active_id: int):
        # Deliberately does NOT call AbstractCamera.__init__: the facade owns no camera
        # config and no hardware-trigger functions — each concrete camera does. Every
        # base-class helper that touches self._config is overridden below to delegate.
        if active_id not in cameras:
            raise ValueError(f"active_id {active_id} not in cameras {sorted(cameras)}")
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._cameras: Dict[int, AbstractCamera] = dict(cameras)
        self._active_id = active_id
        self._lock = threading.RLock()
        self._facade_callbacks: List[Tuple[int, Callable[[CameraFrame], None]]] = []
        self._next_callback_id = 1
        self._facade_callbacks_enabled = True
        for camera_id, camera in self._cameras.items():
            camera.add_frame_callback(self._make_forwarder(camera_id))

    # ---- facade management ----

    def _make_forwarder(self, camera_id: int) -> Callable[[CameraFrame], None]:
        def _forward(frame: CameraFrame):
            with self._lock:
                if camera_id != self._active_id or not self._facade_callbacks_enabled:
                    return
                callbacks = list(self._facade_callbacks)
            for _, callback in callbacks:
                callback(frame)

        return _forward

    def _active(self) -> AbstractCamera:
        with self._lock:
            return self._cameras[self._active_id]

    def set_active(self, camera_id: int) -> None:
        with self._lock:
            if camera_id not in self._cameras:
                raise ValueError(f"Unknown camera id {camera_id}; have {sorted(self._cameras)}")
            self._active_id = camera_id

    def get_active_id(self) -> int:
        with self._lock:
            return self._active_id

    def get_active_camera(self) -> AbstractCamera:
        return self._active()

    def get_concrete_camera(self, camera_id: int) -> AbstractCamera:
        with self._lock:
            return self._cameras[camera_id]

    def all_cameras(self) -> Dict[int, AbstractCamera]:
        with self._lock:
            return dict(self._cameras)

    # ---- callback registry (facade-level; registrations survive switches) ----

    def add_frame_callback(self, frame_callback: Callable[[CameraFrame], None]) -> int:
        with self._lock:
            callback_id = self._next_callback_id
            self._next_callback_id += 1
            self._facade_callbacks.append((callback_id, frame_callback))
            return callback_id

    def remove_frame_callback(self, callback_id):
        with self._lock:
            self._facade_callbacks = [t for t in self._facade_callbacks if t[0] != callback_id]

    def enable_callbacks(self, enabled: bool):
        with self._lock:
            self._facade_callbacks_enabled = enabled

    def get_callbacks_enabled(self) -> bool:
        with self._lock:
            return self._facade_callbacks_enabled

    # ---- pure delegation ----

    def set_exposure_time(self, exposure_time_ms: float):
        return self._active().set_exposure_time(exposure_time_ms)

    def get_exposure_time(self) -> float:
        return self._active().get_exposure_time()

    def get_exposure_limits(self) -> Tuple[float, float]:
        return self._active().get_exposure_limits()

    def get_strobe_time(self) -> float:
        return self._active().get_strobe_time()

    def get_total_frame_time(self) -> float:
        return self._active().get_total_frame_time()

    def set_frame_format(self, frame_format: CameraFrameFormat):
        return self._active().set_frame_format(frame_format)

    def get_frame_format(self) -> CameraFrameFormat:
        return self._active().get_frame_format()

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        return self._active().set_pixel_format(pixel_format)

    def get_pixel_format(self) -> CameraPixelFormat:
        return self._active().get_pixel_format()

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return self._active().get_available_pixel_formats()

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        return self._active().set_binning(binning_factor_x, binning_factor_y)

    def get_binning(self) -> Tuple[int, int]:
        return self._active().get_binning()

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return self._active().get_binning_options()

    def get_resolution(self) -> Tuple[int, int]:
        return self._active().get_resolution()

    def get_pixel_size_unbinned_um(self) -> float:
        return self._active().get_pixel_size_unbinned_um()

    def get_pixel_size_binned_um(self) -> float:
        return self._active().get_pixel_size_binned_um()

    def set_analog_gain(self, analog_gain: float):
        return self._active().set_analog_gain(analog_gain)

    def get_analog_gain(self) -> float:
        return self._active().get_analog_gain()

    def get_gain_range(self) -> CameraGainRange:
        return self._active().get_gain_range()

    def start_streaming(self):
        return self._active().start_streaming()

    def stop_streaming(self):
        return self._active().stop_streaming()

    def get_is_streaming(self):
        return self._active().get_is_streaming()

    def get_crop_size(self) -> Tuple[int, int]:
        return self._active().get_crop_size()

    def get_fov_size_mm(self) -> float:
        return self._active().get_fov_size_mm()

    def set_software_crop_ratio(self, width_ratio: float, height_ratio: float):
        return self._active().set_software_crop_ratio(width_ratio, height_ratio)

    def read_camera_frame(self) -> Optional[CameraFrame]:
        return self._active().read_camera_frame()

    def get_frame_id(self) -> int:
        return self._active().get_frame_id()

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        return self._active().get_white_balance_gains()

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        return self._active().set_white_balance_gains(red_gain, green_gain, blue_gain)

    def set_auto_white_balance_gains(self, on: bool):
        return self._active().set_auto_white_balance_gains(on)

    def set_black_level(self, black_level: float):
        return self._active().set_black_level(black_level)

    def get_black_level(self) -> float:
        return self._active().get_black_level()

    def set_acquisition_mode(self, acquisition_mode: CameraAcquisitionMode):
        # Delegate the PUBLIC method so the concrete camera enforces its own
        # hw_trigger_fn requirement (an unwired camera must reject HARDWARE).
        return self._active().set_acquisition_mode(acquisition_mode)

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        raise NotImplementedError("Facade delegates set_acquisition_mode; this must never be called.")

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        return self._active().get_acquisition_mode()

    def send_trigger(self, illumination_time: Optional[float] = None):
        return self._active().send_trigger(illumination_time)

    def get_ready_for_trigger(self) -> bool:
        return self._active().get_ready_for_trigger()

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        return self._active().set_region_of_interest(offset_x, offset_y, width, height)

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self._active().get_region_of_interest()

    def set_temperature(self, temperature_deg_c: Optional[float]):
        return self._active().set_temperature(temperature_deg_c)

    def get_temperature(self) -> float:
        return self._active().get_temperature()

    def set_temperature_reading_callback(self, callback: Callable):
        return self._active().set_temperature_reading_callback(callback)

    def supports_hardware_trigger(self) -> bool:
        return self._active().supports_hardware_trigger()

    def close(self):
        for camera_id, camera in self.all_cameras().items():
            try:
                camera.close()
            except Exception:
                self._log.exception(f"Error closing camera {camera_id}")
