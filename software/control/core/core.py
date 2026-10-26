# set QT_API environment variable
import os
import sys
import tempfile

# qt libraries
os.environ["QT_API"] = "pyqt5"
import qtpy
import pyqtgraph as pg
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *
from control.core import job_processing
from control.core.contrast_manager import ContrastManager
from control.core.live_controller import LiveController
from control.core.multi_point_worker import MultiPointWorker
from control.core.objective_store import ObjectiveStore
from control.core.scan_coordinates import ScanCoordinates
from control.core.stream_handler import StreamHandlerFunctions, StreamHandler
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from squid.abc import AbstractStage, AbstractCamera, CameraAcquisitionMode, CameraFrame
import control._def
import control.serial_peripherals as serial_peripherals
import control.tracking as tracking
import control.utils as utils
import control.utils_acquisition as utils_acquisition
import control.utils_channel as utils_channel
import squid.logging


from typing import List, Tuple, Optional, Dict, Any, Callable, TypeVar
from queue import Queue, Empty, Full
from threading import Thread, Lock
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from control.models import AcquisitionChannel
import time
import csv
import itertools
import json
import numpy as np
import pandas as pd
import cv2
import imageio as iio
import squid.abc
from control.core.plate_transform import resolve_rotation_deg, rotate_deg
import scipy.ndimage

if ENABLE_NL5:
    import control.NL5 as NL5
else:
    NL5 = TypeVar("NL5")


class QtStreamHandler(QObject):

    image_to_display = Signal(np.ndarray)
    packet_image_to_write = Signal(np.ndarray, int, float)
    signal_new_frame_received = Signal()

    def __init__(
        self,
        accept_new_frame_fn: Callable[[], bool] = lambda: True,
        force_display_fn: Callable[[], bool] = lambda: False,
    ):
        super().__init__()

        functions = StreamHandlerFunctions(
            image_to_display=self.image_to_display.emit,
            packet_image_to_write=self.packet_image_to_write.emit,
            signal_new_frame_received=self.signal_new_frame_received.emit,
            accept_new_frame=accept_new_frame_fn,
            force_display=force_display_fn,
        )
        self._handler = StreamHandler(handler_functions=functions)

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


log = squid.logging.get_logger(__name__)


@dataclass
class _DefaultRecordingChannel:
    """Stand-in for an AcquisitionChannel when no live channel is set during recording.

    save_image() and return_pseudo_colored_image() only consult `.name`; the other
    fields keep frames.csv rows uniform when no channel is available.
    """

    name: str = "live"
    exposure_time: float = 0.0
    analog_gain: float = 0.0
    illumination_intensity: float = 0.0


