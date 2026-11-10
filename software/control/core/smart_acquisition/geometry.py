"""Pixel <-> stage coordinate conversion, and stage-space merging of targets.

This module is the *only* place that knows how image pixels map to stage
millimetres. Analyzers work purely in pixel space so that this convention is
stated, guarded and tested exactly once.

Convention (verified against ``control/stitcher.py`` and
``control/widgets_mosaic.py``): stage +x is image +x, stage +y is increasing
image row, and a tile's top-left corner sits at its centre minus half its
extent. That identity mapping only holds while the camera applies no flip or
rotation -- see :func:`assert_identity_image_transform`.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import squid.logging

from .targets import FovRecord, PixelDetection, Target

_log = squid.logging.get_logger(__name__)


class ImageTransformError(RuntimeError):
    """Raised when the camera applies a flip/rotation that would mirror coordinates."""


def current_image_transform() -> Tuple[Any, Any]:
    """Return ``(flip_image, rotate_image_angle)`` from the machine config.

    Returns ``(None, None)`` if ``control._def`` cannot be imported (offline use
    outside a configured checkout), which is the same as "no transform".
    """
    try:
        from control._def import CAMERA_CONFIG

        return getattr(CAMERA_CONFIG, "FLIP_IMAGE", None), getattr(CAMERA_CONFIG, "ROTATE_IMAGE_ANGLE", None)
    except Exception as e:  # pragma: no cover - depends on machine config presence
        _log.debug(f"Could not read camera image transform from config ({e}); assuming none.")
        return None, None


def assert_identity_image_transform(flip_image: Any = "auto", rotate_image_angle: Any = "auto") -> None:
    """Fail loudly if the camera flips or rotates frames.

    ``control.utils.rotate_and_flip_image`` is applied to every frame before it is
    saved. If it is active, image pixel (0,0) is no longer the corner this
    module assumes, and every emitted coordinate would be silently mirrored --
    which looks like a plausible acquisition rather than an error. Refuse instead.
    """
    if flip_image == "auto" or rotate_image_angle == "auto":
        cfg_flip, cfg_rotate = current_image_transform()
        flip_image = cfg_flip if flip_image == "auto" else flip_image
        rotate_image_angle = cfg_rotate if rotate_image_angle == "auto" else rotate_image_angle

    problems = []
    if flip_image is not None:
        problems.append(f"CAMERA_CONFIG.FLIP_IMAGE = {flip_image!r}")
    if rotate_image_angle is not None:
        problems.append(f"CAMERA_CONFIG.ROTATE_IMAGE_ANGLE = {rotate_image_angle!r}")
    if problems:
        raise ImageTransformError(
            "Smart acquisition assumes an untransformed camera image, but "
            + " and ".join(problems)
            + ". Emitted stage coordinates would be mirrored or rotated. Support for this "
            "must be added to control/core/smart_acquisition/geometry.py before use."
        )


def pixel_to_stage_mm(px: float, py: float, fov: FovRecord) -> Tuple[float, float]:
    """Convert a pixel coordinate in ``fov``'s image to absolute stage millimetres.

    The +0.5 places the result at the centre of the pixel rather than its corner.
    """
    mm_per_px = fov.pixel_size_um / 1000.0
    x_mm = fov.x_mm + (px - fov.width_px / 2.0 + 0.5) * mm_per_px
    y_mm = fov.y_mm + (py - fov.height_px / 2.0 + 0.5) * mm_per_px
    return x_mm, y_mm


def stage_to_pixel(x_mm: float, y_mm: float, fov: FovRecord) -> Tuple[float, float]:
    """Inverse of :func:`pixel_to_stage_mm`. Used by the re-projection self-check."""
    mm_per_px = fov.pixel_size_um / 1000.0
    px = (x_mm - fov.x_mm) / mm_per_px + fov.width_px / 2.0 - 0.5
    py = (y_mm - fov.y_mm) / mm_per_px + fov.height_px / 2.0 - 0.5
    return px, py


def detection_to_target(det: PixelDetection, fov: FovRecord, carry_z: bool = False) -> Target:
    x_mm, y_mm = pixel_to_stage_mm(det.px, det.py, fov)
    metrics = dict(det.metrics)
    if det.radius_px:
        metrics.setdefault("radius_um", det.radius_px * fov.pixel_size_um)
    return Target(
        x_mm=x_mm,
        y_mm=y_mm,
        z_mm=fov.z_mm if carry_z else None,
        label=det.label,
        score=det.score,
        source_region=fov.region,
        source_fov=fov.fov,
        measured=det.measured,
        metrics=metrics,
    )


def detections_to_targets(detections: Iterable[PixelDetection], fov: FovRecord, carry_z: bool = False) -> List[Target]:
    return [detection_to_target(d, fov, carry_z=carry_z) for d in detections]


def merge_targets(targets: Sequence[Target], tolerance_mm: float) -> List[Target]:
    """Collapse targets that refer to the same physical object.

    Overlapping FOVs (and neighbouring FOVs that each see part of a well) will
    both report the same structure. Greedy single-link clustering in stage space
    is enough here because real targets are separated by roughly the grid pitch
    while duplicates differ only by stage repeatability.

    Within a cluster the *measured* detections win over inferred ones, and the
    survivor's position is the mean of the winners.
    """
    if tolerance_mm <= 0 or len(targets) < 2:
        return list(targets)

    remaining = list(targets)
    clusters: List[List[Target]] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        changed = True
        while changed:
            changed = False
            for other in list(remaining):
                if any(math.dist((other.x_mm, other.y_mm), (c.x_mm, c.y_mm)) <= tolerance_mm for c in cluster):
                    cluster.append(other)
                    remaining.remove(other)
                    changed = True
        clusters.append(cluster)

    merged: List[Target] = []
    for cluster in clusters:
        winners = [t for t in cluster if t.measured] or cluster
        n = len(winners)
        best = max(winners, key=lambda t: t.score)
        zs = [t.z_mm for t in winners if t.z_mm is not None]
        merged.append(
            Target(
                x_mm=sum(t.x_mm for t in winners) / n,
                y_mm=sum(t.y_mm for t in winners) / n,
                z_mm=(sum(zs) / len(zs)) if len(zs) == n and n > 0 else None,
                label=best.label,
                score=best.score,
                source_region=best.source_region,
                source_fov=best.source_fov,
                measured=any(t.measured for t in cluster),
                metrics={**best.metrics, "merged_from": len(cluster)},
            )
        )
    return merged
