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
import scipy.ndimage


class ObjectiveStore:
    def __init__(self, objectives_dict=OBJECTIVES, default_objective=DEFAULT_OBJECTIVE):
        self.objectives_dict = objectives_dict
        self.default_objective = default_objective
        self.current_objective = default_objective
        objective = self.objectives_dict[self.current_objective]
        self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, TUBE_LENS_MM)

    def get_pixel_size_factor(self):
        return self.pixel_size_factor

    @staticmethod
    def calculate_pixel_size_factor(objective, tube_lens_mm):
        """pixel_size_um = sensor_pixel_size * binning_factor * lens_factor"""
        magnification = objective["magnification"]
        objective_tube_lens_mm = objective["tube_lens_f_mm"]
        lens_factor = objective_tube_lens_mm / magnification / tube_lens_mm
        return lens_factor

    def set_current_objective(self, objective_name):
        if objective_name in self.objectives_dict:
            self.current_objective = objective_name
            objective = self.objectives_dict[objective_name]
            self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, TUBE_LENS_MM)
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
        image = np.squeeze(frame.frame)

        # send image to display
        time_now = time.time()
        if time_now - self.timestamp_last_display >= 1 / self.fps_display:
            self.image_to_display.emit(
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
            self.packet_image_to_write.emit(image, frame.frame_id, frame.timestamp)
            self.timestamp_last_save = time_now

        self.handler_busy = False


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


class LiveController(QObject):
    def __init__(
        self,
        camera: AbstractCamera,
        microcontroller,
        illuminationController,
        parent=None,
        control_illumination=True,
        use_internal_timer_for_hardware_trigger=True,
        for_displacement_measurement=False,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.microscope = parent
        self.camera: AbstractCamera = camera
        self.microcontroller = microcontroller
        self.currentConfiguration = None
        self.trigger_mode = TriggerMode.SOFTWARE  # @@@ change to None
        self.is_live = False
        self.control_illumination = control_illumination
        self.illumination_on = False
        self.illuminationController = illuminationController
        self.use_internal_timer_for_hardware_trigger = (
            use_internal_timer_for_hardware_trigger  # use QTimer vs timer in the MCU
        )
        self.for_displacement_measurement = for_displacement_measurement

        self.fps_trigger = 1
        self.timer_trigger_interval = (1 / self.fps_trigger) * 1000

        self.timer_trigger = QTimer()
        self.timer_trigger.setInterval(int(self.timer_trigger_interval))
        self.timer_trigger.timeout.connect(self.trigger_acquisition)

        self.trigger_ID = -1

        self.fps_real = 0
        self.counter = 0
        self.timestamp_last = 0

        self.display_resolution_scaling = 1

        self.enable_channel_auto_filter_switching = True

        if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            # to do: add error handling
            self.led_array = serial_peripherals.SciMicroscopyLEDArray(
                SCIMICROSCOPY_LED_ARRAY_SN, SCIMICROSCOPY_LED_ARRAY_DISTANCE, SCIMICROSCOPY_LED_ARRAY_TURN_ON_DELAY
            )
            self.led_array.set_NA(SCIMICROSCOPY_LED_ARRAY_DEFAULT_NA)

    # illumination control
    def turn_on_illumination(self):
        if not "LED matrix" in self.currentConfiguration.name:
            self.illuminationController.turn_on_illumination(
                int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            )
        elif SUPPORT_SCIMICROSCOPY_LED_ARRAY and "LED matrix" in self.currentConfiguration.name:
            self.led_array.turn_on_illumination()
        # LED matrix
        else:
            self.microcontroller.turn_on_illumination()  # to wrap microcontroller in Squid_led_array
        self.illumination_on = True

    def turn_off_illumination(self):
        if not "LED matrix" in self.currentConfiguration.name:
            self.illuminationController.turn_off_illumination(
                int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            )
        elif SUPPORT_SCIMICROSCOPY_LED_ARRAY and "LED matrix" in self.currentConfiguration.name:
            self.led_array.turn_off_illumination()
        # LED matrix
        else:
            self.microcontroller.turn_off_illumination()  # to wrap microcontroller in Squid_led_array
        self.illumination_on = False

    def update_illumination(self):
        illumination_source = self.currentConfiguration.illumination_source
        intensity = self.currentConfiguration.illumination_intensity
        if illumination_source < 10:  # LED matrix
            if SUPPORT_SCIMICROSCOPY_LED_ARRAY:
                # set color
                if "BF LED matrix full_R" in self.currentConfiguration.name:
                    self.led_array.set_color((1, 0, 0))
                elif "BF LED matrix full_G" in self.currentConfiguration.name:
                    self.led_array.set_color((0, 1, 0))
                elif "BF LED matrix full_B" in self.currentConfiguration.name:
                    self.led_array.set_color((0, 0, 1))
                else:
                    self.led_array.set_color(SCIMICROSCOPY_LED_ARRAY_DEFAULT_COLOR)
                # set intensity
                self.led_array.set_brightness(intensity)
                # set mode
                if "BF LED matrix left half" in self.currentConfiguration.name:
                    self.led_array.set_illumination("dpc.l")
                if "BF LED matrix right half" in self.currentConfiguration.name:
                    self.led_array.set_illumination("dpc.r")
                if "BF LED matrix top half" in self.currentConfiguration.name:
                    self.led_array.set_illumination("dpc.t")
                if "BF LED matrix bottom half" in self.currentConfiguration.name:
                    self.led_array.set_illumination("dpc.b")
                if "BF LED matrix full" in self.currentConfiguration.name:
                    self.led_array.set_illumination("bf")
                if "DF LED matrix" in self.currentConfiguration.name:
                    self.led_array.set_illumination("df")
            else:
                if "BF LED matrix full_R" in self.currentConfiguration.name:
                    self.microcontroller.set_illumination_led_matrix(illumination_source, r=(intensity / 100), g=0, b=0)
                elif "BF LED matrix full_G" in self.currentConfiguration.name:
                    self.microcontroller.set_illumination_led_matrix(illumination_source, r=0, g=(intensity / 100), b=0)
                elif "BF LED matrix full_B" in self.currentConfiguration.name:
                    self.microcontroller.set_illumination_led_matrix(illumination_source, r=0, g=0, b=(intensity / 100))
                else:
                    self.microcontroller.set_illumination_led_matrix(
                        illumination_source,
                        r=(intensity / 100) * LED_MATRIX_R_FACTOR,
                        g=(intensity / 100) * LED_MATRIX_G_FACTOR,
                        b=(intensity / 100) * LED_MATRIX_B_FACTOR,
                    )
        else:
            # update illumination
            wavelength = int(utils_channel.extract_wavelength_from_config_name(self.currentConfiguration.name))
            self.illuminationController.set_intensity(wavelength, intensity)
            if ENABLE_NL5 and NL5_USE_DOUT and "Fluorescence" in self.currentConfiguration.name:
                self.microscope.nl5.set_active_channel(NL5_WAVENLENGTH_MAP[wavelength])
                if NL5_USE_AOUT:
                    self.microscope.nl5.set_laser_power(NL5_WAVENLENGTH_MAP[wavelength], int(intensity))
                if ENABLE_CELLX:
                    self.microscope.cellx.set_laser_power(NL5_WAVENLENGTH_MAP[wavelength], int(intensity))

        # set emission filter position
        if ENABLE_SPINNING_DISK_CONFOCAL:
            try:
                self.microscope.xlight.set_emission_filter(
                    XLIGHT_EMISSION_FILTER_MAPPING[illumination_source],
                    extraction=False,
                    validate=XLIGHT_VALIDATE_WHEEL_POS,
                )
            except Exception as e:
                print("not setting emission filter position due to " + str(e))

        if USE_ZABER_EMISSION_FILTER_WHEEL and self.enable_channel_auto_filter_switching:
            try:
                if (
                    self.currentConfiguration.emission_filter_position
                    != self.microscope.emission_filter_wheel.current_index
                ):
                    if ZABER_EMISSION_FILTER_WHEEL_BLOCKING_CALL:
                        self.microscope.emission_filter_wheel.set_emission_filter(
                            self.currentConfiguration.emission_filter_position, blocking=True
                        )
                    else:
                        self.microscope.emission_filter_wheel.set_emission_filter(
                            self.currentConfiguration.emission_filter_position, blocking=False
                        )
                        if self.trigger_mode == TriggerMode.SOFTWARE:
                            time.sleep(ZABER_EMISSION_FILTER_WHEEL_DELAY_MS / 1000)
                        else:
                            time.sleep(
                                max(
                                    0, ZABER_EMISSION_FILTER_WHEEL_DELAY_MS / 1000 - self.camera.get_strobe_time() / 1e3
                                )
                            )
            except Exception as e:
                print("not setting emission filter position due to " + str(e))

        if (
            USE_OPTOSPIN_EMISSION_FILTER_WHEEL
            and self.enable_channel_auto_filter_switching
            and OPTOSPIN_EMISSION_FILTER_WHEEL_TTL_TRIGGER == False
        ):
            try:
                if (
                    self.currentConfiguration.emission_filter_position
                    != self.microscope.emission_filter_wheel.current_index
                ):
                    self.microscope.emission_filter_wheel.set_emission_filter(
                        self.currentConfiguration.emission_filter_position
                    )
                    if self.trigger_mode == TriggerMode.SOFTWARE:
                        time.sleep(OPTOSPIN_EMISSION_FILTER_WHEEL_DELAY_MS / 1000)
                    elif self.trigger_mode == TriggerMode.HARDWARE:
                        time.sleep(
                            max(0, OPTOSPIN_EMISSION_FILTER_WHEEL_DELAY_MS / 1000 - self.camera.get_strobe_time() / 1e3)
                        )
            except Exception as e:
                print("not setting emission filter position due to " + str(e))

        if USE_SQUID_FILTERWHEEL and self.enable_channel_auto_filter_switching:
            try:
                self.microscope.squid_filter_wheel.set_emission(self.currentConfiguration.emission_filter_position)
            except Exception as e:
                print("not setting emission filter position due to " + str(e))

    def start_live(self):
        self.is_live = True
        self.camera.start_streaming()
        if self.trigger_mode == TriggerMode.SOFTWARE or (
            self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
        ):
            self.camera.enable_callbacks(True)  # in case it's disabled e.g. by the laser AF controller
            self._start_triggerred_acquisition()
        # if controlling the laser displacement measurement camera
        if self.for_displacement_measurement:
            self.microcontroller.set_pin_level(MCU_PINS.AF_LASER, 1)

    def stop_live(self):
        if self.is_live:
            self.is_live = False
            if self.trigger_mode == TriggerMode.SOFTWARE:
                self._stop_triggerred_acquisition()
            if self.trigger_mode == TriggerMode.CONTINUOUS:
                self.camera.stop_streaming()
            if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            if self.control_illumination:
                self.turn_off_illumination()
            # if controlling the laser displacement measurement camera
            if self.for_displacement_measurement:
                self.microcontroller.set_pin_level(MCU_PINS.AF_LASER, 0)

    # software trigger related
    def trigger_acquisition(self):
        if not self.camera.get_ready_for_trigger():
            # TODO(imo): Before, send_trigger would pass silently for this case.  Now
            # we do the same here.  Should this warn?  I didn't add a warning because it seems like
            # we over-trigger as standard practice (eg: we trigger at our exposure time frequency, but
            # the cameras can't give us images that fast so we essentially always have at least 1 skipped trigger)
            self._log.debug("Not ready for trigger, skipping.")
            return
        if self.trigger_mode == TriggerMode.SOFTWARE and self.control_illumination:
            if not self.illumination_on:
                self.turn_on_illumination()

        self.trigger_ID = self.trigger_ID + 1

        self.camera.send_trigger(self.camera.get_exposure_time())

        if self.trigger_mode == TriggerMode.SOFTWARE:
            if self.control_illumination and self.illumination_on == False:
                self.turn_on_illumination()

    def _start_triggerred_acquisition(self):
        if not self.timer_trigger.isActive():
            self.timer_trigger.start()

    def _set_trigger_fps(self, fps_trigger):
        self.fps_trigger = fps_trigger
        self.timer_trigger_interval = (1 / self.fps_trigger) * 1000
        self.timer_trigger.setInterval(int(self.timer_trigger_interval))

    def _stop_triggerred_acquisition(self):
        self.timer_trigger.stop()

    # trigger mode and settings
    def set_trigger_mode(self, mode):
        if mode == TriggerMode.SOFTWARE:
            if self.is_live and (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
            if self.is_live:
                self._start_triggerred_acquisition()
        if mode == TriggerMode.HARDWARE:
            if self.trigger_mode == TriggerMode.SOFTWARE and self.is_live:
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
            self.camera.set_exposure_time(self.currentConfiguration.exposure_time)

            if self.is_live and self.use_internal_timer_for_hardware_trigger:
                self._start_triggerred_acquisition()
        if mode == TriggerMode.CONTINUOUS:
            if (self.trigger_mode == TriggerMode.SOFTWARE) or (
                self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
            ):
                self._stop_triggerred_acquisition()
            self.camera.set_acquisition_mode(CameraAcquisitionMode.CONTINUOUS)
        self.trigger_mode = mode

    def set_trigger_fps(self, fps):
        if (self.trigger_mode == TriggerMode.SOFTWARE) or (
            self.trigger_mode == TriggerMode.HARDWARE and self.use_internal_timer_for_hardware_trigger
        ):
            self._set_trigger_fps(fps)

    # set microscope mode
    # @@@ to do: change softwareTriggerGenerator to TriggerGeneratror
    def set_microscope_mode(self, configuration):

        self.currentConfiguration = configuration
        self._log.info("setting microscope mode to " + self.currentConfiguration.name)

        # temporarily stop live while changing mode
        if self.is_live is True:
            self.timer_trigger.stop()
            if self.control_illumination:
                self.turn_off_illumination()

        # set camera exposure time and analog gain
        self.camera.set_exposure_time(self.currentConfiguration.exposure_time)
        try:
            self.camera.set_analog_gain(self.currentConfiguration.analog_gain)
        except NotImplementedError:
            pass

        # set illumination
        if self.control_illumination:
            self.update_illumination()

        # restart live
        if self.is_live is True:
            if self.control_illumination:
                self.turn_on_illumination()
            self.timer_trigger.start()
        self._log.info("Done setting microscope mode.")

    def get_trigger_mode(self):
        return self.trigger_mode

    # slot
    def on_new_frame(self):
        if self.fps_trigger <= 5:
            if self.control_illumination and self.illumination_on == True:
                self.turn_off_illumination()

    def set_display_resolution_scaling(self, display_resolution_scaling):
        self.display_resolution_scaling = display_resolution_scaling / 100


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


class AutofocusWorker(QObject):

    finished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(self, autofocusController):
        QObject.__init__(self)
        self.autofocusController = autofocusController

        self.camera: AbstractCamera = self.autofocusController.camera
        self.microcontroller = self.autofocusController.microcontroller
        self.stage = self.autofocusController.stage
        self.liveController = self.autofocusController.liveController

        self.N = self.autofocusController.N
        self.deltaZ = self.autofocusController.deltaZ

        self.crop_width = self.autofocusController.crop_width
        self.crop_height = self.autofocusController.crop_height

    def run(self):
        self.run_autofocus()
        self.finished.emit()

    def wait_till_operation_is_completed(self):
        while self.microcontroller.is_busy():
            time.sleep(SLEEP_TIME_S)

    def run_autofocus(self):
        # @@@ to add: increase gain, decrease exposure time
        # @@@ can move the execution into a thread - done 08/21/2021
        focus_measure_vs_z = [0] * self.N
        focus_measure_max = 0

        z_af_offset = self.deltaZ * round(self.N / 2)

        self.stage.move_z(-z_af_offset)

        steps_moved = 0
        for i in range(self.N):
            self.stage.move_z(self.deltaZ)
            steps_moved = steps_moved + 1
            # trigger acquisition (including turning on the illumination) and read frame
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self.liveController.turn_on_illumination()
                self.wait_till_operation_is_completed()
                self.camera.send_trigger()
                image = self.camera.read_frame()
            elif self.liveController.trigger_mode == TriggerMode.HARDWARE:
                if "Fluorescence" in self.liveController.currentConfiguration.name and ENABLE_NL5 and NL5_USE_DOUT:
                    self.microscope.nl5.start_acquisition()
                    # TODO(imo): This used to use the "reset_image_ready_flag=False" arg, but oinly the toupcam camera implementation had the
                    #  "reset_image_ready_flag" arg, so this is broken for all other cameras.
                    image = self.camera.read_frame()
                else:
                    self.microcontroller.send_hardware_trigger(
                        control_illumination=True, illumination_on_time_us=self.camera.get_exposure_time() * 1000
                    )
                    image = self.camera.read_frame()
            if image is None:
                continue
            # tunr of the illumination if using software trigger
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self.liveController.turn_off_illumination()

            image = utils.crop_image(image, self.crop_width, self.crop_height)
            self.image_to_display.emit(image)

            QApplication.processEvents()
            timestamp_0 = time.time()
            focus_measure = utils.calculate_focus_measure(image, FOCUS_MEASURE_OPERATOR)
            timestamp_1 = time.time()
            print("             calculating focus measure took " + str(timestamp_1 - timestamp_0) + " second")
            focus_measure_vs_z[i] = focus_measure
            print(i, focus_measure)
            focus_measure_max = max(focus_measure, focus_measure_max)
            if focus_measure < focus_measure_max * AF.STOP_THRESHOLD:
                break

        QApplication.processEvents()

        # maneuver for achiving uniform step size and repeatability when using open-loop control
        self.stage.move_z(-steps_moved * self.deltaZ)
        # determine the in-focus position
        idx_in_focus = focus_measure_vs_z.index(max(focus_measure_vs_z))
        self.stage.move_z((idx_in_focus + 1) * self.deltaZ)

        QApplication.processEvents()

        # move to the calculated in-focus position
        if idx_in_focus == 0:
            print("moved to the bottom end of the AF range")
        if idx_in_focus == self.N - 1:
            print("moved to the top end of the AF range")


class AutoFocusController(QObject):

    z_pos = Signal(float)
    autofocusFinished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(self, camera: AbstractCamera, stage: AbstractStage, liveController, microcontroller: Microcontroller):
        QObject.__init__(self)
        self.camera: AbstractCamera = camera
        self.stage = stage
        self.microcontroller = microcontroller
        self.liveController = liveController
        self.N = None
        self.deltaZ = None
        self.crop_width = AF.CROP_WIDTH
        self.crop_height = AF.CROP_HEIGHT
        self.autofocus_in_progress = False
        self.focus_map_coords = []
        self.use_focus_map = False

    def set_N(self, N):
        self.N = N

    def set_deltaZ(self, delta_z_um):
        self.deltaZ = delta_z_um / 1000

    def set_crop(self, crop_width, crop_height):
        self.crop_width = crop_width
        self.crop_height = crop_height

    def autofocus(self, focus_map_override=False):
        # TODO(imo): We used to have the joystick button wired up to autofocus, but took it out in a refactor.  It needs to be restored.
        if self.use_focus_map and (not focus_map_override):
            self.autofocus_in_progress = True

            self.stage.wait_for_idle(1.0)
            pos = self.stage.get_pos()

            # z here is in mm because that's how the navigation controller stores it
            target_z = utils.interpolate_plane(*self.focus_map_coords[:3], (pos.x_mm, pos.y_mm))
            print(f"Interpolated target z as {target_z} mm from focus map, moving there.")
            self.stage.move_z_to(target_z)
            self.autofocus_in_progress = False
            self.autofocusFinished.emit()
            return
        # stop live
        if self.liveController.is_live:
            self.was_live_before_autofocus = True
            self.liveController.stop_live()
        else:
            self.was_live_before_autofocus = False

        # temporarily disable call back -> image does not go through streamHandler
        if self.camera.get_callbacks_enabled():
            self.callback_was_enabled_before_autofocus = True
            self.camera.enable_callbacks(False)
        else:
            self.callback_was_enabled_before_autofocus = False

        self.autofocus_in_progress = True

        # create a QThread object
        try:
            if self.thread.isRunning():
                print("*** autofocus thread is still running ***")
                self.thread.terminate()
                self.thread.wait()
                print("*** autofocus threaded manually stopped ***")
        except:
            pass
        self.thread = QThread(parent=self)
        # create a worker object
        self.autofocusWorker = AutofocusWorker(self)
        # move the worker to the thread
        self.autofocusWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.autofocusWorker.run)
        self.autofocusWorker.finished.connect(self._on_autofocus_completed)
        self.autofocusWorker.finished.connect(self.autofocusWorker.deleteLater)
        self.autofocusWorker.finished.connect(self.thread.quit)
        self.autofocusWorker.image_to_display.connect(self.slot_image_to_display)
        self.thread.finished.connect(self.thread.quit)
        # start the thread
        self.thread.start()

    def _on_autofocus_completed(self):
        # re-enable callback
        if self.callback_was_enabled_before_autofocus:
            self.camera.enable_callbacks(True)

        # re-enable live if it's previously on
        if self.was_live_before_autofocus:
            self.liveController.start_live()

        # emit the autofocus finished signal to enable the UI
        self.autofocusFinished.emit()
        QApplication.processEvents()
        print("autofocus finished")

        # update the state
        self.autofocus_in_progress = False

    def slot_image_to_display(self, image):
        self.image_to_display.emit(image)

    def wait_till_autofocus_has_completed(self):
        while self.autofocus_in_progress:
            QApplication.processEvents()
            time.sleep(0.005)
        print("autofocus wait has completed, exit wait")

    def set_focus_map_use(self, enable):
        if not enable:
            print("Disabling focus map.")
            self.use_focus_map = False
            return
        if len(self.focus_map_coords) < 3:
            print("Not enough coordinates (less than 3) for focus map generation, disabling focus map.")
            self.use_focus_map = False
            return
        x1, y1, _ = self.focus_map_coords[0]
        x2, y2, _ = self.focus_map_coords[1]
        x3, y3, _ = self.focus_map_coords[2]

        detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if detT == 0:
            print("Your 3 x-y coordinates are linear, cannot use to interpolate, disabling focus map.")
            self.use_focus_map = False
            return

        if enable:
            print("Enabling focus map.")
            self.use_focus_map = True

    def clear_focus_map(self):
        self.focus_map_coords = []
        self.set_focus_map_use(False)

    def gen_focus_map(self, coord1, coord2, coord3):
        """
        Navigate to 3 coordinates and get your focus-map coordinates
        by autofocusing there and saving the z-values.
        :param coord1-3: Tuples of (x,y) values, coordinates in mm.
        :raise: ValueError if coordinates are all on the same line
        """
        x1, y1 = coord1
        x2, y2 = coord2
        x3, y3 = coord3
        detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if detT == 0:
            raise ValueError("Your 3 x-y coordinates are linear")

        self.focus_map_coords = []

        for coord in [coord1, coord2, coord3]:
            print(f"Navigating to coordinates ({coord[0]},{coord[1]}) to sample for focus map")
            self.stage.move_x_to(coord[0])
            self.stage.move_y_to(coord[1])

            print("Autofocusing")
            self.autofocus(True)
            self.wait_till_autofocus_has_completed()
            pos = self.stage.get_pos()

            print(f"Adding coordinates ({pos.x_mm},{pos.y_mm},{pos.z_mm}) to focus map")
            self.focus_map_coords.append((pos.x_mm, pos.y_mm, pos.z_mm))

        print("Generated focus map.")

    def add_current_coords_to_focus_map(self):
        if len(self.focus_map_coords) >= 3:
            print("Replacing last coordinate on focus map.")
        self.stage.wait_for_idle(timeout_s=0.5)
        print("Autofocusing")
        self.autofocus(True)
        self.wait_till_autofocus_has_completed()
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm
        z = pos.z_mm
        if len(self.focus_map_coords) >= 2:
            x1, y1, _ = self.focus_map_coords[0]
            x2, y2, _ = self.focus_map_coords[1]
            x3 = x
            y3 = y

            detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            if detT == 0:
                raise ValueError(
                    "Your 3 x-y coordinates are linear. Navigate to a different coordinate or clear and try again."
                )
        if len(self.focus_map_coords) >= 3:
            self.focus_map_coords.pop()
        self.focus_map_coords.append((x, y, z))
        print(f"Added triple ({x},{y},{z}) to focus map")


class ConfigType(Enum):
    CHANNEL = "channel"
    CONFOCAL = "confocal"
    WIDEFIELD = "widefield"


class ChannelConfigurationManager:
    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_root = None
        self.all_configs: Dict[ConfigType, Dict[str, ChannelConfig]] = {
            ConfigType.CHANNEL: {},
            ConfigType.CONFOCAL: {},
            ConfigType.WIDEFIELD: {},
        }
        self.active_config_type = ConfigType.CHANNEL if not ENABLE_SPINNING_DISK_CONFOCAL else ConfigType.CONFOCAL

    def set_profile_path(self, profile_path: Path) -> None:
        """Set the root path for configurations"""
        self.config_root = profile_path

    def _load_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Load XML configuration for a specific config type, generating default if needed"""
        config_file = self.config_root / objective / f"{config_type.value}_configurations.xml"

        if not config_file.exists():
            utils_config.generate_default_configuration(str(config_file))

        xml_content = config_file.read_bytes()
        self.all_configs[config_type][objective] = ChannelConfig.from_xml(xml_content)

    def load_configurations(self, objective: str) -> None:
        """Load available configurations for an objective"""
        if ENABLE_SPINNING_DISK_CONFOCAL:
            # Load both confocal and widefield configurations
            self._load_xml_config(objective, ConfigType.CONFOCAL)
            self._load_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            # Load only channel configurations
            self._load_xml_config(objective, ConfigType.CHANNEL)

    def _save_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Save XML configuration for a specific config type"""
        if objective not in self.all_configs[config_type]:
            return

        config = self.all_configs[config_type][objective]
        save_path = self.config_root / objective / f"{config_type.value}_configurations.xml"

        if not save_path.parent.exists():
            save_path.parent.mkdir(parents=True)

        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        save_path.write_bytes(xml_str)

    def save_configurations(self, objective: str) -> None:
        """Save configurations based on spinning disk configuration"""
        if ENABLE_SPINNING_DISK_CONFOCAL:
            # Save both confocal and widefield configurations
            self._save_xml_config(objective, ConfigType.CONFOCAL)
            self._save_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            # Save only channel configurations
            self._save_xml_config(objective, ConfigType.CHANNEL)

    def save_current_configuration_to_path(self, objective: str, path: Path) -> None:
        """Only used in TrackingController. Might be temporary."""
        config = self.all_configs[self.active_config_type][objective]
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        path.write_bytes(xml_str)

    def get_configurations(self, objective: str) -> List[ChannelMode]:
        """Get channel modes for current active type"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            return []
        return config.modes

    def update_configuration(self, objective: str, config_id: str, attr_name: str, value: Any) -> None:
        """Update a specific configuration in current active type"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            self._log.error(f"Objective {objective} not found")
            return

        for mode in config.modes:
            if mode.id == config_id:
                setattr(mode, utils_config.get_attr_name(attr_name), value)
                break

        self.save_configurations(objective)

    def write_configuration_selected(
        self, objective: str, selected_configurations: List[ChannelMode], filename: str
    ) -> None:
        """Write selected configurations to a file"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            raise ValueError(f"Objective {objective} not found")

        # Update selected status
        for mode in config.modes:
            mode.selected = any(conf.id == mode.id for conf in selected_configurations)

        # Save to specified file
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        filename = Path(filename)
        filename.write_bytes(xml_str)

        # Reset selected status
        for mode in config.modes:
            mode.selected = False
        self.save_configurations(objective)

    def get_channel_configurations_for_objective(self, objective: str) -> List[ChannelMode]:
        """Get Configuration objects for current active type (alias for get_configurations)"""
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(self, objective: str, name: str) -> ChannelMode:
        """Get Configuration object by name"""
        return next((mode for mode in self.get_configurations(objective) if mode.name == name), None)

    def toggle_confocal_widefield(self, confocal: bool) -> None:
        """Toggle between confocal and widefield configurations"""
        self.active_config_type = ConfigType.CONFOCAL if confocal else ConfigType.WIDEFIELD


class ScanCoordinates(QObject):

    signal_scan_coordinates_updated = Signal()

    def __init__(self, objectiveStore, navigationViewer, stage: AbstractStage):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        # Wellplate settings
        self.objectiveStore = objectiveStore
        self.navigationViewer = navigationViewer
        self.stage = stage
        self.well_selector = None
        self.acquisition_pattern = ACQUISITION_PATTERN
        self.fov_pattern = FOV_PATTERN
        self.format = WELLPLATE_FORMAT
        self.a1_x_mm = A1_X_MM
        self.a1_y_mm = A1_Y_MM
        self.wellplate_offset_x_mm = WELLPLATE_OFFSET_X_mm
        self.wellplate_offset_y_mm = WELLPLATE_OFFSET_Y_mm
        self.well_spacing_mm = WELL_SPACING_MM
        self.well_size_mm = WELL_SIZE_MM
        self.a1_x_pixel = None
        self.a1_y_pixel = None
        self.number_of_skip = None

        # Centralized region management
        self.region_centers = {}  # {region_id: [x, y, z]}
        self.region_shapes = {}  # {region_id: "Square"}
        self.region_fov_coordinates = {}  # {region_id: [(x,y,z), ...]}

    def add_well_selector(self, well_selector):
        self.well_selector = well_selector

    def update_wellplate_settings(
        self, format_, a1_x_mm, a1_y_mm, a1_x_pixel, a1_y_pixel, size_mm, spacing_mm, number_of_skip
    ):
        self.format = format_
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = size_mm
        self.well_spacing_mm = spacing_mm
        self.number_of_skip = number_of_skip

    def _index_to_row(self, index):
        index += 1
        row = ""
        while index > 0:
            index -= 1
            row = chr(index % 26 + ord("A")) + row
            index //= 26
        return row

    def get_selected_wells(self):
        # get selected wells from the widget
        self._log.info("getting selected wells for acquisition")
        if not self.well_selector or self.format == "glass slide":
            return None

        selected_wells = np.array(self.well_selector.get_selected_cells())
        well_centers = {}

        # if no well selected
        if len(selected_wells) == 0:
            return well_centers
        # populate the coordinates
        rows = np.unique(selected_wells[:, 0])
        _increasing = True
        for row in rows:
            items = selected_wells[selected_wells[:, 0] == row]
            columns = items[:, 1]
            columns = np.sort(columns)
            if _increasing == False:
                columns = np.flip(columns)
            for column in columns:
                x_mm = self.a1_x_mm + (column * self.well_spacing_mm) + self.wellplate_offset_x_mm
                y_mm = self.a1_y_mm + (row * self.well_spacing_mm) + self.wellplate_offset_y_mm
                well_id = self._index_to_row(row) + str(column + 1)
                well_centers[well_id] = (x_mm, y_mm)
            _increasing = not _increasing
        return well_centers

    def set_live_scan_coordinates(self, x_mm, y_mm, scan_size_mm, overlap_percent, shape):
        if shape != "Manual" and self.format == "glass slide":
            if self.region_centers:
                self.clear_regions()
            self.add_region("current", x_mm, y_mm, scan_size_mm, overlap_percent, shape)

    def set_well_coordinates(self, scan_size_mm, overlap_percent, shape):
        new_region_centers = self.get_selected_wells()

        if self.format == "glass slide":
            pos = self.stage.get_pos()
            self.set_live_scan_coordinates(pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape)

        elif bool(new_region_centers):
            # Remove regions that are no longer selected
            for well_id in list(self.region_centers.keys()):
                if well_id not in new_region_centers.keys():
                    self.remove_region(well_id)

            # Add regions for selected wells
            for well_id, (x, y) in new_region_centers.items():
                if well_id not in self.region_centers:
                    self.add_region(well_id, x, y, scan_size_mm, overlap_percent, shape)
        else:
            self.clear_regions()

    def set_manual_coordinates(self, manual_shapes, overlap_percent):
        self.clear_regions()
        if manual_shapes is not None:
            # Handle manual ROIs
            manual_region_added = False
            for i, shape_coords in enumerate(manual_shapes):
                scan_coordinates = self.add_manual_region(shape_coords, overlap_percent)
                if scan_coordinates:
                    if len(manual_shapes) <= 1:
                        region_name = f"manual"
                    else:
                        region_name = f"manual{i}"
                    center = np.mean(shape_coords, axis=0)
                    self.region_centers[region_name] = [center[0], center[1]]
                    self.region_shapes[region_name] = "Manual"
                    self.region_fov_coordinates[region_name] = scan_coordinates
                    manual_region_added = True
                    self._log.info(f"Added Manual Region: {region_name}")
            if manual_region_added:
                self.signal_scan_coordinates_updated.emit()
        else:
            self._log.info("No Manual ROI found")

    def add_region(self, well_id, center_x, center_y, scan_size_mm, overlap_percent=10, shape="Square"):
        """add region based on user inputs"""
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.navigationViewer.camera_sensor_pixel_size_um
        # TODO: In the future software cropping size may be changed when program is running,
        # so we may want to use the crop_width from the camera object here.
        fov_size_mm = pixel_size_um * CAMERA_CONFIG.CROP_WIDTH_UNBINNED / 1000
        step_size_mm = fov_size_mm * (1 - overlap_percent / 100)
        scan_coordinates = []

        if shape == "Rectangle":
            # Use scan_size_mm as height, width is 0.6 * height
            height_mm = scan_size_mm
            width_mm = scan_size_mm * 0.6

            # Calculate steps for height and width separately
            steps_height = math.floor(height_mm / step_size_mm)
            steps_width = math.floor(width_mm / step_size_mm)

            # Calculate actual dimensions
            actual_scan_height_mm = (steps_height - 1) * step_size_mm + fov_size_mm
            actual_scan_width_mm = (steps_width - 1) * step_size_mm + fov_size_mm

            steps_height = max(1, steps_height)
            steps_width = max(1, steps_width)

            half_steps_height = (steps_height - 1) / 2
            half_steps_width = (steps_width - 1) / 2

            for i in range(steps_height):
                row = []
                y = center_y + (i - half_steps_height) * step_size_mm
                for j in range(steps_width):
                    x = center_x + (j - half_steps_width) * step_size_mm
                    if self.validate_coordinates(x, y):
                        row.append((x, y))
                        self.navigationViewer.register_fov_to_image(x, y)
                if self.fov_pattern == "S-Pattern" and i % 2 == 1:
                    row.reverse()
                scan_coordinates.extend(row)
        else:
            steps = math.floor(scan_size_mm / step_size_mm)
            if shape == "Circle":
                tile_diagonal = math.sqrt(2) * fov_size_mm
                if steps % 2 == 1:  # for odd steps
                    actual_scan_size_mm = (steps - 1) * step_size_mm + tile_diagonal
                else:  # for even steps
                    actual_scan_size_mm = math.sqrt(
                        ((steps - 1) * step_size_mm + fov_size_mm) ** 2 + (step_size_mm + fov_size_mm) ** 2
                    )

                if actual_scan_size_mm > scan_size_mm:
                    actual_scan_size_mm -= step_size_mm
                    steps -= 1
            else:
                actual_scan_size_mm = (steps - 1) * step_size_mm + fov_size_mm

            steps = max(1, steps)  # Ensure at least one step
            # print("steps:", steps)
            # print("scan size mm:", scan_size_mm)
            # print("actual scan size mm:", actual_scan_size_mm)
            half_steps = (steps - 1) / 2
            radius_squared = (scan_size_mm / 2) ** 2
            fov_size_mm_half = fov_size_mm / 2

            for i in range(steps):
                row = []
                y = center_y + (i - half_steps) * step_size_mm
                for j in range(steps):
                    x = center_x + (j - half_steps) * step_size_mm
                    if (
                        shape == "Square"
                        or shape == "Rectangle"
                        or (
                            shape == "Circle"
                            and self._is_in_circle(x, y, center_x, center_y, radius_squared, fov_size_mm_half)
                        )
                    ):
                        if self.validate_coordinates(x, y):
                            row.append((x, y))
                            self.navigationViewer.register_fov_to_image(x, y)

                if self.fov_pattern == "S-Pattern" and i % 2 == 1:
                    row.reverse()
                scan_coordinates.extend(row)

        if not scan_coordinates and shape == "Circle":
            if self.validate_coordinates(center_x, center_y):
                scan_coordinates.append((center_x, center_y))
                self.navigationViewer.register_fov_to_image(center_x, center_y)

        self.region_shapes[well_id] = shape
        self.region_centers[well_id] = [float(center_x), float(center_y), float(self.stage.get_pos().z_mm)]
        self.region_fov_coordinates[well_id] = scan_coordinates
        self.signal_scan_coordinates_updated.emit()
        self._log.info(f"Added Region: {well_id}")

    def remove_region(self, well_id):
        if well_id in self.region_centers:
            del self.region_centers[well_id]

            if well_id in self.region_shapes:
                del self.region_shapes[well_id]

            if well_id in self.region_fov_coordinates:
                region_scan_coordinates = self.region_fov_coordinates.pop(well_id)
                for coord in region_scan_coordinates:
                    self.navigationViewer.deregister_fov_to_image(coord[0], coord[1])

            self._log.info(f"Removed Region: {well_id}")
            self.signal_scan_coordinates_updated.emit()

    def clear_regions(self):
        self.region_centers.clear()
        self.region_shapes.clear()
        self.region_fov_coordinates.clear()
        self.navigationViewer.clear_overlay()
        self.signal_scan_coordinates_updated.emit()
        self._log.info("Cleared All Regions")

    def add_flexible_region(self, region_id, center_x, center_y, center_z, Nx, Ny, overlap_percent=10):
        """Convert grid parameters NX, NY to FOV coordinates based on overlap"""
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.navigationViewer.camera_sensor_pixel_size_um
        fov_size_mm = pixel_size_um * CAMERA_CONFIG.CROP_WIDTH_UNBINNED / 1000
        step_size_mm = fov_size_mm * (1 - overlap_percent / 100)

        # Calculate total grid size
        grid_width_mm = (Nx - 1) * step_size_mm
        grid_height_mm = (Ny - 1) * step_size_mm

        scan_coordinates = []
        for i in range(Ny):
            row = []
            y = center_y - grid_height_mm / 2 + i * step_size_mm
            for j in range(Nx):
                x = center_x - grid_width_mm / 2 + j * step_size_mm
                if self.validate_coordinates(x, y):
                    row.append((x, y))
                    self.navigationViewer.register_fov_to_image(x, y)

            if self.fov_pattern == "S-Pattern" and i % 2 == 1:  # reverse even rows
                row.reverse()
            scan_coordinates.extend(row)

        # Region coordinates are already centered since center_x, center_y is grid center
        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            self.signal_scan_coordinates_updated.emit()
        else:
            self._log.info(f"Region Out of Bounds: {region_id}")

    def add_flexible_region_with_step_size(self, region_id, center_x, center_y, center_z, Nx, Ny, dx, dy):
        """Convert grid parameters NX, NY to FOV coordinates based on dx, dy"""
        grid_width_mm = (Nx - 1) * dx
        grid_height_mm = (Ny - 1) * dy

        # Pre-calculate step sizes and ranges
        x_steps = [center_x - grid_width_mm / 2 + j * dx for j in range(Nx)]
        y_steps = [center_y - grid_height_mm / 2 + i * dy for i in range(Ny)]

        scan_coordinates = []
        for i, y in enumerate(y_steps):
            row = []
            x_range = x_steps if i % 2 == 0 else reversed(x_steps)
            for x in x_range:
                if self.validate_coordinates(x, y):
                    row.append((x, y))
                    self.navigationViewer.register_fov_to_image(x, y)
            scan_coordinates.extend(row)

        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            self.signal_scan_coordinates_updated.emit()
        else:
            print(f"Region Out of Bounds: {region_id}")

    def add_manual_region(self, shape_coords, overlap_percent):
        """Add region from manually drawn polygon shape"""
        if shape_coords is None or len(shape_coords) < 3:
            self._log.error("Invalid manual ROI data")
            return []

        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.navigationViewer.camera_sensor_pixel_size_um
        fov_size_mm = pixel_size_um * CAMERA_CONFIG.CROP_WIDTH_UNBINNED / 1000
        step_size_mm = fov_size_mm * (1 - overlap_percent / 100)

        # Ensure shape_coords is a numpy array
        shape_coords = np.array(shape_coords)
        if shape_coords.ndim == 1:
            shape_coords = shape_coords.reshape(-1, 2)
        elif shape_coords.ndim > 2:
            self._log.error(f"Unexpected shape of manual_shape: {shape_coords.shape}")
            return []

        # Calculate bounding box
        x_min, y_min = np.min(shape_coords, axis=0)
        x_max, y_max = np.max(shape_coords, axis=0)

        # Create a grid of points within the bounding box
        x_range = np.arange(x_min, x_max + step_size_mm, step_size_mm)
        y_range = np.arange(y_min, y_max + step_size_mm, step_size_mm)
        xx, yy = np.meshgrid(x_range, y_range)
        grid_points = np.column_stack((xx.ravel(), yy.ravel()))

        # # Use Delaunay triangulation for efficient point-in-polygon test
        # # hull = Delaunay(shape_coords)
        # # mask = hull.find_simplex(grid_points) >= 0
        # # or
        # # Use Ray Casting for point-in-polygon test
        # mask = np.array([self._is_in_polygon(x, y, shape_coords) for x, y in grid_points])

        # # Filter points inside the polygon
        # valid_points = grid_points[mask]

        def corners(x_mm, y_mm, fov):
            center_to_corner = fov / 2
            return (
                (x_mm + center_to_corner, y_mm + center_to_corner),
                (x_mm - center_to_corner, y_mm + center_to_corner),
                (x_mm - center_to_corner, y_mm - center_to_corner),
                (x_mm + center_to_corner, y_mm - center_to_corner),
            )

        valid_points = []
        for x_center, y_center in grid_points:
            if not self.validate_coordinates(x_center, y_center):
                self._log.debug(
                    f"Manual coords: ignoring {x_center=},{y_center=} because it is outside our movement range."
                )
                continue
            if not self._is_in_polygon(x_center, y_center, shape_coords) and not any(
                [
                    self._is_in_polygon(x_corner, y_corner, shape_coords)
                    for (x_corner, y_corner) in corners(x_center, y_center, fov_size_mm)
                ]
            ):
                self._log.debug(
                    f"Manual coords: ignoring {x_center=},{y_center=} because no corners or center are in poly. (corners={corners(x_center, y_center, fov_size_mm)}"
                )
                continue

            valid_points.append((x_center, y_center))
        if not valid_points:
            return []
        valid_points = np.array(valid_points)

        # Sort points
        sorted_indices = np.lexsort((valid_points[:, 0], valid_points[:, 1]))
        sorted_points = valid_points[sorted_indices]

        # Apply S-Pattern if needed
        if self.fov_pattern == "S-Pattern":
            unique_y = np.unique(sorted_points[:, 1])
            for i in range(1, len(unique_y), 2):
                mask = sorted_points[:, 1] == unique_y[i]
                sorted_points[mask] = sorted_points[mask][::-1]

        # Register FOVs
        for x, y in sorted_points:
            self.navigationViewer.register_fov_to_image(x, y)

        return sorted_points.tolist()

    def add_template_region(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        template_x_mm: np.ndarray,
        template_y_mm: np.ndarray,
        region_id: str,
    ):
        """Add a region based on a template of x and y coordinates"""
        scan_coordinates = []
        for i in range(len(template_x_mm)):
            x = x_mm + template_x_mm[i]
            y = y_mm + template_y_mm[i]
            if self.validate_coordinates(x, y):
                scan_coordinates.append((x, y))
                self.navigationViewer.register_fov_to_image(x, y)
        self.region_centers[region_id] = [x_mm, y_mm, z_mm]
        self.region_fov_coordinates[region_id] = scan_coordinates

    def region_contains_coordinate(self, region_id: str, x: float, y: float) -> bool:
        # TODO: check for manual region
        if not self.validate_region(region_id):
            return False

        bounds = self.get_region_bounds(region_id)
        shape = self.get_region_shape(region_id)

        # For square regions
        if not (bounds["min_x"] <= x <= bounds["max_x"] and bounds["min_y"] <= y <= bounds["max_y"]):
            return False

        # For circle regions
        if shape == "Circle":
            center_x = (bounds["max_x"] + bounds["min_x"]) / 2
            center_y = (bounds["max_y"] + bounds["min_y"]) / 2
            radius = (bounds["max_x"] - bounds["min_x"]) / 2
            if (x - center_x) ** 2 + (y - center_y) ** 2 > radius**2:
                return False

        return True

    def _is_in_polygon(self, x, y, poly):
        n = len(poly)
        inside = False
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def _is_in_circle(self, x, y, center_x, center_y, radius_squared, fov_size_mm_half):
        corners = [
            (x - fov_size_mm_half, y - fov_size_mm_half),
            (x + fov_size_mm_half, y - fov_size_mm_half),
            (x - fov_size_mm_half, y + fov_size_mm_half),
            (x + fov_size_mm_half, y + fov_size_mm_half),
        ]
        return all((cx - center_x) ** 2 + (cy - center_y) ** 2 <= radius_squared for cx, cy in corners)

    def has_regions(self):
        """Check if any regions exist"""
        return len(self.region_centers) > 0

    def validate_region(self, region_id):
        """Validate a region exists"""
        return region_id in self.region_centers and region_id in self.region_fov_coordinates

    def validate_coordinates(self, x, y):
        return (
            SOFTWARE_POS_LIMIT.X_NEGATIVE <= x <= SOFTWARE_POS_LIMIT.X_POSITIVE
            and SOFTWARE_POS_LIMIT.Y_NEGATIVE <= y <= SOFTWARE_POS_LIMIT.Y_POSITIVE
        )

    def sort_coordinates(self):
        self._log.info(f"Acquisition pattern: {self.acquisition_pattern}")

        if len(self.region_centers) <= 1:
            return

        def sort_key(item):
            key, coord = item
            if "manual" in key:
                return (0, coord[1], coord[0])  # Manual coords: sort by y, then x
            else:
                letters = "".join(c for c in key if c.isalpha())
                numbers = "".join(c for c in key if c.isdigit())

                letter_value = 0
                for i, letter in enumerate(reversed(letters)):
                    letter_value += (ord(letter) - ord("A")) * (26**i)

                return (1, letter_value, int(numbers))  # Well coords: sort by letter value, then number

        sorted_items = sorted(self.region_centers.items(), key=sort_key)

        if self.acquisition_pattern == "S-Pattern":
            # Group by row and reverse alternate rows
            rows = itertools.groupby(sorted_items, key=lambda x: x[1][1] if "manual" in x[0] else x[0][0])
            sorted_items = []
            for i, (_, group) in enumerate(rows):
                row = list(group)
                if i % 2 == 1:
                    row.reverse()
                sorted_items.extend(row)

        # Update dictionaries efficiently
        self.region_centers = {k: v for k, v in sorted_items}
        self.region_fov_coordinates = {
            k: self.region_fov_coordinates[k] for k, _ in sorted_items if k in self.region_fov_coordinates
        }

    def get_region_bounds(self, region_id):
        """Get region boundaries"""
        if not self.validate_region(region_id):
            return None
        fovs = np.array(self.region_fov_coordinates[region_id])
        return {
            "min_x": np.min(fovs[:, 0]),
            "max_x": np.max(fovs[:, 0]),
            "min_y": np.min(fovs[:, 1]),
            "max_y": np.max(fovs[:, 1]),
        }

    def get_region_shape(self, region_id):
        if not self.validate_region(region_id):
            return None
        return self.region_shapes[region_id]

    def get_scan_bounds(self):
        """Get bounds of all scan regions with margin"""
        if not self.has_regions():
            return None

        min_x = float("inf")
        max_x = float("-inf")
        min_y = float("inf")
        max_y = float("-inf")

        # Find global bounds across all regions
        for region_id in self.region_fov_coordinates.keys():
            bounds = self.get_region_bounds(region_id)
            if bounds:
                min_x = min(min_x, bounds["min_x"])
                max_x = max(max_x, bounds["max_x"])
                min_y = min(min_y, bounds["min_y"])
                max_y = max(max_y, bounds["max_y"])

        if min_x == float("inf"):
            return None

        # Add margin around bounds (5% of larger dimension)
        width = max_x - min_x
        height = max_y - min_y
        margin = max(width, height) * 0.00  # 0.05

        return {"x": (min_x - margin, max_x + margin), "y": (min_y - margin, max_y + margin)}

    def update_fov_z_level(self, region_id, fov, new_z):
        """Update z-level for a specific FOV and its region center"""
        if not self.validate_region(region_id):
            print(f"Region {region_id} not found")
            return

        # Update FOV coordinates
        fov_coords = self.region_fov_coordinates[region_id]
        if fov < len(fov_coords):
            # Handle both (x,y) and (x,y,z) cases
            x, y = fov_coords[fov][:2]  # Takes first two elements regardless of length
            self.region_fov_coordinates[region_id][fov] = (x, y, new_z)

        # If first FOV, update region center coordinates
        if fov == 0:
            if len(self.region_centers[region_id]) == 3:
                self.region_centers[region_id][2] = new_z
            else:
                self.region_centers[region_id].append(new_z)

        self._log.info(f"Updated z-level to {new_z} for region:{region_id}, fov:{fov}")


class MultiPointController(QObject):

    acquisition_finished = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    spectrum_to_display = Signal(np.ndarray)
    signal_current_configuration = Signal(ChannelMode)
    signal_register_current_fov = Signal(float, float)
    signal_stitcher = Signal(str)
    napari_layers_init = Signal(int, int, object)
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel
    signal_set_display_tabs = Signal(list, int)
    signal_z_piezo_um = Signal(float)
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)
    signal_coordinates = Signal(np.ndarray, np.ndarray, np.ndarray, np.ndarray)  # x, y, z, region

    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        piezo: Optional[PiezoStage],
        microcontroller: Microcontroller,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_manager: ChannelConfigurationManager,
        usb_spectrometer=None,
        scan_coordinates: Optional[ScanCoordinates] = None,
        fluidics=None,
        parent=None,
        headless=False,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.camera: AbstractCamera = camera
        self.stage: AbstractStage = stage
        self.piezo: Optional[PiezoStage] = piezo
        self.microcontroller: Microcontroller = microcontroller
        self.liveController: LiveController = live_controller
        self.autofocusController: AutoFocusController = autofocus_controller
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigurationManager = channel_configuration_manager
        self.multiPointWorker: Optional[MultiPointWorker] = None
        self.thread: Optional[QThread] = None
        self.NX = 1
        self.NY = 1
        self.NZ = 1
        self.Nt = 1
        self.deltaX = Acquisition.DX
        self.deltaY = Acquisition.DY
        # TODO(imo): Switch all to consistent mm units
        self.deltaZ = Acquisition.DZ / 1000
        self.deltat = 0
        self.do_autofocus = False
        self.do_reflection_af = False
        self.focus_map = None
        self.use_manual_focus_map = False
        self.gen_focus_map = False
        self.focus_map_storage = []
        self.already_using_fmap = False
        self.do_segmentation = False
        self.display_resolution_scaling = Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        self.counter = 0
        self.experiment_ID = None
        self.base_path = None
        self.use_piezo = MULTIPOINT_USE_PIEZO_FOR_ZSTACKS
        self.selected_configurations = []
        self.usb_spectrometer = usb_spectrometer
        self.scanCoordinates = scan_coordinates
        self.scan_region_names = []
        self.scan_region_coords_mm = []
        self.scan_region_fov_coords_mm = {}
        self.parent = parent
        self.start_time = 0
        self.old_images_per_page = 1
        z_mm_current = self.stage.get_pos().z_mm
        self.z_range = [z_mm_current, z_mm_current + self.deltaZ * (self.NZ - 1)]  # [start_mm, end_mm]
        self.use_fluidics = False
        self.fluidics = fluidics

        self.headless = headless
        self.z_stacking_config = Z_STACKING_CONFIG

    def acquisition_in_progress(self):
        if self.thread and self.thread.isRunning() and self.multiPointWorker:
            return True
        return False

    def set_use_piezo(self, checked):
        if checked and self.piezo is None:
            raise ValueError("Cannot enable piezo - no piezo stage configured")
        self.use_piezo = checked
        if self.multiPointWorker:
            self.multiPointWorker.update_use_piezo(checked)

    def set_z_stacking_config(self, z_stacking_config_index):
        if z_stacking_config_index in Z_STACKING_CONFIG_MAP:
            self.z_stacking_config = Z_STACKING_CONFIG_MAP[z_stacking_config_index]
        print(f"z-stacking configuration set to {self.z_stacking_config}")

    def set_z_range(self, minZ, maxZ):
        self.z_range = [minZ, maxZ]

    def set_NX(self, N):
        self.NX = N

    def set_NY(self, N):
        self.NY = N

    def set_NZ(self, N):
        self.NZ = N

    def set_Nt(self, N):
        self.Nt = N

    def set_deltaX(self, delta):
        self.deltaX = delta

    def set_deltaY(self, delta):
        self.deltaY = delta

    def set_deltaZ(self, delta_um):
        self.deltaZ = delta_um / 1000

    def set_deltat(self, delta):
        self.deltat = delta

    def set_af_flag(self, flag):
        self.do_autofocus = flag

    def set_reflection_af_flag(self, flag):
        self.do_reflection_af = flag

    def set_manual_focus_map_flag(self, flag):
        self.use_manual_focus_map = flag

    def set_gen_focus_map_flag(self, flag):
        self.gen_focus_map = flag
        if not flag:
            self.autofocusController.set_focus_map_use(False)

    def set_segmentation_flag(self, flag):
        self.do_segmentation = flag

    def set_focus_map(self, focusMap):
        self.focus_map = focusMap  # None if dont use focusMap

    def set_base_path(self, path):
        self.base_path = path

    def set_use_fluidics(self, use_fluidics):
        self.use_fluidics = use_fluidics

    def start_new_experiment(self, experiment_ID):  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = experiment_ID.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        self.recording_start_time = time.time()
        # create a new folder
        utils.ensure_directory_exists(os.path.join(self.base_path, self.experiment_ID))
        self.channelConfigurationManager.write_configuration_selected(
            self.objectiveStore.current_objective,
            self.selected_configurations,
            os.path.join(self.base_path, self.experiment_ID) + "/configurations.xml",
        )  # save the configuration for the experiment
        # Prepare acquisition parameters
        acquisition_parameters = {
            "dx(mm)": self.deltaX,
            "Nx": self.NX,
            "dy(mm)": self.deltaY,
            "Ny": self.NY,
            "dz(um)": self.deltaZ * 1000 if self.deltaZ != 0 else 1,
            "Nz": self.NZ,
            "dt(s)": self.deltat,
            "Nt": self.Nt,
            "with AF": self.do_autofocus,
            "with reflection AF": self.do_reflection_af,
            "with manual focus map": self.use_manual_focus_map,
        }
        try:  # write objective data if it is available
            current_objective = self.parent.objectiveStore.current_objective
            objective_info = self.parent.objectiveStore.objectives_dict.get(current_objective, {})
            acquisition_parameters["objective"] = {}
            for k in objective_info.keys():
                acquisition_parameters["objective"][k] = objective_info[k]
            acquisition_parameters["objective"]["name"] = current_objective
        except:
            try:
                objective_info = OBJECTIVES[DEFAULT_OBJECTIVE]
                acquisition_parameters["objective"] = {}
                for k in objective_info.keys():
                    acquisition_parameters["objective"][k] = objective_info[k]
                acquisition_parameters["objective"]["name"] = DEFAULT_OBJECTIVE
            except:
                pass
        # TODO: USE OBJECTIVE STORE DATA
        acquisition_parameters["sensor_pixel_size_um"] = self.camera.get_pixel_size_binned_um()
        acquisition_parameters["tube_lens_mm"] = TUBE_LENS_MM
        f = open(os.path.join(self.base_path, self.experiment_ID) + "/acquisition parameters.json", "w")
        f.write(json.dumps(acquisition_parameters))
        f.close()

    def set_selected_configurations(self, selected_configurations_name):
        self.selected_configurations = []
        for configuration_name in selected_configurations_name:
            config = self.channelConfigurationManager.get_channel_configuration_by_name(
                self.objectiveStore.current_objective, configuration_name
            )
            if config:
                self.selected_configurations.append(config)

    def get_acquisition_image_count(self):
        """
        Given the current settings on this controller, return how many images an acquisition will
        capture and save to disk.

        NOTE: This does not cover debug images (eg: auto focus) or user created images (eg: custom scripts).

        NOTE: This does attempt to include the "merged" image if that config is enabled.

        Raises a ValueError if the class is not configured for a valid acquisition.
        """
        try:
            # We have Nt timepoints.  For each timepoint, we capture images at all the regions.  Each
            # region has a list of coordinates that we capture at, and at each coordinate we need to
            # do a capture for each requested camera + lighting + other configuration selected.  So
            # total image count is:
            coords_per_region = [
                len(region_coords) for (region_id, region_coords) in self.scanCoordinates.region_fov_coordinates.items()
            ]
            all_regions_coord_count = sum(coords_per_region)

            non_merged_images = self.Nt * self.NZ * all_regions_coord_count * len(self.selected_configurations)
            # When capturing merged images, we capture 1 per fov (where all the configurations are merged)
            merged_images = self.Nt * self.NZ * all_regions_coord_count if control._def.MERGE_CHANNELS else 0

            return non_merged_images + merged_images
        except AttributeError:
            # We don't init all fields in __init__, so it's easy to get attribute errors.  We consider
            # this "not configured" and want it to be a ValueError.
            raise ValueError("Not properly configured for an acquisition, cannot calculate image count.")

    def _temporary_get_an_image_hack(self) -> Tuple[np.array, bool]:
        was_streaming = self.camera.get_is_streaming()
        callbacks_were_enabled = self.camera.get_callbacks_enabled()
        self.camera.enable_callbacks(False)
        test_frame = None
        if not was_streaming:
            self.camera.start_streaming()
        try:
            config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
                or self.liveController.trigger_mode == TriggerMode.HARDWARE
            ):
                self.camera.send_trigger()
            test_frame = self.camera.read_camera_frame()
        finally:
            self.camera.enable_callbacks(callbacks_were_enabled)
            if not was_streaming:
                self.camera.stop_streaming()
        return (test_frame.frame, test_frame.is_color()) if test_frame else (None, False)

    def get_estimated_acquisition_disk_storage(self):
        """
        This does its best to return the number of bytes needed to store the settings for the currently
        configured acquisition on disk.  If you don't have at least this amount of disk space available
        when starting this acquisition, it is likely it will fail with an "out of disk space" error.
        """
        # TODO(imo): This needs updating for AbstractCamera
        if not len(self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)):
            raise ValueError("Cannot calculate disk space requirements without any valid configurations.")
        first_config = self.channelConfigurationManager.get_configurations(self.objectiveStore.current_objective)[0]

        # Our best bet is to grab an image, and use that for our size estimate.
        test_image = None
        try:
            test_image, is_color = self._temporary_get_an_image_hack()
        except Exception as e:
            self._log.exception("Couldn't capture image from camera for size estimate, using worst cast image.")
            # Not ideal that we need to catch Exception, but the camera implementations vary wildly...
            pass

        if test_image is None:
            is_color = squid.abc.CameraPixelFormat.is_color_format(self.camera.get_pixel_format())
            # Do our best to create a fake image with the correct properties.
            # TODO(imo): It'd be better to pull this from our camera but need to wait for AbstractCamera for a consistent way to do that.
            width, height = self.camera.get_crop_size()
            bytes_per_pixel = 3 if is_color else 2  # Worst case assumptions: 24 bit color, 16 bit grayscale

            test_image = np.random.randint(2**16 - 1, size=(height, width, (3 if is_color else 1)), dtype=np.uint16)

        # Depending on settings, we modify the image before saving.  This means we need to actually save an image
        # to see how much disk space it takes up.  This can be very wrong (eg: if we compress during saving, then
        # it is dependent on the data), but is better than just guessing based on raw image size.
        with tempfile.TemporaryDirectory() as temp_save_dir:
            file_id = "test_id"
            test_config = first_config
            size_before = utils.get_directory_disk_usage(temp_save_dir)
            saved_image = utils_acquisition.save_image(test_image, file_id, temp_save_dir, test_config, is_color)
            size_after = utils.get_directory_disk_usage(temp_save_dir)

            size_per_image = size_after - size_before

        # Add in 100kB for non-image files.  This is normally more like 10k total, so this gives us extra.
        non_image_file_size = 100 * 1024

        return size_per_image * self.get_acquisition_image_count() + non_image_file_size

    def run_acquisition(self, acquire_current_fov=False):
        if not self.validate_acquisition_settings():
            # emit acquisition finished signal to re-enable the UI
            self.acquisition_finished.emit()
            return

        self._log.info("start multipoint")

        if acquire_current_fov:
            pos = self.stage.get_pos()
            self.scan_region_coords_mm = [(pos.x_mm, pos.y_mm)]
            self.scan_region_names = "current"
            self.scan_region_fov_coords_mm = {"current": [(pos.x_mm, pos.y_mm)]}
            self.run_acquisition_current_fov = True
        else:
            self.scan_region_coords_mm = list(self.scanCoordinates.region_centers.values())
            self.scan_region_names = list(self.scanCoordinates.region_centers.keys())
            self.scan_region_fov_coords_mm = self.scanCoordinates.region_fov_coordinates
            self.run_acquisition_current_fov = False
        # Save coordinates to CSV in top level folder
        coordinates_df = pd.DataFrame(columns=["region", "x (mm)", "y (mm)", "z (mm)"])
        for region_id, coords_list in self.scan_region_fov_coords_mm.items():
            for coord in coords_list:
                row = {"region": region_id, "x (mm)": coord[0], "y (mm)": coord[1]}
                # Add z coordinate if available
                if len(coord) > 2:
                    row["z (mm)"] = coord[2]
                coordinates_df = pd.concat([coordinates_df, pd.DataFrame([row])], ignore_index=True)
        coordinates_df.to_csv(os.path.join(self.base_path, self.experiment_ID, "coordinates.csv"), index=False)

        self._log.info(f"num fovs: {sum(len(coords) for coords in self.scan_region_fov_coords_mm)}")
        self._log.info(f"num regions: {len(self.scan_region_coords_mm)}")
        self._log.info(f"region ids: {self.scan_region_names}")
        self._log.info(f"region centers: {self.scan_region_coords_mm}")

        self.abort_acqusition_requested = False

        self.configuration_before_running_multipoint = self.liveController.currentConfiguration
        # stop live
        if self.liveController.is_live:
            self.liveController_was_live_before_multipoint = True
            self.liveController.stop_live()  # @@@ to do: also uncheck the live button
        else:
            self.liveController_was_live_before_multipoint = False

        self.camera_callback_was_enabled_before_multipoint = self.camera.get_callbacks_enabled()
        # We need callbacks, because we trigger and then use callbacks for image processing.  This
        # lets us do overlapping triggering (soon).
        self.camera.enable_callbacks(True)

        if self.usb_spectrometer != None:
            if self.usb_spectrometer.streaming_started == True and self.usb_spectrometer.streaming_paused == False:
                self.usb_spectrometer.pause_streaming()
                self.usb_spectrometer_was_streaming = True
            else:
                self.usb_spectrometer_was_streaming = False

        # set current tabs
        if not self.run_acquisition_current_fov:
            self.signal_set_display_tabs.emit(self.selected_configurations, self.NZ)
        else:
            self.signal_set_display_tabs.emit(
                self.selected_configurations, 2
            )  # temp: modify the signal to show multiChannel Widget instead of Mosaic Widget

        # run the acquisition
        self.timestamp_acquisition_started = time.time()

        if self.focus_map:
            self._log.info("Using focus surface for Z interpolation")
            for region_id in self.scan_region_names:
                region_fov_coords = self.scan_region_fov_coords_mm[region_id]
                # Convert each tuple to list for modification
                for i, coords in enumerate(region_fov_coords):
                    x, y = coords[:2]  # This handles both (x,y) and (x,y,z) formats
                    z = self.focus_map.interpolate(x, y, region_id)
                    # Modify the list directly
                    region_fov_coords[i] = (x, y, z)
                    self.scanCoordinates.update_fov_z_level(region_id, i, z)

        elif self.gen_focus_map and not self.do_reflection_af:
            self._log.info("Generating autofocus plane for multipoint grid")
            bounds = self.scanCoordinates.get_scan_bounds()
            if not bounds:
                return
            x_min, x_max = bounds["x"]
            y_min, y_max = bounds["y"]

            # Calculate scan dimensions and center
            x_span = abs(x_max - x_min)
            y_span = abs(y_max - y_min)
            x_center = (x_max + x_min) / 2
            y_center = (y_max + y_min) / 2

            # Determine grid size based on scan dimensions
            if x_span < self.deltaX:
                fmap_Nx = 2
                fmap_dx = self.deltaX  # Force deltaX spacing for small scans
            else:
                fmap_Nx = min(4, max(2, int(x_span / self.deltaX) + 1))
                fmap_dx = max(self.deltaX, x_span / (fmap_Nx - 1))

            if y_span < self.deltaY:
                fmap_Ny = 2
                fmap_dy = self.deltaY  # Force deltaY spacing for small scans
            else:
                fmap_Ny = min(4, max(2, int(y_span / self.deltaY) + 1))
                fmap_dy = max(self.deltaY, y_span / (fmap_Ny - 1))

            # Calculate starting corner position (top-left of the AF map grid)
            starting_x_mm = x_center - (fmap_Nx - 1) * fmap_dx / 2
            starting_y_mm = y_center - (fmap_Ny - 1) * fmap_dy / 2
            # TODO(sm): af map should be a grid mapped to a surface, instead of just corners mapped to a plane
            try:
                # Store existing AF map if any
                self.focus_map_storage = []
                self.already_using_fmap = self.autofocusController.use_focus_map
                for x, y, z in self.autofocusController.focus_map_coords:
                    self.focus_map_storage.append((x, y, z))

                # Define grid corners for AF map
                coord1 = (starting_x_mm, starting_y_mm)  # Starting corner
                coord2 = (starting_x_mm + (fmap_Nx - 1) * fmap_dx, starting_y_mm)  # X-axis corner
                coord3 = (starting_x_mm, starting_y_mm + (fmap_Ny - 1) * fmap_dy)  # Y-axis corner

                self._log.info(f"Generating AF Map: Nx={fmap_Nx}, Ny={fmap_Ny}")
                self._log.info(f"Spacing: dx={fmap_dx:.3f}mm, dy={fmap_dy:.3f}mm")
                self._log.info(f"Center:  x=({x_center:.3f}mm, y={y_center:.3f}mm)")

                # Generate and enable the AF map
                self.autofocusController.gen_focus_map(coord1, coord2, coord3)
                self.autofocusController.set_focus_map_use(True)

                # Return to center position
                self.stage.move_x_to(x_center)
                self.stage.move_y_to(y_center)

            except ValueError:
                self._log.exception("Invalid coordinates for autofocus plane, aborting.")
                return

        self.multiPointWorker = MultiPointWorker(self)
        self.multiPointWorker.use_piezo = self.use_piezo

        if not self.headless:
            # create a QThread object
            self.thread = QThread(parent=self)
            # move the worker to the thread
            self.multiPointWorker.moveToThread(self.thread)
            # connect signals and slots
            self.thread.started.connect(self.multiPointWorker.run)
            self.multiPointWorker.finished.connect(self._on_acquisition_completed)
            self.multiPointWorker.finished.connect(self.multiPointWorker.deleteLater)
            self.multiPointWorker.finished.connect(self.thread.quit)
            self.multiPointWorker.image_to_display.connect(self.slot_image_to_display)
            self.multiPointWorker.image_to_display_multi.connect(self.slot_image_to_display_multi)
            self.multiPointWorker.spectrum_to_display.connect(self.slot_spectrum_to_display)
            self.multiPointWorker.signal_current_configuration.connect(self.slot_current_configuration)
            self.multiPointWorker.signal_register_current_fov.connect(self.slot_register_current_fov)
            self.multiPointWorker.napari_layers_init.connect(self.slot_napari_layers_init)
            self.multiPointWorker.napari_layers_update.connect(self.slot_napari_layers_update)
            self.multiPointWorker.signal_z_piezo_um.connect(self.slot_z_piezo_um)
            self.multiPointWorker.signal_acquisition_progress.connect(self.slot_acquisition_progress)
            self.multiPointWorker.signal_region_progress.connect(self.slot_region_progress)

            # self.thread.finished.connect(self.thread.deleteLater)
            self.thread.finished.connect(self.thread.quit)
            # start the thread
            self.thread.start()
        else:
            # for headless mode
            self.multiPointWorker.run()

    def _on_acquisition_completed(self):
        self._log.debug("MultiPointController._on_acquisition_completed called")
        # restore the previous selected mode
        if self.gen_focus_map:
            self.autofocusController.clear_focus_map()
            for x, y, z in self.focus_map_storage:
                self.autofocusController.focus_map_coords.append((x, y, z))
            self.autofocusController.use_focus_map = self.already_using_fmap
        self.signal_current_configuration.emit(self.configuration_before_running_multipoint)
        self.liveController.set_microscope_mode(self.configuration_before_running_multipoint)

        # Restore callbacks to pre-acquisition state
        self.camera.enable_callbacks(self.camera_callback_was_enabled_before_multipoint)

        # re-enable live if it's previously on
        if self.liveController_was_live_before_multipoint and RESUME_LIVE_AFTER_ACQUISITION:
            self.liveController.start_live()

        if self.usb_spectrometer != None:
            if self.usb_spectrometer_was_streaming:
                self.usb_spectrometer.resume_streaming()

        # emit the acquisition finished signal to enable the UI
        if self.parent is not None:
            try:
                self.parent.dataHandler.sort("Sort by prediction score")
                self.parent.dataHandler.signal_populate_page0.emit()
            except:
                pass
        self._log.info(f"total time for acquisition + processing + reset: {time.time() - self.recording_start_time}")
        utils.create_done_file(os.path.join(self.base_path, self.experiment_ID))

        if self.run_acquisition_current_fov:
            self.run_acquisition_current_fov = False
        else:
            # move back to the center of the current region if using "glass slide"
            if "current" in self.scanCoordinates.region_centers:
                region_center = self.scanCoordinates.region_centers["current"]
                try:
                    self.stage.move_x_to(region_center[0])
                    self.stage.move_y_to(region_center[1])
                    self.stage.move_z_to(region_center[2])
                except:
                    self._log.error("Failed to move to center of current region")

        self.acquisition_finished.emit()
        if not self.abort_acqusition_requested:
            self.signal_stitcher.emit(os.path.join(self.base_path, self.experiment_ID))

        if not self.headless:
            QApplication.processEvents()

    def request_abort_aquisition(self):
        self.abort_acqusition_requested = True

    def slot_image_to_display(self, image):
        self.image_to_display.emit(image)

    def slot_spectrum_to_display(self, data):
        self.spectrum_to_display.emit(data)

    def slot_image_to_display_multi(self, image, illumination_source):
        self.image_to_display_multi.emit(image, illumination_source)

    def slot_current_configuration(self, configuration):
        self.signal_current_configuration.emit(configuration)

    def slot_register_current_fov(self, x_mm, y_mm):
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def slot_napari_layers_init(self, image_height, image_width, dtype):
        self.napari_layers_init.emit(image_height, image_width, dtype)

    def slot_napari_layers_update(self, image, x_mm, y_mm, k, channel):
        self.napari_layers_update.emit(image, x_mm, y_mm, k, channel)

    def slot_z_piezo_um(self, displacement_um):
        self.signal_z_piezo_um.emit(displacement_um)

    def slot_acquisition_progress(self, current_region, total_regions, current_time_point):
        self.signal_acquisition_progress.emit(current_region, total_regions, current_time_point)

    def slot_region_progress(self, current_fov, total_fovs):
        self.signal_region_progress.emit(current_fov, total_fovs)

    def validate_acquisition_settings(self) -> bool:
        """Validate settings before starting acquisition"""
        if self.do_reflection_af and not self.parent.laserAutofocusController.laser_af_properties.has_reference:
            QMessageBox.warning(
                None,
                "Laser Autofocus Not Ready",
                "Please set the laser autofocus reference position before starting acquisition with laser AF enabled.",
            )
            return False
        return True


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

        # Add labels to status layout with spacing
        status_layout.addWidget(self.cursor_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.pixel_value_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.stage_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.piezo_position_label)
        status_layout.addStretch()  # Push labels to the left
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.btn_line_profiler)  # Add button after stretch

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

                piezo = self.liveController.microscope.piezo
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

                    # Auto-scale the plot
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


class LaserAFSettingManager:
    """Manages JSON-based laser autofocus configurations."""

    def __init__(self):
        self.autofocus_configurations: Dict[str, LaserAFConfig] = {}  # Dict[str, Dict[str, Any]]
        self.current_profile_path = None

    def set_profile_path(self, profile_path: Path) -> None:
        self.current_profile_path = profile_path

    def load_configurations(self, objective: str) -> None:
        """Load autofocus configurations for a specific objective."""
        config_file = self.current_profile_path / objective / "laser_af_settings.json"
        if config_file.exists():
            with open(config_file, "r") as f:
                config_dict = json.load(f)
                self.autofocus_configurations[objective] = LaserAFConfig(**config_dict)

    def save_configurations(self, objective: str) -> None:
        """Save autofocus configurations for a specific objective."""
        if objective not in self.autofocus_configurations:
            return

        objective_path = self.current_profile_path / objective
        if not objective_path.exists():
            objective_path.mkdir(parents=True)
        config_file = objective_path / "laser_af_settings.json"

        config_dict = self.autofocus_configurations[objective].model_dump(serialize=True)
        with open(config_file, "w") as f:
            json.dump(config_dict, f, indent=4)

    def get_settings_for_objective(self, objective: str) -> Dict[str, Any]:
        if objective not in self.autofocus_configurations:
            raise ValueError(f"No configuration found for objective {objective}")
        return self.autofocus_configurations[objective]

    def get_laser_af_settings(self) -> Dict[str, Any]:
        return self.autofocus_configurations

    def update_laser_af_settings(
        self, objective: str, updates: Dict[str, Any], crop_image: Optional[np.ndarray] = None
    ) -> None:
        if objective not in self.autofocus_configurations:
            self.autofocus_configurations[objective] = LaserAFConfig(**updates)
        else:
            config = self.autofocus_configurations[objective]
            self.autofocus_configurations[objective] = config.model_copy(update=updates)
        if crop_image is not None:
            self.autofocus_configurations[objective].set_reference_image(crop_image)


class ConfigurationManager:
    """Main configuration manager that coordinates channel and autofocus configurations."""

    def __init__(
        self,
        channel_manager: ChannelConfigurationManager,
        laser_af_manager: Optional[LaserAFSettingManager] = None,
        base_config_path: Path = Path("acquisition_configurations"),
        profile: str = "default_profile",
    ):
        super().__init__()
        self.base_config_path = Path(base_config_path)
        self.current_profile = profile
        self.available_profiles = self._get_available_profiles()

        self.channel_manager = channel_manager
        self.laser_af_manager = laser_af_manager

        self.load_profile(profile)

    def _get_available_profiles(self) -> List[str]:
        """Get all available user profile names in the base config path. Use default profile if no other profiles exist."""
        if not self.base_config_path.exists():
            os.makedirs(self.base_config_path)
            os.makedirs(self.base_config_path / "default_profile")
            for objective in OBJECTIVES:
                os.makedirs(self.base_config_path / "default_profile" / objective)
        return [d.name for d in self.base_config_path.iterdir() if d.is_dir()]

    def _get_available_objectives(self, profile_path: Path) -> List[str]:
        """Get all available objective names in a profile."""
        return [d.name for d in profile_path.iterdir() if d.is_dir()]

    def load_profile(self, profile_name: str) -> None:
        """Load all configurations from a specific profile."""
        profile_path = self.base_config_path / profile_name
        if not profile_path.exists():
            raise ValueError(f"Profile {profile_name} does not exist")

        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(profile_path)

        # Load configurations for each objective
        for objective in self._get_available_objectives(profile_path):
            if self.channel_manager:
                self.channel_manager.load_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.load_configurations(objective)

    def create_new_profile(self, profile_name: str) -> None:
        """Create a new profile using current configurations."""
        new_profile_path = self.base_config_path / profile_name
        if new_profile_path.exists():
            raise ValueError(f"Profile {profile_name} already exists")
        os.makedirs(new_profile_path)

        objectives = OBJECTIVES

        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(new_profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(new_profile_path)

        for objective in objectives:
            os.makedirs(new_profile_path / objective)
            if self.channel_manager:
                self.channel_manager.save_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.save_configurations(objective)

        self.available_profiles = self._get_available_profiles()


class ContrastManager:
    def __init__(self):
        self.contrast_limits = {}
        self.acquisition_dtype = None

    def update_limits(self, channel, min_val, max_val):
        self.contrast_limits[channel] = (min_val, max_val)

    def get_limits(self, channel, dtype=None):
        if dtype is not None:
            if self.acquisition_dtype is None:
                self.acquisition_dtype = dtype
            elif self.acquisition_dtype != dtype:
                self.scale_contrast_limits(dtype)
        return self.contrast_limits.get(channel, self.get_default_limits())

    def get_default_limits(self):
        if self.acquisition_dtype is None:
            return (0, 1)
        elif np.issubdtype(self.acquisition_dtype, np.integer):
            info = np.iinfo(self.acquisition_dtype)
            return (info.min, info.max)
        elif np.issubdtype(self.acquisition_dtype, np.floating):
            return (0.0, 1.0)
        else:
            return (0, 1)

    def get_scaled_limits(self, channel, target_dtype):
        min_val, max_val = self.get_limits(channel)
        if self.acquisition_dtype == target_dtype:
            return min_val, max_val

        source_info = np.iinfo(self.acquisition_dtype)
        target_info = np.iinfo(target_dtype)

        scaled_min = (min_val - source_info.min) / (source_info.max - source_info.min) * (
            target_info.max - target_info.min
        ) + target_info.min
        scaled_max = (max_val - source_info.min) / (source_info.max - source_info.min) * (
            target_info.max - target_info.min
        ) + target_info.min

        return scaled_min, scaled_max

    def scale_contrast_limits(self, target_dtype):
        print(f"{self.acquisition_dtype} -> {target_dtype}")
        for channel in self.contrast_limits.keys():
            self.contrast_limits[channel] = self.get_scaled_limits(channel, target_dtype)

        self.acquisition_dtype = target_dtype


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


class LaserAutofocusController(QObject):

    image_to_display = Signal(np.ndarray)
    signal_displacement_um = Signal(float)
    signal_cross_correlation = Signal(float)
    signal_piezo_position_update = Signal()  # Signal to emit piezo position updates

    def __init__(
        self,
        microcontroller: Microcontroller,
        camera: AbstractCamera,
        liveController: LiveController,
        stage: AbstractStage,
        piezo: Optional[PiezoStage] = None,
        objectiveStore: Optional[ObjectiveStore] = None,
        laserAFSettingManager: Optional[LaserAFSettingManager] = None,
    ):
        QObject.__init__(self)
        self._log = squid.logging.get_logger(__class__.__name__)
        self.microcontroller = microcontroller
        self.camera: AbstractCamera = camera
        self.liveController: LiveController = liveController
        self.stage = stage
        self.piezo = piezo
        self.objectiveStore = objectiveStore
        self.laserAFSettingManager = laserAFSettingManager
        self.characterization_mode = LASER_AF_CHARACTERIZATION_MODE

        self.is_initialized = False

        self.laser_af_properties = LaserAFConfig()
        self.reference_crop = None

        self.x_width = 3088
        self.y_width = 2064

        self.spot_spacing_pixels = None  # spacing between the spots from the two interfaces (unit: pixel)

        self.image = None  # for saving the focus camera image for debugging when centroid cannot be found

        # Load configurations if provided
        if self.laserAFSettingManager:
            self.load_cached_configuration()

    def initialize_manual(self, config: LaserAFConfig) -> None:
        """Initialize laser autofocus with manual parameters."""
        adjusted_config = config.model_copy(
            update={
                "x_reference": config.x_reference
                - config.x_offset,  # self.x_reference is relative to the cropped region
                "x_offset": int((config.x_offset // 8) * 8),
                "y_offset": int((config.y_offset // 2) * 2),
                "width": int((config.width // 8) * 8),
                "height": int((config.height // 2) * 2),
            }
        )

        self.laser_af_properties = adjusted_config

        if self.laser_af_properties.has_reference:
            self.reference_crop = self.laser_af_properties.reference_image_cropped

        self.camera.set_region_of_interest(
            self.laser_af_properties.x_offset,
            self.laser_af_properties.y_offset,
            self.laser_af_properties.width,
            self.laser_af_properties.height,
        )

        self.is_initialized = True

        # Update cache if objective store and laser_af_settings is available
        if self.objectiveStore and self.laserAFSettingManager and self.objectiveStore.current_objective:
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective, config.model_dump()
            )

    def load_cached_configuration(self):
        """Load configuration from the cache if available."""
        laser_af_settings = self.laserAFSettingManager.get_laser_af_settings()
        current_objective = self.objectiveStore.current_objective if self.objectiveStore else None
        if current_objective and current_objective in laser_af_settings:
            config = self.laserAFSettingManager.get_settings_for_objective(current_objective)

            # Update camera settings
            self.camera.set_exposure_time(config.focus_camera_exposure_time_ms)
            try:
                self.camera.set_analog_gain(config.focus_camera_analog_gain)
            except NotImplementedError:
                pass

            # Initialize with loaded config
            self.initialize_manual(config)

    def initialize_auto(self) -> bool:
        """Automatically initialize laser autofocus by finding the spot and calibrating.

        This method:
        1. Finds the laser spot on full sensor
        2. Sets up ROI around the spot
        3. Calibrates pixel-to-um conversion using two z positions

        Returns:
            bool: True if initialization successful, False if any step fails
        """
        self.camera.set_region_of_interest(0, 0, 3088, 2064)

        # update camera settings
        self.camera.set_exposure_time(self.laser_af_properties.focus_camera_exposure_time_ms)
        try:
            self.camera.set_analog_gain(self.laser_af_properties.focus_camera_analog_gain)
        except NotImplementedError:
            pass

        # Find initial spot position
        self.microcontroller.turn_on_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

        result = self._get_laser_spot_centroid(remove_background=True)
        if result is None:
            self._log.error("Failed to find laser spot during initialization")
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
            return False
        x, y = result

        self.microcontroller.turn_off_AF_laser()
        self.microcontroller.wait_till_operation_is_completed()

        # Set up ROI around spot and clear reference
        config = self.laser_af_properties.model_copy(
            update={
                "x_offset": x - self.laser_af_properties.width / 2,
                "y_offset": y - self.laser_af_properties.height / 2,
                "has_reference": False,
            }
        )
        self.reference_crop = None
        config.set_reference_image(None)
        self._log.info(f"Laser spot location on the full sensor is ({int(x)}, {int(y)})")

        self.initialize_manual(config)

        # Calibrate pixel-to-um conversion
        if not self._calibrate_pixel_to_um():
            self._log.error("Failed to calibrate pixel-to-um conversion")
            return False

        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)

        return True

    def _calibrate_pixel_to_um(self) -> bool:
        """Calibrate pixel-to-um conversion.

        Returns:
            bool: True if calibration successful, False otherwise
        """
        # Calibrate pixel-to-um conversion
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Faield to turn on AF laser before pixel to um calibration, cannot continue!")
            return False

        # Move to first position and measure
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self.piezo is not None:
            time.sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 1)")
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Error turning off AF laser after spot calibration failure (position 1)")
                # Just fall through since we are already on a failure path.
            return False
        x0, y0 = result

        # Move to second position and measure
        self._move_z(self.laser_af_properties.pixel_to_um_calibration_distance)
        time.sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)

        result = self._get_laser_spot_centroid()
        if result is None:
            self._log.error("Failed to find laser spot during calibration (position 2)")
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Error turning off AF laser after spot calibration failure (position 2)")
                # Just fall through since we are already on a failure path.
            return False
        x1, y1 = result

        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception(
                "Error turning off AF laser after spot calibration acquisition.  Continuing in unknown state"
            )

        # move back to initial position
        self._move_z(-self.laser_af_properties.pixel_to_um_calibration_distance / 2)
        if self.piezo is not None:
            time.sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)

        # Calculate conversion factor
        if x1 - x0 == 0:
            pixel_to_um = 0.4  # Simulation value
            self._log.warning("Using simulation value for pixel_to_um conversion")
        else:
            pixel_to_um = self.laser_af_properties.pixel_to_um_calibration_distance / (x1 - x0)
        self._log.info(f"Pixel to um conversion factor is {pixel_to_um:.3f} um/pixel")
        calibration_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Update config with new calibration values
        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={"pixel_to_um": pixel_to_um, "calibration_timestamp": calibration_timestamp}
        )

        # Update cache
        if self.objectiveStore and self.laserAFSettingManager:
            self.laserAFSettingManager.update_laser_af_settings(
                self.objectiveStore.current_objective, self.laser_af_properties.model_dump()
            )

        return True

    def set_laser_af_properties(self, updates: dict) -> None:
        """Update laser autofocus properties. Used for updating settings from GUI."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.is_initialized = False

    def update_threshold_properties(self, updates: dict) -> None:
        """Update threshold properties. Save settings without re-initializing."""
        self.laser_af_properties = self.laser_af_properties.model_copy(update=updates)
        self.laserAFSettingManager.update_laser_af_settings(self.objectiveStore.current_objective, updates)
        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)
        self._log.info("Updated threshold properties")

    def measure_displacement(self) -> float:
        """Measure the displacement of the laser spot from the reference position.

        Returns:
            float: Displacement in micrometers, or float('nan') if measurement fails
        """

        def finish_with(um: float) -> float:
            self.signal_displacement_um.emit(um)
            return um

        try:
            # turn on the laser
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Turning on AF laser timed out, failed to measure displacement.")
            return finish_with(float("nan"))

        # get laser spot location
        result = self._get_laser_spot_centroid()

        # turn off the laser
        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Turning off AF laser timed out!  We got a displacement but laser may still be on.")
            # Continue with the measurement, but we're essentially in an unknown / weird state here.  It's not clear
            # what we should do.

        if result is None:
            self._log.error("Failed to detect laser spot during displacement measurement")
            return finish_with(float("nan"))  # Signal invalid measurement

        x, y = result
        # calculate displacement
        displacement_um = (x - self.laser_af_properties.x_reference) * self.laser_af_properties.pixel_to_um
        return finish_with(displacement_um)

    def move_to_target(self, target_um: float) -> bool:
        """Move the stage to reach a target displacement from reference position.

        Args:
            target_um: Target displacement in micrometers

        Returns:
            bool: True if move was successful, False if measurement failed or displacement was out of range
        """
        if not self.laser_af_properties.has_reference:
            self._log.warning("Cannot move to target - reference not set")
            return False

        current_displacement_um = self.measure_displacement()
        self._log.info(f"Current laser AF displacement: {current_displacement_um:.1f} μm")

        if math.isnan(current_displacement_um):
            self._log.error("Cannot move to target: failed to measure current displacement")
            return False

        if abs(current_displacement_um) > self.laser_af_properties.laser_af_range:
            self._log.warning(
                f"Measured displacement ({current_displacement_um:.1f} μm) is unreasonably large, using previous z position"
            )
            return False

        um_to_move = target_um - current_displacement_um
        self._move_z(um_to_move)

        # Verify using cross-correlation that spot is in same location as reference
        cc_result, correlation = self._verify_spot_alignment()
        self.signal_cross_correlation.emit(correlation)
        if not cc_result:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            # move back to the current position
            self._move_z(-um_to_move)
            return False
        else:
            self._log.info("Cross correlation check passed - spots are well aligned")
            return True

    def _move_z(self, um_to_move: float) -> None:
        if self.piezo is not None:
            # TODO: check if um_to_move is in the range of the piezo
            self.piezo.move_relative(um_to_move)
            self.signal_piezo_position_update.emit()
        else:
            self.stage.move_z(um_to_move / 1000)

    def set_reference(self) -> bool:
        """Set the current spot position as the reference position.

        Captures and stores both the spot position and a cropped reference image
        around the spot for later alignment verification.

        Returns:
            bool: True if reference was set successfully, False if spot detection failed
        """
        # turn on the laser
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for reference setting!")
            return False

        # get laser spot location and image
        result = self._get_laser_spot_centroid()
        reference_image = self.image

        # turn off the laser
        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn off AF laser after setting reference, laser is in an unknown state!")
            # Continue on since we got our reading, but the system is potentially in a weird state!

        if result is None or reference_image is None:
            self._log.error("Failed to detect laser spot while setting reference")
            return False

        x, y = result

        # Store cropped and normalized reference image
        center_y = int(reference_image.shape[0] / 2)
        x_start = max(0, int(x) - self.laser_af_properties.spot_crop_size // 2)
        x_end = min(reference_image.shape[1], int(x) + self.laser_af_properties.spot_crop_size // 2)
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(reference_image.shape[0], center_y + self.laser_af_properties.spot_crop_size // 2)

        reference_crop = reference_image[y_start:y_end, x_start:x_end].astype(np.float32)
        self.reference_crop = (reference_crop - np.mean(reference_crop)) / np.max(reference_crop)

        self.signal_displacement_um.emit(0)
        self._log.info(f"Set reference position to ({x:.1f}, {y:.1f})")

        self.laser_af_properties = self.laser_af_properties.model_copy(
            update={"x_reference": x, "has_reference": True}
        )  # We don't keep reference_crop here to avoid serializing it

        # Update cached file. reference_crop needs to be saved.
        self.laserAFSettingManager.update_laser_af_settings(
            self.objectiveStore.current_objective,
            {"x_reference": x + self.laser_af_properties.x_offset, "has_reference": True},
            crop_image=self.reference_crop,
        )
        self.laserAFSettingManager.save_configurations(self.objectiveStore.current_objective)

        self._log.info("Reference spot position set")

        return True

    def on_settings_changed(self) -> None:
        """Handle objective change or profile load event.

        This method is called when the objective changes. It resets the initialization
        status and loads the cached configuration for the new objective.
        """
        self.is_initialized = False
        self.load_cached_configuration()

    def _verify_spot_alignment(self) -> Tuple[bool, np.array]:
        """Verify laser spot alignment using cross-correlation with reference image.

        Captures current laser spot image and compares it with the reference image
        using normalized cross-correlation. Images are cropped around the expected
        spot location and normalized by maximum intensity before comparison.

        Returns:
            bool: True if spots are well aligned (correlation > CORRELATION_THRESHOLD), False otherwise
        """
        failure_return_value = False, np.array([0.0, 0.0])

        # Get current spot image
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on AF laser for verifying spot alignment.")
            return failure_return_value

        # TODO: create a function to get the current image (taking care of trigger mode checking and laser on/off switching)
        """
        self.camera.send_trigger()
        current_image = self.camera.read_frame()
        """
        self._get_laser_spot_centroid()
        current_image = self.image

        try:
            self.microcontroller.turn_off_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn off AF laser after verifying spot alignment, laser in unknown state!")
            # Continue on because we got a reading, but the system is in a potentially weird and unknown state here.

        if self.reference_crop is None:
            self._log.warning("No reference crop stored")
            return failure_return_value

        if current_image is None:
            self._log.error("Failed to get images for cross-correlation check")
            return failure_return_value

        # Crop and normalize current image
        center_x = int(self.laser_af_properties.x_reference)
        center_y = int(current_image.shape[0] / 2)

        x_start = max(0, center_x - self.laser_af_properties.spot_crop_size // 2)
        x_end = min(current_image.shape[1], center_x + self.laser_af_properties.spot_crop_size // 2)
        y_start = max(0, center_y - self.laser_af_properties.spot_crop_size // 2)
        y_end = min(current_image.shape[0], center_y + self.laser_af_properties.spot_crop_size // 2)

        current_crop = current_image[y_start:y_end, x_start:x_end].astype(np.float32)
        current_norm = (current_crop - np.mean(current_crop)) / np.max(current_crop)

        # Calculate normalized cross correlation
        correlation = np.corrcoef(current_norm.ravel(), self.reference_crop.ravel())[0, 1]

        self._log.info(f"Cross correlation with reference: {correlation:.3f}")

        # Check if correlation exceeds threshold
        if correlation < self.laser_af_properties.correlation_threshold:
            self._log.warning("Cross correlation check failed - spots not well aligned")
            return False, correlation

        return True, correlation

    def get_new_frame(self):
        # IMPORTANT: This assumes that the autofocus laser is already on!
        self.camera.send_trigger(self.camera.get_exposure_time())
        return self.camera.read_frame()

    def _get_laser_spot_centroid(self, remove_background: bool = False) -> Optional[Tuple[float, float]]:
        """Get the centroid location of the laser spot.

        Averages multiple measurements to improve accuracy. The number of measurements
        is controlled by LASER_AF_AVERAGING_N.

        Returns:
            Optional[Tuple[float, float]]: (x,y) coordinates of spot centroid, or None if detection fails
        """
        # disable camera callback
        self.camera.enable_callbacks(False)

        successful_detections = 0
        tmp_x = 0
        tmp_y = 0

        for i in range(self.laser_af_properties.laser_af_averaging_n):
            try:
                image = self.get_new_frame()
                if image is None:
                    self._log.warning(f"Failed to read frame {i+1}/{self.laser_af_properties.laser_af_averaging_n}")
                    continue

                self.image = image  # store for debugging # TODO: add to return instead of storing

                if remove_background:
                    # remove background using top hat filter
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (50, 50))  # TODO: tmp hard coded value
                    image = cv2.morphologyEx(image, cv2.MORPH_TOPHAT, kernel)

                # calculate centroid
                spot_detection_params = {
                    "y_window": self.laser_af_properties.y_window,
                    "x_window": self.laser_af_properties.x_window,
                    "peak_width": self.laser_af_properties.min_peak_width,
                    "peak_distance": self.laser_af_properties.min_peak_distance,
                    "peak_prominence": self.laser_af_properties.min_peak_prominence,
                    "spot_spacing": self.laser_af_properties.spot_spacing,
                }
                result = utils.find_spot_location(
                    image, mode=self.laser_af_properties.spot_detection_mode, params=spot_detection_params
                )
                if result is None:
                    self._log.warning(
                        f"No spot detected in frame {i+1}/{self.laser_af_properties.laser_af_averaging_n}"
                    )
                    continue

                x, y = result
                tmp_x += x
                tmp_y += y
                successful_detections += 1

            except Exception as e:
                self._log.error(
                    f"Error processing frame {i+1}/{self.laser_af_properties.laser_af_averaging_n}: {str(e)}"
                )
                continue

        # optionally display the image
        if LASER_AF_DISPLAY_SPOT_IMAGE:
            self.image_to_display.emit(image)

        # Check if we got enough successful detections
        if successful_detections <= 0:
            self._log.error(f"No successful detections")
            return None

        # Calculate average position from successful detections
        x = tmp_x / successful_detections
        y = tmp_y / successful_detections

        self._log.debug(f"Spot centroid found at ({x:.1f}, {y:.1f}) from {successful_detections} detections")
        return (x, y)

    def get_image(self) -> Optional[np.ndarray]:
        """Capture and display a single image from the laser autofocus camera.

        Turns the laser on, captures an image, displays it, then turns the laser off.

        Returns:
            Optional[np.ndarray]: The captured image, or None if capture failed
        """
        # turn on the laser
        try:
            self.microcontroller.turn_on_AF_laser()
            self.microcontroller.wait_till_operation_is_completed()
        except TimeoutError:
            self._log.exception("Failed to turn on laser AF laser before get_image, cannot get image.")
            return None

        try:
            # send trigger, grab image and display image
            self.camera.send_trigger()
            image = self.camera.read_frame()

            if image is None:
                self._log.error("Failed to read frame in get_image")
                return None

            self.image_to_display.emit(image)
            return image

        except Exception as e:
            self._log.error(f"Error capturing image: {str(e)}")
            return None

        finally:
            # turn off the laser
            try:
                self.microcontroller.turn_off_AF_laser()
                self.microcontroller.wait_till_operation_is_completed()
            except TimeoutError:
                self._log.exception("Failed to turn off AF laser after get_image!")

    def clear_reference(self):
        """Clear reference position"""
        self.has_reference = False
        self.reference_crop = None
        self._log.info("Reference spot position cleared")
