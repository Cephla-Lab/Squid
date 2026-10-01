"""Open- and closed-loop laser AF test: record focus (z) against time with the AF laser kept on.

Each loop takes one focus-camera frame, fits the spot and turns it into a displacement from the reference. In
closed loop it then moves the objective piezo by the control law in next_piezo_um; in open loop the piezo is never
commanded, so the record is the raw drift. Qt-free: the GUI runs it on a worker thread, the tests against
simulated hardware.
"""

import csv
import dataclasses
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from control import utils
from control.microcontroller import Microcontroller
from control.models import LaserAFConfig
from control.piezo import PiezoStage

DISPLAY_INTERVAL_S = 0.1


@dataclasses.dataclass(frozen=True)
class Sample:
    t_s: float  # since the start of the run
    displacement_um: float  # spot offset from the reference; NaN when no spot was found
    piezo_um: float  # piezo position while the frame was taken
    spot_x_px: float
    spot_y_px: float
    frame_ms: float  # focus-camera trigger + read
    fit_ms: float  # spot fit
    dac_ms: float  # piezo write until the controller acked it; NaN when the piezo was not moved


@dataclasses.dataclass(frozen=True)
class Summary:
    samples: int
    missed: int
    rate_hz: float
    rms_displacement_um: float  # about the reference, over the samples that found a spot
    median_frame_ms: float
    median_fit_ms: float
    median_dac_ms: float  # NaN in open loop

    def __str__(self) -> str:
        piezo_write = "n/a" if math.isnan(self.median_dac_ms) else f"{self.median_dac_ms:.2f} ms"
        return (
            f"{self.samples} samples at {self.rate_hz:.0f} Hz ({self.missed} missed), "
            f"RMS displacement {self.rms_displacement_um:.3f} um; median frame {self.median_frame_ms:.2f} ms, "
            f"fit {self.median_fit_ms:.2f} ms, piezo write {piezo_write}"
        )


def next_piezo_um(piezo_um: float, displacement_um: float, gain: float, range_um: float) -> float:
    """Where to put the piezo next, given where it is and the displacement just measured there.

    displacement_um is the spot's offset from the reference in um. Moving the piezo by -displacement_um brings the
    spot back to the reference - the same move Move to Target 0 makes. The loop only calls this with a valid,
    in-range measurement, and the result must lie in [0, range_um] (PiezoStage.move_to raises otherwise).
    """
    target_um = piezo_um - gain * displacement_um
    return min(max(target_um, 0.0), range_um)


def run(
    get_frame: Callable[[], Optional[np.ndarray]],
    config: LaserAFConfig,
    microcontroller: Microcontroller,
    piezo: PiezoStage,
    duration_s: float,
    closed_loop: bool,
    gain: float,
    display_fn: Optional[Callable[[np.ndarray], None]] = None,
) -> List[Sample]:
    """Run the loop for duration_s with the AF laser on throughout; the laser is off again when this returns or
    raises. get_frame must return a frame taken after it was called (LaserAutofocusController.get_new_frame).
    A spot outside config.laser_af_range is recorded but never acted on, as in laser AF itself."""
    if config.x_reference is None:
        raise ValueError("Laser AF has no reference - set one before running the loop test")
    samples = []
    try:
        microcontroller.turn_on_AF_laser()
        microcontroller.wait_till_operation_is_completed()
        start = time.perf_counter()
        last_display = -math.inf
        while (t_start := time.perf_counter()) - start < duration_s:
            piezo_um = piezo.position
            image = get_frame()
            t_frame = time.perf_counter()
            spot = _find_spot(image, config)
            t_fit = time.perf_counter()

            x, y = spot if spot is not None else (math.nan, math.nan)
            displacement_um = (x - config.x_reference) * config.pixel_to_um
            dac_ms = math.nan
            if closed_loop and spot is not None and abs(displacement_um) <= config.laser_af_range:
                target_um = next_piezo_um(piezo_um, displacement_um, gain, piezo.range_um)
                t_dac = time.perf_counter()
                piezo.move_to(target_um)
                microcontroller.wait_till_operation_is_completed()
                dac_ms = (time.perf_counter() - t_dac) * 1000

            samples.append(
                Sample(
                    t_s=t_start - start,
                    displacement_um=displacement_um,
                    piezo_um=piezo_um,
                    spot_x_px=x,
                    spot_y_px=y,
                    frame_ms=(t_frame - t_start) * 1000,
                    fit_ms=(t_fit - t_frame) * 1000,
                    dac_ms=dac_ms,
                )
            )
            if display_fn is not None and image is not None and t_start - last_display >= DISPLAY_INTERVAL_S:
                display_fn(image)
                last_display = t_start
    finally:
        microcontroller.turn_off_AF_laser()
        microcontroller.wait_till_operation_is_completed()
    return samples


