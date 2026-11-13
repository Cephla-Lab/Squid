"""The control server asks the agent session for admission, and serves the session commands."""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from control.core.agent_session import AgentSession, AgentSessionRefused, SessionState, snapshot_microscope


class FakeGui:
    """Stands for the Squid window: it gives the control to the agent on the GUI thread, or says why not."""

    def __init__(self, microscope):
        self.microscope = microscope
        self.refuse_with = None
        self.calls = []

    def run_agent_session_transition(self, kind, note=""):
        self.calls.append((kind, note))
        if self.refuse_with:
            return self.refuse_with
        try:
            if kind == "begin":
                self.microscope.agent_session.begin()
            else:
                self.microscope.agent_session.hand_back(snapshot_microscope(self.microscope), note)
        except AgentSessionRefused as e:
            return str(e)
        return None


@pytest.fixture
def server():
    from control.microscope_control_server import MicroscopeControlServer

    microscope = MagicMock()
    microscope.agent_session = AgentSession()
    microscope.stage_homing_in_progress = False
    microscope.stage.get_pos.return_value = MagicMock(x_mm=1.0, y_mm=2.0, z_mm=0.5)
    microscope.objective_store.current_objective = "20x"
    microscope.camera.get_exposure_time.return_value = 10.0
    microscope.live_controller.is_live = False
    microscope.live_controller.currentConfiguration.name = "Fluorescence 638 nm Ex"
    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        srv = MicroscopeControlServer()
    srv._log = MagicMock()
    srv.microscope = microscope
    srv.multipoint_controller = MagicMock()
    srv.scan_coordinates = MagicMock()
    srv.gui = FakeGui(microscope)
    srv._commands = {
        name[len("_cmd_") :]: method
        for name, method in inspect.getmembers(srv, predicate=callable)
        if name.startswith("_cmd_")
    }
    return srv


@pytest.fixture
def handler(server):
    mock = MagicMock(return_value={"moved": True})
    server._commands["move_to"] = mock
    return mock


def human_drives(server):
    session = server.microscope.agent_session
    session.begin()
    session.request_stop(SessionState.HUMAN, "take over")
    session.finish_stop({"x_mm": 1.0})
    return session


def test_no_session_serves_action_commands_as_before(server, handler):
    assert server._execute("move_to", {}) == {"success": True, "result": {"moved": True}}


def test_action_command_refused_while_a_human_drives(server, handler):
    human_drives(server)
    assert server._execute("move_to", {}) == {"success": False, "error": "a human is driving"}
    handler.assert_not_called()
    server._log.error.assert_not_called()  # a refusal is not an error of the server


def test_reads_are_served_while_stopping(server):
    session = server.microscope.agent_session
    session.begin()
    session.request_stop(SessionState.HUMAN, "take over")
    assert server._execute("get_agent_session", {})["result"]["state"] == "stopping"
    assert server._execute("ping", {})["success"] is True


def test_handler_runs_counted(server):
    session = server.microscope.agent_session
    seen = []
    server._commands["move_to"] = lambda **kw: seen.append(session.in_flight)
    server._execute("move_to", {})
    assert seen == [1] and session.in_flight == 0


def test_session_commands_round_trip(server):
    begun = server._execute("begin_agent_session", {})["result"]
    assert begun["state"] == "agent" and begun["session_id"] == server.microscope.agent_session.session_id
    assert server._execute("take_over", {})["result"] == {"state": "stopping"}
    assert server._execute("get_agent_session", {})["result"]["stop_cause"] == "take over"
    server.microscope.agent_session.finish_stop({"x_mm": 1.0})
    assert server._execute("hand_back", {"note": "looked at one organism"})["result"] == {"state": "agent"}
    status = server._execute("get_agent_session", {})["result"]
    assert status["handover_count"] == 1 and status["handover"]["note"] == "looked at one organism"
    assert status["handover"]["after"]["x_mm"] == 1.0 and status["handover"]["after"]["objective"] == "20x"
    assert server._execute("end_agent_session", {})["result"] == {"state": "stopping"}
    assert server.gui.calls == [("begin", ""), ("hand_back", "looked at one organism")]


def test_refused_session_commands_are_error_replies(server):
    assert server._execute("take_over", {}) == {"success": False, "error": "no agent session"}
    assert server._execute("end_agent_session", {}) == {"success": False, "error": "no agent session"}
    assert server._execute("hand_back", {}) == {"success": False, "error": "no human is driving"}
    server.gui.refuse_with = "a dialog or a menu is open in Squid"
    assert server._execute("begin_agent_session", {}) == {
        "success": False,
        "error": "a dialog or a menu is open in Squid",
    }
    assert server.microscope.agent_session.state is SessionState.NONE


def test_a_session_needs_the_window(server):
    server.gui = None
    assert server._execute("begin_agent_session", {}) == {
        "success": False,
        "error": "an agent session needs the Squid window",
    }


def test_homing_gate_applies_to_actions_and_not_to_session_commands(server, handler):
    server._execute("begin_agent_session", {})
    server.microscope.stage_homing_in_progress = True
    assert server._execute("move_to", {}) == {"success": False, "error": "the stage is homing"}
    handler.assert_not_called()
    assert server.microscope.agent_session.in_flight == 0
    assert server._execute("take_over", {}) == {"success": True, "result": {"state": "stopping"}}
    assert server._execute("end_agent_session", {}) == {"success": True, "result": {"state": "stopping"}}


def test_unknown_command_is_still_an_error(server):
    assert server._execute("fly", {}) == {"success": False, "error": "Unknown command: fly"}
