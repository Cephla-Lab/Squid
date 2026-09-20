"""Frame -> capture-metadata pairing for the acquisition worker.

The camera delivers frames on its own thread and carries no acquisition metadata, so the worker
pairs each arriving frame with the capture it was expecting, in order. For a normal acquisition
that is one capture at a time; for a hardware-sequenced burst it is N captures queued up front,
with N frames arriving back-to-back and no host action in between. A pairing mistake does not
crash anything — it saves the right pixels under the wrong channel or z — so gaps must be
detected, not tolerated.
"""

import threading

import pytest

from control.core.pending_captures import PendingCaptures


def test_single_capture_pairs_with_the_next_frame():
    pending = PendingCaptures()
    assert pending.empty
    pending.expect(["capture-a"])
    assert not pending.empty
    assert pending.take(frame_id=41) == "capture-a"
    assert pending.empty
    assert not pending.gap_detected


def test_a_frame_nobody_expected_pairs_with_nothing():
    pending = PendingCaptures()
    assert pending.take(frame_id=7) is None
    assert pending.empty


def test_a_burst_pairs_in_the_order_it_was_queued():
    pending = PendingCaptures()
    burst = [f"z{z}-ch{ch}" for z in range(3) for ch in range(2)]
    pending.expect(burst)
    assert len(pending) == 6
    received = [pending.take(frame_id=100 + i) for i in range(6)]
    assert received == burst
    assert pending.empty
    assert not pending.gap_detected


def test_a_dropped_frame_inside_a_burst_is_detected():
    pending = PendingCaptures()
    pending.expect(["a", "b", "c"])
    pending.take(frame_id=10)
    pending.take(frame_id=12)  # 11 never arrived: "b" just got frame 12's pixels
    assert pending.gap_detected
    assert pending.first_gap == (11, 12)  # (expected, got)


def test_frame_ids_need_not_be_continuous_across_separate_captures():
    # Between two ordinary captures the camera may have produced live-view frames.
    pending = PendingCaptures()
    pending.expect(["a"])
    pending.take(frame_id=10)
    pending.expect(["b"])
    pending.take(frame_id=25)
    assert not pending.gap_detected


def test_a_new_burst_starts_with_a_clean_gap_record():
    pending = PendingCaptures()
    pending.expect(["a", "b"])
    pending.take(frame_id=1)
    pending.take(frame_id=5)
    assert pending.gap_detected
    pending.expect(["c", "d"])
    assert not pending.gap_detected
    pending.take(frame_id=6)
    pending.take(frame_id=7)
    assert not pending.gap_detected


def test_expecting_more_while_captures_are_outstanding_is_a_programming_error():
    # One outstanding batch at a time: queueing a second one would make "which capture does
    # this frame belong to" depend on timing.
    pending = PendingCaptures()
    pending.expect(["a"])
    with pytest.raises(RuntimeError):
        pending.expect(["b"])


def test_drop_remaining_returns_what_never_arrived():
    # After a cancel or a failed run the frames that were never fired will never come.
    pending = PendingCaptures()
    pending.expect(["a", "b", "c"])
    pending.take(frame_id=1)
    assert pending.drop_remaining() == ["b", "c"]
    assert pending.empty


def test_an_empty_batch_is_rejected():
    with pytest.raises(ValueError):
        PendingCaptures().expect([])


def test_take_is_safe_against_a_concurrent_camera_thread():
    pending = PendingCaptures()
    n = 2000
    pending.expect(list(range(n)))
    received = []

    def camera_thread():
        for i in range(n):
            received.append(pending.take(frame_id=i))

    t = threading.Thread(target=camera_thread)
    t.start()
    t.join(timeout=10)
    assert received == list(range(n))
    assert pending.empty and not pending.gap_detected
