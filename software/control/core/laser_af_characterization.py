"""
One-click laser autofocus characterization for the current objective.

Collects the data that used to require screen recordings: a Z sweep (linearity /
sensitivity / usable range), closed-loop repeatability of move_to_target, and a
displacement stability trace, plus the raw focus-camera spot images.  Results are
written to a timestamped folder as CSV + JSON/text summary + PNG plots.

This module is deliberately Qt-free so it can be unit-tested and driven headlessly;
the GUI wraps the runner with a thin Qt signal adapter (see gui_hcs.py).  Plots are
rendered with Figure/FigureCanvasAgg (never pyplot) because they are generated on a
worker thread inside a Qt application.
"""

import dataclasses
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Union

import cv2
import imageio as iio
import numpy as np
import pandas as pd
import yaml
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

import control._def
import control.utils
import squid.logging
from control.models.laser_af_config import LaserAFConfig


@dataclass
class LaserAFTestParams:
    sweep_range_um: Optional[float] = None  # None -> the objective's configured laser_af_range
    sweep_n_steps: int = 21
    run_sweep: bool = True
    repeatability_cycles: int = 20
    repeatability_offset_um: Optional[float] = None  # None -> half the sweep range, alternating sign
    run_repeatability: bool = True
    stability_n_samples: int = 25
    stability_interval_s: float = 0.5
    run_stability: bool = True
    save_spot_images: bool = True


@dataclass
class MeasurementRecord:
    routine: str  # "sweep" | "repeatability" | "stability"
    index: int
    commanded_dz_um: float
    stage_z_mm: float
    measured_displacement_um: float
    spot_x_px: float
    spot_y_px: float
    timestamp_s: float
    move_success: Optional[bool] = None  # repeatability only


@dataclass
class SweepMetrics:
    slope_um_per_um: float
    r_squared: float
    residual_rms_um: float
    usable_range_neg_um: float
    usable_range_pos_um: float
    n_valid: int
    n_total: int


@dataclass
class RepeatabilityMetrics:
    rms_residual_um: float
    max_abs_residual_um: float
    success_rate: float
    n_cycles: int


@dataclass
class StabilityMetrics:
    sigma_um: float
    drift_um_per_min: float
    duration_s: float
    n_samples: int


@dataclass
class LaserAFTestResults:
    objective: str
    output_dir: str
    params: LaserAFTestParams
    records: List[MeasurementRecord]
    sweep: Optional[SweepMetrics] = None
    repeatability: Optional[RepeatabilityMetrics] = None
    stability: Optional[StabilityMetrics] = None
    aborted: bool = False
    error: Optional[str] = None


CSV_COLUMNS = [
    "routine",
    "index",
    "commanded_dz_um",
    "stage_z_mm",
    "measured_displacement_um",
    "spot_x_px",
    "spot_y_px",
    "timestamp_s",
    "move_success",
]

_REFERENCE_IMAGE_FIELDS = {"reference_image", "reference_image_shape", "reference_image_dtype"}


def compute_sweep_metrics(commanded_dz_um, measured_um) -> SweepMetrics:
    """Fit measured displacement vs commanded Z offset; NaN measurements mark the usable-range edges."""
    commanded = np.asarray(commanded_dz_um, dtype=float)
    measured = np.asarray(measured_um, dtype=float)
    valid = np.isfinite(measured)
    n_valid = int(np.count_nonzero(valid))
    nan = float("nan")

    slope = r_squared = residual_rms = nan
    if n_valid >= 2:
        slope, intercept = np.polyfit(commanded[valid], measured[valid], 1)
        residuals = measured[valid] - (slope * commanded[valid] + intercept)
        residual_rms = float(np.sqrt(np.mean(residuals**2)))
        ss_tot = float(np.sum((measured[valid] - np.mean(measured[valid])) ** 2))
        r_squared = 1.0 - float(np.sum(residuals**2)) / ss_tot if ss_tot > 0 else nan

    # Usable range: from the point closest to 0, extend outward until the first invalid measurement.
    usable_neg = usable_pos = nan
    if n_valid:
        order = np.argsort(commanded)
        commanded_sorted = commanded[order]
        valid_sorted = valid[order]
        center = int(np.argmin(np.abs(commanded_sorted)))
        if valid_sorted[center]:
            i = center
            while i - 1 >= 0 and valid_sorted[i - 1]:
                i -= 1
            j = center
            while j + 1 < commanded_sorted.size and valid_sorted[j + 1]:
                j += 1
            usable_neg = float(commanded_sorted[i])
            usable_pos = float(commanded_sorted[j])

    return SweepMetrics(
        float(slope), float(r_squared), float(residual_rms), usable_neg, usable_pos, n_valid, int(commanded.size)
    )


