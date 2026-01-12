from pathlib import Path
from typing import Optional

import numpy as np

import control._def
from control.core.config import ConfigRepository
from control.core.contrast_manager import ContrastManager
from control.core.live_controller import LiveController
from control.core.objective_store import ObjectiveStore
from control.core.stream_handler import StreamHandler, StreamHandlerFunctions, NoOpStreamHandlerFunctions

from control.lighting import LightSourceType, IntensityControlMode, ShutterControlMode, IlluminationController
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
from control.serial_peripherals import SciMicroscopyLEDArray
from squid.abc import CameraAcquisitionMode, AbstractCamera, AbstractStage, AbstractFilterWheelController
from squid.stage.cephla import CephlaStage
from squid.stage.prior import PriorStage
import control.celesta
import control.illumination_andor
import control.microcontroller
import control.serial_peripherals as serial_peripherals
import squid.camera.utils
import squid.config
import squid.filter_wheel_controller.utils
import squid.logging
import squid.stage.cephla
import squid.stage.utils

if control._def.USE_XERYON:
    from control.objective_changer_2_pos_controller import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )
else:
    ObjectiveChanger2PosController = None

if control._def.RUN_FLUIDICS:
    from control.fluidics import Fluidics
else:
    Fluidics = None

if control._def.ENABLE_NL5:
    import control.NL5 as NL5
else:
    NL5 = None


