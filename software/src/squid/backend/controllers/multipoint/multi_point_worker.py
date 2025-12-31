import os
import queue
import time
from typing import Dict, List, NamedTuple, Optional, Tuple, Type, TYPE_CHECKING
from typing import Callable
from datetime import datetime

import imageio as iio
import numpy as np
import pandas as pd

from _def import *
import squid.core.utils.hardware_utils as utils
from squid.backend.controllers.autofocus import AutoFocusController
from squid.backend.managers import ChannelConfigurationManager
from squid.backend.controllers.autofocus import LaserAutofocusController
from squid.backend.controllers.multipoint.multi_point_utils import AcquisitionParameters
from squid.backend.managers import ObjectiveStore
from squid.core.utils.config_utils import ChannelMode
from squid.core.abc import CameraFrame, CameraFrameFormat
import squid.core.logging
import squid.backend.controllers.multipoint.job_processing
from squid.backend.controllers.multipoint.job_processing import (
    CaptureInfo,
    SaveImageJob,
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
from squid.core.config import CameraPixelFormat
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

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
    from squid.backend.controllers import MicroscopeModeController
    from squid.core.events import EventBus

from squid.core.events import (
    AcquisitionStarted,
    AcquisitionProgress,
    AcquisitionWorkerFinished,
    AcquisitionWorkerProgress,
    PlateViewInit,
    PlateViewUpdate,
)


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
        channel_configuration_mananger: ChannelConfigurationManager,
        acquisition_parameters: AcquisitionParameters,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        trigger_mode: TriggerMode = TriggerMode.SOFTWARE,
        illumination_service: Optional["IlluminationService"] = None,
        filter_wheel_service: Optional["FilterWheelService"] = None,
        enable_channel_auto_filter_switching: bool = True,
        *,
        extra_job_classes: list[type[Job]] | None = None,
        abort_on_failed_jobs: bool = True,
        piezo_service: Optional["PiezoService"] = None,
        fluidics_service: Optional["FluidicsService"] = None,
        nl5_service: Optional["NL5Service"] = None,
        stream_handler: Optional[object] = None,
    ):
        self._log = squid.core.logging.get_logger(__class__.__name__)
        self._timing = utils.TimingManager("MultiPointWorker Timer Manager")
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
        self.channelConfigurationManager: ChannelConfigurationManager = (
            channel_configuration_mananger
        )
        self.use_fluidics = acquisition_parameters.use_fluidics
        self._aborted: bool = False
        self._abort_requested = ThreadSafeFlag(initial=False)
        self.NZ = acquisition_parameters.NZ
        self.deltaZ = acquisition_parameters.deltaZ

        self.Nt = acquisition_parameters.Nt
        self.dt = acquisition_parameters.deltat

        self.do_autofocus = acquisition_parameters.do_autofocus
        self.do_reflection_af = acquisition_parameters.do_reflection_autofocus
        self.use_piezo = acquisition_parameters.use_piezo
        self.display_resolution_scaling = (
            acquisition_parameters.display_resolution_scaling
        )

        self.experiment_ID = acquisition_parameters.experiment_ID
        self.base_path = acquisition_parameters.base_path
        self.experiment_path = os.path.join(
            self.base_path or "", self.experiment_ID or ""
        )
        self.selected_configurations = acquisition_parameters.selected_configurations
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
        self._plate_num_rows = acquisition_parameters.plate_num_rows
        self._plate_num_cols = acquisition_parameters.plate_num_cols
        self._downsampled_well_resolutions_um = acquisition_parameters.downsampled_well_resolutions_um
        self._downsampled_plate_resolution_um = acquisition_parameters.downsampled_plate_resolution_um
        self._downsampled_z_projection = acquisition_parameters.downsampled_z_projection
        self._xy_mode = acquisition_parameters.xy_mode
        self._downsampled_view_manager: Optional[DownsampledViewManager] = None
        self._downsampled_output_dir: Optional[str] = None

        # Track FOV counts per well for multi-FOV wells
        self._well_fov_counts: Dict[str, int] = {}  # well_id -> total FOVs

        job_classes = [] if self.skip_saving else [SaveImageJob]
        if extra_job_classes:
            job_classes.extend(extra_job_classes)

        # Add DownsampledViewJob if downsampled views are enabled
        if self._generate_downsampled_views:
            job_classes.append(DownsampledViewJob)

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
                squid.backend.controllers.multipoint.job_processing.JobRunner()
                if Acquisition.USE_MULTIPROCESSING
                else None
            )
            if job_runner:
                job_runner.daemon = True
                job_runner.start()
            self._job_runners.append((job_class, job_runner))
        self._abort_on_failed_job = abort_on_failed_jobs

    # =========================================================================
    # Service helper methods
    # =========================================================================
    def _camera_get_pixel_size_binned_um(self) -> Optional[float]:
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

    def _camera_add_frame_callback(self, callback: Callable) -> str:
        return self._camera_service.add_frame_callback(callback)

    def _camera_remove_frame_callback(self, callback_id: str) -> None:
        self._camera_service.remove_frame_callback(callback_id)

    def _camera_start_streaming(self) -> None:
        self._camera_service.start_streaming()

    def _camera_stop_streaming(self) -> None:
        self._camera_service.stop_streaming()

    def _camera_send_trigger(self, illumination_time: Optional[float]) -> None:
        self._camera_service.send_trigger(illumination_time=illumination_time)

    def _camera_get_ready_for_trigger(self) -> bool:
        return self._camera_service.get_ready_for_trigger()

    def _camera_get_total_frame_time(self) -> float:
        return self._camera_service.get_total_frame_time()

    def _camera_get_strobe_time(self) -> float:
        return self._camera_service.get_strobe_time()

    def _camera_read_frame(self):
        return self._camera_service.read_frame()

    def _camera_get_exposure_time(self) -> Optional[float]:
        return self._camera_service.get_exposure_time()

    def _stage_get_pos(self) -> "squid.core.abc.Pos":
        return self._stage_service.get_position()

    def _stage_move_x_to(self, x_mm: float) -> None:
        self._stage_service.move_x_to(x_mm)
        self._stage_service.wait_for_idle()

    def _stage_move_y_to(self, y_mm: float) -> None:
        self._stage_service.move_y_to(y_mm)
        self._stage_service.wait_for_idle()

    def _stage_move_z_to(self, z_mm: float) -> None:
        self._stage_service.move_z_to(z_mm)
        self._stage_service.wait_for_idle()

    def _stage_move_z(self, delta_mm: float) -> None:
        self._stage_service.move_z(delta_mm)
        self._stage_service.wait_for_idle()

    def _peripheral_enable_joystick(self, enabled: bool) -> None:
        self._peripheral_service.enable_joystick(enabled)

    def _peripheral_wait_till_operation_is_completed(self) -> None:
        self._peripheral_service.wait_till_operation_is_completed()

    def _piezo_get_position(self) -> float:
        if self._piezo_service:
            return self._piezo_service.get_position()
        return 0.0

    def _piezo_move_to(self, position_um: float) -> None:
        if self._piezo_service:
            self._piezo_service.move_to(position_um)

    def update_use_piezo(self, value: bool) -> None:
        self.use_piezo = value
        self._log.info(f"MultiPointWorker: updated use_piezo to {value}")

    def request_abort(self) -> None:
        self._aborted = True
        self._abort_requested.set()

    def _require_experiment_id(self) -> str:
        """Ensure we have a valid experiment ID before publishing events."""
        if not self.experiment_ID:
            raise RuntimeError("Experiment ID is not set; call start_new_experiment before running acquisition")
        return self.experiment_ID

    def _publish_acquisition_started(self) -> None:
        """Publish AcquisitionStarted event via EventBus."""
        if not self._event_bus:
            return
        self._acquisition_start_time = time.time()
        experiment_id = self._require_experiment_id()
        self._event_bus.publish(
            AcquisitionStarted(
                experiment_id=experiment_id,
                timestamp=self._acquisition_start_time,
            )
        )

    def _publish_acquisition_finished(self, success: bool, error: Optional[Exception] = None) -> None:
        """Publish AcquisitionWorkerFinished event via EventBus."""
        if not self._event_bus:
            return
        experiment_id = self._require_experiment_id()

        # Publish AcquisitionWorkerFinished for controller state machine
        self._event_bus.publish(
            AcquisitionWorkerFinished(
                experiment_id=experiment_id,
                success=success,
                error=str(error) if error else None,
                final_fov_count=self.af_fov_count,
            )
        )

    def _publish_acquisition_progress(
        self,
        current_fov: int,
        total_fovs: int,
        current_region: int,
        total_regions: int,
        current_channel: str,
    ) -> None:
        """Publish AcquisitionProgress event via EventBus."""
        if not self._event_bus:
            return
        experiment_id = self._require_experiment_id()

        # Calculate overall progress percentage
        if total_fovs > 0 and total_regions > 0:
            # Progress across all regions and FOVs
            region_progress = (current_region - 1) / total_regions
            fov_progress_in_region = current_fov / total_fovs
            progress_percent = (region_progress + fov_progress_in_region / total_regions) * 100.0
        else:
            progress_percent = 0.0

        # Calculate ETA based on elapsed time and progress
        eta_seconds: Optional[float] = None
        if self._acquisition_start_time and progress_percent > 0:
            elapsed = time.time() - self._acquisition_start_time
            total_estimated = elapsed * 100.0 / progress_percent
            eta_seconds = total_estimated - elapsed

        self._event_bus.publish(
            AcquisitionProgress(
                current_fov=current_fov,
                total_fovs=total_fovs,
                current_round=current_region,
                total_rounds=total_regions,
                current_channel=current_channel,
                progress_percent=progress_percent,
                experiment_id=experiment_id,
                eta_seconds=eta_seconds,
            )
        )

    def _publish_worker_progress(
        self,
        current_region: int,
        total_regions: int,
        current_fov: int,
        total_fovs: int,
    ) -> None:
        """Publish AcquisitionWorkerProgress event for controller state tracking.

        This event provides detailed progress information that the controller
        can use for internal tracking and validation.
        """
        if not self._event_bus:
            return
        experiment_id = self._require_experiment_id()

        self._event_bus.publish(
            AcquisitionWorkerProgress(
                experiment_id=experiment_id,
                current_region=current_region,
                total_regions=total_regions,
                current_fov=current_fov,
                total_fovs=total_fovs,
                current_timepoint=self.time_point + 1,  # 1-indexed for display
                total_timepoints=self.Nt,
            )
        )

    def run(self) -> None:
        this_image_callback_id: Optional[str] = None
        acquisition_error: Optional[Exception] = None
        try:
            # Publish acquisition started event
            self._publish_acquisition_started()

            start_time: int = time.perf_counter_ns()
            # Register callback before starting streaming to avoid missing initial frames
            this_image_callback_id = self._camera_add_frame_callback(
                self._image_callback
            )
            self._camera_start_streaming()
            sleep_time: float = min(self.dt / 20.0, 0.5)

            while self.time_point < self.Nt:
                # check if abort acquisition has been requested
                if self._abort_requested.is_set():
                    self._log.debug("In run, abort_acquisition_requested=True")
                    break

                if self._fluidics_service and self.use_fluidics:
                    self._fluidics_service.update_port(
                        self.time_point
                    )  # use the port in PORT_LIST
                    # For MERFISH, before imaging, run the first 3 sequences (Add probe, wash buffer, imaging buffer)
                    self._fluidics_service.run_before_imaging()
                    self._fluidics_service.wait_for_completion()

                with self._timing.get_timer("run_single_time_point"):
                    self.run_single_time_point()

                if self._fluidics_service and self.use_fluidics:
                    # For MERFISH, after imaging, run the following 2 sequences (Cleavage buffer, SSC rinse)
                    self._fluidics_service.run_after_imaging()
                    self._fluidics_service.wait_for_completion()

                self.time_point = self.time_point + 1
                if self.dt == 0:  # continous acquisition
                    pass
                else:  # timed acquisition
                    # check if the aquisition has taken longer than dt or integer multiples of dt, if so skip the next time point(s)
                    while (
                        time.time()
                        > self.timestamp_acquisition_started + self.time_point * self.dt
                    ):
                        self._log.info("skip time point " + str(self.time_point + 1))
                        self.time_point = self.time_point + 1

                    # check if it has reached Nt
                    if self.time_point == self.Nt:
                        break  # no waiting after taking the last time point

                    # wait until it's time to do the next acquisition
                    while (
                        time.time()
                        < self.timestamp_acquisition_started + self.time_point * self.dt
                    ):
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
            # We do this above, but there are some paths that skip the proper end of the acquisition so make
            # sure to always wait for final images here before removing our callback.
            self._wait_for_outstanding_callback_images()
            self._log.debug(self._timing.get_report())

            # Stop camera streaming before removing callback to ensure clean state.
            # Without this, the camera continues streaming after acquisition, causing
            # issues on subsequent acquisitions (e.g., GUI freeze).
            self._camera_stop_streaming()

            if this_image_callback_id:
                self._camera_remove_frame_callback(this_image_callback_id)

            self._finish_jobs()
            # Publish acquisition finished event via EventBus
            self._publish_acquisition_finished(
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
                        time.sleep(0.1)
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

    def wait_till_operation_is_completed(self) -> None:
        self._peripheral_wait_till_operation_is_completed()

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
                "Set DISPLAY_PLATE_VIEW=True or GENERATE_DOWNSAMPLED_WELL_IMAGES=True in _def.py"
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

    def run_single_time_point(self) -> None:
        try:
            start: float = time.time()
            self._peripheral_enable_joystick(False)

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
            self._peripheral_enable_joystick(True)

    def initialize_z_stack(self) -> None:
        # z stacking config
        if self.z_stacking_config == "FROM TOP":
            self.deltaZ = -abs(self.deltaZ)
            self.move_to_z_level(self.z_range[1])
        else:
            self.move_to_z_level(self.z_range[0])

        # Get z position at the beginning of the scan
        self.z_pos = self._stage_get_pos().z_mm

    def initialize_coordinates_dataframe(self) -> None:
        base_columns: List[str] = ["z_level", "x (mm)", "y (mm)", "z (um)", "time"]
        piezo_column: List[str] = ["z_piezo (um)"] if self.use_piezo else []
        self.coordinates_pd: pd.DataFrame = pd.DataFrame(
            columns=["region", "fov"] + base_columns + piezo_column
        )

    def update_coordinates_dataframe(
        self,
        region_id: str,
        z_level: int,
        pos: squid.core.abc.Pos,
        fov: Optional[int] = None,
    ) -> None:
        base_data = {
            "z_level": [z_level],
            "x (mm)": [pos.x_mm],
            "y (mm)": [pos.y_mm],
            "z (um)": [pos.z_mm * 1000],
            "time": [datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")],
        }
        piezo_data = {"z_piezo (um)": [self.z_piezo_um]} if self.use_piezo else {}

        new_row: pd.DataFrame = pd.DataFrame(
            {"region": [region_id], "fov": [fov], **base_data, **piezo_data}
        )

        self.coordinates_pd = pd.concat(
            [self.coordinates_pd, new_row], ignore_index=True
        )

    def move_to_coordinate(
        self, coordinate_mm: Tuple[float, ...], region_id: str, fov: int
    ) -> None:
        self._log.info(f"moving to coordinate {coordinate_mm}")
        x_mm: float = coordinate_mm[0]
        self._stage_move_x_to(x_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_X / 1000)

        y_mm: float = coordinate_mm[1]
        self._stage_move_y_to(y_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Y / 1000)

        # check if z is included in the coordinate
        if (self.do_reflection_af or self.do_autofocus) and self.time_point > 0:
            if (region_id, fov) in self._last_time_point_z_pos:
                last_z_mm: float = self._last_time_point_z_pos[(region_id, fov)]
                self.move_to_z_level(last_z_mm)
                self._log.info(f"Moved to last z position {last_z_mm} [mm]")
            else:
                self._log.warning(
                    f"No last z position found for region {region_id}, fov {fov}"
                )
        elif len(coordinate_mm) == 3:
            z_mm: float = coordinate_mm[2]
            self.move_to_z_level(z_mm)

        if self._event_bus is not None:
            from squid.core.events import CurrentFOVRegistered

            fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
            self._event_bus.publish(
                CurrentFOVRegistered(
                    x_mm=x_mm,
                    y_mm=y_mm,
                    fov_width_mm=fov_width_mm,
                    fov_height_mm=fov_height_mm,
                )
            )

    def move_to_z_level(self, z_mm: float) -> None:
        print("moving z")
        self._stage_move_z_to(z_mm)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def _summarize_runner_outputs(self, drain_all: bool = True) -> SummarizeResult:
        """Process job results from output queues.

        Args:
            drain_all: If True, process ALL available results. If False, process at most one per queue.

        Returns:
            SummarizeResult with none_failed and had_results.
        """
        none_failed: bool = True
        had_results: bool = False
        self._log.debug(f"_summarize_runner_outputs: checking {len(self._job_runners)} runners")
        for job_class, job_runner in self._job_runners:
            if job_runner is None:
                self._log.debug(f"  {job_class.__name__}: runner is None, skipping")
                continue
            out_queue = job_runner.output_queue()
            result_count = 0
            # Drain results from the queue
            while True:
                try:
                    job_result: JobResult = out_queue.get_nowait()
                    result_count += 1
                    had_results = True
                    # TODO(imo): Should we abort if there is a failure?
                    none_failed = none_failed and self._summarize_job_result(job_result)

                    # Handle DownsampledViewResult specially
                    if job_result.result is not None and isinstance(
                        job_result.result, DownsampledViewResult
                    ):
                        self._log.info(f"Got DownsampledViewResult for well {job_result.result.well_id}")
                        self._process_downsampled_view_result(job_result.result)
                    elif job_result.result is not None:
                        self._log.debug(f"Got job result of type {type(job_result.result).__name__}")

                    if not drain_all:
                        break  # Only process one result per queue if not draining
                except queue.Empty:
                    if result_count > 0:
                        self._log.debug(f"  {job_class.__name__}: drained {result_count} results from output queue")
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
        n_regions: int = len(self.scan_region_coords_mm)

        for region_index, (region_id, coordinates) in enumerate(
            self.scan_region_fov_coords_mm.items()
        ):
            # Progress is published via events; callback removed
            # Track region info for EventBus progress events
            self._current_region = region_index + 1
            self._total_regions = n_regions
            self.num_fovs = len(coordinates)
            self.total_scans = (
                self.num_fovs * self.NZ * len(self.selected_configurations)
            )

            for fov, coordinate_mm in enumerate(coordinates):
                # Just so the job result queues don't get too big, check and print a summary of intermediate results here
                with self._timing.get_timer("job result summaries"):
                    if (
                        not self._summarize_runner_outputs().none_failed
                        and self._abort_on_failed_job
                    ):
                        self._log.error(
                            "Some jobs failed, aborting acquisition because abort_on_failed_job=True"
                        )
                        self.request_abort()
                        return

                with self._timing.get_timer("move_to_coordinate"):
                    self.move_to_coordinate(coordinate_mm, region_id, fov)
                with self._timing.get_timer("acquire_at_position"):
                    self.acquire_at_position(region_id, current_path, fov)

                if self._abort_requested.is_set():
                    self.handle_acquisition_abort(current_path)
                    return

    def acquire_at_position(self, region_id: str, current_path: str, fov: int) -> None:
        if not self.perform_autofocus(region_id, fov):
            current_z = self._stage_get_pos().z_mm
            self._log.error(
                f"Autofocus failed in acquire_at_position.  Continuing to acquire anyway using the current z position (z={current_z} [mm])"
            )

        if self.NZ > 1:
            self.prepare_z_stack()

        if self.use_piezo:
            self.z_piezo_um: float = self._piezo_get_position()

        for z_level in range(self.NZ):
            file_ID: str = (
                f"{region_id}_{fov:0{FILE_ID_PADDING}}_{z_level:0{FILE_ID_PADDING}}"
            )

            acquire_pos: squid.core.abc.Pos = self._stage_get_pos()
            metadata: Dict[str, float] = {
                "x": acquire_pos.x_mm,
                "y": acquire_pos.y_mm,
                "z": acquire_pos.z_mm,
            }
            self._log.info(f"Acquiring image: ID={file_ID}, Metadata={metadata}")

            if (
                z_level == 0
                and (self.do_reflection_af or self.do_autofocus)
                and self.Nt > 1
            ):
                self._last_time_point_z_pos[(region_id, fov)] = acquire_pos.z_mm

            # laser af characterization mode
            if (
                self.laser_auto_focus_controller
                and self.laser_auto_focus_controller.characterization_mode
            ):
                image: np.ndarray = self.laser_auto_focus_controller.get_image()
                saving_path: str = os.path.join(
                    current_path, file_ID + "_laser af camera" + ".bmp"
                )
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
                        self.acquire_rgb_image(
                            config, file_ID, current_path, z_level, region_id, fov
                        )
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

                if self.NZ == 1:  # TODO: handle z offset for z stack
                    self.handle_z_offset(config, False)

                current_image: int = (
                    fov * self.NZ * len(self.selected_configurations)
                    + z_level * len(self.selected_configurations)
                    + config_idx
                    + 1
                )
                # Publish progress event via EventBus
                self._publish_acquisition_progress(
                    current_fov=current_image,
                    total_fovs=self.total_scans,
                    current_region=getattr(self, "_current_region", 1),
                    total_regions=getattr(self, "_total_regions", 1),
                    current_channel=config.name,
                )

                # Publish worker progress event for controller state tracking
                self._publish_worker_progress(
                    current_region=getattr(self, "_current_region", 1),
                    total_regions=getattr(self, "_total_regions", 1),
                    current_fov=current_image,
                    total_fovs=self.total_scans,
                )

            # updates coordinates df
            self.update_coordinates_dataframe(region_id, z_level, acquire_pos, fov)
            # check if the acquisition should be aborted
            if self._abort_requested.is_set():
                self.handle_acquisition_abort(current_path)

            # update FOV counter
            self.af_fov_count = self.af_fov_count + 1

            if z_level < self.NZ - 1:
                self.move_z_for_stack()

        if self.NZ > 1:
            self.move_z_back_after_stack()

    def _select_config(self, config: ChannelMode) -> None:
        self._apply_channel_mode(config)
        self.wait_till_operation_is_completed()

    def _apply_channel_mode(self, config: ChannelMode) -> None:
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
                        delay = -int(self._camera_get_strobe_time())
                    self._filter_wheel_service.set_delay_offset_ms(delay)
                except Exception:
                    pass
                try:
                    self._filter_wheel_service.set_filter_wheel_position({1: int(position)})
                except Exception:
                    pass

    def _turn_on_illumination(self, config: ChannelMode) -> None:
        if self._illumination_service is None:
            return
        source = getattr(config, "illumination_source", None)
        if source is None:
            return
        try:
            self._illumination_service.turn_on_channel(int(source))
        except Exception:
            pass

    def _turn_off_illumination(self, config: ChannelMode) -> None:
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
        if not self.do_reflection_af:
            # contrast-based AF; perform AF only if when not taking z stack or doing z stack from center
            if (
                ((self.NZ == 1) or self.z_stacking_config == "FROM CENTER")
                and (self.do_autofocus)
                and (self.af_fov_count % Acquisition.NUMBER_OF_FOVS_PER_AF == 0)
            ):
                configuration_name_AF = MULTIPOINT_AUTOFOCUS_CHANNEL
                config_AF = (
                    self.channelConfigurationManager.get_channel_configuration_by_name(
                        self.objectiveStore.current_objective, configuration_name_AF
                    )
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
                saving_path: str = os.path.join(
                    self.base_path, self.experiment_ID, str(self.time_point), file_ID
                )
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
            z_delta = -self.deltaZ * round((self.NZ - 1) / 2.0)
            self._stage_move_z(z_delta)
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)
        self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def handle_z_offset(self, config: ChannelMode, not_offset: bool) -> None:
        if (
            config.z_offset is not None
        ):  # perform z offset for config, assume z_offset is in um
            if config.z_offset != 0.0:
                direction: int = 1 if not_offset else -1
                self._log.info("Moving Z offset" + str(config.z_offset * direction))
                z_delta = config.z_offset / 1000 * direction
                self._stage_move_z(z_delta)
                self.wait_till_operation_is_completed()
                self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

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
                        # Run synchronously and handle the result
                        result = job.run()
                        if result is not None and isinstance(result, DownsampledViewResult):
                            self._log.info(f"Synchronous job returned DownsampledViewResult for well {result.well_id}")
                            self._process_downsampled_view_result(result)

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
        )

        return job

    def _frame_wait_timeout_s(self) -> float:
        override = getattr(self, "frame_wait_timeout_override_s", None)
        if override is not None:
            return override
        return (self._camera_get_total_frame_time() / 1e3) + 10

    def acquire_camera_image(
        self,
        config: ChannelMode,
        file_ID: str,
        current_path: str,
        k: int,
        region_id: str,
        fov: int,
        config_idx: int,
    ) -> None:
        self._select_config(config)

        # trigger acquisition (including turning on the illumination) and read frame
        camera_illumination_time: Optional[float] = self._camera_get_exposure_time()
        if self._trigger_mode == TriggerMode.SOFTWARE:
            self._turn_on_illumination(config)
            self.wait_till_operation_is_completed()
            camera_illumination_time = None
        elif self._trigger_mode == TriggerMode.HARDWARE:
            if "Fluorescence" in config.name and ENABLE_NL5 and NL5_USE_DOUT:
                # TODO(imo): This used to use the "reset_image_ready_flag=False" on the read_frame, but oinly the toupcam camera implementation had the
                #  "reset_image_ready_flag" arg, so this is broken for all other cameras.  Also this used to do some other funky stuff like setting internal camera flags.
                #   I am pretty sure this is broken!
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
            while not self._camera_get_ready_for_trigger():
                self._sleep(0.001)

            self._ready_for_next_trigger.clear()
        with self._timing.get_timer("current_capture_info ="):
            # Even though the capture time will be slightly after this, we need to capture and set the capture info
            # before the trigger to be 100% sure the callback doesn't stomp on it.
            # NOTE(imo): One level up from acquire_camera_image, we have acquire_pos.  We're careful to use that as
            # much as we can, but don't use it here because we'd rather take the position as close as possible to the
            # real capture time for the image info.  Ideally we'd use this position for the caller's acquire_pos as well.
            current_position = self._stage_get_pos()
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
            self._camera_send_trigger(illumination_time=camera_illumination_time)

        with self._timing.get_timer("exposure_time_done_sleep_hw or wait_for_image_sw"):
            total_frame_time = self._camera_get_total_frame_time()
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
                        fallback_frame = self._camera_read_frame()
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
        time.sleep(time_to_sleep)

    def acquire_rgb_image(
        self,
        config: ChannelMode,
        file_ID: str,
        current_path: str,
        k: int,
        region_id: str,
        fov: int,
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
                exposure_time = self._camera_get_exposure_time()
                self._camera_send_trigger(illumination_time=exposure_time)
                image: Optional[np.ndarray] = self._camera_read_frame()
                if image is None:
                    print("camera.read_frame() returned None")
                    continue

                # TODO(imo): use illum controller
                # turn off the illumination if using software trigger
                if self._trigger_mode == TriggerMode.SOFTWARE:
                    self._turn_off_illumination(config_)

                # add the image to dictionary
                images[config_.name] = np.copy(image)

        # Check if the image is RGB or monochrome
        i_size: Tuple[int, ...] = images["BF LED matrix full_R"].shape

        rgb_position = self._stage_get_pos()
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
            print("constructing RGB image")
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
            print("constructing RGB image")
            print(current_round_images["BF LED matrix full_R"].dtype)
            size: Tuple[int, ...] = current_round_images["BF LED matrix full_R"].shape
            rgb_image: np.ndarray = np.zeros(
                (*size, 3), dtype=current_round_images["BF LED matrix full_R"].dtype
            )
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
        print("writing RGB image")
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
        self._peripheral_enable_joystick(True)

        self._wait_for_outstanding_callback_images()

    def move_z_for_stack(self) -> None:
        if self.use_piezo:
            self.z_piezo_um += self.deltaZ * 1000
            self._piezo_move_to(self.z_piezo_um)
            if (
                self._trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            self._stage_move_z(self.deltaZ)
            self._sleep(SCAN_STABILIZATION_TIME_MS_Z / 1000)

    def move_z_back_after_stack(self) -> None:
        if self.use_piezo:
            self.z_piezo_um = self.z_piezo_um - self.deltaZ * 1000 * (self.NZ - 1)
            self._piezo_move_to(self.z_piezo_um)
            if (
                self._trigger_mode == TriggerMode.SOFTWARE
            ):  # for hardware trigger, delay is in waiting for the last row to start exposure
                self._sleep(MULTIPOINT_PIEZO_DELAY_MS / 1000)
        else:
            rel_z_to_start: float
            if self.z_stacking_config == "FROM CENTER":
                rel_z_to_start = -self.deltaZ * (self.NZ - 1) + self.deltaZ * round(
                    (self.NZ - 1) / 2
                )
            else:
                rel_z_to_start = -self.deltaZ * (self.NZ - 1)

            self._stage_move_z(rel_z_to_start)
