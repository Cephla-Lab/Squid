import numpy as np
import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.engine import (
    ObjectiveSpec,
    RunConfig,
    cycle_report,
    first_cycle_residuals_um,
    run_calibration,
)
from squid.objective_calibration.engine import _restore
from squid.objective_calibration.hardware import RestoreError, RunCancelled
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _machine(scene=None, **kw):
    objectives = {
        "4x": FakeObjective("4x", 4, 0.13, pixel_um=1.6 * 1.02, z_focus_um=0.0),
        "10x": FakeObjective("10x", 10, 0.3, pixel_um=0.64 * 0.98, z_focus_um=6.0),
        "20x": FakeObjective("20x", 20, 0.8, pixel_um=0.32 * 1.01, z_focus_um=-4.0),
    }
    hw = FakeCalibrationHardware(
        objectives,
        scene or FakeScene.random(),
        start_objective="10x",
        start_xy_um=(1000.0, 2000.0),
        start_z_um=1.0,
        **kw
    )
    specs = [
        ObjectiveSpec("20x", 20, 0.8, 0.32),
        ObjectiveSpec("4x", 4, 0.13, 1.6),
        ObjectiveSpec("10x", 10, 0.3, 0.64),
    ]
    return hw, RunConfig(specs, "BF", search_range_um=20.0, cycles=2)


def _assert_restored(hw):
    assert hw.current_objective() == "10x"
    assert hw.get_xy_um() == pytest.approx((1000.0, 2000.0))
    assert hw.get_z_um() == pytest.approx(1.0)


def test_full_run_measures_every_objective_and_restores():
    hw, cfg = _machine()
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped is None and len(result.cycles) == 2
    for name, truth in (("4x", 1.6 * 1.02), ("10x", 0.64 * 0.98), ("20x", 0.32 * 1.01)):
        summary = result.pixel_sizes[name]
        assert summary.cycles == 2
        assert summary.pixel_size_um == pytest.approx(truth, rel=0.003)
        assert summary.std_pixel_size_um is not None
        assert summary.orientation_matches_mosaic
    _assert_restored(hw)


def test_objectives_run_in_ascending_magnification():
    hw, cfg = _machine()
    seen = []
    original = hw.switch_objective
    hw.switch_objective = lambda name: (seen.append(name), original(name))[1]
    run_calibration(hw, cfg, fine_metric=lape)
    assert seen[:3] == ["4x", "10x", "20x"]


def test_phase2_hook_receives_this_cycles_matrices():
    hw, cfg = _machine()
    seen = []
    run_calibration(
        hw, cfg, fine_metric=lape, phase2=lambda hw, cycle, view: seen.append((cycle.index, view.pixel_size_um("20x")))
    )
    assert [index for index, _ in seen] == [0, 1]
    assert all(px == pytest.approx(0.32 * 1.01, rel=0.005) for _, px in seen)


def test_untextured_objective_fails_its_gate_and_the_cycle_restores():
    hw, cfg = _machine(scene=FakeScene.flat(), noise=0.0)  # noise-free: the gate outcome is deterministic
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped is None
    assert all("No focus peak" in r.error for r in result.cycles[0].objectives.values())
    assert result.pixel_sizes == {}
    _assert_restored(hw)


def test_unexpected_exception_restores_then_stops_the_run():
    hw, cfg = _machine()
    hw.fail_snap_at = 5
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped and "camera fault" in result.stopped
    assert len(result.cycles) == 1
    _assert_restored(hw)


def test_failed_restore_stops_the_run():
    hw, cfg = _machine()
    original = hw.switch_objective
    calls = []

    def switch(name):
        calls.append(name)
        if len(calls) == 4:  # cycle 1 switches to 4x, 10x, 20x; the fourth switch is the restore to 10x
            raise RuntimeError("changer fault on restore")
        original(name)

    hw.switch_objective = switch
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped.startswith("Restoring")
    assert result.restore_failed
    assert len(result.cycles) == 1


def test_cancel_restores_the_start():
    hw, cfg = _machine()
    result = run_calibration(hw, cfg, fine_metric=lape, should_cancel=lambda: hw.snaps >= 40)
    assert result.stopped == "cancelled"
    assert hw.snaps == 40  # answered before the next frame, not at the next objective
    _assert_restored(hw)


def test_declined_switch_cancels_and_restores():
    hw, cfg = _machine()
    original = hw.switch_objective
    calls = []

    def switch(name):
        calls.append(name)
        if name == "20x" and len(calls) == 3:  # the operator declines the manual switch to 20x
            raise RunCancelled("Objective switch declined")
        original(name)

    hw.switch_objective = switch
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped == "cancelled"
    assert len(result.cycles) == 1
    _assert_restored(hw)


def test_a_failed_focus_returns_z_so_the_next_objectives_still_succeed():
    hw, cfg = _machine()
    cfg.cycles = 1
    hw.objectives["4x"].z_focus_um = 92.0  # at the edge of 4x's ±97.5 um sweep: "widen the range"
    result = run_calibration(hw, cfg, fine_metric=lape)
    cycle = result.cycles[0]
    assert "widen the range" in cycle.objectives["4x"].error
    assert cycle.objectives["10x"].error is None and cycle.objectives["20x"].error is None
    assert set(result.pixel_sizes) == {"10x", "20x"}
    _assert_restored(hw)


