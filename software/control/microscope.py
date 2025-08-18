import serial
from typing import Optional, TypeVar

import control._def
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.configuration_mananger import ConfigurationManager
from control.core.contrast_manager import ContrastManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.core.live_controller import LiveController
from control.core.objective_store import ObjectiveStore
from control.core.stream_handler import StreamHandler, StreamHandlerFunctions, NoOpStreamHandlerFunctions
from control.filterwheel import SquidFilterWheelWrapper

from control.lighting import LightSourceType, IntensityControlMode, ShutterControlMode, IlluminationController
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from control.serial_peripherals import SciMicroscopyLEDArray
from squid.abc import CameraAcquisitionMode, AbstractCamera, AbstractStage
from squid.stage.cephla import CephlaStage
from squid.stage.prior import PriorStage
import control.celesta
import control.filterwheel as filterwheel
import control.microcontroller
import control.serial_peripherals as serial_peripherals
import squid.camera.utils
import squid.config
import squid.logging
import squid.stage.cephla
import squid.stage.utils

if control._def.USE_XERYON:
    from control.objective_changer_2_pos_controller import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )
else:
    ObjectiveChanger2PosController = TypeVar("ObjectiveChanger2PosController")

if control._def.RUN_FLUIDICS:
    from control.fluidics import Fluidics
else:
    Fluidics = TypeVar("Fluidics")

if control._def.ENABLE_NL5:
    import control.NL5 as NL5
else:
    NL5 = TypeVar("NL5")


