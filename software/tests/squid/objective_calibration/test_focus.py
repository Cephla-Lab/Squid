import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.focus import (
    FocusError,
    depth_of_field_um,
    fine_step_um,
    focus_sweep,
    level_targets,
    plan_coarse_level,
    plan_next_level,
)
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


OBJECTIVES = {
    "4x": FakeObjective("4x", 4, 0.13, pixel_um=1.6),
    "10x": FakeObjective("10x", 10, 0.3, pixel_um=0.64),
    "20x": FakeObjective("20x", 20, 0.8, pixel_um=0.32),
}


def _hw(name, z_focus, scene=None, **kw):
    objectives = {
        k: FakeObjective(v.name, v.magnification, v.na, pixel_um=v.pixel_um, z_focus_um=z_focus)
        for k, v in OBJECTIVES.items()
    }
    return FakeCalibrationHardware(
        objectives, scene or FakeScene.random(), start_objective=name, microstep_um=0.0, **kw
    )


def _levels(na, range_um):
    level = plan_coarse_level(na, range_um, 0.0)
    out = [level]
    while True:
        level = plan_next_level(level, level.center_um, na)
        if level is None:
            return out
        out.append(level)


class TestPlanning:
    def test_20x_r100_matches_the_spec_example(self):
        levels = _levels(0.8, 100.0)
        assert [len(level_targets(lv)) for lv in levels] == [41, 17, 17, 13]
        assert levels[-1].step_um == pytest.approx(fine_step_um(0.8))

    @pytest.mark.parametrize("na", [0.13, 0.3, 0.8])
    @pytest.mark.parametrize("range_um", [100.0, 20.0])
    def test_every_default_range_has_at_least_7_coarse_samples(self, na, range_um):
        coarse = plan_coarse_level(na, range_um, 0.0)
        assert 7 <= len(level_targets(coarse)) <= 41
        assert coarse.half_span_um >= 3 * depth_of_field_um(na) - 1e-9

    def test_fine_step_floor(self):
        assert fine_step_um(1.4) == pytest.approx(0.1)


class TestSweep:
    @pytest.mark.parametrize("name", ["4x", "10x", "20x"])
    def test_finds_focus_within_a_quarter_dof(self, name):
        na = OBJECTIVES[name].na
        hw = _hw(name, z_focus=37.0)
        result = focus_sweep(
            hw, objective=name, channel="BF", na=na, center_um=0.0, range_um=100.0, square_px=40, fine_metric=lape
        )
        assert result.z_best_um == pytest.approx(37.0, abs=0.25 * depth_of_field_um(na))
        assert hw.get_z_um() == pytest.approx(result.z_best_um, abs=hw.z_microstep_um)  # lands on a Z microstep
        coarse = plan_coarse_level(na, 100.0, 0.0)
        # the high-passed std runs only while the step exceeds 2*DOF (never at 4x or 10x with these ranges)
        assert result.levels[0].metric == ("highpass_std" if coarse.step_um > 2 * depth_of_field_um(na) else "fine")

    def test_narrow_user_range_at_4x_still_works(self):
        hw = _hw("4x", z_focus=8.0)
        result = focus_sweep(
            hw, objective="4x", channel="BF", na=0.13, center_um=0.0, range_um=20.0, square_px=40, fine_metric=lape
        )
        assert result.z_best_um == pytest.approx(8.0, abs=0.25 * depth_of_field_um(0.13))

    def test_untextured_field_fails_the_peak_rise_gate(self):
        hw = _hw("20x", z_focus=0.0, scene=FakeScene.flat(), noise=0.0)  # LAPE on noise spreads 23% over 40 px crops
        with pytest.raises(FocusError, match="No focus peak found"):
            focus_sweep(
                hw, objective="20x", channel="BF", na=0.8, center_um=0.0, range_um=20.0, square_px=40, fine_metric=lape
            )

    def test_peak_outside_a_user_range_says_widen(self):
        hw = _hw("20x", z_focus=110.0)  # at 150 the whole curve is flat and the no-texture gate fires first
        with pytest.raises(FocusError, match="widen the range"):
            focus_sweep(
                hw, objective="20x", channel="BF", na=0.8, center_um=0.0, range_um=100.0, square_px=40, fine_metric=lape
            )

    def test_computed_range_widens_once(self):
        hw = _hw(
            "20x", z_focus=18.0
        )  # LAPE on a step<=2*DOF coarse level is flat noise beyond a few DOF; 25 is invisible
        result = focus_sweep(
            hw,
            objective="20x",
            channel="BF",
            na=0.8,
            center_um=0.0,
            range_um=15.0,
            square_px=40,
            fine_metric=lape,
            range_is_computed=True,
        )
        assert result.z_best_um == pytest.approx(18.0, abs=0.25)

    def test_samples_outside_the_z_limit_are_dropped_not_clamped(self):
        hw = _hw("20x", z_focus=0.0, z_limits_um=(-8.0, 5000.0))
        result = focus_sweep(
            hw, objective="20x", channel="BF", na=0.8, center_um=0.0, range_um=20.0, square_px=40, fine_metric=lape
        )
        assert min(result.levels[0].z_um) >= -8.0
        assert result.z_best_um == pytest.approx(0.0, abs=0.25)

    def test_too_few_samples_inside_the_limits_fails(self):
        hw = _hw("20x", z_focus=0.0, z_limits_um=(-0.5, 0.5))
        with pytest.raises(FocusError, match="hits the Z limit"):
            focus_sweep(
                hw, objective="20x", channel="BF", na=0.8, center_um=0.0, range_um=20.0, square_px=40, fine_metric=lape
            )
