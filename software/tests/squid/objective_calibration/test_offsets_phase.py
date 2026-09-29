"""The offsets phase on the run engine: pass 2, the gates that fail a cycle, averaging, restore."""

import re

import numpy as np
import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.engine import ObjectiveSpec, RunConfig, run_calibration
from squid.objective_calibration.focus import depth_of_field_um, focus_sweep
from squid.objective_calibration.offsets import OffsetsPhase, aligned_range_um, offsets_report, summarize_offsets
from squid.objective_calibration.synthetic import (
    FakeCalibrationHardware,
    FakeFeature,
    FakeObjective,
    FakeScene,
    FakeTopography,
)

ORIENTATIONS = {"inverted": np.eye(2), "upright": np.diag([1.0, -1.0])}
START_XY = (1000.0, 2000.0)
START_Z = 1.0


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _machine(
    orientation=np.eye(2),
    parcentric=None,
    z_focus=None,
    names=("4x", "10x", "20x"),
    scene=None,
    cycles=1,
    range_um=20.0,
    start_xy_um=START_XY,
    **kw,
):
    """The fake's sample point under the centre is u = -S + parcentric: a topography (a tilt, a step) is
    written in sample coordinates, so the tests that use one start the stage at S = (0, 0)."""
    optics = {"4x": (4, 0.13, 1.6), "10x": (10, 0.3, 0.64), "20x": (20, 0.8, 0.32)}
    parcentric = parcentric or {"4x": (0.0, 0.0), "10x": (12.0, -7.0), "20x": (-15.0, 9.0)}
    z_focus = z_focus or {"4x": 0.0, "10x": 6.0, "20x": -4.0}
    objectives = {
        name: FakeObjective(
            name,
            optics[name][0],
            optics[name][1],
            matrix_um_per_px=optics[name][2] * orientation,
            z_focus_um=z_focus[name],
            parcentric_um=parcentric[name],
        )
        for name in names
    }
    hw = FakeCalibrationHardware(
        objectives,
        scene or FakeScene.random(),
        start_objective=names[0],
        start_xy_um=start_xy_um,
        start_z_um=START_Z,
        **kw,
    )
    specs = [ObjectiveSpec(name, optics[name][0], optics[name][1], optics[name][2]) for name in names]
    return hw, RunConfig(specs, "BF", search_range_um=range_um, cycles=cycles)


def _run(hw, cfg, **kw):
    phase = OffsetsPhase(cfg, fine_metric=lape, **kw)
    result = run_calibration(hw, cfg, fine_metric=lape, phase2=phase)
    return result, phase


def _assert_restored(hw, objective="4x", xy=START_XY):
    assert hw.current_objective() == objective
    assert hw.get_xy_um() == pytest.approx(xy)
    assert hw.get_z_um() == pytest.approx(START_Z)


@pytest.mark.parametrize("name", list(ORIENTATIONS))
def test_a_combined_run_recovers_the_injected_offsets_and_restores(name):
    hw, cfg = _machine(ORIENTATIONS[name])
    result, phase = _run(hw, cfg)
    assert result.stopped is None and result.cycles[0].error is None
    assert set(result.pixel_sizes) == {"4x", "10x", "20x"}
    [cycle] = phase.results
    assert cycle.reference == "4x" and set(cycle.offsets) == {"10x", "20x"}
    for objective, (dx, dy) in (("10x", (12.0, -7.0)), ("20x", (-15.0, 9.0))):
        offset = cycle.offsets[objective]
        assert (offset.dx_um, offset.dy_um) == pytest.approx((dx, dy), abs=0.5)
        # dz is z_focus_k - z_focus_ref; the 4x reference (DOF 32 um) limits it, as spec C §11 R5 says
        truth = hw.objectives[objective].z_focus_um - hw.objectives["4x"].z_focus_um
        assert offset.dz_um == pytest.approx(truth, abs=0.5)
        assert offset.match_score > 0.5 and offset.runner_up_ratio < 0.8 and offset.focus_peak_rise > 0.2
    _assert_restored(hw)


