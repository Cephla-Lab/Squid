"""Multi-point acquisition tools for MCP."""
from __future__ import annotations

from typing import Any, Optional

from squid.mcp.context import check_mode_gate
from squid.mcp.server import get_app, mcp


@mcp.tool()
def set_acquisition_parameters(
    n_z: int = 1,
    delta_z_um: float = 1.0,
    n_x: int = 1,
    n_y: int = 1,
    delta_x_mm: float = 0.5,
    delta_y_mm: float = 0.5,
    use_autofocus: bool = False,
) -> dict[str, Any]:
    """Configure acquisition parameters.

    Sets up the multi-point acquisition grid and Z-stack parameters.

    Args:
        n_z: Number of Z slices per stack
        delta_z_um: Z step size in micrometers
        n_x: Number of FOVs in X direction
        n_y: Number of FOVs in Y direction
        delta_x_mm: X step size in millimeters
        delta_y_mm: Y step size in millimeters
        use_autofocus: Run autofocus at each position

    Returns:
        Dict with configured parameters and total_fovs
    """
    app = get_app()

    from squid.core.events import SetAcquisitionParametersCommand

    app.event_bus.publish(
        SetAcquisitionParametersCommand(
            n_z=n_z,
            delta_z_um=delta_z_um,
            n_x=n_x,
            n_y=n_y,
            delta_x_mm=delta_x_mm,
            delta_y_mm=delta_y_mm,
            use_autofocus=use_autofocus,
        )
    )

    return {
        "n_z": n_z,
        "delta_z_um": delta_z_um,
        "n_x": n_x,
        "n_y": n_y,
        "delta_x_mm": delta_x_mm,
        "delta_y_mm": delta_y_mm,
        "use_autofocus": use_autofocus,
        "total_fovs": n_x * n_y,
    }


@mcp.tool()
def start_acquisition(
    experiment_id: str,
    base_path: Optional[str] = None,
) -> dict[str, Any]:
    """Start a multi-point acquisition.

    Begins capturing images at all configured positions.

    Args:
        experiment_id: Unique identifier for this experiment
        base_path: Directory to save images (optional)

    Returns:
        Dict with status and experiment_id
    """
    app = get_app()
    check_mode_gate(app.mode_gate, "start acquisition")

    from squid.core.events import StartAcquisitionCommand

    app.event_bus.publish(
        StartAcquisitionCommand(
            experiment_id=experiment_id,
            base_path=base_path,
        )
    )

    return {"status": "started", "experiment_id": experiment_id}


@mcp.tool()
def stop_acquisition() -> dict[str, Any]:
    """Stop the current acquisition.

    Safely stops image capture at the current position.

    Returns:
        Dict with status
    """
    app = get_app()

    from squid.core.events import StopAcquisitionCommand

    app.event_bus.publish(StopAcquisitionCommand())

    return {"status": "stopped"}


@mcp.tool()
def get_acquisition_status() -> dict[str, Any]:
    """Get current acquisition progress.

    Returns:
        Dict with is_running, current_fov, total_fovs, progress_percent
    """
    app = get_app()
    controller = app.controllers.multipoint

    if controller is None:
        return {"error": "MultiPoint controller not available"}

    return {
        "is_running": getattr(controller, "is_acquiring", False),
        "current_fov": getattr(controller, "current_fov", 0),
        "total_fovs": getattr(controller, "total_fovs", 0),
        "progress_percent": getattr(controller, "progress_percent", 0),
    }
