#!/usr/bin/env python3
"""MCP stdio bridge for the Squid Core Service REST API.

Claude Code <-stdio-> this bridge <-HTTP:5060-> squid_service (inside the GUI).
Tool names and argument names are kept compatible with the previous TCP-based
bridge so existing .mcp.json configs and pre-approved permissions keep working.
Errors are returned as the canonical Fault JSON ({"error": {category, code,
terminal, ...}}) so agents can branch programmatically.
"""

import asyncio
import json
import os
from typing import Any, Dict, Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

DEFAULT_API_URL = "http://127.0.0.1:5060"


def make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    headers = {}
    token = os.environ.get("SQUID_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base_url = os.environ.get("SQUID_API_URL", DEFAULT_API_URL)
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60.0, transport=transport)


async def _call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> dict:
    # A per-call timeout overrides the client default for long-running ops (home,
    # abort) so the HTTP timeout always outlives the server-side operation timeout.
    request_kwargs = {"json": body}
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    try:
        response = await client.request(method, path, **request_kwargs)
    except httpx.TransportError as e:
        return {
            "error": {
                "category": "HARDWARE_TRANSIENT",
                "code": 4001,
                "message": f"Cannot reach the Squid Core Service at {client.base_url} ({e}). "
                "Is the GUI running with the control server enabled?",
                "terminal": False,
            }
        }
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {"raw": response.text}
    return payload  # non-2xx bodies already carry {"error": Fault}


# ---- tool handlers ----------------------------------------------------------


def _pick(args: dict, mapping: Dict[str, str]) -> dict:
    return {new: args[old] for old, new in mapping.items() if args.get(old) is not None}


async def _ping(c, a):
    return await _call(c, "GET", "/v1/healthz")


async def _get_status(c, a):
    return await _call(c, "GET", "/v1/system/status")


async def _get_capabilities(c, a):
    return await _call(c, "GET", "/v1/system/capabilities")


async def _get_position(c, a):
    return await _call(c, "GET", "/v1/motion/position")


async def _move_to(c, a):
    body = {"mode": "absolute", "block_until_complete": a.get("blocking", True)}
    body.update(_pick(a, {"x_mm": "x", "y_mm": "y", "z_mm": "z"}))
    return await _call(c, "POST", "/v1/motion/move", body)


async def _move_relative(c, a):
    body = {"mode": "relative", "block_until_complete": a.get("blocking", True)}
    body.update(_pick(a, {"dx_mm": "x", "dy_mm": "y", "dz_mm": "z"}))
    return await _call(c, "POST", "/v1/motion/move", body)


async def _home(c, a):
    # Homing all axes can take a while; give the HTTP call a generous timeout.
    return await _call(c, "POST", "/v1/motion/home", timeout=300.0)


async def _start_live(c, a):
    return await _call(c, "POST", "/v1/imaging/live/start")


async def _stop_live(c, a):
    return await _call(c, "POST", "/v1/imaging/live/stop")


async def _acquire_image(c, a):
    return await _call(c, "POST", "/v1/imaging/acquire", _pick(a, {"save_path": "save_path", "channel": "channel"}))


async def _get_channels(c, a):
    return await _call(c, "GET", "/v1/imaging/channels")


async def _set_channel(c, a):
    return await _call(c, "POST", "/v1/imaging/channel", {"name": a["channel_name"]})


async def _set_exposure(c, a):
    return await _call(
        c, "POST", "/v1/imaging/exposure", _pick(a, {"exposure_ms": "exposure_ms", "channel": "channel"})
    )


async def _set_intensity(c, a):
    return await _call(c, "POST", "/v1/imaging/intensity", {"channel": a["channel"], "intensity": a["intensity"]})


async def _illum_on(c, a):
    return await _call(c, "POST", "/v1/imaging/illumination/on")


async def _illum_off(c, a):
    return await _call(c, "POST", "/v1/imaging/illumination/off")


async def _get_objectives(c, a):
    return await _call(c, "GET", "/v1/imaging/objectives")


async def _get_current_objective(c, a):
    return await _call(c, "GET", "/v1/imaging/objective")


async def _set_objective(c, a):
    return await _call(c, "POST", "/v1/imaging/objective", {"name": a["objective_name"]})


async def _autofocus(c, a):
    return await _call(c, "POST", "/v1/autofocus/run", {"target_um": a.get("target_um", 0.0)})


async def _acquire_laser_af_image(c, a):
    body = _pick(a, {"save_path": "save_path", "use_last_frame": "use_last_frame"})
    return await _call(c, "POST", "/v1/autofocus/acquire_image", body)


