"""Laser AF configs saved before the connected-components detector must still load, with their calibration."""

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

import control._def as _def
from control._def import LaserAFConfirmMotionMode, SpotDetectionMode
from control.core.config import ConfigRepository
from control.models import LaserAFConfig

# A laser_af_configs/<objective>.yaml as the line-profile detector wrote it.
LEGACY_YAML = """
version: 1.0
x_offset: 608.0
y_offset: 904.0
width: 1536
height: 256
pixel_to_um: 0.4321
has_reference: true
laser_af_averaging_n: 3
displacement_success_window_um: 1.0
spot_crop_size: 100
correlation_threshold: 0.7
pixel_to_um_calibration_distance: 6.0
calibration_timestamp: '2025-03-01 10:00:00'
laser_af_range: 40.0
focus_camera_exposure_time_ms: 2.0
focus_camera_analog_gain: 0.0
spot_detection_mode: multi_right
y_window: 96
x_window: 20
min_peak_width: 10.0
min_peak_distance: 10.0
min_peak_prominence: 0.2
spot_spacing: 100.0
filter_sigma: null
x_reference: 1377.6
initialize_crop_width: 1200
initialize_crop_height: 800
"""


def legacy_data(**overrides):
    data = yaml.safe_load(LEGACY_YAML)
    data.update(overrides)
    return data


def test_legacy_config_loads_and_keeps_its_calibration():
    config = LaserAFConfig(**legacy_data())

    # calibration and reference survive untouched
    assert config.pixel_to_um == 0.4321
    assert config.x_reference == 1377.6
    assert config.has_reference is True
    assert (config.x_offset, config.y_offset, config.width, config.height) == (608.0, 904.0, 1536, 256)
    assert config.laser_af_range == 40.0
    assert config.correlation_threshold == 0.7
    assert config.calibration_timestamp == "2025-03-01 10:00:00"

    # the line-profile keys are gone, and the new detector's parameters take their defaults
    dumped = config.model_dump()
    for legacy_key in (
        "y_window",
        "x_window",
        "min_peak_width",
        "min_peak_distance",
        "min_peak_prominence",
        "spot_spacing",
        "displacement_success_window_um",
    ):
        assert legacy_key not in dumped
    assert config.cc_threshold == float(_def.LASER_AF_CC_THRESHOLD)
    assert config.cc_min_area == _def.LASER_AF_CC_MIN_AREA
    assert config.cc_max_area == _def.LASER_AF_CC_MAX_AREA
    assert config.cc_row_tolerance == float(_def.LASER_AF_CC_ROW_TOLERANCE)
    assert config.cc_max_aspect_ratio == float(_def.LASER_AF_CC_MAX_ASPECT_RATIO)
    # "filtering off" was the line-profile detector's setting; the new one is tuned with the filter on
    assert config.filter_sigma == _def.LASER_AF_FILTER_SIGMA

    # new behaviours are off, and the search keeps the span laser_af_range used to give it
    assert config.laser_af_search_range_um == 40.0
    assert config.confirm_motion_mode == LaserAFConfirmMotionMode.OFF
    assert config.iterative_correction_enabled is False


@pytest.mark.parametrize("retired_mode", ["multi_right", "multi_second_right"])
def test_retired_spot_modes_become_dual_right(retired_mode):
    config = LaserAFConfig(**legacy_data(spot_detection_mode=retired_mode))

    assert config.spot_detection_mode == SpotDetectionMode.DUAL_RIGHT


@pytest.mark.parametrize("mode", ["single", "dual_left", "dual_right"])
def test_surviving_spot_modes_are_unchanged(mode):
    assert LaserAFConfig(**legacy_data(spot_detection_mode=mode)).spot_detection_mode == SpotDetectionMode(mode)


def test_crop_relative_x_reference_is_repaired():
    # 159.8 is left of the crop (x_offset 608), so it can only be a crop-relative value
    config = LaserAFConfig(**legacy_data(x_reference=159.8))

    assert config.x_reference == pytest.approx(767.8)


@pytest.mark.parametrize(
    "x_reference",
    [
        1377.6,  # inside the crop: already full-sensor
        608.0,  # on the crop edge: ambiguous, left alone
        None,  # no reference
    ],
)
def test_plausible_x_reference_is_left_alone(x_reference):
    assert LaserAFConfig(**legacy_data(x_reference=x_reference)).x_reference == x_reference


