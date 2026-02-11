"""Unit tests for acquisition YAML save/load functionality."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from squid.backend.io.acquisition_yaml import (
    AcquisitionYAMLData,
    ValidationResult,
    parse_acquisition_yaml,
    validate_hardware,
    save_acquisition_yaml,
    _serialize_for_yaml,
)


class TestParseAcquisitionYAML:
    """Tests for parse_acquisition_yaml function."""

    def test_parse_wellplate_yaml(self, tmp_path):
        """Test parsing a wellplate acquisition YAML file."""
        yaml_content = """
acquisition:
  experiment_id: test_exp
  widget_type: wellplate
  xy_mode: Select Wells

objective:
  name: 20x
  magnification: 20.0
  pixel_size_um: 0.325
  camera_binning: [2, 2]

z_stack:
  nz: 5
  delta_z_um: 2.0
  config: FROM BOTTOM
  use_piezo: true

time_series:
  nt: 3
  delta_t_s: 60.0

channels:
  - name: DAPI
  - name: GFP
  - name: RFP

autofocus:
  mode: contrast
  interval_fovs: 3

wellplate_scan:
  scan_size_mm: 1.5
  overlap_percent: 15.0
  regions:
    - name: A1
      center_mm: [10.0, 20.0, 0.5]
      shape: Square
    - name: B2
      center_mm: [15.0, 25.0, 0.5]
      shape: Circle
"""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text(yaml_content)

        data = parse_acquisition_yaml(str(yaml_path))

        assert data.widget_type == "wellplate"
        assert data.xy_mode == "Select Wells"
        assert data.objective_name == "20x"
        assert data.objective_magnification == 20.0
        assert data.camera_binning == (2, 2)
        assert data.nz == 5
        assert data.delta_z_um == 2.0
        assert data.use_piezo is True
        assert data.nt == 3
        assert data.delta_t_s == 60.0
        assert data.channel_names == ["DAPI", "GFP", "RFP"]
        assert data.autofocus_mode == "contrast"
        assert data.autofocus_interval_fovs == 3
        assert data.scan_size_mm == 1.5
        assert data.overlap_percent == 15.0
        assert len(data.wellplate_regions) == 2
        assert data.wellplate_regions[0]["name"] == "A1"

    def test_parse_flexible_yaml(self, tmp_path):
        """Test parsing a flexible acquisition YAML file."""
        yaml_content = """
acquisition:
  widget_type: flexible

objective:
  name: 10x

z_stack:
  nz: 1

time_series:
  nt: 1

channels:
  - name: BF

flexible_scan:
  nx: 3
  ny: 3
  delta_x_mm: 0.8
  delta_y_mm: 0.8
  overlap_percent: 10.0
  positions:
    - name: P1
      center_mm: [5.0, 5.0, 0.0]
"""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text(yaml_content)

        data = parse_acquisition_yaml(str(yaml_path))

        assert data.widget_type == "flexible"
        assert data.nx == 3
        assert data.ny == 3
        assert data.delta_x_mm == 0.8
        assert data.delta_y_mm == 0.8
        assert len(data.flexible_positions) == 1

    def test_parse_minimal_yaml(self, tmp_path):
        """Test parsing minimal YAML with defaults."""
        yaml_content = """
acquisition:
  widget_type: wellplate
"""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text(yaml_content)

        data = parse_acquisition_yaml(str(yaml_path))

        assert data.widget_type == "wellplate"
        assert data.nz == 1
        assert data.nt == 1
        assert data.channel_names == []

    def test_parse_empty_yaml_raises(self, tmp_path):
        """Test that empty YAML raises ValueError."""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text("")

        with pytest.raises(ValueError, match="empty or invalid"):
            parse_acquisition_yaml(str(yaml_path))

    def test_parse_invalid_widget_type_raises(self, tmp_path):
        """Test that invalid widget_type raises ValueError."""
        yaml_content = """
acquisition:
  widget_type: invalid_type
"""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text(yaml_content)

        with pytest.raises(ValueError, match="Invalid widget_type"):
            parse_acquisition_yaml(str(yaml_path))

    def test_parse_z_delta_mm_conversion(self, tmp_path):
        """Test that delta_z_mm is converted to um."""
        yaml_content = """
acquisition:
  widget_type: wellplate
z_stack:
  nz: 5
  delta_z_mm: 0.002
