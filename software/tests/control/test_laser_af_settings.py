"""Laser AF must use the saved connected components detection settings, and the machine config's crop sizes."""

import numpy as np
import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control._def import SpotDetectionMode
from control.models import LaserAFConfig
from control.utils import find_spot_location

SPOT_X_PX = 522.0  # 10 px right of the reference (512) = 2 um at 0.2 um/px
CENTER_ROW_PX = 128.0


def spots_image(spots, shape=(256, 1024)):
    """spots: (x_px, y_px, amplitude, sigma_x_px, sigma_y_px) for each spot, like the AF laser's reflections."""
    y, x = np.ogrid[: shape[0], : shape[1]]
    image = sum(
        amplitude * np.exp(-((x - x_px) ** 2) / (2 * sigma_x_px**2) - ((y - y_px) ** 2) / (2 * sigma_y_px**2))
        for x_px, y_px, amplitude, sigma_x_px, sigma_y_px in spots
    )
    return np.round(255 * image / image.max()).astype(np.uint8)


def roi_frame(camera, sensor):
    """The sensor image cut to the camera's region of interest, as the real focus camera returns it."""
    x, y, width, height = camera.get_region_of_interest()
    return sensor[y : y + height, x : x + width]


@pytest.fixture(scope="module")
def laser_af():
    scope = control.microscope.Microscope.build_from_global_config(True, skip_init=True)
    yield ts.get_test_laser_autofocus_controller(scope)
    scope.close()


@pytest.mark.parametrize(
    "setting, saved_value, spots",
    [
        # a second, weaker spot brighter than the default threshold (8) but dimmer than the saved one
        ("cc_threshold", 100, [(SPOT_X_PX, CENTER_ROW_PX, 1.0, 5.0, 5.0), (700.0, CENTER_ROW_PX, 0.3, 5.0, 5.0)]),
        # a second, small spot larger than the default min area (5 px) but smaller than the saved one
        ("cc_min_area", 200, [(SPOT_X_PX, CENTER_ROW_PX, 1.0, 5.0, 5.0), (700.0, CENTER_ROW_PX, 1.0, 1.5, 1.5)]),
        # one spot larger than the default max area (5000 px)
        ("cc_max_area", 20000, [(SPOT_X_PX, CENTER_ROW_PX, 1.0, 18.0, 18.0)]),
        # one spot farther from the centre row than the default row tolerance (50 px)
        ("cc_row_tolerance", 100, [(SPOT_X_PX, CENTER_ROW_PX + 70, 1.0, 5.0, 5.0)]),
        # one spot more elongated than the default max aspect ratio (2.5)
        ("cc_max_aspect_ratio", 5.0, [(SPOT_X_PX, CENTER_ROW_PX, 1.0, 20.0, 5.0)]),
    ],
)
def test_saved_detection_setting_reaches_spot_detection(laser_af, setting, saved_value, spots):
    """The default settings reject each image, so the measurement succeeds only if the saved value is used."""
    image = spots_image(spots)
    with pytest.raises(ValueError):
        find_spot_location(image, mode=SpotDetectionMode.SINGLE, filter_sigma=control._def.LASER_AF_FILTER_SIGMA)

    laser_af.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        **{setting: saved_value},
    )
    laser_af.get_new_frame = lambda: image

    assert laser_af.measure_displacement() == pytest.approx(2.0, abs=0.05)


def test_initialize_takes_the_crop_sizes_from_the_machine_config(laser_af, monkeypatch):
    """A loaded profile carries the crop sizes it was saved with; Initialize must use the .ini ones."""
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    # not the saved 256
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
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
    sensor = spots_image([(604.0, 401.0, 1.0, 5.0, 5.0)], shape=(2064, 3088))
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (88, 288, 1024, 224)
