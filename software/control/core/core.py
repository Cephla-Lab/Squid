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
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.configuration_mananger import ConfigurationManager
from control.core.contrast_manager import ContrastManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
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
import control.utils_config as utils_config
import squid.logging


from typing import List, Tuple, Optional, Dict, Any, Callable, TypeVar
from queue import Queue
from threading import Thread, Lock
from pathlib import Path
from datetime import datetime
from enum import Enum
from control.utils_config import ChannelConfig, ChannelMode, LaserAFConfig
import time
import itertools
import json
import math
import numpy as np
import pandas as pd
import cv2
import imageio as iio
import squid.abc
import scipy.ndimage

if ENABLE_NL5:
    import control.NL5 as NL5
else:
    NL5 = TypeVar("NL5")


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


class SlidePositionControlWorker(QObject):
    finished = Signal()
    signal_stop_live = Signal()
    signal_resume_live = Signal()

    def __init__(self, slidePositionController, stage: AbstractStage, home_x_and_y_separately=False):
        QObject.__init__(self)
        self.slidePositionController = slidePositionController
        self.stage = stage
        self.liveController = self.slidePositionController.liveController
        self.home_x_and_y_separately = home_x_and_y_separately

    def move_to_slide_loading_position(self):
        was_live = self.liveController.is_live
        if was_live:
            self.signal_stop_live.emit()

        # retract z
        self.slidePositionController.z_pos = self.stage.get_pos().z_mm  # zpos at the beginning of the scan
        self.stage.move_z_to(OBJECTIVE_RETRACTED_POS_MM, blocking=False)
        self.stage.wait_for_idle(SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S)

        print("z retracted")
        self.slidePositionController.objective_retracted = True

        # move to position
        # for well plate
        if self.slidePositionController.is_for_wellplate:
            # So we can home without issue, set our limits to something large.  Then later reset them back to
            # the safe values.
            a_large_limit_mm = 100
            self.stage.set_limits(
                x_pos_mm=a_large_limit_mm,
                x_neg_mm=-a_large_limit_mm,
                y_pos_mm=a_large_limit_mm,
                y_neg_mm=-a_large_limit_mm,
            )

            # home for the first time
            if not self.slidePositionController.homing_done:
                print("running homing first")
                timestamp_start = time.time()
                # x needs to be at > + 20 mm when homing y
                self.stage.move_x(20)
                self.stage.home(x=False, y=True, z=False, theta=False)
                self.stage.home(x=True, y=False, z=False, theta=False)

                self.slidePositionController.homing_done = True
            # homing done previously
            else:
                self.stage.move_x_to(20)
                self.stage.move_y_to(SLIDE_POSITION.LOADING_Y_MM)
                self.stage.move_x_to(SLIDE_POSITION.LOADING_X_MM)
            # set limits again
            self.stage.set_limits(
                x_pos_mm=self.stage.get_config().X_AXIS.MAX_POSITION,
                x_neg_mm=self.stage.get_config().X_AXIS.MIN_POSITION,
                y_pos_mm=self.stage.get_config().Y_AXIS.MAX_POSITION,
                y_neg_mm=self.stage.get_config().Y_AXIS.MIN_POSITION,
            )
        else:

            # for glass slide
            if self.slidePositionController.homing_done == False or SLIDE_POTISION_SWITCHING_HOME_EVERYTIME:
                if self.home_x_and_y_separately:
                    self.stage.home(x=True, y=False, z=False, theta=False)
                    self.stage.move_x_to(SLIDE_POSITION.LOADING_X_MM)

                    self.stage.home(x=False, y=True, z=False, theta=False)
                    self.stage.move_y_to(SLIDE_POSITION.LOADING_Y_MM)
                else:
                    self.stage.home(x=True, y=True, z=False, theta=False)

                    self.stage.move_x_to(SLIDE_POSITION.LOADING_X_MM)
                    self.stage.move_y_to(SLIDE_POSITION.LOADING_Y_MM)
                self.slidePositionController.homing_done = True
            else:
                self.stage.move_y_to(SLIDE_POSITION.LOADING_Y_MM)
                self.stage.move_x_to(SLIDE_POSITION.LOADING_X_MM)

        if was_live:
            self.signal_resume_live.emit()

        self.slidePositionController.slide_loading_position_reached = True
        self.finished.emit()

    def move_to_slide_scanning_position(self):
        was_live = self.liveController.is_live
        if was_live:
            self.signal_stop_live.emit()

        # move to position
        # for well plate
        if self.slidePositionController.is_for_wellplate:
            # home for the first time
            if not self.slidePositionController.homing_done:
                timestamp_start = time.time()

                # x needs to be at > + 20 mm when homing y
                self.stage.move_x_to(20)
                # home y
                self.stage.home(x=False, y=True, z=False, theta=False)
                # home x
                self.stage.home(x=True, y=False, z=False, theta=False)
                self.slidePositionController.homing_done = True

                # move to scanning position
                self.stage.move_x_to(SLIDE_POSITION.SCANNING_X_MM)
                self.stage.move_y_to(SLIDE_POSITION.SCANNING_Y_MM)
            else:
                self.stage.move_x_to(SLIDE_POSITION.SCANNING_X_MM)
                self.stage.move_y_to(SLIDE_POSITION.SCANNING_Y_MM)
        else:
            if self.slidePositionController.homing_done == False or SLIDE_POTISION_SWITCHING_HOME_EVERYTIME:
                if self.home_x_and_y_separately:
                    self.stage.home(x=False, y=True, z=False, theta=False)

                    self.stage.move_y_to(SLIDE_POSITION.SCANNING_Y_MM)

                    self.stage.home(x=True, y=False, z=False, theta=False)
                    self.stage.move_x_to(SLIDE_POSITION.SCANNING_X_MM)
                else:
                    self.stage.home(x=True, y=True, z=False, theta=False)

                    self.stage.move_y_to(SLIDE_POSITION.SCANNING_Y_MM)
                    self.stage.move_x_to(SLIDE_POSITION.SCANNING_X_MM)
                self.slidePositionController.homing_done = True
            else:
                self.stage.move_y_to(SLIDE_POSITION.SCANNING_Y_MM)
                self.stage.move_x_to(SLIDE_POSITION.SCANNING_X_MM)

        # restore z
        if self.slidePositionController.objective_retracted:
            self.stage.move_z_to(self.slidePositionController.z_pos)
            self.slidePositionController.objective_retracted = False
            print("z position restored")

        if was_live:
            self.signal_resume_live.emit()

        self.slidePositionController.slide_scanning_position_reached = True
        self.finished.emit()


