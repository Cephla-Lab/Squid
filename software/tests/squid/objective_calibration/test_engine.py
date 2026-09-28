import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.engine import ObjectiveSpec, RunConfig, run_calibration
from squid.objective_calibration.hardware import RunCancelled
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
        hw, cfg, fine_metric=lape, phase2=lambda cycle, view: seen.append((cycle.index, view.pixel_size_um("20x")))
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
    result = run_calibration(hw, cfg, fine_metric=lape, phase2=lambda cycle, view: armed.update(on=True))
    assert result.stopped.startswith("Restoring") and "XY: XY stage fault" in result.stopped
    assert hw.current_objective() == "10x"
    assert hw.get_z_um() == pytest.approx(1.0)
