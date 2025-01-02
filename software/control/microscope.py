import serial
import time
from enum import Enum

from PyQt5.QtCore import QObject

import control.core.core as core
from control._def import *
import control
from squid.abc import AbstractStage

if CAMERA_TYPE == "Toupcam":
    import control.camera_toupcam as camera
import control.microcontroller as microcontroller
import control.serial_peripherals as serial_peripherals


class Microscope(QObject):

    def __init__(self, stage: AbstractStage, microscope=None, is_simulation=False):
        super().__init__()
        self.stage = stage
        if microscope is None:
            self.initialize_camera(is_simulation=is_simulation)
            self.initialize_microcontroller(is_simulation=is_simulation)
            self.initialize_core_components()
            self.initialize_peripherals()
        else:
            self.camera = microscope.camera
            self.microcontroller = microscope.microcontroller
            self.configurationManager = microscope.microcontroller
            self.objectiveStore = microscope.objectiveStore
            self.streamHandler = microscope.streamHandler
            self.liveController = microscope.liveController
            self.autofocusController = microscope.autofocusController
            self.slidePositionController = microscope.slidePositionController
            if USE_ZABER_EMISSION_FILTER_WHEEL:
                self.emission_filter_wheel = microscope.emission_filter_wheel
            elif USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
                self.emission_filter_wheel = microscope.emission_filter_wheel

    def initialize_camera(self, is_simulation):
        if is_simulation:
            self.camera = camera.Camera_Simulation(rotate_image_angle=ROTATE_IMAGE_ANGLE, flip_image=FLIP_IMAGE)
        else:
            sn_camera_main = camera.get_sn_by_model(MAIN_CAMERA_MODEL)
            self.camera = camera.Camera(sn=sn_camera_main, rotate_image_angle=ROTATE_IMAGE_ANGLE, flip_image=FLIP_IMAGE)

        self.camera.open()
        self.camera.set_pixel_format(DEFAULT_PIXEL_FORMAT)
        self.camera.set_software_triggered_acquisition()

    def initialize_microcontroller(self, is_simulation):
        if is_simulation:
            self.microcontroller = microcontroller.Microcontroller(existing_serial=control.microcontroller.SimSerial())
        else:
            self.microcontroller = microcontroller.Microcontroller(version=CONTROLLER_VERSION, sn=CONTROLLER_SN)

        self.home_x_and_y_separately = False

    def initialize_core_components(self):
        self.configurationManager = core.ConfigurationManager(filename="./channel_configurations.xml")
        self.objectiveStore = core.ObjectiveStore()
        self.streamHandler = core.StreamHandler(display_resolution_scaling=DEFAULT_DISPLAY_CROP / 100)
        self.liveController = core.LiveController(self.camera, self.microcontroller, self.configurationManager, self)
        self.autofocusController = core.AutoFocusController(
            self.camera, self.stage, self.liveController, self.microcontroller
        )
        self.slidePositionController = core.SlidePositionController(self.stage, self.liveController)

    def initialize_peripherals(self):
        if USE_ZABER_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel = serial_peripherals.FilterController(
                FILTER_CONTROLLER_SERIAL_NUMBER, 115200, 8, serial.PARITY_NONE, serial.STOPBITS_ONE
            )
            self.emission_filter_wheel.start_homing()
        elif USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel = serial_peripherals.Optospin(SN=FILTER_CONTROLLER_SERIAL_NUMBER)
            self.emission_filter_wheel.set_speed(OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ)

    def set_channel(self, channel):
        self.liveController.set_channel(channel)

    def acquire_image(self):
        # turn on illumination and send trigger
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_on_illumination()
            self.waitForMicrocontroller()
            self.camera.send_trigger()
        elif self.liveController.trigger_mode == TriggerMode.HARDWARE:
            self.microcontroller.send_hardware_trigger(
                control_illumination=True, illumination_on_time_us=self.camera.exposure_time * 1000
            )

        # read a frame from camera
        image = self.camera.read_frame()
        if image is None:
            print("self.camera.read_frame() returned None")

        # tunr off the illumination if using software trigger
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_off_illumination()

        return image

    def home_xyz(self):
        if HOMING_ENABLED_Z:
            self.stage.home(x=False, y=False, z=True, theta=False)
        if HOMING_ENABLED_X and HOMING_ENABLED_Y:
            self.stage.move_x(20)
            self.stage.home(x=False, y=True, z=False, theta=False)
            self.stage.home(x=True, y=False, z=False, theta=False)
            self.slidePositionController.homing_done = True

    def move_x(self, distance, blocking=True):
        self.stage.move_x(distance, blocking=blocking)

    def move_y(self, distance, blocking=True):
        self.stage.move_y(distance, blocking=blocking)

    def move_x_to(self, position, blocking=True):
        self.stage.move_x_to(position, blocking=blocking)

    def move_y_to(self, position, blocking=True):
        self.stage.move_y_to(position, blocking=blocking)

    def get_x(self):
        return self.stage.get_pos().x_mm

    def get_y(self):
        return self.stage.get_pos().y_mm

    def get_z(self):
        return self.stage.get_pos().z_mm

    def move_z_to(self, z_mm, blocking=True):
        self.stage.move_z_to(z_mm, blocking=blocking)
        clear_backlash = z_mm >= self.stage.get_pos().z_mm
        # clear backlash if moving backward in open loop mode
        if blocking and clear_backlash:
            distance_to_clear_backlash = self.stage.get_config().Z_AXIS.convert_to_real_units(
                max(160, 20 * self.stage.get_config().Z_AXIS.MICROSTEPS_PER_STEP)
            )
            self.stage.move_z(-distance_to_clear_backlash)
            self.stage.move_z(distance_to_clear_backlash)

    def start_live(self):
        self.camera.start_streaming()
        self.liveController.start_live()

    def stop_live(self):
        self.liveController.stop_live()
        self.camera.stop_streaming()

    def waitForMicrocontroller(self, timeout=5.0, error_message=None):
        try:
            self.microcontroller.wait_till_operation_is_completed(timeout)
        except TimeoutError as e:
            self.log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self):
        self.stop_live()
        self.camera.close()
        self.microcontroller.close()
        if USE_ZABER_EMISSION_FILTER_WHEEL or USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel.close()


