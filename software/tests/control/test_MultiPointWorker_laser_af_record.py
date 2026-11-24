"""The worker writes one laser_af.csv row per laser AF call, and the summary at the end of a simulated acquisition."""

import csv
import json
import math
import os

import pytest

import control._def
import control.microscope
from control.core.laser_af_record import LaserAFRecord
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import CountingTracker, add_some_coordinates, select_some_configs
from tests.control.test_MultiPointWorker_z_stack_order import _FakePiezo, _worker


class _SurfaceLaserAF:
    """Laser AF over a coverslip at `surface_um` (piezo um): the spot is in view within `capture_um` of it."""

    def __init__(self, piezo, surface_um, capture_um=4.0):
        self.piezo = piezo
        self.surface_um = surface_um
        self.capture_um = capture_um
        self.image = None
        self.characterization_mode = False
        self.last_displacement_um = self.last_correlation = math.nan

    def move_to_target(self, target_um):
        d = self.piezo.position - self.surface_um
        self.last_displacement_um = d if abs(d) <= self.capture_um else math.nan
        self.last_correlation = math.nan
        if math.isnan(self.last_displacement_um):
            return False
        self.piezo.move_to(self.piezo.position + target_um - d)
        self.last_correlation = 0.95
        return True

    def apply_relative_offset_um(self, offset_um):
        self.piezo.move_to(self.piezo.position + offset_um)


def _record_worker(surface_um, start_um=100.0):
    w = _worker("CHANNEL FIRST", nz=1, channels=("A",), use_piezo=True)
    w.piezo = _FakePiezo(position=start_um)
    w.laser_auto_focus_controller = _SurfaceLaserAF(w.piezo, surface_um)
    w.do_reflection_af = True
    del w.perform_autofocus  # the real one
    return w


def _rows(path):
    with open(os.path.join(path, "laser_af.csv")) as f:
        return list(csv.DictReader(f))


def test_one_row_per_per_fov_laser_af(tmp_path):
    w = _record_worker(surface_um=103.0)  # in view from 100: 3 um off
    w._laser_af_record = LaserAFRecord(str(tmp_path))
    w.acquire_at_position("A1", "/tmp", 0)
    w.laser_auto_focus_controller.surface_um = 150.0  # the next FOV: out of view
    w.acquire_at_position("A1", "/tmp", 1)
    rows = _rows(tmp_path)

    assert [(r["fov"], r["ok"]) for r in rows] == [("0", "True"), ("1", "False")]
    # z is the stage (1 mm) plus the piezo
    assert [(float(r["z_before_um"]), float(r["z_after_um"])) for r in rows] == [(1100.0, 1103.0), (1103.0, 1103.0)]
    assert float(rows[0]["displacement_um"]) == pytest.approx(-3.0)  # what the first measurement saw
    assert float(rows[0]["correlation"]) == pytest.approx(0.95)
    assert math.isnan(float(rows[1]["displacement_um"]))  # no spot
    assert math.isnan(float(rows[1]["correlation"]))  # not reached
    assert (rows[0]["x_mm"], rows[0]["y_mm"], rows[0]["channel"], rows[0]["target_um"]) == ("10.0", "20.0", "", "0.0")


def test_no_record_by_default_on_a_bare_worker(tmp_path):
    w = _record_worker(surface_um=103.0)
    w.acquire_at_position("A1", str(tmp_path), 0)
    assert w._laser_af_record is None


@pytest.mark.parametrize("save_record", [True, False])
def test_simulated_acquisition_writes_the_record_and_its_summary(monkeypatch, save_record):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "LASER_AF_SAVE_RECORD", save_record)
    monkeypatch.setattr(control._def, "LASER_AF_SAVE_FRAMES", save_record)
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = CountingTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())
    add_some_coordinates(mpc)
    select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_reflection_af_flag(True)
    scope.addons.camera_focus.send_trigger()
    mpc.laserAutoFocusController.laser_af_properties.set_reference_image(scope.addons.camera_focus.read_frame())

    mpc.run_acquisition()
    assert tt.finished_event.wait(30)
    mpc.thread.join(10)
    experiment_path = os.path.join(mpc.base_path, mpc.experiment_ID)
    n_fovs = sum(len(c) for c in mpc.scanCoordinates.region_fov_coordinates.values())

    if not save_record:
        assert not os.path.exists(os.path.join(experiment_path, "laser_af.csv"))
        assert not os.path.exists(os.path.join(experiment_path, "laser_af_frames"))
    else:
        rows = _rows(experiment_path)
        assert len(rows) == n_fovs > 1
        # The simulated focus camera shows no spot this detector finds: every AF fails, imaged where it is.
        assert {r["ok"] for r in rows} == {"False"}
        with open(os.path.join(experiment_path, "laser_af_summary.json")) as f:
            (tp,) = json.load(f)["time_points"]
        assert (tp["n"], tp["ok"], tp["focus_surface"]) == (n_fovs, 0, None)
        assert len(os.listdir(os.path.join(experiment_path, "laser_af_frames", "0"))) == n_fovs
    mpc.close()
    scope.close()


def test_the_laser_af_controller_keeps_its_last_displacement_and_correlation(monkeypatch):
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    laser_af = mpc.laserAutoFocusController
    laser_af.laser_af_properties = laser_af.laser_af_properties.model_copy(
        update={"has_reference": True, "x_reference": 500.0, "pixel_to_um": 0.5}
    )
    assert math.isnan(laser_af.last_displacement_um) and math.isnan(laser_af.last_correlation)

    monkeypatch.setattr(laser_af, "_get_laser_spot_centroid", lambda *a, **k: (520.0, 100.0))  # 10 um off
    monkeypatch.setattr(laser_af, "_verify_spot_alignment", lambda: (True, 0.93))
    monkeypatch.setattr(laser_af, "_move_z", lambda um: None)
    assert laser_af.move_to_target(0.0)
    assert (laser_af.last_displacement_um, laser_af.last_correlation) == (10.0, 0.93)

    monkeypatch.setattr(laser_af, "_get_laser_spot_centroid", lambda *a, **k: None)  # the spot is lost
    assert not laser_af.move_to_target(0.0)
    assert math.isnan(laser_af.last_displacement_um) and math.isnan(laser_af.last_correlation)
    mpc.close()
    scope.close()
