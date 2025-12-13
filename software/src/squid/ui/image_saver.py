from __future__ import annotations

import os
import time
from datetime import datetime
from queue import Queue
from threading import Lock, Thread

import cv2
import imageio as iio
import numpy as np
from qtpy.QtCore import QObject, Signal

import squid.core.utils.hardware_utils as utils
from _def import Acquisition


class ImageSaver(QObject):
    stop_recording = Signal()

    def __init__(self, image_format: str = Acquisition.IMAGE_FORMAT) -> None:
        QObject.__init__(self)
        self.base_path: str = "./"
        self.experiment_ID: str = ""
        self.image_format: str = image_format
        self.max_num_image_per_folder: int = 1000
        self.queue: Queue = Queue(10)  # max 10 items in the queue
        self.image_lock: Lock = Lock()
        self.stop_signal_received: bool = False
        self.thread: Thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()
        self.counter: int = 0
        self.recording_start_time: float = 0
        self.recording_time_limit: float = -1

    def process_queue(self) -> None:
        while True:
            if self.stop_signal_received:
                return
            try:
                [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                folder_ID = int(self.counter / self.max_num_image_per_folder)
                file_ID = int(self.counter % self.max_num_image_per_folder)
                if file_ID == 0:
                    utils.ensure_directory_exists(
                        os.path.join(self.base_path, self.experiment_ID, str(folder_ID))
                    )

                if image.dtype == np.uint16:
                    saving_path = os.path.join(
                        self.base_path,
                        self.experiment_ID,
                        str(folder_ID),
                        str(file_ID) + "_" + str(frame_ID) + ".tiff",
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
            except Exception:
                pass

    def enqueue(self, image: np.ndarray, frame_ID: int, timestamp: float) -> None:
        try:
            self.queue.put_nowait([image, frame_ID, timestamp])
            if (self.recording_time_limit > 0) and (
                time.time() - self.recording_start_time >= self.recording_time_limit
            ):
                self.stop_recording.emit()
        except Exception:
            print("imageSaver queue is full, image discarded")

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def set_recording_time_limit(self, time_limit: float) -> None:
        self.recording_time_limit = time_limit

    def start_new_experiment(
        self, experiment_ID: str, add_timestamp: bool = True
    ) -> None:
        if add_timestamp:
            self.experiment_ID = (
                experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
            )
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        try:
            utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
        except Exception:
            pass
        self.counter = 0

    def close(self) -> None:
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()


class ImageSaver_Tracking(QObject):
    def __init__(self, base_path: str, image_format: str = "bmp") -> None:
        QObject.__init__(self)
        self.base_path: str = base_path
        self.image_format: str = image_format
        self.max_num_image_per_folder: int = 1000
        self.queue: Queue = Queue(100)  # max 100 items in the queue
        self.image_lock: Lock = Lock()
        self.stop_signal_received: bool = False
        self.thread: Thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def process_queue(self) -> None:
        while True:
            if self.stop_signal_received:
                return
            try:
                [image, frame_counter, postfix] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                folder_ID = int(frame_counter / self.max_num_image_per_folder)
                file_ID = int(frame_counter % self.max_num_image_per_folder)
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
            except Exception:
                pass

    def enqueue(self, image: np.ndarray, frame_counter: int, postfix: str) -> None:
        try:
            self.queue.put_nowait([image, frame_counter, postfix])
        except Exception:
            print("imageSaver queue is full, image discarded")

    def close(self) -> None:
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()

