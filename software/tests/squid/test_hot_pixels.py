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


def test_compute_frame_stats_robust_sigma():
    # Uniform 100 with a handful of bright outliers. MAD ignores the outliers,
    # so sigma_robust stays ~0 and the median stays at 100.
    frame = np.full((50, 50), 100.0)
    frame[0, 0] = 4000.0
    frame[1, 1] = 4000.0
    stats = hp.compute_frame_stats(frame)
    assert stats.median == 100.0
    assert stats.sigma_robust < 1.0  # outliers do not inflate robust scale
    assert stats.max == 4000.0
    assert stats.min == 100.0


def test_compute_frame_stats_sigma_scales_with_noise():
    rng = np.random.default_rng(0)
    frame = rng.normal(100.0, 10.0, size=(200, 200))
    stats = hp.compute_frame_stats(frame)
    assert abs(stats.sigma_robust - 10.0) < 1.5  # 1.4826*MAD approximates std for gaussian
