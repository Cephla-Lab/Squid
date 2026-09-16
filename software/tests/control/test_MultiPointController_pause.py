"""End-to-end pause/resume of a simulated acquisition through MultiPointController (large acquisition mode).

Mode off must be exactly today's behaviour: no pause support, no callbacks. Mode on: an operator pause
parks the worker at the next FOV boundary, resume completes the run with every image captured, abort
while paused unwinds promptly, and a nearly-full (simulated) disk pauses then resumes once space frees.
"""

import dataclasses
import threading
import time

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker, add_some_coordinates, select_some_configs


class PauseTracker(TestAcquisitionTracker):
    def __init__(self):
        super().__init__()
        self.paused_event = threading.Event()
        self.resumed_event = threading.Event()
        self.paused_states = []
        self.resumed_seconds = []

    def get_callbacks(self):
        callbacks = super().get_callbacks()

        def paused(state, disk):
            self.paused_states.append((state, disk))
            self.paused_event.set()

        def resumed(paused_s):
            self.resumed_seconds.append(paused_s)
            self.resumed_event.set()

        return dataclasses.replace(callbacks, signal_acquisition_paused=paused, signal_acquisition_resumed=resumed)


def _controller(tt: PauseTracker):
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    return scope, mpc


@pytest.fixture
def fast_disk_polling(monkeypatch):
    """Fast disk re-checks on a large *simulated* disk, so no test depends on the host's free space."""
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.1)
    monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 1000.0)
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 1.0)
    yield


def test_mode_off_has_no_pause_support(fast_disk_polling):
    tt = PauseTracker()
    scope, mpc = _controller(tt)
    assert mpc.pause_state is None
    assert mpc.request_pause() is False

    mpc.run_acquisition()
    assert tt.started_event.wait(5)
    assert mpc.pause_state is None, "mode off: no gate is created"
    assert mpc.request_pause() is False
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    assert tt.paused_states == [] and tt.resumed_seconds == []
    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()
    assert mpc.large_acquisition_mode is False


def test_operator_pause_and_resume_completes_the_run(fast_disk_polling):
    """Operator pause parks the worker at a checkpoint; resume completes the run with every image and
    shifts the timepoint schedule anchor by the paused time (so a timed run never skips timepoints).
    Continuous mode (dt=0) keeps this independent of imaging speed; the skip-vs-anchor behaviour itself
    is covered by the worker unit tests."""
    tt = PauseTracker()
    scope, mpc = _controller(tt)
    mpc.set_Nt(2)
    mpc.set_deltat(0.0)
    mpc.set_large_acquisition_mode(True)

    mpc.run_acquisition()
    assert tt.started_event.wait(5)
    assert mpc.pause_state is not None, "mode on: the gate exists as soon as the run starts"

    assert mpc.request_pause() is True
    assert tt.paused_event.wait(10), "worker must park at the next FOV/timepoint boundary"
    state, disk = tt.paused_states[0]
    assert state.paused and state.reasons == ("operator",)
    assert mpc.pause_state.paused
    assert disk is not None and disk.required_bytes > 0

    images_while_paused = tt.image_count
    time.sleep(1.0)
    assert tt.image_count == images_while_paused, "no images while paused"
    assert not tt.finished_event.is_set()

    worker = mpc.multiPointWorker
    assert mpc.request_resume() is True
    assert tt.resumed_event.wait(10)
    paused_s = tt.resumed_seconds[0]
    assert paused_s >= 0.9
    assert worker._schedule_anchor == pytest.approx(worker.timestamp_acquisition_started + paused_s, abs=1e-3)

    assert tt.finished_event.wait(60)
    mpc.thread.join(10)

    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count(), "a pause must not lose or skip images"
    assert mpc.pause_state is None, "gate discarded after the run"
    assert mpc.large_acquisition_mode is False, "per-run flag reset after the run"


def test_abort_while_paused_unwinds_promptly(fast_disk_polling):
    tt = PauseTracker()
    scope, mpc = _controller(tt)
    mpc.set_Nt(2)
    mpc.set_deltat(0.5)
    mpc.set_large_acquisition_mode(True)

    mpc.run_acquisition()
    assert tt.started_event.wait(5)
    assert mpc.request_pause() is True
    assert tt.paused_event.wait(10)

    started = time.monotonic()
    mpc.request_abort_aquisition()
    assert tt.finished_event.wait(10)
    mpc.thread.join(10)
    assert time.monotonic() - started < 5.0
    assert mpc.last_end_reason == "user_abort"


def test_low_disk_space_pauses_then_resumes_when_space_frees(fast_disk_polling, monkeypatch):
    # A "disk" of 1 KB with a 10 GB reserve can never satisfy the guard -> pause at the first checkpoint.
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 1e-6)
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 10.0)
    tt = PauseTracker()
    scope, mpc = _controller(tt)
    mpc.set_large_acquisition_mode(True)

    mpc.run_acquisition()
    assert tt.started_event.wait(5)
    assert tt.paused_event.wait(10)
    state, disk = tt.paused_states[0]
    assert state.reasons == ("disk_space",)
    assert disk is not None and disk.holding and disk.free_bytes < disk.required_bytes
    assert mpc.disk_status is not None and mpc.disk_status.holding
    assert tt.image_count == 0, "paused before the first FOV, nothing captured"

    # Free up "space": a huge simulated disk and no reserve. The tick re-checks every 0.1 s.
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", 0.0)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 1000.0)
    assert tt.resumed_event.wait(10)
    assert tt.finished_event.wait(60)
    mpc.thread.join(10)

    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()


def test_global_setting_enables_mode_without_per_run_flag(fast_disk_polling, monkeypatch):
    monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", True)
    tt = PauseTracker()
    scope, mpc = _controller(tt)
    assert mpc.large_acquisition_mode is False

    mpc.run_acquisition()
    assert tt.started_event.wait(5)
    assert mpc.pause_state is not None
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)
    assert mpc.last_end_reason == "completed"
