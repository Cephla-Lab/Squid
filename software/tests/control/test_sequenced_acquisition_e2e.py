"""End to end, in simulation: a hardware-sequenced acquisition must deliver exactly the image set a
software-sequenced one does - same count, same order, same z / channel / file ids - through the
real MultiPointWorker, the simulated controller's sequencer and the simulated camera."""

import os
import threading

import pytest

import control._def
import control.core.multi_point_worker as multi_point_worker
import control.microscope
import control.sequencer_sim
import tests.control.test_stubs as ts
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.piezo import PiezoStage
from control.sequencer_program import SeqError, SeqState

FLUORESCENCE = ["Fluorescence 405 nm Ex", "Fluorescence 488 nm Ex"]
NZ = 3
DZ_UM = 1.5


class Tracker:
    def __init__(self):
        self.finished = threading.Event()
        self.images = []  # (z_index, channel name, file_id, z_piezo_um), in arrival order
        self.full = []  # (time_point, region, fov, z_index, channel name), in arrival order
        self.interventions = []  # messages asking the user to step in

    def callbacks(self) -> MultiPointControllerFunctions:
        return MultiPointControllerFunctions(
            signal_acquisition_start=lambda *a: None,
            signal_acquisition_finished=lambda *a: self.finished.set(),
            signal_new_image=self._image,
            signal_current_configuration=lambda *a: None,
            signal_current_fov=lambda *a: None,
            signal_overall_progress=lambda *a: None,
            signal_region_progress=lambda *a: None,
            signal_user_intervention_needed=self.interventions.append,
        )

    def _image(self, frame, info):
        self.images.append((info.z_index, info.configuration.name, info.file_id, info.z_piezo_um))
        self.full.append((info.time_point, info.region_id, info.fov, info.z_index, info.configuration.name))


@pytest.fixture
def sequencing_setup(monkeypatch):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", control._def.HardwareTriggerMode.LEVEL)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", True)
    monkeypatch.setattr(control._def, "ACQUISITION_MAX_PENDING_MB", 8000)  # simulated frames are 26 MP
    monkeypatch.setattr(control.sequencer_sim, "SPEED_UP_FACTOR", 50.0)


def saved_files(mpc) -> list:
    """Every file the acquisition wrote, relative to its experiment folder, sorted."""
    root = os.path.join(mpc.base_path, mpc.experiment_ID)
    found = []
    for directory, _, names in os.walk(root):
        found.extend(os.path.relpath(os.path.join(directory, name), root) for name in names)
    return sorted(found)


def run_acquisition(channels, *, sequenced: bool, monkeypatch, n_regions=1, nz=NZ, nt=1, binning=None):
    monkeypatch.setattr(control._def, "USE_HARDWARE_SEQUENCED_ACQUISITION", sequenced)
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mcu = scope.low_level_drivers.microcontroller
        scope.addons.piezo_stage = PiezoStage(
            mcu,
            {
                "OBJECTIVE_PIEZO_HOME_UM": 20,
                "OBJECTIVE_PIEZO_RANGE_UM": control._def.OBJECTIVE_PIEZO_RANGE_UM,
                "OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE": 5,
                "OBJECTIVE_PIEZO_FLIP_DIR": False,
            },
        )
        scope.addons.piezo_stage.home()
        tracker = Tracker()
        mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.callbacks())
        mpc.liveController.set_trigger_mode(control._def.TriggerMode.HARDWARE)
        if binning is not None:
            scope.camera.set_binning(*binning)  # small frames: many FOVs stay fast
        for index in range(n_regions):
            mpc.scanCoordinates.add_single_fov_region(
                f"region_{index + 1}",
                center_x=mpc.stage.get_config().X_AXIS.MIN_POSITION + 1.0 + 0.5 * index,
                center_y=mpc.stage.get_config().Y_AXIS.MIN_POSITION + 1.0,
                center_z=mpc.stage.get_config().Z_AXIS.MIN_POSITION + 1.0,
            )
        mpc.set_selected_configurations(selected_configurations_name=channels)
        mpc.set_use_piezo(True)
        mpc.set_NZ(nz)
        mpc.set_deltaZ(DZ_UM)
        mpc.set_Nt(nt)
        mpc.run_acquisition()
        assert tracker.finished.wait(300), "acquisition did not finish"
        tracker.files = saved_files(mpc)
        return tracker, mcu.seq_status, scope.addons.piezo_stage.position
    finally:
        scope.close()