def test_three_cycles_are_averaged_with_their_std():
    hw, cfg = _machine(cycles=3, positioning_noise_um=0.1, microstep_um=0.4)
    result, phase = _run(hw, cfg)
    assert result.stopped is None and len(phase.results) == 3
    summary = summarize_offsets(phase.results)
    assert summary["20x"].cycles == 3
    assert (summary["20x"].dx_um, summary["20x"].dy_um) == pytest.approx((-15.0, 9.0), abs=0.5)
    assert summary["20x"].dz_um == pytest.approx(-4.0, abs=1.0)
    for std in (summary["20x"].std_dx_um, summary["20x"].std_dy_um):
        assert std is not None and 0.0 <= std < 0.5
    # dz = z_20x - z_4x carries the 4x reference's focus scatter (DOF 32 um), not the 20x's (spec C §11 R5):
    # the 20x's own aligned focus repeats to ~0.01 um while the 4x's varies by ~0.7 um
    assert 0.0 < summary["20x"].std_dz_um < 0.1 * depth_of_field_um(0.13)
    assert np.std([r.offsets["20x"].z_focus_um for r in phase.results]) < 0.1
    assert summary["20x"].match_score == min(r.offsets["20x"].match_score for r in phase.results)
    assert summarize_offsets([]) == {}


def test_one_cycle_has_no_std():
    hw, cfg = _machine()
    _, phase = _run(hw, cfg)
    summary = summarize_offsets(phase.results)
    assert summary["10x"].cycles == 1 and summary["10x"].std_dz_um is None


DISPLACED_20X = {"4x": (0.0, 0.0), "20x": (100.0, 0.0)}  # the 20x centred 100 um from the reference's patch
ORIGIN = (0.0, 0.0)


@pytest.mark.parametrize("name", list(ORIENTATIONS))
def test_pass_2_removes_the_slope_bias_that_pass_1_alone_carries(name):
    # 2% slope along x: pass 1 measures the 20x's focus on a patch 2 um higher than the reference's
    slope = FakeTopography(slope=(0.02, 0.0))
    hw, cfg = _machine(ORIENTATIONS[name], DISPLACED_20X, names=("4x", "20x"), start_xy_um=ORIGIN, topography=slope)
    _, aligned = _run(hw, cfg)
    _assert_restored(hw, xy=ORIGIN)  # pass 2 moved XY; the restore brought it back
    hw, cfg = _machine(ORIENTATIONS[name], DISPLACED_20X, names=("4x", "20x"), start_xy_um=ORIGIN, topography=slope)
    _, unaligned = _run(hw, cfg, align=False)
    truth = -4.0
    assert unaligned.results[0].offsets["20x"].dz_um == pytest.approx(truth + 2.0, abs=0.5)
    assert aligned.results[0].offsets["20x"].dz_um == pytest.approx(truth, abs=0.5)
    assert aligned.results[0].offsets["20x"].dx_um == pytest.approx(100.0, abs=0.5)


def test_pass_2s_computed_range_reaches_a_focus_shift_of_5_um():
    # 5% slope with a 100 um displacement: the aligned focus is 5 um from the pass-1 focus, inside
    # R2 = max(3*DOF, 5 + 0.02*100) = 7 um (spec C §6.2; the 500 um / 1% case scaled to the fake's frame)
    assert aligned_range_um(0.8, (100.0, 0.0)) == pytest.approx(7.0)
    hw, cfg = _machine(
        parcentric=DISPLACED_20X, names=("4x", "20x"), start_xy_um=ORIGIN, topography=FakeTopography(slope=(0.05, 0.0))
    )
    result, phase = _run(hw, cfg)
    assert result.cycles[0].error is None
    assert phase.results[0].offsets["20x"].dz_um == pytest.approx(-4.0, abs=0.5)


def test_a_height_step_between_the_patches_is_found_after_the_one_widening():
    # The 20x's pass-1 patch is 12 um higher than the reference's: outside R2 = 7 um, inside the widened 14
    hw, cfg = _machine(
        parcentric=DISPLACED_20X,
        names=("4x", "20x"),
        start_xy_um=ORIGIN,
        topography=FakeTopography(step_um=12.0, step_x_um=50.0),
    )
    result, phase = _run(hw, cfg)
    assert result.cycles[0].error is None
    assert result.cycles[0].objectives["20x"].focus.z_best_um == pytest.approx(8.0, abs=0.5)  # pass 1, on the step
    assert phase.results[0].offsets["20x"].dz_um == pytest.approx(-4.0, abs=0.5)


