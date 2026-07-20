"""Frozen API contract ("golden") tests for the Squid Core Service REST API.

Each test pins an EXACT surface -- route sets, response key sets, fault codes,
event ordering -- so any change to the public contract fails loudly here. These
tests assert actual behavior; where actual behavior diverged from the written
spec it is pinned as-is and reported in the PR's SPEC DEVIATIONS notes.
"""

import asyncio
import json
import queue
import time

import yaml
from fastapi.testclient import TestClient

from squid_service.config import ServiceConfig
from squid_service.jobs import JobProgress
from squid_service.rest.app import create_app
from squid_service.rest.sse import sse_event_stream


def _yaml_config(channel):
    """The rule-7 minimal wellplate config: wells by name, single tiny FOV per well."""
    return {
        "acquisition": {"widget_type": "wellplate"},
        "sample": {"wellplate_format": "96 well plate"},
        "z_stack": {"nz": 1, "delta_z_mm": 0.001},
        "channels": [{"name": channel}],
        "autofocus": {"contrast_af": False, "laser_af": False},
        "wellplate_scan": {"wells": "A1:A2", "scan_size_mm": 0.1, "overlap_percent": 10},
    }


# ---- 1. Route inventory golden ---------------------------------------------

EXPECTED_ROUTES = {
    ("GET", "/v1/healthz"),
    ("GET", "/v1/sample_formats"),
    ("POST", "/v1/system/initialize"),
    ("POST", "/v1/system/reset"),
    ("GET", "/v1/system/status"),
    ("GET", "/v1/system/heartbeat"),
    ("GET", "/v1/system/capabilities"),
    ("GET", "/v1/system/version"),
    ("GET", "/v1/system/auth_status"),
    ("GET", "/v1/system/faults"),
    ("POST", "/v1/system/reserve"),
    ("POST", "/v1/system/release"),
    ("POST", "/v1/system/shutdown"),
    ("GET", "/v1/motion/position"),
    ("POST", "/v1/motion/move"),
    ("POST", "/v1/motion/home"),
    ("GET", "/v1/imaging/channels"),
    ("POST", "/v1/imaging/channel"),
    ("POST", "/v1/imaging/exposure"),
    ("POST", "/v1/imaging/intensity"),
    ("POST", "/v1/imaging/illumination/on"),
    ("POST", "/v1/imaging/illumination/off"),
    ("GET", "/v1/imaging/objectives"),
    ("GET", "/v1/imaging/objective"),
    ("POST", "/v1/imaging/objective"),
    ("POST", "/v1/imaging/acquire"),
    ("POST", "/v1/imaging/live/start"),
    ("POST", "/v1/imaging/live/stop"),
    ("POST", "/v1/autofocus/run"),
    ("GET", "/v1/autofocus/status"),
    ("POST", "/v1/autofocus/store_reference"),
    ("POST", "/v1/autofocus/correct"),
    ("POST", "/v1/autofocus/acquire_image"),
    ("POST", "/v1/acquisitions/preflight"),
    ("POST", "/v1/acquisitions"),
    ("GET", "/v1/jobs/last"),
    ("GET", "/v1/jobs/{job_id}"),
    ("POST", "/v1/jobs/{job_id}/abort"),
    ("POST", "/v1/jobs/{job_id}/emergency_stop"),
    ("GET", "/v1/methods"),
    ("GET", "/v1/methods/{name}"),
    ("POST", "/v1/methods"),
    ("PUT", "/v1/methods/{name}"),
    ("DELETE", "/v1/methods/{name}"),
    ("POST", "/v1/methods/{name}/validate"),
    ("POST", "/v1/debug/python_exec"),
    ("GET", "/v1/debug/python_exec/status"),
    ("GET", "/v1/debug/settings"),
    ("POST", "/v1/debug/settings"),
    ("GET", "/v1/events"),
}


def test_route_inventory_golden(client):
    # Pins the exact REST surface (method, path); any added/removed route fails here.
    body = client.get("/openapi.json").json()
    actual = {(method.upper(), path) for path, ops in body["paths"].items() for method in ops}
    assert actual == EXPECTED_ROUTES