async def _run_acquisition_from_yaml(c, a):
    body = {
        "yaml_path": a["yaml_path"],
        "experiment_id": a.get("experiment_id"),
        "overrides": {"wells": a.get("wells"), "output_path": a.get("base_path")},
    }
    body = {k: v for k, v in body.items() if v is not None}
    return await _call(c, "POST", "/v1/acquisitions", body)


async def _get_acquisition_status(c, a):
    status = await _call(c, "GET", "/v1/system/status")
    if status.get("current_job_id"):
        job = await _call(c, "GET", f"/v1/jobs/{status['current_job_id']}")
        return {"status": status, "job": job}
    last = await _call(c, "GET", "/v1/jobs/last")
    return {"status": status, "last_job": None if "error" in last else last}


async def _get_job(c, a):
    return await _call(c, "GET", f"/v1/jobs/{a['job_id']}")


async def _abort_acquisition(c, a):
    status = await _call(c, "GET", "/v1/system/status")
    job_id = status.get("current_job_id")
    if not job_id:
        return {
            "error": {"category": "PROTOCOL", "code": 1002, "message": "No acquisition in progress", "terminal": False}
        }
    # The server blocks up to timeout_s draining the abort; the HTTP call must
    # outlive that (+10s slack) or the client times out before the server replies.
    timeout_s = a.get("timeout_s", 60.0)
    return await _call(c, "POST", f"/v1/jobs/{job_id}/abort", {"timeout_s": timeout_s}, timeout=timeout_s + 10.0)


async def _python_exec(c, a):
    return await _call(c, "POST", "/v1/debug/python_exec", {"code": a["code"]})


async def _python_exec_status(c, a):
    return await _call(c, "GET", "/v1/debug/python_exec/status")


# ---- URS delta handlers (API-COMPAT-002) ------------------------------------
# Legacy TCP-era tools not covered above, mapped onto the new REST API, plus
# four brand-new tools. `microscope_set_display_plate_view` has no handler and
# no registry entry: the legacy `control._def.DISPLAY_PLATE_VIEW` flag it
# toggled no longer exists on master (plate view was unified into the mosaic
# view / UnifiedMosaicWidget, governed solely by `display_mosaic_view`), so
# there is nothing left for that tool to control.


async def _run_acquisition_grid(c, a):
    grid = {"wells": a["wells"], "channels": a["channels"]}
    grid.update({k: a[k] for k in ("nx", "ny", "overlap_percent", "wellplate_format") if a.get(k) is not None})
    body = {"grid": grid}
    if a.get("experiment_id") is not None:
        body["experiment_id"] = a["experiment_id"]
    if a.get("base_path") is not None:
        body["overrides"] = {"output_path": a["base_path"]}
    return await _call(c, "POST", "/v1/acquisitions", body)


async def _set_performance_mode(c, a):
    return await _call(c, "POST", "/v1/debug/settings", {"performance_mode": a["enabled"]})


async def _get_performance_mode(c, a):
    return await _call(c, "GET", "/v1/debug/settings")


async def _get_view_settings(c, a):
    return await _call(c, "GET", "/v1/debug/settings")


async def _set_view_settings(c, a):
    body = _pick(
        a,
        {"save_downsampled_well_images": "save_downsampled_well_images", "display_mosaic_view": "display_mosaic_view"},
    )
    return await _call(c, "POST", "/v1/debug/settings", body)


async def _set_save_downsampled_images(c, a):
    return await _call(c, "POST", "/v1/debug/settings", {"save_downsampled_well_images": a["enabled"]})


async def _set_save_downsampled_overview(c, a):
    return await _call(c, "POST", "/v1/debug/settings", {"save_downsampled_overview": a["enabled"]})


async def _set_display_mosaic_view(c, a):
    return await _call(c, "POST", "/v1/debug/settings", {"display_mosaic_view": a["enabled"]})


async def _get_methods(c, a):
    return await _call(c, "GET", "/v1/methods")


async def _run_method(c, a):
    body = {"method": a["method"]}
    if a.get("experiment_id") is not None:
        body["experiment_id"] = a["experiment_id"]
    if a.get("operator") is not None:
        body["operator"] = a["operator"]
    overrides = _pick(a, {"wells": "wells", "base_path": "output_path"})
    if overrides:
        body["overrides"] = overrides
    return await _call(c, "POST", "/v1/acquisitions", body)


async def _autofocus_status(c, a):
    return await _call(c, "GET", "/v1/autofocus/status")


async def _store_af_reference(c, a):
    return await _call(c, "POST", "/v1/autofocus/store_reference")


