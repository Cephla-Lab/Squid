"""Pause checkpoints of MultiPointWorker (large acquisition mode).

Exercises _pause_checkpoint / _pause_tick on a worker built via __new__ (no hardware), following
tests/control/test_worker_reason.py. Mode off must be a strict no-op; mode on must block at the
checkpoint, keep the watchdog alive with status "paused", re-anchor the timepoint schedule, and
unwind cleanly on abort.
"""

import queue
import threading
import time
from types import SimpleNamespace

import pytest

import control._def
from control import utils
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.core.multi_point_worker import MultiPointWorker
from control.core.pause_gate import PauseGate


class RecordingRunState:
    def __init__(self):
        self.statuses = []
        self.beats = 0

    def set_status(self, status):
        self.statuses.append(status)

    def beat(self, progress=None, force=False):
        self.beats += 1

    def end(self, reason, stats=None):
        pass


class FakeDiskGuard:
    """Holds the gate on the first update, releases after `release_after` updates."""

    def __init__(self, release_after=2, free=1, required=10):
        self.updates = 0
        self.release_after = release_after
        self.last_status = SimpleNamespace(free_bytes=free, required_bytes=required, holding=False)

    def update(self, gate):
        self.updates += 1
        if self.updates >= self.release_after:
            gate.release("disk_space")
            self.last_status = SimpleNamespace(free_bytes=100, required_bytes=10, holding=False)
        else:
            gate.hold("disk_space")
            self.last_status = SimpleNamespace(free_bytes=1, required_bytes=10, holding=True)
        return self.last_status


def _callbacks(paused, resumed):
    noop = lambda *a, **kw: None
    return MultiPointControllerFunctions(
        signal_acquisition_start=noop,
        signal_acquisition_finished=noop,
        signal_new_image=noop,
        signal_current_configuration=noop,
        signal_current_fov=noop,
        signal_overall_progress=noop,
        signal_region_progress=noop,
        signal_acquisition_paused=lambda state, disk: paused.append((state, disk)),
        signal_acquisition_resumed=lambda paused_s: resumed.append(paused_s),
    )


def _make_worker(gate=None, disk_guard=None):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = __import__("squid.logging").logging.get_logger("test-worker")
    w._pause_gate = gate
    w._large_acquisition_mode = gate is not None
    w._disk_guard = disk_guard
    # The pause tick drains job results in mode-on runs; give the bare worker an empty drain.
    w._job_runners = []
    w._completion_tracker = None
    w._inline_results = queue.SimpleQueue()
    w._abort_on_failed_job = True
    w._last_disk_check_mono = 0.0
    w._run_state = RecordingRunState()
    w.paused_events = []
    w.resumed_events = []
    w.callbacks = _callbacks(w.paused_events, w.resumed_events)
    w.abort_requested_fn = lambda: False
    w._slack_notifier = None
    w._schedule_anchor = 1000.0
    w.time_point = 0
    w.Nt = 3
    w.experiment_ID = "exp"
    w._timepoint_fov_count = 0
    w.image_count = 0
    w._last_frame_nbytes = None
    return w


def _run_checkpoint_in_thread(worker, current_path=None):
    result = {}

    def _run():
        result["value"] = worker._pause_checkpoint(current_path)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t, result


# --- mode off: strict no-op -------------------------------------------------------------------------


def test_mode_off_checkpoint_is_a_no_op():
    w = _make_worker(gate=None)
    assert w._pause_checkpoint("/tmp/x") is True
    assert w.paused_events == [] and w.resumed_events == []
    assert w._run_state.statuses == []
    assert w._schedule_anchor == 1000.0


def test_mode_off_does_not_consult_abort_or_disk():
    w = _make_worker(gate=None)
    w.abort_requested_fn = lambda: (_ for _ in ()).throw(AssertionError("must not be called"))
    assert w._pause_checkpoint() is True


# --- mode on ---------------------------------------------------------------------------------------


