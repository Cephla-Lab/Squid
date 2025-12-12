# set QT_API environment variable
import os

os.environ["QT_API"] = "pyqt5"

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import squid.core.utils.hardware_utils as utils
from _def import *
from squid.storage.stream_handler import *
from squid.ops.navigation import *
from squid.ops.configuration import *

from typing import List, Optional, Any, TYPE_CHECKING
import time
import numpy as np
import cv2
from datetime import datetime

if TYPE_CHECKING:
    from squid.core.abc import AbstractCamera
    from squid.mcs.microcontroller import Microcontroller
    from squid.mcs.controllers.live_controller import LiveController
    from squid.mcs.controllers.autofocus import AutoFocusController
    from squid.core.utils.config_utils import ChannelMode

from squid.core.config import CameraPixelFormat


class PlateReadingWorker(QObject):
    finished = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(object)

    def __init__(self, plateReadingController: "PlateReadingController") -> None:
        QObject.__init__(self)
        self.plateReadingController = plateReadingController

        self.camera = self.plateReadingController.camera
        self.microcontroller = self.plateReadingController.microcontroller
        self.plateReaderNavigationController = (
            self.plateReadingController.plateReaderNavigationController
        )
        self.liveController = self.plateReadingController.liveController
        self.autofocusController = self.plateReadingController.autofocusController
        self.configurationManager = self.plateReadingController.configurationManager
        self.NX = self.plateReadingController.NX
        self.NY = self.plateReadingController.NY
        self.NZ = self.plateReadingController.NZ
        self.Nt = self.plateReadingController.Nt
        self.deltaX = self.plateReadingController.deltaX
        self.deltaX_usteps = self.plateReadingController.deltaX_usteps
        self.deltaY = self.plateReadingController.deltaY
        self.deltaY_usteps = self.plateReadingController.deltaY_usteps
        self.deltaZ = self.plateReadingController.deltaZ
        self.deltaZ_usteps = self.plateReadingController.deltaZ_usteps
        self.dt = self.plateReadingController.deltat
        self.do_autofocus = self.plateReadingController.do_autofocus
        self.crop_width = self.plateReadingController.crop_width
        self.crop_height = self.plateReadingController.crop_height
        self.display_resolution_scaling = (
            self.plateReadingController.display_resolution_scaling
        )
        self.counter = self.plateReadingController.counter
        self.experiment_ID = self.plateReadingController.experiment_ID
        self.base_path = self.plateReadingController.base_path
        self.timestamp_acquisition_started = (
            self.plateReadingController.timestamp_acquisition_started
        )
        self.time_point = 0
        self.abort_acquisition_requested = False
        self.selected_configurations: List[Any] = (
            self.plateReadingController.selected_configurations
        )
        self.selected_columns: List[int] = self.plateReadingController.selected_columns

    def run(self) -> None:
        self.abort_acquisition_requested = False
        self.plateReaderNavigationController.is_scanning = True
        while self.time_point < self.Nt and not self.abort_acquisition_requested:
            # continous acquisition
            if self.dt == 0:
                self.run_single_time_point()
                self.time_point = self.time_point + 1
            # timed acquisition
            else:
                self.run_single_time_point()
                self.time_point = self.time_point + 1
                # check if the aquisition has taken longer than dt or integer multiples of dt, if so skip the next time point(s)
                while (
                    time.time()
                    > self.timestamp_acquisition_started + self.time_point * self.dt
                ):
                    print("skip time point " + str(self.time_point + 1))
                    self.time_point = self.time_point + 1
                if self.time_point == self.Nt:
                    break  # no waiting after taking the last time point
                # wait until it's time to do the next acquisition
                while (
                    time.time()
                    < self.timestamp_acquisition_started + self.time_point * self.dt
                ):
                    time.sleep(0.05)
        self.plateReaderNavigationController.is_scanning = False
        self.finished.emit()

    def wait_till_operation_is_completed(self) -> None:
        while self.microcontroller.is_busy():
            time.sleep(SLEEP_TIME_S)

    def run_single_time_point(self) -> None:
        if self.base_path is None or self.experiment_ID is None:
            raise ValueError(
                "base_path and experiment_ID must be set before running acquisition"
            )

        self.FOV_counter: int = 0
        column_counter: int = 0
        print("multipoint acquisition - time point " + str(self.time_point + 1))

        # for each time point, create a new folder
        current_path: str = os.path.join(
            self.base_path, self.experiment_ID, str(self.time_point)
        )
        utils.ensure_directory_exists(current_path)

        # run homing
        self.plateReaderNavigationController.home()
        self.wait_till_operation_is_completed()

        # row scan direction
        row_scan_direction: int = 1  # 1: A -> H, 0: H -> A

        # go through columns
        for column in self.selected_columns:
            # increament counter
            column_counter = column_counter + 1

            # move to the current column
            self.plateReaderNavigationController.moveto_column(column - 1)
            self.wait_till_operation_is_completed()

            """
            # row homing
            if column_counter > 1:
                self.plateReaderNavigationController.home_y()
                self.wait_till_operation_is_completed()
            """

            # go through rows
            for row in range(PLATE_READER.NUMBER_OF_ROWS):
                if row_scan_direction == 0:  # reverse scan:
                    row = PLATE_READER.NUMBER_OF_ROWS - 1 - row

                row_str: str = chr(ord("A") + row)
                file_ID: str = row_str + str(column)

                # move to the selected row
                self.plateReaderNavigationController.moveto_row(row)
                self.wait_till_operation_is_completed()
                time.sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)

                # AF
                if (
                    (self.NZ == 1)
                    and (self.do_autofocus)
                    and (self.FOV_counter % Acquisition.NUMBER_OF_FOVS_PER_AF == 0)
                ):
                    configuration_name_AF: str = "BF LED matrix full"
                    # Get configurations for the default objective
                    available_configs = self.configurationManager.get_configurations(
                        DEFAULT_OBJECTIVE
                    )
                    config_AF: Optional["ChannelMode"] = next(
                        (
                            config
                            for config in available_configs
                            if config.name == configuration_name_AF
                        ),
                        None,
                    )
                    if config_AF:
                        self.signal_current_configuration.emit(config_AF)
                    self.autofocusController.autofocus()
                    self.autofocusController.wait_till_autofocus_has_completed()

                # z stack
                for k in range(self.NZ):
                    if self.NZ > 1:
                        # update file ID
                        file_ID = file_ID + "_" + str(k)
                        # maneuver for achiving uniform step size and repeatability when using open-loop control
                        self.plateReaderNavigationController.move_z_usteps(80)
                        self.wait_till_operation_is_completed()
                        self.plateReaderNavigationController.move_z_usteps(-80)
                        self.wait_till_operation_is_completed()
                        time.sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

                    # iterate through selected modes
                    for config in self.selected_configurations:
                        self.signal_current_configuration.emit(config)
                        self.wait_till_operation_is_completed()
                        self.liveController.turn_on_illumination()
                        self.wait_till_operation_is_completed()
                        self.camera.send_trigger()
                        image: Optional[np.ndarray] = self.camera.read_frame()
                        self.liveController.turn_off_illumination()
                        if image is None:
                            print(
                                "Warning: camera.read_frame() returned None, skipping this image"
                            )
                            continue
                        image = utils.crop_image(
                            image, self.crop_width, self.crop_height
                        )
                        saving_path: str = os.path.join(
                            current_path,
                            file_ID
                            + "_"
                            + str(config.name)
                            + "."
                            + Acquisition.IMAGE_FORMAT,
                        )
                        # self.image_to_display.emit(cv2.resize(image,(round(self.crop_width*self.display_resolution_scaling), round(self.crop_height*self.display_resolution_scaling)),cv2.INTER_LINEAR))
                        # image_to_display = utils.crop_image(image,round(self.crop_width*self.liveController.display_resolution_scaling), round(self.crop_height*self.liveController.display_resolution_scaling))
                        image_to_display: np.ndarray = utils.crop_image(
                            image, round(self.crop_width), round(self.crop_height)
                        )
                        self.image_to_display.emit(image_to_display)
                        self.image_to_display_multi.emit(
                            image_to_display, config.illumination_source
                        )
                        if CameraPixelFormat.is_color_format(
                            self.camera.get_pixel_format()
                        ):
                            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                        cv2.imwrite(saving_path, image)
                        QApplication.processEvents()

                    if self.NZ > 1:
                        # move z
                        if k < self.NZ - 1:
                            self.plateReaderNavigationController.move_z_usteps(
                                self.deltaZ_usteps
                            )
                            self.wait_till_operation_is_completed()
                            time.sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

                if self.NZ > 1:
                    # move z back
                    self.plateReaderNavigationController.move_z_usteps(
                        -self.deltaZ_usteps * (self.NZ - 1)
                    )
                    self.wait_till_operation_is_completed()

                if self.abort_acquisition_requested:
                    return

            # update row scan direction
            row_scan_direction = 1 - row_scan_direction


