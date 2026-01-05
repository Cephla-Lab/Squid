"""Tests for the channel configuration system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from control.utils_config import (
    ChannelType,
    NumericChannelMapping,
    ChannelDefinition,
    ObjectiveChannelSettings,
    ChannelDefinitionsConfig,
    ConfocalOverrides,
)
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.models import (
    IlluminationChannel,
    IlluminationChannelConfig,
    AcquisitionChannel,
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    CameraSettings,
    IlluminationSettings,
)
from control.models.illumination_config import IlluminationType


class TestChannelType:
    """Test ChannelType enum."""

    def test_fluorescence_value(self):
        assert ChannelType.FLUORESCENCE.value == "fluorescence"

    def test_led_matrix_value(self):
        assert ChannelType.LED_MATRIX.value == "led_matrix"


class TestNumericChannelMapping:
    """Test NumericChannelMapping model."""

    def test_create_mapping(self):
        mapping = NumericChannelMapping(illumination_source=11, ex_wavelength=405)
        assert mapping.illumination_source == 11
        assert mapping.ex_wavelength == 405

    def test_mapping_serialization(self):
        mapping = NumericChannelMapping(illumination_source=12, ex_wavelength=488)
        data = mapping.model_dump()
        assert data == {"illumination_source": 12, "ex_wavelength": 488}


class TestChannelDefinition:
    """Test ChannelDefinition model."""

    def test_create_fluorescence_channel(self):
        channel = ChannelDefinition(
            name="Fluorescence 488 nm Ex",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=2,
            emission_filter_position=1,
            display_color="#1FFF00",
        )
        assert channel.name == "Fluorescence 488 nm Ex"
        assert channel.type == ChannelType.FLUORESCENCE
        assert channel.numeric_channel == 2
        assert channel.enabled is True  # default

    def test_create_led_matrix_channel(self):
        channel = ChannelDefinition(
            name="BF LED matrix full",
            type=ChannelType.LED_MATRIX,
            illumination_source=0,
        )
        assert channel.name == "BF LED matrix full"
        assert channel.type == ChannelType.LED_MATRIX
        assert channel.illumination_source == 0

    def test_fluorescence_requires_numeric_channel(self):
        with pytest.raises(ValueError, match="must have numeric_channel set"):
            ChannelDefinition(
                name="Test",
                type=ChannelType.FLUORESCENCE,
                # numeric_channel missing
            )

    def test_led_matrix_requires_illumination_source(self):
        with pytest.raises(ValueError, match="must have illumination_source set"):
            ChannelDefinition(
                name="Test",
                type=ChannelType.LED_MATRIX,
                # illumination_source missing
            )

    def test_color_conversion_from_int(self):
        channel = ChannelDefinition(
            name="Test",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=1,
            display_color=0xFF0000,  # int format
        )
        assert channel.display_color == "#FF0000"

    def test_get_illumination_source_fluorescence(self):
        channel = ChannelDefinition(
            name="Test",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=2,
        )
        mapping = {"2": NumericChannelMapping(illumination_source=12, ex_wavelength=488)}
        assert channel.get_illumination_source(mapping) == 12

    def test_get_illumination_source_led_matrix(self):
        channel = ChannelDefinition(
            name="Test",
            type=ChannelType.LED_MATRIX,
            illumination_source=3,
        )
        assert channel.get_illumination_source({}) == 3

    def test_get_ex_wavelength_fluorescence(self):
        channel = ChannelDefinition(
            name="Test",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=2,
        )
        mapping = {"2": NumericChannelMapping(illumination_source=12, ex_wavelength=488)}
        assert channel.get_ex_wavelength(mapping) == 488

    def test_get_ex_wavelength_led_matrix_returns_none(self):
        channel = ChannelDefinition(
            name="Test",
            type=ChannelType.LED_MATRIX,
            illumination_source=0,
        )
        assert channel.get_ex_wavelength({}) is None

    def test_name_validation_empty_name_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ChannelDefinition(
                name="",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
            )

    def test_name_validation_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ChannelDefinition(
                name="   ",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
            )

    def test_name_validation_too_long_rejected(self):
        long_name = "A" * 65  # exceeds 64 char limit
        with pytest.raises(ValueError, match="exceeds maximum length"):
            ChannelDefinition(
                name=long_name,
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
            )

    def test_name_validation_invalid_chars_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            ChannelDefinition(
                name="Test<Channel>",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
            )

    def test_name_validation_valid_name_accepted(self):
        channel = ChannelDefinition(
            name="Fluorescence 488 nm Ex",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=1,
        )
        assert channel.name == "Fluorescence 488 nm Ex"


class TestConfocalOverrides:
    """Test ConfocalOverrides model."""

    def test_default_values_are_none(self):
        overrides = ConfocalOverrides()
        assert overrides.exposure_time is None
        assert overrides.analog_gain is None
        assert overrides.illumination_intensity is None
        assert overrides.z_offset is None

    def test_partial_overrides(self):
        overrides = ConfocalOverrides(
            exposure_time=100.0,
            illumination_intensity=50.0,
        )
        assert overrides.exposure_time == 100.0
        assert overrides.analog_gain is None
        assert overrides.illumination_intensity == 50.0
        assert overrides.z_offset is None

    def test_serialization(self):
        overrides = ConfocalOverrides(exposure_time=100.0)
        data = overrides.model_dump()
        assert data["exposure_time"] == 100.0
        assert data["analog_gain"] is None


class TestObjectiveChannelSettings:
    """Test ObjectiveChannelSettings model."""

    def test_default_values(self):
        settings = ObjectiveChannelSettings()
        assert settings.exposure_time == 25.0
        assert settings.analog_gain == 0.0
        assert settings.illumination_intensity == 20.0
        assert settings.z_offset == 0.0
        assert settings.confocal is None

    def test_custom_values(self):
        settings = ObjectiveChannelSettings(
            exposure_time=100.0,
            analog_gain=5.0,
            illumination_intensity=50.0,
            z_offset=1.5,
        )
        assert settings.exposure_time == 100.0
        assert settings.analog_gain == 5.0

    def test_with_confocal_overrides(self):
        settings = ObjectiveChannelSettings(
            exposure_time=25.0,
            analog_gain=0.0,
            confocal=ConfocalOverrides(
                exposure_time=100.0,
                illumination_intensity=50.0,
            ),
        )
        assert settings.confocal is not None
        assert settings.confocal.exposure_time == 100.0
        assert settings.confocal.analog_gain is None

    def test_get_effective_settings_widefield_mode(self):
        """Test that widefield mode returns base settings."""
        settings = ObjectiveChannelSettings(
            exposure_time=25.0,
            analog_gain=5.0,
            confocal=ConfocalOverrides(
                exposure_time=100.0,
            ),
        )
        effective = settings.get_effective_settings(confocal_mode=False)
        assert effective.exposure_time == 25.0
        assert effective.analog_gain == 5.0

    def test_get_effective_settings_confocal_mode(self):
        """Test that confocal mode applies overrides."""
        settings = ObjectiveChannelSettings(
            exposure_time=25.0,
            analog_gain=5.0,
            illumination_intensity=20.0,
            confocal=ConfocalOverrides(
                exposure_time=100.0,
                illumination_intensity=50.0,
            ),
        )
        effective = settings.get_effective_settings(confocal_mode=True)
        # Overridden values
        assert effective.exposure_time == 100.0
        assert effective.illumination_intensity == 50.0
        # Non-overridden values (inherit from base)
        assert effective.analog_gain == 5.0

    def test_get_effective_settings_confocal_mode_no_overrides(self):
        """Test confocal mode with no overrides returns base settings."""
        settings = ObjectiveChannelSettings(
            exposure_time=25.0,
            analog_gain=5.0,
        )
        effective = settings.get_effective_settings(confocal_mode=True)
        assert effective.exposure_time == 25.0
        assert effective.analog_gain == 5.0

    def test_serialization_with_confocal(self):
        """Test that confocal overrides serialize correctly."""
        settings = ObjectiveChannelSettings(
            exposure_time=25.0,
            confocal=ConfocalOverrides(exposure_time=100.0),
        )
        data = settings.model_dump()
        assert data["confocal"]["exposure_time"] == 100.0
        assert data["confocal"]["analog_gain"] is None


class TestChannelDefinitionsConfig:
    """Test ChannelDefinitionsConfig model."""

    @pytest.fixture
    def sample_config(self):
        return ChannelDefinitionsConfig(
            max_fluorescence_channels=5,
            channels=[
                ChannelDefinition(
                    name="Fluorescence 488 nm Ex",
                    type=ChannelType.FLUORESCENCE,
                    numeric_channel=2,
                    enabled=True,
                ),
                ChannelDefinition(
                    name="BF LED matrix full",
                    type=ChannelType.LED_MATRIX,
                    illumination_source=0,
                    enabled=True,
                ),
                ChannelDefinition(
                    name="Disabled Channel",
                    type=ChannelType.FLUORESCENCE,
                    numeric_channel=1,
                    enabled=False,
                ),
            ],
            numeric_channel_mapping={
                "1": NumericChannelMapping(illumination_source=11, ex_wavelength=405),
                "2": NumericChannelMapping(illumination_source=12, ex_wavelength=488),
            },
        )

    def test_get_enabled_channels(self, sample_config):
        enabled = sample_config.get_enabled_channels()
        assert len(enabled) == 2
        assert all(ch.enabled for ch in enabled)

    def test_get_channel_by_name(self, sample_config):
        channel = sample_config.get_channel_by_name("BF LED matrix full")
        assert channel is not None
        assert channel.type == ChannelType.LED_MATRIX

    def test_get_channel_by_name_not_found(self, sample_config):
        channel = sample_config.get_channel_by_name("Nonexistent")
        assert channel is None

    def test_save_and_load(self, sample_config):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            filepath = Path(f.name)

        try:
            sample_config.save(filepath)
            loaded = ChannelDefinitionsConfig.load(filepath)

            assert loaded.max_fluorescence_channels == 5
            assert len(loaded.channels) == 3
            assert len(loaded.numeric_channel_mapping) == 2
        finally:
            filepath.unlink()

    def test_generate_default(self):
        config = ChannelDefinitionsConfig.generate_default()
        assert config.max_fluorescence_channels == 5
        assert len(config.channels) > 0
        assert len(config.numeric_channel_mapping) == 5

        # Check both channel types exist
        types = {ch.type for ch in config.channels}
        assert ChannelType.FLUORESCENCE in types
        assert ChannelType.LED_MATRIX in types


def _create_test_configs(base_path: Path) -> tuple:
    """Helper to create test illumination and acquisition configs."""
    machine_configs = base_path / "machine_configs"
    machine_configs.mkdir(parents=True, exist_ok=True)

    # Create illumination config
    ill_config = IlluminationChannelConfig(
        version=1,
        controller_port_mapping={"USB1": 0, "D1": 11, "D2": 12},
        channels=[
            IlluminationChannel(
                name="BF LED full",
                type=IlluminationType.TRANSILLUMINATION,
                wavelength_nm=None,
                controller_port="USB1",
            ),
            IlluminationChannel(
                name="405 nm Laser",
                type=IlluminationType.EPI_ILLUMINATION,
                wavelength_nm=405,
                controller_port="D1",
            ),
            IlluminationChannel(
                name="488 nm Laser",
                type=IlluminationType.EPI_ILLUMINATION,
                wavelength_nm=488,
                controller_port="D2",
            ),
        ],
    )

    # Create general config
    general_config = GeneralChannelConfig(
        version=1,
        channels=[
            AcquisitionChannel(
                name="Brightfield",
                illumination_settings=IlluminationSettings(
                    illumination_channels=["BF LED full"],
                    intensity={"BF LED full": 5.0},
                    z_offset_um=0.0,
                ),
                camera_settings={
                    "1": CameraSettings(
                        display_color="#FFFFFF",
                        exposure_time_ms=20.0,
                        gain_mode=10.0,
                    )
                },
                emission_filter_wheel_position={1: 1},
            ),
            AcquisitionChannel(
                name="488 nm",
                illumination_settings=IlluminationSettings(
                    illumination_channels=["488 nm Laser"],
                    intensity={"488 nm Laser": 20.0},
                    z_offset_um=0.0,
                ),
                camera_settings={
                    "1": CameraSettings(
                        display_color="#00FF00",
                        exposure_time_ms=25.0,
                        gain_mode=10.0,
                    )
                },
                emission_filter_wheel_position={1: 2},
            ),
        ],
    )

    # Create objective config
    objective_config = ObjectiveChannelConfig(
        version=1,
        channels=[
            AcquisitionChannel(
                name="Brightfield",
                illumination_settings=IlluminationSettings(
                    illumination_channels=None,
                    intensity={"BF LED full": 10.0},
                    z_offset_um=1.0,
                ),
                camera_settings={
                    "1": CameraSettings(
                        display_color="#FFFFFF",
                        exposure_time_ms=30.0,
                        gain_mode=15.0,
                        pixel_format="Mono12",
                    )
                },
            ),
            AcquisitionChannel(
                name="488 nm",
                illumination_settings=IlluminationSettings(
                    illumination_channels=None,
                    intensity={"488 nm Laser": 30.0},
                    z_offset_um=2.0,
                ),
                camera_settings={
                    "1": CameraSettings(
                        display_color="#00FF00",
                        exposure_time_ms=50.0,
                        gain_mode=20.0,
                        pixel_format="Mono12",
                    )
                },
            ),
        ],
    )

    return ill_config, general_config, objective_config


class TestChannelConfigurationManagerYAML:
    """Test ChannelConfigurationManager with YAML configs."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create a temporary directory for configurations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager_with_yaml(self, temp_config_dir):
        """Create a manager with YAML configs."""
        ill_config, general_config, objective_config = _create_test_configs(temp_config_dir)

        with patch("control.core.channel_configuration_mananger.ConfigLoader") as MockLoader:
            mock_loader = MockLoader.return_value
            mock_loader.load_illumination_config.return_value = ill_config
            mock_loader.load_general_config.return_value = general_config
            mock_loader.load_objective_config.return_value = objective_config
            mock_loader.save_objective_config = MagicMock()

            manager = ChannelConfigurationManager()
            manager.set_profile("default")
            return manager

    def test_init_without_config(self):
        """Test that manager without config has no general config."""
        manager = ChannelConfigurationManager()
        assert manager._general_config is None
        assert not manager.has_yaml_configs()

    def test_set_profile_loads_configs(self, manager_with_yaml):
        """Test that set_profile loads general config."""
        assert manager_with_yaml._general_config is not None
        assert manager_with_yaml.has_yaml_configs()
        assert len(manager_with_yaml._general_config.channels) == 2

    def test_get_configurations(self, manager_with_yaml):
        """Test getting channel modes from YAML config."""
        configs = manager_with_yaml.get_configurations("10x")

        assert len(configs) == 2
        assert configs[0].name == "Brightfield"
        assert configs[1].name == "488 nm"

        # Check merged values (from objective config)
        bf_config = configs[0]
        assert bf_config.exposure_time == 30.0  # From objective
        assert bf_config.illumination_intensity == 10.0  # From objective

    def test_get_channel_configuration_by_name(self, manager_with_yaml):
        """Test getting a specific channel by name."""
        config = manager_with_yaml.get_channel_configuration_by_name("10x", "488 nm")
        assert config is not None
        assert config.name == "488 nm"

        missing = manager_with_yaml.get_channel_configuration_by_name("10x", "Nonexistent")
        assert missing is None

    def test_confocal_mode_default_false(self, manager_with_yaml):
        """Test that confocal mode defaults to False."""
        assert manager_with_yaml.confocal_mode is False
        assert manager_with_yaml.is_confocal_mode() is False

    def test_toggle_confocal_widefield(self, manager_with_yaml):
        """Test toggling confocal mode."""
        manager_with_yaml.toggle_confocal_widefield(True)
        assert manager_with_yaml.confocal_mode is True

        manager_with_yaml.toggle_confocal_widefield(False)
        assert manager_with_yaml.confocal_mode is False

    def test_update_configuration(self, manager_with_yaml):
        """Test updating a configuration attribute."""
        configs = manager_with_yaml.get_configurations("10x")
        bf_config = next(c for c in configs if c.name == "Brightfield")

        # Update exposure time
        manager_with_yaml.update_configuration("10x", bf_config.id, "ExposureTime", 100.0)

        # Verify update
        updated_configs = manager_with_yaml.get_configurations("10x")
        updated_bf = next(c for c in updated_configs if c.name == "Brightfield")
        assert updated_bf.exposure_time == 100.0

    def test_write_configuration_selected(self, manager_with_yaml, temp_config_dir):
        """Test writing selected configurations to YAML."""
        configs = manager_with_yaml.get_configurations("10x")
        output_dir = temp_config_dir / "acquisition"
        output_dir.mkdir()

        # filename is used to determine output directory
        dummy_path = output_dir / "dummy_filename.xml"
        manager_with_yaml.write_configuration_selected("10x", configs, str(dummy_path))

        # Check YAML was written
        yaml_path = output_dir / "acquisition_channels.yaml"
        assert yaml_path.exists()

        # Verify YAML content
        with open(yaml_path) as f:
            yaml_data = yaml.safe_load(f)
        assert yaml_data["version"] == 1
        assert yaml_data["objective"] == "10x"
        assert len(yaml_data["channels"]) == 2