# ---- 2. Fault envelope goldens ---------------------------------------------


def test_fault_envelope_goldens(client):
    # Pins the canonical {"error": Fault} envelope + representative categories/codes.

    # (a) full Fault key-set on an out-of-range absolute move.
    bad = client.post("/v1/motion/move", json={"mode": "absolute", "x": 99999})
    assert bad.status_code == 400
    error = bad.json()["error"]
    assert set(error.keys()) == {
        "category",
        "code",
        "recoverable",
        "scheduler_action",
        "sequence",
        "component",
        "message",
        "detail",
        "timestamp",
        "terminal",
        "operator_intervention_required",
        "plate_removable",
        "resolved_at",
        "resolved_by",
    }
    assert error["category"] == "INVALID_PARAM"
    assert error["code"] == 2001
    assert error["component"] == "stage.x"
    assert error["detail"]["axis"] == "x"

    # (b) unknown route -> canonical PROTOCOL_UNKNOWN_RESOURCE with the path echoed.
    nope = client.get("/v1/nope")
    assert nope.status_code == 404
    assert nope.json()["error"]["category"] == "PROTOCOL"
    assert nope.json()["error"]["code"] == 1001
    assert nope.json()["error"]["detail"]["path"] == "/v1/nope"

    # (c) unknown job id -> 404 / 1001.
    dj = client.get("/v1/jobs/deadbeef")
    assert dj.status_code == 404
    assert dj.json()["error"]["code"] == 1001

    # (d) schema violation (bad enum) -> 422 / 1003 with a list of field errors.
    sv = client.post("/v1/motion/move", json={"mode": "sideways"})
    assert sv.status_code == 422
    assert sv.json()["error"]["code"] == 1003
    assert isinstance(sv.json()["error"]["detail"]["errors"], list)

    # (e) unknown/extra field rejected (all request models are extra=forbid).
    extra = client.post("/v1/motion/move", json={"x": 1, "bogus": True})
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == 1003

    # (f) unknown channel -> CONFIG_UNKNOWN_CHANNEL (422 / 3001).
    uc = client.post("/v1/imaging/channel", json={"name": "NoSuchChannel"})
    assert uc.status_code == 422
    assert uc.json()["error"]["category"] == "CONFIG"
    assert uc.json()["error"]["code"] == 3001

    # (g) reserved / not-implemented endpoints -> 501 / 1006.
    for path in ("/v1/system/reserve", "/v1/system/release", "/v1/system/shutdown"):
        r = client.post(path)
        assert r.status_code == 501
        assert r.json()["error"]["code"] == 1006
    es = client.post("/v1/jobs/x/emergency_stop")
    assert es.status_code == 501
    assert es.json()["error"]["code"] == 1006

    # (h) python_exec disabled by default -> PROTOCOL_FORBIDDEN (403 / 1005).
    pe = client.post("/v1/debug/python_exec", json={"code": "result=1"})
    assert pe.status_code == 403
    assert pe.json()["error"]["code"] == 1005


# ---- 3. System shapes ------------------------------------------------------


