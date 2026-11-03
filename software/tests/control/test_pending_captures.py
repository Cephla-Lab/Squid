"""Frame -> capture-metadata pairing for the acquisition worker.

The camera delivers frames on its own thread and carries no acquisition metadata, so the worker
pairs each arriving frame with the capture it was expecting, in order. For a normal acquisition
that is one capture at a time; for a hardware-sequenced burst it is N captures queued up front,
with N frames arriving back-to-back and no host action in between. A pairing mistake does not
crash anything — it saves the right pixels under the wrong channel or z — so gaps must be
detected, not tolerated, and a frame of another run of the camera stream than the batch's is
refused at the moment of pairing.
"""

import threading

import pytest

from control.core.pending_captures import PendingCaptures


def _deliver(frame, capture):
    pass


def _expect(pending, captures, *, stream_generation=1, deliver=_deliver):
    pending.expect(captures, stream_generation=stream_generation, deliver=deliver)


def _take(pending, frame_id, *, stream_generation=1):
    pairing = pending.take(frame_id, stream_generation=stream_generation)
    return None if pairing is None else pairing[0]


def test_single_capture_pairs_with_the_next_frame():
    pending = PendingCaptures()
    assert pending.empty
    _expect(pending, ["capture-a"])
    assert not pending.empty
    assert _take(pending, frame_id=41) == "capture-a"
    assert pending.empty
    assert not pending.gap_detected


def test_a_frame_nobody_expected_pairs_with_nothing():
    pending = PendingCaptures()
    assert _take(pending, frame_id=7) is None
    assert pending.empty


def test_a_burst_pairs_in_the_order_it_was_queued():
    pending = PendingCaptures()
    burst = [f"z{z}-ch{ch}" for z in range(3) for ch in range(2)]
    _expect(pending, burst)
    assert len(pending) == 6
    received = [_take(pending, frame_id=100 + i) for i in range(6)]
    assert received == burst
    assert pending.empty
    assert not pending.gap_detected


def test_a_dropped_frame_inside_a_burst_is_detected():
    pending = PendingCaptures()
    _expect(pending, ["a", "b", "c"])
    _take(pending, frame_id=10)
    _take(pending, frame_id=12)  # 11 never arrived: "b" just got frame 12's pixels
    assert pending.gap_detected
    assert pending.first_gap == (11, 12)  # (expected, got)


def test_frame_ids_need_not_be_continuous_across_separate_captures():
    # Between two ordinary captures the camera may have produced live-view frames.
    pending = PendingCaptures()
    _expect(pending, ["a"])
    _take(pending, frame_id=10)
    _expect(pending, ["b"])
    _take(pending, frame_id=25)
    assert not pending.gap_detected


def test_a_new_burst_starts_with_a_clean_gap_record():
    pending = PendingCaptures()
    _expect(pending, ["a", "b"])
    _take(pending, frame_id=1)
    _take(pending, frame_id=5)
    assert pending.gap_detected
    _expect(pending, ["c", "d"])
    assert not pending.gap_detected
    _take(pending, frame_id=6)
    _take(pending, frame_id=7)
    assert not pending.gap_detected


def test_expecting_more_while_captures_are_outstanding_is_a_programming_error():
    # One outstanding batch at a time: queueing a second one would make "which capture does
    # this frame belong to" depend on timing.
    pending = PendingCaptures()
    _expect(pending, ["a"])
    with pytest.raises(RuntimeError):
        _expect(pending, ["b"])


def test_drop_remaining_returns_what_never_arrived():
    # After a cancel or a failed run the frames that were never fired will never come.
    pending = PendingCaptures()
    _expect(pending, ["a", "b", "c"])
    _take(pending, frame_id=1)
    assert pending.drop_remaining() == ["b", "c"]
    assert pending.empty


def test_an_empty_batch_is_rejected():
    with pytest.raises(ValueError):
        _expect(PendingCaptures(), [])


def test_a_frame_of_another_stream_run_than_the_batch_is_refused():
    # The worker gave a burst up, restarted the camera stream (run 2) and queued the retry's captures.
    # A frame of run 1 the driver had already read is delivered now: it pairs with nothing, counts as
    # no gap, and the retry's first capture is still there for the retry's own first frame.
    pending = PendingCaptures()
    _expect(pending, ["retry-z0", "retry-z1"], stream_generation=2)
    assert _take(pending, frame_id=3, stream_generation=1) is None
    assert len(pending) == 2 and not pending.gap_detected
    assert _take(pending, frame_id=4, stream_generation=2) == "retry-z0"
    assert _take(pending, frame_id=5, stream_generation=2) == "retry-z1"
    assert not pending.gap_detected


def test_a_pairing_comes_with_the_sink_of_its_own_batch():
    # Where a paired frame goes is decided with the pairing, under the same lock: a callback that paired
    # a frame of batch A and delivers it only after batch B was queued still delivers to A's sink.
    pending = PendingCaptures()
    to_a, to_b = [], []
    pending.expect(["a"], stream_generation=1, deliver=lambda frame, capture: to_a.append((frame, capture)))
    capture, deliver = pending.take(1, stream_generation=1)
    pending.expect(["b"], stream_generation=1, deliver=lambda frame, capture: to_b.append((frame, capture)))
    deliver("frame 1", capture)
    assert to_a == [("frame 1", "a")] and to_b == []


def test_take_is_safe_against_a_concurrent_camera_thread():
    pending = PendingCaptures()
    n = 2000
    _expect(pending, list(range(n)))
    received = []

    def camera_thread():
        for i in range(n):
            received.append(_take(pending, frame_id=i))

    t = threading.Thread(target=camera_thread)
    t.start()
    t.join(timeout=10)
    assert received == list(range(n))
    assert pending.empty and not pending.gap_detected
