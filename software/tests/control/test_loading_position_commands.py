"""Moves to and from the loading position for automation (a robot loading plates): StageUtils' unattended entry
point, which runs the same move as its button without any dialog, and the control server commands built on it."""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QMessageBox

import control._def as _def
from control.widgets import UNATTENDED_MOVE_ALREADY_THERE, StageUtils
from control.widgets_z_lift import LiftMotionGuard
from squid.abc import Pos


def _stage(x_mm=47.5, y_mm=31.25, move_s=0.0):
    """A stage without a Z lift whose moves land where they are sent, each taking move_s."""
    stage = MagicMock(spec=["move_x_to", "move_y_to", "move_z_to", "get_pos"])
    pos = {"x": x_mm, "y": y_mm}

    def mover(axis):
        def move(mm, *a, **k):
            time.sleep(move_s)
            pos[axis] = mm

        return move

    stage.move_x_to.side_effect = mover("x")
    stage.move_y_to.side_effect = mover("y")
    stage.get_pos.side_effect = lambda: Pos(x_mm=pos["x"], y_mm=pos["y"], z_mm=1.0, theta_rad=None)
    return stage


def _stage_utils(qtbot, stage=None):
    live = MagicMock()
    live.is_live = False
    dialog = StageUtils(stage or _stage(), live, is_wellplate=False)
    qtbot.addWidget(dialog)
    return dialog


def _unattended(qtbot, dialog, to_loading, move_id=7):
    """Start a move as the control server does and wait for its end: [success, error message]."""
    with qtbot.waitSignal(dialog.signal_position_move_finished, timeout=15000) as finished:
        assert dialog.start_unattended_position_move(to_loading, move_id) == ""
    assert finished.args[0] == move_id  # the end names the move it ends
    return finished.args[1:]


# --- StageUtils ---------------------------------------------------------------------------------------------------


def test_an_unattended_move_to_loading_reports_back_when_the_stage_is_there(qtbot):
    dialog = _stage_utils(qtbot)
    assert _unattended(qtbot, dialog, to_loading=True) == [True, ""]
    assert dialog.slide_position == "loading"
    assert dialog.btn_load_slide.text() == "Return from Loading Position"  # the button follows


def test_an_unattended_return_goes_back_to_where_the_stage_was_without_asking(qtbot):
    stage = _stage(x_mm=47.5, y_mm=31.25)
    dialog = _stage_utils(qtbot, stage)
    dialog.ask_objective_return = True  # a Z lift system asks the operator; a robot cannot answer
    _unattended(qtbot, dialog, to_loading=True)
    stage._scanning_position_z_mm = 2.5  # the objective was retracted from 2.5 mm for loading

    with patch("control.widgets._ask_objective_z_return", side_effect=AssertionError("asked")):
        assert _unattended(qtbot, dialog, to_loading=False) == [True, ""]
    end = stage.get_pos()
    assert (end.x_mm, end.y_mm) == (47.5, 31.25)
    stage.move_z_to.assert_called_once_with(2.5)  # the objective comes back down, as an OK on the dialog does
    assert dialog.slide_position == "scanning"
    assert dialog.btn_load_slide.text() == "Move to Loading Position"


def test_an_unattended_move_that_fails_reports_the_error_without_a_dialog(qtbot):
    stage = _stage()
    stage.move_y_to.side_effect = TimeoutError("y stalled")
    dialog = _stage_utils(qtbot, stage)
    with patch.object(QMessageBox, "warning") as warning:
        success, error = _unattended(qtbot, dialog, to_loading=True)
    assert success is False and "y stalled" in error
    warning.assert_not_called()
    assert dialog.slide_position == "loading"  # as after the button: treated as there, the return is offered


def test_an_unattended_move_to_where_the_stage_already_is_moves_nothing(qtbot):
    stage = _stage()
    dialog = _stage_utils(qtbot, stage)
    assert dialog.start_unattended_position_move(False, 1) == UNATTENDED_MOVE_ALREADY_THERE  # not at loading
    _unattended(qtbot, dialog, to_loading=True)
    stage.move_x_to.reset_mock()
    stage.move_y_to.reset_mock()
    assert dialog.start_unattended_position_move(True, 1) == UNATTENDED_MOVE_ALREADY_THERE
    stage.move_x_to.assert_not_called()
    stage.move_y_to.assert_not_called()


