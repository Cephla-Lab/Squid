import logging
import time
from collections import Counter

import control.microscope
import squid.logging
from control.core.stream_handler import StreamHandlerFunctions

log = squid.logging.get_logger("Microscope stress test")


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
    scope.stage.home(x=False, y=False, z=True, theta=False, blocking=True)
    scope.stage.home(x=True, y=True, z=False, theta=False, blocking=True)

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
