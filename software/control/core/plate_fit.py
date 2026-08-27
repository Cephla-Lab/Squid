"""Fit a plate's measured placement from reference-well touches.

Pure Python + numpy: no Qt, no hardware, no file IO. The design contract
(AI-docs/Squid/in-progress/2026-07-28-wellplate-rotation-calibration-design.md,
Step 2): fit a 4-parameter SIMILARITY in closed form, APPLY only the
3-parameter rigid part, and surface the discarded scale as a QC signal -
ANSI/SLAS guarantees the well grid is orthogonal at nominal pitch, so fitted
scale/shear is a machine property or a mis-click, and absorbing it into a
plate record would mis-correct the next plate.

Closed form (no SVD, structurally incapable of returning a reflection):

    p~ = p - p_bar ;  q~ = q - q_bar
    num  = sum(p~x*q~y - p~y*q~x)        # cross products
    den  = sum(p~x*q~x + p~y*q~y)        # dot products
    S_pp = sum(|p~|^2)                   # squared lever arm

    theta     = atan2(num, den)          # rounded to 0.01 deg HERE, before t
    s         = hypot(num, den) / S_pp   # QC only, never applied
    t_applied = q_bar - R(theta) @ p_bar # the LS translation for s = 1

``p`` must be A1-RELATIVE plate coordinates (col * pitch_x, row * pitch_y), so
``t_applied`` IS the measured stage position of A1. theta and s are invariant
to the choice of origin for p; only the translation leg depends on it.

Quality numbers are never persisted - they are recomputed from the raw points
(the only stored truth) whenever needed.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

# The rounding quantum for the stored angle. ANSI/SLAS true-position tolerance
# permits ~0.27 deg of apparent skew on 1536 from moulding scatter alone;
# digits beyond 0.01 deg are noise.
ROTATION_QUANTUM_DEG = 0.01

# Gate thresholds, from the design doc (each derived, not invented):
# - G is sigma-free and computable from the clicked well indices alone; the
#   canonical reachable corner rings score 0.87-1.07 on all six formats.
SPREAD_GATE_G = 1.5
# - predicted error above half a well radius means a reference well is almost
#   certainly not the well the user named (catches 24/24 one-pitch mis-clicks
#   at N=3 in the design's Monte Carlo). In um per mm of well size: 250.
MISCLICK_GATE_UM_PER_MM_WELL = 250.0
# - fitted pitch further than this from nominal usually means the wrong plate.
PITCH_WARN_FRACTION = 0.05
# - seating cannot legitimately exceed this on a conforming nest; larger values
#   need explicit confirmation (custom holders, debris).
ROTATION_WARN_DEG = 1.0
# p95 over the noise distribution is ~1.75x the RMS at N=3-4 (empirical factor;
# the pure-Rayleigh floor is 1.73).
P95_OVER_RMS = 1.75


class PlateFitError(ValueError):
    """The input cannot be fit at all (too few points, zero lever arm)."""


@dataclass(frozen=True)
class GateFinding:
    level: str  # "reject" | "warn"
    code: str
    message: str


@dataclass(frozen=True)
class PlateFitResult:
    rotation_deg: float  # rounded; + = CCW in the stage XY math frame
    a1_x_mm: float  # measured stage position of A1 (t_applied)
    a1_y_mm: float
    # --- QC / diagnostics, computed fresh, never persisted ---
    fitted_scale: float  # similarity scale; 1.0 == nominal pitch
    fitted_pitch_x_mm: float  # from the 6-param QC fit (both axes checked -
    fitted_pitch_y_mm: float  # checking only X is pymmcore-widgets' bug)
    fitted_axis_angle_deg: float  # row-vs-column vector angle; 90 == no shear
    sigma_hat_um: float  # click noise, from the SIMILARITY residual (k=4)
    residuals_um: Tuple[float, ...]  # per reference point, applied (rigid) fit
    predicted_rms_um: float  # at the worst query well (incl. discarded-scale bias)
    predicted_p95_um: float
    worst_well: str
    n_points: int
    gates: Tuple[GateFinding, ...] = field(default_factory=tuple)

    @property
    def rejected(self) -> bool:
        return any(g.level == "reject" for g in self.gates)

    @property
    def needs_confirmation(self) -> bool:
        return any(g.level == "warn" for g in self.gates)


def _as_arrays(points: Sequence[Tuple[float, float]]) -> np.ndarray:
    a = np.asarray(points, dtype=float)
    if a.ndim != 2 or a.shape[1] != 2:
        raise PlateFitError("points must be (x, y) pairs")
    return a


def fit_plate_placement(
    nominal_mm: Sequence[Tuple[float, float]],
    measured_mm: Sequence[Tuple[float, float]],
    *,
    well_size_mm: float,
    pitch_x_mm: float,
    pitch_y_mm: float,
    query_wells: Optional[Sequence[Tuple[str, float, float]]] = None,
) -> PlateFitResult:
    """Fit placement from paired reference points.

    Args:
        nominal_mm: A1-relative plate coordinates (col*pitch_x, row*pitch_y).
        measured_mm: stage coordinates, same order. Same feature touched on
            every well - a constant feature offset cancels in the centering
            and lands in the discarded translation component (see design:
            one-corner-per-well method), so ONLY pass center-consistent or
            same-feature touches.
        well_size_mm: nominal well size, for the mis-click gate.
        pitch_x_mm / pitch_y_mm: nominal pitches, for the pitch QC gate.
        query_wells: (well_id, x, y) A1-relative points to evaluate the
            prediction at (typically every well of the plate). Defaults to the
            reference points themselves.
    """
    p = _as_arrays(nominal_mm)
    q = _as_arrays(measured_mm)
    if p.shape != q.shape:
        raise PlateFitError("nominal and measured point counts differ")
    n = p.shape[0]
    if n < 2:
        raise PlateFitError("at least 2 reference wells are required")

    p_bar = p.mean(axis=0)
    q_bar = q.mean(axis=0)
    pt = p - p_bar
    qt = q - q_bar

    num = float(np.sum(pt[:, 0] * qt[:, 1] - pt[:, 1] * qt[:, 0]))
    den = float(np.sum(pt[:, 0] * qt[:, 0] + pt[:, 1] * qt[:, 1]))
    s_pp = float(np.sum(pt**2))
    if s_pp == 0.0:
        raise PlateFitError("reference wells are all at the same nominal position")

    theta = math.atan2(num, den)
    scale = math.hypot(num, den) / s_pp

    # Round FIRST, then derive everything applied from the rounded angle - so
    # the stored angle and the stored a1 are consistent with each other.
    rotation_deg = round(math.degrees(theta) / ROTATION_QUANTUM_DEG) * ROTATION_QUANTUM_DEG
    theta_r = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta_r), math.sin(theta_r)
    rot = np.array([[cos_t, -sin_t], [sin_t, cos_t]])

    # t for the transform we APPLY (s = 1). Using the similarity translation
    # with a rigid linear part would offset the whole plate by (s-1)*R*p_bar.
    t_applied = q_bar - rot @ p_bar

    # --- residuals ---
    applied = (rot @ p.T).T + t_applied
    residuals = np.linalg.norm(q - applied, axis=1)

    # sigma-hat from the SIMILARITY residual (k = 4): the only estimator of the
    # click noise unpolluted by the scale the hybrid discards.
    sim = (scale * (rot @ pt.T)).T + q_bar
    ssr_sim = float(np.sum((q - sim) ** 2))
    dof = 2 * n - 4
    sigma_hat_mm = math.sqrt(ssr_sim / dof) if dof > 0 else float("nan")

    # --- 6-param affine QC fit (never applied): per-axis pitch + axis angle ---
    a_mat = np.zeros((2 * n, 6))
    a_mat[0::2, 0] = pt[:, 0]
    a_mat[0::2, 1] = pt[:, 1]
    a_mat[0::2, 4] = 1.0
    a_mat[1::2, 2] = pt[:, 0]
    a_mat[1::2, 3] = pt[:, 1]
    a_mat[1::2, 5] = 1.0
    b_vec = qt.reshape(-1)
    if n >= 3 and np.linalg.matrix_rank(a_mat) == 6:
        coeffs, *_ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
        col_vec = np.array([coeffs[0], coeffs[2]])  # image of plate +x
        row_vec = np.array([coeffs[1], coeffs[3]])  # image of plate +y
        fitted_pitch_x = float(np.linalg.norm(col_vec)) * pitch_x_mm
        fitted_pitch_y = float(np.linalg.norm(row_vec)) * pitch_y_mm
        cross = float(col_vec[0] * row_vec[1] - col_vec[1] * row_vec[0])
        dot = float(col_vec @ row_vec)
        fitted_axis_angle = math.degrees(math.atan2(abs(cross), dot))
    else:
        # Not enough independent data for shear/per-axis pitch: report the
        # similarity's isotropic values rather than fabricating.
        fitted_pitch_x = scale * pitch_x_mm
        fitted_pitch_y = scale * pitch_y_mm
        fitted_axis_angle = 90.0

    # --- prediction at the query wells ---
    if query_wells is None:
        query = [(f"ref{i}", float(x), float(y)) for i, (x, y) in enumerate(p)]
    else:
        query = [(w, float(x), float(y)) for w, x, y in query_wells]
    lever_sqs = [(x - p_bar[0]) ** 2 + (y - p_bar[1]) ** 2 for _, x, y in query]
    worst_rms_mm = 0.0
    worst_well = query[0][0] if query else ""
    for (well_id, _x, _y), lever_sq in zip(query, lever_sqs):
        var_noise = 2 * sigma_hat_mm**2 / n + sigma_hat_mm**2 * lever_sq / s_pp
        bias_scale = ((scale - 1.0) ** 2) * lever_sq  # the term the hybrid discards
        rms = math.sqrt(var_noise + bias_scale)
        if rms > worst_rms_mm:
            worst_rms_mm = rms
            worst_well = well_id

    # --- gates ---
    gates: List[GateFinding] = []

    # sqrt is monotonic: max of sqrts == sqrt at the max lever arm
    g_factor = math.sqrt(2 / n + max(lever_sqs) / s_pp)
    if g_factor > SPREAD_GATE_G:
        gates.append(
            GateFinding(
                "reject",
                "spread",
                f"Reference wells are too close together (G = {g_factor:.2f}) - expected error "
                f"would be {worst_rms_mm * 1000:.0f} um at {worst_well}. Use wells nearer the plate corners.",
            )
        )

    misclick_limit_mm = MISCLICK_GATE_UM_PER_MM_WELL * well_size_mm / 1000.0
    if well_size_mm > 0 and worst_rms_mm > misclick_limit_mm:
        gates.append(
            GateFinding(
                "reject",
                "misclick",
                f"Expected error {worst_rms_mm * 1000:.0f} um at {worst_well} exceeds half a well radius "
                f"({misclick_limit_mm * 1000:.0f} um). Check that each reference well is the well you named - "
                f"a one-well mix-up looks exactly like this.",
            )
        )

    # Mirror check on the MEASURED POINTS, not the fitted matrix: the fitted
    # similarity's determinant is s^2 >= 0 identically and can never fire.
    if n >= 3:
        pa = _signed_area(p[0], p[1], p[2])
        qa = _signed_area(q[0], q[1], q[2])
        if pa != 0.0 and qa != 0.0 and (pa > 0) != (qa > 0):
            gates.append(
                GateFinding(
                    "reject",
                    "mirrored",
                    "These points don't match the well layout - they look mirrored or entered in the wrong order.",
                )
            )

    for axis, fitted, nominal in (("x", fitted_pitch_x, pitch_x_mm), ("y", fitted_pitch_y, pitch_y_mm)):
        if nominal > 0 and abs(fitted - nominal) > PITCH_WARN_FRACTION * nominal:
            gates.append(
                GateFinding(
                    "warn",
                    f"pitch_{axis}",
                    f"Measured well spacing ({axis}) is {fitted:.3f} mm; nominal is {nominal:.3f} mm. "
                    f"Is the right plate loaded?",
                )
            )

    if abs(rotation_deg) > ROTATION_WARN_DEG:
        gates.append(
            GateFinding(
                "warn",
                "rotation_large",
                f"Measured rotation {rotation_deg:.2f} deg is larger than any normal seating allows. "
                f"Confirm the value, or re-check the reference wells.",
            )
        )

    return PlateFitResult(
        rotation_deg=rotation_deg,
        a1_x_mm=float(t_applied[0]),
        a1_y_mm=float(t_applied[1]),
        fitted_scale=scale,
        fitted_pitch_x_mm=fitted_pitch_x,
        fitted_pitch_y_mm=fitted_pitch_y,
        fitted_axis_angle_deg=fitted_axis_angle,
        sigma_hat_um=sigma_hat_mm * 1000.0,
        residuals_um=tuple(float(r) * 1000.0 for r in residuals),
        predicted_rms_um=worst_rms_mm * 1000.0,
        predicted_p95_um=worst_rms_mm * 1000.0 * P95_OVER_RMS,
        worst_well=worst_well,
        n_points=n,
        gates=tuple(gates),
    )


def circumcenter(p1, p2, p3) -> Tuple[float, float, float]:
    """Center + radius of the circle through three points.

    The circumcenter cancels the touch-radius error the same way the corner
    midpoint cancels fillet displacement. Collinear points have no circle.
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        raise PlateFitError("the three points are (nearly) collinear - they do not define a circle")
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _signed_area(a, b, c) -> float:
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
