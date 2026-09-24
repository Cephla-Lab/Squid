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
    from control.dcamapi4 import DCAM_IDPROP, DCAMERR, DCAMPROP


class FakeRing:
    """DCAM's ring as the ORCA-Fusion BT showed it on the bench: the frame with TRANSFER index n (counted from
    0 at cap_start, in the order frames reached host memory) lives in slot n % depth until overwritten,
    cap_transferinfo() counts transfers in 32 bits, and each frame's framestamp is the low 16 bits of the
    CAMERA's capture count - which runs ahead of the transfer count by every frame lost between the camera
    and host memory (bench 2026-09-21 21:02: a ~460 ms USB stall, 7 captured, 1 transferred)."""

    def __init__(self, trigger_source):
        self.trigger_source = trigger_source
        self.depth = None
        self.stamps = []  # stamps[n] = framestamp of the frame with transfer index n
        self.captured = 0  # frames the camera has captured: transfers + frames lost at the link
        self.transferinfo_calls = 0
        self.fail_next_transferinfo = False
        self.slot_reads = []
        self.corrupt_slot = None  # a slot that holds a frame from the PAST: what a wrong ring layout looks like

    @property
    def produced(self):
        return len(self.stamps)

    def produce(self, count):
        for _ in range(count):
            self.stamps.append(self.captured & 0xFFFF)
            self.captured += 1

    def lose_at_link(self, count):
        """The camera captures `count` frames that never reach host memory."""
        self.captured += count

    # --- the DCAM calls the driver makes ---
    def prop_getvalue(self, idprop):
        assert int(idprop) == int(DCAM_IDPROP.TRIGGERSOURCE)
        return int(self.trigger_source)

    def buf_alloc(self, count):
        self.depth = count
        return True

    def lasterr(self):
        return DCAMERR.INVALIDHANDLE  # a real DCAMERR, as the SDK returns

    def cap_start(self):
        self.stamps = []
        self.captured = 0
        return True

    def cap_transferinfo(self):
        self.transferinfo_calls += 1
        if self.fail_next_transferinfo:
            self.fail_next_transferinfo = False
            return False
        newest = (self.produced - 1) % self.depth if self.produced else -1
        return types.SimpleNamespace(nFrameCount=self.produced, nNewestFrameIndex=newest)

    def buf_getframe(self, index):
        self.slot_reads.append(index)
        newest = self.produced - 1
        # the newest frame whose transfer index maps onto this slot
        number = newest if index == -1 else newest - ((newest - index) % self.depth)
        stamp = self.stamps[number]
        if index == self.corrupt_slot:
            stamp = (stamp - 1000) & 0xFFFF
        return types.SimpleNamespace(framestamp=stamp), np.full((4, 4), number % 60000, dtype=np.uint16)


def make_camera(ring):
    cam = object.__new__(camera_hamamatsu.HamamatsuCamera)
    cam._camera = ring
    cam._log = squid.logging.get_logger("test_camera_hamamatsu_ring_read")
    cam._capture_lock = threading.Lock()
    cam._frame_lock = threading.Lock()
    cam._current_frame = None
    cam._frame_id_base = 1
    cam._frames_read = 0
    cam._last_frame_number = -1
    cam._link_lost = 0
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
    assert "Ring slot 1 holds framestamp" in caplog.text
    ring.corrupt_slot = None
    ring.produce(1)
    assert ids(cam._read_frames()) == [4]  # and it carries on from there


def after_many_frames(trigger_source, delivered):
    """A capture that has already delivered `delivered` frames - the stream stays up across acquisitions."""
    cam, ring = started(trigger_source)
    ring.stamps = [n & 0xFFFF for n in range(delivered)]
    ring.captured = delivered
    cam._frames_read = delivered
    cam._last_frame_number = delivered - 1
    return cam, ring


def test_every_frame_is_still_delivered_when_the_cameras_16_bit_stamp_wraps(caplog):
    """Bench 2026-09-21: 72 acquisitions and 64,800 frames ran clean, then at frame 65,536 framestamp went
    back to 0 while cap_transferinfo() counted on. The slot check compared the two directly, reported
    "Ring slot 0 holds framestamp 0, expected frame 65536" for every frame, and ids started over at 1."""
    cam, ring = after_many_frames(EXTERNAL, 65530)
    ring.produce(10)  # 65530 .. 65539: across the wrap
    with caplog.at_level(logging.ERROR):
        frames = cam._read_frames()
    assert ids(frames) == list(range(65531, 65541))  # ids keep counting
    assert "Ring slot" not in caplog.text
    ring.produce(3)
    assert ids(cam._read_frames()) == [65541, 65542, 65543]


def test_live_view_ids_also_keep_counting_across_the_wrap():
    cam, ring = after_many_frames(INTERNAL, 65535)
    ring.produce(1)
    assert ids(cam._read_frames()) == [65536]
    ring.produce(1)  # stamp 0
    assert ids(cam._read_frames()) == [65537]


def test_frames_lost_between_the_camera_and_the_host_leave_a_hole_and_are_reported(caplog):
    """Bench 2026-09-21 21:02, after 69,300 clean frames: a ~460 ms USB stall (the controller's acks
    stopped at the same moment). The camera captured 7 frames, DCAM transferred 1: six never reached
    host memory. The ring layout was right (slot == frame % 32 in every line), but the camera's stamp
    now ran 6 ahead of the transfer count for good."""
    cam, ring = started(EXTERNAL)
    ring.produce(3)
    assert ids(cam._read_frames()) == [1, 2, 3]
    ring.lose_at_link(6)
    ring.produce(2)
    with caplog.at_level(logging.ERROR):
        frames = cam._read_frames()
    assert ids(frames) == [10, 11]  # ids follow the camera: the six lost frames are the hole 4..9
    assert "6 frame" in caplog.text and "never reached" in caplog.text
    assert "Ring slot" not in caplog.text  # the layout is not in question


def test_after_frames_are_lost_at_the_link_every_later_frame_is_still_delivered(caplog):
    """The bench saw the opposite: after the loss the read fell back to the newest frame, the fall-back
    left the reader 6 frames 'ahead' of DCAM, and exactly one frame in seven arrived for the rest of the
    capture - 'the camera delivered only 3 of 20 frames', three attempts, acquisition stopped."""
    cam, ring = started(EXTERNAL)
    ring.produce(3)
    cam._read_frames()
    ring.lose_at_link(6)
    ring.produce(1)
    with caplog.at_level(logging.ERROR):
        cam._read_frames()
    ring.produce(7)
    frames = cam._read_frames()
    assert ids(frames) == list(range(11, 18))  # all seven, not one
    ring.produce(7)
    assert ids(cam._read_frames()) == list(range(18, 25))


def test_the_newest_frame_fall_back_leaves_the_reader_in_step_with_dcam(caplog):
    """_frames_read is DCAM's transfer count, never a stamp-derived number: with frames lost at the link
    the two differ, and a reader set from the stamp saw 'nothing new' for six wake-ups."""
    cam, ring = started(EXTERNAL)
    ring.produce(3)
    ring.lose_at_link(6)
    ring.produce(2)
    ring.fail_next_transferinfo = True  # forces the newest-frame fall-back
    with caplog.at_level(logging.ERROR):
        frames = cam._read_frames()
    assert ids(frames) == [11]
    ring.produce(2)
    assert ids(cam._read_frames()) == [12, 13]  # was: [] until the count had caught up with the stamp


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
