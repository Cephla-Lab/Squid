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
    def __init__(self, camera, lose_frames_from_scan=None, stall=False):
        self.camera = camera
        self.scans = 0
        self.stops = 0
        self.lose_frames_from_scan = lose_frames_from_scan
        self.stall = stall
        self.release = threading.Event()

    def start_acquisition(self):
        self.scans += 1
        if self.stall:
            self.release.wait(10)  # the camera never reports ready
            return
        time.sleep(0.005)  # the scan
        if self.lose_frames_from_scan is None or self.scans < self.lose_frames_from_scan:
            self.camera._next_frame()

    def stop_continuous_acquisition(self):
        self.stops += 1
        self.release.set()

    def set_active_channel(self, channel):
        pass

    def set_laser_power(self, channel, power):
        pass


@pytest.fixture
def aion_scope(monkeypatch):
    # ENABLE_NL5 stays off in control._def so the simulated build doesn't create its own NL5; the fake
    # unit is attached as addons.nl5 afterwards.
    for module in (control._def, control.core.multi_point_worker, control.core.live_controller):
        if module is not control._def:
            monkeypatch.setattr(module, "ENABLE_NL5", True, raising=False)
        monkeypatch.setattr(module, "NL5_USE_DOUT", True, raising=False)
        monkeypatch.setattr(module, "NL5_USE_AOUT", False, raising=False)
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    camera = scope.camera
    simulated_trigger = camera._send_trigger_imp

    def tucsen_like_trigger(illumination_time=None):
        if camera.get_acquisition_mode() != CameraAcquisitionMode.HARDWARE_TRIGGER:
            return simulated_trigger(illumination_time)
        camera._last_trigger_timestamp = time.time()
        camera._hw_trigger_fn(illumination_time)

    monkeypatch.setattr(camera, "_send_trigger_imp", tucsen_like_trigger)
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


def test_a_stalled_scan_does_not_hold_the_camera(aion_scope, monkeypatch):
    """A scan whose camera never reports ready held the camera's trigger lock forever (live view stalled)."""
    monkeypatch.setattr(control._def, "NL5_TRIGGER_TIMEOUT_S", 0.2, raising=False)
    scope = control.microscope.Microscope.build_from_global_config(True)  # picks up the short timeout
    try:
        unit = FakeConfocalUnit(scope.camera, stall=True)
        scope.addons.nl5 = unit
        scope.camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
        scope.camera.start_streaming()
        rearmed = []
        set_mode = scope.camera.set_acquisition_mode
        scope.camera.set_acquisition_mode = lambda mode: (rearmed.append(mode), set_mode(mode))

        sender = threading.Thread(target=scope.camera._hw_trigger_fn, args=(10.0,), daemon=True)
        sender.start()
        sender.join(5)

        assert not sender.is_alive()
        assert unit.stops == 1  # the stuck scan was aborted
        deadline = time.monotonic() + 5
        while not rearmed and time.monotonic() < deadline:
            time.sleep(0.05)
        # ...and the camera re-armed, as toggling the trigger mode did by hand.
        assert rearmed == [CameraAcquisitionMode.HARDWARE_TRIGGER]
        assert scope.camera.get_is_streaming()
    finally:
        scope.close()


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
    time.sleep(0.1)
    unit.release.clear()
    unit.start_acquisition = lambda: None
    assert trigger.trigger(unit) is True


def test_scan_trigger_passes_errors_through():
    from control.rcm_scan_trigger import RcmScanTrigger

    class Broken:
        def start_acquisition(self):
            raise RuntimeError("RCM error")

    with pytest.raises(RuntimeError, match="RCM error"):
        RcmScanTrigger(timeout_s=1.0).trigger(Broken())


def test_scan_trigger_runs_one_recovery_after_a_timeout():
    from control.rcm_scan_trigger import RcmScanTrigger

    recovering = threading.Event()
    finish = threading.Event()
    recoveries = []

    def recover():
        recoveries.append(1)
        recovering.set()
        finish.wait(5)

    trigger = RcmScanTrigger(timeout_s=0.1, on_timeout=recover)
    unit = StuckUnit()
    unit.stop_continuous_acquisition = lambda: unit.release.set()  # this unit does stop when asked

    assert trigger.trigger(unit) is False
    assert recovering.wait(5)
    unit.release.clear()
    assert trigger.trigger(unit) is False  # times out again while the first recovery still runs
    assert recoveries == [1]
    finish.set()


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
    """An aborted stack used to undo all NZ - 1 steps, moving Z past where the stack started."""
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
