"""
Unit tests for control/acquisition_yaml_loader.py
"""

import pytest

from control.acquisition_yaml_loader import (
    AcquisitionYAMLData,
    parse_acquisition_yaml,
    ValidationResult,
    validate_hardware,
)


class TestParseAcquisitionYAML:
    """Tests for parse_acquisition_yaml function."""

    def test_parse_wellplate_yaml(self, tmp_path):
        """Test parsing a wellplate acquisition YAML file."""
        yaml_content = """
acquisition:
  widget_type: wellplate
  xy_mode: Select Wells
objective:
  name: 20x
  magnification: 20.0
  pixel_size_um: 0.188
  camera_binning:
    - 1
    - 1
z_stack:
  nz: 5
  delta_z_mm: 0.002
  config: FROM CENTER
time_series:
  nt: 10
  delta_t_s: 60.0
channels:
  - name: BF LED matrix full
  - name: Fluorescence 488 nm Ex
autofocus:
  contrast_af: true
  laser_af: false
wellplate_scan:
  scan_size_mm: 2.1
  overlap_percent: 15.0
  regions:
    - name: C4
      center_mm: [38.31, 28.75, 1.2]
      shape: Square
    - name: D5
      center_mm: [47.31, 37.75, 1.2]
      shape: Circle
"""
        yaml_file = tmp_path / "test_wellplate.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))

        assert result.widget_type == "wellplate"
        assert result.xy_mode == "Select Wells"
        assert result.objective_name == "20x"
        assert result.objective_magnification == 20.0
        assert result.objective_pixel_size_um == 0.188
        assert result.camera_binning == (1, 1)
        assert result.nz == 5
        assert result.delta_z_um == 2.0  # 0.002 mm * 1000 = 2.0 um
        assert result.z_stacking_config == "FROM CENTER"
        assert result.nt == 10
        assert result.delta_t_s == 60.0
        assert result.channel_names == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
        assert result.contrast_af is True
        assert result.laser_af is False
        assert result.scan_size_mm == 2.1
        assert result.overlap_percent == 15.0
        assert result.scan_shape == "Square"
        assert len(result.wellplate_regions) == 2
        assert result.wellplate_regions[0]["name"] == "C4"

    def test_parse_flexible_yaml(self, tmp_path):
        """Test parsing a flexible acquisition YAML file."""
        yaml_content = """
acquisition:
  widget_type: flexible
  xy_mode: Manual
objective:
  name: 10x
  magnification: 10.0
  camera_binning:
    - 2
    - 2
z_stack:
  nz: 3
  delta_z_mm: 0.001
time_series:
  nt: 1
  delta_t_s: 0.0
channels:
  - name: BF LED matrix full
autofocus:
  contrast_af: false
  laser_af: true
flexible_scan:
  nx: 3
  ny: 4
  delta_x_mm: 0.5
  delta_y_mm: 0.6
  overlap_percent: 20.0
  positions:
    - name: Position 1
      center_mm: [10.0, 20.0, 1.0]
    - name: Position 2
      center_mm: [15.0, 25.0, 1.5]
"""
        yaml_file = tmp_path / "test_flexible.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))

        assert result.widget_type == "flexible"
        assert result.xy_mode == "Manual"
        assert result.objective_name == "10x"
        assert result.camera_binning == (2, 2)
        assert result.nz == 3
        assert result.delta_z_um == 1.0
        assert result.nx == 3
        assert result.ny == 4
        assert result.delta_x_mm == 0.5
        assert result.delta_y_mm == 0.6
        assert result.overlap_percent == 20.0
        assert result.laser_af is True
        assert len(result.flexible_positions) == 2
        assert result.flexible_positions[0]["name"] == "Position 1"

    def test_parse_minimal_yaml(self, tmp_path):
        """Test parsing YAML with minimal required fields."""
        yaml_content = """
acquisition:
  widget_type: wellplate
"""
        yaml_file = tmp_path / "test_minimal.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))

        # Check defaults
        assert result.widget_type == "wellplate"
        assert result.xy_mode == "Select Wells"
        assert result.objective_name is None
        assert result.camera_binning is None
        assert result.nz == 1
        assert result.delta_z_um == 1.0  # 0.001 * 1000
        assert result.nt == 1
        assert result.delta_t_s == 0.0
        assert result.channel_names == []
        assert result.contrast_af is False
        assert result.laser_af is False
        assert result.overlap_percent == 10.0

    def test_parse_empty_yaml_raises_error(self, tmp_path):
        """Test that empty YAML file raises ValueError."""
        yaml_file = tmp_path / "test_empty.yaml"
        yaml_file.write_text("")

        with pytest.raises(ValueError, match="YAML file is empty or invalid"):
            parse_acquisition_yaml(str(yaml_file))

    def test_parse_yaml_only_comments_raises_error(self, tmp_path):
        """Test that YAML with only comments raises ValueError."""
        yaml_content = """
# This is a comment
# Another comment
"""
        yaml_file = tmp_path / "test_comments.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="YAML file is empty or invalid"):
            parse_acquisition_yaml(str(yaml_file))

    def test_parse_nonexistent_file_raises_error(self):
        """Test that non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_acquisition_yaml("/nonexistent/path/file.yaml")

    def test_parse_invalid_yaml_raises_error(self, tmp_path):
        """Test that invalid YAML syntax raises error."""
        yaml_content = """
