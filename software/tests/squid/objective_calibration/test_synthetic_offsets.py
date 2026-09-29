"""C1 extensions of the synthetic microscope: specimen topography, a strong off-centre feature,
a changer that parks Z per objective (the Xeryon frame), and the camera ROI centre."""

import numpy as np
import pytest

from squid.objective_calibration.synthetic import (
    FakeCalibrationHardware,
    FakeFeature,
    FakeObjective,
    FakeScene,
    FakeTopography,
)


def _hw(scene=None, start_objective="20x", **kw):
    objectives = {
        "4x": FakeObjective("4x", 4, 0.13, pixel_um=1.6),
        "20x": FakeObjective("20x", 20, 0.8, pixel_um=0.32),
    }
    return FakeCalibrationHardware(objectives, scene or FakeScene.random(), start_objective=start_objective, **kw)


def _contrast_at(hw, z_um) -> float:
    hw.move_z_to_um(z_um)
    return float(np.std(hw.snap("20x", "BF").astype(float)))


def test_a_sloped_specimen_moves_the_focus_with_the_stage():
    # 1% slope along x: 200 um away in x the specimen is 2 um higher, and the 20x (DOF 0.86 um) sees it
    hw = _hw(topography=FakeTopography(slope=(0.01, 0.0)))
    hw.move_xy_to_um(-200.0, 0.0)  # the centre now sees sample point u = -S = (+200, 0)
    contrast = {z: _contrast_at(hw, z) for z in (0.0, 1.0, 2.0, 3.0, 4.0)}
    assert max(contrast, key=contrast.get) == 2.0


def test_a_height_step_blurs_one_side_of_the_frame():
    hw = _hw(topography=FakeTopography(step_um=20.0, step_x_um=0.0))  # the specimen is 20 um higher for x > 0
    image = hw.snap("20x", "BF").astype(float)
    # u = -px * p: sample x > 0 is the LEFT half of the image, 20 um out of focus at z = 0
    left, right = image[:, : image.shape[1] // 2 - 4], image[:, image.shape[1] // 2 + 4 :]
    assert np.std(right) > 2 * np.std(left)


def test_a_feature_raises_the_local_contrast():
    plain = _hw().snap("20x", "BF").astype(float)
    hw = _hw(scene=FakeScene.random(feature=FakeFeature(centre_um=(20.0, 0.0), radius_um=4.0, gain=4.0)))
    boosted = hw.snap("20x", "BF").astype(float)
    h, w = boosted.shape
    col = int(round((w - 1) / 2 - 20.0 / 0.32))  # u = -px * p: +20 um in x is 62.5 px left of centre
    patch = (slice(h // 2 - 8, h // 2 + 8), slice(col - 8, col + 8))
    assert np.std(boosted[patch]) > 2 * np.std(plain[patch])
    far = (slice(h // 2 - 8, h // 2 + 8), slice(w - 24, w - 8))
    assert np.std(boosted[far]) == pytest.approx(np.std(plain[far]), rel=0.05)


def test_the_changer_parks_z_per_objective_like_the_xeryon():
    hw = _hw(start_objective="4x", start_z_um=100.0, changer_z_um={"4x": 0.0, "20x": -2000.0})
    hw.switch_objective("20x")
    assert hw.get_z_um() == pytest.approx(-1900.0)  # the switch itself moved Z by the changer's part
    hw.switch_objective("20x")
    assert hw.get_z_um() == pytest.approx(-1900.0)
    hw.switch_objective("4x")
    assert hw.get_z_um() == pytest.approx(100.0)


def test_the_roi_defaults_to_the_whole_frame_and_can_move():
    hw = _hw(shape=(100, 200))
    assert hw.roi() == (0, 0, 200, 100)  # (x, y, width, height), in unbinned sensor pixels on the fake
    assert hw.roi_centre_px() == (100.0, 50.0)
    hw.set_roi(40, 0, 200, 100)
    assert hw.roi_centre_px() == (140.0, 50.0)


def test_a_grid_repeats_with_its_period_along_both_axes():
    hw = _hw(scene=FakeScene.grid(period_um=12.0), noise=0.0, start_objective="4x")
    at_origin = hw.snap("4x", "BF")
    for shifted, same in (((12.0, 0.0), True), ((0.0, 12.0), True), ((6.0, 0.0), False)):
        hw.move_xy_to_um(*shifted)
        repeated = np.abs(hw.snap("4x", "BF").astype(float) - at_origin).max() <= 1
        assert repeated == same, shifted


def test_stripes_are_the_same_along_y_or_repeat_with_their_period():
    # The external review's directional specimen (2026-09-28): a random profile along x, with or without a
    # 20 um sinusoid along y
    for scene, y_shift in ((FakeScene.stripes(None), 7.0), (FakeScene.stripes(20.0), 20.0)):
        hw = _hw(scene=scene, noise=0.0)
        at_origin = hw.snap("20x", "BF").astype(float)
        hw.move_xy_to_um(0.0, y_shift)
        assert np.abs(hw.snap("20x", "BF") - at_origin).max() <= 1
        hw.move_xy_to_um(2.0, 0.0)
        assert np.abs(hw.snap("20x", "BF") - at_origin).max() > 100


def test_a_flat_topography_renders_exactly_as_before():
    plain = _hw().snap("20x", "BF")
    flat = _hw(topography=FakeTopography()).snap("20x", "BF")
    np.testing.assert_array_equal(plain, flat)
