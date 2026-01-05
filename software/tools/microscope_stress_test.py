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
from control.core.stream_handler import StreamHandlerFunctions
from control.models import AcquisitionChannel
from squid.abc import CameraFrame
import control.microscope
import squid.logging

log = squid.logging.get_logger("Microscope stress test")


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

    def config_fn(self, mode: AcquisitionChannel):
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

    # NOTE(imo): We will probably put this into __init__.  For now, keep it separate just in case it'd break
    # anything in the gui.
    scope.setup_hardware()

    # Do manual homing, and again using the scope helper
    # scope.stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    # scope.stage.home(x=True, y=True, z=False, theta=False, blocking=True)

    scope.home_xyz()

    # Do some moves with stage directly, and the stage helpers
    x_max = scope.stage.get_config().X_AXIS.MAX_POSITION
    y_max = scope.stage.get_config().Y_AXIS.MAX_POSITION
    z_max = scope.stage.get_config().Z_AXIS.MAX_POSITION
    scope.stage.move_x_to(x_max / 2)
    scope.stage.move_y_to(y_max / 2)
    scope.stage.move_z_to(z_max / 5)

    scope.move_to_position(x=x_max / 3, y=y_max / 3, z=z_max / 4)

    scope.camera.start_streaming()
    for config_name in scope.configuration_manager.channel_manager.get_configurations(
        scope.objective_store.current_objective
    ):
        scope.live_controller.set_microscope_mode(config_name)
        scope.illumination_controller.turn_on_illumination()

        if focus_cam := scope.addons.camera_focus:
            try:
                scope.low_level_drivers.microcontroller.turn_on_AF_laser()
                scope.low_level_drivers.microcontroller.wait_till_operation_is_completed()
                while not focus_cam.get_ready_for_trigger():
                    time.sleep(0.001)
                focus_cam.send_trigger()
                focus_frame = focus_cam.read_frame()
            finally:
                scope.low_level_drivers.microcontroller.turn_off_AF_laser()
                scope.low_level_drivers.microcontroller.wait_till_operation_is_completed()

        while not scope.camera.get_ready_for_trigger():
            time.sleep(0.001)
        scope.camera.send_trigger()
        frame = scope.camera.read_frame()

    scope.camera.stop_streaming()

    counts = {"image_to_display": 0, "packet_image_to_write": 0, "signal_new_frame_received": 0}

    def add_count(item):
        nonlocal counts
        counts[item] = counts[item] + 1

    stream_handlers = StreamHandlerFunctions(
        image_to_display=lambda a: add_count("image_to_display"),
        packet_image_to_write=lambda a, i, f: add_count("packet_image_to_write"),
        signal_new_frame_received=lambda: add_count("signal_new_frame_received"),
        accept_new_frame=lambda: True,
    )
    trigger_fps = 2
    desired_frames = 6
    scope.update_camera_functions(stream_handlers)
    scope.live_controller.set_trigger_fps(trigger_fps)
    scope.start_live()
    time.sleep(desired_frames / trigger_fps)
    scope.stop_live()

    if abs(counts["signal_new_frame_received"] - desired_frames) > 1:
        log.warning(
            f"Expected {desired_frames} frames, but only received {counts['signal_new_frame_received']} new frame signals!"
        )

    for label, count in counts.items():
        log.info(f"Counter with {label=} saw {count} counts with {desired_frames=}")

    # Running a really simple acquisition
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
    elif counts.finishes != 1:
        log.error("Saw more than 1 finish")

    if counts.starts != 1:
        log.error("Saw more than 1 start!")

    if counts.fovs * len(config_names_to_acquire) != counts.images:
        log.error("fov*config != images")

    if counts.regions != counts.images:
        log.error("region update does not match image count")

    scope.close()


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Create a Microscope object, then run it through its paces")

    ap.add_argument("--runtime", type=float, help="Time, in s, to run the test for.", default=60)
    ap.add_argument("--verbose", action="store_true", help="Turn on debug logging")
    ap.add_argument("--simulate", action="store_true", help="Run with a simulated microscope")

    args = ap.parse_args()

    sys.exit(main(args))
