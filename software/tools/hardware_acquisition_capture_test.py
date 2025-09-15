import logging
import math
import threading
import time

import control.microcontroller
import squid.camera.utils
import squid.config
import squid.logging
from squid.abc import CameraFrame, CameraAcquisitionMode

log = squid.logging.get_logger("camera stress test")


class Stats:
    def __init__(self):
        self.callback_frame_count = 0
        self.last_callback_frame_time = time.time()
        self._last_report_frame_count = -1

        self.start_time = time.time()
        self._update_lock = threading.Lock()

    def start(self):
        with self._update_lock:
            self.callback_frame_count = 0
            self.last_callback_frame_time = time.time()

            self.read_frame_count = 0
            self.last_read_frame_time = time.time()

            self.start_time = time.time()

    def callback_frame(self):
        with self._update_lock:
            self.callback_frame_count += 1
            self.last_callback_frame_time = time.time()

    def _summary_line(self, label, count, last_frame):
        elapsed = last_frame - self.start_time
        return f"{label}: {count} in {elapsed:.3f} [s] ({count / elapsed:.3f} [Hz])\n"

    def report_if_on_interval(self, interval):
        with self._update_lock:
            if self.callback_frame_count % interval == 0 and self._last_report_frame_count != self.callback_frame_count:
                self._last_report_frame_count = self.callback_frame_count
                self.report()

    def report(self):
        log.info(self)

    def __str__(self):
        return (
            f"Stats (elapsed = {time.time() - self.start_time} [s]):\n"
            f"  {self._summary_line('callback', self.callback_frame_count, self.last_callback_frame_time)}"
        )


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    microcontroller = control.microcontroller.Microcontroller(
        serial_device=control.microcontroller.get_microcontroller_serial_device()
    )

    def hw_trigger(illum_time: float) -> bool:
        microcontroller.send_hardware_trigger(False)

        return True

    def strobe_delay_fn(strobe_time_ms: float):
        microcontroller.set_strobe_delay_us(int(strobe_time_ms * 1000))

        return True

    # Special case for simulated camera
    if args.camera.lower() == "simulated":
        log.info("Using simulated camera!")
        camera_type = squid.config.CameraVariant.GXIPY  # Not actually used
        simulated = True
    else:
        camera_type = squid.config.CameraVariant.from_string(args.camera)
        simulated = False

    if not camera_type:
        log.error(f"Invalid camera type '{args.camera}'")
        return 1

    default_config = squid.config.get_camera_config()
    force_this_camera_config = default_config.model_copy(update={"camera_type": camera_type})

    cam = squid.camera.utils.get_camera(
        force_this_camera_config, simulated, hw_trigger_fn=hw_trigger, hw_set_strobe_delay_ms_fn=strobe_delay_fn
    )

    stats = Stats()

    def frame_callback(frame: CameraFrame):
        stats.callback_frame()

    log.info("Registering frame callback...")
    cam.add_frame_callback(frame_callback)
    cam.set_exposure_time(args.exposure)

    # TODO(imo): When cameras officially support LEVEL_TRIGGER we need to add and implement that in the cameras.  For
    # now, always use HARDWARE_TRIGGER and figure it out behind the scenes.
    cam.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)

    # We just want some illumination enabled so we can see if the firmware is doing its job
    microcontroller.set_illumination_led_matrix(0, r=0, g=0, b=100)

    log.info("Starting streaming...")
    cam.start_streaming()
    stats.start()

    end_time = time.time() + args.max_runtime

    log.info(
        (
            f"Camera Info:\n"
            f"  Type: {args.camera}\n"
            f"  Resolution: {cam.get_resolution()}\n"
            f"  Exposure Time: {cam.get_exposure_time()} [ms]\n"
            f"  Strobe Time: {cam.get_strobe_time()} [ms]\n"
        )
    )

    try:

        if args.batch_mode:
            frame_time = cam.get_total_frame_time()
            triggered_ms = args.trigger_ms
            not_triggered_ms = int(math.ceil(frame_time) - math.ceil(triggered_ms) + round(args.extra_not_triggered_ms))

            microcontroller.set_continuous_triggering(args.frame_count, triggered_ms, not_triggered_ms, 0)
        while time.time() < end_time and stats.callback_frame_count < args.frame_count:
            if not args.batch_mode and cam.get_ready_for_trigger():
                log.debug("Sending trigger...")
                cam.send_trigger()
                log.debug("Trigger sent....")

            stats.report_if_on_interval(args.report_interval)
            time.sleep(0.0001)

    finally:
        if args.batch_mode:
            microcontroller.cancel_continuous_triggering()
        log.info("Stopping streaming...")
        cam.stop_streaming()

    stats.report()
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="hammer a camera to test it.")

    ap.add_argument("--frame_count", type=float, help="The number of frames to try to capture", default=100)
    ap.add_argument(
        "--batch_mode", action="store_true", help="Ask the microcontroller to trigger all the frames (in a row) for us."
    )
    ap.add_argument("--exposure", type=float, help="The exposure time in ms", default=1)
    ap.add_argument("--report_interval", type=int, help="Report every this many frames captured.", default=100)
    ap.add_argument("--verbose", action="store_true", help="Turn on debug logging")
    ap.add_argument(
        "--camera",
        type=str,
        required=True,
        choices=["hamamatsu", "toupcam", "gxipy", "simulated"],
        help="The type of camera to create and use for this test.",
    )
    ap.add_argument("--max_runtime", type=float, help="The maximum runtime before timing out.", default=60)
    ap.add_argument(
        "--extra_not_triggered_ms", type=int, help="Extra time, in ms, to add between triggers.", default=0
    )
    ap.add_argument("--trigger_ms", type=int, help="The time to spend in trigger state", default=1)
    args = ap.parse_args()

    sys.exit(main(args))
