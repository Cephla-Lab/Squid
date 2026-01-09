"""MCP server for Squid microscope control.

This exposes the microscope to Claude Code and other MCP clients.

Usage:
    python -m squid.mcp.server --simulation  # Run with simulated hardware
    python -m squid.mcp.server --real        # Run with real hardware
"""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from squid.application import ApplicationContext

# Create FastMCP server
mcp = FastMCP("squid-microscope")

# Application context (initialized on startup)
_app: ApplicationContext | None = None


def get_app() -> "ApplicationContext":
    """Get the application context.

    Raises:
        RuntimeError: If context not initialized (use --simulation or --real flag)
    """
    global _app
    if _app is None:
        raise RuntimeError(
            "ApplicationContext not initialized. "
            "Start server with --simulation or --real flag."
        )
    return _app


def main() -> None:
    """Main entry point for MCP server."""
    parser = argparse.ArgumentParser(
        description="MCP server for Squid microscope control"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--simulation",
        action="store_true",
        help="Run in simulation mode (no hardware required)",
    )
    group.add_argument(
        "--real",
        action="store_true",
        help="Run with real hardware",
    )
    args = parser.parse_args()

    # Initialize application context
    global _app
    from squid.application import ApplicationContext

    _app = ApplicationContext(simulation=args.simulation)

    # Import tools after context is ready (they register with mcp)
    from squid.mcp.tools import (  # noqa: F401
        acquisition,
        camera,
        illumination,
        live,
        stage,
        system,
    )
    from squid.mcp.resources import status  # noqa: F401

    # Run the server
    mcp.run()


if __name__ == "__main__":
    main()
