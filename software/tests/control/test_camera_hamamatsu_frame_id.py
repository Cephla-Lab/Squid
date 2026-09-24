"""A Hamamatsu frame's id comes from the CAMERA's frame stamp, not from a host-side counter.

The read loop takes the NEWEST frame out of DCAM's ring buffer. When two frames land between two
reads, the older one is never seen; when a frame lands between the wake-up and the read, the newer
one is read twice. A host-side counter numbers both cases 1, 2, 3... as if nothing had happened.
A consumer that needs every frame, in order - a hardware-sequenced burst - can only tell from ids
that follow the camera. Only the SDK boundary is faked here.
"""

import contextlib
import ctypes
import threading
import types
from unittest import mock

import numpy as np

import squid.logging
from squid.config import CameraPixelFormat

# control/dcamapi4.py loads the vendor library (libdcamapi.so / dcamapi.dll) at import time. Only
# that load is patched, and only while importing, so the driver module is the real one.
with contextlib.ExitStack() as _stack:
    _stack.enter_context(mock.patch.object(ctypes.cdll, "LoadLibrary", return_value=mock.MagicMock()))
    if hasattr(ctypes, "windll"):
        _stack.enter_context(mock.patch.object(ctypes.windll, "LoadLibrary", return_value=mock.MagicMock()))
    import control.camera_hamamatsu as camera_hamamatsu


class FakeDcam:
    """Serves buf_getframe(-1) from a scripted list of frame stamps (DCAM counts them from 0 at cap_start)."""

    def __init__(self, stamps):
        self._stamps = list(stamps)
        self.cap_starts = 0

    def script(self, stamps):
        self._stamps = list(stamps)

    def buf_getframe(self, index):
        assert index == -1  # "the newest frame", exactly what buf_getlastframedata() asks for
        stamp = self._stamps.pop(0)
        return types.SimpleNamespace(framestamp=stamp), np.full((4, 4), stamp, dtype=np.uint16)

    def buf_alloc(self, count):
        return True

    def cap_start(self):
        self.cap_starts += 1
        return True

    def cap_stop(self):
        return True

    def buf_release(self):
        return True


def make_camera(dcam):
    cam = object.__new__(camera_hamamatsu.HamamatsuCamera)
    cam._camera = dcam
    cam._log = squid.logging.get_logger("test_camera_hamamatsu_frame_id")
    cam._capture_lock = threading.Lock()
    cam._frame_lock = threading.Lock()
    cam._current_frame = None
    cam._frame_id_base = 1
    cam._last_frame_number = -1
    cam._trigger_sent = threading.Event()
    cam._is_streaming = threading.Event()
    cam._ensure_read_thread_running = lambda: None  # no thread: the test drives the reads itself
    cam._process_raw_frame = lambda raw: raw
    cam.get_frame_format = lambda: None
    cam.get_pixel_format = lambda: CameraPixelFormat.MONO16
    return cam


def read_ids(cam, count):
    return [cam._read_newest_frame().frame_id for _ in range(count)]


def test_frame_ids_follow_the_cameras_frame_stamp():
    cam = make_camera(FakeDcam([0, 1, 2]))
    assert read_ids(cam, 3) == [1, 2, 3]  # the first id stays 1, as it always was


def test_a_frame_the_read_loop_never_saw_leaves_a_hole_in_the_ids():
    cam = make_camera(FakeDcam([0, 2]))  # stamp 1 landed and was overtaken before it was read
    assert read_ids(cam, 2) == [1, 3]


def test_a_frame_read_twice_repeats_its_id():
    cam = make_camera(FakeDcam([0, 2, 2]))  # stamp 2 landed between the wake-up for 1 and the read
    assert read_ids(cam, 3) == [1, 3, 3]


def test_the_frame_carries_the_pixels_that_belong_to_its_id():
    cam = make_camera(FakeDcam([0, 2]))
    frames = [cam._read_newest_frame() for _ in range(2)]
    assert [int(frame.frame[0, 0]) for frame in frames] == [0, 2]
    assert cam._current_frame is frames[-1]


