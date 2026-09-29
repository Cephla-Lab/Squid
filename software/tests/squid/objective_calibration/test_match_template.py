import math

import numpy as np
import pytest

from squid.objective_calibration.registration import (
    MAX_SELF_SIMILARITY,
    autocorrelation,
    high_pass,
    match_template,
    runner_up_ratio,
    self_similarity,
    template_shape,
)
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

PARCENTRIC_20X = (12.0, -7.0)  # um: offset_20x - offset_4x in the fake's stage-frame ground truth


def _rot(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])


# The camera's orientation is the same for every objective: only the scale differs (spec C §5).
GEOMETRIES = {
    "inverted": np.eye(2),
    "upright": np.diag([1.0, -1.0]),
    "rotated": _rot(0.5),
    "swap": np.array([[0.0, 1.0], [1.0, 0.0]]),
}


def _pair(orientation, parcentric_20x=PARCENTRIC_20X, scene=None, shape=(192, 256)):
    objectives = {
        "4x": FakeObjective("4x", 4, 0.13, matrix_um_per_px=1.6 * orientation),
        "20x": FakeObjective("20x", 20, 0.8, matrix_um_per_px=0.32 * orientation, parcentric_um=parcentric_20x),
    }
    hw = FakeCalibrationHardware(
        objectives, scene or FakeScene.random(), shape=shape, start_objective="4x", microstep_um=0.0
    )
    image_4x = hw.snap("4x", "BF")
    hw.switch_objective("20x")
    return image_4x, hw.snap("20x", "BF")


@pytest.mark.parametrize("name", list(GEOMETRIES))
def test_finds_where_the_20x_view_sits_in_the_4x_frame(name):
    m4 = 1.6 * GEOMETRIES[name]
    image_4x, image_20x = _pair(GEOMETRIES[name])
    match = match_template(image_4x, image_20x, 0.32 / 1.6)
    # the point centred under 20x appears in the 4x image at delta_px = -M_4x^-1 (offset_20x - offset_4x)
    expected = -np.linalg.inv(m4) @ np.array(PARCENTRIC_20X)
    assert (match.dx, match.dy) == pytest.approx(tuple(expected), abs=0.2)
    assert match.score > 0.5 and not match.at_edge
    assert match.runner_up_ratio < 0.5  # the best distinct peak outside the match's lobe (measured 0.30-0.33)
    assert match.search_px == (102.5, 77.0)  # the 51 x 38 px view can sit anywhere in the 256 x 192 frame
    # and M_4x @ delta_px is the stage move that centres that point under 4x: its negative is the offset
    np.testing.assert_allclose(-(m4 @ [match.dx, match.dy]), PARCENTRIC_20X, atol=0.5)


@pytest.mark.parametrize("fraction", [0.0, 0.25, 0.5, 0.75])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_subpixel_error_is_pinned(fraction, axis):
    """The 3x3 quadratic peak is good to 0.2 px of the lower magnification on the fake (measured max
    0.07 px on this frame); a better refinement must tighten this bound, not loosen it. With
    cv2.resize in place of the centred resampling the y error was 0.33 px on this 192-row frame."""
    shift_px = 6.0 + fraction
    parcentric = (1.6 * shift_px, 0.0) if axis == "x" else (0.0, -1.6 * shift_px)
    image_4x, image_20x = _pair(np.eye(2), parcentric_20x=parcentric)
    match = match_template(image_4x, image_20x, 0.2)
    expected = (-shift_px, 0.0) if axis == "x" else (0.0, shift_px)
    assert (match.dx, match.dy) == pytest.approx(expected, abs=0.2)


def test_a_periodic_scene_is_ambiguous():
    image_4x, image_20x = _pair(np.eye(2), scene=FakeScene.periodic(period_um=16.0))
    assert match_template(image_4x, image_20x, 0.2).runner_up_ratio >= 0.8


def test_a_flat_scene_scores_low():
    image_4x, image_20x = _pair(np.eye(2), scene=FakeScene.flat())
    assert match_template(image_4x, image_20x, 0.2).score < 0.5


def _views(px_low, px_high, offset_um, scene=None, shape=(192, 256), rotation_deg=0.0, **kw):
    """The frames of two objectives with the stage at one XY; the second is offset_um from the first."""
    objectives = {
        "low": FakeObjective("low", 10, 0.3, matrix_um_per_px=px_low * _rot(rotation_deg)),
        "high": FakeObjective("high", 20, 0.8, matrix_um_per_px=px_high * _rot(rotation_deg), parcentric_um=offset_um),
    }
    hw = FakeCalibrationHardware(
        objectives, scene or FakeScene.random(), shape=shape, start_objective="low", microstep_um=0.0, **kw
    )
    low = hw.snap("low", "BF")
    hw.switch_objective("high")
    return low, hw.snap("high", "BF")