def test_system_shapes(client):
    # Pins idle status/heartbeat/capabilities/version/initialize/reset/sample_formats.
    status = client.get("/v1/system/status").json()
    assert set(status.keys()) == {
        "state",
        "current_job_id",
        "latest_fault",
        "last_acquisition",
        "session_id",
        "server_time",
    }
    assert status["state"] == "INITIALIZED"

    hb = client.get("/v1/system/heartbeat").json()
    assert set(hb.keys()) == {"alive", "monotonic_ns", "state"}
    assert hb["alive"] is True

    cap = client.get("/v1/system/capabilities").json()
    assert set(cap.keys()) == {
        "channels",
        "objectives",
        "current_objective",
        "stage",
        "camera",
        "reflection_af_hardware",
        "simulation",
        "api_version",
        "software_version",
        "firmware_version",
    }
    assert cap["api_version"] == "v1"
    assert cap["simulation"] is True
    assert set(cap["stage"].keys()) == {"x_range_mm", "y_range_mm", "z_range_mm"}
    assert set(cap["camera"].keys()) == {"model", "sensor_size_px", "pixel_size_um"}

    ver = client.get("/v1/system/version").json()
    assert set(ver.keys()) == {"software_version", "api_version", "firmware_version"}

    init = client.post("/v1/system/initialize").json()
    assert set(init.keys()) == {"state", "no_op", "duration_s", "verified_components", "home_performed"}
    assert init["no_op"] is True

    reset = client.post("/v1/system/reset").json()
    assert set(reset.keys()) == {"state", "no_op", "duration_s"}
    assert reset["no_op"] is True

    sf = client.get("/v1/sample_formats").json()
    assert set(sf.keys()) == {"formats"}
    for fmt in sf["formats"]:
        assert set(fmt.keys()) == {
            "name",
            "rows",
            "cols",
            "well_spacing_mm",
            "well_size_mm",
            "a1_x_mm",
            "a1_y_mm",
        }
    by_name = {f["name"]: f for f in sf["formats"]}
    assert "96 well plate" in by_name
    assert by_name["96 well plate"]["rows"] == 8
    assert by_name["96 well plate"]["cols"] == 12


# ---- 4. Acquisition happy-path golden (grid mode) --------------------------


def test_grid_acquisition_happy_path(client, service, first_channel, tmp_path):
    # Pins the accept-handle and completed-job-record contract for a clean grid run.
    body = {
        "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
        "overrides": {"output_path": str(tmp_path / "out")},
    }
    accepted = client.post("/v1/acquisitions", json=body)
    assert accepted.status_code == 202
    handle = accepted.json()
    assert set(handle.keys()) == {
        "job_id",
        "kind",
        "experiment_id",
        "expected_fov_count",
        "expected_image_count",
        "output_dir",
        "accepted_at",
    }
    job_id = handle["job_id"]
    assert accepted.headers["location"] == f"/v1/jobs/{job_id}"
    assert handle["kind"] == "acquisition"
    assert handle["expected_fov_count"] == 1
    assert handle["expected_image_count"] == 1

    assert service.jobs.wait(job_id, timeout_s=60.0), "grid acquisition did not finish"
    job = client.get(f"/v1/jobs/{job_id}").json()
    assert set(job.keys()) == {
        "job_id",
        "kind",
        "experiment_id",
        "origin",
        "operator",
        "scheduler_job_id",
        "state",
        "accepted_at",
        "started_at",
        "completed_at",
        "outcome",
        "progress",
        "result",
        "fault",
    }
    assert job["state"] == "COMPLETED"
    assert job["origin"] == "api"
    assert job["outcome"] == "SUCCESS"
    assert job["fault"] is None
    assert set(job["progress"].keys()) == {
        "images_acquired",
        "total_images",
        "current_region",
        "total_regions",
        "current_timepoint",
        "total_timepoints",
        "elapsed_s",
        "estimated_remaining_s",
        "af_failures",
        "save_failures",
    }
    assert set(job["result"].keys()) == {
        "output_dir",
        "image_count_written",
        "partial_write",
        "errors_encountered",
        "end_reason",
        "skipped_fovs",
    }
    assert job["result"]["end_reason"] == "completed"
    assert job["result"]["skipped_fovs"] == []
    assert job["progress"]["images_acquired"] == 1

    assert client.get("/v1/jobs/last").json()["job_id"] == job_id
    last_acq = client.get("/v1/system/status").json()["last_acquisition"]
    assert set(last_acq.keys()) == {"job_id", "outcome", "completed_at"}


# ---- 5. Event-sequence contract + SSE handshake ----------------------------


async def _never_disconnected() -> bool:
    return False


