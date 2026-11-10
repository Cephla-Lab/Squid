"""Score fields of view by focus quality and signal-to-noise.

This analyzer exists to keep the :class:`Analyzer` contract honest. The ring
detector finds *where* things are; this one decides *whether a place is worth
revisiting* -- a different question answered through the same interface. If a
future rule ("re-image every FOV whose focus dropped below X", "skip empty
wells") can be expressed here, the contract is doing its job.

It emits at most one detection per FOV, at the FOV centre (or at the best tile
centre when ``tiles`` > 1), carrying the measured metrics.
"""

from typing import Any, Dict, List

import numpy as np

from control._def import FocusMeasureOperator
from control.utils import calculate_focus_measure

from ..targets import FovRecord, PixelDetection
from .base import BaseAnalyzer, register


def _resolve_focus_method(method):
    """Accept an operator name ('LAPE', 'GLVA', 'TENENGRAD') or the enum itself."""
    if method is None:
        return None
    if isinstance(method, FocusMeasureOperator):
        return method
    try:
        return FocusMeasureOperator[str(method).upper()]
    except KeyError:
        valid = [m.name for m in FocusMeasureOperator]
        raise ValueError(f"Unknown focus_method {method!r}. Valid: {valid}")


def _snr(tile: np.ndarray) -> float:
    """Crude but robust SNR: median signal over noise estimated from the MAD.

    The MAD is used rather than the standard deviation so that a few bright
    objects do not get counted as noise.
    """
    tile = tile.astype(np.float64)
    median = float(np.median(tile))
    mad = float(np.median(np.abs(tile - median)))
    noise = mad * 1.4826  # MAD -> sigma for a normal distribution
    if noise <= 0:
        return float("inf") if median > 0 else 0.0
    return median / noise


class FocusScoreAnalyzer(BaseAnalyzer):
    """Report focus/SNR for a FOV, optionally picking the best sub-tile."""

    name = "focus_score"
    primary = ("tiles", "min_focus_measure", "min_snr")
    defaults: Dict[str, Any] = {
        "tiles": 1,  # split the FOV into tiles x tiles and score each
        "min_focus_measure": 0.0,  # emit nothing below this
        "min_snr": 0.0,
        # Tenengrad, not the LAPE default. Measured on a real bleaching 405 nm
        # z-stack, LAPE simply tracks the intensity decay and calls the *first*
        # plane of every stack best focus; Tenengrad finds the true peak.
        "focus_method": "TENENGRAD",
        # Divide the illumination level out, so a brighter blurry plane cannot
        # beat a dimmer sharp one. Essential across a stack that photobleaches.
        "normalize_intensity": True,
    }

    #: How each operator scales with image intensity, so it can be divided out.
    #: Tenengrad and the standard deviation are linear in intensity; LAPE is a
    #: mean of squared second derivatives, so it goes as intensity squared.
    _INTENSITY_ORDER = {"TENENGRAD": 1, "GLVA": 1, "LAPE": 2}

    def _measure(self, tile: np.ndarray, method) -> float:
        kwargs = {"method": method} if method is not None else {}
        focus = float(calculate_focus_measure(tile, **kwargs))
        if not self.params["normalize_intensity"]:
            return focus
        order = self._INTENSITY_ORDER.get(getattr(method, "name", ""), 1)
        mean = float(np.asarray(tile, dtype=np.float64).mean())
        if mean <= 0:
            return 0.0
        return focus / (mean**order)

    def analyze_fov(self, image: np.ndarray, fov: FovRecord) -> List[PixelDetection]:
        p = self.params
        gray = image if image.ndim == 2 else image[..., 0]
        n = max(1, int(p["tiles"]))
        h, w = gray.shape[:2]
        method = _resolve_focus_method(p["focus_method"])

        best = None
        for row in range(n):
            for col in range(n):
                y0, y1 = row * h // n, (row + 1) * h // n
                x0, x1 = col * w // n, (col + 1) * w // n
                tile = gray[y0:y1, x0:x1]
                if tile.size == 0:
                    continue
                focus = self._measure(tile, method)
                snr = _snr(tile)
                if best is None or focus > best[0]:
                    best = (focus, snr, (x0 + x1) / 2.0, (y0 + y1) / 2.0)

        if best is None:
            return []
        focus, snr, cx, cy = best
        if focus < p["min_focus_measure"] or snr < p["min_snr"]:
            return []

        return [
            PixelDetection(
                px=cx,
                py=cy,
                radius_px=0.0,
                score=focus,
                measured=True,
                # The region is added by emit.label_targets; don't repeat it.
                label=f"fov{fov.fov}",
                metrics={"focus_measure": focus, "snr": snr},
            )
        ]


register(FocusScoreAnalyzer)
