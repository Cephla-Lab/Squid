"""Detect a regular grid of ring/circle structures in a low-magnification image.

Written for a 384-well plate whose wells each hold a 3x3 array of ~320 um rings
on a ~1 mm pitch, but nothing here is specific to those numbers: every prior is
expressed in microns and converted using the FOV's own pixel size, so the same
code works at any magnification.

Two stages:

1. *Candidates* -- flat-field the image by dividing out a heavy Gaussian blur
   (this kills vignetting and uneven illumination, which otherwise dominate
   Hough's gradient response), then run cv2.HoughCircles at several accumulator
   thresholds and pool the results. Over-detection here is fine.
2. *Lattice fit* -- the structures are manufactured on a regular grid, which is
   a much stronger prior than any single-circle score. The grid is rigid: a
   square lattice that may be translated and rotated, with one pitch (held
   near ``grid_pitch_um``) for rows and columns alike. Hypotheses come from
   candidate pairs a pitch or a diagonal apart and are scored by how many nodes
   they explain; the best is refined by least squares on the rigid model. Every
   ring is reported at its lattice node with one shared radius, so debris off
   the grid is ignored, a mis-centred circle cannot bend the grid, and nodes
   where the structure is absent or unreadable are still filled in.
"""

from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..targets import FovRecord, PixelDetection
from .base import BaseAnalyzer, register

#: Ring radius the image is rescaled to before detection. Fixing this is what
#: makes the Hough parameters below magnification-independent.
_WORK_RADIUS_PX = 50.0


