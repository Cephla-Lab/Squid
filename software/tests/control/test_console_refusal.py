"""The terminal console obeys the agent session: no line runs while the agent drives or a stop is in progress."""

import atexit
import sys

import pytest

pytest.importorskip("qtconsole")  # control.console imports it

import control.console as console_module
from control.console import ConsoleThread, EnhancedInteractiveConsole
from control.core.agent_session import (
    REFUSAL_STOPPING,
    AgentSession,
    AgentSessionRefused,
    SessionState,
)


@pytest.fixture(autouse=True)
def no_history_file(monkeypatch):
    """The console reads ~/.pyqt_console_history and writes it at exit. A test must not touch that file."""
    monkeypatch.setattr(console_module.readline, "read_history_file", lambda path: None)
    monkeypatch.setattr(atexit, "register", lambda *args, **kwargs: None)


def test_a_line_is_not_run_while_the_agent_drives(capsys):
    session = AgentSession()
    session.begin()
    namespace = {}
    console = EnhancedInteractiveConsole(locals=namespace, session=session)
    assert console.push("x = 1") is False
    assert "x" not in namespace
    assert "Refused: the agent is driving" in capsys.readouterr().err


def test_a_line_is_not_run_while_a_stop_is_in_progress(capsys):
    session = AgentSession()
    session.begin()
    session.request_stop(SessionState.HUMAN, "take over")
    namespace = {}
    console = EnhancedInteractiveConsole(locals=namespace, session=session)
    assert console.push("x = 1") is False
    assert "x" not in namespace
    assert f"Refused: {REFUSAL_STOPPING}" in capsys.readouterr().err


def test_a_line_runs_when_no_agent_drives():
    session = AgentSession()
    namespace = {}
    console = EnhancedInteractiveConsole(locals=namespace, session=session)
    console.push("x = 1")
    session.begin()
    session.request_stop(SessionState.HUMAN, "take over")
    session.finish_stop({})
    console.push("y = 2")  # a human drives
    assert (namespace["x"], namespace["y"]) == (1, 2)


def test_a_console_with_no_session_works_as_before():
    namespace = {}
    console = EnhancedInteractiveConsole(locals=namespace)
    assert console.push("for i in range(3):") is True  # the statement is not complete
    console.push("    total = i")
    assert console.push("") is False
    assert namespace["total"] == 2


def test_a_refused_line_clears_the_lines_that_were_typed_before():
    session = AgentSession()
    namespace = {}
    console = EnhancedInteractiveConsole(locals=namespace, session=session)
    assert console.push("for i in range(3):") is True
    session.begin()
    assert console.push("    total = i") is False
    session.request_stop(SessionState.HUMAN, "take over")
    session.finish_stop({})
    assert console.push("y = 2") is False  # a new statement, not a line of the loop
    assert namespace["y"] == 2 and "total" not in namespace


def test_the_session_counts_a_line_while_it_runs_and_refuses_the_agent():
    session = AgentSession()

    def try_begin():
        try:
            session.begin()
        except AgentSessionRefused as refusal:
            return str(refusal)

    namespace = {"session": session, "try_begin": try_begin}
    console = EnhancedInteractiveConsole(locals=namespace, session=session)
    console.push("lines = session.console_in_flight")
    console.push("answer = try_begin()")
    assert namespace["lines"] == 1 and session.console_in_flight == 0
    assert namespace["answer"] == "the console is running a line" and session.state is SessionState.NONE


def test_a_line_that_raises_is_not_counted_any_more(capsys, monkeypatch):
    session = AgentSession()
    console = EnhancedInteractiveConsole(locals={}, session=session)
    with monkeypatch.context() as patch:
        # With the usual hook the console writes the traceback itself. The hook of pytest-qt would fail the test.
        patch.setattr(sys, "excepthook", sys.__excepthook__)
        console.push("1 / 0")
    assert "ZeroDivisionError" in capsys.readouterr().err and session.console_in_flight == 0
    session.begin()  # not refused


def test_the_console_thread_gives_the_session_to_its_console(capsys):
    session = AgentSession()
    session.begin()
    thread = ConsoleThread({"value": 5}, session=session)
    assert thread.console.push("x = value") is False
    assert "Refused: the agent is driving" in capsys.readouterr().err
    assert ConsoleThread({"value": 5}).console.push("x = value") is False  # no session: the line runs