def test_sequenced_and_software_sequenced_deliver_the_same_image_set(sequencing_setup, monkeypatch):
    software, software_status, _ = run_acquisition(FLUORESCENCE, sequenced=False, monkeypatch=monkeypatch)
    sequenced, status, piezo_um = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch)

    expected_order = [(z, name) for z in range(NZ) for name in FLUORESCENCE]
    assert [(z, name) for z, name, _, _ in software.images] == expected_order
    assert [(z, name, file_id) for z, name, file_id, _ in sequenced.images] == [
        (z, name, file_id) for z, name, file_id, _ in software.images
    ]
    # the piezo z recorded per image follows the stack, and the piezo is back where it started
    assert [z_um for _, _, _, z_um in sequenced.images] == pytest.approx(
        [20 + z * DZ_UM for z in range(NZ) for _ in FLUORESCENCE]
    )
    assert piezo_um == pytest.approx(20)

    # and it really was the controller that ran the stack
    assert status.state == SeqState.DONE and status.error == SeqError.NONE
    assert status.frames_fired == NZ * len(FLUORESCENCE)
    assert software_status.frames_fired == 0


def test_an_ineligible_acquisition_falls_back_to_software_sequencing(sequencing_setup, monkeypatch):
    # An LED-matrix channel cannot be strobed from an MCU TTL port.
    channels = ["BF LED matrix full", "Fluorescence 488 nm Ex"]
    tracker, status, _ = run_acquisition(channels, sequenced=True, monkeypatch=monkeypatch)
    assert [(z, name) for z, name, _, _ in tracker.images] == [(z, name) for z in range(NZ) for name in channels]
    assert status.frames_fired == 0  # the controller's sequencer was never used


def test_differing_channel_gains_do_not_matter_on_a_camera_without_analog_gain(sequencing_setup, monkeypatch):
    """The ORCA-Fusion BT has no analog gain (its driver raises NotImplementedError); the gain values
    in its channel configs mean nothing, so they must not push the acquisition to software sequencing."""

    def no_gain(*args, **kwargs):
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    real_run = multi_point_worker.MultiPointWorker.run

    def run_with_differing_gains(worker):
        # After construction (the simulated camera sets its own gain while it is built).
        for method in ("set_analog_gain", "get_analog_gain", "get_gain_range"):
            setattr(worker.camera, method, no_gain)
        for index, config in enumerate(worker.selected_configurations):
            config.analog_gain = 5.0 + index
        return real_run(worker)

    monkeypatch.setattr(multi_point_worker.MultiPointWorker, "run", run_with_differing_gains)

    tracker, status, _ = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch)

    assert status.frames_fired == NZ * len(FLUORESCENCE)  # it WAS sequenced
    assert [(z, name) for z, name, _, _ in tracker.images] == [(z, name) for z in range(NZ) for name in FLUORESCENCE]


def test_failed_bursts_are_discarded_and_the_fov_retried_until_the_third_attempt(sequencing_setup, monkeypatch):
    real = multi_point_worker.burst_failure_reason
    calls = []

    def fail_twice(*args, **kwargs):
        calls.append(1)
        return "injected: a frame was dropped inside the burst" if len(calls) <= 2 else real(*args, **kwargs)

    monkeypatch.setattr(multi_point_worker, "burst_failure_reason", fail_twice)
    tracker, status, _ = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch)

    assert len(calls) == 3  # retry, then retry again (Hongquan, 2026-09-20)
    # the two discarded bursts reached nobody: every image exactly once, in order
    assert [(z, name) for z, name, _, _ in tracker.images] == [(z, name) for z in range(NZ) for name in FLUORESCENCE]
    assert status.frames_fired == NZ * len(FLUORESCENCE)
    assert tracker.interventions == []


