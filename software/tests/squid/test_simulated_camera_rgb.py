import numpy as np

import squid.config
from squid.camera.utils import SimulatedCamera
from squid.config import CameraPixelFormat


def make_sim(pixel_format):
    config = squid.config.get_camera_config().model_copy(
        update={"serial_number": "SIM-RGB", "default_pixel_format": pixel_format}
    )
    return SimulatedCamera(config, hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None)


def test_rgb24_frames_are_3_channel_uint8():
    cam = make_sim(CameraPixelFormat.RGB24)
    cam.send_trigger()
    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 3 and frame.frame.shape[2] == 3
    assert frame.frame.dtype == np.uint8
    assert frame.is_color()
    assert cam.is_color is True


def test_rgb48_frames_are_3_channel_uint16():
    cam = make_sim(CameraPixelFormat.RGB48)
    cam.send_trigger()
    frame = cam.read_camera_frame()
    assert frame.frame.ndim == 3 and frame.frame.shape[2] == 3
    assert frame.frame.dtype == np.uint16


def test_rgb_formats_advertised():
    cam = make_sim(CameraPixelFormat.RGB24)
    formats = cam.get_available_pixel_formats()
    assert CameraPixelFormat.RGB24 in formats
    assert CameraPixelFormat.RGB48 in formats