def test_mode_on_not_paused_returns_quickly_and_updates_disk_guard():
    gate = PauseGate()
    guard = FakeDiskGuard(release_after=1)  # never holds
    w = _make_worker(gate=gate, disk_guard=guard)
    assert w._pause_checkpoint() is True
    assert guard.updates == 1
    assert w.paused_events == [] and w.resumed_events == []
    assert w._run_state.statuses == []


def test_operator_pause_blocks_until_resume_and_reanchors_schedule(monkeypatch):
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.0, raising=False)
    gate = PauseGate()
    w = _make_worker(gate=gate)
    gate.hold("operator")

    thread, result = _run_checkpoint_in_thread(w, "/tmp/x")
    time.sleep(0.3)
    assert thread.is_alive()
    assert w._run_state.statuses == ["paused"]
    assert len(w.paused_events) == 1
    state, disk = w.paused_events[0]
    assert state.reasons == ("operator",) and disk is None
    assert w._run_state.beats >= 1, "watchdog must keep beating while paused"

    gate.release("operator")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result["value"] is True
    assert w._run_state.statuses == ["paused", "running"]
    assert len(w.resumed_events) == 1
    paused_s = w.resumed_events[0]
    assert paused_s >= 0.25
    assert w._schedule_anchor == pytest.approx(1000.0 + paused_s)


def test_abort_while_paused_returns_false_and_keeps_reasons():
    gate = PauseGate()
    w = _make_worker(gate=gate)
    abort = threading.Event()
    w.abort_requested_fn = abort.is_set
    gate.hold("disk_space")

    thread, result = _run_checkpoint_in_thread(w, "/tmp/x")
    time.sleep(0.2)
    assert thread.is_alive()
    abort.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result["value"] is False
    assert w._run_state.statuses == ["paused", "running"]
    assert gate.is_paused(), "abort must not silently release the pause holders"
    assert len(w.resumed_events) == 1, "GUI still gets the resumed signal so it can clear the paused state"


def test_abort_requested_before_checkpoint_returns_false_without_pausing():
    gate = PauseGate()
    w = _make_worker(gate=gate)
    w.abort_requested_fn = lambda: True
    assert w._pause_checkpoint() is False
    assert w.paused_events == []


def test_disk_guard_hold_pauses_and_tick_rechecks_until_space_frees(monkeypatch):
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.0, raising=False)
    gate = PauseGate()
    guard = FakeDiskGuard(release_after=3)
    w = _make_worker(gate=gate, disk_guard=guard)

    started = time.monotonic()
    assert w._pause_checkpoint("/tmp/x") is True
    assert time.monotonic() - started < 5.0
    assert guard.updates == 3, "checkpoint update + two tick re-checks"
    assert not gate.is_paused()
    # First emission is the transition (holding), later emissions refresh the disk numbers.
    assert len(w.paused_events) >= 1
    state, disk = w.paused_events[0]
    assert state.reasons == ("disk_space",)
    assert disk.free_bytes == 1 and disk.required_bytes == 10
    assert len(w.resumed_events) == 1


def test_tick_respects_poll_interval(monkeypatch):
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 3600.0, raising=False)
    gate = PauseGate()
    guard = FakeDiskGuard(release_after=99)
    w = _make_worker(gate=gate, disk_guard=guard)
    w._last_disk_check_mono = time.monotonic()
    gate.hold("disk_space")
    beats_before = w._run_state.beats
    w._pause_tick()
    w._pause_tick()
    assert guard.updates == 0, "disk not re-checked before the poll interval elapsed"
    assert w._run_state.beats == beats_before + 2, "but the watchdog is beaten on every tick"


def test_slack_notified_on_transitions_only(monkeypatch):
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.0, raising=False)
    gate = PauseGate()
    guard = FakeDiskGuard(release_after=4)
    w = _make_worker(gate=gate, disk_guard=guard)
    calls = []
    w._slack_notifier = SimpleNamespace(
        notify_acquisition_paused=lambda **kw: calls.append(("paused", kw)),
        notify_acquisition_resumed=lambda **kw: calls.append(("resumed", kw)),
    )
    assert w._pause_checkpoint() is True
    kinds = [c[0] for c in calls]
    assert kinds == ["paused", "resumed"]
    assert calls[0][1]["reasons"] == ("disk_space",)
    assert calls[0][1]["free_bytes"] == 1 and calls[0][1]["required_bytes"] == 10
    assert calls[0][1]["timepoint"] == 1 and calls[0][1]["total_timepoints"] == 3
    assert calls[1][1]["paused_seconds"] >= 0.0


