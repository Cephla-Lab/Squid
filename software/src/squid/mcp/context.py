"""Context helpers for MCP tools."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.core.mode_gate import ModeGate


def check_mode_gate(mode_gate: "ModeGate", operation: str) -> None:
    """Raise error if system is busy and cannot accept commands.

    Args:
        mode_gate: The mode gate to check
        operation: Description of the operation being attempted

    Raises:
        RuntimeError: If system is blocked for commands
    """
    if mode_gate.is_blocked_for_commands():
        current_mode = mode_gate.get_current_mode()
        raise RuntimeError(
            f"Cannot {operation}: system is in {current_mode} mode. "
            "Wait for current operation to complete."
        )


def format_position(x: float, y: float, z: float) -> dict[str, Any]:
    """Format stage position for MCP response.

    Args:
        x: X position in mm
        y: Y position in mm
        z: Z position in mm

    Returns:
        Dict with x_mm, y_mm, z_mm keys, values rounded to 4 decimals
    """
    return {
        "x_mm": round(x, 4),
        "y_mm": round(y, 4),
        "z_mm": round(z, 4),
    }