"""
        yaml_path = tmp_path / "acquisition.yaml"
        yaml_path.write_text(yaml_content)

        data = parse_acquisition_yaml(str(yaml_path))
        assert data.delta_z_um == 2.0  # 0.002 mm * 1000 = 2.0 um


class TestValidateHardware:
    """Tests for validate_hardware function."""

    def test_matching_hardware(self):
        """Test validation passes when hardware matches."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(yaml_data, "20x", (2, 2))

        assert result.is_valid
        assert not result.objective_mismatch
        assert not result.binning_mismatch

    def test_objective_mismatch(self):
        """Test validation fails on objective mismatch."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(yaml_data, "10x", (2, 2))

        assert not result.is_valid
        assert result.objective_mismatch
        assert not result.binning_mismatch
        assert "20x" in result.message
        assert "10x" in result.message

    def test_binning_mismatch(self):
        """Test validation fails on binning mismatch."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(yaml_data, "20x", (1, 1))

        assert not result.is_valid
        assert not result.objective_mismatch
        assert result.binning_mismatch

    def test_both_mismatch(self):
        """Test validation fails on both mismatches."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name="20x",
            camera_binning=(2, 2),
        )

        result = validate_hardware(yaml_data, "10x", (1, 1))

        assert not result.is_valid
        assert result.objective_mismatch
        assert result.binning_mismatch

    def test_none_values_valid(self):
        """Test that None values in YAML don't cause mismatch."""
        yaml_data = AcquisitionYAMLData(
            widget_type="wellplate",
            objective_name=None,
            camera_binning=None,
        )

        result = validate_hardware(yaml_data, "20x", (2, 2))

        assert result.is_valid


class TestSerializeForYAML:
    """Tests for _serialize_for_yaml helper."""

    def test_serialize_enum(self):
        """Test enum serialization."""
        from enum import Enum

        class TestEnum(Enum):
            VALUE = "test_value"

        assert _serialize_for_yaml(TestEnum.VALUE) == "test_value"

    def test_serialize_numpy_array(self):
        """Test numpy array serialization."""
        import numpy as np

        arr = np.array([1, 2, 3])
        result = _serialize_for_yaml(arr)

        assert result == [1, 2, 3]

    def test_serialize_numpy_scalar(self):
        """Test numpy scalar serialization."""
        import numpy as np

        assert _serialize_for_yaml(np.int64(42)) == 42
        assert _serialize_for_yaml(np.float64(3.14)) == 3.14
        assert _serialize_for_yaml(np.bool_(True)) is True

    def test_serialize_dict(self):
        """Test dict serialization."""
        import numpy as np

        d = {"a": 1, "b": np.array([2, 3])}
        result = _serialize_for_yaml(d)

        assert result == {"a": 1, "b": [2, 3]}

    def test_serialize_list(self):
        """Test list serialization."""
        import numpy as np

        lst = [1, np.int64(2), "three"]
        result = _serialize_for_yaml(lst)

        assert result == [1, 2, "three"]

    def test_serialize_none(self):
        """Test None serialization."""
        assert _serialize_for_yaml(None) is None


class TestAcquisitionYAMLData:
    """Tests for AcquisitionYAMLData dataclass."""

    def test_default_values(self):
        """Test default values are sensible."""
        data = AcquisitionYAMLData(widget_type="wellplate")

        assert data.nz == 1
        assert data.nt == 1
        assert data.delta_z_um == 1.0
        assert data.delta_t_s == 0.0
        assert data.channel_names == []
        assert data.autofocus_mode == "none"
        assert data.autofocus_interval_fovs == 1

    def test_all_values(self):
        """Test setting all values."""
        data = AcquisitionYAMLData(
            widget_type="flexible",
            xy_mode="Manual",
            objective_name="40x",
            objective_magnification=40.0,
            objective_pixel_size_um=0.16,
            camera_binning=(1, 1),
            nz=10,
            delta_z_um=0.5,
            z_stacking_config="FROM CENTER",
            use_piezo=True,
            nt=5,
            delta_t_s=30.0,
            channel_names=["DAPI", "GFP"],
            autofocus_mode="laser_reflection",
            autofocus_interval_fovs=2,
            nx=5,
            ny=5,
            delta_x_mm=0.5,
            delta_y_mm=0.5,
            flexible_positions=[{"name": "P1", "center_mm": [0, 0, 0]}],
        )

        assert data.widget_type == "flexible"
        assert data.nz == 10
        assert data.nt == 5
        assert data.nx == 5
