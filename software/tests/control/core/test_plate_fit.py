"""The fit module's contract, from the design doc's test plan."""

import math

import numpy as np
import pytest

import control._def as _def
from control.core.plate_fit import fit_plate_placement, PlateFitError

RNG = np.random.default_rng(20260815)


def nominal_ring(format_key):
    """The canonical reachable 4-corner ring, A1-relative plate coordinates."""
    rings = {
        "6 well plate": [(0, 0), (0, 2), (1, 0), (1, 2)],
        "12 well plate": [(0, 0), (0, 3), (2, 0), (2, 3)],
        "24 well plate": [(0, 0), (0, 4), (2, 0), (2, 4)],
        "96 well plate": [(0, 0), (0, 11), (7, 0), (7, 11)],
        "384 well plate": [(1, 1), (1, 22), (14, 1), (14, 22)],
        "1536 well plate": [(0, 0), (0, 46), (30, 0), (30, 46)],
    }
    s = _def.get_wellplate_settings(format_key)
    pitch = s["well_spacing_mm"]
    return [(c * pitch, r * pitch) for r, c in rings[format_key]], s


def apply_pose(points, theta_deg, scale, tx, ty):
    t = math.radians(theta_deg)
    rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    return [(scale * (rot @ np.array(p)) + np.array([tx, ty])).tolist() for p in points]


def all_wells_query(s):
    pitch = s["well_spacing_mm"]
    out = []
    for r in range(s["rows"]):
        for c in range(s["cols"]):
            out.append((f"r{r}c{c}", c * pitch, r * pitch))
    return out


def test_round_trip_recovers_pose_including_sign():
    nominal, s = nominal_ring("96 well plate")
    measured = apply_pose(nominal, theta_deg=0.21, scale=1.0, tx=11.482, ty=10.913)

    fit = fit_plate_placement(nominal, measured, well_size_mm=s["well_size_mm"], pitch_x_mm=9.0, pitch_y_mm=9.0)

    assert fit.rotation_deg == pytest.approx(0.21, abs=1e-9)  # sign included
    assert fit.a1_x_mm == pytest.approx(11.482, abs=1e-9)
    assert fit.a1_y_mm == pytest.approx(10.913, abs=1e-9)
    assert fit.fitted_scale == pytest.approx(1.0, abs=1e-12)
    assert not fit.gates


def test_negative_rotation_recovers_negative():
    nominal, s = nominal_ring("96 well plate")
    measured = apply_pose(nominal, theta_deg=-0.34, scale=1.0, tx=11.31, ty=10.75)
    fit = fit_plate_placement(nominal, measured, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)
    assert fit.rotation_deg == pytest.approx(-0.34, abs=1e-9)


def test_cross_check_against_skimage():
    """Pins our convention against an independent SVD-based implementation."""
    from skimage.transform import SimilarityTransform

    nominal, s = nominal_ring("1536 well plate")
    for _ in range(25):
        theta = float(RNG.uniform(-1.5, 1.5))
        tx, ty = float(RNG.uniform(5, 20)), float(RNG.uniform(5, 20))
        scale = float(RNG.uniform(0.999, 1.001))
        measured = apply_pose(nominal, theta, scale, tx, ty)
        noisy = [(x + RNG.normal(0, 0.02), y + RNG.normal(0, 0.02)) for x, y in measured]

        fit = fit_plate_placement(nominal, noisy, well_size_mm=1.53, pitch_x_mm=2.25, pitch_y_mm=2.25)

        sk = SimilarityTransform()
        assert sk.estimate(np.asarray(nominal), np.asarray(noisy))
        assert fit.rotation_deg == pytest.approx(math.degrees(sk.rotation), abs=0.011)  # our 0.01 quantum
        assert fit.fitted_scale == pytest.approx(sk.scale, abs=1e-9)