def compute_repeatability_metrics(residuals_um, move_successes) -> RepeatabilityMetrics:
    """Residual statistics over successful cycles; success rate over all cycles."""
    residuals = np.asarray(residuals_um, dtype=float)
    successes = np.asarray(move_successes, dtype=bool)
    ok = successes & np.isfinite(residuals)
    nan = float("nan")
    rms = float(np.sqrt(np.mean(residuals[ok] ** 2))) if np.any(ok) else nan
    max_abs = float(np.max(np.abs(residuals[ok]))) if np.any(ok) else nan
    rate = float(np.count_nonzero(successes) / successes.size) if successes.size else nan
    return RepeatabilityMetrics(rms, max_abs, rate, int(residuals.size))


def compute_stability_metrics(timestamps_s, measured_um) -> StabilityMetrics:
    """Noise sigma after removing linear drift; drift reported in um/min."""
    t = np.asarray(timestamps_s, dtype=float)
    d = np.asarray(measured_um, dtype=float)
    valid = np.isfinite(d)
    nan = float("nan")
    duration = float(t[-1] - t[0]) if t.size else nan
    if np.count_nonzero(valid) >= 2:
        slope, intercept = np.polyfit(t[valid], d[valid], 1)
        detrended = d[valid] - (slope * t[valid] + intercept)
        sigma = float(np.std(detrended))
        drift = float(slope * 60.0)
    else:
        sigma = drift = nan
    return StabilityMetrics(sigma, drift, duration, int(t.size))


def create_output_dir(objective: str) -> Path:
    """Create {DEFAULT_SAVING_PATH}/laser_af_tests/{objective}_{timestamp}/ (saving path read at call time)."""
    sanitized = re.sub(r"[^\w\-]", "_", objective)
    out = (
        Path(control._def.DEFAULT_SAVING_PATH)
        / "laser_af_tests"
        / f"{sanitized}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    )
    control.utils.ensure_directory_exists(str(out))
    return out


def write_measurements_csv(records: List[MeasurementRecord], path: Union[str, Path]) -> None:
    df = pd.DataFrame([dataclasses.asdict(r) for r in records], columns=CSV_COLUMNS)
    df.to_csv(path, index=False)


def _metrics_dict(metrics) -> Optional[dict]:
    return dataclasses.asdict(metrics) if metrics is not None else None


def format_summary_verdict(results: LaserAFTestResults) -> str:
    """One-line pass/fail judgment shown at the end of summary.txt.

    TODO(hongquan): define the acceptance thresholds from bench experience
    (e.g. repeatability RMS limit per objective class, minimum sweep R^2,
    maximum stability sigma) and return "PASS"/"FAIL: <reason>" accordingly.
    """
    return "Verdict: (no acceptance thresholds set)"


def format_summary_text(results: LaserAFTestResults, config: LaserAFConfig) -> str:
    lines = [
        f"Laser AF test - objective {results.objective}",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Output: {results.output_dir}",
        f"pixel_to_um: {config.pixel_to_um:.4f}   laser_af_range: {config.laser_af_range} um",
        "",
    ]
    if results.sweep is not None:
        s = results.sweep
        lines += [
            "Sweep:",
            f"  sensitivity slope: {s.slope_um_per_um:.4f} um/um   R^2: {s.r_squared:.5f}",
            f"  residual RMS: {s.residual_rms_um:.3f} um",
            f"  usable range: {s.usable_range_neg_um:+.1f} .. {s.usable_range_pos_um:+.1f} um "
            f"({s.n_valid}/{s.n_total} points valid)",
        ]
    if results.repeatability is not None:
        r = results.repeatability
        lines += [
            "Repeatability (move_to_target residuals):",
            f"  RMS: {r.rms_residual_um:.3f} um   max |residual|: {r.max_abs_residual_um:.3f} um",
            f"  success rate: {r.success_rate * 100.0:.1f}% of {r.n_cycles} cycles",
        ]
    if results.stability is not None:
        st = results.stability
        lines += [
            "Stability:",
            f"  sigma (detrended): {st.sigma_um:.3f} um   drift: {st.drift_um_per_min:+.3f} um/min",
            f"  {st.n_samples} samples over {st.duration_s:.1f} s",
        ]
    if results.aborted:
        lines.append("NOTE: test was aborted before completion; data above is partial.")
    if results.error:
        lines.append(f"ERROR: {results.error}")
    lines += ["", format_summary_verdict(results)]
    return "\n".join(lines) + "\n"


def write_summary(results: LaserAFTestResults, config: LaserAFConfig, out_dir: Union[str, Path]) -> None:
    summary = {
        "objective": results.objective,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "squid_repo_state": control.utils.get_squid_repo_state_description(),
        "pixel_to_um": config.pixel_to_um,
        "laser_af_range_um": config.laser_af_range,
        "params": dataclasses.asdict(results.params),
        "sweep": _metrics_dict(results.sweep),
        "repeatability": _metrics_dict(results.repeatability),
        "stability": _metrics_dict(results.stability),
        "aborted": results.aborted,
        "error": results.error,
    }
    out = Path(out_dir)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "summary.txt").write_text(format_summary_text(results, config))


