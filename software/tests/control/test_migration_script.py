"""
Unit tests for migrate_acquisition_configs.py.

Tests the migration logic for converting legacy configs to new YAML format.
"""

import tempfile
from pathlib import Path

import pytest

# Import migration functions
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from control._def import SpotDetectionMode
from migrate_acquisition_configs import (
    convert_channel_definitions_to_illumination_config,
    convert_laser_af_json_to_yaml,
    int_to_hex_color,
    parse_xml_config,
)


class TestMigrationHelpers:
    """Tests for migration helper functions."""

    def test_int_to_hex_color(self):
        """Test converting integer color to hex string."""
        # White (16777215 = 0xFFFFFF)
        assert int_to_hex_color(16777215) == "#FFFFFF"

        # Red (16711680 = 0xFF0000)
        assert int_to_hex_color(16711680) == "#FF0000"

        # Green (65280 = 0x00FF00)
        assert int_to_hex_color(65280) == "#00FF00"

        # Blue (255 = 0x0000FF)
        assert int_to_hex_color(255) == "#0000FF"

        # Black (0 = 0x000000)
        assert int_to_hex_color(0) == "#000000"

    def test_parse_xml_config_nonexistent(self):
        """Test parsing nonexistent XML file."""
        result = parse_xml_config(Path("/nonexistent/path.xml"))
        assert result == []

    def test_parse_xml_config(self):
        """Test parsing a valid XML config file."""
        xml_content = """<modes>
  <mode ID="1" Name="BF LED matrix full" ExposureTime="12.0" AnalogGain="0.0" IlluminationSource="0" IlluminationIntensity="5.0" CameraSN="" ZOffset="0.0" EmissionFilterPosition="1" Selected="false">16777215</mode>
  <mode ID="5" Name="Fluorescence 405 nm Ex" ExposureTime="25.0" AnalogGain="10.0" IlluminationSource="11" IlluminationIntensity="20.0" CameraSN="" ZOffset="0.0" EmissionFilterPosition="1" Selected="false">2141688</mode>
</modes>"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            xml_path = Path(f.name)

        try:
            channels = parse_xml_config(xml_path)

            assert len(channels) == 2

            # Check first channel
            assert channels[0]["name"] == "BF LED matrix full"
            assert channels[0]["exposure_time_ms"] == 12.0
            assert channels[0]["analog_gain"] == 0.0
            assert channels[0]["illumination_source"] == 0
            assert channels[0]["illumination_intensity"] == 5.0
            assert channels[0]["emission_filter_position"] == 1
            assert channels[0]["color_int"] == 16777215
            assert channels[0]["display_color"] == "#FFFFFF"

            # Check second channel
            assert channels[1]["name"] == "Fluorescence 405 nm Ex"
            assert channels[1]["exposure_time_ms"] == 25.0
            assert channels[1]["analog_gain"] == 10.0
            assert channels[1]["illumination_source"] == 11
        finally:
            xml_path.unlink()

    def test_convert_channel_definitions_to_illumination_config(self):
        """Test converting channel_definitions.json format to illumination config."""
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
                    "name": "Fluorescence 488 nm Ex",
                    "type": "fluorescence",
                    "emission_filter_position": 1,
                    "display_color": "#1FFF00",
                    "enabled": True,
                    "numeric_channel": 2,
                    "illumination_source": None,
                    "ex_wavelength": None,
                },
            ],
            "numeric_channel_mapping": {
                "2": {"illumination_source": 12, "ex_wavelength": 488},
            },
        }

        config = convert_channel_definitions_to_illumination_config(channel_defs)

        assert config.version == 1
        assert len(config.channels) == 2

        # LED matrix channel
        led_channel = config.get_channel_by_name("BF LED matrix full")
        assert led_channel is not None
        assert led_channel.source_code == 0
        assert led_channel.wavelength_nm is None

        # Fluorescence channel
        fl_channel = config.get_channel_by_name("Fluorescence 488 nm Ex")
        assert fl_channel is not None
        assert fl_channel.source_code == 12
        assert fl_channel.wavelength_nm == 488
        assert fl_channel.intensity_calibration_file == "488.csv"

    def test_convert_laser_af_json_to_yaml_nonexistent(self):
        """Test converting nonexistent laser AF JSON file."""
        result = convert_laser_af_json_to_yaml(Path("/nonexistent/path.json"))
        assert result is None

    def test_convert_laser_af_json_to_yaml(self):
        """Test converting laser AF JSON to YAML config."""
        import json

        laser_af_data = {
            "x_offset": 100,
            "y_offset": 200,
            "width": 1024,
            "height": 256,
            "pixel_to_um": 0.75,
            "has_reference": True,
            "laser_af_averaging_n": 5,
            "spot_detection_mode": "dual_left",
            "reference_image": "base64data",
            "reference_image_shape": [256, 1024],
            "reference_image_dtype": "float32",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(laser_af_data, f)
            json_path = Path(f.name)

        try:
            config = convert_laser_af_json_to_yaml(json_path)

            assert config is not None
            assert config.version == 1
            assert config.x_offset == 100
            assert config.y_offset == 200
            assert config.width == 1024
            assert config.height == 256
            assert config.pixel_to_um == 0.75
            assert config.has_reference is True
            assert config.laser_af_averaging_n == 5
            assert config.spot_detection_mode == SpotDetectionMode.DUAL_LEFT
            assert config.reference_image == "base64data"
            assert config.reference_image_shape == [256, 1024]
            assert config.reference_image_dtype == "float32"
        finally:
            json_path.unlink()


class TestMigrationIntegration:
    """Integration tests for migration workflow."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_migration_creates_yaml_files(self, temp_dir):
        """Test that migration creates proper YAML files."""
        import json

        # Create source structure
        source_dir = temp_dir / "acquisition_configurations" / "test_profile" / "20x"
        source_dir.mkdir(parents=True)

        # Create XML config
        xml_content = """<modes>
  <mode ID="1" Name="BF LED matrix full" ExposureTime="12.0" AnalogGain="0.0" IlluminationSource="0" IlluminationIntensity="5.0" CameraSN="" ZOffset="0.0" EmissionFilterPosition="1" Selected="false">16777215</mode>
</modes>"""
        (source_dir / "channel_configurations.xml").write_text(xml_content)

        # Create laser AF JSON
        laser_af_data = {
            "x_offset": 100,
            "y_offset": 200,
            "width": 1024,
            "height": 256,
            "pixel_to_um": 0.75,
        }
        with open(source_dir / "laser_af_settings.json", "w") as f:
            json.dump(laser_af_data, f)

        # Create channel definitions
        configs_dir = temp_dir / "configurations"
        configs_dir.mkdir()
        channel_defs = {
            "max_fluorescence_channels": 5,
            "channels": [
                {
                    "name": "BF LED matrix full",
                    "type": "led_matrix",
                    "enabled": True,
                    "illumination_source": 0,
                },
            ],
            "numeric_channel_mapping": {},
        }
        with open(configs_dir / "channel_definitions.default.json", "w") as f:
            json.dump(channel_defs, f)

        # Run the migration by importing and calling functions
        from migrate_acquisition_configs import (
            convert_channel_definitions_to_illumination_config,
            convert_xml_channels_to_acquisition_config,
            load_channel_definitions_json,
            parse_xml_config,
            save_yaml,
        )

        # Load and convert illumination config
        channel_defs = load_channel_definitions_json(configs_dir / "channel_definitions.default.json")
        illumination_config = convert_channel_definitions_to_illumination_config(channel_defs)

        # Save illumination config
        machine_configs = temp_dir / "machine_configs"
        machine_configs.mkdir()
        save_yaml(machine_configs / "illumination_channel_config.yaml", illumination_config)

        # Parse and convert XML
        xml_channels = parse_xml_config(source_dir / "channel_configurations.xml")
        obj_config = convert_xml_channels_to_acquisition_config(xml_channels, illumination_config)

        # Save objective config
        target_dir = temp_dir / "user_profiles" / "test_profile" / "channel_configs"
        target_dir.mkdir(parents=True)
        save_yaml(target_dir / "20x.yaml", obj_config)

        # Verify files were created
        assert (machine_configs / "illumination_channel_config.yaml").exists()
        assert (target_dir / "20x.yaml").exists()

        # Verify content
        import yaml

        with open(target_dir / "20x.yaml") as f:
            loaded = yaml.safe_load(f)
        assert loaded["version"] == 1
        assert len(loaded["channels"]) == 1
        assert loaded["channels"][0]["name"] == "BF LED matrix full"

    def test_run_auto_migration_no_source(self, temp_dir):
        """Test auto-migration when no source directory exists."""
        from migrate_acquisition_configs import run_auto_migration

        # Should return False when no acquisition_configurations exists
        result = run_auto_migration(software_path=temp_dir)
        assert result is False

    def test_run_auto_migration_with_marker(self, temp_dir):
        """Test auto-migration skips when marker file exists."""
        import json
        from migrate_acquisition_configs import run_auto_migration

        # Create source structure with marker file
        source_dir = temp_dir / "acquisition_configurations"
        source_dir.mkdir()
        (source_dir / ".migration_complete").write_text("done")

        result = run_auto_migration(software_path=temp_dir)
        assert result is False

    def test_run_auto_migration_success(self, temp_dir):
        """Test successful auto-migration."""
        import json
        from migrate_acquisition_configs import run_auto_migration

        # Create source structure
        source_dir = temp_dir / "acquisition_configurations" / "test_profile" / "20x"
        source_dir.mkdir(parents=True)

        # Create XML config
        xml_content = """<modes>
  <mode ID="1" Name="BF LED matrix full" ExposureTime="12.0" AnalogGain="0.0" IlluminationSource="0" IlluminationIntensity="5.0" CameraSN="" ZOffset="0.0" EmissionFilterPosition="1" Selected="false">16777215</mode>
</modes>"""
        (source_dir / "channel_configurations.xml").write_text(xml_content)

        # Create channel definitions
        configs_dir = temp_dir / "configurations"
        configs_dir.mkdir()
        channel_defs = {
            "max_fluorescence_channels": 5,
            "channels": [
                {
                    "name": "BF LED matrix full",
                    "type": "led_matrix",
                    "enabled": True,
                    "illumination_source": 0,
                },
            ],
            "numeric_channel_mapping": {},
        }
        with open(configs_dir / "channel_definitions.json", "w") as f:
            json.dump(channel_defs, f)

        # Run auto-migration
        result = run_auto_migration(software_path=temp_dir)
        assert result is True

        # Verify marker file created
        assert (temp_dir / "acquisition_configurations" / ".migration_complete").exists()

        # Verify user_profiles created
        assert (temp_dir / "user_profiles" / "test_profile" / "channel_configs" / "general.yaml").exists()
        assert (temp_dir / "user_profiles" / "test_profile" / "channel_configs" / "20x.yaml").exists()

        # Verify backup created
        backups = list(temp_dir.glob("acquisition_configurations_backup_*"))
        assert len(backups) == 1
