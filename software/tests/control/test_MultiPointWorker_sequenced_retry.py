"""A failed hardware-sequenced burst must not leak a frame into the retry.

A burst fails, among other ways, when a frame the controller fired is late. That frame can still
arrive during the retry; paired with the retry's first capture it is saved under the wrong z, and
the retry's count and consecutive-id checks still pass. The worker restarts the camera stream before
giving up on a burst: what is still in the camera or its SDK is lost with the stream, and a frame the
driver had already read (the Hamamatsu read thread publishes a batch AFTER its DCAM read, outside the
capture lock) is delivered afterwards stamped with the run of the stream it was read in, which the
worker refuses.

An ordinary capture is the opposite case: its batch follows the camera, because the trigger itself
may restart the stream under it (a confocal unit re-arming the camera after a stalled scan).

Worker-level: a bare MultiPointWorker with the camera, controller and timing faked.
"""

import contextlib
import dataclasses
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from control.core.multi_point_utils import CaptureInfo
from control.core.multi_point_worker import MultiPointWorker
from control.core.pending_captures import PendingCaptures
from control.sequencer_program import SeqError, SeqState, SequencerStatus


class FakeCamera:
    """Delivers frames to the worker's callback, each stamped with the run of the stream it was read in.

    A frame can be left IN FLIGHT: already read by the driver, not yet delivered (a frame callback ahead
    of the worker's is slow). Restarting the stream does NOT drop it - it is already in Python - so it is
    delivered during the next burst, carrying the number of the run before."""

    def __init__(self, callback):
        self._callback = callback
        self.generation = 1
        self.in_flight = []
        self.stream_restarts = 0

    def stream_generation(self) -> int:
        return self.generation

    def emit(self, frame_id: int, origin: str, *, in_flight: bool = False) -> None:
        frame = SimpleNamespace(frame_id=frame_id, frame=origin, stream_generation=self.generation)
        if in_flight:
            self.in_flight.append(frame)
        else:
            self._callback(frame)

    def deliver_in_flight(self) -> None:
        for frame in self.in_flight:
            self._callback(frame)
        self.in_flight = []

    def stop_streaming(self) -> None:
        pass  # what the driver had already read stays in Python

    def start_streaming(self) -> None:
        self.generation += 1
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
    w._sleep = lambda seconds: None
    w.abort_requested_fn = lambda: False
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
            camera.deliver_in_flight()  # the first attempt's late frame, read before the restart, arrives first
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
    assert any("Ignoring frame 102" in str(call) for call in w._log.warning.call_args_list)
    w._abort_due_to_error.assert_not_called()


def test_a_burst_the_controller_refused_also_resynchronizes_the_camera():
    """Every failed burst goes through the same resynchronization, not only the late-frame one."""
    w = _bare_worker()
    w.microcontroller.seq_status = SequencerStatus(SeqState.FAILED, SeqError.INTERLOCK_OPEN, 0, 0)

    frames, reason = w._run_sequenced_burst(_captures(3), 1000)

    assert frames is None and "interlock" in reason
    assert w.camera.stream_restarts == 1


def test_an_ordinary_capture_pairs_its_frame_after_the_trigger_restarted_the_stream():
    """AION/NL5: a stalled confocal scan is aborted inside send_trigger(), the camera re-armed (stream
    restart) and the scan retried. The retried scan's frame is this capture's, whatever run it is from."""
    w = _bare_worker()
    camera = w.camera
    w._ready_for_next_trigger.clear()
    w._pending_captures.expect(_captures(1), stream_generation=None, deliver=w._dispatch_frame)
    camera.start_streaming()  # the re-arm, inside the trigger
    camera.emit(7, "retried-scan")
    w._dispatch_frame.assert_called_once()
    assert w._dispatch_frame.call_args.args[0].frame == "retried-scan"
    assert w._ready_for_next_trigger.is_set()
    w._log.warning.assert_not_called()


def test_a_stream_restart_under_a_queued_burst_is_named_when_its_frame_is_refused():
    """The camera (or its driver) restarted its own stream under a queued burst. The burst's frames
    are suspect, so a frame of the new run is refused - the burst times out and is retried - and
    the log says what happened rather than that the capture was given up."""
    w = _bare_worker()
    camera = w.camera
    w._ready_for_next_trigger.clear()
    w._pending_captures.expect(_captures(3), stream_generation=camera.generation, deliver=w._dispatch_frame)
    camera.start_streaming()  # not the worker's doing
    camera.emit(7, "run2-z0")
    w._dispatch_frame.assert_not_called()
    assert not w._ready_for_next_trigger.is_set()
    assert len(w._pending_captures) == 3
    assert any(
        "frame 7" in str(call) and "for run 1" in str(call) for call in w._log.warning.call_args_list
    ), w._log.warning.call_args_list


def test_a_burst_is_not_started_after_an_abort_was_requested():
    """The software path checks the abort flag before every capture; the burst is one SEQ_RUN, so the check
    comes before it. Cancelling after sending would still let the controller fire the first exposure."""
    w = _bare_worker()
    w.abort_requested_fn = lambda: True
    frames, reason = w._run_sequenced_burst(_captures(3), 1000)
    assert frames is None and "aborted" in reason
    w.microcontroller.seq_run.assert_not_called()
    assert w.camera.stream_restarts == 0


def test_an_overrun_sequence_is_cancelled_before_the_camera_is_restarted():
    """The host's deadline passed with the controller still running: the run is cancelled and waited out
    before the camera stream is restarted, so no run keeps firing under the resynchronized camera."""
    w = _bare_worker()
    events = []

    def overrun(timeout):
        raise TimeoutError("the hardware sequence did not finish")

    w._wait_for_sequence = overrun
    w._overdue_cancel_wait_s = lambda: 1.0
    w.microcontroller.seq_cancel.side_effect = lambda: events.append("cancel")
    w.microcontroller.is_busy.side_effect = [True, False]
    w.microcontroller.seq_status = SequencerStatus(SeqState.DONE, SeqError.CANCELED, 0, 1)
    start_streaming = w.camera.start_streaming

    def restart():
        events.append("camera restart")
        start_streaming()

    w.camera.start_streaming = restart
    frames, reason = w._run_sequenced_burst(_captures(3), 1000)
    assert frames is None
    assert events == ["cancel", "camera restart"]


def test_a_failed_burst_puts_the_piezo_back_at_the_stack_start():
    """The engine's fail() leaves the piezo at the last plane it commanded (only Done and a cancel return
    to the start) while the worker's cache says the stack start; every failed attempt moves it back there,
    so the retry and the next acquisition start where the cache says."""
    w = _bare_worker()
    w._applied_config = None
    w.selected_configurations = [MagicMock()]
    w.z_piezo_um = 20.0
    w.deltaZ = 0.001  # mm
    w.NZ, w.Nt, w.time_point = 3, 1, 0
    w.do_reflection_af = w.do_autofocus = False
    w.piezo = MagicMock()
    w.piezo.range_um = 300
    w.stage = MagicMock()
    w._run_sequenced_burst = MagicMock(return_value=(None, "the controller reported an error"))
    w._ask_user_to_intervene = MagicMock()

    w._acquire_sequenced_stack("A1", "/tmp", 0)

    assert w.piezo.move_to.call_args_list == [call(20.0)] * 3
    w._ask_user_to_intervene.assert_called_once()