def write_config_snapshot(config: LaserAFConfig, path: Union[str, Path]) -> None:
    """Snapshot of the objective's LaserAFConfig without the bulky base64 reference-image fields."""
    data = config.model_dump(mode="json", warnings=False, exclude=_REFERENCE_IMAGE_FIELDS)
    Path(path).write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))


def _records_for(results: LaserAFTestResults, routine: str) -> List[MeasurementRecord]:
    return [r for r in results.records if r.routine == routine]


def _build_sweep_figure(records: List[MeasurementRecord], metrics: Optional[SweepMetrics]) -> Figure:
    commanded = np.array([r.commanded_dz_um for r in records])
    measured = np.array([r.measured_displacement_um for r in records])
    valid = np.isfinite(measured)

    fig = Figure(figsize=(7, 7))
    FigureCanvasAgg(fig)
    # gridspec_kw (not the direct height_ratios kwarg) — the latter needs matplotlib >= 3.6
    ax_fit, ax_res = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax_fit.plot(commanded[valid], measured[valid], "o", markersize=4, label="measured")
    if np.count_nonzero(valid) >= 2:
        slope, intercept = np.polyfit(commanded[valid], measured[valid], 1)
        fit_x = np.array([commanded.min(), commanded.max()])
        ax_fit.plot(fit_x, slope * fit_x + intercept, "-", linewidth=1, label="fit")
        ax_res.plot(commanded[valid], measured[valid] - (slope * commanded[valid] + intercept), "o", markersize=3)
    ax_res.axhline(0.0, color="gray", linewidth=0.5)
    title = "Laser AF Z sweep"
    if metrics is not None and math.isfinite(metrics.slope_um_per_um):
        title += f"  (slope {metrics.slope_um_per_um:.3f} um/um, R$^2$ {metrics.r_squared:.4f})"
    if np.count_nonzero(~valid):
        title += f"  [{int(np.count_nonzero(~valid))} failed points]"
    ax_fit.set_title(title, fontsize=10)
    ax_fit.set_ylabel("measured displacement (um)")
    ax_fit.legend(fontsize=8)
    ax_fit.grid(True, alpha=0.3)
    ax_res.set_xlabel("commanded Z offset (um)")
    ax_res.set_ylabel("residual (um)")
    ax_res.grid(True, alpha=0.3)
    return fig


