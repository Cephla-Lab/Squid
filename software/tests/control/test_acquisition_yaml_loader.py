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

    def test_a_piezo_stack_is_refused_on_a_microscope_without_a_piezo(self):
        yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=True)

        result = validate_hardware(yaml_data, "20x", (1, 1), piezo_travel_um=None)

        assert result.is_valid is False
        assert result.piezo_mismatch is True
        assert "no piezo" in result.message

    def test_a_stepper_stack_loads_on_a_microscope_without_a_piezo(self):
        yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=False)

        assert validate_hardware(yaml_data, "20x", (1, 1), piezo_travel_um=None).is_valid is True

    @pytest.mark.parametrize("piezo_z_range_um", [(250.0, 350.0), (-5.0, 20.0)])
    def test_a_saved_piezo_range_outside_this_piezos_travel_is_refused(self, piezo_z_range_um):
        yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=True, piezo_z_range_um=piezo_z_range_um)

        result = validate_hardware(yaml_data, "20x", (1, 1), piezo_travel_um=300.0)

        assert result.is_valid is False
        assert result.piezo_mismatch is True
        assert "0 to 300 µm" in result.message

    @pytest.mark.parametrize("piezo_z_range_um", [(250.0, 20.0), (float("nan"), 26.0), (20.0, float("inf"))])
    def test_a_saved_piezo_range_that_is_not_an_ordered_pair_of_numbers_is_refused(self, piezo_z_range_um):
        yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=True, piezo_z_range_um=piezo_z_range_um)

        result = validate_hardware(yaml_data, "20x", (1, 1), piezo_travel_um=300.0)

        assert result.is_valid is False
        assert result.piezo_mismatch is True
        assert "Z-min" in result.message

    def test_a_saved_piezo_range_inside_the_travel_loads(self):
        yaml_data = AcquisitionYAMLData(widget_type="wellplate", use_piezo=True, piezo_z_range_um=(20.0, 26.0))

        assert validate_hardware(yaml_data, "20x", (1, 1), piezo_travel_um=300.0).is_valid is True


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


