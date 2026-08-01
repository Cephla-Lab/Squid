"""Tests for time-lapse pacing: every requested time point must be acquired.

Regression cover for the old skip-if-behind-schedule logic, which silently dropped
requested time points whenever a round took longer than dt.  A real Nt=3 / dt=5 s run
whose rounds took 31.5 s produced exactly one time point.
"""

import copy
import inspect
import logging
import os
import tempfile
import threading
import time

import pytest

import control._def
import control.microscope
from control.core.multi_point_controller import MultiPointController

import tests.control.test_stubs as ts

# Imported as a module, not by name: pytest warns when it sees a `Test*` class with an
# __init__ in a test module's namespace and tries to collect it.
import tests.control.test_MultiPointController as mpc_tests


def _configure_single_fov(mpc: MultiPointController, nz: int = 1, n_channels: int = 1):
    """Give the controller the minimum valid acquisition: one region, one FOV."""
    all_configuration_names = [
        config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
    ]
    assert len(all_configuration_names) >= n_channels

    mpc.set_NZ(nz)
    mpc.set_selected_configurations(all_configuration_names[0:n_channels])
    mpc.scanCoordinates.clear_regions()

    # NOTE: If the coordinates aren't in the valid range for our stage, regions silently fail to add.
    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 1, 1, 0)
    return all_configuration_names


class _IlluminationSpy:
    """Counts every illumination on/off call at both the LiveController and MCU level.

    Wraps rather than replaces, so the real hardware path still executes and the timing
    behaviour under test is the behaviour that ships.
    """

    def __init__(self, live_controller, microcontroller):
        self.lc_on = 0
        self.lc_off = 0
        self.mcu_on = 0
        self.mcu_off = 0
        self._targets = []
        for obj, name, counter in (
            (live_controller, "turn_on_illumination", "lc_on"),
            (live_controller, "turn_off_illumination", "lc_off"),
            (microcontroller, "turn_on_illumination", "mcu_on"),
            (microcontroller, "turn_off_illumination", "mcu_off"),
        ):
            self._targets.append((obj, name, getattr(obj, name)))
            setattr(obj, name, self._wrap(getattr(obj, name), counter))

    def _wrap(self, original, counter):
        def wrapper(*args, **kwargs):
            setattr(self, counter, getattr(self, counter) + 1)
            return original(*args, **kwargs)

        return wrapper

    def restore(self):
        for obj, name, original in self._targets:
            try:
                delattr(obj, name)
            except AttributeError:
                setattr(obj, name, original)


def _run_time_lapse(nt: int, dt: float, dry_run: bool = False, spy: bool = False):
    """Run a full simulated acquisition with the given time-lapse settings."""
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = mpc_tests.TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    mpc_tests.add_some_coordinates(mpc)
    mpc_tests.select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_Nt(nt)
    mpc.set_deltat(dt)
    mpc.set_dry_run(dry_run)

    if spy:
        tt.illumination_spy = _IlluminationSpy(mpc.liveController, scope.low_level_drivers.microcontroller)

    started = time.time()
    mpc.run_acquisition()
    assert tt.started_event.wait(30)
    assert tt.finished_event.wait(120)
    elapsed = time.time() - started

    worker = mpc.multiPointWorker
    return mpc, tt, worker, elapsed, scope


def test_dry_run_flag_reaches_the_worker():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_single_fov(mpc)

    assert mpc.dry_run is False
    mpc.set_dry_run(True)
    assert mpc.dry_run is True

    mpc, tt, worker, _elapsed, scope2 = _run_time_lapse(nt=1, dt=0, dry_run=True)
    assert worker._dry_run is True
    scope.close()
    scope2.close()


