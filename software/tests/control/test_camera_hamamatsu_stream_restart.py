"""A frame the Hamamatsu read thread had already read survives a stream restart - and says which run it came from.

The read thread takes a batch out of DCAM's ring under the capture lock and publishes it AFTER releasing the
lock, one callback at a time. A frame callback ahead of the acquisition worker's (a display) can hold a frame
of the batch for as long as it likes; stop_streaming() + start_streaming() meanwhile tear down and restart the
capture, and that frame is still delivered afterwards. Its ids even continue into the new capture
(_frame_id_base is rebased from the newest frame read), so by count and id order it passes as the restarted
stream's first frame. What tells it apart is the stream generation it was stamped with when it was read.

The end-to-end case drives the REAL read loop and the real worker burst over a fake DCAM ring.
"""

import threading

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


def test_a_frame_read_before_the_restart_is_not_paired_with_the_retried_burst():
    w = _bare_worker()
    cam, ring = _restartable_camera()
    cam._frame_callbacks = []
    cam._frame_callbacks_enabled = True
    cam._read_thread_running = threading.Event()
    cam._read_thread_keep_running = threading.Event()
    cam._read_thread_keep_running.set()
    cam._read_thread_wait_period_s = 0.01
    held = threading.Event()
    release = threading.Event()

    def slow_display_callback(frame):
        # Registered ahead of the worker's callback: holds frame 3 after the driver read it, until released.
        if frame.frame_id == 3:
            held.set()
            assert release.wait(3)

    cam.add_frame_callback(slow_display_callback)
    cam.add_frame_callback(w._image_callback)
    assert cam.start_streaming()
    w.camera = cam
    w._frame_wait_timeout_s = lambda: 0.1
    w.microcontroller.seq_status = SequencerStatus(SeqState.DONE, SeqError.NONE, 0, 3)
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
    reader = threading.Thread(target=cam._read_frames_when_available, daemon=True)
    reader.start()
    try:
        frames, reason = w._run_sequenced_burst(_captures(3), 1000)
        assert frames is None and "2 of 3" in reason
        assert cam.stream_generation() == 2 and cam._frame_id_base == 4  # restarted; ids continue at 4

        frames, reason = w._run_sequenced_burst(_captures(3), 1000)
        assert reason is None
        # (id, DCAM frame number within its capture, z): the retry's own three frames, in order.
        assert [(f.frame_id, int(f.frame[0, 0]), info.z_index) for f, info in frames] == [
            (4, 0, 0),
            (5, 1, 1),
            (6, 2, 2),
        ]
        assert any("Ignoring frame 3" in str(call) for call in w._log.warning.call_args_list)
        w._abort_due_to_error.assert_not_called()
    finally:
        release.set()
        cam._read_thread_keep_running.clear()
        reader.join(2)
        assert not reader.is_alive()
