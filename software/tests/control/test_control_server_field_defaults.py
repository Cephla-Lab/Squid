"""Control server commands get their declared defaults for the parameters a request leaves out.

A command's ``Field(...)`` defaults are schema metadata. Python itself hands a left-out parameter the pydantic
``FieldInfo`` object, so before ``schema_method`` filled in the real defaults, ``move_to`` with only x and y went on
to move Z to a ``FieldInfo``.
"""

import inspect
import json
import socket
import time
from typing import Optional
from unittest.mock import MagicMock

import numpy as np
import pytest
from pydantic.fields import FieldInfo

from control.microscope_control_server import Field, MicroscopeControlServer, schema_method


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _send(port: int, command: str, params: dict) -> Optional[dict]:
    """One request over TCP, as a client sends it; None when the server closes the connection without a reply."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall((json.dumps({"command": command, "params": params}) + "\n").encode("utf-8"))
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer += chunk
    return json.loads(buffer) if buffer else None


@pytest.fixture
def microscope():
    microscope = MagicMock()
    microscope.stage.get_pos.return_value = MagicMock(x_mm=10.0, y_mm=20.0, z_mm=1.0)
    return microscope


@pytest.fixture
def running_server(microscope):
    server = MicroscopeControlServer(microscope=microscope, port=_free_port())
    server.start()
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", server.port), timeout=1).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    yield server
    server.stop()


# --- Over TCP, as a robot or the MCP bridge sends them --------------------------------------------------------------


def test_move_to_with_only_x_and_y_moves_only_x_and_y(running_server, microscope):
    reply = _send(running_server.port, "move_to", {"x_mm": 10.0, "y_mm": 20.0})

    assert reply is not None and reply["success"], reply
    microscope.move_x_to.assert_called_once_with(10.0, blocking=True)
    microscope.move_y_to.assert_called_once_with(20.0, blocking=True)
    microscope.move_z_to.assert_not_called()


def test_move_relative_with_only_dz_moves_only_z(running_server, microscope):
    reply = _send(running_server.port, "move_relative", {"dz_mm": 0.5})

    assert reply is not None and reply["success"], reply
    microscope.move_x.assert_not_called()
    microscope.move_y.assert_not_called()
    microscope.stage.move_z.assert_called_once_with(0.5, blocking=True)


def test_acquire_image_without_a_save_path_saves_nothing(running_server, microscope):
    microscope.acquire_image.return_value = np.zeros((4, 4), dtype=np.uint16)

    reply = _send(running_server.port, "acquire_image", {})

    assert reply is not None and reply["success"], reply
    assert "saved_to" not in reply["result"]
    assert reply["result"]["shape"] == [4, 4]


def test_a_left_out_required_parameter_is_refused_by_name(running_server, microscope):
    reply = _send(running_server.port, "set_channel", {})

    microscope.live_controller.set_microscope_mode.assert_not_called()
    assert reply is not None and not reply["success"], reply
    assert "channel_name" in reply["error"]


# --- Called directly ------------------------------------------------------------------------------------------------


def test_set_exposure_without_a_channel_sets_the_camera(microscope):
    server = MicroscopeControlServer(microscope=microscope)

    server._cmd_set_exposure(exposure_ms=50.0)

    microscope.camera.set_exposure_time.assert_called_once_with(50.0)
    microscope.set_exposure_time.assert_not_called()


@schema_method
def _toy_command(
    required: str = Field(..., description="Has no default"),
    optional_none: Optional[float] = Field(None, description="Left out means None"),
    optional_value: float = Field(2.5, description="Left out means 2.5"),
):
    return {"required": required, "optional_none": optional_none, "optional_value": optional_value}


def test_left_out_parameters_get_their_field_defaults():
    assert _toy_command(required="a") == {"required": "a", "optional_none": None, "optional_value": 2.5}


def test_given_parameters_pass_through_by_name_or_position():
    assert _toy_command("a", 1.0, optional_value=3.0) == {"required": "a", "optional_none": 1.0, "optional_value": 3.0}


def test_a_left_out_required_parameter_raises_type_error_naming_it():
    with pytest.raises(TypeError, match="'required'"):
        _toy_command(optional_value=1.0)


def test_the_schema_still_lists_required_parameters_and_defaults():
    schema = _toy_command._schema

    assert schema["required"] == ["required"]
    assert schema["parameters"]["optional_value"]["default"] == 2.5
    assert "default" not in schema["parameters"]["optional_none"]


_COMMAND_NAMES = sorted(
    name for name, _ in inspect.getmembers(MicroscopeControlServer, inspect.isfunction) if name.startswith("_cmd_")
)


@pytest.mark.parametrize("name", _COMMAND_NAMES)
def test_every_command_gets_real_values_for_the_parameters_left_out(name):
    """Each command's own signature, run through schema_method with a body that records what it was given."""
    signature = inspect.signature(getattr(MicroscopeControlServer, name).__wrapped__)
    received = {}

    def body(*args, **kwargs):
        # What the real command would see: Python fills each parameter left out of the call from the signature
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        received.update(bound.arguments)

    body.__signature__ = signature
    command = schema_method(body)
    required = {param: object() for param in command._schema["required"]}

    command("server", **required)

    assert all(received[param] is value for param, value in required.items())
    assert [param for param, value in received.items() if isinstance(value, FieldInfo)] == []