def _build_repeatability_figure(records: List[MeasurementRecord], metrics: Optional[RepeatabilityMetrics]) -> Figure:
    residuals = np.array([r.measured_displacement_um for r in records])
    cycles = np.array([r.index for r in records])
    valid = np.isfinite(residuals)

    fig = Figure(figsize=(8, 4))
    FigureCanvasAgg(fig)
    # gridspec_kw (not the direct width_ratios kwarg) — the latter needs matplotlib >= 3.6
    ax_series, ax_hist = fig.subplots(1, 2, gridspec_kw={"width_ratios": [2, 1]})
    ax_series.plot(cycles[valid], residuals[valid], "o-", markersize=4)
    ax_series.axhline(0.0, color="gray", linewidth=0.5)
    ax_series.set_xlabel("cycle")
    ax_series.set_ylabel("residual after move_to_target(0) (um)")
    ax_series.grid(True, alpha=0.3)
    if np.count_nonzero(valid):
        ax_hist.hist(residuals[valid], bins=min(15, max(3, int(np.count_nonzero(valid) // 2 + 1))))
    ax_hist.set_xlabel("residual (um)")
    title = "Laser AF repeatability"
    if metrics is not None and math.isfinite(metrics.rms_residual_um):
        title += f"  (RMS {metrics.rms_residual_um:.3f} um, success {metrics.success_rate * 100.0:.0f}%)"
    fig.suptitle(title, fontsize=10)
    return fig


def _build_stability_figure(records: List[MeasurementRecord], metrics: Optional[StabilityMetrics]) -> Figure:
    t = np.array([r.timestamp_s for r in records])
    d = np.array([r.measured_displacement_um for r in records])
    valid = np.isfinite(d)

    fig = Figure(figsize=(8, 4))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.plot(t[valid], d[valid], ".-", markersize=3, linewidth=0.7)
    if np.count_nonzero(valid) >= 2:
        slope, intercept = np.polyfit(t[valid], d[valid], 1)
        ax.plot(t[valid], slope * t[valid] + intercept, "-", color="tab:orange", linewidth=1, label="drift")
        ax.legend(fontsize=8)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("measured displacement (um)")
    title = "Laser AF stability"
    if metrics is not None and math.isfinite(metrics.sigma_um):
        title += f"  (sigma {metrics.sigma_um:.3f} um, drift {metrics.drift_um_per_min:+.3f} um/min)"
    ax.set_title(title, fontsize=10)
    ax.grid(True, alpha=0.3)
    return fig


def _build_phase_figures(results: LaserAFTestResults) -> List[Figure]:
    figures = []
    sweep_records = _records_for(results, "sweep")
    if sweep_records:
        figures.append(_build_sweep_figure(sweep_records, results.sweep))
    repeatability_records = _records_for(results, "repeatability")
    if repeatability_records:
        figures.append(_build_repeatability_figure(repeatability_records, results.repeatability))
    stability_records = _records_for(results, "stability")
    if stability_records:
        figures.append(_build_stability_figure(stability_records, results.stability))
    return figures


def save_plots(results: LaserAFTestResults, out_dir: Union[str, Path]) -> None:
    out = Path(out_dir)
    sweep_records = _records_for(results, "sweep")
    if sweep_records:
        _build_sweep_figure(sweep_records, results.sweep).savefig(out / "sweep.png", dpi=200, bbox_inches="tight")
    repeatability_records = _records_for(results, "repeatability")
    if repeatability_records:
        _build_repeatability_figure(repeatability_records, results.repeatability).savefig(
            out / "repeatability.png", dpi=200, bbox_inches="tight"
        )
    stability_records = _records_for(results, "stability")
    if stability_records:
        _build_stability_figure(stability_records, results.stability).savefig(
            out / "stability.png", dpi=200, bbox_inches="tight"
        )


def _spot_image_paths_in_record_order(results: LaserAFTestResults, images_dir: Path) -> List[tuple]:
    """(record, image path) pairs for the records whose spot image exists, in acquisition order."""
    pairs = []
    for record in results.records:
        path = images_dir / f"{record.routine}_{record.index:03d}.bmp"
        if path.exists():
            pairs.append((record, path))
    return pairs


def _write_annotated_video(pairs: List[tuple], path: Path, fps: float) -> Optional[Path]:
    first_frame = cv2.imread(str(pairs[0][1]), cv2.IMREAD_UNCHANGED)
    if first_frame is None:
        return None
    height, width = first_frame.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    try:
        font_scale = max(0.4, width / 1000.0)
        thickness = max(1, int(round(font_scale * 2)))
        for record, image_path in pairs:
            frame = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
            if frame is None or frame.shape[:2] != (height, width):
                continue  # a resized ROI mid-run would corrupt the container; skip the odd frame
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            measured = (
                f"{record.measured_displacement_um:+.2f} um"
                if math.isfinite(record.measured_displacement_um)
                else "failed"
            )
            label = f"{record.routine} #{record.index}  dz {record.commanded_dz_um:+.1f} um  measured {measured}"
            origin = (8, int(24 * font_scale) + 8)
            cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 2)
            cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness)
            writer.write(frame)
    finally:
        writer.release()
    return path


def write_spot_videos(results: LaserAFTestResults, out_dir: Union[str, Path], fps: float = 10.0) -> List[Path]:
    """Assemble the saved spot images into one annotated mp4 per routine (the screen-recording
    replacement): sweep.mp4, repeatability.mp4, stability.mp4.

    Frames run in acquisition order and carry routine/index plus commanded vs measured
    displacement. Routines without saved images are skipped; returns the written paths.
    """
    out = Path(out_dir)
    pairs = _spot_image_paths_in_record_order(results, out / "spot_images")
    written = []
    for routine in ("sweep", "repeatability", "stability"):
        routine_pairs = [(record, path) for record, path in pairs if record.routine == routine]
        if not routine_pairs:
            continue
        video_path = _write_annotated_video(routine_pairs, out / f"{routine}.mp4", fps)
        if video_path is not None:
            written.append(video_path)
    return written


def _build_pdf_summary_page(results: LaserAFTestResults, config: LaserAFConfig) -> Figure:
    fig = Figure(figsize=(8.27, 11.69))  # A4 portrait
    FigureCanvasAgg(fig)
    fig.text(0.07, 0.96, "Laser AF Test Report", fontsize=16, fontweight="bold", va="top")
    body = format_summary_text(results, config)
    body += "\nLaserAFConfig snapshot:\n"
    body += yaml.safe_dump(
        config.model_dump(mode="json", warnings=False, exclude=_REFERENCE_IMAGE_FIELDS),
        default_flow_style=False,
        sort_keys=False,
    )
    fig.text(0.07, 0.91, body, fontsize=8, family="monospace", va="top")
    return fig


def _build_pdf_spot_image_page(results: LaserAFTestResults, images_dir: Path) -> Optional[Figure]:
    pairs = _spot_image_paths_in_record_order(results, images_dir)
    if not pairs:
        return None
    # Up to 6 samples spread across the run.
    indices = np.unique(np.linspace(0, len(pairs) - 1, num=min(6, len(pairs)), dtype=int))
    fig = Figure(figsize=(8.27, 11.69))
    FigureCanvasAgg(fig)
    fig.suptitle("Spot images (sample)", fontsize=12)
    for slot, pair_index in enumerate(indices):
        record, path = pairs[pair_index]
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            continue
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        ax = fig.add_subplot(3, 2, slot + 1)
        ax.imshow(image, cmap="gray")
        ax.set_title(f"{record.routine} #{record.index}  dz {record.commanded_dz_um:+.1f} um", fontsize=7)
        ax.axis("off")
    return fig


def write_pdf_report(results: LaserAFTestResults, config: LaserAFConfig, out_dir: Union[str, Path]) -> Path:
    """One shareable report.pdf: summary page, the phase plots, and sample spot images."""
    out = Path(out_dir)
    pdf_path = out / "report.pdf"
    with PdfPages(str(pdf_path)) as pdf:
        pdf.savefig(_build_pdf_summary_page(results, config))
        for figure in _build_phase_figures(results):
            pdf.savefig(figure)
        image_page = _build_pdf_spot_image_page(results, out / "spot_images")
        if image_page is not None:
            pdf.savefig(image_page)
    return pdf_path


def write_report(results: LaserAFTestResults, config: LaserAFConfig, out_dir: Union[str, Path]) -> None:
    """Write measurements.csv, summary.json/.txt, laser_af_config.yaml, PNG plots, report.pdf,
    and (when spot images were saved) one annotated mp4 per routine."""
    out = Path(out_dir)
    write_measurements_csv(results.records, out / "measurements.csv")
    write_summary(results, config, out)
    write_config_snapshot(config, out / "laser_af_config.yaml")
    save_plots(results, out)
    write_pdf_report(results, config, out)
    write_spot_videos(results, out)


class LaserAFCharacterizationRunner:
    """Runs the laser AF test phases against a LaserAutofocusController and writes the report.

    Qt-free: progress and completion are plain callbacks (the GUI wraps them in Qt signals).
    ``start()`` runs ``run_blocking()`` on a daemon thread; ``abort()`` stops between
    measurements.  The stage (or piezo) Z position is always restored, the camera callback
    state is put back, and ``finished_fn`` is always called exactly once with the results —
    including on error, abort, or failed preconditions.
    """

    def __init__(
        self,
        laser_af_controller,
        progress_fn: Callable[[str, int, int], None],
        finished_fn: Callable[[LaserAFTestResults], None],
    ):
        self._controller = laser_af_controller
        self._progress_fn = progress_fn
        self._finished_fn = finished_fn
        self._abort_requested = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def abort(self) -> None:
        self._abort_requested.set()

    def start(self, params: LaserAFTestParams) -> None:
        if self.is_running():
            raise RuntimeError("A laser AF test is already running")
        self._thread = threading.Thread(target=self.run_blocking, args=(params,), daemon=True, name="LaserAFTest")
        self._thread.start()

    def run_blocking(self, params: LaserAFTestParams) -> LaserAFTestResults:
        self._abort_requested.clear()
        controller = self._controller
        config = controller.laser_af_properties
        objective = controller.objectiveStore.current_objective if controller.objectiveStore else "unknown"
        results = LaserAFTestResults(objective=objective, output_dir="", params=params, records=[])

        error = self._check_preconditions(controller, config)
        if error:
            self._log.error(f"Laser AF test cannot start: {error}")
            results.error = error
            self._finished_fn(results)
            return results

        sweep_range_um = float(params.sweep_range_um if params.sweep_range_um is not None else config.laser_af_range)
        repeat_offset_um = float(
            params.repeatability_offset_um if params.repeatability_offset_um is not None else sweep_range_um / 2.0
        )

        out_dir = create_output_dir(objective)
        results.output_dir = str(out_dir)
        images_dir = None
        if params.save_spot_images:
            images_dir = out_dir / "spot_images"
            control.utils.ensure_directory_exists(str(images_dir))

        start_z_mm = controller.stage.get_pos().z_mm
        start_piezo_um = controller.piezo.position if controller.piezo is not None else None
        callbacks_were_enabled = controller.camera.get_callbacks_enabled()
        self._t0 = time.monotonic()
        self._log.info(
            f"Starting laser AF test for objective '{objective}' "
            f"(sweep +/-{sweep_range_um} um / {params.sweep_n_steps} steps, "
            f"{params.repeatability_cycles} repeatability cycles, {params.stability_n_samples} stability samples) "
            f"-> {out_dir}"
        )

        try:
            if params.run_sweep and not self._abort_requested.is_set():
                self._run_sweep(params, sweep_range_um, images_dir, results)
                self._restore_start_position(start_z_mm, start_piezo_um)
            if params.run_repeatability and not self._abort_requested.is_set():
                self._run_repeatability(params, repeat_offset_um, images_dir, results)
                self._restore_start_position(start_z_mm, start_piezo_um)
            if params.run_stability and not self._abort_requested.is_set():
                self._run_stability(params, images_dir, results)
        except Exception as exc:
            self._log.exception("Laser AF test failed")
            results.error = str(exc)
        finally:
            results.aborted = self._abort_requested.is_set()
            try:
                self._restore_start_position(start_z_mm, start_piezo_um)
            except Exception as restore_exc:
                self._log.error(f"Failed to restore Z after laser AF test: {restore_exc}. Original: {results.error}")
                if results.error is None:
                    results.error = f"Failed to restore Z position: {restore_exc}"
            try:
                controller.camera.enable_callbacks(callbacks_were_enabled)
            except Exception:
                self._log.exception("Failed to restore focus-camera callback state after laser AF test")
            try:
                # The controller methods leave the AF laser off, but make sure even on error paths.
                controller.microcontroller.turn_off_AF_laser()
                controller.microcontroller.wait_till_operation_is_completed()
            except Exception:
                self._log.exception("Failed to ensure AF laser is off after laser AF test")

            self._compute_metrics(results)
            try:
                write_report(results, config, out_dir)
            except Exception as report_exc:
                self._log.exception("Failed to write laser AF test report")
                if results.error is None:
                    results.error = f"Failed to write report: {report_exc}"

            self._log.info(f"Laser AF test finished (aborted={results.aborted}, error={results.error})")
            self._finished_fn(results)
        return results

    @staticmethod
    def _check_preconditions(controller, config: LaserAFConfig) -> Optional[str]:
        if not controller.is_initialized:
            return "Laser AF is not initialized for the current objective"
        if not config.has_reference or config.x_reference is None:
            return "Laser AF reference is not set for the current objective"
        if not config.pixel_to_um:
            return "Laser AF pixel_to_um calibration is zero or missing"
        return None

    def _settle(self) -> None:
        if self._controller.piezo is not None:
            time.sleep(control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000.0)
        else:
            time.sleep(control._def.SCAN_STABILIZATION_TIME_MS_Z / 1000.0)

    def _restore_start_position(self, start_z_mm: float, start_piezo_um: Optional[float]) -> None:
        if self._controller.piezo is not None and start_piezo_um is not None:
            self._controller.piezo.move_to(start_piezo_um)
        else:
            self._controller.stage.move_z_to(start_z_mm)
        self._settle()

    def _measure_and_record(
        self,
        routine: str,
        index: int,
        commanded_dz_um: float,
        images_dir: Optional[Path],
        results: LaserAFTestResults,
        move_success: Optional[bool] = None,
        save_image: bool = True,
    ):
        measurement = self._controller.measure_displacement_detailed()
        results.records.append(
            MeasurementRecord(
                routine=routine,
                index=index,
                commanded_dz_um=commanded_dz_um,
                stage_z_mm=self._controller.stage.get_pos().z_mm,
                measured_displacement_um=measurement.displacement_um,
                spot_x_px=measurement.spot_x_px,
                spot_y_px=measurement.spot_y_px,
                timestamp_s=time.monotonic() - self._t0,
                move_success=move_success,
            )
        )
        if save_image and images_dir is not None and measurement.image is not None:
            iio.imwrite(images_dir / f"{routine}_{index:03d}.bmp", measurement.image)
        return measurement

    def _run_sweep(self, params: LaserAFTestParams, sweep_range_um: float, images_dir, results) -> None:
        offsets = np.linspace(-sweep_range_um, sweep_range_um, params.sweep_n_steps)
        # One move down, then unidirectional upward steps (the stage's backlash compensation
        # handles the initial descent; measuring bottom-up avoids per-point backlash).
        previous = 0.0
        for i, offset in enumerate(offsets):
            if self._abort_requested.is_set():
                return
            self._progress_fn("Sweep", i + 1, len(offsets))
            self._controller.move_z_um(float(offset - previous))
            previous = float(offset)
            self._settle()
            self._measure_and_record("sweep", i, float(offset), images_dir, results)

    def _run_repeatability(self, params: LaserAFTestParams, repeat_offset_um: float, images_dir, results) -> None:
        for i in range(params.repeatability_cycles):
            if self._abort_requested.is_set():
                return
            self._progress_fn("Repeatability", i + 1, params.repeatability_cycles)
            offset = repeat_offset_um if i % 2 == 0 else -repeat_offset_um
            self._controller.move_z_um(offset)
            self._settle()
            ok = self._controller.move_to_target(0.0)
            self._settle()
            self._measure_and_record("repeatability", i, offset, images_dir, results, move_success=ok)
            if not ok:
                # On failure move_to_target leaves the stage at the offset position; undo it so
                # cycles don't accumulate displacement.
                self._controller.move_z_um(-offset)
                self._settle()

    def _run_stability(self, params: LaserAFTestParams, images_dir, results) -> None:
        # Each sample is an independent laser-gated capture — measure_displacement_detailed()
        # turns the AF laser on before the frame and off after it, never holding it on across
        # samples — so the trace includes the laser's own on/off cycling behavior, exactly as
        # production acquisitions experience it. Every frame is saved.
        for i in range(params.stability_n_samples):
            if self._abort_requested.is_set():
                return
            self._progress_fn("Stability", i + 1, params.stability_n_samples)
            sample_start = time.monotonic()
            self._measure_and_record("stability", i, 0.0, images_dir, results)
            if i + 1 < params.stability_n_samples:
                remaining = params.stability_interval_s - (time.monotonic() - sample_start)
                if remaining > 0:
                    time.sleep(remaining)

    def _compute_metrics(self, results: LaserAFTestResults) -> None:
        sweep = [r for r in results.records if r.routine == "sweep"]
        if sweep:
            results.sweep = compute_sweep_metrics(
                [r.commanded_dz_um for r in sweep], [r.measured_displacement_um for r in sweep]
            )
        repeatability = [r for r in results.records if r.routine == "repeatability"]
        if repeatability:
            results.repeatability = compute_repeatability_metrics(
                [r.measured_displacement_um for r in repeatability], [bool(r.move_success) for r in repeatability]
            )
        stability = [r for r in results.records if r.routine == "stability"]
        if stability:
            results.stability = compute_stability_metrics(
                [r.timestamp_s for r in stability], [r.measured_displacement_um for r in stability]
            )
