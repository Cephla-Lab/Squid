"""The factory repeatability report (spec C §8.3) from synthetic cycles: the files exist, the summary
statistics match values computed here, pair rows are present, the detrend removes a known drift."""

import csv
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure
from squid.objective_calibration.engine import (
    CycleResult,
    ObjectiveCycleResult,
    ObjectiveSpec,
    RunConfig,
    RunResult,
    run_calibration,
)
from squid.objective_calibration.focus import FocusResult, SweepLevel
from squid.objective_calibration.offsets import OffsetsPhase
from squid.objective_calibration.report import (
    CYCLE_COLUMNS,
    DETRENDED_QUANTITY,
    FOCUS_CURVE_COLUMNS,
    SUMMARY_COLUMNS,
    CycleRow,
    ReportHeader,
    cycle_rows,
    detrend,
    run_status,
    summary_rows,
    write_report,
)
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

START_XY = (1000.0, 2000.0)
PARCENTRIC = {"4x": (0.0, 0.0), "10x": (12.0, -7.0), "20x": (-15.0, 9.0)}
Z_FOCUS = {"4x": 0.0, "10x": 6.0, "20x": -4.0}


def lape(crop):
    return float(calculate_focus_measure(crop, FocusMeasureOperator.LAPE))


def _header(n=3):
    return ReportHeader(
        ini_name="configuration_Squid+",
        squid_commit="abc123 (dirty=False)",
        channel="BF",
        exposures_ms={"4x": 10.0, "10x": 12.0, "20x": None},
        cycles_requested=n,
        site_xy_um=START_XY,
        date="2026-10-09T10:00:00",
    )


def _synthetic_run(cycles=3, **kw):
    optics = {"4x": (4, 0.13, 1.6), "10x": (10, 0.3, 0.64), "20x": (20, 0.8, 0.32)}
    objectives = {
        name: FakeObjective(name, mag, na, pixel_um=px, z_focus_um=Z_FOCUS[name], parcentric_um=PARCENTRIC[name])
        for name, (mag, na, px) in optics.items()
    }
    hw = FakeCalibrationHardware(
        objectives,
        FakeScene.random(),
        start_objective="4x",
        start_xy_um=START_XY,
        start_z_um=1.0,
        positioning_noise_um=0.1,
        microstep_um=0.4,
        **kw,
    )
    specs = [ObjectiveSpec(name, mag, na, px) for name, (mag, na, px) in optics.items()]
    cfg = RunConfig(specs, "BF", search_range_um=20.0, cycles=cycles, predict_from_first_cycle=True)
    phase = OffsetsPhase(cfg, fine_metric=lape)
    result = run_calibration(hw, cfg, fine_metric=lape, phase2=phase)
    return result, phase


def _read_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def _floats(rows, key, **match):
    return [float(r[key]) for r in rows if all(r[k] == v for k, v in match.items()) and r[key] != ""]


def _summary(rows, objective, quantity):
    [row] = [r for r in rows if r["objective"] == objective and r["quantity"] == quantity]
    return row


def _assert_statistics(summary_row, values):
    v = np.asarray(values)
    assert int(summary_row["n"]) == len(v)
    assert float(summary_row["mean"]) == pytest.approx(v.mean())
    assert float(summary_row["std"]) == pytest.approx(v.std(ddof=1))
    assert float(summary_row["min"]) == pytest.approx(v.min())
    assert float(summary_row["max"]) == pytest.approx(v.max())
    assert float(summary_row["p95_abs_dev"]) == pytest.approx(np.percentile(np.abs(v - v.mean()), 95))


