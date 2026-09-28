import numpy as np
import pytest

from squid.objective_calibration.hardware import LimitError
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene


def _hw(**kw):
    objectives = {"20x": FakeObjective("20x", 20, 0.8, pixel_um=0.5)}
    return FakeCalibrationHardware(objectives, FakeScene.random(), start_objective="20x", **kw)


def _content_shift(a, b):
    """Integer (dx, dy) that best maps a's content onto b (brute force; test helper only)."""
    best, arg = -np.inf, (0, 0)
    a = a.astype(float) - a.mean()
    b = b.astype(float) - b.mean()
    for dy in range(-12, 13):
        for dx in range(-12, 13):
            sa = a[max(0, -dy) : a.shape[0] - max(0, dy), max(0, -dx) : a.shape[1] - max(0, dx)]
            sb = b[max(0, dy) : b.shape[0] - max(0, -dy), max(0, dx) : b.shape[1] - max(0, -dx)]
            score = float((sa * sb).mean())
            if score > best:
                best, arg = score, (dx, dy)
    return arg


def test_stage_move_moves_content_opposite_for_a_mosaic_consistent_camera():
    hw = _hw()
    ref = hw.snap("20x", "BF")
    hw.move_xy_to_um(2.5, 0.0)  # 5 px at 0.5 um/px
    moved = hw.snap("20x", "BF")
    # M = +px*I (mosaic-consistent): D = -M p  =>  p = -D/px = (-5, 0)
    assert _content_shift(ref, moved) == (-5, 0)


def test_flip_y_matrix_reverses_the_y_shift():
    objectives = {"20x": FakeObjective("20x", 20, 0.8, matrix_um_per_px=np.array([[0.5, 0.0], [0.0, -0.5]]))}
    hw = FakeCalibrationHardware(objectives, FakeScene.random(), start_objective="20x")
    ref = hw.snap("20x", "BF")
    hw.move_xy_to_um(0.0, 2.5)
    assert _content_shift(ref, hw.snap("20x", "BF")) == (0, 5)


def test_defocus_lowers_contrast():
    hw = _hw()
    sharp = hw.snap("20x", "BF").astype(float)
    hw.move_z_to_um(20.0)
    blurred = hw.snap("20x", "BF").astype(float)
    assert np.std(sharp) > 2 * np.std(blurred)


def test_flat_scene_has_no_texture():
    objectives = {"20x": FakeObjective("20x", 20, 0.8, pixel_um=0.5)}
    hw = FakeCalibrationHardware(objectives, FakeScene.flat(), start_objective="20x", noise=0.0)
    assert np.std(hw.snap("20x", "BF").astype(float)) == 0.0


def test_open_loop_readback_and_backlash():
    hw = _hw(backlash_um=2.0)
    hw.move_xy_to_um(10.0, 0.0)
    assert hw.get_xy_um() == (10.0, 0.0)  # the counter, not the actual position
    a = hw.snap("20x", "BF")
    hw.move_xy_to_um(20.0, 0.0)
    hw.move_xy_to_um(10.0, 0.0)  # same target, approached from the other side
    b = hw.snap("20x", "BF")
    assert _content_shift(a, b) != (0, 0)  # backlash shows up in the image, not in get_xy_um


def test_limits_refuse_before_moving():
    hw = _hw(xy_limits_um=((-100.0, 100.0), (-100.0, 100.0)))
    with pytest.raises(LimitError):
        hw.move_xy_to_um(150.0, 0.0)
    assert hw.get_xy_um() == (0.0, 0.0)


def test_rectangular_shape_and_hooks():
    hw = _hw(shape=(100, 200))
    assert hw.snap("20x", "BF").shape == (100, 200) == hw.frame_shape("BF")
    hw.fail_snap_at = 2
    with pytest.raises(RuntimeError):
        hw.snap("20x", "BF")
