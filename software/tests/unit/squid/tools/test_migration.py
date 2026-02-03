"""Tests for JSON/XML → YAML config migration."""

import json

import pytest
import yaml

from tools.migrate_to_yaml_configs import (
    migrate,
    migrate_channel_definitions,
    migrate_profile,
)


@pytest.fixture
def software_dir(tmp_path):
    """Create a mock software/ directory with JSON configs."""
    # Create configurations/channel_definitions.json
    configs = tmp_path / "configurations"
    configs.mkdir()

    channel_defs = {
        "max_fluorescence_channels": 5,
        "channels": [
            {
                "name": "BF LED matrix full",
                "type": "led_matrix",
                "emission_filter_position": 1,
                "display_color": "#FFFFFF",
                "enabled": True,
                "numeric_channel": None,
                "illumination_source": 0,
                "ex_wavelength": None,
            },
            {
                "name": "Fluorescence 405 nm Ex",
                "type": "fluorescence",
                "emission_filter_position": 2,
                "display_color": "#20ADF8",
                "enabled": True,
                "numeric_channel": 1,
                "illumination_source": None,
                "ex_wavelength": None,
            },
            {
                "name": "Fluorescence 488 nm Ex",
                "type": "fluorescence",
                "emission_filter_position": 3,
                "display_color": "#1FFF00",
                "enabled": True,
                "numeric_channel": 2,
                "illumination_source": None,
                "ex_wavelength": None,
            },
        ],
        "numeric_channel_mapping": {
            "1": {"illumination_source": 11, "ex_wavelength": 405},
            "2": {"illumination_source": 12, "ex_wavelength": 488},
        },
    }
    with open(configs / "channel_definitions.json", "w") as f:
        json.dump(channel_defs, f)

    # Create acquisition_configurations/default_profile/10x/
    acq = tmp_path / "acquisition_configurations" / "default_profile"
    (acq / "10x").mkdir(parents=True)
    (acq / "20x").mkdir(parents=True)

    settings_10x = {
        "BF LED matrix full": {
            "exposure_time": 12.0,
            "analog_gain": 0.0,
            "illumination_intensity": 5.0,
            "z_offset": 0.0,
            "confocal": None,
        },
        "Fluorescence 405 nm Ex": {
            "exposure_time": 100.0,
            "analog_gain": 10.0,
            "illumination_intensity": 50.0,
            "z_offset": 1.5,
            "confocal": None,
        },
        "Fluorescence 488 nm Ex": {
            "exposure_time": 25.0,
            "analog_gain": 10.0,
            "illumination_intensity": 20.0,
            "z_offset": 0.0,
            "confocal": None,
        },
    }
    with open(acq / "10x" / "channel_settings.json", "w") as f:
        json.dump(settings_10x, f)

    settings_20x = {
        "BF LED matrix full": {
            "exposure_time": 8.0,
            "analog_gain": 0.0,
            "illumination_intensity": 3.0,
            "z_offset": 0.0,
            "confocal": None,
        },
        "Fluorescence 405 nm Ex": {
            "exposure_time": 50.0,
            "analog_gain": 5.0,
            "illumination_intensity": 30.0,
            "z_offset": 0.0,
            "confocal": {
                "exposure_time": 200.0,
                "analog_gain": None,
                "illumination_intensity": 80.0,
                "z_offset": None,
            },
        },
    }
    with open(acq / "20x" / "channel_settings.json", "w") as f:
        json.dump(settings_20x, f)

    # Create laser_af_settings for 20x
    laser_af = {"has_reference": False, "x_offset": 10, "y_offset": 20}
    with open(acq / "20x" / "laser_af_settings.json", "w") as f:
        json.dump(laser_af, f)

    # Create target directories
    (tmp_path / "machine_configs").mkdir(exist_ok=True)
    (tmp_path / "user_profiles").mkdir(exist_ok=True)

    return tmp_path


