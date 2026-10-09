"""The fit behind a calibration (design §5.2): monotone, spike-proof, threshold-aware, and never past a rollover."""

from dataclasses import replace

import numpy as np
import pytest

from squid.intensity_calibration import (
    intensity_tooltip,
    VERIFY_SETPOINTS,
    CalibrationError,
    build_anchors,
    find_rollover_peak,
    fit_curve,
    isotonic_fit,
    running_median,
    x_for_power_fraction,
)
from squid.power_meter import simulated_laser_mw, simulated_led_mw
from tests.squid.calibration_fixtures import bench_488_led_mw, make_calibration

DAC = np.linspace(0.0, 100.0, 201)
FACTOR = 0.6
X = DAC / 100.0 * FACTOR


def _errors(fit, truth):
    """Relative error (%) at each verification setpoint, measured against the true source curve."""
    errors = {}
    for r in VERIFY_SETPOINTS:
        x = x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, r / 100.0)
        expected = r / 100.0 * fit.p_max_mw
        errors[r] = (float(truth(x)) - expected) / expected * 100.0
    return errors


def test_isotonic_fit_pools_violators_and_keeps_monotone_data():
    assert isotonic_fit([1.0, 3.0, 2.0, 4.0]).tolist() == [1.0, 2.5, 2.5, 4.0]
    monotone = np.cumsum(np.random.default_rng(0).uniform(0, 1, 50))
    assert np.array_equal(isotonic_fit(monotone), monotone)


def test_running_median_is_identity_on_monotone_data_and_removes_spikes():
    monotone = np.cumsum(np.random.default_rng(1).uniform(0, 1, 50))
    assert np.array_equal(running_median(monotone), monotone)
    assert running_median([1.0, 2.0, 10.0, 4.0, 5.0]).tolist() == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_build_anchors_starts_at_the_end_of_the_dead_zone():
    powers, xs = build_anchors(np.arange(7.0), np.array([0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 3.0]), zero_level=0.5)
    assert powers.tolist() == [0.0, 1.0, 2.0, 3.0]
    assert xs.tolist() == [2.0, 3.0, 4.0, 6.0]  # a run of equal power is anchored at its lowest drive


def test_rollover_found_for_a_falling_top_only():
    assert find_rollover_peak(simulated_led_mw(X)) is not None
    assert find_rollover_peak(simulated_laser_mw(X)) is None
    flat_top = np.minimum(300.0 * DAC / 70.0, 300.0)
    assert find_rollover_peak(flat_top) is None
    dip = 300.0 * DAC / 100.0
    dip[(DAC >= 45) & (DAC <= 55)] *= 0.2
    assert find_rollover_peak(dip) is None


def test_noisy_monotone_curves_are_never_flagged_as_rollover():
    rng = np.random.default_rng(2)
    for _ in range(300):
        threshold = rng.uniform(0, 50)
        curve = np.where(DAC < threshold, 0.0, 300.0 * (DAC / 100.0) ** rng.uniform(0.5, 1.8))
        noisy = curve * (1 + rng.uniform(0, 0.01) * rng.standard_normal(DAC.size))
        assert find_rollover_peak(noisy) is None


def test_threshold_laser_fit_is_linear_including_the_low_end():
    rng = np.random.default_rng(0)
    power = simulated_laser_mw(X) * (1 + 0.003 * rng.standard_normal(X.size)) + 0.002 * rng.standard_normal(X.size)
    fit = fit_curve(DAC, X, power, sigma_dark=0.0002)
    errors = _errors(fit, simulated_laser_mw)
    assert fit.rollover is None
    assert max(abs(e) for r, e in errors.items() if r >= 10) < 1.0
    assert max(abs(e) for r, e in errors.items() if r < 10) < 2.0  # the dead-zone anchor at work
    assert x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, 0.01) > 0.18  # above threshold