def test_the_report_from_three_synthetic_cycles(tmp_path):
    result, phase = _synthetic_run()
    assert result.stopped is None and len(phase.results) == 3, [c.error for c in result.cycles]
    report = write_report(tmp_path / "out", result, phase.results, _header())
    folder = tmp_path / "out"
    assert report == folder / "report.html"
    for name in ("cycles.csv", "focus_curves.csv", "summary.csv", "report.html"):
        assert (folder / name).is_file(), name
    for quantity in ("z_focus_um", "dz_um", "dx_um", "dy_um", "pixel_size_um", "rotation_deg"):
        assert (folder / f"hist_{quantity}.png").is_file() and (folder / f"trend_{quantity}.png").is_file()
    for name in ("4x", "10x", "20x"):
        assert (folder / f"focus_curves_{name}.png").is_file()

    cycles = _read_csv(folder / "cycles.csv")
    assert list(cycles[0]) == CYCLE_COLUMNS
    assert len(cycles) == 9 and all(r["status"] == "ok" for r in cycles)
    assert [r["objective"] for r in cycles[:3]] == ["4x", "10x", "20x"]
    assert _floats(cycles, "dz_um", objective="4x") == [0.0, 0.0, 0.0]  # the reference, by definition
    for name in ("10x", "20x"):
        truth = Z_FOCUS[name] - Z_FOCUS["4x"]
        assert all(abs(dz - truth) < 1.0 for dz in _floats(cycles, "dz_um", objective=name))
        assert all(abs(dx - PARCENTRIC[name][0]) < 0.5 for dx in _floats(cycles, "dx_um", objective=name))
    assert all(abs(px - 0.32) < 0.01 for px in _floats(cycles, "pixel_size_um", objective="20x"))
    timestamps = [float(r["timestamp"]) for r in cycles]
    assert timestamps == sorted(timestamps) and timestamps[0] > 1e9

    curves = _read_csv(folder / "focus_curves.csv")
    assert list(curves[0]) == FOCUS_CURVE_COLUMNS
    assert {r["metric"] for r in curves} == {"highpass_std", "fine"}
    assert {(r["cycle"], r["objective"]) for r in curves} == {(str(c), o) for c in (1, 2, 3) for o in Z_FOCUS}

    summary = _read_csv(folder / "summary.csv")
    assert list(summary[0]) == SUMMARY_COLUMNS
    # per objective and quantity, against statistics computed here from cycles.csv
    for name in ("10x", "20x"):
        for quantity in ("z_focus_um", "dz_um", "dx_um", "dy_um", "pixel_size_um", "rotation_deg"):
            _assert_statistics(_summary(summary, name, quantity), _floats(cycles, quantity, objective=name))
    _assert_statistics(_summary(summary, "4x", "z_focus_um"), _floats(cycles, "z_focus_um", objective="4x"))
    assert not [r for r in summary if r["objective"] == "4x" and r["quantity"] in ("dx_um", "dy_um", "dz_um")]
    # Z both ways: std of dz_k and the detrended std of z_focus_k, for every objective
    for name in Z_FOCUS:
        row = _summary(summary, name, DETRENDED_QUANTITY)
        assert int(row["n"]) == 3 and float(row["mean"]) == pytest.approx(0.0, abs=1e-9)
    # pair rows for every (i, j): dz_j - dz_i and the XY difference per axis
    dz = {name: _floats(cycles, "dz_um", objective=name) for name in Z_FOCUS}
    dx = {name: _floats(cycles, "dx_um", objective=name) for name in Z_FOCUS}
    for lower, higher in (("4x", "10x"), ("4x", "20x"), ("10x", "20x")):
        pair = f"{lower}-{higher}"
        _assert_statistics(_summary(summary, pair, "pair_dz_um"), np.subtract(dz[higher], dz[lower]))
        _assert_statistics(_summary(summary, pair, "pair_dx_um"), np.subtract(dx[higher], dx[lower]))
        assert _summary(summary, pair, "pair_dy_um")["n"] == "3"
    # a pair with the reference is that objective's own dz row
    assert _summary(summary, "4x-20x", "pair_dz_um")["std"] == _summary(summary, "20x", "dz_um")["std"]

    page = report.read_text(encoding="utf-8")
    for text in (
        "configuration_Squid+",
        "abc123 (dirty=False)",
        "4x, 10x, 20x",
        "BF",
        "4x 10 ms, 10x 12 ms, 20x",
        "3 completed of 3 requested (completed)",
        "2026-10-09T10:00:00",
        "1000.0, 2000.0",
        "data:image/png;base64,",
        "pair_dz_um",
        "Failed cycles</h2><p>None.</p>",
    ):
        assert text in page, text
    images = sorted(p.name for p in (folder / "images").iterdir())
    assert images == ["cycle_01_10x.tiff", "cycle_01_20x.tiff", "cycle_01_4x.tiff"]  # cycle 1 only: no failures
    frame = cv2.imread(str(folder / "images" / "cycle_01_20x.tiff"), cv2.IMREAD_UNCHANGED)
    assert frame.dtype == np.uint16 and frame.shape == (192, 256)


def test_the_detrend_removes_a_known_linear_drift():
    # z = 100 + 2 um/s * t plus residuals orthogonal to the line (sum 0, sum r*t 0), so the ordinary
    # least-squares fit recovers the line exactly and the detrended std is the residuals' own
    t = np.arange(5.0)
    residuals = np.array([1.0, -2.0, 0.0, 2.0, -1.0])
    z = 100.0 + 2.0 * t + residuals
    detrended, slope_um_per_min = detrend(t, z)
    assert detrended == pytest.approx(residuals)
    assert slope_um_per_min == pytest.approx(120.0)
    rows = [CycleRow(k + 1, float(t[k]), "20x", z_focus_um=float(z[k])) for k in range(5)]
    summary, slopes = summary_rows(rows)
    raw = next(r for r in summary if r.quantity == "z_focus_um")
    detrended_row = next(r for r in summary if r.quantity == DETRENDED_QUANTITY)
    assert raw.std == pytest.approx(np.std(z, ddof=1)) and raw.std > 2.0
    # the fitted line used two degrees of freedom: sqrt((1 + 4 + 0 + 4 + 1) / (5 - 2))
    assert detrended_row.std == pytest.approx(math.sqrt(10 / 3))
    assert detrended_row.n == 5
    assert (detrended_row.max, detrended_row.min) == pytest.approx((2.0, -2.0))
    assert slopes == {"20x": pytest.approx(120.0)}