def _to_gray_f32(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.shape[2] == 3 else image[..., 0]
    return image.astype(np.float32)


def _flat_field(img: np.ndarray, sigma: float) -> np.ndarray:
    """Divide out large-scale illumination, then stretch to uint8."""
    background = cv2.GaussianBlur(img, (0, 0), sigma)
    flat = img / np.maximum(background, 1e-3)
    return cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def _dedupe(points: np.ndarray, radius: float) -> Tuple[np.ndarray, np.ndarray]:
    """Merge pooled Hough candidates closer than ``radius``.

    Returns (centres, weights): each centre is the median x, y, r of its group,
    and its weight is how many candidates agreed -- a circle that survives the
    stricter accumulator thresholds is found more than once.
    """
    order = np.argsort(-points[:, 2])
    used = np.zeros(len(points), dtype=bool)
    merged, weights = [], []
    for i in order:
        if used[i]:
            continue
        near = ~used & (np.linalg.norm(points[:, :2] - points[i, :2], axis=1) < radius)
        used |= near
        merged.append(np.median(points[near], axis=0))
        weights.append(float(near.sum()))
    return np.array(merged), np.array(weights)


def _lattice_nodes(center: np.ndarray, u: np.ndarray, ij: np.ndarray) -> np.ndarray:
    """Nodes of a square lattice: column step ``u``, row step ``u`` turned 90 degrees.

    Tying the row step to the column step is what makes the grid rigid: every
    ring is the same distance from its neighbours, and the only freedoms left
    are translation, rotation, and one overall pitch.
    """
    v = np.array([-u[1], u[0]])
    return center + ij[:, 0:1] * u + ij[:, 1:2] * v


def _fit_lattice(
    points: np.ndarray,
    rows: int,
    cols: int,
    pitch: float,
    pitch_tolerance: float,
    assign_tol_frac: float,
    min_nodes: int,
    image_size: Tuple[int, int],
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Fit a rigid rows x cols square lattice to candidate circles.

    ``pitch`` is the expected node spacing in pixels; the fitted spacing must
    stay within ``pitch_tolerance`` of it. Hypotheses come from pairs of
    candidates one pitch (or one diagonal) apart, each anchored at every
    candidate/node pairing, and are scored by how many nodes have a candidate
    within ``assign_tol_frac`` of a pitch. The winner is refined by least
    squares with the rigid model, so a ring that Hough placed off-centre
    cannot drag its node off the grid.

    Returns (nodes, center, u) where nodes is (rows*cols, 4) of x, y, residual,
    measured, and u is the column step -- or None if fewer than ``min_nodes``
    nodes are supported.
    """
    if len(points) < 2:
        return None

    # Grid indices centred on the middle of the grid, so ``center`` is the
    # centre ring for odd grids and the lattice centroid otherwise.
    ij = np.array([(i - (cols - 1) / 2.0, j - (rows - 1) / 2.0) for j in range(rows) for i in range(cols)], dtype=float)
    xy = points[:, :2]
    weight = points[:, 3] if points.shape[1] > 3 else np.ones(len(points))
    lo, hi = pitch * (1 - pitch_tolerance), pitch * (1 + pitch_tolerance)

    # Candidate (rotation, pitch) pairs: neighbours along a row/column, or along a diagonal.
    steps = []
    for a in range(len(xy)):
        for b in range(len(xy)):
            if a == b:
                continue
            d = xy[b] - xy[a]
            dist = float(np.hypot(*d))
            if lo <= dist <= hi:
                steps.append(d)
            elif lo * np.sqrt(2) <= dist <= hi * np.sqrt(2):
                # Diagonal: rotate back 45 degrees and shrink to one pitch.
                c, s = np.cos(-np.pi / 4), np.sin(-np.pi / 4)
                steps.append(np.array([c * d[0] - s * d[1], s * d[0] + c * d[1]]) / np.sqrt(2))
    if not steps:
        return None

    # Fold every step into the quadrant around +x: a square lattice looks the
    # same under 90-degree turns, and this keeps columns running left to right.
    folded = []
    for d in steps:
        for _ in range(4):
            if d[0] > 0 and abs(d[1]) <= d[0]:
                break
            d = np.array([d[1], -d[0]])
        folded.append(d)
    folded = np.array(folded)
    steps = []
    for d in folded[np.argsort(folded[:, 0])]:
        if not any(np.hypot(*(d - kept)) < 0.03 * pitch for kept in steps):
            steps.append(d)

    def score(nodes: np.ndarray, tol: float) -> Tuple[float, np.ndarray, np.ndarray]:
        dist = np.linalg.norm(xy[:, None, :] - nodes[None], axis=2)
        best = dist.argmin(axis=0)
        best_d = dist[best, np.arange(len(nodes))]
        hit = best_d < tol
        closeness = np.where(hit, 1.0 - (best_d / tol) ** 2, 0.0)
        return float(hit.sum() + 0.5 * (closeness * np.log1p(weight[best])).sum() / len(nodes)), best, hit

    width, height = image_size
    image_center = np.array([width / 2.0, height / 2.0])
    best_key, best_fit = None, None
    for u in steps:
        tol = assign_tol_frac * np.hypot(*u)
        offsets = _lattice_nodes(np.zeros(2), u, ij)
        # Anchor each candidate at each node; that enumerates every placement
        # the evidence could support. Scored all at once: (placement, node, candidate).
        centers = (xy[:, None, :] - offsets[None]).reshape(-1, 2)
        nodes = centers[:, None, :] + offsets[None]
        dist = np.linalg.norm(nodes[:, :, None, :] - xy[None, None], axis=3)
        best = dist.argmin(axis=2)
        best_d = np.take_along_axis(dist, best[..., None], axis=2)[..., 0]
        hit = best_d < tol
        closeness = np.where(hit, 1.0 - (best_d / tol) ** 2, 0.0)
        scores = hit.sum(axis=1) + 0.5 * (closeness * np.log1p(weight[best])).sum(axis=1) / len(ij)
        # Ties (e.g. only two rows visible) go to the placement that keeps
        # more of the grid in frame, then to the one nearest the image centre.
        inside = ((nodes[..., 0] >= 0) & (nodes[..., 0] < width) & (nodes[..., 1] >= 0) & (nodes[..., 1] < height)).sum(
            axis=1
        )
        off_center = np.linalg.norm(centers - image_center, axis=1)
        k = np.lexsort((off_center, -inside, -np.round(scores, 6)))[0]
        key = (round(float(scores[k]), 6), int(inside[k]), -float(off_center[k]))
        if best_key is None or key > best_key:
            best_key, best_fit = key, (centers[k].copy(), u.copy())

    center, u = best_fit
    design_ij = ij
    for _ in range(20):
        tol = assign_tol_frac * np.hypot(*u)
        nodes = _lattice_nodes(center, u, design_ij)
        _, best, hit = score(nodes, tol)
        if hit.sum() < max(min_nodes, 2):
            return None
        obs = xy[best[hit]]
        obs_ij = design_ij[hit]
        # Rigid (similarity) least squares: x = cx + i*a - j*b, y = cy + i*b + j*a.
        n = len(obs)
        A = np.zeros((2 * n, 4))
        A[0::2] = np.column_stack([np.ones(n), np.zeros(n), obs_ij[:, 0], -obs_ij[:, 1]])
        A[1::2] = np.column_stack([np.zeros(n), np.ones(n), obs_ij[:, 1], obs_ij[:, 0]])
        rhs = obs.reshape(-1)
        sol, *_ = np.linalg.lstsq(A, rhs, rcond=None)
        # One trimming pass: an observation far worse than the rest is a
        # mis-centred Hough circle, not information about the grid.
        resid = np.linalg.norm((A @ sol - rhs).reshape(-1, 2), axis=1)
        if n > max(min_nodes, 3):
            cut = max(3.0 * np.median(resid), 0.05 * np.hypot(*u))
            good = resid <= cut
            if good.sum() >= max(min_nodes, 3) and not good.all():
                rows_mask = np.repeat(good, 2)
                sol, *_ = np.linalg.lstsq(A[rows_mask], rhs[rows_mask], rcond=None)
        new_center, new_u = sol[:2], sol[2:]
        converged = np.allclose(new_center, center, atol=1e-3) and np.allclose(new_u, u, atol=1e-3)
        center, u = new_center, new_u
        if converged:
            break

    if not lo <= np.hypot(*u) <= hi:
        return None

    tol = assign_tol_frac * np.hypot(*u)
    nodes = _lattice_nodes(center, u, ij)
    _, best, hit = score(nodes, tol)
    if hit.sum() < min_nodes:
        return None
    residual = np.linalg.norm(xy[best] - nodes, axis=1)
    out = np.column_stack([nodes, np.where(hit, residual, np.nan), hit.astype(float)])
    return out, center, u


class RingGridAnalyzer(BaseAnalyzer):
    """Find a grid_rows x grid_cols lattice of rings per field of view."""

    name = "ring_grid"
    #: Describe the sample; the rest are detector tuning knobs.
    primary = ("ring_diameter_um", "grid_pitch_um", "grid_rows", "grid_cols")
    defaults: Dict[str, Any] = {
        # Physical priors -- normally the only things a user needs to change.
        "ring_diameter_um": 320.0,
        "grid_pitch_um": 1000.0,
        "grid_rows": 3,
        "grid_cols": 3,
        # Detection tuning.
        "radius_tolerance": 0.45,
        "hough_param1": 100.0,
        "hough_param2_steps": (34, 30, 26, 22),
        "hough_dp": 1.2,
        "flatfield_sigma_factor": 1.2,
        # Lattice fitting.
        "fit_lattice": True,
        # A circle counts towards a node if it is within this fraction of a
        # pitch of it. The nodes themselves always sit on the rigid lattice.
        "assign_tolerance": 0.2,
        # How far the fitted pitch may stray from grid_pitch_um (fraction).
        "pitch_tolerance": 0.15,
        # Rings that must be found on the lattice before the fit is trusted.
        "min_lattice_nodes": 4,
        "keep_inferred": True,
        "min_candidates": 4,
    }

    def analyze_fov(self, image: np.ndarray, fov: FovRecord) -> List[PixelDetection]:
        p = self.params
        if fov.pixel_size_um <= 0:
            raise ValueError(f"FovRecord {fov.key} has non-positive pixel_size_um")
        if p["ring_diameter_um"] <= 0:
            raise ValueError("ring_diameter_um must be positive")

        gray = _to_gray_f32(image)
        native_h, native_w = gray.shape[:2]

        # Rescale so a ring is _WORK_RADIUS_PX across, fixing the Hough params.
        native_radius_px = (p["ring_diameter_um"] / 2.0) / fov.pixel_size_um
        scale = _WORK_RADIUS_PX / native_radius_px
        work_w = max(64, int(round(native_w * scale)))
        work_h = max(64, int(round(native_h * scale)))
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        work = cv2.resize(gray, (work_w, work_h), interpolation=interp)

        blurred = cv2.medianBlur(_flat_field(work, _WORK_RADIUS_PX * p["flatfield_sigma_factor"]), 5)

        tol = p["radius_tolerance"]
        min_dist = max(8.0, p["grid_pitch_um"] / fov.pixel_size_um * scale * 0.5)
        candidates = []
        for param2 in p["hough_param2_steps"]:
            found = cv2.HoughCircles(
                blurred,
                cv2.HOUGH_GRADIENT,
                dp=p["hough_dp"],
                minDist=min_dist,
                param1=p["hough_param1"],
                param2=param2,
                minRadius=int(_WORK_RADIUS_PX * (1 - tol)),
                maxRadius=int(_WORK_RADIUS_PX * (1 + tol)),
            )
            if found is not None:
                candidates.append(found[0])

        if not candidates:
            return []
        points = np.vstack(candidates)

        rows, cols = int(p["grid_rows"]), int(p["grid_cols"])
        detections: List[PixelDetection] = []

        if p["fit_lattice"] and rows * cols > 1:
            # The grid is rigid, so a lattice that cannot be fitted means no
            # answer rather than a scatter of unconstrained circles.
            if len(points) < p["min_candidates"]:
                return []
            merged, weights = _dedupe(points, _WORK_RADIUS_PX * 0.5)
            pitch_px = p["grid_pitch_um"] / fov.pixel_size_um * scale
            fit = _fit_lattice(
                np.column_stack([merged, weights]),
                rows,
                cols,
                pitch_px,
                p["pitch_tolerance"],
                p["assign_tolerance"],
                int(p["min_lattice_nodes"]),
                (work_w, work_h),
            )
            if fit is None:
                return []
            nodes, _center, u = fit
            pitch_um = float(np.linalg.norm(u)) / scale * fov.pixel_size_um
            angle_deg = float(np.degrees(np.arctan2(u[1], u[0])))
            # Every ring is the same size: one radius for the whole grid, the
            # median of the circles that supported a node.
            supporting = [merged[np.argmin(np.linalg.norm(merged[:, :2] - n[:2], axis=1))] for n in nodes if n[3]]
            radius = float(np.median([c[2] for c in supporting])) if supporting else _WORK_RADIUS_PX
            for idx, (cx, cy, residual, measured) in enumerate(nodes):
                is_measured = bool(measured)
                if not is_measured and not p["keep_inferred"]:
                    continue
                metrics = {
                    "grid_row": idx // cols,
                    "grid_col": idx % cols,
                    "lattice_pitch_um": pitch_um,
                    "lattice_angle_deg": angle_deg,
                }
                if is_measured:
                    # How far the ring's own circle sat from its grid node.
                    metrics["node_residual_um"] = float(residual) / scale * fov.pixel_size_um
                detections.append(
                    PixelDetection(
                        px=float(cx) / scale,
                        py=float(cy) / scale,
                        radius_px=radius / scale,
                        score=1.0 if is_measured else 0.5,
                        measured=is_measured,
                        label=f"r{idx // cols}c{idx % cols}",
                        metrics=metrics,
                    )
                )
            return detections

        # No usable lattice: fall back to pooled candidates, deduplicated.
        used = np.zeros(len(points), dtype=bool)
        for i, (cx, cy, _r) in enumerate(points):
            if used[i]:
                continue
            near = np.linalg.norm(points[:, :2] - np.array([cx, cy]), axis=1) < min_dist
            used |= near
            group = points[near]
            detections.append(
                PixelDetection(
                    px=float(np.median(group[:, 0])) / scale,
                    py=float(np.median(group[:, 1])) / scale,
                    radius_px=float(np.median(group[:, 2])) / scale,
                    score=float(min(1.0, near.sum() / max(len(p["hough_param2_steps"]), 1))),
                    measured=True,
                    label=f"c{len(detections)}",
                    metrics={"lattice": False},
                )
            )
        return detections


register(RingGridAnalyzer)
