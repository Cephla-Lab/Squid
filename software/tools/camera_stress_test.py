import logging
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

        self.read_frame_count = 0
        self.last_read_frame_time = time.time()
        self.total_bytes_received = 0

        self.start_time = time.time()
        self._update_lock = threading.Lock()

    def start(self):
        with self._update_lock:
            self.callback_frame_count = 0
            self.last_callback_frame_time = time.time()

            self.read_frame_count = 0
            self.last_read_frame_time = time.time()

            self.start_time = time.time()

    def callback_frame(self, cf: CameraFrame):
        with self._update_lock:
            self.callback_frame_count += 1
            self.last_callback_frame_time = time.time()
            self.total_bytes_received += cf.frame.nbytes

    def read_frame(self):
        with self._update_lock:
            self.read_frame_count += 1
            self.last_read_frame_time = time.time()

    def _summary_line(self, label, count, last_frame):
        elapsed = last_frame - self.start_time
        return f"{label}: {count} in {elapsed:.3f} [s] ({count / elapsed:.3f} [Hz])\n"

    def report_if_on_interval(self, interval):
        if self.read_frame_count % interval == 0:
            log.info(self)

    def __str__(self):
        return (
            f"Stats (elapsed = {time.time() - self.start_time} [s]:\n"
            f"  {self._summary_line('callback', self.callback_frame_count, self.last_callback_frame_time)}"
            f"  {self._summary_line('read frame', self.read_frame_count, self.last_read_frame_time)}"
            f"  Total Bytes Received: {self.total_bytes_received} ({self.total_bytes_received / self.callback_frame_count} / frame)"
        )


def parse_binning(binning_arg: str):
    """
    Should be a comma separated 2 tuple of int.
    """
    parts = binning_arg.split(",")

    if len(parts) != 2:
        return None

    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    software_trigger = not args.hardware_trigger
    use_trigger = software_trigger or args.hardware_trigger

    if args.hardware_trigger:
        microcontroller = control.microcontroller.Microcontroller(
            serial_device=control.microcontroller.get_microcontroller_serial_device()
        )

        def hw_trigger(illum_time: float) -> bool:
            microcontroller.send_hardware_trigger(False)

            return True

        def strobe_delay_fn(strobe_time_ms):
            microcontroller.set_strobe_delay_us(int(strobe_time_ms * 1000))

    else:
        hw_trigger = None
        strobe_delay_fn = None

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

    binning = parse_binning(args.binning)
    if not binning:
        log.error(f"Invalid binning argument: {args.binning}")
        return 1

    default_config = squid.config.get_camera_config()
    force_this_camera_config = default_config.model_copy(
        update={"camera_type": camera_type, "default_binning": binning}
    )

    cam = squid.camera.utils.get_camera(
        force_this_camera_config, simulated, hw_trigger_fn=hw_trigger, hw_set_strobe_delay_ms_fn=strobe_delay_fn
    )

    stats = Stats()

    def frame_callback(frame: CameraFrame):
        stats.callback_frame(frame)

    log.info("Registering frame callback...")
    callback_id = cam.add_frame_callback(frame_callback)

    cam.set_exposure_time(args.exposure)

    if software_trigger:
        cam.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    elif args.hardware_trigger:
        cam.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)

    log.info("Starting streaming...")
    cam.start_streaming()
    stats.start()

    end_time = time.time() + args.runtime

    log.info(
        (
            f"Camera Info:\n"
            f"  Type: {args.camera}\n"
            f"  Resolution: {cam.get_resolution()}\n"
            f"  Exposure Time: {cam.get_exposure_time()} [ms]\n"
            f"  Binning: {cam.get_binning()}\n"
            f"  Strobe Time: {cam.get_strobe_time()} [ms]\n"
        )
    )

    try:
        while time.time() < end_time:
            if use_trigger and cam.get_ready_for_trigger():
                log.debug("Sending trigger...")
                cam.send_trigger()
                log.debug("Trigger sent....")

            read_frame = cam.read_camera_frame()
            log.debug(f"Read frame with id={read_frame.frame_id}")
            stats.read_frame()

            stats.report_if_on_interval(args.report_interval)

    finally:
        log.info("Stopping streaming...")
        cam.stop_streaming()
        return 0


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="hammer a camera to test it.")

    ap.add_argument("--runtime", type=float, help="Time, in s, to run the test for.", default=60)
    ap.add_argument(
        "--hardware_trigger",
        action="store_true",
        help="Use the hardware trigger, not software trigger (requires microcontroller)",
    )
    ap.add_argument("--exposure", type=float, help="The exposure time in ms", default=1)
    ap.add_argument(
        "--binning",
        type=str,
        help="A comma separated 2-tuple to use as the camera binning.  EG 1,1 or 2,2",
        default="1,1",
    )
    ap.add_argument("--report_interval", type=int, help="Report every this many frames captured.", default=100)
    ap.add_argument("--verbose", action="store_true", help="Turn on debug logging")
    ap.add_argument(
        "--camera",
        type=str,
        required=True,
        choices=["hamamatsu", "toupcam", "gxipy", "simulated"],
        help="The type of camera to create and use for this test.",
    )

    args = ap.parse_args()

    sys.exit(main(args))