def test_a_third_failure_stops_the_acquisition_and_asks_the_user_to_intervene(sequencing_setup, monkeypatch):
    calls = []

    def always_fail(*args, **kwargs):
        calls.append(1)
        return "injected: the camera delivered only 5 of 6 frames"

    monkeypatch.setattr(multi_point_worker, "burst_failure_reason", always_fail)
    tracker, _, _ = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch)

    assert len(calls) == 3  # three attempts, then stop
    assert tracker.images == []  # nothing of a failed burst is ever dispatched
    assert len(tracker.interventions) == 1  # the user is asked exactly once
    message = tracker.interventions[0]
    assert "region_1" in message and "3 times" in message
    assert "the camera delivered only 5 of 6 frames" in message
    assert "restart the acquisition" in message


def test_a_run_the_controller_refuses_fails_at_once_not_after_the_frame_timeout(sequencing_setup, monkeypatch):
    """First bench contact (2026-09-20): the laser interlock was open, the controller refused SEQ_RUN
    with INTERLOCK_OPEN and fired nothing - and the host then waited ~10 s per attempt for the "last
    expected frame" of a burst that had never started. What can still arrive is bounded by what the
    controller FIRED, not by what was expected."""
    import time

    def refuse(sequencer, stack_start):
        sequencer._fail(SeqError.INTERLOCK_OPEN, 0)  # as the firmware does: Failed, nothing triggered

    monkeypatch.setattr(control.sequencer_sim.SimulatedSequencer, "_seq_run", refuse)

    started = time.time()
    tracker, status, _ = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch)
    elapsed = time.time() - started

    assert tracker.images == []
    assert len(tracker.interventions) == 1 and "INTERLOCK_OPEN" in tracker.interventions[0]
    assert status.frames_fired == 0
    # Three attempts. With the dead wait that is 3 x ~10 s on top of building the microscope.
    assert (
        elapsed < 20
    ), f"three refused attempts took {elapsed:.0f} s: the burst waited for frames that were never fired"


def test_the_next_burst_starts_while_the_previous_one_is_still_being_handed_to_the_save_jobs(
    sequencing_setup, monkeypatch
):
    """Bench 2026-09-20: 0.36 s of every sequenced FOV was the worker blocked on the save queue
    before it moved the stage. A validated burst is now handed over on its own thread; the worker
    moves on. Order must survive a slow disk."""
    import time

    import control.microcontroller

    events = []  # (kind, region, time)
    real_dispatch = multi_point_worker.MultiPointWorker._dispatch_frame
    real_seq_run = control.microcontroller.Microcontroller.seq_run

    def slow_dispatch(worker, camera_frame, info):
        time.sleep(0.05)  # a slow disk: 6 frames = 0.3 s per burst, far longer than a simulated burst
        events.append(("dispatched", info.region_id, time.time()))
        return real_dispatch(worker, camera_frame, info)

    def seq_run(mcu, stack_start):
        events.append(("burst", None, time.time()))
        return real_seq_run(mcu, stack_start)

    monkeypatch.setattr(multi_point_worker.MultiPointWorker, "_dispatch_frame", slow_dispatch)
    monkeypatch.setattr(control.microcontroller.Microcontroller, "seq_run", seq_run)

    tracker, status, _ = run_acquisition(
        FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch, n_regions=3, binning=(4, 4)
    )

    bursts = [t for kind, _, t in events if kind == "burst"]
    assert len(bursts) == 3
    first_region_done = max(t for kind, region, t in events if kind == "dispatched" and region == "region_1")
    assert bursts[1] < first_region_done, "the second burst waited for the first one's frames to be handed over"
    # ...and nothing was reordered or lost by doing so
    assert [(region, z, name) for _, region, _, z, name in tracker.full] == [
        (f"region_{r}", z, name) for r in (1, 2, 3) for z in range(NZ) for name in FLUORESCENCE
    ]
    assert tracker.interventions == []


