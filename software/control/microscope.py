import math
import re
import serial
from typing import Optional

from PyQt5.QtCore import QObject

import control.core.core as core
from control._def import *
import squid.camera.utils
from squid.abc import CameraAcquisitionMode
import squid.stage.cephla
import squid.abc
import squid.logging
import squid.config
import squid.stage.utils

import control.microcontroller as microcontroller
from control.lighting import LightSourceType, IntensityControlMode, ShutterControlMode, IlluminationController
from control.piezo import PiezoStage
import control.serial_peripherals as serial_peripherals
import control.filterwheel as filterwheel

if USE_XERYON:
    from control.objective_changer_2_pos_controller import (
        ObjectiveChanger2PosController,
        ObjectiveChanger2PosController_Simulation,
    )

if SUPPORT_LASER_AUTOFOCUS:
    import control.core_displacement_measurement as core_displacement_measurement


class Microscope(QObject):

    def __init__(self, microscope=None, is_simulation=False):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        if microscope is None:
            self.initialize_microcontroller(is_simulation=is_simulation)
            self.initialize_camera(is_simulation=is_simulation)
            self.initialize_core_components()
            if not is_simulation:
                self.initialize_peripherals()
            else:
                self.initialize_simulation_objects()
            self.setup_hardware()
            self.performance_mode = True
        else:
            self.camera = microscope.camera
            self.camera_focus = microscope.camera_focus
            self.stage = microscope.stage
            self.microcontroller = microscope.microcontroller
            self.configurationManager = microscope.configurationManager
            self.objectiveStore = microscope.objectiveStore
            self.streamHandler = microscope.streamHandler
            self.liveController = microscope.liveController
            self.multipointController = microscope.multipointController
            self.illuminationController = microscope.illuminationController
            self.performance_mode = microscope.performance_mode

            if SUPPORT_LASER_AUTOFOCUS:
                self.laserAutofocusController = microscope.laserAutofocusController
            self.slidePositionController = microscope.slidePositionController

            if ENABLE_SPINNING_DISK_CONFOCAL:
                self.xlight = microscope.xlight

            if ENABLE_NL5:
                self.nl5 = microscope.nl5

            if ENABLE_CELLX:
                self.cellx = microscope.cellx

            if USE_LDI_SERIAL_CONTROL:
                self.ldi = microscope.ldi

            if USE_CELESTA_ETHENET_CONTROL:
                self.celesta = microscope.celesta

            if USE_ZABER_EMISSION_FILTER_WHEEL or USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
                self.emission_filter_wheel = microscope.emission_filter_wheel

            if USE_XERYON:
                self.objective_changer = microscope.objective_changer

    def initialize_camera(self, is_simulation):
        def acquisition_camera_hw_trigger_fn(illumination_time: Optional[float]) -> bool:
            # NOTE(imo): If this succeeds, it means means we sent the request,
            # but we didn't necessarily get confirmation of success.
            if ENABLE_NL5 and NL5_USE_DOUT:
                self.nl5.start_acquisition()
            else:
                illumination_time_us = 1000.0 * illumination_time if illumination_time else 0
                self._log.debug(
                    f"Sending hw trigger with illumination_time={illumination_time_us if illumination_time else None} [us]"
                )
                self.microcontroller.send_hardware_trigger(True if illumination_time else False, illumination_time_us)
            return True

        def acquisition_camera_hw_strobe_delay_fn(strobe_delay_ms: float) -> bool:
            strobe_delay_us = int(1000 * strobe_delay_ms)
            self._log.debug(f"Setting microcontroller strobe delay to {strobe_delay_us} [us]")
            self.microcontroller.set_strobe_delay_us(strobe_delay_us)
            self.microcontroller.wait_till_operation_is_completed()

            return True

        self.camera = squid.camera.utils.get_camera(
            squid.config.get_camera_config(),
            simulated=is_simulation,
            hw_trigger_fn=acquisition_camera_hw_trigger_fn,
            hw_set_strobe_delay_ms_fn=acquisition_camera_hw_strobe_delay_fn,
        )

        self.camera.set_pixel_format(squid.config.CameraPixelFormat.from_string(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT))
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        if SUPPORT_LASER_AUTOFOCUS:
            self.camera_focus = squid.camera.utils.get_camera(
                squid.config.get_autofocus_camera_config(), simulated=is_simulation
            )
            self.camera_focus.set_pixel_format(squid.config.CameraPixelFormat.from_string("MONO8"))
            self.camera_focus.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        else:
            self.camera_focus = None

    def initialize_microcontroller(self, is_simulation):
        self.microcontroller = microcontroller.Microcontroller(
            serial_device=microcontroller.get_microcontroller_serial_device(
                version=CONTROLLER_VERSION, sn=CONTROLLER_SN, simulated=is_simulation
            )
        )
        self.illuminationController = IlluminationController(self.microcontroller)
        if not USE_PRIOR_STAGE or is_simulation:  # TODO: Simulated Prior stage is not implemented yet
            self.stage = squid.stage.cephla.CephlaStage(
                microcontroller=self.microcontroller, stage_config=squid.config.get_stage_config()
            )

        self.home_x_and_y_separately = False

    def initialize_core_components(self):
        if HAS_OBJECTIVE_PIEZO:
            self.piezo = PiezoStage(
                self.microcontroller,
                {
                    "OBJECTIVE_PIEZO_HOME_UM": OBJECTIVE_PIEZO_HOME_UM,
                    "OBJECTIVE_PIEZO_RANGE_UM": OBJECTIVE_PIEZO_RANGE_UM,
                    "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE,
                    "OBJECTIVE_PIEZO_FLIP_DIR": OBJECTIVE_PIEZO_FLIP_DIR,
                },
            )
            self.piezo.home()
        else:
            self.piezo = None

        self.objectiveStore = core.ObjectiveStore()
        self.channelConfigurationManager = core.ChannelConfigurationManager()
        if SUPPORT_LASER_AUTOFOCUS:
            self.laserAFSettingManager = core.LaserAFSettingManager()
        else:
            self.laserAFSettingManager = None
        self.configurationManager = core.ConfigurationManager(
            self.channelConfigurationManager, self.laserAFSettingManager
        )

        self.liveController = core.LiveController(self.camera, self.microcontroller, self.illuminationController, self)
        self.streamHandler = core.StreamHandler(accept_new_frame_fn=lambda: self.liveController.is_live)
        self.slidePositionController = core.SlidePositionController(self.stage, self.liveController)

        if SUPPORT_LASER_AUTOFOCUS:
            self.liveController_focus_camera = core.LiveController(
                self.camera_focus,
                self.microcontroller,
                None,
                self,
                control_illumination=False,
                for_displacement_measurement=True,
            )
            self.streamHandler_focus_camera = core.StreamHandler(
                accept_new_frame_fn=lambda: self.liveController_focus_camera.is_live
            )
            self.displacementMeasurementController = core_displacement_measurement.DisplacementMeasurementController()
            self.laserAutofocusController = core.LaserAutofocusController(
                self.microcontroller,
                self.camera_focus,
                self.liveController_focus_camera,
                self.stage,
                self.piezo,
                self.objectiveStore,
                self.laserAFSettingManager,
            )
        else:
            self.laserAutofocusController = None

        self.multipointController = core.MultiPointController(
            self.camera,
            self.stage,
            self.piezo,
            self.microcontroller,
            self.liveController,
            self.laserAutofocusController,
            self.objectiveStore,
            self.channelConfigurationManager,
            scan_coordinates=None,
            parent=self,
            headless=True,
        )

    def setup_hardware(self):
        self.camera.add_frame_callback(self.streamHandler.on_new_frame)
        self.camera.enable_callbacks(True)

        if SUPPORT_LASER_AUTOFOCUS:
            self.camera_focus.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
            self.camera_focus.add_frame_callback(self.streamHandler_focus_camera.on_new_frame)
            self.camera_focus.enable_callbacks(True)
            self.camera_focus.start_streaming()

    def initialize_peripherals(self):
        if USE_PRIOR_STAGE:
            self.stage: squid.abc.AbstractStage = squid.stage.prior.PriorStage(
                sn=PRIOR_STAGE_SN, stage_config=squid.config.get_stage_config()
            )

        if ENABLE_SPINNING_DISK_CONFOCAL:
            try:
                self.xlight = serial_peripherals.XLight(XLIGHT_SERIAL_NUMBER, XLIGHT_SLEEP_TIME_FOR_WHEEL)
            except Exception:
                self._log.error("Error initializing Spinning Disk Confocal")
                raise

        if ENABLE_NL5:
            try:
                import control.NL5 as NL5

                self.nl5 = NL5.NL5()
            except Exception:
                self._log.error("Error initializing NL5")
                raise

        if ENABLE_CELLX:
            try:
                self.cellx = serial_peripherals.CellX(CELLX_SN)
                for channel in [1, 2, 3, 4]:
                    self.cellx.set_modulation(channel, CELLX_MODULATION)
                    self.cellx.turn_on(channel)
            except Exception:
                self._log.error("Error initializing CellX")
                raise

        if USE_LDI_SERIAL_CONTROL:
            try:
                self.ldi = serial_peripherals.LDI()
                self.illuminationController = IlluminationController(
                    self.microcontroller, self.ldi.intensity_mode, self.ldi.shutter_mode, LightSourceType.LDI, self.ldi
                )
            except Exception:
                self._log.error("Error initializing LDI")
                raise

        if USE_CELESTA_ETHENET_CONTROL:
            try:
                import control.celesta

                self.celesta = control.celesta.CELESTA()
                self.illuminationController = IlluminationController(
                    self.microcontroller,
                    IntensityControlMode.Software,
                    ShutterControlMode.TTL,
                    LightSourceType.CELESTA,
                    self.celesta,
                )
            except Exception:
                self._log.error("Error initializing CELESTA")
                raise

        if USE_ZABER_EMISSION_FILTER_WHEEL:
            try:
                self.emission_filter_wheel = serial_peripherals.FilterController(
                    FILTER_CONTROLLER_SERIAL_NUMBER, 115200, 8, serial.PARITY_NONE, serial.STOPBITS_ONE
                )
                self.emission_filter_wheel.start_homing()
            except Exception:
                self._log.error("Error initializing Zaber Emission Filter Wheel")
                raise
        if USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            try:
                self.emission_filter_wheel = serial_peripherals.Optospin(SN=FILTER_CONTROLLER_SERIAL_NUMBER)
                self.emission_filter_wheel.set_speed(OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ)
            except Exception:
                self._log.error("Error initializing Optospin Emission Filter Wheel")
                raise

        if USE_SQUID_FILTERWHEEL:
            self.squid_filter_wheel = filterwheel.SquidFilterWheelWrapper(self.microcontroller)

        if USE_XERYON:
            try:
                self.objective_changer = ObjectiveChanger2PosController(sn=XERYON_SERIAL_NUMBER, stage=self.stage)
            except Exception:
                self._log.error("Error initializing Xeryon objective switcher")
                raise

    def initialize_simulation_objects(self):
        if ENABLE_SPINNING_DISK_CONFOCAL:
            self.xlight = serial_peripherals.XLight_Simulation()
        if ENABLE_NL5:
            import control.NL5 as NL5

            self.nl5 = NL5.NL5_Simulation()
        if ENABLE_CELLX:
            self.cellx = serial_peripherals.CellX_Simulation()

        if USE_LDI_SERIAL_CONTROL:
            self.ldi = serial_peripherals.LDI_Simulation()
            self.illuminationController = IlluminationController(
                self.microcontroller, self.ldi.intensity_mode, self.ldi.shutter_mode, LightSourceType.LDI, self.ldi
            )
        if USE_ZABER_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel = serial_peripherals.FilterController_Simulation(
                115200, 8, serial.PARITY_NONE, serial.STOPBITS_ONE
            )
        if USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel = serial_peripherals.Optospin_Simulation(SN=None)
        if USE_SQUID_FILTERWHEEL:
            self.squid_filter_wheel = filterwheel.SquidFilterWheelWrapper_Simulation(None)
        if USE_XERYON:
            self.objective_changer = ObjectiveChanger2PosController_Simulation(
                sn=XERYON_SERIAL_NUMBER, stage=self.stage
            )

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
                control_illumination=True, illumination_on_time_us=self.camera.get_exposure_time() * 1000
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
        self.stage.move_z_to(z_mm)

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
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self):
        self.stop_live()
        self.microcontroller.close()
        if USE_ZABER_EMISSION_FILTER_WHEEL or USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel.close()
        self.camera.close()

    def to_loading_position(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()

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
            self.liveController.start_live()

        self.slidePositionController.slide_loading_position_reached = True

    def to_scanning_position(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()

        # move to position
        # for well plate
        if self.slidePositionController.is_for_wellplate:
            # home for the first time
            if not self.slidePositionController.homing_done:

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
            self.liveController.start_live()

        self.slidePositionController.slide_scanning_position_reached = True

    def move_to_position(self, x, y, z):
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective):
        self.objectiveStore.set_current_objective(objective)

    def set_coordinates(self, wellplate_format, selected, scan_size_mm, overlap_percent):
        self.scanCoordinates = ScanCoordinatesSiLA2(self.objectiveStore, self.camera.get_pixel_size_unbinned_um())
        self.scanCoordinates.get_scan_coordinates_from_selected_wells(
            wellplate_format, selected, scan_size_mm, overlap_percent
        )

    def perform_scanning(self, path, experiment_ID, z_pos_um, channels, use_laser_af=False, dz=1.5, Nz=1):
        if self.scanCoordinates is not None:
            self.multipointController.scanCoordinates = self.scanCoordinates
        self.move_z_to(z_pos_um / 1000)
        self.multipointController.set_deltaZ(dz)
        self.multipointController.set_NZ(Nz)
        self.multipointController.set_z_range(z_pos_um / 1000, z_pos_um / 1000 + dz / 1000 * (Nz - 1))
        self.multipointController.set_base_path(path)
        if use_laser_af:
            self.multipointController.set_reflection_af_flag(True)
        self.multipointController.set_selected_configurations(channels)
        self.multipointController.start_new_experiment(experiment_ID)
        self.multipointController.run_acquisition()

    def set_illumination_intensity(self, channel, intensity, objective=None):
        if objective is None:
            objective = self.objectiveStore.current_objective
        channel_config = self.channelConfigurationManager.get_channel_configuration_by_name(objective, channel)
        channel_config.illumination_intensity = intensity
        self.liveController.set_microscope_mode(channel_config)

    def set_exposure_time(self, channel, exposure_time, objective=None):
        if objective is None:
            objective = self.objectiveStore.current_objective
        channel_config = self.channelConfigurationManager.get_channel_configuration_by_name(objective, channel)
        channel_config.exposure_time = exposure_time
        self.liveController.set_microscope_mode(channel_config)


class ScanCoordinatesSiLA2:
    def __init__(self, objectiveStore, camera_sensor_pixel_size_um):
        self.objectiveStore = objectiveStore
        self.camera_sensor_pixel_size_um = camera_sensor_pixel_size_um
        self.region_centers = {}
        self.region_fov_coordinates = {}
        self.wellplate_settings = None

    def get_scan_coordinates_from_selected_wells(
        self, wellplate_format, selected, scan_size_mm=None, overlap_percent=10
    ):
        self.wellplate_settings = self.get_wellplate_settings(wellplate_format)
        self.get_selected_well_coordinates(selected, self.wellplate_settings)

        if wellplate_format in ["384 well plate", "1536 well plate"]:
            well_shape = "Square"
        else:
            well_shape = "Circle"

        if scan_size_mm is None:
            scan_size_mm = self.wellplate_settings["well_size_mm"]

        for k, v in self.region_centers.items():
            coords = self.create_region_coordinates(v[0], v[1], scan_size_mm, overlap_percent, well_shape)
            self.region_fov_coordinates[k] = coords

    def get_selected_well_coordinates(self, selected, wellplate_settings):
        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        descriptions = selected.split(",")
        for desc in descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = self._row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = self._row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(min(start_row_index, end_row_index), max(start_row_index, end_row_index) + 1):
                        cols = range(min(start_col_index, end_col_index), max(start_col_index, end_col_index) + 1)
                        # Reverse column order for alternating rows if needed
                        if (row - start_row_index) % 2 == 1:
                            cols = reversed(cols)

                        for col in cols:
                            x_mm = (
                                wellplate_settings["a1_x_mm"]
                                + col * wellplate_settings["well_spacing_mm"]
                                + WELLPLATE_OFFSET_X_mm
                            )
                            y_mm = (
                                wellplate_settings["a1_y_mm"]
                                + row * wellplate_settings["well_spacing_mm"]
                                + WELLPLATE_OFFSET_Y_mm
                            )
                            self.region_centers[self._index_to_row(row) + str(col + 1)] = (x_mm, y_mm)
                else:
                    x_mm = (
                        wellplate_settings["a1_x_mm"]
                        + start_col_index * wellplate_settings["well_spacing_mm"]
                        + WELLPLATE_OFFSET_X_mm
                    )
                    y_mm = (
                        wellplate_settings["a1_y_mm"]
                        + start_row_index * wellplate_settings["well_spacing_mm"]
                        + WELLPLATE_OFFSET_Y_mm
                    )
                    self.region_centers[start_row + start_col] = (x_mm, y_mm)
            else:
                raise ValueError(f"Invalid well format: {desc}. Expected format is 'A1' or 'A1:B2' for ranges.")

    def _row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def _index_to_row(self, index):
        index += 1
        row = ""
        while index > 0:
            index -= 1
            row = chr(index % 26 + ord("A")) + row
            index //= 26
        return row

    def get_wellplate_settings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "0":
            settings = {
                "format": "0",
                "a1_x_mm": 0,
                "a1_y_mm": 0,
                "a1_x_pixel": 0,
                "a1_y_pixel": 0,
                "well_size_mm": 0,
                "well_spacing_mm": 0,
                "number_of_skip": 0,
                "rows": 1,
                "cols": 1,
            }
        else:
            raise ValueError(
                f"Invalid wellplate format: {wellplate_format}. Expected formats are: {list(WELLPLATE_FORMAT_SETTINGS.keys())} or '0'"
            )
        return settings

    def create_region_coordinates(self, center_x, center_y, scan_size_mm, overlap_percent=10, shape="Square"):
        # if shape == 'Manual':
        #    return self.create_manual_region_coordinates(objectiveStore, self.manual_shapes, overlap_percent)

        # if scan_size_mm is None:
        #    scan_size_mm = self.wellplate_settings.well_size_mm
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera_sensor_pixel_size_um
        fov_size_mm = (pixel_size_um / 1000) * CAMERA_CONFIG.CROP_WIDTH_UNBINNED
        step_size_mm = fov_size_mm * (1 - overlap_percent / 100)

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
        # print(f"steps: {steps}, step_size_mm: {step_size_mm}")
        # print(f"scan size mm: {scan_size_mm}")
        # print(f"actual scan size mm: {actual_scan_size_mm}")

        scan_coordinates = []
        half_steps = (steps - 1) / 2
        radius_squared = (scan_size_mm / 2) ** 2
        fov_size_mm_half = fov_size_mm / 2

        for i in range(steps):
            row = []
            y = center_y + (i - half_steps) * step_size_mm
            for j in range(steps):
                x = center_x + (j - half_steps) * step_size_mm
                if shape == "Square" or (
                    shape == "Circle" and self._is_in_circle(x, y, center_x, center_y, radius_squared, fov_size_mm_half)
                ):
                    row.append((x, y))
                    # self.navigationViewer.register_fov_to_image(x, y)

            if FOV_PATTERN == "S-Pattern" and i % 2 == 1:
                row.reverse()
            scan_coordinates.extend(row)

        if not scan_coordinates and shape == "Circle":
            scan_coordinates.append((center_x, center_y))
            # self.navigationViewer.register_fov_to_image(center_x, center_y)

        # self.signal_update_navigation_viewer.emit()
        return scan_coordinates

    def _is_in_circle(self, x, y, center_x, center_y, radius_squared, fov_size_mm_half):
        corners = [
            (x - fov_size_mm_half, y - fov_size_mm_half),
            (x + fov_size_mm_half, y - fov_size_mm_half),
            (x - fov_size_mm_half, y + fov_size_mm_half),
            (x + fov_size_mm_half, y + fov_size_mm_half),
        ]
        return all((cx - center_x) ** 2 + (cy - center_y) ** 2 <= radius_squared for cx, cy in corners)
