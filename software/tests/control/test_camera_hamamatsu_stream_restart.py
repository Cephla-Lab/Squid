"""A frame the Hamamatsu read thread had already read survives a stream restart - and says which run it came from.

The read thread takes a batch out of DCAM's ring under the capture lock and publishes it AFTER releasing the
lock, one callback at a time. A frame callback ahead of the acquisition worker's (a display) can hold a frame
of the batch for as long as it likes; stop_streaming() + start_streaming() meanwhile tear down and restart the
capture, and that frame is still delivered afterwards. Its ids even continue into the new capture
(_frame_id_base is rebased from the newest frame read), so by count and id order it passes as the restarted
stream's first frame. What tells it apart is the stream generation it was stamped with when it was read.

The worker's own callback can be the one that is slow: it may already be running (past the point where the
worker waits for it to finish) when the burst is given up, the stream restarted and the retry's captures
queued. Whatever it does when it resumes must not touch the retry: the pairing decision is made under the
batch's lock against the run the batch was queued for, and a frame paired before the batch was given up goes
to that batch's sink, not the retry's and not the save jobs.

The end-to-end cases drive the REAL read loop and the real worker burst over a fake DCAM ring.
"""

import contextlib
import threading

from control.core.pending_captures import PendingCaptures

from control.sequencer_program import SeqError, SeqState, SequencerStatus
from tests.control.test_camera_hamamatsu_ring_read import EXTERNAL, FakeRing, make_camera
from tests.control.test_MultiPointWorker_sequenced_retry import _bare_worker, _captures


class RestartableRing(FakeRing):
    """FakeRing plus what a capture restart and the read loop's wait need."""

    def __init__(self):
        super().__init__(EXTERNAL)
        self.wake = threading.Event()

    def produce(self, count):
        super().produce(count)
        self.wake.set()

    def wait_event(self, event, timeout_ms):
        ready = self.wake.wait(0.01)
        self.wake.clear()
        return ready

    def cap_stop(self):
        return True

    def buf_release(self):
        return True


def _restartable_camera():
    ring = RestartableRing()
    cam = make_camera(ring)
    return cam, ring


def test_frames_carry_the_generation_of_the_capture_they_were_read_in():
    cam, ring = _restartable_camera()
    assert cam.start_streaming()
    ring.produce(2)
    before = cam._read_frames()
    assert [frame.stream_generation for frame in before] == [1, 1] == [cam.stream_generation()] * 2

    assert cam.stop_streaming()
    assert cam.start_streaming()
    ring.produce(1)
    [after] = cam._read_frames()
    assert after.stream_generation == 2 == cam.stream_generation()
    assert after.frame_id == before[-1].frame_id + 1  # ids keep counting across the restart; the generation does not


@contextlib.contextmanager
def _worker_reading_from(cam, w, *, ahead_of_the_worker=()):
    """The real read loop on its own thread, publishing to the given callbacks and then the worker's."""
    cam._frame_callbacks = []
    cam._frame_callbacks_enabled = True
    cam._read_thread_running = threading.Event()
    cam._read_thread_keep_running = threading.Event()
    cam._read_thread_keep_running.set()
    cam._read_thread_wait_period_s = 0.01
    for callback in ahead_of_the_worker:
        cam.add_frame_callback(callback)
    cam.add_frame_callback(w._image_callback)
    assert cam.start_streaming()
    w.camera = cam
    w._frame_wait_timeout_s = lambda: 0.1
    w.microcontroller.seq_status = SequencerStatus(SeqState.DONE, SeqError.NONE, 0, 3)
    reader = threading.Thread(target=cam._read_frames_when_available, daemon=True)
    reader.start()
    try:
        yield
    finally:
        cam._read_thread_keep_running.clear()
        reader.join(2)
        assert not reader.is_alive()


def _retry_frames(frames):
    # (id, DCAM frame number within its capture, z)
    return [(f.frame_id, int(f.frame[0, 0]), info.z_index) for f, info in frames]


