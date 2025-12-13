from dataclasses import dataclass
import time
from typing import Callable, Optional

import cv2
import numpy as np

import squid.core.utils.hardware_utils as utils
import _def
from squid.core.abc import CameraFrame


@dataclass
class StreamHandlerFunctions:
    image_to_display: Callable[[np.ndarray], None]
    packet_image_to_write: Callable[[np.ndarray, int, float], None]
    signal_new_frame_received: Callable[[], None]
    accept_new_frame: Callable[[], bool]
    capture: Callable[[np.ndarray, object], None] = lambda _image, _info: None


NoOpStreamHandlerFunctions = StreamHandlerFunctions(
    image_to_display=lambda x: None,
    packet_image_to_write=lambda a, i, f: None,
    signal_new_frame_received=lambda: None,
    accept_new_frame=lambda: True,
    capture=lambda _image, _info: None,
)


class StreamHandler:
    def __init__(
        self,
        handler_functions: StreamHandlerFunctions,
        display_resolution_scaling: float = 1,
    ) -> None:
        self.fps_display: float = 1
        self.fps_save: float = 1
        self.fps_track: float = 1
        self.timestamp_last_display: float = 0
        self.timestamp_last_save: float = 0
        self.timestamp_last_track: float = 0

        self.display_resolution_scaling: float = display_resolution_scaling

        self.save_image_flag: bool = False
        self.handler_busy: bool = False

        # for fps measurement
        self.timestamp_last: int = 0
        self.counter: int = 0
        self.fps_real: int = 0

        self._fns: StreamHandlerFunctions = (
            handler_functions if handler_functions else NoOpStreamHandlerFunctions
        )

    def start_recording(self) -> None:
        self.save_image_flag = True

    def stop_recording(self) -> None:
        self.save_image_flag = False

    def set_display_fps(self, fps: float) -> None:
        self.fps_display = fps

    def set_save_fps(self, fps: float) -> None:
        self.fps_save = fps

    def set_display_resolution_scaling(self, display_resolution_scaling: float) -> None:
        self.display_resolution_scaling = display_resolution_scaling / 100
        print(self.display_resolution_scaling)

    def set_functions(self, functions: Optional[StreamHandlerFunctions], *, merge: bool = False) -> None:
        if not functions:
            functions = NoOpStreamHandlerFunctions
        if not merge or self._fns is NoOpStreamHandlerFunctions:
            self._fns = functions
            return

        previous = self._fns

        def _chain_call(a: Callable, b: Callable) -> Callable:
            def _chained(*args, **kwargs):
                a(*args, **kwargs)
                b(*args, **kwargs)

            return _chained

        def _chain_accept(a: Callable[[], bool], b: Callable[[], bool]) -> Callable[[], bool]:
            return lambda: bool(a()) and bool(b())

        self._fns = StreamHandlerFunctions(
            image_to_display=_chain_call(previous.image_to_display, functions.image_to_display),
            packet_image_to_write=_chain_call(previous.packet_image_to_write, functions.packet_image_to_write),
            signal_new_frame_received=_chain_call(
                previous.signal_new_frame_received, functions.signal_new_frame_received
            ),
            accept_new_frame=_chain_accept(previous.accept_new_frame, functions.accept_new_frame),
            capture=_chain_call(previous.capture, functions.capture),
        )

    def on_new_frame(self, frame: CameraFrame) -> None:
        if not self._fns.accept_new_frame():
            return

        self.handler_busy = True
        self._fns.signal_new_frame_received()

        # measure real fps
        timestamp_now = round(time.time())
        if timestamp_now == self.timestamp_last:
            self.counter = self.counter + 1
        else:
            self.timestamp_last = timestamp_now
            self.fps_real = self.counter
            self.counter = 0
            if _def.PRINT_CAMERA_FPS:
                print("real camera fps is " + str(self.fps_real))

        # crop image
        image = np.squeeze(frame.frame)

        # send image to display
        time_now = time.time()
        if time_now - self.timestamp_last_display >= 1 / self.fps_display:
            self._fns.image_to_display(
                utils.crop_image(
                    image,
                    round(image.shape[1] * self.display_resolution_scaling),
                    round(image.shape[0] * self.display_resolution_scaling),
                )
            )
            self.timestamp_last_display = time_now

        # send image to write
        if (
            self.save_image_flag
            and time_now - self.timestamp_last_save >= 1 / self.fps_save
        ):
            if frame.is_color():
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            self._fns.packet_image_to_write(image, frame.frame_id, frame.timestamp)
            self.timestamp_last_save = time_now

        self.handler_busy = False

    def on_new_image(
        self,
        image: np.ndarray,
        frame_id: int = 0,
        timestamp: Optional[float] = None,
        *,
        is_color: Optional[bool] = None,
        respect_accept_new_frame: bool = False,
        capture_info: Optional[object] = None,
    ) -> None:
        """Push an image into the StreamHandler without a CameraFrame wrapper.

        Used for frames produced by worker threads (e.g., acquisition/autofocus)
        where camera callback delivery is disabled/stopped.
        """
        if respect_accept_new_frame and not self._fns.accept_new_frame():
            return

        if timestamp is None:
            timestamp = time.time()

        self.handler_busy = True
        self._fns.signal_new_frame_received()

        image = np.squeeze(image)

        if capture_info is not None:
            try:
                self._fns.capture(image, capture_info)
            except Exception:
                # Never let UI fanout failures break the worker/data-plane path.
                pass

        time_now = time.time()
        if time_now - self.timestamp_last_display >= 1 / self.fps_display:
            self._fns.image_to_display(
                utils.crop_image(
                    image,
                    round(image.shape[1] * self.display_resolution_scaling),
                    round(image.shape[0] * self.display_resolution_scaling),
                )
            )
            self.timestamp_last_display = time_now

        if self.save_image_flag and time_now - self.timestamp_last_save >= 1 / self.fps_save:
            if is_color is None:
                is_color = len(image.shape) > 2
            if is_color:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            self._fns.packet_image_to_write(image, frame_id, float(timestamp))
            self.timestamp_last_save = time_now

        self.handler_busy = False
