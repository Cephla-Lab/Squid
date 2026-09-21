"""PendingOutputs: the handshake that lets an acquisition wait for output writers living outside the worker
(the GUI's per-timepoint mosaic saves) before it writes timepoint_done / end to the transfer manifest."""

import concurrent.futures
import threading
import time

from control.core.pending_outputs import PendingOutputs, TimepointReply


def _done(exc=None):
    f = concurrent.futures.Future()
    f.set_exception(exc) if exc is not None else f.set_result(None)
    return f


def test_nothing_expected_is_immediately_complete():
    p = PendingOutputs()
    started = time.monotonic()
    result = p.wait(0, timeout_s=5.0)
    assert result.outputs == () and result.complete
    assert time.monotonic() - started < 0.5, "a headless run must never wait"


def test_wait_blocks_for_the_reply_and_then_for_the_writer():
    p = PendingOutputs()
    p.expect(3)
    future = concurrent.futures.Future()
    threading.Timer(0.15, lambda: p.register(3, future, "/exp/3/mosaic_view")).start()
    threading.Timer(0.35, lambda: future.set_result(None)).start()
    result = p.wait(3, timeout_s=5.0)
    assert result.outputs == ((3, "/exp/3/mosaic_view"),) and result.complete


def test_a_nothing_to_write_reply_completes_the_timepoint():
    p = PendingOutputs()
    p.expect(1)
    threading.Timer(0.1, lambda: p.nothing_to_write(1)).start()
    result = p.wait(1, timeout_s=5.0)
    assert result.outputs == () and result.complete


def test_a_missing_reply_times_out_incomplete_and_is_picked_up_by_a_later_wait():
    p = PendingOutputs()
    p.expect(0)
    assert p.wait(0, timeout_s=0.1).complete is False
    p.register(0, _done(), "/exp/0/mosaic_view")
    result = p.wait(None, timeout_s=1.0)
    assert result.outputs == ((0, "/exp/0/mosaic_view"),) and result.complete


def test_an_unfinished_writer_is_incomplete_and_stays_registered():
    p = PendingOutputs()
    p.expect(0)
    future = concurrent.futures.Future()
    p.register(0, future, "/exp/0/mosaic_view")
    first = p.wait(0, timeout_s=0.1)
    assert first.outputs == () and first.complete is False
    future.set_result(None)
    assert p.wait(None, timeout_s=1.0).outputs == ((0, "/exp/0/mosaic_view"),)
    assert p.wait(None, timeout_s=0.0).outputs == (), "outputs are handed out once"


def test_a_failed_writer_is_dropped_and_marks_the_wait_incomplete():
    p = PendingOutputs()
    p.expect(0)
    p.register(0, _done(RuntimeError("disk error")), "/exp/0/mosaic_view")
    result = p.wait(0, timeout_s=1.0)
    assert result.outputs == () and result.complete is False
    assert p.wait(None, timeout_s=0.0).complete, "a failed writer is not waited for again"


def test_wait_is_scoped_to_its_timepoint_and_none_means_everything():
    p = PendingOutputs()
    p.expect(0)
    p.expect(1)
    p.register(0, _done(), "/exp/0/mosaic_view")
    p.register(1, _done(), "/exp/1/mosaic_view")
    assert p.wait(1, timeout_s=1.0).outputs == ((1, "/exp/1/mosaic_view"),)
    assert p.wait(None, timeout_s=1.0).outputs == ((0, "/exp/0/mosaic_view"),)


def test_abort_ends_the_wait_early():
    p = PendingOutputs()
    p.expect(0)
    started = time.monotonic()
    result = p.wait(0, timeout_s=10.0, abort_fn=lambda: True)
    assert result.complete is False and time.monotonic() - started < 1.0


def test_unexpected_registrations_are_harmless():
    p = PendingOutputs()
    p.register(7, _done(), "/exp/7/mosaic_view")  # mode-off run: the GUI replies, nobody expected it
    assert p.wait(None, timeout_s=0.0).outputs == ((7, "/exp/7/mosaic_view"),)


def test_when_settled_fires_immediately_if_nothing_is_outstanding():
    p = PendingOutputs()
    p.register(0, _done(), "/exp/0/mosaic_view")
    got = []
    p.when_settled(got.append)
    assert len(got) == 1 and got[0].outputs == ((0, "/exp/0/mosaic_view"),)


def test_when_settled_waits_for_the_reply_and_the_writer_then_fires_once():
    p = PendingOutputs()
    p.expect(2)
    got = []
    p.when_settled(got.append)
    assert got == [], "a reply is still awaited"
    future = concurrent.futures.Future()
    p.register(2, future, "/exp/2/mosaic_view")
    assert got == [], "the writer is still running"
    future.set_result(None)
    assert len(got) == 1 and got[0].outputs == ((2, "/exp/2/mosaic_view"),) and got[0].complete
    p.nothing_to_write(2)
    assert len(got) == 1


def test_when_settled_fires_for_a_failed_writer_without_listing_it():
    p = PendingOutputs()
    future = concurrent.futures.Future()
    p.register(0, future, "/exp/0/mosaic_view")
    got = []
    p.when_settled(got.append)
    future.set_exception(OSError("disk full"))
    assert len(got) == 1 and got[0].outputs == () and got[0].complete is False


# --- replies are bound to the run that asked ------------------------------------------------------


def test_a_late_reply_reaches_the_run_that_asked_not_the_run_that_is_current():
    """Run A ends still waiting for its timepoint-0 answer; run B starts and also expects timepoint 0.
    A's answer arriving late must settle A and leave B waiting for its own."""
    old_run, new_run = PendingOutputs(), PendingOutputs()
    old_reply = old_run.expect(0)
    new_reply = new_run.expect(0)
    old_settled, new_settled = [], []
    old_run.when_settled(old_settled.append)
    new_run.when_settled(new_settled.append)

    old_reply.nothing_to_write()
    assert len(old_settled) == 1 and new_settled == [], "the new run still awaits its own answer"
    assert new_run.wait(0, timeout_s=0.05).complete is False

    future = concurrent.futures.Future()
    new_reply.register(future, "/new/0/mosaic_view")
    future.set_result(None)
    assert len(new_settled) == 1 and new_settled[0].outputs == ((0, "/new/0/mosaic_view"),)
    assert len(old_settled) == 1


def test_a_reply_nobody_expected_is_harmless_and_carries_its_timepoint():
    registry = PendingOutputs()
    reply = TimepointReply(registry, 4)  # mode-off or headless-style run: nothing was expected
    assert reply.time_point == 4
    reply.nothing_to_write()
    reply.register(_done(), "/exp/4/mosaic_view")
    assert registry.wait(None, timeout_s=0.0).outputs == ((4, "/exp/4/mosaic_view"),)
