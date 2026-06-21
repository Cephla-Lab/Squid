import numpy as np

import squid.camera.utils
import squid.config
from squid.abc import CameraAcquisitionMode
from squid.config import CameraPixelFormat
from squid.camera import hot_pixel_capture as cap


def _sim_camera():
    config = squid.config.get_camera_config().model_copy(
        update={"rotate_image_angle": None, "flip": None, "default_pixel_format": CameraPixelFormat.MONO12}
    )
    camera = squid.camera.utils.get_camera(config, simulated=True)
    camera.set_pixel_format(CameraPixelFormat.MONO12)
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()
    return camera


def test_capture_dark_stack_shapes_and_count():
    camera = _sim_camera()
    try:
        stack = cap.capture_dark_stack(camera, exposure_ms=1.0, n_frames=5, warmup_frames=1)
    finally:
        camera.stop_streaming()
    assert stack is not None
    assert stack.n_frames == 5
    h, w = camera.get_resolution()[1], camera.get_resolution()[0]
    assert stack.mean.shape == stack.min_proj.shape == stack.max_proj.shape
    # min projection never exceeds max projection anywhere
    assert np.all(stack.min_proj <= stack.max_proj)


def test_capture_dark_stack_stops_when_requested():
    camera = _sim_camera()
    try:
        stack = cap.capture_dark_stack(camera, exposure_ms=1.0, n_frames=100, warmup_frames=0, should_stop=lambda: True)
    finally:
        camera.stop_streaming()
    assert stack is None
