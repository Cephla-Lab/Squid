"""Illumination control tools for MCP."""
from __future__ import annotations

from typing import Any

from squid.mcp.server import get_app, mcp


@mcp.tool()
def set_illumination(channel: str, intensity: float, on: bool = True) -> dict[str, Any]:
    """Set illumination for a channel.

    Args:
        channel: Channel name (e.g., "BF", "405", "488", "561", "638")
        intensity: Intensity percentage (0-100)
        on: Turn illumination on or off (default True)

    Returns:
        Dict with channel, intensity, and on status
    """
    if not 0 <= intensity <= 100:
        return {"error": "Intensity must be between 0 and 100"}

    app = get_app()
    illum = app.services.get("illumination")
    if illum is None:
        return {"error": "Illumination service not available"}

    illum.set_illumination(channel, intensity, on)

    return {"channel": channel, "intensity": intensity, "on": on}


@mcp.tool()
def turn_off_all_illumination() -> dict[str, Any]:
    """Turn off all illumination sources.

    Safety function to ensure all lights are off.

    Returns:
        Dict with status
    """
    app = get_app()
    illum = app.services.get("illumination")
    if illum is None:
        return {"error": "Illumination service not available"}

    illum.turn_off_all()

    return {"status": "all_off"}


@mcp.tool()
def get_illumination_state() -> dict[str, Any]:
    """Get current illumination state for all channels.

    Returns:
        Dict with channel states (varies by hardware configuration)
    """
    app = get_app()
    illum = app.services.get("illumination")
    if illum is None:
        return {"error": "Illumination service not available"}

    return illum.get_state()
