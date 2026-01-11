import os
import queue
import threading
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple, Type
from datetime import datetime

import imageio as iio
import numpy as np
import pandas as pd

from control._def import *
from control._def import DOWNSAMPLED_VIEW_JOB_TIMEOUT_S, DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S
import control._def
from control import utils
from control.core.auto_focus_controller import AutoFocusController
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.core.multi_point_utils import (
    AcquisitionParameters,
    MultiPointControllerFunctions,
    OverallProgressUpdate,
    RegionProgressUpdate,
    PlateViewInit,
    PlateViewUpdate,
)
from control.core.objective_store import ObjectiveStore
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.piezo import PiezoStage
from control.utils_config import ChannelMode
from squid.abc import AbstractCamera, CameraFrame, CameraFrameFormat
import squid.logging
import control.core.job_processing
from control.core.job_processing import (
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    AcquisitionInfo,
    Job,
    JobImage,
    JobRunner,
    JobResult,
    DownsampledViewJob,
    DownsampledViewResult,
)
from control.core.downsampled_views import (
    DownsampledViewManager,
    calculate_overlap_pixels,
    parse_well_id,
    ensure_plate_resolution_in_well_resolutions,
)
from control.core.backpressure import BackpressureController
from squid.config import CameraPixelFormat


class SummarizeResult(NamedTuple):
    """Result from processing job output queues."""

    none_failed: bool  # True if no jobs failed (or no results to process)
    had_results: bool  # True if any results were pulled from queue


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

        self.acquisition_info = AcquisitionInfo(
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

        self.skip_saving = acquisition_parameters.skip_saving
        job_classes = []
        use_ome_tiff = FILE_SAVING_OPTION == FileSavingOption.OME_TIFF
        if not self.skip_saving:
            if use_ome_tiff:
                job_classes.append(SaveOMETiffJob)
            else:
                job_classes.append(SaveImageJob)

        if extra_job_classes:
            job_classes.extend(extra_job_classes)

        # Downsampled view generation setup
        # Only generate downsampled views for well-based acquisitions
        is_select_wells = acquisition_parameters.xy_mode == "Select Wells"
        is_loaded_wells = acquisition_parameters.xy_mode == "Load Coordinates" and self._is_well_based_acquisition()
        self._generate_downsampled_views = acquisition_parameters.generate_downsampled_views and (
            is_select_wells or is_loaded_wells
        )
        self._downsampled_view_manager: Optional[DownsampledViewManager] = None
        self._downsampled_well_resolutions_um = acquisition_parameters.downsampled_well_resolutions_um or [
            5.0,
            10.0,
            20.0,
        ]
        self._downsampled_plate_resolution_um = acquisition_parameters.downsampled_plate_resolution_um
        self._downsampled_z_projection = acquisition_parameters.downsampled_z_projection
        self._downsampled_interpolation_method = acquisition_parameters.downsampled_interpolation_method
        self._save_downsampled_well_images = acquisition_parameters.save_downsampled_well_images
        self._plate_num_rows = acquisition_parameters.plate_num_rows
        self._plate_num_cols = acquisition_parameters.plate_num_cols
        self._overlap_pixels: Optional[Tuple[int, int, int, int]] = None
        self._region_fov_counts: Dict[str, int] = {}  # Track total FOVs per region

        if self._generate_downsampled_views:
            # Ensure plate resolution is in well resolutions
            self._downsampled_well_resolutions_um = ensure_plate_resolution_in_well_resolutions(
                self._downsampled_well_resolutions_um,
                self._downsampled_plate_resolution_um,
            )
            # Add DownsampledViewJob to job classes
            job_classes.append(DownsampledViewJob)
            # Pre-calculate FOV counts per region
            for region_id, coords in self.scan_region_fov_coords_mm.items():
                self._region_fov_counts[region_id] = len(coords)
            mode = "Select Wells" if is_select_wells else "Load Coordinates (auto-detected)"
            self._log.info(
                f"Downsampled view generation enabled ({mode}). Resolutions: {self._downsampled_well_resolutions_um} um"
            )

        # Initialize backpressure controller for throttling acquisition when queue fills up
        self._backpressure = BackpressureController(
            max_jobs=control._def.ACQUISITION_MAX_PENDING_JOBS,
            max_mb=control._def.ACQUISITION_MAX_PENDING_MB,
            timeout_s=control._def.ACQUISITION_THROTTLE_TIMEOUT_S,
            enabled=control._def.ACQUISITION_THROTTLING_ENABLED,
        )

        # For now, use 1 runner per job class.  There's no real reason/rationale behind this, though.  The runners
        # can all run any job type.  But 1 per is a reasonable arbitrary arrangement while we don't have a lot
        # of job types.  If we have a lot of custom jobs, this could cause problems via resource hogging.
        self._job_runners: List[Tuple[Type[Job], JobRunner]] = []
        self._log.info(f"Acquisition.USE_MULTIPROCESSING = {Acquisition.USE_MULTIPROCESSING}")

        # Get the current log file path to share with subprocess workers
        log_file_path = squid.logging.get_current_log_file_path()

        for job_class in job_classes:
            self._log.info(f"Creating job runner for {job_class.__name__} jobs")
            job_runner = (
                control.core.job_processing.JobRunner(
                    self.acquisition_info,
                    cleanup_stale_ome_files=use_ome_tiff,
                    log_file_path=log_file_path,
                    # Pass backpressure shared values for cross-process tracking
                    bp_pending_jobs=self._backpressure.pending_jobs_value,
                    bp_pending_bytes=self._backpressure.pending_bytes_value,
                    bp_capacity_event=self._backpressure.capacity_event,
                )
                if Acquisition.USE_MULTIPROCESSING
                else None
            )
            if job_runner:
                job_runner.daemon = True
                job_runner.start()
            self._job_runners.append((job_class, job_runner))
        self._abort_on_failed_job = abort_on_failed_jobs

    def update_use_piezo(self, value):
        self.use_piezo = value
        self._log.info(f"MultiPointWorker: updated use_piezo to {value}")

    def _is_well_based_acquisition(self) -> bool:
        """Check if regions represent a valid well-based acquisition.

        Returns True if:
        - All region names are valid well IDs (A1, B2, etc.)
        - All regions have the same FOV grid pattern (same distinct X and Y counts)
        """
        if not self.scan_region_names:
            self._log.debug(
                "_is_well_based_acquisition: no scan_region_names defined; treating as non well-based acquisition"
            )
            return False

        # Check all region names are valid well IDs using parse_well_id
        for region_id in self.scan_region_names:
            if not region_id:
                self._log.debug(
                    "_is_well_based_acquisition: encountered empty region_id in scan_region_names; "
                    "treating as invalid well-based acquisition"
                )
                return False
            try:
                parse_well_id(region_id)
            except ValueError as exc:
                self._log.debug(
                    "_is_well_based_acquisition: region_id '%s' is not a valid well ID: %s; "
                    "treating as invalid well-based acquisition",
                    region_id,
                    exc,
                )
                return False

        # Check all wells have same grid size
        grid_sizes = set()
        for region_id, coords in self.scan_region_fov_coords_mm.items():
            if not coords:
                self._log.debug(
                    "_is_well_based_acquisition: region '%s' has no FOV coordinates; skipping in grid-size check",
                    region_id,
                )
                continue
            x_positions = set(round(c[0], 4) for c in coords)  # Round to avoid float precision issues
            y_positions = set(round(c[1], 4) for c in coords)
            grid_sizes.add((len(x_positions), len(y_positions)))

        # All wells should have the same grid pattern
        if not grid_sizes:
            self._log.debug(
                "_is_well_based_acquisition: no valid FOV coordinates found for any region; "
                "treating as non well-based acquisition"
            )
            return False

        if len(grid_sizes) > 1:
            self._log.debug(
                "_is_well_based_acquisition: inconsistent FOV grid sizes detected across wells: %s; "
                "treating as non well-based acquisition",
                grid_sizes,
            )
            return False

        self._log.debug(
            "_is_well_based_acquisition: valid well-based acquisition detected with grid size %s",
            next(iter(grid_sizes)),
        )
        return True

    def run(self):
        this_image_callback_id = None
        try:
            start_time = time.perf_counter_ns()
            self.camera.start_streaming()
            this_image_callback_id = self.camera.add_frame_callback(self._image_callback)
            sleep_time = min(self.dt / 20.0, 0.5)

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

            elapsed_time = time.perf_counter_ns() - start_time
            self._log.info("Time taken for acquisition: " + str(elapsed_time / 10**9))

            # Since we use callback based acquisition, make sure to wait for any final images to come in
            self._wait_for_outstanding_callback_images()
            self._log.info(f"Time taken for acquisition/processing: {(time.perf_counter_ns() - start_time) / 1e9} [s]")
        except TimeoutError as te:
            self._log.error(f"Operation timed out during acquisition, aborting acquisition!")
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

    def _finish_jobs(self, timeout_s=10):
        # Drain and summarize all currently available job results before waiting for completion
        self._summarize_runner_outputs(drain_all=True)

        self._log.info(
            f"Waiting for jobs to finish on {len(self._job_runners)} job runners before shutting them down..."
        )
        timeout_time = time.time() + timeout_s

        def timed_out():
            return time.time() > timeout_time

        def time_left():
            return max(timeout_time - time.time(), 0)

        for job_class, job_runner in self._job_runners:
            if job_runner is not None:
                while job_runner.has_pending():
                    # Process any available results while waiting
                    self._summarize_runner_outputs(drain_all=True)
                    if not timed_out():
                        time.sleep(0.1)
                    else:
                        self._log.error(
                            f"Timed out after {timeout_s} [s] waiting for jobs to finish.  Pending jobs for {job_class.__name__} abandoned!!!"
                        )
                        job_runner.kill()
                        break

                # Give worker a moment to put results in queue after processing
                time.sleep(0.2)
                # Drain results before shutdown
                self._summarize_runner_outputs(drain_all=True)

                self._log.info("Trying to shut down job runner...")
                job_runner.shutdown(time_left())

        # Final drain of all output queues
        self._summarize_runner_outputs(drain_all=True)

    def wait_till_operation_is_completed(self):
        self.microcontroller.wait_till_operation_is_completed()

    def run_single_time_point(self):
        try:
            start = time.time()
            self.microcontroller.enable_joystick(False)

            self._log.debug("multipoint acquisition - time point " + str(self.time_point + 1))

            # for each time point, create a new folder
            if self.experiment_path:
                utils.ensure_directory_exists(str(self.experiment_path))
            current_path = os.path.join(self.experiment_path, f"{self.time_point:0{FILE_ID_PADDING}}")
            utils.ensure_directory_exists(str(current_path))

            # create a dataframe to save coordinates
            self.initialize_coordinates_dataframe()

            # init z parameters, z range
            self.initialize_z_stack()

            with self._timing.get_timer("run_coordinate_acquisition"):
                self.run_coordinate_acquisition(current_path)

            # Save plate view for this timepoint
            if self._generate_downsampled_views and self._downsampled_view_manager is not None:
                # Wait for pending downsampled view jobs to complete
                self._wait_for_downsampled_view_jobs()
                # Save plate view
                plate_resolution = int(self._downsampled_plate_resolution_um)
                plate_view_path = os.path.join(current_path, "downsampled", f"plate_{plate_resolution}um.tiff")
                self.save_plate_view(plate_view_path)
                self._log.info(f"Saved plate view for timepoint {self.time_point} to {plate_view_path}")
                # Clear plate view for next timepoint
                self._downsampled_view_manager.clear()

            # finished region scan
            self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)

            utils.create_done_file(current_path)
            self._log.debug(f"Single time point took: {time.time() - start} [s]")
        finally:
            self.microcontroller.enable_joystick(True)

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

    def move_to_coordinate(self, coordinate_mm, region_id, fov):
        self._log.info(f"moving to coordinate {coordinate_mm}")
        x_mm = coordinate_mm[0]
        self.stage.move_x_to(x_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_X / 1000)

        y_mm = coordinate_mm[1]
        self.stage.move_y_to(y_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)

        # check if z is included in the coordinate
        if (self.do_reflection_af or self.do_autofocus) and self.time_point > 0:
            if (region_id, fov) in self._last_time_point_z_pos:
                last_z_mm = self._last_time_point_z_pos[(region_id, fov)]
                self.move_to_z_level(last_z_mm)
                self._log.info(f"Moved to last z position {last_z_mm} [mm]")
                return
            else:
                self._log.warning(f"No last z position found for region {region_id}, fov {fov}")
        if len(coordinate_mm) == 3:
            z_mm = coordinate_mm[2]
            self.move_to_z_level(z_mm)

    def move_to_z_level(self, z_mm):
        print("moving z")
        self.stage.move_z_to(z_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _summarize_runner_outputs(self, drain_all: bool = False) -> SummarizeResult:
        """Process job results from output queues.

        Args:
            drain_all: If True, process ALL available results. If False, process at most one per queue.

        Returns:
            SummarizeResult with none_failed and had_results.
        """
        none_failed = True
        had_results = False
        for job_class, job_runner in self._job_runners:
            if job_runner is None:
                continue
            out_queue = job_runner.output_queue()
            if out_queue is None:
                # Queue was cleared during shutdown
                continue
            while True:
                try:
                    job_result: JobResult = out_queue.get_nowait()
                    none_failed = none_failed and self._summarize_job_result(job_result)
                    had_results = True
                    if not drain_all:
                        break  # Only process one result per queue if not draining
                except queue.Empty:
                    break
                except ValueError:
                    # Queue was closed during shutdown - nothing more to drain
                    break

        return SummarizeResult(none_failed=none_failed, had_results=had_results)

    def _summarize_job_result(self, job_result: JobResult) -> bool:
        """
        Prints a summary, then returns True if the result was successful or False otherwise.
        """
        if job_result.exception is not None:
            self._log.error(f"Error while running job {job_result.job_id}: {job_result.exception}")
            return False
        else:
            self._log.info(f"Got result for job {job_result.job_id}, it completed!")
            # Handle DownsampledViewResult - update plate view
            if isinstance(job_result.result, DownsampledViewResult) and job_result.result.well_images:
                self._handle_downsampled_view_result(job_result.result)
            return True

    def _handle_downsampled_view_result(self, result: DownsampledViewResult) -> None:
        """Update plate view with completed well image."""
        t_start = time.perf_counter()

        if self._downsampled_view_manager is None:
            return
        try:
            self._downsampled_view_manager.update_well(
                result.well_row,
                result.well_col,
                result.well_images,
            )
            t_update = time.perf_counter()

            self._log.info(
                f"Updated plate view for well {result.well_id} at ({result.well_row}, {result.well_col}) "
                f"with {len(result.well_images)} channels"
            )

            # Emit plate view update for each channel
            for ch_idx, plate_image in enumerate(self._downsampled_view_manager.plate_view):
                channel_name = (
                    self._downsampled_view_manager.channel_names[ch_idx]
                    if ch_idx < len(self._downsampled_view_manager.channel_names)
                    else f"Channel_{ch_idx}"
                )
                self.callbacks.signal_plate_view_update(
                    PlateViewUpdate(
                        channel_idx=ch_idx,
                        channel_name=channel_name,
                        plate_image=plate_image.copy(),
                    )
                )

            t_signal = time.perf_counter()
            self._log.debug(
                f"[PERF] _handle_downsampled_view_result {result.well_id}: "
                f"update_well={t_update - t_start:.3f}s, signals={t_signal - t_update:.3f}s, "
                f"TOTAL={t_signal - t_start:.3f}s"
            )
        except Exception as e:
            self._log.exception(
                f"Failed to update plate view for well {result.well_id} "
                f"at ({result.well_row}, {result.well_col}): {e}"
            )

    def _create_job(self, job_class: Type[Job], info: CaptureInfo, image: np.ndarray) -> Optional[Job]:
        """Create a job instance for the given job class.

        Returns None if the job should be skipped.
        """
        if job_class == DownsampledViewJob:
            return self._create_downsampled_view_job(info, image)
        else:
            return job_class(capture_info=info, capture_image=JobImage(image_array=image))

    def _create_downsampled_view_job(self, info: CaptureInfo, image: np.ndarray) -> Optional[DownsampledViewJob]:
        """Create a DownsampledViewJob for the given capture.

        Returns None if downsampled views are disabled or not applicable.
        """
        if not self._generate_downsampled_views:
            return None

        # Calculate overlap first (needed for plate view manager initialization)
        if self._overlap_pixels is None:
            self._calculate_overlap_pixels(image)

        # Initialize plate view manager on first image (we need image dimensions)
        if self._downsampled_view_manager is None:
            self._initialize_downsampled_view_manager(image)

        # Get well info from region_id
        region_id = str(info.region_id)
        try:
            well_row, well_col = parse_well_id(region_id)
        except (ValueError, IndexError):
            # Region ID is not a valid well ID (e.g., "R0", "manual")
            # Region ID is not a valid well ID (e.g., "R0", "manual", custom names).
            # Use region index as a fallback. This is expected for non-plate acquisitions.
            self._log.debug(f"Region {region_id} is not a well ID, using fallback positioning")
            if not self._plate_num_rows or not self._plate_num_cols:
                self._log.warning(
                    f"Plate dimensions not set (rows={self._plate_num_rows}, cols={self._plate_num_cols}); "
                    "using (0, 0) for well position"
                )
                well_row, well_col = 0, 0
            else:
                region_idx = self.scan_region_names.index(region_id) if region_id in self.scan_region_names else 0
                well_row = region_idx // self._plate_num_cols
                well_col = region_idx % self._plate_num_cols
                # Warn if region index exceeds plate capacity (data will be overwritten)
                max_slots = self._plate_num_rows * self._plate_num_cols
                if region_idx >= max_slots:
                    self._log.warning(
                        f"Region index {region_idx} exceeds plate capacity ({max_slots} slots); "
                        f"well position will be clamped and may overwrite existing data"
                    )
                # Clamp to plate bounds
                well_row = min(well_row, self._plate_num_rows - 1)
                well_col = min(well_col, self._plate_num_cols - 1)

        # Get FOV position within well
        total_fovs = self._region_fov_counts.get(region_id, 1)
        fov_index = info.fov

        # Get the first FOV position for this region to calculate relative position
        region_coords = self.scan_region_fov_coords_mm.get(region_id, [])
        if region_coords and fov_index < len(region_coords):
            first_fov = region_coords[0]
            current_fov = region_coords[fov_index]
            # Relative position in mm from first FOV
            fov_position = (current_fov[0] - first_fov[0], current_fov[1] - first_fov[1])
        else:
            fov_position = (0.0, 0.0)

        # Determine output directory
        output_dir = os.path.join(self.experiment_path, str(self.time_point), "downsampled")

        # Get channel info
        channel_idx = info.configuration_idx
        total_channels = len(self.selected_configurations)
        channel_name = info.configuration.name if info.configuration else f"Channel_{channel_idx}"
        channel_names = [cfg.name for cfg in self.selected_configurations]

        return DownsampledViewJob(
            capture_info=info,
            capture_image=JobImage(image_array=image),
            well_id=region_id,
            well_row=well_row,
            well_col=well_col,
            fov_index=fov_index,
            total_fovs_in_well=total_fovs,
            channel_idx=channel_idx,
            total_channels=total_channels,
            channel_name=channel_name,
            fov_position_in_well=fov_position,
            overlap_pixels=self._overlap_pixels,
            pixel_size_um=self._pixel_size_um or 1.0,
            target_resolutions_um=self._downsampled_well_resolutions_um,
            plate_resolution_um=self._downsampled_plate_resolution_um,
            output_dir=output_dir,
            channel_names=channel_names,
            z_index=info.z_index,
            total_z_levels=self.NZ,
            z_projection_mode=self._downsampled_z_projection,
            interpolation_method=self._downsampled_interpolation_method,
            skip_saving=self.skip_saving
            or not self._save_downsampled_well_images
            or control._def.SIMULATED_DISK_IO_ENABLED,
        )

    def _initialize_downsampled_view_manager(self, image: np.ndarray) -> None:
        """Initialize the plate view manager based on image dimensions and FOV grid."""
        height, width = image.shape[:2]
        pixel_size_um = self._pixel_size_um or 1.0

        # Calculate downsample factor (must match downsample_tile's rounding)
        downsample_factor = int(round(self._downsampled_plate_resolution_um / pixel_size_um))
        if downsample_factor < 1:
            downsample_factor = 1

        # Calculate cropped tile dimensions (after overlap removal)
        # This matches what stitch_tiles receives
        if self._overlap_pixels:
            top, bottom, left, right = self._overlap_pixels
            cropped_width = width - left - right
            cropped_height = height - top - bottom
        else:
            cropped_width = width
            cropped_height = height

        cropped_tile_width_mm = cropped_width * pixel_size_um / 1000.0
        cropped_tile_height_mm = cropped_height * pixel_size_um / 1000.0

        # Calculate expected stitched well size using same logic as stitch_tiles:
        # canvas_size = (max_coord - min_coord) + tile_size
        well_extent_x_mm = 0.0
        well_extent_y_mm = 0.0

        for region_id, coords in self.scan_region_fov_coords_mm.items():
            if len(coords) >= 1:
                # Find extent of FOV positions within this well
                x_coords = [c[0] for c in coords]
                y_coords = [c[1] for c in coords]
                # Match stitch_tiles logic: extent = (max - min) + cropped_tile_size
                extent_x = max(x_coords) - min(x_coords) + cropped_tile_width_mm
                extent_y = max(y_coords) - min(y_coords) + cropped_tile_height_mm
                well_extent_x_mm = max(well_extent_x_mm, extent_x)
                well_extent_y_mm = max(well_extent_y_mm, extent_y)

        # Convert to pixels at native resolution (matching stitch_tiles)
        well_width_pixels = int(round(well_extent_x_mm * 1000.0 / pixel_size_um))
        well_height_pixels = int(round(well_extent_y_mm * 1000.0 / pixel_size_um))

        # Apply downsampling to get final slot size (matching downsample_tile)
        well_slot_width = well_width_pixels // downsample_factor
        well_slot_height = well_height_pixels // downsample_factor

        # Ensure minimum size (single cropped FOV downsampled)
        min_slot_width = cropped_width // downsample_factor
        min_slot_height = cropped_height // downsample_factor
        well_slot_width = max(well_slot_width, min_slot_width)
        well_slot_height = max(well_slot_height, min_slot_height)

        # Get channel info
        num_channels = len(self.selected_configurations)
        channel_names = [cfg.name for cfg in self.selected_configurations]

        self._downsampled_view_manager = DownsampledViewManager(
            num_rows=self._plate_num_rows,
            num_cols=self._plate_num_cols,
            well_slot_shape=(well_slot_height, well_slot_width),
            num_channels=num_channels,
            channel_names=channel_names,
            dtype=image.dtype,
        )
        self._log.info(
            f"Initialized downsampled view manager: {self._plate_num_rows}x{self._plate_num_cols} wells, "
            f"{num_channels} channels, slot shape ({well_slot_height}, {well_slot_width}), "
            f"well extent ({well_extent_x_mm:.2f}x{well_extent_y_mm:.2f} mm)"
        )

        # Calculate FOV grid shape for click coordinate mapping
        # Determine from the first region that has multiple FOVs
        fov_grid_shape = (1, 1)
        for region_id, coords in self.scan_region_fov_coords_mm.items():
            if len(coords) >= 1:
                x_positions = set(round(c[0], 4) for c in coords)
                y_positions = set(round(c[1], 4) for c in coords)
                fov_grid_shape = (len(y_positions), len(x_positions))
                break

        # Emit plate view init signal
        self.callbacks.signal_plate_view_init(
            PlateViewInit(
                num_rows=self._plate_num_rows,
                num_cols=self._plate_num_cols,
                well_slot_shape=(well_slot_height, well_slot_width),
                fov_grid_shape=fov_grid_shape,
                channel_names=channel_names,
            )
        )

    def _calculate_overlap_pixels(self, image: np.ndarray) -> None:
        """Calculate overlap pixels based on acquisition parameters."""
        height, width = image.shape[:2]
        pixel_size_um = self._pixel_size_um or 1.0

        # Find step size from FOV coordinates by grouping FOVs into rows
        dx_mm = 0.0
        dy_mm = 0.0

        try:
            for coords in self.scan_region_fov_coords_mm.values():
                if len(coords) < 2:
                    continue

                # Group FOVs by Y coordinate to find rows
                # Rounding to 4 decimal places (0.1 µm precision) assumes stage positioning
                # is accurate to within 0.1 µm, which is typical for microscope stages.
                rows: Dict[float, List[float]] = {}
                for coord in coords:
                    x, y = coord[0], coord[1]
                    y_key = round(y, 4)
                    if y_key not in rows:
                        rows[y_key] = []
                    rows[y_key].append(x)

                # Find X step from first row with 2+ FOVs
                for y_key in sorted(rows.keys()):
                    x_coords = rows[y_key]
                    if len(x_coords) >= 2:
                        x_sorted = sorted(x_coords)
                        dx_mm = x_sorted[1] - x_sorted[0]
                        break

                # Find Y step from two adjacent rows
                y_keys = sorted(rows.keys())
                if len(y_keys) >= 2:
                    dy_mm = y_keys[1] - y_keys[0]

                if dx_mm > 0 or dy_mm > 0:
                    break
        except Exception as e:
            self._log.warning(f"Could not calculate step size from coordinates: {e}")
            dx_mm = 0
            dy_mm = 0

        # If only one direction has steps, assume same step in both directions (square grid)
        if dx_mm > 0 and dy_mm == 0:
            dy_mm = dx_mm
        elif dy_mm > 0 and dx_mm == 0:
            dx_mm = dy_mm

        if dx_mm == 0 and dy_mm == 0:
            # No overlap or single FOV per well - don't crop anything
            self._overlap_pixels = (0, 0, 0, 0)
            self._log.info("Single FOV per well or cannot determine step size, no overlap cropping")
        else:
            self._overlap_pixels = calculate_overlap_pixels(width, height, dx_mm, dy_mm, pixel_size_um)
            self._log.info(f"Calculated overlap pixels: {self._overlap_pixels} (dx={dx_mm}mm, dy={dy_mm}mm)")

    def _wait_for_downsampled_view_jobs(self, timeout_s: Optional[float] = None) -> None:
        """Wait for all pending downsampled view jobs to complete and process results.

        Args:
            timeout_s: Maximum time to wait for jobs to complete. If None, uses
                      DOWNSAMPLED_VIEW_JOB_TIMEOUT_S from _def.py.
        """
        from control.core.job_processing import DownsampledViewJob

        if timeout_s is None:
            timeout_s = DOWNSAMPLED_VIEW_JOB_TIMEOUT_S
        timeout_time = time.time() + timeout_s
        timed_out = False

        for job_class, job_runner in self._job_runners:
            if job_runner is None or job_class != DownsampledViewJob:
                continue

            # Wait for input queue to empty
            while job_runner.has_pending():
                self._summarize_runner_outputs(drain_all=True)
                if time.time() > timeout_time:
                    self._log.warning(
                        f"Timeout ({timeout_s}s) waiting for downsampled view jobs - "
                        f"some wells may not appear in plate view"
                    )
                    timed_out = True
                    break
                time.sleep(0.1)

            if timed_out:
                break

            # After input queue is empty, the last job may still be running
            # Keep polling for results until we get no new results for a while
            last_result_time = time.time()
            while time.time() < timeout_time:
                result = self._summarize_runner_outputs(drain_all=True)
                if result.had_results:
                    last_result_time = time.time()
                # If no results for DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S, assume all jobs are done
                if time.time() - last_result_time > DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S:
                    break
                time.sleep(0.1)

            # Final drain of results
            self._summarize_runner_outputs(drain_all=True)

    def get_plate_view(self) -> Optional[np.ndarray]:
        """Get a copy of the current plate view array."""
        if self._downsampled_view_manager is None:
            return None
        return self._downsampled_view_manager.get_plate_view()

    def save_plate_view(self, path: str) -> None:
        """Save the plate view to disk."""
        if self._downsampled_view_manager is not None:
            self._downsampled_view_manager.save_plate_view(path)

    def run_coordinate_acquisition(self, current_path):
        # Reset backpressure counters at acquisition start
        # IMPORTANT: Must be before any camera triggers
        self._backpressure.reset()

        n_regions = len(self.scan_region_coords_mm)

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
                    result = self._summarize_runner_outputs()
                    if not result.none_failed and self._abort_on_failed_job:
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

    def acquire_at_position(self, region_id, current_path, fov):
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

            if z_level == 0 and (self.do_reflection_af or self.do_autofocus) and self.Nt > 1:
                self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

            # laser af characterization mode
            if self.laser_auto_focus_controller and self.laser_auto_focus_controller.characterization_mode:
                image = self.laser_auto_focus_controller.get_image()
                saving_path = os.path.join(current_path, file_ID + "_laser af camera" + ".bmp")
                iio.imwrite(saving_path, image)

            current_round_images = {}
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

                current_image = (
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

    def _select_config(self, config: ChannelMode):
        self.callbacks.signal_current_configuration(config)
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
                self.laser_auto_focus_controller.move_to_target(0)
            except Exception as e:
                file_ID = f"{region_id}_focus_camera.bmp"
                saving_path = os.path.join(self.base_path, self.experiment_ID, str(self.time_point), file_ID)
                iio.imwrite(saving_path, self.laser_auto_focus_controller.image)
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
            if self._ready_for_next_trigger.is_set():
                self._log.warning(
                    "Got an image in the image callback, but we didn't send a trigger.  Ignoring the image."
                )
                return

            self._image_callback_idle.clear()
            with self._timing.get_timer("_image_callback"):
                self._log.debug(f"In Image callback for frame_id={camera_frame.frame_id}")
                info = self._current_capture_info
                self._current_capture_info = None

                self._ready_for_next_trigger.set()
                if not info:
                    self._log.error("In image callback, no current capture info! Something is wrong. Aborting.")
                    self.request_abort_fn()
                    return

                image = camera_frame.frame
                if not camera_frame or image is None:
                    self._log.warning("image in frame callback is None. Something is really wrong, aborting!")
                    self.request_abort_fn()
                    return

                with self._timing.get_timer("job creation and dispatch"):
                    for job_class, job_runner in self._job_runners:
                        job = self._create_job(job_class, info, image)
                        if job is None:
                            continue  # Skip if job creation returns None (e.g., downsampled views disabled for this image)
                        if job_runner is not None:
                            if not job_runner.dispatch(job):
                                self._log.error("Failed to dispatch multiprocessing job!")
                                self.request_abort_fn()
                                return
                        else:
                            try:
                                # NOTE(imo): We don't have any way of people using results, so for now just
                                # grab and ignore it.
                                result = job.run()
                            except Exception:
                                self._log.exception("Failed to execute job, abandoning acquisition!")
                                self.request_abort_fn()
                                return

                height, width = image.shape[:2]
                # with self._timing.get_timer("crop_image"):
                #     image_to_display = utils.crop_image(
                #         image,
                #         round(width * self.display_resolution_scaling),
                #         round(height * self.display_resolution_scaling),
                #     )
                with self._timing.get_timer("image_to_display*.emit"):
                    self.callbacks.signal_new_image(camera_frame, info)

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

        # Backpressure check AFTER previous frame dispatched, BEFORE next trigger
        # This is when we know the previous image's jobs have been dispatched (and counters incremented)
        if self._backpressure.should_throttle():
            with self._timing.get_timer("backpressure.wait_for_capacity"):
                got_capacity = self._backpressure.wait_for_capacity()
                if not got_capacity:
                    self._log.error(
                        f"Backpressure timeout - disk I/O cannot keep up. Stats: {self._backpressure.get_stats()}"
                    )

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
            current_capture_info = CaptureInfo(
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
            )
            self._current_capture_info = current_capture_info
        with self._timing.get_timer("send_trigger"):
            self.camera.send_trigger(illumination_time=camera_illumination_time)

        with self._timing.get_timer("exposure_time_done_sleep_hw or wait_for_image_sw"):
            if self.liveController.trigger_mode == TriggerMode.HARDWARE:
                exposure_done_time = time.time() + self.camera.get_total_frame_time() / 1e3
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
                non_hw_frame_timeout = 5 * self.camera.get_total_frame_time() / 1e3 + 2
                if not self._ready_for_next_trigger.wait(non_hw_frame_timeout):
                    self._log.error("Timed out waiting {non_hw_frame_timeout} [s] for a frame, aborting acquisition.")
                    self.request_abort_fn()
                    # Let this fall through so we still turn off illumination.  Let the caller actually break out
                    # of the acquisition.

        # turn off the illumination if using software trigger
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_off_illumination()

    def _sleep(self, sec):
        time_to_sleep = max(sec, 1e-6)
        # self._log.debug(f"Sleeping for {time_to_sleep} [s]")
        time.sleep(time_to_sleep)

    def acquire_rgb_image(self, config, file_ID, current_path, k, region_id, fov):
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
                    self.wait_till_operation_is_completed()

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

        current_capture_info = CaptureInfo(
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
        file_name = (
            capture_info.file_id
            + "_BF_LED_matrix_full_RGB"
            + (".tiff" if rgb_image.dtype == np.uint16 else "." + Acquisition.IMAGE_FORMAT)
        )
        iio.imwrite(os.path.join(capture_info.save_directory, file_name), rgb_image)

    def handle_acquisition_abort(self, current_path):
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
        else:
            if self.z_stacking_config == "FROM CENTER":
                rel_z_to_start = -self.deltaZ * (self.NZ - 1) + self.deltaZ * round((self.NZ - 1) / 2)
            else:
                rel_z_to_start = -self.deltaZ * (self.NZ - 1)

            self.stage.move_z(rel_z_to_start)
