"""The calibration run: cycles over objectives, restore rules, a phase-2 hook, averaging.

Restore rules (AI-docs objective-offset design §6.6): every cycle, however it ends, returns to the
starting objective, XY and Z. A failed restore stops the whole run. A quality-gate failure
(CalibrationError) is recorded, XY and Z go back to where that objective started, and the run
continues; RunCancelled (Cancel, or a declined manual switch) and any other exception stop the run
after the restore. Cancel is checked before every frame: a stage move or turret switch already in
progress finishes first, then the restore runs.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from squid.objective_calibration.focus import FocusResult, focus_sweep
from squid.objective_calibration.hardware import CalibrationError, LimitError, RestoreError, RunCancelled
from squid.objective_calibration.pixel_size import PixelSizeResult, approach_xy, decompose, measure_pixel_size


@dataclass(frozen=True)
class ObjectiveSpec:
    name: str
    magnification: float
    na: float
    nominal_px_um: float


@dataclass
class RunConfig:
    objectives: List[ObjectiveSpec]
    channel: str
    search_range_um: float = 100.0
    cycles: int = 3
    measure_pixel_size: bool = True
    # An offsets-only run (measure_pixel_size False) registers with these matrices, for the current
    # image pixels, from the saved and valid records (spec B §4.5); a combined run uses this cycle's.
    saved_matrices_um_per_px: Dict[str, np.ndarray] = field(default_factory=dict)
    # Each objective's parfocal residual from a prior calibration: z_frame minus the changer's own
    # frame (spec C §4). The first focus of objective k is centred on the Z after the switch plus
    # residual[k] - residual[previous objective]; a missing objective counts as 0 (today's frame).
    predicted_residual_um: Dict[str, float] = field(default_factory=dict)


@dataclass
class ObjectiveCycleResult:
    objective: str
    focus: Optional[FocusResult] = None
    pixel: Optional[PixelSizeResult] = None
    image: Optional[np.ndarray] = None
    error: Optional[str] = None


@dataclass
class CycleResult:
    index: int
    objectives: Dict[str, ObjectiveCycleResult] = field(default_factory=dict)
    error: Optional[str] = None
    start_objective: Optional[str] = None
    start_xy_um: Tuple[float, float] = (0.0, 0.0)
    start_z_um: float = 0.0


class CalibrationView:
    """The pixel matrices measured in this cycle, for the phase-2 hook (unsaved; the store is not touched)."""

    def __init__(self, matrices: Dict[str, np.ndarray]):
        self._matrices = dict(matrices)

    def pixel_matrix(self, name: str) -> Optional[np.ndarray]:
        return self._matrices.get(name)

    def pixel_size_um(self, name: str) -> Optional[float]:
        m = self._matrices.get(name)
        return None if m is None else math.sqrt(abs(np.linalg.det(m)))


@dataclass
class PixelSizeSummary:
    objective: str
    cycles: int
    pixel_size_um: float
    std_pixel_size_um: Optional[float]
    matrix_um_per_px: np.ndarray
    rotation_deg: float
    flip: np.ndarray
    orientation_matches_mosaic: bool
    anisotropy: float
    fit_residual_um: float


@dataclass
class RunResult:
    cycles: List[CycleResult]
    pixel_sizes: Dict[str, PixelSizeSummary]
    stopped: Optional[str] = None
    restore_failed: bool = False  # the machine may not be where the run started


class _Cancellable:
    """The hardware, checking for Cancel before every snap: a cancel is answered within one frame."""

    def __init__(self, hw, should_cancel: Callable[[], bool]):
        self._hw = hw
        self._should_cancel = should_cancel

    def __getattr__(self, name):
        return getattr(self._hw, name)

    def snap(self, objective: str, channel: str) -> np.ndarray:
        if self._should_cancel():
            raise RunCancelled()
        return self._hw.snap(objective, channel)


def square_um(objectives: List[ObjectiveSpec], frame_shape: Tuple[int, int]) -> float:
    """Side W of the shared physical focus square: half the smallest nominal field of view (spec B §5.1)."""
    shorter = min(frame_shape)
    return 0.5 * min(spec.nominal_px_um * shorter for spec in objectives)


def _return_xy(hw, x: float, y: float) -> None:
    """Back to (x, y): from the same side when the pre-move fits inside the limits, else directly.
    Going back needs the position, not backlash-free registration; the start itself is in limits."""
    try:
        approach_xy(hw, x, y)
    except LimitError:
        hw.move_xy_to_um(x, y)


def _restore(hw, objective: str, x: float, y: float, z: float) -> None:
    """Put the objective, XY and Z back; any failure raises RestoreError, which stops the run.

    XY is attempted even if the objective step fails (a lateral move is safe), and Z even if XY
    fails, so a stuck XY axis never leaves Z out of place. But Z returns to the imaging position
    only once the start objective is confirmed in place: with a jammed or partly rotated turret it
    stays where the changer left it."""
    failures = []
    try:
        if hw.current_objective() != objective:
            hw.switch_objective(objective)
    except Exception as e:  # noqa: BLE001 - reported below, after the other steps
        failures.append(f"objective: {e}")
    try:
        _return_xy(hw, x, y)
    except Exception as e:  # noqa: BLE001
        failures.append(f"XY: {e}")
    if hw.current_objective() != objective:
        failures.append(
            f"Z: not returned to {z:.1f} µm because {objective} is not confirmed in place; "
            "check the objective changer before moving Z"
        )
    else:
        try:
            hw.move_z_to_um(z)
        except Exception as e:  # noqa: BLE001
            failures.append(f"Z: {e}")
    if failures:
        raise RestoreError(f"Restoring {objective} at ({x:.1f}, {y:.1f}, {z:.1f}) µm failed: " + "; ".join(failures))


def _summarize(name: str, results: List[PixelSizeResult]) -> PixelSizeSummary:
    matrix = np.mean([r.matrix_um_per_px for r in results], axis=0)
    pixel_size, rotation, flip, anisotropy = decompose(matrix)
    sizes = [r.pixel_size_um for r in results]
    return PixelSizeSummary(
        objective=name,
        cycles=len(results),
        pixel_size_um=float(np.mean(sizes)),
        std_pixel_size_um=float(np.std(sizes, ddof=1)) if len(sizes) > 1 else None,
        matrix_um_per_px=matrix,
        rotation_deg=rotation,
        flip=flip,
        orientation_matches_mosaic=bool((flip == np.eye(2)).all()),
        anisotropy=anisotropy,
        fit_residual_um=max(r.fit_residual_um for r in results),
    )


def run_calibration(
    hw,
    cfg: RunConfig,
    *,
    fine_metric: Callable[[np.ndarray], float],
    phase2: Optional[Callable[[Any, CycleResult, CalibrationView], None]] = None,
    progress: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> RunResult:
    progress = progress or (lambda message: None)
    should_cancel = should_cancel or (lambda: False)
    hw = _Cancellable(hw, should_cancel)
    ordered = sorted(cfg.objectives, key=lambda spec: spec.magnification)
    try:
        side_um = square_um(cfg.objectives, hw.frame_shape(cfg.channel))
    except CalibrationError as e:
        return RunResult([], {}, str(e))
    cycles: List[CycleResult] = []
    stopped: Optional[str] = None
    restore_failed = False

    for index in range(cfg.cycles):
        if should_cancel():
            stopped = "cancelled"
            break
        start_xy = hw.get_xy_um()
        start = (hw.current_objective(), *start_xy, hw.get_z_um())
        cycle = CycleResult(index, start_objective=start[0], start_xy_um=start_xy, start_z_um=start[3])
        cycles.append(cycle)
        previous = start[0]
        try:
            for spec in ordered:
                if should_cancel():
                    raise RunCancelled()
                progress(f"Cycle {index + 1}/{cfg.cycles}: {spec.name}")
                result = ObjectiveCycleResult(spec.name)
                cycle.objectives[spec.name] = result
                hw.switch_objective(spec.name)  # a failed switch is a hardware fault, not a quality gate
                z_after_switch = hw.get_z_um()
                residual = cfg.predicted_residual_um
                predicted_step = residual.get(spec.name, 0.0) - residual.get(previous, 0.0)
                try:
                    result.focus = focus_sweep(
                        hw,
                        objective=spec.name,
                        channel=cfg.channel,
                        na=spec.na,
                        center_um=z_after_switch + predicted_step,
                        range_um=cfg.search_range_um,
                        square_px=side_um / spec.nominal_px_um,
                        fine_metric=fine_metric,
                    )
                    if cfg.measure_pixel_size:
                        result.pixel = measure_pixel_size(
                            hw, objective=spec.name, channel=cfg.channel, nominal_px_um=spec.nominal_px_um
                        )
                    result.image = hw.snap(spec.name, cfg.channel)
                except CalibrationError as e:
                    result.error = str(e)
                    # A failed sweep ends at the top of its range: the next objective must start from
                    # where this one started, not up to R_eff out of focus.
                    _return_xy(hw, *start_xy)
                    hw.move_z_to_um(z_after_switch)
                else:
                    # Z is at this objective's focus now; after a failure it is back at the last one's,
                    # and the next prediction starts from there.
                    previous = spec.name
            if phase2 is not None:
                if cfg.measure_pixel_size:
                    matrices = {n: r.pixel.matrix_um_per_px for n, r in cycle.objectives.items() if r.pixel is not None}
                else:
                    matrices = cfg.saved_matrices_um_per_px
                try:
                    phase2(hw, cycle, CalibrationView(matrices))
                except CalibrationError as e:  # a phase-2 quality gate fails the cycle; the run continues
                    cycle.error = str(e)
        except RunCancelled:  # Cancel pressed, or a manual objective switch declined
            stopped = "cancelled"
        except Exception as e:  # a hardware or programming fault: restore, then stop the run
            cycle.error = f"{type(e).__name__}: {e}"
            stopped = cycle.error
        try:
            _restore(hw, *start)
        except RestoreError as e:
            stopped = str(e)
            restore_failed = True
            break
        if stopped:
            break

    by_objective: Dict[str, List[PixelSizeResult]] = {}
    for cycle in cycles:
        for name, r in cycle.objectives.items():
            if r.pixel is not None:
                by_objective.setdefault(name, []).append(r.pixel)
    summaries = {name: _summarize(name, results) for name, results in by_objective.items()}
    return RunResult(cycles, summaries, stopped, restore_failed)


def cycle_report(result: RunResult) -> List[str]:
    """One line per objective per cycle, with every gate value or the failure: the bench record."""
    lines = []
    for cycle in result.cycles:
        for name, r in cycle.objectives.items():
            prefix = f"cycle {cycle.index + 1} {name}: "
            if r.error:
                lines.append(prefix + r.error)
                continue
            parts = []
            if r.focus is not None:
                parts.append(f"focus {r.focus.z_best_um:.2f} µm (rise {r.focus.peak_rise:.0%})")
            if r.pixel is not None:
                p = r.pixel
                parts.append(
                    f"{p.pixel_size_um:.5f} µm/px, rotation {p.rotation_deg:+.3f}°, anisotropy {p.anisotropy:.4f}, "
                    f"residual {p.fit_residual_um:.2f} µm, drift {p.drift_um:.2f} µm"
                )
            lines.append(prefix + ", ".join(parts))
        if cycle.error:
            lines.append(f"cycle {cycle.index + 1}: {cycle.error}")
    return lines