# ---- tool registry -----------------------------------------------------------


def _tool(name: str, description: str, properties: Optional[dict] = None, required: Optional[list] = None) -> Tool:
    return Tool(
        name=f"microscope_{name}",
        description=description,
        inputSchema={"type": "object", "properties": properties or {}, "required": required or []},
    )


_NUM = {"type": "number"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_ARR_STR = {"type": "array", "items": {"type": "string"}}

_TOOLS: Dict[str, tuple] = {
    "microscope_ping": (_tool("ping", "Check the Squid Core Service is reachable"), _ping),
    "microscope_get_status": (_tool("get_status", "Instrument state, active job, latest fault"), _get_status),
    "microscope_get_capabilities": (
        _tool("get_capabilities", "Channels, objectives, stage travel, camera, simulation flag"),
        _get_capabilities,
    ),
    "microscope_get_position": (_tool("get_position", "Current XYZ stage position (mm)"), _get_position),
    "microscope_move_to": (
        _tool(
            "move_to",
            "Move stage to absolute XYZ position in mm",
            {"x_mm": _NUM, "y_mm": _NUM, "z_mm": _NUM, "blocking": _BOOL},
        ),
        _move_to,
    ),
    "microscope_move_relative": (
        _tool(
            "move_relative",
            "Move stage by a relative amount in mm",
            {"dx_mm": _NUM, "dy_mm": _NUM, "dz_mm": _NUM, "blocking": _BOOL},
        ),
        _move_relative,
    ),
    "microscope_home": (_tool("home", "Home all stage axes (X, Y, Z)"), _home),
    "microscope_start_live": (_tool("start_live", "Start live camera streaming"), _start_live),
    "microscope_stop_live": (_tool("stop_live", "Stop live camera streaming"), _stop_live),
    "microscope_acquire_image": (
        _tool(
            "acquire_image",
            "Acquire one image; optionally select channel and save to disk",
            {"channel": _STR, "save_path": _STR},
        ),
        _acquire_image,
    ),
    "microscope_get_channels": (_tool("get_channels", "List channels for the current objective"), _get_channels),
    "microscope_set_channel": (
        _tool("set_channel", "Select the active imaging channel", {"channel_name": _STR}, ["channel_name"]),
        _set_channel,
    ),
    "microscope_set_exposure": (
        _tool(
            "set_exposure",
            "Set exposure time (ms), optionally for a named channel",
            {"exposure_ms": _NUM, "channel": _STR},
            ["exposure_ms"],
        ),
        _set_exposure,
    ),
    "microscope_set_illumination_intensity": (
        _tool(
            "set_illumination_intensity",
            "Set illumination intensity 0-100% for a channel",
            {"channel": _STR, "intensity": _NUM},
            ["channel", "intensity"],
        ),
        _set_intensity,
    ),
    "microscope_turn_on_illumination": (_tool("turn_on_illumination", "Illumination on"), _illum_on),
    "microscope_turn_off_illumination": (_tool("turn_off_illumination", "Illumination off"), _illum_off),
    "microscope_get_objectives": (_tool("get_objectives", "List objectives and current selection"), _get_objectives),
    "microscope_get_current_objective": (
        _tool("get_current_objective", "Get the current objective"),
        _get_current_objective,
    ),
    "microscope_set_objective": (
        _tool("set_objective", "Switch objective", {"objective_name": _STR}, ["objective_name"]),
        _set_objective,
    ),
    "microscope_autofocus": (
        _tool("autofocus", "Run reflection (laser) autofocus at the current position", {"target_um": _NUM}),
        _autofocus,
    ),
    "microscope_run_acquisition_from_yaml": (
        _tool(
            "run_acquisition_from_yaml",
            "Start a wellplate acquisition from a saved acquisition.yaml; returns a job handle",
            {
                "yaml_path": _STR,
                "wells": {"type": "string", "description": "Override wells, e.g. 'A1:B3' or 'A1,B2'"},
                "experiment_id": _STR,
                "base_path": _STR,
            },
            ["yaml_path"],
        ),
        _run_acquisition_from_yaml,
    ),
    "microscope_get_acquisition_status": (
        _tool("get_acquisition_status", "Instrument status plus active or last job progress"),
        _get_acquisition_status,
    ),
    "microscope_get_job": (
        _tool("get_job", "Get a job record by id", {"job_id": _STR}, ["job_id"]),
        _get_job,
    ),
    "microscope_abort_acquisition": (
        _tool("abort_acquisition", "Gracefully abort the running acquisition", {"timeout_s": _NUM}),
        _abort_acquisition,
    ),
    "microscope_python_exec": (
        _tool(
            "python_exec",
            "Execute Python with microscope objects in scope (requires GUI opt-in; NOT sandboxed). "
            "Set 'result' for return data, 'image' (ndarray) to auto-save.",
            {"code": _STR},
            ["code"],
        ),
        _python_exec,
    ),
    "microscope_get_python_exec_status": (
        _tool("get_python_exec_status", "Check whether python_exec is enabled"),
        _python_exec_status,
    ),
    # ---- URS delta (API-COMPAT-002): remaining legacy tools + new tools ----
    "microscope_run_acquisition": (
        _tool(
            "run_acquisition",
            "Run a grid-mode multi-well acquisition (legacy grid API); returns a job handle",
            {
                "wells": _STR,
                "channels": _ARR_STR,
                "nx": _NUM,
                "ny": _NUM,
                "experiment_id": _STR,
                "base_path": _STR,
                "wellplate_format": _STR,
                "overlap_percent": _NUM,
            },
            ["wells", "channels"],
        ),
        _run_acquisition_grid,
    ),
    "microscope_set_performance_mode": (
        _tool(
            "set_performance_mode",
            "Enable or disable performance mode (disables mosaic view to save RAM); requires a GUI",
            {"enabled": _BOOL},
            ["enabled"],
        ),
        _set_performance_mode,
    ),
    "microscope_get_performance_mode": (
        _tool("get_performance_mode", "Get current performance/view debug settings"),
        _get_performance_mode,
    ),
    "microscope_get_view_settings": (
        _tool(
            "get_view_settings",
            "Get current view settings (downsampled-well-image saving, mosaic display, performance mode)",
        ),
        _get_view_settings,
    ),
    "microscope_set_view_settings": (
        _tool(
            "set_view_settings",
            "Set multiple view settings at once (mosaic view: immediate; others: next acquisition)",
            {"save_downsampled_well_images": _BOOL, "display_mosaic_view": _BOOL},
        ),
        _set_view_settings,
    ),
    "microscope_set_save_downsampled_images": (
        _tool(
            "set_save_downsampled_images",
            "Enable/disable saving per-well downsampled TIFFs (takes effect on next acquisition)",
            {"enabled": _BOOL},
            ["enabled"],
        ),
        _set_save_downsampled_images,
    ),
    "microscope_set_save_downsampled_overview": (
        _tool(
            "set_save_downsampled_overview",
            "Enable/disable saving the downsampled mosaic overview image (takes effect on next acquisition)",
            {"enabled": _BOOL},
            ["enabled"],
        ),
        _set_save_downsampled_overview,
    ),
    "microscope_set_display_mosaic_view": (
        _tool(
            "set_display_mosaic_view",
            "Enable/disable mosaic view display (takes effect immediately)",
            {"enabled": _BOOL},
            ["enabled"],
        ),
        _set_display_mosaic_view,
    ),
    "microscope_get_methods": (
        _tool("get_methods", "List named acquisition methods stored on the server"),
        _get_methods,
    ),
    "microscope_run_method": (
        _tool(
            "run_method",
            "Start an acquisition from a named server-side method; returns a job handle",
            {"method": _STR, "experiment_id": _STR, "wells": _STR, "base_path": _STR, "operator": _STR},
            ["method"],
        ),
        _run_method,
    ),
    "microscope_autofocus_status": (
        _tool("autofocus_status", "Reflection (laser) autofocus hardware/reference readiness"),
        _autofocus_status,
    ),
    "microscope_store_af_reference": (
        _tool("store_af_reference", "Capture the current laser spot as the new reflection-AF reference"),
        _store_af_reference,
    ),
    "microscope_acquire_laser_af_image": (
        _tool(
            "acquire_laser_af_image",
            "Acquire an image from the laser autofocus camera; optionally save to disk",
            {"save_path": _STR, "use_last_frame": _BOOL},
        ),
        _acquire_laser_af_image,
    ),
}


def tool_definitions() -> list:
    return [definition for definition, _ in _TOOLS.values()]


async def dispatch(client: httpx.AsyncClient, name: str, arguments: dict) -> dict:
    entry = _TOOLS.get(name)
    if entry is None:
        raise ValueError(f"Unknown tool: {name}")
    _, handler = entry
    return await handler(client, arguments or {})


# ---- MCP plumbing -------------------------------------------------------------

app = Server("squid-microscope")
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = make_client()
    return _client


@app.list_tools()
async def list_tools() -> list:
    return tool_definitions()


@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> list:
    try:
        result = await dispatch(_get_client(), name, arguments)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
