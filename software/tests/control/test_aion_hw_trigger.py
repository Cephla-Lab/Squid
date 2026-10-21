"""Hardware triggering through a Confocal.nl unit (AION / NL5, NL5_USE_DOUT), run headless.

FakeConfocalUnit behaves like the real unit: every start_acquisition() is one scan that triggers exactly
one camera frame. The simulated camera is made to behave like the Tucsen on AION: in HARDWARE_TRIGGER mode
send_trigger() only calls the hardware trigger function, and frames come from the unit's scans.
"""

import re
import threading
import time
import types

import pytest

import control._def
import control.core.live_controller
import control.core.multi_point_worker
import control.microscope
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker
from squid.abc import CameraAcquisitionMode
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker, add_some_coordinates


class FakeConfocalUnit:
    def __init__(self, camera, lose_frames_from_scan=None, stall=False, stall_on_scans=()):
        self.camera = camera
        self.scans = 0
        self.stops = 0
        self.lose_frames_from_scan = lose_frames_from_scan
        self.stall = stall
        self.stall_on_scans = set(stall_on_scans)
        self.release = threading.Event()

    def start_acquisition(self):
        self.scans += 1
        if self.stall or self.scans in self.stall_on_scans:
            self.release.clear()
            self.release.wait(10)  # the camera never reports ready, until the scan is aborted
            return
        time.sleep(0.005)  # the scan
        if self.lose_frames_from_scan is None or self.scans < self.lose_frames_from_scan:
            # Like the camera SDK: the frame arrives on another thread, not inside startAcquisition(), so the
            # acquisition's frame handling doesn't count towards the scan's timeout.
            threading.Thread(target=self.camera._next_frame, daemon=True).start()

    def stop_continuous_acquisition(self):
        self.stops += 1
        self.release.set()

    def set_active_channel(self, channel):
        pass

    def set_laser_power(self, channel, power):
        pass


@pytest.fixture
def nl5_trigger_timeout_s():
    return 2.0  # tests parametrize this to make a stalled scan time out quickly


@pytest.fixture
def aion_scope(monkeypatch, nl5_trigger_timeout_s):
    # ENABLE_NL5 stays off in control._def so the simulated build doesn't create its own NL5; the fake
    # unit is attached as addons.nl5 afterwards.
    for module in (control._def, control.core.multi_point_worker, control.core.live_controller):
        if module is not control._def:
            monkeypatch.setattr(module, "ENABLE_NL5", True, raising=False)
        monkeypatch.setattr(module, "NL5_USE_DOUT", True, raising=False)
        monkeypatch.setattr(module, "NL5_USE_AOUT", False, raising=False)
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "NL5_TRIGGER_TIMEOUT_S", nl5_trigger_timeout_s, raising=False)
    scope = control.microscope.Microscope.build_from_global_config(True)
    camera = scope.camera
    simulated_trigger = camera._send_trigger_imp
    simulated_set_mode = camera._set_acquisition_mode_imp

    def tucsen_like_trigger(illumination_time=None):
        if camera.get_acquisition_mode() != CameraAcquisitionMode.HARDWARE_TRIGGER:
            return simulated_trigger(illumination_time)
        camera._last_trigger_timestamp = time.time()
        camera._hw_trigger_fn(illumination_time)

    def tucsen_like_set_mode(acquisition_mode):
        # Like the Tucsen: a mode change pauses streaming, which takes the camera's trigger lock.
        with camera._pause_streaming():
            simulated_set_mode(acquisition_mode)

    monkeypatch.setattr(camera, "_send_trigger_imp", tucsen_like_trigger)
    monkeypatch.setattr(camera, "_set_acquisition_mode_imp", tucsen_like_set_mode)
    yield scope
    scope.close()


def _wavelength(channel_name):
    match = re.search(r"(\d{3}) nm", channel_name)
    return int(match.group(1)) if match else None


def _fluorescence_acquisition(scope, unit, nz=2):
    scope.addons.nl5 = unit
    tracker = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.get_callbacks())
    mpc.liveController.set_trigger_mode(TriggerMode.HARDWARE)
    add_some_coordinates(mpc)
    names = [
        c.name
        for c in mpc.liveController.get_channels(scope.objective_store.current_objective)
        if "Fluorescence" in c.name and _wavelength(c.name) in control._def.NL5_WAVENLENGTH_MAP
    ][:2]
    assert len(names) == 2
    mpc.set_selected_configurations(names)
    mpc.set_NZ(nz)
    return mpc, tracker


def test_each_image_is_one_confocal_scan(aion_scope):
    """Every image used to start two scans: an untracked frame per image (black frames, channels mixed, aborts)."""
    unit = FakeConfocalUnit(aion_scope.camera)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit)
    expected = mpc.get_acquisition_image_count()

    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(60)

    assert tracker.image_count == expected
    assert unit.scans == expected


