"""
Unit tests for acquisition configuration models.

Tests the Pydantic models in control/models/ for:
- IlluminationChannelConfig
- ConfocalConfig
- CameraMappingsConfig
- AcquisitionChannel, GeneralChannelConfig, ObjectiveChannelConfig
- LaserAFConfig
"""

import pytest
from pydantic import ValidationError

from control.models import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraHardwareInfo,
    CameraMappingsConfig,
    CameraPropertyBindings,
    CameraSettings,
    ConfocalConfig,
    ConfocalSettings,
    GeneralChannelConfig,
    IlluminationChannel,
    IlluminationChannelConfig,
    IlluminationSettings,
    LaserAFConfig,
    ObjectiveChannelConfig,
)
from control.models.illumination_config import (
    DEFAULT_LED_COLOR,
    DEFAULT_WAVELENGTH_COLORS,
    IlluminationType,
)
from control._def import SpotDetectionMode


class TestIlluminationChannelConfig:
    """Tests for IlluminationChannel and IlluminationChannelConfig models."""

    def test_illumination_channel_creation(self):
        """Test creating an illumination channel with required fields."""
        channel = IlluminationChannel(
            name="Fluorescence 488nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=488,
            controller_port="D2",
        )
        assert channel.name == "Fluorescence 488nm"
        assert channel.type == IlluminationType.EPI_ILLUMINATION
        assert channel.wavelength_nm == 488
        assert channel.controller_port == "D2"
        assert channel.intensity_calibration_file is None  # Default

    def test_illumination_channel_led_matrix(self):
        """Test creating an LED matrix channel without wavelength."""
        channel = IlluminationChannel(
            name="BF LED matrix full",
            type=IlluminationType.TRANSILLUMINATION,
            wavelength_nm=None,
            controller_port="USB1",  # USB port encodes the LED pattern
        )
        assert channel.wavelength_nm is None
        assert channel.type == IlluminationType.TRANSILLUMINATION
        assert channel.controller_port == "USB1"

    def test_illumination_channel_with_calibration(self):
        """Test channel with intensity calibration file."""
        channel = IlluminationChannel(
            name="Fluorescence 405nm",
            type=IlluminationType.EPI_ILLUMINATION,
            wavelength_nm=405,
            controller_port="D1",
            intensity_calibration_file="405.csv",
        )
        assert channel.intensity_calibration_file == "405.csv"

    def test_illumination_channel_config_get_by_name(self):
        """Test getting channel by name from config."""
        config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Channel A",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D1",
                    wavelength_nm=405,
                ),
                IlluminationChannel(
                    name="Channel B",
                    type=IlluminationType.TRANSILLUMINATION,
                    controller_port="USB1",
                    led_matrix_pattern="full",
                ),
            ],
        )

        found = config.get_channel_by_name("Channel A")
        assert found is not None
        assert found.name == "Channel A"

        not_found = config.get_channel_by_name("Nonexistent")
        assert not_found is None

    def test_illumination_channel_config_get_source_code(self):
        """Test resolving source code from controller port mapping."""
        config = IlluminationChannelConfig(
            version=1,
            controller_port_mapping={
                "D1": 11,
                "D2": 12,
                "USB1": 0,
                "USB4": 3,  # USB ports encode LED patterns
            },
            channels=[
                IlluminationChannel(
                    name="Laser 405",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D1",
                    wavelength_nm=405,
                ),
                IlluminationChannel(
                    name="BF LED",
                    type=IlluminationType.TRANSILLUMINATION,
                    controller_port="USB4",  # dark_field pattern
                ),
            ],
        )

        # Test laser channel - should get source code from controller_port_mapping
        laser = config.get_channel_by_name("Laser 405")
        assert config.get_source_code(laser) == 11

        # Test LED channel - should get source code from controller_port_mapping (USB port)
        led = config.get_channel_by_name("BF LED")
        assert config.get_source_code(led) == 3

    def test_illumination_channel_config_get_by_source_code(self):
        """Test getting channel by source code."""
        config = IlluminationChannelConfig(
            version=1,
            controller_port_mapping={"D1": 11},
            channels=[
                IlluminationChannel(
                    name="Channel A",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D1",
                    wavelength_nm=405,
                ),
            ],
        )

        found = config.get_channel_by_source_code(11)
        assert found is not None
        assert found.name == "Channel A"

        not_found = config.get_channel_by_source_code(99)
        assert not_found is None

    def test_default_wavelength_colors(self):
        """Test default color mapping for common wavelengths."""
        assert 405 in DEFAULT_WAVELENGTH_COLORS
        assert 488 in DEFAULT_WAVELENGTH_COLORS
        assert 561 in DEFAULT_WAVELENGTH_COLORS
        assert 638 in DEFAULT_WAVELENGTH_COLORS
        assert DEFAULT_LED_COLOR == "#FFFFFF"


