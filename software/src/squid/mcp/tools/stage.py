"""Stage movement tools for MCP."""
from __future__ import annotations

from typing import Any, Optional

from squid.mcp.context import check_mode_gate, format_position
from squid.mcp.server import get_app, mcp


@mcp.tool()
def get_position() -> dict[str, Any]:
    """Get current stage position in millimeters.

    Returns:
        Dict with x_mm, y_mm, z_mm coordinates
    """
    app = get_app()
    stage = app.services.get("stage")
    if stage is None:
        return {"error": "Stage service not available"}

    return format_position(
        stage.position_x_mm,
        stage.position_y_mm,
        stage.position_z_mm,
    )


@mcp.tool()
def move_to(
    x_mm: Optional[float] = None,
    y_mm: Optional[float] = None,
    z_mm: Optional[float] = None,
    blocking: bool = True,
) -> dict[str, Any]:
    """Move stage to absolute position.

    Args:
        x_mm: Target X position in millimeters
        y_mm: Target Y position in millimeters
        z_mm: Target Z position in millimeters
        blocking: Wait for movement to complete (default True)

    Returns:
        Final position after move
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "move stage")

    stage = app.services.get("stage")
    if stage is None:
        return {"error": "Stage service not available"}

    # Move XY if specified
    if x_mm is not None or y_mm is not None:
        target_x = x_mm if x_mm is not None else stage.position_x_mm
        target_y = y_mm if y_mm is not None else stage.position_y_mm
        stage.move_to(target_x, target_y, blocking=blocking)

    # Move Z if specified
    if z_mm is not None:
        stage.move_z(z_mm, blocking=blocking)

    return format_position(
        stage.position_x_mm,
        stage.position_y_mm,
        stage.position_z_mm,
    )


@mcp.tool()
def move_relative(
    dx_mm: float = 0.0,
    dy_mm: float = 0.0,
    dz_mm: float = 0.0,
) -> dict[str, Any]:
    """Move stage by relative amount.

    Args:
        dx_mm: Distance to move in X (millimeters)
        dy_mm: Distance to move in Y (millimeters)
        dz_mm: Distance to move in Z (millimeters)

    Returns:
        Final position after move
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "move stage")

    stage = app.services.get("stage")
    if stage is None:
        return {"error": "Stage service not available"}

    # Move XY relative
    if dx_mm != 0 or dy_mm != 0:
        stage.move_relative(dx_mm, dy_mm)

    # Move Z relative
    if dz_mm != 0:
        stage.move_z_relative(dz_mm)

    return format_position(
        stage.position_x_mm,
        stage.position_y_mm,
        stage.position_z_mm,
    )


@mcp.tool()
def home(x: bool = True, y: bool = True, z: bool = True) -> dict[str, Any]:
    """Home stage axes to reference position.

    Args:
        x: Home X axis (default True)
        y: Home Y axis (default True)
        z: Home Z axis (default True)

    Returns:
        Dict with status and which axes were homed
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "home stage")

    stage = app.services.get("stage")
    if stage is None:
        return {"error": "Stage service not available"}

    stage.home(x=x, y=y, z=z)

    return {"status": "homed", "axes": {"x": x, "y": y, "z": z}}