def test_a_lost_frame_stops_the_acquisition_promptly(aion_scope, monkeypatch):
    """After an abort the z-stack used to carry on through every remaining z level and channel."""
    monkeypatch.setattr(MultiPointWorker, "_frame_wait_timeout_s", lambda self: 0.3)
    unit = FakeConfocalUnit(aion_scope.camera, lose_frames_from_scan=3)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit, nz=4)
    assert mpc.get_acquisition_image_count() > 8

    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(30)

    assert unit.scans == 3  # nothing more is triggered once the frame for scan 3 never came


@pytest.mark.parametrize("nl5_trigger_timeout_s", [0.2])
def test_a_stalled_scan_is_aborted_rearmed_and_retried(aion_scope):
    """A scan whose camera never reports ready used to hold the camera's trigger lock forever."""
    camera = aion_scope.camera
    unit = FakeConfocalUnit(camera, stall_on_scans={1})
    aion_scope.addons.nl5 = unit
    camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
    camera.start_streaming()
    frames = []
    camera.add_frame_callback(lambda frame: frames.append(frame.frame_id))
    mode_changes = []
    set_mode = camera.set_acquisition_mode

    def recording_set_mode(mode):
        mode_changes.append((threading.current_thread().name, mode))
        set_mode(mode)

    camera.set_acquisition_mode = recording_set_mode

    sender = threading.Thread(target=camera.send_trigger, args=(10.0,), name="trigger", daemon=True)
    sender.start()
    time.sleep(0.05)  # the first scan is now stalled, with send_trigger holding the camera's lock
    # A user switching to software trigger meanwhile waits for the trigger to finish, then wins.
    toggler = threading.Thread(
        target=camera.set_acquisition_mode, args=(CameraAcquisitionMode.SOFTWARE_TRIGGER,), name="user", daemon=True
    )
    toggler.start()
    sender.join(10)
    toggler.join(10)

    assert not sender.is_alive() and not toggler.is_alive()
    assert unit.stops == 1  # the stalled scan was aborted
    assert unit.scans == 2  # ...and retried once
    deadline = time.monotonic() + 5
    while not frames and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(frames) == 1  # the retry delivered the image
    # The re-arm ran on the triggering thread, inside send_trigger, before the user's change.
    assert [c for c in mode_changes if c[0] == "trigger"] == [("trigger", CameraAcquisitionMode.HARDWARE_TRIGGER)]
    assert camera.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER
    assert camera.get_is_streaming()


@pytest.mark.parametrize("nl5_trigger_timeout_s", [0.2])
def test_a_stall_during_an_acquisition_does_not_abort_it(aion_scope):
    """A dropped trigger used to leave the acquisition waiting for a frame that never came, then abort."""
    unit = FakeConfocalUnit(aion_scope.camera, stall_on_scans={3})
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit)
    expected = mpc.get_acquisition_image_count()

    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(60)

    assert tracker.image_count == expected  # every image, including the one whose first scan stalled
    assert unit.scans == expected + 1  # one retry
    assert unit.stops == 1


class StuckUnit:
    """A unit whose startAcquisition() stays stuck even after stopContinuousAcquisition()."""

    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.release = threading.Event()

    def start_acquisition(self):
        self.starts += 1
        self.release.wait(10)

    def stop_continuous_acquisition(self):
        self.stops += 1


def test_scan_trigger_times_out_and_never_calls_into_a_stuck_unit_again():
    from control.rcm_scan_trigger import RcmScanTrigger

    trigger = RcmScanTrigger(timeout_s=0.1)
    unit = StuckUnit()

    t0 = time.monotonic()
    assert trigger.trigger(unit) is False
    assert time.monotonic() - t0 < 3
    assert unit.stops == 1

    assert trigger.trigger(unit) is False  # still stuck: dropped without a second DLL call
    assert unit.starts == 1

    unit.release.set()
    trigger._scan.join(5)
    unit.start_acquisition = lambda: None
    assert trigger.trigger(unit) is True


def test_scan_trigger_passes_errors_through():
    from control.rcm_scan_trigger import RcmScanTrigger

    class Broken:
        def start_acquisition(self):
            raise RuntimeError("RCM error")

    with pytest.raises(RuntimeError, match="RCM error"):
        RcmScanTrigger(timeout_s=1.0).trigger(Broken())


