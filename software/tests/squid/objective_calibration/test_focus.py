import json
from pathlib import Path

import numpy as np
import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.focus import (
    COARSE_SAMPLES,
    FocusError,
    _is_peak,
    _peak_at_edge,
    autofocus,
    depth_of_field_um,
    fine_step_um,
    focus_sweep,
    level_targets,
    plan_coarse_level,
    plan_next_level,
)
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

DATA = Path(__file__).parent / "data"


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


def _sweep(hw, name, **kw):
    args = dict(objective=name, channel="BF", na=OBJECTIVES[name].na, center_um=0.0, square_px=40, fine_metric=lape)
    return focus_sweep(hw, **{**args, **kw})


def _levels(na, range_um):
    level = plan_coarse_level(na, range_um, 0.0)
    out = [level]
    while True:
        level = plan_next_level(level, level.center_um, na)
        if level is None:
            return out
        out.append(level)


def _openflexure_cases():
    """OpenFlexure's 41 hand-labelled sharpness curves (openflexure-microscope-server,
    tests/unit_tests/data/sharpness_test_cases.json, GPL-3). success = the peak is safely inside the
    stack; continue / restart = it is at the top / bottom end."""
    cases = json.loads((DATA / "openflexure_sharpness_test_cases.json").read_text())
    for i, case in enumerate(cases):
        expected = case["label"] if isinstance(case["label"], list) else [case["label"]]
        marks = [pytest.mark.xfail(reason="OpenFlexure: known failing case")] if case.get("allow_failure") else []
        if case["sharpnesses"] == [100000, 200000, 1000000, 200000, 1000000, 200000, 100000]:
            marks.append(pytest.mark.xfail(reason="double peak: OpenFlexure classifies by shape, we by argmax"))
        yield pytest.param(case["sharpnesses"], expected, marks=marks, id=f"case_{i}")


class TestPlanning:
    def test_20x_r100_matches_the_spec_example(self):
        levels = _levels(0.8, 100.0)
        assert [len(level_targets(lv)) for lv in levels] == [41, 17, 17, 13]
        assert levels[-1].step_um == pytest.approx(fine_step_um(0.8))

    @pytest.mark.parametrize("na", [0.13, 0.3, 0.8])
    @pytest.mark.parametrize("range_um", [200.0, 100.0, 20.0])
    def test_coarse_level_is_a_fixed_grid_over_at_least_3_dof(self, na, range_um):
        coarse = plan_coarse_level(na, range_um, 0.0)
        assert len(level_targets(coarse)) == COARSE_SAMPLES
        assert coarse.search_range_um >= 3 * depth_of_field_um(na) - 1e-9

    def test_fine_step_floor(self):
        assert fine_step_um(1.4) == pytest.approx(0.1)


class TestEdgeRule:
    """_peak_at_edge is OpenFlexure's check_stack_result rule (edge_size = 2). We do not fit their
    parabola, so the one curve they label by shape (a double peak) is the documented mismatch."""

    @pytest.mark.parametrize(("scores", "expected"), list(_openflexure_cases()))
    def test_matches_openflexure_labels(self, scores, expected):
        s = np.asarray(scores, dtype=float)
        if s.max() == s.min():
            verdict = "continue"  # a constant sweep never reaches the edge rule: _is_peak refuses it first
        elif not _peak_at_edge(scores):
            verdict = "success"
        else:
            top = np.flatnonzero(s == s.max())
            verdict = "restart" if top[0] < 2 else "continue"
        assert verdict in expected


class TestSweep:
    @pytest.mark.parametrize("name", ["4x", "10x", "20x"])
    def test_finds_focus_within_a_quarter_dof(self, name):
        na = OBJECTIVES[name].na
        hw = _hw(name, z_focus=37.0)
        result = _sweep(hw, name, range_um=100.0)
        assert result.z_best_um == pytest.approx(37.0, abs=0.25 * depth_of_field_um(na))
        assert hw.get_z_um() == pytest.approx(result.z_best_um, abs=hw.z_microstep_um)  # lands on a Z microstep
        # The first level always uses the high-passed std (spec C §6.3 as amended 2026-09-28); finer
        # levels use it only while their step exceeds 2*DOF.
        assert result.levels[0].metric == "highpass_std"
        for level in result.levels[1:]:
            step = abs(level.z_um[1] - level.z_um[0])
            assert level.metric == ("highpass_std" if step > 2 * depth_of_field_um(na) else "fine")

    @pytest.mark.parametrize("z_focus", [70.0, -70.0])
    def test_low_na_focus_well_inside_the_range_is_not_an_edge(self, z_focus):
        # Bench 2026-10-05: a 4x whose focus sat 70 um from the 20x's start Z (ordinary parfocality)
        # was refused as "at the edge" of +-100 um on every cycle, because the 4x coarse step (0.7*DOF
        # = 22.8 um, 10 samples) put it on sample index 1 and the edge test counted samples.
        hw = _hw("4x", z_focus=z_focus)
        result = _sweep(hw, "4x", range_um=100.0)
        assert result.z_best_um == pytest.approx(z_focus, abs=0.25 * depth_of_field_um(0.13))

    def test_narrow_user_range_at_4x_still_works(self):
        hw = _hw("4x", z_focus=8.0)
        result = _sweep(hw, "4x", range_um=20.0)
        assert result.z_best_um == pytest.approx(8.0, abs=0.25 * depth_of_field_um(0.13))

    def test_untextured_field_is_refused_as_flat(self):
        hw = _hw("20x", z_focus=0.0, scene=FakeScene.flat(), noise=0.0)  # LAPE on noise spreads 23% over 40 px crops
        with pytest.raises(FocusError, match=r"No focus peak within ±[\d.]+ µm of [\d.-]+ µm \(contrast rise \d+%\)"):
            _sweep(hw, "20x", range_um=20.0)

    @pytest.mark.parametrize("z_focus", [110.0, -110.0])
    def test_a_focus_past_the_range_is_refused_at_the_edge(self, z_focus):
        hw = _hw("20x", z_focus=z_focus)
        with pytest.raises(FocusError, match="at the edge of ±100 µm|No focus peak"):
            _sweep(hw, "20x", range_um=100.0)

    def test_a_4x_focus_just_past_the_range_is_refused_at_the_edge(self):
        hw = _hw("4x", z_focus=110.0)  # 4x coarse curve is wide: the edge rule, not the flat test, fires
        with pytest.raises(FocusError, match="Sharpest sample at the edge of ±100 µm"):
            _sweep(hw, "4x", range_um=100.0)

    def test_samples_outside_the_z_limit_are_dropped_not_clamped(self):
        hw = _hw("20x", z_focus=0.0, z_limits_um=(-8.0, 5000.0))
        result = _sweep(hw, "20x", range_um=20.0)
        assert min(result.levels[0].z_um) >= -8.0
        assert result.z_best_um == pytest.approx(0.0, abs=0.25)

    def test_too_few_samples_inside_the_limits_fails(self):
        hw = _hw("20x", z_focus=0.0, z_limits_um=(-0.5, 0.5))
        with pytest.raises(FocusError, match="hits the Z limit"):
            _sweep(hw, "20x", range_um=20.0)