def _predicted_machine(z_focus_10x_um, matrix_10x=None):
    """4x, 10x and 20x whose saved parfocal residuals (0, 30 and 45 um) predict each focus from the Z of
    the last objective focused."""
    objectives = {
        "4x": FakeObjective("4x", 4, 0.13, pixel_um=1.6, z_focus_um=0.0),
        "10x": FakeObjective("10x", 10, 0.3, matrix_um_per_px=matrix_10x, pixel_um=0.64, z_focus_um=z_focus_10x_um),
        "20x": FakeObjective("20x", 20, 0.8, pixel_um=0.32, z_focus_um=45.0),
    }
    hw = FakeCalibrationHardware(objectives, FakeScene.random(), start_objective="4x", start_z_um=0.0)
    specs = [
        ObjectiveSpec("4x", 4, 0.13, 1.6),
        ObjectiveSpec("10x", 10, 0.3, 0.64),
        ObjectiveSpec("20x", 20, 0.8, 0.32),
    ]
    cfg = RunConfig(specs, "BF", search_range_um=20.0, cycles=1, predicted_residual_um={"10x": 30.0, "20x": 45.0})
    return hw, cfg


@pytest.mark.parametrize("failure", ["before its focus", "after its focus"])
def test_a_failed_objective_leaves_the_next_prediction_on_the_last_one_focused(failure):
    # 10x fails before its focus (its focus lies far outside the prediction) or after it (its pixel scale
    # differs by 5% between x and y). Either way Z returns to 4x's focus, so 20x is predicted from 4x.
    if failure == "before its focus":
        hw, cfg = _predicted_machine(300.0)
    else:
        hw, cfg = _predicted_machine(30.0, matrix_10x=np.diag([0.64, 0.64 * 1.05]))
    objectives = run_calibration(hw, cfg, fine_metric=lape).cycles[0].objectives
    assert objectives["10x"].error is not None
    assert objectives["20x"].error is None, objectives["20x"].error
    assert objectives["20x"].focus.z_best_um == pytest.approx(45.0, abs=1.0)


def test_a_start_near_the_lower_xy_limit_restores_without_the_pre_move():
    hw, cfg = _machine(xy_limits_um=((990.0, 50000.0), (-50000.0, 50000.0)))
    cfg.cycles = 1
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped is None
    assert all("stage limits" in r.error for r in result.cycles[0].objectives.values())
    _assert_restored(hw)


def test_a_failed_xy_restore_still_restores_z_and_the_objective():
    hw, cfg = _machine()
    cfg.cycles = 1
    original = hw.move_xy_to_um
    armed = {"on": False}

    def move(x, y):
        if armed["on"]:
            raise RuntimeError("XY stage fault")
        original(x, y)

    hw.move_xy_to_um = move
    # The phase-2 hook runs just before the restore: arm the XY fault there.
    result = run_calibration(hw, cfg, fine_metric=lape, phase2=lambda hw, cycle, view: armed.update(on=True))
    assert result.stopped.startswith("Restoring") and "XY: XY stage fault" in result.stopped
    assert hw.current_objective() == "10x"
    assert hw.get_z_um() == pytest.approx(1.0)


def test_cycle_report_has_one_line_per_objective_per_cycle_with_the_gate_values():
    hw, cfg = _machine()
    cfg.cycles = 1
    hw.objectives["4x"].z_focus_um = 92.0  # 4x fails; 10x and 20x succeed
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert not result.restore_failed
    lines = cycle_report(result)
    assert len(lines) == 3
    assert lines[0].startswith("cycle 1 4x: ") and "widen the range" in lines[0]
    for line in lines[1:]:
        for field in ("focus", "rise", "µm/px", "rotation", "anisotropy", "residual", "drift"):
            assert field in line, (field, line)


class _JammedTurret:
    """The external review's reproducer: the turret never confirms any objective."""

    def __init__(self):
        self.calls = []

    def current_objective(self):
        return None

    def switch_objective(self, name):
        self.calls.append(("switch", name))
        raise RuntimeError("turret jammed; objective unknown")

    def xy_limits_um(self):
        return ((-10000, 10000), (-10000, 10000))

    def move_xy_to_um(self, x, y):
        self.calls.append(("xy", x, y))

    def move_z_to_um(self, z):
        self.calls.append(("z", z))


def test_an_unconfirmed_objective_never_gets_the_old_focus_z():
    hw = _JammedTurret()
    with pytest.raises(RestoreError, match="objective unknown"):
        _restore(hw, "4x", 1000, 2000, 5000)
    assert ("xy", 1000, 2000) in hw.calls  # XY is still put back: a lateral move is safe
    assert not any(call[0] == "z" for call in hw.calls), hw.calls


