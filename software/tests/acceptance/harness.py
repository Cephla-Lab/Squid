"""
Shared harness for the simulation-mode acquisition acceptance suite.

Drives MultiPointController directly (in-process, no GUI, no QApplication),
reusing the wiring helpers from tests/control/test_stubs.py. Assertions in the
scenario files operate on observable artifacts: files on disk and process
state.

Timing thresholds are deliberately generous — CI runners are slow.
"""

import csv
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import control._def
import control.microscope
from control.core.multi_point_controller import MultiPointController

import tests.control.test_stubs as ts
from control.core.multi_point_utils import MultiPointControllerFunctions

# Generous CI-safe waits (seconds).
START_TIMEOUT_S = 60
FINISH_TIMEOUT_S = 300


class AcquisitionTracker:
    """Collects acquisition callbacks so tests can wait on observable events."""

    def __init__(self):
        self.started_event = threading.Event()
        self.finished_event = threading.Event()
        self.first_image_event = threading.Event()
        self.image_count = 0
        self._lock = threading.Lock()

    def get_callbacks(self) -> MultiPointControllerFunctions:
        return MultiPointControllerFunctions(
            signal_acquisition_start=lambda params: self.started_event.set(),
            signal_acquisition_finished=lambda: self.finished_event.set(),
            signal_new_image=self._receive_image,
            signal_current_configuration=lambda config: None,
            signal_current_fov=lambda x, y: None,
            signal_overall_progress=lambda progress: None,
            signal_region_progress=lambda progress: None,
        )

    def _receive_image(self, frame, info):
        with self._lock:
            self.image_count += 1
        self.first_image_event.set()


@dataclass
class AcquisitionHarness:
    """A simulated microscope + MultiPointController pair with cleanup."""

    scope: "control.microscope.Microscope"
    mpc: MultiPointController
    tracker: AcquisitionTracker
    experiment_dirs: List[Path] = field(default_factory=list)

    def close(self):
        try:
            self.mpc.close()
        finally:
            self.scope.close()

    def new_experiment(self, base_path: Path, experiment_id: str) -> None:
        """Start a fresh experiment; the timestamped directory it creates is
        recorded in self.experiment_dirs.

        start_new_experiment() serializes 'acquisition parameters.json' from
        the controller's CURRENT settings — configure NZ/Nt/deltaZ/channels
        before calling this if the test asserts on that file's contents."""
        base_path.mkdir(parents=True, exist_ok=True)
        before = set(base_path.iterdir())
        self.mpc.set_base_path(str(base_path))
        self.mpc.start_new_experiment(experiment_id)
        created = [p for p in base_path.iterdir() if p not in before and p.is_dir()]
        assert len(created) == 1, f"expected exactly one new experiment dir, found {created}"
        self.experiment_dirs.append(created[0])

    @property
    def experiment_dir(self) -> Path:
        assert self.experiment_dirs, "new_experiment() has not been called"
        return self.experiment_dirs[-1]

    def add_fov_grid(self, region_id: str = "region0", nx: int = 2, ny: int = 2) -> None:
        """Add an nx x ny FOV grid at a stage position guaranteed to be within
        limits (coordinates outside stage limits are silently dropped)."""
        stage_config = self.mpc.stage.get_config()
        x = stage_config.X_AXIS.MIN_POSITION + 1.0
        y = stage_config.Y_AXIS.MIN_POSITION + 1.0
        z = (stage_config.Z_AXIS.MIN_POSITION + stage_config.Z_AXIS.MAX_POSITION) / 2.0
        self.mpc.scanCoordinates.add_flexible_region(region_id, x, y, z, nx, ny, 0)
        fovs = self.mpc.scanCoordinates.region_fov_coordinates.get(region_id, [])
        assert len(fovs) == nx * ny, f"region {region_id}: expected {nx * ny} FOVs, got {len(fovs)}"

    def select_channels(self, count: int) -> List[str]:
        objective = self.scope.objective_store.current_objective
        names = [c.name for c in self.mpc.liveController.get_channels(objective)]
        assert len(names) >= count, f"need {count} channels, simulation config provides {names}"
        selected = names[:count]
        self.mpc.set_selected_configurations(selected)
        return selected

    def run_and_wait(self, timeout_s: float = FINISH_TIMEOUT_S) -> None:
        self.mpc.run_acquisition()
        assert self.tracker.started_event.wait(START_TIMEOUT_S), "acquisition did not start"
        assert self.tracker.finished_event.wait(timeout_s), f"acquisition did not finish within {timeout_s}s"


def make_harness() -> AcquisitionHarness:
    """Build a fresh simulated microscope wired to a MultiPointController.

    Callers own cleanup: call harness.close() (use the `harness` fixture or
    try/finally) so JobRunner subprocesses and semaphores are released.
    """
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tracker = AcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.get_callbacks())
    mpc.scanCoordinates.clear_regions()
    return AcquisitionHarness(scope=scope, mpc=mpc, tracker=tracker)


def read_coordinates_csv(path: Path) -> List[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def timepoint_dir(experiment_dir: Path, timepoint: int) -> Path:
    # FILE_ID_PADDING is 0 by default, so per-timepoint dirs are "0", "1", ...
    return experiment_dir / str(timepoint).zfill(control._def.FILE_ID_PADDING)


def list_image_files(directory: Path) -> List[Path]:
    """Individual-images mode: per-image files named
    {region}_{fov}_{z}_{channel}.<ext> directly in the timepoint dir."""
    return sorted(p for p in directory.glob("*.tiff") if p.is_file()) + sorted(
        p for p in directory.glob("*.bmp") if p.is_file()
    )