def test_dry_run_never_opens_the_illumination():
    """The load-bearing safety test.

    A dry run must not open the light on ANY path.  If this regresses, an operator running
    a timing simulation silently photobleaches their sample with no error message.
    """
    mpc, tt, worker, _elapsed, scope = _run_time_lapse(nt=1, dt=0, dry_run=True, spy=True)
    spy = tt.illumination_spy
    spy.restore()

    assert spy.lc_on == 0, f"LiveController.turn_on_illumination called {spy.lc_on}x during a dry run"
    assert spy.mcu_on == 0, f"Microcontroller.turn_on_illumination called {spy.mcu_on}x during a dry run"
    # Every frame still pays the round trip -- twice, since the "on" is substituted by an "off".
    assert spy.lc_off > 0, "the equal-cost substitute command was never issued"
    scope.close()


def test_real_run_does_open_the_illumination():
    """Counterpart to the dry-run test: proves the spy would have caught a leak."""
    mpc, tt, worker, _elapsed, scope = _run_time_lapse(nt=1, dt=0, dry_run=False, spy=True)
    spy = tt.illumination_spy
    spy.restore()

    assert spy.lc_on > 0, "a normal acquisition must open the illumination"
    assert spy.lc_on == spy.lc_off, f"illumination left unbalanced: {spy.lc_on} on vs {spy.lc_off} off"
    scope.close()


def _run_probe(mpc, timeout_s: float = 120):
    """Run the timing probe and block until it reports back."""
    done = threading.Event()
    captured = {}

    def on_finished(result):
        captured["result"] = result
        done.set()

    refusal = mpc.run_timing_probe(on_finished=on_finished)
    assert refusal is None, f"probe refused unexpectedly: {refusal}"
    assert done.wait(timeout_s), "timing probe never finished"
    return captured["result"]


def _configure_probeable(mpc, scope, n_x: int = 3, n_y: int = 1):
    """A valid, probeable acquisition: autofocus off, several FOVs, a scratch save path."""
    mpc_tests.select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_af_flag(False)
    mpc.set_reflection_af_flag(False)
    mpc.set_NZ(1)
    mpc.set_base_path(tempfile.mkdtemp(prefix="probe_test_"))
    mpc.scanCoordinates.clear_regions()

    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, n_x, n_y, 0)
    return mpc


def test_timing_probe_runs_the_first_two_planned_fovs():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)
    assert len(mpc._flatten_planned_fovs()) >= 2

    result = _run_probe(mpc)

    assert result.ok, result.note
    assert result.n_fovs_probed == 2
    assert result.per_fov_s > 0
    # The measured FOV is the second one, so its move is a real inter-FOV hop.
    assert result.move_s is not None and result.acquire_s is not None
    scope.close()


