#!/usr/bin/env python3
"""
MCP Server for Squid Microscope Control

This MCP (Model Context Protocol) server allows Claude Code to directly control
the Squid microscope while the GUI is running.

Architecture:
- GUI runs with MicroscopeControlServer (TCP server on port 5050)
- This MCP server connects to the TCP server
- Claude Code connects to this MCP server via stdio

Usage:
1. Start the Squid microscope GUI (which starts the TCP control server)
2. Configure Claude Code to use this MCP server
3. Claude Code can now call microscope control tools directly

Claude Code configuration (~/.claude/claude_code_config.json):
{
  "mcpServers": {
    "squid-microscope": {
      "command": "python",
      "args": ["/path/to/mcp_microscope_server.py"],
      "env": {}
    }
  }
}
"""

import asyncio
import json
import socket
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Default connection settings for the microscope control server
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050


def send_command(
    command: str,
    params: Optional[dict] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
) -> dict:
    """Send a command to the microscope control server."""
    request = {"command": command, "params": params or {}}

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

            buffer = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if b"\n" in buffer:
                    break

            return json.loads(buffer.decode("utf-8").strip())
    except ConnectionRefusedError:
        return {
            "success": False,
            "error": "Cannot connect to microscope. Is the Squid GUI running with the control server enabled?",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# Create MCP server
app = Server("squid-microscope")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available microscope control tools."""
    return [
        Tool(
            name="microscope_get_position",
            description="Get the current XYZ stage position of the microscope in millimeters",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_move_to",
            description="Move the microscope stage to an absolute XYZ position. Coordinates are in millimeters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x_mm": {
                        "type": "number",
                        "description": "Target X position in millimeters (optional)",
                    },
                    "y_mm": {
                        "type": "number",
                        "description": "Target Y position in millimeters (optional)",
                    },
                    "z_mm": {
                        "type": "number",
                        "description": "Target Z position in millimeters (optional)",
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "Wait for move to complete (default: true)",
                        "default": True,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="microscope_move_relative",
            description="Move the microscope stage by a relative amount. Distances are in millimeters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dx_mm": {
                        "type": "number",
                        "description": "Relative X movement in millimeters (default: 0)",
                        "default": 0,
                    },
                    "dy_mm": {
                        "type": "number",
                        "description": "Relative Y movement in millimeters (default: 0)",
                        "default": 0,
                    },
                    "dz_mm": {
                        "type": "number",
                        "description": "Relative Z movement in millimeters (default: 0)",
                        "default": 0,
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "Wait for move to complete (default: true)",
                        "default": True,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="microscope_home",
            description="Home the microscope stage. This moves the stage to its reference/home position.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "boolean", "description": "Home X axis", "default": True},
                    "y": {"type": "boolean", "description": "Home Y axis", "default": True},
                    "z": {"type": "boolean", "description": "Home Z axis", "default": True},
                },
                "required": [],
            },
        ),
        Tool(
            name="microscope_start_live",
            description="Start live imaging mode. The camera will continuously stream images to the GUI.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_stop_live",
            description="Stop live imaging mode.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_acquire_image",
            description="Acquire a single image from the microscope camera. Optionally save to a file path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "File path to save the image (optional). Supports TIFF format.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="microscope_set_channel",
            description="Set the current imaging channel/mode (e.g., 'BF', 'DAPI', 'GFP', etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel_name": {
                        "type": "string",
                        "description": "Name of the channel to activate",
                    },
                },
                "required": ["channel_name"],
            },
        ),
        Tool(
            name="microscope_get_channels",
            description="Get list of available imaging channels for the current objective",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_set_exposure",
            description="Set the camera exposure time in milliseconds",
            inputSchema={
                "type": "object",
                "properties": {
                    "exposure_ms": {
                        "type": "number",
                        "description": "Exposure time in milliseconds",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Channel to set exposure for (optional, applies to current if not specified)",
                    },
                },
                "required": ["exposure_ms"],
            },
        ),
        Tool(
            name="microscope_set_illumination_intensity",
            description="Set the illumination/laser intensity for a specific channel (0-100%)",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name",
                    },
                    "intensity": {
                        "type": "number",
                        "description": "Intensity value (0-100)",
                    },
                },
                "required": ["channel", "intensity"],
            },
        ),
        Tool(
            name="microscope_get_objectives",
            description="Get list of available objectives and the currently selected one",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_set_objective",
            description="Set the current objective",
            inputSchema={
                "type": "object",
                "properties": {
                    "objective_name": {
                        "type": "string",
                        "description": "Name of the objective to select",
                    },
                },
                "required": ["objective_name"],
            },
        ),
        Tool(
            name="microscope_turn_on_illumination",
            description="Turn on the illumination for the current channel",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_turn_off_illumination",
            description="Turn off the illumination",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_get_status",
            description="Get comprehensive status of the microscope including position, objective, exposure, etc.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="microscope_ping",
            description="Check if the microscope control server is running and responsive",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls by forwarding to microscope control server."""

    # Map tool names to control server commands
    tool_to_command = {
        "microscope_get_position": "get_position",
        "microscope_move_to": "move_to",
        "microscope_move_relative": "move_relative",
        "microscope_home": "home",
        "microscope_start_live": "start_live",
        "microscope_stop_live": "stop_live",
        "microscope_acquire_image": "acquire_image",
        "microscope_set_channel": "set_channel",
        "microscope_get_channels": "get_channels",
        "microscope_set_exposure": "set_exposure",
        "microscope_set_illumination_intensity": "set_illumination_intensity",
        "microscope_get_objectives": "get_objectives",
        "microscope_set_objective": "set_objective",
        "microscope_turn_on_illumination": "turn_on_illumination",
        "microscope_turn_off_illumination": "turn_off_illumination",
        "microscope_get_status": "get_status",
        "microscope_ping": "ping",
    }

    if name not in tool_to_command:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    command = tool_to_command[name]

    # Run the blocking socket call in a thread pool
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: send_command(command, arguments))

    if response.get("success"):
        result = response.get("result", {})
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        error = response.get("error", "Unknown error")
        return [TextContent(type="text", text=f"Error: {error}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
