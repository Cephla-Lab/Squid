"""Per-FOV laser AF record of an acquisition: laser_af.csv, a focus-surface summary, optional focus-camera frames.

One row per laser AF call (the per-FOV AF, and the per-stack AF of Z First "AF per stack") goes to laser_af.csv in
the experiment folder as it happens, so a run that ends early keeps its rows. At the end, laser_af_summary.json gets
one summary per time point of the per-FOV rows: counts, |displacement| p95 / max, and a quadratic fit of the focus
height z(x, y) (its centre, its dome = peak-to-peak over the FOVs, and the rms residual).

Writing never raises: a failure is logged once and the acquisition goes on.
"""

import csv
import json
import math
import os
from typing import Dict, List, Optional, Sequence

import imageio as iio
import numpy as np

import squid.logging

CSV_NAME = "laser_af.csv"
SUMMARY_NAME = "laser_af_summary.json"
FRAMES_DIR = "laser_af_frames"

COLUMNS = (
    "time",  # unix time (s) the AF call started
    "time_point",
    "region",
    "fov",
    "channel",  # empty for the per-FOV AF; the channel for a Z First per-stack AF
    "x_mm",
    "y_mm",
    "z_before_um",  # focus height before / after: stage z, plus the piezo when laser AF moves the piezo (um)
    "z_after_um",
    "displacement_um",  # the first measurement of the call (nan: no spot)
    "correlation",  # the alignment check of the last move (nan: not reached)
    "ok",
    "target_um",  # the displacement laser AF was asked to reach (the region's laser AF offset)
    "af_time_s",
    "searched",  # the spot search ran (LASER_AF_SPOT_SEARCH)
)

_MIN_POINTS = {"quadratic": 6, "plane": 3, "constant": 1}

_log = squid.logging.get_logger(__name__)


def focus_surface(x_mm: Sequence[float], y_mm: Sequence[float], z_um: Sequence[float]) -> Optional[dict]:
    """Fit z(x, y) with a quadratic (a plane, or a constant, when the points cannot hold one) about the centre of
    the points' bounding box. Returns the centre z, the dome (peak-to-peak of the fit over the points) and the rms
    residual, or None without points."""
    x, y, z = (np.asarray(v, dtype=float) for v in (x_mm, y_mm, z_um))
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[finite], y[finite], z[finite]
    if z.size == 0:
        return None
    cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
    dx, dy = x - cx, y - cy
    one = np.ones_like(dx)
    terms = {
        "quadratic": [one, dx, dy, dx * dx, dx * dy, dy * dy],
        "plane": [one, dx, dy],
        "constant": [one],
    }
    for model, columns in terms.items():
        a = np.stack(columns, axis=1)
        if z.size < _MIN_POINTS[model] or np.linalg.matrix_rank(a) < a.shape[1]:
            continue
        coefficients, *_ = np.linalg.lstsq(a, z, rcond=None)
        fitted = a @ coefficients
        return {
            "model": model,
            "n_points": int(z.size),
            "centre_x_mm": float(cx),
            "centre_y_mm": float(cy),
            "centre_z_um": float(coefficients[0]),
            "dome_um": float(np.ptp(fitted)),
            "residual_rms_um": float(np.sqrt(np.mean((z - fitted) ** 2))),
        }
    return None


def summarize(rows: List[dict]) -> dict:
    """The summary of one time point's per-FOV rows."""
    ok_rows = [r for r in rows if r["ok"]]
    abs_d = np.abs([r["displacement_um"] for r in rows if math.isfinite(r["displacement_um"])])
    return {
        "n": len(rows),
        "ok": len(ok_rows),
        "searched": sum(1 for r in rows if r["searched"]),
        "abs_displacement_um_p95": float(np.percentile(abs_d, 95)) if abs_d.size else None,
        "abs_displacement_um_max": float(abs_d.max()) if abs_d.size else None,
        # The reference plane: z after laser AF, less the region's laser AF offset (applied 1:1 in z).
        "focus_surface": focus_surface(
            [r["x_mm"] for r in ok_rows],
            [r["y_mm"] for r in ok_rows],
            [r["z_after_um"] - r["target_um"] for r in ok_rows],
        ),
    }


def _summary_line(time_point, s: dict) -> str:
    def um(v):
        return "n/a" if v is None else f"{v:.1f} um"

    line = (
        f"laser AF, time point {time_point}: {s['ok']}/{s['n']} ok, {s['searched']} searched, "
        f"|displacement| p95 {um(s['abs_displacement_um_p95'])}, max {um(s['abs_displacement_um_max'])}"
    )
    fs = s["focus_surface"]
    if fs is not None:
        line += (
            f"; focus surface ({fs['model']}, {fs['n_points']} FOVs): centre z {fs['centre_z_um']:.1f} um, "
            f"dome {fs['dome_um']:.1f} um, residual rms {fs['residual_rms_um']:.2f} um"
        )
    return line


class LaserAFRecord:
    def __init__(self, experiment_path: str, save_frames: bool = False):
        self._experiment_path = experiment_path
        self._save_frames = save_frames
        self._file = None
        self._writer = None
        self._failed = False
        self._frames_failed = False
        self._per_fov_rows: Dict[int, List[dict]] = {}

    def add(self, row: dict, frame: Optional[np.ndarray] = None) -> None:
        """Append one AF call (keys: COLUMNS) to laser_af.csv; with save_frames, also save its focus-camera frame."""
        if not row["channel"]:
            self._per_fov_rows.setdefault(row["time_point"], []).append(row)
        if not self._failed:
            try:
                if self._writer is None:
                    self._file = open(os.path.join(self._experiment_path, CSV_NAME), "w", newline="")
                    self._writer = csv.DictWriter(self._file, fieldnames=COLUMNS)
                    self._writer.writeheader()
                self._writer.writerow(row)
                self._file.flush()
            except Exception as e:
                self._failed = True
                _log.warning(f"Could not write {CSV_NAME}, the laser AF record stops here: {e}")
        if self._save_frames and frame is not None and not self._frames_failed:
            try:
                folder = os.path.join(self._experiment_path, FRAMES_DIR, str(row["time_point"]))
                os.makedirs(folder, exist_ok=True)
                name = f"{row['region']}_{row['fov']}" + (f"_{row['channel']}" if row["channel"] else "")
                iio.imwrite(os.path.join(folder, name.replace(" ", "_") + ".bmp"), frame)
            except Exception as e:
                self._frames_failed = True
                _log.warning(f"Could not save a laser AF frame, no more are saved: {e}")

    def close(self) -> Optional[dict]:
        """Close laser_af.csv, log a summary per time point and write laser_af_summary.json. Returns the summary."""
        if self._file is not None:
            try:
                self._file.close()
            except Exception as e:
                _log.warning(f"Could not close {CSV_NAME}: {e}")
            self._file = self._writer = None
        if not self._per_fov_rows:
            return None
        summary = {"time_points": []}
        for time_point, rows in sorted(self._per_fov_rows.items()):
            s = summarize(rows)
            _log.info(_summary_line(time_point, s))
            summary["time_points"].append({"time_point": time_point, **s})
        try:
            path = os.path.join(self._experiment_path, SUMMARY_NAME)
            with open(path + ".tmp", "w") as f:
                json.dump(summary, f, indent=2)
            os.replace(path + ".tmp", path)
        except Exception as e:
            _log.warning(f"Could not write {SUMMARY_NAME}: {e}")
        return summary