class ImageSaver(QObject):
    stop_recording = Signal()

    def __init__(self):
        QObject.__init__(self)
        self.base_path = "./"
        self.experiment_ID = ""
        self.max_num_image_per_folder = 1000
        self.queue = Queue(10)  # max 10 items in the queue
        self.image_lock = Lock()
        self.stop_signal_received = False
        self.counter = 0
        self.recording_start_time = 0
        self.recording_time_limit = -1
        self._channel_provider: Optional[Callable[[], Optional[Any]]] = None
        self._csv_file = None
        self._csv_writer = None
        self._dropped_count = 0
        self._last_queue_full_warning_ts = 0.0
        self._stop_requested_from_writer = False
        # Per-recording diagnostics (reset in start_new_experiment, summarized in stop_experiment).
        self._imwrite_ns_total = 0
        self._imwrite_ns_max = 0
        self._imwrite_count = 0
        self._silent_write_failures = 0
        self._queue_wait_ns_total = 0
        self._queue_wait_count = 0
        self._enqueue_call_count = 0
        # Start the saver thread last, after all state it touches has been initialized.
        self.thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def set_channel_provider(self, provider):
        """Register a callable returning the active live channel (or None).

        Called per-frame from the saver thread. Cross-thread reads of the
        channel reference are GIL-atomic, so no lock is needed; the GUI thread
        just swaps the reference when the user toggles channels.
        """
        self._channel_provider = provider

    def process_queue(self):
        while True:
            if self.stop_signal_received:
                return
            get_started_ns = time.perf_counter_ns()
            try:
                image, frame_id, timestamp = self.queue.get(timeout=0.1)
            except Empty:
                continue
            self._queue_wait_ns_total += time.perf_counter_ns() - get_started_ns
            self._queue_wait_count += 1
            try:
                # A Qt-queued enqueue slot can arrive after stop_experiment cleared state;
                # discard those frames instead of writing to <base>/0/.
                if not self.experiment_ID:
                    continue
                with self.image_lock:
                    folder_id = self.counter // self.max_num_image_per_folder
                    file_id = self.counter % self.max_num_image_per_folder
                    save_dir = os.path.join(self.base_path, self.experiment_ID, str(folder_id))
                    if file_id == 0:
                        utils.ensure_directory_exists(save_dir)

                    channel = (
                        self._channel_provider() if self._channel_provider else None
                    ) or _DefaultRecordingChannel()
                    channel_name = getattr(channel, "name", "live")
                    # cv2.imwrite handles both uint8 (BMP/PNG/TIFF) and uint16 (TIFF/PNG).
                    # ~10x faster than imageio for uint8, ~2-3x faster for uint16.
                    saving_path = utils_acquisition.get_image_filepath(
                        save_dir, str(file_id), channel_name, image.dtype
                    )
                    imwrite_started_ns = time.perf_counter_ns()
                    ok = cv2.imwrite(saving_path, image)
                    imwrite_ns = time.perf_counter_ns() - imwrite_started_ns
                    self._imwrite_ns_total += imwrite_ns
                    if imwrite_ns > self._imwrite_ns_max:
                        self._imwrite_ns_max = imwrite_ns

                    if not ok:
                        # cv2.imwrite returns False (without raising) on bad path, missing
                        # codec, unsupported dtype, etc. Don't bump counter or write a CSV
                        # row that would point at a file that isn't on disk.
                        self._silent_write_failures += 1
                        log.warning(f"cv2.imwrite returned False for {saving_path}; frame {frame_id} skipped")
                        continue

                    self._imwrite_count += 1
                    # Increment counter only after a verified-successful image write — if the
                    # CSV row write below raises, the next frame must not reuse this file_id
                    # and overwrite what we just saved.
                    self.counter += 1

                    if self._csv_writer is not None:
                        rel_path = os.path.relpath(saving_path, os.path.join(self.base_path, self.experiment_ID))
                        self._csv_writer.writerow(
                            [
                                frame_id,
                                datetime.fromtimestamp(timestamp).isoformat(),
                                channel_name,
                                getattr(channel, "exposure_time", 0.0),
                                getattr(channel, "analog_gain", 0.0),
                                getattr(channel, "illumination_intensity", 0.0),
                                rel_path,
                            ]
                        )
                        # No per-row flush: it's an fsync per frame on most filesystems
                        # and was the second-largest contributor to the FPS regression.
                        # stop_experiment() flushes via close().
            except OSError as e:
                if not self._stop_requested_from_writer:
                    self._stop_requested_from_writer = True
                    log.error(f"Writer fatal error: {e}; stopping recording")
                    self.stop_recording.emit()
                else:
                    log.warning(f"Writer error during shutdown: {e}")
            except Exception as e:
                log.warning(f"Failed to write frame {frame_id}: {e}")
            finally:
                self.queue.task_done()

    def enqueue(self, image, frame_id, timestamp):
        self._enqueue_call_count += 1
        try:
            self.queue.put_nowait([image, frame_id, timestamp])
        except Full:
            self._dropped_count += 1
            now = time.time()
            if now - self._last_queue_full_warning_ts >= 1.0:
                log.warning(f"Image queue full; frame {frame_id} dropped")
                self._last_queue_full_warning_ts = now
            return

        if self.recording_time_limit > 0 and time.time() - self.recording_start_time >= self.recording_time_limit:
            log.info(f"Auto-stopping: time limit reached ({self.recording_time_limit}s)")
            self.stop_recording.emit()

    def set_base_path(self, path):
        self.base_path = path

    def set_recording_time_limit(self, time_limit):
        self.recording_time_limit = time_limit

    def start_new_experiment(self, experiment_ID, add_timestamp=True):
        # Defensively close any prior recording in case the caller didn't.
        self.stop_experiment()

        if add_timestamp:
            self.experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        else:
            self.experiment_ID = experiment_ID
        self.recording_start_time = time.time()
        self.counter = 0
        self._dropped_count = 0
        self._last_queue_full_warning_ts = 0.0
        self._stop_requested_from_writer = False
        self._imwrite_ns_total = 0
        self._imwrite_ns_max = 0
        self._imwrite_count = 0
        self._silent_write_failures = 0
        self._queue_wait_ns_total = 0
        self._queue_wait_count = 0
        self._enqueue_call_count = 0

        experiment_dir = os.path.join(self.base_path, self.experiment_ID)
        try:
            utils.ensure_directory_exists(experiment_dir)
        except Exception as e:
            log.error(f"Failed to create experiment directory {experiment_dir}: {e}")
            self.experiment_ID = ""
            raise

        csv_path = os.path.join(experiment_dir, "frames.csv")
        try:
            self._csv_file = open(csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(
                ["frame_id", "timestamp_iso", "channel", "exposure_ms", "gain", "illumination_intensity", "file"]
            )
            self._csv_file.flush()
        except OSError as e:
            log.error(f"Failed to open {csv_path}: {e}")
            # If open() succeeded but writerow/flush raised, the handle is open — close it
            # before nulling the ref so we don't leak the fd.
            if self._csv_file is not None:
                try:
                    self._csv_file.close()
                except OSError:
                    pass
            self._csv_file = None
            self._csv_writer = None
            self.experiment_ID = ""
            raise

        log.info(f"Recording started: id={self.experiment_ID}, dir={experiment_dir}")
        if self._channel_provider is None:
            log.warning("channel_provider not set; frames tagged with default 'live' channel")
        elif self._channel_provider() is None:
            log.warning(
                "channel_provider returned None at recording start; "
                "frames will be tagged 'live' until a live channel is selected"
            )

    def stop_experiment(self):
        """Finalize the current recording: drain the queue, close frames.csv, log summary.

        Called by the widget on Stop and on time-limit auto-stop. Idempotent.
        Drains the queue first so any buffered frames land in the current
        experiment dir before experiment_ID is cleared — without that, the
        saver thread races and writes to a non-existent directory.
        """
        # Block until the saver thread has processed every buffered frame.
        # Safe because the caller has already stopped the streamHandler, so no
        # new items can be enqueued; queue.task_done() runs in finally so a
        # write exception cannot leave us hanging here.
        self.queue.join()
        if self._csv_file is not None:
            try:
                self._csv_file.close()
            finally:
                self._csv_file = None
                self._csv_writer = None
        if self.experiment_ID:
            duration = time.time() - self.recording_start_time
            log.info(
                f"Recording stopped: frames_saved={self.counter}, "
                f"dropped={self._dropped_count}, duration={duration:.1f}s"
            )
            if self._imwrite_count > 0:
                avg_imwrite_ms = self._imwrite_ns_total / self._imwrite_count / 1e6
                max_imwrite_ms = self._imwrite_ns_max / 1e6
                avg_queue_wait_ms = (
                    self._queue_wait_ns_total / self._queue_wait_count / 1e6 if self._queue_wait_count else 0.0
                )
                input_fps = self._enqueue_call_count / duration if duration > 0 else 0.0
                save_fps = self._imwrite_count / duration if duration > 0 else 0.0
                log.info(
                    f"Saver diagnostics: "
                    f"imwrite_avg={avg_imwrite_ms:.1f}ms, "
                    f"imwrite_max={max_imwrite_ms:.1f}ms, "
                    f"queue_wait_avg={avg_queue_wait_ms:.1f}ms, "
                    f"input={input_fps:.1f}fps ({self._enqueue_call_count} enqueues, {self._dropped_count} drops), "
                    f"saved={save_fps:.1f}fps, "
                    f"silent_write_failures={self._silent_write_failures}"
                )
            self.experiment_ID = ""

    def close(self):
        self.stop_experiment()
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


class ImageDisplay(QObject):
    image_to_display = Signal(np.ndarray)

    def __init__(self):
        QObject.__init__(self)
        self.queue = Queue(10)  # max 10 items in the queue
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
                [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                self.image_to_display.emit(image)
                self.image_lock.release()
                self.queue.task_done()
            except:
                pass
            time.sleep(0)

    # def enqueue(self,image,frame_ID,timestamp):
    def enqueue(self, image):
        try:
            self.queue.put_nowait([image, None, None])
            # when using self.queue.put(str_) instead of try + nowait, program can be slowed down despite multithreading because of the block and the GIL
            pass
        except:
            print("imageDisplay queue is full, image discarded")

    def emit_directly(self, image):
        self.image_to_display.emit(image)

    def close(self):
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()


class TrackingController(QObject):
    signal_tracking_stopped = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(AcquisitionChannel)

    def __init__(
        self,
        camera: AbstractCamera,
        microcontroller: Microcontroller,
        stage: AbstractStage,
        objectiveStore,
        liveController: LiveController,
        autofocusController,
        imageDisplayWindow,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.camera: AbstractCamera = camera
        self.microcontroller = microcontroller
        self.stage = stage
        self.objectiveStore = objectiveStore
        self.liveController = liveController
        self.autofocusController = autofocusController
        self.imageDisplayWindow = imageDisplayWindow
        self.tracker = tracking.Tracker_Image()

        self.tracking_time_interval_s = 0

        self.counter = 0
        self.experiment_ID = None
        self.base_path = None
        self.selected_configurations = []

        self.flag_stage_tracking_enabled = True
        self.flag_AF_enabled = False
        self.flag_save_image = False
        self.flag_stop_tracking_requested = False

        self.pixel_size_um = None
        self.objective = None

    def start_tracking(self):

        # save pre-tracking configuration
        self._log.info("start tracking")
        self.configuration_before_running_tracking = self.liveController.currentConfiguration

        # stop live
        if self.liveController.is_live:
            self.was_live_before_tracking = True
            self.liveController.stop_live()  # @@@ to do: also uncheck the live button
        else:
            self.was_live_before_tracking = False

        # disable callback
        if self.camera.get_callbacks_enabled():
            self.camera_callback_was_enabled_before_tracking = True
            self.camera.enable_callbacks(False)
        else:
            self.camera_callback_was_enabled_before_tracking = False

        # hide roi selector
        self.imageDisplayWindow.hide_ROI_selector()

        # run tracking
        self.flag_stop_tracking_requested = False
        # create a QThread object
        try:
            if self.thread.isRunning():
                self._log.info("*** previous tracking thread is still running ***")
                self.thread.terminate()
                self.thread.wait()
                self._log.info("*** previous tracking threaded manually stopped ***")
        except:
            pass
        self.thread = QThread()
        # create a worker object
        self.trackingWorker = TrackingWorker(self)
        # move the worker to the thread
        self.trackingWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.trackingWorker.run)
        self.trackingWorker.finished.connect(self._on_tracking_stopped)
        self.trackingWorker.finished.connect(self.trackingWorker.deleteLater)
        self.trackingWorker.finished.connect(self.thread.quit)
        self.trackingWorker.image_to_display.connect(self.slot_image_to_display)
        self.trackingWorker.image_to_display_multi.connect(self.slot_image_to_display_multi)
        self.trackingWorker.signal_current_configuration.connect(self.slot_current_configuration)
        # self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.thread.quit)
        # start the thread
        self.thread.start()

    def _on_tracking_stopped(self):

        # restore the previous selected mode
        self.signal_current_configuration.emit(self.configuration_before_running_tracking)
        self.liveController.set_microscope_mode(self.configuration_before_running_tracking)

        # re-enable callback
        if self.camera_callback_was_enabled_before_tracking:
            self.camera.enable_callbacks(True)
            self.camera_callback_was_enabled_before_tracking = False

        # re-enable live if it's previously on
        if self.was_live_before_tracking:
            self.liveController.start_live()

        # show ROI selector
        self.imageDisplayWindow.show_ROI_selector()

        # emit the acquisition finished signal to enable the UI
        self.signal_tracking_stopped.emit()
        QApplication.processEvents()

    def start_new_experiment(self, experiment_ID):  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        self.recording_start_time = time.time()
        # create a new folder
        try:
            experiment_dir = os.path.join(self.base_path, self.experiment_ID)
            utils.ensure_directory_exists(experiment_dir)
            # Save acquisition configuration via ConfigRepository
            self.liveController.microscope.config_repo.save_acquisition_output(
                output_dir=experiment_dir,
                objective=self.objectiveStore.current_objective,
                channels=self.selected_configurations,
                confocal_mode=self.liveController.is_confocal_mode(),
            )
        except:
            self._log.info("error in making a new folder")
            pass

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.liveController.get_channel_by_name(self.objectiveStore.current_objective, configuration_name)
            if config:
                self.selected_configurations.append(config)

    def toggle_stage_tracking(self, state):
        self.flag_stage_tracking_enabled = state > 0
        self._log.info("set stage tracking enabled to " + str(self.flag_stage_tracking_enabled))

    def toggel_enable_af(self, state):
        self.flag_AF_enabled = state > 0
        self._log.info("set af enabled to " + str(self.flag_AF_enabled))

    def toggel_save_images(self, state):
        self.flag_save_image = state > 0
        self._log.info("set save images to " + str(self.flag_save_image))

    def set_base_path(self, path):
        self.base_path = path

    def stop_tracking(self):
        self.flag_stop_tracking_requested = True
        self._log.info("stop tracking requested")

    def slot_image_to_display(self, image):
        self.image_to_display.emit(image)

    def slot_image_to_display_multi(self, image, illumination_source):
        self.image_to_display_multi.emit(image, illumination_source)

    def slot_current_configuration(self, configuration):
        self.signal_current_configuration.emit(configuration)

    def update_pixel_size(self, pixel_size_um):
        self.pixel_size_um = pixel_size_um

    def update_tracker_selection(self, tracker_str):
        self.tracker.update_tracker_type(tracker_str)

    def set_tracking_time_interval(self, time_interval):
        self.tracking_time_interval_s = time_interval

    def update_image_resizing_factor(self, image_resizing_factor):
        self.image_resizing_factor = image_resizing_factor
        self._log.info("update tracking image resizing factor to " + str(self.image_resizing_factor))
        self.pixel_size_um_scaled = self.pixel_size_um / self.image_resizing_factor


class TrackingWorker(QObject):
    finished = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(AcquisitionChannel)

    def __init__(self, trackingController: TrackingController):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.trackingController = trackingController

        self.camera: AbstractCamera = self.trackingController.camera
        self.stage = self.trackingController.stage
        self.microcontroller = self.trackingController.microcontroller
        self.liveController = self.trackingController.liveController
        self.autofocusController = self.trackingController.autofocusController
        self.imageDisplayWindow = self.trackingController.imageDisplayWindow
        self.counter = self.trackingController.counter
        self.experiment_ID = self.trackingController.experiment_ID
        self.base_path = self.trackingController.base_path
        self.selected_configurations = self.trackingController.selected_configurations
        self.tracker = trackingController.tracker

        self.number_of_selected_configurations = len(self.selected_configurations)

        self.image_saver = ImageSaver_Tracking(
            base_path=os.path.join(self.base_path, self.experiment_ID), image_format="bmp"
        )

    def _select_config(self, config: AcquisitionChannel):
        self.signal_current_configuration.emit(config)
        # TODO(imo): replace with illumination controller.
        self.liveController.set_microscope_mode(config)
        self.microcontroller.wait_till_operation_is_completed()
        self.liveController.turn_on_illumination()  # keep illumination on for single configuration acqusition
        self.microcontroller.wait_till_operation_is_completed()

    def run(self):
        # every tracking channel is in use for the whole run: wakes the lines, opens a mechanical shutter
        with self.liveController.use_channels("tracking", self.selected_configurations) as light:
            # e.g. the 560 restarting after its idle-off: never track on dark frames
            if light.wait_until_ready(cancel_fn=lambda: self.trackingController.flag_stop_tracking_requested):
                self._run_tracking()
            else:
                self._log.error("tracking not started: the illumination did not become ready")
                self.image_saver.close()  # made in __init__ with its own thread; _run_tracking closes it otherwise
                self.finished.emit()

    def _run_tracking(self):

        tracking_frame_counter = 0
        t0 = time.time()

        # save metadata
        self.txt_file = open(os.path.join(self.base_path, self.experiment_ID, "metadata.txt"), "w+")
        self.txt_file.write("t0: " + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f") + "\n")
        self.txt_file.write("objective: " + self.trackingController.objective + "\n")
        self.txt_file.close()

        # create a file for logging
        self.csv_file = open(os.path.join(self.base_path, self.experiment_ID, "track.csv"), "w+")
        self.csv_file.write(
            "dt (s), x_stage (mm), y_stage (mm), z_stage (mm), x_image (mm), y_image(mm), image_filename\n"
        )

        # reset tracker
        self.tracker.reset()

        # get the manually selected roi
        init_roi = self.imageDisplayWindow.get_roi_bounding_box()
        self.tracker.set_roi_bbox(init_roi)

        # tracking loop
        while not self.trackingController.flag_stop_tracking_requested:
            self._log.info("tracking_frame_counter: " + str(tracking_frame_counter))
            if tracking_frame_counter == 0:
                is_first_frame = True
            else:
                is_first_frame = False

            # timestamp
            timestamp_last_frame = time.time()

            # switch to the tracking config
            config = self.selected_configurations[0]

            # do autofocus
            if self.trackingController.flag_AF_enabled and tracking_frame_counter > 1:
                # do autofocus
                self._log.info(">>> autofocus")
                self.autofocusController.autofocus()
                self.autofocusController.wait_till_autofocus_has_completed()
                self._log.info(">>> autofocus completed")

            # get current position
            pos = self.stage.get_pos()

            # grab an image
            config = self.selected_configurations[0]
            if self.number_of_selected_configurations > 1:
                self._select_config(config)
            self.camera.send_trigger()
            camera_frame = self.camera.read_camera_frame()
            image = camera_frame.frame
            t = camera_frame.timestamp
            if self.number_of_selected_configurations > 1:
                self.liveController.turn_off_illumination()  # keep illumination on for single configuration acqusition
            image = np.squeeze(image)
            # get image size
            image_shape = image.shape
            image_center = np.array([image_shape[1] * 0.5, image_shape[0] * 0.5])

            # image the rest configurations
            for config_ in self.selected_configurations[1:]:
                self._select_config(config_)

                self.camera.send_trigger()
                image_ = self.camera.read_frame()
                # TODO(imo): use illumination controller
                self.liveController.turn_off_illumination()
                image_ = np.squeeze(image_)
                self.image_to_display_multi.emit(image_, config_.illumination_source)
                # save image
                if self.trackingController.flag_save_image:
                    if camera_frame.is_color():
                        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                    self.image_saver.enqueue(image_, tracking_frame_counter, str(config_.name))

            # track
            object_found, centroid, rect_pts = self.tracker.track(image, None, is_first_frame=is_first_frame)
            if not object_found:
                self._log.error("tracker: object not found")
                break
            in_plane_position_error_pixel = image_center - centroid
            in_plane_position_error_mm = (
                in_plane_position_error_pixel * self.trackingController.pixel_size_um_scaled / 1000
            )
            x_error_mm = in_plane_position_error_mm[0]
            y_error_mm = in_plane_position_error_mm[1]

            # display the new bounding box and the image
            self.imageDisplayWindow.update_bounding_box(rect_pts)
            self.imageDisplayWindow.display_image(image)

            # move
            if self.trackingController.flag_stage_tracking_enabled:
                # TODO(imo): This needs testing!
                self.stage.move_x(x_error_mm)
                self.stage.move_y(y_error_mm)

            # save image
            if self.trackingController.flag_save_image:
                self.image_saver.enqueue(image, tracking_frame_counter, str(config.name))

            # save position data
            self.csv_file.write(
                str(t)
                + ","
                + str(pos.x_mm)
                + ","
                + str(pos.y_mm)
                + ","
                + str(pos.z_mm)
                + ","
                + str(x_error_mm)
                + ","
                + str(y_error_mm)
                + ","
                + str(tracking_frame_counter)
                + "\n"
            )
            if tracking_frame_counter % 100 == 0:
                self.csv_file.flush()

            # wait till tracking interval has elapsed
            while time.time() - timestamp_last_frame < self.trackingController.tracking_time_interval_s:
                time.sleep(0.005)

            # increament counter
            tracking_frame_counter = tracking_frame_counter + 1

        # tracking terminated
        self.csv_file.close()
        self.image_saver.close()
        self.finished.emit()


MAGENTA_COLORMAP = pg.ColorMap(pos=[0.0, 1.0], color=[(0, 0, 0), (255, 0, 255)])
# One green pixel, stretched over the live image and multiplied into it: keeps only the green channel
GREEN_TINT_PIXEL = np.array([[[0, 255, 0]]], dtype=np.uint8)
# Mask overlay: 0 = see-through, 1 = red
OVEREXPOSURE_LUT = np.array([[0, 0, 0, 0], [255, 0, 0, 255]], dtype=np.uint8)
# Stacking order above the live image (z = 0): tint multiplies, reference adds, the red mask covers both
Z_LIVE_TINT, Z_ALIGNMENT_REFERENCE, Z_OVEREXPOSURE, Z_FRAME = 1, 2, 3, 4


def _overexposure_mask(image: np.ndarray, saturation: float) -> np.ndarray:
    """1 where any channel is at or above ``saturation``, else 0."""
    intensity = image if image.ndim == 2 else image.max(axis=2)
    return (intensity >= saturation).view(np.uint8)


def _dtype_range(dtype):
    """np.iinfo / np.finfo for the dtype, whichever applies."""
    return np.iinfo(dtype) if np.issubdtype(dtype, np.integer) else np.finfo(dtype)


class ImageDisplayWindow(QMainWindow):
    image_click_coordinates = Signal(int, int, int, int)
    signal_z_um_delta = Signal(float)

    # Line colors, in the order lines are added; the first matches the original single-line profiler
    LINE_PROFILE_COLORS = ("y", "c", "m", "g", (255, 128, 0), (100, 150, 255))
    DEFAULT_LINE_WIDTH_PX = 5  # every new line, however wide the selected one is
    LINE_SELECT_TOLERANCE_PX = 8
    MAX_BAND_SAMPLES = 256

    def __init__(
        self,
        liveController=None,
        contrastManager=None,
        window_title="",
        show_LUT=False,
        autoLevels=False,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController = liveController
        self.contrastManager = contrastManager
        self.setWindowTitle(window_title)
        self.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.widget = QWidget()
        self.show_LUT = show_LUT
        self.autoLevels = autoLevels

        self.first_image = True

        # Store last valid cursor position
        self.last_valid_x = 0
        self.last_valid_y = 0
        self.last_valid_value = 0
        self.has_valid_position = False

        # Line profiler state
        self.line_rois: list[pg.LineROI] = []
        self.selected_line_roi: Optional[pg.LineROI] = None
        self._line_roi_labels = {}  # roi -> (color, name), fixed for the line's lifetime
        self._line_count = 0
        self.is_drawing_line = False
        self.line_start_pos = None
        self.line_end_pos = None
        self.drawing_cursor = QCursor(Qt.CrossCursor)  # Cross cursor for drawing mode
        self.normal_cursor = QCursor(Qt.ArrowCursor)  # Normal cursor
        self.preview_line = None
        self.start_point_marker = None

        # Last frame as received (before any on-screen marking), for registration
        self._current_image: Optional[np.ndarray] = None

        # Overlays on the live view: reference image during alignment, overexposure mask when toggled on
        self.alignment_reference_item: Optional[pg.ImageItem] = None
        self.live_tint_item: Optional[pg.ImageItem] = (
            None  # green multiply over the live image while a reference is shown
        )
        self.overexposure_item: Optional[pg.ImageItem] = None
        self._overexposure_source = None  # (frame id, saturation) the current mask was computed from

        # Create main layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create status bar widget
        status_widget = QWidget()
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 2, 5, 2)
        status_layout.setSpacing(10)

        # Create labels with minimum width to prevent jumping
        self.cursor_position_label = QLabel()
        self.cursor_position_label.setMinimumWidth(150)
        self.pixel_value_label = QLabel()
        self.pixel_value_label.setMinimumWidth(150)
        self.stage_position_label = QLabel()
        self.stage_position_label.setMinimumWidth(200)
        self.piezo_position_label = QLabel()
        self.piezo_position_label.setMinimumWidth(150)
        # The Z lift (squid/stage/lift.py), shown only on a system that has one
        self.lift_position_label = QLabel()
        self.lift_position_label.setMinimumWidth(120)
        self.lift_position_label.setVisible(False)
        self.lift_separator = QLabel(" | ")
        self.lift_separator.setVisible(False)

        # Add line profiler toggle button
        self.btn_line_profiler = QPushButton("Line Profiler")
        self.btn_line_profiler.setCheckable(True)
        self.btn_line_profiler.setChecked(False)
        self.btn_line_profiler.setEnabled(False)
        self.btn_line_profiler.clicked.connect(self.toggle_line_profiler)

        # Line profiler shortcuts, shown while the profiler is on
        self.btn_line_draw = QPushButton("Draw")
        self.btn_line_draw.setToolTip("Draw another line: click its start, then its end")
        self.btn_line_draw.clicked.connect(self.start_drawing_line)
        self.btn_line_horizontal = QPushButton("H")
        self.btn_line_horizontal.setToolTip("Add a horizontal line across the full field of view")
        self.btn_line_horizontal.clicked.connect(self.add_horizontal_line)
        self.btn_line_vertical = QPushButton("V")
        self.btn_line_vertical.setToolTip("Add a vertical line across the full field of view")
        self.btn_line_vertical.clicked.connect(self.add_vertical_line)
        self.btn_line_full = QPushButton("Full")
        self.btn_line_full.setToolTip("Extend the selected line to the edges of the field of view")
        self.btn_line_full.clicked.connect(self.extend_line_to_fov)
        self.btn_line_fill = QPushButton("Fill FOV")
        self.btn_line_fill.setToolTip(
            "Widen the selected line's band to cover the whole field of view: "
            "the profile becomes the average across the image"
        )
        self.btn_line_fill.clicked.connect(self.fill_fov_with_line)
        self.btn_line_clear = QPushButton("Clear")
        self.btn_line_clear.setToolTip("Remove all lines (right-click a line to remove just that one)")
        self.btn_line_clear.clicked.connect(self.clear_line_rois)
        self.line_shortcut_buttons = (
            self.btn_line_draw,
            self.btn_line_horizontal,
            self.btn_line_vertical,
            self.btn_line_full,
            self.btn_line_fill,
            self.btn_line_clear,
        )
        for button in self.line_shortcut_buttons:
            button.setMaximumWidth(button.fontMetrics().horizontalAdvance(button.text()) + 20)
            button.hide()

        self.btn_overexposure = QPushButton("Over-exposed Pixels")
        self.btn_overexposure.setCheckable(True)
        self.btn_overexposure.setToolTip("Highlight pixels at the camera's maximum value in red")
        self.btn_overexposure.toggled.connect(self.set_overexposure_indicator)

        # Add well selector toggle button
        self.btn_well_selector = QPushButton("Show Well Selector")
        self.btn_well_selector.setCheckable(False)

        # Add labels to status layout with spacing
        status_layout.addWidget(self.cursor_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.pixel_value_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.stage_position_label)
        status_layout.addWidget(self.lift_separator)
        status_layout.addWidget(self.lift_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.piezo_position_label)
        status_layout.addStretch()  # Push labels to the left
        status_layout.addWidget(self.btn_well_selector)  # Add well selector button
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.btn_overexposure)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.btn_line_profiler)  # Add line profiler button
        for button in self.line_shortcut_buttons:
            status_layout.addWidget(button)

        status_widget.setLayout(status_layout)

        # Initialize labels with default text
        self.cursor_position_label.setText("Position: (0, 0)")
        self.pixel_value_label.setText("Value: N/A")
        self.stage_position_label.setText("Stage: X: 0.00 mm, Y: 0.00 mm, Z: 0.00 mm")
        self.piezo_position_label.setText("Piezo: N/A")

        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")

        # Create a container widget for the image display
        self.image_container = QWidget()
        image_layout = QVBoxLayout()
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(0)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.view = self.graphics_widget.addViewBox()
        self.graphics_widget.view.invertY()

        ## lock the aspect ratio so pixels are always square
        self.graphics_widget.view.setAspectLocked(True)

        ## Create image item
        if self.show_LUT:
            self.graphics_widget.view = pg.ImageView()
            self.graphics_widget.img = self.graphics_widget.view.getImageItem()
            self.graphics_widget.view.ui.roiBtn.hide()
            self.graphics_widget.view.ui.menuBtn.hide()
            self.LUTWidget = self.graphics_widget.view.getHistogramWidget()
            self.LUTWidget.region.sigRegionChanged.connect(self.update_contrast_limits)
            self.LUTWidget.region.sigRegionChangeFinished.connect(self.update_contrast_limits)
            self.LUTWidget.item.sigLevelsChanged.connect(self._update_overlays)
        else:
            self.graphics_widget.img = pg.ImageItem()
            self.graphics_widget.view.addItem(self.graphics_widget.img)

        # White frame around the live image, above every overlay so tints and masks never recolor it
        self.frame_item = QGraphicsRectItem()
        self.frame_item.setPen(pg.mkPen("w"))
        self.frame_item.setZValue(Z_FRAME)
        self._active_view().addItem(self.frame_item)

        ## Create ROI
        self.roi_pos = (500, 500)
        self.roi_size = (500, 500)
        self.ROI = pg.ROI(self.roi_pos, self.roi_size, scaleSnap=True, translateSnap=True)
        self.ROI.setZValue(10)
        self.ROI.addScaleHandle((0, 0), (1, 1))
        self.ROI.addScaleHandle((1, 1), (0, 0))
        self.graphics_widget.view.addItem(self.ROI)
        self.ROI.hide()
        self.ROI.sigRegionChanged.connect(self.update_ROI)
        self.roi_pos = self.ROI.pos()
        self.roi_size = self.ROI.size()

        ## Variables for annotating images
        self.draw_rectangle = False
        self.ptRect1 = None
        self.ptRect2 = None
        self.DrawCirc = False
        self.centroid = None
        self.image_offset = np.array([0, 0])

        # Add image widget to container
        if self.show_LUT:
            image_layout.addWidget(self.graphics_widget.view)
        else:
            image_layout.addWidget(self.graphics_widget)
        self.image_container.setLayout(image_layout)

        # Wheel events arrive at the QGraphicsView's viewport; in show_LUT mode the
        # graphics view is nested inside pg.ImageView's auto-generated UI.
        if self.show_LUT:
            graphics_view = self.graphics_widget.view.ui.graphicsView
        else:
            graphics_view = self.graphics_widget
        graphics_view.viewport().installEventFilter(self)

        # Create line profiler widget
        self.line_profiler_widget = pg.GraphicsLayoutWidget()
        self.line_profiler_plot = self.line_profiler_widget.addPlot()
        self.line_profiler_plot.setLabel("left", "Intensity")
        self.line_profiler_plot.setLabel("bottom", "Distance (pixels)")
        self.line_profiler_plot.addLegend()
        self.line_profiler_widget.hide()  # Initially hidden
        self.line_profiler_manual_range = False  # Flag to track if y-range is manually set
        self.line_profiler_plot.sigRangeChanged.connect(self._on_range_changed)

        # Create splitter
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(self.image_container)
        self.splitter.addWidget(self.line_profiler_widget)
        self.splitter.setStretchFactor(0, 1)  # Image container gets more space
        self.splitter.setStretchFactor(1, 0)  # Line profiler starts collapsed

        # Set initial sizes (80% image, 20% profiler)
        self.splitter.setSizes([800, 200])

        # Add splitter to main layout
        layout.addWidget(self.splitter)

        # Add status bar at the bottom
        layout.addWidget(status_widget)

        self.widget.setLayout(layout)
        self.setCentralWidget(self.widget)

        # set window size
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        width = min(screen_geometry.height() * 0.9, 1000)
        height = width
        self.setFixedSize(int(width), int(height))

        # Connect mouse click handler
        if self.show_LUT:
            self.graphics_widget.view.getView().scene().sigMouseClicked.connect(self.handle_mouse_click)
            self.graphics_widget.view.getView().scene().sigMouseMoved.connect(self.handle_mouse_move)
        else:
            self.graphics_widget.view.scene().sigMouseClicked.connect(self.handle_mouse_click)
            self.graphics_widget.view.scene().sigMouseMoved.connect(self.handle_mouse_move)

        # Set up timer for updating stage and piezo positions
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_stage_piezo_positions)
        self.update_timer.start(100)  # Update every 100ms

    def update_stage_piezo_positions(self):
        try:
            if self.liveController and self.liveController.microscope:
                stage = self.liveController.microscope.stage
                if stage:
                    pos = stage.get_pos()
                    self.stage_position_label.setText(
                        f"Stage: X={pos.x_mm:.2f} mm, Y={pos.y_mm:.2f} mm, Z={pos.z_mm:.3f} mm"
                    )
                else:
                    self.stage_position_label.setText("Stage: N/A")

                lift = getattr(self.liveController.microscope, "z_lift", None)
                if lift is not None:
                    self.lift_position_label.setText(f"Lift: {lift.get_position_mm():.3f} mm")
                self.lift_position_label.setVisible(lift is not None)
                self.lift_separator.setVisible(lift is not None)

                piezo = self.liveController.microscope.addons.piezo_stage
                if piezo:
                    try:
                        piezo_pos = piezo.position
                        self.piezo_position_label.setText(f"Piezo: {piezo_pos:.1f} µm")
                        self.piezo_position_label.setVisible(True)
                    except Exception as e:
                        self._log.error(f"Error getting piezo position: {str(e)}")
                        self.piezo_position_label.setText("Piezo: Error")
                        self.piezo_position_label.setVisible(True)
                else:
                    self.piezo_position_label.setVisible(False)
            else:
                self.stage_position_label.setText("Stage: N/A")
                self.piezo_position_label.setVisible(False)
        except Exception as e:
            self._log.error(f"Error updating stage/piezo positions: {str(e)}")
            self.stage_position_label.setText("Stage: Error")
            self.piezo_position_label.setVisible(False)

    def closeEvent(self, event):
        # Stop the timer when the window is closed
        self.update_timer.stop()
        super().closeEvent(event)

    def set_overexposure_indicator(self, enabled: bool):
        """Overlay pixels at the camera's maximum value in red, whatever the contrast setting."""
        if enabled and self.overexposure_item is None:
            self.overexposure_item = self._add_overlay_item(Z_OVEREXPOSURE, lut=OVEREXPOSURE_LUT)
            self._update_overlays()
        elif not enabled and self.overexposure_item is not None:
            self._active_view().removeItem(self.overexposure_item)
            self.overexposure_item = None
            self._overexposure_source = None

    def current_image(self) -> Optional[np.ndarray]:
        """The most recently received frame, unmodified (None before the first one or after invalidation)."""
        return self._current_image

    def invalidate_current_image(self):
        """Forget the last frame, e.g. after a stage move it no longer shows what is under the objective."""
        self._current_image = None

    def show_alignment_reference(self, image: np.ndarray):
        """Overlay a reference image in additive magenta on a green live view.

        Misalignment shows as magenta and green fringes; where the two line up the colors add to white.
        The live image item itself (levels, lookup table, histogram) is left untouched.
        """
        if self.alignment_reference_item is None:
            self.live_tint_item = self._add_overlay_item(Z_LIVE_TINT, QPainter.CompositionMode_Multiply)
            self.live_tint_item.setImage(GREEN_TINT_PIXEL, levels=(0, 255))
            self.alignment_reference_item = self._add_overlay_item(
                Z_ALIGNMENT_REFERENCE, QPainter.CompositionMode_Plus, lut=MAGENTA_COLORMAP.getLookupTable(nPts=256)
            )
        # lookup tables do not apply to H x W x 3 data
        self.alignment_reference_item.setImage(utils.to_grayscale(image), autoLevels=False)
        self._update_overlays()

    def hide_alignment_reference(self):
        if self.alignment_reference_item is not None:
            self._active_view().removeItem(self.alignment_reference_item)
            self._active_view().removeItem(self.live_tint_item)
            self.alignment_reference_item = self.live_tint_item = None

    def _add_overlay_item(self, z: int, composition_mode=None, lut: Optional[np.ndarray] = None) -> pg.ImageItem:
        item = pg.ImageItem()
        item.setZValue(z)
        if lut is not None:
            item.setLookupTable(lut)
        if composition_mode is not None:
            item.setCompositionMode(composition_mode)
        self._active_view().addItem(item)
        return item

    def _update_overlays(self, *_):
        """Keep the overlays in step with the live image and its contrast range."""
        image_rect = self.graphics_widget.img.boundingRect()
        self.frame_item.setRect(image_rect)
        if self.live_tint_item is not None:
            self.live_tint_item.setRect(image_rect)
        levels = self.graphics_widget.img.getLevels()
        if levels is None:
            return
        reference = self.alignment_reference_item
        if reference is not None and not np.array_equal(reference.getLevels(), levels):
            reference.setLevels(levels)
        if self.overexposure_item is not None and self._current_image is not None:
            saturation = self._saturation_level(self._current_image)
            source = (id(self._current_image), saturation)
            if source != self._overexposure_source:
                self._overexposure_source = source
                mask = _overexposure_mask(self._current_image, saturation)
                self.overexposure_item.setImage(mask, autoLevels=False, levels=(0, 1))

    def _saturation_level(self, image: np.ndarray) -> float:
        """The camera's maximum pixel value; the dtype's full range when no camera is attached."""
        if self.liveController is None or not np.issubdtype(image.dtype, np.integer):
            return _dtype_range(image.dtype).max
        return self.liveController.camera.get_pixel_format().max_value(image.dtype)

    def toggle_line_profiler(self):
        """Toggle the visibility of the line profiler widget."""
        for button in self.line_shortcut_buttons:
            button.setVisible(self.btn_line_profiler.isChecked())
        if self.btn_line_profiler.isChecked():
            self.line_profiler_widget.show()
            if not self.line_rois:
                self.start_drawing_line()
            else:
                for roi in self.line_rois:
                    roi.show()
                self.update_line_profile()
        else:
            self.line_profiler_widget.hide()
            for roi in self.line_rois:
                roi.hide()
            self._cancel_line_drawing()

    def start_drawing_line(self):
        """Enter drawing mode: the next two clicks on the image set a new line's start and end."""
        self._cancel_line_drawing()
        self.is_drawing_line = True
        self._active_view().setCursor(self.drawing_cursor)
        self._log.info("Line profiler ready to draw line")

    def _cancel_line_drawing(self):
        self._remove_line_preview_items()
        self.is_drawing_line = False
        self.line_start_pos = None
        self.line_end_pos = None
        self._active_view().setCursor(self.normal_cursor)

    def _on_range_changed(self, view_range):
        """Handle manual range changes in the line profiler plot."""
        self.line_profiler_manual_range = True

    def _active_view(self):
        return self.graphics_widget.view.getView() if self.show_LUT else self.graphics_widget.view

    def _remove_line_preview_items(self):
        view = self._active_view()
        if self.preview_line is not None:
            view.removeItem(self.preview_line)
            self.preview_line = None
        if self.start_point_marker is not None:
            view.removeItem(self.start_point_marker)
            self.start_point_marker = None

    def create_line_roi(self):
        """Add a line ROI from line_start_pos to line_end_pos (view coordinates) for intensity profiling."""
        if self.line_start_pos is None or self.line_end_pos is None:
            return None
        try:
            color = self.LINE_PROFILE_COLORS[self._line_count % len(self.LINE_PROFILE_COLORS)]
            self._line_count += 1
            roi = pg.LineROI(
                pos1=pg.Point(*self.line_start_pos),
                pos2=pg.Point(*self.line_end_pos),
                width=self.DEFAULT_LINE_WIDTH_PX,
                pen=pg.mkPen(color, width=2),
                hoverPen=pg.mkPen(color, width=3),
                handlePen=pg.mkPen(color, width=2),
                handleHoverPen=pg.mkPen(color, width=2),
                movable=True,
                rotatable=True,
                resizable=True,
                removable=True,  # right-click menu offers "Remove ROI"
            )
            self._line_roi_labels[roi] = (color, f"Line {self._line_count}")
            self.line_rois.append(roi)
            self._active_view().addItem(roi)

            roi.sigRegionChanged.connect(self.update_line_profile)
            roi.sigRegionChangeStarted.connect(self.select_line_roi)
            roi.sigRemoveRequested.connect(self.remove_line_roi)
            self.line_profiler_manual_range = False  # fit the plot to the new line
            self.select_line_roi(roi)
            self._log.info("Line ROI created successfully")
            return roi
        except Exception as e:
            self._log.error(f"Error creating line ROI: {str(e)}")
            return None
        finally:
            self.line_start_pos = None
            self.line_end_pos = None

    def select_line_roi(self, roi):
        """Make roi the line that shortcuts act on, drawn thicker on the image and in the plot."""
        self.selected_line_roi = roi
        for line in self.line_rois:
            color, _ = self._line_roi_labels[line]
            line.setPen(pg.mkPen(color, width=4 if line is roi else 2))
        self.update_line_profile()

    def _select_line_near(self, scene_pos):
        """Select the line nearest a click, if within LINE_SELECT_TOLERANCE_PX screen pixels.

        A 5 px line on a zoomed-out camera frame is 1-2 screen pixels thick, too thin to click exactly."""
        if not self.btn_line_profiler.isChecked():
            return
        view = self._active_view()
        click = np.array([scene_pos.x(), scene_pos.y()])
        nearest, nearest_distance = None, self.LINE_SELECT_TOLERANCE_PX
        for roi in self.line_rois:
            # Scene coordinates are screen pixels
            a, b = (np.array([q.x(), q.y()]) for q in map(view.mapViewToScene, self._line_roi_endpoints(roi)))
            ab = b - a
            t = np.clip(np.dot(click - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1)
            distance = np.linalg.norm(click - (a + t * ab))
            if distance <= nearest_distance:
                nearest, nearest_distance = roi, distance
        if nearest is not None and nearest is not self.selected_line_roi:
            self.select_line_roi(nearest)

    def remove_line_roi(self, roi):
        """Remove one line ROI; the most recently added remaining line becomes selected."""
        if roi not in self.line_rois:
            return
        self.line_rois.remove(roi)
        del self._line_roi_labels[roi]
        self._active_view().removeItem(roi)
        if self.selected_line_roi is roi:
            self.selected_line_roi = None
            if self.line_rois:
                self.select_line_roi(self.line_rois[-1])
        self.update_line_profile()

    def clear_line_rois(self):
        """Remove every line ROI."""
        for roi in list(self.line_rois):
            self.remove_line_roi(roi)

    def update_line_profile(self):
        """Plot one intensity profile per line ROI, in the line's color."""
        if not self.btn_line_profiler.isChecked():
            return

        try:
            self.line_profiler_plot.clear()
            image = self.graphics_widget.img.image
            if image is None or not self.line_rois:
                return
            for roi in self.line_rois:
                length, width = roi.size()
                start, end = self._line_roi_endpoints(roi)

                # Convert ROI coordinates to image coordinates
                start_img = self.graphics_widget.img.mapFromView(start)
                end_img = self.graphics_widget.img.mapFromView(end)
                profile = self.get_line_profile(image, start_img, end_img, width)

                pixel_distance = np.linspace(0, length, len(profile))
                color, name = self._line_roi_labels[roi]
                pen = pg.mkPen(color, width=2 if roi is self.selected_line_roi else 1)
                self.line_profiler_plot.plot(pixel_distance, profile, pen=pen, name=name, connect="finite")

            # Only auto-range if not manually set
            if not self.line_profiler_manual_range:
                self.line_profiler_plot.autoRange()
        except Exception as e:
            self._log.error(f"Error updating line profile: {str(e)}")

    @staticmethod
    def _line_roi_endpoints(roi):
        """The line ROI's centerline endpoints in view coordinates.

        The ROI's position is its corner, half a width off the line, so map the centerline instead."""
        length, width = roi.size()
        start = roi.mapToParent(pg.Point(0, width / 2))
        end = roi.mapToParent(pg.Point(length, width / 2))
        return start, end

    def _add_line_across_image(self, start, end):
        """Add a line ROI from start to end, given in image coordinates; cancels a line being drawn by hand."""
        self._cancel_line_drawing()
        start_view = self.graphics_widget.img.mapToView(pg.Point(*start))
        end_view = self.graphics_widget.img.mapToView(pg.Point(*end))
        self.line_start_pos = (start_view.x(), start_view.y())
        self.line_end_pos = (end_view.x(), end_view.y())
        self.create_line_roi()

    def _image_size(self):
        """(width, height) of the displayed image, or None before the first frame."""
        if self.graphics_widget.img.image is None:
            return None
        return self.graphics_widget.img.width(), self.graphics_widget.img.height()

    def add_horizontal_line(self):
        """Add a horizontal line through the image center, across the full field of view."""
        size = self._image_size()
        if size is None:
            return
        width, height = size
        y = (height - 1) / 2
        self._add_line_across_image((0, y), (width - 1, y))

    def add_vertical_line(self):
        """Add a vertical line through the image center, across the full field of view."""
        size = self._image_size()
        if size is None:
            return
        width, height = size
        x = (width - 1) / 2
        self._add_line_across_image((x, 0), (x, height - 1))

    def extend_line_to_fov(self):
        """Extend the selected line along its direction until it meets the edges of the field of view."""
        size = self._image_size()
        roi = self.selected_line_roi
        if size is None or roi is None:
            return
        start, end = (self.graphics_widget.img.mapFromView(p) for p in self._line_roi_endpoints(roi))
        origin = np.array([start.x(), start.y()])
        direction = np.array([end.x(), end.y()]) - origin
        if np.linalg.norm(direction) == 0:
            return

        # Clip the infinite line origin + t * direction to the pixel centers [0, W-1] x [0, H-1]
        t_min, t_max = -np.inf, np.inf
        for o, d, upper in zip(origin, direction, (size[0] - 1, size[1] - 1)):
            if abs(d) < 1e-9:
                if not 0 <= o <= upper:
                    return  # parallel to and outside this pair of edges
                continue
            t0, t1 = sorted(((0 - o) / d, (upper - o) / d))
            t_min, t_max = max(t_min, t0), min(t_max, t1)
        if t_max <= t_min:
            return  # the line misses the image

        new_start = self.graphics_widget.img.mapToView(pg.Point(*(origin + t_min * direction)))
        new_end = self.graphics_widget.img.mapToView(pg.Point(*(origin + t_max * direction)))
        self.line_profiler_manual_range = False  # fit the plot to the longer line
        self._set_line_roi_endpoints(roi, new_start, new_end)

    def fill_fov_with_line(self):
        """Center the selected line on the image and widen its band to cover the whole field of view.

        The line keeps its angle; its profile becomes the mean across the image perpendicular to it, e.g. the
        mean of all rows for a horizontal line."""
        size = self._image_size()
        roi = self.selected_line_roi
        if size is None or roi is None:
            return
        start, end = (self.graphics_widget.img.mapFromView(p) for p in self._line_roi_endpoints(roi))
        direction = np.array([end.x() - start.x(), end.y() - start.y()])
        if np.linalg.norm(direction) == 0:
            return
        along = direction / np.linalg.norm(direction)
        across = np.array([-along[1], along[0]])

        # Project the corner pixel centers onto the line and its normal
        width, height = size
        corners = np.array([(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)], dtype=float)
        center = corners.mean(axis=0)
        t = (corners - center) @ along
        band = (corners - center) @ across
        new_start = self.graphics_widget.img.mapToView(pg.Point(*(center + t.min() * along)))
        new_end = self.graphics_widget.img.mapToView(pg.Point(*(center + t.max() * along)))
        self.line_profiler_manual_range = False  # fit the plot to the new profile
        # +1: the band covers whole pixels, half a pixel beyond the outermost pixel centers on each side
        self._set_line_roi_endpoints(roi, new_start, new_end, band.max() - band.min() + 1)

    @staticmethod
    def _set_line_roi_endpoints(roi, start, end, width=None):
        """Move a line ROI so its centerline runs from start to end (view coordinates); width defaults to its own."""
        if width is None:
            width = roi.size()[1]
        start, end = pg.Point(start), pg.Point(end)
        delta = end - start
        angle = np.arctan2(delta.y(), delta.x())
        # Same placement as pg.LineROI: the position is the corner half a width off the start point
        corner = start + pg.Point(width / 2 * np.sin(angle), -width / 2 * np.cos(angle))
        roi.setState({"pos": corner, "size": pg.Point(delta.length(), width), "angle": np.degrees(angle)})

    @staticmethod
    def _axis_aligned_line_profile(image, start, end, width):
        """Exact profile of a horizontal or vertical line: the mean of the whole pixels its band covers.

        Returns None for a tilted line. Columns (rows) off the image read NaN."""
        if abs(end.y() - start.y()) < 1e-6:
            pixels, along, across, lo_hi = image, (start.x(), end.x()), start.y(), image.shape[0]
        elif abs(end.x() - start.x()) < 1e-6:
            pixels, along, across, lo_hi = image.T, (start.y(), end.y()), start.x(), image.shape[1]
        else:
            return None
        # Pixel i spans [i - 0.5, i + 0.5]: take every pixel whose center lies in the band, at least the nearest one
        first = max(int(np.ceil(across - width / 2)), 0)
        last = min(int(np.floor(across + width / 2)), lo_hi - 1)
        if last < first:
            first = last = int(round(across))
        positions = np.arange(int(round(along[0])), int(round(along[1])) + 1)
        if along[1] < along[0]:
            positions = np.arange(int(round(along[0])), int(round(along[1])) - 1, -1)
        profile = np.full(len(positions), np.nan)
        on_image = (positions >= 0) & (positions < pixels.shape[1])
        if 0 <= first < lo_hi:
            profile[on_image] = pixels[first : last + 1, positions[on_image]].mean(axis=0)
        return profile

    def get_line_profile(self, image, start, end, width=1):
        """Get intensity profile along a line with specified width."""
        exact = self._axis_aligned_line_profile(image, start, end, width)
        if exact is not None:
            return exact
        try:
            # Calculate the line vector
            line_vec = np.array([end.x() - start.x(), end.y() - start.y()])
            line_length = np.linalg.norm(line_vec)

            # Calculate the number of points along the line
            num_points = int(line_length)
            if num_points < 2:
                num_points = 2  # Ensure at least 2 points

            # Create coordinate arrays
            x = np.linspace(start.x(), end.x(), num_points)
            y = np.linspace(start.y(), end.y(), num_points)

            # Calculate perpendicular vector
            perp_vec = np.array([-line_vec[1], line_vec[0]]) / line_length

            # Create meshgrid for width sampling
            # One sample per pixel across the band, capped so a band as wide as the image (Fill FOV on a tilted
            # line) stays cheap enough to recompute on every live frame
            width_points = min(max(1, int(width)), self.MAX_BAND_SAMPLES)
            width_offsets = np.linspace(-width / 2, width / 2, width_points)

            # Sum and count only samples on the image: a band wider than the image (Fill FOV on a tilted line)
            # must not average in made-up values
            height, width_px = image.shape[:2]
            profile = np.zeros(num_points)
            counts = np.zeros(num_points)

            # Sample points along the width
            for w in width_offsets:
                x_offset = x + perp_vec[0] * w
                y_offset = y + perp_vec[1] * w

                # Get values at these points
                # "nearest" so a sample on the outer half of an edge pixel reads that pixel, not 0
                values = scipy.ndimage.map_coordinates(
                    image, [y_offset, x_offset], order=1, output=np.float64, mode="nearest"
                )
                on_image = (
                    (x_offset >= -0.5) & (x_offset <= width_px - 0.5) & (y_offset >= -0.5) & (y_offset <= height - 0.5)
                )
                profile += np.where(on_image, values, 0)
                counts += on_image

            # Average the values; NaN (a gap in the plot) where the line is off the image
            with np.errstate(invalid="ignore", divide="ignore"):
                profile /= counts

            return profile

        except Exception as e:
            self._log.error(f"Error getting line profile: {str(e)}")
            return np.zeros(1)

    def handle_mouse_move(self, pos):
        try:
            if self.show_LUT:
                view_coord = self.graphics_widget.view.getView().mapSceneToView(pos)
            else:
                view_coord = self.graphics_widget.view.mapSceneToView(pos)

            # Update preview line if we're drawing
            if self.is_drawing_line and self.line_start_pos is not None and self.preview_line is not None:
                self.preview_line.setData(
                    x=[self.line_start_pos[0], view_coord.x()], y=[self.line_start_pos[1], view_coord.y()]
                )

            image_coord = self.graphics_widget.img.mapFromView(view_coord)

            if self.is_within_image(image_coord):
                x = int(image_coord.x())
                y = int(image_coord.y())
                self.last_valid_x = x
                self.last_valid_y = y
                self.has_valid_position = True

                self.cursor_position_label.setText(f"Position: ({x}, {y})")

                # Get pixel value
                image = self.graphics_widget.img.image
                if image is not None and 0 <= y < image.shape[0] and 0 <= x < image.shape[1]:
                    pixel_value = image[y, x]
                    self.last_valid_value = pixel_value
                    self.pixel_value_label.setText(f"Value: {pixel_value}")
                else:
                    self.pixel_value_label.setText("Value:")
            else:
                self.cursor_position_label.setText("Position:")
                self.pixel_value_label.setText("Value:")
                self.has_valid_position = False
        except:
            pass

    def handle_mouse_click(self, evt):
        """Handle mouse clicks for both line drawing and other interactions."""
        if self.is_drawing_line:
            try:
                view = self._active_view()

                # Convert click position to view coordinates
                pos = evt.pos()
                view_coord = view.mapSceneToView(pos)

                if self.line_start_pos is None:
                    # First click - start drawing
                    self.line_start_pos = (view_coord.x(), view_coord.y())
                    self._log.info(f"Line start position set to: {self.line_start_pos}")

                    # Add a point marker at the start position
                    self.start_point_marker = pg.ScatterPlotItem(
                        pos=[(self.line_start_pos[0], self.line_start_pos[1])],
                        size=10,
                        symbol="o",
                        pen=pg.mkPen("y", width=2),
                        brush=pg.mkBrush("y"),
                    )
                    view.addItem(self.start_point_marker)

                    # Create preview line
                    self.preview_line = pg.PlotDataItem(pen=pg.mkPen("y", width=2, style=Qt.DashLine))
                    view.addItem(self.preview_line)
                else:
                    # Second click - finish drawing
                    self.line_end_pos = (view_coord.x(), view_coord.y())
                    self._log.info(f"Line end position set to: {self.line_end_pos}")

                    self._remove_line_preview_items()
                    self.create_line_roi()
                    self.is_drawing_line = False
                    # Reset cursor to normal
                    view.setCursor(self.normal_cursor)
            except Exception as e:
                self._log.error(f"Error drawing line: {str(e)}")
                self.is_drawing_line = False
                self.line_start_pos = None
                self.line_end_pos = None
                self._remove_line_preview_items()
            return

        # Handle double clicks for other purposes
        if not evt.double():
            if evt.button() == Qt.LeftButton:
                self._select_line_near(evt.scenePos())
            return

        try:
            pos = evt.pos()
            if self.show_LUT:
                view_coord = self.graphics_widget.view.getView().mapSceneToView(pos)
            else:
                view_coord = self.graphics_widget.view.mapSceneToView(pos)
            image_coord = self.graphics_widget.img.mapFromView(view_coord)
        except:
            return

        if self.is_within_image(image_coord):
            x_pixel_centered = int(image_coord.x() - self.graphics_widget.img.width() / 2)
            y_pixel_centered = int(image_coord.y() - self.graphics_widget.img.height() / 2)
            self.image_click_coordinates.emit(
                x_pixel_centered, y_pixel_centered, self.graphics_widget.img.width(), self.graphics_widget.img.height()
            )

    def is_within_image(self, coordinates):
        try:
            image_width = self.graphics_widget.img.width()
            image_height = self.graphics_widget.img.height()
            return 0 <= coordinates.x() < image_width and 0 <= coordinates.y() < image_height
        except:
            return False

    def eventFilter(self, source, event):
        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            notches = event.angleDelta().y() / 120.0
            if notches == 0:
                return True
            # Read via the module so runtime updates of the constant are picked up;
            # the `from control._def import *` binding above would freeze it at import.
            step_um = (
                control._def.LIVE_VIEW_Z_STEP_FAST_UM
                if (event.modifiers() & Qt.ShiftModifier)
                else control._def.LIVE_VIEW_Z_STEP_UM
            )
            self.signal_z_um_delta.emit(notches * step_um)
            return True
        return super().eventFilter(source, event)

    def display_image(self, image):
        # enable the line profiler button after the first image is displayed
        if self.first_image:
            self.first_image = False
            self.btn_line_profiler.setEnabled(True)
        self._current_image = image

        if ENABLE_TRACKING:
            image = np.copy(image)
            self.image_height, self.image_width = image.shape[:2]
            if self.draw_rectangle:
                cv2.rectangle(image, self.ptRect1, self.ptRect2, (255, 255, 255), 4)
                self.draw_rectangle = False

        info = _dtype_range(image.dtype)
        min_val, max_val = info.min, info.max

        if self.liveController is not None and self.contrastManager is not None:
            channel_name = self.liveController.currentConfiguration.name
            if self.contrastManager.acquisition_dtype != None and self.contrastManager.acquisition_dtype != np.dtype(
                image.dtype
            ):
                self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            min_val, max_val = self.contrastManager.get_limits(channel_name, image.dtype)

        self.graphics_widget.img.setImage(image, autoLevels=self.autoLevels, levels=(min_val, max_val))

        if not self.autoLevels:
            if self.show_LUT:
                self.LUTWidget.setLevels(min_val, max_val)
                self.LUTWidget.setHistogramRange(info.min, info.max)
            else:
                self.graphics_widget.img.setLevels((min_val, max_val))

        self.graphics_widget.img.updateImage()
        self._update_overlays()

        # Update pixel value based on last valid position
        if self.has_valid_position:
            try:
                if 0 <= self.last_valid_y < image.shape[0] and 0 <= self.last_valid_x < image.shape[1]:
                    pixel_value = image[self.last_valid_y, self.last_valid_x]
                    self.last_valid_value = pixel_value
                    self.cursor_position_label.setText(f"Position: ({self.last_valid_x}, {self.last_valid_y})")
                    self.pixel_value_label.setText(f"Value: {pixel_value}")
            except:
                # If there's an error, keep the last valid values
                self.cursor_position_label.setText(f"Position: ({self.last_valid_x}, {self.last_valid_y})")
                self.pixel_value_label.setText(f"Value: {self.last_valid_value}")

        if self.line_rois and self.btn_line_profiler.isChecked():
            self.update_line_profile()

    def mark_spot(self, image: np.ndarray, x: float, y: float):
        """Mark the detected laserspot location on the image.

        Args:
            image: Image to mark
            x: x-coordinate of the spot
            y: y-coordinate of the spot

        Returns:
            Image with marked spot
        """
        # Draw a green crosshair at the specified x,y coordinates
        crosshair_size = 10  # Size of crosshair lines in pixels
        crosshair_color = (0, 255, 0)  # Green in BGR format
        crosshair_thickness = 1
        x = int(round(x))
        y = int(round(y))

        # Convert grayscale to BGR
        marked_image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        # Draw horizontal line
        cv2.line(marked_image, (x - crosshair_size, y), (x + crosshair_size, y), crosshair_color, crosshair_thickness)

        # Draw vertical line
        cv2.line(marked_image, (x, y - crosshair_size), (x, y + crosshair_size), crosshair_color, crosshair_thickness)

        self.display_image(marked_image)

    def update_contrast_limits(self):
        if self.show_LUT and self.contrastManager and self.contrastManager.acquisition_dtype:
            min_val, max_val = self.LUTWidget.region.getRegion()
            self.contrastManager.update_limits(self.liveController.currentConfiguration.name, min_val, max_val)

    def update_ROI(self):
        self.roi_pos = self.ROI.pos()
        self.roi_size = self.ROI.size()

    def show_ROI_selector(self):
        self.ROI.show()

    def hide_ROI_selector(self):
        self.ROI.hide()

    def get_roi(self):
        return self.roi_pos, self.roi_size

    def update_bounding_box(self, pts):
        self.draw_rectangle = True
        self.ptRect1 = (pts[0][0], pts[0][1])
        self.ptRect2 = (pts[1][0], pts[1][1])

    def get_roi_bounding_box(self):
        self.update_ROI()
        width = self.roi_size[0]
        height = self.roi_size[1]
        xmin = max(0, self.roi_pos[0])
        ymin = max(0, self.roi_pos[1])
        return np.array([xmin, ymin, width, height])

    def set_autolevel(self, enabled):
        self.autoLevels = enabled
        self._log.info("set autolevel to " + str(enabled))


class NavigationViewer(QFrame):
    signal_coordinates_clicked = Signal(float, float)  # Will emit x_mm, y_mm when clicked

    def __init__(self, objectivestore, camera, sample="glass slide", invertX=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        # Catalog identity vs display identity: "4 glass slide" is an image
        # choice, not a format. Constructors may pass either; normalize to the
        # format and derive the display name from the ONE owner of the alias.
        self.format = "glass slide" if sample in ("glass slide", "4 glass slide") else sample
        self.sample = self._display_sample_for(self.format)
        self.objectiveStore = objectivestore
        self.camera = camera
        self.well_size_mm = WELL_SIZE_MM
        self.number_of_skip = NUMBER_OF_SKIP
        self.a1_x_mm = A1_X_MM
        self.a1_y_mm = A1_Y_MM
        self.a1_x_pixel = A1_X_PIXEL
        self.a1_y_pixel = A1_Y_PIXEL
        self.location_update_threshold_mm = 0.2
        self.box_color = (255, 0, 0)
        self.box_line_thickness = 2
        self.x_mm = None
        self.y_mm = None
        self.alignment_widget = None  # Optional AlignmentWidget
        self.image_paths = {
            "glass slide": "images/slide carrier_828x662.png",
            "4 glass slide": "images/4 slide carrier_1509x1010.png",
            "6 well plate": "images/6 well plate_1509x1010.png",
            "12 well plate": "images/12 well plate_1509x1010.png",
            "24 well plate": "images/24 well plate_1509x1010.png",
            "96 well plate": "images/96 well plate_1509x1010.png",
            "384 well plate": "images/384 well plate_1509x1010.png",
            "1536 well plate": "images/1536 well plate_1509x1010.png",
        }

        print("navigation viewer:", sample)
        self.init_ui(invertX)

        self.load_background_image(self.image_paths.get(self.sample, "images/4 slide carrier_1509x1010.png"))
        self.create_layers()
        self.update_display_properties(self.sample)
        # self.update_display()

    def init_ui(self, invertX):
        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setBackground("w")

        self.view = self.graphics_widget.addViewBox(invertX=not INVERTED_OBJECTIVE, invertY=True)
        self.view.setAspectLocked(True)

        # Create Clear Coordinates button with seamless styling
        self.btn_clear_coordinates = QPushButton("Clear Scan Grid", self.graphics_widget)
        self.btn_clear_coordinates.clicked.connect(self.clear_slide)
        self.btn_clear_coordinates.setCursor(Qt.PointingHandCursor)
        # Position button
        self.btn_clear_coordinates.adjustSize()
        self._position_button()

        self.grid = QVBoxLayout()
        self.grid.addWidget(self.graphics_widget)
        self.setLayout(self.grid)
        # Connect double-click handler
        self.view.scene().sigMouseClicked.connect(self.handle_mouse_click)

    def _position_button(self):
        """Position buttons at the bottom-right corner of the graphics widget"""
        margin = 10  # Margin from edges
        button_height = self.btn_clear_coordinates.sizeHint().height()

        # Position clear button (rightmost)
        clear_width = self.btn_clear_coordinates.sizeHint().width()
        clear_x = self.graphics_widget.width() - clear_width - margin
        clear_y = self.graphics_widget.height() - button_height - margin
        self.btn_clear_coordinates.move(clear_x, clear_y)
        self.btn_clear_coordinates.raise_()

        # Position alignment button (to the left of clear) if present
        if self.alignment_widget is not None:
            align_width = self.alignment_widget.sizeHint().width()
            align_x = clear_x - align_width - margin
            self.alignment_widget.move(align_x, clear_y)
            self.alignment_widget.raise_()

    def set_alignment_widget(self, alignment_widget):
        """Set the alignment widget to be displayed in the navigation viewer."""
        self.alignment_widget = alignment_widget
        self.alignment_widget.setParent(self.graphics_widget)
        self.alignment_widget.installEventFilter(self)  # it grows/shrinks as its Auto button toggles
        self.alignment_widget.adjustSize()

    def eventFilter(self, obj, event):
        if obj is self.alignment_widget and event.type() == QEvent.Resize:
            self._position_button()
        return super().eventFilter(obj, event)
        self._position_button()

    def resizeEvent(self, event):
        """Reposition buttons when widget is resized"""
        super().resizeEvent(event)
        if hasattr(self, "btn_clear_coordinates"):
            self._position_button()

    def load_background_image(self, image_path):
        self.view.clear()
        self.background_image = cv2.imread(image_path)
        if self.background_image is None:
            # raise ValueError(f"Failed to load image from {image_path}")
            self.background_image = cv2.imread(self.image_paths.get("glass slide"))

        if len(self.background_image.shape) == 2:  # Grayscale image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_GRAY2RGBA)
        elif self.background_image.shape[2] == 3:  # BGR image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_BGR2RGBA)
        elif self.background_image.shape[2] == 4:  # BGRA image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_BGRA2RGBA)

        self.background_image_copy = self.background_image.copy()
        self.image_height, self.image_width = self.background_image.shape[:2]
        self.background_item = pg.ImageItem(self.background_image)
        self.view.addItem(self.background_item)

    def create_layers(self):
        self.scan_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.fov_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.focus_point_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)

        self.scan_overlay_item = pg.ImageItem()
        self.fov_overlay_item = pg.ImageItem()
        self.focus_point_overlay_item = pg.ImageItem()

        self.view.addItem(self.scan_overlay_item)
        self.view.addItem(self.fov_overlay_item)
        self.view.addItem(self.focus_point_overlay_item)

        self.background_item.setZValue(-1)  # Background layer at the bottom
        self.scan_overlay_item.setZValue(0)  # Scan overlay in the middle
        self.focus_point_overlay_item.setZValue(1)  # # Focus points next
        self.fov_overlay_item.setZValue(2)  # FOV overlay on top

    def update_display_properties(self, sample):
        if sample == "glass slide":
            self.location_update_threshold_mm = 0.2
            self.mm_per_pixel = 0.1453
            self.origin_x_pixel = 200
            self.origin_y_pixel = 120
        elif sample == "4 glass slide":
            self.location_update_threshold_mm = 0.2
            self.mm_per_pixel = 0.084665
            self.origin_x_pixel = 50
            self.origin_y_pixel = 0
        else:
            self.location_update_threshold_mm = 0.05
            self.mm_per_pixel = 0.084665
            self.origin_x_pixel = self.a1_x_pixel - (self.a1_x_mm) / self.mm_per_pixel
            self.origin_y_pixel = self.a1_y_pixel - (self.a1_y_mm) / self.mm_per_pixel
        self.update_fov_size()

    def update_fov_size(self):
        self.fov_size_mm = self.camera.get_fov_size_mm() * self.objectiveStore.get_pixel_size_factor()

    @staticmethod
    def _display_sample_for(format_: str) -> str:
        """The display alias for a catalog format - the one owner of the
        "4 glass slide is how an HCS machine DRAWS a glass slide" rule."""
        if format_ == "glass slide" and IS_HCS:
            return "4 glass slide"
        return format_

    def _current_rotation_deg(self) -> float:
        """The measured plate rotation, resolved at CALL time (never cached).

        The background image is a NOMINAL drawing - wells on an unrotated grid -
        so under a measured rotation the stage<->pixel mapping must go through
        the plate frame: pixels are always nominal, stage coordinates are
        rotated about A1. Pitch-0 formats (glass slides) resolve to 0.0 in the
        resolver itself - no sample-name special cases here.
        """
        try:
            return resolve_rotation_deg(self.format)[0]
        except Exception:
            self._log.exception(
                f"Could not resolve plate rotation for {self.format!r}; drawing the navigation view with 0.00 deg."
            )
            return 0.0

    def redraw_fov(self):
        self.clear_overlay()
        self.update_fov_size()
        self.draw_current_fov(self.x_mm, self.y_mm)

    def update_wellplate_settings(self, settings: "WellplateSettings"):
        sample_format = settings.format
        a1_x_mm = settings.a1_x_mm
        a1_y_mm = settings.a1_y_mm
        a1_x_pixel = settings.a1_x_pixel
        a1_y_pixel = settings.a1_y_pixel
        well_size_mm = settings.well_size_mm
        number_of_skip = settings.number_of_skip
        rows = settings.rows
        cols = settings.cols

        sample = self._display_sample_for(sample_format)

        self.sample = sample
        self.format = sample_format  # catalog identity survives the display renaming
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = well_size_mm
        # settings.well_spacing_mm is deliberately not stored: NavigationViewer's
        # mm<->pixel mapping never uses the pitch.
        self.number_of_skip = number_of_skip
        self.rows = rows
        self.cols = cols

        # Try to find the image for the wellplate
        image_path = self.image_paths.get(sample)
        if image_path is None or not os.path.exists(image_path):
            # Custom wellplate image: raw name is the convention; the underscore
            # variant is a legacy fallback for images generated by older builds,
            # which wrote spaces as underscores.
            custom_image_path = os.path.join("images", self.sample + ".png")
            legacy_image_path = os.path.join("images", self.sample.replace(" ", "_") + ".png")
            if os.path.exists(custom_image_path):
                image_path = custom_image_path
            elif os.path.exists(legacy_image_path):
                image_path = legacy_image_path
            else:
                self._log.warning(
                    f"No plate image for {sample!r} (tried {custom_image_path!r} and {legacy_image_path!r}); "
                    f"using the default image - the navigation view will not match this plate."
                )
                image_path = self.image_paths.get("glass slide")  # Use a default image

        self.load_background_image(image_path)
        self.create_layers()
        self.update_display_properties(sample)
        self.draw_current_fov(self.x_mm, self.y_mm)

    def draw_fov_current_location(self, pos: squid.abc.Pos):
        if not pos:
            if self.x_mm is None and self.y_mm is None:
                return
            self.draw_current_fov(self.x_mm, self.y_mm)
        else:
            x_mm = pos.x_mm
            y_mm = pos.y_mm
            self.draw_current_fov(x_mm, y_mm)
            self.x_mm = x_mm
            self.y_mm = y_mm

    def get_FOV_pixel_coordinates(self, x_mm, y_mm, rotation_deg=None):
        """Stage mm -> image pixel corners of the FOV box.

        rotation_deg lets batch callers resolve the rotation once per redraw
        instead of once per FOV (resolution reads the placement/holder files).
        """
        if self.sample == "glass slide":
            current_FOV_top_left = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel - self.fov_size_mm / 2 / self.mm_per_pixel),
                round(
                    self.image_height
                    - (self.origin_y_pixel + y_mm / self.mm_per_pixel)
                    - self.fov_size_mm / 2 / self.mm_per_pixel
                ),
            )
            current_FOV_bottom_right = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel + self.fov_size_mm / 2 / self.mm_per_pixel),
                round(
                    self.image_height
                    - (self.origin_y_pixel + y_mm / self.mm_per_pixel)
                    + self.fov_size_mm / 2 / self.mm_per_pixel
                ),
            )
        else:
            if rotation_deg is None:
                rotation_deg = self._current_rotation_deg()
            if rotation_deg == 0.0:
                # Legacy arithmetic kept verbatim (origin_pixel form) so the
                # unrotated path is bit-for-bit what it always was.
                center_x_pixel = self.origin_x_pixel + x_mm / self.mm_per_pixel
                center_y_pixel = self.origin_y_pixel + y_mm / self.mm_per_pixel
            else:
                # Stage -> plate frame (R(-theta) about A1), then the nominal
                # drawing's anchor. The pivot is the same composed a1 the
                # planner rotates about, so a planned well-center FOV lands on
                # the well's drawn (nominal) position.
                plate_x, plate_y = rotate_deg(-rotation_deg, x_mm - self.a1_x_mm, y_mm - self.a1_y_mm)
                center_x_pixel = self.a1_x_pixel + plate_x / self.mm_per_pixel
                center_y_pixel = self.a1_y_pixel + plate_y / self.mm_per_pixel
            half_fov_pixel = self.fov_size_mm / 2 / self.mm_per_pixel
            current_FOV_top_left = (
                round(center_x_pixel - half_fov_pixel),
                round(center_y_pixel - half_fov_pixel),
            )
            current_FOV_bottom_right = (
                round(center_x_pixel + half_fov_pixel),
                round(center_y_pixel + half_fov_pixel),
            )
        return current_FOV_top_left, current_FOV_bottom_right

    def draw_current_fov(self, x_mm, y_mm):
        self.fov_overlay.fill(0)
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(
            self.fov_overlay, current_FOV_top_left, current_FOV_bottom_right, (255, 0, 0, 255), self.box_line_thickness
        )
        self.fov_overlay_item.setImage(self.fov_overlay)

    def register_fov(self, x_mm, y_mm):
        color = (0, 0, 255, 255)  # Blue RGBA
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(
            self.background_image, current_FOV_top_left, current_FOV_bottom_right, color, self.box_line_thickness
        )
        self.background_item.setImage(self.background_image)

    def register_fovs_to_image(self, fov_list):
        """
        Register FOVs to image with single display update.

        Args:
            fov_list: List of tuples (x_mm, y_mm) or (x_mm, y_mm, z_mm), or list of FovCenter objects
        """
        if not fov_list:
            return

        color = (252, 174, 30, 128)  # Yellow RGBA
        rotation_deg = self._current_rotation_deg()  # once per batch, not per FOV
        for fov in fov_list:
            # Handle tuple (2D or 3D) and FovCenter object formats
            if isinstance(fov, tuple):
                x_mm = fov[0]
                y_mm = fov[1]
            else:
                x_mm = fov.x_mm
                y_mm = fov.y_mm
            current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(
                x_mm, y_mm, rotation_deg=rotation_deg
            )
            cv2.rectangle(
                self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, color, self.box_line_thickness
            )
        # Single update after all rectangles are drawn
        self.scan_overlay_item.setImage(self.scan_overlay)

    def deregister_fovs_from_image(self, fov_list):
        """
        Deregister FOVs from image with single display update.

        Args:
            fov_list: List of tuples (x_mm, y_mm) or (x_mm, y_mm, z_mm), or list of FovCenter objects
        """
        if not fov_list:
            return

        rotation_deg = self._current_rotation_deg()  # once per batch, not per FOV
        for fov in fov_list:
            # Handle tuple (2D or 3D) and FovCenter object formats
            if isinstance(fov, tuple):
                x_mm = fov[0]
                y_mm = fov[1]
            else:
                x_mm = fov.x_mm
                y_mm = fov.y_mm
            current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(
                x_mm, y_mm, rotation_deg=rotation_deg
            )
            cv2.rectangle(
                self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, (0, 0, 0, 0), self.box_line_thickness
            )
        # Single update after all rectangles are cleared
        self.scan_overlay_item.setImage(self.scan_overlay)

    def register_focus_point(self, x_mm, y_mm):
        """Draw focus point marker as filled circle centered on the FOV"""
        color = (0, 255, 0, 255)  # Green RGBA
        # Get FOV corner coordinates, then calculate FOV center pixel coordinates
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        center_x = (current_FOV_top_left[0] + current_FOV_bottom_right[0]) // 2
        center_y = (current_FOV_top_left[1] + current_FOV_bottom_right[1]) // 2
        # Draw a filled circle at the center
        radius = 5  # Radius of circle in pixels
        cv2.circle(self.focus_point_overlay, (center_x, center_y), radius, color, -1)  # -1 thickness means filled
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def clear_focus_points(self):
        """Clear just the focus point overlay"""
        self.focus_point_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def clear_slide(self):
        self.background_image = self.background_image_copy.copy()
        self.background_item.setImage(self.background_image)
        self.draw_current_fov(self.x_mm, self.y_mm)

    def clear_overlay(self):
        self.scan_overlay.fill(0)
        self.scan_overlay_item.setImage(self.scan_overlay)
        self.focus_point_overlay.fill(0)
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def pixel_to_stage_mm(self, x_pixel, y_pixel):
        """Image pixel -> stage mm: the inverse of get_FOV_pixel_coordinates.

        The clicked pixel is a NOMINAL plate position (the drawing is unrotated),
        so under a measured rotation the plate-frame point rotates forward
        (R(+theta) about A1) into stage coordinates - a double-click on a drawn
        well navigates to where that well actually sits.
        """
        rotation_deg = self._current_rotation_deg()  # pitch-0 formats resolve to 0.0
        if rotation_deg == 0.0:
            # Legacy arithmetic kept verbatim for the unrotated path.
            x_mm = (x_pixel - self.origin_x_pixel) * self.mm_per_pixel
            y_mm = (y_pixel - self.origin_y_pixel) * self.mm_per_pixel
            return x_mm, y_mm
        plate_x = (x_pixel - self.a1_x_pixel) * self.mm_per_pixel
        plate_y = (y_pixel - self.a1_y_pixel) * self.mm_per_pixel
        dx, dy = rotate_deg(rotation_deg, plate_x, plate_y)
        return (self.a1_x_mm + dx, self.a1_y_mm + dy)

    def handle_mouse_click(self, evt):
        if not evt.double():
            return
        try:
            # Get mouse position in image coordinates (independent of zoom)
            mouse_point = self.background_item.mapFromScene(evt.scenePos())

            x_mm, y_mm = self.pixel_to_stage_mm(mouse_point.x(), mouse_point.y())

            self._log.debug(f"Got double click at (x_mm, y_mm) = {x_mm, y_mm}")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

        except Exception as e:
            print(f"Error processing navigation click: {e}")
            return