@pytest.mark.parametrize("px_low", [0.4, 0.32])  # a near-equal pair (16x/20x) and an equal one (20x/20x)
def test_equal_and_near_equal_magnifications_match_through_a_central_crop(px_low):
    low, high = _views(px_low, 0.32, (2.0, -1.0))
    # the view is cropped to 60% of the 192 x 256 frame, so 20% of it stays free on every side
    assert template_shape(low.shape, high.shape, 0.32 / px_low) == (115, 153)
    match = match_template(low, high, 0.32 / px_low)
    assert (match.dx, match.dy) == pytest.approx((-2.0 / px_low, 1.0 / px_low), abs=0.2)  # -M_low^-1 @ offset
    assert match.score > 0.9 and match.runner_up_ratio < 0.5 and not match.at_edge
    assert match.search_px == (51.5, 38.5)  # 16.5 x 12.3 um: 20% of this frame, which is all such a pair measures


def test_an_offset_beyond_the_search_margin_ends_at_the_edge_not_in_a_wrong_match():
    # equal pixels on a 192-row frame leave 20% of 61 um = 12.3 um free above and below the crop;
    # 13 um matches 2 px short on the border with a score of 0.79, so only the edge flag refuses it
    low, high = _views(0.32, 0.32, (0.0, 13.0))
    assert match_template(low, high, 1.0).at_edge
    low, high = _views(0.32, 0.32, (0.0, 11.0))
    assert not match_template(low, high, 1.0).at_edge


@pytest.mark.parametrize("px_low", [1.6, 0.64, 0.4, 0.32])  # 4x, 10x, 16x and 20x against a 20x
def test_a_periodic_grid_is_ambiguous_at_every_magnification_ratio(px_low):
    # The external review's case: the 20x is one 12 um period away, so its true match and the grid's
    # next peak look alike. The runner-up is that next peak, not an empty comparison.
    low, high = _views(px_low, 0.32, (12.0, 0.0), scene=FakeScene.grid(period_um=12.0))
    assert match_template(low, high, 0.32 / px_low).runner_up_ratio >= 0.8


def _cone(shape=(40, 60), peak=(20, 30), slope=0.02):
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    return (1.0 - slope * np.hypot(rows - peak[0], cols - peak[1])).astype(np.float32)


def test_the_runner_up_is_the_best_distinct_peak_outside_the_lobe():
    surface = _cone()
    surface[5, 50] += 0.3  # a second peak (0.8), outside the 3 px lobe
    assert runner_up_ratio(surface, 20, 30, lobe=(3, 3)) == pytest.approx(0.8, abs=1e-6)


def test_without_another_peak_the_ratio_is_not_measured_rather_than_zero():
    # A single smooth peak: every other point rises towards it, so nothing was compared. The review's
    # empty comparison reported 0, the most unique result possible.
    assert runner_up_ratio(_cone(), 20, 30, lobe=(3, 3)) is None
    surface = _cone()
    surface[5, 50] += 0.3
    assert runner_up_ratio(surface, 20, 30, lobe=(40, 40)) is None  # an exclusion covering everything


def test_a_rise_towards_the_border_and_a_plateau_count_as_competing_peaks():
    rising = _cone()
    rising[:, 45:] = np.linspace(0.6, 0.9, 15, dtype=np.float32)[None, :]  # towards a peak beyond the border
    assert runner_up_ratio(rising, 20, 30, lobe=(3, 3)) == pytest.approx(0.9, abs=1e-6)
    ridge = _cone()
    ridge[:, 30] = 1.0  # a grating matches all along its lines
    assert runner_up_ratio(ridge, 20, 30, lobe=(3, 3)) == pytest.approx(1.0)


def test_the_autocorrelation_is_normalized_over_the_overlap():
    frame = np.random.default_rng(1).normal(size=(30, 41)).cumsum(axis=1)  # correlated along x
    rho = autocorrelation(frame)
    assert rho.shape == (31, 41) and rho[15, 20] == pytest.approx(1.0)
    for u, v in [(3, -2), (-20, 15), (7, 0), (0, -9)]:
        a = frame[max(0, -v) : 30 - max(0, v), max(0, -u) : 41 - max(0, u)]
        b = frame[max(0, v) : 30 - max(0, -v), max(0, u) : 41 - max(0, -u)]  # a, shifted by (u, v)
        assert rho[15 + v, 20 + u] == pytest.approx(np.corrcoef(a.ravel(), b.ravel())[0, 1], abs=1e-5)


