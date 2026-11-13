"""The stop sequence of an agent session: a timer on the GUI thread ends "stopping" when the agent's work ended."""

import pytest

from control.agent_session_stop import (
    WAIT_ACQUISITION,
    WAIT_COMMAND,
    WAIT_STAGE,
    AgentSessionStopSequence,
)
from control.core.agent_session import AgentSession, SessionState


class FakeController:
    """Has the method names of MultiPointController (the abort method is spelled as it is there)."""

    def __init__(self):
        self.running = False
        self.abort_requests = 0
        self.fail = False

    def acquisition_in_progress(self):
        if self.fail:
            raise RuntimeError("the controller does not answer")
        return self.running

    def request_abort_aquisition(self):
        self.abort_requests += 1


class FakeStage:
    def __init__(self):
        self.stopped = True
        self.asked = 0
        self.fail = False

    def reports_stopped(self):
        self.asked += 1
        if self.fail:
            raise RuntimeError("no answer from the stage")
        return self.stopped


@pytest.fixture
def session():
    s = AgentSession()
    s.begin()
    return s


@pytest.fixture
def mpc():
    return FakeController()


@pytest.fixture
def stage():
    return FakeStage()


def make(session, mpc, stage, snapshots=None, clock=None):
    def snapshot(with_position):
        if snapshots is not None:
            snapshots.append(with_position)
        return {"x_mm": 1.0 if with_position else None}

    kwargs = {"clock": clock} if clock else {}
    return AgentSessionStopSequence(session, mpc, stage, snapshot, **kwargs)


def test_finishes_when_nothing_is_in_progress(qtbot, session, mpc, stage):
    seq = make(session, mpc, stage)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    assert session.state is SessionState.STOPPING  # start() returns at once; the timer does the work
    qtbot.waitUntil(lambda: session.state is SessionState.HUMAN, timeout=2000)
    assert session.status()["handover"]["before"] == {"x_mm": 1.0} and not seq.is_running()


def test_an_end_finishes_with_no_session(qtbot, session, mpc, stage):
    seq = make(session, mpc, stage)
    session.end()
    seq.start()
    qtbot.waitUntil(lambda: session.state is SessionState.NONE, timeout=2000)


def test_requests_the_abort_again_at_each_check(qtbot, session, mpc, stage):
    mpc.running = True
    seq = make(session, mpc, stage)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    qtbot.waitUntil(lambda: mpc.abort_requests >= 3, timeout=2000)
    assert session.state is SessionState.STOPPING and session.status()["waiting_for"] == [WAIT_ACQUISITION]
    assert stage.asked == 0  # the stage is not asked while an acquisition runs
    mpc.running = False
    qtbot.waitUntil(lambda: session.state is SessionState.HUMAN, timeout=2000)


def test_waits_for_a_command_and_then_for_the_stage(qtbot, session, mpc, stage):
    stage.stopped = False
    command = session.admit("move_to")
    command.__enter__()  # a command that was admitted before the take over and still runs
    seq = make(session, mpc, stage)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    qtbot.wait(300)
    assert session.status()["waiting_for"] == [WAIT_COMMAND] and stage.asked == 0
    command.__exit__(None, None, None)
    qtbot.waitUntil(lambda: session.status()["waiting_for"] == [WAIT_STAGE], timeout=2000)
    assert session.state is SessionState.STOPPING
    stage.stopped = True
    qtbot.waitUntil(lambda: session.state is SessionState.HUMAN, timeout=2000)


def test_a_check_that_fails_counts_as_not_yet(qtbot, session, mpc, stage):
    stage.fail = True
    seq = make(session, mpc, stage)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    qtbot.waitUntil(lambda: session.status()["waiting_for"] == [WAIT_STAGE], timeout=2000)
    mpc.fail = True
    qtbot.waitUntil(lambda: session.status()["waiting_for"] == [WAIT_ACQUISITION], timeout=2000)
    assert session.state is SessionState.STOPPING and seq.is_running()
    stage.fail = mpc.fail = False
    qtbot.waitUntil(lambda: session.state is SessionState.HUMAN, timeout=2000)


def test_time_limit_gives_one_signal_and_does_not_unlock(qtbot, session, mpc, stage):
    stage.stopped = False
    now = [0.0]
    seq = make(session, mpc, stage, clock=lambda: now[0])
    emitted = []
    seq.signal_timed_out.connect(emitted.append)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    qtbot.wait(250)
    assert emitted == []
    now[0] = 31.0
    qtbot.waitUntil(lambda: len(emitted) == 1, timeout=2000)
    qtbot.wait(300)
    assert emitted == [[WAIT_STAGE]] and session.state is SessionState.STOPPING and seq.is_running()


def test_unlock_anyway_ends_stopping_and_reads_no_position_under_a_command(qtbot, session, mpc, stage):
    stage.stopped = False
    snapshots = []
    command = session.admit("move_to")
    command.__enter__()
    seq = make(session, mpc, stage, snapshots=snapshots)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    seq.unlock_anyway()
    command.__exit__(None, None, None)
    status = session.status()
    assert session.state is SessionState.HUMAN and status["unlocked_anyway"] is True and not seq.is_running()
    assert snapshots == [False] and status["handover"]["before"] == {"x_mm": None}


def test_start_two_times_is_one_sequence_and_stop_cancels_it(qtbot, session, mpc, stage):
    stage.stopped = False
    seq = make(session, mpc, stage)
    session.request_stop(SessionState.HUMAN, "take over")
    seq.start()
    seq.start()
    assert seq.is_running()
    seq.stop()
    stage.stopped = True
    qtbot.wait(300)
    assert not seq.is_running() and session.state is SessionState.STOPPING