from scipy.interpolate import SmoothBivariateSpline, RBFInterpolator


class FocusMap:
    """Handles fitting and interpolation of slide surfaces through measured focus points"""

    def __init__(self, smoothing_factor=0.1):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.smoothing_factor = smoothing_factor
        self.method = "spline"  # can be 'spline' or 'rbf' or 'constant'
        self.global_surface_fit = None
        self.global_method = None
        self.global_errors = None
        self.region_surface_fits = {}
        self.region_methods = {}
        self.region_errors = {}
        self.fit_by_region = False
        self.focus_points = {}
        self.is_fitted = False

    def generate_grid_coordinates(
        self, scanCoordinates: ScanCoordinates, rows: int = 4, cols: int = 4, add_margin: bool = False
    ) -> Dict[str, List[Tuple[float, float]]]:
        """
        Generate focus point grid coordinates for each scan region

        Args:
            scanCoordinates: ScanCoordinates instance containing regions
            rows: Number of rows in focus grid
            cols: Number of columns in focus grid
            add_margin: If True, adds margin to avoid points at region borders

        Returns:
            Dictionary with region_id as key and list of (x,y) coordinate tuples as value
        """
        if rows <= 0 or cols <= 0:
            raise ValueError("Number of rows and columns must be greater than 0")

        # Dictionary to store focus points by region
        focus_coords = {}

        # Generate focus points for each region
        for region_id, region_coords in scanCoordinates.region_fov_coordinates.items():
            # Get region bounds
            bounds = scanCoordinates.get_region_bounds(region_id)
            if not bounds:
                continue

            region_focus_coords = []
            x_min, x_max = bounds["min_x"], bounds["max_x"]
            y_min, y_max = bounds["min_y"], bounds["max_y"]

            # For add_margin we are using one more row and col, taking the middle points on the grid so that the
            # focus points are not located at the edges of the scaning grid.
            # TODO: set a value for margin from user input
            # Calculate x and y positions
            if add_margin:
                # With margin, divide the area into equal cells and use cell centers
                x_step = (x_max - x_min) / cols
                y_step = (y_max - y_min) / rows

                x_positions = [x_min + (j + 0.5) * x_step for j in range(cols)]
                y_positions = [y_min + (i + 0.5) * y_step for i in range(rows)]
            else:
                # Without margin, handle special cases for rows=1 or cols=1
                if rows == 1:
                    y_positions = [y_min + (y_max - y_min) / 2]  # Center point
                else:
                    y_step = (y_max - y_min) / (rows - 1)
                    y_positions = [y_min + i * y_step for i in range(rows)]

                if cols == 1:
                    x_positions = [x_min + (x_max - x_min) / 2]  # Center point
                else:
                    x_step = (x_max - x_min) / (cols - 1)
                    x_positions = [x_min + j * x_step for j in range(cols)]

            # Generate grid points by combining x and y positions
            for y in y_positions:
                for x in x_positions:
                    # Check if point is within region bounds
                    if scanCoordinates.validate_coordinates(x, y) and scanCoordinates.region_contains_coordinate(
                        region_id, x, y
                    ):
                        region_focus_coords.append((x, y))

            focus_coords[region_id] = region_focus_coords

        return focus_coords

    def set_method(self, method: str):
        """Set interpolation method

        Args:
            method (str): Either 'spline' or 'rbf' (Radial Basis Function)
        """
        if method not in ["spline", "rbf", "constant"]:
            raise ValueError("Method must be either 'spline' or 'rbf' or 'constant'")
        self.method = method
        self.is_fitted = False
        self.region_surface_fits = {}  # Reset region fits when method changes

    def set_fit_by_region(self, fit_by_region: bool):
        """Set if the surface fit should be done by region or globally

        Args:
            fit_by_region (bool): If True, fitting functions will be bounded by region
        """
        self.fit_by_region = fit_by_region

    def fit(self, points: Dict[str, List[Tuple[float, float, float]]]):
        """Fit surface through provided focus points

        Args:
            points: A dictionary with region_id as key and list of (x,y,z) tuples as value

        Returns:
            If by_region=False: tuple (mean_error, std_error) in mm
            If by_region=True: dict with region_id as key and (mean_error, std_error) as value
        """
        if not hasattr(self, "fit_by_region"):
            raise ValueError("fit_by_region must be set before fitting")

        self.focus_points = points

        if self.fit_by_region:
            self.region_surface_fits = {}
            self.region_methods = {}
            self.region_errors = {}
            for region_id, region_points in points.items():
                if len(region_points) in [0, 2, 3]:
                    raise ValueError("Use 1 point for constant plane, or at least 4 points for surface fitting")
                self.region_surface_fits[region_id], self.region_methods[region_id], self.region_errors[region_id] = (
                    self._fit_surface(region_points)
                )
            if self.method == "constant":
                mean_error = 0
                std_error = 0
            else:
                all_errors = np.concatenate([errors for errors in self.region_errors.values()])
                mean_error = np.mean(all_errors)
                std_error = np.std(all_errors)
        else:
            all_points = []
            for region_points in points.values():
                all_points.extend(region_points)
            if len(all_points) < 4:
                raise ValueError("Use 1 point for constant plane, or at least 4 points for surface fitting")

            self.global_surface_fit, self.global_method, self.global_errors = self._fit_surface(all_points)
            mean_error = np.mean(self.global_errors)
            std_error = np.std(self.global_errors)

        self.is_fitted = True

        return mean_error, std_error

    def _fit_surface(self, points: List[Tuple[float, float, float]]) -> Tuple[Callable, str, np.ndarray]:
        """Fit surface through provided focus points for a specific region or globally

        Args:
            points (list): List of (x,y,z) tuples

        Returns:
            tuple: (surface_fit, method, errors)
        """
        points_array = np.array(points)
        x = points_array[:, 0]
        y = points_array[:, 1]
        z = points_array[:, 2]

        if len(points) == 1:
            # For single point, create a flat plane at that z-height
            if self.method != "constant":
                self._log.warning("One point can only be used for constant plane, falling back to constant")
            z_value = z[0]
            surface_fit = self._fit_constant_plane(z_value)
            method = "constant"

            self.is_fitted = True
            errors = None  # No error for a single point
        else:
            if self.method == "spline":
                try:
                    surface_fit = SmoothBivariateSpline(
                        x, y, z, kx=3, ky=3, s=self.smoothing_factor  # cubic spline in x  # cubic spline in y
                    )
                    method = self.method
                except Exception as e:
                    self._log.warning(f"Spline fitting failed: {str(e)}, falling back to RBF")
                    surface_fit = self._fit_rbf(x, y, z)
                    method = "rbf"
            elif self.method == "constant":
                self._log.warning("Constant method cannot be used for multiple points, falling back to RBF")
                surface_fit = self._fit_rbf(x, y, z)
                method = "rbf"
            else:
                surface_fit = self._fit_rbf(x, y, z)
                method = "rbf"

            self.is_fitted = True
            errors = self._calculate_fitting_errors(points, surface_fit, method)

        return surface_fit, method, errors

    def _fit_rbf(self, x, y, z):
        """Fit using Radial Basis Function interpolation"""
        xy = np.column_stack((x, y))
        return RBFInterpolator(xy, z, kernel="thin_plate_spline", epsilon=self.smoothing_factor)

    def _fit_constant_plane(self, z_value):
        """Create a constant height plane"""

        def constant_plane(x, y):
            if isinstance(x, np.ndarray):
                return np.full_like(x, z_value)
            else:
                return z_value

        return constant_plane

    def interpolate(self, x, y, region_id=None):
        """Get interpolated Z value at given (x,y) coordinates

        Args:
            x (float or array): X coordinate(s)
            y (float or array): Y coordinate(s)
            region_id: Region identifier for region-specific interpolation

        Returns:
            float or array: Interpolated Z value(s)
        """
        if not self.is_fitted and not self.region_surface_fits:
            raise RuntimeError("Must fit surface before interpolating")

        # If fit_by_region is True and region_id is provided, use region-specific surface
        if self.fit_by_region:
            if region_id is None or region_id not in self.region_surface_fits:
                raise ValueError(f"Region {region_id} not found")
            surface_fit = self.region_surface_fits[region_id]
            method = self.region_methods[region_id]
        else:
            surface_fit = self.global_surface_fit
            method = self.global_method

        return self._interpolate_helper(x, y, surface_fit, method)

    def _interpolate_helper(self, x, y, surface_fit, method):
        if np.isscalar(x) and np.isscalar(y):
            if method == "spline":
                return float(surface_fit.ev(x, y))
            elif method == "constant":
                return surface_fit(x, y)
            else:  # rbf
                return float(surface_fit([[x, y]]))
        else:
            x = np.asarray(x)
            y = np.asarray(y)
            if method == "spline":
                return surface_fit.ev(x, y)
            elif method == "constant":
                return surface_fit(x, y)
            else:  # rbf
                xy = np.column_stack((x.ravel(), y.ravel()))
                z = surface_fit(xy)
                return z.reshape(x.shape)

    def _calculate_fitting_errors(
        self, points: List[Tuple[float, float, float]], surface_fit: Callable, method: str
    ) -> np.ndarray:
        """Calculate absolute errors at measured points"""
        errors = []
        for x, y, z_measured in points:
            z_fit = self._interpolate_helper(x, y, surface_fit, method)
            errors.append(abs(z_fit - z_measured))
        return np.array(errors)

    def get_surface_grid(self, x_range, y_range, num_points=50, region_id=None):
        """Generate grid of interpolated Z values for visualization

        Args:
            x_range (tuple): (min_x, max_x)
            y_range (tuple): (min_y, max_y)
            num_points (int): Number of points per dimension
            region_id: Region identifier for region-specific visualization

        Returns:
            tuple: (X grid, Y grid, Z grid)
        """
        if not self.is_fitted:
            raise RuntimeError("Must fit surface before generating grid")

        x = np.linspace(x_range[0], x_range[1], num_points)
        y = np.linspace(y_range[0], y_range[1], num_points)
        X, Y = np.meshgrid(x, y)
        Z = self.interpolate(X, Y, region_id)

        return X, Y, Z