class TestConfocalConfig:
    """Tests for ConfocalConfig model."""

    def test_confocal_config_creation(self):
        """Test creating a confocal config."""
        config = ConfocalConfig(
            version=1,
            filter_wheel_mappings={
                1: {1: "ET520/40", 2: "ET680/42"},
                2: {1: "ET750/60"},
            },
            public_properties=["emission_filter_wheel_position"],
            objective_specific_properties=["illumination_iris", "emission_iris"],
        )
        assert config.version == 1
        assert len(config.filter_wheel_mappings) == 2

    def test_confocal_config_get_filter_name(self):
        """Test getting filter name by wheel and slot."""
        config = ConfocalConfig(
            filter_wheel_mappings={
                1: {1: "ET520/40", 2: "ET680/42"},
            },
        )

        assert config.get_filter_name(1, 1) == "ET520/40"
        assert config.get_filter_name(1, 2) == "ET680/42"
        assert config.get_filter_name(1, 3) is None  # Slot not found
        assert config.get_filter_name(2, 1) is None  # Wheel not found

    def test_confocal_config_has_property(self):
        """Test checking if property is available."""
        config = ConfocalConfig(
            public_properties=["emission_filter_wheel_position"],
            objective_specific_properties=["illumination_iris"],
        )

        assert config.has_property("emission_filter_wheel_position") is True
        assert config.has_property("illumination_iris") is True
        assert config.has_property("nonexistent") is False

    def test_confocal_config_empty(self):
        """Test confocal config with no mappings."""
        config = ConfocalConfig()
        assert config.filter_wheel_mappings is None
        assert config.get_filter_name(1, 1) is None


class TestCameraMappingsConfig:
    """Tests for CameraMappingsConfig model."""

    def test_camera_mappings_creation(self):
        """Test creating camera mappings config."""
        config = CameraMappingsConfig(
            version=1,
            hardware_connection_info={
                "camera_1": CameraHardwareInfo(filter_wheel_id=1),
            },
            property_bindings={
                "camera_1": CameraPropertyBindings(dichroic_position=1),
            },
        )
        assert config.version == 1
        assert "camera_1" in config.hardware_connection_info

    def test_camera_mappings_get_hardware_info(self):
        """Test getting hardware info for a camera."""
        config = CameraMappingsConfig(
            hardware_connection_info={
                "camera_1": CameraHardwareInfo(filter_wheel_id=1),
            },
        )

        hw = config.get_hardware_info("camera_1")
        assert hw is not None
        assert hw.filter_wheel_id == 1

        assert config.get_hardware_info("camera_2") is None

    def test_camera_mappings_has_confocal(self):
        """Test checking if confocal is in light path."""
        from control.models.camera_config import ConfocalCameraSettings

        # Without confocal
        config_no_confocal = CameraMappingsConfig(
            hardware_connection_info={
                "camera_1": CameraHardwareInfo(filter_wheel_id=1),
            },
        )
        assert config_no_confocal.has_confocal_in_light_path("camera_1") is False

        # With confocal
        config_with_confocal = CameraMappingsConfig(
            hardware_connection_info={
                "camera_1": CameraHardwareInfo(confocal_settings=ConfocalCameraSettings(filter_wheel_id=1)),
            },
        )
        assert config_with_confocal.has_confocal_in_light_path("camera_1") is True


