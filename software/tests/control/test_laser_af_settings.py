"""Laser AF must use the saved connected-component detection settings, and the machine config's crop sizes."""

import math

import numpy as np
import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control._def import SpotDetectionMode
from control.models import LaserAFConfig

FRAME_SHAPE = (256, 1024)
SPOT_X_PX = 522.0  # 10 px right of the reference (512) = 2 um at 0.2 um/px


def blob_image(center_x, center_y, width, height, value=200, shape=FRAME_SHAPE):
    """A uniform rectangle on black; its centroid is (center_x, center_y) for odd width and height."""
    image = np.zeros(shape, dtype=np.uint8)
    x0 = int(round(center_x - (width - 1) / 2))
    y0 = int(round(center_y - (height - 1) / 2))
    image[y0 : y0 + height, x0 : x0 + width] = value
    return image


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
    "setting, saved_value, image",
    [
        # dimmer than the default threshold (8)
        ("cc_threshold", 2, blob_image(SPOT_X_PX, 128, 11, 11, value=6)),
        # smaller than the default minimum area (5 px)
        ("cc_min_area", 1, blob_image(SPOT_X_PX, 128, 2, 2)),
        # larger than the default maximum area (5000 px)
        ("cc_max_area", 10000, blob_image(SPOT_X_PX, 128, 81, 81)),
        # farther from the middle row than the default row tolerance (50 px)
        ("cc_row_tolerance", 100, blob_image(SPOT_X_PX, 48, 11, 11)),
        # more elongated than the default maximum aspect ratio (2.5)
        ("cc_max_aspect_ratio", 8, blob_image(SPOT_X_PX, 128, 55, 11)),
    ],
)
def test_saved_cc_setting_reaches_spot_detection(laser_af, setting, saved_value, image):
    """The default settings reject each image, so the measurement succeeds only if the saved value is used."""
    base = dict(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        filter_sigma=None,
    )
    laser_af.get_new_frame = lambda: image

    laser_af.laser_af_properties = LaserAFConfig(**base)
    assert math.isnan(laser_af.measure_displacement(search_for_spot=False))

    laser_af.laser_af_properties = LaserAFConfig(**base, **{setting: saved_value})
    # within one pixel (0.2 um): the 2 x 2 blob cannot be centred on a whole pixel
    assert laser_af.measure_displacement(search_for_spot=False) == pytest.approx(2.0, abs=0.15)


def test_initialize_takes_the_crop_sizes_from_the_machine_config(laser_af, monkeypatch):
    """A loaded profile carries the crop sizes it was saved with; a full Initialize must use the .ini ones."""
    sensor_width, sensor_height = laser_af.get_sensor_size()
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 512)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", sensor_width - 100)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", sensor_height - 100)
    # keep the simulated calibration out of the checkout's user profile
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    laser_af.laser_af_properties = LaserAFConfig(
        width=1024,
        height=256,
        initialize_crop_width=200,
        initialize_crop_height=200,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        filter_sigma=None,
    )
    # outside the saved initialize crop (200 x 200 around the sensor centre), inside the .ini one
    spot_x, spot_y = 404, 301
    sensor = blob_image(spot_x, spot_y, 11, 11, shape=(sensor_height, sensor_width))
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    # centred on the spot, snapped down to the camera's 8 x 2 px grid
    assert laser_af.camera.get_region_of_interest() == (144, 188, 512, 224)


def test_initialize_within_the_current_crop_leaves_the_crop_alone(laser_af, monkeypatch):
    """search_within_current_crop keeps the operator's crop, whatever the machine config says."""
    sensor_width, sensor_height = laser_af.get_sensor_size()
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 512)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    laser_af.laser_af_properties = LaserAFConfig(
        x_offset=200,
        y_offset=100,
        width=800,
        height=300,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
        filter_sigma=None,
    )
    sensor = blob_image(404, 251, 11, 11, shape=(sensor_height, sensor_width))
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto(search_within_current_crop=True)
    assert laser_af.camera.get_region_of_interest() == (200, 100, 800, 300)
    assert (laser_af.laser_af_properties.width, laser_af.laser_af_properties.height) == (800, 300)
