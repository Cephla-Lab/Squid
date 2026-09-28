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
from typing import Callable, Dict, List, Optional, Tuple

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
    """Put the objective, XY and Z back. Every step is attempted even if an earlier one fails, so a
    stuck XY axis never leaves Z out of place; any failure raises RestoreError, which stops the run."""
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
    phase2: Optional[Callable[[CycleResult, CalibrationView], None]] = None,
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

    for index in range(cfg.cycles):
        if should_cancel():
            stopped = "cancelled"
            break
        start_xy = hw.get_xy_um()
        start = (hw.current_objective(), *start_xy, hw.get_z_um())
        cycle = CycleResult(index)
        cycles.append(cycle)
        try:
            for spec in ordered:
                if should_cancel():
                    raise RunCancelled()
                progress(f"Cycle {index + 1}/{cfg.cycles}: {spec.name}")
                result = ObjectiveCycleResult(spec.name)
                cycle.objectives[spec.name] = result
                hw.switch_objective(spec.name)  # a failed switch is a hardware fault, not a quality gate
                z_after_switch = hw.get_z_um()
                try:
                    result.focus = focus_sweep(
                        hw,
                        objective=spec.name,
                        channel=cfg.channel,
                        na=spec.na,
                        center_um=hw.get_z_um(),
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
            if phase2 is not None:
                matrices = {n: r.pixel.matrix_um_per_px for n, r in cycle.objectives.items() if r.pixel is not None}
                phase2(cycle, CalibrationView(matrices))
        except RunCancelled:  # Cancel pressed, or a manual objective switch declined
            stopped = "cancelled"
        except Exception as e:  # a hardware or programming fault: restore, then stop the run
            cycle.error = f"{type(e).__name__}: {e}"
            stopped = cycle.error
        try:
            _restore(hw, *start)
        except RestoreError as e:
            stopped = str(e)
            break
        if stopped:
            break

    by_objective: Dict[str, List[PixelSizeResult]] = {}
    for cycle in cycles:
        for name, r in cycle.objectives.items():
            if r.pixel is not None:
                by_objective.setdefault(name, []).append(r.pixel)
    summaries = {name: _summarize(name, results) for name, results in by_objective.items()}
    return RunResult(cycles, summaries, stopped)