class PlateReadingController(QObject):
    acquisitionFinished = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(object)

    def __init__(
        self,
        camera: "AbstractCamera",
        plateReaderNavigationController: Any,  # TODO: Create proper type for this
        liveController: "LiveController",
        autofocusController: "AutoFocusController",
        configurationManager: "ChannelConfigurationManager",
    ) -> None:
        QObject.__init__(self)

        self.camera: "AbstractCamera" = camera
        self.microcontroller: "Microcontroller" = (
            plateReaderNavigationController.microcontroller
        )  # to move to gui for transparency
        self.plateReaderNavigationController: Any = (
            plateReaderNavigationController  # TODO: Create proper type for this
        )
        self.liveController: "LiveController" = liveController
        self.autofocusController: "AutoFocusController" = autofocusController
        self.configurationManager: "ChannelConfigurationManager" = configurationManager
        self.NX: int = 1
        self.NY: int = 1
        self.NZ: int = 1
        self.Nt: int = 1
        mm_per_ustep_X: float = SCREW_PITCH_X_MM / (
            self.plateReaderNavigationController.x_microstepping * FULLSTEPS_PER_REV_X
        )
        mm_per_ustep_Y: float = SCREW_PITCH_Y_MM / (
            self.plateReaderNavigationController.y_microstepping * FULLSTEPS_PER_REV_Y
        )
        mm_per_ustep_Z: float = SCREW_PITCH_Z_MM / (
            self.plateReaderNavigationController.z_microstepping * FULLSTEPS_PER_REV_Z
        )
        self.deltaX: float = Acquisition.DX
        self.deltaX_usteps: int = round(self.deltaX / mm_per_ustep_X)
        self.deltaY: float = Acquisition.DY
        self.deltaY_usteps: int = round(self.deltaY / mm_per_ustep_Y)
        self.deltaZ: float = Acquisition.DZ / 1000
        self.deltaZ_usteps: int = round(self.deltaZ / mm_per_ustep_Z)
        self.deltat: float = 0
        self.do_autofocus: bool = False
        self.crop_width: int = AF.CROP_WIDTH
        self.crop_height: int = AF.CROP_HEIGHT
        self.display_resolution_scaling: float = (
            Acquisition.IMAGE_DISPLAY_SCALING_FACTOR
        )
        self.counter: int = 0
        self.experiment_ID: Optional[str] = None
        self.base_path: Optional[str] = None
        self.selected_configurations: List[Any] = []
        self.selected_columns: List[int] = []
        self.thread: Optional[QThread] = None
        self.plateReadingWorker: Optional[PlateReadingWorker] = None
        self.timestamp_acquisition_started: float = 0
        self.recording_start_time: float = 0

        # State tracking for acquisition
        self.camera_callback_was_enabled_before_acquisition: bool = False
        self.liveController_was_live_before_acquisition: bool = False
        self.configuration_before_running_multipoint: Optional["ChannelMode"] = None

    def set_NZ(self, N: int) -> None:
        self.NZ = N

    def set_Nt(self, N: int) -> None:
        self.Nt = N

    def set_deltaZ(self, delta_um: float) -> None:
        mm_per_ustep_Z: float = SCREW_PITCH_Z_MM / (
            self.plateReaderNavigationController.z_microstepping * FULLSTEPS_PER_REV_Z
        )
        self.deltaZ = delta_um / 1000
        self.deltaZ_usteps = round((delta_um / 1000) / mm_per_ustep_Z)

    def set_deltat(self, delta: float) -> None:
        self.deltat = delta

    def set_af_flag(self, flag: bool) -> None:
        self.do_autofocus = flag

    def set_crop(self, crop_width: int, height: int) -> None:
        self.crop_width = crop_width
        self.crop_height = height

    def set_base_path(self, path: str) -> None:
        self.base_path = path

    def start_new_experiment(
        self, experiment_ID: str
    ) -> None:  # @@@ to do: change name to prepare_folder_for_new_experiment
        # generate unique experiment ID
        self.experiment_ID = (
            experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        )
        self.recording_start_time = time.time()
        # create a new folder
        if self.base_path is None:
            raise ValueError("base_path must be set before starting experiment")
        try:
            os.mkdir(os.path.join(self.base_path, self.experiment_ID))
            self.configurationManager.write_configuration_selected(
                DEFAULT_OBJECTIVE,
                self.selected_configurations,
                os.path.join(self.base_path, self.experiment_ID)
                + "/configurations.xml",
            )  # save the configuration for the experiment
        except Exception:
            pass

    def set_selected_configurations(
        self, selected_configurations_name: List[str]
    ) -> None:
        self.selected_configurations = []
        available_configs = self.configurationManager.get_configurations(
            DEFAULT_OBJECTIVE
        )
        for configuration_name in selected_configurations_name:
            config = next(
                (
                    config
                    for config in available_configs
                    if config.name == configuration_name
                ),
                None,
            )
            if config:
                self.selected_configurations.append(config)

    def set_selected_columns(self, selected_columns: List[int]) -> None:
        selected_columns.sort()
        self.selected_columns = selected_columns

    def run_acquisition(self) -> None:  # @@@ to do: change name to run_experiment
        print("start plate reading")
        # save the current microscope configuration
        self.configuration_before_running_multipoint = (
            self.liveController.currentConfiguration
        )
        # stop live
        if self.liveController.is_live:
            self.liveController_was_live_before_acquisition = True
            self.liveController.stop_live()  # @@@ to do: also uncheck the live button
        else:
            self.liveController_was_live_before_acquisition = False
        # disable callback
        if self.camera.get_callbacks_enabled():
            self.camera_callback_was_enabled_before_acquisition = True
            self.camera.stop_streaming()
            self.camera.enable_callbacks(False)
            self.camera.start_streaming()  # @@@ to do: absorb stop/start streaming into enable/disable callback - add a flag is_streaming to the camera class
        else:
            self.camera_callback_was_enabled_before_acquisition = False

        # run the acquisition
        self.timestamp_acquisition_started = time.time()
        # create a QThread object
        self.thread = QThread()
        # create a worker object
        self.plateReadingWorker = PlateReadingWorker(self)
        # move the worker to the thread
        self.plateReadingWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.plateReadingWorker.run)
        self.plateReadingWorker.finished.connect(self._on_acquisition_completed)
        self.plateReadingWorker.finished.connect(self.plateReadingWorker.deleteLater)
        self.plateReadingWorker.finished.connect(self.thread.quit)
        self.plateReadingWorker.image_to_display.connect(self.slot_image_to_display)
        self.plateReadingWorker.image_to_display_multi.connect(
            self.slot_image_to_display_multi
        )
        self.plateReadingWorker.signal_current_configuration.connect(
            self.slot_current_configuration, type=Qt.BlockingQueuedConnection
        )
        self.thread.finished.connect(self.thread.deleteLater)
        # start the thread
        self.thread.start()

    def stop_acquisition(self) -> None:
        if self.plateReadingWorker is not None:
            self.plateReadingWorker.abort_acquisition_requested = True

    def _on_acquisition_completed(self) -> None:
        # restore the previous selected mode
        self.signal_current_configuration.emit(
            self.configuration_before_running_multipoint
        )

        # re-enable callback
        if self.camera_callback_was_enabled_before_acquisition:
            self.camera.stop_streaming()
            self.camera.enable_callbacks(True)
            self.camera.start_streaming()
            self.camera_callback_was_enabled_before_acquisition = False

        # re-enable live if it's previously on
        if self.liveController_was_live_before_acquisition:
            self.liveController.start_live()

        # emit the acquisition finished signal to enable the UI
        self.acquisitionFinished.emit()
        QApplication.processEvents()

    def slot_image_to_display(self, image: np.ndarray) -> None:
        self.image_to_display.emit(image)

    def slot_image_to_display_multi(
        self, image: np.ndarray, illumination_source: int
    ) -> None:
        self.image_to_display_multi.emit(image, illumination_source)

    def slot_current_configuration(self, configuration: "ChannelMode") -> None:
        self.signal_current_configuration.emit(configuration)
