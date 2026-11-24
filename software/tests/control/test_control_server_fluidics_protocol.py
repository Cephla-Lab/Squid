"""The control server's fluidics protocol commands, driven against a real ProtocolRunner on scripted engines.

The commands go through MicroscopeControlServer._execute, so each check sees the reply a client would get.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import RunnerState
from control.microscope_control_server import MicroscopeControlServer
from tests.control.core.fluidics_protocol.fakes import FakeFluidicsPort, FakeImagingPort, wait_until
from tests.control.core.fluidics_protocol.test_runner import _held, _runner


def _server(runner=None, has_tab=True, control=True):
    microscope = MagicMock()
    microscope.stage_homing_in_progress = False
    gui = SimpleNamespace(fluidicsProtocolWidget=SimpleNamespace(runner=runner) if has_tab else None)
    server = MicroscopeControlServer(microscope=microscope, port=0, gui=gui)
    server.set_fluidics_protocol_control_enabled(control)  # Settings → Enable MCP Fluidics Protocol Control
    return server


def _ok(server, command, **params):
    reply = server._execute(command, params)
    assert reply["success"], reply
    return reply["result"]


def _refused(server, command, **params):
    reply = server._execute(command, params)
    assert not reply["success"], reply
    return reply["error"]


@pytest.fixture
def runners():
    started = []
    yield started
    for runner in started:
        runner.abort_run()
        runner.wait(10)


def _start(runners, tmp_path, **engines):
    runner, fluidics, imaging, run_dir = _runner(tmp_path, **engines)
    runners.append(runner)
    runner.start()
    return runner, fluidics, imaging, run_dir


def test_commands_are_listed_in_the_schemas():
    schemas = _ok(_server(), "get_schemas")["schemas"]
    for name in (
        "get_fluidics_protocol_status",
        "fluidics_protocol_hold_action",
        "pause_fluidics_protocol",
        "resume_fluidics_protocol",
        "abort_fluidics_protocol",
        "start_fluidics_protocol",
    ):
        assert name in schemas
    assert schemas["fluidics_protocol_hold_action"]["required"] == ["action"]
    assert schemas["fluidics_protocol_hold_action"]["parameters"]["restore_tec"]["default"] is False


def test_the_commands_that_act_on_a_run_are_off_until_enabled_in_the_gui(tmp_path, runners):
    # the control socket has no authentication: like python_exec, acting on a run needs the Settings toggle
    runner, *_ = _start(runners, tmp_path, fluidics=FakeFluidicsPort(script=[("failed", 0, "boom"), ("finished",)]))
    assert _held(runner)
    server = MicroscopeControlServer(microscope=MagicMock(stage_homing_in_progress=False), port=0, gui=None)
    server.gui = SimpleNamespace(fluidicsProtocolWidget=SimpleNamespace(runner=runner))
    for command, params in (
        ("fluidics_protocol_hold_action", {"action": "restart"}),
        ("pause_fluidics_protocol", {}),
        ("resume_fluidics_protocol", {}),
        ("abort_fluidics_protocol", {}),
        ("start_fluidics_protocol", {"confirm": True}),
    ):
        assert "Enable MCP Fluidics Protocol Control" in _refused(server, command, **params), command
    status = _ok(server, "get_fluidics_protocol_status")  # reading stays open
    assert status["state"] == "HELD" and status["control_enabled"] is False
    assert runner.state is RunnerState.HELD and len(runner.manifest.steps[0].attempts) == 1

    server.set_fluidics_protocol_control_enabled(True)
    _ok(server, "fluidics_protocol_hold_action", action="restart")
    assert runner.wait(10) and runner.outcome == "finished"


def test_without_a_fluidics_tab_the_commands_say_so():
    server = _server(has_tab=False)
    assert "no Fluidics Protocol tab" in _refused(server, "get_fluidics_protocol_status")
    assert "no Fluidics Protocol tab" in _refused(server, "fluidics_protocol_hold_action", action="restart")


def test_before_any_run_the_status_has_no_run_and_actions_are_refused():
    server = _server(runner=None)
    assert _ok(server, "get_fluidics_protocol_status") == {"has_run": False, "state": None, "control_enabled": True}
    assert "No fluidics protocol run" in _refused(server, "fluidics_protocol_hold_action", action="restart")
    assert "No fluidics protocol run" in _refused(server, "pause_fluidics_protocol")


def test_a_held_fluidics_step_reports_its_hold_and_restart_runs_it_again(tmp_path, runners):
    fluidics = FakeFluidicsPort(script=[("failed", 0, "Flow fault on syringe_draw"), ("finished",)])
    runner, fluidics, imaging, run_dir = _start(runners, tmp_path, fluidics=fluidics)
    server = _server(runner)
    assert _held(runner)

    status = _ok(server, "get_fluidics_protocol_status")
    assert status["state"] == "HELD" and status["run_dir"] == str(run_dir) and status["run_name"] == "liver"
    assert (status["step_index"], status["total_steps"], status["attempt"]) == (0, 4, 1)
    assert (status["round"], status["step_kind"]) == ("setup", "fluidics")
    assert status["step_name"] == runner.manifest.steps[0].label
    hold = status["hold"]
    assert hold["reason"] == "failed" and "Flow fault" in hold["message"]
    assert hold["can_resume"] and not hold["can_accept"] and hold["resume_position"] == 0
    assert hold["allowed_actions"] == ["resume", "restart", "skip", "end"]
    # one value: the runner's, kept in the run manifest (which the HELD panel and the watchdog read)
    assert hold["held_at"] == runner.snapshot().held_at == manifest_io.read_manifest(run_dir).held_at
    assert hold["held_for_s"] >= 0
    assert status["last_heartbeat"] == runner.manifest.heartbeat_at and status["heartbeat_age_s"] >= 0

    assert "not offered" in _refused(server, "fluidics_protocol_hold_action", action="accept")
    assert "Unknown hold action 'retry'" in _refused(server, "fluidics_protocol_hold_action", action="retry")
    assert runner.state is RunnerState.HELD  # neither refusal touched the run

    result = _ok(server, "fluidics_protocol_hold_action", action="Restart")
    assert result == {"action": "restart", "step_index": 0, "attempt": 1}
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(runner.manifest.steps[0].attempts) == 2 and not runner.manifest.steps[0].skipped

    status = _ok(server, "get_fluidics_protocol_status")
    assert status["state"] == "ENDED" and status["outcome"] == "finished" and status["hold"] is None
    assert "not held (state ENDED)" in _refused(server, "fluidics_protocol_hold_action", action="restart")


def test_resume_is_refused_where_the_held_panel_offers_none(tmp_path, runners):
    # An imaging hold can't resume from where it stopped: the runner would quietly restart it instead.
    imaging = FakeImagingPort(script=["error", "completed"])
    runner, fluidics, imaging, run_dir = _start(runners, tmp_path, imaging=imaging)
    server = _server(runner)
    assert _held(runner)

    hold = _ok(server, "get_fluidics_protocol_status")["hold"]
    assert hold["kind"] == "imaging" and not hold["can_resume"]
    assert hold["allowed_actions"] == ["restart", "skip", "end"]
    error = _refused(server, "fluidics_protocol_hold_action", action="resume")
    assert "'resume' is not offered" in error and "restart, skip, end" in error
    assert runner.state is RunnerState.HELD and len(imaging.requests) == 1


def test_accept_takes_a_completed_with_errors_imaging_step(tmp_path, runners):
    imaging = FakeImagingPort(script=["completed_with_errors"])
    runner, fluidics, imaging, run_dir = _start(runners, tmp_path, imaging=imaging)
    server = _server(runner)
    assert _held(runner)

    assert "accept" in _ok(server, "get_fluidics_protocol_status")["hold"]["allowed_actions"]
    _ok(server, "fluidics_protocol_hold_action", action="accept")
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(imaging.requests) == 1 and not runner.manifest.steps[2].skipped


def test_end_from_a_hold_stops_the_run(tmp_path, runners):
    runner, *_ = _start(runners, tmp_path, fluidics=FakeFluidicsPort(script=[("failed", 0, "boom")]))
    server = _server(runner)
    assert _held(runner)
    _ok(server, "fluidics_protocol_hold_action", action="end")
    assert runner.wait(10) and runner.outcome == "stopped"


def test_pause_resume_and_abort_a_running_protocol(tmp_path, runners):
    fluidics = FakeFluidicsPort(script=[("hold",), ("hold",)])
    runner, fluidics, imaging, run_dir = _start(runners, tmp_path, fluidics=fluidics)
    server = _server(runner)
    assert wait_until(lambda: len(fluidics.starts) == 1)

    assert "not paused (state RUNNING)" in _refused(server, "resume_fluidics_protocol")
    assert "not held (state RUNNING)" in _refused(server, "fluidics_protocol_hold_action", action="restart")
    _ok(server, "pause_fluidics_protocol")
    assert wait_until(lambda: runner.state is RunnerState.PAUSED)
    assert "not running (state PAUSED)" in _refused(server, "pause_fluidics_protocol")

    _ok(server, "resume_fluidics_protocol")
    assert wait_until(lambda: len(fluidics.starts) == 2)  # the first step finished, the second is flowing

    _ok(server, "abort_fluidics_protocol")
    assert runner.wait(10) and runner.outcome == "stopped"
    assert "No fluidics protocol run in progress" in _refused(server, "abort_fluidics_protocol")


def test_resume_of_a_held_run_points_to_the_hold_action(tmp_path, runners):
    runner, *_ = _start(runners, tmp_path, fluidics=FakeFluidicsPort(script=[("failed", 0, "boom")]))
    server = _server(runner)
    assert _held(runner)
    assert "use fluidics_protocol_hold_action" in _refused(server, "resume_fluidics_protocol")


def test_restore_tec_must_be_a_boolean(tmp_path, runners):
    # "false" is a non-empty string: taken as-is it would turn the TEC back on
    runner, *_ = _start(runners, tmp_path, fluidics=FakeFluidicsPort(script=[("failed", 0, "boom"), ("finished",)]))
    server = _server(runner)
    assert _held(runner)
    assert "restore_tec must be true or false" in _refused(
        server, "fluidics_protocol_hold_action", action="restart", restore_tec="false"
    )
    assert runner.state is RunnerState.HELD and len(runner.manifest.steps[0].attempts) == 1
    _ok(server, "fluidics_protocol_hold_action", action="restart", restore_tec=False)
    assert runner.wait(10) and runner.outcome == "finished"
