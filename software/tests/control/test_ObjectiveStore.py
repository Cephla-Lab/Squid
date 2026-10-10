import numpy as np
import pytest

import tests.control.test_stubs as ts
import control._def
from control.core.objective_store import CalibrationChange, ObjectiveStore
from control.models.objective_calibration_config import (
    TRANSFORM_CHANGED,
    CurrentSetup,
    ImageTransform,
    build_pixel_record,
    merge_pixel_records,
)
from squid.objective_calibration.engine import PixelSizeSummary


def test_objective_store():
    objective_store = ts.get_test_objective_store()
    objective1 = {
        "name": "10x",
        "magnification": 10,
        "NA": 0.3,
        "tube_lens_f_mm": 180,
    }
    objective2 = {
        "name": "60x",
        "magnification": 60,
        "NA": 1.2,
        "tube_lens_f_mm": 180,
    }
    assert objective_store.calculate_pixel_size_factor(objective1, control._def.TUBE_LENS_MM) == float(
        18 / control._def.TUBE_LENS_MM
    )
    assert objective_store.calculate_pixel_size_factor(objective2, control._def.TUBE_LENS_MM) == float(
        3 / control._def.TUBE_LENS_MM
    )


# ---------------------------------------------------------------- the seam (spec B §4.4-4.5, §8)

OBJECTIVES = {
    "10x": {"magnification": 10, "NA": 0.3, "tube_lens_f_mm": 180},
    "20x": {"magnification": 20, "NA": 0.8, "tube_lens_f_mm": 180},
}
SENSOR_UM = 3.76  # the unbinned sensor pixel; 20x nominal is 0.188 um/px, 10x is 0.376 um/px at 1x
SETUP = CurrentSetup("TOUPCAM/ITR3CMOS26000KMA/unknown", SENSOR_UM, 180.0, ImageTransform())
OTHER_CAMERA = CurrentSetup("TOUPCAM/OLDCAMERA/unknown", SENSOR_UM, 180.0, ImageTransform())
FLIPPED = CurrentSetup(SETUP.camera_key, SENSOR_UM, 180.0, ImageTransform(flip="Vertical"))


def _declared(name):
    info = OBJECTIVES[name]
    return {
        "magnification": float(info["magnification"]),
        "na": info["NA"],
        "tube_lens_f_mm": float(info["tube_lens_f_mm"]),
        "model": "",
        "serial": "",
    }


def _summary(px, matrix=None):
    return PixelSizeSummary(
        objective="x",
        cycles=3,
        pixel_size_um=px,
        std_pixel_size_um=0.0003,
        matrix_um_per_px=np.asarray(matrix, dtype=float) if matrix is not None else px * np.eye(2),
        rotation_deg=0.1,
        flip=np.eye(2),
        orientation_matches_mosaic=True,
        anisotropy=1.0,
        fit_residual_um=0.3,
    )


def _record(px, name, *, setup=SETUP, binned=SENSOR_UM, measured_at="2026-09-28T10:00:00"):
    return build_pixel_record(
        _summary(px),
        measured_at=measured_at,
        camera_key=setup.camera_key,
        unbinned_sensor_pixel_um=setup.unbinned_sensor_pixel_um,
        binned_sensor_pixel_um=binned,
        binning=int(round(binned / setup.unbinned_sensor_pixel_um)),
        image_transform=setup.image_transform,
        tube_lens_mm=setup.tube_lens_mm,
        declared=_declared(name),
    )


def _calibration(**records):
    return merge_pixel_records(None, records)


class _Setup:
    """A mutable CurrentSetup provider: tests swap the camera under a live store."""

    def __init__(self, setup=SETUP, binned=SENSOR_UM):
        self.setup = setup
        self.binned = binned

    def current(self):
        if isinstance(self.setup, Exception):
            raise self.setup
        return self.setup


def _store(calibration=None, provider=None, objective="20x"):
    provider = provider or _Setup()
    return ObjectiveStore(
        OBJECTIVES,
        objective,
        calibration=calibration,
        current_setup=provider.current,
        get_declared=_declared,
        binned_sensor_pixel_um=lambda: provider.binned,
    )


def test_the_no_argument_constructor_is_nominal_everywhere():
    store = ObjectiveStore()
    name = store.current_objective
    assert store.get_pixel_size_factor() == ObjectiveStore.calculate_pixel_size_factor(
        store.objectives_dict[name], control._def.TUBE_LENS_MM
    )
    assert store.pixel_size_source(name) == "nominal"
    assert store.pixel_matrix(name) is None
    assert store.pixel_calibration_measured_at(name) is None
    assert store.orientation_matches_mosaic() is None
    assert store.invalid_calibrations() == {}


def test_uncalibrated_objective_uses_the_nominal_factor():
    store = _store()
    assert store.get_pixel_size_factor() == pytest.approx(180 / 20 / 180)
    assert store.pixel_size_source("20x") == "nominal"
    assert store.pixel_matrix("20x") is None
    assert store.orientation_matches_mosaic() is None


def test_calibrated_objective_uses_the_saved_factor():
    # 20x measured 3% off nominal: 0.188 * 1.03 um/px at 1x
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}))
    assert store.get_pixel_size_factor() == pytest.approx(0.188 * 1.03 / SENSOR_UM)
    assert store.get_pixel_size_factor() * SENSOR_UM == pytest.approx(0.188 * 1.03)
    assert store.pixel_size_source("20x") == "calibrated"
    assert store.pixel_calibration_measured_at("20x") == "2026-09-28T10:00:00"
    assert store.orientation_matches_mosaic() is True
    assert store.invalid_calibrations() == {}