class TestComputedRange:
    """C1 pass 2 (offsets.py _aligned_focus): a computed range widens once about the same centre."""

    def test_computed_range_widens_once(self):
        hw = _hw("20x", z_focus=16.0)  # 1 um past the ±15 um computed range
        result = _sweep(hw, "20x", range_um=15.0, range_is_computed=True)
        coarse = [lv for lv in result.levels if lv.metric == "highpass_std"]
        assert len(coarse) == 2
        assert max(coarse[1].z_um) == pytest.approx(30.0)  # doubled, still centred on 0
        assert result.z_best_um == pytest.approx(16.0, abs=0.25)

    def test_a_second_edge_hit_is_too_uneven(self):
        hw = _hw("4x", z_focus=250.0)  # past ±100 and past the widened ±200
        with pytest.raises(FocusError, match="too uneven"):
            _sweep(hw, "4x", range_um=100.0, range_is_computed=True)

    def test_a_user_range_never_widens(self):
        hw = _hw("20x", z_focus=16.0)
        with pytest.raises(FocusError, match="at the edge of ±15 µm"):
            _sweep(hw, "20x", range_um=15.0)


class TestFlatTest:
    """_is_peak compares the sweep's peak to its own noise: no absolute contrast threshold."""

    @pytest.mark.parametrize("seed", range(8))
    def test_noise_is_not_a_peak(self, seed):
        rng = np.random.default_rng(seed)
        assert not _is_peak(1.0 + 0.02 * rng.standard_normal(COARSE_SAMPLES))

    def test_a_constant_curve_is_not_a_peak(self):
        assert not _is_peak([3.0] * COARSE_SAMPLES)

    @pytest.mark.parametrize("rise", [0.3, 1.0, 10.0])
    def test_a_peak_above_the_noise_is_a_peak_whatever_its_contrast(self, rise):
        # The coarse curve near focus: a bump on a flat base, with 2 % noise. 0.3 is the dust-like case.
        x = np.linspace(-100, 100, COARSE_SAMPLES)
        noise = 0.02 * np.random.default_rng(1).standard_normal(COARSE_SAMPLES)
        assert _is_peak(1.0 + rise * np.exp(-((x - 12.0) ** 2) / (2 * 8.0**2)) + noise)


class TestAutofocus:
    def test_focuses_from_the_current_z_with_defaults(self):
        hw = _hw("20x", z_focus=37.0)
        hw.move_z_to_um(5.0)
        result = autofocus(hw, objective="20x", channel="BF", na=0.8, range_um=100.0)
        assert result.z_best_um == pytest.approx(37.0, abs=0.25 * depth_of_field_um(0.8))
        assert hw.get_z_um() == pytest.approx(result.z_best_um, abs=hw.z_microstep_um)


class TestNoFalseFocus:
    """External reviews: a sweep either finds the true focus or refuses; it must never report an
    unrelated peak. The original noisy cases, with the default noise and a 40 px focus square."""

    def test_a_noisy_flat_field_is_refused(self):
        hw = _hw("20x", z_focus=0.0, scene=FakeScene.flat())  # default noise 0.002
        with pytest.raises(FocusError, match="No focus peak within"):
            _sweep(hw, "20x", range_um=20.0)

    @pytest.mark.parametrize("z_focus", [18.0, 25.0])
    def test_a_focus_past_the_range_is_refused(self, z_focus):
        hw = _hw("20x", z_focus=z_focus)
        with pytest.raises(FocusError, match="at the edge|No focus peak"):
            _sweep(hw, "20x", range_um=15.0)

    def test_noisy_flat_fields_are_refused_across_seeds(self):
        for seed in range(6):
            hw = _hw("20x", z_focus=0.0, scene=FakeScene.flat(), seed=seed)
            with pytest.raises(FocusError, match="No focus peak within"):
                _sweep(hw, "20x", range_um=20.0)
