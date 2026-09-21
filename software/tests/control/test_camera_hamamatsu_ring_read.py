"""In TRIGGERED acquisition modes the Hamamatsu driver delivers EVERY frame, in order.

Every triggered frame was asked for. The read thread used to take only the NEWEST frame out of DCAM's
ring, so a thread that ran late by more than one frame period dropped frames that were still sitting
in the ring. Bench 2026-09-21 (ORCA-Fusion BT, hardware-sequenced bursts at 41 ms per frame): the read
thread was held up ~112 ms while the previous burst was being handed to the save jobs, ids
6535-6536 and 6953 never reached the host, and two bursts had to be retried.

CONTINUOUS (live) mode keeps newest-only: there, dropping frames to stay current is the right policy.
Only the SDK boundary is faked: a ring with DCAM's semantics.
"""

import contextlib
import ctypes
import logging
import threading
import types
from unittest import mock

import numpy as np
import pytest

import squid.logging
from squid.config import CameraPixelFormat

with contextlib.ExitStack() as _stack:
    _stack.enter_context(mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock()))
    if hasattr(ctypes, "windll"):
        _stack.enter_context(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
    import control.camera_hamamatsu as camera_hamamatsu
    from control.dcamapi4 import DCAM_IDPROP, DCAMPROP


class FakeRing:
    """DCAM's ring: frame n (counted from 0 at cap_start) lives in slot n % depth until overwritten."""

    def __init__(self, trigger_source):
        self.trigger_source = trigger_source
        self.depth = None
        self.produced = 0
        self.transferinfo_calls = 0
        self.slot_reads = []
        self.corrupt_slot = None  # a slot that reports the wrong stamp, to model a wrong ring assumption

    def produce(self, count):
        self.produced += count

    # --- the DCAM calls the driver makes ---
    def prop_getvalue(self, idprop):
        assert int(idprop) == int(DCAM_IDPROP.TRIGGERSOURCE)
        return int(self.trigger_source)

    def buf_alloc(self, count):
        self.depth = count
        return True

    def cap_start(self):
        self.produced = 0
        return True

    def cap_transferinfo(self):
        self.transferinfo_calls += 1
        newest = (self.produced - 1) % self.depth if self.produced else -1
        return types.SimpleNamespace(nFrameCount=self.produced, nNewestFrameIndex=newest)

    def buf_getframe(self, index):
        self.slot_reads.append(index)
        if index == -1:
            stamp = self.produced - 1
        else:
            # the newest frame whose number maps onto this slot
            stamp = max(n for n in range(self.produced) if n % self.depth == index)
            if index == self.corrupt_slot:
                stamp += 1000
        return types.SimpleNamespace(framestamp=stamp), np.full((4, 4), stamp % 60000, dtype=np.uint16)


def make_camera(ring):
    cam = object.__new__(camera_hamamatsu.HamamatsuCamera)
    cam._camera = ring
    cam._log = squid.logging.get_logger("test_camera_hamamatsu_ring_read")
    cam._capture_lock = threading.Lock()
    cam._frame_lock = threading.Lock()
    cam._current_frame = None
    cam._frame_id_base = 1
    cam._frames_read = 0
    cam._ring_frames = 5
    cam._read_every_frame = False
    cam._trigger_sent = threading.Event()
    cam._is_streaming = threading.Event()
    cam._ensure_read_thread_running = lambda: None  # no thread: the test drives the reads itself
    cam._process_raw_frame = lambda raw: raw
    cam.get_frame_format = lambda: None
    cam.get_pixel_format = lambda: CameraPixelFormat.MONO16
    return cam


def started(trigger_source):
    ring = FakeRing(trigger_source)
    cam = make_camera(ring)
    assert cam.start_streaming()
    return cam, ring


def ids(frames):
    return [frame.frame_id for frame in frames]


EXTERNAL = DCAMPROP.TRIGGERSOURCE.EXTERNAL
SOFTWARE = DCAMPROP.TRIGGERSOURCE.SOFTWARE
INTERNAL = DCAMPROP.TRIGGERSOURCE.INTERNAL


@pytest.mark.parametrize("trigger_source", [EXTERNAL, SOFTWARE])
def test_frames_that_landed_while_the_thread_was_late_are_all_delivered_in_order(trigger_source):
    cam, ring = started(trigger_source)
    ring.produce(3)  # the thread wakes once, three frames behind
    frames = cam._read_frames()
    assert ids(frames) == [1, 2, 3]
    assert [int(frame.frame[0, 0]) for frame in frames] == [0, 1, 2]  # each id carries its own pixels
    assert cam._current_frame is frames[-1]


def test_a_frame_is_never_delivered_twice():
    cam, ring = started(EXTERNAL)
    ring.produce(2)
    assert ids(cam._read_frames()) == [1, 2]
    ring.produce(1)
    assert ids(cam._read_frames()) == [3]
    assert cam._read_frames() == []  # a wake-up with nothing new delivers nothing


def test_reading_follows_the_ring_around_its_end():
    cam, ring = started(EXTERNAL)
    depth = ring.depth
    ring.produce(depth - 1)
    assert ids(cam._read_frames()) == list(range(1, depth))
    ring.produce(3)  # these wrap: slots depth-1, 0, 1
    assert ids(cam._read_frames()) == [depth, depth + 1, depth + 2]
    assert ring.slot_reads[-3:] == [depth - 1, 0, 1]


def test_frames_overwritten_before_they_were_read_leave_a_hole_and_an_error(caplog):
    cam, ring = started(EXTERNAL)
    depth = ring.depth
    ring.produce(1)
    assert ids(cam._read_frames()) == [1]
    ring.produce(depth + 2)  # two more than the ring holds: frames 2 and 3 are gone for good
    with caplog.at_level(logging.ERROR):
        frames = cam._read_frames()
    assert ids(frames) == list(range(4, depth + 4))  # the ring's worth that survived, in order
    assert "2 frame" in caplog.text and "overwritten" in caplog.text


def test_a_slot_that_does_not_hold_the_expected_frame_is_reported_and_skipped_never_mislabelled(caplog):
    """The slot arithmetic is a model of DCAM's ring that could not be checked against hardware when
    it was written. If a slot's stamp is not the frame number expected: say so, keep what WAS verified,
    and finish this wake-up with the newest frame. ids stay truthful - a consumer that needs every
    frame sees the hole instead of a frame filed under the wrong number."""
    cam, ring = started(EXTERNAL)
    ring.produce(3)
    ring.corrupt_slot = 1
    with caplog.at_level(logging.ERROR):
        frames = cam._read_frames()
    assert ids(frames) == [1, 3]  # frame 1 was verified; slot 1 was not what it should be; then the newest
    assert [int(frame.frame[0, 0]) for frame in frames] == [0, 2]  # and each still carries its own pixels
    assert "expected frame" in caplog.text
    ring.corrupt_slot = None
    ring.produce(1)
    assert ids(cam._read_frames()) == [4]  # and it carries on from there


def test_live_view_still_takes_only_the_newest_frame():
    cam, ring = started(INTERNAL)
    ring.produce(3)
    assert ids(cam._read_frames()) == [3]  # frames 1 and 2 are dropped on purpose: stay current
    assert ring.transferinfo_calls == 0
    assert ring.slot_reads == [-1]


def test_the_ring_is_deep_only_where_every_frame_matters():
    _, triggered = started(EXTERNAL)
    _, live = started(INTERNAL)
    assert live.depth == 5  # what it has always been
    assert triggered.depth >= 32  # > 1 s of slack at 41 ms per frame


def test_a_capture_restart_starts_counting_frames_again():
    cam, ring = started(EXTERNAL)
    ring.produce(2)
    assert ids(cam._read_frames()) == [1, 2]
    cam._is_streaming.clear()  # what stop_streaming() leaves behind
    assert cam.start_streaming()  # DCAM's count and stamps start over; ids must not
    ring.produce(2)
    assert ids(cam._read_frames()) == [3, 4]