class MicroscopeAddons:
    @staticmethod
    def build_from_global_config(
        stage: AbstractStage, micro: Optional[Microcontroller], simulated: bool = False
    ) -> "MicroscopeAddons":

        xlight = None
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL:
            xlight = (
                serial_peripherals.XLight(control._def.XLIGHT_SERIAL_NUMBER, control._def.XLIGHT_SLEEP_TIME_FOR_WHEEL)
                if not simulated
                else serial_peripherals.XLight_Simulation()
            )

        nl5 = None
        if control._def.ENABLE_NL5:
            nl5 = NL5.NL5() if not simulated else NL5.NL5_Simulation()

        cellx = None
        if control._def.ENABLE_CELLX:
            cellx = (
                serial_peripherals.CellX(control._def.CELLX_SN)
                if not simulated
                else serial_peripherals.CellX_Simulation()
            )

        emission_filter_wheel = None
        if control._def.USE_ZABER_EMISSION_FILTER_WHEEL:
            emission_filter_wheel = (
                serial_peripherals.FilterController(
                    control._def.FILTER_CONTROLLER_SERIAL_NUMBER, 115200, 8, serial.PARITY_NONE, serial.STOPBITS_ONE
                )
                if not simulated
                else serial_peripherals.FilterController_Simulation(115200, 8, serial.PARITY_NONE, serial.STOPBITS_ONE)
            )
        elif control._def.USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            emission_filter_wheel = (
                serial_peripherals.Optospin(SN=control._def.FILTER_CONTROLLER_SERIAL_NUMBER)
                if not simulated
                else serial_peripherals.Optospin_Simulation(SN=None)
            )

        squid_filter_wheel = None
        if control._def.USE_SQUID_FILTERWHEEL:
            squid_filter_wheel = (
                filterwheel.SquidFilterWheelWrapper(microcontroller=micro)
                if not simulated
                else filterwheel.SquidFilterWheelWrapper_Simulation(None)
            )

        objective_changer = None
        if control._def.USE_XERYON:
            objective_changer = (
                ObjectiveChanger2PosController(sn=control._def.XERYON_SERIAL_NUMBER, stage=stage)
                if not simulated
                else ObjectiveChanger2PosController_Simulation(sn=control._def.XERYON_SERIAL_NUMBER, stage=stage)
            )

        camera_focus = None
        if control._def.SUPPORT_LASER_AUTOFOCUS:
            camera_focus = squid.camera.utils.get_camera(
                squid.config.get_autofocus_camera_config(), simulated=simulated
            )

        fluidics = None
        if control._def.RUN_FLUIDICS:
            fluidics = Fluidics(config_path=control._def.FLUIDICS_CONFIG_PATH, simulation=simulated)

        piezo_stage = None
        if control._def.HAS_OBJECTIVE_PIEZO:
            if not micro:
                raise ValueError("Cannot create PiezoStage without a Microcontroller.")
            piezo_stage = PiezoStage(
                microcontroller=micro,
                config={
                    "OBJECTIVE_PIEZO_HOME_UM": control._def.OBJECTIVE_PIEZO_HOME_UM,
                    "OBJECTIVE_PIEZO_RANGE_UM": control._def.OBJECTIVE_PIEZO_RANGE_UM,
                    "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": control._def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
                    "OBJECTIVE_PIEZO_FLIP_DIR": control._def.OBJECTIVE_PIEZO_FLIP_DIR,
                },
            )

        sci_microscopy_led_array = None
        if control._def.SUPPORT_SCIMICROSCOPY_LED_ARRAY:
            # to do: add error handling
            sci_microscopy_led_array = serial_peripherals.SciMicroscopyLEDArray(
                control._def.SCIMICROSCOPY_LED_ARRAY_SN,
                control._def.SCIMICROSCOPY_LED_ARRAY_DISTANCE,
                control._def.SCIMICROSCOPY_LED_ARRAY_TURN_ON_DELAY,
            )
            sci_microscopy_led_array.set_NA(control._def.SCIMICROSCOPY_LED_ARRAY_DEFAULT_NA)

        return MicroscopeAddons(
            xlight,
            nl5,
            cellx,
            emission_filter_wheel,
            squid_filter_wheel,
            objective_changer,
            camera_focus,
            fluidics,
            piezo_stage,
            sci_microscopy_led_array,
        )

    def __init__(
        self,
        xlight: Optional[serial_peripherals.XLight] = None,
        nl5: Optional[NL5] = None,
        cellx: Optional[serial_peripherals.CellX] = None,
        emission_filter_wheel: Optional[serial_peripherals.Optospin | serial_peripherals.FilterController] = None,
        filter_wheel: Optional[SquidFilterWheelWrapper] = None,
        objective_changer: Optional[ObjectiveChanger2PosController] = None,
        camera_focus: Optional[AbstractCamera] = None,
        fluidics: Optional[Fluidics] = None,
        piezo_stage: Optional[PiezoStage] = None,
        sci_microscopy_led_array: Optional[SciMicroscopyLEDArray] = None,
    ):
        self.xlight: Optional[serial_peripherals.XLight] = xlight
        self.nl5: Optional[NL5] = nl5
        self.cellx: Optional[serial_peripherals.CellX] = cellx
        self.emission_filter_wheel = emission_filter_wheel
        self.filter_wheel = filter_wheel
        self.objective_changer = objective_changer
        self.camera_focus: Optional[AbstractCamera] = camera_focus
        self.fluidics = fluidics
        self.piezo_stage = piezo_stage
        self.sci_microscopy_led_array = sci_microscopy_led_array

    def prepare_for_use(self):
        """
        Prepare all the addon hardware for immediate use.

        NOTE(imo): The emission_filter wheel is confusing - there's no base class but it can be multiple
        different variants with different methods.  For now leave as is just to make progress, but
        we need to fix this.
        """
        if control._def.USE_ZABER_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel.start_homing()
        if control._def.USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel.set_speed(control._def.OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ)


class LowLevelDrivers:
    @staticmethod
    def build_from_global_config(simulated: bool = False) -> "LowLevelDrivers":
        micro_serial_device = (
            control.microcontroller.get_microcontroller_serial_device(
                version=control._def.CONTROLLER_VERSION, sn=control._def.CONTROLLER_SN
            )
            if not simulated
            else control.microcontroller.get_microcontroller_serial_device(simulated=True)
        )
        micro = control.microcontroller.Microcontroller(serial_device=micro_serial_device)

        return LowLevelDrivers(microcontroller=micro)

    def __init__(self, microcontroller: Optional[Microcontroller] = None):
        self.microcontroller: Optional[Microcontroller] = microcontroller

    def prepare_for_use(self):
        pass