def late_in_a_long_capture(dcam):
    """A camera that has already delivered frames 0..65533 of this capture (ids 1..65534)."""
    cam = make_camera(dcam)
    cam._last_frame_number = 65533
    return cam


def test_ids_keep_increasing_when_the_cameras_16_bit_stamp_wraps():
    """Bench 2026-09-21, ORCA-Fusion BT: framestamp is a 16-bit counter. After 65,536 frames in one capture
    (45 minutes at 24 fps) it went 65535 -> 0 while DCAM's own 32-bit frame count went on to 65537; ids
    restarted at 1 and a hardware-sequenced burst was discarded as "a frame was dropped"."""
    cam = late_in_a_long_capture(FakeDcam([65534, 65535, 0, 1]))
    assert read_ids(cam, 4) == [65535, 65536, 65537, 65538]


def test_a_frame_skipped_across_the_wrap_still_leaves_exactly_its_hole():
    cam = late_in_a_long_capture(FakeDcam([65535, 1]))  # stamp 0 landed and was overtaken
    assert read_ids(cam, 2) == [65536, 65538]


def test_a_frame_read_twice_at_the_wrap_still_repeats_its_id():
    cam = late_in_a_long_capture(FakeDcam([65535, 0, 0]))
    assert read_ids(cam, 3) == [65536, 65537, 65537]


def test_a_second_wrap_keeps_counting():
    cam = late_in_a_long_capture(FakeDcam([65535, 0]))
    cam._last_frame_number += 65536  # ...and another 65,536 frames later
    assert read_ids(cam, 2) == [131072, 131073]


def test_ids_keep_increasing_when_capture_restarts_and_the_stamp_starts_over():
    dcam = FakeDcam([0, 1])
    cam = make_camera(dcam)
    assert cam.start_streaming()
    assert read_ids(cam, 2) == [1, 2]

    cam._is_streaming.clear()  # what stop_streaming() leaves behind
    dcam.script([0, 1])  # DCAM counts from 0 again
    assert cam.start_streaming()
    assert read_ids(cam, 2) == [3, 4]
    assert dcam.cap_starts == 2


def test_a_frame_in_flight_across_a_capture_restart_never_moves_ids_backward_or_reuses_one():
    """stop_streaming() leaves the read thread running. A frame it has copied out of DCAM but not yet
    published was numbered against the NEXT capture's base: ids went 1, 3, 2, 3 across a restart (an
    ROI, sensor-mode or trigger-mode change)."""
    dcam = FakeDcam([0, 1])
    cam = make_camera(dcam)
    assert cam.start_streaming()
    ids = read_ids(cam, 1)

    # Park the read thread where the race lives: frame copied out of DCAM, not yet published.
    copied, publish = threading.Event(), threading.Event()

    def process_once_released(raw):
        copied.set()
        publish.wait(5)
        return raw

    def read_in_flight_frame():
        frame = cam._read_newest_frame()
        assert frame is not None
        ids.append(frame.frame_id)

    stopped = threading.Event()

    def restart_capture():
        cam.stop_streaming()
        stopped.set()
        dcam.script([0, 1])  # DCAM counts from 0 again
        cam.start_streaming()

    cam._process_raw_frame = process_once_released
    reader = threading.Thread(target=read_in_flight_frame)
    reader.start()
    assert copied.wait(5)

    restarter = threading.Thread(target=restart_capture)
    restarter.start()
    # An unsynchronized restart finishes while the frame is still in flight. A synchronized one waits
    # for the frame, so stop waiting for it and let the frame through.
    restarter.join(0.5)
    stop_waited_for_the_frame = not stopped.is_set()
    publish.set()
    reader.join(5)
    restarter.join(5)
    assert not reader.is_alive() and not restarter.is_alive()
    # The frame's pixel format is read from DCAM as it is published, so stop_streaming() must not
    # return (and let the caller change that property) while the frame is still in flight.
    assert stop_waited_for_the_frame

    cam._process_raw_frame = lambda raw: raw
    ids += read_ids(cam, 2)
    assert ids == [1, 2, 3, 4]  # Preserve the in-flight frame and rebase on its published ID.