def _find_spot(image: Optional[np.ndarray], config: LaserAFConfig) -> Optional[Tuple[float, float]]:
    try:
        return utils.find_spot_location(
            image,
            mode=config.get_spot_detection_mode(),
            params=config.spot_detection_params(),
            filter_sigma=config.filter_sigma,
        )
    except ValueError:  # find_spot_location's answer for "no spot" (and for a missing frame)
        return None


def _median(values) -> float:
    finite = [v for v in values if not math.isnan(v)]
    return statistics.median(finite) if finite else math.nan


def summarize(samples: List[Sample]) -> Summary:
    found = [s.displacement_um for s in samples if not math.isnan(s.displacement_um)]
    span_s = samples[-1].t_s - samples[0].t_s if len(samples) > 1 else 0.0
    return Summary(
        samples=len(samples),
        missed=len(samples) - len(found),
        rate_hz=(len(samples) - 1) / span_s if span_s > 0 else math.nan,
        rms_displacement_um=math.sqrt(sum(d * d for d in found) / len(found)) if found else math.nan,
        median_frame_ms=_median(s.frame_ms for s in samples),
        median_fit_ms=_median(s.fit_ms for s in samples),
        median_dac_ms=_median(s.dac_ms for s in samples),
    )


def save(samples: List[Sample], base_dir, objective: str, closed_loop: bool, gain: float) -> Path:
    """Write z_vs_t.csv and z_vs_t.png into {base_dir}/laser_af_closed_loop/{objective}_{mode}_{timestamp}/ and
    return that folder."""
    mode = "closed_loop" if closed_loop else "open_loop"
    folder = Path(base_dir) / "laser_af_closed_loop" / f"{objective}_{mode}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    folder.mkdir(parents=True)
    settings = f"mode={mode}" + (f", gain={gain:g}" if closed_loop else "") + f", objective={objective}"
    summary = summarize(samples)

    with open(folder / "z_vs_t.csv", "w", newline="") as f:
        f.write(f"# {settings}\n")
        writer = csv.writer(f)
        writer.writerow(field.name for field in dataclasses.fields(Sample))
        writer.writerows(dataclasses.astuple(s) for s in samples)

    _plot(samples, folder / "z_vs_t.png", title=f"Laser AF z vs t - {settings}", subtitle=str(summary))
    return folder


_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_GRID = "#e4e3df"
_SERIES = "#2a78d6"


def _plot(samples: List[Sample], path: Path, title: str, subtitle: str) -> None:
    # Figure + Agg canvas, not pyplot: this runs on the GUI's worker thread and must not touch pyplot's global state.
    fig = Figure(figsize=(11, 8), constrained_layout=True, facecolor=_SURFACE)
    FigureCanvasAgg(fig)
    t = np.array([s.t_s for s in samples])
    panels = [
        ("displacement (um)", np.array([s.displacement_um for s in samples]), t),
        ("piezo (um)", np.array([s.piezo_um for s in samples]), t),
        ("loop period (ms)", np.diff(t) * 1000, t[1:]),
    ]
    axes = fig.subplots(len(panels), 1, sharex=True)
    for ax, (label, values, x) in zip(axes, panels):
        ax.plot(x, values, color=_SERIES, linewidth=1.0)
        ax.set_facecolor(_SURFACE)
        ax.set_ylabel(label, color=_INK_SECONDARY)
        ax.grid(True, color=_GRID, linewidth=0.6)
        ax.tick_params(colors=_INK_SECONDARY, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(_GRID)
    axes[-1].set_xlabel("time (s)", color=_INK_SECONDARY)
    fig.suptitle(f"{title}\n{subtitle}", color=_INK, fontsize=11)
    fig.savefig(path, dpi=120, facecolor=_SURFACE)
