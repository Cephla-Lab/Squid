"""
Unit tests for control/acquisition_yaml_loader.py
"""

from pathlib import Path

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


RECORD_ZSTACK_FIXTURES = Path(__file__).parent.parent / "files" / "record_zstack"


def _legacy(recording=None, z_stack=None):
    """Convert pre-table recording / z_stack sections; returns (rows, plan offsets, recorded offsets, notes)."""
    from control.acquisition_yaml_loader import _convert_legacy_record_zstack
    from control.core.record_zstack_controller import (
        RecordSliceSelection,
        ZStackPlan,
        recording_offsets_for_plan,
        zstack_offsets_for_mode,
    )

    rows, z, slices, notes = _convert_legacy_record_zstack(recording or {}, z_stack or {})
    plan = ZStackPlan(mode=z["z_mode"], dz_um=z["z_dz_um"], nz=z["z_nz"], z_range_um=z["z_range_um"])
    selection = RecordSliceSelection.from_yaml_dict(slices)
    recorded = [offset for _index, offset in recording_offsets_for_plan(plan, selection, 0.0)]
    return rows, zstack_offsets_for_mode(plan, 0.0), recorded, notes


def _rec(**fields):
    return {"enabled": True, "channel": {"name": "BF"}, "fps": 20.0, "duration_s": 2.0, **fields}


def _stack(z_min, z_max, step, names=("488",), enabled=True):
    channels = [{"name": n} for n in names]
    return {"enabled": enabled, "channels": channels, "z_min_um": z_min, "z_max_um": z_max, "z_step_um": step}


