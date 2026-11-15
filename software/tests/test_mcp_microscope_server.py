"""The MCP bridge (mcp_microscope_server.py) that Launch Claude Code starts.

The bridge is a separate script, so nothing else in the suite imports it. These tests make sure it still loads
with the installed ``mcp`` package, which once broke unnoticed when ``mcp`` 2.0 dropped the decorator API.
"""

import asyncio
import json
from unittest.mock import patch

import mcp_microscope_server

_SCHEMAS = {
    "get_schemas": {"description": "Not a tool", "parameters": {}, "required": []},
    "move_to": {
        "description": "Move stage to absolute XYZ position in millimeters.",
        "parameters": {"x_mm": {"type": "number", "description": "Target X"}},
        "required": [],
    },
}


def test_tools_are_the_control_server_commands():
    with patch.object(
        mcp_microscope_server, "send_command", return_value={"success": True, "result": {"schemas": _SCHEMAS}}
    ):
        tools = asyncio.run(mcp_microscope_server.list_tools())

    assert [tool.name for tool in tools] == ["microscope_move_to"]
    assert tools[0].inputSchema["properties"]["x_mm"] == {"type": "number", "description": "Target X"}


def test_a_tool_call_is_forwarded_as_its_command():
    reply = {"success": True, "result": {"moved_to": {"x_mm": 1.0}}}
    with patch.object(mcp_microscope_server, "send_command", return_value=reply) as send:
        content = asyncio.run(mcp_microscope_server.call_tool("microscope_move_to", {"x_mm": 1.0}))

    assert send.call_args.args[:2] == ("move_to", {"x_mm": 1.0})
    assert json.loads(content[0].text) == {"moved_to": {"x_mm": 1.0}}


def test_the_bridge_waits_for_an_autofocus_as_long_as_the_server_works_on_it():
    from control.microscope_control_server import MicroscopeControlServer

    longest = MicroscopeControlServer._AUTOFOCUS_TIMEOUT_S + MicroscopeControlServer._AUTOFOCUS_STOP_S
    with patch.object(mcp_microscope_server, "send_command", return_value={"success": True, "result": {}}) as send:
        asyncio.run(mcp_microscope_server.call_tool("microscope_autofocus", {}))
        asyncio.run(mcp_microscope_server.call_tool("microscope_move_to", {"x_mm": 1.0}))

    autofocus, move = send.call_args_list
    assert autofocus.kwargs["timeout"] > longest  # the reply of a long sweep is not lost
    assert move.kwargs["timeout"] == 30.0  # each other command as before
