"""Agent session state: one driver at a time, admission of commands, handover snapshots."""

import threading
from types import SimpleNamespace

import pytest

from control.core.agent_session import (
    REFUSAL_HUMAN,
    REFUSAL_STOPPING,
    SESSION_COMMANDS,
    AgentSession,
    AgentSessionRefused,
    SessionState,
    is_read_command,
    snapshot_microscope,
)


def stopped_for_human(session: AgentSession, snapshot=None) -> AgentSession:
    session.begin()
    session.request_stop(SessionState.HUMAN, "take over")
    session.finish_stop(snapshot if snapshot is not None else {})
    return session


def test_read_commands_and_session_commands():
    assert is_read_command("ping") and is_read_command("get_position") and not is_read_command("move_to")
    assert SESSION_COMMANDS == {"begin_agent_session", "take_over", "hand_back", "end_agent_session"}


def test_begin_gives_agent_and_an_id():
    s = AgentSession()
    assert s.state is SessionState.NONE and s.session_id is None
    sid = s.begin()
    assert s.state is SessionState.AGENT and s.session_id == sid and sid


def test_begin_refused_by_blocker_by_a_command_in_progress_and_by_a_session():
    s = AgentSession()
    s.set_blocker(lambda: "an acquisition is running")
    with pytest.raises(AgentSessionRefused, match="an acquisition is running"):
        s.begin()
    s.set_blocker(None)
    with s.admit("move_to"):
        with pytest.raises(AgentSessionRefused, match="a command is in progress"):
            s.begin()
    s.begin()
    with pytest.raises(AgentSessionRefused, match="a session exists"):
        s.begin()


def test_action_commands_refused_in_stopping_and_human():
    s = AgentSession()
    s.begin()
    s.request_stop(SessionState.HUMAN, "take over")
    with pytest.raises(AgentSessionRefused, match="^a stop is in progress$"):
        with s.admit("move_to"):
            pass
    s.finish_stop({"x_mm": 1.0})
    with pytest.raises(AgentSessionRefused, match="^a human is driving$"):
        with s.admit("move_to"):
            pass
    assert (REFUSAL_STOPPING, REFUSAL_HUMAN) == ("a stop is in progress", "a human is driving")


def test_reads_and_session_commands_admitted_in_every_state():
    s = AgentSession()
    s.begin()
    s.request_stop(SessionState.HUMAN, "take over")
    for command in ("ping", "get_position", "take_over", "hand_back", "end_agent_session"):
        with s.admit(command):
            assert s.in_flight == 0


def test_in_flight_counts_and_is_released_when_the_body_raises():
    s = AgentSession()
    with pytest.raises(ValueError):
        with s.admit("move_to"):
            assert s.in_flight == 1 and s.status()["commands_in_progress"] == 1
            raise ValueError
    assert s.in_flight == 0


def test_take_over_then_finish_gives_human_with_before_snapshot():
    s = AgentSession()
    s.begin()
    assert s.request_stop(SessionState.HUMAN, "take over") is SessionState.STOPPING
    s.set_waiting_for(["the stage is moving"])
    assert s.status()["waiting_for"] == ["the stage is moving"] and s.status()["stop_cause"] == "take over"
    assert s.finish_stop({"x_mm": 1.0}, unlocked_anyway=True) is SessionState.HUMAN
    st = s.status()
    assert st["state"] == "human" and st["handover"] == {"before": {"x_mm": 1.0}, "after": None, "note": ""}
    assert st["unlocked_anyway"] is True and st["waiting_for"] == [] and st["stop_cause"] == "take over"


def test_request_stop_when_the_agent_does_not_drive():
    s = AgentSession()
    with pytest.raises(AgentSessionRefused, match="no agent session"):
        s.request_stop(SessionState.HUMAN, "take over")
    stopped_for_human(s)
    assert s.request_stop(SessionState.HUMAN, "take over") is SessionState.HUMAN


def test_end_from_agent_goes_through_stopping_to_none():
    s = AgentSession()
    s.begin()
    assert s.end() is SessionState.STOPPING and s.status()["stop_cause"] == "end"
    assert s.finish_stop({}) is SessionState.NONE and s.session_id is None
    assert s.status()["handover"] == {"before": None, "after": None, "note": ""}


def test_end_during_stopping_changes_the_target_and_end_from_human_is_direct():
    s = AgentSession()
    s.begin()
    s.request_stop(SessionState.HUMAN, "take over")
    assert s.end() is SessionState.STOPPING
    assert s.finish_stop({}) is SessionState.NONE
    stopped_for_human(s)
    assert s.end() is SessionState.NONE
    with pytest.raises(AgentSessionRefused, match="no agent session"):
        s.end()


