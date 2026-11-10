"""Time-lapse pacing: the time-point estimate, the pacing dialog decision, the no-illumination
timing probe, and a time lapse that never skips a time point when a round overruns its interval."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def
import control.microscope
import control.widgets
import squid.acquisition_state
import tests.control.test_stubs as ts
from control._def import TriggerMode
from control.core.multi_point_controller import TimePointEstimate, TimingProbeResult
from control.core.multi_point_worker import MultiPointWorker
from control.widgets import PacingChoice, pacing_message, probe_note_for, time_lapse_pacing_choice
from tests.control.test_MultiPointController import TestAcquisitionTracker


def _estimate(seconds, measured=False):
    return TimePointEstimate(seconds=seconds, measured=measured, per_fov_s=seconds / 4, n_fovs=4)


# ---- the message and the decision ----


@pytest.mark.parametrize(
    "dt, seconds, measured, expected",
    [
        (60, 31.4, False, "Interval 60 s. Est: 31 s. Proceed?"),
        (60, 31.4, True, "Interval 60 s. Measured: 31 s. Proceed?"),
        (30, 45.0, False, "Estimated loop interval exceeds 30 s. Est: 45 s. Proceed?"),
        (30, 45.0, True, "Measured loop interval exceeds 30 s. Measured: 45 s. Proceed?"),
    ],
)
def test_pacing_message(dt, seconds, measured, expected):
    assert pacing_message(dt, _estimate(seconds, measured)) == expected


def test_pacing_message_leads_with_a_failed_simulation():
    assert pacing_message(60, _estimate(31.4), "Simulation failed.") == "Simulation failed. Est: 31 s. Proceed?"


@pytest.mark.parametrize(
    "result, note",
    [
        (TimingProbeResult(ok=True, per_fov_s=2.0), ""),
        (TimingProbeResult(ok=True, per_fov_s=2.0, aborted=True), "Simulation stopped."),
        (TimingProbeResult(ok=False), "Simulation failed."),
        (TimingProbeResult(ok=True, per_fov_s=None), "Simulation failed."),
        (TimingProbeResult(ok=True, per_fov_s=2.0, laser_af_failures=1), "Simulation AF failed."),
    ],
)
def test_probe_note_and_usability(result, note):
    assert probe_note_for(result) == note
    assert result.usable() is (note == "")


class _FakeBox:
    """Stands in for QMessageBox: records its buttons and 'clicks' the one named by `click`."""

    Warning, Question, ActionRole, AcceptRole, RejectRole = range(5)
    click = "Proceed"
    last = None

    def __init__(self, parent=None):
        self.buttons = []
        self.text = None
        self.icon = None
        _FakeBox.last = self

    def setIcon(self, icon):
        self.icon = icon

    def setWindowTitle(self, title):
        pass

    def setText(self, text):
        self.text = text

    def addButton(self, label, role):
        self.buttons.append(label)
        return label

    def setDefaultButton(self, button):
        pass

    def exec_(self):
        pass

    def clickedButton(self):
        return self.click if self.click in self.buttons else None


def _pacing_controller(nt=5, dt=60.0, seconds=31.0, measured=False, refusal=None):
    controller = MagicMock()
    controller.Nt = nt
    controller.deltat = dt
    controller.get_time_point_estimate.return_value = _estimate(seconds, measured)
    controller.timing_probe_refusal_reason.return_value = refusal
    return controller


@pytest.fixture
def fake_box(monkeypatch):
    monkeypatch.setattr(control.widgets, "QMessageBox", _FakeBox)
    _FakeBox.last = None
    return _FakeBox


@pytest.mark.parametrize("nt, dt", [(1, 60.0), (5, 0.0)])
def test_no_dialog_without_a_time_lapse_to_pace(fake_box, nt, dt):
    assert time_lapse_pacing_choice(_pacing_controller(nt=nt, dt=dt), MagicMock()) is PacingChoice.PROCEED
    assert fake_box.last is None


def test_no_dialog_when_the_estimate_cannot_be_made(fake_box):
    controller = _pacing_controller()
    controller.get_time_point_estimate.side_effect = ValueError("not configured")
    assert time_lapse_pacing_choice(controller, MagicMock()) is PacingChoice.PROCEED
    assert fake_box.last is None


@pytest.mark.parametrize(
    "click, expected",
    [("Proceed", PacingChoice.PROCEED), ("Simulate", PacingChoice.SIMULATE), ("Cancel", PacingChoice.CANCEL)],
)
def test_dialog_is_shown_for_every_time_lapse_even_when_the_estimate_fits(fake_box, monkeypatch, click, expected):
    monkeypatch.setattr(fake_box, "click", click)

    assert time_lapse_pacing_choice(_pacing_controller(seconds=31.0, dt=60.0), MagicMock()) is expected

    assert fake_box.last.buttons == ["Simulate", "Proceed", "Cancel"]
    assert fake_box.last.icon == fake_box.Question
    assert fake_box.last.text == "Interval 60 s. Est: 31 s. Proceed?"


def test_dialog_warns_when_the_round_will_overrun_and_offers_to_resimulate(fake_box):
    controller = _pacing_controller(seconds=90.0, dt=60.0, measured=True)
    assert time_lapse_pacing_choice(controller, MagicMock()) is PacingChoice.PROCEED  # advisory, never a veto
    assert fake_box.last.icon == fake_box.Warning
    assert fake_box.last.buttons == ["Re-simulate", "Proceed", "Cancel"]


def test_simulate_is_not_offered_when_the_probe_would_be_refused(fake_box):
    controller = _pacing_controller(refusal="Contrast autofocus needs illumination")
    time_lapse_pacing_choice(controller, MagicMock())
    assert fake_box.last.buttons == ["Proceed", "Cancel"]


# ---- the two tabs ----


def _tab_fake():
    fake = MagicMock()
    fake.base_path_is_set = True
    fake.performance_mode = False
    fake.location_list = [object()]
    fake.acquisition_in_place = False
    fake.multipointController.acquisition_in_progress.return_value = False
    fake.configure_controller_from_ui.return_value = None
    fake.checkbox_skipSaving.isChecked.return_value = True
    fake._probe_note = "Simulation failed."
    return fake


_TABS = [control.widgets.FlexibleMultiPointWidget, control.widgets.WellplateMultiPointWidget]


@pytest.fixture
def enough_ram(monkeypatch):
    monkeypatch.setattr(control.widgets, "mosaic_ram_exceeds_available", lambda *a, **kw: False)


@pytest.mark.parametrize("widget_class", _TABS)
def test_unattended_start_skips_the_pacing_dialog(widget_class, monkeypatch, enough_ram):
    monkeypatch.setattr(
        control.widgets, "time_lapse_pacing_choice", lambda *a, **kw: pytest.fail("no dialog for an unattended start")
    )
    fake = _tab_fake()

    widget_class.toggle_acquisition(fake, True, interactive=False)

    fake.multipointController.start_new_experiment.assert_called_once()
    fake.multipointController.run_acquisition.assert_called_once()


@pytest.mark.parametrize("widget_class", _TABS)
def test_proceed_starts_the_run_and_passes_the_probe_note_to_the_dialog(widget_class, monkeypatch, enough_ram):
    seen = []

    def choice(controller, log, parent, probe_note):
        seen.append(probe_note)
        return PacingChoice.PROCEED

    monkeypatch.setattr(control.widgets, "time_lapse_pacing_choice", choice)
    fake = _tab_fake()

    widget_class.toggle_acquisition(fake, True)

    assert seen == ["Simulation failed."]
    assert fake._probe_note == ""  # shown once
    fake.multipointController.run_acquisition.assert_called_once()


@pytest.mark.parametrize("widget_class", _TABS)
@pytest.mark.parametrize("choice", [PacingChoice.CANCEL, PacingChoice.SIMULATE])
def test_cancel_and_simulate_leave_no_experiment_folder(widget_class, choice, monkeypatch, enough_ram):
    monkeypatch.setattr(control.widgets, "time_lapse_pacing_choice", lambda *a, **kw: choice)
    fake = _tab_fake()

    widget_class.toggle_acquisition(fake, True)

    fake.multipointController.start_new_experiment.assert_not_called()
    fake.multipointController.run_acquisition.assert_not_called()
    fake.btn_startAcquisition.setChecked.assert_called_with(False)
    assert fake._start_timing_simulation.called is (choice is PacingChoice.SIMULATE)


def test_cancel_on_the_flexible_tab_removes_the_location_the_start_added(monkeypatch, enough_ram):
    monkeypatch.setattr(control.widgets, "time_lapse_pacing_choice", lambda *a, **kw: PacingChoice.CANCEL)
    fake = _tab_fake()
    fake.location_list = []  # empty list: the start adds the current position

    control.widgets.FlexibleMultiPointWidget.toggle_acquisition(fake, True)

    fake.add_location.assert_called_once()
    fake.clear_only_location_list.assert_called_once()
    assert fake.acquisition_in_place is False


def _simulation_host():
    host = SimpleNamespace(
        multipointController=MagicMock(),
        _log=MagicMock(),
        btn_startAcquisition=MagicMock(),
        setEnabled_all=MagicMock(),
        toggle_acquisition=MagicMock(),
        _hide_simulation_dialog=MagicMock(),
        _show_simulation_dialog=MagicMock(),
    )
    control.widgets._TimingSimulationMixin._init_timing_simulation(host)
    return host


def test_a_usable_probe_result_becomes_the_measurement_and_reopens_the_dialog():
    host = _simulation_host()
    host._timing_simulation_is_mine = True

    control.widgets._TimingSimulationMixin._on_timing_probe_finished(
        host, TimingProbeResult(ok=True, per_fov_s=2.5, n_fovs_probed=2)
    )

    host.multipointController.set_measured_per_fov_s.assert_called_once()
    assert host.multipointController.set_measured_per_fov_s.call_args[0][0] == 2.5
    assert host._probe_note == ""
    host.setEnabled_all.assert_called_once_with(True)
    host.toggle_acquisition.assert_called_once_with(True)


def test_an_unusable_probe_result_is_explained_in_the_next_dialog_not_recorded():
    host = _simulation_host()
    host._timing_simulation_is_mine = True

    control.widgets._TimingSimulationMixin._on_timing_probe_finished(
        host, TimingProbeResult(ok=True, per_fov_s=2.5, aborted=True)
    )

    host.multipointController.set_measured_per_fov_s.assert_not_called()
    assert host._probe_note == "Simulation stopped."
    host.toggle_acquisition.assert_called_once_with(True)


def test_a_probe_launched_by_the_other_tab_is_ignored():
    host = _simulation_host()
    control.widgets._TimingSimulationMixin._on_timing_probe_finished(host, TimingProbeResult(ok=True, per_fov_s=2.5))
    host.toggle_acquisition.assert_not_called()
    host.setEnabled_all.assert_not_called()


def test_a_probe_that_cannot_start_re_enables_the_tab():
    host = _simulation_host()
    host.multipointController.timing_probe_refusal_reason.return_value = None
    host.multipointController.run_timing_probe.side_effect = RuntimeError("stage error")
    host._on_timing_probe_finished = lambda result: control.widgets._TimingSimulationMixin._on_timing_probe_finished(
        host, result
    )

    control.widgets._TimingSimulationMixin._start_timing_simulation(host)

    assert host.setEnabled_all.call_args_list[-1].args == (True,)
    assert host._probe_note == "Simulation failed."
    assert host._timing_simulation_is_mine is False


# ---- the estimate and the probe, on a simulated microscope ----


@pytest.fixture
def mpc():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = ts.get_test_multi_point_controller(microscope=scope)
    names = [c.name for c in controller.liveController.get_channels(scope.objective_store.current_objective)]
    controller.set_selected_configurations(names[:2])
    controller.scanCoordinates.clear_regions()
    x = controller.stage.get_config().X_AXIS.MIN_POSITION + 20
    y = controller.stage.get_config().Y_AXIS.MIN_POSITION + 20
    for i in range(3):
        controller.scanCoordinates.add_single_fov_region(f"R{i}", center_x=x + i, center_y=y, center_z=1.0)
    return controller


def test_modelled_estimate_scales_with_the_work_per_time_point(mpc):
    base = mpc.get_time_point_estimate()
    assert not base.measured
    assert base.n_fovs == 3
    assert base.seconds == pytest.approx(3 * base.per_fov_s + mpc._EST_TIME_POINT_OVERHEAD_S)
    assert mpc.get_estimated_time_point_duration_s() == base.seconds

    mpc.set_NZ(5)
    assert mpc.get_time_point_estimate().seconds > base.seconds
    mpc.set_NZ(1)
    mpc.set_reflection_af_flag(True)
    assert mpc.get_time_point_estimate().per_fov_s == pytest.approx(base.per_fov_s + mpc._EST_LASER_AF_S)


def test_estimate_needs_fovs_and_channels(mpc):
    mpc.scanCoordinates.clear_regions()
    with pytest.raises(ValueError):
        mpc.get_time_point_estimate()


def test_measured_per_fov_time_is_used_until_a_setting_that_affects_it_changes(mpc):
    mpc.set_measured_per_fov_s(2.0)

    estimate = mpc.get_time_point_estimate()
    assert estimate.measured
    assert estimate.seconds == pytest.approx(3 * 2.0 + mpc._EST_TIME_POINT_OVERHEAD_S)

    # More FOVs: the measurement still applies, and that is where it matters most.
    x = mpc.stage.get_config().X_AXIS.MIN_POSITION + 30
    mpc.scanCoordinates.add_single_fov_region("R9", center_x=x, center_y=x, center_z=1.0)
    more = mpc.get_time_point_estimate()
    assert more.measured and more.seconds == pytest.approx(4 * 2.0 + mpc._EST_TIME_POINT_OVERHEAD_S)

    # A different stack is different work per FOV: the stale number is dropped, not shown as fresh.
    mpc.set_NZ(7)
    assert not mpc.get_time_point_estimate().measured
    mpc.set_NZ(1)
    assert mpc.get_time_point_estimate().measured

    mpc.set_z_stack_order("Z FIRST")
    assert not mpc.get_time_point_estimate().measured
    mpc.set_z_stack_order("CHANNEL FIRST")

    mpc.clear_measured_per_fov_s()
    assert not mpc.get_time_point_estimate().measured


def test_probe_is_refused_where_it_could_not_run_dark_or_would_mislead(mpc, monkeypatch):
    assert mpc.timing_probe_refusal_reason() is None

    mpc.set_af_flag(True)
    assert "Contrast autofocus" in mpc.timing_probe_refusal_reason()
    mpc.set_af_flag(False)

    # Laser AF without a reference (whether one is cached depends on the machine's profiles).
    monkeypatch.setattr(mpc, "validate_acquisition_settings", lambda: False)
    assert "reference" in mpc.timing_probe_refusal_reason()
    monkeypatch.setattr(mpc, "validate_acquisition_settings", lambda: True)

    mpc.start_blocker = lambda: "Z lift is moving"
    assert mpc.timing_probe_refusal_reason() == "Z lift is moving"
    mpc.start_blocker = None

    monkeypatch.setattr(mpc.liveController, "trigger_mode", TriggerMode.HARDWARE)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", control._def.HardwareTriggerMode.LEVEL)
    assert "LEVEL" in mpc.timing_probe_refusal_reason()
    monkeypatch.setattr(mpc.liveController, "trigger_mode", TriggerMode.SOFTWARE)

    monkeypatch.setattr(mpc.microscope.addons, "nl5", object())
    monkeypatch.setattr(control._def, "NL5_USE_DOUT", True)
    assert "NL5" in mpc.timing_probe_refusal_reason()
    monkeypatch.setattr(mpc.microscope.addons, "nl5", None)

    mpc.scanCoordinates.clear_regions()
    assert mpc.timing_probe_refusal_reason() == "No FOVs planned."
    assert mpc.run_timing_probe(on_finished=lambda result: pytest.fail("refused probe must not run")) is not None


def _run_probe(mpc, timeout_s=60):
    results = []
    done = threading.Event()

    def on_finished(result):
        results.append(result)
        done.set()

    assert mpc.run_timing_probe(on_finished) is None
    assert done.wait(timeout_s), "the timing probe did not finish"
    return results[0]


@pytest.mark.parametrize(
    "file_saving_option", [control._def.FileSavingOption.INDIVIDUAL_IMAGES, control._def.FileSavingOption.ZARR_V3]
)
def test_probe_measures_two_fovs_dark_and_leaves_nothing_behind(mpc, monkeypatch, file_saving_option):
    monkeypatch.setattr(control._def, "FILE_SAVING_OPTION", file_saving_option)
    mpc.set_Nt(10)
    mpc.set_deltat(30)
    experiment_id = mpc.experiment_ID
    mpc.stage.move_z_to(1.5)  # a z inside the travel limits, so "back to the start" is reachable
    start = mpc.stage.get_pos()
    planned = {k: list(v) for k, v in mpc.scanCoordinates.region_fov_coordinates.items()}
    before = sorted(os.listdir(mpc.base_path))
    callbacks_fired = []
    mpc.callbacks = TestAcquisitionTracker().get_callbacks()
    mpc.callbacks.signal_acquisition_finished = lambda: callbacks_fired.append("finished")

    result = _run_probe(mpc)

    assert result.ok and result.usable(), result
    assert result.n_fovs_probed == 2
    assert result.per_fov_s == pytest.approx(result.move_s + result.acquire_s)
    assert result.per_fov_s > 0
    # result.ok also means nothing tried to open the light: the fuse would have aborted the probe.
    assert callbacks_fired == []  # no acquisition-finished fan-out for a probe

    # Nothing of the probe outlives it: no folder, no partial output, no watchdog run state.
    assert sorted(os.listdir(mpc.base_path)) == before
    assert squid.acquisition_state.read_run() is None
    # The controller is as the operator left it.
    assert (mpc.Nt, mpc.deltat, mpc.experiment_ID, mpc.dry_run) == (10, 30, experiment_id, False)
    assert {k: list(v) for k, v in mpc.scanCoordinates.region_fov_coordinates.items()} == planned
    assert not mpc.acquisition_in_progress()
    end = mpc.stage.get_pos()
    assert (end.x_mm, end.y_mm, end.z_mm) == pytest.approx((start.x_mm, start.y_mm, start.z_mm), abs=1e-3)
    # The fuse is gone again: the illumination methods are the real ones.
    for obj in (mpc.liveController, mpc.microcontroller, mpc.microscope.illumination_controller):
        assert "turn_on_illumination" not in vars(obj)


def test_illumination_fuse_turns_any_attempt_to_open_the_light_into_an_error(mpc):
    fused = (mpc.liveController, mpc.microcontroller, mpc.microscope.illumination_controller)
    with mpc._illumination_fuse():
        for obj in fused:
            with pytest.raises(RuntimeError, match="attempted to open illumination"):
                obj.turn_on_illumination()
        mpc.liveController.turn_off_illumination()  # turning the light off stays possible
    for obj in fused:
        assert "turn_on_illumination" not in vars(obj)

    # Restored even when the block fails.
    with pytest.raises(ValueError):
        with mpc._illumination_fuse():
            raise ValueError("probe failed")
    assert "turn_on_illumination" not in vars(mpc.liveController)


def test_a_dry_run_that_reaches_a_camera_triggering_scan_unit_is_stopped(monkeypatch):
    worker = SimpleNamespace(
        _dry_run=True,
        liveController=SimpleNamespace(trigger_mode=TriggerMode.HARDWARE),
        camera=MagicMock(),
        _config_already_applied=lambda config: True,
        _scan_unit_triggers_camera=lambda: True,
    )
    with pytest.raises(RuntimeError, match="no dry-run-safe path"):
        MultiPointWorker.acquire_camera_image(worker, MagicMock(), "id", "/tmp", 0, "A1", 0, 0)
    worker.camera.send_trigger.assert_not_called()


def test_stale_probe_folders_are_swept(mpc):
    stale = os.path.join(mpc.base_path, "_timing_probe_2020-01-01_00-00-00.000000")
    fresh = os.path.join(mpc.base_path, "_timing_probe_2999-01-01_00-00-00.000000")
    other = os.path.join(mpc.base_path, "my_experiment")
    for path in (stale, fresh, other):
        os.makedirs(path)
    os.utime(stale, (0, 0))

    mpc.sweep_stale_timing_probe_dirs()

    assert not os.path.exists(stale)
    assert os.path.isdir(fresh) and os.path.isdir(other)


# ---- the worker ----


def test_dry_run_frame_issues_the_off_command_instead_of_on():
    worker = SimpleNamespace(_dry_run=True, liveController=MagicMock())
    MultiPointWorker._illumination_on_for_frame(worker)
    worker.liveController.turn_off_illumination.assert_called_once()
    worker.liveController.turn_on_illumination.assert_not_called()

    worker = SimpleNamespace(_dry_run=False, liveController=MagicMock())
    MultiPointWorker._illumination_on_for_frame(worker)
    worker.liveController.turn_on_illumination.assert_called_once()


def _lateness_worker(nt=5, dt=60.0, done=1):
    return SimpleNamespace(
        Nt=nt,
        dt=dt,
        time_point=done,
        _late_time_points=0,
        _max_lateness_s=0.0,
        _total_lateness_s=0.0,
        _log=MagicMock(),
    )


def test_a_round_inside_its_interval_is_not_late():
    worker = _lateness_worker()
    assert (
        MultiPointWorker._record_time_point_lateness(worker, next_start=1060.0, time_point_start=1000.0, now=1031.0) < 0
    )
    assert (worker._late_time_points, worker._max_lateness_s, worker._total_lateness_s) == (0, 0.0, 0.0)
    worker._log.warning.assert_not_called()


def test_overruns_are_counted_with_their_worst_and_total():
    worker = _lateness_worker()
    MultiPointWorker._record_time_point_lateness(worker, next_start=1060.0, time_point_start=1000.0, now=1075.0)
    worker.time_point = 2
    MultiPointWorker._record_time_point_lateness(worker, next_start=1120.0, time_point_start=1075.0, now=1125.0)

    assert worker._late_time_points == 2
    assert worker._max_lateness_s == pytest.approx(15.0)
    assert worker._total_lateness_s == pytest.approx(20.0)
    assert "no time points are skipped" in worker._log.warning.call_args_list[0].args[0]

    worker.time_point = 5  # the last one
    MultiPointWorker._record_time_point_lateness(worker, next_start=1300.0, time_point_start=1125.0, now=1301.0)
    assert "last time point" in worker._log.warning.call_args_list[-1].args[0]


def test_time_lapse_acquires_every_time_point_when_every_round_overruns(mpc):
    tracker = TestAcquisitionTracker()
    mpc.callbacks = tracker.get_callbacks()
    mpc.set_Nt(3)
    mpc.set_deltat(0.01)  # far shorter than a round: the old code skipped time points here

    assert mpc.run_acquisition()
    assert tracker.finished_event.wait(60)

    experiment_dir = os.path.join(mpc.base_path, mpc.experiment_ID)
    time_point_dirs = sorted(d for d in os.listdir(experiment_dir) if d.isdigit())
    assert [int(d) for d in time_point_dirs] == [0, 1, 2]
    assert tracker.image_count == mpc.get_acquisition_image_count()
    stats = squid.acquisition_state.read_run()["stats"]
    assert stats["total_timepoints"] == 3
    assert stats["late_timepoints"] == 3
    assert stats["max_lateness_seconds"] > 0