class StallsUntilStopped(StuckUnit):
    """A unit whose first `stalls` scans hang until stopContinuousAcquisition(); later scans succeed."""

    def __init__(self, stalls):
        super().__init__()
        self.stalls = stalls

    def start_acquisition(self):
        self.starts += 1
        if self.starts <= self.stalls:
            self.release.clear()
            self.release.wait(10)

    def stop_continuous_acquisition(self):
        self.stops += 1
        self.release.set()


def test_a_stall_is_rearmed_and_retried_on_the_calling_thread():
    from control.rcm_scan_trigger import RcmScanTrigger

    rearms = []
    trigger = RcmScanTrigger(timeout_s=0.1, rearm=lambda: rearms.append(threading.current_thread().name))
    unit = StallsUntilStopped(stalls=1)

    assert trigger.trigger(unit) is True  # the retry succeeded
    assert unit.starts == 2 and unit.stops == 1
    assert rearms == [threading.current_thread().name]  # synchronous, not on a background thread


def test_a_retry_that_stalls_too_drops_the_trigger():
    from control.rcm_scan_trigger import RcmScanTrigger

    rearms = []
    trigger = RcmScanTrigger(timeout_s=0.1, rearm=lambda: rearms.append(1))
    unit = StallsUntilStopped(stalls=2)

    assert trigger.trigger(unit) is False
    assert unit.starts == 2 and unit.stops == 2
    assert rearms == [1]  # one re-arm per trigger, not a loop


def test_no_rearm_while_the_aborted_scan_is_still_stuck(caplog):
    """Re-arming while the aborted scan is still in startAcquisition() could let it deliver a late frame."""
    from control.rcm_scan_trigger import RcmScanTrigger

    rearms = []
    trigger = RcmScanTrigger(timeout_s=0.1, rearm=lambda: rearms.append(1))
    unit = StuckUnit()  # stays stuck after stop_continuous_acquisition()

    assert trigger.trigger(unit) is False
    for _ in range(3):
        assert trigger.trigger(unit) is False
    assert rearms == []  # not re-armed while the old scan is still running
    assert unit.starts == 1  # and no new scan was started meanwhile
    assert sum("still stuck" in r.getMessage() for r in caplog.records) == 1  # logged once, not per trigger

    unit.release.set()  # the old scan finally exits
    trigger._scan.join(5)
    unit.start_acquisition = lambda: None
    assert trigger.trigger(unit) is True
    assert rearms == [1]  # re-armed before the next scan


def _z_moves_during(scope, mpc, tracker, monkeypatch):
    """Run the acquisition and return its relative Z moves (moving between FOVs is absolute, so excluded)."""
    moves = []
    move_z = scope.stage.move_z
    monkeypatch.setattr(scope.stage, "move_z", lambda rel_mm, *a, **k: (moves.append(rel_mm), move_z(rel_mm, *a, **k)))
    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(60)
    return moves


@pytest.mark.parametrize(
    "lose_frames_from_scan, stacking",
    [
        (1, "FROM BOTTOM"),  # abort on the first plane
        (8, "FROM BOTTOM"),  # abort in the middle of the stack
        (None, "FROM BOTTOM"),  # no abort: a full stack
        (1, "FROM CENTER"),
        (8, "FROM CENTER"),
        (None, "FROM CENTER"),
    ],
)
def test_z_returns_to_the_start_of_the_stack(aion_scope, monkeypatch, lose_frames_from_scan, stacking):
    """Z ends where the stack started, also when an abort leaves the z loop early (only the steps taken are undone)."""
    monkeypatch.setattr(MultiPointWorker, "_frame_wait_timeout_s", lambda self: 0.3)
    unit = FakeConfocalUnit(aion_scope.camera, lose_frames_from_scan=lose_frames_from_scan)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit, nz=10)
    mpc.set_deltaZ(10)  # um
    mpc.z_stacking_config = stacking

    moves = _z_moves_during(aion_scope, mpc, tracker, monkeypatch)

    assert sum(moves) == pytest.approx(0.0, abs=1e-9), moves
    if lose_frames_from_scan is not None:
        assert unit.scans == lose_frames_from_scan


class PiezoStub:
    def __init__(self):
        self.moves = []

    def move_to(self, um):
        self.moves.append(um)


@pytest.mark.parametrize("steps_taken, expected_um", [(0, 100.0), (3, 70.0), (None, 10.0)])
def test_piezo_stack_undoes_only_the_steps_taken(steps_taken, expected_um):
    worker = types.SimpleNamespace(
        use_piezo=True,
        z_piezo_um=100.0,
        deltaZ=0.010,  # mm
        NZ=10,
        piezo=PiezoStub(),
        liveController=types.SimpleNamespace(trigger_mode=TriggerMode.HARDWARE),
    )
    MultiPointWorker.move_z_back_after_stack(worker, steps_taken)
    assert worker.piezo.moves == [pytest.approx(expected_um)]