def test_event_sequence_and_sse_handshake(service, client, first_channel, tmp_path):
    # Pins the SSE session_started handshake shape and the event ordering on a clean run.

    # SSE handshake: the first yielded dict is session_started with a fixed data shape.
    async def _first_sse():
        gen = sse_event_stream(service, "0", _never_disconnected)
        try:
            return await gen.__anext__()
        finally:
            await gen.aclose()

    first = asyncio.run(_first_sse())
    assert first["event"] == "session_started"
    assert set(json.loads(first["data"]).keys()) == {"session_id", "current_state", "last_event_id"}

    # Event ordering over a 1-FOV grid run. Subscribe BEFORE starting.
    q = service.events.subscribe()
    try:
        body = {
            "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
            "overrides": {"output_path": str(tmp_path / "evt_out")},
        }
        accepted = client.post("/v1/acquisitions", json=body)
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]

        events = []
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                ev = q.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                break
            events.append(ev)
            if ev.event == "job_completed":
                break
    finally:
        service.events.unsubscribe(q)

    ids = [e.id for e in events]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))  # strictly increasing
    assert all(e.event in {"state_changed", "progress", "job_completed"} for e in events)

    state_changes = [e for e in events if e.event == "state_changed"]
    for e in state_changes:
        assert set(e.data.keys()) == {"old", "new", "at"}
    assert [(e.data["old"], e.data["new"]) for e in state_changes] == [
        ("INITIALIZED", "ACQUIRING"),
        ("ACQUIRING", "PROCESSING"),
        ("PROCESSING", "INITIALIZED"),
    ]

    # At least one progress event between the first two state_changed events,
    # carrying the right job_id and the progress-model key-set plus job_id.
    first_sc_id, second_sc_id = state_changes[0].id, state_changes[1].id
    progress_between = [e for e in events if e.event == "progress" and first_sc_id < e.id < second_sc_id]
    assert progress_between
    expected_progress_keys = set(JobProgress().model_dump().keys()) | {"job_id"}
    for e in progress_between:
        assert set(e.data.keys()) == expected_progress_keys
        assert e.data["job_id"] == job_id

    assert events[-1].event == "job_completed"
    assert set(events[-1].data.keys()) == {"job_id", "outcome", "completed_at"}
    assert events[-1].data["outcome"] == "SUCCESS"


# ---- 6. z_reference contract -----------------------------------------------


