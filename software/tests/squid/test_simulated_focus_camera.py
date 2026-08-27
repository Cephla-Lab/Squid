"""Tests for the Z-responsive simulated focus camera used by the laser AF in simulation."""

import numpy as np
import pytest

import control.utils
import squid.config
from control._def import SpotDetectionMode
from squid.camera.utils import SimulatedFocusCamera


def _make_camera(z_holder, **kwargs):
    config = squid.config.get_autofocus_camera_config().model_copy(update={"rotate_image_angle": None, "flip": None})
    kwargs.setdefault("jitter_px_rms", 0.0)
    return SimulatedFocusCamera(config, get_z_um_fn=lambda: z_holder["z_um"], **kwargs)


def _spot_x(camera, mode=SpotDetectionMode.DUAL_LEFT):
    camera.send_trigger()
    frame = camera.read_frame()
    location = control.utils.find_spot_location(frame, mode=mode)
    assert location is not None
    return location[0]


def test_frame_is_mono8_at_camera_resolution():
    cam = _make_camera({"z_um": 0.0})
    cam.send_trigger()
    frame = cam.read_frame()
    width, height = cam.get_resolution()
    assert frame.dtype == np.uint8
    assert frame.shape == (height, width)


def test_spot_moves_linearly_with_z():
    z = {"z_um": 0.0}
    cam = _make_camera(z)
    x0 = _spot_x(cam)

    z["z_um"] = 20.0
    x1 = _spot_x(cam)

    assert x1 - x0 == pytest.approx(20.0 / 0.4, abs=1.0)  # default 0.4 um/px -> 50 px


def test_z_reference_latches_on_first_frame():
    # Even if the stage boots at some arbitrary Z, the first frame defines "in focus"
    # and puts the primary spot at the horizontal center.
    z = {"z_um": 1234.5}
    cam = _make_camera(z)
    x0 = _spot_x(cam)
    width, _ = cam.get_resolution()
    assert x0 == pytest.approx(width / 2, abs=2.0)

    z["z_um"] = 1234.5 - 10.0
    x1 = _spot_x(cam)
    assert x1 - x0 == pytest.approx(-10.0 / 0.4, abs=1.0)


def test_single_spot_mode():
    z = {"z_um": 0.0}
    cam = _make_camera(z, num_spots=1)
    x0 = _spot_x(cam, mode=SpotDetectionMode.SINGLE)

    z["z_um"] = 8.0
    x1 = _spot_x(cam, mode=SpotDetectionMode.SINGLE)
    assert x1 - x0 == pytest.approx(8.0 / 0.4, abs=1.0)