class TestExtendedLoader:
    def test_parse_dict_reads_new_fields_and_keeps_fovs(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        data = parse_acquisition_dict(
            {
                "acquisition": {"experiment_id": "R01_image", "widget_type": "wellplate", "skip_saving": True},
                "sample": {"wellplate_format": "384 well plate"},
                "z_stack": {"nz": 2, "delta_z_mm": 0.001, "z_range_mm": [1.0, 1.001]},
                "channels": [{"name": "A"}],
                "wellplate_scan": {
                    "regions": [{"name": "A1", "center_mm": [1, 2, 3], "fovs": [[1.0, 2.0, 3.0], [1.5, 2.0, 3.0]]}]
                },
            }
        )
        assert data.experiment_id == "R01_image"
        assert data.wellplate_format == "384 well plate"
        assert data.z_range_mm == (1.0, 1.001)
        assert data.skip_saving is True
        assert data.wellplate_regions[0]["fovs"] == [[1.0, 2.0, 3.0], [1.5, 2.0, 3.0]]

    def test_parse_folder_fills_fovs_from_coordinates_csv(self, tmp_path):
        from control.acquisition_yaml_loader import parse_acquisition_yaml

        (tmp_path / "acquisition.yaml").write_text(
            "acquisition:\n  widget_type: wellplate\nwellplate_scan:\n  regions:\n  - name: A1\n    center_mm: [1, 2, 3]\n"
        )
        (tmp_path / "coordinates.csv").write_text("region,x (mm),y (mm),z (mm)\nA1,1.0,2.0,3.0\nA1,1.5,2.0,3.1\n")

        data = parse_acquisition_yaml(str(tmp_path))

        assert data.wellplate_regions[0]["fovs"] == [[1.0, 2.0, 3.0], [1.5, 2.0, 3.1]]

    def test_read_coordinates_csv_drops_z_when_incomplete_and_groups_by_region(self, tmp_path):
        from control.acquisition_yaml_loader import read_coordinates_csv

        csv_path = tmp_path / "coordinates.csv"
        csv_path.write_text("region,x (mm),y (mm),z (mm)\nA1,1.0,2.0,3.0\nB2,4.0,5.0,\nA1,1.5,2.0,3.0\n")

        regions = read_coordinates_csv(str(csv_path))

        assert regions == [{"name": "A1", "fovs": [[1.0, 2.0], [1.5, 2.0]]}, {"name": "B2", "fovs": [[4.0, 5.0]]}]

    def test_read_coordinates_csv_requires_columns(self, tmp_path):
        from control.acquisition_yaml_loader import read_coordinates_csv

        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("x,y\n1,2\n")
        with pytest.raises(ValueError):
            read_coordinates_csv(str(csv_path))

    def test_read_coordinates_csv_skips_a_provenance_stamp(self, tmp_path):
        """A file written by "Save Coordinates" carries a one-line JSON stamp above the header;
        it must read the same as an unstamped acquisition coordinates.csv."""
        import pandas as pd

        from control.acquisition_yaml_loader import read_coordinates_csv
        from control.core.coordinate_provenance import make_stamp, STAMP_PREFIX, write_scan_coordinates_csv

        csv_path = tmp_path / "saved.csv"
        df = pd.DataFrame(
            [["A1", 1.0, 2.0, 3.0], ["A1", 1.5, 2.0, 3.1]], columns=["region", "x (mm)", "y (mm)", "z (mm)"]
        )
        write_scan_coordinates_csv(str(csv_path), df, make_stamp("96 well plate"))
        assert csv_path.read_text().startswith(STAMP_PREFIX)

        assert read_coordinates_csv(str(csv_path)) == [{"name": "A1", "fovs": [[1.0, 2.0, 3.0], [1.5, 2.0, 3.1]]}]

    def test_parse_folder_tolerates_a_bad_coordinates_csv(self, tmp_path):
        from control.acquisition_yaml_loader import parse_acquisition_yaml

        (tmp_path / "acquisition.yaml").write_text(
            "acquisition:\n  widget_type: wellplate\nwellplate_scan:\n  regions:\n  - name: A1\n    center_mm: [1, 2, 3]\n"
        )
        (tmp_path / "coordinates.csv").write_text("region,x (mm),y (mm),z (mm)\n")  # header only (aborted run)

        data = parse_acquisition_yaml(str(tmp_path))

        assert data.wellplate_regions[0]["center_mm"] == [1, 2, 3] and "fovs" not in data.wellplate_regions[0]

    def test_parse_folder_fills_only_the_regions_missing_fovs(self, tmp_path):
        from control.acquisition_yaml_loader import parse_acquisition_yaml

        (tmp_path / "acquisition.yaml").write_text(
            "acquisition:\n  widget_type: wellplate\nwellplate_scan:\n  regions:\n"
            "  - name: A1\n    fovs: [[9.0, 9.0]]\n  - name: B2\n    center_mm: [1, 2, 3]\n"
        )
        (tmp_path / "coordinates.csv").write_text("region,x (mm),y (mm)\nA1,1.0,2.0\nB2,4.0,5.0\n")

        data = parse_acquisition_yaml(str(tmp_path))

        regions = {r["name"]: r for r in data.wellplate_regions}
        assert regions["A1"]["fovs"] == [[9.0, 9.0]]  # kept
        assert regions["B2"]["fovs"] == [[4.0, 5.0]]  # completed from the CSV


class TestZStackOrder:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, "CHANNEL FIRST"),
            ("CHANNEL FIRST", "CHANNEL FIRST"),
            ("Z FIRST", "Z FIRST"),
            ("z first", "Z FIRST"),
            ("Z_FIRST", "Z FIRST"),
            ("channel-first", "CHANNEL FIRST"),
        ],
    )
    def test_parse_z_stack_order_normalizes(self, value, expected):
        from control.acquisition_yaml_loader import parse_z_stack_order

        assert parse_z_stack_order(value) == expected

    @pytest.mark.parametrize("value", ["", "None", "zfirst", "TIME FIRST", 3])
    def test_parse_z_stack_order_rejects_unknown_values(self, value):
        from control.acquisition_yaml_loader import parse_z_stack_order

        with pytest.raises(ValueError, match="z_stack.order"):
            parse_z_stack_order(value)

    def test_yaml_without_order_loads_as_channel_first(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        assert parse_acquisition_dict({"z_stack": {"nz": 3}}).z_stack_order == "CHANNEL FIRST"

    def test_yaml_order_is_read(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        assert parse_acquisition_dict({"z_stack": {"nz": 3, "order": "Z FIRST"}}).z_stack_order == "Z FIRST"

    def test_yaml_with_unknown_order_is_rejected(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        with pytest.raises(ValueError, match="z_stack.order"):
            parse_acquisition_dict({"z_stack": {"nz": 3, "order": "TIME FIRST"}})


class TestPiezoZRange:
    def _params(self, piezo_z_range_um):
        from control.core.multi_point_utils import AcquisitionParameters, ScanPositionInformation

        return AcquisitionParameters(
            experiment_ID="piezo_range",
            base_path=None,
            selected_configurations=[],
            acquisition_start_time=0.0,
            scan_position_information=ScanPositionInformation(
                scan_region_coords_mm=[], scan_region_names=[], scan_region_fov_coords_mm={}
            ),
            NX=1,
            deltaX=0,
            NY=1,
            deltaY=0,
            NZ=4,
            deltaZ=0.002,
            Nt=1,
            deltat=0,
            do_autofocus=False,
            do_reflection_autofocus=False,
            use_piezo=True,
            display_resolution_scaling=1.0,
            z_stacking_config="FROM BOTTOM",
            z_range=(1.0, 1.0),
            piezo_z_range_um=piezo_z_range_um,
        )

    @pytest.mark.parametrize("piezo_z_range_um", [(20.0, 26.0), None])
    def test_a_saved_acquisition_yaml_reads_back_its_piezo_range(self, tmp_path, piezo_z_range_um):
        from control.core.multi_point_controller import _save_acquisition_yaml

        _save_acquisition_yaml(self._params(piezo_z_range_um), str(tmp_path))
        data = parse_acquisition_yaml(str(tmp_path))

        assert data.piezo_z_range_um == piezo_z_range_um
        assert data.z_range_mm == (1.0, 1.0)  # the stepper's z stays in its own field

    def test_an_older_piezo_yaml_without_the_field_keeps_its_stepper_range(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        data = parse_acquisition_dict({"z_stack": {"nz": 3, "use_piezo": True, "z_range_mm": [3.2, 3.204]}})

        assert data.piezo_z_range_um is None
        assert data.z_range_mm == (3.2, 3.204)


class TestLaserAfPerStack:
    """autofocus.laser_af_per_stack: off unless asked for, and only with laser AF in Z First."""

    @pytest.mark.parametrize("value", [None, False, "false"])  # missing / null / off
    def test_laser_af_per_stack_is_off_unless_asked_for(self, value):
        from control.acquisition_yaml_loader import parse_laser_af_per_stack

        assert parse_laser_af_per_stack(value, laser_af=True, z_stack_order="Z FIRST") is False

    @pytest.mark.parametrize("value", [True, "true", "yes", 1])
    def test_laser_af_per_stack_on_needs_laser_af_and_z_first(self, value):
        from control.acquisition_yaml_loader import parse_laser_af_per_stack

        assert parse_laser_af_per_stack(value, laser_af=True, z_stack_order="Z FIRST") is True

    def test_laser_af_per_stack_without_laser_af_is_rejected(self):
        from control.acquisition_yaml_loader import parse_laser_af_per_stack

        with pytest.raises(ValueError, match="laser_af_per_stack"):
            parse_laser_af_per_stack(True, laser_af=False, z_stack_order="Z FIRST")

    def test_laser_af_per_stack_with_channel_first_is_rejected(self):
        from control.acquisition_yaml_loader import parse_laser_af_per_stack

        with pytest.raises(ValueError, match="laser_af_per_stack"):
            parse_laser_af_per_stack(True, laser_af=True, z_stack_order="CHANNEL FIRST")

    def test_laser_af_per_stack_rejects_a_value_that_is_not_a_flag(self):
        from control.acquisition_yaml_loader import parse_laser_af_per_stack

        with pytest.raises(ValueError, match="laser_af_per_stack"):
            parse_laser_af_per_stack("sometimes", laser_af=True, z_stack_order="Z FIRST")

    def test_a_yaml_with_laser_af_per_stack_and_one_plane_still_loads(self):
        # Nz 1 makes the option a no-op, not a mistake (Review Focus 1).
        from control.acquisition_yaml_loader import parse_acquisition_dict

        data = parse_acquisition_dict(
            {"z_stack": {"nz": 1, "order": "Z FIRST"}, "autofocus": {"laser_af": True, "laser_af_per_stack": True}}
        )
        assert data.laser_af_per_stack is True and data.nz == 1

    def test_a_yaml_without_the_key_loads_off(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        assert parse_acquisition_dict({"z_stack": {"nz": 3}}).laser_af_per_stack is False


class TestLaserAfRelativeRange:
    def _dict(self, **z_stack):
        z = {"nz": 3, "delta_z_mm": 0.002, "config": "FROM BOTTOM", "laser_af_relative_range_um": [-5, 15]}
        z.update(z_stack)
        return {"z_stack": z, "autofocus": {"laser_af": True}}

    def test_relative_range_round_trips_and_requires_laser_af_and_from_bottom(self, tmp_path):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        assert parse_acquisition_dict(self._dict()).laser_af_relative_range_um == (-5.0, 15.0)

        no_af = self._dict()
        no_af["autofocus"] = {"laser_af": False}
        with pytest.raises(ValueError, match="needs laser AF"):
            parse_acquisition_dict(no_af)

        with pytest.raises(ValueError, match="A relative Z-range runs from Z-min up"):
            parse_acquisition_dict(self._dict(config="FROM TOP"))

    def test_saved_yaml_reads_back_the_relative_range(self, tmp_path):
        (tmp_path / "acquisition.yaml").write_text(
            "z_stack:\n  delta_z_mm: 0.002\n  config: FROM BOTTOM\n  laser_af_relative_range_um: [-5, 15]\n"
            "autofocus:\n  laser_af: true\n"
        )
        assert parse_acquisition_yaml(str(tmp_path)).laser_af_relative_range_um == (-5.0, 15.0)

    @pytest.mark.parametrize("value", [[-5, 15, 20], [-5], [], "-5, 15", ["a", "b"], [True, 15], [None, 15]])
    def test_a_malformed_relative_range_is_refused_not_dropped(self, value):
        # [-5, 15, 20] used to load as no range at all, and the run went ahead without the requested window.
        from control.acquisition_yaml_loader import parse_acquisition_dict

        with pytest.raises(ValueError, match=r"laser_af_relative_range_um must be \[z_min, z_max\]"):
            parse_acquisition_dict(self._dict(laser_af_relative_range_um=value))

    def test_a_null_relative_range_is_no_range(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        assert parse_acquisition_dict(self._dict(laser_af_relative_range_um=None)).laser_af_relative_range_um is None

    def test_relative_and_absolute_range_together_are_refused(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        with pytest.raises(ValueError, match="both"):
            parse_acquisition_dict(self._dict(piezo_z_range_um=[20, 26]))

    @pytest.mark.parametrize("delta_z_um", [0.0, -1.0, float("nan"), float("inf")])
    def test_relative_range_with_invalid_spacing_is_refused_at_load(self, delta_z_um):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        with pytest.raises(ValueError, match="positive"):
            parse_acquisition_dict(self._dict(delta_z_mm=delta_z_um / 1000))

    def test_absolute_piezo_range_with_laser_af_is_still_refused_at_load(self):
        from control.acquisition_yaml_loader import parse_acquisition_dict

        data = self._dict(piezo_z_range_um=[20, 26])
        del data["z_stack"]["laser_af_relative_range_um"]
        with pytest.raises(ValueError, match="absolute piezo Z-range can't run with laser AF"):
            parse_acquisition_dict(data)

    def test_validate_hardware_refuses_a_relative_range_this_scope_cannot_run(self):
        def data(use_piezo, rng=(-5.0, 15.0)):
            return AcquisitionYAMLData(
                widget_type="wellplate",
                use_piezo=use_piezo,
                laser_af=True,
                delta_z_um=2.0,
                z_stacking_config="FROM BOTTOM",
                laser_af_relative_range_um=rng,
            )

        on_piezo_scope = lambda d: validate_hardware(d, "20x", (1, 1), piezo_travel_um=300.0)
        assert on_piezo_scope(data(use_piezo=True)).is_valid is True
        assert on_piezo_scope(data(use_piezo=False)).is_valid is False
        too_long = on_piezo_scope(data(use_piezo=True, rng=(0.0, 400.0)))
        assert too_long.is_valid is False and too_long.piezo_mismatch is True
        assert validate_hardware(data(use_piezo=False), "20x", (1, 1), piezo_travel_um=None).is_valid is True
