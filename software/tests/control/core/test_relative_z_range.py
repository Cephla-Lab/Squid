import pytest

from control.core.relative_z_range import frame_targets, nz_for_range, relative_range_problem


def test_nz_matches_the_widget_formula_with_a_float_guard():
    assert nz_for_range(-5.0, 15.0, 2.0) == 11
    assert nz_for_range(0.0, 0.6, 0.2) == 4  # 0.6/0.2 = 3.0000000000000004 must not give 5
    assert nz_for_range(3.0, 3.0, 2.0) == 1  # single plane
    assert nz_for_range(0.0, 5.0, 2.0) == 4  # last plane one dz past Z-max, as update_Nz


@pytest.mark.parametrize("config", ["FROM TOP", "FROM CENTER"])
def test_problem_refuses_other_stack_configs(config):
    assert relative_range_problem((-5, 15), 2.0, 300.0, config).startswith("A relative Z-range runs from Z-min up")


@pytest.mark.parametrize("dz", [0.0, -1.0, float("nan"), float("inf")])
def test_problem_refuses_a_spacing_that_is_not_finite_and_positive(dz):
    assert relative_range_problem((-5, 15), dz, 300.0, "FROM BOTTOM") is not None
    assert relative_range_problem((3, 3), dz, 300.0, "FROM BOTTOM") is not None  # even a single plane


def test_problem_refuses_reversed_non_finite_and_too_long():
    assert relative_range_problem((15, -5), 2.0, 300.0, "FROM BOTTOM") is not None
    assert relative_range_problem((float("nan"), 5), 2.0, 300.0, "FROM BOTTOM") is not None
    assert relative_range_problem((0, 301), 1.0, 300.0, "FROM BOTTOM") is not None
    assert relative_range_problem((-5, 15), 2.0, 300.0, "FROM BOTTOM") is None


def test_targets_decide_eligibility_after_the_channel_offset():
    t = frame_targets(
        anchor_um=0.0, z_min_rel_um=-2.0, nz=3, dz_um=2.0, channel_offsets_um=[0.0, 5.0], travel_um=(0.0, 300.0)
    )
    assert t[0] == [None, 0.0, 2.0]  # -2 is out of travel for the un-offset channel
    assert t[1] == [3.0, 5.0, 7.0]  # nominal -2 with +5 lands at 3: imaged


def test_targets_keep_the_travel_ends_inclusive():
    assert frame_targets(296.0, 0.0, 3, 2.0, [0.0], (0.0, 300.0)) == [[296.0, 298.0, 300.0]]
