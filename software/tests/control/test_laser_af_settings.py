"""Laser AF must use the saved detection settings (peak limits, which spot), the machine config's crop
sizes, and the sensor size the camera reports; the full-sensor view must never leak into a measurement."""

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
    # tall enough for spot detection's y window (96 rows either side of the spot), and not the saved 256
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    # keep the simulated calibration out of the checkout's user profile
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    # the real focus camera's sensor; the simulated one is smaller than the .ini initialize crop
    monkeypatch.setattr(laser_af.camera, "get_resolution", lambda: (3088, 2064))
    laser_af.laser_af_properties = LaserAFConfig(
        width=1536,
        height=256,
        initialize_crop_width=1200,
        initialize_crop_height=800,
        spot_detection_mode=SpotDetectionMode.SINGLE,
        laser_af_averaging_n=1,
    )
    # outside the saved initialize crop (1200 x 800 around the sensor centre), inside the .ini one
    sensor = spots_image([(604.0, 1.0, 5.0)], shape=(2064, 3088), y_px=401)
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (88, 288, 1024, 224)


def test_measurement_uses_the_saved_spot_index(laser_af):
    """Three reflections on the strip; the saved choice (2nd from the right) is the middle one."""
    laser_af.laser_af_properties = LaserAFConfig(
        x_reference=512.0,
        pixel_to_um=0.2,
        has_reference=True,
        spot_detection_mode=SpotDetectionMode.NTH_FROM_RIGHT,
        spot_index=2,
        laser_af_averaging_n=1,
    )
    laser_af.get_new_frame = lambda: spots_image([(400.0, 1.0, 5.0), (SPOT_X_PX, 1.0, 5.0), (700.0, 1.0, 5.0)])

    assert laser_af.measure_displacement() == pytest.approx(2.0, abs=0.05)


def test_initialize_searches_the_sensor_the_camera_reports(laser_af, monkeypatch):
    """The spot sits beyond the 3088 x 2064 that used to be hard-coded, on a camera reporting 4000 x 3000."""
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 4000)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 3000)
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    monkeypatch.setattr(laser_af.camera, "get_resolution", lambda: (4000, 3000))
    laser_af.laser_af_properties = LaserAFConfig(spot_detection_mode=SpotDetectionMode.SINGLE, laser_af_averaging_n=1)
    sensor = spots_image([(3500.0, 1.0, 5.0)], shape=(3000, 4000), y_px=1500)
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (2984, 1388, 1024, 224)


def test_initialize_keeps_the_chosen_reflection_when_the_strip_drops_a_neighbour(laser_af, monkeypatch):
    """2nd from the left among 1000 / 1600 / 1800 on the sensor is 1600. The 1024 px strip around it starts
    at 1088 and drops the 1000 spot, so inside the strip that same reflection is the 1st from the left."""
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    monkeypatch.setattr(laser_af.camera, "get_resolution", lambda: (3088, 2064))
    laser_af.laser_af_properties = LaserAFConfig(
        spot_detection_mode=SpotDetectionMode.NTH_FROM_LEFT, spot_index=2, laser_af_averaging_n=1
    )
    sensor = spots_image([(1000.0, 1.0, 5.0), (1600.0, 1.0, 5.0), (1800.0, 1.0, 5.0)], shape=(2064, 3088), y_px=1032)
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (1088, 920, 1024, 224)
    assert laser_af.set_reference()

    properties = laser_af.laser_af_properties
    assert properties.x_reference + properties.x_offset == pytest.approx(1600.0, abs=1.0)  # sensor coordinates
    assert properties.spot_index == 2  # the user's choice is untouched
    assert properties.spot_index_within_strip() == 1


def test_initialize_that_fails_after_placing_the_strip_is_not_initialized(laser_af, monkeypatch):
    """Calibration (or the strip ranking) can still fail after the strip is on the camera."""
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    monkeypatch.setattr(laser_af.camera, "get_resolution", lambda: (3088, 2064))
    monkeypatch.setattr(laser_af, "_calibrate_pixel_to_um", lambda: False)
    laser_af.laser_af_properties = LaserAFConfig(spot_detection_mode=SpotDetectionMode.SINGLE, laser_af_averaging_n=1)
    sensor = spots_image([(1600.0, 1.0, 5.0)], shape=(2064, 3088), y_px=1032)
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert not laser_af.initialize_auto()
    assert not laser_af.is_initialized


