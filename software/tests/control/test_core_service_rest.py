import asyncio
import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

import control.microscope
import tests.control.test_stubs as ts
from squid_service.config import ServiceConfig
from squid_service.events import EventBus
from squid_service.rest.app import create_app
from squid_service.rest.sse import sse_event_stream
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


@pytest.fixture()
def client(service):
    app = create_app(service, ServiceConfig())
    return TestClient(app)


def _first_channel(sim_scope):
    objective = sim_scope.objective_store.current_objective
    return sim_scope.live_controller.get_channels(objective)[0].name


def _method_config(sim_scope, wells_region="A1"):
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


def test_healthz_and_status(client):
    assert client.get("/v1/healthz").json() == {"alive": True}
    status = client.get("/v1/system/status")
    assert status.status_code == 200
    assert status.json()["state"] == "INITIALIZED"


def test_openapi_and_docs(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_move_and_fault_shape(client, sim_scope):
    max_x = sim_scope.stage.get_config().X_AXIS.MAX_POSITION
    ok = client.post("/v1/motion/move", json={"mode": "absolute", "x": max_x / 2})
    assert ok.status_code == 200
    bad = client.post("/v1/motion/move", json={"mode": "absolute", "x": max_x + 100})
    assert bad.status_code == 400
    error = bad.json()["error"]
    assert error["category"] == "INVALID_PARAM"
    assert error["code"] == 2001
    assert error["scheduler_action"] in (
        "RETRY",
        "ABORT_PLATE",
        "REJECT_PLATE",
        "PAUSE_INSTRUMENT",
        "ESCALATE_OPERATOR",
    )


def test_schema_violation_is_canonical_fault(client):
    r = client.post("/v1/motion/move", json={"mode": "sideways"})
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "PROTOCOL"
    assert r.json()["error"]["code"] == 1003


def test_unknown_channel_is_config_fault(client):
    r = client.post("/v1/imaging/channel", json={"name": "No Such Channel"})
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "CONFIG"


def test_reserved_endpoints_501(client):
    for path in ("/v1/system/reserve", "/v1/system/release", "/v1/system/shutdown"):
        r = client.post(path)
        assert r.status_code == 501
        assert r.json()["error"]["code"] == 1006


def test_auth_enforced_when_enabled(service):
    app = create_app(service, ServiceConfig(auth_enabled=True, auth_token="s3cret"))
    client = TestClient(app)
    assert client.get("/v1/system/status").status_code == 401
    assert client.get("/v1/system/status").json()["error"]["category"] == "PROTOCOL"
    assert client.get("/v1/healthz").status_code == 200  # open path
    auth_status = client.get("/v1/system/auth_status")
    assert auth_status.status_code == 200 and auth_status.json()["auth_enabled"] is True
    ok = client.get("/v1/system/status", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_jobs_last_404_when_none(client):
    r = client.get("/v1/jobs/last")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == 1001


async def _never_disconnected() -> bool:
    return False


def test_sse_replays_with_last_event_id(service):
    # The SSE stream is an infinite live tail. Starlette's TestClient (and httpx's
    # ASGITransport) buffer the ENTIRE response before returning and only deliver
    # http.disconnect once the response completes, so consuming an infinite stream
    # through them deadlocks. Drive the async generator directly instead: read a
    # bounded number of events, then close it (running the finally that unsubscribes).
    service.events.publish("progress", {"n": 1})
    service.events.publish("progress", {"n": 2})

    async def collect():
        received = []
        gen = sse_event_stream(service, "0", _never_disconnected)
        try:
            async for event in gen:
                received.append(event["event"])
                if len(received) >= 3:
                    break
        finally:
            await gen.aclose()
        return received

    received = asyncio.run(collect())
    assert received == ["session_started", "progress", "progress"]
    assert service.events._subscribers == []  # finally unsubscribed on close


def test_sse_live_tail_stops_on_disconnect(service):
    # Exercises the tail loop that previously deadlocked: session_started, then a
    # live event delivered via the subscriber queue, then the is_disconnected()
    # check breaking the loop and the finally unsubscribing.
    disconnected = {"value": False}

    async def is_disconnected() -> bool:
        return disconnected["value"]

    async def collect():
        received = []
        gen = sse_event_stream(service, None, is_disconnected)  # no Last-Event-Id -> no replay
        received.append(await gen.__anext__())  # session_started (subscription now active)
        service.events.publish("progress", {"n": 99})  # arrives via the queue, not replay
        received.append(await gen.__anext__())  # live tail delivers it
        disconnected["value"] = True  # next loop iteration must break
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        return received

    received = asyncio.run(collect())
    assert received[0]["event"] == "session_started"
    assert received[1]["event"] == "progress"
    assert json.loads(received[1]["data"])["n"] == 99
    assert service.events._subscribers == []  # finally unsubscribed


def test_sse_emits_resume_gap_on_evicted_history(service):
    # Swap in a small-buffer bus BEFORE publishing so the ring buffer actually
    # evicts history: with buffer_size=3 and 10 published events, only ids
    # 8-10 survive. Requesting Last-Event-Id=1 must surface a resume_gap
    # (the client missed evicted events 2-7) followed by the surviving tail.
    service.events = EventBus(buffer_size=3)
    for n in range(1, 11):
        service.events.publish("progress", {"n": n})

    async def collect():
        received = []
        gen = sse_event_stream(service, "1", _never_disconnected)
        try:
            async for event in gen:
                received.append(event)
                if len(received) >= 5:
                    break
        finally:
            await gen.aclose()
        return received

    received = asyncio.run(collect())
    assert [e["event"] for e in received] == [
        "session_started",
        "resume_gap",
        "progress",
        "progress",
        "progress",
    ]
    replayed_ids = [int(e["id"]) for e in received[2:]]
    assert replayed_ids == [8, 9, 10]  # only the surviving buffered events, in id order
    assert service.events._subscribers == []  # finally unsubscribed


def test_sse_dedupes_replayed_events_from_live_queue(service):
    # The generator subscribes BEFORE replay is computed, so events published
    # between the session_started yield and the replay call land in both the
    # ring buffer (replayed) and the already-active subscriber queue (live).
    # The yielded_up_to guard in the tail loop must skip those live-queue
    # copies so nothing is delivered twice.
    async def collect():
        received = []
        gen = sse_event_stream(service, "0", _never_disconnected)
        try:
            received.append(await gen.__anext__())  # session_started; subscription now active
            service.events.publish("progress", {"n": 1})
            service.events.publish("progress", {"n": 2})
            service.events.publish("progress", {"n": 3})
            for _ in range(3):
                received.append(await gen.__anext__())  # delivered via bus.replay_since
            service.events.publish("progress", {"n": 4})
            received.append(await gen.__anext__())  # live tail; must not repeat 1-3
        finally:
            await gen.aclose()
        return received

    received = asyncio.run(collect())
    assert [e["event"] for e in received] == [
        "session_started",
        "progress",
        "progress",
        "progress",
        "progress",
    ]
    ids = [int(e["id"]) for e in received]
    assert ids[1:] == [1, 2, 3, 4]
    assert len(ids) == len(set(ids))  # no id delivered twice (replay vs. live queue)
    assert service.events._subscribers == []  # finally unsubscribed


def test_rest_acquisition_end_to_end(client, service, sim_scope, tmp_path):
    objective = sim_scope.objective_store.current_objective
    channel = sim_scope.live_controller.get_channels(objective)[0].name
    yaml_path = tmp_path / "acq.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
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
        )
    )
    body = {
        "yaml_path": str(yaml_path),
        "experiment_id": "rest_e2e",
        "overrides": {"output_path": str(tmp_path / "out")},
    }
    pre = client.post("/v1/acquisitions/preflight", json=body)
    assert pre.status_code == 200 and pre.json()["ok"] is True

    accepted = client.post("/v1/acquisitions", json=body)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert accepted.headers["location"] == f"/v1/jobs/{job_id}"

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["state"] == "COMPLETED":
            break
        time.sleep(0.5)
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "SUCCESS"
    assert client.get("/v1/jobs/last").json()["job_id"] == job_id