class TestParseRecordZstackYAML:
    """Tests for parse_acquisition_yaml with widget_type: record_zstack (the Time at XY tab)."""

    def test_parse_time_at_xy_schema(self):
        """A file as Spencer's build saves it: every field of his _save_record_zstack_yaml."""
        result = parse_acquisition_yaml(str(RECORD_ZSTACK_FIXTURES / "spencer_time_at_xy.yaml"))

        assert result.widget_type == "record_zstack"
        assert result.legacy_schema is False and result.legacy_notes == []
        assert result.xy_mode == "Select Wells"
        assert result.objective_name == "20x"
        assert result.camera_binning == (2, 2)
        assert (result.nt, result.delta_t_s) == (6, 600.0)
        assert (result.laser_af, result.contrast_af, result.use_focus_map) == (True, False, False)
        assert [row["channel"]["name"] for row in result.channels] == [
            "Fluorescence 488 nm Ex",
            "Fluorescence 561 nm Ex",
            "BF LED matrix full",
        ]
        first = result.channels[0]
        assert (first["fps"], first["duration_s"], first["zi"], first["r"], first["zf"]) == (
            50.0,
            10.0,
            True,
            True,
            True,
        )
        assert first["camera"]["binning"] == 2 and first["camera"]["pixel_format"] == "MONO8"
        assert "camera" not in result.channels[1]
        assert (result.record_slice_roles, result.record_slice_indices) == (["bottom", "mid", "top"], [2])
        assert (result.z_mode, result.z_dz_um, result.z_nz, result.z_range_um) == ("Relative", 1.5, 9, 12.0)
        assert (result.z_min_mm, result.z_max_mm) == (0.0, 0.0)
        assert (result.scan_size_mm, result.overlap_percent) == (0.5, 10.0)
        assert [r["name"] for r in result.wellplate_regions] == ["B2", "B3"]

    def test_parse_record_zstack_yaml_minimal_defaults(self, tmp_path):
        yaml_file = tmp_path / "test_minimal_record.yaml"
        yaml_file.write_text("acquisition:\n  widget_type: record_zstack\nchannels: []\n")

        result = parse_acquisition_yaml(str(yaml_file))

        assert result.widget_type == "record_zstack"
        assert result.legacy_schema is False
        assert result.channels == []
        assert (result.z_mode, result.z_dz_um, result.z_nz) == ("From Bottom", 1.0, 1)
        assert (result.record_slice_roles, result.record_slice_indices) == (["bottom", "mid", "top"], [])
        assert (result.laser_af, result.contrast_af, result.use_focus_map) == (False, False, False)
        assert result.wellplate_regions is None

    def test_validate_hardware_accepts_record_zstack_yaml_data(self, tmp_path):
        """validate_hardware() duck-types .objective_name/.camera_binning; must work unchanged."""
        yaml_content = """
acquisition:
  widget_type: record_zstack
objective:
  name: 20x
  camera_binning: [1, 1]
"""
        yaml_file = tmp_path / "test_record_hw.yaml"
        yaml_file.write_text(yaml_content)
        result = parse_acquisition_yaml(str(yaml_file))

        validation = validate_hardware(result, current_objective="20x", current_binning=(1, 1))
        assert validation.is_valid is True

    # ---- files written by the tab this one replaces (recording: / z_stack: phases)

    def test_legacy_file_with_both_phases(self):
        """Written by the previous tab's own code: recording at the reference, then a -3..+3 µm stack."""
        result = parse_acquisition_yaml(str(RECORD_ZSTACK_FIXTURES / "legacy_4dcell_recording_and_zstack.yaml"))

        assert result.legacy_schema is True
        assert result.legacy_notes == []  # the converted acquisition is the same acquisition
        assert [(r["channel"]["name"], r["zi"], r["r"], r["zf"]) for r in result.channels] == [
            ("BF LED matrix full", False, True, False),
            ("DF LED matrix", False, False, True),
            ("Fluorescence 405 nm Ex", False, False, True),
        ]
        rec = result.channels[0]
        assert (rec["fps"], rec["duration_s"]) == (30.0, 5.0)  # the requested rate; effective_fps is per run
        assert rec["channel"]["camera_settings"]["exposure_time_ms"] == 20.0
        assert (result.z_mode, result.z_dz_um, result.z_range_um) == ("Relative", 1.0, 6.0)
        assert (result.record_slice_roles, result.record_slice_indices) == (["mid"], [])
        assert (result.nt, result.delta_t_s, result.laser_af) == (3, 60.0, False)
        assert (result.contrast_af, result.use_focus_map) == (False, False)
        assert (result.xy_mode, result.scan_size_mm, result.overlap_percent) == ("Select Wells", 0.5, 15.0)
        assert [r["name"] for r in result.wellplate_regions] == ["B2", "B3"]

    def test_legacy_file_with_a_multi_plane_recording_and_loaded_coordinates(self):
        result = parse_acquisition_yaml(str(RECORD_ZSTACK_FIXTURES / "legacy_4dcell_multiplane_recording.yaml"))

        assert result.legacy_schema is True and result.legacy_notes == []
        assert [(r["channel"]["name"], r["r"], r["zf"]) for r in result.channels] == [
            ("BF LED matrix full", True, False)
        ]
        # planes at -2 / 0 / +2 µm: a Relative stack of three planes, all recorded
        assert (result.z_mode, result.z_dz_um, result.z_range_um) == ("Relative", 2.0, 4.0)
        assert (result.record_slice_roles, result.record_slice_indices) == (["bottom", "mid", "top"], [])
        assert result.laser_af is True
        assert result.xy_mode == "Load Coordinates"
        assert [r["fovs"] for r in result.wellplate_regions] == [[[10.0, 12.0, 1.5]], [[11.0, 12.5]]]

    def test_legacy_without_any_section_keeps_the_defaults(self, tmp_path):
        yaml_file = tmp_path / "bare.yaml"
        yaml_file.write_text("acquisition:\n  widget_type: record_zstack\n")
        result = parse_acquisition_yaml(str(yaml_file))
        assert result.legacy_schema is True and result.channels == []
        assert (result.z_mode, result.z_nz) == ("From Bottom", 1)

    @pytest.mark.parametrize(
        "bottom, nz, dz, planes, recorded",
        [
            (0.0, 1, 1.0, [0.0], [0.0]),
            (0.0, 3, 2.0, [0.0, 2.0, 4.0], [0.0, 2.0, 4.0]),
            (5.0, 1, 1.0, [0.0, 5.0], [5.0]),  # one plane above the reference: record Top of two
            (-1.5, 1, 1.0, [-1.5, 0.0, 1.5], [-1.5]),  # one plane below: record Bottom of a Relative stack
            (4.0, 2, 2.0, [0.0, 2.0, 4.0, 6.0], [4.0, 6.0]),
            (-2.0, 3, 2.0, [-2.0, 0.0, 2.0], [-2.0, 0.0, 2.0]),
            (-6.0, 2, 2.0, [-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0], [-6.0, -4.0]),
            (-2.0, 5, 2.0, [-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0], [-2.0, 0.0, 2.0, 4.0, 6.0]),
        ],
    )
    def test_legacy_recording_only_keeps_its_planes(self, bottom, nz, dz, planes, recorded):
        """Without a z-stack the z block only has to place the recordings; unrecorded planes are never visited."""
        rows, offsets, rec_offsets, notes = _legacy(_rec(bottom_z_offset_um=bottom, nz=nz, dz_um=dz))
        assert [(r["channel"]["name"], r["fps"], r["duration_s"], r["r"]) for r in rows] == [("BF", 20.0, 2.0, True)]
        assert offsets == pytest.approx(planes)
        assert rec_offsets == pytest.approx(recorded)
        assert notes == []

    def test_legacy_recording_planes_off_the_dz_grid_are_reported(self):
        rows, offsets, rec_offsets, notes = _legacy(_rec(bottom_z_offset_um=-1.0, nz=3, dz_um=2.0))
        assert rec_offsets == pytest.approx([0.0, 2.0, 4.0])
        assert len(notes) == 1 and "offset is dropped" in notes[0]

    @pytest.mark.parametrize(
        "z_min, z_max, step, planes",
        [
            (-3.0, 3.0, 1.0, [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]),
            (-3.0, 3.5, 1.0, [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]),  # the old stack stopped at +3 too
            (0.0, 5.0, 2.0, [0.0, 2.0, 4.0]),
            (0.0, 0.0, 1.0, [0.0]),
        ],
    )
    def test_legacy_zstack_only_keeps_its_planes(self, z_min, z_max, step, planes):
        rows, offsets, _rec_offsets, notes = _legacy(None, _stack(z_min, z_max, step, names=("488", "561")))
        assert [(r["channel"]["name"], r["zi"], r["r"], r["zf"]) for r in rows] == [
            ("488", False, False, True),
            ("561", False, False, True),
        ]
        assert offsets == pytest.approx(planes)
        assert notes == []

    @pytest.mark.parametrize(
        "z_min, z_max, step, planes, fragment",
        [
            (-2.0, 8.0, 2.0, [0.0, 2.0, 4.0, 6.0, 8.0, 10.0], "bottom offset is dropped"),
            (-2.5, 2.5, 1.0, [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0], "no plane on the reference"),
        ],
    )
    def test_legacy_zstack_ranges_without_an_equivalent_are_reported(self, z_min, z_max, step, planes, fragment):
        """The table's z block has no bottom offset, so these stacks move; the note says how."""
        _rows, offsets, _rec_offsets, notes = _legacy(None, _stack(z_min, z_max, step))
        assert offsets == pytest.approx(planes)
        assert len(notes) == 1 and fragment in notes[0]

    def test_legacy_recording_planes_are_mapped_onto_the_stack(self):
        # planes at -2 and +2 are planes of a -4..+4 / 2 µm stack: exact
        _rows, offsets, rec_offsets, notes = _legacy(_rec(bottom_z_offset_um=-2.0, nz=2, dz_um=4.0), _stack(-4, 4, 2))
        assert offsets == pytest.approx([-4.0, -2.0, 0.0, 2.0, 4.0])
        assert rec_offsets == pytest.approx([-2.0, 2.0]) and notes == []
        # planes at 1.5 / 2.3 / 3.1 are not planes of a -2..+2 / 0.5 µm stack: nearest planes, reported
        _rows, _offsets, rec_offsets, notes = _legacy(_rec(bottom_z_offset_um=1.5, nz=3, dz_um=0.8), _stack(-2, 2, 0.5))
        assert rec_offsets == pytest.approx([1.5, 2.0])
        assert len(notes) == 1 and "nearest" in notes[0] and "+3.1 -> +2" in notes[0]

    def test_legacy_disabled_phases_keep_their_rows_unticked(self):
        rows, _offsets, _recorded, notes = _legacy(_rec(enabled=False), _stack(-1, 1, 1, enabled=False))
        assert [(r["channel"]["name"], r["zi"], r["r"], r["zf"]) for r in rows] == [
            ("BF", False, False, False),
            ("488", False, False, False),
        ]
        assert notes == []

    def test_legacy_channel_in_both_phases_becomes_one_row(self):
        recording = _rec()
        recording["channel"] = {"name": "488", "camera_settings": {"exposure_time_ms": 5.0}}
        z_stack = _stack(-1, 1, 1)
        z_stack["channels"] = [{"name": "488", "camera_settings": {"exposure_time_ms": 50.0}}, {"name": "561"}]
        rows, _offsets, _recorded, notes = _legacy(recording, z_stack)
        assert [(r["channel"]["name"], r["r"], r["zf"]) for r in rows] == [("488", True, True), ("561", False, True)]
        assert rows[0]["channel"]["camera_settings"]["exposure_time_ms"] == 5.0  # the recording copy wins
        assert len(notes) == 1 and "different settings" in notes[0]


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