def test_a_target_too_uneven_for_the_widened_range_fails_the_cycle():
    # An 18 um step: inside pass 1's ±20 um, but 18 um from the pass-1 focus is outside the widened ±14
    hw, cfg = _machine(
        parcentric=DISPLACED_20X,
        names=("4x", "20x"),
        start_xy_um=ORIGIN,
        topography=FakeTopography(step_um=18.0, step_x_um=50.0),
    )
    result, phase = _run(hw, cfg)
    assert phase.results == []
    # focus_sweep's own widening (spec C amendment 2026-09-28) searched ±14 um and refused the rest
    assert result.cycles[0].error == (
        "20x at its aligned position: Focus is outside the computed search range; the target is too uneven. "
        "Use a flatter target."
    )
    _assert_restored(hw, xy=ORIGIN)


def test_the_shared_square_bounds_the_tilt_bias_where_a_fixed_crop_does_not():
    # A 3% tilt and a strong feature 30 um off-centre: inside the 20x frame (±41 um) but outside the
    # shared square (W = 30.7 um, ±15 um). A fixed full-frame crop follows the feature's height.
    slope, feature = 0.03, FakeFeature(centre_um=(30.0, 0.0), radius_um=5.0, gain=6.0)
    hw, cfg = _machine(
        parcentric={"4x": (0.0, 0.0), "20x": (0.0, 0.0)},
        names=("4x", "20x"),
        start_xy_um=ORIGIN,
        scene=FakeScene.random(feature=feature),
        topography=FakeTopography(slope=(slope, 0.0)),
    )
    hw.switch_objective("20x")
    frame_h = hw.frame_shape("BF")[0]
    side_um = 0.5 * 0.32 * frame_h  # W: half the smallest field of view (spec C §6.2 step 5)
    sweep = dict(hw=hw, objective="20x", channel="BF", na=0.8, center_um=0.0, range_um=20.0, fine_metric=lape)
    shared = focus_sweep(square_px=side_um / 0.32, **sweep).z_best_um
    fixed = focus_sweep(square_px=frame_h, **sweep).z_best_um
    truth = hw.objectives["20x"].z_focus_um  # the centre of the frame is at height 0
    assert abs(shared - truth) <= slope * side_um / 2 + 0.1  # the documented bound |s|·W/2 (spec C §11 R6)
    assert abs(fixed - truth) > 0.3  # the feature 0.9 um higher pulls the full-frame focus
    assert abs(fixed - truth) > 5 * abs(shared - truth)


def test_an_aligned_position_outside_the_xy_limits_is_refused_never_clamped():
    # An offsets-only run (pass 1 does not move XY) inside ±10 um of the start: S + offset_20x is outside
    hw, cfg = _machine(xy_limits_um=((START_XY[0] - 10.0, START_XY[0] + 10.0), (-50000.0, 50000.0)), cycles=2)
    cfg.measure_pixel_size = False
    cfg.saved_matrices_um_per_px = {name: obj.matrix_um_per_px for name, obj in hw.objectives.items()}
    moves = []
    original = hw.move_xy_to_um
    hw.move_xy_to_um = lambda x, y: (moves.append((x, y)), original(x, y))[1]
    result, phase = _run(hw, cfg)
    assert result.stopped is None and phase.results == []
    for cycle in result.cycles:
        assert "aligned position for 20x is outside the stage limits" in cycle.error
    assert all(START_XY[0] - 10.0 <= x <= START_XY[0] + 10.0 for x, _ in moves)
    _assert_restored(hw)


def test_an_objective_that_failed_pass_1_fails_the_cycle_and_the_run_continues():
    hw, cfg = _machine(z_focus={"4x": 0.0, "10x": 6.0, "20x": 80.0}, cycles=2)  # 20x outside ±20 um
    result, phase = _run(hw, cfg)
    assert result.stopped is None and phase.results == []
    assert all("Offsets not measured" in c.error and "20x:" in c.error for c in result.cycles)
    _assert_restored(hw)