def test_hand_back_stores_after_note_and_count():
    s = stopped_for_human(AgentSession(), {"x_mm": 1.0})
    s.hand_back({"x_mm": 2.0}, note="looked at one organism")
    st = s.status()
    assert st["state"] == "agent" and st["handover_count"] == 1 and st["stop_cause"] is None
    assert st["handover"] == {"before": {"x_mm": 1.0}, "after": {"x_mm": 2.0}, "note": "looked at one organism"}


def test_hand_back_is_refused_by_the_blocker_and_when_no_human_drives():
    s = AgentSession()
    with pytest.raises(AgentSessionRefused, match="no human is driving"):
        s.hand_back({})
    stopped_for_human(s)
    s.set_blocker(lambda: "the stage is homing")
    with pytest.raises(AgentSessionRefused, match="the stage is homing"):
        s.hand_back({})
    assert s.state is SessionState.HUMAN


def test_listener_runs_outside_the_lock_and_a_bad_listener_does_not_stop_the_next():
    s = AgentSession()
    seen = []

    def bad(old, new):
        raise RuntimeError("bad listener")

    s.add_listener(bad)
    s.add_listener(lambda old, new: seen.append((old, new, s.status()["state"])))  # status() takes the lock
    s.begin()
    assert seen == [(SessionState.NONE, SessionState.AGENT, "agent")]


def test_console_lines_are_admitted_by_state():
    s = AgentSession()
    with s.admit_console():
        with pytest.raises(AgentSessionRefused, match="the console is running a line"):
            s.begin()
    s.begin()
    with pytest.raises(AgentSessionRefused, match="^the agent is driving$"):
        with s.admit_console():
            pass
    s.request_stop(SessionState.HUMAN, "take over")
    with pytest.raises(AgentSessionRefused, match="^a stop is in progress$"):
        with s.admit_console():
            pass
    s.finish_stop({})
    with s.admit_console():
        with pytest.raises(AgentSessionRefused, match="the console is running a line"):
            s.hand_back({})


def test_a_console_line_and_begin_never_both_start():
    for _ in range(200):
        s = AgentSession()
        barrier = threading.Barrier(2)
        result = {}

        def console():
            barrier.wait()
            try:
                with s.admit_console():
                    result["console_saw"] = s.state
            except AgentSessionRefused:
                result["console_saw"] = None

        def begin():
            barrier.wait()
            try:
                s.begin()
                result["began_with_console_lines"] = s.console_in_flight
            except AgentSessionRefused:
                result["began_with_console_lines"] = None

        threads = [threading.Thread(target=console), threading.Thread(target=begin)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # a console line that was admitted never runs while the agent drives, and begin never passes over a running line
        assert result["console_saw"] in (None, SessionState.NONE)
        assert result["began_with_console_lines"] in (None, 0)


def test_snapshot_reads_the_microscope_state():
    microscope = SimpleNamespace(
        stage=SimpleNamespace(get_pos=lambda: SimpleNamespace(x_mm=1.0, y_mm=2.0, z_mm=0.5)),
        objective_store=SimpleNamespace(current_objective="20x"),
        camera=SimpleNamespace(get_exposure_time=lambda: 12.5),
        live_controller=SimpleNamespace(
            is_live=False, currentConfiguration=SimpleNamespace(name="Fluorescence 638 nm Ex")
        ),
    )
    snap = snapshot_microscope(microscope)
    assert {k: snap[k] for k in ("x_mm", "y_mm", "z_mm", "objective", "channel", "exposure_ms", "is_live")} == {
        "x_mm": 1.0, "y_mm": 2.0, "z_mm": 0.5, "objective": "20x", "channel": "Fluorescence 638 nm Ex",
        "exposure_ms": 12.5, "is_live": False,
    }  # fmt: skip
    assert snap["time"].endswith("+00:00")
    microscope.live_controller.currentConfiguration = None
    blind = snapshot_microscope(microscope, with_position=False)
    assert (blind["x_mm"], blind["y_mm"], blind["z_mm"], blind["channel"]) == (None, None, None, None)


def test_why_not_gives_the_reason_that_begin_and_hand_back_would_give():
    s = AgentSession()
    assert s.why_not("begin") is None and s.why_not("hand_back") == "no human is driving"
    s.set_blocker(lambda: "the stage is homing")
    assert s.why_not("begin") == "the stage is homing"
    s.set_blocker(None)
    with s.admit("move_to"):
        assert s.why_not("begin") == "a command is in progress"
    with s.admit_console():
        assert s.why_not("begin") == "the console is running a line"
    s.begin()
    assert s.why_not("begin") == "a session exists" and s.state is SessionState.AGENT
    s.request_stop(SessionState.HUMAN, "take over")
    s.finish_stop({})
    assert s.why_not("hand_back") is None and s.why_not("begin") == "a session exists"
    assert s.state is SessionState.HUMAN  # the question changes nothing
    with pytest.raises(ValueError):
        s.why_not("end")