acquisition:
  widget_type: wellplate
  invalid yaml here: [unclosed bracket
"""
        yaml_file = tmp_path / "test_invalid.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(Exception):  # yaml.YAMLError
            parse_acquisition_yaml(str(yaml_file))

    def test_parse_invalid_widget_type_raises_error(self, tmp_path):
        """Test that invalid widget_type raises ValueError."""
        yaml_content = """
acquisition:
  widget_type: unknown
"""
        yaml_file = tmp_path / "test_invalid_widget.yaml"
        yaml_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="Invalid widget_type"):
            parse_acquisition_yaml(str(yaml_file))

    def test_parse_camera_binning_invalid_format(self, tmp_path):
        """Test that invalid camera binning format is handled."""
        yaml_content = """
acquisition:
  widget_type: wellplate
objective:
  camera_binning: not_a_list
"""
        yaml_file = tmp_path / "test_binning.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))
        assert result.camera_binning is None

    def test_parse_camera_binning_incomplete(self, tmp_path):
        """Test that incomplete camera binning is handled."""
        yaml_content = """
acquisition:
  widget_type: wellplate
objective:
  camera_binning:
    - 1
"""
        yaml_file = tmp_path / "test_binning_incomplete.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))
        assert result.camera_binning is None

    def test_parse_camera_binning_oversized(self, tmp_path):
        """Test that oversized camera binning (more than 2 values) is handled."""
        yaml_content = """
acquisition:
  widget_type: wellplate
objective:
  camera_binning:
    - 1
    - 2
    - 3
"""
        yaml_file = tmp_path / "test_binning_oversized.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))
        assert result.camera_binning is None

    def test_parse_channels_with_missing_names(self, tmp_path):
        """Test that channels with missing names are filtered out."""
        yaml_content = """
acquisition:
  widget_type: wellplate
channels:
  - name: Valid Channel
  - exposure_time: 100
  - name: Another Valid
"""
        yaml_file = tmp_path / "test_channels.yaml"
        yaml_file.write_text(yaml_content)

        result = parse_acquisition_yaml(str(yaml_file))
        assert result.channel_names == ["Valid Channel", "Another Valid"]


class TestValidateHardware:
    """Tests for validate_hardware function."""

    def test_validate_matching_hardware(self):
        """Test validation when hardware matches."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(1, 1),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="20x",
            current_binning=(1, 1),
        )

        assert result.is_valid is True
        assert result.objective_mismatch is False
        assert result.binning_mismatch is False
        assert result.message == ""

    def test_validate_objective_mismatch(self):
        """Test validation when objective doesn't match."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(1, 1),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="10x",
            current_binning=(1, 1),
        )

        assert result.is_valid is False
        assert result.objective_mismatch is True
        assert result.binning_mismatch is False
        assert "Objective mismatch" in result.message
        assert "20x" in result.message
        assert "10x" in result.message

    def test_validate_binning_mismatch(self):
        """Test validation when camera binning doesn't match."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="20x",
            current_binning=(1, 1),
        )

        assert result.is_valid is False
        assert result.objective_mismatch is False
        assert result.binning_mismatch is True
        assert "Camera binning mismatch" in result.message

    def test_validate_both_mismatch(self):
        """Test validation when both objective and binning don't match."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="10x",
            current_binning=(1, 1),
        )

        assert result.is_valid is False
        assert result.objective_mismatch is True
        assert result.binning_mismatch is True
        assert "Objective mismatch" in result.message
        assert "Camera binning mismatch" in result.message

    def test_validate_no_objective_in_yaml(self):
        """Test validation when YAML doesn't specify objective."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name=None,
            camera_binning=(1, 1),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="20x",
            current_binning=(1, 1),
        )

        assert result.is_valid is True
        assert result.objective_mismatch is False

    def test_validate_no_binning_in_yaml(self):
        """Test validation when YAML doesn't specify binning."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=None,
        )

        result = validate_hardware(
            yaml_data,
            current_objective="20x",
            current_binning=(2, 2),
        )

        assert result.is_valid is True
        assert result.binning_mismatch is False

    def test_validation_result_fields(self):
        """Test that ValidationResult contains all expected fields."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(
            yaml_data,
            current_objective="10x",
            current_binning=(1, 1),
        )

        assert result.current_objective == "10x"
        assert result.yaml_objective == "20x"
        assert result.current_binning == (1, 1)
        assert result.yaml_binning == (2, 2)


