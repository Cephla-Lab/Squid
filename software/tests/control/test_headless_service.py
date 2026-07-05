"""End-to-end test of the headless wiring (squid_service.headless).

The other core-service tests build their MultiPointController from test stubs;
these go through the production factory used by main_headless.py, proving a
GUI-free process can serve the full API and run acquisitions.
"""

import pytest
import yaml

import control._def
import control.microscope
from squid_service.headless import create_headless_service
from squid_service.models import AcquisitionRequest, MoveRequest
from squid_service.state import InstrumentState


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def service(sim_scope, tmp_path):
    return create_headless_service(
        sim_scope,
        simulation=True,
        job_persist_path=tmp_path / "last_job.json",
        methods_dir=tmp_path / "methods",
    )


def _write_yaml(tmp_path, sim_scope):
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    config = {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "regions": [{"name": "A1", "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}],
        },
    }
    path = tmp_path / "acquisition.yaml"
    path.write_text(yaml.safe_dump(config))
    return str(path)


def test_headless_service_basic_commands(service):
    status = service.status()
    assert status["state"] == InstrumentState.INITIALIZED.value
    assert service.capabilities()["channels"]

    before = service.get_position()
    moved = service.move(MoveRequest(mode="relative", x=1.0))
    assert moved["position"]["x_mm"] == pytest.approx(before["x_mm"] + 1.0, abs=0.01)


def test_headless_laser_af_shares_microscope_instance(service, sim_scope):
    if not (control._def.SUPPORT_LASER_AUTOFOCUS and sim_scope.addons.camera_focus):
        pytest.skip("laser AF not enabled in this configuration")
    assert sim_scope.laser_autofocus_controller is not None
    assert service._mpc.laserAutoFocusController is sim_scope.laser_autofocus_controller


def test_headless_full_acquisition(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        experiment_id="headless_test",
        overrides={"output_path": str(tmp_path / "out")},
    )
    handle = service.start_acquisition(req)
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0), "acquisition did not finish"
    job = service.get_job(handle["job_id"])
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "SUCCESS"
    assert job["progress"]["images_acquired"] > 0
    assert service.state == InstrumentState.INITIALIZED
