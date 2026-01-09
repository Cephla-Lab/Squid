"""Status resources for MCP."""
from __future__ import annotations

from squid.mcp.server import get_app, mcp


@mcp.resource("squid://status")
def get_system_status() -> str:
    """Current system status summary.

    Returns:
        Human-readable status text
    """
    app = get_app()
    mode = app.mode_gate.get_current_mode()

    stage = app.services.get("stage")
    if stage is not None:
        position = f"X={stage.position_x_mm:.3f}mm, Y={stage.position_y_mm:.3f}mm, Z={stage.position_z_mm:.3f}mm"
    else:
        position = "Stage not available"

    camera = app.services.get("camera")
    if camera is not None:
        camera_info = f"Exposure: {camera.exposure_time_ms}ms, Gain: {camera.analog_gain}"
    else:
        camera_info = "Camera not available"

    return f"""Squid Microscope Status
=======================
Mode: {mode}
Position: {position}
Camera: {camera_info}
"""


@mcp.resource("squid://channels")
def get_available_channels() -> str:
    """List of available imaging channels.

    Returns:
        Newline-separated list of channel names
    """
    app = get_app()

    # Try to get channel names from channel configuration manager
    channel_config = getattr(app.managers, "channel_config", None)
    if channel_config is not None:
        try:
            channels = channel_config.get_channel_names()
            return "\n".join(f"- {ch}" for ch in channels)
        except Exception:
            pass

    return "Channel configuration not available"


@mcp.resource("squid://objectives")
def get_available_objectives() -> str:
    """List of available objectives.

    Returns:
        Newline-separated list of objective names
    """
    app = get_app()

    objective_store = getattr(app.managers, "objective_store", None)
    if objective_store is not None:
        try:
            objectives = objective_store.get_objective_names()
            return "\n".join(f"- {obj}" for obj in objectives)
        except Exception:
            pass

    return "Objective store not available"
