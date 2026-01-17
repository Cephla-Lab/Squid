"""
Tests for MERFISHFluidicsConfig class.
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from squid.backend.drivers.fluidics.merfish_driver import MERFISHFluidicsConfig


@pytest.fixture
def valid_config_data():
    """Return valid configuration data."""
    return {
        "microcontroller": {"serial_number": "TEST123"},
        "syringe_pump": {
            "serial_number": "PUMP123",
            "volume_ul": 5000,
            "waste_port": 3,
            "extract_port": 2,
            "speed_code_limit": 10,
        },
        "selector_valves": {
            "valve_ids_allowed": [0, 1],
            "number_of_ports": {"0": 5, "1": 5},
        },
        "solution_port_mapping": {
            "wash_buffer": 1,
            "imaging_buffer": 2,
            "probe_1": 6,
            "probe_2": 7,
        },
        "limits": {
            "max_flow_rate_ul_per_min": 10000.0,
            "min_flow_rate_ul_per_min": 1.0,
            "max_volume_ul": 5000.0,
        },
    }


@pytest.fixture
def config_file(valid_config_data):
    """Create a temporary config file."""
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_config.json"
        with open(config_path, "w") as f:
            json.dump(valid_config_data, f)
        yield str(config_path)


class TestMERFISHFluidicsConfig:
    """Tests for MERFISHFluidicsConfig."""

    def test_load_valid_config(self, config_file):
        """Test loading a valid configuration."""
        config = MERFISHFluidicsConfig(config_file)

        assert config.raw_config is not None
        assert config.syringe_volume_ul == 5000

    def test_port_mapping_lookup(self, config_file):
        """Test solution name to port lookup."""
        config = MERFISHFluidicsConfig(config_file)

        assert config.get_port_for_solution("wash_buffer") == 1
        assert config.get_port_for_solution("imaging_buffer") == 2
        assert config.get_port_for_solution("probe_1") == 6
        assert config.get_port_for_solution("nonexistent") is None

    def test_case_insensitive_lookup(self, config_file):
        """Test that solution lookup is case-insensitive."""
        config = MERFISHFluidicsConfig(config_file)

        assert config.get_port_for_solution("WASH_BUFFER") == 1
        assert config.get_port_for_solution("Wash_Buffer") == 1
        assert config.get_port_for_solution("wash_BUFFER") == 1

    def test_reverse_mapping(self, config_file):
        """Test port to solution name lookup."""
        config = MERFISHFluidicsConfig(config_file)

        assert config.get_solution_for_port(1) == "wash_buffer"
        assert config.get_solution_for_port(2) == "imaging_buffer"
        assert config.get_solution_for_port(99) is None

    def test_available_ports(self, config_file):
        """Test available_ports returns sorted list."""
        config = MERFISHFluidicsConfig(config_file)

        ports = config.available_ports
        assert isinstance(ports, list)
        assert ports == sorted(ports)
        assert 1 in ports
        assert 2 in ports

    def test_limits_property(self, config_file):
        """Test limits property returns limits dict."""
        config = MERFISHFluidicsConfig(config_file)

        limits = config.limits
        assert "max_flow_rate_ul_per_min" in limits
        assert "min_flow_rate_ul_per_min" in limits
        assert "max_volume_ul" in limits
        assert limits["max_flow_rate_ul_per_min"] == 10000.0

    def test_missing_config_file(self):
        """Test FileNotFoundError for missing config file."""
        with pytest.raises(FileNotFoundError):
            MERFISHFluidicsConfig("/nonexistent/path/config.json")

    def test_invalid_json(self):
        """Test json.JSONDecodeError for invalid JSON."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid.json"
            with open(config_path, "w") as f:
                f.write("not valid json {{{")

            with pytest.raises(json.JSONDecodeError):
                MERFISHFluidicsConfig(str(config_path))

    def test_missing_required_section(self, valid_config_data):
        """Test ValueError for missing required section."""
        with TemporaryDirectory() as tmpdir:
            # Remove required section
            del valid_config_data["syringe_pump"]

            config_path = Path(tmpdir) / "incomplete.json"
            with open(config_path, "w") as f:
                json.dump(valid_config_data, f)

            with pytest.raises(ValueError, match="syringe_pump"):
                MERFISHFluidicsConfig(str(config_path))

    def test_invalid_syringe_volume(self, valid_config_data):
        """Test ValueError for invalid syringe_pump.volume_ul."""
        with TemporaryDirectory() as tmpdir:
            valid_config_data["syringe_pump"]["volume_ul"] = 0

            config_path = Path(tmpdir) / "bad_volume.json"
            with open(config_path, "w") as f:
                json.dump(valid_config_data, f)

            with pytest.raises(ValueError, match="volume_ul"):
                MERFISHFluidicsConfig(str(config_path))

    def test_duplicate_port_numbers(self, valid_config_data):
        """Test ValueError for duplicate port numbers."""
        with TemporaryDirectory() as tmpdir:
            # Add duplicate port
            valid_config_data["solution_port_mapping"]["duplicate"] = 1

            config_path = Path(tmpdir) / "duplicate_ports.json"
            with open(config_path, "w") as f:
                json.dump(valid_config_data, f)

            with pytest.raises(ValueError, match="Duplicate"):
                MERFISHFluidicsConfig(str(config_path))

    def test_explicit_allowed_ports(self, valid_config_data):
        """Test config with explicit allowed_ports list."""
        with TemporaryDirectory() as tmpdir:
            # Add explicit allowed_ports
            valid_config_data["selector_valves"]["allowed_ports"] = [1, 2, 6, 7, 8, 9, 10]

            config_path = Path(tmpdir) / "explicit_ports.json"
            with open(config_path, "w") as f:
                json.dump(valid_config_data, f)

            config = MERFISHFluidicsConfig(str(config_path))
            assert config.get_port_for_solution("wash_buffer") == 1
