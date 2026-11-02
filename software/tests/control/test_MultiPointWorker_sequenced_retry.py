"""A failed hardware-sequenced burst must not leak a frame into the retry.

A burst fails, among other ways, when a frame the controller fired is late. That frame can still
arrive during the retry, and a frame carries nothing that tells it from the retry's first frame:
paired with the retry's first capture it is saved under the wrong z, and the retry's count and
consecutive-id checks still pass. The worker resynchronizes the camera before giving up on a burst
(it restarts the stream, which drops what is in flight, as the drivers do), so the retry starts
from a quiet camera.

Worker-level: a bare MultiPointWorker with the camera, controller and timing faked.
"""

import contextlib
import dataclasses
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from control.core.multi_point_utils import CaptureInfo
from control.core.multi_point_worker import MultiPointWorker
from control.core.pending_captures import PendingCaptures
from control.sequencer_program import SeqError, SeqState, SequencerStatus


class FakeCamera:
    """Delivers frames to the worker's callback. A frame can be left IN FLIGHT (fired, not delivered yet);
    it is delivered on the next call to the camera unless the stream was stopped meanwhile, which drops
    it - as the real drivers do (ToupCam Stop(), Hamamatsu cap_stop + buf_release)."""

    def __init__(self, callback):
        self._callback = callback
        self.in_flight = []
        self.stream_restarts = 0

    def emit(self, frame_id: int, origin: str, *, in_flight: bool = False) -> None:
        frame = SimpleNamespace(frame_id=frame_id, frame=origin)
        if in_flight:
            self.in_flight.append(frame)
        else:
            self._callback(frame)

    def deliver_in_flight(self) -> None:
        for frame in self.in_flight:
            self._callback(frame)
        self.in_flight = []

    def stop_streaming(self) -> None:
        self.in_flight = []

    def start_streaming(self) -> None:
        self.stream_restarts += 1


def _bare_worker():
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._ready_for_next_trigger = threading.Event()
    w._ready_for_next_trigger.set()
    w._image_callback_idle = threading.Event()
    w._image_callback_idle.set()
    w._pending_captures = PendingCaptures()
    w._log = MagicMock()
    w._timing = MagicMock()
    w._timing.get_timer.side_effect = lambda *_: contextlib.nullcontext()
    w._dispatch_frame = MagicMock()
    w._abort_due_to_error = MagicMock()
    w._frame_wait_timeout_s = lambda: 0.001  # a frame still in flight after this is "late"
    w._sequenced_burst_timeout_s = lambda *_: 1.0
    w._wait_for_sequence = lambda timeout: None
    w.microcontroller = MagicMock()
    w.camera = FakeCamera(w._image_callback)
    return w


def _captures(n):
    base = CaptureInfo(
        position=None,
        z_index=0,
        capture_time=0,
        z_piezo_um=20,
        configuration=None,
        save_directory="/tmp",
        file_id="test",
        region_id="r",
        fov=0,
        configuration_idx=0,
        time_point=0,
    )
    return [dataclasses.replace(base, z_index=z) for z in range(n)]


def test_a_frame_still_in_flight_from_a_failed_burst_is_not_paired_with_the_retry():
    w = _bare_worker()
    camera = w.camera
    # Both attempts fire all three frames; the first attempt's last frame is late.
    w.microcontroller.seq_status = SequencerStatus(SeqState.DONE, SeqError.NONE, 0, 3)
    attempts = []

    def run(*args):
        attempts.append(args)
        if len(attempts) == 1:
            camera.emit(100, "attempt1-z0")
            camera.emit(101, "attempt1-z1")
            camera.emit(102, "attempt1-z2", in_flight=True)  # still on its way past the frame timeout
        else:
            camera.deliver_in_flight()  # whatever the first attempt left in the pipe arrives first
            camera.emit(103, "attempt2-z0")
            camera.emit(104, "attempt2-z1")
            camera.emit(105, "attempt2-z2")

    w.microcontroller.seq_run.side_effect = run

    frames, reason = w._run_sequenced_burst(_captures(3), 1000)
    assert frames is None and "2 of 3" in reason
    assert camera.stream_restarts == 1, "the camera is resynchronized before the burst can be retried"

    frames, reason = w._run_sequenced_burst(_captures(3), 1000)
    assert reason is None
    assert [(frame.frame, info.z_index) for frame, info in frames] == [
        ("attempt2-z0", 0),
        ("attempt2-z1", 1),
        ("attempt2-z2", 2),
    ]
    w._abort_due_to_error.assert_not_called()


def test_a_burst_the_controller_refused_also_resynchronizes_the_camera():
    """Every failed burst goes through the same resynchronization, not only the late-frame one."""
    w = _bare_worker()
    w.microcontroller.seq_status = SequencerStatus(SeqState.FAILED, SeqError.INTERLOCK_OPEN, 0, 0)

    frames, reason = w._run_sequenced_burst(_captures(3), 1000)

    assert frames is None and "interlock" in reason
    assert w.camera.stream_restarts == 1
