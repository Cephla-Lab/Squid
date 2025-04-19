import math
import re
import serial

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
from control.piezo import PiezoStage
import control.serial_peripherals as serial_peripherals

if SUPPORT_LASER_AUTOFOCUS:
    import control.core_displacement_measurement as core_displacement_measurement


class Microscope(QObject):

    def __init__(self, microscope = None, is_simulation=False):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        if microscope is None:
            self.initialize_camera(is_simulation=is_simulation)
            self.initialize_microcontroller(is_simulation=is_simulation)
            self.initialize_core_components()
            self.initialize_peripherals()
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
            self.performance_mode = microscope.performance_mode

            if SUPPORT_LASER_AUTOFOCUS:
                self.laserAutofocusController = microscope.laserAutofocusController
            self.slidePositionController = microscope.slidePositionController
            if USE_ZABER_EMISSION_FILTER_WHEEL:
                self.emission_filter_wheel = microscope.emission_filter_wheel
            elif USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
                self.emission_filter_wheel = microscope.emission_filter_wheel

    def initialize_camera(self, is_simulation):
        self.camera = squid.camera.utils.get_camera(squid.config.get_camera_config(), simulated=is_simulation)

        self.camera.set_pixel_format(DEFAULT_PIXEL_FORMAT)
        self.camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        self.camera_focus = squid.camera.utils.get_camera(
            squid.config.get_autofocus_camera_config(), simulated=is_simulation
        )
        self.camera_focus.set_pixel_format("MONO8")
        self.camera_focus.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    def initialize_microcontroller(self, is_simulation):
        self.microcontroller = microcontroller.Microcontroller(
            serial_device=microcontroller.get_microcontroller_serial_device(
                version=CONTROLLER_VERSION, sn=CONTROLLER_SN, simulated=is_simulation
            )
        )
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

        self.objectiveStore = core.ObjectiveStore(parent=self)
        self.channelConfigurationManager = core.ChannelConfigurationManager()
        if SUPPORT_LASER_AUTOFOCUS:
            self.laserAFSettingManager = core.LaserAFSettingManager()
        else:
            self.laserAFSettingManager = None
        self.configurationManager = core.ConfigurationManager(
            self.channelConfigurationManager, self.laserAFSettingManager
        )

        self.liveController = core.LiveController(self.camera, self.microcontroller, None, self)
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
            self.streamHandler_focus_camera = core.StreamHandler(accept_new_frame_fn=lambda: self.liveController_focus_camera.is_live)
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
            scanCoordinates=None,
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
            self._log.error(error_message or "Microcontroller operation timed out!")
            raise e

    def close(self):
        self.stop_live()
        self.microcontroller.close()
        if USE_ZABER_EMISSION_FILTER_WHEEL or USE_OPTOSPIN_EMISSION_FILTER_WHEEL:
            self.emission_filter_wheel.close()

    # ===============================================
    # Methods for SiLA2
    # ===============================================
    def to_loading_position(self):
        # retract z
        self.move_z_to(1.0)
        self.move_x_to(30.0)
        self.move_y_to(30.0)

    def move_to_position(self, x, y, z):
        self.move_x_to(x)
        self.move_y_to(y)
        self.move_z_to(z)

    def set_objective(self, objective):
        self.objectiveStore.set_current_objective(objective)

    def set_coordinates(self, wellplate_format, selected, scan_size_mm, overlap_percent):
        self.scanCoordinates = ScanCoordinatesSiLA2(self.objectiveStore)
        self.scanCoordinates.get_scan_coordinates_from_selected_wells(
            wellplate_format, selected, scan_size_mm, overlap_percent
        )

    def perform_scanning(self, path, experiment_ID, z_pos_um, channels, use_laser_af=False):
        if self.scanCoordinates is not None:
            self.multipointController.scanCoordinates = self.scanCoordinates
        self.move_z_to(z_pos_um / 1000)
        dz = 1.5  # um
        Nz = 1
        self.multipointController.set_deltaZ(dz)
        self.multipointController.set_NZ(Nz)
        self.multipointController.set_z_range(z_pos_um / 1000, z_pos_um / 1000 + dz / 1000 * (Nz - 1))
        self.multipointController.set_base_path(path)
        if use_laser_af:
            self.multipointController.set_reflection_af_flag(True)
        self.multipointController.set_selected_configurations(channels)
        self.multipointController.start_new_experiment(experiment_ID)
        self.multipointController.run_acquisition()


class ScanCoordinatesSiLA2:
    def __init__(self, objectiveStore):
        self.objectiveStore = objectiveStore
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
        pixel_size_um = self.objectiveStore.get_pixel_size()
        fov_size_mm = (pixel_size_um / 1000) * Acquisition.CROP_WIDTH
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
