import pytest
import yaml

import control.microscope
import tests.control.test_stubs as ts
from squid_service.models import AcquisitionRequest
from squid_service.service import SquidCoreService


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def service(sim_scope, tmp_path):
    mpc = ts.get_test_multi_point_controller(sim_scope)
    return SquidCoreService(
        microscope=sim_scope,
        multipoint_controller=mpc,
        scan_coordinates=mpc.scanCoordinates,
        simulation=True,
        job_persist_path=tmp_path / "last_job.json",
        methods_dir=tmp_path / "methods",
    )


def _write_yaml(tmp_path, sim_scope, wellplate_scan):
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    config = {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {"wells": "A1:A2", "overlap_percent": 10, **wellplate_scan},
    }
    path = tmp_path / "acquisition.yaml"
    path.write_text(yaml.safe_dump(config))
    return str(path)


def _preflight_and_configure(service, yaml_path, tmp_path):
    req = AcquisitionRequest(yaml_path=yaml_path, overrides={"output_path": str(tmp_path)})
    assert service.preflight(req)["ok"] is True
    # drive region configuration exactly as start_acquisition does, without running
    from control.acquisition_yaml_loader import parse_acquisition_yaml
    import yaml as _y

    yaml_data = parse_acquisition_yaml(yaml_path)
    raw = _y.safe_load(open(yaml_path))
    z0 = service._microscope.stage.get_pos().z_mm
    service._configure_regions(yaml_data, raw, None, None, z0)
    return service._scan_coordinates


def test_centered_grid_counts_and_rowmajor_order(service, sim_scope, tmp_path):
    sc = _preflight_and_configure(
        service,
        _write_yaml(tmp_path, sim_scope, {"fov_pattern": {"type": "centered_grid", "nx": 3, "ny": 2}}),
        tmp_path,
    )
    for well in ("A1", "A2"):
        coords = sc.region_fov_coordinates[well]
        assert len(coords) == 6
        # row-major: y non-decreasing, x increasing within each row
        ys = [c[1] for c in coords]
        assert ys == sorted(ys)
        row0 = coords[0:3]
        assert [c[0] for c in row0] == sorted(c[0] for c in row0)
    # same relative offsets in every well
    a1 = sc.region_fov_coordinates["A1"]
    a2 = sc.region_fov_coordinates["A2"]
    rel1 = [(round(x - a1[0][0], 6), round(y - a1[0][1], 6)) for x, y, *_ in a1]
    rel2 = [(round(x - a2[0][0], 6), round(y - a2[0][1], 6)) for x, y, *_ in a2]
    assert rel1 == rel2


def test_coverage_unchanged(service, sim_scope, tmp_path):
    sc = _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"scan_size_mm": 0.5}), tmp_path)
    assert set(sc.region_fov_coordinates.keys()) == {"A1", "A2"}


def test_grid_subset_filters_rowmajor_tiles(service, sim_scope, tmp_path):
    pattern = {"type": "grid_subset", "nx": 3, "ny": 2, "tiles": [[0, 0], [1, 2]]}
    sc = _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"fov_pattern": pattern}), tmp_path)
    for well in ("A1", "A2"):
        assert len(sc.region_fov_coordinates[well]) == 2
    # tile [0,0] is the grid's min-x/min-y corner; [1,2] is max-x of row 1
    a1 = sc.region_fov_coordinates["A1"]
    assert a1[0][0] < a1[1][0] and a1[0][1] < a1[1][1]
    # identical relative geometry across wells
    a2 = sc.region_fov_coordinates["A2"]
    rel1 = [(round(x - a1[0][0], 6), round(y - a1[0][1], 6)) for x, y, *_ in a1]
    rel2 = [(round(x - a2[0][0], 6), round(y - a2[0][1], 6)) for x, y, *_ in a2]
    assert rel1 == rel2


def test_grid_subset_forces_unidirectional_and_restores(service, sim_scope, tmp_path):
    sc = service._scan_coordinates
    original = sc.fov_pattern
    sc.fov_pattern = "S-Pattern"
    try:
        pattern = {"type": "grid_subset", "nx": 2, "ny": 2, "tiles": [[1, 0]]}
        _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"fov_pattern": pattern}), tmp_path)
        a1 = service._scan_coordinates.region_fov_coordinates["A1"]
        assert len(a1) == 1
        assert service._scan_coordinates.fov_pattern == "S-Pattern"  # restored
    finally:
        sc.fov_pattern = original