def test_a_failed_switch_back_leaves_z_where_the_changer_left_it():
    hw, cfg = _machine()
    cfg.cycles = 1
    hw.fail_switch_to = "10x"  # the switch to 10x faults, and so does the restore back to it
    original_move_z = hw.move_z_to_um
    z_moves_after_the_fault = []

    def move_z(z):
        if hw.faulted:
            z_moves_after_the_fault.append(z)
        original_move_z(z)

    original_switch = hw.switch_objective
    hw.faulted = False

    def switch(name):
        if name == "10x":
            hw.faulted = True
        original_switch(name)

    hw.switch_objective = switch
    hw.move_z_to_um = move_z
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.restore_failed and "not confirmed" in result.stopped
    assert z_moves_after_the_fault == []
    assert hw.get_xy_um() == pytest.approx((1000.0, 2000.0))


def _coarse_centre_um(cycle, name):
    """The centre of the objective's coarse sweep: the mean Z of its first level (an even grid)."""
    return float(np.mean(cycle.objectives[name].focus.levels[0].z_um))


@pytest.mark.parametrize("changer_z_um", [None, {"20x": -2000.0}])
def test_with_no_saved_prediction_the_cycles_after_the_first_centre_on_cycle_1s_focus(changer_z_um):
    # Spec C §8.2: the 4x focus lies 60 um from the start Z, found by cycle 1's ±100 um search; every
    # later cycle centres on it (within the hand-focus error, start Z minus the 10x focus = -5 um), so
    # its search never depends on the cycle before it. A changer that parks 20x 2 mm lower is inside
    # "Z after the switch" and does not disturb the chain.
    hw, cfg = _machine(changer_z_um=changer_z_um)
    hw.objectives["4x"].z_focus_um = 60.0
    if changer_z_um:
        hw.objectives["20x"].z_focus_um += changer_z_um["20x"]
    cfg.search_range_um, cfg.cycles, cfg.predict_from_first_cycle = 100.0, 3, True
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert result.stopped is None and all(c.completed for c in result.cycles)
    assert _coarse_centre_um(result.cycles[0], "4x") == pytest.approx(1.0, abs=0.5)  # the start Z, no prediction
    for cycle in result.cycles[1:]:
        assert _coarse_centre_um(cycle, "4x") == pytest.approx(60.0 - 5.0, abs=1.0)
        for name in ("4x", "10x", "20x"):
            assert cycle.objectives[name].focus.z_best_um == pytest.approx(hw.objectives[name].z_focus_um, abs=0.5)
    _assert_restored(hw)


def test_a_saved_prediction_is_kept_and_without_the_flag_nothing_is_predicted():
    hw, cfg = _machine()
    hw.objectives["4x"].z_focus_um = 60.0
    cfg.search_range_um, cfg.cycles = 100.0, 2
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert [_coarse_centre_um(c, "4x") for c in result.cycles] == pytest.approx([1.0, 1.0], abs=0.5)
    hw, cfg = _machine()
    cfg.cycles, cfg.predict_from_first_cycle = 2, True
    cfg.predicted_residual_um = {"4x": 10.0, "10x": 0.0, "20x": 0.0}  # the saved calibration wins over cycle 1
    result = run_calibration(hw, cfg, fine_metric=lape)
    assert [_coarse_centre_um(c, "4x") for c in result.cycles] == pytest.approx([11.0, 11.0], abs=0.5)


def test_cycle_1_residuals_need_every_objective_focused():
    hw, cfg = _machine()
    cfg.cycles = 1
    hw.objectives["4x"].z_focus_um = 92.0  # 4x fails its gate; 10x and 20x succeed
    cycle = run_calibration(hw, cfg, fine_metric=lape).cycles[0]
    assert cycle.completed and cycle.objectives["4x"].error
    assert first_cycle_residuals_um(cycle, ["4x", "10x", "20x"]) == {}
    hw, cfg = _machine()
    cfg.cycles = 1
    cycle = run_calibration(hw, cfg, fine_metric=lape).cycles[0]
    residuals = first_cycle_residuals_um(cycle, ["4x", "10x", "20x"])
    # The chain is the true parfocal frame up to the hand-focus error (start Z 1 um, 10x focus 6 um):
    # 4x - 10x should be 0 - 6, 20x - 10x should be -4 - 6
    assert residuals["4x"] - residuals["10x"] == pytest.approx(-6.0, abs=0.5)
    assert residuals["20x"] - residuals["10x"] == pytest.approx(-10.0, abs=0.5)


def test_an_interrupted_cycle_is_not_completed_and_the_timing_is_recorded():
    hw, cfg = _machine()
    result = run_calibration(hw, cfg, fine_metric=lape, should_cancel=lambda: hw.snaps >= 40)
    assert result.stopped == "cancelled"
    [cycle] = result.cycles
    assert not cycle.completed and cycle.started_at > 0
    hw, cfg = _machine()
    cfg.cycles = 1
    [cycle] = run_calibration(hw, cfg, fine_metric=lape).cycles
    assert cycle.completed
    for r in cycle.objectives.values():
        assert r.timestamp >= cycle.started_at and r.z_after_switch_um is not None