def test_a_prior_calibration_centres_the_first_focus():
    hw, cfg = _machine(z_focus={"4x": 0.0, "10x": 6.0, "20x": 30.0}, names=("4x", "20x"))
    result, _ = _run(hw, cfg)
    assert "20x:" in result.cycles[0].error and "widen the range" in result.cycles[0].error
    hw, cfg = _machine(z_focus={"4x": 0.0, "10x": 6.0, "20x": 30.0}, names=("4x", "20x"))
    cfg.predicted_residual_um = {"20x": 30.0}
    result, phase = _run(hw, cfg)
    assert result.cycles[0].error is None
    assert phase.results[0].offsets["20x"].dz_um == pytest.approx(30.0, abs=0.5)


def test_an_offsets_only_run_registers_with_the_saved_matrices():
    hw, cfg = _machine()
    cfg.measure_pixel_size = False
    cfg.saved_matrices_um_per_px = {name: obj.matrix_um_per_px for name, obj in hw.objectives.items()}
    result, phase = _run(hw, cfg)
    assert result.stopped is None and result.pixel_sizes == {}
    assert phase.results[0].offsets["20x"].dx_um == pytest.approx(-15.0, abs=0.5)


def test_an_offsets_only_run_without_a_saved_matrix_fails_the_cycle():
    hw, cfg = _machine()
    cfg.measure_pixel_size = False
    cfg.saved_matrices_um_per_px = {"4x": hw.objectives["4x"].matrix_um_per_px}
    result, phase = _run(hw, cfg)
    assert phase.results == [] and "no pixel calibration for 10x in this cycle" in result.cycles[0].error


def test_the_changer_frame_is_in_the_raw_dz():
    # A Xeryon-like changer parks the 20x 2000 um lower; its parfocal residual is +6 um in that frame
    hw, cfg = _machine(
        z_focus={"4x": 0.0, "10x": 6.0, "20x": -2000.0 + 6.0},
        names=("4x", "20x"),
        changer_z_um={"4x": 0.0, "20x": -2000.0},
    )
    result, phase = _run(hw, cfg)
    assert result.cycles[0].error is None
    assert phase.results[0].offsets["20x"].dz_um == pytest.approx(
        -1994.0, abs=0.5
    )  # raw; the frame is subtracted at use time
    _assert_restored(hw)


def test_cancel_during_pass_2_restores():
    hw, cfg = _machine()
    run_calibration(hw, cfg, fine_metric=lape)  # pass 1 alone, to count its frames
    pass1_snaps = hw.snaps
    hw, cfg = _machine()
    cut = pass1_snaps + 10  # inside pass 2's first aligned sweep
    result = run_calibration(
        hw, cfg, fine_metric=lape, phase2=OffsetsPhase(cfg, fine_metric=lape), should_cancel=lambda: hw.snaps >= cut
    )
    assert result.stopped == "cancelled" and hw.snaps == cut
    assert hw.moves > 0
    _assert_restored(hw)


def test_an_offsets_only_run_on_a_periodic_grid_is_refused():
    # The external review's first full run: a 12 um grid, the 20x one period from the 10x. It used to pass
    # with dx = 0.0002 um, match 0.99 and runner-up 0.0; now the cycle fails, and nothing can be saved.
    hw, cfg = _machine(
        parcentric={"10x": (0.0, 0.0), "20x": (12.0, 0.0)}, names=("10x", "20x"), scene=FakeScene.grid(period_um=12.0)
    )
    cfg.measure_pixel_size = False
    cfg.saved_matrices_um_per_px = {name: obj.matrix_um_per_px for name, obj in hw.objectives.items()}
    result, phase = _run(hw, cfg)
    assert result.stopped is None and phase.results == []
    assert result.cycles[0].error.startswith(
        "Cannot uniquely match 10x and 20x: the sample remains too similar after a ~12 µm shift (self-similarity 1.00; limit 0.4)"
    )
    assert offsets_report(result, phase.results) == [f"cycle 1 offsets: {result.cycles[0].error}"]
    _assert_restored(hw, objective="10x")