def test_nothing_starts_while_a_move_to_or_from_loading_is_running(qtbot):
    dialog = _stage_utils(qtbot)
    with patch("control.widgets.move_to_loading_position") as move:  # starts, never reports back
        assert dialog.start_unattended_position_move(True, 1) == ""
        refusal = dialog.start_unattended_position_move(True, 1)
        dialog.switch_position()  # the button, had a click got through
    assert refusal not in ("", UNATTENDED_MOVE_ALREADY_THERE)
    move.assert_called_once()

    dialog._callback_loading_position_reached(True, None)  # it got there
    with patch("control.widgets.move_to_scanning_position") as move:
        assert dialog.start_unattended_position_move(False, 1) == ""
        # leaving the loading position is not being at it: a robot must not be told to reach in now
        refusal = dialog.start_unattended_position_move(True, 1)
    assert refusal not in ("", UNATTENDED_MOVE_ALREADY_THERE)
    move.assert_called_once()

    dialog._callback_scanning_position_reached(True, None)  # back
    with patch("control.widgets.move_to_loading_position") as move:
        assert dialog.start_unattended_position_move(True, 1) == ""
    move.assert_called_once()


def test_an_unattended_move_is_refused_while_the_lift_is_held(qtbot):
    dialog = _stage_utils(qtbot)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    assert guard.claim()  # e.g. an objective swap is running
    with patch("control.widgets.move_to_loading_position") as move:
        refusal = dialog.start_unattended_position_move(True, 1)
    assert refusal not in ("", UNATTENDED_MOVE_ALREADY_THERE)
    move.assert_not_called()


def test_a_move_is_reported_once_even_when_live_view_cannot_restart(qtbot, caplog):
    dialog = _stage_utils(qtbot)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    dialog.live_controller.is_live = True  # live was on, so it comes back on after each move
    dialog.live_controller.start_live.side_effect = RuntimeError("camera gone")

    assert _unattended(qtbot, dialog, to_loading=True) == [True, ""]
    assert dialog.slide_position == "loading"
    assert _unattended(qtbot, dialog, to_loading=False) == [True, ""]

    assert dialog.slide_position == "scanning"  # not reported a second time, as a failure, and so back at loading
    assert dialog.btn_load_slide.text() == "Move to Loading Position"
    assert not guard.busy and not guard.at_loading_position
    assert "camera gone" in caplog.text


def test_a_move_whose_thread_cannot_start_is_a_failed_move_that_holds_nothing(qtbot):
    dialog = _stage_utils(qtbot)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    with patch("control.widgets.move_to_loading_position", side_effect=RuntimeError("can't start new thread")):
        success, error = _unattended(qtbot, dialog, to_loading=True)

    assert success is False and "can't start new thread" in error
    # as after a move to loading that failed in its thread: treated as there, the return is offered
    assert dialog.slide_position == "loading"
    assert dialog.btn_load_slide.isEnabled()
    assert not guard.busy
    assert _unattended(qtbot, dialog, to_loading=False) == [True, ""]  # nothing left refusing the next move


def test_a_robot_move_is_refused_while_the_operator_answers_the_return_question(qtbot):
    # A Z lift system asks, on the button's return, whether the objective comes back down. The question's event loop
    # runs the control server's queued calls: a robot's return must not start under it, then a second move start on
    # the operator's answer (and the operator's "stay retracted" be lost to the robot's default).
    stage = _stage()
    dialog = _stage_utils(qtbot, stage)
    dialog.ask_objective_return = True
    _unattended(qtbot, dialog, to_loading=True)
    stage._scanning_position_z_mm = 2.5  # the objective was retracted from 2.5 mm for loading
    robot = []

    def operator_answers(*_):
        robot.append(dialog.start_unattended_position_move(False, 9))  # arrives while the question is open
        return QMessageBox.Ok

    with patch("control.widgets._ask_objective_z_return", side_effect=operator_answers):
        with patch("control.widgets.move_to_scanning_position") as move:  # starts, never reports back
            dialog.switch_position()

    assert robot and robot[0] not in ("", UNATTENDED_MOVE_ALREADY_THERE)
    move.assert_called_once()
    assert move.call_args.kwargs["restore_z"] is True  # the operator's answer
    assert dialog._position_move_id == 0  # the move running is the button's


