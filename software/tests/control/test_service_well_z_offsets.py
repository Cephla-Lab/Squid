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


def _write_yaml(tmp_path, sim_scope, offsets, laser_af):
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    config = {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": laser_af},
        "wellplate_scan": {
            "wells": "A1:A2",
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "well_z_offsets_um": offsets,
        },
    }
    p = tmp_path / "acq.yaml"
    p.write_text(yaml.safe_dump(config))
    return str(p)


def test_offsets_require_laser_af(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope, {"A1": 3.0}, laser_af=False),
        overrides={"output_path": str(tmp_path)},
    )
    result = service.preflight(req)
    assert result["ok"] is False
    # Preflight check results carry the human-readable text under "message"
    # (see SquidCoreService._run_checks_report), not "error".
    assert any("well_z_offsets_um" in (c.get("message") or "") for c in result["checks"] if not c["ok"])


def test_offsets_resolved_with_default(service, sim_scope, tmp_path):
    yaml_path = _write_yaml(tmp_path, sim_scope, {"A1": 3.0, "default": -1.5}, laser_af=True)
    from control.acquisition_yaml_loader import parse_acquisition_yaml

    yaml_data = parse_acquisition_yaml(yaml_path)
    resolved = service._resolve_well_z_offsets(yaml_data.well_z_offsets_um, ["A1", "A2"])
    assert resolved == {"A1": 3.0, "A2": -1.5}


def test_offsets_default_zero_omitted(service, sim_scope, tmp_path):
    resolved = service._resolve_well_z_offsets({"A1": 3.0}, ["A1", "A2"])
    assert resolved == {"A1": 3.0}