def test_a_frame_read_before_the_restart_is_not_paired_with_the_retried_burst():
    w = _bare_worker()
    cam, ring = _restartable_camera()
    held = threading.Event()
    release = threading.Event()

    def slow_display_callback(frame):
        # Registered ahead of the worker's callback: holds frame 3 after the driver read it, until released.
        if frame.frame_id == 3:
            held.set()
            assert release.wait(3)

    attempts = []

    def run(*args):
        attempts.append(args)
        if len(attempts) == 1:
            ring.produce(3)
            assert held.wait(1)  # frames 1 and 2 reached the worker; frame 3 is held past the frame timeout
        else:
            ring.produce(3)  # the restarted capture's frames, numbered from 0 again by DCAM
            release.set()  # ...and the held frame 3 of the run before is delivered first

    w.microcontroller.seq_run.side_effect = run
    with _worker_reading_from(cam, w, ahead_of_the_worker=[slow_display_callback]):
        try:
            frames, reason = w._run_sequenced_burst(_captures(3), 1000)
            assert frames is None and "2 of 3" in reason
            assert cam.stream_generation() == 2 and cam._frame_id_base == 4  # restarted; ids continue at 4

            frames, reason = w._run_sequenced_burst(_captures(3), 1000)
            assert reason is None
            assert _retry_frames(frames) == [(4, 0, 0), (5, 1, 1), (6, 2, 2)]  # the retry's own three, in order
            assert any("Ignoring frame 3" in str(call) for call in w._log.warning.call_args_list)
            w._abort_due_to_error.assert_not_called()
        finally:
            release.set()


class _PairingPausedAt(PendingCaptures):
    """The worker's pending captures, with the camera thread held at a chosen point of pairing frame 3:
    BEFORE the decision (the callback is in, the generation of frame 3 is still the stream's) or AFTER it
    (frame 3 is paired with the first burst's last capture, not yet delivered)."""

    def __init__(self, *, before: bool):
        super().__init__()
        self.before = before
        self.held = threading.Event()
        self.release = threading.Event()

    def _hold(self):
        self.held.set()
        assert self.release.wait(3)

    def take(self, frame_id, *, stream_generation):
        if frame_id == 3 and self.before:
            self._hold()
        pairing = super().take(frame_id, stream_generation=stream_generation)
        if frame_id == 3 and not self.before:
            self._hold()
        return pairing


def _burst_given_up_under_a_running_callback(pause_before_pairing: bool):
    w = _bare_worker()
    w._pending_captures = _PairingPausedAt(before=pause_before_pairing)
    cam, ring = _restartable_camera()
    attempts = []

    def run(*args):
        attempts.append(args)
        if len(attempts) == 1:
            ring.produce(3)
            assert w._pending_captures.held.wait(1)  # the worker's callback for frame 3 is in, and stays in
        else:
            ring.produce(3)  # the retry's frames; the paused callback resumes first and finishes
            w._pending_captures.release.set()

    w.microcontroller.seq_run.side_effect = run
    with _worker_reading_from(cam, w):
        try:
            frames, reason = w._run_sequenced_burst(_captures(3), 1000)
            assert frames is None and "2 of 3" in reason
            assert not w._image_callback_idle.is_set(), "the callback is still running after the drain timeout"
            assert any("callback" in str(call) for call in w._log.warning.call_args_list)
            assert cam.stream_generation() == 2

            frames, reason = w._run_sequenced_burst(_captures(3), 1000)
            assert reason is None
            assert _retry_frames(frames) == [(4, 0, 0), (5, 1, 1), (6, 2, 2)]
            w._dispatch_frame.assert_not_called()  # nothing of the failed burst reached a save job
            w._abort_due_to_error.assert_not_called()
        finally:
            w._pending_captures.release.set()
    return w


def test_a_callback_that_was_still_to_pair_when_the_burst_was_given_up_cannot_pair_with_the_retry():
    w = _burst_given_up_under_a_running_callback(pause_before_pairing=True)
    assert any("Ignoring frame 3" in str(call) for call in w._log.warning.call_args_list)


def test_a_callback_that_had_paired_when_the_burst_was_given_up_delivers_to_the_failed_burst_only():
    _burst_given_up_under_a_running_callback(pause_before_pairing=False)