class TestAcquisitionConfig:
    """Tests for acquisition channel config models."""

    def test_camera_settings_required_fields(self):
        """Test that exposure_time_ms and gain_mode are required."""
        # Should work with required fields
        settings = CameraSettings(
            exposure_time_ms=20.0,
            gain_mode=10.0,
        )
        assert settings.exposure_time_ms == 20.0
        assert settings.gain_mode == 10.0
        assert settings.display_color == "#FFFFFF"  # Default

        # Should fail without required fields
        with pytest.raises(ValidationError):
            CameraSettings()

    def test_confocal_settings_defaults(self):
        """Test confocal settings have correct defaults."""
        settings = ConfocalSettings()
        assert settings.filter_wheel_id == 1
        assert settings.emission_filter_wheel_position == 1
        assert settings.illumination_iris is None
        assert settings.emission_iris is None

    def test_illumination_settings_required_fields(self):
        """Test that illumination_channels and intensity are required."""
        settings = IlluminationSettings(
            illumination_channels=["Fluorescence 488nm"],
            intensity={"Fluorescence 488nm": 20.0},
        )
        assert len(settings.illumination_channels) == 1
        assert settings.intensity["Fluorescence 488nm"] == 20.0
        assert settings.z_offset_um == 0.0  # Default

        # Should fail without required fields
        with pytest.raises(ValidationError):
            IlluminationSettings()

    def test_acquisition_channel_creation(self):
        """Test creating an acquisition channel."""
        channel = AcquisitionChannel(
            name="488 nm",
            illumination_settings=IlluminationSettings(
                illumination_channels=["Fluorescence 488nm"],
                intensity={"Fluorescence 488nm": 20.0},
            ),
            camera_settings={
                "1": CameraSettings(exposure_time_ms=25.0, gain_mode=10.0),
            },
        )
        assert channel.name == "488 nm"
        assert "1" in channel.camera_settings
        assert channel.confocal_settings is None
        assert channel.confocal_override is None

    def test_acquisition_channel_with_confocal(self):
        """Test acquisition channel with confocal settings."""
        channel = AcquisitionChannel(
            name="488 nm",
            illumination_settings=IlluminationSettings(
                illumination_channels=["Fluorescence 488nm"],
                intensity={"Fluorescence 488nm": 20.0},
            ),
            camera_settings={
                "1": CameraSettings(exposure_time_ms=25.0, gain_mode=10.0),
            },
            confocal_settings=ConfocalSettings(
                emission_filter_wheel_position=2,
            ),
        )
        assert channel.confocal_settings is not None
        assert channel.confocal_settings.emission_filter_wheel_position == 2

    def test_acquisition_channel_effective_settings_no_confocal(self):
        """Test get_effective_settings without confocal mode."""
        channel = AcquisitionChannel(
            name="488 nm",
            illumination_settings=IlluminationSettings(
                illumination_channels=["Fluorescence 488nm"],
                intensity={"Fluorescence 488nm": 20.0},
            ),
            camera_settings={
                "1": CameraSettings(exposure_time_ms=25.0, gain_mode=10.0),
            },
            confocal_override=AcquisitionChannelOverride(
                camera_settings={
                    "1": CameraSettings(exposure_time_ms=50.0, gain_mode=10.0),
                },
            ),
        )

        # Without confocal mode, should return original settings
        effective = channel.get_effective_settings(confocal_mode=False)
        assert effective.camera_settings["1"].exposure_time_ms == 25.0

    def test_acquisition_channel_effective_settings_with_confocal(self):
        """Test get_effective_settings with confocal mode."""
        channel = AcquisitionChannel(
            name="488 nm",
            illumination_settings=IlluminationSettings(
                illumination_channels=["Fluorescence 488nm"],
                intensity={"Fluorescence 488nm": 20.0},
            ),
            camera_settings={
                "1": CameraSettings(exposure_time_ms=25.0, gain_mode=10.0),
            },
            confocal_override=AcquisitionChannelOverride(
                camera_settings={
                    "1": CameraSettings(exposure_time_ms=50.0, gain_mode=10.0),
                },
            ),
        )

        # With confocal mode, should apply override
        effective = channel.get_effective_settings(confocal_mode=True)
        assert effective.camera_settings["1"].exposure_time_ms == 50.0

    def test_general_channel_config(self):
        """Test GeneralChannelConfig creation and methods."""
        config = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel A",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        assert config.version == 1
        assert len(config.channels) == 1

        found = config.get_channel_by_name("Channel A")
        assert found is not None
        assert found.name == "Channel A"

        assert config.get_channel_by_name("Nonexistent") is None

    def test_objective_channel_config(self):
        """Test ObjectiveChannelConfig creation."""
        config = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="Channel A",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["A"],
                        intensity={"A": 25.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=30.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        assert config.version == 1
        found = config.get_channel_by_name("Channel A")
        assert found is not None


class TestLaserAFConfig:
    """Tests for LaserAFConfig model."""

    def test_laser_af_config_defaults(self):
        """Test LaserAFConfig has correct defaults."""
        config = LaserAFConfig()
        assert config.version == 1
        assert config.x_offset == 0
        assert config.y_offset == 0
        assert config.width == 1536
        assert config.height == 256
        assert config.pixel_to_um == 1.0
        assert config.has_reference is False
        assert config.laser_af_range == 100.0
        assert config.spot_detection_mode == SpotDetectionMode.DUAL_RIGHT

    def test_laser_af_config_custom_values(self):
        """Test LaserAFConfig with custom values."""
        config = LaserAFConfig(
            x_offset=100,
            y_offset=200,
            width=1024,
            height=512,
            pixel_to_um=0.5,
            has_reference=True,
            spot_detection_mode="single",
        )
        assert config.x_offset == 100
        assert config.y_offset == 200
        assert config.width == 1024
        assert config.height == 512
        assert config.pixel_to_um == 0.5
        assert config.has_reference is True
        assert config.spot_detection_mode == SpotDetectionMode.SINGLE

    def test_laser_af_config_with_reference_image(self):
        """Test LaserAFConfig with reference image data."""
        config = LaserAFConfig(
            has_reference=True,
            reference_image="base64encodeddata",
            reference_image_shape=[256, 1536],
            reference_image_dtype="float32",
        )
        assert config.reference_image == "base64encodeddata"
        assert config.reference_image_shape == [256, 1536]
        assert config.reference_image_dtype == "float32"

    def test_laser_af_config_spot_detection_mode(self):
        """Test spot detection mode getter."""
        from control._def import SpotDetectionMode

        config = LaserAFConfig(spot_detection_mode="dual_left")
        mode = config.get_spot_detection_mode()
        assert mode == SpotDetectionMode.DUAL_LEFT


class TestMergeChannelConfigs:
    """Tests for merge_channel_configs function."""

    def test_merge_basic(self):
        """Test basic merge of general and objective configs."""
        from control.models import merge_channel_configs

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 488nm"],
                        intensity={"Fluorescence 488nm": 10.0},  # Will be overridden
                        z_offset_um=5.0,  # z_offset is in general
                    ),
                    camera_settings={
                        "1": CameraSettings(
                            display_color="#00FF00",
                            exposure_time_ms=10.0,  # Will be overridden
                            gain_mode=5.0,  # Will be overridden
                        ),
                    },
                    emission_filter_wheel_position={1: 2},
                ),
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=None,  # Not in objective
                        intensity={"Fluorescence 488nm": 25.0},
                        z_offset_um=0.0,  # Ignored, z_offset is in general
                    ),
                    camera_settings={
                        "1": CameraSettings(
                            display_color="#FFFFFF",  # Should be ignored
                            exposure_time_ms=30.0,
                            gain_mode=15.0,
                            pixel_format="Mono12",
                        ),
                    },
                ),
            ],
        )

        merged = merge_channel_configs(general, objective)

        assert len(merged) == 1
        ch = merged[0]

        # From general
        assert ch.illumination_settings.illumination_channels == ["Fluorescence 488nm"]
        assert ch.illumination_settings.z_offset_um == 5.0  # From general
        assert ch.camera_settings["1"].display_color == "#00FF00"
        assert ch.emission_filter_wheel_position == {1: 2}

        # From objective
        assert ch.illumination_settings.intensity["Fluorescence 488nm"] == 25.0
        assert ch.camera_settings["1"].exposure_time_ms == 30.0
        assert ch.camera_settings["1"].gain_mode == 15.0
        assert ch.camera_settings["1"].pixel_format == "Mono12"

    def test_merge_no_objective_override(self):
        """Test merge when objective has no override for a channel."""
        from control.models import merge_channel_configs

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="405 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 405nm"],
                        intensity={"Fluorescence 405nm": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(
                            exposure_time_ms=20.0,
                            gain_mode=10.0,
                        ),
                    },
                ),
            ],
        )

        objective = ObjectiveChannelConfig(version=1, channels=[])

        merged = merge_channel_configs(general, objective)

        # Should use general settings as-is
        assert len(merged) == 1
        ch = merged[0]
        assert ch.name == "405 nm"
        assert ch.illumination_settings.intensity["Fluorescence 405nm"] == 20.0
        assert ch.camera_settings["1"].exposure_time_ms == 20.0

    def test_merge_with_confocal_override(self):
        """Test merge preserves confocal_override from objective."""
        from control.models import merge_channel_configs

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 488nm"],
                        intensity={"Fluorescence 488nm": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                    confocal_settings=ConfocalSettings(
                        filter_wheel_id=1,
                        emission_filter_wheel_position=2,
                    ),
                ),
            ],
        )

        objective = ObjectiveChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        intensity={"Fluorescence 488nm": 30.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=40.0, gain_mode=15.0),
                    },
                    confocal_settings=ConfocalSettings(
                        illumination_iris=50.0,
                        emission_iris=60.0,
                    ),
                    confocal_override=AcquisitionChannelOverride(
                        camera_settings={
                            "1": CameraSettings(exposure_time_ms=80.0, gain_mode=20.0),
                        },
                    ),
                ),
            ],
        )

        merged = merge_channel_configs(general, objective)
        ch = merged[0]

        # Confocal settings should be merged
        assert ch.confocal_settings.filter_wheel_id == 1  # From general
        assert ch.confocal_settings.emission_filter_wheel_position == 2  # From general
        assert ch.confocal_settings.illumination_iris == 50.0  # From objective
        assert ch.confocal_settings.emission_iris == 60.0  # From objective

        # Confocal override should be preserved from objective
        assert ch.confocal_override is not None
        assert ch.confocal_override.camera_settings["1"].exposure_time_ms == 80.0


