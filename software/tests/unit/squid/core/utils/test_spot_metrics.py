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
    assert snr == 4.0


def test_extract_spot_metrics_low_signal():
    crop = np.zeros((12, 12), dtype=float)
    snr, peak, background = extract_spot_metrics(crop, 6.0, 6.0)

    assert peak == 0.0
    assert background == 0.0
    assert snr == 0.0


def test_extract_spot_metrics_empty():
    crop = np.zeros((0, 0), dtype=float)
    snr, peak, background = extract_spot_metrics(crop, 0.0, 0.0)

    assert math.isnan(snr)
    assert math.isnan(peak)
    assert math.isnan(background)
