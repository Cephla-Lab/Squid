from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from qtpy.QtCore import QObject, Signal

from squid.core.abc import CameraFrame
from squid.storage.stream_handler import StreamHandler, StreamHandlerFunctions


class QtStreamHandler(QObject):
    image_to_display = Signal(np.ndarray)
    packet_image_to_write = Signal(np.ndarray, int, float)
    signal_new_frame_received = Signal()
    capture = Signal(np.ndarray, object)

    def __init__(
        self,
        display_resolution_scaling: float = 1,
        accept_new_frame_fn: Callable[[], bool] = lambda: True,
        handler: Optional[StreamHandler] = None,
    ) -> None:
        super().__init__()

        functions = StreamHandlerFunctions(
            image_to_display=self.image_to_display.emit,
            packet_image_to_write=self.packet_image_to_write.emit,
            signal_new_frame_received=self.signal_new_frame_received.emit,
            accept_new_frame=accept_new_frame_fn,
            capture=self.capture.emit,
        )
        if handler is None:
            self._handler = StreamHandler(
                handler_functions=functions,
                display_resolution_scaling=display_resolution_scaling,
            )
        else:
            handler.set_functions(functions, merge=True)
            handler.display_resolution_scaling = display_resolution_scaling
            self._handler = handler

    def get_frame_callback(self) -> Callable[[CameraFrame], None]:
        return self._handler.on_new_frame

    def start_recording(self) -> None:
        self._handler.start_recording()

    def stop_recording(self) -> None:
        self._handler.stop_recording()

    def set_display_fps(self, fps: float) -> None:
        self._handler.set_display_fps(fps)

    def set_save_fps(self, fps: float) -> None:
        self._handler.set_save_fps(fps)

    def set_display_resolution_scaling(self, display_resolution_scaling: float) -> None:
        self._handler.set_display_resolution_scaling(display_resolution_scaling)
