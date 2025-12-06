# set QT_API environment variable
import os

os.environ["QT_API"] = "pyqt5"

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import control.utils as utils
from control._def import *

from queue import Queue
from threading import Thread
import time
import numpy as np
from datetime import datetime


class SpectrumStreamHandler(QObject):
    spectrum_to_display = Signal(np.ndarray)
    spectrum_to_write = Signal(np.ndarray)
    signal_new_spectrum_received = Signal()

    def __init__(self) -> None:
        QObject.__init__(self)
        self.fps_display: float = 30
        self.fps_save: float = 1
        self.timestamp_last_display: float = 0
        self.timestamp_last_save: float = 0

        self.save_spectrum_flag: bool = False

        # for fps measurement
        self.timestamp_last: int = 0
        self.counter: int = 0
        self.fps_real: int = 0

    def start_recording(self) -> None:
        self.save_spectrum_flag = True

    def stop_recording(self) -> None:
        self.save_spectrum_flag = False

    def set_display_fps(self, fps: float) -> None:
        self.fps_display = fps

    def set_save_fps(self, fps: float) -> None:
        self.fps_save = fps

    def on_new_measurement(self, data: np.ndarray) -> None:
        self.signal_new_spectrum_received.emit()
        # measure real fps
        timestamp_now = round(time.time())
        if timestamp_now == self.timestamp_last:
            self.counter = self.counter + 1
        else:
            self.timestamp_last = timestamp_now
            self.fps_real = self.counter
            self.counter = 0
            print("real spectrometer fps is " + str(self.fps_real))
        # send image to display
        time_now = time.time()
        if time_now - self.timestamp_last_display >= 1 / self.fps_display:
            self.spectrum_to_display.emit(data)
            self.timestamp_last_display = time_now
        # send image to write
        if (
            self.save_spectrum_flag
            and time_now - self.timestamp_last_save >= 1 / self.fps_save
        ):
            self.spectrum_to_write.emit(data)
            self.timestamp_last_save = time_now


class SpectrumSaver(QObject):
    stop_recording = Signal()

    def __init__(self) -> None:
        QObject.__init__(self)
        self.base_path: str = "./"
        self.experiment_ID: str = ""
        self.max_num_file_per_folder: int = 1000
        self.queue: Queue = Queue(10)  # max 10 items in the queue
        self.stop_signal_received: bool = False
        self.thread: Thread = Thread(target=self.process_queue)
        self.thread.start()
        self.counter: int = 0
        self.recording_start_time: float = 0
        self.recording_time_limit: float = -1

    def process_queue(self) -> None:
        while True:
            # stop the thread if stop signal is received
            if self.stop_signal_received:
                return
            # process the queue
            try:
                data = self.queue.get(timeout=0.1)
                folder_ID = int(self.counter / self.max_num_file_per_folder)
                file_ID = int(self.counter % self.max_num_file_per_folder)
                # create a new folder
                if file_ID == 0:
                    utils.ensure_directory_exists(
                        os.path.join(self.base_path, self.experiment_ID, str(folder_ID))
                    )

                saving_path = os.path.join(
                    self.base_path,
                    self.experiment_ID,
                    str(folder_ID),
                    str(file_ID) + ".csv",
                )
                np.savetxt(saving_path, data, delimiter=",")

                self.counter = self.counter + 1
                self.queue.task_done()
            except Exception:
                pass

    def enqueue(self, data: np.ndarray) -> None:
        try:
            self.queue.put_nowait(data)
            if (self.recording_time_limit > 0) and (
                time.time() - self.recording_start_time >= self.recording_time_limit
            ):
                self.stop_recording.emit()
            # when using self.queue.put(str_), program can be slowed down despite multithreading because of the block and the GIL
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
            # generate unique experiment ID
            self.experiment_ID = (
                experiment_ID
                + "_spectrum_"
                + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
            )
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        # create a new folder
        try:
            os.mkdir(os.path.join(self.base_path, self.experiment_ID))
            # to do: save configuration
        except Exception:
            pass
        # reset the counter
        self.counter = 0

    def close(self) -> None:
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()
