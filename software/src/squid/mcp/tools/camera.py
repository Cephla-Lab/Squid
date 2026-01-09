"""Camera control tools for MCP."""
from __future__ import annotations

from typing import Any

from squid.mcp.server import get_app, mcp


@mcp.tool()
def set_exposure(exposure_ms: float) -> dict[str, Any]:
    """Set camera exposure time.

    Args:
        exposure_ms: Exposure time in milliseconds (0.1 to 10000)

    Returns:
        Dict with new exposure_ms value
    """
    if not 0.1 <= exposure_ms <= 10000:
        return {"error": "Exposure must be between 0.1 and 10000 ms"}

    app = get_app()
    camera = app.services.get("camera")
    if camera is None:
        return {"error": "Camera service not available"}

    camera.set_exposure_time(exposure_ms)

    return {"exposure_ms": camera.exposure_time_ms}


@mcp.tool()
def set_gain(gain: float) -> dict[str, Any]:
    """Set camera analog gain.

    Args:
        gain: Analog gain factor (typical range 1 to 64)

    Returns:
        Dict with new gain value
    """
    app = get_app()
    camera = app.services.get("camera")
    if camera is None:
        return {"error": "Camera service not available"}

    camera.set_analog_gain(gain)

    return {"gain": camera.analog_gain}


@mcp.tool()
def get_camera_settings() -> dict[str, Any]:
    """Get current camera settings.

    Returns:
        Dict with exposure_ms, gain, binning, and roi
    """
    app = get_app()
    camera = app.services.get("camera")
    if camera is None:
        return {"error": "Camera service not available"}

    return {
        "exposure_ms": getattr(camera, "exposure_time_ms", None),
        "gain": getattr(camera, "analog_gain", None),
        "binning": getattr(camera, "binning", None),
        "roi": getattr(camera, "roi", None),
    }
