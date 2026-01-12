from typing import Optional, TypeVar

import numpy as np

import _def
from squid.backend.managers import ChannelConfigurationManager
from squid.backend.managers import ConfigurationManager
from squid.backend.managers import ContrastManager
from squid.backend.controllers.autofocus import LaserAFSettingManager
from squid.backend.controllers.live_controller import LiveController
from squid.backend.managers import ObjectiveStore
from squid.backend.io.stream_handler import (
    StreamHandler,
    StreamHandlerFunctions,
    NoOpStreamHandlerFunctions,
)

from squid.backend.drivers.lighting.led import (
    LightSourceType,
    IntensityControlMode,
    ShutterControlMode,
    IlluminationController,
)
from squid.backend.microcontroller import Microcontroller
from squid.backend.drivers.peripherals.piezo import PiezoStage
from squid.backend.drivers.lighting import SciMicroscopyLEDArray
from squid.core.abc import (
    CameraAcquisitionMode,
    AbstractCamera,
    AbstractStage,
    AbstractFilterWheelController,
)
from squid.core.events import event_bus
from squid.backend.drivers.stages.stage_utils import get_stage
import squid.backend.drivers.lighting.celesta
import squid.backend.drivers.lighting.illumination_andor
import squid.backend.microcontroller
import squid.backend.drivers.lighting as serial_peripherals
import squid.backend.drivers.cameras.camera_utils
import squid.core.config
import squid.backend.drivers.filter_wheels.utils
import squid.core.logging
import squid.backend.drivers.stages.cephla
import squid.backend.drivers.stages.stage_utils

if _def.USE_XERYON:
    from squid.backend.drivers.peripherals.objective_changer import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )
else:
    ObjectiveChanger2PosController = TypeVar("ObjectiveChanger2PosController")

if _def.RUN_FLUIDICS:
    from squid.backend.drivers.fluidics.fluidics import Fluidics
else:
    Fluidics = TypeVar("Fluidics")

if _def.ENABLE_NL5:
    import squid.backend.drivers.peripherals.nl5 as NL5
else:
    NL5 = TypeVar("NL5")


class MicroscopeAddons:
    @staticmethod
    def build_from_global_config(
        stage: AbstractStage, micro: Optional[Microcontroller], simulated: bool = False
    ) -> "MicroscopeAddons":
        from squid.backend.microscope_factory import build_microscope_addons

        return build_microscope_addons(stage, micro, simulated=simulated)

    def __init__(
        self,
        xlight: Optional[serial_peripherals.XLight] = None,
        dragonfly: Optional[serial_peripherals.Dragonfly] = None,
        nl5: Optional[NL5] = None,
        cellx: Optional[serial_peripherals.CellX] = None,
        emission_filter_wheel: Optional[AbstractFilterWheelController] = None,
        objective_changer: Optional[ObjectiveChanger2PosController] = None,
        camera_focus: Optional[AbstractCamera] = None,
        fluidics: Optional[Fluidics] = None,
        piezo_stage: Optional[PiezoStage] = None,
        sci_microscopy_led_array: Optional[SciMicroscopyLEDArray] = None,
    ):
        self.xlight: Optional[serial_peripherals.XLight] = xlight
        self.dragonfly: Optional[serial_peripherals.Dragonfly] = dragonfly
        self.nl5: Optional[NL5] = nl5
        self.cellx: Optional[serial_peripherals.CellX] = cellx
        self.emission_filter_wheel = emission_filter_wheel
        self.objective_changer = objective_changer
        self.camera_focus: Optional[AbstractCamera] = camera_focus
        self.fluidics = fluidics
        self.piezo_stage = piezo_stage
        self.sci_microscopy_led_array = sci_microscopy_led_array

    def prepare_for_use(self) -> None:
        """
        Prepare all the addon hardware for immediate use.
        """
        if self.emission_filter_wheel:
            fw_config = squid.core.config.get_filter_wheel_config()
            self.emission_filter_wheel.initialize(fw_config.indices)
            self.emission_filter_wheel.home()
        if self.piezo_stage:
            self.piezo_stage.home()


class LowLevelDrivers:
    @staticmethod
    def build_from_global_config(simulated: bool = False) -> "LowLevelDrivers":
        from squid.backend.microscope_factory import build_low_level_drivers

        return build_low_level_drivers(simulated=simulated)

    def __init__(self, microcontroller: Optional[Microcontroller] = None):
        self.microcontroller: Optional[Microcontroller] = microcontroller

    def prepare_for_use(self) -> None:
        if self.microcontroller and _def.HAS_OBJECTIVE_PIEZO:
            # Configure DAC gains for objective piezo
            _def.OUTPUT_GAINS.CHANNEL7_GAIN = (
                _def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE == 5
            )
            div = 1 if _def.OUTPUT_GAINS.REFDIV else 0
            gains = sum(
                getattr(_def.OUTPUT_GAINS, f"CHANNEL{i}_GAIN") << i
                for i in range(8)
            )
            self.microcontroller.configure_dac80508_refdiv_and_gain(div, gains)


