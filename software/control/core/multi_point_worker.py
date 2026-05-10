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
import control._def
from control import utils
from control.slack_notifier import TimepointStats, AcquisitionStats
from control.core.auto_focus_controller import AutoFocusController
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.core.live_controller import LiveController
from control.core.multi_point_utils import (
    AcquisitionParameters,
    MultiPointControllerFunctions,
    OverallProgressUpdate,
    RegionProgressUpdate,
    PlateViewInit,
)
from control.core.objective_store import ObjectiveStore
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.piezo import PiezoStage
from control.models import AcquisitionChannel
from squid.abc import AbstractCamera, CameraFrame, CameraFrameFormat
import squid.logging
import control.core.job_processing
from control.core.job_processing import ZarrWriteResult
from control.core.job_processing import (
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    SaveZarrJob,
    ZarrWriterInfo,
    AcquisitionInfo,
    Job,
    JobImage,
    JobRunner,
    JobResult,
)
from control.core.mosaic_utils import (
    calculate_overlap_pixels,
    parse_well_id,
)
from control.core.backpressure import BackpressureController, BackpressureValues
from squid.config import CameraPixelFormat

# Module-level logger for static methods
_log = squid.logging.get_logger(__name__)


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
        acquisition_parameters: AcquisitionParameters,
        callbacks: MultiPointControllerFunctions,
        abort_requested_fn: Callable[[], bool],
        request_abort_fn: Callable[[], None],
        extra_job_classes: list[type[Job]] | None = None,
        abort_on_failed_jobs: bool = True,
        alignment_widget=None,
        slack_notifier=None,
        prewarmed_job_runner: Optional[JobRunner] = None,
        prewarmed_bp_values: Optional["BackpressureValues"] = None,
    ):
        self._log = squid.logging.get_logger(__class__.__name__)
        self._timing = utils.TimingManager("MultiPointWorker Timer Manager")
        self._alignment_widget = alignment_widget  # Optional AlignmentWidget for coordinate offset
        self._slack_notifier = slack_notifier  # Optional SlackNotifier for notifications

        # Slack notification tracking counters
        self._timepoint_image_count = 0
        self._timepoint_fov_count = 0
        self._timepoint_start_time = 0.0
        self._acquisition_error_count = 0
        self._laser_af_successes = 0
        self._laser_af_failures = 0
        self.microscope: Microscope = scope
        self.camera: AbstractCamera = scope.camera
        self.microcontroller: Microcontroller = scope.low_level_drivers.microcontroller
        self.stage: squid.abc.AbstractStage = scope.stage
        self.piezo: Optional[PiezoStage] = scope.addons.piezo_stage
        self.liveController = live_controller
        self.autofocusController: Optional[AutoFocusController] = auto_focus_controller
        self.laser_auto_focus_controller: Optional[LaserAutofocusController] = laser_auto_focus_controller
        self.objectiveStore: ObjectiveStore = objective_store
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
        self._physical_size_z_um = abs(self.deltaZ) * 1000 if self.NZ > 1 else None
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
        use_zarr_v3 = FILE_SAVING_OPTION == FileSavingOption.ZARR_V3
        if not self.skip_saving:
            if use_ome_tiff:
                job_classes.append(SaveOMETiffJob)
            elif use_zarr_v3:
                job_classes.append(SaveZarrJob)
            else:
                job_classes.append(SaveImageJob)

        if extra_job_classes:
            job_classes.extend(extra_job_classes)

        # Plate-based scan detection — drives whether we emit plate_view_init so
        # the unified mosaic widget can offer Plate Mode. Per-tile downsampled
        # generation and per-well TIFF saving were retired in the unified
        # mosaic/plate refactor (see plan 2026-03-14-unified-mosaic-plate-view.md
        # R2/R7); the scan-mode detection survives because the widget still
        # needs the plate layout.
        is_select_wells = acquisition_parameters.xy_mode == "Select Wells"
        is_loaded_wells = acquisition_parameters.xy_mode == "Load Coordinates" and self._is_well_based_acquisition()
        self._is_plate_based_scan = is_select_wells or is_loaded_wells
        self._plate_num_rows = acquisition_parameters.plate_num_rows
        self._plate_num_cols = acquisition_parameters.plate_num_cols
        self._overlap_pixels: Optional[Tuple[int, int, int, int]] = None
        self._plate_layout_emitted = False
        self._region_fov_counts: Dict[str, int] = {}  # Track total FOVs per region

        if self._is_plate_based_scan:
            for region_id, coords in self.scan_region_fov_coords_mm.items():
                self._region_fov_counts[region_id] = len(coords)
            mode = "Select Wells" if is_select_wells else "Load Coordinates (auto-detected)"
            self._log.info(f"Plate-based scan detected ({mode}); plate layout will be emitted on first image.")

        # Initialize backpressure controller for throttling acquisition when queue fills up.
        # If pre-warmed values are provided, use them for consistent tracking with the
        # pre-warmed job runner. Otherwise, BackpressureController creates its own values.
        bp_kwargs = {
            "max_jobs": control._def.ACQUISITION_MAX_PENDING_JOBS,
            "max_mb": control._def.ACQUISITION_MAX_PENDING_MB,
            "timeout_s": control._def.ACQUISITION_THROTTLE_TIMEOUT_S,
            "enabled": control._def.ACQUISITION_THROTTLING_ENABLED,
        }
        if prewarmed_bp_values is not None:
            bp_kwargs["bp_values"] = prewarmed_bp_values
        self._backpressure = BackpressureController(**bp_kwargs)

        # For now, use 1 runner per job class.  There's no real reason/rationale behind this, though.  The runners
        # can all run any job type.  But 1 per is a reasonable arbitrary arrangement while we don't have a lot
        # of job types.  If we have a lot of custom jobs, this could cause problems via resource hogging.
        self._job_runners: List[Tuple[Type[Job], JobRunner]] = []
        self._log.info(f"Acquisition.USE_MULTIPROCESSING = {Acquisition.USE_MULTIPROCESSING}")

        # Get the current log file path to share with subprocess workers
        log_file_path = squid.logging.get_current_log_file_path()

        # Build ZarrWriterInfo if using ZARR_V3 format
        # Output structure depends on acquisition type and settings:
        # - HCS (wells): {experiment_path}/plate.ome.zarr/{row}/{col}/{fov}/0  (5D per FOV, OME-NGFF compliant)
        # - Non-HCS default: {experiment_path}/zarr/{region}/fov_{n}.ome.zarr  (5D per FOV, OME-NGFF compliant)
        # - Non-HCS 6D: {experiment_path}/zarr/{region}/acquisition.zarr  (6D, non-standard)
        zarr_writer_info = None
        if use_zarr_v3:
            # Detect HCS mode using well-based acquisition state.
            # is_loaded_wells already reflects the result of _is_well_based_acquisition(),
            # so we only need to combine it with is_select_wells here.
            is_hcs = is_select_wells or is_loaded_wells

            # Pre-compute FOV counts per region (needed for 6D shape calculation in non-HCS mode)
            region_fov_counts = {}
            for region_id, coords in self.scan_region_fov_coords_mm.items():
                region_fov_counts[str(region_id)] = len(coords)

            # Extract channel metadata for zarr output
            channel_names = [cfg.name for cfg in self.selected_configurations]
            channel_colors = [cfg.display_color for cfg in self.selected_configurations]

            # Get wavelengths from illumination config
            channel_wavelengths = []
            illumination_config = self.microscope.config_repo.get_illumination_config()
            for cfg in self.selected_configurations:
                wavelength = cfg.get_illumination_wavelength(illumination_config) if illumination_config else None
                channel_wavelengths.append(wavelength)

            zarr_writer_info = ZarrWriterInfo(
                base_path=self.experiment_path,
                t_size=self.Nt,
                c_size=len(self.selected_configurations),
                z_size=self.NZ,
                is_hcs=is_hcs,
                use_6d_fov=control._def.ZARR_USE_6D_FOV_DIMENSION,
                region_fov_counts=region_fov_counts,
                pixel_size_um=self._pixel_size_um,
                z_step_um=self._physical_size_z_um,
                time_increment_s=self._time_increment_s,
                channel_names=channel_names,
                channel_colors=channel_colors,
                channel_wavelengths=channel_wavelengths,
            )
            if is_hcs:
                mode_str = "HCS plate hierarchy"
            elif control._def.ZARR_USE_6D_FOV_DIMENSION:
                mode_str = "per-region 6D (non-standard)"
            else:
                mode_str = "per-FOV 5D (OME-NGFF compliant)"
            self._log.info(f"ZARR_V3 output: {mode_str}, base path: {self.experiment_path}")

        # Use pre-warmed job runner if available, otherwise create new ones.
        # IMPORTANT: Only use pre-warmed runner if BOTH runner AND backpressure values
        # are available. Using a runner without matching backpressure values would cause
        # the BackpressureController to track different counters than the JobRunner.
        can_use_prewarmed = prewarmed_job_runner is not None and prewarmed_bp_values is not None
        used_prewarmed = False
        for job_class in job_classes:
            job_runner = None
            if Acquisition.USE_MULTIPROCESSING:
                # Try to use pre-warmed runner for the first job class
                if can_use_prewarmed and not used_prewarmed:
                    if prewarmed_job_runner.is_ready():
                        self._log.info(f"Using pre-warmed job runner for {job_class.__name__} jobs")
                        job_runner = prewarmed_job_runner
                        # Configure it with current acquisition settings
                        job_runner.set_acquisition_info(self.acquisition_info)
                        if zarr_writer_info:
                            job_runner.set_zarr_writer_info(zarr_writer_info)
                        used_prewarmed = True
                    else:
                        self._log.warning(
                            f"Pre-warmed job runner not ready (possibly hung during warmup), "
                            f"shutting it down and creating new one for {job_class.__name__}"
                        )
                        # Shutdown the hung pre-warmed runner to avoid resource leak
                        try:
                            prewarmed_job_runner.shutdown(timeout_s=1.0)
                        except Exception as e:
                            self._log.error(f"Error shutting down hung pre-warmed runner: {e}")
                        # Don't try to use pre-warmed runner again for subsequent job classes
                        can_use_prewarmed = False

                if job_runner is None:
                    self._log.info(f"Creating job runner for {job_class.__name__} jobs")
                    job_runner = control.core.job_processing.JobRunner(
                        self.acquisition_info,
                        cleanup_stale_ome_files=use_ome_tiff,
                        log_file_path=log_file_path,
                        # Pass backpressure shared values for cross-process tracking
                        bp_pending_jobs=self._backpressure.pending_jobs_value,
                        bp_pending_bytes=self._backpressure.pending_bytes_value,
                        bp_capacity_event=self._backpressure.capacity_event,
                        # Pass zarr writer info for ZARR_V3 format
                        zarr_writer_info=zarr_writer_info,
                    )
                    job_runner.start()
                    # Subprocess starts warming up in background - don't block here

            self._job_runners.append((job_class, job_runner))
        self._abort_on_failed_job = abort_on_failed_jobs
        self._first_job_dispatched = False  # Track if we've waited for subprocess warmup

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

            # Send Slack acquisition start notification
            if self._slack_notifier is not None:
                try:
                    self._slack_notifier.notify_acquisition_start(
                        experiment_id=self.experiment_ID or "unknown",
                        num_regions=len(self.scan_region_names) if self.scan_region_names else 0,
                        num_timepoints=self.Nt,
                        num_channels=len(self.selected_configurations) if self.selected_configurations else 0,
                        num_z_levels=self.NZ,
                    )
                except Exception as e:
                    self._log.warning(f"Failed to send Slack acquisition start notification: {e}")

            # Cache laser-engine refs for the gate (None when flag is off).
            self._laser_engine = getattr(self.microscope.addons, "squid_laser_engine", None)
            self._laser_channels_needed = self._compute_laser_channels_needed()

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self.abort_requested_fn():
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break

                # Gate on laser engine readiness for the channels this acquisition will fire.
                # Re-checked every timepoint so dt-induced sleep gaps are handled.
                if self._laser_engine is not None and self._laser_channels_needed:
                    self._wait_for_laser_engine()
                    if self.abort_requested_fn():
                        break

                if self.fluidics and self.use_fluidics:
                    self.fluidics.update_port(self.time_point)  # use the port in PORT_LIST
                    # For MERFISH, before imaging, run the first 3 sequences (Add probe, wash buffer, imaging buffer)
                    self.fluidics.run_before_imaging()
                    self.fluidics.wait_for_completion()
                    # Check for abort after fluidics completes (user may have stopped during fluidics)
                    if self.abort_requested_fn():
                        self._log.debug("Abort requested after fluidics, skipping imaging")
                        break

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

            # Send Slack acquisition finished notification via callback (ensures ordering with timepoint notifications)
            if self._slack_notifier is not None:
                try:
                    total_duration = time.time() - self.timestamp_acquisition_started
                    stats = AcquisitionStats(
                        total_images=self.image_count,
                        total_timepoints=self.time_point,
                        total_duration_seconds=total_duration,
                        errors_encountered=self._acquisition_error_count,
                        experiment_id=self.experiment_ID or "unknown",
                    )
                    self.callbacks.signal_slack_acquisition_finished(stats)
                except Exception as e:
                    self._log.warning(f"Failed to send Slack acquisition finished notification: {e}")

            self.callbacks.signal_acquisition_finished()

    def _compute_laser_channels_needed(self) -> List[str]:
        if self._laser_engine is None:
            return []
        ill_config = self.microscope.config_repo.get_illumination_config()
        if ill_config is None:
            return []
        wavelengths = []
        for cfg in self.selected_configurations:
            try:
                w = cfg.get_illumination_wavelength(ill_config)
            except Exception:
                w = None
            if w is not None:
                wavelengths.append(w)
        return self._laser_engine.channel_keys_for_wavelengths(wavelengths)

    def _wait_for_laser_engine(self) -> None:
        """Block until needed channels are ACTIVE. Raises on timeout / disconnect / ERROR.

        Returns silently when abort_requested_fn fires — caller handles abort.
        """
        status = self._laser_engine.get_latest_status()
        if status is not None and status.is_ready_for(self._laser_channels_needed):
            return
        self.callbacks.signal_laser_engine_waiting(list(self._laser_channels_needed))
        try:
            ok = self._laser_engine.wait_until_ready(
                self._laser_channels_needed,
                timeout_s=self._laser_engine.READY_TIMEOUT_S,
                cancel_fn=self.abort_requested_fn,
            )
        finally:
            self.callbacks.signal_laser_engine_ready()
        if not ok:
            if self.abort_requested_fn():
                return
            channels = ", ".join(self._laser_channels_needed)
            if self._laser_engine.is_connection_lost():
                raise RuntimeError(
                    f"Laser engine connection lost while waiting on channel(s) {channels}; aborting acquisition"
                )
            raise RuntimeError(
                f"Laser engine did not reach ready state within timeout "
                f"while waiting on channel(s) {channels}; aborting acquisition"
            )

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

        active_runners = [
            (job_class, job_runner) for job_class, job_runner in self._job_runners if job_runner is not None
        ]

        self._log.info(f"Waiting for jobs to finish on {len(active_runners)} job runners before shutting them down...")
        timeout_time = time.time() + timeout_s

        def timed_out():
            return time.time() > timeout_time

        def time_left():
            return max(timeout_time - time.time(), 0)

        # Wait for all pending jobs across all runners (round-robin to avoid blocking on one)
        while not timed_out():
            any_pending = False
            for job_class, job_runner in active_runners:
                if job_runner.has_pending():
                    any_pending = True
                    break
            if not any_pending:
                break
            # Process any available results while waiting
            self._summarize_runner_outputs(drain_all=True)
            time.sleep(0.1)
        else:
            # Timed out - kill any runners that still have pending jobs
            for job_class, job_runner in active_runners:
                if job_runner.has_pending():
                    self._log.error(
                        f"Timed out after {timeout_s} [s] waiting for jobs to finish. Pending jobs for {job_class.__name__} abandoned!!!"
                    )
                    job_runner.kill()

        # Drain results before shutdown
        self._summarize_runner_outputs(drain_all=True)

        # Shut down all job runners in parallel (in background to avoid blocking on subprocess termination).
        # Using daemon threads is safe here because:
        # 1. All jobs are complete and results are already drained
        # 2. The subprocess termination is best-effort cleanup only
        # 3. If app exits before threads complete, OS will terminate subprocesses anyway
        # 4. This prevents slow subprocess termination from blocking acquisition completion
        log = self._log  # Capture for closure

        def shutdown_runner(job_runner, timeout):
            try:
                job_runner.shutdown(timeout)
            except Exception as e:
                log.error(f"Error shutting down job runner in background: {e}")

        self._log.info("Shutting down job runners (non-blocking)...")
        remaining_time = time_left()
        for job_class, job_runner in active_runners:
            t = threading.Thread(target=shutdown_runner, args=(job_runner, remaining_time), daemon=True)
            t.start()

        # Final drain of all output queues (should be empty, but check anyway)
        self._summarize_runner_outputs(drain_all=True)

        # Release backpressure resources now that all jobs are complete
        try:
            self._backpressure.close()
        except Exception as e:
            self._log.error(f"Error closing backpressure controller: {e}")

    def wait_till_operation_is_completed(self):
        self.microcontroller.wait_till_operation_is_completed()

    def run_single_time_point(self):
        try:
            start = time.time()
            self._timepoint_start_time = start
            self._timepoint_image_count = 0
            self._timepoint_fov_count = 0
            self._laser_af_successes = 0
            self._laser_af_failures = 0
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

            # finished region scan
            self.coordinates_pd.to_csv(os.path.join(current_path, "coordinates.csv"), index=False, header=True)

            # Send Slack timepoint notification via callback (allows main thread to capture screenshot)
            if self._slack_notifier is not None:
                try:
                    elapsed = time.time() - self.timestamp_acquisition_started
                    timepoint_duration = time.time() - self._timepoint_start_time
                    self._slack_notifier.record_timepoint_duration(timepoint_duration)
                    estimated_remaining = self._slack_notifier.estimate_remaining_time(self.time_point + 1, self.Nt)
                    stats = TimepointStats(
                        timepoint=self.time_point + 1,
                        total_timepoints=self.Nt,
                        elapsed_seconds=elapsed,
                        estimated_remaining_seconds=estimated_remaining,
                        images_captured=self._timepoint_image_count,
                        fovs_captured=self._timepoint_fov_count,
                        laser_af_successes=self._laser_af_successes,
                        laser_af_failures=self._laser_af_failures,
                        laser_af_failure_reasons=[],
                    )
                    # Use callback to allow main thread to capture screenshot before sending
                    self.callbacks.signal_slack_timepoint_notification(stats)
                except Exception as e:
                    self._log.warning(f"Failed to send Slack timepoint notification: {e}")

            try:
                self.callbacks.signal_timepoint_finished(self.time_point)
            except Exception:
                self._log.exception("signal_timepoint_finished callback failed")

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
        x_mm = coordinate_mm[0]
        y_mm = coordinate_mm[1]

        if self._alignment_widget is not None and self._alignment_widget.has_offset:
            x_mm, y_mm = self._alignment_widget.apply_offset(x_mm, y_mm)
            self._log.info(
                f"moving to coordinate ({x_mm:.4f}, {y_mm:.4f}) "
                f"[original: ({coordinate_mm[0]:.4f}, {coordinate_mm[1]:.4f}), offset applied]"
            )
        else:
            self._log.info(f"moving to coordinate {coordinate_mm}")

        self.stage.move_x_to(x_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_X / 1000)

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
        self._log.debug("moving z")
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
            self._acquisition_error_count += 1

            # Send Slack error notification
            if self._slack_notifier is not None:
                try:
                    context = {"job_id": job_result.job_id}
                    self._slack_notifier.notify_error(
                        str(job_result.exception),
                        context,
                    )
                except Exception as e:
                    self._log.warning(f"Failed to send Slack error notification: {e}")
            return False
        else:
            self._log.info(f"Got result for job {job_result.job_id}, it completed!")
            # Handle ZarrWriteResult - notify viewer that frame is written
            if isinstance(job_result.result, ZarrWriteResult):
                r = job_result.result
                self.callbacks.signal_zarr_frame_written(r.fov, r.time_point, r.z_index, r.channel_name, r.region_idx)
            return True

    def _create_job(self, job_class: Type[Job], info: CaptureInfo, image: np.ndarray) -> Optional[Job]:
        """Create a job instance for the given job class.

        Returns None if the job should be skipped.
        """
        return job_class(capture_info=info, capture_image=JobImage(image_array=image))

    def _emit_plate_layout(self, image: np.ndarray) -> None:
        """Emit plate_view_init for the unified mosaic widget on the first image.

        Slot dimensions must match what UnifiedMosaicWidget actually renders:
          - effective µm/px is ``pixel_size_um * int(target_um / pixel_size_um)``
            (integer downsample factor, can be smaller than target_um)
          - tile dims are full (the widget does not crop overlap; adjacent tiles
            simply overlap on the canvas)
        Mismatching either of these can under-size slots and cause tiles to spill
        into neighboring wells.

        Only fires on plate-based scans, only once per run.
        """
        if self._plate_layout_emitted or not self._is_plate_based_scan:
            return
        if self._overlap_pixels is None:
            self._calculate_overlap_pixels(image)

        height, width = image.shape[:2]
        pixel_size_um = self._pixel_size_um or 1.0
        target_um = float(control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM)
        # Must match downsample_tile's `int(round(...))` so the slot pixel
        # space exactly equals what the widget renders (any disagreement under-
        # or over-sizes slots and tiles spill into neighbors).
        downsample_factor = max(1, int(round(target_um / pixel_size_um)))
        effective_um_per_px = pixel_size_um * downsample_factor

        tile_w_mm = width * pixel_size_um / 1000.0
        tile_h_mm = height * pixel_size_um / 1000.0

        well_extent_x_mm = 0.0
        well_extent_y_mm = 0.0
        for coords in self.scan_region_fov_coords_mm.values():
            if not coords:
                continue
            x_coords = [c[0] for c in coords]
            y_coords = [c[1] for c in coords]
            well_extent_x_mm = max(well_extent_x_mm, max(x_coords) - min(x_coords) + tile_w_mm)
            well_extent_y_mm = max(well_extent_y_mm, max(y_coords) - min(y_coords) + tile_h_mm)

        well_slot_w = int(round(well_extent_x_mm * 1000.0 / effective_um_per_px))
        well_slot_h = int(round(well_extent_y_mm * 1000.0 / effective_um_per_px))
        min_slot_w = int(round(tile_w_mm * 1000.0 / effective_um_per_px))
        min_slot_h = int(round(tile_h_mm * 1000.0 / effective_um_per_px))
        well_slot_w = max(well_slot_w, min_slot_w)
        well_slot_h = max(well_slot_h, min_slot_h)

        fov_grid_shape: Tuple[int, int] = (1, 1)
        for coords in self.scan_region_fov_coords_mm.values():
            if not coords:
                continue
            x_positions = {round(c[0], 4) for c in coords}
            y_positions = {round(c[1], 4) for c in coords}
            fov_grid_shape = (len(y_positions), len(x_positions))
            break

        well_ids = [name for name, coords in self.scan_region_fov_coords_mm.items() if coords]
        self.callbacks.signal_plate_view_init(
            PlateViewInit(
                num_rows=self._plate_num_rows,
                num_cols=self._plate_num_cols,
                well_slot_shape=(well_slot_h, well_slot_w),
                fov_grid_shape=fov_grid_shape,
                well_ids=well_ids,
            )
        )
        self._plate_layout_emitted = True
        self._log.info(
            f"Emitted plate layout: {self._plate_num_rows}x{self._plate_num_cols} wells, "
            f"slot shape ({well_slot_h}, {well_slot_w}) px @ {effective_um_per_px:.3f}µm/px, "
            f"well extent ({well_extent_x_mm:.2f}x{well_extent_y_mm:.2f} mm)"
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

        # Increment FOV counter for Slack notification stats
        self._timepoint_fov_count += 1

    def _select_config(self, config: AcquisitionChannel):
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
                config_AF = self.liveController.get_channel_by_name(
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
                self._laser_af_successes += 1
            except Exception as e:
                file_ID = f"{region_id}_focus_camera.bmp"
                saving_path = os.path.join(self.base_path, self.experiment_ID, str(self.time_point), file_ID)
                iio.imwrite(saving_path, self.laser_auto_focus_controller.image)
                self._log.error(
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! laser AF failed !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                    exc_info=e,
                )
                self._laser_af_failures += 1
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

                # Increment image counter for Slack notification stats
                self._timepoint_image_count += 1
                self.image_count += 1

                with self._timing.get_timer("job creation and dispatch"):
                    # Wait for subprocess to be ready before first dispatch
                    if not self._first_job_dispatched:
                        for job_class, job_runner in self._job_runners:
                            if job_runner is not None:
                                t_wait_start = time.perf_counter()
                                if job_runner.wait_ready(timeout_s=10.0):
                                    t_wait_end = time.perf_counter()
                                    wait_ms = (t_wait_end - t_wait_start) * 1000
                                    if wait_ms > 10:  # Only log if we actually had to wait
                                        self._log.info(f"Job runner ready (waited {wait_ms:.0f}ms for subprocess)")
                                else:
                                    self._log.warning(f"Job runner for {job_class.__name__} not ready after 10s")
                        self._first_job_dispatched = True

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
                # Emit plate layout once on the first image so the unified mosaic
                # widget can lay out the plate grid before tiles start arriving.
                self._emit_plate_layout(image)
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

        for config_ in self.liveController.get_channels(self.objectiveStore.current_objective):
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
                    self._log.warning("self.camera.read_frame() returned None")
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
            self._log.debug("writing R, G, B channels")
            self.handle_rgb_channels(images, current_capture_info)
        else:
            # If monochrome, reconstruct RGB image
            self._log.debug("constructing RGB image")
            self.construct_rgb_image(images, current_capture_info)

    @staticmethod
    def handle_rgb_generation(current_round_images, capture_info: CaptureInfo):
        keys_to_check = ["BF LED matrix full_R", "BF LED matrix full_G", "BF LED matrix full_B"]
        if all(key in current_round_images for key in keys_to_check):
            _log.debug(f"constructing RGB image: dtype={current_round_images['BF LED matrix full_R'].dtype}")
            size = current_round_images["BF LED matrix full_R"].shape
            rgb_image = np.zeros((*size, 3), dtype=current_round_images["BF LED matrix full_R"].dtype)
            _log.debug(f"RGB image shape: {rgb_image.shape}")
            rgb_image[:, :, 0] = current_round_images["BF LED matrix full_R"]
            rgb_image[:, :, 1] = current_round_images["BF LED matrix full_G"]
            rgb_image[:, :, 2] = current_round_images["BF LED matrix full_B"]

            # TODO(imo): There used to be a "display image" comment here, and then an unused cropped image.  Do we need to emit an image here?

            # write the image
            if len(rgb_image.shape) == 3:
                _log.debug("writing RGB image")
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
        self._log.debug("writing RGB image")
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
