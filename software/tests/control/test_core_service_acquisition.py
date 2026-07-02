import time

import pytest
import yaml

import control.microscope
import tests.control.test_stubs as ts
from squid_service.faults import FaultCategory, FaultError
from squid_service.models import AcquisitionRequest
from squid_service.service import SquidCoreService
from squid_service.state import InstrumentState


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def service(sim_scope, tmp_path):
    mpc = ts.get_test_multi_point_controller(sim_scope)
    svc = SquidCoreService(
        microscope=sim_scope,
        multipoint_controller=mpc,
        scan_coordinates=mpc.scanCoordinates,
        simulation=True,
        job_persist_path=tmp_path / "last_job.json",
        methods_dir=tmp_path / "methods",
    )
    return svc


def _first_channel(sim_scope):
    objective = sim_scope.objective_store.current_objective
    return sim_scope.live_controller.get_channels(objective)[0].name


def _config(sim_scope, wells_region="A1"):
    return {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "time_series": {"nt": 1, "delta_t_s": 0.0},
        "channels": [{"name": _first_channel(sim_scope)}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {
            "scan_size_mm": 0.5,
            "overlap_percent": 10,
            "regions": [{"name": wells_region, "center_mm": [14.3, 11.36, 0.5], "shape": "Square"}],
        },
    }


def _write_yaml(tmp_path, sim_scope, wells_region="A1"):
    path = tmp_path / "acquisition.yaml"
    path.write_text(yaml.safe_dump(_config(sim_scope, wells_region)))
    return str(path)


# ---- preflight ----------------------------------------------------------


def test_preflight_ok(service, sim_scope, tmp_path):
    req = AcquisitionRequest(yaml_path=_write_yaml(tmp_path, sim_scope), overrides={"output_path": str(tmp_path)})
    result = service.preflight(req)
    assert result["ok"] is True
    names = {c["name"] for c in result["checks"]}
    assert {"yaml", "widget_type", "hardware", "channels", "regions", "output_path"} <= names


def test_preflight_reports_bad_yaml_path(service):
    result = service.preflight(AcquisitionRequest(yaml_path="/nonexistent/acq.yaml"))
    assert result["ok"] is False
    assert any(c["name"] == "yaml" and not c["ok"] for c in result["checks"])


def test_preflight_reports_unknown_channel(service, sim_scope, tmp_path):
    path = _write_yaml(tmp_path, sim_scope)
    text = (tmp_path / "acquisition.yaml").read_text().replace(_first_channel(sim_scope), "No Such Channel")
    (tmp_path / "acquisition.yaml").write_text(text)
    result = service.preflight(AcquisitionRequest(yaml_path=path))
    assert result["ok"] is False
    assert any(c["name"] == "channels" and not c["ok"] for c in result["checks"])


# ---- full lifecycle -----------------------------------------------------


def test_full_acquisition_lifecycle(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        experiment_id="svc_test",
        overrides={"output_path": str(tmp_path / "out")},
    )
    q = service.events.subscribe()
    handle = service.start_acquisition(req)
    assert handle["job_id"]
    assert handle["expected_fov_count"] >= 1
    assert handle["expected_image_count"] >= 1
    assert service.state in (
        InstrumentState.ACQUIRING,
        InstrumentState.PROCESSING,
        InstrumentState.INITIALIZED,
    )

    assert service.jobs.wait(handle["job_id"], timeout_s=120.0), "acquisition did not finish"
    job = service.get_job(handle["job_id"])
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "SUCCESS"
    assert job["result"]["end_reason"] == "completed"
    assert service.state == InstrumentState.INITIALIZED

    seen = []
    while not q.empty():
        seen.append(q.get_nowait())
    kinds = [e.event for e in seen]
    assert "job_completed" in kinds
    assert any(e.event == "state_changed" and e.data["new"] == "ACQUIRING" for e in seen)
    service.events.unsubscribe(q)

    assert service.last_job()["job_id"] == handle["job_id"]


def test_second_acquisition_rejected_while_running(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "out2")},
    )
    handle = service.start_acquisition(req)
    try:
        with pytest.raises(FaultError) as exc:
            service.start_acquisition(req)
        assert exc.value.fault.category == FaultCategory.PROTOCOL
        assert exc.value.fault.code == 1002  # PROTOCOL_WRONG_STATE
    finally:
        assert service.jobs.wait(handle["job_id"], timeout_s=120.0)


