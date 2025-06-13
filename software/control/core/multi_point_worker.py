import os
import threading
import time
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime

import cv2
import imageio as iio
import tifffile
import numpy as np
from numpy.typing import NDArray
import pandas as pd

import control.utils

os.environ["QT_API"] = "pyqt5"
from qtpy.QtCore import Signal, QObject
from PyQt5.QtWidgets import QApplication

from control._def import *
from control import utils, utils_acquisition
from control.piezo import PiezoStage
from control.utils_config import ChannelMode
from squid.abc import AbstractCamera, CameraFrame
import squid.logging

try:
    from control.multipoint_custom_script_entry_v2 import *

    print("custom multipoint script found")
except:
    pass


@dataclass
class CaptureInfo:
    position: squid.abc.Pos
    z_index: int
    capture_time: float
    configuration: ChannelMode
    save_directory: str
    file_id: str
    region_id: int
    fov: int
    configuration_idx: int


class MultiPointWorker(QObject):

    finished = Signal()
    image_to_display = Signal(np.ndarray)
    spectrum_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    # This should connect to UI updates only - it should not trigger a liveController.set_microscope_mode!
    # We call liveController.set_microscope_mode ourselves.
    signal_current_configuration = Signal(ChannelMode)
    signal_register_current_fov = Signal(float, float)
    signal_z_piezo_um = Signal(float)
    napari_layers_init = Signal(int, int, object)
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)

    def __init__(self, multiPointController):
        QObject.__init__(self)
        self.multiPointController = multiPointController
        self._log = squid.logging.get_logger(__class__.__name__)
        self._timing = control.utils.TimingManager("MultiPointWorker Timer Manager")
        self.start_time = 0
        self.camera: AbstractCamera = self.multiPointController.camera
        self.microcontroller = self.multiPointController.microcontroller
        self.usb_spectrometer = self.multiPointController.usb_spectrometer
        self.stage: squid.abc.AbstractStage = self.multiPointController.stage
        self.piezo: PiezoStage = self.multiPointController.piezo
        self.liveController = self.multiPointController.liveController
        self.autofocusController = self.multiPointController.autofocusController
        self.objectiveStore = self.multiPointController.objectiveStore
        self.channelConfigurationManager = self.multiPointController.channelConfigurationManager
        self.NX = self.multiPointController.NX
        self.NY = self.multiPointController.NY
        self.NZ = self.multiPointController.NZ
        self.Nt = self.multiPointController.Nt
        self.deltaX = self.multiPointController.deltaX
        self.deltaY = self.multiPointController.deltaY
        self.deltaZ = self.multiPointController.deltaZ
        self.dt = self.multiPointController.deltat
        self.do_autofocus = self.multiPointController.do_autofocus
        self.do_reflection_af = self.multiPointController.do_reflection_af
        self.display_resolution_scaling = self.multiPointController.display_resolution_scaling
        self.experiment_ID = self.multiPointController.experiment_ID
        self.base_path = self.multiPointController.base_path
        self.selected_configurations = self.multiPointController.selected_configurations
        self.use_piezo = self.multiPointController.use_piezo
        self.timestamp_acquisition_started = self.multiPointController.timestamp_acquisition_started
        self.time_point = 0
        self.af_fov_count = 0
        self.num_fovs = 0
        self.total_scans = 0
        self.scan_region_fov_coords_mm = self.multiPointController.scan_region_fov_coords_mm.copy()
        self.scan_region_coords_mm = self.multiPointController.scan_region_coords_mm
        self.scan_region_names = self.multiPointController.scan_region_names
        self.z_stacking_config = self.multiPointController.z_stacking_config  # default 'from bottom'
        self.z_range = self.multiPointController.z_range
        self.fluidics = self.multiPointController.fluidics

        self.headless = self.multiPointController.headless
        self.microscope = self.multiPointController.parent
        self.performance_mode = self.microscope and self.microscope.performance_mode

        self.crop = SEGMENTATION_CROP

        self.t_dpc = []
        self.t_inf = []
        self.t_over = []

        self.init_napari_layers = not USE_NAPARI_FOR_MULTIPOINT

        self.count = 0

        self.merged_image = None
        self.image_count = 0

        # This is for keeping track of whether or not we have the last image we tried to capture.
        # NOTE(imo): Once we do overlapping triggering, we'll want to keep a queue of images we are expecting.
        # For now, this is an improvement over blocking immediately while waiting for the next image!
        self._ready_for_next_trigger = threading.Event()
        # Set this to true so that the first frame capture can proceed.
        self._ready_for_next_trigger.set()
        # This is cleared when the image callback is no longer processing an image.  If true, an image is still
        # in flux and we need to make sure the object doesn't disappear.
        self._image_callback_idle = threading.Event()
        self._image_callback_idle.set()
        # This is protected by the threading event above (aka set after clear, take copy before set)
        self._current_capture_info: Optional[CaptureInfo] = None
        # This is only touched via the image callback path.  Don't touch it outside of there!
        self._current_round_images = {}

    def update_use_piezo(self, value):
        self.use_piezo = value
        self._log.info(f"MultiPointWorker: updated use_piezo to {value}")

    def run(self):
        this_image_callback_id = None
        try:
            self.start_time = time.perf_counter_ns()
            self.camera.start_streaming()
            this_image_callback_id = self.camera.add_frame_callback(self._image_callback)

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self.multiPointController.abort_acqusition_requested:
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break

                if self.fluidics and self.multiPointController.use_fluidics:
                    self.fluidics.update_port(self.time_point)  # use the port in PORT_LIST
                    # For MERFISH, before imaging, run the first 3 sequences (Add probe, wash buffer, imaging buffer)
                    self.fluidics.run_before_imaging()
                    self.fluidics.wait_for_completion()

                with self._timing.get_timer("run_single_time_point"):
                    self.run_single_time_point()

                if self.fluidics and self.multiPointController.use_fluidics:
                    # For MERFISH, after imaging, run the following 2 sequences (Cleavage buffer, SSC rinse)
                    self.fluidics.run_after_imaging()
                    self.fluidics.wait_for_completion()

                self.time_point = self.time_point + 1
                if self.dt == 0:  # continous acquisition
                    pass
                else:  # timed acquisition

                    # check if the aquisition has taken longer than dt or integer multiples of dt, if so skip the next time point(s)
                    while time.time() > self.timestamp_acquisition_started + self.time_point * self.dt:
                        self._log.info("skip time point " + str(self.time_point + 1))
                        self.time_point = self.time_point + 1

                    # check if it has reached Nt
                    if self.time_point == self.Nt:
                        break  # no waiting after taking the last time point

                    # wait until it's time to do the next acquisition
                    while time.time() < self.timestamp_acquisition_started + self.time_point * self.dt:
                        if self.multiPointController.abort_acqusition_requested:
                            self._log.debug("In run wait loop, abort_acquisition_requested=True")
                            break
                        self._sleep(0.001)

            elapsed_time = time.perf_counter_ns() - self.start_time
            self._log.info("Time taken for acquisition: " + str(elapsed_time / 10**9))

            # Since we use callback based acquisition, make sure to wait for any final images to come in
            self._wait_for_outstanding_callback_images()
            self._log.info(
                f"Time taken for acquisition/processing: {(time.perf_counter_ns() - self.start_time) / 1e9} [s]"
            )
        except TimeoutError as te:
            self._log.error(f"Operation timed out during acquisition, aborting acquisition!")
            self._log.error(te)
            self.multiPointController.request_abort_aquisition()
        finally:
            # We do this above, but there are some paths that skip the proper end of the acquisition so make
            # sure to always wait for final images here before removing our callback.
            self._wait_for_outstanding_callback_images()
            self._log.debug(self._timing.get_report())
            if this_image_callback_id:
                self.camera.remove_frame_callback(this_image_callback_id)
        if not self.headless:
            self.finished.emit()

    def _wait_for_outstanding_callback_images(self):
        # If there are outstanding frames, wait for them to come in.
        self._log.info("Waiting for any outstanding frames.")
        if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
            self._log.warning("Timed out waiting for the last outstanding frames at end of acquisition!")

        if not self._image_callback_idle.wait(self._frame_wait_timeout_s()):
            self._log.warning("Timed out waiting for the last image to process!")

        # No matter what, set the flags so things can continue
        self._ready_for_next_trigger.set()
        self._image_callback_idle.set()

    def wait_till_operation_is_completed(self):
        self.microcontroller.wait_till_operation_is_completed()

    def run_single_time_point(self):
        start = time.time()
        self.microcontroller.enable_joystick(False)

        self._log.debug("multipoint acquisition - time point " + str(self.time_point + 1))

        # for each time point, create a new folder
        current_path = os.path.join(self.base_path, self.experiment_ID, f"{self.time_point:0{FILE_ID_PADDING}}")
        utils.ensure_directory_exists(current_path)

        slide_path = os.path.join(self.base_path, self.experiment_ID)

        # create a dataframe to save coordinates
        self.initialize_coordinates_dataframe()

        # init z parameters, z range
        self.initialize_z_stack()

        with self._timing.get_timer("run_coordinate_acquisition"):
            self.run_coordinate_acquisition(current_path)

        # finished region scan
        self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)

        # Emit the xyz data for plotting
        if len(self.coordinates_pd) > 1:
            x = self.coordinates_pd["x (mm)"].values
            y = self.coordinates_pd["y (mm)"].values

            # When performing a z-stack (NZ > 1), only use the bottom z position for each (x,y) location
            if self.NZ > 1:
                # Create a copy to avoid modifying the original dataframe
                plot_df = self.coordinates_pd.copy()

                # Group by x, y, region and get the minimum z value for each group
                if "z_piezo (um)" in plot_df.columns:
                    # Calculate total z for grouping
                    plot_df["total_z"] = plot_df["z (um)"] + plot_df["z_piezo (um)"]
                    # Group by x, y, region and get indices of minimum z values
                    idx = plot_df.groupby(["x (mm)", "y (mm)", "region"])["total_z"].idxmin()
                    # Filter the dataframe to only include bottom z positions
                    plot_df = plot_df.loc[idx]
                    z = plot_df["z (um)"].values + plot_df["z_piezo (um)"].values
                else:
                    # Group by x, y, region and get indices of minimum z values
                    idx = plot_df.groupby(["x (mm)", "y (mm)", "region"])["z (um)"].idxmin()
                    # Filter the dataframe to only include bottom z positions
                    plot_df = plot_df.loc[idx]
                    z = plot_df["z (um)"].values

                # Get the filtered x, y, region values
                x = plot_df["x (mm)"].values
                y = plot_df["y (mm)"].values
                region = plot_df["region"].values
            else:
                # For single z acquisitions, use all points as before
                if "z_piezo (um)" in self.coordinates_pd.columns:
                    z = self.coordinates_pd["z (um)"].values + self.coordinates_pd["z_piezo (um)"].values
                else:
                    z = self.coordinates_pd["z (um)"].values
                region = self.coordinates_pd["region"].values

            x = np.array(x).astype(float)
            y = np.array(y).astype(float)
            z = np.array(z).astype(float)
            self.multiPointController.signal_coordinates.emit(x, y, z, region)

        utils.create_done_file(current_path)
        # TODO(imo): If anything throws above, we don't re-enable the joystick
        self.microcontroller.enable_joystick(True)
        self._log.debug(f"Single time point took: {time.time() - start} [s]")

    def initialize_z_stack(self):
        # z stacking config
        if self.z_stacking_config == "FROM TOP":
            self.deltaZ = -abs(self.deltaZ)
            self.move_to_z_level(self.z_range[1])
        else:
            self.move_to_z_level(self.z_range[0])

        self.z_pos = self.stage.get_pos().z_mm  # zpos at the beginning of the scan

    def initialize_coordinates_dataframe(self):
        base_columns = ["z_level", "x (mm)", "y (mm)", "z (um)", "time"]
        piezo_column = ["z_piezo (um)"] if self.use_piezo else []
        self.coordinates_pd = pd.DataFrame(columns=["region", "fov"] + base_columns + piezo_column)

    def update_coordinates_dataframe(self, region_id, z_level, pos: squid.abc.Pos, fov=None):
        base_data = {
            "z_level": [z_level],
            "x (mm)": [pos.x_mm],
            "y (mm)": [pos.y_mm],
            "z (um)": [pos.z_mm * 1000],
            "time": [datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")],
        }
        piezo_data = {"z_piezo (um)": [self.z_piezo_um]} if self.use_piezo else {}

        new_row = pd.DataFrame({"region": [region_id], "fov": [fov], **base_data, **piezo_data})

        self.coordinates_pd = pd.concat([self.coordinates_pd, new_row], ignore_index=True)

    def move_to_coordinate(self, coordinate_mm):
        print("moving to coordinate", coordinate_mm)

        # check if z is included in the coordinate
        if len(coordinate_mm) == 3:
            x_mm = coordinate_mm[0]
            y_mm = coordinate_mm[1]
            z_mm = coordinate_mm[2]

            self.stage.move_xy_to(x_mm, y_mm, blocking=False)
            self.move_to_z_level(z_mm)

            time.sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)
        else:
            x_mm = coordinate_mm[0]
            y_mm = coordinate_mm[1]

            self.stage.move_xy_to(x_mm, y_mm)
            time.sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)

    def move_to_z_level(self, z_mm):
        print("moving z")
        self.stage.move_z_to(z_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def run_coordinate_acquisition(self, current_path):
        n_regions = len(self.scan_region_coords_mm)

        for region_index, (region_id, coordinates) in enumerate(self.scan_region_fov_coords_mm.items()):

            self.signal_acquisition_progress.emit(region_index + 1, n_regions, self.time_point)

            self.num_fovs = len(coordinates)
            self.total_scans = self.num_fovs * self.NZ * len(self.selected_configurations)

            for fov_count, coordinate_mm in enumerate(coordinates):
                with self._timing.get_timer("move_to_coordinate"):
                    self.move_to_coordinate(coordinate_mm)
                with self._timing.get_timer("acquire_at_position"):
                    self.acquire_at_position(region_id, current_path, fov_count)

                if self.multiPointController.abort_acqusition_requested:
                    self.handle_acquisition_abort(current_path, region_id)
                    return

    def acquire_at_position(self, region_id, current_path, fov):
        if RUN_CUSTOM_MULTIPOINT and "multipoint_custom_script_entry" in globals():
            print("run custom multipoint")
            multipoint_custom_script_entry(self, current_path, region_id, fov)
            return

        if not self.perform_autofocus(region_id, fov):
            self._log.error(
                f"Autofocus failed in acquire_at_position.  Continuing to acquire anyway using the current z position (z={self.stage.get_pos().z_mm} [mm])"
            )

        if self.NZ > 1:
            self.prepare_z_stack()

        if self.use_piezo:
            self.z_piezo_um = self.piezo.position

        for z_level in range(self.NZ):
            file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"

            acquire_pos = self.stage.get_pos()
            metadata = {"x": acquire_pos.x_mm, "y": acquire_pos.y_mm, "z": acquire_pos.z_mm}
            self._log.info(f"Acquiring image: ID={file_ID}, Metadata={metadata}")

            # laser af characterization mode
            if self.do_reflection_af and self.microscope.laserAutofocusController.characterization_mode:
                image = self.microscope.laserAutofocusController.get_image()
                saving_path = os.path.join(current_path, file_ID + "_laser af camera" + ".bmp")
                iio.imwrite(saving_path, image)

            current_round_images = {}
            # iterate through selected modes
            for config_idx, config in enumerate(self.selected_configurations):
                if self.NZ == 1:  # TODO: handle z offset for z stack
                    self.handle_z_offset(config, True)

                # acquire image
                with self._timing.get_timer("acquire_camera_image"):
                    if "USB Spectrometer" not in config.name and "RGB" not in config.name:
                        self.acquire_camera_image(
                            config, file_ID, current_path, z_level, region_id=region_id, fov=fov, config_idx=config_idx
                        )
                    elif "RGB" in config.name:
                        self.acquire_rgb_image(config, file_ID, current_path, current_round_images, z_level)
                    else:
                        self.acquire_spectrometer_data(config, file_ID, current_path)

                if self.NZ == 1:  # TODO: handle z offset for z stack
                    self.handle_z_offset(config, False)

                current_image = (
                    fov * self.NZ * len(self.selected_configurations)
                    + z_level * len(self.selected_configurations)
                    + config_idx
                    + 1
                )
                self.signal_region_progress.emit(current_image, self.total_scans)

            # updates coordinates df
            self.update_coordinates_dataframe(region_id, z_level, acquire_pos, fov)
            self.signal_register_current_fov.emit(acquire_pos.x_mm, acquire_pos.y_mm)

            # check if the acquisition should be aborted
            if self.multiPointController.abort_acqusition_requested:
                self.handle_acquisition_abort(current_path, region_id)

            # update FOV counter
            self.af_fov_count = self.af_fov_count + 1

            if z_level < self.NZ - 1:
                self.move_z_for_stack()

        if self.NZ > 1:
            self.move_z_back_after_stack()

    def _select_config(self, config: ChannelMode):
        self.signal_current_configuration.emit(config)
        self.liveController.set_microscope_mode(config)
        self.wait_till_operation_is_completed()

    def perform_autofocus(self, region_id, fov):
        if not self.do_reflection_af:
            # contrast-based AF; perform AF only if when not taking z stack or doing z stack from center
            if (
                ((self.NZ == 1) or self.z_stacking_config == "FROM CENTER")
                and (self.do_autofocus)
                and (self.af_fov_count % Acquisition.NUMBER_OF_FOVS_PER_AF == 0)
            ):
                configuration_name_AF = MULTIPOINT_AUTOFOCUS_CHANNEL
                config_AF = self.channelConfigurationManager.get_channel_configuration_by_name(
                    self.objectiveStore.current_objective, configuration_name_AF
                )
                self._select_config(config_AF)
                if (
                    self.af_fov_count % Acquisition.NUMBER_OF_FOVS_PER_AF == 0
                ) or self.autofocusController.use_focus_map:
                    self.autofocusController.autofocus()
                    self.autofocusController.wait_till_autofocus_has_completed()
        else:
            self._log.info("laser reflection af")
            try:
                self.microscope.laserAutofocusController.move_to_target(0)
            except Exception as e:
                file_ID = f"{region_id}_focus_camera.bmp"
                saving_path = os.path.join(self.base_path, self.experiment_ID, str(self.time_point), file_ID)
                iio.imwrite(saving_path, self.microscope.laserAutofocusController.image)
                self._log.error(
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! laser AF failed !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                    exc_info=e,
                )
                return False
        return True

    def prepare_z_stack(self):
        # move to bottom of the z stack
        if self.z_stacking_config == "FROM CENTER":
            self.stage.move_z(-self.deltaZ * round((self.NZ - 1) / 2.0))
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def handle_z_offset(self, config, not_offset):
        if config.z_offset is not None:  # perform z offset for config, assume z_offset is in um
            if config.z_offset != 0.0:
                direction = 1 if not_offset else -1
                self._log.info("Moving Z offset" + str(config.z_offset * direction))
                self.stage.move_z(config.z_offset / 1000 * direction)
                self.wait_till_operation_is_completed()
                self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _image_callback(self, camera_frame: CameraFrame):
        try:
            self._image_callback_idle.clear()
            with self._timing.get_timer("_image_callback"):
                self._log.debug(f"In Image callback for frame_id={camera_frame.frame_id}")
                info = self._current_capture_info
                self._current_capture_info = None

                self._ready_for_next_trigger.set()
                if not info:
                    self._log.error("In image callback, no current capture info! Something is wrong. Aborting.")
                    self.multiPointController.request_abort_aquisition()
                    return

                image = camera_frame.frame
                if not camera_frame or image is None:
                    self._log.warning("image in frame callback is None. Something is really wrong, aborting!")
                    self.multiPointController.request_abort_aquisition()
                    return

                height, width = image.shape[:2]
                with self._timing.get_timer("crop_image"):
                    image_to_display = utils.crop_image(
                        image,
                        round(width * self.display_resolution_scaling),
                        round(height * self.display_resolution_scaling),
                    )
                with self._timing.get_timer("image_to_display*.emit"):
                    self.image_to_display.emit(image_to_display)
                    self.image_to_display_multi.emit(image_to_display, info.configuration.illumination_source)

                with self._timing.get_timer("save_image"):
                    self.save_image(image, info, camera_frame.is_color())
                with self._timing.get_timer("update_napari"):
                    self.update_napari(image, info)
        finally:
            self._image_callback_idle.set()

    def _frame_wait_timeout_s(self):
        return (self.camera.get_total_frame_time() / 1e3) + 10

    def acquire_camera_image(
        self, config, file_ID: str, current_path: str, k: int, region_id: int, fov: int, config_idx: int
    ):
        self._select_config(config)

        # trigger acquisition (including turning on the illumination) and read frame
        camera_illumination_time = self.camera.get_exposure_time()
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_on_illumination()
            camera_illumination_time = None
        elif self.liveController.trigger_mode == TriggerMode.HARDWARE:
            if "Fluorescence" in config.name and ENABLE_NL5 and NL5_USE_DOUT:
                # TODO(imo): This used to use the "reset_image_ready_flag=False" on the read_frame, but oinly the toupcam camera implementation had the
                #  "reset_image_ready_flag" arg, so this is broken for all other cameras.  Also this used to do some other funky stuff like setting internal camera flags.
                #   I am pretty sure this is broken!
                self.microscope.nl5.start_acquisition()
        # This is some large timeout that we use just so as to not block forever
        if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
            self._log.error("Frame callback never set _have_last_triggered_image callback! Aborting acquisition.")
            self.multiPointController.request_abort_aquisition()
            return

        # This should be a noop - we have the frame already.  Still, check!
        while not self.camera.get_ready_for_trigger():
            self._sleep(0.001)

        self._ready_for_next_trigger.clear()
        # Even though the capture time will be slightly after this, we need to capture and set the capture info
        # before the trigger to be 100% sure the callback doesn't stomp on it.
        # NOTE(imo): One level up from acquire_camera_image, we have acquire_pos.  We're careful to use that as
        # much as we can, but don't use it here because we'd rather take the position as close as possible to the
        # real capture time for the image info.  Ideally we'd use this position for the caller's acquire_pos as well.
        current_capture_info = CaptureInfo(
            position=self.stage.get_pos(),
            z_index=k,
            capture_time=time.time(),
            configuration=config,
            save_directory=current_path,
            file_id=file_ID,
            region_id=region_id,
            fov=fov,
            configuration_idx=config_idx,
        )
        self._current_capture_info = current_capture_info
        self.camera.send_trigger(illumination_time=camera_illumination_time)
        exposure_done_time = time.time() + self.camera.get_total_frame_time() / 1e3
        # Even though we can do overlapping triggers, we want to make sure that we don't move before our exposure
        # is done.  So we still need to at least sleep for the total frame time corresponding to this exposure.
        self._sleep(max(0.0, exposure_done_time - time.time()))

        # turn off the illumination if using software trigger
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_off_illumination()

        if not self.headless:
            QApplication.processEvents()

    def _sleep(self, sec):
        self._log.info(f"Sleeping for {sec} [s]")
        self.thread().usleep(max(1, round(sec * 1e6)))

    def acquire_rgb_image(self, config, file_ID, current_path, current_round_images, k):
        # go through the channels
        rgb_channels = ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]
        images = {}

        for config_ in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            if config_.name in rgb_channels:
                self._select_config(config_)

                # trigger acquisition (including turning on the illumination)
                if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                    # TODO(imo): use illum controller
                    self.liveController.turn_on_illumination()

                # read camera frame
                self.camera.send_trigger(illumination_time=self.camera.get_exposure_time())
                image = self.camera.read_frame()
                if image is None:
                    print("self.camera.read_frame() returned None")
                    continue

                # TODO(imo): use illum controller
                # turn off the illumination if using software trigger
                if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                    self.liveController.turn_off_illumination()

                # add the image to dictionary
                images[config_.name] = np.copy(image)

        # Check if the image is RGB or monochrome
        i_size = images["BF LED matrix full_R"].shape

        if len(i_size) == 3:
            # If already RGB, write and emit individual channels
            print("writing R, G, B channels")
            self.handle_rgb_channels(images, file_ID, current_path, config, k)
        else:
            # If monochrome, reconstruct RGB image
            print("constructing RGB image")
            self.construct_rgb_image(images, file_ID, current_path, config, k)

    def acquire_spectrometer_data(self, config, file_ID, current_path):
        if self.usb_spectrometer is not None:
            for l in range(N_SPECTRUM_PER_POINT):
                data = self.usb_spectrometer.read_spectrum()
                self.spectrum_to_display.emit(data)
                saving_path = os.path.join(
                    current_path, file_ID + "_" + str(config.name).replace(" ", "_") + "_" + str(l) + ".csv"
                )
                np.savetxt(saving_path, data, delimiter=",")

    def save_image(self, image: np.array, info: CaptureInfo, is_color: bool):
        # NOTE(imo): We silently fall back to individual image saving here.  We should warn or do something.
        if FILE_SAVING_OPTION == FileSavingOption.MULTI_PAGE_TIFF:
            metadata = {
                "z_level": info.z_index,
                "channel": info.configuration.name,
                "channel_index": info.configuration_idx,
                "region_id": info.region_id,
                "fov": info.fov,
                "x_mm": info.position.x_mm,
                "y_mm": info.position.y_mm,
                "z_mm": info.position.z_mm,
            }
            output_path = os.path.join(
                info.save_directory, f"{info.region_id}_{info.fov:0{FILE_ID_PADDING}}_stack.tiff"
            )
            with tifffile.TiffWriter(output_path, append=True) as tiff_writer:
                tiff_writer.write(image, metadata=metadata)
        else:
            saved_image = utils_acquisition.save_image(
                image=image,
                file_id=info.file_id,
                save_directory=info.save_directory,
                config=info.configuration,
                is_color=is_color,
            )

            if MERGE_CHANNELS:
                self._save_merged_image(saved_image, info.file_id, info.save_directory)

    def _save_merged_image(self, image: np.array, file_ID: str, current_path: str):
        self.image_count += 1

        if self.image_count == 1:
            self.merged_image = image
        else:
            self.merged_image = np.maximum(self.merged_image, image)

            if self.image_count == len(self.selected_configurations):
                if image.dtype == np.uint16:
                    saving_path = os.path.join(current_path, file_ID + "_merged" + ".tiff")
                else:
                    saving_path = os.path.join(current_path, file_ID + "_merged" + "." + Acquisition.IMAGE_FORMAT)

                iio.imwrite(saving_path, self.merged_image)
                self.image_count = 0

        return

    def update_napari(self, image, capture_info: CaptureInfo):
        if not self.performance_mode and (USE_NAPARI_FOR_MOSAIC_DISPLAY or USE_NAPARI_FOR_MULTIPOINT):

            if not self.init_napari_layers:
                print("init napari layers")
                self.init_napari_layers = True
                self.napari_layers_init.emit(image.shape[0], image.shape[1], image.dtype)
            objective_magnification = str(int(self.objectiveStore.get_current_objective_info()["magnification"]))
            self.napari_layers_update.emit(
                image,
                capture_info.position.x_mm,
                capture_info.position.y_mm,
                capture_info.z_index,
                objective_magnification + "x " + capture_info.configuration.name,
            )

    @staticmethod
    def handle_rgb_generation(current_round_images, capture_info: CaptureInfo):
        keys_to_check = ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]
        if all(key in current_round_images for key in keys_to_check):
            print("constructing RGB image")
            print(current_round_images["BF LED matrix full_R"].dtype)
            size = current_round_images["BF LED matrix full_R"].shape
            rgb_image = np.zeros((*size, 3), dtype=current_round_images["BF LED matrix full_R"].dtype)
            print(rgb_image.shape)
            rgb_image[:, :, 0] = current_round_images["BF LED matrix full_R"]
            rgb_image[:, :, 1] = current_round_images["BF LED matrix full_G"]
            rgb_image[:, :, 2] = current_round_images["BF LED matrix full_B"]

            # TODO(imo): There used to be a "display image" comment here, and then an unused cropped image.  Do we need to emit an image here?

            # write the image
            if len(rgb_image.shape) == 3:
                print("writing RGB image")
                if rgb_image.dtype == np.uint16:
                    iio.imwrite(
                        os.path.join(
                            capture_info.save_directory, capture_info.file_id + "_BF_LED_matrix_full_RGB.tiff"
                        ),
                        rgb_image,
                    )
                else:
                    iio.imwrite(
                        os.path.join(
                            capture_info.save_directory,
                            capture_info.file_id + "_BF_LED_matrix_full_RGB." + Acquisition.IMAGE_FORMAT,
                        ),
                        rgb_image,
                    )

    def handle_rgb_channels(self, images, capture_info: CaptureInfo):
        for channel in ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]:
            image_to_display = utils.crop_image(
                images[channel],
                round(images[channel].shape[1] * self.display_resolution_scaling),
                round(images[channel].shape[0] * self.display_resolution_scaling),
            )
            self.image_to_display.emit(image_to_display)
            self.image_to_display_multi.emit(image_to_display, capture_info.configuration.illumination_source)

            self.update_napari(images[channel], capture_info)

            file_name = (
                capture_info.file_id
                + "_"
                + channel.replace(" ", "_")
                + (".tiff" if images[channel].dtype == np.uint16 else "." + Acquisition.IMAGE_FORMAT)
            )
            iio.imwrite(os.path.join(capture_info.save_directory, file_name), images[channel])

    def construct_rgb_image(self, images, capture_info: CaptureInfo):
        rgb_image = np.zeros((*images["BF LED matrix full_R"].shape, 3), dtype=images["BF LED matrix full_R"].dtype)
        rgb_image[:, :, 0] = images["BF LED matrix full_R"]
        rgb_image[:, :, 1] = images["BF LED matrix full_G"]
        rgb_image[:, :, 2] = images["BF LED matrix full_B"]

        # send image to display
        height, width = rgb_image.shape[:2]
        image_to_display = utils.crop_image(
            rgb_image,
            round(width * self.display_resolution_scaling),
            round(height * self.display_resolution_scaling),
        )
        self.image_to_display.emit(image_to_display)
        self.image_to_display_multi.emit(image_to_display, capture_info.configuration.illumination_source)

        self.update_napari(rgb_image, capture_info)

        # write the RGB image
        print("writing RGB image")
        file_name = (
            capture_info.file_id
            + "_BF_LED_matrix_full_RGB"
            + (".tiff" if rgb_image.dtype == np.uint16 else "." + Acquisition.IMAGE_FORMAT)
        )
        iio.imwrite(os.path.join(capture_info.save_directory, file_name), rgb_image)

    def handle_acquisition_abort(self, current_path, region_id=0):
        # Move to the current region center
        region_center = self.scan_region_coords_mm[self.scan_region_names.index(region_id)]
        self.move_to_coordinate(region_center)

        # Save coordinates.csv
        self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)
        self.microcontroller.enable_joystick(True)

        self._wait_for_outstanding_callback_images()

    def move_z_for_stack(self):
        if self.use_piezo:
            self.z_piezo_um += self.deltaZ * 1000
            self.piezo.move_to(self.z_piezo_um)
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
            if MULTIPOINT_PIEZO_UPDATE_DISPLAY:
                self.signal_z_piezo_um.emit(self.z_piezo_um)
        else:
            self.stage.move_z(self.deltaZ)
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def move_z_back_after_stack(self):
        if self.use_piezo:
            self.z_piezo_um = self.z_piezo_um - self.deltaZ * 1000 * (self.NZ - 1)
            self.piezo.move_to(self.z_piezo_um)
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
            if MULTIPOINT_PIEZO_UPDATE_DISPLAY:
                self.signal_z_piezo_um.emit(self.z_piezo_um)
        else:
            if self.z_stacking_config == "FROM CENTER":
                rel_z_to_start = -self.deltaZ * (self.NZ - 1) + self.deltaZ * round((self.NZ - 1) / 2)
            else:
                rel_z_to_start = -self.deltaZ * (self.NZ - 1)

            self.stage.move_z(rel_z_to_start)