# ---- URS delta (LA-WC-0001) -------------------------------------------------


def test_initialize_accepts_optional_body(client):
    r = client.post("/v1/system/initialize")
    assert r.status_code == 200
    assert r.json()["no_op"] is True

    r2 = client.post("/v1/system/initialize", json={"home": False})
    assert r2.status_code == 200
    assert r2.json()["no_op"] is True


def test_methods_crud_over_rest(client, sim_scope):
    config = _method_config(sim_scope)

    created = client.post("/v1/methods", json={"name": "rest_method", "config": config})
    assert created.status_code == 201
    assert created.json() == {"name": "rest_method", "created": True}

    listed = client.get("/v1/methods")
    assert listed.status_code == 200
    assert any(m["name"] == "rest_method" for m in listed.json()["methods"])

    got = client.get("/v1/methods/rest_method")
    assert got.status_code == 200
    assert got.json()["config"]["acquisition"]["widget_type"] == "wellplate"

    validated = client.post("/v1/methods/rest_method/validate")
    assert validated.status_code == 200
    assert validated.json()["ok"] is True

    deleted = client.delete("/v1/methods/rest_method")
    assert deleted.status_code == 200
    assert deleted.json() == {"name": "rest_method", "deleted": True}

    missing = client.delete("/v1/methods/rest_method")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == 1001


def test_acquisition_by_method_name_e2e(client, sim_scope, tmp_path):
    config = _method_config(sim_scope)
    created = client.post("/v1/methods", json={"name": "rest_e2e_method", "config": config})
    assert created.status_code == 201

    body = {
        "method": "rest_e2e_method",
        "experiment_id": "rest_method_e2e",
        "overrides": {"output_path": str(tmp_path / "out_method")},
    }
    accepted = client.post("/v1/acquisitions", json=body)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert accepted.headers["location"] == f"/v1/jobs/{job_id}"

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["state"] == "COMPLETED":
            break
        time.sleep(0.5)
    assert job["state"] == "COMPLETED"
    assert job["outcome"] == "SUCCESS"


