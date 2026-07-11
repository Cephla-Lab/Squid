import pytest
import yaml

import control.microscope
import tests.control.test_stubs as ts
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


def test_z_plan_points_bakes_plane_into_coordinates(service, sim_scope, tmp_path):
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    # plane: z = 1.0 + 0.01*x  (pure x tilt)
    config = {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {
            "wells": "A1,A3",
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "z_plan": {
                "type": "focus_map",
                "points": [[0.0, 0.0, 1.0], [100.0, 0.0, 2.0], [0.0, 100.0, 1.0]],
            },
        },
    }
    p = tmp_path / "acq.yaml"
    p.write_text(yaml.safe_dump(config))
    from control.acquisition_yaml_loader import parse_acquisition_yaml

    yaml_data = parse_acquisition_yaml(str(p))
    raw = yaml.safe_load(open(str(p)))
    z0 = sim_scope.stage.get_pos().z_mm
    service._configure_regions(yaml_data, raw, None, None, z0)
    service._apply_z_plan_points(yaml_data.z_plan["points"])
    sc = service._scan_coordinates
    a1 = sc.region_fov_coordinates["A1"][0]
    a3 = sc.region_fov_coordinates["A3"][0]
    # A3 is right of A1 (larger x) -> larger z on this plane
    assert a3[0] > a1[0]
    assert a3[2] > a1[2]
    expected_a1_z = 1.0 + (a1[0] / 100.0) * 1.0
    assert a1[2] == pytest.approx(expected_a1_z, abs=1e-6)


def test_gen_focus_map_flag_survives_reset_when_requested(service, sim_scope, tmp_path):
    # Real signature: _reset_z_range_and_focus_map(z0, nz, delta_z_um, keep_gen_focus_map=False)
    service._mpc.set_gen_focus_map_flag(True)
    service._reset_z_range_and_focus_map(1.0, 1, 1.0, keep_gen_focus_map=True)
    assert service._mpc.gen_focus_map is True
    service._reset_z_range_and_focus_map(1.0, 1, 1.0)
    assert service._mpc.gen_focus_map is False
