"""PendingOutputs: the handshake that lets an acquisition wait for output writers living outside the worker
(the GUI's per-timepoint mosaic saves) before it writes timepoint_done / end to the transfer manifest."""

import concurrent.futures
import threading
import time

from control.core.pending_outputs import PendingOutputs


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


def test_reset_forgets_everything_and_unexpected_registrations_are_harmless():
    p = PendingOutputs()
    p.register(7, _done(), "/exp/7/mosaic_view")  # mode-off run: the GUI replies, nobody expected it
    p.expect(8)
    p.reset()
    result = p.wait(None, timeout_s=0.0)
    assert result.outputs == () and result.complete