class LightSourceType(Enum):
    SquidLED = 0
    SquidLaser = 1
    LDI = 2
    CELESTA = 3
    VersaLase = 4
    SCI = 5


class IntensityControlMode(Enum):
    SquidControllerDAC = 0
    Software = 1


class ShutterControlMode(Enum):
    TTL = 0
    Software = 1


class IlluminationController:
    def __init__(
        self, microcontroller, intensity_control_mode, shutter_control_mode, light_source_type=None, light_source=None
    ):
        self.microcontroller = microcontroller
        self.intensity_control_mode = intensity_control_mode
        self.shutter_control_mode = shutter_control_mode
        self.light_source_type = light_source_type
        self.light_source = light_source
        self.channel_mappings_TTL = {
            405: 11,
            470: 12,
            488: 12,
            545: 14,
            550: 14,
            555: 14,
            561: 14,
            638: 13,
            640: 13,
            730: 15,
            735: 15,
            750: 15,
        }

        self.channel_mappings_software = {}
        self.is_on = {}
        self.intensity_settings = {}
        self.pmin, self.pmax = 0, 1000
        self.current_channel = None

        if self.light_source_type is not None:
            self.configure_light_source()

    def configure_light_source(self):
        self.light_source.initialize()
        self.set_intensity_control_mode(self.intensity_control_mode)
        self.set_shutter_control_mode(self.shutter_control_mode)
        self.channel_mappings_software = self.light_source.channel_mappings
        self.get_intensity_range()
        for ch in self.channel_mappings_software:
            self.intensity_settings[ch] = self.get_intensity(ch)
            self.is_on[ch] = self.light_source.get_shutter_state(self.channel_mappings_software[ch])

    def set_intensity_control_mode(self, mode):
        self.light_source.set_intensity_control_mode(mode)
        self.intensity_control_mode = mode

    def get_intensity_control_mode(self):
        mode = self.light_source.get_intensity_control_mode()
        if mode is not None:
            self.intensity_control_mode = mode
            return mode

    def set_shutter_control_mode(self, mode):
        self.light_source.set_shutter_control_mode(mode)
        self.shutter_control_mode = mode

    def get_shutter_control_mode(self):
        mode = self.light_source.get_shutter_control_mode()
        if mode is not None:
            self.shutter_control_mode = mode
            return mode

    def get_intensity_range(self, channel=None):
        if self.intensity_control_mode == IntensityControlMode.Software:
            [self.pmin, self.pmax] = self.light_source.get_intensity_range()

    def get_intensity(self, channel):
        if self.intensity_control_mode == IntensityControlMode.Software:
            power = self.light_source.get_intensity(self.channel_mappings_software[channel])
            intensity = power / self.pmax * 100
            self.intensity_settings[channel] = intensity
            return intensity  # 0 - 100

    def turn_on_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=True)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            print("TTL!!")
            # self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], self.intensity_settings[channel])
            self.microcontroller.turn_on_illumination()

        self.is_on[channel] = True

    def turn_off_illumination(self, channel=None):
        if channel is None:
            channel = self.current_channel

        if self.shutter_control_mode == ShutterControlMode.Software:
            self.light_source.set_shutter_state(self.channel_mappings_software[channel], on=False)
        elif self.shutter_control_mode == ShutterControlMode.TTL:
            self.microcontroller.turn_off_illumination()

        self.is_on[channel] = False

    def set_current_channel(self, channel):
        self.current_channel = channel

    def set_intensity(self, channel, intensity):
        if self.intensity_control_mode == IntensityControlMode.Software:
            if intensity != self.intensity_settings[channel]:
                power = intensity / 100 * self.pmax
                self.light_source.set_intensity(self.channel_mappings_software[channel], power)
                self.intensity_settings[channel] = intensity
        self.microcontroller.set_illumination(self.channel_mappings_TTL[channel], intensity)

    def get_shutter_state(self):
        return self.is_on

    def close(self):
        self.light_source.shut_down()
