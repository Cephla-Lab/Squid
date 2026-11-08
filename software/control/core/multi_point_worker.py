import dataclasses
import math
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
from control.core.relative_z_range import frame_targets, nz_for_range
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.piezo import PiezoStage
from control.models import AcquisitionChannel
from squid.abc import AbstractCamera, CameraFrame, CameraFrameFormat
import squid.acquisition_state
import squid.stage.utils
from control.rcm_scan_trigger import ConfocalScanFailedError
import squid.logging
import control.core.job_processing
from control.core.job_processing import ZarrWriteResult
from control.core.job_processing import (
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    SaveSettings,
    SaveZarrJob,
    SkippedFrames,
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

# A relative Z-range with no valid lock-on record: the run stops when the first FOV's lock-on fails.
RELATIVE_FIRST_FOV_LOCK_ON_FAILED_MESSAGE = (
    "Couldn't lock onto the laser AF reference at the first FOV: move near the reference plane, "
    "or measure a relative z once."
)

# A relative frame whose target is within this of where the actuator already is takes no move (and no move settle).
RELATIVE_SAME_TARGET_TOLERANCE_UM = 1e-4


def save_job_classes_for_format(file_saving_option: FileSavingOption) -> List[Type[Job]]:
    """The saving job classes that implement the given file saving format."""
    if file_saving_option == FileSavingOption.OME_TIFF:
        return [SaveOMETiffJob]
    if file_saving_option == FileSavingOption.ZARR_V3:
        return [SaveZarrJob]
    return [SaveImageJob]


class SummarizeResult(NamedTuple):
    """Result from processing job output queues."""

    none_failed: bool  # True if no jobs failed (or no results to process)
    had_results: bool  # True if any results were pulled from queue


def _z_steps_below_center(nz: int) -> int:
    """Number of z steps a From Center stack starts below its centre (and climbs back afterwards)."""
    return round((nz - 1) / 2.0)


class MultiPointWorker:
    # Relative-mode state (Set Range with laser AF on). Class defaults so a bare instance (tests build some with
    # __new__) runs in absolute / plain mode.
    laser_af_relative_range_um: Optional[Tuple[float, float]] = None
    _relative_mode: bool = False
    _anchor_um: Optional[float] = None
    _require_first_lock_on: bool = False

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
        run_state_writer=None,
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
        # Laser AF re-runs before later channels' stacks (Z First "AF per stack"), counted apart from the per-FOV AF.
        self._laser_af_per_stack_successes = 0
        self._laser_af_per_stack_failures = 0
        self._current_z_offset_um: float = 0.0
        self.microscope: Microscope = scope
        self.camera: AbstractCamera = scope.camera
        self.microcontroller: Microcontroller = scope.low_level_drivers.microcontroller
        self.stage: squid.abc.AbstractStage = scope.stage
        self.piezo: Optional[PiezoStage] = scope.addons.piezo_stage
        self.liveController = live_controller
        self.autofocusController: Optional[AutoFocusController] = auto_focus_controller
        self.laser_auto_focus_controller: Optional[LaserAutofocusController] = laser_auto_focus_controller
        self.objectiveStore: ObjectiveStore = objective_store

        self.callbacks: MultiPointControllerFunctions = callbacks
        self.abort_requested_fn: Callable[[], bool] = abort_requested_fn
        self.request_abort_fn: Callable[[], None] = request_abort_fn
        self._run_state = run_state_writer or squid.acquisition_state.NullRunStateWriter()
        self._abort_cause = None  # set to "error" by auto-abort paths (timeout / failed jobs)
        self.end_reason: Optional[str] = None  # set in run()'s finally; read by the controller
        self.NZ = acquisition_parameters.NZ
        self.deltaZ = acquisition_parameters.deltaZ

        self.Nt = acquisition_parameters.Nt
        self.dt = acquisition_parameters.deltat

        self.do_autofocus = acquisition_parameters.do_autofocus
        self.do_reflection_af = acquisition_parameters.do_reflection_autofocus
        self.use_piezo = acquisition_parameters.use_piezo
        self.apply_channel_offset = acquisition_parameters.apply_channel_offset
        self.region_laser_af_offsets = acquisition_parameters.region_laser_af_offsets
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
        self.z_stack_order = acquisition_parameters.z_stack_order  # 'CHANNEL FIRST' or 'Z FIRST'
        # Z First only: laser AF before each later channel's stack (the first channel keeps the per-FOV AF).
        self.laser_af_per_stack = acquisition_parameters.laser_af_per_stack
        self.z_range = acquisition_parameters.z_range
        self.piezo_z_range_um = acquisition_parameters.piezo_z_range_um
        # Set Range with laser AF on: (z_min, z_max) µm from each FOV's laser AF anchor, or None.
        self.laser_af_relative_range_um = acquisition_parameters.laser_af_relative_range_um
        self._relative_mode = self.laser_af_relative_range_um is not None
        self._anchor_um = None  # the actuator position (µm) the current FOV's planes are relative to
        # Where the current FOV's lock-on or last relative move left the actuator (µm), None when unknown: outside a
        # FOV, during a lock-on, after a failed move. The commanded target, not a readback (a stage reads back in
        # microsteps), so a frame at the same target as the last one (equal channel offsets) takes no move.
        self._actuator_at_um: Optional[float] = None
        self._require_first_lock_on = False  # set per time point by initialize_z_stack
        self._relative_offsets_um: List[float] = []  # the current FOV's channel offsets, per selected channel
        self._out_of_travel_by_k: Dict[int, List[str]] = {}  # the current FOV's skipped channels per plane
        self._skipped_frames: List[Tuple[int, str, float]] = []  # the current FOV's (k, channel, target µm)
        # Skip notices: each tells the Zarr / OME-TIFF writers one lock-on group's skipped frames, with the shape and
        # dtype of the run's first frame. Until that frame arrives they wait here; the frame callback (camera
        # thread) sends them, hence the lock. A run that takes no frame at all sends them at its end.
        self._skip_notice_lock = threading.Lock()
        self._pending_skip_notices: List[Tuple[CaptureInfo, Tuple[Tuple[int, int], ...]]] = []
        self._frame_format: Optional[Tuple[Tuple[int, int], str]] = None  # ((height, width), dtype) of that frame
        # Per time point, for the run summary (TimepointStats).
        self._relative_frames_skipped = 0
        self._relative_fovs_with_skips = 0

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
        # A copy of the channel last applied with set_microscope_mode. Within one position, an image whose channel
        # is unchanged skips re-applying it (camera, MCU, illumination and filter wheel commands on every z level).
        # Cleared at every position, so whatever runs between positions (autofocus, laser AF) can't leave it stale.
        self._applied_config: Optional[AcquisitionChannel] = None

        self.skip_saving = acquisition_parameters.skip_saving
        # The settings in effect now, for the whole acquisition.
        self._save_settings = SaveSettings()
        file_saving_option = self._save_settings.file_saving_option
        use_ome_tiff = file_saving_option == FileSavingOption.OME_TIFF
        use_zarr_v3 = file_saving_option == FileSavingOption.ZARR_V3
        job_classes = [] if self.skip_saving else save_job_classes_for_format(file_saving_option)

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

    def _abort_due_to_error(self) -> None:
        """Abort the run due to an internal error (vs a user abort).

        The worker only ever aborts itself on error conditions; user aborts arrive
        via the external abort flag. Tagging the cause here lets _compute_end_reason
        classify the end as "error" instead of "user_abort".
        """
        self._abort_cause = "error"
        self.request_abort_fn()

    def _run_state_beat(self) -> None:
        self._run_state.beat(
            {
                "timepoint": self.time_point,
                "fov": self._timepoint_fov_count,
                "images": self.image_count,
            }
        )

    def _compute_end_reason(self) -> str:
        if self._run_state_fatal:
            return "error"
        if self.abort_requested_fn():
            return "error" if self._abort_cause == "error" else "user_abort"
        if self._acquisition_error_count > 0:
            return "completed_with_errors"
        return "completed"

    def run(self):
        this_image_callback_id = None
        self._run_state_fatal = False
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
            self._illumination_wavelengths = self._compute_illumination_wavelengths()
            self._laser_channels_needed = self._compute_laser_channels_needed()

            # Warn once if non-zero channel offsets won't be applied this run.
            self._log_ignored_offsets()

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self.abort_requested_fn():
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break
                self._run_state_beat()

                # The time point uses its channels (wakes the lines, keeps the 560 on, opens its mechanical shutter),
                # released for the wait to the next one. The gate runs inside it, so it sees the shutter open.
                with self.microscope.illumination_controller.use("acquisition", self._illumination_wavelengths):
                    # Gate on laser engine readiness for the channels this acquisition will fire.
                    # Re-checked every timepoint so dt-induced sleep gaps are handled.
                    if self._laser_engine is not None and self._laser_channels_needed:
                        self._wait_for_laser_engine()
                        if self.abort_requested_fn():
                            break

                    with self._timing.get_timer("run_single_time_point"):
                        self.run_single_time_point()

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
                        self._run_state_beat()
                        self._sleep(sleep_time)

            elapsed_time = time.perf_counter_ns() - start_time
            self._log.info("Time taken for acquisition: " + str(elapsed_time / 10**9))

            # Since we use callback based acquisition, make sure to wait for any final images to come in
            self._wait_for_outstanding_callback_images()
            self._log.info(f"Time taken for acquisition/processing: {(time.perf_counter_ns() - start_time) / 1e9} [s]")
        except TimeoutError as te:
            self._log.error(f"Operation timed out during acquisition, aborting acquisition!")
            self._log.error(te)
            self._abort_due_to_error()
        except Exception as e:
            self._log.exception(e)
            self._run_state_fatal = True
            raise
        finally:
            # We do this above, but there are some paths that skip the proper end of the acquisition so make
            # sure to always wait for final images here before removing our callback.
            self._wait_for_outstanding_callback_images()
            # Skip notices still waiting for the run's first frame (it took none) go out while the runners are open.
            self._flush_pending_skip_notices()
            self._log.debug(self._timing.get_report())
            if this_image_callback_id:
                self.camera.remove_frame_callback(this_image_callback_id)

            self._finish_jobs()

            # Determine why the acquisition ended (drives the watchdog + the in-process finish msg).
            reason = self.end_reason = self._compute_end_reason()
            total_duration = time.time() - self.timestamp_acquisition_started
            self._run_state.end(
                reason,
                {
                    "total_images": self.image_count,
                    "total_timepoints": self.time_point,
                    "total_duration_seconds": total_duration,
                    "errors_encountered": self._acquisition_error_count,
                },
            )

            # Send Slack acquisition finished notification via callback (ensures ordering with timepoint notifications)
            if self._slack_notifier is not None:
                try:
                    stats = AcquisitionStats(
                        total_images=self.image_count,
                        total_timepoints=self.time_point,
                        total_duration_seconds=total_duration,
                        errors_encountered=self._acquisition_error_count,
                        experiment_id=self.experiment_ID or "unknown",
                        reason=reason,
                    )
                    self.callbacks.signal_slack_acquisition_finished(stats)
                except Exception as e:
                    self._log.warning(f"Failed to send Slack acquisition finished notification: {e}")

            self.callbacks.signal_acquisition_finished()

    def _compute_illumination_wavelengths(self) -> List[int]:
        """The wavelengths this acquisition images with: its channels, and the contrast autofocus channel when it runs."""
        ill_config = self.microscope.config_repo.get_illumination_config()
        if ill_config is None:
            return []
        configs = list(self.selected_configurations)
        if self.do_autofocus and not self.do_reflection_af:
            config_af = self.liveController.get_channel_by_name(
                self.objectiveStore.current_objective, MULTIPOINT_AUTOFOCUS_CHANNEL
            )
            if config_af is not None:
                configs.append(config_af)
        wavelengths = []
        for cfg in configs:
            try:
                w = cfg.get_illumination_wavelength(ill_config)
            except Exception:
                w = None
            if w is not None and w not in wavelengths:
                wavelengths.append(w)
        return wavelengths

    def _compute_laser_channels_needed(self) -> List[str]:
        if self._laser_engine is None:
            return []
        return self._laser_engine.channel_keys_for_wavelengths(self._illumination_wavelengths)

    def _wait_for_laser_engine(self) -> None:
        """Block until needed channels are ACTIVE. Raises on timeout / disconnect / ERROR.

        Returns silently when abort_requested_fn fires — caller handles abort.
        """
        if self._laser_engine.is_ready_to_image(self._laser_channels_needed):
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
            self._laser_af_per_stack_successes = 0
            self._laser_af_per_stack_failures = 0
            self._relative_frames_skipped = 0
            self._relative_fovs_with_skips = 0
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

            if self._relative_frames_skipped > 0:
                # The run summary in the log, with or without Slack (the per-FOV warnings list each frame).
                self._log.warning(
                    f"time point {self.time_point + 1}: relative z-stack skipped {self._relative_frames_skipped} "
                    f"frame(s) outside the travel, not clamped, in {self._relative_fovs_with_skips} FOV(s)"
                )

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
                        laser_af_per_stack_successes=self._laser_af_per_stack_successes,
                        laser_af_per_stack_failures=self._laser_af_per_stack_failures,
                        relative_frames_skipped=self._relative_frames_skipped,
                        relative_fovs_with_skips=self._relative_fovs_with_skips,
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

        # A piezo range is swept by the piezo from its first plane; each FOV's stack returns it there.
        if self.piezo_z_range_um is not None:
            piezo_min_um, piezo_max_um = self.piezo_z_range_um
            self.piezo.move_to(piezo_max_um if self.z_stacking_config == "FROM TOP" else piezo_min_um)
            self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)

        # A relative range starts the time point at the last lock-on's anchor, not at the Start-time z (usually
        # Z-max, often outside the capture range). With no valid record the first FOV's lock-on must succeed.
        if self._relative_mode:
            record = self.laser_auto_focus_controller.valid_lock_on()
            if record is not None:
                self._move_actuator_to_um(record.anchor_um)
                self._actuator_at_um = None  # tracked inside a FOV only: the move to the first FOV may move Z
                self._anchor_um = record.anchor_um
            self._require_first_lock_on = record is None

        self.z_pos = self.stage.get_pos().z_mm  # zpos at the beginning of the scan

    def initialize_coordinates_dataframe(self):
        base_columns = ["z_level", "x (mm)", "y (mm)", "z (um)", "time"]
        piezo_column = ["z_piezo (um)"] if self.use_piezo else []
        relative_columns = ["z_rel (um)", "out_of_travel"] if self._relative_mode else []
        self.coordinates_pd = pd.DataFrame(columns=["region", "fov"] + base_columns + piezo_column + relative_columns)

    def update_coordinates_dataframe(
        self,
        region_id,
        z_level,
        pos: squid.abc.Pos,
        fov=None,
        z_piezo_um=None,
        z_rel_um=None,
        skipped_channels=None,
    ):
        base_data = {
            "z_level": [z_level],
            "x (mm)": [pos.x_mm],
            "y (mm)": [pos.y_mm],
            "z (um)": [pos.z_mm * 1000],
            "time": [datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")],
        }
        piezo_data = {}
        if self.use_piezo:
            piezo_data = {"z_piezo (um)": [self.z_piezo_um if z_piezo_um is None else z_piezo_um]}
        relative_data = {}
        if self._relative_mode:
            # The plane's distance from the reference, and the channels left out there (outside the travel).
            relative_data = {"z_rel (um)": [z_rel_um], "out_of_travel": [";".join(skipped_channels or [])]}

        new_row = pd.DataFrame({"region": [region_id], "fov": [fov], **base_data, **piezo_data, **relative_data})

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
        self._sleep(squid.stage.utils.z_stabilization_time_s())

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
        return job_class(
            capture_info=info, capture_image=JobImage(image_array=image), save_settings=self._save_settings
        )

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
                        self._abort_due_to_error()
                        return

                with self._timing.get_timer("move_to_coordinate"):
                    self.move_to_coordinate(coordinate_mm, region_id, fov)
                with self._timing.get_timer("acquire_at_position"):
                    self.acquire_at_position(region_id, current_path, fov)

                if self.abort_requested_fn():
                    self.handle_acquisition_abort(current_path)
                    return

    def acquire_at_position(self, region_id, current_path, fov):
        self._applied_config = None
        if self._relative_mode:
            self._acquire_relative(region_id, current_path, fov)
            return
        af_succeeded = self.perform_autofocus(region_id, fov)
        if not af_succeeded:
            self._log.error(
                f"Autofocus failed in acquire_at_position.  Continuing to acquire anyway using the current z position (z={self.stage.get_pos().z_mm} [mm])"
            )

        if self.use_piezo:
            # Re-read the piezo: laser AF moves it directly, bypassing this cache, and the
            # From Center move below and every stack step start from the cached value.
            self.z_piezo_um = self.piezo.position

        if self.NZ > 1:
            self.prepare_z_stack()

        if self.z_stack_order == "Z FIRST" and self.NZ > 1:
            z_steps_taken = self._acquire_z_first(region_id, current_path, fov, af_succeeded)
        else:
            z_steps_taken = self._acquire_channel_first(region_id, current_path, fov, af_succeeded)

        if self.NZ > 1:
            # Undo only the steps actually taken: an abort leaves the loop before the last plane.
            self.move_z_back_after_stack(z_steps_taken)

        # Increment FOV counter for Slack notification stats
        self._timepoint_fov_count += 1

    def _acquire_channel_first(self, region_id, current_path, fov, af_succeeded) -> int:
        """At each z level, acquire every selected channel before stepping to the next z level.

        Returns the number of z steps taken (fewer than NZ - 1 after an abort).
        """
        n_configs = len(self.selected_configurations)
        z_steps_taken = 0
        for z_level in range(self.NZ):
            file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"
            acquire_pos = self._begin_z_level(region_id, current_path, fov, z_level, file_ID)

            # iterate through selected modes
            try:
                for config_idx, config in enumerate(self.selected_configurations):
                    if self.abort_requested_fn():
                        break
                    self._apply_channel_z_offset(config, af_succeeded)
                    current_image = fov * self.NZ * n_configs + z_level * n_configs + config_idx + 1
                    self._acquire_channel_image(
                        config, config_idx, file_ID, current_path, z_level, region_id, fov, current_image
                    )
            finally:
                self._reset_channel_z_offset()

            self._finish_z_level(region_id, fov, z_level, acquire_pos)

            # On an abort, stop here: the caller (run_coordinate_acquisition) handles it once. Carrying on
            # would still try every remaining z level and channel, each waiting out its frame timeout.
            if self.abort_requested_fn():
                break

            # update FOV counter
            self.af_fov_count = self.af_fov_count + 1

            if z_level < self.NZ - 1:
                self.move_z_for_stack()
                z_steps_taken += 1
        return z_steps_taken

    def _acquire_z_first(self, region_id, current_path, fov, af_succeeded) -> int:
        """For each selected channel, acquire the whole z stack before switching to the next channel.

        Per-z bookkeeping (coordinates.csv row, FOV signal, AF counter) happens during the first
        channel's pass only. Positions are recorded without the channel z-offset, matching the
        channel-first order. Returns the number of z steps above the first plane where it stopped
        (NZ - 1 unless aborted), so move_z_back_after_stack() applies unchanged.
        """
        n_configs = len(self.selected_configurations)
        z_steps_taken = 0
        try:
            for config_idx, config in enumerate(self.selected_configurations):
                if self.abort_requested_fn():
                    break
                if config_idx > 0:
                    self._move_z_to_stack_start(stack_start)
                    z_steps_taken = 0
                    if self.laser_af_per_stack and self.do_reflection_af:
                        if self.abort_requested_fn():
                            break
                        af_succeeded = self._re_anchor_before_stack(config, region_id, fov, af_succeeded)
                self._apply_channel_z_offset(config, af_succeeded)
                # Where this channel's stack starts (piezo um or stage mm), offset included.
                stack_start = self.z_piezo_um if self.use_piezo else self.stage.get_pos().z_mm
                first_pass = config_idx == 0
                for z_level in range(self.NZ):
                    if self.abort_requested_fn():
                        break
                    file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"
                    if first_pass:
                        # Record the un-offset position (stage z, or piezo for piezo stacks).
                        offset_um = self._current_z_offset_um
                        acquire_pos = self.stage.get_pos()
                        z_piezo_um = None
                        if self.use_piezo:
                            z_piezo_um = self.z_piezo_um - offset_um
                        else:
                            acquire_pos = acquire_pos.model_copy(update={"z_mm": acquire_pos.z_mm - offset_um / 1000})
                        self._begin_z_level(region_id, current_path, fov, z_level, file_ID, acquire_pos)

                    current_image = fov * self.NZ * n_configs + config_idx * self.NZ + z_level + 1
                    self._acquire_channel_image(
                        config, config_idx, file_ID, current_path, z_level, region_id, fov, current_image
                    )

                    if first_pass:
                        self._finish_z_level(region_id, fov, z_level, acquire_pos, z_piezo_um)
                        if not self.abort_requested_fn():
                            self.af_fov_count = self.af_fov_count + 1

                    if z_level < self.NZ - 1 and not self.abort_requested_fn():
                        self.move_z_for_stack()
                        z_steps_taken += 1
        finally:
            self._reset_channel_z_offset()
        return z_steps_taken

    # --- Relative z-stack: Set Range with laser AF on, planes relative to each FOV's laser AF anchor ----------

    def _acquire_relative(self, region_id, current_path, fov) -> None:
        """One FOV of a relative z-stack, in place of perform_autofocus, prepare_z_stack, the stack steps and
        move_z_back_after_stack.

        Lock on (Z restored to where it was on any failure); with no valid lock-on record a failed first lock-on
        stops the run. Then every frame is an absolute move to anchor + z_min + k * dz + channel offset; a frame
        outside the actuator's travel is skipped and reported, never clamped. Z ends at the anchor: a failed return
        raises, unless another error is already ending the FOV (that one propagates, the failed return is logged).
        """
        af_succeeded = self._relative_lock_on(region_id, fov)
        self._out_of_travel_by_k = {}
        self._skipped_frames = []
        in_flight = None
        try:
            if not af_succeeded and self._require_first_lock_on:
                self._log.error(RELATIVE_FIRST_FOV_LOCK_ON_FAILED_MESSAGE)
                self._abort_due_to_error()
                return
            if not af_succeeded:
                self._log.info(
                    f"relative z-stack anchor, region '{region_id}' fov {fov}: {self._anchor_um:.3f} µm "
                    "(lock-on failed: stacking from the restored z)"
                )
            if (self.do_reflection_af or self.do_autofocus) and self.Nt > 1:
                # The next time point's lock-on starts here: the anchor on a stepper, so it starts from
                # displacement 0; the stage z on a piezo scope, where the piezo holds the anchor.
                self._last_time_point_z_pos[(region_id, fov)] = (
                    self.stage.get_pos().z_mm if self._actuator_is_piezo() else self._anchor_um / 1000
                )
            if self.z_stack_order == "Z FIRST":
                self._acquire_relative_z_first(region_id, current_path, fov, af_succeeded)
                self._timepoint_fov_count += 1
                return
            targets = self._relative_targets(self.selected_configurations, af_succeeded)
            skipped = [(c, k) for c, row in enumerate(targets) for k, target in enumerate(row) if target is None]
            self._report_skipped_frames(region_id, current_path, fov, skipped)
            self._acquire_relative_channel_first(region_id, current_path, fov, targets)
            self._timepoint_fov_count += 1
        except BaseException as e:
            in_flight = e
            raise
        finally:
            if self._skipped_frames:
                self._relative_fovs_with_skips += 1
            self._warn_skipped_frames(region_id, fov)
            try:
                self._move_actuator_to_um(self._anchor_um)
            except Exception:
                if in_flight is None:
                    raise  # Z is in an unknown place: the FOV fails
                # Never replace the error that ended the FOV with the failed return.
                self._log.error(
                    f"region '{region_id}' fov {fov}: returning Z to the anchor {self._anchor_um:.3f} µm failed "
                    f"after {type(in_flight).__name__}: {in_flight}",
                    exc_info=True,
                )
            finally:
                self._actuator_at_um = None  # outside a FOV the actuator position is not tracked

    def _acquire_relative_channel_first(self, region_id, current_path, fov, targets) -> None:
        """For each plane k, every channel whose target at k is inside the travel; one coordinates row per k."""
        z_min_um = self.laser_af_relative_range_um[0]
        dz_um = abs(self.deltaZ) * 1000
        nz = self._relative_nz()
        n_configs = len(self.selected_configurations)
        first_frame = True  # the FOV's first eligible frame settles after the lock-on
        for k in range(nz):
            file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{k:0{FILE_ID_PADDING}}"
            # Record the plane's nominal, un-offset position (computed even where it lies outside the travel).
            z_rel_um = z_min_um + k * dz_um
            nominal_um = self._anchor_um + z_rel_um
            acquire_pos = self.stage.get_pos()
            z_piezo_um = None
            if self._actuator_is_piezo():
                z_piezo_um = nominal_um
            else:
                acquire_pos = acquire_pos.model_copy(update={"z_mm": nominal_um / 1000})
            self._begin_z_level(region_id, current_path, fov, k, file_ID, acquire_pos)

            for config_idx, config in enumerate(self.selected_configurations):
                if self.abort_requested_fn():
                    break
                target_um = targets[config_idx][k]
                if target_um is None:
                    continue
                self._move_actuator_to_um(target_um, after_lock_on=first_frame)
                first_frame = False
                current_image = fov * nz * n_configs + k * n_configs + config_idx + 1
                self._acquire_channel_image(config, config_idx, file_ID, current_path, k, region_id, fov, current_image)

            self._finish_z_level(
                region_id,
                fov,
                k,
                acquire_pos,
                z_piezo_um,
                z_rel_um=z_rel_um,
                skipped_channels=self._out_of_travel_by_k.get(k, []),
            )
            # On an abort, stop here: the caller handles it once.
            if self.abort_requested_fn():
                break
            self.af_fov_count = self.af_fov_count + 1

    def _acquire_relative_z_first(self, region_id, current_path, fov, af_succeeded) -> None:
        """Z First: each channel images all of its eligible planes before the next channel.

        With laser AF per stack every later channel returns to the anchor and re-locks from there (never from
        the previous stack's last plane); its targets, skips and reports come from its own anchor. The first
        eligible move after each lock-on settles. The channel loop's first pass begins each plane (the
        coordinates row's data); the rows are written in a finally for every plane reached, with the channels
        skipped per plane accumulated across channels.
        """
        z_min_um = self.laser_af_relative_range_um[0]
        dz_um = abs(self.deltaZ) * 1000
        nz = self._relative_nz()
        n_configs = len(self.selected_configurations)
        settle = True  # the FOV's first eligible move settles after the per-FOV lock-on
        reached = []  # (k, acquire_pos, z_piezo_um, z_rel_um) of every plane the first channel began
        try:
            for config_idx, config in enumerate(self.selected_configurations):
                if self.abort_requested_fn():
                    break
                if config_idx > 0 and self.laser_af_per_stack:
                    self._move_actuator_to_um(self._anchor_um)
                    af_succeeded = self._relative_lock_on(region_id, fov, channel_name=config.name) or af_succeeded
                    settle = True  # the next eligible move, whichever channel makes it, settles
                if config_idx == 0 or self.laser_af_per_stack:
                    # A lock-on group: the whole FOV without AF per stack, else this channel's stack.
                    targets = self._relative_targets(self.selected_configurations, af_succeeded)
                    group = [config_idx] if self.laser_af_per_stack else range(n_configs)
                    self._report_skipped_frames(
                        region_id,
                        current_path,
                        fov,
                        [(c, k) for c in group for k, target in enumerate(targets[c]) if target is None],
                    )
                row = targets[config_idx]
                for k in range(nz):
                    if self.abort_requested_fn():
                        break
                    file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}_{k:0{FILE_ID_PADDING}}"
                    if config_idx == 0:
                        # Record the plane's nominal, un-offset position (even where it lies outside the travel).
                        z_rel_um = z_min_um + k * dz_um
                        nominal_um = self._anchor_um + z_rel_um
                        acquire_pos = self.stage.get_pos()
                        z_piezo_um = None
                        if self._actuator_is_piezo():
                            z_piezo_um = nominal_um
                        else:
                            acquire_pos = acquire_pos.model_copy(update={"z_mm": nominal_um / 1000})
                        self._begin_z_level(region_id, current_path, fov, k, file_ID, acquire_pos)
                        reached.append((k, acquire_pos, z_piezo_um, z_rel_um))
                    if row[k] is not None:
                        self._move_actuator_to_um(row[k], after_lock_on=settle)
                        settle = False
                        current_image = fov * nz * n_configs + config_idx * nz + k + 1
                        self._acquire_channel_image(
                            config, config_idx, file_ID, current_path, k, region_id, fov, current_image
                        )
                    if config_idx == 0 and not self.abort_requested_fn():
                        self.af_fov_count = self.af_fov_count + 1
        finally:
            # The caller's finally returns Z to the anchor.
            for k, acquire_pos, z_piezo_um, z_rel_um in reached:
                self._finish_z_level(
                    region_id,
                    fov,
                    k,
                    acquire_pos,
                    z_piezo_um,
                    z_rel_um=z_rel_um,
                    skipped_channels=self._out_of_travel_by_k.get(k, []),
                )

    def _relative_lock_on(self, region_id, fov, channel_name=None) -> bool:
        """Lock on to the laser AF reference plane. True on success.

        On any failure (a False return or an exception anywhere in the AF) Z is restored absolutely to where it
        was (z_pre) and settled; a failed restore raises, and run() treats that as fatal. Either way _anchor_um
        is the actuator position afterwards: the reference plane, or z_pre. A success is recorded as the last
        lock-on and lifts the first-FOV requirement. Counted per FOV, or per stack when channel_name names the
        channel re-locking before its stack.
        """
        self._actuator_at_um = None  # the AF moves the actuator itself; a failed restore leaves it unknown
        locked = self._laser_af_anchor(region_id, fov, restore_on_failure=True, channel_name=channel_name)
        if channel_name is None:
            if locked:
                self._laser_af_successes += 1
            else:
                self._laser_af_failures += 1
        elif locked:
            self._laser_af_per_stack_successes += 1
        else:
            self._laser_af_per_stack_failures += 1
        self._anchor_um = self._actuator_position_um()
        self._actuator_at_um = self._anchor_um
        where = f"region '{region_id}' fov {fov}" + (f" before the '{channel_name}' stack" if channel_name else "")
        if locked:
            self.laser_auto_focus_controller.record_lock_on()
            self._require_first_lock_on = False
            self._log.info(f"relative z-stack anchor, {where}: {self._anchor_um:.3f} µm (locked on)")
        else:
            # Whether the stack still runs from here is the caller's call (the first-FOV stop may end the run).
            self._log.error(f"laser AF lock-on failed, {where}: Z restored to {self._anchor_um:.3f} µm")
        return locked

    def _relative_nz(self) -> int:
        """Planes in the relative range at the run's dz (the same Nz the controller derived)."""
        return nz_for_range(*self.laser_af_relative_range_um, abs(self.deltaZ) * 1000)

    def _relative_targets(self, configs, af_succeeded: bool) -> List[List[Optional[float]]]:
        """Absolute actuator target (µm) per [channel][k] from the current anchor, None outside the travel."""
        z_min_um = self.laser_af_relative_range_um[0]
        self._relative_offsets_um = [self._channel_offset_um(config, af_succeeded) for config in configs]
        return frame_targets(
            self._anchor_um,
            z_min_um,
            self._relative_nz(),
            abs(self.deltaZ) * 1000,
            self._relative_offsets_um,
            self._actuator_travel_um(),
        )

    def _channel_offset_um(self, config, af_succeeded: bool) -> float:
        """The channel's z-offset (µm) from the anchor, under _apply_channel_z_offset's gates, else 0.

        The relative path adds it to each absolute target instead of moving by it (no apply / undo moves, so
        no clamp).
        """
        if not (self.apply_channel_offset and self.do_reflection_af):
            return 0.0
        raw_target = config.z_offset_um
        if raw_target is None:
            return 0.0
        if not math.isfinite(raw_target):
            self._log.warning(f"Channel '{config.name}' has non-finite z_offset_um={raw_target!r}; treating as 0")
            return 0.0
        if not af_succeeded:
            if raw_target:
                self._log.warning(
                    f"Skipping per-channel z-offset for '{config.name}' because reflection AF failed for this FOV"
                )
            return 0.0
        return float(raw_target)

    def _actuator_is_piezo(self) -> bool:
        # The actuator is the one laser AF drives: the piezo whenever the scope has one (its _move_z), else the
        # stage z. Relative mode refuses a stage stack on a piezo scope, so this matches use_piezo.
        return getattr(self.laser_auto_focus_controller, "piezo", None) is not None

    def _actuator_position_um(self) -> float:
        return self.piezo.position if self._actuator_is_piezo() else self.stage.get_pos().z_mm * 1000

    def _actuator_travel_um(self) -> Tuple[float, float]:
        """The actuator's travel in µm: the piezo's [0, range], or the stage's Z software limits."""
        if self._actuator_is_piezo():
            return 0.0, float(self.piezo.range_um)
        limits = control._def.SOFTWARE_POS_LIMIT
        return limits.Z_NEGATIVE * 1000, limits.Z_POSITIVE * 1000

    def _move_actuator_to_um(self, um: float, *, after_lock_on: bool = False) -> None:
        """Absolute actuator move (no clamp: the caller passes a target inside the travel), then settle: the piezo
        delay in software trigger, or the stage's stabilization time.

        after_lock_on: the move to the first frame after a lock-on (successful or rolled back). It also settles the
        stabilization time on a piezo, in both trigger modes, as prepare_z_stack does after the per-FOV AF; a
        stage move settles that anyway. Later steps of the stack keep the per-mode settle only.

        A target the actuator already holds (_actuator_at_um, within RELATIVE_SAME_TARGET_TOLERANCE_UM: the next
        channel at the same plane and offset, or a plane at the anchor) takes no move and no move settle; the
        post-lock-on settle still runs.
        """
        if self._actuator_at_um is not None and abs(um - self._actuator_at_um) <= RELATIVE_SAME_TARGET_TOLERANCE_UM:
            if after_lock_on:
                self._sleep(squid.stage.utils.z_stabilization_time_s())
            return
        self._actuator_at_um = None  # unknown until the move succeeds
        if self._actuator_is_piezo():
            self.piezo.move_to(um)
            self.z_piezo_um = um  # only after the move succeeded
            self._actuator_at_um = um
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
            if after_lock_on:
                self._sleep(squid.stage.utils.z_stabilization_time_s())
        else:
            self.stage.move_z_to(um / 1000)
            self._actuator_at_um = um
            self._sleep(squid.stage.utils.z_stabilization_time_s())

    def _report_skipped_frames(self, region_id, current_path, fov, skipped: List[Tuple[int, int]]) -> None:
        """Per lock-on group hook, called where the targets are computed, before the group's first frame: once per
        FOV without AF per stack, once per re-locked channel with it.

        skipped: (config_idx, k) pairs, from targets computed by _relative_targets for the current anchor.
        Accumulates the FOV's skipped channels per plane (the coordinates.csv rows) and the frames for the FOV's
        single warning (_warn_skipped_frames), counts the frames for the run summary, and sends the group's skip
        notice to the writers.
        """
        names = [config.name for config in self.selected_configurations]
        z_min_um = self.laser_af_relative_range_um[0]
        dz_um = abs(self.deltaZ) * 1000
        for config_idx, k in skipped:
            self._out_of_travel_by_k.setdefault(k, []).append(names[config_idx])
            target_um = self._anchor_um + z_min_um + k * dz_um + self._relative_offsets_um[config_idx]
            self._skipped_frames.append((k, names[config_idx], target_um))
        if not skipped:
            return
        self._relative_frames_skipped += len(skipped)
        self._send_skip_notice(region_id, current_path, fov, skipped)

    # --- Skip notices: a FOV's skipped frames reach the Zarr and OME-TIFF writers -------------------------------

    def _skip_notice_runners(self) -> List[Tuple[Type[Job], Optional[JobRunner]]]:
        """The runners that take skip notices: Zarr and OME-TIFF. Individual TIFFs write nothing for a skip."""
        return [(cls, runner) for cls, runner in self._job_runners if issubclass(cls, (SaveZarrJob, SaveOMETiffJob))]

    def _send_skip_notice(self, region_id, current_path, fov, skipped: List[Tuple[int, int]]) -> None:
        """Dispatch one lock-on group's skip notice, sized by the run's first frame, or hold it until that frame
        arrives (a notice never guesses the frame shape)."""
        if not self._skip_notice_runners():
            return
        config_idx, k = skipped[0]
        info = CaptureInfo(
            position=self.stage.get_pos(),
            z_index=k,
            capture_time=time.time(),
            configuration=self.selected_configurations[config_idx],
            save_directory=current_path,
            file_id=f"{region_id}_{fov:0{FILE_ID_PADDING}}_{k:0{FILE_ID_PADDING}}",
            region_id=region_id,
            fov=fov,
            configuration_idx=config_idx,
            time_point=self.time_point,
        )
        frames = tuple((int(c), int(z)) for c, z in skipped)
        with self._skip_notice_lock:
            if self._frame_format is None:
                self._pending_skip_notices.append((info, frames))
                return
            frame_format = self._frame_format
        self._dispatch_skip_notice(info, frames, frame_format)

    def _dispatch_skip_notice(
        self, info: CaptureInfo, frames: Tuple[Tuple[int, int], ...], frame_format: Tuple[Tuple[int, int], str]
    ) -> None:
        """Send the notice through the Zarr and OME-TIFF runners, in order with their image jobs. A failure is logged
        and counted as an acquisition error; it never stops the acquisition (the skips stay in coordinates.csv)."""
        frame_shape, dtype = frame_format
        notice = dataclasses.replace(info, skipped_frames=SkippedFrames(frames, frame_shape, dtype))
        for job_class, job_runner in self._skip_notice_runners():
            job = job_class(
                capture_info=notice, capture_image=JobImage(image_array=None), save_settings=self._save_settings
            )
            try:
                if job_runner is not None:
                    sent = job_runner.dispatch(job)
                else:
                    job.run()
                    sent = True
            except Exception:
                self._log.exception(f"{job_class.__name__} failed on the skip notice of {self._fov_name(info)}")
                sent = False
            if not sent:
                self._log.error(f"The skip notice of {self._fov_name(info)} did not reach {job_class.__name__}")
                self._acquisition_error_count += 1

    @staticmethod
    def _fov_name(info: CaptureInfo) -> str:
        return f"region '{info.region_id}' fov {info.fov} (t={info.time_point})"

    def _record_first_frame(self, image: np.ndarray) -> None:
        """Frame callback (camera thread), on the run's first frame after its jobs are dispatched: record its
        (height, width) and dtype for every skip notice, and send the notices that waited for it."""
        with self._skip_notice_lock:
            if self._frame_format is not None:
                return
            self._frame_format = ((int(image.shape[0]), int(image.shape[1])), str(image.dtype))
            pending, self._pending_skip_notices = self._pending_skip_notices, []
            # Sent under the lock, so a notice the worker reports meanwhile goes out after these.
            for info, frames in pending:
                self._dispatch_skip_notice(info, frames, self._frame_format)

    def _flush_pending_skip_notices(self) -> None:
        """At run end, with the last frames in: notices still waiting mean the run took no frame at all. One dark
        frame (_frame_shape_without_a_frame) sizes them. If the camera delivers none, nothing can size the arrays:
        the notices are dropped, logged and counted as an acquisition error (the skips stay in coordinates.csv, the
        log and the run summary). Never raises, so the cleanup after it always runs."""
        if not self._relative_mode:
            return
        try:
            with self._skip_notice_lock:
                pending, self._pending_skip_notices = self._pending_skip_notices, []
                frame_format = self._frame_format
            if not pending:
                return
            if frame_format is None:
                frame_format = self._frame_shape_without_a_frame()
            if frame_format is None:
                fovs = ", ".join(self._fov_name(info) for info, _ in pending)
                self._log.error(
                    f"The camera delivered no frame to size the arrays: {len(pending)} skip notice(s) not sent "
                    f"({fovs}). Their FOVs may have no Zarr array or OME-TIFF file; the skipped frames are listed "
                    "in coordinates.csv."
                )
                self._acquisition_error_count += 1
                return
            for info, frames in pending:
                self._dispatch_skip_notice(info, frames, frame_format)
        except Exception:
            self._log.exception("Sending the pending skip notices failed")
            self._acquisition_error_count += 1

    def _frame_shape_without_a_frame(self) -> Optional[Tuple[Tuple[int, int], str]]:
        """((height, width), dtype) of one real, processed frame, so binning, hardware ROI, rotation and software
        crop are whatever the camera delivers; nothing is derived from its ROI, resolution or crop settings.

        Only for a run that took no frame at all, after its last FOV: the acquisition's illumination is released
        and no illumination time is sent, so it is a dark frame. The frame callbacks are disabled around it (the
        documented use for frames outside the acquisition), so it never reaches the save jobs. None, with an error
        logged, if the camera is not ready within the frame timeout, the trigger fails, or no frame is read.
        """
        camera = self.camera
        camera.enable_callbacks(False)
        try:
            timeout_s = self._frame_wait_timeout_s()
            deadline = time.monotonic() + timeout_s
            while not camera.get_ready_for_trigger():
                if time.monotonic() >= deadline:
                    self._log.error(f"Frame-shape frame: the camera was not ready for a trigger within {timeout_s:g} s")
                    return None
                self._sleep(0.001)
            try:
                camera.send_trigger()
            except Exception as e:
                self._log.error(f"Frame-shape frame: the trigger failed: {e!r}")
                return None
            try:
                camera_frame = camera.read_camera_frame()
            except Exception as e:
                self._log.error(f"Frame-shape frame: reading the frame failed: {e!r}")
                return None
            image = camera_frame.frame if camera_frame is not None else None
            if image is None:
                self._log.error("Frame-shape frame: the camera returned no frame")
                return None
            return (int(image.shape[0]), int(image.shape[1])), str(image.dtype)
        finally:
            camera.enable_callbacks(True)

    def _warn_skipped_frames(self, region_id, fov) -> None:
        """One warning per FOV, at its end: every frame left out because its target is outside the travel."""
        if not self._skipped_frames:
            return
        low_um, high_um = self._actuator_travel_um()
        frames = []
        for k, name, target_um in self._skipped_frames:
            reason = f"below {low_um:g} µm" if target_um < low_um else f"past {high_um:g} µm"
            frames.append(f"plane {k} '{name}' at {target_um:.2f} µm ({reason})")
        self._log.warning(
            f"region '{region_id}' fov {fov}: {len(frames)} frame(s) outside the travel "
            f"[{low_um:g}, {high_um:g}] µm skipped, not clamped: " + "; ".join(frames)
        )

    def _begin_z_level(self, region_id, current_path, fov, z_level, file_ID, acquire_pos=None):
        if acquire_pos is None:
            acquire_pos = self.stage.get_pos()
        metadata = {"x": acquire_pos.x_mm, "y": acquire_pos.y_mm, "z": acquire_pos.z_mm}
        self._log.info(f"Acquiring image: ID={file_ID}, Metadata={metadata}")

        # A relative stack records its anchor instead (_acquire_relative): plane 0 is below the reference.
        if z_level == 0 and (self.do_reflection_af or self.do_autofocus) and self.Nt > 1 and not self._relative_mode:
            self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

        # laser af characterization mode
        if self.laser_auto_focus_controller and self.laser_auto_focus_controller.characterization_mode:
            image = self.laser_auto_focus_controller.get_image()
            saving_path = os.path.join(current_path, file_ID + "_laser af camera" + ".bmp")
            iio.imwrite(saving_path, image)

        return acquire_pos

    def _finish_z_level(
        self, region_id, fov, z_level, acquire_pos, z_piezo_um=None, z_rel_um=None, skipped_channels=None
    ):
        # updates coordinates df
        self.update_coordinates_dataframe(
            region_id,
            z_level,
            acquire_pos,
            fov,
            z_piezo_um=z_piezo_um,
            z_rel_um=z_rel_um,
            skipped_channels=skipped_channels,
        )
        self.callbacks.signal_current_fov(acquire_pos.x_mm, acquire_pos.y_mm)

    def _acquire_channel_image(self, config, config_idx, file_ID, current_path, z_level, region_id, fov, current_image):
        with self._timing.get_timer("acquire_camera_image"):
            # TODO(imo): This really should not look for a string in a user configurable name.  We
            # need some proper flag on the config to signal this instead...
            if "RGB" in config.name:
                self.acquire_rgb_image(config, file_ID, current_path, z_level, region_id, fov)
            else:
                self.acquire_camera_image(
                    config,
                    file_ID,
                    current_path,
                    z_level,
                    region_id=region_id,
                    fov=fov,
                    config_idx=config_idx,
                )

        self.callbacks.signal_region_progress(
            RegionProgressUpdate(current_fov=current_image, region_fovs=self.total_scans)
        )

    def _select_config(self, config: AcquisitionChannel):
        self.callbacks.signal_current_configuration(config)
        self.liveController.set_microscope_mode(config)
        self.wait_till_operation_is_completed()
        self._applied_config = config.model_copy(deep=True)

    def _config_already_applied(self, config: AcquisitionChannel) -> bool:
        # Compared by value against the copy, so a channel edited since it was applied is applied again.
        return (
            self._applied_config is not None
            and config == self._applied_config
            and self.liveController.currentConfiguration == config
        )

    def _scan_unit_triggers_camera(self) -> bool:
        # With an NL5/AION and NL5_USE_DOUT, camera.send_trigger() runs one confocal scan and returns once the
        # unit has triggered the camera (see microscope.py's acquisition_camera_hw_trigger_fn).
        return self.microscope.addons.nl5 is not None and control._def.NL5_USE_DOUT

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
            if not self._laser_af_anchor(region_id, fov, restore_on_failure=False):
                self._laser_af_failures += 1
                return False
            self._laser_af_successes += 1
        return True

    def _laser_af_anchor(self, region_id, fov, *, restore_on_failure: bool, channel_name=None) -> bool:
        """Closed-loop laser AF to the reference plane, then the region's open-loop offset. True on success.

        move_to_target reports soft failures (no reference, NaN displacement, displacement out of range,
        cross-correlation mismatch) via its return value, NOT by raising; a correction past the piezo's
        travel raises from piezo.move_to. Either way the caller must treat the FOV as unanchored, or the
        per-channel z-offset gate would apply offsets from an unanchored z.

        Z is not reliably unchanged on failure: move_to_target can succeed and leave Z at the reference
        plane before the region offset raises. With restore_on_failure (the per-stack re-AF, and every lock-on
        of a relative z-stack) the absolute pre-AF Z is restored (an exception from that restore propagates:
        run() treats it as fatal) and the piezo cache is re-read on every outcome, since the laser AF controller
        moves the piezo directly (its _move_z). The per-FOV caller re-reads the cache itself in
        acquire_at_position.
        """
        if restore_on_failure:
            # The controller moves the piezo whenever the scope has one (its _move_z), whichever actuator runs
            # the stack; so the restore must follow the controller, not use_piezo.
            af_moves_piezo = getattr(self.laser_auto_focus_controller, "piezo", None) is not None
            pre_af = self.piezo.position if af_moves_piezo else self.stage.get_pos().z_mm
        try:
            target_um = self.region_laser_af_offsets.get(region_id, 0.0)
            # Anchor closed-loop at the reference (displacement 0), where spot-alignment
            # verification is valid, then apply the per-region offset as an OPEN-LOOP
            # relative move. Driving move_to_target() straight to a nonzero displacement
            # would always fail verification (its crop is fixed at x_reference) and revert.
            af_succeeded = self.laser_auto_focus_controller.move_to_target(0.0)
            if af_succeeded and target_um:
                self._log.info(f"applying per-region laser AF offset for region '{region_id}': {target_um:+.2f} µm")
                self.laser_auto_focus_controller.apply_relative_offset_um(target_um)
        except Exception as e:
            self._log.error(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! laser AF failed !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                exc_info=e,
            )
            self._save_laser_af_diagnostic(region_id, fov, channel_name)  # best effort: never blocks the restore
            af_succeeded = False
        if restore_on_failure:
            if not af_succeeded:
                if af_moves_piezo:
                    self.piezo.move_to(pre_af)
                    if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                        self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
                else:
                    self.stage.move_z_to(pre_af)
                # Settle as prepare_z_stack does, in both trigger modes: after a partial AF move and its
                # rollback the next exposure must not start before the actuator has settled.
                self._sleep(squid.stage.utils.z_stabilization_time_s())
            if self.use_piezo:
                self.z_piezo_um = self.piezo.position
        return af_succeeded

    def _save_laser_af_diagnostic(self, region_id, fov, channel_name=None) -> None:
        """Best effort: save the focus-camera image of a failed laser AF next to the time point's images.

        Named per region and FOV, plus the channel for a per-stack re-AF, so one failure never overwrites another.
        """
        try:
            file_ID = f"{region_id}_{fov:0{FILE_ID_PADDING}}"
            if channel_name:
                file_ID += "_" + channel_name.replace(" ", "_")
            file_ID += "_focus_camera.bmp"
            saving_path = os.path.join(self.base_path, self.experiment_ID, str(self.time_point), file_ID)
            iio.imwrite(saving_path, self.laser_auto_focus_controller.image)
        except Exception as e:
            self._log.warning(f"Could not save the laser AF focus-camera image for region '{region_id}': {e}")

    def _re_anchor_before_stack(self, config, region_id, fov, af_succeeded: bool) -> bool:
        """Z First "AF per stack": laser AF before this later channel's stack, at the stack start (the
        bottom for From Center: on an inverted microscope the reflection comes off the coverslip there).

        Returns the af_succeeded to use for this channel: True after a successful re-AF (a verified anchor,
        even if the FOV's per-FOV AF had failed), else the FOV's current value (the previous anchor stands and
        the channel offset is still applied from it).
        """
        if self._laser_af_anchor(region_id, fov, restore_on_failure=True, channel_name=config.name):
            # The AF consumed the previous channel's offset: Z is at the anchored plane again.
            self._current_z_offset_um = 0.0
            self._laser_af_per_stack_successes += 1
            self._log.info(f"laser AF before '{config.name}' stack: anchored")
            # Exactly as per FOV: From Center moves down half a stack, and the stack settles before its first frame.
            self.prepare_z_stack()
            return True
        self._laser_af_per_stack_failures += 1
        self._log.error(f"laser AF before '{config.name}' stack failed; keeping the previous anchor")
        return af_succeeded

    def prepare_z_stack(self):
        # move to bottom of the z stack
        if self.z_stacking_config == "FROM CENTER":
            self._move_down_half_stack()
        self._sleep(squid.stage.utils.z_stabilization_time_s())

    def _move_down_half_stack(self):
        """From Center: from the anchored plane down to the stack bottom, with the stack's own mover."""
        if self.use_piezo:
            # The piezo takes the whole stack (centring included): on a piezo-only Z the stage
            # has no z to move, and on a stage + piezo system a stage move here was never undone.
            self.z_piezo_um -= self.deltaZ * 1000 * _z_steps_below_center(self.NZ)
            self.piezo.move_to(self.z_piezo_um)
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            self.stage.move_z(-self.deltaZ * _z_steps_below_center(self.NZ))
            self._sleep(squid.stage.utils.z_stabilization_time_s())

    def _move_z_for_offset(self, delta_um: float) -> float:
        """Dispatch a relative z move via piezo when use_piezo, otherwise via stage.

        Piezo moves are clamped to [0, piezo.range_um] with a warning log if the offset
        would otherwise drive the piezo out of range. Stage moves inherit backlash
        compensation from CephlaStage.move_z().

        Returns:
            The delta actually moved (may differ from requested when the piezo is clamped
            to its range).
        """
        if self.use_piezo:
            requested_piezo_um = self.z_piezo_um + delta_um
            clamped = max(0.0, min(self.piezo.range_um, requested_piezo_um))
            if clamped != requested_piezo_um:
                self._log.warning(
                    f"channel z-offset {delta_um:+.2f} µm would drive piezo out of range "
                    f"({requested_piezo_um:.2f} µm vs [0, {self.piezo.range_um}]); clamping to "
                    f"{clamped:.2f} µm"
                )
            actual_delta_um = clamped - self.z_piezo_um
            # Command the move first; only update the software cache after it succeeds,
            # otherwise an exception in move_to leaves z_piezo_um pointing at a position
            # the hardware never reached, biasing every subsequent z_stack step.
            self.piezo.move_to(clamped)
            self.z_piezo_um = clamped
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
            self._log.info(
                f"[z-offset] moved {actual_delta_um:+.2f} µm via piezo; piezo at {self.z_piezo_um:.2f} µm, "
                f"actual stage z = {self.stage.get_pos().z_mm:.5f} mm"
            )
            return actual_delta_um
        else:
            self.stage.move_z(delta_um / 1000)
            self.wait_till_operation_is_completed()
            self._sleep(squid.stage.utils.z_stabilization_time_s())
            self._log.info(
                f"[z-offset] moved {delta_um:+.2f} µm via stage; actual z = {self.stage.get_pos().z_mm:.5f} mm"
            )
            return delta_um

    # Sub-µm tolerance for offset deltas; well below stage µ-step and piezo step resolution.
    # Prevents accumulated float-subtraction error from triggering spurious sub-nm moves.
    _Z_OFFSET_EPS_UM = 1e-4

    def _apply_channel_z_offset(self, config, af_succeeded: bool) -> None:
        """Move z by the delta needed to reach this channel's per-channel z-offset.

        No-op when laser AF is not the active AF method, when the 'Apply channel offset'
        flag is off, when reflection AF failed for this FOV (af_succeeded is False, so no
        anchor), when the channel's z_offset_um is non-finite, or when the resulting delta
        is below the move-resolution tolerance.
        """
        if not (self.apply_channel_offset and self.do_reflection_af):
            return
        if not af_succeeded:
            # Reflection AF failed for this FOV; the FOV is at an unanchored z. Applying
            # the channel offset would shift the FOV by an unintended amount relative to
            # the absent reference, so skip.
            self._log.warning(
                f"Skipping per-channel z-offset for '{config.name}' because reflection AF " f"failed for this FOV"
            )
            return
        raw_target = config.z_offset_um
        if raw_target is None:
            target_um = 0.0
        elif not math.isfinite(raw_target):
            self._log.warning(
                f"Channel '{config.name}' has non-finite z_offset_um={raw_target!r}; "
                f"treating as 0 (will reset to the un-offset baseline if a prior channel "
                f"already applied an offset)"
            )
            target_um = 0.0
        else:
            target_um = raw_target
        delta_um = target_um - self._current_z_offset_um
        if abs(delta_um) < self._Z_OFFSET_EPS_UM:
            return
        actual_delta_um = self._move_z_for_offset(delta_um)
        self._current_z_offset_um = self._current_z_offset_um + actual_delta_um

    def _reset_channel_z_offset(self) -> None:
        """Undo any remaining offset so z returns to the un-offset baseline."""
        if abs(self._current_z_offset_um) < self._Z_OFFSET_EPS_UM:
            # Snap residual FP drift to exact zero so the tracker doesn't grow over runs.
            self._current_z_offset_um = 0.0
            return
        saved = self._current_z_offset_um
        try:
            self._move_z_for_offset(-saved)
            # Only zero the tracker after the move actually succeeds; if it raised below,
            # the tracker stays at `saved` so the next reset attempt knows the outstanding
            # amount and a follow-on apply computes deltas from the right baseline.
            self._current_z_offset_um = 0.0
        except Exception:
            self._log.exception(
                f"Failed to reset channel z-offset of {saved:+.2f} µm; stage may be at "
                f"non-baseline z (tracker retained at {saved:+.2f} µm for the next reset)"
            )

    def _log_ignored_offsets(self) -> None:
        """Log the per-channel z-offset plan at acquisition start.

        Cases:
        - Non-finite offsets exist → warn separately; _apply_channel_z_offset treats
          them as 0, so they must not appear in a "will be applied" summary.
        - Gate is ON AND finite non-zero offsets exist → log they'll be applied (helps
          diagnose 'offsets not applied' reports by confirming the worker saw them).
        - Gate is OFF AND finite non-zero offsets exist → log they're being ignored,
          with the reason.
        """
        non_finite = []
        finite_non_zero = []
        for c in self.selected_configurations:
            offset = c.z_offset_um or 0.0
            if not math.isfinite(offset):
                non_finite.append(c.name)
            elif offset != 0.0:
                finite_non_zero.append((c.name, offset))
        if non_finite:
            self._log.warning(
                f"[multi-point] Channels with non-finite z_offset_um (treated as 0): [{', '.join(non_finite)}]"
            )
        if not finite_non_zero:
            return
        summary = ", ".join(f"{name}: {off:+.2f}µm" for name, off in finite_non_zero)
        if self.apply_channel_offset and self.do_reflection_af:
            self._log.info(f"[multi-point] Per-channel z-offsets will be applied: [{summary}]")
            return
        reason = "laser AF off" if not self.do_reflection_af else "'Apply channel offset' unchecked"
        self._log.info(f"[multi-point] {reason} — ignoring non-zero z-offsets on channels: [{summary}]")

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
                    self._abort_due_to_error()
                    return

                image = camera_frame.frame
                if not camera_frame or image is None:
                    self._log.warning("image in frame callback is None. Something is really wrong, aborting!")
                    self._abort_due_to_error()
                    return

                # Increment image counter for Slack notification stats
                self._timepoint_image_count += 1
                self.image_count += 1
                self._run_state_beat()

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
                                self._abort_due_to_error()
                                return
                        else:
                            try:
                                # NOTE(imo): We don't have any way of people using results, so for now just
                                # grab and ignore it.
                                result = job.run()
                            except Exception:
                                self._log.exception("Failed to execute job, abandoning acquisition!")
                                self._abort_due_to_error()
                                return

                    # A relative run's first frame sizes its skip notices; the ones waiting for it go out now.
                    if self._relative_mode and self._frame_format is None:
                        self._record_first_frame(image)

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
        if not self._config_already_applied(config):
            self._select_config(config)

        # trigger acquisition (including turning on the illumination) and read frame
        camera_illumination_time = self.camera.get_exposure_time()
        if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
            self.liveController.turn_on_illumination()
            self.wait_till_operation_is_completed()
            camera_illumination_time = None
        # In HARDWARE mode with an NL5/AION (NL5_USE_DOUT), camera.send_trigger() below starts the confocal
        # scan through the camera's hardware trigger function. Do not also start one here: that second scan
        # gave every image an extra, untracked camera frame (black frames, channels mixed, aborts).
        # This is some large timeout that we use just so as to not block forever
        with self._timing.get_timer("_ready_for_next_trigger.wait"):
            if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
                self._log.error("Frame callback never set _have_last_triggered_image callback! Aborting acquisition.")
                self._abort_due_to_error()
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
            try:
                self.camera.send_trigger(illumination_time=camera_illumination_time)
            except ConfocalScanFailedError as e:
                # No frame will come for this image: abort now, with the reason, rather than waiting out the
                # frame timeout. Returning (not raising) keeps the z stack's cleanup, so Z still goes back.
                self._log.error(f"Aborting the acquisition: {e}")
                self._current_capture_info = None
                self._ready_for_next_trigger.set()
                self._abort_due_to_error()
                return

        with self._timing.get_timer("exposure_time_done_sleep_hw or wait_for_image_sw"):
            if self.liveController.trigger_mode == TriggerMode.HARDWARE and self._scan_unit_triggers_camera():
                # send_trigger() above already blocked through the confocal scan, so sleeping a full frame time
                # from here on ended long after the frame had arrived (AION: ~40 ms per image). The frame arriving
                # proves its exposure has ended; waiting at most a frame time keeps the old upper bound when it
                # doesn't come (a dropped trigger is still caught by the next image's _ready_for_next_trigger wait).
                self._ready_for_next_trigger.wait(self.camera.get_total_frame_time() / 1e3)
            elif self.liveController.trigger_mode == TriggerMode.HARDWARE:
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
                    self._abort_due_to_error()
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
        # Undo any stranded per-channel offset before saving abort state
        self._reset_channel_z_offset()
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
            self._sleep(squid.stage.utils.z_stabilization_time_s())

    def _move_z_to_stack_start(self, stack_start: float):
        """Return to the first z level of the stack (z-first order, between channels).

        An absolute move to the recorded start (piezo um, or stage mm): a relative move back would not
        exactly undo the stack's steps, since the stage rounds each move to whole microsteps, and the
        error would build up channel after channel.
        """
        if self.use_piezo:
            self.z_piezo_um = stack_start
            self.piezo.move_to(self.z_piezo_um)
            if self.liveController.trigger_mode == TriggerMode.SOFTWARE:
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            self.stage.move_z_to(stack_start)
            self._sleep(squid.stage.utils.z_stabilization_time_s())

    def move_z_back_after_stack(self, steps_taken: Optional[int] = None):
        """Return Z to where the stack started, undoing steps_taken z steps (default: a full stack)."""
        if steps_taken is None:
            steps_taken = self.NZ - 1
        # Undo the steps taken; From Center also climbs back to the centre the stack started below.
        steps_to_start = -steps_taken
        if self.z_stacking_config == "FROM CENTER":
            steps_to_start += _z_steps_below_center(self.NZ)
        if self.use_piezo:
            self.z_piezo_um = self.z_piezo_um + self.deltaZ * 1000 * steps_to_start
            self.piezo.move_to(self.z_piezo_um)
            if (
                self.liveController.trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            self.stage.move_z(self.deltaZ * steps_to_start)