class Microscope:
    @staticmethod
    def build_from_global_config(
        simulated: bool = False, skip_controller_creation: bool = False
    ) -> "Microscope":
        from squid.backend.microscope_factory import build_microscope

        return build_microscope(
            simulated=simulated,
            skip_controller_creation=skip_controller_creation,
        )

    def __init__(
        self,
        stage: AbstractStage,
        camera: AbstractCamera,
        illumination_controller: IlluminationController,
        addons: MicroscopeAddons,
        low_level_drivers: LowLevelDrivers,
        stream_handler_callbacks: Optional[
            StreamHandlerFunctions
        ] = NoOpStreamHandlerFunctions,
        simulated: bool = False,
        skip_prepare_for_use: bool = False,
        skip_controller_creation: bool = False,
    ):
        super().__init__()
        self._log = squid.core.logging.get_logger(self.__class__.__name__)

        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self.illumination_controller: IlluminationController = illumination_controller

        self.addons = addons
        self.low_level_drivers = low_level_drivers

        self._simulated = simulated

        # These are always created by Microscope (simple data managers)
        self.objective_store: ObjectiveStore = ObjectiveStore()
        self.channel_configuration_manager: ChannelConfigurationManager = (
            ChannelConfigurationManager(configurations_path=_def.PROJECT_ROOT / "configurations")
        )
        self.laser_af_settings_manager: Optional[LaserAFSettingManager] = None
        if _def.SUPPORT_LASER_AUTOFOCUS:
            self.laser_af_settings_manager = LaserAFSettingManager()

        self.configuration_manager: ConfigurationManager = ConfigurationManager(
            self.channel_configuration_manager, self.laser_af_settings_manager
        )
        self.contrast_manager: ContrastManager = ContrastManager()

        # Controllers can be created externally and injected
        # Initialize to None; will be set below or by ApplicationContext
        self.stream_handler: Optional[StreamHandler] = None
        self.stream_handler_focus: Optional[StreamHandler] = None
        self.live_controller_focus: Optional[LiveController] = None
        self.live_controller: Optional[LiveController] = None

        if not skip_controller_creation:
            # Default behavior: create controllers internally
            self._create_controllers(stream_handler_callbacks)

        if not skip_prepare_for_use:
            self._prepare_for_use()

    def _create_controllers(
        self, stream_handler_callbacks: Optional[StreamHandlerFunctions] = None
    ) -> None:
        """Create core non-Qt components internally.

        Controllers are created by `ApplicationContext` so they can be constructed
        with service dependencies and without UI coupling.
        """
        if stream_handler_callbacks is None:
            stream_handler_callbacks = NoOpStreamHandlerFunctions

        self.stream_handler = StreamHandler(handler_functions=stream_handler_callbacks)

        if self.addons.camera_focus:
            self.stream_handler_focus = StreamHandler(
                handler_functions=NoOpStreamHandlerFunctions
            )
        # Live controllers are created externally by ApplicationContext.

    def _prepare_for_use(self) -> None:
        self.low_level_drivers.prepare_for_use()
        self.addons.prepare_for_use()

        self.camera.set_pixel_format(
            squid.core.config.CameraPixelFormat.from_string(
                _def.CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT
            )
        )
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        if self.addons.camera_focus:
            self.addons.camera_focus.set_pixel_format(
                squid.core.config.CameraPixelFormat.from_string("MONO8")
            )
            self.addons.camera_focus.set_acquisition_mode(
                CameraAcquisitionMode.SOFTWARE_TRIGGER
            )

    def update_camera_functions(self, functions: StreamHandlerFunctions) -> None:
        self.stream_handler.set_functions(functions)

    def update_camera_focus_functions(self, functions: StreamHandlerFunctions):
        if not self.addons.camera_focus:
            raise ValueError(
                "No focus camera, cannot change its stream handler functions."
            )

        self.stream_handler_focus.set_functions(functions)

    def initialize_core_components(self) -> None:
        if self.addons.piezo_stage:
            self.addons.piezo_stage.home()

    def setup_hardware(self) -> None:
        self.camera.add_frame_callback(self.stream_handler.on_new_frame)
        self.camera.enable_callbacks(True)

        if self.addons.camera_focus:
            self.addons.camera_focus.add_frame_callback(
                self.stream_handler_focus.on_new_frame
            )
            self.addons.camera_focus.enable_callbacks(True)
            self.addons.camera_focus.start_streaming()

    def acquire_image(self) -> np.ndarray:
        """Acquire a single image from the camera.

        Turns on illumination, triggers the camera, reads the frame, and turns off
        illumination. The trigger mode (software vs hardware) is determined by the
        live controller configuration.

        Returns:
            The acquired image as a numpy array.

        Raises:
            RuntimeError: If the camera fails to return a frame.
        """
        using_software_trigger = self.live_controller.trigger_mode == _def.TriggerMode.SOFTWARE

        # turn on illumination and send trigger
        if using_software_trigger:
            self.live_controller.turn_on_illumination()
            self.waitForMicrocontroller()
            self.camera.send_trigger()
        elif self.live_controller.trigger_mode == _def.TriggerMode.HARDWARE:
            self.low_level_drivers.microcontroller.send_hardware_trigger(
                control_illumination=True,
                illumination_on_time_us=self.camera.get_exposure_time() * 1000,
            )

        try:
            # read a frame from camera
            image = self.camera.read_frame()
            if image is None:
                self._log.error("camera.read_frame() returned None")
                raise RuntimeError("Failed to acquire image: camera.read_frame() returned None")
            return image
        finally:
            # always turn off illumination when using software trigger
            if using_software_trigger:
                self.live_controller.turn_off_illumination()

    def home_xyz(self) -> None:
        if _def.HOMING_ENABLED_Z:
            self.stage.home(x=False, y=False, z=True, theta=False)
        if _def.HOMING_ENABLED_X and _def.HOMING_ENABLED_Y:
            # The plate clamp actuation post can get in the way of homing if we start with
            # the stage in "just the wrong" position.  Blindly moving the Y out 20, then home x
            # and move x over 20 , guarantees we'll clear the post for homing.  If we are <20mm
            # from the end travel of either axis, we'll just stop at the extent without consequence.
            #
            # The one odd corner case is if the system gets shut down in the loading position.
            # in that case, we drive off of the loading position and the clamp closes quickly.
            # This doesn't seem to cause problems, and there isn't a clean way to avoid the corner
            # case.
            self._log.info(
                "Moving y+20, then x->home->+50 to make sure system is clear for homing."
            )
            self.stage.move_y(20)
            self.stage.home(x=True, y=False, z=False, theta=False)
            self.stage.move_x(50)

            self._log.info("Homing the Y axis...")
            self.stage.home(x=False, y=True, z=False, theta=False)

    def move_x(self, distance: float, blocking: bool = True) -> None:
        self.stage.move_x(distance, blocking=blocking)

    def move_y(self, distance: float, blocking: bool = True) -> None:
        self.stage.move_y(distance, blocking=blocking)

    def move_x_to(self, position: float, blocking: bool = True) -> None:
        self.stage.move_x_to(position, blocking=blocking)

    def move_y_to(self, position: float, blocking: bool = True) -> None:
        self.stage.move_y_to(position, blocking=blocking)

    def get_x(self) -> float:
        return self.stage.get_pos().x_mm

    def get_y(self) -> float:
        return self.stage.get_pos().y_mm

    def get_z(self) -> float:
        return self.stage.get_pos().z_mm

    def move_z_to(self, z_mm: float, blocking: bool = True) -> None:
        self.stage.move_z_to(z_mm, blocking=blocking)

    def start_live(self) -> None:
        self.live_controller.start_live()

    def stop_live(self) -> None:
        if self.live_controller is not None:
            self.live_controller.stop_live()

    def waitForMicrocontroller(
        self, timeout: float = 5.0, error_message: Optional[str] = None
    ) -> None:
        try:
            self.low_level_drivers.microcontroller.wait_till_operation_is_completed(
                timeout
            )
        except TimeoutError as e:
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self) -> None:
        self.stop_live()
        self.low_level_drivers.microcontroller.close()
        if self.addons.emission_filter_wheel:
            self.addons.emission_filter_wheel.close()
        if self.addons.camera_focus:
            self.addons.camera_focus.close()
        self.camera.close()

    def move_to_position(self, x: float, y: float, z: float) -> None:
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective: str) -> None:
        self.objective_store.set_current_objective(objective)

    def set_illumination_intensity(
        self, channel: str, intensity: float, objective: Optional[str] = None
    ) -> None:
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = (
            self.channel_configuration_manager.get_channel_configuration_by_name(
                objective, channel
            )
        )
        channel_config.illumination_intensity = intensity
        self.live_controller.set_microscope_mode(channel_config)

    def set_exposure_time(
        self, channel: str, exposure_time: float, objective: Optional[str] = None
    ) -> None:
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = (
            self.channel_configuration_manager.get_channel_configuration_by_name(
                objective, channel
            )
        )
        channel_config.exposure_time = exposure_time
        self.live_controller.set_microscope_mode(channel_config)