def test_a_failed_button_move_is_reported_before_its_warning_opens(qtbot):
    # The warning's event loop runs the control server's queued calls: a robot's move may start under it. The failed
    # move must have been reported under its own id by then, not under the robot's once the warning is dismissed.
    stage = _stage()
    stage.move_y_to.side_effect = TimeoutError("y stalled")
    dialog = _stage_utils(qtbot, stage)
    ends = []
    dialog.signal_position_move_finished.connect(lambda *end: ends.append(end))
    robot = []

    def warning(*_):
        robot.append(dialog.start_unattended_position_move(False, 9))  # the way out of a failed move
        return QMessageBox.Ok

    with patch.object(QMessageBox, "warning", side_effect=warning):
        with patch("control.widgets.move_to_scanning_position"):  # the robot's return: starts, never reports back
            dialog.switch_position()
            qtbot.waitUntil(lambda: len(robot) > 0, timeout=15000)

    assert robot == [""]
    assert [(move_id, success) for move_id, success, _ in ends] == [(0, False)]
    assert "y stalled" in ends[0][2]


# --- control server commands --------------------------------------------------------------------------------------


def _server(stage_utils, acquisition_running=False, gui=True):
    from control.microscope_control_server import MicroscopeControlServer

    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        server = MicroscopeControlServer()
    server._log = MagicMock()
    server.microscope = MagicMock()
    server.microscope.stage = stage_utils.stage
    server.multipoint_controller = MagicMock()
    server.multipoint_controller.acquisition_in_progress.return_value = acquisition_running
    server.gui = SimpleNamespace(stageUtils=stage_utils) if gui else None
    return server


def _call(qtbot, command):
    """Run a command from another thread, as the server's socket thread does, while the GUI thread's event loop
    keeps going (the command waits on it)."""
    outcome = {}

    def run():
        try:
            outcome["result"] = command()
        except Exception as e:
            outcome["error"] = e

    thread = threading.Thread(target=run)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=30000)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


_LOADING_XY = (_def.SLIDE_POSITION.LOADING_X_MM, _def.SLIDE_POSITION.LOADING_Y_MM)
_SCANNING_XY = (_def.SLIDE_POSITION.SCANNING_X_MM, _def.SLIDE_POSITION.SCANNING_Y_MM)


def _xy(position):
    return (position["x_mm"], position["y_mm"])


def test_move_to_loading_position_replies_once_the_stage_is_there(qtbot):
    stage = _stage(move_s=0.2)  # slow enough that a reply sent before the move ends shows the old XY
    dialog = _stage_utils(qtbot, stage)
    result = _call(qtbot, _server(dialog)._cmd_move_to_loading_position)
    assert result["moved"] is True
    assert _xy(result["position"]) == _LOADING_XY
    assert dialog.slide_position == "loading" and dialog.btn_load_slide.text() == "Return from Loading Position"


def test_return_from_loading_position_goes_back_to_where_the_stage_was(qtbot):
    stage = _stage(x_mm=47.5, y_mm=31.25)
    dialog = _stage_utils(qtbot, stage)
    server = _server(dialog)
    _call(qtbot, server._cmd_move_to_loading_position)
    result = _call(qtbot, server._cmd_return_from_loading_position)
    assert result["moved"] is True and result["returned_to_pre_loading_xy"] is True
    assert _xy(result["position"]) == (47.5, 31.25)
    assert dialog.slide_position == "scanning"


