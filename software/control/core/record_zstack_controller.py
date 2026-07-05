import math
import os
import time
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import List, Optional

import yaml

import squid.logging
import control._def
from control.models.acquisition_config import AcquisitionChannel
from control.utils import serialize_for_yaml as _serialize_for_yaml

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


def recording_plane_offsets_um(bottom_um: float, nz: int, dz_um: float) -> List[float]:
    """Offsets (µm, relative to the z reference) of the Nz recording planes.

    Plane j sits at bottom_um + j*dz_um.  Nz=1 is the single-plane case and
    ignores dz_um (the widget hides dz entirely for Nz=1).
    """
    if nz < 1:
        raise ValueError("require Nz >= 1")
    if nz > 1 and dz_um <= 0:
        raise ValueError("require dz > 0 when Nz > 1")
    return [round(bottom_um + j * dz_um, 6) for j in range(nz)]


def _build_objective_info(objective_store, camera) -> dict:
    """Build the informational `objective:` YAML section.

    Mirrors the dict multi_point_controller.py builds before calling
    _save_acquisition_yaml, adapted to tolerate camera=None (the record widget's
    Save-button path may not always have a live camera reference).
    """
    current_objective = objective_store.current_objective
    objective_dict = getattr(objective_store, "objectives_dict", {}).get(current_objective, {})

    camera_binning = None
    pixel_size_um = None
    if camera is not None and hasattr(camera, "get_binning"):
        camera_binning = list(camera.get_binning())
    if camera is not None and hasattr(camera, "get_pixel_size_binned_um"):
        try:
            pixel_size_um = objective_store.get_pixel_size_factor() * camera.get_pixel_size_binned_um()
        except Exception:
            pixel_size_um = None

    return {
        "name": current_objective,
        "magnification": objective_dict.get("magnification"),
        "pixel_size_um": pixel_size_um,
        "camera_binning": camera_binning,
    }


def _save_record_zstack_yaml(
    params: "RecordZStackAcquisitionParameters",
    yaml_path: str,
    scan_coordinates=None,
    objective_info: dict = None,
) -> None:
    """Save full record/z-stack acquisition settings to *yaml_path*.

    Mirrors multi_point_controller.py's _save_acquisition_yaml for the
    wellplate/flexible widgets, but kept separate: this widget's shape
    (recording phase with fps/duration/z-offset, z-stack as min/max/step,
    two channel lists) doesn't fit the shared builder without polluting it
    with fields the other two widgets have no reason to know about.
    """
    yaml_dict = {
        "acquisition": {
            "experiment_id": params.experiment_id,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "widget_type": "record_zstack",
            "xy_mode": params.xy_mode,
        },
        "objective": objective_info or {},
        "time_series": {
            "nt": params.Nt,
            "delta_t_s": params.dt_s,
        },
        "autofocus": {
            "laser_af": params.use_laser_af,
        },
        "recording": {
            "enabled": params.recording_enabled,
            "channel": _serialize_for_yaml(params.recording_channel) if params.recording_channel else None,
            "fps": params.fps,
            "duration_s": params.duration_s,
            "bottom_z_offset_um": params.recording_bottom_z_offset_um,
            "nz": params.recording_Nz,
            "dz_um": params.recording_dz_um,
        },
        "z_stack": {
            "enabled": params.zstack_enabled,
            "channels": [_serialize_for_yaml(ch) for ch in params.zstack_channels],
            "z_min_um": params.z_min_um,
            "z_max_um": params.z_max_um,
            "z_step_um": params.z_step_um,
        },
    }

    if params.xy_mode == "Select Wells":
        region_centers = getattr(scan_coordinates, "region_centers", {}) or {}
        region_shapes = getattr(scan_coordinates, "region_shapes", {}) or {}
        yaml_dict["wellplate_scan"] = {
            "scan_size_mm": params.scan_size_mm,
            "overlap_percent": params.overlap_percent,
            "regions": [
                {"name": name, "center_mm": _serialize_for_yaml(center), "shape": region_shapes.get(name)}
                for name, center in region_centers.items()
            ],
        }

    # Let OSError/yaml.YAMLError propagate: both real call sites already handle
    # failures appropriately one level up -- run_acquisition()'s snapshot call site
    # wraps this in try/except Exception: log.exception(...) so a failed settings
    # snapshot never aborts a real acquisition, and the Save Settings button
    # handler (_on_save_settings_clicked) wraps this in try/except Exception:
    # QMessageBox.warning(...) so the user is told the save failed. Swallowing the
    # error here made that button's warning dialog unreachable.
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"# Record/Z-Stack Acquisition Parameters - {params.experiment_id}\n\n")
        yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


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
    # Recording planes: plane j at z_ref + recording_bottom_z_offset_um + j*recording_dz_um.
    recording_bottom_z_offset_um: float = 0.0
    recording_Nz: int = 1
    recording_dz_um: float = 1.0
    # z-stack phase
    zstack_enabled: bool = False
    zstack_channels: List[AcquisitionChannel] = field(default_factory=list)
    z_min_um: float = -3.0
    z_max_um: float = 3.0
    z_step_um: float = 1.0
    # XY / well-selection state (needed to save a full reusable settings snapshot)
    xy_mode: str = "Select Wells"
    scan_size_mm: float = 0.1
    overlap_percent: float = 10.0


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
            # Dedupe by name (recording entry wins): channels are identified by
            # name, so a channel used by both phases must not appear twice with
            # different settings in the snapshot.
            channels = []
            seen_names = set()
            candidates = []
            if params.recording_enabled and params.recording_channel is not None:
                candidates.append(params.recording_channel)
            if params.zstack_enabled:
                candidates.extend(params.zstack_channels)
            for ch in candidates:
                if ch.name not in seen_names:
                    seen_names.add(ch.name)
                    channels.append(ch)
            self._microscope.config_repo.save_acquisition_output(
                output_dir=experiment_dir,
                objective=self._objective_store.current_objective,
                channels=channels,
                confocal_mode=self._live_controller.is_confocal_mode(),
            )
        except Exception:
            log.exception("Failed to save acquisition settings snapshot to the experiment directory")

        # Full reusable settings snapshot (superset of acquisition_channels.yaml above,
        # written alongside it — not a replacement; see design doc's "Snapshot files" decision).
        try:
            objective_info = _build_objective_info(self._objective_store, getattr(self._microscope, "camera", None))
            _save_record_zstack_yaml(
                params,
                os.path.join(experiment_dir, "acquisition.yaml"),
                self._scan_coordinates,
                objective_info,
            )
        except Exception:
            log.exception("Failed to save full record_zstack acquisition.yaml snapshot")

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
