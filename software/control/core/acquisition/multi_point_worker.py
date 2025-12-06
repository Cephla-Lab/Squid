import os
import queue
import time
from typing import Callable, List, Optional, Tuple, Type
from datetime import datetime

import imageio as iio
import numpy as np
import pandas as pd

from control._def import *
from control import utils
from control.core.autofocus import AutoFocusController
from control.core.configuration import ChannelConfigurationManager
from control.core.autofocus import LaserAutofocusController
from control.core.display import LiveController
from control.core.acquisition.multi_point_utils import (
    AcquisitionParameters,
    MultiPointControllerFunctions,
    OverallProgressUpdate,
    RegionProgressUpdate,
)
from control.core.navigation import ObjectiveStore
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.peripherals.piezo import PiezoStage
from control.utils_config import ChannelMode
from squid.abc import AbstractCamera, CameraFrame, CameraFrameFormat
import squid.logging
import control.core.acquisition.job_processing
from control.core.acquisition.job_processing import CaptureInfo, SaveImageJob, Job, JobImage, JobRunner, JobResult
from squid.config import CameraPixelFormat
from squid.utils.safe_callback import safe_callback
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag
from squid.utils.worker_manager import WorkerManager


class MultiPointWorker:
    def __init__(
        self,
        scope: Microscope,
        live_controller: LiveController,
        auto_focus_controller: Optional[AutoFocusController],
        laser_auto_focus_controller: Optional[LaserAutofocusController],
        objective_store: ObjectiveStore,
        channel_configuration_mananger: ChannelConfigurationManager,
        acquisition_parameters: AcquisitionParameters,
        callbacks: MultiPointControllerFunctions,
        abort_requested_fn: Callable[[], bool],
        request_abort_fn: Callable[[], None],
        extra_job_classes: list[type[Job]] | None = None,
        abort_on_failed_jobs: bool = True,
    ):
        self._log = squid.logging.get_logger(__class__.__name__)
        self._timing = utils.TimingManager("MultiPointWorker Timer Manager")
        self.microscope: Microscope = scope
        self.camera: AbstractCamera = scope.camera
        self.microcontroller: Microcontroller = scope.low_level_drivers.microcontroller
        self.stage: squid.abc.AbstractStage = scope.stage
        self.piezo: Optional[PiezoStage] = scope.addons.piezo_stage
        self.liveController = live_controller
        self.autofocusController: Optional[AutoFocusController] = auto_focus_controller
        self.laser_auto_focus_controller: Optional[LaserAutofocusController] = laser_auto_focus_controller
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigurationManager = channel_configuration_mananger
        self.fluidics = scope.addons.fluidics
        self.use_fluidics = acquisition_parameters.use_fluidics

        self.callbacks: MultiPointControllerFunctions = callbacks
        self.abort_requested_fn: Callable[[], bool] = abort_requested_fn
        self.request_abort_fn: Callable[[], None] = request_abort_fn
        self.NZ = acquisition_parameters.NZ
        self.deltaZ = acquisition_parameters.deltaZ

        self.Nt = acquisition_parameters.Nt
        self.dt = acquisition_parameters.deltat

        self.do_autofocus = acquisition_parameters.do_autofocus
        self.do_reflection_af = acquisition_parameters.do_reflection_autofocus
        self.use_piezo = acquisition_parameters.use_piezo
        self.display_resolution_scaling = acquisition_parameters.display_resolution_scaling

        self.experiment_ID = acquisition_parameters.experiment_ID
        self.base_path = acquisition_parameters.base_path
        self.experiment_path = os.path.join(self.base_path or "", self.experiment_ID or "")
        self.selected_configurations = acquisition_parameters.selected_configurations

        # Pre-compute acquisition metadata that remains constant throughout the run.
        try:
            pixel_factor = self.objectiveStore.get_pixel_size_factor()
            sensor_pixel_um = self.camera.get_pixel_size_binned_um()
            if pixel_factor is not None and sensor_pixel_um is not None:
                self._pixel_size_um = float(pixel_factor) * float(sensor_pixel_um)
            else:
                self._pixel_size_um = None
        except Exception:
            self._pixel_size_um = None
        self._time_increment_s = self.dt if self.Nt > 1 and self.dt > 0 else None
        self._physical_size_z_um = self.deltaZ if self.NZ > 1 else None
        self.timestamp_acquisition_started = acquisition_parameters.acquisition_start_time

        self.time_point = 0
        self.af_fov_count = 0
        self.num_fovs = 0
        self.total_scans = 0
        self._last_time_point_z_pos = {}
        self.scan_region_fov_coords_mm = (
            acquisition_parameters.scan_position_information.scan_region_fov_coords_mm.copy()
        )
        self.scan_region_coords_mm = acquisition_parameters.scan_position_information.scan_region_coords_mm
        self.scan_region_names = acquisition_parameters.scan_position_information.scan_region_names
        self.z_stacking_config = acquisition_parameters.z_stacking_config  # default 'from bottom'
        self.z_range = acquisition_parameters.z_range

        self.crop = SEGMENTATION_CROP

        self.t_dpc = []
        self.t_inf = []
        self.t_over = []

        self.count = 0

        self.merged_image = None
        self.image_count = 0

        # This is for keeping track of whether or not we have the last image we tried to capture.
        # NOTE(imo): Once we do overlapping triggering, we'll want to keep a queue of images we are expecting.
        # For now, this is an improvement over blocking immediately while waiting for the next image!
        # Thread-safe flags for synchronization
        self._ready_for_next_trigger = ThreadSafeFlag(initial=True)
        self._image_callback_idle = ThreadSafeFlag(initial=True)

        # Thread-safe capture info - accessed from main thread and camera callback thread
        self._current_capture_info: ThreadSafeValue[CaptureInfo] = ThreadSafeValue(None)
        # This is only touched via the image callback path.  Don't touch it outside of there!
        self._current_round_images = {}

        # Error tracking for debugging
        self._last_error: Optional[Exception] = None
        self._last_stack_trace: Optional[str] = None

        # Worker manager for timeout detection
        self._worker_manager = WorkerManager(max_workers=2)
        self._worker_manager.signals.timeout.connect(self._on_worker_timeout)

        # Configurable acquisition timeout (default 5 minutes)
        self._acquisition_timeout_ms = 300000

        job_classes = [SaveImageJob]
        if extra_job_classes:
            job_classes.extend(extra_job_classes)

        # For now, use 1 runner per job class.  There's no real reason/rationale behind this, though.  The runners
        # can all run any job type.  But 1 per is a reasonable arbitrary arrangement while we don't have a lot
        # of job types.  If we have a lot of custom jobs, this could cause problems via resource hogging.
        self._job_runners: List[Tuple[Type[Job], JobRunner]] = []
        self._log.info(f"Acquisition.USE_MULTIPROCESSING = {Acquisition.USE_MULTIPROCESSING}")
        for job_class in job_classes:
            self._log.info(f"Creating job runner for {job_class.__name__} jobs")
            job_runner = control.core.job_processing.JobRunner() if Acquisition.USE_MULTIPROCESSING else None
            if job_runner:
                job_runner.daemon = True
                job_runner.start()
            self._job_runners.append((job_class, job_runner))
        self._abort_on_failed_job = abort_on_failed_jobs

    def update_use_piezo(self, value: bool) -> None:
        self.use_piezo = value
        self._log.info(f"MultiPointWorker: updated use_piezo to {value}")

    def run(self) -> None:
        this_image_callback_id: Optional[str] = None
        try:
            start_time: int = time.perf_counter_ns()
            self.camera.start_streaming()
            this_image_callback_id = self.camera.add_frame_callback(self._image_callback)
            sleep_time: float = min(self.dt / 20.0, 0.5)

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self.abort_requested_fn():
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break

                if self.fluidics and self.use_fluidics:
                    self.fluidics.update_port(self.time_point)  # use the port in PORT_LIST
                    # For MERFISH, before imaging, run the first 3 sequences (Add probe, wash buffer, imaging buffer)
                    self.fluidics.run_before_imaging()
                    self.fluidics.wait_for_completion()

                with self._timing.get_timer("run_single_time_point"):
                    self.run_single_time_point()

                if self.fluidics and self.use_fluidics:
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
                        if self.abort_requested_fn():
                            self._log.debug("In run wait loop, abort_acquisition_requested=True")
                            break
                        self._sleep(sleep_time)

            elapsed_time: int = time.perf_counter_ns() - start_time
            self._log.info("Time taken for acquisition: " + str(elapsed_time / 10**9))

            # Since we use callback based acquisition, make sure to wait for any final images to come in
            self._wait_for_outstanding_callback_images()
            self._log.info(f"Time taken for acquisition/processing: {(time.perf_counter_ns() - start_time) / 1e9} [s]")
        except TimeoutError as te:
            self._log.error("Operation timed out during acquisition, aborting acquisition!")
            self._log.error(te)
            self.request_abort_fn()
        except Exception as e:
            self._log.exception(e)
            raise
        finally:
            # We do this above, but there are some paths that skip the proper end of the acquisition so make
            # sure to always wait for final images here before removing our callback.
            self._wait_for_outstanding_callback_images()
            self._log.debug(self._timing.get_report())
            if this_image_callback_id:
                self.camera.remove_frame_callback(this_image_callback_id)

            self._finish_jobs()
            self.callbacks.signal_acquisition_finished()

    def _wait_for_outstanding_callback_images(self) -> None:
        # If there are outstanding frames, wait for them to come in.
        self._log.info("Waiting for any outstanding frames.")
        if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
            self._log.warning("Timed out waiting for the last outstanding frames at end of acquisition!")

        if not self._image_callback_idle.wait(self._frame_wait_timeout_s()):
            self._log.warning("Timed out waiting for the last image to process!")

        # No matter what, set the flags so things can continue
        self._ready_for_next_trigger.set()
        self._image_callback_idle.set()

    def _finish_jobs(self, timeout_s: float = 10) -> None:
        self._summarize_runner_outputs()

        self._log.info(
            f"Waiting for jobs to finish on {len(self._job_runners)} job runners before shutting them down..."
        )
        timeout_time: float = time.time() + timeout_s

        def timed_out() -> bool:
            return time.time() > timeout_time

        def time_left() -> float:
            return max(timeout_time - time.time(), 0)

        for job_class, job_runner in self._job_runners:
            if job_runner is not None:
                while job_runner.has_pending():
                    if not timed_out():
                        time.sleep(0.1)
                    else:
                        self._log.error(
                            f"Timed out after {timeout_s} [s] waiting for jobs to finish.  Pending jobs for {job_class.__name__} abandoned!!!"
                        )
                        job_runner.kill()
                        break

                self._log.info("Trying to shut down job runner...")
                job_runner.shutdown(time_left())

    def wait_till_operation_is_completed(self) -> None:
        self.microcontroller.wait_till_operation_is_completed()

    def run_single_time_point(self) -> None:
        try:
            start: float = time.time()
            self.microcontroller.enable_joystick(False)

            self._log.debug("multipoint acquisition - time point " + str(self.time_point + 1))

            # for each time point, create a new folder
            if self.experiment_path:
                utils.ensure_directory_exists(str(self.experiment_path))
            current_path: str = os.path.join(self.experiment_path, f"{self.time_point:0{FILE_ID_PADDING}}")
            utils.ensure_directory_exists(str(current_path))

            # create a dataframe to save coordinates
            self.initialize_coordinates_dataframe()

            # init z parameters, z range
            self.initialize_z_stack()

            with self._timing.get_timer("run_coordinate_acquisition"):
                self.run_coordinate_acquisition(current_path)

            # finished region scan
            self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)

            utils.create_done_file(current_path)
            self._log.debug(f"Single time point took: {time.time() - start} [s]")
        finally:
            self.microcontroller.enable_joystick(True)

    def initialize_z_stack(self) -> None:
        # z stacking config
        if self.z_stacking_config == "FROM TOP":
            self.deltaZ = -abs(self.deltaZ)
            self.move_to_z_level(self.z_range[1])
        else:
            self.move_to_z_level(self.z_range[0])

        self.z_pos: float = self.stage.get_pos().z_mm  # zpos at the beginning of the scan

    def initialize_coordinates_dataframe(self) -> None:
        base_columns: List[str] = ["z_level", "x (mm)", "y (mm)", "z (um)", "time"]
        piezo_column: List[str] = ["z_piezo (um)"] if self.use_piezo else []
        self.coordinates_pd: pd.DataFrame = pd.DataFrame(columns=["region", "fov"] + base_columns + piezo_column)

    def update_coordinates_dataframe(self, region_id: str, z_level: int, pos: squid.abc.Pos, fov: Optional[int] = None) -> None:
        base_data = {
            "z_level": [z_level],
            "x (mm)": [pos.x_mm],
            "y (mm)": [pos.y_mm],
            "z (um)": [pos.z_mm * 1000],
            "time": [datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")],
        }
        piezo_data = {"z_piezo (um)": [self.z_piezo_um]} if self.use_piezo else {}

        new_row: pd.DataFrame = pd.DataFrame({"region": [region_id], "fov": [fov], **base_data, **piezo_data})

        self.coordinates_pd = pd.concat([self.coordinates_pd, new_row], ignore_index=True)

    def move_to_coordinate(self, coordinate_mm: Tuple[float, ...], region_id: str, fov: int) -> None:
        self._log.info(f"moving to coordinate {coordinate_mm}")
        x_mm: float = coordinate_mm[0]
        self.stage.move_x_to(x_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_X / 1000)

        y_mm: float = coordinate_mm[1]
        self.stage.move_y_to(y_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)

        # check if z is included in the coordinate
        if (self.do_reflection_af or self.do_autofocus) and self.time_point > 0:
            if (region_id, fov) in self._last_time_point_z_pos:
                last_z_mm: float = self._last_time_point_z_pos[(region_id, fov)]
                self.move_to_z_level(last_z_mm)
                self._log.info(f"Moved to last z position {last_z_mm} [mm]")
                return
            else:
                self._log.warning(f"No last z position found for region {region_id}, fov {fov}")
        if len(coordinate_mm) == 3:
            z_mm: float = coordinate_mm[2]
            self.move_to_z_level(z_mm)

    def move_to_z_level(self, z_mm: float) -> None:
        print("moving z")
        self.stage.move_z_to(z_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _summarize_runner_outputs(self) -> bool:
        none_failed: bool = True
        for job_class, job_runner in self._job_runners:
            if job_runner is None:
                continue
            out_queue = job_runner.output_queue()
            try:
                job_result: JobResult = out_queue.get_nowait()
                # TODO(imo): Should we abort if there is a failure?
                none_failed = none_failed and self._summarize_job_result(job_result)
            except queue.Empty:
                continue

        return none_failed

    def _summarize_job_result(self, job_result: JobResult) -> bool:
        """
        Prints a summary, then returns True if the result was successful or False otherwise.
        """
        if job_result.exception is not None:
            self._log.error(f"Error while running job {job_result.job_id}: {job_result.exception}")
            return False
        else:
            self._log.info(f"Got result for job {job_result.job_id}, it completed!")
            return True

    def run_coordinate_acquisition(self, current_path: str) -> None:
        n_regions: int = len(self.scan_region_coords_mm)

        for region_index, (region_id, coordinates) in enumerate(self.scan_region_fov_coords_mm.items()):
            self.callbacks.signal_overall_progress(
                OverallProgressUpdate(
                    current_region=region_index + 1,
                    total_regions=n_regions,
                    current_timepoint=self.time_point,
                    total_timepoints=self.Nt,
                )
            )
            self.num_fovs = len(coordinates)
            self.total_scans = self.num_fovs * self.NZ * len(self.selected_configurations)

            for fov, coordinate_mm in enumerate(coordinates):
                # Just so the job result queues don't get too big, check and print a summary of intermediate results here
                with self._timing.get_timer("job result summaries"):
                    if not self._summarize_runner_outputs() and self._abort_on_failed_job:
                        self._log.error("Some jobs failed, aborting acquisition because abort_on_failed_job=True")
                        self.request_abort_fn()
                        return

                with self._timing.get_timer("move_to_coordinate"):
                    self.move_to_coordinate(coordinate_mm, region_id, fov)
                with self._timing.get_timer("acquire_at_position"):
                    self.acquire_at_position(region_id, current_path, fov)

                if self.abort_requested_fn():
                    self.handle_acquisition_abort(current_path)
                    return

    def acquire_at_position(self, region_id: str, current_path: str, fov: int) -> None:
        if not self.perform_autofocus(region_id, fov):
            self._log.error(
                f"Autofocus failed in acquire_at_position.  Continuing to acquire anyway using the current z position (z={self.stage.get_pos().z_mm} [mm])"
            )

        if self.NZ > 1:
            self.prepare_z_stack()

        if self.use_piezo:
            self.z_piezo_um: float = self.piezo.position

        for z_level in range(self.NZ):
            file_ID: str = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"

            acquire_pos: squid.abc.Pos = self.stage.get_pos()
            metadata: Dict[str, float] = {"x": acquire_pos.x_mm, "y": acquire_pos.y_mm, "z": acquire_pos.z_mm}
            self._log.info(f"Acquiring image: ID={file_ID}, Metadata={metadata}")

            if z_level == 0 and (self.do_reflection_af or self.do_autofocus) and self.Nt > 1:
                self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

            # laser af characterization mode
            if self.laser_auto_focus_controller and self.laser_auto_focus_controller.characterization_mode:
                image: np.ndarray = self.laser_auto_focus_controller.get_image()
                saving_path: str = os.path.join(current_path, file_ID + "_laser af camera" + ".bmp")
                iio.imwrite(saving_path, image)

            # iterate through selected modes
            for config_idx, config in enumerate(self.selected_configurations):
                if self.NZ == 1:  # TODO: handle z offset for z stack
                    self.handle_z_offset(config, True)

                # acquire image
                with self._timing.get_timer("acquire_camera_image"):
                    # TODO(imo): This really should not look for a string in a user configurable name.  We
                    # need some proper flag on the config to signal this instead...
                    if "RGB" in config.name:
                        self.acquire_rgb_image(config, file_ID, current_path, z_level, region_id, fov)
                    else:
                        self.acquire_camera_image(
                            config, file_ID, current_path, z_level, region_id=region_id, fov=fov, config_idx=config_idx
                        )

                if self.NZ == 1:  # TODO: handle z offset for z stack
                    self.handle_z_offset(config, False)

                current_image: int = (
                    fov * self.NZ * len(self.selected_configurations)
                    + z_level * len(self.selected_configurations)
                    + config_idx
                    + 1
                )
                self.callbacks.signal_region_progress(
                    RegionProgressUpdate(current_fov=current_image, region_fovs=self.total_scans)
                )

            # updates coordinates df
            self.update_coordinates_dataframe(region_id, z_level, acquire_pos, fov)
            self.callbacks.signal_current_fov(acquire_pos.x_mm, acquire_pos.y_mm)

            # check if the acquisition should be aborted
            if self.abort_requested_fn():
                self.handle_acquisition_abort(current_path)

            # update FOV counter
            self.af_fov_count = self.af_fov_count + 1

            if z_level < self.NZ - 1:
                self.move_z_for_stack()

        if self.NZ > 1:
            self.move_z_back_after_stack()

    def _select_config(self, config: ChannelMode) -> None:
        self.callbacks.signal_current_configuration(config)
        self.liveController.set_microscope_mode(config)
        self.wait_till_operation_is_completed()

    def perform_autofocus(self, region_id: str, fov: int) -> bool:
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
                self.laser_auto_focus_controller.move_to_target(0)
            except Exception as e:
                file_ID: str = f"{region_id}_focus_camera.bmp"
                saving_path: str = os.path.join(self.base_path, self.experiment_ID, str(self.time_point), file_ID)
                iio.imwrite(saving_path, self.laser_auto_focus_controller.image)
                self._log.error(
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! laser AF failed !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                    exc_info=e,
                )
                return False
        return True

    def prepare_z_stack(self) -> None:
        # move to bottom of the z stack
        if self.z_stacking_config == "FROM CENTER":
            self.stage.move_z(-self.deltaZ * round((self.NZ - 1) / 2.0))
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def handle_z_offset(self, config: ChannelMode, not_offset: bool) -> None:
        if config.z_offset is not None:  # perform z offset for config, assume z_offset is in um
            if config.z_offset != 0.0:
                direction: int = 1 if not_offset else -1
                self._log.info("Moving Z offset" + str(config.z_offset * direction))
                self.stage.move_z(config.z_offset / 1000 * direction)
                self.wait_till_operation_is_completed()
                self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _image_callback(self, camera_frame: CameraFrame) -> None:
        """
        Handle incoming camera frame.

        Wrapped with safe_callback to contain exceptions and prevent crashes.
        """
        if self._ready_for_next_trigger.is_set():
            self._log.warning(
                "Got an image in the image callback, but we didn't send a trigger. Ignoring the image."
            )
            return

        self._image_callback_idle.clear()
        try:
            result: Any = safe_callback(
                self._process_camera_frame,
                camera_frame,
                on_error=self._handle_callback_error
            )

            if not result.success:
                self._log.error(f"Image callback failed, aborting: {result.error}")
                self.request_abort_fn()
        finally:
            self._image_callback_idle.set()

    def _process_camera_frame(self, camera_frame: CameraFrame) -> None:
        """
        Process a camera frame - extracted from _image_callback for error containment.
        """
        with self._timing.get_timer("_image_callback"):
            self._log.debug(f"In Image callback for frame_id={camera_frame.frame_id}")
            info: Optional[CaptureInfo] = self._current_capture_info.get_and_clear()

            self._ready_for_next_trigger.set()
            if not info:
                raise RuntimeError("No current capture info! Something is wrong.")

            image: np.ndarray = camera_frame.frame
            if not camera_frame or image is None:
                raise RuntimeError("Image in frame callback is None.")

            with self._timing.get_timer("job creation and dispatch"):
                for job_class, job_runner in self._job_runners:
                    job = job_class(capture_info=info, capture_image=JobImage(image_array=image))
                    if job_runner is not None:
                        if not job_runner.dispatch(job):
                            raise RuntimeError("Failed to dispatch multiprocessing job!")
                    else:
                        # NOTE(imo): We don't have any way of people using results, so for now just
                        # run and ignore it.
                        job.run()

            height: int
            width: int
            height, width = image.shape[:2]
            with self._timing.get_timer("image_to_display*.emit"):
                self.callbacks.signal_new_image(camera_frame, info)

    def _handle_callback_error(self, error: Exception, stack_trace: str) -> None:
        """
        Handle errors from image callback - store for debugging.
        """
        self._last_error = error
        self._last_stack_trace = stack_trace

    def _on_worker_timeout(self, task_name: str) -> None:
        """Handle worker timeout - abort gracefully instead of hanging."""
        self._log.error(f"Worker '{task_name}' timed out, aborting acquisition")
        self.request_abort_fn()

    def _frame_wait_timeout_s(self) -> float:
        return (self.camera.get_total_frame_time() / 1e3) + 10

    def acquire_camera_image(
        self, config: ChannelMode, file_ID: str, current_path: str, k: int, region_id: str, fov: int, config_idx: int
    ) -> None:
        self._select_config(config)

        # trigger acquisition (including turning on the illumination) and read frame
        camera_illumination_time: Optional[float] = self.camera.get_exposure_time()
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_on_illumination()
            self.wait_till_operation_is_completed()
            camera_illumination_time = None
        elif self.liveController.trigger_mode == TriggerMode.HARDWARE:
            if "Fluorescence" in config.name and ENABLE_NL5 and NL5_USE_DOUT:
                # TODO(imo): This used to use the "reset_image_ready_flag=False" on the read_frame, but oinly the toupcam camera implementation had the
                #  "reset_image_ready_flag" arg, so this is broken for all other cameras.  Also this used to do some other funky stuff like setting internal camera flags.
                #   I am pretty sure this is broken!
                self.microscope.addons.nl5.start_acquisition()
        # This is some large timeout that we use just so as to not block forever
        with self._timing.get_timer("_ready_for_next_trigger.wait"):
            if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
                self._log.error("Frame callback never set _have_last_triggered_image callback! Aborting acquisition.")
                self.request_abort_fn()
                return
        with self._timing.get_timer("get_ready_for_trigger re-check"):
            # This should be a noop - we have the frame already.  Still, check!
            while not self.camera.get_ready_for_trigger():
                self._sleep(0.001)

            self._ready_for_next_trigger.clear()
        with self._timing.get_timer("current_capture_info ="):
            # Even though the capture time will be slightly after this, we need to capture and set the capture info
            # before the trigger to be 100% sure the callback doesn't stomp on it.
            # NOTE(imo): One level up from acquire_camera_image, we have acquire_pos.  We're careful to use that as
            # much as we can, but don't use it here because we'd rather take the position as close as possible to the
            # real capture time for the image info.  Ideally we'd use this position for the caller's acquire_pos as well.
            current_capture_info: CaptureInfo = CaptureInfo(
                position=self.stage.get_pos(),
                z_index=k,
                capture_time=time.time(),
                z_piezo_um=(self.z_piezo_um if self.use_piezo else None),
                configuration=config,
                save_directory=current_path,
                file_id=file_ID,
                region_id=region_id,
                fov=fov,
                configuration_idx=config_idx,
                time_point=self.time_point,
                total_time_points=self.Nt,
                total_z_levels=self.NZ,
                total_channels=len(self.selected_configurations),
                channel_names=[cfg.name for cfg in self.selected_configurations],
                experiment_path=self.experiment_path,
                time_increment_s=self._time_increment_s,
                physical_size_z_um=self._physical_size_z_um,
                physical_size_x_um=self._pixel_size_um,
                physical_size_y_um=self._pixel_size_um,
            )
            self._current_capture_info.set(current_capture_info)
        with self._timing.get_timer("send_trigger"):
            self.camera.send_trigger(illumination_time=camera_illumination_time)

        with self._timing.get_timer("exposure_time_done_sleep_hw or wait_for_image_sw"):
            if self.liveController.trigger_mode == TriggerMode.HARDWARE:
                exposure_done_time: float = time.time() + self.camera.get_total_frame_time() / 1e3
                # Even though we can do overlapping triggers, we want to make sure that we don't move before our exposure
                # is done.  So we still need to at least sleep for the total frame time corresponding to this exposure.
                self._sleep(max(0.0, exposure_done_time - time.time()))
            else:
                # In SW trigger mode (or anything not HARDWARE mode), there's indeterminism in the trigger timing.
                # To overcome this, just wait until the frame for this capture actually comes into the image
                # callback.  That way we know we have it.  This also helps by making sure the illumination for this
                # frame is on from before the trigger until after we get the frame (which guarantees it will be on
                # for the full exposure).
                #
                # If we wait for longer than 5x the exposure + 2 seconds, abort the acquisition because something is
                # wrong.
                non_hw_frame_timeout: float = 5 * self.camera.get_total_frame_time() / 1e3 + 2
                if not self._ready_for_next_trigger.wait(non_hw_frame_timeout):
                    self._log.error("Timed out waiting {non_hw_frame_timeout} [s] for a frame, aborting acquisition.")
                    self.request_abort_fn()
                    # Let this fall through so we still turn off illumination.  Let the caller actually break out
                    # of the acquisition.

        # turn off the illumination if using software trigger
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_off_illumination()

    def _sleep(self, sec: float) -> None:
        time_to_sleep: float = max(sec, 1e-6)
        # self._log.debug(f"Sleeping for {time_to_sleep} [s]")
        time.sleep(time_to_sleep)

    def acquire_rgb_image(self, config: ChannelMode, file_ID: str, current_path: str, k: int, region_id: str, fov: int) -> None:
        # go through the channels
        rgb_channels: List[str] = ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]
        images: Dict[str, np.ndarray] = {}

        for config_ in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            if config_.name in rgb_channels:
                self._select_config(config_)

                # trigger acquisition (including turning on the illumination)
                if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                    # TODO(imo): use illum controller
                    self.liveController.turn_on_illumination()
                    self.wait_till_operation_is_completed()

                # read camera frame
                self.camera.send_trigger(illumination_time=self.camera.get_exposure_time())
                image: Optional[np.ndarray] = self.camera.read_frame()
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
        i_size: Tuple[int, ...] = images["BF LED matrix full_R"].shape

        current_capture_info: CaptureInfo = CaptureInfo(
            position=self.stage.get_pos(),
            z_index=k,
            capture_time=time.time(),
            z_piezo_um=(self.z_piezo_um if self.use_piezo else None),
            configuration=config,
            save_directory=current_path,
            file_id=file_ID,
            region_id=region_id,
            fov=fov,
            configuration_idx=config.id,
            time_point=self.time_point,
            total_time_points=self.Nt,
            total_z_levels=self.NZ,
            total_channels=len(self.selected_configurations),
            channel_names=[cfg.name for cfg in self.selected_configurations],
            experiment_path=self.experiment_path,
            time_increment_s=self._time_increment_s,
            physical_size_z_um=self._physical_size_z_um,
            physical_size_x_um=self._pixel_size_um,
            physical_size_y_um=self._pixel_size_um,
        )

        if len(i_size) == 3:
            # If already RGB, write and emit individual channels
            print("writing R, G, B channels")
            self.handle_rgb_channels(images, current_capture_info)
        else:
            # If monochrome, reconstruct RGB image
            print("constructing RGB image")
            self.construct_rgb_image(images, current_capture_info)

    @staticmethod
    def handle_rgb_generation(current_round_images: Dict[str, np.ndarray], capture_info: CaptureInfo) -> None:
        keys_to_check: List[str] = ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]
        if all(key in current_round_images for key in keys_to_check):
            print("constructing RGB image")
            print(current_round_images["BF LED matrix full_R"].dtype)
            size: Tuple[int, ...] = current_round_images["BF LED matrix full_R"].shape
            rgb_image: np.ndarray = np.zeros((*size, 3), dtype=current_round_images["BF LED matrix full_R"].dtype)
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

    def handle_rgb_channels(self, images: Dict[str, np.ndarray], capture_info: CaptureInfo) -> None:
        for channel in ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]:
            image_to_display: np.ndarray = utils.crop_image(
                images[channel],
                round(images[channel].shape[1] * self.display_resolution_scaling),
                round(images[channel].shape[0] * self.display_resolution_scaling),
            )
            self.callbacks.signal_new_image(
                CameraFrame(
                    self.image_count,
                    capture_info.capture_time,
                    image_to_display,
                    CameraFrameFormat.RAW,
                    CameraPixelFormat.MONO16,
                ),
                capture_info,
            )

            file_name: str = (
                capture_info.file_id
                + "_"
                + channel.replace(" ", "_")
                + (".tiff" if images[channel].dtype == np.uint16 else "." + Acquisition.IMAGE_FORMAT)
            )
            iio.imwrite(os.path.join(capture_info.save_directory, file_name), images[channel])

    def construct_rgb_image(self, images: Dict[str, np.ndarray], capture_info: CaptureInfo) -> None:
        rgb_image: np.ndarray = np.zeros((*images["BF LED matrix full_R"].shape, 3), dtype=images["BF LED matrix full_R"].dtype)
        rgb_image[:, :, 0] = images["BF LED matrix full_R"]
        rgb_image[:, :, 1] = images["BF LED matrix full_G"]
        rgb_image[:, :, 2] = images["BF LED matrix full_B"]

        # send image to display
        height: int
        width: int
        height, width = rgb_image.shape[:2]
        image_to_display: np.ndarray = utils.crop_image(
            rgb_image,
            round(width * self.display_resolution_scaling),
            round(height * self.display_resolution_scaling),
        )
        self.callbacks.signal_new_image(
            CameraFrame(
                self.image_count,
                capture_info.capture_time,
                image_to_display,
                CameraFrameFormat.RGB,
                CameraPixelFormat.RGB48,
            ),
            capture_info,
        )

        # write the RGB image
        print("writing RGB image")
        file_name: str = (
            capture_info.file_id
            + "_BF_LED_matrix_full_RGB"
            + (".tiff" if rgb_image.dtype == np.uint16 else "." + Acquisition.IMAGE_FORMAT)
        )
        iio.imwrite(os.path.join(capture_info.save_directory, file_name), rgb_image)

    def handle_acquisition_abort(self, current_path: str) -> None:
        # Save coordinates.csv
        self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)
        self.microcontroller.enable_joystick(True)

        self._wait_for_outstanding_callback_images()

    def move_z_for_stack(self) -> None:
        if self.use_piezo:
            self.z_piezo_um += self.deltaZ * 1000
            self.piezo.move_to(self.z_piezo_um)
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            self.stage.move_z(self.deltaZ)
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def move_z_back_after_stack(self) -> None:
        if self.use_piezo:
            self.z_piezo_um = self.z_piezo_um - self.deltaZ * 1000 * (self.NZ - 1)
            self.piezo.move_to(self.z_piezo_um)
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            rel_z_to_start: float
            if self.z_stacking_config == "FROM CENTER":
                rel_z_to_start = -self.deltaZ * (self.NZ - 1) + self.deltaZ * round((self.NZ - 1) / 2)
            else:
                rel_z_to_start = -self.deltaZ * (self.NZ - 1)

            self.stage.move_z(rel_z_to_start)