def test_the_return_says_when_it_stopped_at_the_scanning_position(qtbot):
    dialog = _stage_utils(qtbot)
    dialog.slide_position = "loading"  # e.g. restored from a record without an XY: nothing to go back to
    result = _call(qtbot, _server(dialog)._cmd_return_from_loading_position)
    assert result["moved"] is True and result["returned_to_pre_loading_xy"] is False
    assert _xy(result["position"]) == _SCANNING_XY


def test_a_command_to_where_the_stage_already_is_replies_without_moving(qtbot):
    stage = _stage()
    dialog = _stage_utils(qtbot, stage)
    result = _call(qtbot, _server(dialog)._cmd_return_from_loading_position)
    assert result["moved"] is False and result["returned_to_pre_loading_xy"] is False
    stage.move_x_to.assert_not_called()


def test_the_commands_are_refused_while_an_acquisition_runs(qtbot):
    stage = _stage()
    dialog = _stage_utils(qtbot, stage)
    with pytest.raises(RuntimeError, match="acquisition"):
        _call(qtbot, _server(dialog, acquisition_running=True)._cmd_move_to_loading_position)
    stage.move_x_to.assert_not_called()
    assert dialog.slide_position is None


def test_a_refusal_from_stage_utils_is_the_error(qtbot):
    stage = _stage()
    dialog = _stage_utils(qtbot, stage)
    guard = LiftMotionGuard(dialog)
    dialog.set_lift_motion_guard(guard)
    assert guard.claim()  # e.g. an objective swap is running
    with pytest.raises(RuntimeError, match="Refused"):
        _call(qtbot, _server(dialog)._cmd_move_to_loading_position)
    stage.move_x_to.assert_not_called()


def test_a_failed_move_is_the_error_and_shows_no_dialog(qtbot):
    stage = _stage()
    stage.move_y_to.side_effect = TimeoutError("y stalled")
    dialog = _stage_utils(qtbot, stage)
    with patch.object(QMessageBox, "warning") as warning, pytest.raises(RuntimeError, match="y stalled"):
        _call(qtbot, _server(dialog)._cmd_move_to_loading_position)
    warning.assert_not_called()


def test_after_a_failed_move_the_robot_is_not_told_the_stage_is_at_loading(qtbot):
    stage = _stage(x_mm=47.5, y_mm=31.25)
    moves_y = stage.move_y_to.side_effect
    stage.move_y_to.side_effect = TimeoutError("y stalled")  # the stage stops short, somewhere on the way
    dialog = _stage_utils(qtbot, stage)
    server = _server(dialog)
    with pytest.raises(RuntimeError, match="y stalled"):
        _call(qtbot, server._cmd_move_to_loading_position)

    # Asked again, it must not say "there, moved: false": a robot would reach in
    with pytest.raises(RuntimeError, match="Refused.*return from the loading position"):
        _call(qtbot, server._cmd_move_to_loading_position)

    stage.move_y_to.side_effect = moves_y
    result = _call(qtbot, server._cmd_return_from_loading_position)  # the way out, as the button offers
    assert result["moved"] is True and _xy(result["position"]) == (47.5, 31.25)
    assert _call(qtbot, server._cmd_move_to_loading_position)["moved"] is True


def test_a_stage_moved_away_from_the_loading_position_is_not_reported_there(qtbot):
    # Something else moved the stage while it was at the loading position (a control-server move_to, or the joystick
    # on a system without a Z lift): asked again, it must not say "there, moved: false", or a robot reaches in.
    stage = _stage(x_mm=47.5, y_mm=31.25)
    dialog = _stage_utils(qtbot, stage)
    server = _server(dialog)
    _call(qtbot, server._cmd_move_to_loading_position)
    stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM + 20.0)

    with pytest.raises(RuntimeError, match="Refused.*no longer at the loading position.*return from"):
        _call(qtbot, server._cmd_move_to_loading_position)

    result = _call(qtbot, server._cmd_return_from_loading_position)  # the way out, as the button offers
    assert result["moved"] is True and _xy(result["position"]) == (47.5, 31.25)