def test_initialize_finds_the_spot_when_the_initialize_crop_exceeds_the_sensor(laser_af, monkeypatch):
    """The 2800 x 1800 search crop is clamped to the 1920 x 1080 simulated sensor; the spot must map back
    to its true sensor position, not be shifted by half the missing crop."""
    monkeypatch.setattr(control._def, "LASER_AF_CROP_WIDTH", 1024)
    monkeypatch.setattr(control._def, "LASER_AF_CROP_HEIGHT", 224)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_WIDTH", 2800)
    monkeypatch.setattr(control._def, "LASER_AF_INITIALIZE_CROP_HEIGHT", 1800)
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    laser_af.laser_af_properties = LaserAFConfig(spot_detection_mode=SpotDetectionMode.SINGLE, laser_af_averaging_n=1)
    sensor = spots_image([(604.0, 1.0, 5.0)], shape=(1080, 1920), y_px=401)
    laser_af.get_new_frame = lambda: roi_frame(laser_af.camera, sensor)

    assert laser_af.initialize_auto()
    assert laser_af.camera.get_region_of_interest() == (88, 288, 1024, 224)


STRIP = dict(x_offset=800, y_offset=900, width=1536, height=256)


@pytest.fixture
def on_the_strip(laser_af, monkeypatch):
    """The controller set up on a strip away from the sensor corner, reference 512 px into the strip."""
    monkeypatch.setattr(laser_af._config_repo, "save_laser_af_config", lambda *args: None)
    laser_af.initialize_manual(
        LaserAFConfig(
            **STRIP,
            x_reference=800 + 512.0,
            pixel_to_um=0.2,
            has_reference=True,
            spot_detection_mode=SpotDetectionMode.SINGLE,
            laser_af_averaging_n=1,
        )
    )
    laser_af.get_new_frame = lambda: spots_image([(SPOT_X_PX, 1.0, 5.0)])
    seen = []
    laser_af.signal_full_sensor_view_changed.connect(seen.append)
    yield laser_af, seen
    laser_af.signal_full_sensor_view_changed.disconnect(seen.append)


def test_full_sensor_view_streams_the_whole_sensor_then_the_strip_again(on_the_strip):
    laser_af, seen = on_the_strip

    laser_af.set_full_sensor_view(True)
    assert laser_af.camera.get_region_of_interest() == (0, 0, 1920, 1080)  # the simulated focus camera
    assert laser_af.full_sensor_view is True

    laser_af.set_full_sensor_view(False)
    assert laser_af.camera.get_region_of_interest() == (800, 900, 1536, 256)
    assert laser_af.full_sensor_view is False
    assert seen == [True, False]


def test_measuring_returns_to_the_strip_first(on_the_strip):
    """x_reference is relative to the strip, so a measurement taken on the full sensor would be nonsense."""
    laser_af, seen = on_the_strip
    laser_af.set_full_sensor_view(True)

    assert laser_af.measure_displacement() == pytest.approx(2.0, abs=0.05)
    assert laser_af.camera.get_region_of_interest() == (800, 900, 1536, 256)
    assert seen == [True, False]


def test_setting_the_reference_returns_to_the_strip_first(on_the_strip):
    laser_af, seen = on_the_strip
    laser_af.set_full_sensor_view(True)

    assert laser_af.set_reference()
    assert laser_af.camera.get_region_of_interest() == (800, 900, 1536, 256)
    assert seen == [True, False]


def test_initializing_leaves_the_full_sensor_view(on_the_strip):
    laser_af, seen = on_the_strip
    laser_af.set_full_sensor_view(True)

    laser_af.initialize_manual(LaserAFConfig(**STRIP))
    assert laser_af.camera.get_region_of_interest() == (800, 900, 1536, 256)
    assert seen == [True, False]
