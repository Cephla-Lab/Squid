import pytest
import yaml

from control.acquisition_yaml_loader import parse_acquisition_yaml


def _write(tmp_path, wellplate_scan_extra, autofocus=None):
    config = {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": "BF LED matrix full"}],
        "autofocus": autofocus or {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {"wells": "A1:A2", "overlap_percent": 10, **wellplate_scan_extra},
    }
    p = tmp_path / "acq.yaml"
    p.write_text(yaml.safe_dump(config))
    return str(p)


def test_no_pattern_is_none(tmp_path):
    data = parse_acquisition_yaml(_write(tmp_path, {"scan_size_mm": 0.5}))
    assert data.fov_pattern is None
    assert data.well_z_offsets_um is None
    assert data.z_plan is None


def test_centered_grid_normalized(tmp_path):
    data = parse_acquisition_yaml(_write(tmp_path, {"fov_pattern": {"type": "centered_grid", "nx": 3, "ny": 2}}))
    assert data.fov_pattern == {"type": "centered_grid", "nx": 3, "ny": 2, "overlap_percent": 10.0}


def test_grid_subset_tile_bounds(tmp_path):
    with pytest.raises(ValueError, match="tile"):
        parse_acquisition_yaml(
            _write(tmp_path, {"fov_pattern": {"type": "grid_subset", "nx": 2, "ny": 2, "tiles": [[0, 0], [2, 0]]}})
        )


def test_grid_subset_ok(tmp_path):
    data = parse_acquisition_yaml(
        _write(tmp_path, {"fov_pattern": {"type": "grid_subset", "nx": 2, "ny": 2, "tiles": [[0, 0], [1, 1]]}})
    )
    assert data.fov_pattern["tiles"] == [[0, 0], [1, 1]]


def test_random_requires_positive_n(tmp_path):
    with pytest.raises(ValueError, match="n_fovs"):
        parse_acquisition_yaml(_write(tmp_path, {"fov_pattern": {"type": "random", "n_fovs": 0}}))


def test_unknown_pattern_type(tmp_path):
    with pytest.raises(ValueError, match="fov_pattern"):
        parse_acquisition_yaml(_write(tmp_path, {"fov_pattern": {"type": "spiral"}}))


def test_pattern_requires_wells(tmp_path):
    config_extra = {"fov_pattern": {"type": "centered_grid", "nx": 2, "ny": 2}}
    p = tmp_path / "acq2.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "acquisition": {"widget_type": "wellplate"},
                "z_stack": {"nz": 1, "delta_z_mm": 0.001},
                "channels": [{"name": "BF LED matrix full"}],
                "wellplate_scan": {
                    "regions": [{"name": "A1", "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}],
                    **config_extra,
                },
            }
        )
    )
    with pytest.raises(ValueError, match="wells"):
        parse_acquisition_yaml(str(p))


def test_well_z_offsets_validation(tmp_path):
    data = parse_acquisition_yaml(
        _write(tmp_path, {"scan_size_mm": 0.5, "well_z_offsets_um": {"A1": 3.0, "default": -1.5}})
    )
    assert data.well_z_offsets_um == {"A1": 3.0, "default": -1.5}
    with pytest.raises(ValueError, match="well_z_offsets_um"):
        parse_acquisition_yaml(_write(tmp_path, {"scan_size_mm": 0.5, "well_z_offsets_um": {"A1": float("nan")}}))


def test_z_plan_points_exactly_three(tmp_path):
    with pytest.raises(ValueError, match="z_plan"):
        parse_acquisition_yaml(
            _write(tmp_path, {"scan_size_mm": 0.5, "z_plan": {"type": "focus_map", "points": [[0, 0, 1]]}})
        )
    data = parse_acquisition_yaml(
        _write(
            tmp_path,
            {"scan_size_mm": 0.5, "z_plan": {"type": "focus_map", "points": [[0, 0, 1.0], [10, 0, 1.1], [0, 10, 1.2]]}},
        )
    )
    assert data.z_plan["points"] == [[0.0, 0.0, 1.0], [10.0, 0.0, 1.1], [0.0, 10.0, 1.2]]
    data = parse_acquisition_yaml(
        _write(tmp_path, {"scan_size_mm": 0.5, "z_plan": {"type": "focus_map", "generate": True}})
    )
    assert data.z_plan == {"type": "focus_map", "generate": True, "points": None}
    with pytest.raises(ValueError, match="z_plan"):
        parse_acquisition_yaml(
            _write(
                tmp_path,
                {
                    "scan_size_mm": 0.5,
                    "z_plan": {"type": "focus_map", "generate": True, "points": [[0, 0, 1], [1, 0, 1], [0, 1, 1]]},
                },
            )
        )


def test_z_plan_colinear_points_rejected(tmp_path):
    # All three points share y=0 -> colinear in XY -> cannot define a plane.
    # Must raise a clean loader ValueError (surfaced as INVALID_PARAM at preflight)
    # rather than letting interpolate_plane raise deep inside start_acquisition.
    with pytest.raises(ValueError, match="colinear"):
        parse_acquisition_yaml(
            _write(
                tmp_path,
                {"scan_size_mm": 0.5, "z_plan": {"type": "focus_map", "points": [[0, 0, 1], [1, 0, 2], [2, 0, 3]]}},
            )
        )


def test_z_plan_non_colinear_plane_parses(tmp_path):
    data = parse_acquisition_yaml(
        _write(
            tmp_path,
            {"scan_size_mm": 0.5, "z_plan": {"type": "focus_map", "points": [[0, 0, 1.0], [10, 0, 1.1], [0, 10, 1.2]]}},
        )
    )
    assert data.z_plan["points"] == [[0.0, 0.0, 1.0], [10.0, 0.0, 1.1], [0.0, 10.0, 1.2]]
