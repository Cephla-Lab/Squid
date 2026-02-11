"""Tests for extract_spot_metrics."""

import math

import numpy as np

from squid.core.utils.hardware_utils import extract_spot_metrics


def test_extract_spot_metrics_basic():
    crop = np.full((20, 20), 10.0, dtype=float)
    crop[10, 10] = 50.0
    snr, peak, background = extract_spot_metrics(crop, 10.0, 10.0)

    assert peak == 50.0
    assert background == 10.0
    # New SNR is (peak-background)/noise_floor; with flat background this
    # is dominated by shot-noise sqrt(background) ~= 3.16.
    assert 12.0 < snr < 13.0


def test_extract_spot_metrics_low_signal():
    crop = np.zeros((12, 12), dtype=float)
    snr, peak, background = extract_spot_metrics(crop, 6.0, 6.0)

    assert peak == 0.0
    assert background == 0.0
    assert snr == 0.0


def test_extract_spot_metrics_increases_with_cleaner_background():
    clean = np.full((40, 40), 20.0, dtype=float)
    noisy = clean.copy()

    # Same peak signal in both crops.
    clean[20, 20] = 120.0
    noisy[20, 20] = 120.0

    rng = np.random.default_rng(42)
    noisy += rng.normal(0.0, 8.0, size=noisy.shape)

    snr_clean, peak_clean, bg_clean = extract_spot_metrics(clean, 20.0, 20.0)
    snr_noisy, peak_noisy, bg_noisy = extract_spot_metrics(noisy, 20.0, 20.0)

    assert peak_clean > bg_clean
    assert peak_noisy > bg_noisy
    assert snr_clean > snr_noisy


def test_extract_spot_metrics_empty():
    crop = np.zeros((0, 0), dtype=float)
    snr, peak, background = extract_spot_metrics(crop, 0.0, 0.0)

    assert math.isnan(snr)
    assert math.isnan(peak)
    assert math.isnan(background)
