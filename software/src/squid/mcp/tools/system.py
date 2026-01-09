"""System status and control tools for MCP."""
from __future__ import annotations

from typing import Any

from squid.mcp.server import get_app, mcp

# Python exec state (disabled by default for security)
_python_exec_enabled = False


def enable_python_exec(enable: bool = True) -> None:
    """Enable/disable python_exec tool.

    Args:
        enable: Whether to enable python execution
    """
    global _python_exec_enabled
    _python_exec_enabled = enable


def is_python_exec_enabled() -> bool:
    """Check if python_exec is enabled."""
    return _python_exec_enabled


@mcp.tool()
def ping() -> dict[str, Any]:
    """Check if the microscope server is responding.

    Returns:
        Status dict with "ok" status and message
    """
    return {"status": "ok", "message": "Microscope server is running"}


@mcp.tool()
def get_status() -> dict[str, Any]:
    """Get comprehensive microscope status.

    Returns current position, camera settings, illumination state,
    and system mode.

    Returns:
        Dict with mode, position, and camera settings
    """
    app = get_app()
    stage_service = app.services.get("stage")
    camera_service = app.services.get("camera")

    position = {}
    if stage_service is not None:
        position = {
            "x_mm": getattr(stage_service, "position_x_mm", None),
            "y_mm": getattr(stage_service, "position_y_mm", None),
            "z_mm": getattr(stage_service, "position_z_mm", None),
        }

    camera_settings = {}
    if camera_service is not None:
        camera_settings = {
            "exposure_ms": getattr(camera_service, "exposure_time_ms", None),
            "gain": getattr(camera_service, "analog_gain", None),
        }

    return {
        "mode": str(app.mode_gate.get_current_mode()),
        "position": position,
        "camera": camera_settings,
    }


@mcp.tool()
def get_mode() -> str:
    """Get current system mode.

    Returns:
        Current mode as string (IDLE, LIVE, ACQUIRING, etc)
    """
    app = get_app()
    return str(app.mode_gate.get_current_mode())


@mcp.tool()
def get_python_exec_status() -> dict[str, Any]:
    """Check if python_exec is enabled.

    Returns:
        Dict with "enabled" boolean
    """
    return {"enabled": is_python_exec_enabled()}


@mcp.tool()
def python_exec(code: str) -> dict[str, Any]:
    """Execute arbitrary Python code in the microscope context.

    WARNING: This is a powerful tool that can execute any Python code.
    It is disabled by default for security reasons. Enable via GUI
    Settings menu.

    Args:
        code: Python code to execute

    Returns:
        Dict with success status and result or error message
    """
    if not is_python_exec_enabled():
        return {
            "error": "python_exec is disabled. Enable via Settings menu.",
            "enabled": False,
        }

    app = get_app()
    local_vars: dict[str, Any] = {
        "app": app,
        "microscope": app.microscope,
        "services": app.services,
        "event_bus": app.event_bus,
    }

    try:
        exec(code, {"__builtins__": __builtins__}, local_vars)
        return {"success": True, "locals": list(local_vars.keys())}
    except Exception as e:
        return {"success": False, "error": str(e)}