def test_invalid_scalar_falls_back_to_nominal_and_reports_why():
    # recorded on another camera: the scalar (and so the matrix) is invalid
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x", setup=OTHER_CAMERA)}))
    assert store.get_pixel_size_factor() == pytest.approx(180 / 20 / 180)
    assert store.pixel_size_source("20x") == "nominal"
    assert store.pixel_matrix("20x") is None
    assert store.pixel_calibration_measured_at("20x") is None
    assert "camera changed" in store.invalid_calibrations()["20x"]
    assert store.calibration_warnings()[0].startswith("20x: nominal pixel size in use (camera changed")


def test_binning_change_needs_no_re_evaluation():
    # Spec B §4.5 example: calibrated at 1x (0.376 um/px), now at 2x. A 100 px displacement needs
    # 75.2 um, so the factor x binned pixel doubles and pixel_matrix() returns 0.752 um/px.
    provider = _Setup()
    store = _store(_calibration(**{"10x": _record(0.376, "10x")}), provider, objective="10x")
    assert store.get_pixel_size_factor() * provider.binned == pytest.approx(0.376)
    np.testing.assert_allclose(store.pixel_matrix("10x"), 0.376 * np.eye(2))
    provider.binned = 2 * SENSOR_UM
    assert store.get_pixel_size_factor() * provider.binned == pytest.approx(0.752)
    np.testing.assert_allclose(store.pixel_matrix("10x"), 0.752 * np.eye(2))
    assert store.pixel_size_source("10x") == "calibrated"


def test_a_changed_image_transform_invalidates_only_the_matrix():
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}), _Setup(setup=FLIPPED))
    assert store.get_pixel_size_factor() == pytest.approx(0.188 * 1.03 / SENSOR_UM)
    assert store.pixel_size_source("20x") == "calibrated"
    assert store.pixel_matrix("20x") is None
    assert store.orientation_matches_mosaic() is None
    assert store.invalid_calibrations()["20x"] == TRANSFORM_CHANGED
    assert store.calibration_warnings() == [f"20x: pixel size in use, XY matrix not ({TRANSFORM_CHANGED})"]


def test_switching_objective_switches_between_calibrated_and_nominal():
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}))
    store.set_current_objective("10x")
    assert store.get_pixel_size_factor() == pytest.approx(180 / 10 / 180)
    assert store.pixel_size_source("10x") == "nominal"
    store.set_current_objective("20x")
    assert store.get_pixel_size_factor() == pytest.approx(0.188 * 1.03 / SENSOR_UM)
    with pytest.raises(ValueError):
        store.set_current_objective("4x")


def test_set_calibration_reports_what_changed():
    store = _store()
    change = store.set_calibration(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}))
    assert change == CalibrationChange(quantities=frozenset({"pixel_size"}), validity_flipped=True)
    assert store.get_pixel_size_factor() == pytest.approx(0.188 * 1.03 / SENSOR_UM)
    # the same records again: nothing effective changed
    again = store.set_calibration(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}))
    assert again == CalibrationChange(quantities=frozenset(), validity_flipped=False)
    # a new measurement of a valid objective: the factor changed, validity did not
    remeasured = store.set_calibration(_calibration(**{"20x": _record(0.188 * 1.02, "20x")}))
    assert remeasured == CalibrationChange(quantities=frozenset({"pixel_size"}), validity_flipped=False)
    # cleared: back to nominal
    cleared = store.set_calibration(None)
    assert cleared == CalibrationChange(quantities=frozenset({"pixel_size"}), validity_flipped=True)
    assert store.pixel_size_source("20x") == "nominal"


def test_refresh_validity_follows_a_camera_change():
    provider = _Setup()
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}), provider)
    assert store.refresh_validity() == CalibrationChange(quantities=frozenset(), validity_flipped=False)
    provider.setup = OTHER_CAMERA
    change = store.refresh_validity()
    assert change == CalibrationChange(quantities=frozenset({"pixel_size"}), validity_flipped=True)
    assert store.get_pixel_size_factor() == pytest.approx(180 / 20 / 180)
    assert store.pixel_size_source("20x") == "nominal"


def test_subset_save_after_a_camera_change_leaves_the_other_block_invalid():
    # 10x and 20x calibrated on the old camera; the camera changes; only 10x is recalibrated
    old = {
        "10x": _record(0.376 * 0.98, "10x", setup=OTHER_CAMERA),
        "20x": _record(0.188 * 1.03, "20x", setup=OTHER_CAMERA),
    }
    store = _store(_calibration(**old), objective="10x")
    assert store.invalid_calibrations().keys() == {"10x", "20x"}
    store.set_calibration(merge_pixel_records(_calibration(**old), {"10x": _record(0.376 * 0.99, "10x")}))
    assert store.get_pixel_size_factor() == pytest.approx(0.376 * 0.99 / SENSOR_UM)
    assert store.pixel_size_source("10x") == "calibrated"
    assert store.pixel_size_source("20x") == "nominal"
    assert store.invalid_calibrations().keys() == {"20x"}
    assert store.orientation_matches_mosaic() is True  # from the valid matrix only


def test_an_unavailable_camera_setup_makes_every_record_invalid_without_crashing():
    provider = _Setup(setup=NotImplementedError("unknown camera model"))
    store = _store(_calibration(**{"20x": _record(0.188 * 1.03, "20x")}), provider)
    assert store.get_pixel_size_factor() == pytest.approx(180 / 20 / 180)
    assert "unknown camera model" in store.invalid_calibrations()["20x"]


def test_a_record_for_an_objective_not_in_the_list_is_ignored():
    store = _store(_calibration(**{"4x": _record(0.188, "20x")}))
    assert store.pixel_size_source("20x") == "nominal"
    assert "4x" in store.invalid_calibrations()