class TestChannelDefinitionValidation:
    """Test validation edge cases."""

    def test_led_matrix_with_null_numeric_channel_is_valid(self):
        channel = ChannelDefinition(
            name="Test LED",
            type=ChannelType.LED_MATRIX,
            illumination_source=0,
            numeric_channel=None,
        )
        assert channel.numeric_channel is None

    def test_fluorescence_with_null_illumination_source_is_valid(self):
        channel = ChannelDefinition(
            name="Test Fluorescence",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=1,
            illumination_source=None,
        )
        assert channel.illumination_source is None

    def test_invalid_numeric_channel_mapping_raises_at_load(self):
        """Test that invalid numeric_channel mapping is caught at config load time."""
        from pydantic import ValidationError

        # Create config with fluorescence channel referencing non-existent mapping
        with pytest.raises(ValidationError) as exc_info:
            ChannelDefinitionsConfig(
                channels=[
                    ChannelDefinition(
                        name="Test Fluorescence",
                        type=ChannelType.FLUORESCENCE,
                        numeric_channel=99,  # No mapping for this
                    )
                ],
                numeric_channel_mapping={"1": {"illumination_source": 11, "ex_wavelength": 488}},
            )
        assert "numeric_channel 99" in str(exc_info.value)
        assert "no mapping exists" in str(exc_info.value)

    def test_valid_numeric_channel_mapping_passes(self):
        """Test that valid numeric_channel mapping passes validation."""
        config = ChannelDefinitionsConfig(
            channels=[
                ChannelDefinition(
                    name="Test Fluorescence",
                    type=ChannelType.FLUORESCENCE,
                    numeric_channel=1,
                )
            ],
            numeric_channel_mapping={"1": {"illumination_source": 11, "ex_wavelength": 488}},
        )
        assert len(config.channels) == 1