def test_t_applied_not_t_sim():
    """With true scale != 1, the persisted origin must be the s=1 least-squares
    translation - no whole-plate (s-1)*R*p_bar offset."""
    nominal, s = nominal_ring("1536 well plate")
    true_scale = 1.0006
    measured = apply_pose(nominal, theta_deg=0.0, scale=true_scale, tx=11.01, ty=7.87)

    fit = fit_plate_placement(nominal, measured, well_size_mm=1.53, pitch_x_mm=2.25, pitch_y_mm=2.25)

    p = np.asarray(nominal)
    q = np.asarray(measured)
    t_applied = q.mean(axis=0) - p.mean(axis=0)  # rigid LS translation (theta = 0)
    assert fit.a1_x_mm == pytest.approx(t_applied[0], abs=1e-12)
    assert fit.a1_y_mm == pytest.approx(t_applied[1], abs=1e-12)
    # and the naive similarity translation would differ measurably:
    t_sim = q.mean(axis=0) - true_scale * p.mean(axis=0)
    assert abs(t_sim[0] - t_applied[0]) > 0.01  # tens of um at this lever arm
    assert fit.fitted_scale == pytest.approx(true_scale, abs=1e-9)


def test_mirrored_input_rejected():
    nominal, s = nominal_ring("96 well plate")
    mirrored = [(x, -y) for x, y in apply_pose(nominal, 0.1, 1.0, 11.31, 10.75)]
    fit = fit_plate_placement(nominal, mirrored, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)
    assert any(g.code == "mirrored" and g.level == "reject" for g in fit.gates)


@pytest.mark.parametrize(
    "format_", ["6 well plate", "12 well plate", "24 well plate", "96 well plate", "384 well plate", "1536 well plate"]
)
def test_one_pitch_misclick_rejected(format_):
    nominal, s = nominal_ring(format_)
    measured = apply_pose(nominal, theta_deg=0.1, scale=1.0, tx=11.0, ty=8.0)
    # the operator touched the well one pitch to the right of the one they named
    measured[3] = (measured[3][0] + s["well_spacing_mm"], measured[3][1])

    fit = fit_plate_placement(
        nominal,
        measured,
        well_size_mm=s["well_size_mm"],
        pitch_x_mm=s["well_spacing_mm"],
        pitch_y_mm=s["well_spacing_mm"],
        query_wells=all_wells_query(s),
    )

    assert fit.rejected, format_


@pytest.mark.parametrize(
    "format_", ["6 well plate", "12 well plate", "24 well plate", "96 well plate", "384 well plate", "1536 well plate"]
)
def test_canonical_ring_passes_all_gates(format_):
    """Gate reachability: a clean 4-corner calibration with realistic click
    noise must pass on every format - a gate nobody can satisfy is a bug."""
    nominal, s = nominal_ring(format_)
    measured = apply_pose(nominal, theta_deg=0.21, scale=1.0, tx=11.0, ty=8.0)
    noisy = [(x + RNG.normal(0, 0.02), y + RNG.normal(0, 0.02)) for x, y in measured]

    fit = fit_plate_placement(
        nominal,
        noisy,
        well_size_mm=s["well_size_mm"],
        pitch_x_mm=s["well_spacing_mm"],
        pitch_y_mm=s["well_spacing_mm"],
        query_wells=all_wells_query(s),
    )

    assert not fit.rejected, (format_, fit.gates)
    assert not fit.needs_confirmation, (format_, fit.gates)


def test_clustered_wells_rejected_by_spread_gate():
    s = _def.get_wellplate_settings("1536 well plate")
    pitch = s["well_spacing_mm"]
    cluster = [(0, 0), (pitch, 0), (0, pitch), (pitch, pitch)]  # 2x2 corner block
    measured = apply_pose(cluster, 0.2, 1.0, 11.0, 8.0)
    fit = fit_plate_placement(
        cluster,
        [(x + RNG.normal(0, 0.02), y + RNG.normal(0, 0.02)) for x, y in measured],
        well_size_mm=1.53,
        pitch_x_mm=pitch,
        pitch_y_mm=pitch,
        query_wells=all_wells_query(s),
    )
    assert any(g.code == "spread" for g in fit.gates)


def test_wrong_plate_pitch_warns_on_either_axis():
    nominal, s = nominal_ring("24 well plate")
    # plate on the stage is NEST (18.0 mm), catalog says Corning (19.3 mm):
    scale_wrong = 18.0 / 19.3
    measured = apply_pose(nominal, 0.0, scale_wrong, 24.45, 22.07)
    fit = fit_plate_placement(nominal, measured, well_size_mm=15.54, pitch_x_mm=19.3, pitch_y_mm=19.3)
    assert any(g.code.startswith("pitch_") and g.level == "warn" for g in fit.gates)


