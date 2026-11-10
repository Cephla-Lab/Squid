"""Relative ("FROM CENTER") z stacks in MultiPointWorker.acquire_at_position: the stack straddles the
focus plane on whichever actuator steps the stack, z returns to the focus plane afterwards, and the
next time point is seeded from the focus plane rather than the bottom of the stack."""

import pytest

from tests.control.test_MultiPointWorker_z_stack_order import _worker


def _planes(w):
    return sorted({z for _, _, z in w.frames})


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
def test_stage_stack_is_centred_on_the_focus_plane_and_returns_to_it(order):
    w = _worker(order, nz=5, z_stacking_config="FROM CENTER")
    focus_um = w.stage.z_mm * 1000
    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([focus_um + 2 * k for k in (-2, -1, 0, 1, 2)])
    assert w.stage.z_mm * 1000 == pytest.approx(focus_um)


@pytest.mark.parametrize("order", ["CHANNEL FIRST", "Z FIRST"])
def test_piezo_stack_is_centred_with_the_piezo_and_leaves_the_stage_alone(order):
    w = _worker(order, nz=5, use_piezo=True, z_stacking_config="FROM CENTER")
    stage_z_mm = w.stage.z_mm
    focus_piezo_um = w.piezo.position
    w.acquire_at_position("A1", "/tmp", 0)

    assert _planes(w) == pytest.approx([focus_piezo_um + 2 * k for k in (-2, -1, 0, 1, 2)])
    assert w.piezo.position == pytest.approx(focus_piezo_um)
    # The pre-move to the stack bottom must not go through the stage: nothing would undo it.
    assert w.stage.z_mm == pytest.approx(stage_z_mm)


def test_from_bottom_stack_starts_at_the_focus_plane():
    w = _worker("CHANNEL FIRST", nz=3, z_stacking_config="FROM BOTTOM")
    focus_um = w.stage.z_mm * 1000
    w.acquire_at_position("A1", "/tmp", 0)
    assert _planes(w) == pytest.approx([focus_um, focus_um + 2, focus_um + 4])


@pytest.mark.parametrize("z_stacking_config", ["FROM BOTTOM", "FROM CENTER"])
@pytest.mark.parametrize("use_piezo", [False, True])
def test_next_time_point_is_seeded_from_the_focus_plane(z_stacking_config, use_piezo):
    w = _worker("CHANNEL FIRST", nz=5, use_piezo=use_piezo, z_stacking_config=z_stacking_config)
    w.do_autofocus = True
    w.Nt = 3
    focus_mm = w.stage.z_mm
    w.acquire_at_position("A1", "/tmp", 2)

    # Not the z_level 0 position, which under FROM CENTER is half a stack below the focus plane.
    assert w._last_time_point_z_pos == {("A1", 2): pytest.approx(focus_mm)}


def test_no_seed_is_recorded_without_autofocus_or_for_a_single_time_point():
    w = _worker("CHANNEL FIRST", nz=3, z_stacking_config="FROM CENTER")
    w.acquire_at_position("A1", "/tmp", 0)
    assert w._last_time_point_z_pos == {}

    w = _worker("CHANNEL FIRST", nz=3, z_stacking_config="FROM CENTER")
    w.do_autofocus = True  # Nt stays 1
    w.acquire_at_position("A1", "/tmp", 0)
    assert w._last_time_point_z_pos == {}


@pytest.mark.parametrize("nz, expected", [(1, 0), (3, 1), (5, 2), (4, 2), (9, 4)])
def test_center_offset_steps(nz, expected):
    w = _worker("CHANNEL FIRST", nz=nz, z_stacking_config="FROM CENTER")
    assert w._center_offset_steps() == expected
    w.z_stacking_config = "FROM BOTTOM"
    assert w._center_offset_steps() == 0
