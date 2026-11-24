"""What an acquisition does when laser AF fails at FOVs: by default it images each FOV where it is; with
LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES = N it stops (as an error) at the Nth FOV in a row whose laser AF failed.

The worker tests use a bare MultiPointWorker (the harness of test_MultiPointWorker_z_stack_order) with a fake laser AF
that succeeds or fails per call as scripted. The end-to-end tests run a simulated acquisition, whose laser AF finds no
spot at any FOV, on its own and as the imaging step of a fluidics protocol run.
"""

import math
import os

import pytest

import control._def
import control.microscope
import squid.acquisition_state as ast
from control.core.fluidics_protocol.events import HoldAction
from control.core.fluidics_protocol.ports import ImagingResult
from control.core.multi_point_worker import laser_af_stop_after_consecutive_failures
import tests.control.test_stubs as ts
from tests.control.core.fluidics_protocol.test_runner import _held, _runner
from tests.control.test_MultiPointController import CountingTracker, add_some_coordinates, select_some_configs
from tests.control.test_MultiPointWorker_z_stack_order import _FakePiezo, _worker


class _ScriptedLaserAF:
    """Laser AF whose move_to_target succeeds or fails as `results` says, one entry per call."""

    def __init__(self, piezo, results):
        self.piezo = piezo
        self.results = list(results)
        self.calls = 0
        self.during_move = None  # called inside move_to_target, e.g. to press Stop
        self.image = None
        self.characterization_mode = False

    def move_to_target(self, target_um):
        self.calls += 1
        if self.during_move is not None:
            self.during_move()
        return self.results.pop(0)

    def apply_relative_offset_um(self, offset_um):
        self.piezo.move_to(self.piezo.position + offset_um)


class _RecordingPiezo(_FakePiezo):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.moves = []

    def move_to(self, um):
        self.moves.append(um)
        super().move_to(um)


def _stop_worker(results, *, stop_after=0, nz=1, z_stacking_config="FROM BOTTOM"):
    w = _worker("CHANNEL FIRST", nz=nz, channels=("A",), use_piezo=True, z_stacking_config=z_stacking_config)
    w.piezo = _RecordingPiezo(position=100.0)
    w.laser_auto_focus_controller = _ScriptedLaserAF(w.piezo, results)
    w.do_reflection_af = True
    del w.perform_autofocus  # the real one
    w._laser_af_stop_after_failures = stop_after
    w._laser_af_consecutive_failures = 0
    aborted = []

    def request_abort():
        aborted.append(True)

    w.request_abort_fn = request_abort
    w.abort_requested_fn = lambda: bool(aborted)
    w._abort_cause = None
    w._end_detail = None
    w.aborted = aborted
    return w


def _acquire_fovs(w, n, region_id="A1"):
    """Like run_coordinate_acquisition: acquire FOVs 0, 1, ... in turn until an abort."""
    for fov in range(n):
        w.acquire_at_position(region_id, "/tmp", fov)
        if w.abort_requested_fn():
            break


def test_by_default_failed_laser_afs_never_stop_the_acquisition():
    w = _stop_worker([False] * 5)
    _acquire_fovs(w, 5)
    assert len(w.frames) == 5  # every FOV imaged where it is
    assert not w.aborted
    assert (w._laser_af_successes, w._laser_af_failures) == (0, 5)


@pytest.mark.parametrize("n", [1, 3])
def test_n_consecutive_failures_stop_the_acquisition_without_imaging_the_nth_fov(n):
    w = _stop_worker([True] + [False] * n, stop_after=n)
    _acquire_fovs(w, n + 1, region_id="B2")
    assert w.aborted == [True]
    assert w._abort_cause == "error"
    assert len(w.frames) == n  # FOV 0 (AF ok) and the n - 1 failed FOVs before the stop
    assert w._end_detail == f"laser AF failed at {n} FOV(s) in a row, the last region 'B2' FOV {n} (time point 0)"


def test_n_minus_1_failures_then_a_success_do_not_stop():
    w = _stop_worker([False, False, True, False, False, True], stop_after=3)
    _acquire_fovs(w, 6)
    assert not w.aborted
    assert len(w.frames) == 6
    assert w._laser_af_consecutive_failures == 0


def test_the_count_carries_over_fovs_that_are_imaged_after_a_failure():
    w = _stop_worker([False, False, True, False, False, False], stop_after=3)
    _acquire_fovs(w, 6)
    assert w.aborted == [True]
    assert len(w.frames) == 5  # stopped at FOV 5, the third failure since the success at FOV 2