class TestMigrateChannelDefinitions:
    def test_creates_illumination_yaml(self, software_dir):
        defs = software_dir / "configurations" / "channel_definitions.json"
        output = software_dir / "machine_configs" / "illumination_channel_config.yaml"

        result = migrate_channel_definitions(defs, output)
        assert result is True
        assert output.exists()

        with open(output) as f:
            data = yaml.safe_load(f)

        assert "channels" in data
        assert len(data["channels"]) == 3

        # Check BF LED
        bf = data["channels"][0]
        assert bf["name"] == "BF LED matrix full"
        assert bf["type"] == "transillumination"
        assert bf["controller_port"] == "USB1"
        assert "wavelength_nm" not in bf

        # Check fluorescence 405
        fl405 = data["channels"][1]
        assert fl405["name"] == "Fluorescence 405 nm Ex"
        assert fl405["type"] == "epi_illumination"
        assert fl405["controller_port"] == "D1"
        assert fl405["wavelength_nm"] == 405

    def test_skips_if_already_exists(self, software_dir):
        output = software_dir / "machine_configs" / "illumination_channel_config.yaml"
        output.write_text("existing: true")

        result = migrate_channel_definitions(
            software_dir / "configurations" / "channel_definitions.json",
            output,
        )
        assert result is False


class TestMigrateProfile:
    def test_creates_general_and_objectives(self, software_dir):
        profile_path = software_dir / "acquisition_configurations" / "default_profile"
        output_path = software_dir / "user_profiles" / "default_profile"

        migrated, skipped = migrate_profile(profile_path, output_path)
        assert migrated == 2
        assert skipped == 0

        # Check general.yaml
        general_path = output_path / "channel_configs" / "general.yaml"
        assert general_path.exists()
        with open(general_path) as f:
            general = yaml.safe_load(f)
        assert len(general["channels"]) == 3
        assert general["channels"][0]["name"] == "BF LED matrix full"

        # Check 10x.yaml
        obj_path = output_path / "channel_configs" / "10x.yaml"
        assert obj_path.exists()
        with open(obj_path) as f:
            obj = yaml.safe_load(f)
        assert len(obj["channels"]) == 3
        fl405 = next(ch for ch in obj["channels"] if ch["name"] == "Fluorescence 405 nm Ex")
        assert fl405["camera_settings"]["exposure_time_ms"] == 100.0
        assert fl405["z_offset_um"] == 1.5

    def test_confocal_overrides_migrated(self, software_dir):
        profile_path = software_dir / "acquisition_configurations" / "default_profile"
        output_path = software_dir / "user_profiles" / "default_profile"

        migrate_profile(profile_path, output_path)

        with open(output_path / "channel_configs" / "20x.yaml") as f:
            obj = yaml.safe_load(f)

        fl405 = next(ch for ch in obj["channels"] if ch["name"] == "Fluorescence 405 nm Ex")
        assert "confocal_override" in fl405
        override = fl405["confocal_override"]
        assert override["camera_settings"]["exposure_time_ms"] == 200.0
        assert override["illumination_settings"]["intensity"] == 80.0

    def test_laser_af_migrated(self, software_dir):
        profile_path = software_dir / "acquisition_configurations" / "default_profile"
        output_path = software_dir / "user_profiles" / "default_profile"

        migrate_profile(profile_path, output_path)

        af_path = output_path / "laser_af_configs" / "20x.yaml"
        assert af_path.exists()
        with open(af_path) as f:
            data = yaml.safe_load(f)
        assert data["x_offset"] == 10

    def test_idempotent(self, software_dir):
        profile_path = software_dir / "acquisition_configurations" / "default_profile"
        output_path = software_dir / "user_profiles" / "default_profile"

        # First run
        migrated1, skipped1 = migrate_profile(profile_path, output_path)
        assert migrated1 == 2

        # Second run — should skip all
        migrated2, skipped2 = migrate_profile(profile_path, output_path)
        assert migrated2 == 0
        assert skipped2 == 2


class TestFullMigration:
    def test_end_to_end(self, software_dir):
        result = migrate(software_dir)

        assert result["illumination_migrated"] is True
        assert "default_profile" in result["profiles"]
        assert result["profiles"]["default_profile"]["objectives_migrated"] == 2
        assert len(result["errors"]) == 0

    def test_channel_metadata_preserved(self, software_dir):
        """Verify display_color and enabled state carry through."""
        migrate(software_dir)

        general_path = (
            software_dir / "user_profiles" / "default_profile" / "channel_configs" / "general.yaml"
        )
        with open(general_path) as f:
            general = yaml.safe_load(f)

        fl405 = next(ch for ch in general["channels"] if ch["name"] == "Fluorescence 405 nm Ex")
        assert fl405["display_color"] == "#20ADF8"
        assert fl405["filter_position"] == 2
