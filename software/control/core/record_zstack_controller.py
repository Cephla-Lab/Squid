import math
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import List, Optional

import squid.logging
import control._def
from control.models.acquisition_config import AcquisitionChannel

log = squid.logging.get_logger("RecordZStackController")


def frame_count(fps: float, duration_s: float) -> int:
    return int(round(fps * duration_s))


def zstack_plane_count(z_min_um: float, z_max_um: float, step_um: float) -> int:
    if step_um <= 0 or z_max_um < z_min_um:
        raise ValueError("require step>0 and z_max>=z_min")
    # epsilon absorbs float representation error so e.g. 6.0/1.0 -> 5.999... still floors to 6
    return int(math.floor((z_max_um - z_min_um) / step_um + 1e-9)) + 1


def zstack_offsets_um(z_min_um: float, z_max_um: float, step_um: float) -> List[float]:
    return [round(z_min_um + i * step_um, 6) for i in range(zstack_plane_count(z_min_um, z_max_um, step_um))]


@dataclass
class RecordZStackAcquisitionParameters:
    base_path: str
    experiment_id: str
    Nt: int = 1
    dt_s: float = 0.0
    use_laser_af: bool = False
    # recording phase
    recording_enabled: bool = False
    recording_channel: Optional[AcquisitionChannel] = None
    fps: float = 10.0
    duration_s: float = 1.0
    recording_z_offset_um: float = 0.0
    # z-stack phase
    zstack_enabled: bool = False
    zstack_channels: List[AcquisitionChannel] = field(default_factory=list)
    z_min_um: float = -3.0
    z_max_um: float = 3.0
    z_step_um: float = 1.0


