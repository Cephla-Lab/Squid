import math

import numpy as np
import pytest

import squid.objective_calibration.pixel_size as ps
from squid.objective_calibration.hardware import LimitError
from squid.objective_calibration.pixel_size import PixelSizeError, decompose, measure_pixel_size
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene


def _rot(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])


GEOMETRIES = {
    "identity": 0.5 * np.eye(2),
    "rotated": 0.5 * _rot(0.5),
    "flip_y": np.array([[0.5, 0.0], [0.0, -0.5]]),
    "swap": np.array([[0.0, 0.5], [0.5, 0.0]]),
    "swap_flip": np.array([[0.0, -0.5], [0.5, 0.0]]) @ _rot(0.3),
}


def _hw(matrix, shape=(192, 256), scene=None, **kw):
    obj = FakeObjective("20x", 20, 0.8, matrix_um_per_px=matrix)
    return FakeCalibrationHardware({"20x": obj}, scene or FakeScene.random(), shape=shape, start_objective="20x", **kw)


@pytest.mark.parametrize("shape", [(192, 256), (100, 256)])
@pytest.mark.parametrize("name", list(GEOMETRIES))
def test_recovers_every_geometry(name, shape):
    true_m = GEOMETRIES[name]
    hw = _hw(true_m, shape=shape)
    result = measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)
    np.testing.assert_allclose(
        result.matrix_um_per_px, true_m, atol=0.004
    )  # cv2 sub-pixel bias up to 0.4 px at 25-90 px shifts
    s, theta, flip, anisotropy = decompose(true_m)
    assert result.pixel_size_um == pytest.approx(s, rel=0.005)
    assert result.rotation_deg == pytest.approx(theta, abs=0.3)  # 0.3 px over a 50 px shift is 0.34 deg
    np.testing.assert_array_equal(result.flip, flip)
    assert result.orientation_matches_mosaic == (name in ("identity", "rotated"))
    assert hw.get_xy_um() == (0.0, 0.0)


def test_decompose_puts_the_flip_on_the_right():
    s, theta, flip, anisotropy = decompose(0.5 * _rot(0.5) @ np.array([[1, 0], [0, -1]]))
    assert s == pytest.approx(0.5) and theta == pytest.approx(0.5, abs=1e-6)
    np.testing.assert_array_equal(flip, [[1, 0], [0, -1]])
    assert anisotropy == pytest.approx(1.0)


@pytest.mark.parametrize("first_move", [(+300.0, +300.0), (-300.0, -300.0)])
def test_same_side_approach_cancels_backlash_whatever_the_arrival_direction(first_move):
    hw = _hw(0.5 * np.eye(2), backlash_um=2.0)
    hw.move_xy_to_um(*first_move)
    hw.move_xy_to_um(0.0, 0.0)  # arrive at S from either side
    result = measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)
    assert result.pixel_size_um == pytest.approx(0.5, rel=0.005)  # as above


def test_without_the_same_side_approach_backlash_fails_the_residual_gate(monkeypatch):
    hw = _hw(0.5 * np.eye(2), backlash_um=2.0)
    monkeypatch.setattr(ps, "approach_xy", lambda hw, x, y, margin_um=ps.APPROACH_MARGIN_UM: hw.move_xy_to_um(x, y))
    with pytest.raises(PixelSizeError):
        measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)


def test_realistic_stage_error_passes_the_microstep_aware_gates():
    hw = _hw(0.5 * np.eye(2), positioning_noise_um=0.1, microstep_um=0.79)
    result = measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)
    assert result.pixel_size_um == pytest.approx(0.5, rel=0.01)


def test_target_outside_limits_is_refused_before_moving():
    hw = _hw(0.5 * np.eye(2), xy_limits_um=((-40.0, 40.0), (-40.0, 40.0)))
    moves = hw.moves
    with pytest.raises(LimitError):
        measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)
    assert hw.moves == moves


def test_periodic_sample_fails_registration():
    hw = _hw(0.5 * np.eye(2), scene=FakeScene.periodic(period_um=10.0))
    with pytest.raises(PixelSizeError, match="don't match|disagree"):
        measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)


def test_drift_fails_the_drift_gate():
    hw = _hw(0.5 * np.eye(2), drift_um_per_op=(0.05, 0.0))
    with pytest.raises(PixelSizeError, match="drifted|inconsistent"):
        measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)


def test_anisotropy_gate():
    hw = _hw(np.diag([0.5, 0.51]))
    with pytest.raises(PixelSizeError, match="differs between x and y"):
        measure_pixel_size(hw, objective="20x", channel="BF", nominal_px_um=0.5)