def test_rolling_over_led_is_cut_at_its_peak():
    rng = np.random.default_rng(0)
    power = simulated_led_mw(X) * (1 + 0.003 * rng.standard_normal(X.size))
    fit = fit_curve(DAC, X, power)
    assert fit.rollover is not None and "calibrated range ends at DAC" in fit.rollover
    assert DAC[fit.top_index] < 86.0
    assert np.all(np.isnan(fit.fitted_mw[fit.top_index + 1 :]))
    assert x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, 1.0) <= X[fit.top_index]
    assert max(abs(e) for r, e in _errors(fit, simulated_led_mw).items() if r >= 10) < 1.0


def test_a_spike_at_the_rollover_peak_does_not_set_p_max():
    # smoothing before the cut: the peak is not an end point of the median window (external review, finding 7)
    power = np.where(X <= 0.48, X / 0.48, 1.0 - 0.5 * (X - 0.48) / FACTOR)
    power[160] = 2.0
    fit = fit_curve(DAC, X, power)
    assert fit.rollover is not None
    assert fit.p_max_mw == pytest.approx(1.0, rel=0.02)


def test_spikes_do_not_distort_the_fit():
    power = 300.0 * DAC / 100.0
    power[100] *= 2.0
    power[140] *= 1.6
    fit = fit_curve(DAC, X, power)
    assert max(abs(e) for r, e in _errors(fit, lambda x: 300.0 * x / FACTOR).items() if r >= 10) < 1.0


def test_lookup_is_off_at_zero_and_saturates_at_one():
    fit = fit_curve(DAC, X, simulated_laser_mw(X))
    assert x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, 0.0) == 0.0
    assert x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, -0.1) == 0.0
    top = x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, 1.0)
    assert x_for_power_fraction(fit.anchor_power_mw, fit.anchor_x, fit.p_max_mw, 1.5) == top


def test_unusable_measurements_raise():
    with pytest.raises(CalibrationError, match="at least 3"):
        fit_curve([0.0, 1.0], [0.0, 0.01], [0.0, 1.0])
    with pytest.raises(CalibrationError, match="no light"):
        fit_curve(DAC, X, np.zeros(DAC.size))
    with pytest.raises(CalibrationError, match="dark noise"):  # every point below the zero level
        fit_curve(DAC, X, simulated_laser_mw(X), sigma_dark=1000.0)


def test_a_source_that_jumps_on_has_a_lowest_power_and_a_request_below_it_gets_it():
    c = make_calibration(model=bench_488_led_mw)
    assert c.lowest_percent == pytest.approx(2.12 / 31.7 * 100, rel=0.05)  # about 6.7 % of max
    lowest_command = c.commanded_percent(c.lowest_percent, 0.6, 1.0)
    for request in (0.5, 1.0, 3.0, c.lowest_percent * 0.99):
        assert c.commanded_percent(request, 0.6, 1.0) == lowest_command
    assert c.commanded_percent(0.0, 0.6, 1.0) == (0.0, False)  # off stays off
    assert c.commanded_percent(50.0, 0.6, 1.0)[0] > lowest_command[0]
    assert c.describe()["lowest_percent"] == pytest.approx(c.lowest_percent, abs=0.05)
    assert f"lowest {c.lowest_percent:.1f} %" in c.status(0.6, 1.0)


def test_a_source_that_rises_smoothly_has_no_lowest_power():
    c = make_calibration()  # the simulated laser rises from its threshold in steps far under 2 % of max
    assert c.lowest_percent == 0.0
    assert "lowest_percent" not in c.describe() and "lowest" not in c.status(0.6, 1.0)
    assert c.commanded_percent(0.5, 0.6, 1.0)[0] < c.commanded_percent(1.0, 0.6, 1.0)[0]


def test_the_lowest_power_reads_the_same_everywhere():
    # bench 2026-10-09: 488 nm lowest 6.65 % showed "6.7 %" in the tooltip but "6.6 %" in the status (rounded twice)
    c = make_calibration(model=bench_488_led_mw)
    c = replace(c, anchor_power_mw=np.r_[0.0, 0.0664999 * c.p_max_mw, c.anchor_power_mw[2:]])
    tooltip = intensity_tooltip(c.describe())
    shown = tooltip.split("Lowest non-zero: ")[1].split(" (")[0]
    assert f"lowest {shown}" in c.status(0.6, 1.0)