class TestValidateIlluminationReferences:
    """Tests for validate_illumination_references function."""

    def test_valid_references(self):
        """Test validation passes with valid references."""
        from control.models import validate_illumination_references

        ill_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Fluorescence 488nm",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D2",
                    wavelength_nm=488,
                ),
                IlluminationChannel(
                    name="BF LED full",
                    type=IlluminationType.TRANSILLUMINATION,
                    controller_port="USB1",
                ),
            ],
        )

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 488nm"],
                        intensity={"Fluorescence 488nm": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
                AcquisitionChannel(
                    name="Brightfield",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["BF LED full"],
                        intensity={"BF LED full": 5.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=10.0, gain_mode=5.0),
                    },
                ),
            ],
        )

        errors = validate_illumination_references(general, ill_config)
        assert len(errors) == 0

    def test_invalid_illumination_channel_reference(self):
        """Test validation fails with invalid illumination_channels reference."""
        from control.models import validate_illumination_references

        ill_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Fluorescence 488nm",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D2",
                    wavelength_nm=488,
                ),
            ],
        )

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="561 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 561nm"],  # Does not exist
                        intensity={"Fluorescence 561nm": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        errors = validate_illumination_references(general, ill_config)
        assert len(errors) == 2  # One for illumination_channels, one for intensity
        assert "Fluorescence 561nm" in errors[0]

    def test_invalid_intensity_key(self):
        """Test validation fails with invalid intensity key."""
        from control.models import validate_illumination_references

        ill_config = IlluminationChannelConfig(
            version=1,
            channels=[
                IlluminationChannel(
                    name="Fluorescence 488nm",
                    type=IlluminationType.EPI_ILLUMINATION,
                    controller_port="D2",
                    wavelength_nm=488,
                ),
            ],
        )

        general = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 488nm"],
                        intensity={"Wrong Name": 20.0},  # Wrong key
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
            ],
        )

        errors = validate_illumination_references(general, ill_config)
        assert len(errors) == 1
        assert "Wrong Name" in errors[0]


class TestGetIlluminationChannelNames:
    """Tests for get_illumination_channel_names function."""

    def test_get_names(self):
        """Test extracting illumination channel names from config."""
        from control.models import get_illumination_channel_names

        config = GeneralChannelConfig(
            version=1,
            channels=[
                AcquisitionChannel(
                    name="488 nm",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["Fluorescence 488nm"],
                        intensity={"Fluorescence 488nm": 20.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=20.0, gain_mode=10.0),
                    },
                ),
                AcquisitionChannel(
                    name="Brightfield",
                    illumination_settings=IlluminationSettings(
                        illumination_channels=["BF LED full"],
                        intensity={"BF LED full": 5.0, "BF LED dark field": 10.0},
                    ),
                    camera_settings={
                        "1": CameraSettings(exposure_time_ms=10.0, gain_mode=5.0),
                    },
                ),
            ],
        )

        names = get_illumination_channel_names(config)
        assert "Fluorescence 488nm" in names
        assert "BF LED full" in names
        assert "BF LED dark field" in names
        assert len(names) == 3