class SlidePositionController(QObject):
    signal_slide_loading_position_reached = Signal()
    signal_slide_scanning_position_reached = Signal()
    signal_clear_slide = Signal()

    def __init__(self, stage: AbstractStage, liveController, is_for_wellplate=False):
        QObject.__init__(self)
        self.stage = stage
        self.liveController = liveController
        self.slide_loading_position_reached = False
        self.slide_scanning_position_reached = False
        self.homing_done = False
        self.is_for_wellplate = is_for_wellplate
        self.retract_objective_before_moving = RETRACT_OBJECTIVE_BEFORE_MOVING_TO_LOADING_POSITION
        self.objective_retracted = False
        self.thread = None

    def move_to_slide_loading_position(self):
        # create a QThread object
        self.thread = QThread()
        # create a worker object
        self.slidePositionControlWorker = SlidePositionControlWorker(self, self.stage)
        # move the worker to the thread
        self.slidePositionControlWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.slidePositionControlWorker.move_to_slide_loading_position)
        self.slidePositionControlWorker.signal_stop_live.connect(self.slot_stop_live, type=Qt.BlockingQueuedConnection)
        self.slidePositionControlWorker.signal_resume_live.connect(
            self.slot_resume_live, type=Qt.BlockingQueuedConnection
        )
        self.slidePositionControlWorker.finished.connect(self.signal_slide_loading_position_reached.emit)
        self.slidePositionControlWorker.finished.connect(self.slidePositionControlWorker.deleteLater)
        self.slidePositionControlWorker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.quit)
        # self.slidePositionControlWorker.finished.connect(self.threadFinished,type=Qt.BlockingQueuedConnection)
        # start the thread
        self.thread.start()

    def move_to_slide_scanning_position(self):
        # create a QThread object
        self.thread = QThread()
        # create a worker object
        self.slidePositionControlWorker = SlidePositionControlWorker(self, self.stage)
        # move the worker to the thread
        self.slidePositionControlWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.slidePositionControlWorker.move_to_slide_scanning_position)
        self.slidePositionControlWorker.signal_stop_live.connect(self.slot_stop_live, type=Qt.BlockingQueuedConnection)
        self.slidePositionControlWorker.signal_resume_live.connect(
            self.slot_resume_live, type=Qt.BlockingQueuedConnection
        )
        self.slidePositionControlWorker.finished.connect(self.signal_slide_scanning_position_reached.emit)
        self.slidePositionControlWorker.finished.connect(self.slidePositionControlWorker.deleteLater)
        self.slidePositionControlWorker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.quit)
        # self.slidePositionControlWorker.finished.connect(self.threadFinished,type=Qt.BlockingQueuedConnection)
        # start the thread
        print("before thread.start()")
        self.thread.start()
        self.signal_clear_slide.emit()

    def slot_stop_live(self):
        self.liveController.stop_live()

    def slot_resume_live(self):
        self.liveController.start_live()


