"""Laser AF must use the saved Min Peak Width / Distance / Prominence, and the machine config's crop sizes."""

import numpy as np
import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control._def import SpotDetectionMode
from control.models import LaserAFConfig

SPOT_X_PX = 522.0  # 10 px right of the reference (512) = 2 um at 0.2 um/px


def spots_image(spots, shape=(256, 1024), y_px=128):
    """spots: (x_px, amplitude, sigma_x_px) for each spot on one row, like the AF laser's reflections."""
    x = np.arange(shape[1])
    profile = sum(amplitude * np.exp(-((x - x_px) ** 2) / (2 * sigma_px**2)) for x_px, amplitude, sigma_px in spots)
    gy = np.exp(-((np.arange(shape[0]) - y_px) ** 2) / (2 * 5.0**2))
    return np.round(255 * np.outer(gy, profile) / profile.max()).astype(np.uint8)


@pytest.fixture(scope="module")
def laser_af():
    scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    yield ts.get_test_laser_autofocus_controller(scope)
    scope.close()


@pytest.mark.parametrize(
    "setting, saved_value, spots",
    [
        # one spot narrower than the default min width (10 px)
        ("min_peak_width", 3, [(SPOT_X_PX, 1.0, 1.5)]),
        # a second, weaker spot that stands out more than the default prominence (0.2) but less than the saved one
        ("min_peak_prominence", 0.8, [(SPOT_X_PX, 1.0, 5.0), (700.0, 0.5, 5.0)]),
        # a second, weaker spot farther than the default min distance (10 px) but closer than the saved one
        ("min_peak_distance", 60, [(SPOT_X_PX, 1.0, 5.0), (SPOT_X_PX + 40, 0.6, 5.0)]),
    ],
)
def test_saved_peak_setting_reaches_spot_detection(laser_af, setting, saved_value, spots):
    """The default settings reject each image, so the measurement succeeds only if the saved value is used."""
    laser_af.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        **{setting: saved_value},
    )
    laser_af.get_new_frame = lambda: spots_image(spots)

    assert laser_af.measure_displacement() == pytest.approx(2.0, abs=0.05)


def test_initialize_takes_the_crop_sizes_from_the_machine_config(laser_af, monkeypatch):
    """A loaded profile carries the crop sizes it was saved with; Initialize must use the .ini ones."""
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 128)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    # keep the simulated calibration out of the checkout's user profile
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    laser_af.laser_af_properties = LaserAFConfig(
        width=1536,
        height=256,
        initialize_crop_width=1200,
        initialize_crop_height=800,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )
    # outside the saved initialize crop (1200 x 800 around the sensor centre), inside the .ini one
    laser_af.get_new_frame = lambda: spots_image([(604.0, 1.0, 5.0)], shape=(2064, 3088), y_px=401)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (88, 336, 1024, 128)