class MicroscopeAddons:
    @staticmethod
    def build_from_global_config(
        stage: AbstractStage, micro: Optional[Microcontroller], simulated: bool = False
    ) -> "MicroscopeAddons":

        xlight = None
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL and not control._def.USE_DRAGONFLY:
            # TODO: For user compatibility, when ENABLE_SPINNING_DISK_CONFOCAL is True, we use XLight/Cicero on default.
            # This needs to be changed when we figure out better machine configuration structure.
            xlight = (
                serial_peripherals.XLight(control._def.XLIGHT_SERIAL_NUMBER, control._def.XLIGHT_SLEEP_TIME_FOR_WHEEL)
                if not simulated
                else serial_peripherals.XLight_Simulation()
            )

        dragonfly = None
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL and control._def.USE_DRAGONFLY:
            dragonfly = (
                serial_peripherals.Dragonfly(SN=control._def.DRAGONFLY_SERIAL_NUMBER)
                if not simulated
                else serial_peripherals.Dragonfly_Simulation()
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
        fw_config = squid.config.get_filter_wheel_config()
        if fw_config:
            emission_filter_wheel = squid.filter_wheel_controller.utils.get_filter_wheel_controller(
                fw_config, microcontroller=micro, simulated=simulated
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
            dragonfly,
            nl5,
            cellx,
            emission_filter_wheel,
            objective_changer,
            camera_focus,
            fluidics,
            piezo_stage,
            sci_microscopy_led_array,
        )

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

    def prepare_for_use(self):
        """
        Prepare all the addon hardware for immediate use.
        """
        if self.emission_filter_wheel:
            fw_config = squid.config.get_filter_wheel_config()
            self.emission_filter_wheel.initialize(fw_config.indices)
            self.emission_filter_wheel.home()
        if self.piezo_stage:
            self.piezo_stage.home()


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
        if self.microcontroller and control._def.HAS_OBJECTIVE_PIEZO:
            # Configure DAC gains for objective piezo
            control._def.OUTPUT_GAINS.CHANNEL7_GAIN = control._def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE == 5
            div = 1 if control._def.OUTPUT_GAINS.REFDIV else 0
            gains = sum(getattr(control._def.OUTPUT_GAINS, f"CHANNEL{i}_GAIN") << i for i in range(8))
            self.microcontroller.configure_dac80508_refdiv_and_gain(div, gains)


class Microscope:
    @staticmethod
    def build_from_global_config(simulated: bool = False) -> "Microscope":
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
                    illumination_time is not None, illumination_time_us
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
        elif control._def.USE_CELESTA_ETHERNET_CONTROL and not simulated:
            celesta = control.celesta.CELESTA()
            illumination_controller = IlluminationController(
                low_level_devices.microcontroller,
                IntensityControlMode.Software,
                ShutterControlMode.TTL,
                LightSourceType.CELESTA,
                celesta,
            )
        elif control._def.USE_ANDOR_LASER_CONTROL and not simulated:
            andor_laser = control.illumination_andor.AndorLaser(
                control._def.ANDOR_LASER_VID, control._def.ANDOR_LASER_PID
            )
            illumination_controller = IlluminationController(
                low_level_devices.microcontroller,
                IntensityControlMode.Software,
                ShutterControlMode.TTL,
                LightSourceType.AndorLaser,
                andor_laser,
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
        self._log = squid.logging.get_logger(self.__class__.__name__)

        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self.illumination_controller: IlluminationController = illumination_controller

        self.addons = addons
        self.low_level_drivers = low_level_drivers

        self._simulated = simulated

        self.objective_store: ObjectiveStore = ObjectiveStore()

        # Centralized config management
        self.config_repo: ConfigRepository = ConfigRepository()

        # Note: Migration from acquisition_configurations to user_profiles is handled
        # by run_auto_migration() in main_hcs.py before Microscope is created

        # Load default profile (ensures configs exist)
        profiles = self.config_repo.get_available_profiles()
        if profiles:
            self.config_repo.load_profile(profiles[0])
        else:
            # Create a default profile if none exist - load_profile() will call
            # ensure_default_configs() to generate configs from illumination_channel_config.yaml
            self._log.info("No profiles found, creating 'default' profile")
            self.config_repo.create_profile("default")
            self.config_repo.load_profile("default")

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

        # Sync confocal mode from hardware (must be after LiveController creation)
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL:
            self._sync_confocal_mode_from_hardware()

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

    def _sync_confocal_mode_from_hardware(self) -> bool:
        """Sync confocal mode state from spinning disk hardware.

        Queries the actual hardware state (XLight disk position or Dragonfly modality)
        and updates the live controller accordingly.
        This ensures correct channel settings are used in both GUI and headless modes.

        Returns:
            True if sync was successful, False if hardware query failed.
        """
        confocal_mode = False
        sync_successful = True

        if self.addons.dragonfly is not None:
            try:
                modality = self.addons.dragonfly.get_modality()
                confocal_mode = modality == "CONFOCAL" if modality else False
            except Exception as e:
                self._log.warning(f"Could not query Dragonfly modality: {e}")
                sync_successful = False
        elif self.addons.xlight is not None:
            try:
                # XLight returns 0 for widefield, 1 for confocal
                disk_position = self.addons.xlight.get_disk_position()
                confocal_mode = bool(disk_position)
            except Exception as e:
                self._log.warning(f"Could not query XLight disk position: {e}")
                sync_successful = False

        if sync_successful:
            self.live_controller.sync_confocal_mode_from_hardware(confocal_mode)
        else:
            self._log.warning(
                "Confocal mode could not be synchronized from hardware; " "keeping existing live controller state."
            )
        return sync_successful

    def set_confocal_mode(self, confocal: bool) -> None:
        """Set confocal/widefield mode and move the spinning disk.

        This is the preferred method for headless scripts to switch imaging modes.
        It updates both the hardware and the live controller.

        Args:
            confocal: True for confocal mode, False for widefield mode.

        Raises:
            RuntimeError: If spinning disk confocal is not enabled or hardware unavailable.
        """
        if not control._def.ENABLE_SPINNING_DISK_CONFOCAL:
            raise RuntimeError("Spinning disk confocal is not enabled in configuration")

        if self.addons.dragonfly is not None:
            modality = "CONFOCAL" if confocal else "BF"
            self.addons.dragonfly.set_modality(modality)
        elif self.addons.xlight is not None:
            # XLight: 1 for confocal, 0 for widefield
            self.addons.xlight.set_disk_position(1 if confocal else 0)
        else:
            raise RuntimeError("No spinning disk hardware available")

        self.live_controller.toggle_confocal_widefield(confocal)

    def is_confocal_mode(self) -> bool:
        """Check if currently in confocal mode.

        Returns:
            True if in confocal mode, False if in widefield mode.
        """
        return self.live_controller.is_confocal_mode()

    def update_camera_functions(self, functions: StreamHandlerFunctions) -> None:
        """Update the stream handler callback functions for the main camera.

        Args:
            functions: New callback functions for frame handling.
        """
        self.stream_handler.set_functions(functions)

    def update_camera_focus_functions(self, functions: StreamHandlerFunctions) -> None:
        """Update the stream handler callback functions for the focus camera.

        Args:
            functions: New callback functions for frame handling.

        Raises:
            ValueError: If no focus camera is configured.
        """
        if not self.addons.camera_focus:
            raise ValueError("No focus camera, cannot change its stream handler functions.")

        self.stream_handler_focus.set_functions(functions)

    def initialize_core_components(self) -> None:
        """Initialize and home core hardware components like piezo stage."""
        if self.addons.piezo_stage:
            self.addons.piezo_stage.home()

    def setup_hardware(self) -> None:
        """Set up camera frame callbacks and start streaming for focus camera if present."""
        self.camera.add_frame_callback(self.stream_handler.on_new_frame)
        self.camera.enable_callbacks(True)

        if self.addons.camera_focus:
            self.addons.camera_focus.add_frame_callback(self.stream_handler_focus.on_new_frame)
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
        using_software_trigger = self.live_controller.trigger_mode == control._def.TriggerMode.SOFTWARE

        # turn on illumination and send trigger
        if using_software_trigger:
            self.live_controller.turn_on_illumination()
            self._wait_for_microcontroller()
            self.camera.send_trigger()
        elif self.live_controller.trigger_mode == control._def.TriggerMode.HARDWARE:
            self.low_level_drivers.microcontroller.send_hardware_trigger(
                control_illumination=True, illumination_on_time_us=self.camera.get_exposure_time() * 1000
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
        """Home the X, Y, and Z axes based on configuration settings.

        Homes Z first if enabled, then performs a coordinated X/Y homing sequence
        that avoids the plate clamp actuation post by moving Y first, homing X,
        moving X clear, then homing Y.
        """
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

    def move_x(self, distance: float, blocking: bool = True) -> None:
        """Move the stage by a relative distance along the X axis.

        Args:
            distance: Distance to move in mm (positive or negative).
            blocking: If True, wait for movement to complete before returning.
        """
        self.stage.move_x(distance, blocking=blocking)

    def move_y(self, distance: float, blocking: bool = True) -> None:
        """Move the stage by a relative distance along the Y axis.

        Args:
            distance: Distance to move in mm (positive or negative).
            blocking: If True, wait for movement to complete before returning.
        """
        self.stage.move_y(distance, blocking=blocking)

    def move_x_to(self, position: float, blocking: bool = True) -> None:
        """Move the stage to an absolute X position.

        Args:
            position: Target position in mm.
            blocking: If True, wait for movement to complete before returning.
        """
        self.stage.move_x_to(position, blocking=blocking)

    def move_y_to(self, position: float, blocking: bool = True) -> None:
        """Move the stage to an absolute Y position.

        Args:
            position: Target position in mm.
            blocking: If True, wait for movement to complete before returning.
        """
        self.stage.move_y_to(position, blocking=blocking)

    def get_x(self) -> float:
        """Get the current X position of the stage.

        Returns:
            Current X position in mm.
        """
        return self.stage.get_pos().x_mm

    def get_y(self) -> float:
        """Get the current Y position of the stage.

        Returns:
            Current Y position in mm.
        """
        return self.stage.get_pos().y_mm

    def get_z(self) -> float:
        """Get the current Z position of the stage.

        Returns:
            Current Z position in mm.
        """
        return self.stage.get_pos().z_mm

    def move_z_to(self, z_mm: float, blocking: bool = True) -> None:
        """Move the stage to an absolute Z position.

        Args:
            z_mm: Target position in mm.
            blocking: If True, wait for movement to complete before returning.
        """
        self.stage.move_z_to(z_mm, blocking=blocking)

    def start_live(self) -> None:
        """Start live view streaming from the camera."""
        self.camera.start_streaming()
        self.live_controller.start_live()

    def stop_live(self) -> None:
        """Stop live view streaming from the camera."""
        self.live_controller.stop_live()
        self.camera.stop_streaming()

    def _wait_for_microcontroller(self, timeout: float = 5.0, error_message: Optional[str] = None) -> None:
        """Wait for the microcontroller to complete the current operation.

        Args:
            timeout: Maximum time to wait in seconds.
            error_message: Custom error message for timeout errors.

        Raises:
            TimeoutError: If operation does not complete within timeout.
        """
        try:
            self.low_level_drivers.microcontroller.wait_till_operation_is_completed(timeout)
        except TimeoutError as e:
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self) -> None:
        """Close the microscope and release all hardware resources.

        Attempts to cleanly shut down all hardware components. Errors during
        shutdown are logged but do not prevent other components from being closed.
        """
        try:
            self.stop_live()
        except Exception as e:
            self._log.warning(f"Error stopping live view during close: {e}")

        if self.low_level_drivers.microcontroller:
            try:
                self.low_level_drivers.microcontroller.close()
            except Exception as e:
                self._log.warning(f"Error closing microcontroller: {e}")

        if self.addons.emission_filter_wheel:
            try:
                self.addons.emission_filter_wheel.close()
            except Exception as e:
                self._log.warning(f"Error closing emission filter wheel: {e}")

        if self.addons.camera_focus:
            try:
                self.addons.camera_focus.close()
            except Exception as e:
                self._log.warning(f"Error closing focus camera: {e}")

        try:
            self.camera.close()
        except Exception as e:
            self._log.warning(f"Error closing camera: {e}")

    def move_to_position(self, x: float, y: float, z: float) -> None:
        """Move the stage to an absolute XYZ position.

        Args:
            x: Target X position in mm.
            y: Target Y position in mm.
            z: Target Z position in mm.
        """
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective: str) -> None:
        """Set the current objective lens.

        Args:
            objective: Name of the objective to set as current.
        """
        self.objective_store.set_current_objective(objective)

    def set_illumination_intensity(self, channel: str, intensity: float, objective: Optional[str] = None) -> None:
        """Set the illumination intensity for a channel.

        Args:
            channel: Name of the channel.
            intensity: Illumination intensity value.
            objective: Objective name. If None, uses current objective.
        """
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self.live_controller.get_channel_by_name(objective, channel)
        if channel_config:
            channel_config.illumination_intensity = intensity
            self.live_controller.set_microscope_mode(channel_config)

    def set_exposure_time(self, channel: str, exposure_time: float, objective: Optional[str] = None) -> None:
        """Set the exposure time for a channel.

        Args:
            channel: Name of the channel.
            exposure_time: Exposure time in milliseconds.
            objective: Objective name. If None, uses current objective.
        """
        if objective is None:
            objective = self.objective_store.current_objective
        channel_config = self.live_controller.get_channel_by_name(objective, channel)
        if channel_config:
            channel_config.exposure_time = exposure_time
            self.live_controller.set_microscope_mode(channel_config)