def test_a_user_stop_during_the_nth_failed_laser_af_stays_a_user_abort():
    w = _stop_worker([False, False], stop_after=2)
    w.acquire_at_position("A1", "/tmp", 0)
    w.laser_auto_focus_controller.during_move = lambda: w.aborted.append("user")
    w.acquire_at_position("A1", "/tmp", 1)
    assert w.aborted == ["user"]
    assert w._abort_cause is None and w._end_detail is None  # not turned into an error end
    assert len(w.frames) == 1


def test_a_user_stop_during_a_successful_laser_af_makes_no_further_move_or_image():
    w = _stop_worker([True], nz=3, z_stacking_config="FROM CENTER")
    w.laser_auto_focus_controller.during_move = lambda: w.aborted.append("user")
    w.acquire_at_position("A1", "/tmp", 0)
    assert w.piezo.moves == []  # no From Center move down for the stack
    assert w.frames == []
    assert w._abort_cause is None


def test_stop_after_consecutive_failures_setting(monkeypatch):
    assert control._def.LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES == 0  # off by default
    assert laser_af_stop_after_consecutive_failures() == 0
    monkeypatch.setattr(control._def, "LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES", 3)
    assert laser_af_stop_after_consecutive_failures() == 3
    for bad in (-1, 2.5, "3", True, None):
        monkeypatch.setattr(control._def, "LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES", bad)
        assert laser_af_stop_after_consecutive_failures() == 0


def _simulated_controller_with_no_laser_af_spot():
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = CountingTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_reflection_af_flag(True)
    scope.addons.camera_focus.send_trigger()
    mpc.laserAutoFocusController.laser_af_properties.set_reference_image(scope.addons.camera_focus.read_frame())
    # The simulated focus camera shows no spot this detector finds: every laser AF of the run fails.
    assert math.isnan(mpc.laserAutoFocusController.measure_displacement())
    return scope, tt, mpc


@pytest.mark.parametrize("stop_after", [0, 2])
def test_simulated_acquisition_with_no_laser_af_spot(monkeypatch, stop_after):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES", stop_after)
    scope, tt, mpc = _simulated_controller_with_no_laser_af_spot()

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    record = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])
    if stop_after:
        assert mpc.last_end_reason == "error"
        assert tt.image_count == 2  # the first FOV's two channels; it stopped at the second FOV
        assert "laser AF failed at 2 FOV(s) in a row" in record["stats"]["detail"]
    else:
        assert mpc.last_end_reason == "completed"
        assert tt.image_count == mpc.get_acquisition_image_count()
        assert "detail" not in record["stats"]
    mpc.close()
    scope.close()


class _SimulatedImagingPort:
    """An ImagingPort that runs each imaging step as an acquisition of the simulated controller, and reports how it
    ended the way the GUI's port does (last_end_reason, last_image_count)."""

    def __init__(self, mpc, tracker):
        self.mpc = mpc
        self.tracker = tracker
        self.results = []

    def start(self, request):
        self.tracker.finished_event.clear()
        self.mpc.set_base_path(request.run_dir)
        self.mpc.start_new_experiment(request.folder, add_timestamp=False)
        self.mpc.run_acquisition()
        port = self

        class _Handle:
            def wait(self, timeout):
                if not port.tracker.finished_event.wait(timeout):
                    return None
                port.mpc.thread.join(10)
                result = ImagingResult(port.mpc.last_end_reason, port.mpc.last_image_count, request.folder)
                port.results.append(result)
                return result

            def abort(self):
                port.mpc.request_abort_aquisition()

        return _Handle()


def test_a_protocol_run_holds_when_its_acquisition_stops_on_laser_af_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "LASER_AF_STOP_AFTER_CONSECUTIVE_FAILURES", 2)
    scope, tt, mpc = _simulated_controller_with_no_laser_af_spot()
    imaging = _SimulatedImagingPort(mpc, tt)
    runner, _, _, _ = _runner(tmp_path, imaging=imaging)
    try:
        runner.start()
        assert _held(runner, attempt=1)
        assert runner.hold.kind == "imaging" and runner.hold.reason == "error"
        assert [(r.end_reason, r.image_count) for r in imaging.results] == [("error", 2)]
        runner.hold_action(HoldAction.END)
        assert runner.wait(10)
    finally:
        mpc.close()
        scope.close()
