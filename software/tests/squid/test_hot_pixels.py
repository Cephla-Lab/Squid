import numpy as np
import pytest

from squid.config import CameraPixelFormat
from squid.camera import hot_pixels as hp


def test_max_value_for_mono_formats():
    assert hp.max_value_for_pixel_format(CameraPixelFormat.MONO8) == 255
    assert hp.max_value_for_pixel_format(CameraPixelFormat.MONO12) == 4095
    assert hp.max_value_for_pixel_format(CameraPixelFormat.MONO16) == 65535


def test_max_value_rejects_color_formats():
    with pytest.raises(ValueError):
        hp.max_value_for_pixel_format(CameraPixelFormat.RGB24)


def test_default_thresholds():
    t = hp.DefectThresholds()
    assert t.sigma_n == 5.0
    assert t.abs_threshold_dn is None
    assert t.stuck_high_frac == 0.99
    assert t.dead_max_dn == 1
