import time

from pyqtgraph import image
from sympy.physics.units import micro

import control.camera_toupcam
import control.microcontroller
import squid.logging

class ImageStatCollector:
    def __init__(self, expected_count):
        self.expected_count = expected_count
        self.log = squid.logging.get_logger("image collector")

        self.receive_timestamps = []
        self.trigger_timestamps = []

    def record_trigger(self):
        self.trigger_timestamps.append(time.time())
        self.log.debug(f"Trigger {len(self.trigger_timestamps)} / {self.expected_count}")

    def get_streaming_callback(self):
        def streaming_cb(camera):
            nonlocal self
            self.receive_timestamps.append(time.time())
            self.log.debug(f"Received {len(self.receive_timestamps)} / {self.expected_count}")


def main(args):
    capture_count = args.count
    exposure_ms = args.exposure
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
    image_collector = ImageStatCollector(capture_count)

    camera.set_callback(image_collector.get_streaming_callback())
    camera.enable_callback()

    inter_trigger_sleep_ms = camera.get_full_frame_time()
    log.info(f"Starting camera streaming, then triggering {image_collector.expected_count} triggers with {inter_trigger_sleep_ms} [ms] sleeps between them.")
    camera.start_streaming()

    for i in range(image_collector.expected_count):
        microcontroller.send_hardware_trigger(control_illumination=True, illumination_on_time_us=exposure_ms * 1000)

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="A tool for testing toupcam external triggering and image streaming.")
    ap.add_argument("c,count", type=int, default=100, help="How many frames to grab for this test.")
    ap.add_argument("e,exposure", type=float, default=10, help="The exposure time, in ms, to use for each capture.")
    # NOTE(imo): I don't think this default is actually a toupcam model, but it's the model name used in the Squid+
    # config when we're using toupcam cameras.
    ap.add_argument("camera_model", type=str, default="MER2-1220-32U3M")
    ap.add_argument("pixel_format", type=str, default="MONO16")
    ap.add_argument("microcontroller_version", type=str, default="Teensy")
    parsed_args = ap.parse_args()

    sys.exit(main(parsed_args))
