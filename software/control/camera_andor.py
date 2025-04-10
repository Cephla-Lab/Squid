import time
import numpy as np
import threading
import os

import pyAndorSDK3
from pyAndorSDK3 import AndorSDK3
from control._def import *


# For using in Windows only
package_path = os.path.dirname(pyAndorSDK3.__file__)
library_path = os.path.join(package_path, "libs", "Windows", "64")
pyAndorSDK3.utils.add_library_path(library_path)


def get_sn_by_model(model_name):
    pass


class Camera(object):
    def __init__(
        self, sn=None, resolution=(2048, 2048), is_global_shutter=False, rotate_image_angle=None, flip_image=None
    ):
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
        self.line_rate = None  # in us
        
        self.image_locked = False
        self.current_frame = None
        self.callback_is_enabled = False
        self.new_image_callback_external = None
        self.stop_waiting = False
        
        self.GAIN_MAX = 0
        self.GAIN_MIN = 0
        self.GAIN_STEP = 0
        self.EXPOSURE_TIME_MS_MIN = 0.01
        self.EXPOSURE_TIME_MS_MAX = 10000.0
        
        self.rotate_image_angle = rotate_image_angle
        self.flip_image = flip_image
        self.is_global_shutter = is_global_shutter

        self.ROI_offset_x = 0
        self.ROI_offset_y = 0
        self.ROI_width = resolution[0]
        self.ROI_height = resolution[1]
        
        self.OffsetX = 0
        self.OffsetY = 0
        self.Width = resolution[0]
        self.Height = resolution[1]
        
        self.WidthMax = resolution[0]
        self.HeightMax = resolution[1]

    def open(self, index=0):
        sdk3 = AndorSDK3()
        self.cam = sdk3.GetCamera(index)
        self.cam.open()
        self._initialize_camera()
        print(f"Andor Camera opened. SN: {self.cam.SerialNumber}")
        return True

    def open_by_sn(self, sn):
        self.open()

    def close(self):
        if self.is_streaming:
            self.stop_streaming()
        
        self.disable_callback()
        
        if self.cam is not None:
            self.cam.close()
            self.cam = None
            return True
        return False
        

    def _initialize_camera(self):
        if self.cam is None:
            return
        # Get exposure time limits
        try:
            self.EXPOSURE_TIME_MS_MIN = self.cam.min_ExposureTime * 1000  # convert to ms
            self.EXPOSURE_TIME_MS_MAX = self.cam.max_ExposureTime * 1000  # convert to ms
        except:
            print("Could not determine exposure time limits")

        try:
            self.line_rate = self.cam.LineScanSpeed
        except:
            print("Could not determine line rate")
            raise

    def set_callback(self, function):
        self.new_image_callback_external = function

    def enable_callback(self):
        if self.callback_is_enabled:
            return

        if not self.is_streaming:
            self.start_streaming()

        self.stop_waiting = False
        self.callback_thread = threading.Thread(target=self._wait_and_callback)
        self.callback_thread.start()

        self.callback_is_enabled = True

    def _wait_and_callback(self):
        while True:
            if self.stop_waiting:
                break
            try:
                # Wait for a new frame with a timeout
                image = self.read_frame()
                if image is not False:
                    self._on_new_frame(image)
            except Exception as e:
                print(f"Error waiting for frame: {e}")
                time.sleep(0.01)  # Prevent tight loop on error

    def _on_new_frame(self, image):
        if self.image_locked:
            print("Last image is still being processed; a frame is dropped")
            return

        self.current_frame = image

        self.frame_ID_software += 1
        self.frame_ID += 1

        # Frame ID for hardware triggered acquisition
        if self.trigger_mode == TriggerMode.HARDWARE:
            if self.frame_ID_offset_hardware_trigger is None:
                self.frame_ID_offset_hardware_trigger = self.frame_ID
            self.frame_ID = self.frame_ID - self.frame_ID_offset_hardware_trigger

        self.timestamp = time.time()
        self.new_image_callback_external(self)

    def disable_callback(self):
        if not self.callback_is_enabled:
            return

        was_streaming = self.is_streaming
        if self.is_streaming:
            self.stop_streaming()

        self.stop_waiting = True
        time.sleep(0.2)
        if hasattr(self, 'callback_thread'):
            self.callback_thread.join()
            del self.callback_thread
        self.callback_is_enabled = False

        if was_streaming:
            self.start_streaming()

    def set_analog_gain(self, gain):
        pass

    def set_exposure_time(self, exposure_time):
        try:
            # Convert ms to seconds for Andor SDK
            exposure_time_s = exposure_time / 1000.0
            
            # Limit to valid range
            limited_exposure = max(min(exposure_time_s, self.EXPOSURE_TIME_MS_MAX/1000), 
                                self.EXPOSURE_TIME_MS_MIN/1000)
            
            self.cam.ExposureTime = limited_exposure
            self.exposure_time = exposure_time
        except Exception as e:
            print(f"Error setting exposure time: {e}")
            raise e

    def set_continuous_acquisition(self):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        try:
            self.cam.CycleMode = "Continuous"
            self.cam.TriggerMode = "Internal"
            self.trigger_mode = TriggerMode.CONTINUOUS
        except Exception as e:
            print(f"Error setting continuous acquisition: {e}")

        if was_streaming:
            self.start_streaming()

    def set_software_triggered_acquisition(self):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        try:
            self.cam.CycleMode = "Fixed"
            self.cam.FrameCount = 1
            self.cam.TriggerMode = "Software"
            self.trigger_mode = TriggerMode.SOFTWARE
        except Exception as e:
            print(f"Error setting software triggered acquisition: {e}")

        if was_streaming:
            self.start_streaming()

    def set_hardware_triggered_acquisition(self):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        try:
            self.cam.CycleMode = "Fixed"
            self.cam.FrameCount = 1
            self.cam.TriggerMode = "External"
            self.frame_ID_offset_hardware_trigger = None
            self.trigger_mode = TriggerMode.HARDWARE
        except Exception as e:
            print(f"Error setting hardware triggered acquisition: {e}")

        if was_streaming:
            self.start_streaming()

    def set_pixel_format(self, pixel_format):
        was_streaming = False
        if self.is_streaming:
            was_streaming = True
            self.stop_streaming()

        try:
            result = False
            if pixel_format == "MONO12":
                self.cam.PixelEncoding = "Mono12"
                self.pixel_format = pixel_format
            elif pixel_format == "MONO16":
                self.cam.PixelEncoding = "Mono16"
                self.pixel_format = pixel_format
            else:
                raise ValueError(f"Invalid pixel format: {pixel_format}")
        except Exception as e:
            print(f"Error setting pixel format: {e}")

        if was_streaming:
            self.start_streaming()

    def send_trigger(self):
        try:
            self.cam.SoftwareTrigger()
        except Exception as e:
            print(f"Trigger not sent - error: {e}")

    def read_frame(self, no_wait=False):
        try:
            acq = self.cam.wait_buffer(1000)
            return acq.image
        except Exception as e:
            print(f"Error reading frame: {e}")
            return False

    def start_streaming(self, buffer_frame_num=5):
        if self.is_streaming:
            return
        
        try:
            # Queue buffers based on ImageSizeBytes
            img_size = self.cam.ImageSizeBytes
            for _ in range(buffer_frame_num):
                buf = np.empty((img_size,), dtype='B')
                self.cam.queue(buf, img_size)
                self.buffer_queue.append(buf)  # Keep reference to avoid garbage collection
            
            # Start acquisition
            self.cam.AcquisitionStart()
            self.is_streaming = True
            print("Andor Camera starts streaming")
            return True
        except Exception as e:
            print(f"Andor Camera cannot start streaming: {e}")
            self.is_streaming = False
            return False

    def stop_streaming(self):
        try:
            if self.cam.AcquisitionStop() and self.cam.flush():
                self.buffer_queue = []  # Clear buffer references
                self.is_streaming = False
                print("Andor Camera streaming stopped")
                return True
            else:
                print("Andor Camera cannot stop streaming")
                return False
        except Exception as e:
            print(f"Error stopping streaming: {e}")
            return False

    def set_ROI(self, offset_x=None, offset_y=None, width=None, height=None):
        pass

    def calculate_strobe_delay(self):
        self.strobe_delay_us = int(self.line_rate * 2760)


class Camera_Simulation(object):
    def __init__(self, sn=None, is_global_shutter=False, rotate_image_angle=None, flip_image=None):
        sdk3 = AndorSDK3()
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
        self.ROI_width = 2760
        self.ROI_height = 2760

        self.OffsetX = 0
        self.OffsetY = 0
        self.Width = 2760
        self.Height = 2760

        self.WidthMax = 2760
        self.HeightMax = 2760

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

    def calculate_strobe_delay(self):
        self.strobe_delay_us = 20000
