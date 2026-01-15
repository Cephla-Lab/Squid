from typing import Optional

import _def
import squid.core.config
import squid.core.logging
import squid.backend.drivers.cameras.camera_utils
import squid.backend.drivers.filter_wheels.utils
import squid.backend.drivers.lighting as serial_peripherals
import squid.backend.drivers.lighting.celesta
import squid.backend.drivers.lighting.illumination_andor
import squid.backend.microcontroller
from squid.backend.drivers.lighting import SciMicroscopyLEDArray
from squid.backend.drivers.lighting.led import (
    LightSourceType,
    IntensityControlMode,
    ShutterControlMode,
    IlluminationController,
)
from squid.backend.drivers.peripherals.piezo import PiezoStage
from squid.backend.drivers.stages.stage_utils import get_stage
from squid.backend.microcontroller import Microcontroller
from squid.backend.microscope import Microscope, MicroscopeAddons, LowLevelDrivers
from squid.core.abc import AbstractCamera, AbstractStage, AbstractFilterWheelController


def build_microscope_addons(
    stage: AbstractStage,
    micro: Optional[Microcontroller],
    simulated: bool = False,
) -> MicroscopeAddons:
    xlight = None
    if _def.ENABLE_SPINNING_DISK_CONFOCAL and not _def.USE_DRAGONFLY:
        # TODO: For user compatibility, when ENABLE_SPINNING_DISK_CONFOCAL is True, we use XLight/Cicero on default.
        # This needs to be changed when we figure out better machine configuration structure.
        xlight = (
            serial_peripherals.XLight(
                _def.XLIGHT_SERIAL_NUMBER,
                _def.XLIGHT_SLEEP_TIME_FOR_WHEEL,
            )
            if not simulated
            else serial_peripherals.XLight_Simulation()
        )

    dragonfly = None
    if _def.ENABLE_SPINNING_DISK_CONFOCAL and _def.USE_DRAGONFLY:
        dragonfly = (
            serial_peripherals.Dragonfly(SN=_def.DRAGONFLY_SERIAL_NUMBER)
            if not simulated
            else serial_peripherals.Dragonfly_Simulation()
        )

    nl5 = None
    if _def.ENABLE_NL5:
        import squid.backend.drivers.peripherals.nl5 as nl5_module

        nl5 = (
            nl5_module.NL5()
            if not simulated
            else nl5_module.NL5_Simulation()
        )

    cellx = None
    if _def.ENABLE_CELLX:
        cellx = (
            serial_peripherals.CellX(_def.CELLX_SN)
            if not simulated
            else serial_peripherals.CellX_Simulation()
        )

    emission_filter_wheel = None
    fw_config = squid.core.config.get_filter_wheel_config()
    if fw_config:
        emission_filter_wheel = (
            squid.backend.drivers.filter_wheels.utils.get_filter_wheel_controller(
                fw_config, microcontroller=micro, simulated=simulated
            )
        )

    objective_changer = None
    if _def.USE_XERYON:
        from squid.backend.drivers.peripherals.objective_changer import (
            ObjectiveChanger2PosController,
            ObjectiveChanger2PosController_Simulation,
        )

        objective_changer = (
            ObjectiveChanger2PosController(
                sn=_def.XERYON_SERIAL_NUMBER, stage=stage
            )
            if not simulated
            else ObjectiveChanger2PosController_Simulation(
                sn=_def.XERYON_SERIAL_NUMBER, stage=stage
            )
        )

    camera_focus = None
    if _def.SUPPORT_LASER_AUTOFOCUS:
        camera_focus = squid.backend.drivers.cameras.camera_utils.get_camera(
            squid.core.config.get_autofocus_camera_config(), simulated=simulated
        )

    # Legacy fluidics removed - now handled by FluidicsService in ApplicationContext
    # The new FluidicsService uses AbstractFluidicsController implementations:
    # - SimulatedFluidicsController for simulation
    # - MERFISHFluidicsDriver for real hardware
    # See: application.py _build_fluidics_driver()

    piezo_stage = None
    if _def.HAS_OBJECTIVE_PIEZO:
        if not micro:
            raise ValueError("Cannot create PiezoStage without a Microcontroller.")
        piezo_stage = PiezoStage(
            microcontroller=micro,
            config={
                "OBJECTIVE_PIEZO_HOME_UM": _def.OBJECTIVE_PIEZO_HOME_UM,
                "OBJECTIVE_PIEZO_RANGE_UM": _def.OBJECTIVE_PIEZO_RANGE_UM,
                "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": _def.OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
                "OBJECTIVE_PIEZO_FLIP_DIR": _def.OBJECTIVE_PIEZO_FLIP_DIR,
            },
        )

    sci_microscopy_led_array = None
    if _def.SUPPORT_SCIMICROSCOPY_LED_ARRAY:
        # to do: add error handling
        sci_microscopy_led_array = SciMicroscopyLEDArray(
            _def.SCIMICROSCOPY_LED_ARRAY_SN,
            _def.SCIMICROSCOPY_LED_ARRAY_DISTANCE,
            _def.SCIMICROSCOPY_LED_ARRAY_TURN_ON_DELAY,
        )
        sci_microscopy_led_array.set_NA(
            _def.SCIMICROSCOPY_LED_ARRAY_DEFAULT_NA
        )

    # Connect focus camera to event bus for Z position tracking (simulation)
    if camera_focus is not None:
        if hasattr(camera_focus, "set_event_bus"):
            from squid.core.events import event_bus

            camera_focus.set_event_bus(event_bus)
        # Legacy: also set piezo reference as fallback
        if piezo_stage is not None and hasattr(camera_focus, "set_piezo"):
            camera_focus.set_piezo(piezo_stage)

    return MicroscopeAddons(
        xlight,
        dragonfly,
        nl5,
        cellx,
        emission_filter_wheel,
        objective_changer,
        camera_focus,
        None,  # fluidics - now handled by FluidicsService
        piezo_stage,
        sci_microscopy_led_array,
    )


