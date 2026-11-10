"""Tie the stages together: acquisition folder in, stage targets out."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

import squid.logging

from . import geometry, source as source_mod
from .analyzers import base as analyzer_base
from .targets import FovRecord, PixelDetection, Target

_log = squid.logging.get_logger(__name__)


@dataclass
class AnalysisResult:
    targets: List[Target] = field(default_factory=list)
    fovs: List[FovRecord] = field(default_factory=list)
    detections_by_fov: Dict[Any, List[PixelDetection]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def measured_count(self) -> int:
        return sum(1 for t in self.targets if t.measured)


def _is_blank(image: np.ndarray) -> bool:
    """True for a plane that carries no image data.

    An OME-TIFF stack is pre-allocated zero-filled and planes are poked in as
    they are captured, so an aborted or still-running acquisition leaves whole
    planes as zeros. They are not dark images -- they are absent ones, and
    scoring them would let a blank plane win a focus sweep.
    """
    if image.size == 0:
        return True
    return not np.any(image)


def _record_error(result: "AnalysisResult", message: str) -> None:
    """Add an error once, however many planes hit it."""
    if message not in result.errors:
        result.errors.append(message)


def _sharpness(image: np.ndarray) -> float:
    """Focus measure of a whole frame, used to break ties between z planes.

    Gradient magnitude divided by mean intensity. The division is what makes it
    usable across a z-stack: fluorescence photobleaches as the stack is
    acquired, and an un-normalised measure follows the intensity decay instead
    of the focus, reporting the first plane as sharpest every time.
    """
    import cv2

    gray = image if image.ndim == 2 else image[..., 0]
    try:
        f = np.asarray(gray, dtype=np.float32)
        mean = float(f.mean())
        if mean <= 0:
            return 0.0
        sobel_x = cv2.Sobel(f, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(f, cv2.CV_64F, 0, 1, ksize=3)
        return float(cv2.magnitude(sobel_x, sobel_y).mean()) / mean
    except Exception as e:  # a metric failure must not abort the sweep
        _log.debug(f"Could not compute sharpness: {e}")
        return 0.0


def _plane_score(targets: List[Target], sharpness: float) -> tuple:
    """How good a single z-plane looked, best-first when compared with max().

    Primary criterion is the analyzer's own confidence -- the sum of its
    detection scores. For ``focus_score`` that *is* the focus measure, so it
    decides outright.

    Sharpness breaks ties, and that matters: ``ring_grid`` scores 1.0 per
    readable ring, which saturates at 9 on every plane where the lattice fits,
    blurred or not. Without the tie-break a defocused plane would win simply by
    coming first.
    """
    return (sum(t.score for t in targets), sharpness)


def _focus_curve_value(targets: List[Target], sharpness: float, use_sharpness: bool) -> float:
    return sharpness if use_sharpness else sum(t.score for t in targets)


def _interpolate_focus_z(ordered_keys, best_index, fov_by_key, curve) -> tuple:
    """Refine the focus Z below the z-step by fitting a parabola to the peak.

    A focus measure near best focus is well approximated by a quadratic, so the
    vertex of the parabola through the winning plane and its two neighbours
    lands closer to true focus than any plane does. With a 3 um step, picking
    the plane alone can be up to 1.5 um out; this typically gets well inside
    that.

    Returns ``(z_mm, interpolated, note)``. Falls back to the plane's own Z
    whenever the fit is not trustworthy: at the ends of the stack (no bracket,
    and true focus may lie outside the stack entirely), when the three points
    are not concave (no peak -- noise or a monotonic ramp), or when the vertex
    escapes the bracketing planes.
    """
    best_fov = fov_by_key[ordered_keys[best_index]]
    z_here = best_fov.z_mm

    if best_index == 0 or best_index == len(ordered_keys) - 1:
        return z_here, False, "peak at stack edge; true focus may be outside the stack"

    trio = [ordered_keys[best_index - 1], ordered_keys[best_index], ordered_keys[best_index + 1]]
    zs = [fov_by_key[k].z_mm for k in trio]
    ys = [curve[k] for k in trio]

    if any(z is None for z in zs):
        return z_here, False, "missing z on a neighbouring plane"
    if len({round(float(y), 12) for y in ys}) == 1:
        return z_here, False, "focus curve is flat around the peak"

    try:
        a, b, _c = np.polyfit(np.asarray(zs, dtype=float), np.asarray(ys, dtype=float), 2)
    except Exception as e:
        _log.debug(f"Focus interpolation failed: {e}")
        return z_here, False, "quadratic fit failed"

    if a >= 0:
        return z_here, False, "focus curve is not concave at the peak"

    z_peak = float(-b / (2.0 * a))
    low, high = min(zs[0], zs[2]), max(zs[0], zs[2])
    if not (low <= z_peak <= high):
        return z_here, False, "interpolated peak fell outside the bracketing planes"
    return z_peak, True, ""


def _best_plane_per_fov(
    targets_by_key, fov_by_key, sharpness_by_key, result: "AnalysisResult", interpolate: bool = True
) -> List[Any]:
    """Keep one z-plane per (region, fov): the best-scoring one.

    Records the winning plane on each surviving target so the table and the
    emitted metadata say which slice the Z came from.
    """
    by_fov: Dict[Any, List[Any]] = {}
    for key in targets_by_key:
        fov = fov_by_key[key]
        by_fov.setdefault((fov.region, fov.fov), []).append(key)

    kept = []
    for (region, fov_index), keys in by_fov.items():
        scored = [(k, _plane_score(targets_by_key[k], sharpness_by_key.get(k, 0.0))) for k in keys if targets_by_key[k]]
        if not scored:
            _record_error(result, f"{region}/fov{fov_index}: nothing found on any z plane")
            continue
        best_key, best_score = max(scored, key=lambda ks: ks[1])
        best_fov = fov_by_key[best_key]

        # Refine below the z-step. Interpolate whichever quantity actually
        # decided the winner: the analyzer's score when it varies across the
        # peak, otherwise the sharpness that broke the tie (ring_grid saturates
        # at one point per ring, so its own score carries no focus information).
        ordered = sorted((k for k, _ in scored), key=lambda k: fov_by_key[k].z_level)
        analyzer_curve = {
            k: _focus_curve_value(targets_by_key[k], sharpness_by_key.get(k, 0.0), False) for k in ordered
        }
        use_sharpness = len({round(v, 12) for v in analyzer_curve.values()}) == 1
        curve = {k: sharpness_by_key.get(k, 0.0) for k in ordered} if use_sharpness else analyzer_curve

        z_mm, interpolated, note = (best_fov.z_mm, False, "interpolation disabled")
        if interpolate:
            z_mm, interpolated, note = _interpolate_focus_z(ordered, ordered.index(best_key), fov_by_key, curve)

        detail = f"z={z_mm:.5f} mm" if z_mm is not None else "z unknown"
        if interpolated:
            detail += f" (interpolated from z_level {best_fov.z_level})"
        elif note:
            detail += f" (plane z; {note})"
        _log.info(
            f"{region}/fov{fov_index}: best focus at z_level {best_fov.z_level}, {detail}; "
            f"score={best_score[0]:.4g}, sharpness={best_score[1]:.4g}, {len(scored)} plane(s)"
        )

        targets_by_key[best_key] = [
            Target(
                x_mm=t.x_mm,
                y_mm=t.y_mm,
                z_mm=z_mm,
                label=t.label,
                score=t.score,
                source_region=t.source_region,
                source_fov=t.source_fov,
                measured=t.measured,
                metrics={
                    **t.metrics,
                    "best_z_level": best_fov.z_level,
                    "planes_examined": len(scored),
                    "plane_sharpness": best_score[1],
                    "z_interpolated": interpolated,
                    "z_plane_mm": best_fov.z_mm,
                    **({"z_interpolation_note": note} if note else {}),
                },
            )
            for t in targets_by_key[best_key]
        ]
        kept.append(best_key)
    return kept


def run_analysis(
    acq_dir: str,
    analyzer_name: str,
    params: Optional[Dict[str, Any]] = None,
    channel: Optional[str] = None,
    time_point: Optional[int] = None,
    z_level: Optional[int] = None,
    carry_z: bool = False,
    merge_tolerance_um: Optional[float] = None,
    min_score: float = 0.0,
    max_targets: Optional[int] = None,
    require_complete: bool = True,
    check_image_transform: bool = True,
    sweep_z: bool = False,
    interpolate_z: bool = True,
    progress_cb=None,
) -> AnalysisResult:
    """Analyse every FOV of a finished acquisition and return stage targets.

    Detection runs per FOV and results are merged in stage space afterwards.
    That handles one-FOV-per-well, several FOVs tiling one well, and several
    wells inside one FOV with the same code, and keeps stitching error out of
    the coordinate path entirely.

    With ``sweep_z`` the analyzer is run on *every* plane of each z-stack and
    only the best-scoring plane survives, with that plane's Z carried into the
    emitted target. That turns any analyzer into a focus finder: ``focus_score``
    picks the sharpest plane, and ``ring_grid`` finds the plane where the most
    rings are readable. It implies ``carry_z``, since the whole point is the Z.

    ``interpolate_z`` then refines that Z below the z-step by fitting a parabola
    across the peak, so the answer is not quantised to the planes you happened
    to acquire.
    """
    if check_image_transform:
        # Refuse rather than silently emit mirrored coordinates.
        geometry.assert_identity_image_transform()

    src = source_mod.open_acquisition(acq_dir, require_complete=require_complete)
    analyzer = analyzer_base.create(analyzer_name, params or {})
    fovs = source_mod.list_fovs(src, time_point=time_point, z_level=z_level, all_z=sweep_z)
    if sweep_z:
        carry_z = True
        planes = len({f.z_level for f in fovs})
        _log.info(f"Analysing {len(fovs)} plane(s) across {planes} z level(s) from {acq_dir} with {analyzer_name}")
    else:
        _log.info(f"Analysing {len(fovs)} FOV(s) from {acq_dir} with {analyzer_name}")

    result = AnalysisResult(fovs=fovs)
    all_targets: List[Target] = []
    targets_by_key: Dict[Any, List[Target]] = {}
    fov_by_key: Dict[Any, FovRecord] = {}
    sharpness_by_key: Dict[Any, float] = {}

    for i, fov in enumerate(fovs):
        try:
            image = source_mod.load_fov_image(src, fov, channel=channel)
        except Exception as e:
            msg = f"{fov.region}/fov{fov.fov}: could not load image ({e})"
            if msg not in result.errors:
                _log.warning(msg)
            _record_error(result, msg)
            continue

        if _is_blank(image):
            msg = f"{fov.region}/fov{fov.fov}: blank plane(s) skipped (unwritten pre-allocated image data)"
            if msg not in result.errors:
                _log.warning(msg)
            _record_error(result, msg)
            continue

        # width/height may not have been resolvable up front; fix them now so
        # the pixel->stage transform uses the true frame size.
        if fov.width_px != image.shape[1] or fov.height_px != image.shape[0]:
            fov = FovRecord(
                region=fov.region,
                fov=fov.fov,
                time_point=fov.time_point,
                z_level=fov.z_level,
                x_mm=fov.x_mm,
                y_mm=fov.y_mm,
                z_mm=fov.z_mm,
                pixel_size_um=fov.pixel_size_um,
                width_px=int(image.shape[1]),
                height_px=int(image.shape[0]),
            )
            result.fovs[i] = fov

        try:
            detections = analyzer.analyze_fov(image, fov)
        except Exception as e:
            msg = f"{fov.region}/fov{fov.fov}: analyzer failed ({e})"
            if msg not in result.errors:
                _log.warning(msg)
            _record_error(result, msg)
            continue

        result.detections_by_fov[fov.key] = detections
        targets_by_key[fov.key] = geometry.detections_to_targets(detections, fov, carry_z=carry_z)
        fov_by_key[fov.key] = fov
        if sweep_z:
            sharpness_by_key[fov.key] = _sharpness(image)
        if progress_cb:
            progress_cb(i + 1, len(fovs))

    if sweep_z:
        kept_keys = _best_plane_per_fov(targets_by_key, fov_by_key, sharpness_by_key, result, interpolate=interpolate_z)
    else:
        kept_keys = list(targets_by_key)
    for key in kept_keys:
        all_targets.extend(targets_by_key[key])

    if merge_tolerance_um is None:
        # Default to a fraction of the analyzer's own spacing prior when it has
        # one, so overlapping FOVs collapse but real neighbours do not.
        pitch = getattr(analyzer, "params", {}).get("grid_pitch_um")
        merge_tolerance_um = 0.3 * pitch if pitch else 0.0
    if merge_tolerance_um:
        before = len(all_targets)
        all_targets = geometry.merge_targets(all_targets, merge_tolerance_um / 1000.0)
        if before != len(all_targets):
            _log.info(f"Merged {before} detections into {len(all_targets)} unique targets")

    if min_score > 0:
        all_targets = [t for t in all_targets if t.score >= min_score]

    all_targets.sort(key=lambda t: (t.source_region, t.y_mm, t.x_mm))
    if max_targets is not None:
        all_targets = sorted(all_targets, key=lambda t: -t.score)[:max_targets]
        all_targets.sort(key=lambda t: (t.source_region, t.y_mm, t.x_mm))

    result.targets = all_targets
    return result


def save_overlays(
    acq_dir: str,
    result: AnalysisResult,
    out_dir: str,
    channel: Optional[str] = None,
    require_complete: bool = True,
) -> List[str]:
    """Write annotated PNGs showing what was detected in each FOV.

    Green = measured, orange = inferred from the lattice. This is the cheapest
    way to check the analyzer and the coordinate transform without using the
    microscope.
    """
    import cv2

    src = source_mod.open_acquisition(acq_dir, require_complete=require_complete)
    os.makedirs(out_dir, exist_ok=True)
    written = []

    for fov in result.fovs:
        detections = result.detections_by_fov.get(fov.key)
        if not detections:
            continue
        try:
            image = source_mod.load_fov_image(src, fov, channel=channel)
        except Exception:
            continue

        img = np.asarray(image)
        if img.dtype != np.uint8:
            img = cv2.normalize(img.astype(np.float32), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()

        for det in detections:
            color = (0, 255, 0) if det.measured else (0, 165, 255)
            center = (int(round(det.px)), int(round(det.py)))
            if det.radius_px > 0:
                cv2.circle(vis, center, int(round(det.radius_px)), color, 3)
            cv2.drawMarker(vis, center, color, cv2.MARKER_CROSS, 24, 2)
            x_mm, y_mm = geometry.pixel_to_stage_mm(det.px, det.py, fov)
            cv2.putText(
                vis,
                f"{x_mm:.3f},{y_mm:.3f}",
                (center[0] + 12, center[1] - 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )

        path = os.path.join(out_dir, f"{fov.region}_{fov.fov}_overlay.png")
        cv2.imwrite(path, vis)
        written.append(path)

    return written
