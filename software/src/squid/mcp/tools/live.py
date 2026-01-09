"""Live imaging control tools for MCP."""
from __future__ import annotations

from typing import Any, Optional

from squid.mcp.context import check_mode_gate
from squid.mcp.server import get_app, mcp


@mcp.tool()
def start_live() -> dict[str, Any]:
    """Start live camera streaming.

    Begins continuous image acquisition and display.

    Returns:
        Dict with status and mode
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "start live view")

    # Import here to avoid circular imports
    from squid.core.events import StartLiveCommand

    app.event_bus.publish(StartLiveCommand())

    return {"status": "started", "mode": "LIVE"}


@mcp.tool()
def stop_live() -> dict[str, Any]:
    """Stop live camera streaming.

    Returns:
        Dict with status
    """
    app = get_app()

    from squid.core.events import StopLiveCommand

    app.event_bus.publish(StopLiveCommand())

    return {"status": "stopped"}


@mcp.tool()
def acquire_image(save_path: Optional[str] = None) -> dict[str, Any]:
    """Acquire a single image.

    Captures one frame from the camera without affecting live streaming.

    Args:
        save_path: Optional path to save the image as TIFF

    Returns:
        Image metadata (shape, dtype, path if saved)
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "acquire image")

    camera = app.services.get("camera")
    if camera is None:
        return {"error": "Camera service not available"}

    frame = camera.get_frame()
    if frame is None:
        return {"error": "Failed to capture frame"}

    result: dict[str, Any] = {
        "shape": list(frame.shape),
        "dtype": str(frame.dtype),
    }

    if save_path:
        try:
            import tifffile

            tifffile.imwrite(save_path, frame)
            result["saved_to"] = save_path
        except Exception as e:
            result["save_error"] = str(e)

    return result
