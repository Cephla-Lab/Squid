# Core Service API

The Squid GUI process embeds a REST + Server-Sent-Events API (`squid_service`) for programmatic control:
starting/monitoring acquisitions, moving the stage, selecting channels, running autofocus, and more. This
is the API used by [`scripts/run_acquisition.py`](../scripts/run_acquisition.py) and the
[MCP bridge](mcp_integration.md); it can also be driven directly with `curl`/`httpx`/any HTTP client.

## Overview

- **Base URL:** `http://127.0.0.1:5060` by default (see [Configuration](#configuration) to change host/port)
- **Interactive docs:** `GET /docs` (Swagger UI) and `GET /openapi.json` are always available, unauthenticated-or-not per the auth setting
- **Transport:** HTTP/1.1 + JSON only — there is no TLS termination built in; put a reverse proxy in front if you need it on a non-loopback network
- **Versioning:** all routes are prefixed `/v1/...`, except `GET /healthz` (unversioned, always open)

### Deviations from the (aspirational) spec

This implementation intentionally deviates from the abstract Core Service spec in a few places:

1. **Acquisition source is `yaml_path`, `method`, or `grid` (exactly one), not just a method name.** The spec
   envisions purely named methods; this implementation additionally accepts a filesystem path to an
   `acquisition.yaml` (as saved by the GUI) or an inline grid-scan spec, for backward compatibility with the
   existing GUI-driven workflow. See [Starting an acquisition](#starting-an-acquisition).
2. **Auth is off by default on loopback binds.** The spec assumes auth-on-by-default; here, a server bound to
   `127.0.0.1`/`localhost` may run without a bearer token (matching the previous TCP server's trust model).
   Binding to any non-loopback host makes `auth_enabled=true` + a non-empty `auth_token` **mandatory** — the
   server refuses to start otherwise. See [Authentication](#authentication).
3. **HTTP only, no built-in TLS.**
4. **Several endpoints intentionally return `501 Not Implemented`** rather than being unimplemented-by-omission,
   so the route shape and the compliance gap are both visible in the OpenAPI schema. See
   [Deferred endpoints](#deferred-endpoints-501).

## Endpoint reference

Every non-2xx response body is `{"error": <Fault>}` — see [Fault model](#fault-model).

### Meta

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/v1/healthz` | open | Liveness check: `{"alive": true}` |
| GET | `/v1/sample_formats` | | List every known wellplate/sample format and its layout (URS API-LAB-001) |

### System

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/v1/system/initialize` | `{"home": bool}` (optional) | Initialize the instrument, optionally homing all axes |
| POST | `/v1/system/reset` | | Reset from an error/recovering state |
| GET | `/v1/system/status` | | Instrument state, active job id, latest fault, last-acquisition summary |
| GET | `/v1/system/heartbeat` | | `{"alive", "monotonic_ns", "state"}` — cheap, in-process, no MCU round-trip |
| GET | `/v1/system/capabilities` | | Channels, objectives, stage travel ranges, camera info, simulation flag |
| GET | `/v1/system/version` | | Software/API/firmware version strings |
| GET | `/v1/system/auth_status` | open | `{"auth_enabled", "bind_to_tls", "scheme"}` |
| GET | `/v1/system/faults?since=&limit=` | | Fault history (monotonic `sequence` cursor) |
| POST | `/v1/system/reserve` | | **501** — see [Deferred endpoints](#deferred-endpoints-501) |
| POST | `/v1/system/release` | | **501** |
| POST | `/v1/system/shutdown` | | **501** |

### Motion

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/v1/motion/position` | | Current XYZ stage position (mm) |
| POST | `/v1/motion/move` | `MoveRequest{mode: absolute\|relative, x, y, z, block_until_complete}` | Move the stage |
| POST | `/v1/motion/home` | | Home all axes |

### Imaging

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/v1/imaging/channels` | | Channels available for the current objective |
| POST | `/v1/imaging/channel` | `{"name": str}` | Select the active channel |
| POST | `/v1/imaging/exposure` | `{"exposure_ms": float, "channel": str?}` | Set exposure time |
| POST | `/v1/imaging/intensity` | `{"channel": str, "intensity": float}` | Set illumination intensity (0-100%) |
| POST | `/v1/imaging/illumination/on` \| `/off` | | Toggle illumination |
| GET | `/v1/imaging/objectives` | | List objectives + current selection |
| GET \| POST | `/v1/imaging/objective` | `{"name": str}` (POST) | Get/set the current objective |
| POST | `/v1/imaging/acquire` | `{"channel": str?, "save_path": str?}` | Capture a single image |
| POST | `/v1/imaging/live/start` \| `/stop` | | Toggle live camera streaming |

### Autofocus

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/v1/autofocus/run` | `{"mode": "reflection", "target_um": float}` | Run reflection (laser) autofocus |
| GET | `/v1/autofocus/status` | | Hardware/reference readiness |
| POST | `/v1/autofocus/store_reference` | | Capture the current laser spot as the new reference |
| POST | `/v1/autofocus/correct` | `{"threshold_um": float}` | Apply a correction if drift exceeds the threshold |
| POST | `/v1/autofocus/acquire_image` | `{"save_path": str?, "use_last_frame": bool}` | Grab a laser-AF camera frame |

### Acquisitions & Jobs

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/v1/acquisitions/preflight` | `AcquisitionRequest` | Run validation checks without starting anything |
| POST | `/v1/acquisitions` | `AcquisitionRequest` | Start an acquisition. **202** + `Location: /v1/jobs/{id}` on acceptance |
| GET | `/v1/jobs/last` | | Most recent job |
| GET | `/v1/jobs/{job_id}` | | Job record: state, progress, result, outcome |
| POST | `/v1/jobs/{job_id}/abort` | `{"timeout_s": float}` (optional) | Gracefully abort a running job |
| POST | `/v1/jobs/{job_id}/emergency_stop` | | **501** |

`AcquisitionRequest` requires **exactly one** of `method`, `yaml_path`, `grid`, plus optional
`experiment_id`, `operator`, `scheduler_job_id`, `autofocus` overrides, and `overrides`
(`wells`, `output_path`, `sample_format`). See [Starting an acquisition](#starting-an-acquisition).

### Methods

Named, server-side acquisition configurations (URS API-METH-001..005) — see [Method registry](#method-registry) below.

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/v1/methods` | | List all methods with a summary (channels, objective, nz/nt, ...) |
| GET | `/v1/methods/{name}` | | Full method config |
| POST | `/v1/methods` | `{"name": str, "config": dict}` | Create a method. **201** |
| PUT | `/v1/methods/{name}` | `{"config": dict}` | Update (overwrite) a method |
| DELETE | `/v1/methods/{name}` | | Delete a method (rejected while an acquisition is running) |
| POST | `/v1/methods/{name}/validate` | | Run preflight-style checks against a stored method |

### Debug

| Method | Path | Body | Description |
|--------|------|------|-------------|
| POST | `/v1/debug/python_exec` | `{"code": str}` | Execute Python with microscope objects in scope. **403** when disabled |
| GET | `/v1/debug/python_exec/status` | | `{"enabled": bool}` |
| GET | `/v1/debug/settings` | | `{"performance_mode", "save_downsampled_well_images", "display_mosaic_view"}` |
| POST | `/v1/debug/settings` | any subset of the above, all optional | Update one or more debug settings |

`python_exec` is **not sandboxed**. It is gated by the GUI opt-in toggle (**Settings → Enable MCP Python
Exec**) and is intended only for loopback binds — do not expose it on a non-loopback network.

`GET /v1/debug/settings` has no `display_plate_view` field: the legacy `DISPLAY_PLATE_VIEW` flag it used to
report no longer exists (plate view was unified into the mosaic view / `UnifiedMosaicWidget`, governed
solely by `display_mosaic_view`). `performance_mode` is `null` when no GUI is attached (headless service).

### Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/events` | Server-Sent-Events stream — see [SSE](#server-sent-events) |

## Fault model

Every non-2xx response body has the shape `{"error": Fault}`:

```json
{
  "error": {
    "category": "INVALID_PARAM",
    "code": 2001,
    "recoverable": false,
    "scheduler_action": "REJECT_PLATE",
    "sequence": 42,
    "component": "stage.x",
    "message": "x target 200.000 mm outside [0.0, 120.0]",
    "detail": {"axis": "x", "target_mm": 200.0},
    "timestamp": "2026-07-02T12:00:00Z",
    "terminal": false,
    "operator_intervention_required": false,
    "plate_removable": true,
    "resolved_at": null,
    "resolved_by": null
  }
}
```

Drivers should branch on `category`/`code`/`terminal`, not on the HTTP status code or message text (the
HTTP status is advisory triage only). Codes are allocated in 1000-blocks per category:

| Category | Code block | Meaning | Typical HTTP status |
|----------|-----------|---------|---------------------|
| `PROTOCOL` | 1xxx | Unknown resource, wrong state, schema violation, auth, forbidden, not-implemented | 401/403/404/409/422/501 |
| `INVALID_PARAM` | 2xxx | Out-of-range or malformed request parameter | 400 |
| `CONFIG` | 3xxx | Unknown channel/objective, missing capability, hardware mismatch | 422 |
| `HARDWARE_TRANSIENT` | 4xxx | Timeout or other retryable hardware issue | 503 |
| `HARDWARE_FAULT` | 5xxx | Hardware fault (5999 = internal error) | 500/503 |
| `ACQUISITION` | 6xxx | Failed to start, or runtime failure during a run | 503 |
| `IO` | 7xxx | Path not writable, disk full, other I/O error | 500/507 |
| `AUTOFOCUS` | 8xxx | Autofocus failure or not-ready | 503 |

`scheduler_action` is one of `RETRY`, `ABORT_PLATE`, `REJECT_PLATE`, `PAUSE_INSTRUMENT`, `ESCALATE_OPERATOR`
— a hint for an automated scheduler about how to react; it does not change what the API call itself did.

`GET /v1/system/faults?since=&limit=` returns the fault history (monotonically increasing `sequence`); poll
it with `since=<last sequence you've seen>` to get only new faults.

## Instrument state

`GET /v1/system/status` reports `state`, one of (`squid_service/state.py`):

| State | Meaning |
|-------|---------|
| `UNINITIALIZED` | Service constructed but `initialize()` not yet called |
| `INITIALIZING` | Homing/initialization in progress |
| `INITIALIZED` | Idle and ready to accept commands |
| `RESERVED` | Reserved for exclusive use (reserve/release are currently 501 — see below) |
| `ACQUIRING` | An acquisition job is actively imaging |
| `PROCESSING` | Acquisition imaging finished; post-processing/writers draining |
| `ERROR` | An unrecoverable fault occurred; call `POST /v1/system/reset` after resolving it |
| `RECOVERING` | Recovering from an error |
| `SHUTTING_DOWN` | Instrument shutting down |

## Jobs lifecycle

Acquisitions are asynchronous jobs:

```bash
# 1. Start (accepts a job, returns immediately)
curl -i -X POST http://127.0.0.1:5060/v1/acquisitions \
  -H "Content-Type: application/json" \
  -d '{"yaml_path": "/path/to/acquisition.yaml", "overrides": {"output_path": "/data/out"}}'
# HTTP/1.1 202 Accepted
# Location: /v1/jobs/c46b7c7d825b
# {"job_id": "c46b7c7d825b", "kind": "acquisition", "experiment_id": "...",
#  "expected_fov_count": 1, "expected_image_count": 6, "output_dir": "/data/out/...",
#  "accepted_at": "2026-07-02T12:00:00Z"}

# 2. Poll until COMPLETED (see Polling guidance below)
curl http://127.0.0.1:5060/v1/jobs/c46b7c7d825b
# {"job_id": "...", "state": "RUNNING", "progress": {"images_acquired": 2, "total_images": 6, ...}, ...}
# ... poll again ...
# {"job_id": "...", "state": "COMPLETED", "outcome": "SUCCESS",
#  "result": {"end_reason": "completed", "output_dir": "...", "image_count_written": 6, ...}}
```

Job `state` is one of `ACCEPTED`, `RUNNING`, `COMPLETED`. Once `COMPLETED`, `outcome` is one of `SUCCESS`,
`FAILURE`, `ABORTED`, `PARTIAL`. Only one acquisition may run at a time; starting a second while one is
active fails with a `PROTOCOL_WRONG_STATE` (1002) fault.

### Starting an acquisition

`AcquisitionRequest` requires exactly one of:

- `yaml_path` — absolute path to a GUI-saved `acquisition.yaml` (wellplate mode only)
- `method` — name of a method registered under [Method registry](#method-registry)
- `grid` — inline grid-scan spec: `{"wells": "A1:B3", "channels": [...], "nx", "ny", "overlap_percent", "wellplate_format"}`

Optional fields: `experiment_id`, `operator`, `scheduler_job_id` (audit trail, written to
`<output_dir>/api_request.json`), `autofocus: {"reflection": bool?, "contrast": bool?}` (override the
YAML/method's autofocus flags), and `overrides: {"wells", "output_path", "sample_format"}`.

## Method registry

A **method** is an acquisition YAML (same schema as a GUI-saved `acquisition.yaml`) stored server-side, so
clients reference it by name instead of by filesystem path (URS API-METH-001..005). Methods live in the
directory configured by `methods_dir` (default `machine_configs/acquisition_methods/`), one file per method
named `<name>.yaml`.

```bash
curl -X POST http://127.0.0.1:5060/v1/methods \
  -H "Content-Type: application/json" \
  -d '{"name": "daily_scan", "config": {"acquisition": {"widget_type": "wellplate"}, ...}}'

curl -X POST http://127.0.0.1:5060/v1/acquisitions -d '{"method": "daily_scan"}'
```

`GET /v1/methods` returns a summary per method including `estimated_duration_s`, which is **always `null`
in this version** — it is a documented placeholder for a future duration estimator, not a bug. Deleting a
method while an acquisition is in progress is rejected with a `PROTOCOL_WRONG_STATE` fault.

## Polling guidance (URS API-POLL-005)

`GET /v1/system/status` and `GET /v1/system/heartbeat` are served from in-process state — they never make a
round-trip to the MCU or block on hardware I/O, so they are cheap to poll frequently. Recommended intervals:

| Endpoint | Recommended interval | Notes |
|----------|----------------------|-------|
| `GET /v1/system/status` | ~1 s | Cheap; use for state/current-job monitoring |
| `GET /v1/system/heartbeat` | ~5 s | Liveness only; cheaper than `/status` |
| `GET /v1/jobs/{job_id}` | 2-5 s | Job progress; `run_acquisition.py` polls every 2 s |

Short bursts above these rates are tolerated (there is no server-side rate limiting), but sustained polling
faster than ~1 Hz per client provides no additional information — state only changes on hardware/acquisition
events, not on a faster clock.

## Server-Sent Events

`GET /v1/events` streams state changes, progress updates, and job completions as they happen, so clients
that need low-latency updates don't have to poll `/v1/jobs/{id}` at all:

```bash
curl -N -H "Last-Event-Id: 0" http://127.0.0.1:5060/v1/events
```

- The stream always opens with a `session_started` event (`session_id`, `current_state`, `last_event_id`).
- Sending `Last-Event-Id: <n>` replays events with id greater than `n` from the in-process buffer before
  tailing live; if the requested id has already fallen out of the buffer, a `resume_gap` event is emitted
  first so the client knows it missed events and should re-sync via `GET /v1/system/status` /
  `GET /v1/jobs/{id}`.
- Event ids are a monotonically increasing per-session sequence; there is no persistence across service
  restarts (a new `session_id` means a new sequence).

## Authentication

Bearer token auth, off by default:

- **Loopback bind** (`host` is `127.0.0.1`/`localhost`/any loopback address): auth is **off** unless you
  explicitly set `auth_enabled=true`.
- **Non-loopback bind**: auth is **mandatory** — the service refuses to start (`ServiceConfig` validation
  error) unless `auth_enabled=true` and `auth_token` is a non-empty string.

When enabled, send `Authorization: Bearer <token>` on every request except the open paths: `/v1/healthz`,
`/v1/system/auth_status`, `/openapi.json`, `/docs`, `/redoc`. A missing/invalid token yields `401` with a
`PROTOCOL_AUTH` (1004) fault. `GET /v1/system/auth_status` lets a client check whether it needs a token
before making authenticated calls.

The MCP bridge reads `SQUID_API_URL` (base URL) and `SQUID_API_TOKEN` (bearer token, optional) from the
environment — see [MCP Integration](mcp_integration.md#environment-variables).

## Deferred endpoints (501)

The following routes exist (for OpenAPI-schema completeness and forward compatibility) but currently return
`501 Not Implemented` with a `PROTOCOL_NOT_IMPLEMENTED` (1006) fault. Each is tracked against a URS
requirement so the compliance gap is explicit:

| Endpoint | URS id | Notes |
|----------|--------|-------|
| `POST /v1/jobs/{job_id}/emergency_stop` | API-ACQ-005 | Use `POST /v1/jobs/{job_id}/abort` for a graceful stop today |
| `POST /v1/system/reserve` | API-LIFE-005 | Exclusive-reservation lifecycle not yet implemented |
| `POST /v1/system/release` | API-LIFE-006 | See above |
| `POST /v1/system/shutdown` | — | No remote shutdown path yet |

Additionally, **plate-handling endpoints do not exist at all yet** (no routes registered) — tracked as
URS API-PLATE-* (loading/unloading/presence sensing). There is currently no REST equivalent; plate handling
remains GUI-only.

## Known limitations

- **GUI/API acquisition race**: the API's 409 "already in progress" guard reads the controller state, but
  acquisitions started from the **GUI** bypass the service's command lock. If a GUI-initiated and an
  API-initiated start land in the same instant, they can race on the shared `MultiPointController`. Do not
  operate the GUI while a scheduler is driving the instrument through the API. A follow-up will route GUI
  starts through the service so a single lock serializes both paths.

## Configuration

`[CORE_SERVICE]` section in the machine `.ini` config (all keys optional; defaults shown):

```ini
[CORE_SERVICE]
enabled = true
host = 127.0.0.1
port = 5060
auth_enabled = false
auth_token =
methods_dir = machine_configs/acquisition_methods
```

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `true` | Set `false` to disable the REST API entirely (the legacy TCP server is unaffected) |
| `host` | `127.0.0.1` | Bind address. Non-loopback requires `auth_enabled=true` + `auth_token` (see [Authentication](#authentication)) |
| `port` | `5060` | Bind port |
| `auth_enabled` | `false` | See [Authentication](#authentication) |
| `auth_token` | `""` | Bearer token; required (non-empty) when `auth_enabled=true` |
| `methods_dir` | `machine_configs/acquisition_methods` | Directory for the [method registry](#method-registry), relative to the `software/` working directory |

The server starts alongside the legacy TCP control server (port 5050) when the control server is enabled —
via `python3 main_hcs.py --start-server`, or **Settings → Enable MCP Control Server** in the GUI. Both are
stopped together on GUI exit.

## See Also

- [Automation](automation.md) - `run_acquisition.py` and `curl` recipes
- [MCP Integration](mcp_integration.md) - AI-agent control via Claude Code
