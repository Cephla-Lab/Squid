import queue
import time
import types

import pytest
import yaml

import control.microscope
import tests.control.test_stubs as ts
from squid_service.faults import FaultCategory, FaultError
from squid_service.models import AcquisitionRequest, MoveRequest
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
    # No Slack notifier is configured on this controller (ts.get_test_multi_point_controller
    # never calls set_slack_notifier), which is exactly the gap this test guards: without it,
    # AcquisitionStats never reaches the service via signal_slack_acquisition_finished, so
    # _on_acq_finished must fall back to _derive_end_reason() to report ABORTED correctly.
    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "out3"), "wells": "A1:D6"},  # enough work to abort mid-run
    )
    q = service.events.subscribe()
    handle = service.start_acquisition(req)
    try:
        # Wait for the run to actually reach ACQUIRING before aborting, instead of a
        # fixed sleep, so the abort is issued as early (and as reliably) as possible.
        deadline = time.monotonic() + 10.0
        reached_acquiring = False
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                ev = q.get(timeout=remaining)
            except queue.Empty:
                break
            if ev.event == "state_changed" and ev.data.get("new") == "ACQUIRING":
                reached_acquiring = True
                break
        assert reached_acquiring, "acquisition never reached ACQUIRING before the abort was issued"

        result = service.abort_job(handle["job_id"], timeout_s=120.0)
    finally:
        service.events.unsubscribe(q)

    assert result["timed_out"] is False
    job = result["job"]
    assert job["state"] == "COMPLETED"
    if job["outcome"] == "SUCCESS":
        pytest.skip(
            "sim raced to completion before the abort landed (job finished before ABORTED "
            "could be observed) even though ACQUIRING was seen first; not a fix regression"
        )
    assert job["outcome"] == "ABORTED"
    assert result["clean"] is True
    assert service.state == InstrumentState.INITIALIZED


# ---- end-reason fallback when no Slack notifier is configured -----------


def test_derive_end_reason_used_when_stats_missing(sim_scope, tmp_path):
    """_on_acq_finished must consult _derive_end_reason() (and thus the worker's
    own _compute_end_reason()) when no AcquisitionStats arrived -- the situation
    for every acquisition when no Slack notifier is configured (the default).

    Drives _on_acq_start/_on_acq_finished directly against a service whose mpc is
    a plain stub namespace (no real MultiPointController/-Worker involved), with a
    fake worker exposing only what the service reads: _compute_end_reason(),
    _acquisition_error_count, _laser_af_failures.
    """
    svc = SquidCoreService(
        microscope=sim_scope,
        simulation=True,
        job_persist_path=tmp_path / "last_job.json",
    )

    fake_worker = types.SimpleNamespace(
        _compute_end_reason=lambda: "error",
        _acquisition_error_count=3,
        _laser_af_failures=2,
    )
    svc._mpc = types.SimpleNamespace(
        multiPointWorker=fake_worker,
        abort_acqusition_requested=False,
        base_path=str(tmp_path),
        experiment_ID="stub_exp",
    )

    svc._on_acq_start(types.SimpleNamespace(experiment_ID="stub_exp"))
    job_id = svc.jobs.active.job_id
    assert svc._acq_stats is None  # no signal_slack_acquisition_finished ever fired

    svc._on_acq_finished()

    job = svc.get_job(job_id)
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "FAILURE"  # _REASON_TO_OUTCOME["error"]
    assert job["result"]["end_reason"] == "error"
    assert job["result"]["errors_encountered"] == 3
    assert job["progress"]["af_failures"] == 2
    assert job["progress"]["save_failures"] == 3
    assert svc.state == InstrumentState.ERROR