def test_sample_formats_endpoint(client):
    r = client.get("/v1/sample_formats")
    assert r.status_code == 200
    formats = {f["name"]: f for f in r.json()["formats"]}
    assert "96 well plate" in formats
    fmt = formats["96 well plate"]
    for key in ("rows", "cols", "well_spacing_mm", "well_size_mm", "a1_x_mm", "a1_y_mm"):
        assert isinstance(fmt[key], (int, float))


def test_autofocus_store_reference_and_correct_rest(client):
    # The default simulated scope has no reflection-AF hardware, so these
    # ops must guard on hardware presence (CONFIG) or controller readiness
    # (AUTOFOCUS) rather than crash. Assert the actual sim behavior.
    r = client.post("/v1/autofocus/store_reference")
    assert r.json()["error"]["category"] in ("CONFIG", "AUTOFOCUS")
    assert r.status_code in (422, 503)

    r2 = client.post("/v1/autofocus/correct", json={})
    assert r2.json()["error"]["category"] in ("CONFIG", "AUTOFOCUS")
    assert r2.status_code in (422, 503)


# ---- Task 11: python_exec debug endpoint + URS delta (/v1/debug/settings) ---


def test_python_exec_disabled_by_default(client):
    status = client.get("/v1/debug/python_exec/status")
    assert status.json() == {"enabled": False}
    r = client.post("/v1/debug/python_exec", json={"code": "result = 1 + 1"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == 1005


def test_python_exec_when_enabled(client, service):
    service.set_python_exec_enabled(True)
    try:
        r = client.post("/v1/debug/python_exec", json={"code": "result = {'x': stage.get_pos().x_mm}"})
        assert r.status_code == 200
        assert "x" in r.json()["result"]
    finally:
        service.set_python_exec_enabled(False)


def test_python_exec_bad_code_is_invalid_param_fault(client, service):
    service.set_python_exec_enabled(True)
    try:
        r = client.post("/v1/debug/python_exec", json={"code": "this is not valid python"})
        assert r.status_code == 400
        assert r.json()["error"]["category"] == "INVALID_PARAM"
        assert r.json()["error"]["code"] == 2002
    finally:
        service.set_python_exec_enabled(False)


def test_python_exec_image_autosave(client, service):
    service.set_python_exec_enabled(True)
    try:
        r = client.post(
            "/v1/debug/python_exec",
            json={"code": "image = np.zeros((4, 4), dtype=np.uint16)"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["image_shape"] == [4, 4]
        assert body["image_dtype"] == "uint16"
        assert body["image_path"].endswith((".tiff", ".npy"))
        import os

        assert os.path.exists(body["image_path"])
    finally:
        service.set_python_exec_enabled(False)


def test_debug_settings_view_settings_roundtrip_headless(client):
    # client's service has NO gui attached (see fixtures above), which is exactly
    # what's needed to exercise the headless performance_mode behavior below.
    import control._def

    original_wells = control._def.SAVE_DOWNSAMPLED_WELL_IMAGES
    original_mosaic = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    try:
        got = client.get("/v1/debug/settings")
        assert got.status_code == 200
        body = got.json()
        assert body["performance_mode"] is None  # headless -> null
        assert body["save_downsampled_well_images"] == original_wells
        assert body["display_mosaic_view"] == original_mosaic

        flipped = {
            "save_downsampled_well_images": not original_wells,
            "display_mosaic_view": not original_mosaic,
        }
        posted = client.post("/v1/debug/settings", json=flipped)
        assert posted.status_code == 200
        assert posted.json()["save_downsampled_well_images"] == flipped["save_downsampled_well_images"]
        assert posted.json()["display_mosaic_view"] == flipped["display_mosaic_view"]
        assert control._def.SAVE_DOWNSAMPLED_WELL_IMAGES == flipped["save_downsampled_well_images"]
        assert control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY == flipped["display_mosaic_view"]

        got2 = client.get("/v1/debug/settings")
        assert got2.json()["save_downsampled_well_images"] == flipped["save_downsampled_well_images"]
        assert got2.json()["display_mosaic_view"] == flipped["display_mosaic_view"]

        # Set the two settings back independently (both directions exercised).
        restored = client.post(
            "/v1/debug/settings",
            json={"save_downsampled_well_images": original_wells, "display_mosaic_view": original_mosaic},
        )
        assert restored.status_code == 200
        assert restored.json()["save_downsampled_well_images"] == original_wells
        assert restored.json()["display_mosaic_view"] == original_mosaic
    finally:
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = original_wells
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_mosaic


def test_debug_settings_performance_mode_headless_is_config_fault(client):
    r = client.post("/v1/debug/settings", json={"performance_mode": True})
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "CONFIG"
    assert r.json()["error"]["code"] == 3003


def test_debug_settings_get_performance_mode_null_headless(client):
    r = client.get("/v1/debug/settings")
    assert r.status_code == 200
    assert r.json()["performance_mode"] is None