def test_slack_failures_do_not_break_the_checkpoint():
    gate = PauseGate()
    guard = FakeDiskGuard(release_after=2)
    w = _make_worker(gate=gate, disk_guard=guard)

    def boom(**kw):
        raise RuntimeError("slack down")

    w._slack_notifier = SimpleNamespace(notify_acquisition_paused=boom, notify_acquisition_resumed=boom)
    assert w._pause_checkpoint() is True


# --- frame size estimate ----------------------------------------------------------------------------


def test_estimate_frame_bytes_prefers_last_frame_then_camera_worst_case():
    w = _make_worker()
    w._last_frame_nbytes = 12345
    assert w._estimate_frame_bytes() == 12345

    w._last_frame_nbytes = None
    w.camera = SimpleNamespace(get_crop_size=lambda: (100, 50), get_pixel_format=lambda: "MONO16")
    monkeypatched = __import__("squid.config").config.CameraPixelFormat
    assert w._estimate_frame_bytes() == 100 * 50 * (3 if monkeypatched.is_color_format("MONO16") else 2)


# --- FOV loop wiring: drain policy and checkpoint placement ------------------------------------------


def _fov_loop_worker(gate):
    w = _make_worker(gate=gate)
    w._timing = utils.TimingManager("t")
    w._backpressure = SimpleNamespace(reset=lambda: None)
    w.scan_region_coords_mm = {"A1": (0.0, 0.0)}
    w.scan_region_fov_coords_mm = {"A1": [(0.0, 0.0), (1.0, 0.0)]}
    w.NZ = 1
    w.selected_configurations = [object()]
    w._abort_on_failed_job = True
    w.drain_calls = []
    w.moves = []
    w._summarize_runner_outputs = lambda drain_all=False: (
        w.drain_calls.append(drain_all),
        SimpleNamespace(none_failed=True, had_results=False),
    )[1]
    w.move_to_coordinate = lambda coord, region_id, fov: w.moves.append((region_id, fov))
    w.acquire_at_position = lambda region_id, current_path, fov: None
    w.handle_acquisition_abort = lambda current_path: w.moves.append(("abort", current_path))
    return w


def test_fov_loop_mode_off_drains_one_result_per_queue_as_before():
    w = _fov_loop_worker(gate=None)
    w.run_coordinate_acquisition("/tmp/x")
    assert w.drain_calls == [False, False]
    assert w.moves == [("A1", 0), ("A1", 1)]


def test_fov_loop_mode_on_drains_everything_and_checkpoints_before_moving():
    gate = PauseGate()
    w = _fov_loop_worker(gate=gate)
    order = []
    w.move_to_coordinate = lambda coord, region_id, fov: order.append(("move", fov))
    original = w._pause_checkpoint
    w._pause_checkpoint = lambda current_path=None: (
        order.append(("checkpoint", current_path)),
        original(current_path),
    )[1]
    w.run_coordinate_acquisition("/tmp/x")
    assert w.drain_calls == [True, True]
    assert order == [("checkpoint", "/tmp/x"), ("move", 0), ("checkpoint", "/tmp/x"), ("move", 1)]


def test_fov_loop_abort_while_paused_unwinds_via_handle_acquisition_abort():
    gate = PauseGate()
    w = _fov_loop_worker(gate=gate)
    gate.hold("operator")
    abort = threading.Event()
    w.abort_requested_fn = abort.is_set
    threading.Timer(0.2, abort.set).start()
    w.run_coordinate_acquisition("/tmp/x")
    assert w.moves == [("abort", "/tmp/x")], "no stage move after an abort at the checkpoint"