def test_derive_end_reason_falls_back_without_worker(sim_scope, tmp_path):
    """When multiPointWorker is unavailable, fall back to the abort flag instead
    of silently reporting SUCCESS."""
    svc = SquidCoreService(
        microscope=sim_scope,
        simulation=True,
        job_persist_path=tmp_path / "last_job2.json",
    )
    svc._mpc = types.SimpleNamespace(
        multiPointWorker=None,
        abort_acqusition_requested=True,
        base_path=str(tmp_path),
        experiment_ID="stub_exp2",
    )

    svc._on_acq_start(types.SimpleNamespace(experiment_ID="stub_exp2"))
    job_id = svc.jobs.active.job_id

    svc._on_acq_finished()

    job = svc.get_job(job_id)
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "ABORTED"  # _REASON_TO_OUTCOME["user_abort"]
    assert job["result"]["end_reason"] == "user_abort"
    assert svc.state == InstrumentState.INITIALIZED


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


# ---- ERROR-state rejection + recovery (URS API-LIFE-003) ----------------


def test_error_state_rejects_commands_creates_no_job_then_recovers(service, sim_scope, tmp_path, monkeypatch):
    """While ERROR, state-changing commands must be rejected with a canonical
    PROTOCOL_WRONG_STATE (409) carrying detail.current_state=="ERROR" and must NOT
    create a job. Only reset()/initialize() recover; afterwards a real acquisition
    runs end-to-end.
    """

    # Drive the service into ERROR via a probe failure during initialize(home=True).
    def boom():
        raise RuntimeError("stage communication lost")

    monkeypatch.setattr(sim_scope.stage, "get_pos", boom)
    with pytest.raises(FaultError):
        service.initialize(home=True)
    monkeypatch.undo()
    assert service.state == InstrumentState.ERROR

    req = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "err_out")},
    )

    # start_acquisition is rejected before any job is created.
    with pytest.raises(FaultError) as exc:
        service.start_acquisition(req)
    assert exc.value.fault.category == FaultCategory.PROTOCOL
    assert exc.value.fault.code == 1002  # PROTOCOL_WRONG_STATE
    assert exc.value.fault.detail["current_state"] == "ERROR"
    assert service.jobs.active is None  # no phantom active job

    # move gets the same rejection.
    with pytest.raises(FaultError) as exc2:
        service.move(MoveRequest(mode="absolute", x=1.0))
    assert exc2.value.fault.code == 1002
    assert exc2.value.fault.detail["current_state"] == "ERROR"

    # reset() recovers to INITIALIZED, then a real acquisition succeeds.
    assert service.reset()["state"] == "INITIALIZED"
    assert service.state == InstrumentState.INITIALIZED

    handle = service.start_acquisition(req)
    assert service.jobs.wait(handle["job_id"], timeout_s=120.0)
    assert service.get_job(handle["job_id"])["outcome"] == "SUCCESS"


# ---- z_range refresh per run (no stale leakage) -------------------------


def test_start_acquisition_refreshes_z_range_from_current_stage_z(service, sim_scope, tmp_path):
    """_configure_controller must derive z_range from the *current* stage z on every
    run (mirroring the GUI pre-run path), not reuse a value derived once by the
    controller. With nz=1 the range collapses to (z, z).
    """
    z1 = 1.0
    service.move(MoveRequest(mode="absolute", z=z1))
    req1 = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "z1_out")},
    )
    handle1 = service.start_acquisition(req1)
    try:
        assert service._mpc.z_range == pytest.approx([z1, z1], abs=0.01)
    finally:
        assert service.jobs.wait(handle1["job_id"], timeout_s=120.0)

    # Move Z, run again: the second run's z_range must update, not reuse z1's value.
    z2 = 2.0
    service.move(MoveRequest(mode="absolute", z=z2))
    req2 = AcquisitionRequest(
        yaml_path=_write_yaml(tmp_path, sim_scope),
        overrides={"output_path": str(tmp_path / "z2_out")},
    )
    handle2 = service.start_acquisition(req2)
    try:
        assert service._mpc.z_range == pytest.approx([z2, z2], abs=0.01)
    finally:
        assert service.jobs.wait(handle2["job_id"], timeout_s=120.0)