def test_fewer_than_three_cycles_have_no_detrended_row_and_one_cycle_no_std():
    rows = [CycleRow(1, 0.0, "20x", z_focus_um=1.0), CycleRow(2, 1.0, "20x", z_focus_um=2.0)]
    summary, slopes = summary_rows(rows)
    assert [r.quantity for r in summary] == ["z_focus_um"] and summary[0].std == pytest.approx(math.sqrt(0.5))
    assert slopes == {}
    summary, _ = summary_rows(rows[:1])
    assert summary[0].std is None and summary[0].n == 1


def _focus(z):
    return FocusResult(z, [SweepLevel("highpass_std", [z - 1, z, z + 1], [1.0, 2.0, 1.0])], 0.5)


def _hand_built_result(stopped=None, restore_failed=False):
    """Cycle 1 ok, cycle 2 with a 20x gate failure (the cycle completed), cycle 3 interrupted."""
    image = np.full((4, 6), 1000, dtype=np.uint16)
    cycles = []
    for index in range(3):
        cycle = CycleResult(index, start_objective="4x", started_at=1000.0 + 10 * index, completed=index < 2)
        for name, z in (("4x", 0.0), ("20x", -4.0)):
            r = ObjectiveCycleResult(name, focus=_focus(z), image=image, timestamp=cycle.started_at + 1)
            if index == 1 and name == "20x":
                r.error, r.focus, r.image = "Focus moved during the sweep (drift or backlash); retry.", None, None
            if index == 2 and name == "20x":
                r.focus = None  # cancelled before its sweep
            cycle.objectives[name] = r
        if index == 1:
            cycle.error = "Offsets not measured in this cycle. 20x: Focus moved"
        cycles.append(cycle)
    return RunResult(cycles, {}, stopped, restore_failed)


def test_failed_and_interrupted_cycles_are_reported_and_the_run_status_counts_completed_cycles(tmp_path):
    result = _hand_built_result(stopped="cancelled")
    assert run_status(result) == "cancelled after 2 cycles"
    assert run_status(_hand_built_result()) == "completed"
    assert run_status(_hand_built_result(stopped="Restoring 4x failed: x", restore_failed=True)).startswith(
        "stopped after 2 cycles: Restoring"
    )
    rows = cycle_rows(result, [])
    assert [(r.cycle, r.objective, r.status) for r in rows] == [
        (1, "4x", "ok"),
        (1, "20x", "ok"),
        (2, "4x", "cycle: Offsets not measured in this cycle. 20x: Focus moved"),
        (2, "20x", "Focus moved during the sweep (drift or backlash); retry."),
        (3, "4x", "interrupted"),
        (3, "20x", "interrupted"),
    ]
    assert rows[1].z_focus_um == -4.0 and rows[1].dz_um is None  # no offsets phase result: no dz
    report = write_report(tmp_path, result, [], _header(n=10))
    page = report.read_text(encoding="utf-8")
    assert "2 completed of 10 requested (cancelled after 2 cycles)" in page
    assert "cycle 2: 20x: Focus moved during the sweep (drift or backlash); retry.; Offsets not measured" in page
    assert "cycle 3: cancelled" in page
    # images of cycle 1 and of every failed or interrupted cycle, for the objectives that have one
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == [
        "cycle_01_20x.tiff",
        "cycle_01_4x.tiff",
        "cycle_02_4x.tiff",
        "cycle_03_20x.tiff",
        "cycle_03_4x.tiff",
    ]
    summary = _read_csv(tmp_path / "summary.csv")
    assert {r["objective"] for r in summary} == {"4x", "20x"}  # one ok cycle each: n 1, no std, no pairs
    assert all(r["n"] == "1" and r["std"] == "" for r in summary)
    # the failed cycle's partial curves are still in focus_curves.csv, and the plots exist for what was measured
    assert {(r["cycle"], r["objective"]) for r in _read_csv(tmp_path / "focus_curves.csv")} == {
        ("1", "4x"),
        ("1", "20x"),
        ("2", "4x"),
        ("3", "4x"),
    }
    assert (tmp_path / "trend_z_focus_um.png").is_file() and not (tmp_path / "hist_dz_um.png").exists()