class TrackingController(QObject):
    signal_tracking_stopped = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(ChannelMode)

    def __init__(
        self,
        camera: AbstractCamera,
        microcontroller: Microcontroller,
        stage: AbstractStage,
        objectiveStore,
        channelConfigurationManager,
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
        self.channelConfigurationManager = channelConfigurationManager
        self.liveController = liveController
        self.autofocusController = autofocusController
        self.imageDisplayWindow = imageDisplayWindow
        self.tracker = tracking.Tracker_Image()

        self.tracking_time_interval_s = 0

        self.display_resolution_scaling = Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
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
            utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
            self.channelConfigurationManager.save_current_configuration_to_path(
                self.objectiveStore.current_objective,
                os.path.join(self.base_path, self.experiment_ID) + "/configurations.xml",
            )  # save the configuration for the experiment
        except:
            self._log.info("error in making a new folder")
            pass

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
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
    signal_current_configuration = Signal(ChannelMode)

    def __init__(self, trackingController: TrackingController):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.trackingController = trackingController

        self.camera: AbstractCamera = self.trackingController.camera
        self.stage = self.trackingController.stage
        self.microcontroller = self.trackingController.microcontroller
        self.liveController = self.trackingController.liveController
        self.autofocusController = self.trackingController.autofocusController
        self.channelConfigurationManager = self.trackingController.channelConfigurationManager
        self.imageDisplayWindow = self.trackingController.imageDisplayWindow
        self.display_resolution_scaling = self.trackingController.display_resolution_scaling
        self.counter = self.trackingController.counter
        self.experiment_ID = self.trackingController.experiment_ID
        self.base_path = self.trackingController.base_path
        self.selected_configurations = self.trackingController.selected_configurations
        self.tracker = trackingController.tracker

        self.number_of_selected_configurations = len(self.selected_configurations)

        self.image_saver = ImageSaver_Tracking(
            base_path=os.path.join(self.base_path, self.experiment_ID), image_format="bmp"
        )

    def _select_config(self, config: ChannelMode):
        self.signal_current_configuration.emit(config)
        # TODO(imo): replace with illumination controller.
        self.liveController.set_microscope_mode(config)
        self.microcontroller.wait_till_operation_is_completed()
        self.liveController.turn_on_illumination()  # keep illumination on for single configuration acqusition
        self.microcontroller.wait_till_operation_is_completed()

    def run(self):

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
                # display image
                image_to_display_ = utils.crop_image(
                    image_,
                    round(image_.shape[1] * self.liveController.display_resolution_scaling),
                    round(image_.shape[0] * self.liveController.display_resolution_scaling),
                )
                self.image_to_display_multi.emit(image_to_display_, config_.illumination_source)
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


class ImageDisplayWindow(QMainWindow):
    image_click_coordinates = Signal(int, int, int, int)

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
        self.line_roi = None
        self.is_drawing_line = False
        self.line_start_pos = None
        self.line_end_pos = None
        self.drawing_cursor = QCursor(Qt.CrossCursor)  # Cross cursor for drawing mode
        self.normal_cursor = QCursor(Qt.ArrowCursor)  # Normal cursor
        self.preview_line = None
        self.start_point_marker = None

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

        # Add line profiler toggle button
        self.btn_line_profiler = QPushButton("Line Profiler")
        self.btn_line_profiler.setCheckable(True)
        self.btn_line_profiler.setChecked(False)
        self.btn_line_profiler.setEnabled(False)
        self.btn_line_profiler.clicked.connect(self.toggle_line_profiler)

        # Add well selector toggle button
        self.btn_well_selector = QPushButton("Show Well Selector")
        self.btn_well_selector.setCheckable(False)

        # Add labels to status layout with spacing
        status_layout.addWidget(self.cursor_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.pixel_value_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.stage_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.piezo_position_label)
        status_layout.addStretch()  # Push labels to the left
        status_layout.addWidget(self.btn_well_selector)  # Add well selector button
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.btn_line_profiler)  # Add line profiler button

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
            self.graphics_widget.img.setBorder("w")
            self.graphics_widget.view.ui.roiBtn.hide()
            self.graphics_widget.view.ui.menuBtn.hide()
            self.LUTWidget = self.graphics_widget.view.getHistogramWidget()
            self.LUTWidget.region.sigRegionChanged.connect(self.update_contrast_limits)
            self.LUTWidget.region.sigRegionChangeFinished.connect(self.update_contrast_limits)
        else:
            self.graphics_widget.img = pg.ImageItem(border="w")
            self.graphics_widget.view.addItem(self.graphics_widget.img)

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

        # Create line profiler widget
        self.line_profiler_widget = pg.GraphicsLayoutWidget()
        self.line_profiler_plot = self.line_profiler_widget.addPlot()
        self.line_profiler_plot.setLabel("left", "Intensity")
        self.line_profiler_plot.setLabel("bottom", "Position")
        self.line_profiler_widget.hide()  # Initially hidden
        self.line_profiler_manual_range = False  # Flag to track if y-range is manually set

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
        desktopWidget = QDesktopWidget()
        width = min(desktopWidget.height() * 0.9, 1000)
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

    def toggle_line_profiler(self):
        """Toggle the visibility of the line profiler widget."""
        if self.btn_line_profiler.isChecked():
            self.line_profiler_widget.show()
            if self.line_roi is None:
                # Start in drawing mode
                self.is_drawing_line = True
                self.line_start_pos = None
                self.line_end_pos = None
                # Set cross cursor
                if self.show_LUT:
                    self.graphics_widget.view.getView().setCursor(self.drawing_cursor)
                else:
                    self.graphics_widget.view.setCursor(self.drawing_cursor)
                self._log.info("Line profiler opened - ready to draw line")
            else:
                self.line_roi.show()
                self.update_line_profile()
        else:
            self.line_profiler_widget.hide()
            if self.line_roi is not None:
                self.line_roi.hide()
            # Reset cursor to normal
            if self.show_LUT:
                self.graphics_widget.view.getView().setCursor(self.normal_cursor)
            else:
                self.graphics_widget.view.setCursor(self.normal_cursor)

        # Connect to the view range changed signal to detect manual range changes
        self.line_profiler_plot.sigRangeChanged.connect(self._on_range_changed)

    def _on_range_changed(self, view_range):
        """Handle manual range changes in the line profiler plot."""
        self.line_profiler_manual_range = True

    def create_line_roi(self):
        """Create a line ROI for intensity profiling."""
        if self.line_roi is None and self.line_start_pos is not None and self.line_end_pos is not None:
            try:
                # Convert coordinates to Point objects
                start_point = pg.Point(self.line_start_pos[0], self.line_start_pos[1])
                end_point = pg.Point(self.line_end_pos[0], self.line_end_pos[1])

                # Create the line ROI with width parameter
                self.line_roi = pg.LineROI(
                    pos1=start_point,
                    pos2=end_point,
                    width=5,  # Default width in pixels
                    pen=pg.mkPen("y", width=2),
                    hoverPen=pg.mkPen("y", width=2),
                    handlePen=pg.mkPen("y", width=2),
                    handleHoverPen=pg.mkPen("y", width=2),
                    movable=True,
                    rotatable=True,
                    resizable=True,
                )

                # Add the ROI to the view
                if self.show_LUT:
                    self.graphics_widget.view.getView().addItem(self.line_roi)
                else:
                    self.graphics_widget.view.addItem(self.line_roi)

                # Connect signal
                self.line_roi.sigRegionChanged.connect(self.update_line_profile)
                self.update_line_profile()
                self._log.info("Line ROI created successfully")
            except Exception as e:
                self._log.error(f"Error creating line ROI: {str(e)}")
                self.line_roi = None
                self.line_start_pos = None
                self.line_end_pos = None

    def update_line_profile(self):
        """Update the line profile plot based on the line ROI."""
        if not self.btn_line_profiler.isChecked() or self.line_roi is None:
            return

        try:
            if hasattr(self.graphics_widget.img, "image"):
                image = self.graphics_widget.img.image
                if image is not None:
                    # Get the line ROI state
                    state = self.line_roi.getState()
                    pos = state["pos"]
                    size = state["size"]
                    angle = state["angle"]
                    print(angle)
                    angle = np.radians(angle)

                    # Calculate start and end points
                    start = (pos[0], pos[1])
                    end = (pos[0] + size[0] * np.cos(angle), pos[1] + size[0] * np.sin(angle))

                    # Convert ROI coordinates to image coordinates
                    start_img = self.graphics_widget.img.mapFromView(pg.Point(start[0], start[1]))
                    end_img = self.graphics_widget.img.mapFromView(pg.Point(end[0], end[1]))

                    # Get the profile along the line
                    profile = self.get_line_profile(image, start_img, end_img, size[1])  # size[1] is the width

                    # Clear previous plots
                    self.line_profiler_plot.clear()

                    # Calculate pixel distance for x-axis
                    pixel_distance = np.linspace(0, size[0], len(profile))

                    # Plot the profile
                    self.line_profiler_plot.plot(pixel_distance, profile, pen="w", name="Intensity Profile")

                    # Set labels
                    self.line_profiler_plot.setLabel("left", "Intensity")
                    self.line_profiler_plot.setLabel("bottom", "Distance (pixels)")

                    # Add legend
                    self.line_profiler_plot.addLegend()

                    # Only auto-range if not manually set
                    if not self.line_profiler_manual_range:
                        self.line_profiler_plot.autoRange()
        except Exception as e:
            self._log.error(f"Error updating line profile: {str(e)}")

    def get_line_profile(self, image, start, end, width=1):
        """Get intensity profile along a line with specified width."""
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
            width_points = max(1, int(width))  # Ensure at least 1 point
            width_offsets = np.linspace(-width / 2, width / 2, width_points)

            # Initialize profile array
            profile = np.zeros(num_points)

            # Sample points along the width
            for w in width_offsets:
                x_offset = x + perp_vec[0] * w
                y_offset = y + perp_vec[1] * w

                # Get values at these points
                values = scipy.ndimage.map_coordinates(image, [y_offset, x_offset], order=1)
                profile += values

            # Average the values
            profile /= width_points

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
                # Get the view that received the click
                if self.show_LUT:
                    view = self.graphics_widget.view.getView()
                else:
                    view = self.graphics_widget.view

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
                    if self.show_LUT:
                        self.graphics_widget.view.getView().addItem(self.start_point_marker)
                    else:
                        self.graphics_widget.view.addItem(self.start_point_marker)

                    # Create preview line
                    self.preview_line = pg.PlotDataItem(pen=pg.mkPen("y", width=2, style=Qt.DashLine))
                    if self.show_LUT:
                        self.graphics_widget.view.getView().addItem(self.preview_line)
                    else:
                        self.graphics_widget.view.addItem(self.preview_line)
                else:
                    # Second click - finish drawing
                    self.line_end_pos = (view_coord.x(), view_coord.y())
                    self._log.info(f"Line end position set to: {self.line_end_pos}")

                    # Remove preview line and start point marker
                    if self.preview_line is not None:
                        if self.show_LUT:
                            self.graphics_widget.view.getView().removeItem(self.preview_line)
                        else:
                            self.graphics_widget.view.removeItem(self.preview_line)
                        self.preview_line = None

                    if self.start_point_marker is not None:
                        if self.show_LUT:
                            self.graphics_widget.view.getView().removeItem(self.start_point_marker)
                        else:
                            self.graphics_widget.view.removeItem(self.start_point_marker)
                        self.start_point_marker = None

                    self.create_line_roi()
                    self.is_drawing_line = False
                    # Reset cursor to normal
                    view.setCursor(self.normal_cursor)
            except Exception as e:
                self._log.error(f"Error drawing line: {str(e)}")
                self.is_drawing_line = False
                self.line_start_pos = None
                self.line_end_pos = None
                # Clean up any remaining preview items
                if self.preview_line is not None:
                    if self.show_LUT:
                        self.graphics_widget.view.getView().removeItem(self.preview_line)
                    else:
                        self.graphics_widget.view.removeItem(self.preview_line)
                    self.preview_line = None
                if self.start_point_marker is not None:
                    if self.show_LUT:
                        self.graphics_widget.view.getView().removeItem(self.start_point_marker)
                    else:
                        self.graphics_widget.view.removeItem(self.start_point_marker)
                    self.start_point_marker = None
            return

        # Handle double clicks for other purposes
        if not evt.double():
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

    def display_image(self, image):
        # enable the line profiler button after the first image is displayed
        if self.first_image:
            self.first_image = False
            self.btn_line_profiler.setEnabled(True)

        if ENABLE_TRACKING:
            image = np.copy(image)
            self.image_height, self.image_width = image.shape[:2]
            if self.draw_rectangle:
                cv2.rectangle(image, self.ptRect1, self.ptRect2, (255, 255, 255), 4)
                self.draw_rectangle = False

        info = np.iinfo(image.dtype) if np.issubdtype(image.dtype, np.integer) else np.finfo(image.dtype)
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

        if self.line_roi is not None and self.btn_line_profiler.isChecked():
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

    def __init__(self, objectivestore, camera_pixel_size, sample="glass slide", invertX=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.sample = sample
        self.objectiveStore = objectivestore
        self.camera_sensor_pixel_size_um = camera_pixel_size  # unbinned pixel size
        self.well_size_mm = WELL_SIZE_MM
        self.well_spacing_mm = WELL_SPACING_MM
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

        self.load_background_image(self.image_paths.get(sample, "images/4 slide carrier_1509x1010.png"))
        self.create_layers()
        self.update_display_properties(sample)
        # self.update_display()

    def init_ui(self, invertX):
        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setBackground("w")

        self.view = self.graphics_widget.addViewBox(invertX=not INVERTED_OBJECTIVE, invertY=True)
        self.view.setAspectLocked(True)

        self.grid = QVBoxLayout()
        self.grid.addWidget(self.graphics_widget)
        self.setLayout(self.grid)
        # Connect double-click handler
        self.view.scene().sigMouseClicked.connect(self.handle_mouse_click)

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
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera_sensor_pixel_size_um
        self.fov_size_mm = CAMERA_CONFIG.CROP_WIDTH_UNBINNED * pixel_size_um / 1000

    def redraw_fov(self):
        self.clear_overlay()
        self.update_fov_size()
        self.draw_current_fov(self.x_mm, self.y_mm)

    def update_wellplate_settings(
        self,
        sample_format,
        a1_x_mm,
        a1_y_mm,
        a1_x_pixel,
        a1_y_pixel,
        well_size_mm,
        well_spacing_mm,
        number_of_skip,
        rows,
        cols,
    ):
        if isinstance(sample_format, QVariant):
            sample_format = sample_format.value()

        if sample_format == "glass slide":
            if IS_HCS:
                sample = "4 glass slide"
            else:
                sample = "glass slide"
        else:
            sample = sample_format

        self.sample = sample
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = well_size_mm
        self.well_spacing_mm = well_spacing_mm
        self.number_of_skip = number_of_skip
        self.rows = rows
        self.cols = cols

        # Try to find the image for the wellplate
        image_path = self.image_paths.get(sample)
        if image_path is None or not os.path.exists(image_path):
            # Look for a custom wellplate image
            custom_image_path = os.path.join("images", self.sample + ".png")
            self._log.info(custom_image_path)
            if os.path.exists(custom_image_path):
                image_path = custom_image_path
            else:
                self._log.warning(f"Image not found for {sample}. Using default image.")
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

    def get_FOV_pixel_coordinates(self, x_mm, y_mm):
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
            current_FOV_top_left = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel - self.fov_size_mm / 2 / self.mm_per_pixel),
                round((self.origin_y_pixel + y_mm / self.mm_per_pixel) - self.fov_size_mm / 2 / self.mm_per_pixel),
            )
            current_FOV_bottom_right = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel + self.fov_size_mm / 2 / self.mm_per_pixel),
                round((self.origin_y_pixel + y_mm / self.mm_per_pixel) + self.fov_size_mm / 2 / self.mm_per_pixel),
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

    def register_fov_to_image(self, x_mm, y_mm):
        color = (252, 174, 30, 128)  # Yellow RGBA
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, color, self.box_line_thickness)
        self.scan_overlay_item.setImage(self.scan_overlay)

    def deregister_fov_to_image(self, x_mm, y_mm):
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(
            self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, (0, 0, 0, 0), self.box_line_thickness
        )
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

    def handle_mouse_click(self, evt):
        if not evt.double():
            return
        try:
            # Get mouse position in image coordinates (independent of zoom)
            mouse_point = self.background_item.mapFromScene(evt.scenePos())

            # Subtract origin offset before converting to mm
            x_mm = (mouse_point.x() - self.origin_x_pixel) * self.mm_per_pixel
            y_mm = (mouse_point.y() - self.origin_y_pixel) * self.mm_per_pixel

            self._log.debug(f"Got double click at (x_mm, y_mm) = {x_mm, y_mm}")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

        except Exception as e:
            print(f"Error processing navigation click: {e}")
            return