def test_unrepairable_x_reference_is_left_for_the_operator():
    # adding the offset back would still not land inside the crop
    config = LaserAFConfig(**legacy_data(x_offset=608.0, width=64, x_reference=300.0))

    assert config.x_reference == 300.0


def test_x_reference_is_not_touched_when_the_crop_starts_at_zero():
    assert LaserAFConfig(x_offset=0, width=1536, x_reference=12.0).x_reference == 12.0


def test_unreachable_correlation_threshold_is_clamped():
    assert LaserAFConfig(correlation_threshold=1.0).correlation_threshold == _def.MAX_CORRELATION_THRESHOLD
    assert LaserAFConfig(correlation_threshold=0.9).correlation_threshold == 0.9


def test_explicit_search_span_is_not_overwritten_by_laser_af_range():
    config = LaserAFConfig(laser_af_range=40.0, laser_af_search_range_um=15.0)

    assert config.laser_af_search_range_um == 15.0


def test_retired_window_field_is_dropped_without_the_line_profile_migration():
    # An already-migrated config that still carries the retired window keeps its filter_sigma choice.
    config = LaserAFConfig(displacement_success_window_pixels=300, filter_sigma=None, cc_threshold=20)

    assert config.filter_sigma is None
    assert config.cc_threshold == 20


def test_unknown_keys_are_still_rejected():
    with pytest.raises(ValidationError):
        LaserAFConfig(**legacy_data(cc_treshold=12))


def test_zero_search_step_is_rejected():
    with pytest.raises(ValidationError):
        LaserAFConfig(laser_af_search_step_um=0)


def test_new_format_config_round_trips():
    original = LaserAFConfig(
        x_offset=712.0,
        y_offset=904.0,
        width=1536,
        height=256,
        pixel_to_um=0.21,
        x_reference=1400.25,
        has_reference=True,
        laser_af_range=60.0,
        laser_af_search_range_um=25.0,
        laser_af_search_step_um=2.5,
        confirm_motion_mode=LaserAFConfirmMotionMode.SEARCH_ONLY,
        confirm_step_um=3.0,
        confirm_tolerance_px=5.0,
        iterative_correction_enabled=True,
        iterative_correction_tolerance_um=0.5,
        iterative_correction_min_displacement_um=8.0,
        spot_detection_mode=SpotDetectionMode.DUAL_LEFT,
        correlation_threshold=0.8,
        cc_threshold=12.0,
        cc_min_area=9,
        cc_max_area=3000,
        cc_row_tolerance=70.0,
        cc_max_aspect_ratio=3.5,
        filter_sigma=0,
    )
    original.set_reference_image(np.arange(64 * 64, dtype=np.float32).reshape(64, 64))

    reloaded = LaserAFConfig(**yaml.safe_load(yaml.safe_dump(original.model_dump(mode="json"))))

    assert reloaded == original
    assert reloaded.filter_sigma == 0  # a deliberate "off" survives once the config is in the new format
    np.testing.assert_array_equal(reloaded.reference_image_cropped, original.reference_image_cropped)


def test_legacy_file_loads_through_the_repository_and_is_rewritten_in_the_new_format(tmp_path):
    """The repository returns None for a config that fails validation, which would silently drop the calibration."""
    profile_dir = tmp_path / "user_profiles" / "default" / "laser_af_configs"
    profile_dir.mkdir(parents=True)
    path = profile_dir / "20x.yaml"
    path.write_text(LEGACY_YAML)
    repo = ConfigRepository(base_path=tmp_path)
    repo.set_profile("default")

    config = repo.get_laser_af_config("20x")

    assert config is not None
    assert config.pixel_to_um == 0.4321
    assert config.spot_detection_mode == SpotDetectionMode.DUAL_RIGHT

    repo.save_laser_af_config("default", "20x", config)
    on_disk = yaml.safe_load(path.read_text())
    assert "y_window" not in on_disk and "min_peak_width" not in on_disk
    assert on_disk["spot_detection_mode"] == "dual_right"
    assert on_disk["cc_threshold"] == float(_def.LASER_AF_CC_THRESHOLD)
    # and what was written loads back unchanged
    assert LaserAFConfig(**on_disk) == config
