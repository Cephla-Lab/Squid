"""The factory repeatability report (AI-docs objective-offset design §8.3, normative).

From an N-cycle run's RunResult and the offsets phase's per-cycle results: cycles.csv, focus_curves.csv,
summary.csv, hist_<quantity>.png, trend_<quantity>.png, focus_curves_<objective>.png, report.html and
images/. Qt-free, and matplotlib is used through the Agg Figure only (no pyplot, no backend switch).
The dialog passes the header fields only it knows (ini name, Squid commit, exposures).

What the rows hold:
- z_focus_um is the objective's aligned (pass-2) focus when the cycle's offsets were measured, the
  reference's pass-1 focus (its final one, spec C §6.2), else the pass-1 focus of a cycle whose offsets
  failed. The statistics use only ok rows, so they never mix the two.
- the reference's dx, dy and dz are 0 by definition and get no per-objective rows; the pairs that
  include it are kept (dz_j - dz_ref is dz_j), because §10 target 1 reads pair rows.
- the detrended Z row is the std of z_focus_k after removing an ordinary least-squares line over time
  (np.polyfit, degree 1, time in seconds); its mean, min, max and p95 are those of the residuals. It
  needs 3 cycles: 2 points fit a line exactly.
- focus_curves.csv holds the pass-1 sweeps (the engine keeps those); the aligned sweeps' curves are not
  kept by the offsets phase.
"""

import base64
import csv
import html
import math
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

CYCLE_COLUMNS = [
    "cycle",
    "timestamp",
    "objective",
    "z_focus_um",
    "dx_um",
    "dy_um",
    "dz_um",
    "pixel_size_um",
    "rotation_deg",
    "match_score",
    "runner_up_ratio",
    "focus_peak_rise",
    "closure_error_um",
    "status",
]
FOCUS_CURVE_COLUMNS = ["cycle", "objective", "level", "metric", "z_um", "value"]
SUMMARY_COLUMNS = ["objective", "quantity", "n", "mean", "std", "min", "max", "p95_abs_dev"]
QUANTITIES = ["z_focus_um", "dz_um", "dx_um", "dy_um", "pixel_size_um", "rotation_deg"]
OFFSET_QUANTITIES = ("dx_um", "dy_um", "dz_um")  # 0 for the reference: no per-objective rows
DETRENDED_QUANTITY = "z_focus_detrended_um"
PAIR_QUANTITIES = {"pair_dz_um": "dz_um", "pair_dx_um": "dx_um", "pair_dy_um": "dy_um"}  # of j minus of i
P95 = 95.0  # §8.3: the 95th percentile of |x - mean|
MIN_DETREND_CYCLES = 3  # a line through 2 points leaves no residual
OK = "ok"


@dataclass
class ReportHeader:
    """What the dialog knows and the engine does not (§8.3 report.html header)."""

    ini_name: str
    squid_commit: str
    channel: str
    exposures_ms: Dict[str, Optional[float]]  # per objective, the channel's exposure
    cycles_requested: int
    site_xy_um: Tuple[float, float]
    date: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class CycleRow:
    cycle: int  # 1-based
    timestamp: float  # time.time() of the focus, or of the cycle's start
    objective: str
    z_focus_um: Optional[float] = None
    dx_um: Optional[float] = None
    dy_um: Optional[float] = None
    dz_um: Optional[float] = None
    pixel_size_um: Optional[float] = None
    rotation_deg: Optional[float] = None
    match_score: Optional[float] = None
    runner_up_ratio: Optional[float] = None
    focus_peak_rise: Optional[float] = None
    closure_error_um: Optional[float] = None
    status: str = OK

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass
class SummaryRow:
    objective: str  # an objective, or "i-j" for a pair row
    quantity: str
    n: int
    mean: float
    std: Optional[float]  # None with fewer than 2 values (spec C §6.5)
    min: float
    max: float
    p95_abs_dev: float


def completed_cycles(result) -> int:
    return sum(1 for cycle in result.cycles if cycle.completed)


def run_status(result) -> str:
    """ "completed", "cancelled after k cycles", or "stopped after k cycles: <why>" (§8.2)."""
    k = completed_cycles(result)
    if result.stopped is None:
        return "completed"
    if result.stopped == "cancelled":
        return f"cancelled after {k} cycles"
    return f"stopped after {k} cycles: {result.stopped}"