@pytest.mark.parametrize("noise", [0.0, 0.002])
@pytest.mark.parametrize("period", [12.0, 20.0, 24.0, 28.0])
def test_an_offsets_only_run_refuses_a_grid_whose_next_period_lies_beyond_the_search(period, noise):
    # The external review's second full run: two 20x objectives (0.32 um/px) search ±16.5 x ±12.3 um on
    # this frame, and the second is one period away. From 20 um on the true match and the grid's other
    # peaks lie outside the search, and the run used to report dx ~ 0 (score 0.96, runner-up 0.71 at
    # 20 um; 0.94 and 0.32 at 24 um) with no warning, ready to save. The review's construction, as it ran.
    objectives = {
        "20x-air": FakeObjective("20x-air", 20, 0.8, pixel_um=0.32),
        "20x-water": FakeObjective("20x-water", 20, 1.0, pixel_um=0.32, parcentric_um=(period, 0.0)),
    }
    hw = FakeCalibrationHardware(objectives, FakeScene.grid(period_um=period), noise=noise)
    cfg = RunConfig(
        [ObjectiveSpec(name, 20, objective.na, 0.32) for name, objective in objectives.items()],
        "BF",
        search_range_um=20.0,
        cycles=1,
        measure_pixel_size=False,
        saved_matrices_um_per_px={name: objective.matrix_um_per_px for name, objective in objectives.items()},
    )
    result, phase = _run(hw, cfg)
    assert result.stopped is None and phase.results == []
    error = result.cycles[0].error
    reported = re.match(
        r"Cannot uniquely match \S+ and \S+: the sample remains too similar after a ~(\d+) µm shift \(self-similarity (\S+); limit 0\.4\)",
        error,
    )
    assert reported, error
    assert float(reported.group(1)) == pytest.approx(period, rel=0.06) and float(reported.group(2)) > 0.9
    assert offsets_report(result, phase.results) == [f"cycle 1 offsets: {error}"]
    assert hw.current_objective() == "20x-air" and hw.get_xy_um() == (0.0, 0.0) and hw.get_z_um() == 0.0


@pytest.mark.parametrize("noise", [0.0, 0.002])
def test_an_offsets_only_run_refuses_a_repeat_at_the_end_of_a_ridge(noise):
    # The external review's P1 of revision 3 (2026-09-28), as it ran: two 20x objectives, 512 x 192 frames,
    # stripes along x (70% of the variance) and a 20 um sinusoid along y, the second objective 20 um away
    # in y. The autocorrelation stays above 0.66 from zero to the repeat (0.99), and revision 3 took the
    # repeat into its lobe: dy -0.2 um, match 0.94, runner-up 0.71, ready to save.
    objectives = {
        "20x-air": FakeObjective("20x-air", 20, 0.8, pixel_um=0.32),
        "20x-water": FakeObjective("20x-water", 20, 1.0, pixel_um=0.32, parcentric_um=(0.0, 20.0)),
    }
    hw = FakeCalibrationHardware(objectives, FakeScene.stripes(20.0, 0.7), shape=(192, 512), noise=noise)
    cfg = RunConfig(
        [ObjectiveSpec(name, 20, objective.na, 0.32) for name, objective in objectives.items()],
        "BF",
        search_range_um=20.0,
        cycles=1,
        measure_pixel_size=False,
        saved_matrices_um_per_px={name: objective.matrix_um_per_px for name, objective in objectives.items()},
    )
    result, phase = _run(hw, cfg)
    assert result.stopped is None and phase.results == []
    refused = re.match(
        r"Cannot uniquely match \S+ and \S+: the sample remains too similar after a ~(\d+) µm shift \(self-similarity 0\.99; limit 0\.4\)",
        result.cycles[0].error,
    )
    assert refused and float(refused.group(1)) == pytest.approx(20.0, rel=0.06), result.cycles[0].error
    assert hw.current_objective() == "20x-air" and hw.get_xy_um() == (0.0, 0.0) and hw.get_z_um() == 0.0