class ImageArrayDisplayWindow(QMainWindow):

    def __init__(self, window_title=""):
        super().__init__()
        self.setWindowTitle(window_title)
        self.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.widget = QWidget()

        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")

        self.graphics_widget_1 = pg.GraphicsLayoutWidget()
        self.graphics_widget_1.view = self.graphics_widget_1.addViewBox()
        self.graphics_widget_1.view.setAspectLocked(True)
        self.graphics_widget_1.img = pg.ImageItem(border="w")
        self.graphics_widget_1.view.addItem(self.graphics_widget_1.img)
        self.graphics_widget_1.view.invertY()

        self.graphics_widget_2 = pg.GraphicsLayoutWidget()
        self.graphics_widget_2.view = self.graphics_widget_2.addViewBox()
        self.graphics_widget_2.view.setAspectLocked(True)
        self.graphics_widget_2.img = pg.ImageItem(border="w")
        self.graphics_widget_2.view.addItem(self.graphics_widget_2.img)
        self.graphics_widget_2.view.invertY()

        self.graphics_widget_3 = pg.GraphicsLayoutWidget()
        self.graphics_widget_3.view = self.graphics_widget_3.addViewBox()
        self.graphics_widget_3.view.setAspectLocked(True)
        self.graphics_widget_3.img = pg.ImageItem(border="w")
        self.graphics_widget_3.view.addItem(self.graphics_widget_3.img)
        self.graphics_widget_3.view.invertY()

        self.graphics_widget_4 = pg.GraphicsLayoutWidget()
        self.graphics_widget_4.view = self.graphics_widget_4.addViewBox()
        self.graphics_widget_4.view.setAspectLocked(True)
        self.graphics_widget_4.img = pg.ImageItem(border="w")
        self.graphics_widget_4.view.addItem(self.graphics_widget_4.img)
        self.graphics_widget_4.view.invertY()
        ## Layout
        layout = QGridLayout()
        layout.addWidget(self.graphics_widget_1, 0, 0)
        layout.addWidget(self.graphics_widget_2, 0, 1)
        layout.addWidget(self.graphics_widget_3, 1, 0)
        layout.addWidget(self.graphics_widget_4, 1, 1)
        self.widget.setLayout(layout)
        self.setCentralWidget(self.widget)

        # set window size
        desktopWidget = QDesktopWidget()
        width = min(desktopWidget.height() * 0.9, 1000)  # @@@TO MOVE@@@#
        height = width
        self.setFixedSize(int(width), int(height))

    def display_image(self, image, illumination_source):
        if illumination_source < 11:
            self.graphics_widget_1.img.setImage(image, autoLevels=False)
        elif illumination_source == 11:
            self.graphics_widget_2.img.setImage(image, autoLevels=False)
        elif illumination_source == 12:
            self.graphics_widget_3.img.setImage(image, autoLevels=False)
        elif illumination_source == 13:
            self.graphics_widget_4.img.setImage(image, autoLevels=False)


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