def cycle_rows(result, offsets) -> List[CycleRow]:
    """One row per cycle x objective, in the engine's order (ascending magnification)."""
    by_index = {measured.cycle_index: measured for measured in offsets}
    rows = []
    for cycle in result.cycles:
        measured = by_index.get(cycle.index)
        names = list(cycle.objectives)
        reference = measured.reference if measured is not None else (names[0] if names else None)
        for name in names:
            r = cycle.objectives[name]
            row = CycleRow(cycle.index + 1, r.timestamp if r.timestamp is not None else cycle.started_at, name)
            if r.focus is not None:
                row.z_focus_um = r.focus.z_best_um
                row.focus_peak_rise = r.focus.peak_rise
            if r.pixel is not None:
                row.pixel_size_um = r.pixel.pixel_size_um
                row.rotation_deg = r.pixel.rotation_deg
            if measured is not None and name in measured.offsets:
                o = measured.offsets[name]
                row.z_focus_um = o.z_focus_um
                row.dx_um, row.dy_um, row.dz_um = o.dx_um, o.dy_um, o.dz_um
                row.match_score, row.runner_up_ratio = o.match_score, o.runner_up_ratio
                row.focus_peak_rise, row.closure_error_um = o.focus_peak_rise, o.closure_error_um
            elif measured is not None and name == reference:
                row.dx_um = row.dy_um = row.dz_um = 0.0
            if r.error:
                row.status = r.error
            elif cycle.error:
                row.status = f"cycle: {cycle.error}"
            elif not cycle.completed:
                row.status = "interrupted"
            rows.append(row)
    return rows


def statistics(objective: str, quantity: str, values: Sequence[float], ddof: int = 1) -> SummaryRow:
    """ddof: degrees of freedom already used by the values (1 for a mean, 2 for the residuals of a line)."""
    v = np.asarray(values, dtype=float)
    mean = float(v.mean())
    return SummaryRow(
        objective,
        quantity,
        len(v),
        mean,
        float(np.std(v, ddof=ddof)) if len(v) > ddof else None,
        float(v.min()),
        float(v.max()),
        float(np.percentile(np.abs(v - mean), P95)),
    )


def detrend(times_s: Sequence[float], values: Sequence[float]) -> Tuple[np.ndarray, float]:
    """Residuals of an ordinary least-squares line over time, and its slope in um per minute."""
    t = np.asarray(times_s, dtype=float)
    v = np.asarray(values, dtype=float)
    slope, intercept = np.polyfit(t - t[0], v, 1)
    return v - (slope * (t - t[0]) + intercept), float(slope * 60.0)


def _ok_values(rows: List[CycleRow], objective: str, quantity: str) -> List[Tuple[float, float]]:
    """(timestamp, value) of the objective's ok rows that hold the quantity, in cycle order."""
    return [
        (row.timestamp, getattr(row, quantity))
        for row in rows
        if row.objective == objective and row.ok and getattr(row, quantity) is not None
    ]


def _objectives(rows: List[CycleRow]) -> List[str]:
    names: List[str] = []
    for row in rows:
        if row.objective not in names:
            names.append(row.objective)
    return names


def _reference(rows: List[CycleRow]) -> Optional[str]:
    """The lowest-magnification objective: the first of the engine's order."""
    names = _objectives(rows)
    return names[0] if names else None


def summary_rows(rows: List[CycleRow]) -> Tuple[List[SummaryRow], Dict[str, float]]:
    """Per objective and quantity over the ok rows, the detrended Z, and a row per pair (i, j) for the
    difference of j's and i's dz, dx and dy within a cycle (§8.3). Also each objective's Z drift slope
    (um/min) from the detrend, for the HTML."""
    out: List[SummaryRow] = []
    slopes: Dict[str, float] = {}
    names = _objectives(rows)
    reference = _reference(rows)
    for name in names:
        for quantity in QUANTITIES:
            if name == reference and quantity in OFFSET_QUANTITIES:
                continue
            series = _ok_values(rows, name, quantity)
            if not series:
                continue
            values = [value for _, value in series]
            out.append(statistics(name, quantity, values))
            if quantity == "z_focus_um" and len(series) >= MIN_DETREND_CYCLES:
                residuals, slopes[name] = detrend([t for t, _ in series], values)
                out.append(statistics(name, DETRENDED_QUANTITY, residuals, ddof=2))  # the line used two
    for lower, higher in combinations(names, 2):
        for pair_quantity, quantity in PAIR_QUANTITIES.items():
            by_cycle = {row.cycle: getattr(row, quantity) for row in rows if row.objective == lower and row.ok}
            differences = [
                getattr(row, quantity) - by_cycle[row.cycle]
                for row in rows
                if row.objective == higher
                and row.ok
                and getattr(row, quantity) is not None
                and by_cycle.get(row.cycle) is not None
            ]
            if differences:
                out.append(statistics(f"{lower}-{higher}", pair_quantity, differences))
    return out, slopes