RATIOS = {"4x/20x": 1.6, "10x/20x": 0.64, "16x/20x": 0.4, "20x/20x": 0.32}  # the lower objective's um/px


@pytest.mark.parametrize("period", [8.0, 20.0, 40.0])
@pytest.mark.parametrize("px_low", list(RATIOS.values()), ids=list(RATIOS))
def test_a_grid_repeats_at_its_period_whatever_the_search_area(px_low, period):
    # The search area of two 20x objectives holds ±16.5 x ±12.3 um: a 20 or 40 um grid's next peak lies
    # outside it, so only the frames themselves can show that the sample repeats (the external review's P1).
    low, high = _views(px_low, 0.32, (period, 0.0), scene=FakeScene.grid(period_um=period))
    repeat = match_template(low, high, 0.32 / px_low).self_similarity
    assert repeat.value > MAX_SELF_SIMILARITY + 0.4  # measured 0.91-1.00
    assert math.hypot(*repeat.shift_px) * px_low == pytest.approx(period, rel=0.06)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("px_low", list(RATIOS.values()), ids=list(RATIOS))
def test_a_textured_specimen_does_not_repeat_within_the_frame(px_low, seed):
    # With camera noise and vignetting. Over 20 seeds and both noise levels the highest was 0.24.
    low, high = _views(px_low, 0.32, (5.0, -3.0), FakeScene.random(seed), seed=seed, noise=0.01, vignetting=0.3)
    match = match_template(low, high, 0.32 / px_low)
    assert match.self_similarity.value < MAX_SELF_SIMILARITY - 0.1 and match.open_lobe is None


def test_a_grating_resembles_itself_along_its_lines():
    # Along its lines a grating resembles itself at every shift: a plateau, which ends the lobe along y at
    # once and counts as a maximum outside it. The repeat reported is that shift along the lines.
    low, high = _views(0.64, 0.32, (0.0, 0.0), scene=FakeScene.periodic(period_um=16.0))
    repeat = match_template(low, high, 0.5).self_similarity
    assert repeat.value > 0.9 and repeat.shift_px[0] == 0


@pytest.mark.parametrize("noise", [0.0, 0.002])
@pytest.mark.parametrize("x_fraction", [0.6, 0.7, 0.8, 0.9])
def test_a_repeat_at_the_end_of_a_ridge_is_not_absorbed_into_the_central_peak(x_fraction, noise):
    # The external review's P1 (2026-09-28): stripes along x carry the autocorrelation along y above 0.4
    # from zero shift to the 20 um repeat (0.66 at its lowest for 0.7). Revision 3's lobe, everything
    # connected to zero above 0.4, took the repeat in, and the pair passed with dy ~ 0 for a true 20 um.
    # The lobe along y now ends at the ridge's first minimum, so the repeat is a maximum outside it.
    low, high = _views(0.32, 0.32, (0.0, 20.0), FakeScene.stripes(20.0, x_fraction), shape=(192, 512), noise=noise)
    match = match_template(low, high, 1.0)
    assert match.self_similarity.value > 0.9 and match.open_lobe is None
    assert abs(match.self_similarity.shift_px[1]) * 0.32 == pytest.approx(20.0, rel=0.06)


def test_a_lobe_that_does_not_close_cannot_isolate_the_central_peak():
    # Stripes tilted by 1 degree: along y the autocorrelation keeps falling without reaching zero or a
    # minimum within the shifts tested, so the central peak's extent along y is unknown.
    low, high = _views(0.32, 0.32, (0.0, 0.0), FakeScene.stripes(None), shape=(192, 512), rotation_deg=1.0)
    match = match_template(low, high, 1.0)
    assert match.open_lobe.shift_px == (0, 96) and 0.4 < match.open_lobe.value < 0.9


def test_without_a_peak_outside_the_main_lobe_the_self_similarity_is_not_measured():
    # A 140 um grid on this 82 x 61 um frame, high-passed: along each axis the autocorrelation falls, and
    # nothing outside that rises to a maximum. Nothing was compared, so the value is None, never 0.
    low, high = _views(0.32, 0.32, (0.0, 0.0), FakeScene.grid(period_um=140.0))
    assert match_template(low, high, 1.0).self_similarity is None
    assert self_similarity(high_pass(low.astype(np.float32), 115 / 8)) == (None, None)
