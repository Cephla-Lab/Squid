import ast
import hashlib
import os
import queue
import time
from queue import Empty
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple, Type, TYPE_CHECKING

import imageio as iio
import numpy as np
import pandas as pd

from _def import (
    Acquisition,
    ACQUISITION_MAX_PENDING_JOBS,
    ACQUISITION_MAX_PENDING_MB,
    ACQUISITION_THROTTLE_TIMEOUT_S,
    ACQUISITION_THROTTLING_ENABLED,
    DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S,
    DOWNSAMPLED_VIEW_JOB_TIMEOUT_S,
    FILE_ID_PADDING,
    FILE_SAVING_OPTION,
    FileSavingOption,
    SCAN_STABILIZATION_TIME_MS_X,
    SCAN_STABILIZATION_TIME_MS_Y,
    SCAN_STABILIZATION_TIME_MS_Z,
    SEGMENTATION_CROP,
    SIMULATED_DISK_IO_ENABLED,
    TriggerMode,
    ZARR_USE_6D_FOV_DIMENSION,
)
import squid.core.utils.hardware_utils as utils
from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.managers import ChannelConfigService
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.controllers.multipoint.multi_point_utils import AcquisitionParameters
from squid.backend.controllers.multipoint.dependencies import AcquisitionDependencies
from squid.backend.managers import ObjectiveStore
from squid.core.config.models import AcquisitionChannel
from squid.core.abc import CameraFrame
import squid.core.logging
import squid.backend.controllers.multipoint.job_processing
from squid.backend.io import utils_acquisition
from squid.backend.controllers.multipoint.job_processing import (
    AcquisitionInfo,
    CaptureInfo,
    SaveImageJob,
    SaveOMETiffJob,
    SaveZarrJob,
    ZarrWriterInfo,
    ZarrWriteResult,
    DownsampledViewJob,
    DownsampledViewResult,
    Job,
    JobImage,
    JobRunner,
    JobResult,
)
from squid.backend.controllers.multipoint.downsampled_views import (
    DownsampledViewManager,
    calculate_overlap_pixels,
    parse_well_id,
)
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag
from squid.core.config.test_timing import scale_duration
from squid.backend.controllers.multipoint.progress_tracking import (
    ProgressTracker,
    CoordinateTracker,
)
from squid.backend.controllers.orchestrator.state import AddWarningCommand
from squid.backend.controllers.multipoint.position_zstack import (
    PositionController,
    ZStackConfig,
    ZStackExecutor,
)
from squid.backend.controllers.multipoint.focus_operations import AutofocusExecutor
from squid.backend.controllers.multipoint.backpressure import (
    BackpressureController,
    BackpressureStats,
)
from squid.backend.controllers.multipoint.fov_task import (
    FovStatus,
    FovTask,
    FovTaskList,
)
from squid.backend.controllers.multipoint.events import (
    JumpToFovCommand,
    SkipFovCommand,
    RequeueFovCommand,
    DeferFovCommand,
    ReorderFovsCommand,
    FovTaskStarted,
    FovTaskCompleted,
    FovTaskListChanged,
)
from squid.backend.controllers.multipoint.checkpoint import (
    CheckpointPlanMismatch,
    MultiPointCheckpoint,
    get_checkpoint_path,
    find_latest_checkpoint,
)
from squid.core.config.feature_flags import get_feature_flags
from squid.core.events import AutofocusMode

if TYPE_CHECKING:
    from squid.backend.services import (
        CameraService,
        StageService,
        PeripheralService,
        PiezoService,
        FluidicsService,
        NL5Service,
        IlluminationService,
        FilterWheelService,
    )
    from squid.core.events import EventBus
    from squid.backend.controllers.autofocus.continuous_focus_lock import ContinuousFocusLockController
    from squid.backend.controllers.autofocus.focus_lock_simulator import FocusLockSimulator

from squid.core.events import PlateViewInit, PlateViewUpdate

# Module-level logger for static methods
_log = squid.core.logging.get_logger(__name__)


class SummarizeResult(NamedTuple):
    """Result from processing job output queues."""

    none_failed: bool  # True if no jobs failed (or no results to process)
    had_results: bool  # True if any results were pulled from queue


