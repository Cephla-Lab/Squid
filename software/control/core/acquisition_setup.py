"""Shared acquisition setup helpers.

Helpers used by MultiPointController and RecordZStackController to set up
experiment directories and the pre-warmed job-runner subprocess without
duplicating logic across controller classes.
"""

import os
from datetime import datetime
from typing import Optional, Tuple

import control._def
import squid.acquisition_state
import squid.logging
from control import utils

_log = squid.logging.get_logger(__name__)


def compute_pixel_size_um(objective_store, camera) -> Optional[float]:
    """Compute the physical pixel size in µm from objective and camera metadata.

    Returns the product of the objective's pixel-size factor and the camera's
    binned pixel size in µm, or None if either value is unavailable or an
    exception is raised.

    Args:
        objective_store: ObjectiveStore (or compatible object) with
            ``get_pixel_size_factor() -> Optional[float]``.
        camera: AbstractCamera (or compatible) with
            ``get_pixel_size_binned_um() -> Optional[float]``.

    Returns:
        Pixel size in µm, or None.
    """
    try:
        pixel_factor = objective_store.get_pixel_size_factor()
        sensor_pixel_um = camera.get_pixel_size_binned_um()
        if pixel_factor is not None and sensor_pixel_um is not None:
            return float(pixel_factor) * float(sensor_pixel_um)
        return None
    except Exception:
        return None


def create_experiment_dir(base_path: str, experiment_id: str, add_timestamp: bool = True) -> Tuple[str, str]:
    """Resolve a unique experiment ID and create its output directory.

    With *add_timestamp* (the default) appends a timestamp to *experiment_id*
    (spaces replaced with underscores) to guarantee uniqueness, then creates the
    directory tree under *base_path*.  With ``add_timestamp=False`` the folder is
    *experiment_id* verbatim and a ``FileExistsError`` is raised if it already
    exists (the fluidics protocol runner names session folders itself and never
    reuses one).

    Args:
        base_path: Root directory for all experiments.
        experiment_id: Human-readable experiment name supplied by the user.
        add_timestamp: Append a timestamp to make the folder name unique.

    Returns:
        A ``(resolved_id, dir_path)`` tuple where *resolved_id* is the
        (possibly timestamped) identifier and *dir_path* is the absolute path
        of the newly created directory.
    """
    if add_timestamp:
        resolved_id = experiment_id.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
    else:
        resolved_id = experiment_id
    dir_path = os.path.join(base_path, resolved_id)
    if not add_timestamp and os.path.exists(dir_path):
        raise FileExistsError(dir_path)
    utils.ensure_directory_exists(dir_path)
    return resolved_id, dir_path


class PrewarmedJobRunnerSlot:
    """Owns one pre-warmed JobRunner subprocess kept ready for the next acquisition.

    Shared by MultiPointController and RecordZStackController so the
    warm-up / consume / re-warm / shutdown lifecycle — and its invariants (the
    runner must stay paired with the backpressure values it was created from;
    a consumed slot immediately re-warms for the next run) — lives in one place.

    Known limitation: pre-warming for the NEXT acquisition starts when ``take()``
    is called (i.e. when the CURRENT acquisition begins). If another acquisition
    starts before warm-up finishes (~1.2 s), the worker will wait for the
    subprocess. This only affects rapid-fire manual clicking; real workloads
    (full plate scans, time-lapse with intervals >2 s) are unaffected.
    """

    def __init__(self, logger):
        self._log = logger
        self._runner = None
        self._bp_values = None

    def start(self) -> None:
        """Start a JobRunner subprocess warming up in the background."""
        from control.core.backpressure import create_backpressure_values
        from control.core.job_processing import JobRunner

        self._log.info("Pre-warming job runner subprocess...")
        # Shared backpressure values pair the runner with the worker's
        # BackpressureController for consistent cross-process tracking.
        self._bp_values = create_backpressure_values()
        self._runner = JobRunner(
            bp_pending_jobs=self._bp_values[0],
            bp_pending_bytes=self._bp_values[1],
            bp_capacity_event=self._bp_values[2],
        )
        self._runner.start()

    def take(self):
        """Consume the pre-warmed runner and start warming a fresh one.

        Returns ``(runner, bp_values)``; both are None when multiprocessing is
        off or the slot was already consumed. Use them together or not at all —
        a runner without its matching values would track different counters
        than the BackpressureController built from them.
        """
        runner, bp_values = self._runner, self._bp_values
        self._runner = None
        self._bp_values = None
        if control._def.Acquisition.USE_MULTIPROCESSING:
            self.start()
        return runner, bp_values

    def shutdown_runner(self, runner, timeout_s: float = 1.0, context: str = "") -> None:
        """Shut down *runner* (previously returned by ``take()``), tolerating errors."""
        if runner is not None:
            try:
                runner.shutdown(timeout_s=timeout_s)
            except Exception as e:
                self._log.error(f"Error shutting down pre-warmed runner {context}: {e}")

    def close(self, timeout_s: float = 1.0) -> None:
        """Shut down the currently held pre-warmed runner (application shutdown)."""
        if self._runner is not None:
            self._log.info("Shutting down pre-warmed job runner...")
        self.shutdown_runner(self._runner, timeout_s=timeout_s, context="during close")
        self._runner = None
        self._bp_values = None


def start_run_state_writer(
    experiment_id: str, output_path: str, *, timepoints: int, region_fov_coords: dict, channels: int, z: int
):
    """Start the acquisition watchdog's run.json breadcrumb, or return the null writer.

    The run's size goes in as timepoints x regions x fovs x channels x z images.  A breadcrumb
    failure must never stop an acquisition: it is logged, and the null writer stands in so
    beat()/end() stay safe to call.
    """
    try:
        config_path = (getattr(control._def, "CACHED_CONFIG_FILE_PATH", None) or "").strip() or None
        return squid.acquisition_state.RunStateWriter.start(
            experiment_id=experiment_id,
            pid=os.getpid(),
            config_path=config_path,
            output_path=output_path,
            expected={
                "timepoints": timepoints,
                "regions": len(region_fov_coords),
                "fovs": sum(len(coords) for coords in region_fov_coords.values()),
                "channels": channels,
                "z": z,
            },
        )
    except Exception as e:
        _log.warning(f"Failed to write acquisition watchdog start state: {e}")
        return squid.acquisition_state.NullRunStateWriter()
