"""BurstDispatcher: validated bursts reach the save pipeline on their own thread - in order, at most
one waiting - so the worker can move the stage and run the next burst meanwhile."""

import threading
import time

import pytest

from control.core.burst_dispatcher import BurstDispatcher


class Recorder:
    def __init__(self):
        self.dispatched = []
        self.errors = []
        self.gate = threading.Event()
        self.gate.set()  # open: dispatch returns at once
        self.started = threading.Event()  # set whenever a dispatch call begins

    def dispatch(self, burst):
        self.started.set()
        assert self.gate.wait(5), "test gate never opened"
        if burst == "poison":
            raise RuntimeError("the save pipeline is gone")
        self.dispatched.append(burst)


@pytest.fixture
def harness():
    recorder = Recorder()
    dispatcher = BurstDispatcher(recorder.dispatch, on_error=recorder.errors.append)
    dispatcher.start()
    yield recorder, dispatcher
    recorder.gate.set()
    dispatcher.stop()


def test_bursts_are_dispatched_in_the_order_they_were_submitted(harness):
    recorder, dispatcher = harness
    for burst in ("fov0", "fov1", "fov2", "fov3"):
        dispatcher.submit(burst)
    assert dispatcher.drain(timeout_s=5)
    assert recorder.dispatched == ["fov0", "fov1", "fov2", "fov3"]


def test_submit_returns_while_the_previous_burst_is_still_being_dispatched(harness):
    recorder, dispatcher = harness
    recorder.gate.clear()  # dispatch blocks: the disk is "slow"
    dispatcher.submit("fov0")
    assert recorder.started.wait(5)
    started = time.time()
    dispatcher.submit("fov1")  # one burst may wait behind the one in flight
    assert time.time() - started < 0.5  # the worker is free to move on


def test_at_most_one_burst_waits_so_the_worker_blocks_on_the_third(harness):
    recorder, dispatcher = harness
    recorder.gate.clear()
    dispatcher.submit("fov0")
    assert recorder.started.wait(5)
    dispatcher.submit("fov1")
    third_submitted = threading.Event()

    def submit_third():
        dispatcher.submit("fov2")
        third_submitted.set()

    threading.Thread(target=submit_third, daemon=True).start()
    assert not third_submitted.wait(0.3)  # two undispatched bursts is the limit
    recorder.gate.set()
    assert third_submitted.wait(5)
    assert dispatcher.drain(timeout_s=5)
    assert recorder.dispatched == ["fov0", "fov1", "fov2"]


def test_drain_waits_for_everything_submitted_so_far(harness):
    recorder, dispatcher = harness
    recorder.gate.clear()
    dispatcher.submit("fov0")
    assert not dispatcher.drain(timeout_s=0.2)  # still in flight
    recorder.gate.set()
    assert dispatcher.drain(timeout_s=5)
    assert recorder.dispatched == ["fov0"]


def test_a_failing_dispatch_reports_once_and_never_blocks_the_worker(harness):
    recorder, dispatcher = harness
    dispatcher.submit("poison")
    assert dispatcher.drain(timeout_s=5)
    assert len(recorder.errors) == 1 and "save pipeline is gone" in str(recorder.errors[0])
    # The acquisition is aborting; what the worker still hands over must not deadlock it.
    started = time.time()
    for burst in ("fov1", "fov2", "fov3"):
        dispatcher.submit(burst)
    assert dispatcher.drain(timeout_s=5)
    assert time.time() - started < 2
    assert recorder.dispatched == []  # nothing is saved through a pipeline that failed
    assert len(recorder.errors) == 1


def test_stop_finishes_what_was_submitted_then_ends_the_thread():
    recorder = Recorder()
    dispatcher = BurstDispatcher(recorder.dispatch, on_error=recorder.errors.append)
    dispatcher.start()
    dispatcher.submit("fov0")
    dispatcher.stop()
    assert recorder.dispatched == ["fov0"]
    assert not dispatcher.is_alive()
