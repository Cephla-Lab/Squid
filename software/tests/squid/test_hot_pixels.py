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


def test_darkness_check_passes_on_dark_frame():
    frame = np.full((20, 20), 5.0)  # near black
    assert hp.darkness_check(frame, black_level=2.0, max_value=4095) is None


def test_darkness_check_warns_on_bright_frame():
    frame = np.full((20, 20), 2000.0)  # ~half of full scale
    msg = hp.darkness_check(frame, black_level=2.0, max_value=4095)
    assert msg is not None
    assert "dark" in msg.lower()


def test_darkness_check_boundary_at_threshold():
    threshold = 2.0 + 0.25 * 4095  # 1025.75
    frame = np.full((20, 20), threshold)
    assert hp.darkness_check(frame, black_level=2.0, max_value=4095) is None


def _dark_stack_with_defects():
    """Build mean/min/max projections of a 64x64, 12-bit dark sensor with known defects."""
    shape = (64, 64)
    max_value = 4095
    mean = np.full(shape, 100.0)  # uniform dark floor at 100 DN
    min_proj = np.full(shape, 90, dtype=np.uint16)
    max_proj = np.full(shape, 110, dtype=np.uint16)

    # Hot (statistical + absolute): elevated mean, normal min/max-ish
    mean[10, 20] = 1500.0
    max_proj[10, 20] = 1500

    # Stuck-high: always near max
    mean[30, 40] = 4090.0
    min_proj[30, 40] = 4090
    max_proj[30, 40] = 4095

    # Dead/stuck-low: never rises above 0 while floor is 100
    mean[50, 5] = 0.0
    min_proj[50, 5] = 0
    max_proj[50, 5] = 0
    return mean, min_proj, max_proj, max_value


def test_detect_defects_finds_each_type():
    mean, min_proj, max_proj, max_value = _dark_stack_with_defects()
    thresholds = hp.DefectThresholds(sigma_n=5.0, abs_threshold_dn=1000)
    res = hp.detect_defects(mean, min_proj, max_proj, max_value, thresholds)

    assert res.masks[hp.DefectType.HOT_STATISTICAL][10, 20]
    assert res.masks[hp.DefectType.HOT_ABSOLUTE][10, 20]
    assert res.masks[hp.DefectType.STUCK_HIGH][30, 40]
    assert res.masks[hp.DefectType.DEAD_LOW][50, 5]
    # exact counts: one pixel each
    assert res.count(hp.DefectType.STUCK_HIGH) == 1
    assert res.count(hp.DefectType.DEAD_LOW) == 1
    assert res.combined_count() == 3  # stuck-high pixel is counted once in combined
    # coords are (x, y)
    hot_stat = res.coords(hp.DefectType.HOT_STATISTICAL).tolist()
    assert [20, 10] in hot_stat  # injected hot pixel
    assert [40, 30] in hot_stat  # stuck-high pixel also reads bright
    # flagged value recorded
    assert res.flagged_values[(20, 10)] == 1500.0
    assert res.flagged_values[(40, 30)] == 4090.0


def test_detect_defects_absolute_off_by_default():
    mean, min_proj, max_proj, max_value = _dark_stack_with_defects()
    res = hp.detect_defects(mean, min_proj, max_proj, max_value, hp.DefectThresholds())
    assert res.count(hp.DefectType.HOT_ABSOLUTE) == 0


def test_detect_defects_dead_requires_floor_above_threshold():
    # If the whole frame is ~0 (no real dark floor), do not flag everything as dead.
    shape = (16, 16)
    mean = np.zeros(shape)
    min_proj = np.zeros(shape, dtype=np.uint16)
    max_proj = np.zeros(shape, dtype=np.uint16)
    res = hp.detect_defects(mean, min_proj, max_proj, 4095, hp.DefectThresholds())
    assert res.count(hp.DefectType.DEAD_LOW) == 0


def _condition(temp, exp):
    mean, min_proj, max_proj, max_value = _dark_stack_with_defects()
    res = hp.detect_defects(mean, min_proj, max_proj, max_value, hp.DefectThresholds())
    return hp.ConditionResult(temperature_c=temp, actual_temperature_c=temp, exposure_ms=exp, n_frames=10, result=res)


def test_aggregate_sweep_tracks_pixels_and_conditions():
    results = [_condition(-10.0, 100.0), _condition(-10.0, 500.0)]
    summary = hp.aggregate_sweep(results)

    assert len(summary.per_condition) == 2
    # the stuck-high pixel (40, 30) was flagged in both conditions
    stuck = [p for p in summary.pixels if (p.x, p.y) == (40, 30)]
    assert len(stuck) == 1
    assert "stuck_high" in stuck[0].types
    assert len(stuck[0].conditions) == 2


def test_condition_label_handles_ambient():
    assert "ambient" in hp.condition_label(None, 100.0)
    assert "100" in hp.condition_label(None, 100.0)
    assert "-10" in hp.condition_label(-10.0, 100.0)
