"""Laser AF must find the spot with the saved Min Peak Width / Distance / Prominence, not the _def defaults.

Each case shows the focus camera an image the default settings reject but the saved setting accepts, so the
measurement succeeds only if the saved value reaches utils.find_spot_location.
"""

import numpy as np
import pytest

import control.microscope
import tests.control.test_stubs as ts
from control._def import SpotDetectionMode
from control.models import LaserAFConfig

X_REFERENCE_PX = 512.0
PIXEL_TO_UM = 0.2
SPOT_X_PX = 522.0  # 10 px right of the reference = 2 um


def spots_image(spots, shape=(256, 1024), y_px=128, sigma_y_px=5.0):
    """spots: (x_px, amplitude, sigma_x_px) for each spot on one row, like the AF laser's reflections."""
    x = np.arange(shape[1])
    profile = sum(amplitude * np.exp(-((x - x_px) ** 2) / (2 * sigma_px**2)) for x_px, amplitude, sigma_px in spots)
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * sigma_y_px**2))
    return np.round(255 * np.outer(gy, profile) / profile.max()).astype(np.uint8)


@pytest.fixture(scope="module")
def laser_af():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield ts.get_test_laser_autofocus_controller(scope)
    scope.close()


CASES = {
    # one spot narrower than the default min width (10 px)
    "min_peak_width": ({"min_peak_width": 3}, [(SPOT_X_PX, 1.0, 1.5)]),
    # a second, weaker spot that stands out more than the default prominence (0.2) but less than the saved one
    "min_peak_prominence": ({"min_peak_prominence": 0.8}, [(SPOT_X_PX, 1.0, 5.0), (700.0, 0.5, 5.0)]),
    # a second, weaker spot farther than the default min distance (10 px) but closer than the saved one
    "min_peak_distance": ({"min_peak_distance": 60}, [(SPOT_X_PX, 1.0, 5.0), (SPOT_X_PX + 40, 0.6, 5.0)]),
}


@pytest.mark.parametrize("setting", CASES)
def test_saved_peak_setting_reaches_spot_detection(laser_af, setting):
    saved, spots = CASES[setting]
    laser_af.laser_af_properties = LaserAFConfig(
        x_reference=X_REFERENCE_PX,
        pixel_to_um=PIXEL_TO_UM,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        **saved,
    )
    laser_af.get_new_frame = lambda: spots_image(spots)

    assert laser_af.measure_displacement() == pytest.approx(2.0, abs=0.05)
