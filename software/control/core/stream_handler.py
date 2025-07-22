from dataclasses import dataclass
import time
from typing import Callable

import numpy as np
import cv2

from control import utils
from control import _def
from squid.abc import CameraFrame


@dataclass
class StreamHandlerFunctions:
    image_to_display: Callable[[np.ndarray], None]
    packet_image_to_write: Callable[[np.ndarray, int, float], None]
    signal_new_frame_received: Callable[[], None]
    accept_new_frame: Callable[[], bool]


NoOpStreamHandlerFunctions = StreamHandlerFunctions(
    image_to_display=lambda x: None,
    packet_image_to_write=lambda a, i, f: None,
    signal_new_frame_received=lambda: None,
    accept_new_frame=lambda: True,
)


class StreamHandler:
    def __init__(
        self,
        handler_functions: StreamHandlerFunctions,
        display_resolution_scaling=1,
    ):
        self.fps_display = 1
        self.fps_save = 1
        self.fps_track = 1
        self.timestamp_last_display = 0
        self.timestamp_last_save = 0
        self.timestamp_last_track = 0

        self.display_resolution_scaling = display_resolution_scaling

        self.save_image_flag = False
        self.handler_busy = False

        # for fps measurement
        self.timestamp_last = 0
        self.counter = 0
        self.fps_real = 0

        self._fns: StreamHandlerFunctions = handler_functions if handler_functions else NoOpStreamHandlerFunctions

    def start_recording(self):
        self.save_image_flag = True

    def stop_recording(self):
        self.save_image_flag = False

    def set_display_fps(self, fps):
        self.fps_display = fps

    def set_save_fps(self, fps):
        self.fps_save = fps

    def set_display_resolution_scaling(self, display_resolution_scaling):
        self.display_resolution_scaling = display_resolution_scaling / 100
        print(self.display_resolution_scaling)

    def set_functions(self, functions: StreamHandlerFunctions):
        if not functions:
            functions = NoOpStreamHandlerFunctions
        self._fns = functions

    def on_new_frame(self, frame: CameraFrame):
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
        if self.save_image_flag and time_now - self.timestamp_last_save >= 1 / self.fps_save:
            if frame.is_color():
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            self._fns.packet_image_to_write(image, frame.frame_id, frame.timestamp)
            self.timestamp_last_save = time_now

        self.handler_busy = False