def test_equal_magnification_objectives_calibrate_end_to_end():
    # The external review's pair: two 20x objectives (NA 0.8 and 1.0) with the same pixel size
    objectives = {
        "20x-air": FakeObjective("20x-air", 20, 0.8, pixel_um=0.32),
        "20x-water": FakeObjective("20x-water", 20, 1.0, pixel_um=0.32, z_focus_um=3.0, parcentric_um=(2.0, -1.0)),
    }
    hw = FakeCalibrationHardware(
        objectives, FakeScene.random(), start_objective="20x-air", start_xy_um=START_XY, start_z_um=START_Z
    )
    specs = [ObjectiveSpec(name, 20, objective.na, 0.32) for name, objective in objectives.items()]
    result, phase = _run(hw, RunConfig(specs, "BF", search_range_um=20.0, cycles=1))
    assert result.stopped is None and result.cycles[0].error is None
    [cycle] = phase.results
    assert cycle.reference == "20x-air"  # equal magnifications keep the objective list's order
    offset = cycle.offsets["20x-water"]
    assert (offset.dx_um, offset.dy_um) == pytest.approx((2.0, -1.0), abs=0.2)
    assert offset.dz_um == pytest.approx(3.0, abs=0.3)
    _assert_restored(hw, objective="20x-air")


class _JamsOnPass2:
    """The fake, with its changer jamming on the second switch to `name` (pass 2's): from then on no
    objective is confirmed and every switch faults, like B1's jammed-turret reproducer."""

    def __init__(self, hw, name):
        self._hw, self._name, self._switches, self.jammed = hw, name, 0, False
        self.z_moves_after_the_jam = []

    def __getattr__(self, attribute):
        return getattr(self._hw, attribute)

    def current_objective(self):
        return None if self.jammed else self._hw.current_objective()

    def switch_objective(self, name):
        self._switches += name == self._name
        self.jammed = self.jammed or self._switches == 2
        if self.jammed:
            raise RuntimeError("turret jammed; objective unknown")
        self._hw.switch_objective(name)

    def move_z_to_um(self, z_um):
        if self.jammed:
            self.z_moves_after_the_jam.append(z_um)
        self._hw.move_z_to_um(z_um)


def test_a_changer_fault_in_pass_2_never_moves_z_with_the_objective_unconfirmed():
    # B1's restore rule holds through pass 2: XY goes back, Z stays where the changer left it
    fake, cfg = _machine()
    hw = _JamsOnPass2(fake, "10x")
    result = run_calibration(hw, cfg, fine_metric=lape, phase2=OffsetsPhase(cfg, fine_metric=lape))
    assert result.restore_failed and "not confirmed" in result.stopped
    assert hw.jammed and hw.z_moves_after_the_jam == []
    assert fake.get_xy_um() == pytest.approx(START_XY)


def test_the_offsets_report_has_every_measured_value():
    hw, cfg = _machine()
    result, phase = _run(hw, cfg)
    lines = offsets_report(result, phase.results)
    assert lines[0].startswith("cycle 1 offsets: reference 4x, focus ")
    for pair in ("4x-10x", "4x-20x"):
        [line] = [line for line in lines if line.startswith(f"cycle 1 {pair}: ")]
        for value in ("dx ", "dy ", "dz ", "aligned focus ", "match ", "runner-up ", "self-similarity "):
            assert value in line, (value, line)
    # the search area's reach on this frame (192 x 256 px): 20% of the 4x's field is not the limit here
    assert "4x-20x: " in lines[2] and lines[2].endswith("measurable to ±164.0 µm in x and ±123.2 µm in y")
    [closure] = [line for line in lines if line.startswith("cycle 1 10x-20x (closure pair): ")]
    for value in ("closure ", "match ", "runner-up ", "self-similarity ", "measurable to ±"):
        assert value in closure, (value, closure)


def test_the_closure_pair_that_does_not_fit_is_reported_not_measurable():
    hw, cfg = _machine(parcentric={"4x": (0.0, 0.0), "10x": (80.0, 0.0), "20x": (-80.0, 0.0)})
    result, phase = _run(hw, cfg)
    assert result.cycles[0].error is None
    [cycle] = phase.results
    closure = next(p for p in cycle.pairs if (p.lower, p.higher) == ("10x", "20x"))
    assert not closure.measurable
    assert cycle.offsets["20x"].closure_error_um is None
    assert depth_of_field_um(0.8) < aligned_range_um(0.8, (80.0, 0.0)) == pytest.approx(6.6)
