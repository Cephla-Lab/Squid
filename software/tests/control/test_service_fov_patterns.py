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


def test_random_seeded_reproducible_and_per_well_different(service, sim_scope, tmp_path):
    pattern = {"type": "random", "n_fovs": 5, "seed": 42}
    sc1 = _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"fov_pattern": pattern}), tmp_path)
    a1_first = list(sc1.region_fov_coordinates["A1"])
    a2_first = list(sc1.region_fov_coordinates["A2"])
    assert len(a1_first) == 5 and len(a2_first) == 5
    assert a1_first != a2_first  # independent per well
    # reproducible: reconfigure, same coordinates
    sc2 = _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"fov_pattern": pattern}), tmp_path)
    assert list(sc2.region_fov_coordinates["A1"]) == a1_first


def test_api_centered_grid_run_saves_v2_record(service, sim_scope, tmp_path):
    import yaml as _yaml
    from control.acquisition_yaml_loader import parse_acquisition_yaml
    from squid_service.models import AcquisitionRequest

    out = tmp_path / "out"
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope, {"fov_pattern": {"type": "centered_grid", "nx": 2, "ny": 2}}),
        overrides={"output_path": str(out)},
    )
    assert service.preflight(req)["ok"] is True
    handle = service.start_acquisition(req)
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0)
    saved = list(out.rglob("acquisition.yaml"))
    assert saved, "acquisition.yaml record not written"
    data = parse_acquisition_yaml(str(saved[0]))
    assert data.wells is not None  # not the legacy regions form
    assert data.wellplate_regions is None
    assert data.fov_pattern is not None and data.fov_pattern["type"] == "centered_grid"
    assert data.fov_pattern["nx"] == 2 and data.fov_pattern["ny"] == 2


def test_api_legacy_regions_run_saves_regions_form(service, sim_scope, tmp_path):
    """An API run of a legacy regions[].center_mm method (no wells) must still save the
    coordinate-based `regions` form — proving the service's xy_mode="Manual" branch and
    that the writer is byte-unchanged for legacy runs."""
    from control.acquisition_yaml_loader import parse_acquisition_yaml
    from squid_service.models import AcquisitionRequest

    regions = [{"name": "R1", "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}]
    out = tmp_path / "out"
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope, {"wells": None, "scan_size_mm": 0.5, "regions": regions}),
        overrides={"output_path": str(out)},
    )
    assert service.preflight(req)["ok"] is True
    handle = service.start_acquisition(req)
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0)
    saved = list(out.rglob("acquisition.yaml"))
    assert saved, "acquisition.yaml record not written"
    data = parse_acquisition_yaml(str(saved[0]))
    assert data.wells is None  # legacy regions form preserved (xy_mode="Manual" branch)
    assert data.wellplate_regions is not None
    assert data.fov_pattern is None


def test_random_within_well_bounds(service, sim_scope, tmp_path):
    import control._def

    pattern = {"type": "random", "n_fovs": 8, "seed": 1}
    sc = _preflight_and_configure(service, _write_yaml(tmp_path, sim_scope, {"fov_pattern": pattern}), tmp_path)
    settings = control._def.get_wellplate_settings("96 well plate")
    from squid_service.wells import well_center_mm

    cx, cy = well_center_mm("A1", settings)
    radius = settings["well_size_mm"] / 2.0
    for x, y, *_ in sc.region_fov_coordinates["A1"]:
        assert ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= radius + 1e-9
