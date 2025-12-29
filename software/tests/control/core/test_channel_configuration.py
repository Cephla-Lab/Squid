"""Tests for the channel configuration system."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from control.utils_config import (
    ChannelType,
    NumericChannelMapping,
    ChannelDefinition,
    ObjectiveChannelSettings,
    ChannelDefinitionsConfig,
    ConfocalOverrides,
)
from control.core.channel_configuration_mananger import (
    ChannelConfigurationManager,
    ConfigType,
)


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


class TestChannelConfigurationManager:
    """Test ChannelConfigurationManager class."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create a temporary directory for configurations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager_with_config(self, temp_config_dir):
        """Create a manager with a default config."""
        # Create default config file
        default_config = ChannelDefinitionsConfig.generate_default()
        default_file = temp_config_dir / "channel_definitions.default.json"
        default_config.save(default_file)

        manager = ChannelConfigurationManager(configurations_path=temp_config_dir)
        return manager

    def test_init_without_config_path(self):
        manager = ChannelConfigurationManager()
        assert manager.channel_definitions is None

    def test_init_with_config_path(self, manager_with_config, temp_config_dir):
        assert manager_with_config.channel_definitions is not None
        # User file should be created from default
        user_file = temp_config_dir / "channel_definitions.json"
        assert user_file.exists()

    def test_load_creates_user_file_from_default(self, temp_config_dir):
        # Create only default file
        default_config = ChannelDefinitionsConfig.generate_default()
        default_file = temp_config_dir / "channel_definitions.default.json"
        default_config.save(default_file)

        user_file = temp_config_dir / "channel_definitions.json"
        assert not user_file.exists()

        manager = ChannelConfigurationManager(configurations_path=temp_config_dir)

        assert user_file.exists()
        assert manager.channel_definitions is not None

    def test_save_channel_definitions(self, manager_with_config, temp_config_dir):
        # Modify config
        manager_with_config.channel_definitions.max_fluorescence_channels = 7
        manager_with_config.save_channel_definitions()

        # Reload and verify
        user_file = temp_config_dir / "channel_definitions.json"
        with open(user_file) as f:
            data = json.load(f)
        assert data["max_fluorescence_channels"] == 7

    def test_get_channel_definitions(self, manager_with_config):
        definitions = manager_with_config.get_channel_definitions()
        assert definitions is not None
        assert isinstance(definitions, ChannelDefinitionsConfig)

    def test_add_channel_definition(self, manager_with_config):
        initial_count = len(manager_with_config.channel_definitions.channels)

        new_channel = ChannelDefinition(
            name="New Test Channel",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=3,
        )
        manager_with_config.add_channel_definition(new_channel)

        assert len(manager_with_config.channel_definitions.channels) == initial_count + 1

    def test_remove_channel_definition(self, manager_with_config):
        # Add a channel first
        new_channel = ChannelDefinition(
            name="Channel To Remove",
            type=ChannelType.FLUORESCENCE,
            numeric_channel=3,
        )
        manager_with_config.add_channel_definition(new_channel)
        count_after_add = len(manager_with_config.channel_definitions.channels)

        manager_with_config.remove_channel_definition("Channel To Remove")

        assert len(manager_with_config.channel_definitions.channels) == count_after_add - 1
        assert manager_with_config.channel_definitions.get_channel_by_name("Channel To Remove") is None

    def test_set_channel_enabled(self, manager_with_config):
        channel_name = manager_with_config.channel_definitions.channels[0].name
        manager_with_config.set_channel_enabled(channel_name, False)

        channel = manager_with_config.channel_definitions.get_channel_by_name(channel_name)
        assert channel.enabled is False

    def test_get_enabled_configurations(self, manager_with_config, temp_config_dir):
        # Set up profile path
        profile_path = temp_config_dir / "profiles" / "default"
        profile_path.mkdir(parents=True)
        manager_with_config.set_profile_path(profile_path)

        # Disable one channel
        channel_name = manager_with_config.channel_definitions.channels[0].name
        manager_with_config.set_channel_enabled(channel_name, False)

        all_configs = manager_with_config.get_configurations("10x")
        enabled_configs = manager_with_config.get_enabled_configurations("10x")

        assert len(enabled_configs) < len(all_configs)

    def test_channel_id_stability(self, manager_with_config, temp_config_dir):
        """Test that channel IDs are stable across sessions."""
        profile_path = temp_config_dir / "profiles" / "default"
        profile_path.mkdir(parents=True)
        manager_with_config.set_profile_path(profile_path)

        configs1 = manager_with_config.get_configurations("10x")
        id_map1 = {c.name: c.id for c in configs1}

        # Create new manager (simulating new session)
        manager2 = ChannelConfigurationManager(configurations_path=temp_config_dir)
        manager2.set_profile_path(profile_path)

        configs2 = manager2.get_configurations("10x")
        id_map2 = {c.name: c.id for c in configs2}

        # IDs should be the same
        for name in id_map1:
            assert id_map1[name] == id_map2[name], f"ID mismatch for {name}"

    def test_confocal_mode_default_is_false(self, manager_with_config):
        """Test that confocal mode defaults to False."""
        assert manager_with_config.confocal_mode is False
        assert manager_with_config.is_confocal_mode() is False

    def test_toggle_confocal_widefield(self, manager_with_config):
        """Test toggling between confocal and widefield modes."""
        assert manager_with_config.confocal_mode is False

        manager_with_config.toggle_confocal_widefield(True)
        assert manager_with_config.confocal_mode is True
        assert manager_with_config.is_confocal_mode() is True
        assert manager_with_config.active_config_type == ConfigType.CONFOCAL

        manager_with_config.toggle_confocal_widefield(False)
        assert manager_with_config.confocal_mode is False
        assert manager_with_config.is_confocal_mode() is False
        assert manager_with_config.active_config_type == ConfigType.WIDEFIELD

    def test_confocal_mode_affects_channel_settings(self, manager_with_config, temp_config_dir):
        """Test that confocal mode uses confocal overrides for channel settings."""
        profile_path = temp_config_dir / "profiles" / "default"
        objective_path = profile_path / "10x"
        objective_path.mkdir(parents=True)
        manager_with_config.set_profile_path(profile_path)

        # Create settings with confocal overrides
        channel_name = manager_with_config.channel_definitions.channels[0].name
        settings_with_confocal = {
            channel_name: {
                "exposure_time": 25.0,
                "analog_gain": 5.0,
                "illumination_intensity": 20.0,
                "z_offset": 0.0,
                "confocal": {
                    "exposure_time": 100.0,
                    "illumination_intensity": 50.0,
                },
            }
        }
        settings_file = objective_path / "channel_settings.json"
        settings_file.write_text(json.dumps(settings_with_confocal))

        # Load configurations
        manager_with_config.load_configurations("10x")

        # Get configurations in widefield mode
        manager_with_config.toggle_confocal_widefield(False)
        widefield_configs = manager_with_config.get_configurations("10x")
        widefield_channel = next(c for c in widefield_configs if c.name == channel_name)
        assert widefield_channel.exposure_time == 25.0
        assert widefield_channel.illumination_intensity == 20.0

        # Get configurations in confocal mode
        manager_with_config.toggle_confocal_widefield(True)
        confocal_configs = manager_with_config.get_configurations("10x")
        confocal_channel = next(c for c in confocal_configs if c.name == channel_name)
        assert confocal_channel.exposure_time == 100.0
        assert confocal_channel.illumination_intensity == 50.0
        # Non-overridden value should inherit from base
        assert confocal_channel.analog_gain == 5.0

    def test_update_configuration_in_confocal_mode(self, manager_with_config, temp_config_dir):
        """Test that updates in confocal mode go to confocal overrides."""
        profile_path = temp_config_dir / "profiles" / "default"
        objective_path = profile_path / "10x"
        objective_path.mkdir(parents=True)
        manager_with_config.set_profile_path(profile_path)

        # Initialize settings
        channel_name = manager_with_config.channel_definitions.channels[0].name
        manager_with_config.objective_settings["10x"] = {channel_name: ObjectiveChannelSettings(exposure_time=25.0)}

        # Get channel ID
        configs = manager_with_config.get_configurations("10x")
        channel = next(c for c in configs if c.name == channel_name)

        # Update in confocal mode
        manager_with_config.toggle_confocal_widefield(True)
        manager_with_config.update_configuration("10x", channel.id, "ExposureTime", 100.0)

        # Verify the update went to confocal overrides
        settings = manager_with_config.objective_settings["10x"][channel_name]
        assert settings.exposure_time == 25.0  # Base unchanged
        assert settings.confocal is not None
        assert settings.confocal.exposure_time == 100.0

    def test_update_configuration_in_widefield_mode(self, manager_with_config, temp_config_dir):
        """Test that updates in widefield mode go to base settings."""
        profile_path = temp_config_dir / "profiles" / "default"
        objective_path = profile_path / "10x"
        objective_path.mkdir(parents=True)
        manager_with_config.set_profile_path(profile_path)

        # Initialize settings
        channel_name = manager_with_config.channel_definitions.channels[0].name
        manager_with_config.objective_settings["10x"] = {channel_name: ObjectiveChannelSettings(exposure_time=25.0)}

        # Get channel ID
        configs = manager_with_config.get_configurations("10x")
        channel = next(c for c in configs if c.name == channel_name)

        # Update in widefield mode
        manager_with_config.toggle_confocal_widefield(False)
        manager_with_config.update_configuration("10x", channel.id, "ExposureTime", 50.0)

        # Verify the update went to base settings
        settings = manager_with_config.objective_settings["10x"][channel_name]
        assert settings.exposure_time == 50.0  # Base changed
        assert settings.confocal is None  # No confocal overrides created


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