def test_a_user_abort_still_saves_every_burst_that_was_already_validated(sequencing_setup, monkeypatch):
    """A validated burst is good data. Aborting while it is being handed to the save jobs must not
    drop the rest of it - software mode saves the frames it captured before an abort, too."""
    import time

    real_dispatch = multi_point_worker.MultiPointWorker._dispatch_frame
    dispatched = []

    def slow_dispatch_that_aborts_on_the_first_frame(worker, camera_frame, info):
        if not dispatched:
            worker.request_abort_fn()  # the user presses Abort while region_1 is being handed over
        time.sleep(0.05)
        dispatched.append((info.region_id, info.z_index, info.configuration.name))
        return real_dispatch(worker, camera_frame, info)

    monkeypatch.setattr(
        multi_point_worker.MultiPointWorker, "_dispatch_frame", slow_dispatch_that_aborts_on_the_first_frame
    )

    tracker, _, _ = run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch, n_regions=4, binning=(4, 4))

    whole_burst = [("region_1", z, name) for z in range(NZ) for name in FLUORESCENCE]
    assert dispatched[: len(whole_burst)] == whole_burst  # all six frames, not just the first
    assert {region for region, _, _ in dispatched} < {"region_1", "region_2", "region_3", "region_4"}  # it DID abort
    # whatever was validated before the abort took effect is complete, never a partial burst
    assert len(dispatched) % len(whole_burst) == 0


def test_a_time_point_is_completely_handed_over_before_the_next_one_begins(sequencing_setup, monkeypatch):
    """Bursts overlap across FOVs, not across time points: a time point's image count, coordinates
    file and stats are written when it ends, so every one of its frames must have been handed over."""
    import time

    import control.microcontroller

    events = []
    real_dispatch = multi_point_worker.MultiPointWorker._dispatch_frame
    real_seq_run = control.microcontroller.Microcontroller.seq_run

    def slow_dispatch(worker, camera_frame, info):
        time.sleep(0.05)
        events.append(("dispatched", info.time_point, time.time()))
        return real_dispatch(worker, camera_frame, info)

    def seq_run(mcu, stack_start):
        events.append(("burst", None, time.time()))
        return real_seq_run(mcu, stack_start)

    monkeypatch.setattr(multi_point_worker.MultiPointWorker, "_dispatch_frame", slow_dispatch)
    monkeypatch.setattr(control.microcontroller.Microcontroller, "seq_run", seq_run)

    run_acquisition(FLUORESCENCE, sequenced=True, monkeypatch=monkeypatch, n_regions=2, nt=2, binning=(4, 4))

    bursts = [t for kind, _, t in events if kind == "burst"]
    assert len(bursts) == 4  # 2 regions x 2 time points
    last_of_first_time_point = max(t for kind, time_point, t in events if kind == "dispatched" and time_point == 0)
    assert bursts[2] > last_of_first_time_point, "time point 1 began while time point 0 was still being handed over"


def test_the_qt_controller_forwards_the_intervention_message_to_the_gui_signal(qtbot):
    """The worker runs on its own thread; the GUI hears about an intervention through a Qt signal."""
    import tests.control.gui_test_stubs as gts

    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mpc = gts.get_test_qt_multi_point_controller(microscope=scope)
        with qtbot.waitSignal(mpc.user_intervention_needed, timeout=2000) as blocker:
            mpc.callbacks.signal_user_intervention_needed("check the camera cable")
        assert blocker.args == ["check the camera cable"]
    finally:
        scope.close()


def test_many_positions_and_time_points_pair_every_frame_and_write_the_same_files(sequencing_setup, monkeypatch):
    """The stress case: many bursts back to back, across time points, with the camera thread and
    the worker thread running concurrently. Every frame must land on its own (time point, region,
    fov, z, channel), and the files on disk must be the ones a software-sequenced run writes."""
    channels = ["Fluorescence 405 nm Ex", "Fluorescence 488 nm Ex", "Fluorescence 561 nm Ex"]
    kwargs = dict(monkeypatch=monkeypatch, n_regions=6, nz=4, nt=2, binning=(4, 4))
    software, _, _ = run_acquisition(channels, sequenced=False, **kwargs)
    sequenced, status, piezo_um = run_acquisition(channels, sequenced=True, **kwargs)

    assert len(software.full) == 6 * 4 * 3 * 2
    assert sequenced.full == software.full  # same images, same order, same identities
    assert len(set(sequenced.full)) == len(sequenced.full)  # nothing delivered twice
    assert sequenced.interventions == []
    assert status.state == SeqState.DONE and status.frames_fired == 4 * 3  # the last FOV's burst
    assert piezo_um == pytest.approx(20)

    assert len(software.files) > 0
    assert sequenced.files == software.files