def test_a_command_is_answered_by_its_own_move_not_by_another_one_ending(qtbot):
    # The race: the button's move ends just as a command comes in. Its end is queued to the GUI thread ahead of the
    # command's start, after the command began listening; it must not answer the command while its own move runs.
    dialog = _stage_utils(qtbot)
    server = _server(dialog)
    callbacks, outcome = [], {}

    def run():
        try:
            outcome["result"] = server._cmd_move_to_loading_position()
        except Exception as e:
            outcome["error"] = e

    def capture(*args, callback, **kwargs):
        callbacks.append(callback)

    with patch("control.widgets.move_to_scanning_position", side_effect=capture), patch(
        "control.widgets.move_to_loading_position", side_effect=capture
    ):
        dialog._callback_loading_position_reached(True, None)  # at the loading position
        dialog.switch_position()  # the button's move back to scanning
        callbacks[0](True, None)  # it ends: the report waits in the GUI thread's queue
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 10
        while dialog.receivers(dialog.signal_position_move_finished) == 0 and time.monotonic() < deadline:
            time.sleep(0.01)  # (no events processed meanwhile, so the report stays queued)
        time.sleep(0.1)  # the command's start is queued behind it now

        qtbot.waitUntil(lambda: len(callbacks) == 2, timeout=10000)  # the report ran, then the command's move started
        qtbot.wait(300)
        assert thread.is_alive(), outcome  # still waiting for its own move

        callbacks[1](False, "y stalled")  # its own move ends
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=10000)
    assert "y stalled" in str(outcome.get("error")), outcome


def test_an_unattended_move_is_refused_during_an_objective_swap(qtbot):
    dialog = _stage_utils(qtbot)  # no Z lift: no lift guard for the swap to hold
    dialog.set_objective_swap_active(True)
    with patch("control.widgets.move_to_loading_position") as move:
        refusal = dialog.start_unattended_position_move(True, 1)
    assert "objective" in refusal
    move.assert_not_called()

    dialog.set_objective_swap_active(False)
    with patch("control.widgets.move_to_loading_position") as move:
        assert dialog.start_unattended_position_move(True, 1) == ""
    move.assert_called_once()


def test_a_move_that_does_not_report_back_in_time_is_a_timeout(qtbot, monkeypatch):
    import control.microscope_control_server as server_module

    monkeypatch.setattr(server_module, "LOADING_POSITION_MOVE_TIMEOUT_S", 0.2)
    dialog = _stage_utils(qtbot)
    with patch("control.widgets.move_to_loading_position"), pytest.raises(TimeoutError):  # never reports back
        _call(qtbot, _server(dialog)._cmd_move_to_loading_position)


def test_the_commands_need_the_gui(qtbot):
    dialog = _stage_utils(qtbot)
    with pytest.raises(RuntimeError, match="GUI"):
        _server(dialog, gui=False)._cmd_move_to_loading_position()


def test_the_mcp_bridge_waits_for_the_whole_move():
    # The bridge's 30 s default is shorter than a move with a Z lift can take: the tool would fail while the
    # stage is still moving.
    import mcp_microscope_server

    with patch.object(mcp_microscope_server, "send_command", return_value={"success": True, "result": {}}) as send:
        for command in ("move_to_loading_position", "return_from_loading_position", "ping"):
            asyncio.run(mcp_microscope_server.call_tool(f"microscope_{command}", {}))
    timeouts = {call.args[0]: call.kwargs.get("timeout", 30.0) for call in send.call_args_list}
    assert timeouts["ping"] == 30.0
    assert timeouts["move_to_loading_position"] > 180 and timeouts["return_from_loading_position"] > 180


def test_the_tool_descriptions_are_whole_sentences():
    # An MCP client sees only the docstring's first line (schema_method)
    from control.microscope_control_server import MicroscopeControlServer

    for command in ("_cmd_move_to_loading_position", "_cmd_return_from_loading_position"):
        assert getattr(MicroscopeControlServer, command)._schema["description"].endswith("."), command