# ------------------------------------------------------------------------------------------- files
def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _write_csv(path: Path, columns: List[str], rows: List[List[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(v) for v in row])


def write_cycles_csv(path: Path, rows: List[CycleRow]) -> None:
    _write_csv(path, CYCLE_COLUMNS, [[getattr(row, column) for column in CYCLE_COLUMNS] for row in rows])


def write_focus_curves_csv(path: Path, result) -> None:
    rows = []
    for cycle in result.cycles:
        for name, r in cycle.objectives.items():
            if r.focus is None:
                continue
            for level_index, level in enumerate(r.focus.levels):
                for z, value in zip(level.z_um, level.values):
                    rows.append([cycle.index + 1, name, level_index, level.metric, z, value])
    _write_csv(path, FOCUS_CURVE_COLUMNS, rows)


def write_summary_csv(path: Path, summary: List[SummaryRow]) -> None:
    _write_csv(path, SUMMARY_COLUMNS, [[getattr(row, column) for column in SUMMARY_COLUMNS] for row in summary])


def _save(figure: Figure, path: Path) -> None:
    FigureCanvasAgg(figure)
    figure.savefig(path, dpi=100)


def plot_histograms(path: Path, rows: List[CycleRow], quantity: str) -> bool:
    """One histogram per objective that has the quantity; False (no file) when none has."""
    series = [(name, [v for _, v in _ok_values(rows, name, quantity)]) for name in _objectives(rows)]
    series = [(name, values) for name, values in series if values]
    if not series:
        return False
    figure = Figure(figsize=(4 * len(series), 3.2))
    axes = figure.subplots(1, len(series), squeeze=False)[0]
    for ax, (name, values) in zip(axes, series):
        ax.hist(values, bins=min(len(values), 10), color="#4a7ebb")
        std = f", std {np.std(values, ddof=1):.3g}" if len(values) > 1 else ""
        ax.set_title(f"{name}: n {len(values)}, mean {np.mean(values):.4g}{std}", fontsize=9)
        ax.set_xlabel(quantity)
    figure.tight_layout()
    _save(figure, path)
    return True


def plot_trends(path: Path, rows: List[CycleRow], quantity: str) -> bool:
    """The quantity against the cycle number, one line per objective; False when no objective has it."""
    figure = Figure(figsize=(7, 3.6))
    ax = figure.subplots()
    drawn = False
    for name in _objectives(rows):
        points = [(row.cycle, getattr(row, quantity)) for row in rows if row.objective == name and row.ok]
        points = [(c, v) for c, v in points if v is not None]
        if points:
            ax.plot([c for c, _ in points], [v for _, v in points], marker="o", label=name)
            drawn = True
    if not drawn:
        return False
    ax.set_xlabel("cycle")
    ax.set_ylabel(quantity)
    ax.legend(fontsize=8)
    figure.tight_layout()
    _save(figure, path)
    return True


def plot_focus_curves(path: Path, result, objective: str) -> bool:
    """The last (finest) pass-1 level of every cycle, overlaid; False when the objective never focused."""
    figure = Figure(figsize=(7, 3.6))
    ax = figure.subplots()
    drawn = False
    for cycle in result.cycles:
        r = cycle.objectives.get(objective)
        if r is None or r.focus is None or not r.focus.levels:
            continue
        level = r.focus.levels[-1]
        ax.plot(level.z_um, level.values, marker=".", label=f"cycle {cycle.index + 1}")
        drawn = True
    if not drawn:
        return False
    ax.set_xlabel("z (µm)")
    ax.set_ylabel("focus metric (last level)")
    ax.set_title(objective)
    ax.legend(fontsize=7, ncol=2)
    figure.tight_layout()
    _save(figure, path)
    return True


def write_images(folder: Path, result) -> List[Path]:
    """The in-focus frames of cycle 1 and of every failed or interrupted cycle, as 16-bit TIFFs."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for cycle in result.cycles:
        failed = bool(cycle.error) or not cycle.completed or any(r.error for r in cycle.objectives.values())
        if cycle.index != 0 and not failed:
            continue
        for name, r in cycle.objectives.items():
            if r.image is None:
                continue
            path = folder / f"cycle_{cycle.index + 1:02d}_{name.replace('/', '_')}.tiff"
            if not cv2.imwrite(str(path), np.ascontiguousarray(r.image)):
                raise OSError(f"could not write {path}")
            written.append(path)
    return written


def _fmt(value, digits=4) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}g}" if math.isfinite(value) else str(value)
    return str(value)


def _png_tag(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<figure><img src="data:image/png;base64,{data}" alt="{html.escape(path.name)}"><figcaption>{html.escape(path.name)}</figcaption></figure>'


def write_html(
    path: Path,
    header: ReportHeader,
    result,
    rows: List[CycleRow],
    summary: List[SummaryRow],
    slopes: Dict[str, float],
    pngs: List[Path],
) -> None:
    names = _objectives(rows)
    exposures = ", ".join(
        f"{name} {_fmt(header.exposures_ms.get(name))} ms" if header.exposures_ms.get(name) is not None else name
        for name in names
    )
    fields = [
        ("Machine ini", header.ini_name),
        ("Squid commit", header.squid_commit),
        ("Objectives", ", ".join(names)),
        ("Channel", header.channel),
        ("Exposures", exposures),
        ("N", f"{completed_cycles(result)} completed of {header.cycles_requested} requested ({run_status(result)})"),
        ("Date", header.date),
        ("Site XY (µm)", f"{header.site_xy_um[0]:.1f}, {header.site_xy_um[1]:.1f}"),
    ]
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Objective calibration repeatability</title>",
        "<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}td,th{border:1px solid #999;"
        "padding:2px 6px;font-size:90%}th{background:#eee}figure{margin:1em 0}img{max-width:100%}</style>",
        "</head><body><h1>Objective calibration repeatability</h1><table>",
    ]
    parts += [f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in fields]
    parts.append("</table><h2>Summary</h2><table><tr>" + "".join(f"<th>{c}</th>" for c in SUMMARY_COLUMNS) + "</tr>")
    for row in summary:
        parts.append(
            "<tr>" + "".join(f"<td>{html.escape(_fmt(getattr(row, c)))}</td>" for c in SUMMARY_COLUMNS) + "</tr>"
        )
    parts.append("</table>")
    if slopes:
        drift = "; ".join(f"{name} {slope:+.3g} µm/min" for name, slope in slopes.items())
        parts.append(
            f"<p>Z drift removed from {html.escape(DETRENDED_QUANTITY)} (least-squares line over time): {drift}.</p>"
        )
    failed = []
    for cycle in result.cycles:
        messages = [f"{name}: {r.error}" for name, r in cycle.objectives.items() if r.error]
        if cycle.error:
            messages.append(cycle.error)
        if not cycle.completed:
            messages.append("interrupted" if result.stopped is None else result.stopped)
        if messages:
            failed.append(f"<li>cycle {cycle.index + 1}: {html.escape('; '.join(messages))}</li>")
    parts.append("<h2>Failed cycles</h2>" + (f"<ul>{''.join(failed)}</ul>" if failed else "<p>None.</p>"))
    parts.append("<h2>Plots</h2>" + "".join(_png_tag(png) for png in pngs))
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_report(folder, result, offsets, header: ReportHeader) -> Path:
    """Every §8.3 file into `folder` (created); returns report.html's path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = cycle_rows(result, offsets)
    summary, slopes = summary_rows(rows)
    write_cycles_csv(folder / "cycles.csv", rows)
    write_focus_curves_csv(folder / "focus_curves.csv", result)
    write_summary_csv(folder / "summary.csv", summary)
    pngs = []
    for quantity in QUANTITIES:
        for prefix, plot in (("hist", plot_histograms), ("trend", plot_trends)):
            path = folder / f"{prefix}_{quantity}.png"
            if plot(path, rows, quantity):
                pngs.append(path)
    for name in _objectives(rows):
        path = folder / f"focus_curves_{name.replace('/', '_')}.png"
        if plot_focus_curves(path, result, name):
            pngs.append(path)
    write_images(folder / "images", result)
    report = folder / "report.html"
    write_html(report, header, result, rows, summary, slopes, pngs)
    return report