class MultiPointWorker:
    def __init__(
        self,
        auto_focus_controller: Optional[AutoFocusController],
        laser_auto_focus_controller: Optional[LaserAutofocusController],
        objective_store: ObjectiveStore,
        channel_configuration_mananger: ChannelConfigService,
        acquisition_parameters: AcquisitionParameters,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        trigger_mode: TriggerMode = TriggerMode.SOFTWARE,
        illumination_service: Optional["IlluminationService"] = None,
        filter_wheel_service: Optional["FilterWheelService"] = None,
        enable_channel_auto_filter_switching: bool = True,
        focus_lock_controller: Optional["ContinuousFocusLockController | FocusLockSimulator"] = None,
        *,
        dependencies: Optional[AcquisitionDependencies] = None,
        extra_job_classes: list[type[Job]] | None = None,
        abort_on_failed_jobs: bool = True,
        piezo_service: Optional["PiezoService"] = None,
        fluidics_service: Optional["FluidicsService"] = None,
        nl5_service: Optional["NL5Service"] = None,
        stream_handler: Optional[object] = None,
        alignment_widget: Optional[object] = None,
    ):
        self._log = squid.core.logging.get_logger(__class__.__name__)
        self._timing = utils.TimingManager("MultiPointWorker Timer Manager")
        if dependencies is not None:
            dependencies.validate()
            auto_focus_controller = dependencies.controllers.autofocus
            laser_auto_focus_controller = dependencies.controllers.laser_autofocus
            focus_lock_controller = dependencies.controllers.focus_lock
            camera_service = dependencies.services.camera
            stage_service = dependencies.services.stage
            peripheral_service = dependencies.services.peripheral
            event_bus = dependencies.services.event_bus
            illumination_service = dependencies.services.illumination
            filter_wheel_service = dependencies.services.filter_wheel
            piezo_service = dependencies.services.piezo
            fluidics_service = dependencies.services.fluidics
            nl5_service = dependencies.services.nl5
            stream_handler = dependencies.services.stream_handler

        self._feature_flags = get_feature_flags()
        self._camera_service: "CameraService" = camera_service
        self._stage_service: "StageService" = stage_service
        self._peripheral_service: "PeripheralService" = peripheral_service
        self._piezo_service = piezo_service
        self._fluidics_service = fluidics_service
        self._nl5_service = nl5_service
        self._event_bus = event_bus
        self._stream_handler = stream_handler
        self._trigger_mode = trigger_mode
        self._illumination_service = illumination_service
        self._filter_wheel_service = filter_wheel_service
        self._enable_channel_auto_filter_switching = enable_channel_auto_filter_switching
        self._focus_lock_controller = focus_lock_controller
        self._alignment_widget = alignment_widget

        if self._camera_service is None or self._stage_service is None or self._peripheral_service is None:
            raise ValueError(
                "MultiPointWorker requires CameraService, StageService, and PeripheralService"
            )

        # Track acquisition timing for ETA calculation
        self._acquisition_start_time: Optional[float] = None
        self._total_images_to_acquire: int = 0
        self._images_acquired: int = 0

        # Controller references
        self.autofocusController: Optional[AutoFocusController] = auto_focus_controller
        self.laser_auto_focus_controller: Optional[LaserAutofocusController] = (
            laser_auto_focus_controller
        )
        self.objectiveStore: ObjectiveStore = objective_store
        self.channelConfigurationManager: ChannelConfigService = (
            channel_configuration_mananger
        )
        self.use_fluidics = acquisition_parameters.use_fluidics
        self._aborted: bool = False
        self._abort_requested = ThreadSafeFlag(initial=False)
        self._pause_requested = ThreadSafeFlag(initial=False)

        # FOV Task System - first-class FOV tasks with stable IDs
        self._fov_task_list: Optional[FovTaskList] = None
        self._fov_command_queue: "queue.Queue[JumpToFovCommand | SkipFovCommand | RequeueFovCommand | DeferFovCommand | ReorderFovsCommand]" = queue.Queue()
        self._current_round_index: int = 0  # For FOV event context
        self._start_fov_index: int = 0  # For resume support - start from this FOV
        self._fatal_error: Optional[Exception] = None

        # Checkpoint configuration
        self._checkpoint_enabled: bool = True  # Save checkpoints after each FOV
        self._checkpoint_interval: int = 1  # Save every N FOVs (1 = every FOV)
        self._fov_since_checkpoint: int = 0  # Counter for checkpoint interval
        self._max_checkpoints: int = 5  # Keep last N checkpoints per time point
        self._resumed_from_checkpoint: bool = False

        self.NZ = acquisition_parameters.NZ
        self.deltaZ = acquisition_parameters.deltaZ

        self.Nt = acquisition_parameters.Nt
        self.dt = scale_duration(acquisition_parameters.deltat)

        self.autofocus_mode = acquisition_parameters.autofocus_mode
        self.autofocus_interval_fovs = acquisition_parameters.autofocus_interval_fovs
        self.focus_lock_settings = acquisition_parameters.focus_lock_settings
        self.use_piezo = acquisition_parameters.use_piezo
        self.display_resolution_scaling = (
            acquisition_parameters.display_resolution_scaling
        )
        self._focus_lock_started_by_acquisition = False

        self.experiment_ID = acquisition_parameters.experiment_ID
        self.base_path = acquisition_parameters.base_path
        self.experiment_path = os.path.join(
            self.base_path or "", self.experiment_ID or ""
        )
        self.selected_configurations = acquisition_parameters.selected_configurations
        self.acquisition_order = getattr(acquisition_parameters, "acquisition_order", "channel_first")
        self._acquisition_parameters = acquisition_parameters  # Store for start callback

        # Pre-compute acquisition metadata that remains constant throughout the run.
        try:
            pixel_factor = self.objectiveStore.get_pixel_size_factor()
            sensor_pixel_um = self._camera_get_pixel_size_binned_um()
            if pixel_factor is not None and sensor_pixel_um is not None:
                self._pixel_size_um = float(pixel_factor) * float(sensor_pixel_um)
            else:
                self._pixel_size_um = None
        except Exception:
            self._pixel_size_um = None
        self._time_increment_s = self.dt if self.Nt > 1 and self.dt > 0 else None
        self._physical_size_z_um = self.deltaZ if self.NZ > 1 else None
        self.timestamp_acquisition_started = (
            acquisition_parameters.acquisition_start_time
        )

        self.time_point = 0
        self.af_fov_count = 0
        self.num_fovs = 0
        self.total_scans = 0
        self._last_time_point_z_pos = {}
        self.scan_region_fov_coords_mm = acquisition_parameters.scan_position_information.scan_region_fov_coords_mm.copy()
        self.scan_region_coords_mm = (
            acquisition_parameters.scan_position_information.scan_region_coords_mm
        )
        self.scan_region_names = (
            acquisition_parameters.scan_position_information.scan_region_names
        )

        # Build cumulative FOV offsets per region for flat FOV index conversion (5D zarr mode)
        self._region_fov_offsets: Dict[int, int] = {}
        offset = 0
        for region_idx, region_id in enumerate(self.scan_region_names):
            self._region_fov_offsets[region_idx] = offset
            offset += len(self.scan_region_fov_coords_mm.get(region_id, []))

        self.z_stacking_config = (
            acquisition_parameters.z_stacking_config
        )  # default 'from bottom'
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

        self.skip_saving = acquisition_parameters.skip_saving

        # Downsampled view parameters
        self._generate_downsampled_views = acquisition_parameters.generate_downsampled_views
        self._save_downsampled_well_images = acquisition_parameters.save_downsampled_well_images
        self._plate_num_rows = acquisition_parameters.plate_num_rows
        self._plate_num_cols = acquisition_parameters.plate_num_cols
        self._downsampled_well_resolutions_um = acquisition_parameters.downsampled_well_resolutions_um
        self._downsampled_plate_resolution_um = acquisition_parameters.downsampled_plate_resolution_um
        self._downsampled_z_projection = acquisition_parameters.downsampled_z_projection
        self._downsampled_interpolation_method = acquisition_parameters.downsampled_interpolation_method
        self._xy_mode = acquisition_parameters.xy_mode
        self._downsampled_view_manager: Optional[DownsampledViewManager] = None
        self._downsampled_output_dir: Optional[str] = None

        # Track FOV counts per well for multi-FOV wells
        self._well_fov_counts: Dict[str, int] = {}  # well_id -> total FOVs

        # Create acquisition-wide metadata for OME-TIFF file generation
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

        # Determine job classes based on file saving option
        job_classes: List[Type[Job]] = []
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

        # Add DownsampledViewJob if downsampled views are enabled
        if self._generate_downsampled_views:
            job_classes.append(DownsampledViewJob)

        # Create backpressure controller for throttling
        self._backpressure_controller: Optional[BackpressureController] = None
        if Acquisition.USE_MULTIPROCESSING and ACQUISITION_THROTTLING_ENABLED:
            self._backpressure_controller = BackpressureController(
                max_jobs=ACQUISITION_MAX_PENDING_JOBS,
                max_mb=ACQUISITION_MAX_PENDING_MB,
                timeout_s=ACQUISITION_THROTTLE_TIMEOUT_S,
                enabled=True,
            )
            self._log.info(
                f"Backpressure enabled: max_jobs={ACQUISITION_MAX_PENDING_JOBS}, "
                f"max_mb={ACQUISITION_MAX_PENDING_MB}"
            )

        # Build ZarrWriterInfo if using ZARR_V3 format
        zarr_writer_info: Optional[ZarrWriterInfo] = None
        if use_zarr_v3:
            is_hcs = self._is_well_based_acquisition()

            # Pre-compute FOV counts per region (needed for 6D shape calculation)
            region_fov_counts = {}
            for region_id, coords in self.scan_region_fov_coords_mm.items():
                region_fov_counts[str(region_id)] = len(coords)

            # Extract channel metadata for zarr output
            channel_names = [cfg.name for cfg in self.selected_configurations]
            channel_colors = [cfg.display_color for cfg in self.selected_configurations]

            # Get wavelengths from illumination config
            channel_wavelengths = []
            illumination_config = self.channelConfigurationManager._illumination_config
            for cfg in self.selected_configurations:
                wavelength = cfg.get_illumination_wavelength(illumination_config) if illumination_config else None
                channel_wavelengths.append(wavelength)

            zarr_writer_info = ZarrWriterInfo(
                base_path=self.experiment_path,
                t_size=self.Nt,
                c_size=len(self.selected_configurations),
                z_size=self.NZ,
                is_hcs=is_hcs,
                use_6d_fov=ZARR_USE_6D_FOV_DIMENSION,
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
            elif ZARR_USE_6D_FOV_DIMENSION:
                mode_str = "per-region 6D (non-standard)"
            else:
                mode_str = "per-FOV 5D (OME-NGFF compliant)"
            self._log.info(f"ZARR_V3 output: {mode_str}, base path: {self.experiment_path}")

        # For now, use 1 runner per job class.  There's no real reason/rationale behind this, though.  The runners
        # can all run any job type.  But 1 per is a reasonable arbitrary arrangement while we don't have a lot
        # of job types.  If we have a lot of custom jobs, this could cause problems via resource hogging.
        self._job_runners: List[Tuple[Type[Job], JobRunner]] = []
        self._log.info(
            f"Acquisition.USE_MULTIPROCESSING = {Acquisition.USE_MULTIPROCESSING}"
        )
        for job_class in job_classes:
            self._log.info(f"Creating job runner for {job_class.__name__} jobs")
            job_runner = (
                squid.backend.controllers.multipoint.job_processing.JobRunner(
                    self.acquisition_info,
                    cleanup_stale_ome_files=use_ome_tiff,
                    backpressure_jobs=self._backpressure_controller.pending_jobs_value if self._backpressure_controller else None,
                    backpressure_bytes=self._backpressure_controller.pending_bytes_value if self._backpressure_controller else None,
                    backpressure_event=self._backpressure_controller.capacity_event if self._backpressure_controller else None,
                    zarr_writer_info=zarr_writer_info,
                )
                if Acquisition.USE_MULTIPROCESSING
                else None
            )
            if job_runner:
                job_runner.daemon = True
                job_runner.start()
            self._job_runners.append((job_class, job_runner))
        self._abort_on_failed_job = abort_on_failed_jobs
        self._use_zarr_v3 = use_zarr_v3
        self._zarr_writer_info = zarr_writer_info

        # =========================================================================
        # Initialize domain objects (Phase 4 integration)
        # =========================================================================
        # Progress and coordinate tracking
        self._progress_tracker = ProgressTracker(
            event_bus=self._event_bus,
            experiment_id=self.experiment_ID or "",
            base_path=self.base_path or "",
        )
        self._coordinate_tracker = CoordinateTracker(use_piezo=self.use_piezo)

        # Autofocus executor
        self._autofocus_executor = AutofocusExecutor(
            autofocus_controller=auto_focus_controller,
            laser_af_controller=laser_auto_focus_controller,
            focus_lock_controller=focus_lock_controller,
            channel_config_manager=channel_configuration_mananger,
            objective_store=objective_store,
        )
        self._autofocus_executor.configure(
            autofocus_mode=self.autofocus_mode,
            nz=self.NZ,
            z_stacking_config=self.z_stacking_config,
            fovs_per_af=self.autofocus_interval_fovs,
        )
        self._autofocus_executor.set_apply_config_callback(self._select_config)

        # Z-stack executor
        self._zstack_config = ZStackConfig(
            num_z_levels=self.NZ,
            delta_z_um=self.deltaZ,
            stacking_direction=self.z_stacking_config,
            z_range=self.z_range,
            use_piezo=self.use_piezo,
        )
        self._zstack_executor = ZStackExecutor(
            stage_service=stage_service,
            piezo_service=piezo_service,
            config=self._zstack_config,
        )

        # Position controller
        self._position_controller = PositionController(
            stage_service=stage_service,
            stabilization_time_x_ms=SCAN_STABILIZATION_TIME_MS_X,
            stabilization_time_y_ms=SCAN_STABILIZATION_TIME_MS_Y,
            stabilization_time_z_ms=SCAN_STABILIZATION_TIME_MS_Z,
        )

    # =========================================================================
    # Helper methods
    # =========================================================================
    def _camera_get_pixel_size_binned_um(self) -> Optional[float]:
        """Get binned pixel size from camera service."""
        return self._camera_service.get_pixel_size_binned_um() if self._camera_service else None

    def _get_current_fov_dimensions(self) -> Tuple[float, float]:
        """Get current FOV dimensions from camera and objective."""
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        if pixel_size_factor is None:
            pixel_size_factor = 1.0

        camera = self._camera_service._camera if self._camera_service else None
        if camera is None:
            return 0.0, 0.0

        fov_width_mm = pixel_size_factor * (camera.get_fov_size_mm() or 0.0)
        fov_height_mm = (
            pixel_size_factor * camera.get_fov_height_mm()
            if hasattr(camera, "get_fov_height_mm") and camera.get_fov_height_mm() is not None
            else fov_width_mm
        )

        return fov_width_mm, fov_height_mm

    def _is_simulated_camera(self) -> bool:
        """Check if using simulated camera (for test purposes)."""
        camera = getattr(self._camera_service, "_camera", None)
        if camera is None:
            return False
        module_name = getattr(camera.__class__, "__module__", "")
        return "cameras.simulated" in module_name

    def _compute_flat_fov_idx(self, region_id: str, fov: int) -> int:
        """Compute flat FOV index across all regions.

        The flat index follows the same order as FOV labels built by the controller:
        iterate through regions in order, then FOVs within each region.

        Args:
            region_id: Region identifier (could be well ID like "A1")
            fov: FOV index within the region

        Returns:
            Flat FOV index across all wells/regions
        """
        flat_idx = 0
        for r_id in self.scan_region_names:
            if r_id == region_id:
                return flat_idx + fov
            fov_coords = self.scan_region_fov_coords_mm.get(r_id, [])
            flat_idx += len(fov_coords)
        # Region not found - return the FOV as fallback
        return fov

    def update_use_piezo(self, value: bool) -> None:
        """Update piezo usage flag."""
        self.use_piezo = value
        self._zstack_config.use_piezo = value
        self._zstack_executor._config.use_piezo = value
        self._log.info(f"MultiPointWorker: updated use_piezo to {value}")

    def request_abort(self) -> None:
        self._aborted = True
        self._abort_requested.set()

    def request_pause(self) -> None:
        """Request a pause at the next safe boundary."""
        self._pause_requested.set()

    def resume(self) -> None:
        """Resume after a pause request."""
        self._pause_requested.clear()

    def _wait_if_paused(self) -> None:
        """Block while paused, returning when resumed or abort requested."""
        if not self._pause_requested.is_set():
            return

        self._log.info("Pausing acquisition at FOV boundary")
        while self._pause_requested.is_set() and not self._abort_requested.is_set():
            self._sleep(0.1)

    # =========================================================================
    # FOV Task System Methods
    # =========================================================================
    def _build_fov_task_list(self) -> FovTaskList:
        """Build FovTaskList from scan region coordinates.

        Creates FovTask for each FOV with stable IDs and computes
        a plan_hash for checkpoint validation.

        Returns:
            FovTaskList with tasks created from scan_region_fov_coords_mm
        """
        tasks: List[FovTask] = []
        for region_id, coordinates in self.scan_region_fov_coords_mm.items():
            for index, coord in enumerate(coordinates):
                task = FovTask.from_coordinate(region_id, index, coord)
                tasks.append(task)

        # Compute plan_hash from task positions for checkpoint validation
        hash_data = [
            (t.fov_id, t.region_id, t.x_mm, t.y_mm, t.z_mm)
            for t in tasks
        ]
        plan_hash = hashlib.sha256(str(hash_data).encode()).hexdigest()[:16]

        task_list = FovTaskList(tasks=tasks, cursor=0, plan_hash=plan_hash)
        self._log.info(f"Built FOV task list with {len(tasks)} tasks, plan_hash={plan_hash}")
        return task_list

    def queue_fov_command(
        self,
        cmd: "JumpToFovCommand | SkipFovCommand | RequeueFovCommand | DeferFovCommand | ReorderFovsCommand",
    ) -> None:
        """Queue a command for processing at next FOV boundary.

        Commands are processed between FOV acquisitions to ensure
        atomic FOV execution.

        Args:
            cmd: The command to queue
        """
        self._fov_command_queue.put(cmd)
        self._log.debug(f"Queued FOV command: {type(cmd).__name__} for {cmd.fov_id}")

    def _process_pending_fov_commands(self) -> None:
        """Process commands from queue.

        Called between FOV acquisitions. All mutations are thread-safe
        via FovTaskList internal lock.
        """
        if self._fov_task_list is None:
            return

        while True:
            try:
                cmd = self._fov_command_queue.get_nowait()
            except Empty:
                break

            if cmd.round_index != self._current_round_index or cmd.time_point != self.time_point:
                self._log.debug(
                    "Ignoring FOV command for different context: "
                    f"{type(cmd).__name__} round={cmd.round_index} time_point={cmd.time_point}"
                )
                continue

            if isinstance(cmd, JumpToFovCommand):
                if self._fov_task_list.jump_to(cmd.fov_id):
                    self._log.info(f"Jumped to FOV {cmd.fov_id}")
                else:
                    self._log.warning(f"Failed to jump to FOV {cmd.fov_id}: not found")
            elif isinstance(cmd, SkipFovCommand):
                if self._fov_task_list.skip(cmd.fov_id):
                    self._log.info(f"Skipped FOV {cmd.fov_id}")
                else:
                    self._log.warning(f"Failed to skip FOV {cmd.fov_id}")
            elif isinstance(cmd, RequeueFovCommand):
                if self._fov_task_list.requeue(cmd.fov_id, cmd.before_current):
                    self._log.info(
                        f"Requeued FOV {cmd.fov_id} "
                        f"({'before' if cmd.before_current else 'after'} current)"
                    )
                else:
                    self._log.warning(f"Failed to requeue FOV {cmd.fov_id}")
            elif isinstance(cmd, DeferFovCommand):
                if self._fov_task_list.defer(cmd.fov_id):
                    self._log.info(f"Deferred FOV {cmd.fov_id}")
                else:
                    self._log.warning(f"Failed to defer FOV {cmd.fov_id}")
            elif isinstance(cmd, ReorderFovsCommand):
                if self._fov_task_list.reorder(list(cmd.fov_ids)):
                    self._log.info("Reordered pending FOVs")
                else:
                    self._log.warning("Failed to reorder pending FOVs")

            self._publish_fov_task_list_changed()

    def _publish_fov_started(self, task: FovTask) -> None:
        """Publish FovTaskStarted event."""
        if self._event_bus is None or self._fov_task_list is None:
            return

        event = FovTaskStarted(
            fov_id=task.fov_id,
            fov_index=task.fov_index,
            region_id=task.region_id,
            round_index=self._current_round_index,
            time_point=self.time_point,
            x_mm=task.x_mm,
            y_mm=task.y_mm,
            attempt=task.attempt,
            pending_count=self._fov_task_list.pending_count(),
            completed_count=self._fov_task_list.completed_count(),
        )
        self._event_bus.publish(event)

    def _publish_fov_completed(self, task: FovTask) -> None:
        """Publish FovTaskCompleted event."""
        if self._event_bus is None:
            return

        event = FovTaskCompleted(
            fov_id=task.fov_id,
            fov_index=task.fov_index,
            round_index=self._current_round_index,
            time_point=self.time_point,
            status=task.status,
            attempt=task.attempt,
            error_message=task.error_message,
        )
        self._event_bus.publish(event)

    def _publish_fov_task_list_changed(self) -> None:
        """Publish FovTaskListChanged event."""
        if self._event_bus is None or self._fov_task_list is None:
            return

        event = FovTaskListChanged(
            round_index=self._current_round_index,
            time_point=self.time_point,
            cursor=self._fov_task_list.cursor,
            pending_count=self._fov_task_list.pending_count(),
            completed_count=self._fov_task_list.completed_count(),
            skipped_count=self._fov_task_list.skipped_count(),
            deferred_count=self._fov_task_list.deferred_count(),
        )
        self._event_bus.publish(event)

    def get_fov_task_list(self) -> Optional[FovTaskList]:
        """Get the current FOV task list (for external inspection)."""
        return self._fov_task_list

    def set_current_round_index(self, round_index: int) -> None:
        """Set the current round index for event context."""
        self._current_round_index = round_index

    def set_start_fov_index(self, fov_index: int) -> None:
        """Set the FOV index to start from (for resume support).

        This causes the worker to skip to the specified FOV index when
        starting the acquisition. Used by orchestrator for checkpoint resume.

        Args:
            fov_index: The FOV index to start from (0-based)
        """
        self._start_fov_index = fov_index
        self._log.info(f"Set start FOV index to {fov_index} for resume")

    # =========================================================================
    # Checkpoint Methods
    # =========================================================================
    def _save_checkpoint(self) -> None:
        """Save current acquisition state to checkpoint file.

        Called periodically during acquisition based on checkpoint_interval.
        Uses atomic write to prevent corruption.
        """
        if not self._checkpoint_enabled or self._fov_task_list is None:
            return

        if not self.experiment_path:
            self._log.warning("Cannot save checkpoint: experiment_path not set")
            return

        try:
            checkpoint = MultiPointCheckpoint.from_state(
                experiment_id=self.experiment_ID or "",
                round_index=self._current_round_index,
                time_point=self.time_point,
                fov_task_list=self._fov_task_list,
            )
            checkpoint_path = get_checkpoint_path(self.experiment_path, self.time_point)
            checkpoint.save(checkpoint_path)
            self._log.debug(f"Saved checkpoint to {checkpoint_path}")

            # Clean up old checkpoints if we have too many
            self._cleanup_old_checkpoints()
        except Exception as e:
            self._log.warning(f"Failed to save checkpoint: {e}")

    def _cleanup_old_checkpoints(self) -> None:
        """Remove old checkpoint files, keeping only the most recent ones."""
        if not self.experiment_path:
            return

        try:
            from pathlib import Path
            checkpoint_dir = Path(self.experiment_path) / "checkpoints"
            if not checkpoint_dir.exists():
                return

            checkpoints = sorted(checkpoint_dir.glob("checkpoint_t*.json"))
            if len(checkpoints) > self._max_checkpoints:
                for old_checkpoint in checkpoints[:-self._max_checkpoints]:
                    old_checkpoint.unlink()
                    self._log.debug(f"Removed old checkpoint: {old_checkpoint}")
        except Exception as e:
            self._log.warning(f"Failed to cleanup old checkpoints: {e}")

    def resume_from_checkpoint(self, checkpoint_path: Optional[str] = None) -> bool:
        """Resume acquisition from a checkpoint file.

        Args:
            checkpoint_path: Path to checkpoint file. If None, finds latest checkpoint.

        Returns:
            True if successfully resumed, False otherwise.

        Raises:
            CheckpointPlanMismatch: If checkpoint plan_hash doesn't match current plan
        """
        from pathlib import Path

        if not self.experiment_path:
            self._log.error("Cannot resume: experiment_path not set")
            return False

        # Find checkpoint file
        if checkpoint_path is None:
            found_path = find_latest_checkpoint(self.experiment_path)
            if found_path is None:
                self._log.info("No checkpoint found to resume from")
                return False
            checkpoint_path = str(found_path)

        self._log.info(f"Attempting to resume from checkpoint: {checkpoint_path}")

        # Build current task list to get plan_hash for validation
        if self._fov_task_list is None:
            self._fov_task_list = self._build_fov_task_list()

        current_plan_hash = self._fov_task_list.plan_hash

        # Load checkpoint with plan validation
        checkpoint = MultiPointCheckpoint.load(
            Path(checkpoint_path),
            current_plan_hash=current_plan_hash,
        )

        # Restore state from checkpoint
        self._fov_task_list = checkpoint.restore_fov_task_list()
        self.time_point = checkpoint.time_point
        self._current_round_index = checkpoint.round_index
        self._resumed_from_checkpoint = True

        self._log.info(
            f"Resumed from checkpoint: time_point={self.time_point}, "
            f"cursor={self._fov_task_list.cursor}, "
            f"completed={self._fov_task_list.completed_count()}, "
            f"pending={self._fov_task_list.pending_count()}"
        )
        return True

    def set_checkpoint_enabled(self, enabled: bool) -> None:
        """Enable or disable checkpoint saving."""
        self._checkpoint_enabled = enabled
        self._log.info(f"Checkpoint saving {'enabled' if enabled else 'disabled'}")

    def set_checkpoint_interval(self, interval: int) -> None:
        """Set the checkpoint save interval (save every N FOVs)."""
        self._checkpoint_interval = max(1, interval)
        self._log.info(f"Checkpoint interval set to {self._checkpoint_interval}")

    def _prepare_focus_lock_for_acquisition(self) -> None:
        """Start and verify focus lock for focus-lock acquisition mode."""
        if self.autofocus_mode != AutofocusMode.FOCUS_LOCK:
            return

        controller = self._focus_lock_controller
        if controller is None:
            raise RuntimeError("Focus lock controller not available for focus-lock mode")

        was_active = self._autofocus_executor.is_focus_lock_active()
        if not was_active:
            controller.start()
            self._focus_lock_started_by_acquisition = True

        prepare_focus_lock = getattr(self._autofocus_executor, "prepare_focus_lock_for_acquisition", None)
        prepared = False
        if callable(prepare_focus_lock):
            prepared = bool(prepare_focus_lock(self.focus_lock_settings))
        else:
            controller_status = getattr(controller, "status", None)
            if controller_status != "locked":
                if hasattr(controller, "set_lock_reference"):
                    controller.set_lock_reference()
                elif hasattr(controller, "set_lock"):
                    controller.set_lock()
            timeout_s = float(getattr(self.focus_lock_settings, "lock_timeout_s", 5.0))
            prepared = self._autofocus_executor.wait_for_focus_lock(timeout_s=timeout_s)

        if not prepared:
            timeout_s = float(getattr(self.focus_lock_settings, "lock_timeout_s", 5.0))
            raise RuntimeError(
                f"Focus lock failed to acquire before acquisition start (timeout={timeout_s:.1f}s)"
            )

    def _teardown_focus_lock_for_acquisition(self) -> None:
        """Stop focus lock if this acquisition started it."""
        if not self._focus_lock_started_by_acquisition:
            return
        controller = self._focus_lock_controller
        if controller is None:
            return
        try:
            controller.stop()
        except Exception:
            self._log.exception("Failed to stop focus lock during acquisition teardown")
        finally:
            self._focus_lock_started_by_acquisition = False

    def run(self) -> None:
        this_image_callback_id: Optional[str] = None
        acquisition_error: Optional[Exception] = None
        try:
            self._prepare_focus_lock_for_acquisition()
            # Publish acquisition started event via ProgressTracker
            self._progress_tracker.start()

            start_time: int = time.perf_counter_ns()
            # Register callback before starting streaming to avoid missing initial frames
            this_image_callback_id = self._camera_service.add_frame_callback(
                self._image_callback
            )
            self._camera_service.start_streaming()
            sleep_time: float = min(self.dt / 20.0, 0.5)

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self._abort_requested.is_set():
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break

                if self._fluidics_service and self.use_fluidics:
                    # Deprecated: multipoint fluidics integration will be removed in favor of the orchestrator.
                    # Keep for backwards compatibility with legacy workflows.
                    fluidics_focus_paused = False
                    if self._autofocus_executor.is_focus_lock_active():
                        fluidics_focus_paused = self._autofocus_executor.pause_focus_lock()

                    try:
                        self._run_fluidics_phase("before_imaging", self.time_point)
                    finally:
                        # Resume focus lock before imaging
                        if fluidics_focus_paused:
                            self._autofocus_executor.resume_focus_lock()
                            # Wait for focus lock to re-establish after fluid exchange
                            if not self._autofocus_executor.wait_for_focus_lock(timeout_s=5.0):
                                self._log.warning("Focus lock re-acquisition failed after fluidics setup")

                with self._timing.get_timer("run_single_time_point"):
                    self.run_single_time_point()

                if self._fluidics_service and self.use_fluidics:
                    # Deprecated: multipoint fluidics integration will be removed in favor of the orchestrator.
                    # Keep for backwards compatibility with legacy workflows.
                    fluidics_focus_paused = False
                    if self._autofocus_executor.is_focus_lock_active():
                        fluidics_focus_paused = self._autofocus_executor.pause_focus_lock()

                    try:
                        self._run_fluidics_phase("after_imaging", self.time_point)
                    finally:
                        # Resume focus lock for next timepoint
                        if fluidics_focus_paused:
                            self._autofocus_executor.resume_focus_lock()

                self.time_point = self.time_point + 1
                if self.dt == 0:  # continous acquisition
                    pass
                else:  # timed acquisition
                    # check if it has reached Nt
                    if self.time_point == self.Nt:
                        break  # no waiting after taking the last time point

                    # wait until it's time to do the next acquisition
                    target_time = (
                        self.timestamp_acquisition_started + self.time_point * self.dt
                    )
                    while time.time() < target_time:
                        if self._abort_requested.is_set():
                            self._log.debug(
                                "In run wait loop, abort_acquisition_requested=True"
                            )
                            break
                        self._sleep(sleep_time)

            elapsed_time: int = time.perf_counter_ns() - start_time
            self._log.info("Time taken for acquisition: " + str(elapsed_time / 10**9))

            # Since we use callback based acquisition, make sure to wait for any final images to come in
            self._wait_for_outstanding_callback_images()
            self._log.info(
                f"Time taken for acquisition/processing: {(time.perf_counter_ns() - start_time) / 1e9} [s]"
            )
        except TimeoutError as te:
            self._log.error(
                "Operation timed out during acquisition, aborting acquisition!"
            )
            self._log.error(te)
            acquisition_error = te
            self.request_abort()
        except Exception as e:
            self._log.exception(e)
            acquisition_error = e
            raise
        finally:
            self._teardown_focus_lock_for_acquisition()
            # We do this above, but there are some paths that skip the proper end of the acquisition so make
            # sure to always wait for final images here before removing our callback.
            self._wait_for_outstanding_callback_images()
            self._log.debug(self._timing.get_report())

            # Stop camera streaming before removing callback to ensure clean state.
            # Without this, the camera continues streaming after acquisition, causing
            # issues on subsequent acquisitions (e.g., GUI freeze).
            self._camera_service.stop_streaming()

            if this_image_callback_id:
                self._camera_service.remove_frame_callback(this_image_callback_id)

            self._finish_jobs()
            if acquisition_error is None and self._fatal_error is not None:
                acquisition_error = self._fatal_error
            # Publish acquisition finished event via ProgressTracker
            self._progress_tracker.finish(
                success=(acquisition_error is None and not self._aborted),
                error=acquisition_error,
            )

    def _wait_for_outstanding_callback_images(self) -> None:
        # If there are outstanding frames, wait for them to come in.
        self._log.info("Waiting for any outstanding frames.")
        if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
            self._log.warning(
                "Timed out waiting for the last outstanding frames at end of acquisition!"
            )

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
            self._log.info(f"Processing job runner for {job_class.__name__}, runner={job_runner}")
            if job_runner is not None:
                pending_count = 0
                while job_runner.has_pending():
                    pending_count += 1
                    if pending_count == 1 or pending_count % 50 == 0:
                        self._log.info(f"{job_class.__name__}: has_pending() returned True (iteration {pending_count})")
                    if not timed_out():
                        self._sleep(0.1)
                    else:
                        self._log.error(
                            f"Timed out after {timeout_s} [s] waiting for jobs to finish.  Pending jobs for {job_class.__name__} abandoned!!!"
                        )
                        job_runner.kill()
                        break
                self._log.info(f"{job_class.__name__}: has_pending() returned False after {pending_count} iterations")

                # Drain results after waiting for this runner's jobs to complete
                self._log.info(f"Draining output queue for {job_class.__name__}...")
                self._summarize_runner_outputs()

                self._log.info(f"Trying to shut down job runner for {job_class.__name__}...")
                job_runner.shutdown(time_left())

        # Clean up backpressure controller
        if self._backpressure_controller is not None:
            self._log.info("Closing backpressure controller...")
            self._backpressure_controller.close()
            self._backpressure_controller = None

    def wait_till_operation_is_completed(self) -> None:
        self._peripheral_service.wait_till_operation_is_completed()

    def get_backpressure_stats(self) -> Optional[BackpressureStats]:
        """Get current backpressure statistics.

        Returns:
            BackpressureStats if backpressure is enabled, None otherwise.
        """
        if self._backpressure_controller is not None:
            return self._backpressure_controller.get_stats()
        return None

    def get_plate_view(self) -> Optional[np.ndarray]:
        """Get a copy of the current plate view array.

        Returns:
            Copy of the plate view array, or None if not available.
        """
        if self._downsampled_view_manager is not None:
            return self._downsampled_view_manager.plate_view.copy()
        return None

    def _is_well_based_acquisition(self) -> bool:
        """Check if this is a well-based acquisition (Select Wells or Load Coordinates)."""
        return self._xy_mode in ("Select Wells", "Load Coordinates")

    def _initialize_downsampled_view_manager(self, image: np.ndarray) -> None:
        """Initialize the plate view manager based on image dimensions and FOV grid.

        This must be called with the first captured image to get accurate dimensions.
        """
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

        # Emit plate view init event
        if self._event_bus:
            self._event_bus.publish(
                PlateViewInit(
                    num_rows=self._plate_num_rows,
                    num_cols=self._plate_num_cols,
                    well_slot_shape=(well_slot_height, well_slot_width),
                    fov_grid_shape=fov_grid_shape,
                    channel_names=channel_names,
                )
            )

    def _initialize_plate_view(self, current_path: str) -> None:
        """Set up plate view output directory. Manager initialization is deferred until first image."""
        if not self._generate_downsampled_views:
            self._log.debug(
                "Plate view disabled: generate_downsampled_views=False. "
                "Set DISPLAY_PLATE_VIEW=True or SAVE_DOWNSAMPLED_WELL_IMAGES=True in _def.py"
            )
            return
        if not self._is_well_based_acquisition():
            self._log.info(
                f"Plate view disabled: xy_mode='{self._xy_mode}' is not a well-based mode. "
                "Use 'Select Wells' or 'Load Coordinates' mode for plate view preview."
            )
            return

        # Create output directory for downsampled views
        self._downsampled_output_dir = os.path.join(current_path, "downsampled")
        os.makedirs(os.path.join(self._downsampled_output_dir, "wells"), exist_ok=True)

        # Count FOVs per well from scan coordinates
        self._well_fov_counts = {}
        for region_id, coords in self.scan_region_fov_coords_mm.items():
            self._well_fov_counts[region_id] = len(coords)

        self._log.info(
            f"Plate view directory initialized: {self._downsampled_output_dir}. "
            f"Manager will be initialized on first image."
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
                self._sleep(0.1)

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
                self._sleep(0.1)

            # Final drain of results
            self._summarize_runner_outputs(drain_all=True)

    # ---------------------------------------------------------------------
    # Legacy fluidics integration (removed)
    # ---------------------------------------------------------------------
    # NOTE: Legacy multipoint fluidics has been removed. Use the orchestrator
    # with FluidicsController for experiment workflows that need fluidics.

    def _run_fluidics_phase(self, phase: str, time_point: int) -> None:
        """Run legacy multipoint fluidics phase - DEPRECATED/REMOVED.

        This method is a no-op. Legacy multipoint fluidics has been removed
        in favor of the orchestrator with FluidicsController.
        """
        pass

    def run_single_time_point(self) -> None:
        try:
            start: float = time.time()
            self._peripheral_service.enable_joystick(False)

            self._log.debug(
                "multipoint acquisition - time point " + str(self.time_point + 1)
            )

            # for each time point, create a new folder
            if self.experiment_path:
                utils.ensure_directory_exists(str(self.experiment_path))
            current_path: str = os.path.join(
                self.experiment_path, f"{self.time_point:0{FILE_ID_PADDING}}"
            )
            utils.ensure_directory_exists(str(current_path))

            # Initialize plate view for this time point (if enabled)
            self._initialize_plate_view(current_path)

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
            self.coordinates_pd.to_csv(
                os.path.join(current_path, "coordinates.csv"), index=False, header=True
            )

            utils.create_done_file(current_path)
            self._log.debug(f"Single time point took: {time.time() - start} [s]")
        finally:
            self._peripheral_service.enable_joystick(True)

    def initialize_z_stack(self) -> None:
        """Initialize z-stack using ZStackExecutor."""
        # Use ZStackExecutor for z-stack initialization
        self.z_pos = self._zstack_executor.initialize()

        # Keep deltaZ in sync for other code that uses it
        if self.z_stacking_config == "FROM TOP":
            self.deltaZ = -abs(self.deltaZ)

    def initialize_coordinates_dataframe(self) -> None:
        """Initialize coordinates tracking via CoordinateTracker."""
        self._coordinate_tracker.initialize()
        # Also keep legacy attribute for backwards compatibility
        self.coordinates_pd = self._coordinate_tracker._coordinates_df

    def update_coordinates_dataframe(
        self,
        region_id: str,
        z_level: int,
        pos: squid.core.abc.Pos,
        fov: Optional[int] = None,
        fov_id: Optional[str] = None,
    ) -> None:
        """Record coordinate via CoordinateTracker."""
        piezo_um = self.z_piezo_um if self.use_piezo else None
        self._coordinate_tracker.record(
            region_id=region_id,
            fov=fov if fov is not None else 0,
            z_level=z_level,
            pos=pos,
            z_piezo_um=piezo_um,
            fov_id=fov_id,
        )
        # Update legacy attribute reference
        self.coordinates_pd = self._coordinate_tracker._coordinates_df

    def move_to_coordinate(
        self, coordinate_mm: Tuple[float, ...], region_id: str, fov: int
    ) -> None:
        """Move to a coordinate using PositionController."""
        x_mm: float = coordinate_mm[0]
        y_mm: float = coordinate_mm[1]

        # Apply alignment offset if available
        if self._alignment_widget is not None and getattr(self._alignment_widget, "has_offset", False):
            x_mm, y_mm = self._alignment_widget.apply_offset(x_mm, y_mm)
            self._log.info(f"moving to ({x_mm:.4f}, {y_mm:.4f}) [alignment offset applied]")
        else:
            self._log.info(f"moving to coordinate {coordinate_mm}")

        # Use PositionController for X/Y movement with stabilization
        self._position_controller.move_to_coordinate(x_mm=x_mm, y_mm=y_mm)

        # Handle Z positioning based on autofocus mode and timepoint
        if self.autofocus_mode != AutofocusMode.NONE and self.time_point > 0:
            if (region_id, fov) in self._last_time_point_z_pos:
                last_z_mm: float = self._last_time_point_z_pos[(region_id, fov)]
                self._position_controller.move_to_z(last_z_mm)
                self._log.info(f"Moved to last z position {last_z_mm} [mm]")
            else:
                self._log.warning(
                    f"No last z position found for region {region_id}, fov {fov}"
                )
        elif len(coordinate_mm) >= 3:
            z_mm: float = coordinate_mm[2]
            self._position_controller.move_to_z(z_mm)

        # Register FOV position via ProgressTracker (use actual moved-to position)
        fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
        self._progress_tracker.register_fov(
            x_mm=x_mm,  # This is the offset-adjusted position if offset was applied
            y_mm=y_mm,
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
        )

    def move_to_z_level(self, z_mm: float) -> None:
        """Move to a Z level using PositionController."""
        self._position_controller.move_to_z(z_mm)

    def _summarize_runner_outputs(self, drain_all: bool = True) -> SummarizeResult:
        """Process job results from output queues.

        Args:
            drain_all: If True, process ALL available results. If False, process at most one per queue.

        Returns:
            SummarizeResult with none_failed and had_results.
        """
        none_failed: bool = True
        had_results: bool = False
        had_failure: bool = False
        self._log.debug(f"_summarize_runner_outputs: checking {len(self._job_runners)} runners")
        for job_class, job_runner in self._job_runners:
            if job_runner is None:
                self._log.debug(f"  {job_class.__name__}: runner is None, skipping")
                continue
            out_queue = job_runner.output_queue()
            # Skip if queue was cleared during shutdown
            if out_queue is None:
                self._log.debug(f"  {job_class.__name__}: output queue is None, skipping")
                continue
            result_count = 0
            # Drain results from the queue
            while True:
                try:
                    job_result: JobResult = out_queue.get_nowait()
                    result_count += 1
                    had_results = True
                    result_ok = self._summarize_job_result(job_result)
                    none_failed = none_failed and result_ok
                    if not result_ok:
                        had_failure = True

                    # Handle DownsampledViewResult specially
                    if job_result.result is not None and isinstance(
                        job_result.result, DownsampledViewResult
                    ):
                        self._log.info(f"Got DownsampledViewResult for well {job_result.result.well_id}")
                        self._process_downsampled_view_result(job_result.result)
                    # Handle ZarrWriteResult - notify viewer that frame is written
                    elif job_result.result is not None and isinstance(
                        job_result.result, ZarrWriteResult
                    ):
                        r = job_result.result
                        # For 5D mode, convert local FOV to flat index across regions
                        if self._use_zarr_v3 and ZARR_USE_6D_FOV_DIMENSION:
                            fov_idx = r.fov
                        else:
                            fov_idx = self._region_fov_offsets.get(r.region_idx, 0) + r.fov
                        self._progress_tracker.notify_zarr_frame(
                            t=r.time_point,
                            fov_idx=fov_idx,
                            z=r.z_index,
                            channel=r.channel_name,
                            region_idx=r.region_idx,
                        )
                    elif job_result.result is not None:
                        self._log.debug(f"Got job result of type {type(job_result.result).__name__}")

                    if not drain_all:
                        break  # Only process one result per queue if not draining
                except queue.Empty:
                    if result_count > 0:
                        self._log.debug(f"  {job_class.__name__}: drained {result_count} results from output queue")
                    break
                except (ValueError, OSError):
                    # Queue was closed during shutdown
                    self._log.debug(f"  {job_class.__name__}: queue closed, stopping drain")
                    break

            if had_failure and self._abort_on_failed_job:
                self._log.error(
                    "Some jobs failed, aborting acquisition because abort_on_failed_job=True"
                )
                self.request_abort()
                break

        return SummarizeResult(none_failed=none_failed, had_results=had_results)

    def _process_downsampled_view_result(self, result: DownsampledViewResult) -> None:
        """Process a DownsampledViewResult and emit PlateViewUpdate events.

        Updates the DownsampledViewManager with well images and emits
        PlateViewUpdate events for each channel so the UI can refresh.
        """
        self._log.info(
            f"_process_downsampled_view_result: well={result.well_id}, "
            f"row={result.well_row}, col={result.well_col}, "
            f"channels={list(result.well_images.keys())}"
        )
        if self._downsampled_view_manager is None:
            self._log.warning("_process_downsampled_view_result: _downsampled_view_manager is None!")
            return
        if not self._event_bus:
            self._log.warning("_process_downsampled_view_result: _event_bus is None!")
            return

        self._log.info(
            f"DownsampledViewManager: plate grid {self._downsampled_view_manager.num_rows}x"
            f"{self._downsampled_view_manager.num_cols}, "
            f"slot shape {self._downsampled_view_manager.well_slot_shape}"
        )

        # Update the view manager with all channel images for this well
        self._downsampled_view_manager.update_well(
            row=result.well_row,
            col=result.well_col,
            well_images=result.well_images,
        )

        # Emit PlateViewUpdate for each channel
        for channel_idx in result.well_images.keys():
            # Get channel name
            channel_name = (
                result.channel_names[channel_idx]
                if channel_idx < len(result.channel_names)
                else f"Channel {channel_idx}"
            )

            # Get the updated plate view for this channel
            plate_image = self._downsampled_view_manager.get_channel_view(channel_idx)

            # Emit PlateViewUpdate event
            self._event_bus.publish(
                PlateViewUpdate(
                    channel_idx=channel_idx,
                    channel_name=channel_name,
                    plate_image=plate_image,
                )
            )

        self._log.debug(
            f"PlateViewUpdate emitted for well {result.well_id} "
            f"({len(result.well_images)} channels)"
        )

    def get_plate_view(self) -> Optional[np.ndarray]:
        """Get a copy of the current plate view array."""
        if self._downsampled_view_manager is None:
            return None
        return self._downsampled_view_manager.get_plate_view()

    def save_plate_view(self, path: str) -> None:
        """Save the plate view to disk."""
        if self._downsampled_view_manager is not None:
            self._downsampled_view_manager.save_plate_view(path)

    def _summarize_job_result(self, job_result: JobResult) -> bool:
        """
        Prints a summary, then returns True if the result was successful or False otherwise.
        """
        if job_result.exception is not None:
            self._log.error(
                f"Error while running job {job_result.job_id}: {job_result.exception}"
            )
            return False
        else:
            self._log.info(f"Got result for job {job_result.job_id}, it completed!")
            return True

    def run_coordinate_acquisition(self, current_path: str) -> None:
        """Execute FOV tasks with command processing at boundaries.

        Key design points:
        - FovTaskList owns cursor movement (single owner)
        - Commands processed between FOVs (atomic FOV execution)
        - Thread-safe via FovTaskList internal lock
        """
        # Build task list if not already built (first time point)
        if self._fov_task_list is None:
            self._fov_task_list = self._build_fov_task_list()
            # Apply start FOV index for resume support
            if self._start_fov_index > 0:
                if self._fov_task_list.jump_to_index(self._start_fov_index):
                    self._log.info(f"Jumped to FOV index {self._start_fov_index} for resume")
                else:
                    self._log.warning(
                        f"Could not jump to FOV index {self._start_fov_index} "
                        f"(task list has {len(self._fov_task_list)} tasks)"
                    )
                self._start_fov_index = 0  # Reset after applying
        elif self._resumed_from_checkpoint:
            self._log.info("Using FOV task list from checkpoint for resume")
        else:
            # Reset tasks for a new time point, keeping SKIPPED tasks skipped
            reset_count = self._fov_task_list.reset_for_timepoint()
            if reset_count > 0:
                self._log.info(f"Reset {reset_count} tasks for new time point")

        # Compute totals for progress tracking (backward compatibility)
        n_regions: int = len(self.scan_region_coords_mm)
        self._total_regions = n_regions
        total_fovs = len(self._fov_task_list)
        self.num_fovs = total_fovs
        self.total_scans = total_fovs * self.NZ * len(self.selected_configurations)

        # Track current region for progress events
        current_region_id: Optional[str] = None
        region_index = 0

        try:
            while True:
                # Process any pending commands (jump, skip, requeue, defer)
                self._process_pending_fov_commands()

                # Get next task - FovTaskList owns cursor advancement
                task = self._fov_task_list.advance_and_get()
                if task is None:
                    restored = self._fov_task_list.restore_deferred()
                    if restored > 0:
                        self._fov_task_list.reset_cursor()
                        self._log.info(f"Restored {restored} deferred tasks for another pass")
                        continue
                    break  # All tasks processed

                # Update region tracking for progress events
                if task.region_id != current_region_id:
                    current_region_id = task.region_id
                    # Find region index
                    for idx, rid in enumerate(self.scan_region_fov_coords_mm.keys()):
                        if rid == current_region_id:
                            region_index = idx
                            break
                    self._current_region = region_index + 1

                # Just so the job result queues don't get too big, check and print a summary
                with self._timing.get_timer("job result summaries"):
                    self._summarize_runner_outputs()
                    if self._abort_requested.is_set():
                        return

                # Check for pause/abort
                self._wait_if_paused()
                if self._abort_requested.is_set():
                    return

                # Mark task as executing
                self._fov_task_list.mark_executing_task(task)

                # Publish FOV started event
                self._publish_fov_started(task)

                # Execute the FOV
                has_z = task.metadata.get("has_z", True)
                if has_z:
                    coordinate_mm = (task.x_mm, task.y_mm, task.z_mm)
                else:
                    coordinate_mm = (task.x_mm, task.y_mm)
                success = True
                error_msg: Optional[str] = None

                try:
                    with self._timing.get_timer("move_to_coordinate"):
                        self.move_to_coordinate(coordinate_mm, task.region_id, task.fov_index)
                except Exception as e:
                    success = False
                    error_msg = str(e)
                    self._fatal_error = e
                    self._log.error(f"Error executing FOV {task.fov_id}: {e}")
                    self.request_abort()
                else:
                    try:
                        with self._timing.get_timer("acquire_at_position"):
                            self.acquire_at_position(
                                task.region_id,
                                current_path,
                                task.fov_index,
                                fov_id=task.fov_id,
                                attempt=task.attempt,
                            )
                    except Exception as e:
                        success = False
                        error_msg = str(e)
                        self._log.error(f"Error executing FOV {task.fov_id}: {e}")

                # Mark task complete - this advances cursor
                self._fov_task_list.mark_complete_task(task, success, error_msg)

                # Publish FOV completed event
                self._publish_fov_completed(task)

                # Save checkpoint periodically
                self._fov_since_checkpoint += 1
                if self._fov_since_checkpoint >= self._checkpoint_interval:
                    self._save_checkpoint()
                    self._fov_since_checkpoint = 0

                if self._abort_requested.is_set():
                    self.handle_acquisition_abort(current_path)
                    return
        finally:
            # Any deferred tasks should have been restored and handled before exit.
            self._resumed_from_checkpoint = False

    def acquire_at_position(
        self,
        region_id: str,
        current_path: str,
        fov: int,
        fov_id: Optional[str] = None,
        attempt: int = 1,
    ) -> None:
        if not self.perform_autofocus(region_id, fov):
            current_z = self._stage_service.get_position().z_mm
            self._log.error(
                f"Autofocus failed in acquire_at_position.  Continuing to acquire anyway using the current z position (z={current_z} [mm])"
            )
            if self._event_bus is not None:
                self._event_bus.publish(
                    AddWarningCommand(
                        category="FOCUS",
                        severity="MEDIUM",
                        message=(
                            f"Autofocus failed at region {region_id}, "
                            f"fov {fov} (z={current_z:.4f} mm)"
                        ),
                        round_index=self._current_round_index,
                        round_name=f"Round {self._current_round_index + 1}",
                        time_point=self.time_point,
                        operation_type="imaging",
                        fov_id=fov_id,
                        fov_index=fov,
                        context={"region_id": region_id},
                    )
                )

        if self.use_piezo:
            self.z_piezo_um: float = self._piezo_service.get_position() if self._piezo_service else 0.0
            # Sync piezo position to ZStackExecutor for coordinated z-stack movement
            self._zstack_executor.z_piezo_um = self.z_piezo_um

        # Check if focus lock is active (handles focus continuously)
        focus_lock_active = self._autofocus_executor.is_focus_lock_active()

        # Verify focus lock before capture (replaces per-FOV laser AF when focus lock is active)
        if focus_lock_active:
            focus_lock_settings = getattr(self, "focus_lock_settings", None)
            timeout_s = float(getattr(focus_lock_settings, "lock_timeout_s", 5.0))
            focus_lock_controller = getattr(self, "_focus_lock_controller", None)
            focus_lock_status = getattr(focus_lock_controller, "status", "unknown")
            self._log.info(
                "Verifying focus lock before FOV capture: region=%s fov=%s status=%s piezo=%s",
                region_id,
                fov,
                focus_lock_status,
                f"{self.z_piezo_um:.2f} um" if self.use_piezo else "stage-z",
            )
            verify_focus_lock = getattr(self._autofocus_executor, "verify_focus_lock_before_capture", None)
            if callable(verify_focus_lock):
                focus_lock_ok, reason = verify_focus_lock(timeout_s=timeout_s)
            else:
                focus_lock_ok = self._autofocus_executor.wait_for_focus_lock(timeout_s=timeout_s)
                reason = None if focus_lock_ok else f"timeout={timeout_s:.1f}s"
            if not focus_lock_ok:
                message = (
                    "Focus lock verification failed before FOV capture "
                    f"({reason or f'timeout={timeout_s:.1f}s'})"
                )
                if self.autofocus_mode == AutofocusMode.FOCUS_LOCK:
                    raise RuntimeError(message)
                self._log.warning("%s, continuing anyway", message)

        # Pause focus lock for ALL captures (prevents piezo jitter during exposure)
        focus_lock_paused = False
        if focus_lock_active:
            focus_lock_paused = self._autofocus_executor.pause_focus_lock()
            self._log.info(
                "Focus lock capture state: region=%s fov=%s paused=%s status=%s",
                region_id,
                fov,
                focus_lock_paused,
                getattr(getattr(self, "_focus_lock_controller", None), "status", "unknown"),
            )

        # Save piezo position before z-stack offset so we can restore it after.
        # This is critical for focus lock: the lock's reference is the pre-offset
        # position, and return_to_start only undoes the z-stack steps, not the
        # prepare_z_stack offset (FROM CENTER mode).
        pre_zstack_piezo_um = None
        if self.NZ > 1:
            if self.use_piezo and self._piezo_service is not None:
                pre_zstack_piezo_um = self._piezo_service.get_position()
            self.prepare_z_stack()
            if self.use_piezo and self._piezo_service is not None:
                self.z_piezo_um = self._piezo_service.get_position()
                self._zstack_executor.z_piezo_um = self.z_piezo_um

        try:
            if self.acquisition_order == "z_first":
                self._acquire_z_first(region_id, current_path, fov, fov_id, attempt)
            else:
                self._acquire_channel_first(region_id, current_path, fov, fov_id, attempt)
            # update FOV counter after completing all z-levels for this FOV
            self.af_fov_count += 1
            self._progress_tracker.af_fov_count = self.af_fov_count
        finally:
            if self.NZ > 1:
                self.move_z_back_after_stack()
                # Restore piezo to pre-z-stack position (undo FROM CENTER offset)
                if pre_zstack_piezo_um is not None and self._piezo_service is not None:
                    self._piezo_service.move_to(pre_zstack_piezo_um)
                    self.z_piezo_um = pre_zstack_piezo_um
            if focus_lock_paused:
                self._autofocus_executor.resume_focus_lock()
                self._log.info(
                    "Focus lock resumed after FOV capture: region=%s fov=%s status=%s",
                    region_id,
                    fov,
                    getattr(getattr(self, "_focus_lock_controller", None), "status", "unknown"),
                )

    def _acquire_channel_first(
        self,
        region_id: str,
        current_path: str,
        fov: int,
        fov_id: Optional[str],
        attempt: int,
    ) -> None:
        """Acquire all channels at each z-level before moving to the next z-level."""
        for z_level in range(self.NZ):
            file_ID = self._make_file_id(region_id, fov, z_level, fov_id, attempt)

            acquire_pos: squid.core.abc.Pos = self._stage_service.get_position()
            self._log.info(
                f"Acquiring image: ID={file_ID}, "
                f"Metadata={{x: {acquire_pos.x_mm}, y: {acquire_pos.y_mm}, z: {acquire_pos.z_mm}}}"
            )

            if (
                z_level == 0
                and self.autofocus_mode != AutofocusMode.NONE
                and self.Nt > 1
            ):
                self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

            self._save_laser_af_characterization(current_path, file_ID)

            for config_idx, config in enumerate(self.selected_configurations):
                self.handle_z_offset(config, True)

                with self._timing.get_timer("acquire_camera_image"):
                    if self._is_rgb_config(config):
                        self.acquire_rgb_image(
                            config, file_ID, current_path, z_level, region_id, fov,
                            fov_id=fov_id,
                        )
                    else:
                        self.acquire_camera_image(
                            config, file_ID, current_path, z_level,
                            region_id=region_id, fov=fov,
                            config_idx=config_idx, fov_id=fov_id,
                        )

                self.handle_z_offset(config, False)

                current_image = (
                    fov * self.NZ * len(self.selected_configurations)
                    + z_level * len(self.selected_configurations)
                    + config_idx
                    + 1
                )
                self._progress_tracker.update(
                    current_fov=current_image,
                    total_fovs=self.total_scans,
                    current_region=getattr(self, "_current_region", 1),
                    total_regions=getattr(self, "_total_regions", 1),
                    current_timepoint=self.time_point + 1,
                    total_timepoints=self.Nt,
                    current_channel=config.name,
                )

            self.update_coordinates_dataframe(
                region_id, z_level, acquire_pos, fov, fov_id=fov_id
            )

            if self._abort_requested.is_set():
                self.handle_acquisition_abort(current_path)

            if z_level < self.NZ - 1:
                self.move_z_for_stack()

    def _acquire_z_first(
        self,
        region_id: str,
        current_path: str,
        fov: int,
        fov_id: Optional[str],
        attempt: int,
    ) -> None:
        """Acquire all z-levels for each channel before moving to the next channel."""
        num_configs = len(self.selected_configurations)

        for config_idx, config in enumerate(self.selected_configurations):
            # Prepare z-stack position at the start of each channel
            if self.NZ > 1 and config_idx > 0:
                # Return to starting z for this channel's z-stack
                self.move_z_back_after_stack()
                self.prepare_z_stack()

            for z_level in range(self.NZ):
                file_ID = self._make_file_id(region_id, fov, z_level, fov_id, attempt)

                acquire_pos: squid.core.abc.Pos = self._stage_service.get_position()
                self._log.info(
                    f"Acquiring image: ID={file_ID}, "
                    f"Metadata={{x: {acquire_pos.x_mm}, y: {acquire_pos.y_mm}, z: {acquire_pos.z_mm}}}"
                )

                if (
                    z_level == 0
                    and config_idx == 0
                    and self.autofocus_mode != AutofocusMode.NONE
                    and self.Nt > 1
                ):
                    self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

                self._save_laser_af_characterization(current_path, file_ID)
                self.handle_z_offset(config, True)

                with self._timing.get_timer("acquire_camera_image"):
                    if self._is_rgb_config(config):
                        self.acquire_rgb_image(
                            config, file_ID, current_path, z_level, region_id, fov,
                            fov_id=fov_id,
                        )
                    else:
                        self.acquire_camera_image(
                            config, file_ID, current_path, z_level,
                            region_id=region_id, fov=fov,
                            config_idx=config_idx, fov_id=fov_id,
                        )

                self.handle_z_offset(config, False)

                current_image = (
                    fov * self.NZ * num_configs
                    + config_idx * self.NZ
                    + z_level
                    + 1
                )
                self._progress_tracker.update(
                    current_fov=current_image,
                    total_fovs=self.total_scans,
                    current_region=getattr(self, "_current_region", 1),
                    total_regions=getattr(self, "_total_regions", 1),
                    current_timepoint=self.time_point + 1,
                    total_timepoints=self.Nt,
                    current_channel=config.name,
                )

                self.update_coordinates_dataframe(
                    region_id, z_level, acquire_pos, fov, fov_id=fov_id
                )

                if self._abort_requested.is_set():
                    self.handle_acquisition_abort(current_path)

                if z_level < self.NZ - 1:
                    self.move_z_for_stack()

    def _make_file_id(
        self,
        region_id: str,
        fov: int,
        z_level: int,
        fov_id: Optional[str],
        attempt: int,
    ) -> str:
        """Generate file ID string for an acquisition frame."""
        if fov_id is not None:
            if attempt == 1:
                return f"{fov_id}_{z_level:0{FILE_ID_PADDING}}"
            else:
                return f"{fov_id}_attempt{attempt:02d}_{z_level:0{FILE_ID_PADDING}}"
        return f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"

    def _save_laser_af_characterization(self, current_path: str, file_ID: str) -> None:
        """Save laser AF characterization image if in characterization mode."""
        if (
            self.laser_auto_focus_controller
            and self.laser_auto_focus_controller.characterization_mode
        ):
            image = self.laser_auto_focus_controller.get_image()
            saving_path = os.path.join(current_path, file_ID + "_laser af camera" + ".bmp")
            iio.imwrite(saving_path, image)

    def _select_config(self, config: AcquisitionChannel) -> None:
        self._apply_channel_mode(config)
        self.wait_till_operation_is_completed()

    def _is_rgb_config(self, config: AcquisitionChannel) -> bool:
        return bool(getattr(config, "is_rgb", False))

    def _apply_channel_mode(self, config: AcquisitionChannel) -> None:
        exposure = getattr(config, "exposure_time", None)
        if exposure is not None:
            self._camera_service.set_exposure_time(exposure)
        gain = getattr(config, "analog_gain", None)
        if gain is not None:
            try:
                self._camera_service.set_analog_gain(gain)
            except Exception:
                pass

        if self._illumination_service is not None:
            source = getattr(config, "illumination_source", None)
            intensity = getattr(config, "illumination_intensity", None)
            if source is not None and intensity is not None:
                try:
                    self._illumination_service.set_channel_power(int(source), float(intensity))
                except Exception:
                    pass

        if (
            self._filter_wheel_service
            and self._filter_wheel_service.is_available()
            and self._enable_channel_auto_filter_switching
        ):
            position = getattr(config, "emission_filter_position", None)
            if position is not None:
                try:
                    delay = 0
                    if self._trigger_mode == TriggerMode.HARDWARE:
                        delay = -int(self._camera_service.get_strobe_time())
                    self._filter_wheel_service.set_delay_offset_ms(delay)
                except Exception:
                    pass
                try:
                    self._filter_wheel_service.set_filter_wheel_position({1: int(position)})
                except Exception:
                    pass

    def _turn_on_illumination(self, config: AcquisitionChannel) -> None:
        if self._illumination_service is None:
            return
        source = getattr(config, "illumination_source", None)
        if source is None:
            return
        try:
            self._illumination_service.turn_on_channel(int(source))
        except Exception:
            pass

    def _turn_off_illumination(self, config: AcquisitionChannel) -> None:
        if self._illumination_service is None:
            return
        source = getattr(config, "illumination_source", None)
        if source is None:
            return
        try:
            self._illumination_service.turn_off_channel(int(source))
        except Exception:
            pass

    def perform_autofocus(self, region_id: str, fov: int) -> bool:
        """Perform autofocus using the AutofocusExecutor.

        Delegates to AutofocusExecutor for the core logic while keeping
        worker-specific error handling (saving laser AF camera image on failure).
        """
        # Sync the FOV count to the executor
        self._autofocus_executor.af_fov_count = self.af_fov_count

        # Use shorter timeout for simulated camera
        timeout_s = 2.0 if self._is_simulated_camera() else None

        # Try the standard autofocus path via executor
        try:
            result = self._autofocus_executor.perform_autofocus(
                region_id=region_id,
                fov=fov,
                timeout_s=timeout_s,
            )
            return result
        except Exception as e:
            # Save laser AF camera image on failure (worker-specific behavior)
            if (
                self.autofocus_mode == AutofocusMode.LASER_REFLECTION
                and self.laser_auto_focus_controller is not None
            ):
                file_ID: str = f"{region_id}_focus_camera.bmp"
                saving_path: str = os.path.join(
                    self.base_path, self.experiment_ID, str(self.time_point), file_ID
                )
                try:
                    frame = self.laser_auto_focus_controller.get_last_frame()
                    if frame is None:
                        raise ValueError("No laser AF frame available to save")
                    iio.imwrite(saving_path, frame)
                except Exception:
                    pass  # Don't fail if we can't save the debug image
                self._log.error(
                    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! laser AF failed !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
                    exc_info=e,
                )
            return False

    def prepare_z_stack(self) -> None:
        """Prepare for z-stack by moving to start position (FROM CENTER mode)."""
        # For FROM CENTER mode, move to bottom of z-stack.
        if self.z_stacking_config != "FROM CENTER":
            return

        z_delta_um = -float(self.deltaZ) * round((self.NZ - 1) / 2.0)
        if z_delta_um == 0:
            return

        if self.use_piezo and self._piezo_service is not None:
            start_piezo_um = self._piezo_service.get_position() + z_delta_um
            self._zstack_executor.reset_piezo(start_piezo_um)
            self.z_piezo_um = self._zstack_executor.z_piezo_um
            self._log.info(
                "Prepared z-stack start on piezo: delta=%+.2f um start=%+.2f um",
                z_delta_um,
                self.z_piezo_um,
            )
            return

        z_delta_mm = z_delta_um / 1000.0
        self._position_controller.move_z_relative(z_delta_mm)
        self._log.info("Prepared z-stack start on stage: delta=%+.4f mm", z_delta_mm)

    def handle_z_offset(self, config: AcquisitionChannel, not_offset: bool) -> None:
        if (
            config.z_offset is not None
        ):  # perform z offset for config, assume z_offset is in um
            if config.z_offset != 0.0:
                direction: int = 1 if not_offset else -1
                self._log.info("Moving Z offset" + str(config.z_offset * direction))
                z_delta = config.z_offset / 1000 * direction
                self._position_controller.move_z_relative(z_delta)

    def _image_callback(self, camera_frame: CameraFrame) -> None:
        """
        Handle incoming camera frame.

        Must not throw - exceptions would destabilize the camera callback thread.
        """
        if self._ready_for_next_trigger.is_set():
            self._log.warning(
                "Got an image in the image callback, but we didn't send a trigger. Ignoring the image."
            )
            return

        self._image_callback_idle.clear()
        try:
            self._process_camera_frame(camera_frame)
        except Exception as exc:
            self._handle_callback_error(exc, stack_trace="")
            self._log.exception("Image callback failed, aborting acquisition")
            self.request_abort()
        finally:
            self._image_callback_idle.set()

    def _process_camera_frame(self, camera_frame: CameraFrame) -> None:
        """
        Process a camera frame - extracted from _image_callback for error containment.
        """
        with self._timing.get_timer("_image_callback"):
            info: Optional[CaptureInfo] = self._current_capture_info.get_and_clear()

            self._ready_for_next_trigger.set()
            if not info:
                raise RuntimeError("No current capture info! Something is wrong.")

            image: np.ndarray = camera_frame.frame
            if not camera_frame or image is None:
                raise RuntimeError("Image in frame callback is None.")

            with self._timing.get_timer("job creation and dispatch"):
                # Check backpressure before dispatching jobs
                if self._backpressure_controller is not None:
                    if self._backpressure_controller.should_throttle():
                        self._backpressure_controller.wait_for_capacity()

                for job_class, job_runner in self._job_runners:
                    # DownsampledViewJob requires additional parameters
                    if job_class == DownsampledViewJob:
                        job = self._create_downsampled_view_job(info, image)
                        if job is None:
                            continue  # Skip if not a well-based acquisition
                    else:
                        job = job_class(
                            capture_info=info, capture_image=JobImage(image_array=image)
                        )
                    if job_runner is not None:
                        if not job_runner.dispatch(job):
                            raise RuntimeError(
                                "Failed to dispatch multiprocessing job!"
                            )
                    else:
                        # Run synchronously - inject info for jobs that need it
                        # (mirrors JobRunner.dispatch() which injects before pickling)
                        if isinstance(job, SaveOMETiffJob):
                            job.acquisition_info = self.acquisition_info
                        elif isinstance(job, SaveZarrJob) and self._zarr_writer_info is not None:
                            job.zarr_writer_info = self._zarr_writer_info
                        result = job.run()
                        if result is not None and isinstance(result, DownsampledViewResult):
                            self._log.info(f"Synchronous job returned DownsampledViewResult for well {result.well_id}")
                            self._process_downsampled_view_result(result)
                        elif result is not None and isinstance(result, ZarrWriteResult):
                            # For 5D mode, convert local FOV to flat index across regions
                            if self._use_zarr_v3 and ZARR_USE_6D_FOV_DIMENSION:
                                fov_idx = result.fov
                            else:
                                fov_idx = self._region_fov_offsets.get(result.region_idx, 0) + result.fov
                            self._progress_tracker.notify_zarr_frame(
                                t=result.time_point,
                                fov_idx=fov_idx,
                                z=result.z_index,
                                channel=result.channel_name,
                                region_idx=result.region_idx,
                            )

            # Register image with NDViewer for push-mode display
            # Only register when using individual image saving - OME-TIFF uses different paths
            if (
                self._progress_tracker is not None
                and FILE_SAVING_OPTION == FileSavingOption.INDIVIDUAL_IMAGES
            ):
                filepath = utils_acquisition.get_image_filepath(
                    save_directory=info.save_directory,
                    file_id=info.file_id,
                    config_name=info.configuration.name,
                    dtype=image.dtype,
                )
                flat_fov_idx = self._compute_flat_fov_idx(str(info.region_id), info.fov)
                self._progress_tracker.register_ndviewer_image(
                    t=info.time_point or 0,
                    fov_idx=flat_fov_idx,
                    z=info.z_index,
                    channel=info.configuration.name,
                    filepath=filepath,
                )

            height: int
            width: int
            height, width = image.shape[:2]
            with self._timing.get_timer("acquisition frame fanout"):
                if self._stream_handler is not None:
                    self._stream_handler.on_new_image(  # type: ignore[attr-defined]
                        image,
                        frame_id=camera_frame.frame_id,
                        timestamp=camera_frame.timestamp,
                        is_color=camera_frame.is_color(),
                        respect_accept_new_frame=False,
                        capture_info=info,
                    )

            if self._event_bus is not None:
                from squid.core.events import AcquisitionCoordinates

                self._event_bus.publish(
                    AcquisitionCoordinates(
                        x_mm=info.position.x_mm,
                        y_mm=info.position.y_mm,
                        z_mm=info.position.z_mm,
                        region_id=info.region_id,
                    )
                )

    def _handle_callback_error(self, error: Exception, stack_trace: str) -> None:
        """
        Handle errors from image callback - store for debugging.
        """
        self._last_error = error
        self._last_stack_trace = stack_trace

    def _create_downsampled_view_job(
        self, info: CaptureInfo, image: np.ndarray
    ) -> Optional[DownsampledViewJob]:
        """Create a DownsampledViewJob for the captured frame.

        Returns None if this is not a well-based acquisition or downsampled views are disabled.
        """
        if not self._generate_downsampled_views:
            return None
        if not self._is_well_based_acquisition():
            return None
        if self._downsampled_output_dir is None:
            return None

        # Initialize manager on first image (deferred from _initialize_plate_view)
        if self._downsampled_view_manager is None:
            self._calculate_overlap_pixels(image)
            self._initialize_downsampled_view_manager(image)

        well_id = info.region_id
        try:
            well_row, well_col = parse_well_id(well_id)
        except ValueError:
            self._log.warning(f"Could not parse well ID '{well_id}', skipping downsampled view")
            return None

        # Get FOV index and total FOVs for this well
        total_fovs = self._well_fov_counts.get(well_id, 1)
        fov_index = info.fov

        # Get the first FOV position for this region to calculate relative position
        region_coords = self.scan_region_fov_coords_mm.get(well_id, [])
        if region_coords and fov_index < len(region_coords):
            first_fov = region_coords[0]
            current_fov = region_coords[fov_index]
            # Relative position in mm from first FOV
            fov_position = (current_fov[0] - first_fov[0], current_fov[1] - first_fov[1])
        else:
            fov_position = (0.0, 0.0)

        # Get pixel size
        pixel_size_um = self._pixel_size_um if self._pixel_size_um else 1.0

        # Create the job
        job = DownsampledViewJob(
            capture_info=info,
            capture_image=JobImage(image_array=image),
            well_id=well_id,
            well_row=well_row,
            well_col=well_col,
            fov_index=fov_index,
            total_fovs_in_well=total_fovs,
            channel_idx=info.configuration_idx,
            total_channels=len(self.selected_configurations),
            channel_name=info.configuration.name,
            fov_position_in_well=fov_position,
            overlap_pixels=self._overlap_pixels,
            pixel_size_um=pixel_size_um,
            target_resolutions_um=list(self._downsampled_well_resolutions_um),
            plate_resolution_um=self._downsampled_plate_resolution_um,
            output_dir=self._downsampled_output_dir,
            channel_names=[cfg.name for cfg in self.selected_configurations],
            z_index=info.z_index,
            total_z_levels=self.NZ,
            z_projection_mode=self._downsampled_z_projection,
            skip_saving=self.skip_saving,
            save_well_images=self._save_downsampled_well_images and not SIMULATED_DISK_IO_ENABLED,
            interpolation_method=self._downsampled_interpolation_method,
        )

        return job

    def _frame_wait_timeout_s(self) -> float:
        override = getattr(self, "frame_wait_timeout_override_s", None)
        if override is not None:
            return override
        return (self._camera_service.get_total_frame_time() / 1e3) + 10

    def acquire_camera_image(
        self,
        config: AcquisitionChannel,
        file_ID: str,
        current_path: str,
        k: int,
        region_id: str,
        fov: int,
        config_idx: int,
        fov_id: Optional[str] = None,
    ) -> None:
        self._select_config(config)

        # trigger acquisition (including turning on the illumination) and read frame
        camera_illumination_time: Optional[float] = self._camera_service.get_exposure_time()
        if self._trigger_mode == TriggerMode.SOFTWARE:
            self._turn_on_illumination(config)
            self.wait_till_operation_is_completed()
            camera_illumination_time = None
        elif self._trigger_mode == TriggerMode.HARDWARE:
            if (
                "Fluorescence" in config.name
                and self._feature_flags.is_enabled("ENABLE_NL5")
                and self._feature_flags.is_enabled("NL5_USE_DOUT")
            ):
                if self._nl5_service:
                    try:
                        self._nl5_service.start_acquisition()
                    except Exception:
                        self._log.exception("Failed to start NL5 acquisition via service")
                else:
                    self._log.warning("NL5 service unavailable; skipping start_acquisition()")
        # This is some large timeout that we use just so as to not block forever
        with self._timing.get_timer("_ready_for_next_trigger.wait"):
            if not self._ready_for_next_trigger.wait(self._frame_wait_timeout_s()):
                self._log.warning(
                    "Frame callback never set _have_last_triggered_image callback within timeout; continuing."
                )
                # Ensure we do not block future triggers; treat as ready and continue.
                self._ready_for_next_trigger.set()
        with self._timing.get_timer("get_ready_for_trigger re-check"):
            # This should be a noop - we have the frame already.  Still, check!
            while not self._camera_service.get_ready_for_trigger():
                self._sleep(0.001)

            self._ready_for_next_trigger.clear()
        with self._timing.get_timer("current_capture_info ="):
            # Even though the capture time will be slightly after this, we need to capture and set the capture info
            # before the trigger to be 100% sure the callback doesn't stomp on it.
            # NOTE(imo): One level up from acquire_camera_image, we have acquire_pos.  We're careful to use that as
            # much as we can, but don't use it here because we'd rather take the position as close as possible to the
            # real capture time for the image info.  Ideally we'd use this position for the caller's acquire_pos as well.
            current_position = self._stage_service.get_position()
            current_capture_info: CaptureInfo = CaptureInfo(
                position=current_position,
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
                pixel_size_um=self._pixel_size_um,
                fov_id=fov_id,
            )
            self._current_capture_info.set(current_capture_info)
        with self._timing.get_timer("send_trigger"):
            self._camera_service.send_trigger(illumination_time=camera_illumination_time)

        with self._timing.get_timer("exposure_time_done_sleep_hw or wait_for_image_sw"):
            total_frame_time = self._camera_service.get_total_frame_time()
            if self._trigger_mode == TriggerMode.HARDWARE:
                exposure_done_time: float = time.time() + total_frame_time / 1e3
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
                non_hw_frame_timeout: float = 5 * total_frame_time / 1e3 + 2
                if not self._ready_for_next_trigger.wait(non_hw_frame_timeout):
                    self._log.error(
                        f"Timed out waiting {non_hw_frame_timeout} [s] for a frame, trying synchronous read."
                    )
                    try:
                        fallback_frame = self._camera_service.read_frame()
                        if fallback_frame is not None:
                            # Process immediately to keep pipeline moving.
                            self._process_camera_frame(fallback_frame)
                        else:
                            self._log.error("Fallback frame read returned None.")
                    except Exception as exc:  # best effort
                        self._log.error("Fallback frame read failed.", exc_info=exc)
                    # Do not abort here; let caller decide based on _aborted flag.

        # turn off the illumination if using software trigger
        if self._trigger_mode == TriggerMode.SOFTWARE:
            self._turn_off_illumination(config)

    def _sleep(self, sec: float) -> None:
        time_to_sleep: float = max(sec, 1e-6)
        # self._log.debug(f"Sleeping for {time_to_sleep} [s]")
        scaled_sleep = scale_duration(time_to_sleep, min_seconds=1e-6)
        time.sleep(scaled_sleep)

    def acquire_rgb_image(
        self,
        config: AcquisitionChannel,
        file_ID: str,
        current_path: str,
        k: int,
        region_id: str,
        fov: int,
        fov_id: Optional[str] = None,
    ) -> None:
        # go through the channels
        rgb_channels: List[str] = [
            "BF LED matrix full_R",
            "BF LED matrix full_G",
            "BF LED matrix full_B",
        ]
        images: Dict[str, np.ndarray] = {}

        for (
            config_
        ) in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            if config_.name in rgb_channels:
                self._select_config(config_)

                # trigger acquisition (including turning on the illumination)
                if self._trigger_mode == TriggerMode.SOFTWARE:
                    self._turn_on_illumination(config_)
                    self.wait_till_operation_is_completed()

                # read camera frame
                exposure_time = self._camera_service.get_exposure_time()
                self._camera_service.send_trigger(illumination_time=exposure_time)
                image: Optional[np.ndarray] = self._camera_service.read_frame()
                if image is None:
                    self._log.warning("camera.read_frame() returned None")
                    continue

                # turn off the illumination if using software trigger
                if self._trigger_mode == TriggerMode.SOFTWARE:
                    self._turn_off_illumination(config_)

                # add the image to dictionary
                images[config_.name] = np.copy(image)

        # Check if the image is RGB or monochrome
        i_size: Tuple[int, ...] = images["BF LED matrix full_R"].shape

        rgb_position = self._stage_service.get_position()
        current_capture_info: CaptureInfo = CaptureInfo(
            position=rgb_position,
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
            pixel_size_um=self._pixel_size_um,
            fov_id=fov_id,
        )

        if len(i_size) == 3:
            # If already RGB, write and emit individual channels
            self._log.debug("writing R, G, B channels")
            self.handle_rgb_channels(images, current_capture_info)
            rgb_image = None
            try:
                r = images["BF LED matrix full_R"]
                g = images["BF LED matrix full_G"]
                b = images["BF LED matrix full_B"]
                if r.ndim == 3 and r.shape[2] == 3:
                    r = r[:, :, 0]
                if g.ndim == 3 and g.shape[2] == 3:
                    g = g[:, :, 1]
                if b.ndim == 3 and b.shape[2] == 3:
                    b = b[:, :, 2]
                rgb_image = np.stack([r, g, b], axis=2)
            except Exception:
                rgb_image = None
        else:
            # If monochrome, reconstruct RGB image
            self._log.debug("constructing RGB image")
            rgb_image = self.construct_rgb_image(images, current_capture_info)

        # Emit a single composite RGB frame onto the data-plane so the mosaic updates.
        if rgb_image is not None and self._stream_handler is not None:
            try:
                self._stream_handler.on_new_image(  # type: ignore[attr-defined]
                    rgb_image,
                    frame_id=0,
                    timestamp=current_capture_info.capture_time,
                    is_color=True,
                    respect_accept_new_frame=False,
                    capture_info=current_capture_info,
                )
            except Exception:
                self._log.exception("Failed to emit RGB image to StreamHandler for display")

    @staticmethod
    def handle_rgb_generation(
        current_round_images: Dict[str, np.ndarray], capture_info: CaptureInfo
    ) -> None:
        keys_to_check: List[str] = [
            "BF LED matrix full_R",
            "BF LED matrix full_G",
            "BF LED matrix full_B",
        ]
        if all(key in current_round_images for key in keys_to_check):
            _log.debug(f"constructing RGB image: dtype={current_round_images['BF LED matrix full_R'].dtype}")
            size: Tuple[int, ...] = current_round_images["BF LED matrix full_R"].shape
            rgb_image: np.ndarray = np.zeros(
                (*size, 3), dtype=current_round_images["BF LED matrix full_R"].dtype
            )
            _log.debug(f"RGB image shape: {rgb_image.shape}")
            rgb_image[:, :, 0] = current_round_images["BF LED matrix full_R"]
            rgb_image[:, :, 1] = current_round_images["BF LED matrix full_G"]
            rgb_image[:, :, 2] = current_round_images["BF LED matrix full_B"]

            # Display emission is handled by StreamHandler in acquire_rgb_image.

            # write the image
            if len(rgb_image.shape) == 3:
                _log.debug("writing RGB image")
                if rgb_image.dtype == np.uint16:
                    iio.imwrite(
                        os.path.join(
                            capture_info.save_directory,
                            capture_info.file_id + "_BF_LED_matrix_full_RGB.tiff",
                        ),
                        rgb_image,
                    )
                else:
                    iio.imwrite(
                        os.path.join(
                            capture_info.save_directory,
                            capture_info.file_id
                            + "_BF_LED_matrix_full_RGB."
                            + Acquisition.IMAGE_FORMAT,
                        ),
                        rgb_image,
                    )

    def handle_rgb_channels(
        self, images: Dict[str, np.ndarray], capture_info: CaptureInfo
    ) -> None:
        for channel in [
            "BF LED matrix full_R",
            "BF LED matrix full_G",
            "BF LED matrix full_B",
        ]:
            image_to_display: np.ndarray = utils.crop_image(
                images[channel],
                round(images[channel].shape[1] * self.display_resolution_scaling),
                round(images[channel].shape[0] * self.display_resolution_scaling),
            )
            # Display is handled by data plane / storage; callbacks removed

            file_name: str = (
                capture_info.file_id
                + "_"
                + channel.replace(" ", "_")
                + (
                    ".tiff"
                    if images[channel].dtype == np.uint16
                    else "." + Acquisition.IMAGE_FORMAT
                )
            )
            iio.imwrite(
                os.path.join(capture_info.save_directory, file_name), images[channel]
            )

    def construct_rgb_image(
        self, images: Dict[str, np.ndarray], capture_info: CaptureInfo
    ) -> np.ndarray:
        rgb_image: np.ndarray = np.zeros(
            (*images["BF LED matrix full_R"].shape, 3),
            dtype=images["BF LED matrix full_R"].dtype,
        )
        rgb_image[:, :, 0] = images["BF LED matrix full_R"]
        rgb_image[:, :, 1] = images["BF LED matrix full_G"]
        rgb_image[:, :, 2] = images["BF LED matrix full_B"]

        # write the RGB image
        self._log.debug("writing RGB image")
        file_name: str = (
            capture_info.file_id
            + "_BF_LED_matrix_full_RGB"
            + (
                ".tiff"
                if rgb_image.dtype == np.uint16
                else "." + Acquisition.IMAGE_FORMAT
            )
        )
        iio.imwrite(os.path.join(capture_info.save_directory, file_name), rgb_image)
        return rgb_image

    def handle_acquisition_abort(self, current_path: str) -> None:
        # Save coordinates.csv
        self.coordinates_pd.to_csv(
            os.path.join(current_path, "coordinates.csv"), index=False, header=True
        )
        self._aborted = True
        self._peripheral_service.enable_joystick(True)

        self._wait_for_outstanding_callback_images()

    def move_z_for_stack(self) -> None:
        """Move to next z-level in stack using ZStackExecutor."""
        self._zstack_executor.step()
        # Keep z_piezo_um in sync for backward compatibility
        if self.use_piezo:
            self.z_piezo_um = self._zstack_executor.z_piezo_um

    def move_z_back_after_stack(self) -> None:
        """Return to stack start position using ZStackExecutor."""
        self._zstack_executor.return_to_start()
        # Keep z_piezo_um in sync for backward compatibility
        if self.use_piezo:
            self.z_piezo_um = self._zstack_executor.z_piezo_um
