from pyvcam import pvc
from pyvcam.camera import Camera as PVCam
from typing import Callable
import numpy as np
import threading
import time

import squid.logging
from control._def import *


def get_sn_by_model(model_name: str) -> str:
    # We don't need this for kinetix camera
    return None


class Camera(object):
    def __init__(
        self, sn=None, resolution=(2760, 2760), is_global_shutter=False, rotate_image_angle=None, flip_image=None
    ):
        self.log = squid.logging.get_logger(self.__class__.__name__)

        pvc.init_pvcam()
        self.cam = None

        self.exposure_time = 1  # ms
        self.analog_gain = 0
        self.is_streaming = False
        self.pixel_format = None
        self.is_color = False

        self.frame_ID = -1
        self.frame_ID_software = -1
        self.frame_ID_offset_hardware_trigger = 0
        self.timestamp = 0
        self.trigger_mode = None

        self.strobe_delay_us = None

        self.image_locked = False
        self.current_frame = None
        self.callback_is_enabled = False
        self.new_image_callback_external = None
        self.stop_waiting = False

        self.GAIN_MAX = 0
        self.GAIN_MIN = 0
        self.GAIN_STEP = 0
        self.EXPOSURE_TIME_MS_MIN = 0
        self.EXPOSURE_TIME_MS_MAX = 10000

        self.rotate_image_angle = rotate_image_angle
        self.flip_image = flip_image
        self.is_global_shutter = is_global_shutter

        self.temperature_reading_callback = None
        self.terminate_read_temperature_thread = False
        self.temperature_reading_thread = threading.Thread(target=self.check_temperature, daemon=True)

        self.ROI_offset_x = 0
        self.ROI_offset_y = 0
        self.ROI_width = 2760
        self.ROI_height = 2760

        self.OffsetX = 0
        self.OffsetY = 0
        self.Width = 2760
        self.Height = 2760

        self.WidthMax = 2760
        self.HeightMax = 2760

    def open(self):
        self.cam = next(PVCam.detect_camera())
        self.cam.open()
        self.cam.exp_res = 1  # Exposure resolution in microseconds
        self.cam.readout_port = 2  # Dynamic Range Mode
        self.cam.set_roi(220, 220, 2760, 2760)  # Crop fov to 25mm
        self.log.info(f"Cropped area: {self.cam.shape(0)}")
        self.calculate_strobe_delay()  # hard coded before implementing roi
        self.set_temperature(15)  # temperature range: -15 - 15 degree Celcius
        # self.temperature_reading_thread.start()
        """
        port_speed_gain_table:
        {'Sensitivity': {'port_value': 0, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 12, 'gain_range': [1], 'Standard': {'gain_index': 1}}}, 
        'Speed': {'port_value': 1, 'Speed_0': {'speed_index': 0, 'pixel_time': 5, 'bit_depth': 8, 'gain_range': [1, 2], 'Sensitivity': {'gain_index': 1}, 'Full Well': {'gain_index': 2}}}, 
        'Dynamic Range': {'port_value': 2, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 16, 'gain_range': [1], 'Standard': {'gain_index': 1}}}, 
        'Sub-Electron': {'port_value': 3, 'Speed_0': {'speed_index': 0, 'pixel_time': 10, 'bit_depth': 16, 'gain_range': [1], 'Standard': {'gain_index': 1}}}}
        """

    def open_by_sn(self, sn: str):
        self.open()

    def close(self):
        if self.is_streaming:
            self.stop_streaming()
        self.terminate_read_temperature_thread = True
        self.temperature_reading_callback = None
        # self.temperature_reading_thread.join()
        self.cam.close()
        pvc.uninit_pvcam()

    def set_callback(self, function: Callable):
        self.new_image_callback_external = function

    def enable_callback(self):
        self.log.info("enable callback")
        if self.callback_is_enabled:
            return
        self.start_streaming()

        self.stop_waiting = False
        self.callback_thread = threading.Thread(target=self._wait_and_callback, daemon=True)
        self.callback_thread.start()

        self.callback_is_enabled = True

    def _wait_and_callback(self):
        while True:
            if self.stop_waiting:
                break
            data = self.read_frame()
            if data is not None:
                self._on_new_frame(data)

    def _on_new_frame(self, image: np.ndarray):
        if self.image_locked:
            self.log.warning("Last image is still being processed; a frame is dropped")
            return

        self.current_frame = image

        self.frame_ID_software += 1
        self.frame_ID += 1

        # frame ID for hardware triggered acquisition
        if self.trigger_mode == TriggerMode.HARDWARE:
            if self.frame_ID_offset_hardware_trigger == None:
                self.frame_ID_offset_hardware_trigger = self.frame_ID
            self.frame_ID = self.frame_ID - self.frame_ID_offset_hardware_trigger

        self.timestamp = time.time()
        self.new_image_callback_external(self)

    def disable_callback(self):
        self.log.info("disable callback")
        if not self.callback_is_enabled:
            return

        self.stop_waiting = True
        time.sleep(0.02)
        if hasattr(self, "callback_thread"):
            try:
                self.cam.abort()
            except Exception as e:
                self.log.error("abort failed")
                raise e
            self.callback_thread.join()
        self.callback_is_enabled = False

        if self.is_streaming:
            self.cam.start_live()

    def set_analog_gain(self, gain: float):
        pass

    def set_exposure_time(self, exposure_time: float):
        if self.trigger_mode == TriggerMode.SOFTWARE:
            adjusted = exposure_time * 1000
        elif self.trigger_mode == TriggerMode.HARDWARE:
            adjusted = self.strobe_delay_us + exposure_time * 1000
        try:
            has_callback = self.callback_is_enabled
            self.stop_streaming()
            print("setting exposure time")
            self.cam.exp_time = int(adjusted)  # us
            self.exposure_time = exposure_time  # ms
            if has_callback:
                self.enable_callback()
            if not self.is_streaming:
                self.start_streaming()
        except Exception as e:
            self.log.error("set_exposure_time failed")
            raise e

    def set_temperature_reading_callback(self, func: Callable):
        self.temperature_reading_callback = func

    def set_temperature(self, temperature: float):
        try:
            self.cam.temp_setpoint = int(temperature)
        except Exception as e:
            self.log.error("set_temperature failed")
            raise e

    def get_temperature(self) -> float:
        try:
            return self.cam.temp
        except Exception as e:
            self.log.error("get_temperature failed")

    def check_temperature(self):
        while self.terminate_read_temperature_thread == False:
            time.sleep(2)
            temperature = self.get_temperature()
            if self.temperature_reading_callback is not None:
                try:
                    self.temperature_reading_callback(temperature)
                except Exception as e:
                    self.log.error("Temperature read callback failed due to error: " + repr(e))

    def set_continuous_acquisition(self):
        try:
            has_callback = self.callback_is_enabled
            self.stop_streaming()
            self.cam.exp_mode = "Internal Trigger"
            self.trigger_mode = TriggerMode.CONTINUOUS
            if has_callback:
                self.enable_callback()
            if not self.is_streaming:
                self.start_streaming()
        except Exception as e:
            self.log.error("set_continuous_acquisition failed")
            raise e

    def set_software_triggered_acquisition(self):
        try:
            has_callback = self.callback_is_enabled
            self.stop_streaming()
            self.cam.exp_mode = "Software Trigger Edge"
            self.trigger_mode = TriggerMode.SOFTWARE
            if has_callback:
                self.enable_callback()
            if not self.is_streaming:
                self.start_streaming()
        except Exception as e:
            self.log.error("set_software_triggered_acquisition failed")
            raise e

    def set_hardware_triggered_acquisition(self):
        try:
            has_callback = self.callback_is_enabled
            self.stop_streaming()
            self.cam.exp_mode = "Edge Trigger"
            self.frame_ID_offset_hardware_trigger = None
            self.trigger_mode = TriggerMode.HARDWARE
            if has_callback:
                self.enable_callback()
            if not self.is_streaming:
                self.start_streaming()
        except Exception as e:
            self.log.error("set_hardware_triggered_acquisition failed")
            raise e

    def set_pixel_format(self, pixel_format: str):
        pass

    def send_trigger(self):
        print("sending trigger")
        try:
            self.cam.sw_trigger()
        except Exception as e:
            self.log.error(f"sending trigger failed: {e}")

    def read_frame(self) -> np.ndarray:
        print("reading frame")
        try:
            frame, _, _ = self.cam.poll_frame()
            data = frame["pixel_data"]
            return data
        except Exception as e:
            self.log.error(f"poll frame interrupted: {e}")
            return None

    def start_streaming(self):
        print("starting streaming")
        if self.is_streaming:
            return
        self.cam.start_live()
        self.is_streaming = True

    def stop_streaming(self):
        if self.callback_is_enabled:
            self.disable_callback()
        self.cam.finish()
        self.is_streaming = False

    def set_ROI(self, offset_x=None, offset_y=None, width=None, height=None):
        pass

    def calculate_strobe_delay(self):
        # Line time (us) from the manual:
        # Dynamic Range Mode: 3.75; Speed Mode: 0.625; Sensitivity Mode: 3.53125; Sub-Electron Mode: 60.1
        self.strobe_delay_us = int(3.75 * 2760)  # us
        # TODO: trigger delay, line delay