class TestMigrationAndCleanup:
    """Test migration and cleanup functions."""

    @pytest.fixture
    def temp_config_dir(self):
        """Create a temporary directory for configurations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_acquisition_configs(self):
        """Create a temporary acquisition_configurations structure with XML files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)

            # Create profile/objective structure with XML files
            for profile in ["default_profile", "profile2"]:
                for objective in ["10x", "20x"]:
                    obj_dir = base_path / profile / objective
                    obj_dir.mkdir(parents=True)

                    # Create a mock XML config file
                    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<modes>
    <mode ID="1" Name="Test Channel" ExposureTime="50.0" AnalogGain="2.0"
          IlluminationSource="11" IlluminationIntensity="30.0" CameraSN=""
          ZOffset="0.5" EmissionFilterPosition="1" Selected="false"/>
</modes>"""
                    (obj_dir / "channel_configurations.xml").write_text(xml_content)

            yield base_path

    @pytest.fixture
    def manager_for_migration(self, temp_config_dir):
        """Create a manager for migration testing."""
        default_config = ChannelDefinitionsConfig.generate_default()
        default_file = temp_config_dir / "channel_definitions.default.json"
        default_config.save(default_file)

        manager = ChannelConfigurationManager(configurations_path=temp_config_dir)
        return manager

    def test_migrate_all_profiles(self, manager_for_migration, temp_acquisition_configs):
        """Test that migrate_all_profiles creates JSON files from XML."""
        # Verify no JSON files and no marker file exist initially
        for profile_dir in temp_acquisition_configs.iterdir():
            if not profile_dir.is_dir():
                continue
            for obj_dir in profile_dir.iterdir():
                if not obj_dir.is_dir():
                    continue
                assert not (obj_dir / "channel_settings.json").exists()
                assert (obj_dir / "channel_configurations.xml").exists()

        # Run migration
        manager_for_migration.migrate_all_profiles(temp_acquisition_configs)

        # Verify JSON files now exist
        for profile_dir in temp_acquisition_configs.iterdir():
            if not profile_dir.is_dir():
                continue
            for obj_dir in profile_dir.iterdir():
                if not obj_dir.is_dir():
                    continue
                json_file = obj_dir / "channel_settings.json"
                assert json_file.exists(), f"JSON file not created: {json_file}"

        # Verify marker file was created
        assert (temp_acquisition_configs / ".migration_complete").exists()

    def test_migrate_all_profiles_skips_existing_json(self, manager_for_migration, temp_acquisition_configs):
        """Test that migration doesn't overwrite existing JSON files."""
        # Create a JSON file with custom content
        obj_dir = temp_acquisition_configs / "default_profile" / "10x"
        json_file = obj_dir / "channel_settings.json"
        json_file.write_text('{"Custom Channel": {"exposure_time": 999.0}}')

        # Run migration
        manager_for_migration.migrate_all_profiles(temp_acquisition_configs)

        # Verify custom content is preserved
        content = json.loads(json_file.read_text())
        assert "Custom Channel" in content
        assert content["Custom Channel"]["exposure_time"] == 999.0

    def test_migrate_all_profiles_handles_errors(self, manager_for_migration, temp_acquisition_configs):
        """Test that migration continues after encountering errors."""
        # Create an invalid XML file
        bad_xml = temp_acquisition_configs / "default_profile" / "10x" / "channel_configurations.xml"
        bad_xml.write_text("not valid xml <><>")

        # Migration should not raise, just log warning
        manager_for_migration.migrate_all_profiles(temp_acquisition_configs)

        # Other profiles should still be migrated
        good_json = temp_acquisition_configs / "profile2" / "10x" / "channel_settings.json"
        assert good_json.exists()

    def test_cleanup_orphaned_settings(self, manager_for_migration, temp_acquisition_configs):
        """Test that orphaned settings are cleaned up when channel is deleted."""
        # First, create JSON settings files with a channel
        for profile_dir in temp_acquisition_configs.iterdir():
            if not profile_dir.is_dir():
                continue
            for obj_dir in profile_dir.iterdir():
                if not obj_dir.is_dir():
                    continue
                settings = {
                    "Channel To Delete": {"exposure_time": 100.0, "analog_gain": 1.0},
                    "Channel To Keep": {"exposure_time": 50.0, "analog_gain": 0.5},
                }
                (obj_dir / "channel_settings.json").write_text(json.dumps(settings))

        # Add channel to definitions so we can remove it
        manager_for_migration.channel_definitions.channels.append(
            ChannelDefinition(name="Channel To Delete", type=ChannelType.FLUORESCENCE, numeric_channel=1)
        )

        # Remove channel with cleanup
        manager_for_migration.remove_channel_definition("Channel To Delete", base_config_path=temp_acquisition_configs)

        # Verify orphaned settings are removed
        for profile_dir in temp_acquisition_configs.iterdir():
            if not profile_dir.is_dir():
                continue
            for obj_dir in profile_dir.iterdir():
                if not obj_dir.is_dir():
                    continue
                settings = json.loads((obj_dir / "channel_settings.json").read_text())
                assert "Channel To Delete" not in settings
                assert "Channel To Keep" in settings
