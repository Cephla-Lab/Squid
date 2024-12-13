import logging
import time

import control.camera_toupcam
import control.microcontroller
import squid.logging

class ImageStatCollector:
    def __init__(self, expected_count, camera: control.camera_toupcam.Camera):
        self.expected_count = expected_count
        self.log = squid.logging.get_logger("image collector")
        self.camera = camera

        self.receive_timestamps = []
        self.trigger_timestamps = []

    def record_trigger(self):
        self.trigger_timestamps.append(time.time())
        self.log.debug(f"Trigger {len(self.trigger_timestamps)} / {self.expected_count}")

    def get_streaming_callback(self):
        def streaming_cb(camera):
            nonlocal self
            now = time.time()
            self.receive_timestamps.append(now)
            dt = now - self.trigger_timestamps[-1]
            frame_time_s = self.camera.get_full_frame_time() / 1000.0
            self.log.debug(
                f"Received {len(self.receive_timestamps)} / {self.expected_count}"
                f"  {dt} [s] since last trigger)"
                f"  {frame_time_s} [s] frame time"
                f"  {dt - frame_time_s} [s] extra over frame time")

        return streaming_cb

    def get_trigger_count(self):
        return len(self.trigger_timestamps)

    def get_received_count(self):
        return len(self.receive_timestamps)

    def get_summary(self):
        runtime = max(self.receive_timestamps) - min(self.trigger_timestamps)
        return (
            f"expected count: {self.expected_count}\n"
            f"triggers sent : {self.get_trigger_count()}\n"
            f"received count: {self.get_received_count()}\n"
            f"dt first trigger to last received: {runtime} [s]\n"
            f"frame rate (only received): {self.get_received_count() / runtime} [Hz]"
        )


def main(args):
    capture_count = args.count
    exposure_ms = args.exposure

    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    log = squid.logging.get_logger("")

    log.info(f"Using camera_model='{args.camera_model}' with pixel_format='{args.pixel_format}'")
    sn_camera_main = control.camera_toupcam.get_sn_by_model(args.camera_model)
    camera: control.camera_toupcam.Camera = control.camera_toupcam.Camera(sn=sn_camera_main)
    camera.open()
    camera.set_pixel_format(args.pixel_format)

    log.info(f"Using microcontroller version='{args.microcontroller_version}'")
    microcontroller: control.microcontroller.Microcontroller = control.microcontroller.Microcontroller(version=args.microcontroller_version)

    log.info(f"Turning on hardware triggering.")
    camera.set_hardware_triggered_acquisition()

    log.info(f"Setting camera exposure time to {exposure_ms} [ms]")
    camera.set_exposure_time(exposure_ms)

    # This is copying what we do in the liveController
    # First this recalcs strobe_delay_us based on the camera properties *and* exposure time, so exposure time
    # must be set first.
    camera.calculate_hardware_trigger_arguments()
    # Since we're triggering and lighting via the micro, we need to tell it the strobe delay so it
    # can delay the lighting after the camera trigger.
    # NOTE/TODO(imo): It looks like calculate_hardware_trigger_arguments uses the full frame time, not frame_time - exposure_time. Is that right?
    microcontroller.set_strobe_delay_us(camera.strobe_delay_us)
    log.info(f"With exposure time of {exposure_ms} [ms], we actually set the camera exposure to {camera.get_full_frame_time()} [ms] for rolling shutter compensation.")
    image_collector = ImageStatCollector(capture_count, camera)

    camera.set_callback(image_collector.get_streaming_callback())
    camera.enable_callback()

    inter_trigger_sleep_ms = camera.get_full_frame_time() + args.extra_inter_sleep_ms
    log.info(f"Starting camera streaming, then triggering {image_collector.expected_count} triggers with {inter_trigger_sleep_ms} [ms] sleeps between them.")
    camera.start_streaming()

    for i in range(image_collector.expected_count):
        # Arbitrarily use 10 x the full frame time as a timeout, and add a 250ms offset so we
        # don't get too small with small frame times.
        trigger_timeout = (250 + 10 * camera.get_full_frame_time()) / 1000.0
        timeout_time = time.time() + trigger_timeout
        while not camera.is_ready_for_trigger():
            if time.time() > timeout_time:
                raise TimeoutError(f"Timed out waiting for image acquisition. {timeout_time} [s] timeout.")
            time.sleep(0.001)
        camera.mark_triggered()
        microcontroller.send_hardware_trigger(control_illumination=True, illumination_on_time_us=exposure_ms * 1000)
        image_collector.record_trigger()
        time.sleep(inter_trigger_sleep_ms / 1000.0)

    # Make sure to wait for the last frame. Once we've gotten
    # word from the camera that we can send another trigger, that means all the images
    # are here.

    time.sleep(0.1 + 3 * camera.get_full_frame_time()/1000)

    if args.print_camera_settings:
        log.info(f"Camera settings: {camera.get_settings_summary()}")

    log.info(f"Done, summarizing results:\n{image_collector.get_summary()}")
if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="A tool for testing toupcam external triggering and image streaming.")
    ap.add_argument("-c", "--count", type=int, default=100, help="How many frames to grab for this test.")
    ap.add_argument("-e", "--exposure", type=float, default=10, help="The exposure time, in ms, to use for each capture.")
    # NOTE(imo): I don't think this default is actually a toupcam model, but it's the model name used in the Squid+
    # config when we're using toupcam cameras.
    ap.add_argument("--camera_model", type=str, default="MER2-1220-32U3M")
    ap.add_argument("--pixel_format", type=str, default="MONO16")
    ap.add_argument("--microcontroller_version", type=str, default="Teensy")
    ap.add_argument("--extra_inter_sleep_ms", type=float, default=0, help="Extra number of ms to sleep after a trigger (in addition to frame time)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--print_camera_settings", action="store_true")

    parsed_args = ap.parse_args()

    sys.exit(main(parsed_args))
