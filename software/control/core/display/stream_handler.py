from dataclasses import dataclass
import os
import time
from datetime import datetime
from queue import Queue
from threading import Thread, Lock
from typing import Callable

import cv2
import imageio as iio
import numpy as np
from qtpy.QtCore import QObject, Signal

from control import utils
import control._def
from control._def import Acquisition
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
            if control._def.PRINT_CAMERA_FPS:
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


class QtStreamHandler(QObject):

    image_to_display = Signal(np.ndarray)
    packet_image_to_write = Signal(np.ndarray, int, float)
    signal_new_frame_received = Signal()

    def __init__(self, display_resolution_scaling=1, accept_new_frame_fn: Callable[[], bool] = lambda: True):
        super().__init__()

        functions = StreamHandlerFunctions(
            image_to_display=self.image_to_display.emit,
            packet_image_to_write=self.packet_image_to_write.emit,
            signal_new_frame_received=self.signal_new_frame_received.emit,
            accept_new_frame=accept_new_frame_fn,
        )
        self._handler = StreamHandler(
            handler_functions=functions, display_resolution_scaling=display_resolution_scaling
        )

    def get_frame_callback(self) -> Callable[[CameraFrame], None]:
        return self._handler.on_new_frame

    def start_recording(self):
        self._handler.start_recording()

    def stop_recording(self):
        self._handler.stop_recording()

    def set_display_fps(self, fps):
        self._handler.set_display_fps(fps)

    def set_save_fps(self, fps):
        self._handler.set_save_fps(fps)

    def set_display_resolution_scaling(self, display_resolution_scaling):
        self._handler.set_display_resolution_scaling(display_resolution_scaling)


class ImageSaver(QObject):
    stop_recording = Signal()

    def __init__(self, image_format=Acquisition.IMAGE_FORMAT):
        QObject.__init__(self)
        self.base_path = "./"
        self.experiment_ID = ""
        self.image_format = image_format
        self.max_num_image_per_folder = 1000
        self.queue = Queue(10)  # max 10 items in the queue
        self.image_lock = Lock()
        self.stop_signal_received = False
        self.thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()
        self.counter = 0
        self.recording_start_time = 0
        self.recording_time_limit = -1

    def process_queue(self):
        while True:
            # stop the thread if stop signal is received
            if self.stop_signal_received:
                return
            # process the queue
            try:
                [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                folder_ID = int(self.counter / self.max_num_image_per_folder)
                file_ID = int(self.counter % self.max_num_image_per_folder)
                # create a new folder
                if file_ID == 0:
                    utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID, str(folder_ID)))

                if image.dtype == np.uint16:
                    # need to use tiff when saving 16 bit images
                    saving_path = os.path.join(
                        self.base_path, self.experiment_ID, str(folder_ID), str(file_ID) + "_" + str(frame_ID) + ".tiff"
                    )
                    iio.imwrite(saving_path, image)
                else:
                    saving_path = os.path.join(
                        self.base_path,
                        self.experiment_ID,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_ID) + "." + self.image_format,
                    )
                    cv2.imwrite(saving_path, image)

                self.counter = self.counter + 1
                self.queue.task_done()
                self.image_lock.release()
            except:
                pass

    def enqueue(self, image, frame_ID, timestamp):
        try:
            self.queue.put_nowait([image, frame_ID, timestamp])
            if (self.recording_time_limit > 0) and (
                time.time() - self.recording_start_time >= self.recording_time_limit
            ):
                self.stop_recording.emit()
            # when using self.queue.put(str_), program can be slowed down despite multithreading because of the block and the GIL
        except:
            print("imageSaver queue is full, image discarded")

    def set_base_path(self, path):
        self.base_path = path

    def set_recording_time_limit(self, time_limit):
        self.recording_time_limit = time_limit

    def start_new_experiment(self, experiment_ID, add_timestamp=True):
        if add_timestamp:
            # generate unique experiment ID
            self.experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        # create a new folder
        try:
            utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
            # to do: save configuration
        except:
            pass
        # reset the counter
        self.counter = 0

    def close(self):
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()


class ImageSaver_Tracking(QObject):
    def __init__(self, base_path, image_format="bmp"):
        QObject.__init__(self)
        self.base_path = base_path
        self.image_format = image_format
        self.max_num_image_per_folder = 1000
        self.queue = Queue(100)  # max 100 items in the queue
        self.image_lock = Lock()
        self.stop_signal_received = False
        self.thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def process_queue(self):
        while True:
            # stop the thread if stop signal is received
            if self.stop_signal_received:
                return
            # process the queue
            try:
                [image, frame_counter, postfix] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                folder_ID = int(frame_counter / self.max_num_image_per_folder)
                file_ID = int(frame_counter % self.max_num_image_per_folder)
                # create a new folder
                if file_ID == 0:
                    utils.ensure_directory_exists(os.path.join(self.base_path, str(folder_ID)))
                if image.dtype == np.uint16:
                    saving_path = os.path.join(
                        self.base_path,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_counter) + "_" + postfix + ".tiff",
                    )
                    iio.imwrite(saving_path, image)
                else:
                    saving_path = os.path.join(
                        self.base_path,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_counter) + "_" + postfix + "." + self.image_format,
                    )
                    cv2.imwrite(saving_path, image)
                self.queue.task_done()
                self.image_lock.release()
            except:
                pass

    def enqueue(self, image, frame_counter, postfix):
        try:
            self.queue.put_nowait([image, frame_counter, postfix])
        except:
            print("imageSaver queue is full, image discarded")

    def close(self):
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()
