"""What an acquisition does when laser AF fails at a FOV: the optional piezo search for the spot, and the optional stop.

The worker tests use a bare MultiPointWorker (the harness of test_MultiPointWorker_z_stack_order) with a fake laser AF
whose spot is in view only within a few um of a coverslip height, so the search has something to find. The end-to-end
test runs a simulated acquisition, whose laser AF finds no spot.
"""

import math
import os

import pytest

import control._def
import control.microscope
import squid.acquisition_state as ast
from control.core.multi_point_worker import laser_af_spot_search_offsets_um
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import CountingTracker, add_some_coordinates, select_some_configs
from tests.control.test_MultiPointWorker_z_stack_order import _FakePiezo, _worker

OFFSETS = (5.0, -5.0, 10.0, -10.0, 15.0, -15.0, 20.0, -20.0)


class _SurfaceLaserAF:
    """Laser AF over a coverslip at `surface_um` (piezo um): the spot is in view within `capture_um` of it."""

    def __init__(self, piezo, surface_um, capture_um=4.0, laser_af_range=100.0):
        self.piezo = piezo
        self.surface_um = surface_um
        self.capture_um = capture_um
        self.laser_af_properties = type("P", (), {"laser_af_range": laser_af_range})()
        self.measured_at = []  # piezo position of every measure_displacement()
        self.move_to_target_calls = 0
        self.image = None
        self.characterization_mode = False

    def _displacement(self):
        d = self.piezo.position - self.surface_um
        self.last_displacement_um = d if abs(d) <= self.capture_um else math.nan
        return self.last_displacement_um

    def measure_displacement(self):
        self.measured_at.append(self.piezo.position)
        return self._displacement()

    def move_to_target(self, target_um):
        self.move_to_target_calls += 1
        d = self._displacement()
        if math.isnan(d):
            return False
        self.piezo.move_to(self.piezo.position + target_um - d)
        return True

    def apply_relative_offset_um(self, offset_um):
        self.piezo.move_to(self.piezo.position + offset_um)


def _spot_loss_worker(surface_um, *, offsets=(), stop=False, start_um=100.0, range_um=300.0):
    w = _worker("CHANNEL FIRST", nz=1, channels=("A",), use_piezo=True)
    w.piezo = _FakePiezo(position=start_um, range_um=range_um)
    w.laser_auto_focus_controller = _SurfaceLaserAF(w.piezo, surface_um)
    w.do_reflection_af = True
    del w.perform_autofocus  # the real one
    w._laser_af_search_offsets_um = tuple(offsets)
    w._laser_af_stop_on_spot_loss = stop
    aborted = []

    def request_abort():
        aborted.append(True)

    w.request_abort_fn = request_abort
    w.abort_requested_fn = lambda: bool(aborted)
    w._abort_cause = None
    w.aborted = aborted
    return w


def test_by_default_a_failed_laser_af_images_the_fov_where_it_is():
    w = _spot_loss_worker(surface_um=112.0)  # 12 um away: out of view
    w.acquire_at_position("A1", "/tmp", 0)
    assert w.laser_auto_focus_controller.measured_at == []  # no search
    assert w.frames == [(0, "A", 100.0)]
    assert (w._laser_af_successes, w._laser_af_failures) == (0, 1)
    assert not w.aborted


def test_the_search_steps_the_offsets_in_order_and_laser_afs_from_the_first_spot():
    w = _spot_loss_worker(surface_um=112.0, offsets=OFFSETS)
    w.acquire_at_position("A1", "/tmp", 0)
    # +5 (105) and -5 (95) see no spot; +10 (110) does, and laser AF from there reaches the surface.
    assert w.laser_auto_focus_controller.measured_at == [105.0, 95.0, 110.0]
    assert w.laser_auto_focus_controller.move_to_target_calls == 2
    assert w.frames == [(0, "A", 112.0)]
    assert (w._laser_af_successes, w._laser_af_failures) == (1, 0)


def test_a_search_that_finds_nothing_puts_z_back_and_images_the_fov_there():
    w = _spot_loss_worker(surface_um=150.0, offsets=OFFSETS)
    w.acquire_at_position("A1", "/tmp", 0)
    assert w.laser_auto_focus_controller.measured_at == [100.0 + o for o in OFFSETS]
    assert w.piezo.position == 100.0
    assert w.frames == [(0, "A", 100.0)]
    assert (w._laser_af_successes, w._laser_af_failures) == (0, 1)
    assert not w.aborted


