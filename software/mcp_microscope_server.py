#!/usr/bin/env python3
"""
MCP Server for Squid Microscope Control

This MCP (Model Context Protocol) server allows Claude Code to directly control
the Squid microscope while the GUI is running.

Architecture:
- GUI runs with MicroscopeControlServer (TCP server on port 5050)
- This MCP server connects to the TCP server and dynamically fetches available commands
- Claude Code connects to this MCP server via stdio

Usage:
1. Start the Squid microscope GUI (which starts the TCP control server)
2. Configure Claude Code to use this MCP server
3. Claude Code can now call microscope control tools directly

Claude Code configuration (.mcp.json in project directory):
{
  "mcpServers": {
    "squid-microscope": {
      "command": "python3",
      "args": ["/path/to/mcp_microscope_server.py"]
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

# Cache for schemas (refreshed on each list_tools call)
_schemas_cache: Optional[dict] = None


MAX_BUFFER_SIZE = 10 * 1024 * 1024  # 10 MB limit to prevent memory exhaustion


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
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_BUFFER_SIZE:
                    return {"success": False, "error": "Response too large"}
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


def fetch_schemas() -> dict:
    """Fetch command schemas from the microscope control server."""
    global _schemas_cache

    response = send_command("get_schemas", timeout=10)
    if response.get("success"):
        _schemas_cache = response.get("result", {}).get("schemas", {})
    else:
        # Return empty if server not available
        _schemas_cache = {}

    return _schemas_cache


def schema_to_mcp_tool(command_name: str, schema: dict) -> Tool:
    """Convert a command schema to an MCP Tool definition."""
    # Build JSON Schema properties from the schema
    properties = {}
    for param_name, param_info in schema.get("parameters", {}).items():
        prop = {"type": param_info.get("type", "string")}
        if "description" in param_info:
            prop["description"] = param_info["description"]
        if "default" in param_info:
            prop["default"] = param_info["default"]
        if "minimum" in param_info:
            prop["minimum"] = param_info["minimum"]
        if "maximum" in param_info:
            prop["maximum"] = param_info["maximum"]
        properties[param_name] = prop

    return Tool(
        name=f"microscope_{command_name}",
        description=schema.get("description", f"Execute {command_name} command"),
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": schema.get("required", []),
        },
    )


# Create MCP server
app = Server("squid-microscope")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available microscope control tools by fetching schemas from the server."""
    # Fetch schemas from the microscope control server
    loop = asyncio.get_event_loop()
    schemas = await loop.run_in_executor(None, fetch_schemas)

    if not schemas:
        # Return a minimal ping tool if server is not available
        return [
            Tool(
                name="microscope_ping",
                description="Check if the microscope control server is running and responsive",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            )
        ]

    # Convert all schemas to MCP tools
    tools = []
    for command_name, schema in schemas.items():
        # Skip get_schemas itself from the tool list
        if command_name == "get_schemas":
            continue
        tools.append(schema_to_mcp_tool(command_name, schema))

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls by forwarding to microscope control server."""
    # Extract command name from tool name (remove "microscope_" prefix)
    if name.startswith("microscope_"):
        command = name[len("microscope_") :]
    else:
        command = name

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