class Microscope:
    @staticmethod
    def build_from_global_config(simulated: bool = False):
        low_level_devices = LowLevelDrivers.build_from_global_config(simulated)

        stage_config = squid.config.get_stage_config()
        if control._def.USE_PRIOR_STAGE:
            stage = PriorStage(sn=control._def.PRIOR_STAGE_SN, stage_config=stage_config)
        else:
            if low_level_devices.microcontroller is None:
                raise ValueError("For a cephla stage microscope, you must provide a microcontroller.")
            stage = CephlaStage(low_level_devices.microcontroller, stage_config)

        addons = MicroscopeAddons.build_from_global_config(
            stage, low_level_devices.microcontroller, simulated=simulated
        )

        cam_trigger_log = squid.logging.get_logger("camera hw functions")

        def acquisition_camera_hw_trigger_fn(illumination_time: Optional[float]) -> bool:
            # NOTE(imo): If this succeeds, it means we sent the request,
            # but we didn't necessarily get confirmation of success.
            if addons.nl5 and control._def.NL5_USE_DOUT:
                addons.nl5.start_acquisition()
            else:
                illumination_time_us = 1000.0 * illumination_time if illumination_time else 0
                cam_trigger_log.debug(
                    f"Sending hw trigger with illumination_time={illumination_time_us if illumination_time else None} [us]"
                )
                low_level_devices.microcontroller.send_hardware_trigger(
                    True if illumination_time else False, illumination_time_us
                )
            return True

        def acquisition_camera_hw_strobe_delay_fn(strobe_delay_ms: float) -> bool:
            strobe_delay_us = int(1000 * strobe_delay_ms)
            cam_trigger_log.debug(f"Setting microcontroller strobe delay to {strobe_delay_us} [us]")
            low_level_devices.microcontroller.set_strobe_delay_us(strobe_delay_us)
            low_level_devices.microcontroller.wait_till_operation_is_completed()

            return True

        camera = squid.camera.utils.get_camera(
            config=squid.config.get_camera_config(),
            simulated=simulated,
            hw_trigger_fn=acquisition_camera_hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=acquisition_camera_hw_strobe_delay_fn,
        )

        if control._def.USE_LDI_SERIAL_CONTROL and not simulated:
            ldi = serial_peripherals.LDI()

            illumination_controller = IlluminationController(
                low_level_devices.microcontroller, ldi.intensity_mode, ldi.shutter_mode, LightSourceType.LDI, ldi
            )
        elif control._def.USE_CELESTA_ETHENET_CONTROL and not simulated:
            celesta = control.celesta.CELESTA()
            illumination_controller = IlluminationController(
                low_level_devices.microcontroller,
                IntensityControlMode.Software,
                ShutterControlMode.TTL,
                LightSourceType.CELESTA,
                celesta,
            )
        else:
            illumination_controller = IlluminationController(low_level_devices.microcontroller)

        return Microscope(
            stage=stage,
            camera=camera,
            illumination_controller=illumination_controller,
            addons=addons,
            low_level_drivers=low_level_devices,
            simulated=simulated,
        )

    def __init__(
        self,
        stage: AbstractStage,
        camera: AbstractCamera,
        illumination_controller: IlluminationController,
        addons: MicroscopeAddons,
        low_level_drivers: LowLevelDrivers,
        stream_handler_callbacks: Optional[StreamHandlerFunctions] = NoOpStreamHandlerFunctions,
        simulated: bool = False,
        skip_prepare_for_use: bool = False,
    ):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)

        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self.illumination_controller: IlluminationController = illumination_controller

        self.addons = addons
        self.low_level_drivers = low_level_drivers

        self._simulated = simulated

        self.objective_store: ObjectiveStore = ObjectiveStore()
        self.channel_configuration_manager: ChannelConfigurationManager = ChannelConfigurationManager()
        self.laser_af_settings_manager: Optional[LaserAFSettingManager] = None
        if control._def.SUPPORT_LASER_AUTOFOCUS:
            self.laser_af_settings_manager = LaserAFSettingManager()

        self.configuration_manager: ConfigurationManager = ConfigurationManager(
            self.channel_configuration_manager, self.laser_af_settings_manager
        )
        self.contrast_manager: ContrastManager = ContrastManager()
        self.stream_handler: StreamHandler = StreamHandler(handler_functions=stream_handler_callbacks)

        self.stream_handler_focus: Optional[StreamHandler] = None
        self.live_controller_focus: Optional[LiveController] = None
        if self.addons.camera_focus:
            self.stream_handler_focus = StreamHandler(handler_functions=NoOpStreamHandlerFunctions)
            self.live_controller_focus = LiveController(
                microscope=self,
                camera=self.addons.camera_focus,
                control_illumination=False,
                for_displacement_measurement=True,
            )

        self.live_controller: LiveController = LiveController(microscope=self, camera=self.camera)

        if not skip_prepare_for_use:
            self._prepare_for_use()

    def _prepare_for_use(self):
        self.low_level_drivers.prepare_for_use()
        self.addons.prepare_for_use()

        self.camera.set_pixel_format(
            squid.config.CameraPixelFormat.from_string(control._def.CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT)
        )
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        if self.addons.camera_focus:
            self.addons.camera_focus.set_pixel_format(squid.config.CameraPixelFormat.from_string("MONO8"))
            self.addons.camera_focus.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    def update_camera_functions(self, functions: StreamHandlerFunctions):
        self.stream_handler.set_functions(functions)

    def update_camera_focus_functions(self, functions: StreamHandlerFunctions):
        if not self.addons.camera_focus:
            raise ValueError("No focus camera, cannot change its stream handler functions.")

        self.stream_handler_focus.set_functions(functions)

    def initialize_core_components(self):
        if self.addons.piezo_stage:
            self.addons.piezo_stage.home()

    def setup_hardware(self):
        self.camera.add_frame_callback(self.stream_handler.on_new_frame)
        self.camera.enable_callbacks(True)

        if self.addons.camera_focus:
            self.addons.camera_focus.add_frame_callback(self.stream_handler_focus.on_new_frame)
            self.addons.camera_focus.enable_callbacks(True)
            self.addons.camera_focus.start_streaming()

    def acquire_image(self):
        # turn on illumination and send trigger
        if self.live_controller.trigger_mode == control._def.TriggerMode.SOFTWARE:
            self.live_controller.turn_on_illumination()
            self.waitForMicrocontroller()
            self.camera.send_trigger()
        elif self.live_controller.trigger_mode == control._def.TriggerMode.HARDWARE:
            self.low_level_drivers.microcontroller.send_hardware_trigger(
                control_illumination=True, illumination_on_time_us=self.camera.get_exposure_time() * 1000
            )

        # read a frame from camera
        image = self.camera.read_frame()
        if image is None:
            print("self.camera.read_frame() returned None")

        # turn off the illumination if using software trigger
        if self.live_controller.trigger_mode == control._def.TriggerMode.SOFTWARE:
            self.live_controller.turn_off_illumination()

        return image

    def home_xyz(self):
        if control._def.HOMING_ENABLED_Z:
            self.stage.home(x=False, y=False, z=True, theta=False)
        if control._def.HOMING_ENABLED_X and control._def.HOMING_ENABLED_Y:
            # The plate clamp actuation post can get in the way of homing if we start with
            # the stage in "just the wrong" position.  Blindly moving the Y out 20, then home x
            # and move x over 20 , guarantees we'll clear the post for homing.  If we are <20mm
            # from the end travel of either axis, we'll just stop at the extent without consequence.
            #
            # The one odd corner case is if the system gets shut down in the loading position.
            # in that case, we drive off of the loading position and the clamp closes quickly.
            # This doesn't seem to cause problems, and there isn't a clean way to avoid the corner
            # case.
            self._log.info("Moving y+20, then x->home->+50 to make sure system is clear for homing.")
            self.stage.move_y(20)
            self.stage.home(x=True, y=False, z=False, theta=False)
            self.stage.move_x(50)

            self._log.info("Homing the Y axis...")
            self.stage.home(x=False, y=True, z=False, theta=False)

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
        self.stage.move_z_to(z_mm)

    def start_live(self):
        self.camera.start_streaming()
        self.live_controller.start_live()

    def stop_live(self):
        self.live_controller.stop_live()
        self.camera.stop_streaming()

    def waitForMicrocontroller(self, timeout=5.0, error_message=None):
        try:
            self.low_level_drivers.microcontroller.wait_till_operation_is_completed(timeout)
        except TimeoutError as e:
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self):
        self.stop_live()
        self.low_level_drivers.microcontroller.close()
        if self.addons.emission_filter_wheel:
            self.addons.emission_filter_wheel.close()
        if self.addons.camera_focus:
            self.addons.camera_focus.close()
        self.camera.close()

    def move_to_position(self, x, y, z):
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective):
        self.objective_store.set_current_objective(objective)

    def set_illumination_intensity(self, channel, intensity, objective=None):
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self.channel_configuration_manager.get_channel_configuration_by_name(objective, channel)
        channel_config.illumination_intensity = intensity
        self.live_controller.set_microscope_mode(channel_config)

    def set_exposure_time(self, channel, exposure_time, objective=None):
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self.channel_configuration_manager.get_channel_configuration_by_name(objective, channel)
        channel_config.exposure_time = exposure_time
        self.live_controller.set_microscope_mode(channel_config)
