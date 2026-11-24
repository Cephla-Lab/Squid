"""The per-FOV laser AF record: laser_af.csv, laser_af_summary.json, the focus-surface fit, the optional frames."""

import csv
import json
import math
import os

import numpy as np
import pytest

from control.core.laser_af_record import COLUMNS, LaserAFRecord, focus_surface, summarize


def _grid(n=5, pitch_mm=1.0, x0=10.0, y0=20.0):
    x, y = np.meshgrid(x0 + pitch_mm * np.arange(n), y0 + pitch_mm * np.arange(n))
    return x.ravel(), y.ravel()


def test_focus_surface_of_a_dome():
    x, y = _grid()  # 10..14 x 20..24 mm, centre (12, 22)
    z = 320.0 + 1.5 * (x - 12) - 0.5 * (y - 22) - 2.0 * ((x - 12) ** 2 + (y - 22) ** 2)
    fs = focus_surface(x, y, z)
    assert fs["model"] == "quadratic" and fs["n_points"] == 25
    assert (fs["centre_x_mm"], fs["centre_y_mm"]) == (12.0, 22.0)
    assert fs["centre_z_um"] == pytest.approx(320.0)
    assert fs["dome_um"] == pytest.approx(np.ptp(z))
    assert fs["residual_rms_um"] == pytest.approx(0.0, abs=1e-9)


def test_focus_surface_residual_is_the_noise():
    x, y = _grid(n=30, pitch_mm=0.3)
    noise = np.random.default_rng(0).normal(0, 0.5, x.size)
    fs = focus_surface(x, y, 300.0 + 0.2 * x + noise)
    assert fs["residual_rms_um"] == pytest.approx(0.5, rel=0.1)


@pytest.mark.parametrize(
    "x, y, model",
    [
        ([0, 1, 0], [0, 0, 1], "plane"),  # too few points for a quadratic
        ([0, 1, 2, 3, 4, 5, 6], [0] * 7, "constant"),  # one row of FOVs: no plane through it
        ([5], [5], "constant"),
    ],
)
def test_focus_surface_falls_back_to_what_the_points_hold(x, y, model):
    fs = focus_surface(x, y, [300.0 + i for i in range(len(x))])
    assert fs["model"] == model


def test_focus_surface_without_points():
    assert focus_surface([], [], []) is None
    assert focus_surface([1.0], [1.0], [math.nan]) is None


def _row(**kw):
    row = {c: "" for c in COLUMNS}
    row.update(
        time=0.0,
        time_point=0,
        region="A1",
        fov=0,
        x_mm=0.0,
        y_mm=0.0,
        z_before_um=0.0,
        z_after_um=300.0,
        displacement_um=1.0,
        correlation=0.9,
        ok=True,
        target_um=0.0,
        af_time_s=0.2,
    )
    row.update(kw)
    return row


def test_summarize_counts_and_displacements():
    rows = [_row(displacement_um=d) for d in range(-10, 11)]  # |d| 0..10
    rows += [_row(ok=False, displacement_um=math.nan)]
    s = summarize(rows)
    assert (s["n"], s["ok"]) == (22, 21)
    assert s["abs_displacement_um_max"] == 10.0
    assert s["abs_displacement_um_p95"] == pytest.approx(np.percentile(np.abs(np.arange(-10, 11)), 95))


def test_summarize_fits_the_reference_plane_less_the_region_offset():
    x, y = _grid()
    rows = [_row(x_mm=a, y_mm=b, z_after_um=305.0, target_um=5.0) for a, b in zip(x, y)]
    fs = summarize(rows)["focus_surface"]
    assert fs["centre_z_um"] == pytest.approx(300.0) and fs["dome_um"] == pytest.approx(0.0, abs=1e-9)


def test_the_record_writes_rows_as_they_come_and_the_summary_at_close(tmp_path):
    record = LaserAFRecord(str(tmp_path))
    x, y = _grid()
    for i, (a, b) in enumerate(zip(x, y)):
        record.add(_row(fov=i, x_mm=a, y_mm=b, z_after_um=310.0 + a))
    record.add(_row(fov=0, channel="Fluorescence 488 nm Ex", ok=False))  # a per-stack AF: in the csv only
    record.add(_row(time_point=1, fov=0, ok=False, displacement_um=math.nan))

    with open(tmp_path / "laser_af.csv") as f:  # readable before close: a run that dies keeps its rows
        rows = list(csv.DictReader(f))
    assert len(rows) == 27 and list(rows[0]) == list(COLUMNS)
    assert rows[25]["channel"] == "Fluorescence 488 nm Ex"
    assert not (tmp_path / "laser_af_summary.json").exists()

    summary = record.close()
    with open(tmp_path / "laser_af_summary.json") as f:
        assert json.load(f) == summary
    tp0, tp1 = summary["time_points"]
    assert (tp0["time_point"], tp0["n"], tp0["ok"]) == (0, 25, 25)
    assert tp0["focus_surface"]["centre_z_um"] == pytest.approx(322.0)
    assert tp0["focus_surface"]["dome_um"] == pytest.approx(4.0)
    assert (tp1["n"], tp1["ok"], tp1["focus_surface"], tp1["abs_displacement_um_max"]) == (1, 0, None, None)
    assert not (tmp_path / "laser_af_frames").exists()


def test_the_record_saves_the_frames_when_asked(tmp_path):
    frame = np.random.default_rng(0).integers(0, 255, (256, 1536), dtype=np.uint8)
    record = LaserAFRecord(str(tmp_path), save_frames=True)
    record.add(_row(region="B 2", fov=3), frame)
    record.add(_row(region="B 2", fov=3, channel="Fluorescence 561 nm Ex"), frame)
    record.close()
    names = sorted(os.listdir(tmp_path / "laser_af_frames" / "0"))
    assert names == ["B_2_3.bmp", "B_2_3_Fluorescence_561_nm_Ex.bmp"]
    size = os.path.getsize(tmp_path / "laser_af_frames" / "0" / names[0])
    assert 256 * 1536 <= size < 256 * 1536 + 2048  # an 8-bit BMP: the pixels plus a header and palette


def test_a_record_that_cannot_write_does_not_raise(tmp_path):
    record = LaserAFRecord(str(tmp_path / "missing"), save_frames=True)
    record.add(_row(), np.zeros((4, 4), dtype=np.uint8))
    record.add(_row())
    assert record.close()["time_points"][0]["n"] == 2  # still summarized for the log


def test_no_summary_without_per_fov_rows(tmp_path):
    record = LaserAFRecord(str(tmp_path))
    assert record.close() is None
    assert os.listdir(tmp_path) == []