class TestAcquisitionYAMLDataclass:
    """Tests for AcquisitionYAMLData dataclass."""

    def test_default_values(self):
        """Test that dataclass has correct default values."""
        data = AcquisitionYAMLData(widget_type="wellplate")

        assert data.xy_mode == "Select Wells"
        assert data.objective_name is None
        assert data.nz == 1
        assert data.delta_z_um == 1.0
        assert data.z_stacking_config == "FROM BOTTOM"
        assert data.nt == 1
        assert data.delta_t_s == 0.0
        assert data.channel_names == []
        assert data.contrast_af is False
        assert data.laser_af is False
        assert data.overlap_percent == 10.0
        assert data.nx == 1
        assert data.ny == 1
        assert data.delta_x_mm == 0.9
        assert data.delta_y_mm == 0.9

    def test_required_widget_type(self):
        """Test that widget_type is required."""
        with pytest.raises(TypeError):
            AcquisitionYAMLData()  # Missing widget_type

    def test_channel_names_isolation(self):
        """Test that channel_names default list is not shared between instances."""
        data1 = AcquisitionYAMLData(widget_type="wellplate")
        data2 = AcquisitionYAMLData(widget_type="flexible")

        data1.channel_names.append("Channel 1")

        assert data1.channel_names == ["Channel 1"]
        assert data2.channel_names == []


class TestValidationResultDataclass:
    """Tests for ValidationResult dataclass."""

    def test_default_values(self):
        """Test that ValidationResult has correct default values."""
        result = ValidationResult(is_valid=True)

        assert result.is_valid is True
        assert result.objective_mismatch is False
        assert result.binning_mismatch is False
        assert result.current_objective == ""
        assert result.yaml_objective == ""
        assert result.current_binning == (1, 1)
        assert result.yaml_binning == (1, 1)
        assert result.message == ""

    def test_all_fields_set(self):
        """Test setting all fields."""
        result = ValidationResult(
            is_valid=False,
            objective_mismatch=True,
            binning_mismatch=True,
            current_objective="10x",
            yaml_objective="20x",
            current_binning=(1, 1),
            yaml_binning=(2, 2),
            message="Hardware mismatch",
        )

        assert result.is_valid is False
        assert result.objective_mismatch is True
        assert result.binning_mismatch is True
        assert result.current_objective == "10x"
        assert result.yaml_objective == "20x"
        assert result.current_binning == (1, 1)
        assert result.yaml_binning == (2, 2)
        assert result.message == "Hardware mismatch"