def test_the_search_skips_offsets_past_the_piezo_travel():
    w = _spot_loss_worker(surface_um=250.0, offsets=OFFSETS, start_um=290.0, range_um=300.0)
    w.acquire_at_position("A1", "/tmp", 0)
    assert w.laser_auto_focus_controller.measured_at == [295.0, 285.0, 300.0, 280.0, 275.0, 270.0]
    assert w.piezo.position == 290.0


def test_no_search_without_a_piezo():
    w = _spot_loss_worker(surface_um=112.0, offsets=OFFSETS)
    w.laser_auto_focus_controller.piezo = None  # laser AF moves the stage
    w.acquire_at_position("A1", "/tmp", 0)
    assert w.laser_auto_focus_controller.measured_at == []
    assert w.frames == [(0, "A", 100.0)]


def test_stop_on_spot_loss_ends_the_acquisition_as_an_error_without_imaging_the_fov():
    w = _spot_loss_worker(surface_um=150.0, offsets=OFFSETS, stop=True)
    w.acquire_at_position("B2", "/tmp", 7)
    assert w.aborted == [True]
    assert w._abort_cause == "error"
    assert w.frames == []
    assert "region 'B2' FOV 7" in w._end_detail and "after searching" in w._end_detail


def test_stop_on_spot_loss_without_the_search():
    w = _spot_loss_worker(surface_um=150.0, stop=True)
    w.acquire_at_position("B2", "/tmp", 7)
    assert w.laser_auto_focus_controller.measured_at == []
    assert w.aborted == [True] and w.frames == []


def test_stop_on_spot_loss_does_not_stop_when_the_search_finds_the_spot():
    w = _spot_loss_worker(surface_um=92.0, offsets=OFFSETS, stop=True)
    w.acquire_at_position("A1", "/tmp", 0)
    assert not w.aborted
    assert w.frames == [(0, "A", 92.0)]


def test_a_user_abort_during_the_search_stays_a_user_abort():
    w = _spot_loss_worker(surface_um=150.0, offsets=OFFSETS, stop=True)
    laser_af = w.laser_auto_focus_controller
    measure = laser_af.measure_displacement

    def measure_then_user_abort():
        w.aborted.append("user")  # the user's Stop arrives during the first measurement
        return measure()

    laser_af.measure_displacement = measure_then_user_abort
    w.acquire_at_position("A1", "/tmp", 0)
    assert laser_af.measured_at == [105.0]
    assert w.piezo.position == 100.0
    assert w._abort_cause is None  # not turned into an error end
    assert w.frames == []


def test_search_offsets_setting(monkeypatch):
    monkeypatch.setattr(control._def, "LASER_AF_SPOT_SEARCH", False)
    assert laser_af_spot_search_offsets_um() == ()
    monkeypatch.setattr(control._def, "LASER_AF_SPOT_SEARCH", True)
    assert laser_af_spot_search_offsets_um() == OFFSETS  # the default list
    monkeypatch.setattr(control._def, "LASER_AF_SPOT_SEARCH_OFFSETS_UM", [2, 0, "-2"])
    assert laser_af_spot_search_offsets_um() == (2.0, -2.0)
    monkeypatch.setattr(control._def, "LASER_AF_SPOT_SEARCH_OFFSETS_UM", ["up"])
    assert laser_af_spot_search_offsets_um() == ()


@pytest.mark.parametrize("stop", [False, True])
def test_simulated_acquisition_with_no_laser_af_spot(monkeypatch, stop):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "LASER_AF_STOP_ON_SPOT_LOSS", stop)
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

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)

    record = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])
    if stop:
        assert mpc.last_end_reason == "error"
        assert tt.image_count == 0  # stopped at the first FOV, before imaging it
        assert "laser AF lost the spot" in record["stats"]["detail"]
    else:
        assert mpc.last_end_reason == "completed"
        assert tt.image_count == mpc.get_acquisition_image_count()
        assert "detail" not in record["stats"]
    mpc.close()
    scope.close()
