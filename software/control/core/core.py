# set QT_API environment variable
import os
import sys
import tempfile

import control._def
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from squid.abc import AbstractStage, AbstractCamera, CameraAcquisitionMode
import squid.logging

# qt libraries
os.environ["QT_API"] = "pyqt5"
import qtpy
import pyqtgraph as pg
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

# control
from control._def import *
from control.core.multi_point_worker import MultiPointWorker

import control.utils as utils
import control.utils_acquisition as utils_acquisition
import control.utils_channel as utils_channel
import control.utils_config as utils_config
import control.tracking as tracking
import control.serial_peripherals as serial_peripherals

try:
    from control.multipoint_custom_script_entry_v2 import *

    print("custom multipoint script found")
except:
    pass

from typing import List, Tuple, Optional, Dict, Any, Callable
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


class ObjectiveStore:
    def __init__(self, objectives_dict=OBJECTIVES, default_objective=DEFAULT_OBJECTIVE):
        self.objectives_dict = objectives_dict
        self.default_objective = default_objective
        self.current_objective = default_objective
        self.tube_lens_mm = TUBE_LENS_MM
        self.sensor_pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR]
        self.pixel_binning = 1
        self.pixel_size_um = self.calculate_pixel_size(self.current_objective)

    def get_pixel_size(self):
        return self.pixel_size_um

    def calculate_pixel_size(self, objective_name):
        objective = self.objectives_dict[objective_name]
        magnification = objective["magnification"]
        objective_tube_lens_mm = objective["tube_lens_f_mm"]
        pixel_size_um = self.sensor_pixel_size_um / (magnification / (objective_tube_lens_mm / self.tube_lens_mm))
        pixel_size_um *= self.pixel_binning
        return pixel_size_um

    def set_current_objective(self, objective_name):
        if objective_name in self.objectives_dict:
            self.current_objective = objective_name
            self.pixel_size_um = self.calculate_pixel_size(objective_name)
        else:
            raise ValueError(f"Objective {objective_name} not found in the store.")

    def get_current_objective_info(self):
        return self.objectives_dict[self.current_objective]


class StreamHandler(QObject):

    image_to_display = Signal(np.ndarray)
    packet_image_to_write = Signal(np.ndarray, int, float)
    packet_image_for_tracking = Signal(np.ndarray, int, float)
    signal_new_frame_received = Signal()

    def __init__(
        self,
        crop_width=Acquisition.CROP_WIDTH,
        crop_height=Acquisition.CROP_HEIGHT,
        display_resolution_scaling=1,
        accept_new_frame_fn: Callable[[], bool] = lambda: True,
    ):
        QObject.__init__(self)
        self.fps_display = 1
        self.fps_save = 1
        self.fps_track = 1
        self.timestamp_last_display = 0
        self.timestamp_last_save = 0
        self.timestamp_last_track = 0

        self.crop_width = crop_width
        self.crop_height = crop_height
        self.display_resolution_scaling = display_resolution_scaling

        self.save_image_flag = False
        self.handler_busy = False

        # for fps measurement
        self.timestamp_last = 0
        self.counter = 0
        self.fps_real = 0

        # Only accept new frames if this user defined function returns true
        self._accept_new_frames_fn = accept_new_frame_fn

    def start_recording(self):
        self.save_image_flag = True

    def stop_recording(self):
        self.save_image_flag = False

    def set_display_fps(self, fps):
        self.fps_display = fps

    def set_save_fps(self, fps):
        self.fps_save = fps

    def set_crop(self, crop_width, crop_height):
        self.crop_width = crop_width
        self.crop_height = crop_height

    def set_display_resolution_scaling(self, display_resolution_scaling):
        self.display_resolution_scaling = display_resolution_scaling / 100
        print(self.display_resolution_scaling)

    def on_new_frame(self, frame: squid.abc.CameraFrame):
        if not self._accept_new_frames_fn():
            return

        self.handler_busy = True
        self.signal_new_frame_received.emit()

        # measure real fps
        timestamp_now = round(time.time())
        if timestamp_now == self.timestamp_last:
            self.counter = self.counter + 1
        else:
            self.timestamp_last = timestamp_now
            self.fps_real = self.counter
            self.counter = 0
            if PRINT_CAMERA_FPS:
                print("real camera fps is " + str(self.fps_real))

        # crop image
        image_cropped = utils.crop_image(frame.frame, self.crop_width, self.crop_height)
        image_cropped = np.squeeze(image_cropped)

        # send image to display
        time_now = time.time()
        if time_now - self.timestamp_last_display >= 1 / self.fps_display:
            self.image_to_display.emit(
                utils.crop_image(
                    image_cropped,
                    round(self.crop_width * self.display_resolution_scaling),
                    round(self.crop_height * self.display_resolution_scaling),
                )
            )
            self.timestamp_last_display = time_now

        # send image to write
        if self.save_image_flag and time_now - self.timestamp_last_save >= 1 / self.fps_save:
            if frame.is_color():
                image_cropped = cv2.cvtColor(image_cropped, cv2.COLOR_RGB2BGR)
            self.packet_image_to_write.emit(image_cropped, frame.frame_id, frame.timestamp)
            self.timestamp_last_save = time_now

        self.handler_busy = False


class TrackingController(QObject):
    def __init__(self, camera: AbstractCamera, stage: AbstractStage, config_manager: ChannelConfigurationManager):
        QObject.__init__(self)
        self.camera = camera
        self.stage = stage
        self.config_manager = config_manager
        self.tracking_enabled = False

    def enable_tracking(self):
        self.tracking_enabled = True

    def disable_tracking(self):
        self.tracking_enabled = False

    def track_object(self, object_coordinates: Tuple[float, float]):
        if not self.tracking_enabled:
            return
        self.stage.move_to(object_coordinates[0], object_coordinates[1])


class ConfigurationManager:
    def __init__(self, channel_manager: ChannelConfigurationManager, laser_af_manager: LaserAFConfig, base_config_path: Path):
        self.channel_manager = channel_manager
        self.laser_af_manager = laser_af_manager
        self.base_config_path = base_config_path

    def load_configurations(self):
        self.channel_manager.set_profile_path(self.base_config_path)
        self.channel_manager.load_configurations(DEFAULT_OBJECTIVE)

    def save_configurations(self):
        self.channel_manager.save_configurations(DEFAULT_OBJECTIVE)

    def get_channel_configurations(self):
        return self.channel_manager.get_channel_configurations_for_objective(DEFAULT_OBJECTIVE)

    def update_channel_configuration(self, config_id: str, attr_name: str, value: Any):
        self.channel_manager.update_configuration(DEFAULT_OBJECTIVE, config_id, attr_name, value)

    def get_laser_af_settings(self):
        return self.laser_af_manager

    def update_laser_af_setting(self, setting_name: str, value: Any):
        setattr(self.laser_af_manager, setting_name, value)