def test_large_rotation_warns_not_rejects():
    nominal, s = nominal_ring("96 well plate")
    measured = apply_pose(nominal, theta_deg=1.8, scale=1.0, tx=11.0, ty=8.0)
    fit = fit_plate_placement(nominal, measured, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)
    assert any(g.code == "rotation_large" and g.level == "warn" for g in fit.gates)
    assert not fit.rejected


def test_rounding_happens_before_a1():
    """The stored a1 must be derived from the ROUNDED angle, so the two stored
    numbers are consistent with each other."""
    nominal, s = nominal_ring("96 well plate")
    theta_unround = 0.2149  # rounds to 0.21
    measured = apply_pose(nominal, theta_unround, 1.0, 11.482, 10.913)
    fit = fit_plate_placement(nominal, measured, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)

    assert fit.rotation_deg == pytest.approx(0.21, abs=1e-12)
    # a1 recomputed with the rounded angle:
    t = math.radians(fit.rotation_deg)
    rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    p = np.asarray(nominal)
    q = np.asarray(measured)
    expected = q.mean(axis=0) - rot @ p.mean(axis=0)
    assert fit.a1_x_mm == pytest.approx(expected[0], abs=1e-12)
    assert fit.a1_y_mm == pytest.approx(expected[1], abs=1e-12)


def test_sigma_hat_is_unbiased_monte_carlo():
    """E[SSR_sim / (2N-4)] ~= sigma^2 - catches the /2N mistake and the
    rigid-residual contamination, across true scales."""
    nominal, s = nominal_ring("1536 well plate")
    sigma = 0.02  # 20 um
    for true_scale in (1.0, 1.0006):
        estimates = []
        for _ in range(400):
            measured = apply_pose(nominal, 0.2, true_scale, 11.0, 8.0)
            noisy = [(x + RNG.normal(0, sigma), y + RNG.normal(0, sigma)) for x, y in measured]
            fit = fit_plate_placement(nominal, noisy, well_size_mm=1.53, pitch_x_mm=2.25, pitch_y_mm=2.25)
            estimates.append((fit.sigma_hat_um / 1000.0) ** 2)
        assert np.mean(estimates) == pytest.approx(sigma**2, rel=0.15), true_scale


def test_two_points_fit_but_three_needed_for_qc():
    nominal = [(0.0, 0.0), (99.0, 63.0)]
    measured = apply_pose(nominal, 0.3, 1.0, 11.0, 8.0)
    fit = fit_plate_placement(nominal, measured, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)
    assert fit.rotation_deg == pytest.approx(0.3, abs=0.011)
    assert fit.fitted_axis_angle_deg == 90.0  # no shear info fabricated from N=2


def test_degenerate_inputs_raise():
    with pytest.raises(PlateFitError):
        fit_plate_placement([(0.0, 0.0)], [(1.0, 1.0)], well_size_mm=6.0, pitch_x_mm=9.0, pitch_y_mm=9.0)
    with pytest.raises(PlateFitError):
        fit_plate_placement(
            [(1.0, 1.0), (1.0, 1.0)], [(0.0, 0.0), (0.1, 0.1)], well_size_mm=6.0, pitch_x_mm=9.0, pitch_y_mm=9.0
        )


def test_collinear_points_are_fine_for_rigid():
    """Three wells all in row A: collinear is genuinely OK for theta (only an
    affine fit would degenerate), per the design's conditioning analysis."""
    nominal = [(0.0, 0.0), (49.5, 0.0), (99.0, 0.0)]
    measured = apply_pose(nominal, 0.5, 1.0, 11.0, 8.0)
    fit = fit_plate_placement(nominal, measured, well_size_mm=6.21, pitch_x_mm=9.0, pitch_y_mm=9.0)
    assert fit.rotation_deg == pytest.approx(0.5, abs=0.011)
    assert fit.fitted_axis_angle_deg == 90.0  # rank-deficient affine: not fabricated
