import numpy as np
import pytest

from squid.objective_calibration.registration import overlap_crops, phase_shift
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene


def _pair(scene, dx_um, dy_um, shape=(192, 256)):
    hw = FakeCalibrationHardware(
        {"20x": FakeObjective("20x", 20, 0.8, pixel_um=0.5)},
        scene,
        shape=shape,
        start_objective="20x",
        microstep_um=0.0,
    )
    ref = hw.snap("20x", "BF")
    hw.move_xy_to_um(dx_um, dy_um)
    return ref, hw.snap("20x", "BF")


def test_sign_convention_content_moved_right_and_down_is_positive():
    rng = np.random.default_rng(1)
    ref = rng.random((128, 160)).astype(np.float32)
    img = np.roll(np.roll(ref, 7, axis=1), 3, axis=0)  # content moves right by 7, down by 3
    s = phase_shift(ref, img)
    assert s.dx == pytest.approx(7, abs=0.2) and s.dy == pytest.approx(3, abs=0.2)


def test_subpixel_accuracy_on_the_fake():
    # stage +3.3 um in x with M = 0.5*I  =>  content moves by p = -D/px = -6.6 px
    ref, img = _pair(FakeScene.random(), 3.3, -1.1)
    s = phase_shift(ref, img)
    assert s.dx == pytest.approx(-6.6, abs=0.3)
    assert s.dy == pytest.approx(2.2, abs=0.3)
    assert s.response > 0.15 and s.peak_ratio > 2


@pytest.mark.parametrize("shift_px", [10.0, 10.25, 10.5, 10.75, 48.25, 90.75])
def test_subpixel_error_is_pinned(shift_px):
    """Pins the estimator's real accuracy rather than assuming it: cv2.phaseCorrelate's weighted
    centroid is biased by up to ~0.3 px at quarter-pixel fractions and ~0.45 px at 90 px shifts.
    A better sub-pixel refinement must tighten this bound, not loosen it."""
    ref, img = _pair(FakeScene.random(), -shift_px * 0.5, 0.0)
    assert phase_shift(ref, img).dx == pytest.approx(shift_px, abs=0.5)


def test_periodic_scene_is_ambiguous():
    ref, img = _pair(FakeScene.periodic(period_um=10.0), 3.0, 0.0)
    assert phase_shift(ref, img).peak_ratio < 2


def test_flat_scene_has_no_peak():
    ref, img = _pair(FakeScene.flat(), 3.0, 0.0)
    s = phase_shift(ref, img)
    assert s.response < 0.15 or s.peak_ratio < 2


def test_overlap_crops_align_the_shared_content():
    rng = np.random.default_rng(2)
    ref = rng.random((100, 120))
    img = np.zeros_like(ref)
    img[20:, 30:] = ref[:-20, :-30]  # content displaced by (+30, +20)
    a, b = overlap_crops(ref, img, (30, 20))
    assert a.shape == b.shape == (80, 90)
    np.testing.assert_array_equal(a, b)
    a, b = overlap_crops(img, ref, (-30, -20))
    assert a.shape == b.shape == (80, 90)
    np.testing.assert_array_equal(a, b)