class RecordZStackController:
    """Controller that sets up the pre-warmed JobRunner and spawns RecordZStackWorker
    on a daemon thread.

    Constructor arguments mirror MultiPointController's signature where the concept
    overlaps. Call run_acquisition(params) with a fully-built
    RecordZStackAcquisitionParameters object (as the widget does via build_parameters()).
    """

    def __init__(
        self,
        microscope,
        live_controller,
        laser_autofocus_controller,
        objective_store,
        scan_coordinates,
        callbacks,
    ):
        self._microscope = microscope
        self._live_controller = live_controller
        self._laser_af = laser_autofocus_controller
        self._objective_store = objective_store
        self._scan_coordinates = scan_coordinates
        self._callbacks = callbacks

        self._abort_event: Event = Event()
        self._worker = None
        self._thread: Optional[Thread] = None

        # Pre-warm a job runner subprocess at init so it is ready when the user
        # clicks "Start Acquisition" (mirrors MultiPointController.__init__).
        self._prewarmed_job_runner = None
        self._prewarmed_bp_values = None
        if control._def.Acquisition.USE_MULTIPROCESSING:
            self._start_prewarmed_job_runner()

    # ---------------------------------------------------------------------- pre-warm

    def _start_prewarmed_job_runner(self) -> None:
        from control.core.job_processing import JobRunner
        from control.core.backpressure import create_backpressure_values

        log.info("Pre-warming job runner subprocess for RecordZStack...")
        self._prewarmed_bp_values = create_backpressure_values()
        self._prewarmed_job_runner = JobRunner(
            bp_pending_jobs=self._prewarmed_bp_values[0],
            bp_pending_bytes=self._prewarmed_bp_values[1],
            bp_capacity_event=self._prewarmed_bp_values[2],
        )
        self._prewarmed_job_runner.start()

    def _get_prewarmed_job_runner(self):
        """Consume the pre-warmed runner (start a fresh one for next time).

        Returns (runner, bp_values) or (None, None) when multiprocessing is off.
        """
        runner = self._prewarmed_job_runner
        bp_values = self._prewarmed_bp_values
        self._prewarmed_job_runner = None
        self._prewarmed_bp_values = None
        if control._def.Acquisition.USE_MULTIPROCESSING:
            self._start_prewarmed_job_runner()
        return runner, bp_values

    def _cleanup_prewarmed_runner(self, runner, timeout_s: float = 1.0, context: str = "") -> None:
        if runner is not None:
            try:
                runner.shutdown(timeout_s=timeout_s)
            except Exception as e:
                log.error(f"Error shutting down pre-warmed runner {context}: {e}")

    # ---------------------------------------------------------------------- acquisition

    def acquisition_in_progress(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def request_abort(self) -> None:
        """Signal the running worker to stop after the current FOV."""
        self._abort_event.set()

    def run_acquisition(self, params: RecordZStackAcquisitionParameters) -> None:
        """Set up the pre-warmed JobRunner and spawn the worker thread.

        *params* must be a fully-built RecordZStackAcquisitionParameters (as produced
        by the widget's build_parameters() method).
        """
        from control.core.acquisition_setup import create_experiment_dir
        from control.core.record_zstack_worker import RecordZStackWorker

        # Resolve and create a timestamped unique output directory.
        resolved_id, experiment_dir = create_experiment_dir(params.base_path, params.experiment_id)
        params.experiment_id = resolved_id

        # Snapshot the acquisition settings into the experiment directory
        # (mirrors MultiPointController.start_new_experiment) so the run is
        # reproducible/auditable: acquisition_channels.yaml records objective +
        # every channel used by either phase.
        try:
            channels = []
            if params.recording_enabled and params.recording_channel is not None:
                channels.append(params.recording_channel)
            if params.zstack_enabled:
                channels.extend(params.zstack_channels)
            self._microscope.config_repo.save_acquisition_output(
                output_dir=experiment_dir,
                objective=self._objective_store.current_objective,
                channels=channels,
                confocal_mode=self._live_controller.is_confocal_mode(),
            )
        except Exception:
            log.exception("Failed to save acquisition settings snapshot to the experiment directory")

        # Collect scan coordinates: {region_id: [(x_mm, y_mm[, z_mm]), ...]}
        scan_region_fov_coords = {}
        if self._scan_coordinates is not None and hasattr(self._scan_coordinates, "region_fov_coordinates"):
            scan_region_fov_coords = dict(self._scan_coordinates.region_fov_coordinates)

        # Clear abort event for this run (thread-safe: Event.clear() is atomic).
        # The small window between clear() and the worker thread starting is safe
        # under the single-acquisition-at-a-time assumption enforced by the widget
        # (toggle_acquisition checks acquisition_in_progress() before calling here).
        self._abort_event.clear()

        # Consume the pre-warmed runner; only pass it to the worker when
        # USE_MULTIPROCESSING is True (otherwise it's None and passing it would
        # create a resource leak if the worker ignores non-multiprocessing paths).
        prewarmed_runner, prewarmed_bp_values = self._get_prewarmed_job_runner()

        try:
            self._worker = RecordZStackWorker(
                scope=self._microscope,
                live_controller=self._live_controller,
                laser_auto_focus_controller=self._laser_af,
                objective_store=self._objective_store,
                params=params,
                callbacks=self._callbacks,
                abort_requested_fn=lambda: self._abort_event.is_set(),
                request_abort_fn=self.request_abort,
                scan_region_fov_coords=scan_region_fov_coords,
                prewarmed_job_runner=prewarmed_runner if control._def.Acquisition.USE_MULTIPROCESSING else None,
                prewarmed_bp_values=prewarmed_bp_values if control._def.Acquisition.USE_MULTIPROCESSING else None,
            )
        except Exception:
            # Clean up the pre-warmed runner if worker construction failed.
            self._cleanup_prewarmed_runner(prewarmed_runner, context="after worker creation failure")
            raise

        self._thread = Thread(target=self._worker.run, name="RecordZStack-acquisition", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the acquisition thread to finish (useful in tests and scripts)."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ---------------------------------------------------------------------- cleanup

    def close(self, timeout_s: float = 5.0) -> None:
        """Abort any running acquisition and shut down the pre-warmed job runner."""
        if self._prewarmed_job_runner is not None:
            log.info("Shutting down pre-warmed job runner for RecordZStackController...")
        self._cleanup_prewarmed_runner(
            self._prewarmed_job_runner,
            timeout_s=1.0,
            context="during close",
        )
        self._prewarmed_job_runner = None
        self._prewarmed_bp_values = None

        if self.acquisition_in_progress():
            self._abort_event.set()
            if self._thread is not None:
                self._thread.join(timeout=timeout_s)
                if self._thread.is_alive():
                    log.warning(f"RecordZStack acquisition thread did not stop within {timeout_s}s")

        self._worker = None
        self._thread = None