class Camera_Simulation(object):
    def __init__(self, sn=None, is_global_shutter=False, rotate_image_angle=None, flip_image=None):
        pvc.init_pvcam()
        self.cam = None

        self.exposure_time = 1  # ms
        self.analog_gain = 0
        self.is_streaming = False
        self.pixel_format = None
        self.is_color = False

        self.frame_ID = -1
        self.frame_ID_software = -1
        self.frame_ID_offset_hardware_trigger = 0
        self.timestamp = 0
        self.trigger_mode = None

        self.strobe_delay_us = None

        self.image_locked = False
        self.current_frame = None
        self.callback_is_enabled = False
        self.new_image_callback_external = None
        self.stop_waiting = False

        self.GAIN_MAX = 0
        self.GAIN_MIN = 0
        self.GAIN_STEP = 0
        self.EXPOSURE_TIME_MS_MIN = 0.01
        self.EXPOSURE_TIME_MS_MAX = 10000

        self.rotate_image_angle = rotate_image_angle
        self.flip_image = flip_image
        self.is_global_shutter = is_global_shutter
        self.sn = sn

        self.ROI_offset_x = 0
        self.ROI_offset_y = 0
        self.ROI_width = 3200
        self.ROI_height = 3200

        self.OffsetX = 0
        self.OffsetY = 0
        self.Width = 3200
        self.Height = 3200

        self.WidthMax = 3200
        self.HeightMax = 3200

        self.new_image_callback_external = None

    def open(self, index=0):
        pass

    def set_callback(self, function):
        self.new_image_callback_external = function

    def enable_callback(self):
        self.callback_is_enabled = True

    def disable_callback(self):
        self.callback_is_enabled = False

    def open_by_sn(self, sn):
        pass

    def close(self):
        pass

    def set_exposure_time(self, exposure_time):
        pass

    def set_analog_gain(self, analog_gain):
        pass

    def start_streaming(self):
        self.frame_ID_software = 0

    def stop_streaming(self):
        pass

    def set_pixel_format(self, pixel_format):
        self.pixel_format = pixel_format
        print(pixel_format)
        self.frame_ID = 0

    def set_continuous_acquisition(self):
        pass

    def set_software_triggered_acquisition(self):
        pass

    def set_hardware_triggered_acquisition(self):
        pass

    def send_trigger(self):
        print("send trigger")
        self.frame_ID = self.frame_ID + 1
        self.timestamp = time.time()
        if self.frame_ID == 1:
            if self.pixel_format == "MONO8":
                self.current_frame = np.random.randint(255, size=(2000, 2000), dtype=np.uint8)
                self.current_frame[901:1100, 901:1100] = 200
            elif self.pixel_format == "MONO16":
                self.current_frame = np.random.randint(65535, size=(2000, 2000), dtype=np.uint16)
                self.current_frame[901:1100, 901:1100] = 200 * 256
        else:
            self.current_frame = np.roll(self.current_frame, 10, axis=0)
            pass
            # self.current_frame = np.random.randint(255,size=(768,1024),dtype=np.uint8)
        if self.new_image_callback_external is not None and self.callback_is_enabled:
            self.new_image_callback_external(self)

    def read_frame(self):
        return self.current_frame

    def _on_frame_callback(self, user_param, raw_image):
        pass

    def set_ROI(self, offset_x=None, offset_y=None, width=None, height=None):
        pass