def test_abort_acquisition(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "out3"), "wells": "A1:B3"},  # more work to abort
    )
    handle = service.start_acquisition(req)
    time.sleep(0.5)
    result = service.abort_job(handle["job_id"], timeout_s=120.0)
    assert result["timed_out"] is False
    job = result["job"]
    assert job["state"] == "COMPLETED"
    # user_abort maps to ABORTED; a fast sim run may legitimately finish first
    assert job["outcome"] in ("ABORTED", "SUCCESS")
    assert service.state == InstrumentState.INITIALIZED


def test_get_job_unknown_id_faults(service):
    with pytest.raises(FaultError) as exc:
        service.get_job("doesnotexist")
    assert exc.value.fault.code == 1001  # PROTOCOL_UNKNOWN_RESOURCE


# ---- URS delta (LA-WC-0001) ---------------------------------------------


def test_run_by_method_e2e(service, sim_scope, tmp_path):
    service.create_method("routine_a", _config(sim_scope))
    req = AcquisitionRequest(
        method="routine_a",
        operator="alice",
        scheduler_job_id="sched-42",
        overrides={"output_path": str(tmp_path / "meth_out")},
    )
    handle = service.start_acquisition(req)
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0)
    job = service.get_job(handle["job_id"])
    assert job["outcome"] == "SUCCESS"
    assert job["operator"] == "alice"
    assert job["scheduler_job_id"] == "sched-42"

    import json
    import os

    api_json = os.path.join(handle["output_dir"], "api_request.json")
    assert os.path.exists(api_json)
    with open(api_json) as f:
        payload = json.load(f)
    assert payload["operator"] == "alice"
    assert payload["source"] == "routine_a"


def test_method_crud_and_delete_while_running(service, sim_scope, tmp_path):
    service.create_method("crud_m", _config(sim_scope))
    assert any(m["name"] == "crud_m" for m in service.list_methods()["methods"])
    assert service.get_method("crud_m")["config"]["acquisition"]["widget_type"] == "wellplate"

    updated = _config(sim_scope)
    updated["time_series"]["nt"] = 2
    service.update_method("crud_m", updated)
    assert service.get_method("crud_m")["config"]["time_series"]["nt"] == 2

    # delete-while-running rejection (URS API-METH-005)
    service.create_method("run_m", _config(sim_scope))
    req = AcquisitionRequest(method="run_m", overrides={"output_path": str(tmp_path / "crud_out")})
    handle = service.start_acquisition(req)
    try:
        with pytest.raises(FaultError) as exc:
            service.delete_method("run_m")
        assert exc.value.fault.category == FaultCategory.PROTOCOL
        assert exc.value.fault.code == 1002  # PROTOCOL_WRONG_STATE
    finally:
        assert service.jobs.wait(handle["job_id"], timeout_s=120.0)

    # deletion succeeds once idle
    service.delete_method("crud_m")
    assert not any(m["name"] == "crud_m" for m in service.list_methods()["methods"])


def test_grid_acquisition_e2e(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        grid={"wells": "A1", "channels": [_first_channel(sim_scope)], "nx": 1, "ny": 1},
        overrides={"output_path": str(tmp_path / "grid_out")},
    )
    handle = service.start_acquisition(req)
    assert handle["expected_fov_count"] >= 1
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0)
    assert service.get_job(handle["job_id"])["outcome"] == "SUCCESS"


def test_autofocus_override_respected(service, sim_scope, tmp_path):
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        autofocus={"reflection": False, "contrast": False},
        overrides={"output_path": str(tmp_path / "af_out")},
    )
    handle = service.start_acquisition(req)
    try:
        assert service._mpc.do_reflection_af is False
        assert service._mpc.do_autofocus is False
    finally:
        assert service.jobs.wait(handle["job_id"], timeout_s=120.0)


def test_validate_method_ok(service, sim_scope):
    service.create_method("valid_m", _config(sim_scope))
    result = service.validate_method("valid_m")
    assert result["ok"] is True
    names = {c["name"] for c in result["checks"]}
    assert "output_path" not in names
    assert {"yaml", "widget_type", "hardware", "channels", "regions"} <= names
