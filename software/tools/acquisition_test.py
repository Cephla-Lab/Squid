import logging
import threading
import time
from dataclasses import dataclass

from control.core.auto_focus_controller import AutoFocusController
from control.core.job_processing import CaptureInfo
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import (
    MultiPointControllerFunctions,
    AcquisitionParameters,
    RegionProgressUpdate,
    OverallProgressUpdate,
)
from control.core.scan_coordinates import ScanCoordinates
from control.utils_config import ChannelMode
from squid.abc import CameraFrame
import control.microscope
import squid.logging

log = squid.logging.get_logger("Acquisition test")


@dataclass
class MpcCounts:
    starts: int
    finishes: int
    configs: int
    images: int
    regions: int
    overall_progresses: int
    fovs: int


class MpcTestTracker:
    def __init__(self):
        self._start_count = 0
        self._finish_count = 0
        self._config_count = 0
        self._image_count = 0
        self._region_count = 0
        self._overall_progress_count = 0
        self._fov_count = 0

        self._update_lock = threading.Lock()
        self._last_update_time = time.time()

    @property
    def counts(self):
        with self._update_lock:
            return MpcCounts(
                starts=self._start_count,
                finishes=self._finish_count,
                configs=self._config_count,
                images=self._image_count,
                regions=self._region_count,
                overall_progresses=self._overall_progress_count,
                fovs=self._fov_count,
            )

    @property
    def last_update_time(self):
        with self._update_lock:
            return self._last_update_time

    def _update(self):
        with self._update_lock:
            self._last_update_time = time.time()

    def start_fn(self, params: AcquisitionParameters):
        self._update()
        with self._update_lock:
            self._start_count += 1

    def finish_fn(self):
        self._update()
        with self._update_lock:
            self._finish_count += 1

    def config_fn(self, mode: ChannelMode):
        self._update()
        with self._update_lock:
            self._config_count += 1

    def new_image_fn(self, frame: CameraFrame, info: CaptureInfo):
        self._update()
        with self._update_lock:
            self._image_count += 1

    def fov_fn(self, x_mm: float, y_mm: float):
        self._update()
        with self._update_lock:
            self._fov_count += 1

    def region_progress(self, progress: RegionProgressUpdate):
        self._update()
        with self._update_lock:
            self._region_count += 1

    def overall_progress(self, progress: OverallProgressUpdate):
        self._update()
        with self._update_lock:
            self._overall_progress_count += 1

    def get_callbacks(self) -> MultiPointControllerFunctions:
        return MultiPointControllerFunctions(
            signal_acquisition_start=self.start_fn,
            signal_acquisition_finished=self.finish_fn,
            signal_new_image=self.new_image_fn,
            signal_current_configuration=self.config_fn,
            signal_current_fov=self.fov_fn,
            signal_overall_progress=self.overall_progress,
            signal_region_progress=self.region_progress,
        )


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    # NOTE(imo): This will be expanded as we expand upon `Microscope` functionality.  The expectation is that
    # you can use this to test on real hardware (in addition to the existing unit tests)
    scope: control.microscope.Microscope = control.microscope.Microscope.build_from_global_config(args.simulate)
    scope.setup_hardware()

    scope.home_xyz()

    x_max = scope.stage.get_config().X_AXIS.MAX_POSITION
    y_max = scope.stage.get_config().Y_AXIS.MAX_POSITION
    z_max = scope.stage.get_config().Z_AXIS.MAX_POSITION

    af_controller = AutoFocusController(
        camera=scope.camera,
        stage=scope.stage,
        liveController=scope.live_controller,
        microcontroller=scope.low_level_drivers.microcontroller,
        nl5=None,
    )

    mpc_tracker = MpcTestTracker()
    simple_scan_coordinates = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)
    simple_scan_coordinates.add_single_fov_region("single_fov_1", x_max / 2.0, y_max / 2.0, z_max / 2.0)
    simple_scan_coordinates.add_flexible_region("flexible_region", x_max / 3.0, y_max / 3.0, z_max / 3.0, 2, 2)

    mpc = MultiPointController(
        microscope=scope,
        live_controller=scope.live_controller,
        autofocus_controller=af_controller,
        objective_store=scope.objective_store,
        channel_configuration_manager=scope.channel_configuration_manager,
        callbacks=mpc_tracker.get_callbacks(),
        scan_coordinates=simple_scan_coordinates,
        laser_autofocus_controller=None,
    )

    config_names_to_acquire = [
        "BF LED matrix full",
        "DF LED matrix",
        "Fluorescence 405 nm Ex",
        "Fluorescence 561 nm Ex",
    ]
    mpc.set_selected_configurations(config_names_to_acquire)
    mpc.set_base_path("/tmp")
    mpc.start_new_experiment("stress_experiment")
    mpc.run_acquisition(False)
    update_timeout_s = 5.0

    try:
        while mpc_tracker.counts.finishes <= 0:
            if time.time() - mpc_tracker.last_update_time > update_timeout_s:
                raise TimeoutError(f"Didn't see an acquisition update after {update_timeout_s}, failing.")
            time.sleep(0.1)
    except TimeoutError:
        mpc.request_abort_aquisition()

    counts = mpc_tracker.counts
    log.info(f"After acquisition, counts on tracker are:\n{counts}")
    if counts.finishes <= 0:
        log.error("Acquisition timed out!")

    scope.close()


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Create a Microscope object, then run a basic acquisition")

    ap.add_argument("--runtime", type=float, help="Time, in s, to run the test for.", default=60)
    ap.add_argument("--verbose", action="store_true", help="Turn on debug logging")
    ap.add_argument("--simulate", action="store_true", help="Run with a simulated microscope")

    args = ap.parse_args()

    sys.exit(main(args))