def build_low_level_drivers(simulated: bool = False) -> LowLevelDrivers:
    micro_serial_device = (
        squid.backend.microcontroller.get_microcontroller_serial_device(
            version=_def.CONTROLLER_VERSION, sn=_def.CONTROLLER_SN
        )
        if not simulated
        else squid.backend.microcontroller.get_microcontroller_serial_device(
            simulated=True
        )
    )
    micro = squid.backend.microcontroller.Microcontroller(
        serial_device=micro_serial_device
    )

    return LowLevelDrivers(microcontroller=micro)


def build_microscope(
    simulated: bool = False,
    skip_controller_creation: bool = False,
) -> Microscope:
    low_level_devices = build_low_level_drivers(simulated)

    stage_config = squid.core.config.get_stage_config()
    stage = get_stage(
        stage_config=stage_config,
        microcontroller=low_level_devices.microcontroller,
        simulated=simulated,
    )

    addons = build_microscope_addons(
        stage, low_level_devices.microcontroller, simulated=simulated
    )

    cam_trigger_log = squid.core.logging.get_logger("camera hw functions")

    def acquisition_camera_hw_trigger_fn(
        illumination_time: Optional[float],
    ) -> bool:
        # NOTE(imo): If this succeeds, it means we sent the request,
        # but we didn't necessarily get confirmation of success.
        if addons.nl5 and _def.NL5_USE_DOUT:
            addons.nl5.start_acquisition()
        else:
            illumination_time_us = (
                1000.0 * illumination_time if illumination_time else 0
            )
            cam_trigger_log.debug(
                f"Sending hw trigger with illumination_time={illumination_time_us if illumination_time else None} [us]"
            )
            low_level_devices.microcontroller.send_hardware_trigger(
                True if illumination_time else False, illumination_time_us
            )
        return True

    def acquisition_camera_hw_strobe_delay_fn(strobe_delay_ms: float) -> bool:
        strobe_delay_us = int(1000 * strobe_delay_ms)
        cam_trigger_log.debug(
            f"Setting microcontroller strobe delay to {strobe_delay_us} [us]"
        )
        low_level_devices.microcontroller.set_strobe_delay_us(strobe_delay_us)
        low_level_devices.microcontroller.wait_till_operation_is_completed()

        return True

    camera = squid.backend.drivers.cameras.camera_utils.get_camera(
        config=squid.core.config.get_camera_config(),
        simulated=simulated,
        hw_trigger_fn=acquisition_camera_hw_trigger_fn,
        hw_set_strobe_delay_ms_fn=acquisition_camera_hw_strobe_delay_fn,
    )

    # Connect main camera to event bus for stage position tracking (simulation)
    if hasattr(camera, "set_event_bus"):
        from squid.core.events import event_bus

        camera.set_event_bus(event_bus)

    if _def.USE_LDI_SERIAL_CONTROL and not simulated:
        ldi = serial_peripherals.LDI()

        illumination_controller = IlluminationController(
            low_level_devices.microcontroller,
            ldi.intensity_mode,
            ldi.shutter_mode,
            LightSourceType.LDI,
            ldi,
        )
    elif _def.USE_CELESTA_ETHERNET_CONTROL and not simulated:
        celesta = squid.backend.drivers.lighting.celesta.CELESTA()
        illumination_controller = IlluminationController(
            low_level_devices.microcontroller,
            IntensityControlMode.Software,
            ShutterControlMode.TTL,
            LightSourceType.CELESTA,
            celesta,
        )
    elif _def.USE_ANDOR_LASER_CONTROL and not simulated:
        andor_laser = squid.backend.drivers.lighting.illumination_andor.AndorLaser(
            _def.ANDOR_LASER_VID, _def.ANDOR_LASER_PID
        )
        illumination_controller = IlluminationController(
            low_level_devices.microcontroller,
            IntensityControlMode.Software,
            ShutterControlMode.TTL,
            LightSourceType.AndorLaser,
            andor_laser,
        )
    else:
        illumination_controller = IlluminationController(
            low_level_devices.microcontroller
        )

    return Microscope(
        stage=stage,
        camera=camera,
        illumination_controller=illumination_controller,
        addons=addons,
        low_level_drivers=low_level_devices,
        simulated=simulated,
        skip_controller_creation=skip_controller_creation,
    )