def test_timing_probe_does_not_mutate_the_scan_plan():
    """run_acquisition applies the focus map by mutating ScanCoordinates; a probe must not."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    before_fovs = copy.deepcopy(mpc.scanCoordinates.region_fov_coordinates)
    before_centers = copy.deepcopy(mpc.scanCoordinates.region_centers)

    _run_probe(mpc)

    assert mpc.scanCoordinates.region_fov_coordinates == before_fovs
    assert mpc.scanCoordinates.region_centers == before_centers
    scope.close()


def test_timing_probe_cleans_up_after_itself():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)
    mpc.set_Nt(7)
    mpc.set_deltat(11)
    before = {"Nt": mpc.Nt, "deltat": mpc.deltat, "experiment_ID": mpc.experiment_ID}

    _run_probe(mpc)

    assert mpc.Nt == before["Nt"], "probe must restore Nt"
    assert mpc.deltat == before["deltat"], "probe must restore deltat"
    assert mpc.experiment_ID == before["experiment_ID"], "probe must restore experiment_ID"
    assert mpc.dry_run is False, "probe must clear the dry_run flag"
    leftovers = [n for n in os.listdir(mpc.base_path) if n.startswith(mpc._TIMING_PROBE_DIR_PREFIX)]
    assert leftovers == [], f"probe left folders behind: {leftovers}"
    scope.close()


def test_timing_probe_never_opens_the_illumination():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    spy = _IlluminationSpy(mpc.liveController, scope.low_level_drivers.microcontroller)
    try:
        result = _run_probe(mpc)
    finally:
        spy.restore()

    assert result.ok, result.note
    assert spy.lc_on == 0, f"LiveController.turn_on_illumination called {spy.lc_on}x during a probe"
    assert spy.mcu_on == 0, f"Microcontroller.turn_on_illumination called {spy.mcu_on}x during a probe"
    scope.close()


def test_illumination_fuse_raises_then_restores():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    with mpc._illumination_fuse():
        with pytest.raises(RuntimeError, match="attempted to open illumination"):
            mpc.liveController.turn_on_illumination()
        with pytest.raises(RuntimeError, match="attempted to open illumination"):
            scope.low_level_drivers.microcontroller.turn_on_illumination()

    # Restored to the real bound methods, not left shadowed by the raising stubs.
    assert inspect.ismethod(mpc.liveController.turn_on_illumination)
    assert inspect.ismethod(scope.low_level_drivers.microcontroller.turn_on_illumination)
    scope.close()


def test_timing_probe_refusals():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    # Nothing configured yet: no channels, no FOVs.
    mpc.scanCoordinates.clear_regions()
    mpc.set_selected_configurations([])
    assert mpc.timing_probe_refusal_reason() is not None

    _configure_probeable(mpc, scope)
    assert mpc.timing_probe_refusal_reason() is None

    # Contrast autofocus cannot run dark.
    mpc.set_af_flag(True)
    mpc.set_reflection_af_flag(False)
    reason = mpc.timing_probe_refusal_reason()
    assert reason is not None and "Contrast autofocus" in reason
    # ...and run_timing_probe refuses rather than starting a thread.
    assert mpc.run_timing_probe(on_finished=lambda _r: None) == reason
    mpc.set_af_flag(False)

    mpc.protocol_info = {"name": "demo", "round": "R01"}
    assert mpc.timing_probe_refusal_reason() is not None
    mpc.protocol_info = None

    assert mpc.timing_probe_refusal_reason() is None
    scope.close()


def test_measurement_feeds_the_estimate():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    modelled = mpc.get_time_point_estimate()
    assert modelled.measured is False
    assert modelled.n_fovs == len(mpc._flatten_planned_fovs())

    mpc.set_measured_per_fov_s(7.0)
    measured = mpc.get_time_point_estimate()

    assert measured.measured is True
    assert measured.per_fov_s == 7.0
    assert measured.seconds == pytest.approx(measured.n_fovs * 7.0 + mpc._EST_TIME_POINT_OVERHEAD_S)
    # The scalar accessor follows the measurement too.
    assert mpc.get_estimated_time_point_duration_s() == pytest.approx(measured.seconds)
    scope.close()


def test_measurement_is_discarded_when_the_per_fov_cost_changes():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    for mutate in (
        lambda: mpc.set_NZ(mpc.NZ + 1),
        lambda: mpc.set_deltaZ(mpc.deltaZ * 1000 + 1),
        lambda: mpc.set_reflection_af_flag(not mpc.do_reflection_af),
        lambda: mpc.set_overlap_percent(mpc.overlap_percent + 5),
        lambda: mpc.set_skip_saving(not mpc.skip_saving),
        lambda: mpc.set_base_path(tempfile.mkdtemp(prefix="probe_other_")),
    ):
        mpc.set_measured_per_fov_s(7.0)
        assert mpc.get_time_point_estimate().measured is True
        mutate()
        assert mpc.get_time_point_estimate().measured is False, f"stale measurement survived {mutate}"

    # Changing an exposure time must also invalidate it.
    mpc.set_measured_per_fov_s(7.0)
    assert mpc.get_time_point_estimate().measured is True
    mpc.selected_configurations[0].exposure_time = mpc.selected_configurations[0].exposure_time + 10
    assert mpc.get_time_point_estimate().measured is False
    scope.close()


def test_measurement_survives_adding_fovs():
    """per_fov_s scales out of the model, so more wells must NOT discard the measurement.

    This is the edit where a good estimate matters most -- growing the plate is exactly
    when the operator needs to know the loop no longer fits.
    """
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    mpc.set_measured_per_fov_s(7.0)
    before = mpc.get_time_point_estimate()
    assert before.measured is True

    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(99, x_min + 2, y_min + 2, z_mid, 3, 1, 0)

    after = mpc.get_time_point_estimate()
    assert after.measured is True, "adding FOVs must not discard a valid per-FOV measurement"
    assert after.n_fovs > before.n_fovs
    assert after.seconds > before.seconds
    scope.close()


def test_clearing_the_measurement_falls_back_to_the_model():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    _configure_probeable(mpc, scope)

    modelled_seconds = mpc.get_time_point_estimate().seconds
    mpc.set_measured_per_fov_s(7.0)
    assert mpc.get_time_point_estimate().measured is True

    mpc.clear_measured_per_fov_s()
    fallback = mpc.get_time_point_estimate()
    assert fallback.measured is False
    assert fallback.seconds == pytest.approx(modelled_seconds)
    scope.close()


def test_probe_result_usable_only_when_representative():
    from control.core.multi_point_controller import TimingProbeResult

    assert TimingProbeResult(ok=True, per_fov_s=7.0).usable()
    assert not TimingProbeResult(ok=False, per_fov_s=7.0).usable()
    assert not TimingProbeResult(ok=True, per_fov_s=7.0, aborted=True).usable()
    assert not TimingProbeResult(ok=True, per_fov_s=7.0, laser_af_failures=1).usable()
    assert not TimingProbeResult(ok=True, per_fov_s=None).usable()


def test_all_time_points_run_when_rounds_overrun_dt():
    """The regression: a round slower than dt must NOT cost us the remaining time points.

    dt is deliberately tiny so every round overruns it.  The old skip-if-behind logic
    turned this into a single time point.
    """
    nt = 3
    mpc, tt, worker, _elapsed, scope = _run_time_lapse(nt=nt, dt=0.01)

    # get_acquisition_image_count() multiplies by Nt, so this only holds if every round ran.
    assert tt.image_count == mpc.get_acquisition_image_count()
    assert worker.time_point == nt, "time_point must land exactly on Nt, never overshoot"
    assert worker._late_time_points > 0, "rounds should have been recorded as late"

    # Every time point must have produced its own output folder.
    for time_point in range(nt):
        folder = os.path.join(worker.experiment_path, f"{time_point:0{control._def.FILE_ID_PADDING}}")
        assert os.path.isdir(folder), f"missing output folder for time point {time_point}"
        assert os.path.isfile(os.path.join(folder, "coordinates.csv"))

    scope.close()


def test_continuous_mode_runs_all_time_points_without_waiting():
    """dt == 0 is continuous acquisition: all rounds, back to back, nothing flagged late."""
    nt = 2
    mpc, tt, worker, _elapsed, scope = _run_time_lapse(nt=nt, dt=0)

    assert tt.image_count == mpc.get_acquisition_image_count()
    assert worker.time_point == nt
    assert worker._late_time_points == 0, "continuous mode has no interval to run past"

    scope.close()


def test_start_to_start_pacing_is_honored_when_rounds_are_fast():
    """A dt longer than the round must actually delay the next round by the remainder."""
    nt = 2
    dt = 6.0
    mpc, tt, worker, elapsed, scope = _run_time_lapse(nt=nt, dt=dt)

    assert tt.image_count == mpc.get_acquisition_image_count()
    assert worker.time_point == nt

    if worker._late_time_points == 0:
        # Rounds fit inside dt, so round 2 started one full dt after the acquisition began.
        assert elapsed >= dt, f"start-to-start pacing not honored: {elapsed=} < {dt=}"
    else:
        pytest.skip("simulated rounds were slower than dt; pacing not exercised")

    scope.close()


def test_time_point_duration_estimate_scales_with_acquisition_size():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    mpc.set_reflection_af_flag(False)
    mpc.set_af_flag(False)
    all_configuration_names = _configure_single_fov(mpc, nz=1, n_channels=1)

    one_fov_one_z = mpc.get_estimated_time_point_duration_s()
    assert one_fov_one_z > 0

    # More z levels costs strictly more, and the per-z cost is linear.
    mpc.set_NZ(2)
    two_z = mpc.get_estimated_time_point_duration_s()
    mpc.set_NZ(3)
    three_z = mpc.get_estimated_time_point_duration_s()
    assert two_z > one_fov_one_z
    assert three_z - two_z == pytest.approx(two_z - one_fov_one_z, rel=1e-6)

    # More FOVs costs strictly more.
    mpc.set_NZ(1)
    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    mpc.scanCoordinates.add_flexible_region(2, x_min + 1, y_min + 1, z_mid, 1, 1, 0)
    assert mpc.get_estimated_time_point_duration_s() > one_fov_one_z

    # Autofocus costs strictly more.
    before_af = mpc.get_estimated_time_point_duration_s()
    mpc.set_reflection_af_flag(True)
    assert mpc.get_estimated_time_point_duration_s() > before_af

    scope.close()


def test_time_point_duration_estimate_requires_configuration():
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    mpc.scanCoordinates.clear_regions()
    mpc.set_selected_configurations([])
    with pytest.raises(ValueError):
        mpc.get_estimated_time_point_duration_s()

    scope.close()


def test_pacing_dialog_is_skipped_when_there_is_nothing_to_warn_about():
    """The helper must return True without ever constructing a dialog in these cases.

    Qt is not driven here on purpose: every branch under test returns before the
    QMessageBox call, so reaching one would raise instead of silently passing.
    """
    from control.widgets import check_time_lapse_pacing_with_dialog

    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test_pacing_dialog")

    _configure_single_fov(mpc, nz=1, n_channels=1)
    mpc.set_reflection_af_flag(False)
    mpc.set_af_flag(False)

    # Single time point: no interval to pace against.
    mpc.set_Nt(1)
    mpc.set_deltat(10)
    assert check_time_lapse_pacing_with_dialog(mpc, logger) is True

    # Continuous mode: dt == 0 means "as fast as possible", nothing to warn about.
    mpc.set_Nt(5)
    mpc.set_deltat(0)
    assert check_time_lapse_pacing_with_dialog(mpc, logger) is True

    # dt comfortably larger than the estimate: the run fits its schedule.
    mpc.set_deltat(mpc.get_estimated_time_point_duration_s() * 100)
    assert check_time_lapse_pacing_with_dialog(mpc, logger) is True

    # An estimator failure must not block the acquisition.
    mpc.set_deltat(0.001)
    mpc.scanCoordinates.clear_regions()  # makes get_estimated_time_point_duration_s raise
    assert check_time_lapse_pacing_with_dialog(mpc, logger) is True

    scope.close()


def test_time_point_duration_estimate_matches_reference_run():
    """The constants are calibrated against a real run; keep them honest.

    Reference: 4 FOVs x 10 z x 1 channel @ 500 ms exposure, laser AF on, took 31.5 s
    (Downloads\\z_and_time_2026-07-31_15-56-18.823673).
    """
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)

    all_configuration_names = _configure_single_fov(mpc, nz=10, n_channels=1)
    mpc.set_reflection_af_flag(True)
    mpc.set_af_flag(False)
    mpc.set_use_piezo(False)
    mpc.selected_configurations[0].exposure_time = 500.0

    # Add 3 more single-FOV regions to reach the reference run's 4 FOVs.
    x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
    y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
    z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
    for i in range(1, 4):
        mpc.scanCoordinates.add_flexible_region(i + 1, x_min + i, y_min + i, z_mid, 1, 1, 0)

    estimated = mpc.get_estimated_time_point_duration_s()
    # Within 30% of the measured 31.5 s -- the docstring's stated accuracy.
    assert estimated == pytest.approx(31.5, rel=0.3)

    scope.close()