def test_z_reference_z_mm_out_of_range(client, first_channel):
    # Pins z_reference.z_mm out-of-limits -> INVALID_PARAM_OUT_OF_RANGE (400 / 2001 / stage.z).
    r = client.post(
        "/v1/acquisitions",
        json={
            "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
            "z_reference": {"z_mm": 99999},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == 2001
    assert r.json()["error"]["component"] == "stage.z"


def test_z_reference_autofocus_without_af_flag(client, first_channel):
    # Pins z_reference="autofocus" on a grid run (no AF flags) -> INVALID_PARAM_BAD_VALUE (400 / 2002).
    r = client.post(
        "/v1/acquisitions",
        json={
            "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
            "z_reference": "autofocus",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == 2002


def test_z_reference_autofocus_without_stored_reference(client, first_channel):
    # Pins z_reference="autofocus" + reflection AF but no stored reference -> AUTOFOCUS_NOT_READY (503 / 8002).
    # Precondition: the shared sim scope must have NO stored laser-AF reference.
    af = client.get("/v1/autofocus/status").json()
    assert af["reference_set"] is False, "sim scope unexpectedly has a stored AF reference"
    r = client.post(
        "/v1/acquisitions",
        json={
            "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
            "z_reference": "autofocus",
            "autofocus": {"reflection": True},
        },
    )
    assert r.status_code == 503
    assert r.json()["error"]["category"] == "AUTOFOCUS"
    assert r.json()["error"]["code"] == 8002


# ---- 7. Preflight report golden --------------------------------------------


def test_preflight_report_golden(client, first_channel, tmp_path):
    # Pins the preflight report shape + ordered check names for yaml, grid, and failure.
    yaml_path = tmp_path / "pf.yaml"
    yaml_path.write_text(yaml.safe_dump(_yaml_config(first_channel)))

    pf = client.post(
        "/v1/acquisitions/preflight",
        json={"yaml_path": str(yaml_path), "overrides": {"output_path": str(tmp_path / "pf_out")}},
    )
    assert pf.status_code == 200
    body = pf.json()
    assert set(body.keys()) == {"ok", "checks", "free_bytes"}
    assert body["ok"] is True
    assert [c["name"] for c in body["checks"]] == [
        "yaml",
        "widget_type",
        "hardware",
        "channels",
        "regions",
        "z_reference",
        "output_path",
    ]
    for c in body["checks"]:
        assert set(c.keys()) == {"name", "ok", "message"}

    grid_pf = client.post(
        "/v1/acquisitions/preflight",
        json={
            "grid": {"wells": "A1", "channels": [first_channel], "nx": 1, "ny": 1},
            "overrides": {"output_path": str(tmp_path / "gpf_out")},
        },
    )
    assert grid_pf.status_code == 200
    assert [c["name"] for c in grid_pf.json()["checks"]] == [
        "channels",
        "wellplate_format",
        "regions",
        "z_reference",
        "output_path",
    ]

    bad = client.post("/v1/acquisitions/preflight", json={"yaml_path": "/nonexistent.yaml"})
    assert bad.status_code == 200
    bad_body = bad.json()
    assert bad_body["ok"] is False
    assert bad_body["checks"][0]["name"] == "yaml"
    assert bad_body["checks"][0]["ok"] is False
    for c in bad_body["checks"][1:]:
        assert c["message"] == "skipped (yaml check failed)"


# ---- 8. Methods contract ---------------------------------------------------


def test_methods_contract(client, first_channel):
    # Pins the method-registry CRUD + summary/validate/run-by-name contract.
    cfg = _yaml_config(first_channel)

    created = client.post("/v1/methods", json={"name": "contract_m1", "config": cfg})
    assert created.status_code == 201
    assert created.json() == {"name": "contract_m1", "created": True}

    dup = client.post("/v1/methods", json={"name": "contract_m1", "config": cfg})
    assert dup.status_code == 400
    assert dup.json()["error"]["code"] == 2002

    listed = client.get("/v1/methods").json()
    item = next(m for m in listed["methods"] if m["name"] == "contract_m1")
    assert set(item.keys()) == {
        "name",
        "widget_type",
        "channels",
        "objective",
        "wellplate_format",
        "wells",
        "nz",
        "nt",
        "estimated_duration_s",
    }
    assert item["estimated_duration_s"] is None
    assert item["wells"] == "A1:A2"

    got = client.get("/v1/methods/contract_m1").json()
    assert set(got.keys()) == {"name", "config"}
    assert got["config"] == cfg

    put404 = client.put("/v1/methods/never_created", json={"config": {}})
    assert put404.status_code == 404
    assert put404.json()["error"]["code"] == 1001

    validated = client.post("/v1/methods/contract_m1/validate").json()
    assert [c["name"] for c in validated["checks"]] == [
        "yaml",
        "widget_type",
        "hardware",
        "channels",
        "regions",
        "z_reference",
    ]

    badname = client.post("/v1/methods", json={"name": "../evil", "config": {}})
    assert badname.status_code == 400

    deleted = client.delete("/v1/methods/contract_m1")
    assert deleted.json() == {"name": "contract_m1", "deleted": True}
    assert client.delete("/v1/methods/contract_m1").status_code == 404

    ghost = client.post("/v1/acquisitions", json={"method": "ghost"})
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == 1001


# ---- 9. Busy-state + abort contract ----------------------------------------


def test_busy_state_and_abort(client, service, first_channel, tmp_path):
    # Pins concurrency rejection (409 / 1002) mid-run + the abort-result contract.
    # A wide grid (A1:D6 x 2x2 = 96 FOV) keeps the run alive while we probe/abort.
    created = client.post("/v1/methods", json={"name": "busy_del_m", "config": _yaml_config(first_channel)})
    assert created.status_code == 201

    body = {
        "grid": {"wells": "A1:D6", "channels": [first_channel], "nx": 2, "ny": 2},
        "overrides": {"output_path": str(tmp_path / "busy_out")},
    }
    accepted = client.post("/v1/acquisitions", json=body)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    # Wait until the run has actually reached ACQUIRING (<=10s).
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client.get("/v1/system/status").json()["state"] == "ACQUIRING":
            break
        time.sleep(0.05)
    assert client.get("/v1/system/status").json()["state"] == "ACQUIRING", "run never reached ACQUIRING"

    # Concurrency rejections while ACQUIRING.
    second = client.post("/v1/acquisitions", json=body)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == 1002

    mv = client.post("/v1/motion/move", json={"z": 0.01, "mode": "relative"})
    assert mv.status_code == 409
    assert mv.json()["error"]["code"] == 1002
    assert mv.json()["error"]["detail"]["current_state"] == "ACQUIRING"

    dele = client.delete("/v1/methods/busy_del_m")
    assert dele.status_code == 409
    assert dele.json()["error"]["code"] == 1002

    # Abort -> clean, job ABORTED, back to INITIALIZED.
    ab = client.post(f"/v1/jobs/{job_id}/abort", json={"timeout_s": 60})
    assert ab.status_code == 200
    ab_body = ab.json()
    assert set(ab_body.keys()) == {"clean", "timed_out", "job"}
    assert ab_body["clean"] is True
    assert ab_body["timed_out"] is False
    assert ab_body["job"]["outcome"] == "ABORTED"
    assert ab_body["job"]["result"]["end_reason"] == "user_abort"
    assert client.get("/v1/system/status").json()["state"] == "INITIALIZED"

    # Second abort of the same (now completed) job is idempotent.
    ab2 = client.post(f"/v1/jobs/{job_id}/abort", json={"timeout_s": 60})
    assert ab2.status_code == 200
    assert ab2.json()["clean"] is True
    assert ab2.json()["timed_out"] is False


# ---- 10. Wells contract ----------------------------------------------------


def test_wells_validation_faults(client, first_channel):
    # Pins well-range validation: reversed range and malformed name both 400.
    rev = client.post(
        "/v1/acquisitions",
        json={"grid": {"wells": "B2:A1", "channels": [first_channel], "nx": 1, "ny": 1}},
    )
    assert rev.status_code == 400
    assert "Range end before start" in rev.json()["error"]["message"]

    bad = client.post(
        "/v1/acquisitions",
        json={"grid": {"wells": "1A", "channels": [first_channel], "nx": 1, "ny": 1}},
    )
    assert bad.status_code == 400


def test_wells_override_precedence(client, service, first_channel, tmp_path):
    # Pins overrides.wells precedence: the override halves the FOV count vs the yaml's wells.
    client.post("/v1/methods", json={"name": "prec_m", "config": _yaml_config(first_channel)})

    override = client.post(
        "/v1/acquisitions",
        json={"method": "prec_m", "overrides": {"wells": "A1", "output_path": str(tmp_path / "ov_out")}},
    )
    assert override.status_code == 202
    override_fov = override.json()["expected_fov_count"]
    assert override_fov == 1
    assert service.jobs.wait(override.json()["job_id"], timeout_s=60.0)

    full = client.post(
        "/v1/acquisitions",
        json={"method": "prec_m", "overrides": {"output_path": str(tmp_path / "full_out")}},
    )
    assert full.status_code == 202
    full_fov = full.json()["expected_fov_count"]
    assert override_fov == full_fov // 2
    assert service.jobs.wait(full.json()["job_id"], timeout_s=60.0)


# ---- 11. Auth contract -----------------------------------------------------


def test_auth_contract(service):
    # Pins bearer-auth enforcement, the open-path allowlist, and the auth_status shape.
    app = create_app(
        service,
        ServiceConfig(host="127.0.0.1", port=8060, auth_enabled=True, auth_token="contract-secret"),
    )
    auth_client = TestClient(app)

    unauth = auth_client.get("/v1/system/status")
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == 1004
    ok = auth_client.get("/v1/system/status", headers={"Authorization": "Bearer contract-secret"})
    assert ok.status_code == 200

    # Open paths need no token.
    assert auth_client.get("/v1/healthz").status_code == 200
    assert auth_client.get("/v1/system/auth_status").status_code == 200
    assert auth_client.get("/openapi.json").status_code == 200

    assert auth_client.get("/v1/system/auth_status").json() == {
        "auth_enabled": True,
        "bind_to_tls": False,
        "scheme": "bearer",
    }
