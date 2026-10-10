import os

import numpy as np
import pytest
import yaml

import control.models.objective_calibration_config as occ
from control.core.config.repository import ConfigRepository
from control.models.objective_calibration_config import (
    TRANSFORM_CHANGED,
    CurrentSetup,
    ImageTransform,
    ObjectiveCalibrationConfig,
    ObjectiveCalibrationFileError,
    build_pixel_record,
    clear_pixel_records,
    load_objective_calibration,
    merge_pixel_records,
    pixel_size_validity,
    review_save,
    save_objective_calibration,
    with_summary,
)
from squid.objective_calibration.engine import PixelSizeSummary

SETUP = CurrentSetup("TOUPCAM/ITR3CMOS26000KMA/unknown", 3.76, 180.0, ImageTransform())
OLD_SETUP = CurrentSetup("TOUPCAM/OLDCAMERA/unknown", 3.76, 180.0, ImageTransform())
D20 = {"magnification": 20.0, "na": 0.8, "tube_lens_f_mm": 180.0, "model": "", "serial": ""}
D10 = {**D20, "magnification": 10.0, "na": 0.3}
D40 = {**D20, "magnification": 40.0, "na": 0.95}


def _summary(px=0.188, matrix=None, rotation=0.1):
    m = np.asarray(matrix, dtype=float) if matrix is not None else px * np.eye(2)
    return PixelSizeSummary(
        objective="x",
        cycles=3,
        pixel_size_um=px,
        std_pixel_size_um=0.0003,
        matrix_um_per_px=m,
        rotation_deg=rotation,
        flip=np.eye(2),
        orientation_matches_mosaic=True,
        anisotropy=1.0,
        fit_residual_um=0.3,
    )


def _record(summary=None, *, declared=D20, setup=SETUP, binned=3.76, binning=1):
    return build_pixel_record(
        summary or _summary(),
        measured_at="2026-09-28T10:00:00",
        camera_key=setup.camera_key,
        unbinned_sensor_pixel_um=setup.unbinned_sensor_pixel_um,
        binned_sensor_pixel_um=binned,
        binning=binning,
        image_transform=setup.image_transform,
        tube_lens_mm=setup.tube_lens_mm,
        declared=declared,
    )


def test_record_normalizes_by_the_binned_sensor_pixel():
    # 20x on a 3.76 um sensor at binning 2: 7.52 um binned pixels, 0.376 um in the image
    record = _record(_summary(px=0.376), binned=7.52, binning=2)
    assert record.factor == pytest.approx(0.05)
    np.testing.assert_allclose(record.matrix_norm, 0.05 * np.eye(2))
    assert record.binning == 2 and record.pixel_size_um == pytest.approx(0.376)


def test_save_replaces_only_the_measured_blocks_and_preserves_other_keys(tmp_path):
    path = tmp_path / "objective_calibration.yaml"
    old_10x = _record(_summary(px=0.376), declared=D10).model_dump(mode="json", exclude_none=True)
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "future_section": {"camera_key": "c1"},
                "objectives": {"10x": {"pixel_size": old_10x, "xy_offset_um": [1.0, 2.0]}, "20x": {"z_offset_um": 3.5}},
            }
        ),
        encoding="utf-8",
    )
    merged = merge_pixel_records(load_objective_calibration(path), {"20x": _record()})
    save_objective_calibration(merged, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["future_section"] == {"camera_key": "c1"}
    assert raw["objectives"]["10x"] == {"pixel_size": old_10x, "xy_offset_um": [1.0, 2.0]}
    assert raw["objectives"]["20x"]["z_offset_um"] == 3.5
    assert raw["objectives"]["20x"]["pixel_size"]["factor"] == pytest.approx(0.188 / 3.76)
    assert load_objective_calibration(path) == merged


def test_clear_removes_only_the_named_pixel_blocks():
    config = ObjectiveCalibrationConfig.model_validate(
        {
            "objectives": {
                "20x": {"pixel_size": _record().model_dump(mode="json"), "z_offset_um": 3.5},
                "10x": {"pixel_size": _record(declared=D10).model_dump(mode="json")},
            }
        }
    )
    cleared = clear_pixel_records(config, ["20x", "10x"])
    assert cleared.objectives["20x"].pixel_size is None
    assert cleared.objectives["20x"].model_extra == {"z_offset_um": 3.5}
    assert "10x" not in cleared.objectives
    assert config.objectives["20x"].pixel_size is not None  # the input is untouched


@pytest.mark.parametrize(
    "setup, declared, reason",
    [
        (OLD_SETUP, D20, "camera changed"),
        (CurrentSetup(SETUP.camera_key, 3.45, 180.0, ImageTransform()), D20, "sensor pixel size changed"),
        (CurrentSetup(SETUP.camera_key, 3.76, 200.0, ImageTransform()), D20, "TUBE_LENS_MM changed"),
        (SETUP, {**D20, "magnification": 25.0}, "declared magnification or tube lens changed"),
        (SETUP, None, "not in the objective list"),
    ],
)
def test_a_changed_identity_invalidates_scale_and_matrix(setup, declared, reason):
    validity = pixel_size_validity(_record(), setup, declared)
    assert not validity.scalar and not validity.directional
    assert reason in validity.reason


def test_the_serial_counts_only_when_one_was_recorded():
    assert pixel_size_validity(_record(), SETUP, {**D20, "serial": "A123"}).scalar
    recorded = _record(declared={**D20, "serial": "A123"})
    assert pixel_size_validity(recorded, SETUP, {**D20, "serial": "A123"}).scalar
    validity = pixel_size_validity(recorded, SETUP, {**D20, "serial": "B456"})
    assert not validity.scalar and "serial" in validity.reason


def test_a_changed_image_transform_invalidates_only_the_matrix():
    flipped = CurrentSetup(SETUP.camera_key, 3.76, 180.0, ImageTransform(flip="Vertical"))
    validity = pixel_size_validity(_record(), flipped, D20)
    assert validity.scalar and not validity.directional
    assert validity.reason == TRANSFORM_CHANGED


def test_uncalibrated_is_invalid():
    assert pixel_size_validity(None, SETUP, D20) == occ.Validity(False, False, "not calibrated")
    assert pixel_size_validity(_record(), SETUP, D20) == occ.Validity(True, True, "")


def test_summary_comes_from_the_valid_blocks_only():
    config = merge_pixel_records(
        None,
        {
            "10x": _record(_summary(px=0.376, rotation=0.2), declared=D10),
            "20x": _record(_summary(rotation=0.1)),
            "40x": _record(_summary(px=0.094, rotation=5.0), declared=D40, setup=OLD_SETUP),
        },
    )
    summary = with_summary(config, SETUP, {"10x": D10, "20x": D20, "40x": D40}).pixel_calibration
    assert summary.camera_rotation_deg == pytest.approx(0.15)
    assert summary.orientation == {"col": "+x", "row": "+y"}
    assert summary.orientation_matches_mosaic


def test_summary_reports_a_flip_and_is_none_without_valid_blocks():
    flipped = merge_pixel_records(None, {"20x": _record(_summary(matrix=np.diag([0.188, -0.188])))})
    summary = with_summary(flipped, SETUP, {"20x": D20}).pixel_calibration
    assert summary.orientation == {"col": "+x", "row": "-y"} and not summary.orientation_matches_mosaic
    assert with_summary(flipped, OLD_SETUP, {"20x": D20}).pixel_calibration is None


def test_a_large_deviation_from_nominal_blocks_the_save():
    merged = merge_pixel_records(None, {"20x": _record(_summary(px=0.21))})
    blockers, _ = review_save(merged, {"20x": 0.21}, {"20x": 0.188}, SETUP, {"20x": D20})
    assert blockers == ["Calibrated pixel size for 20x differs from nominal by +11.7%; check the objective list."]


def test_objectives_disagreeing_on_orientation_block_the_save():
    merged = merge_pixel_records(
        None,
        {
            "10x": _record(_summary(px=0.376), declared=D10),
            "20x": _record(_summary(matrix=np.diag([0.188, -0.188]))),
        },
    )
    blockers, _ = review_save(
        merged, {"10x": 0.376, "20x": 0.188}, {"10x": 0.376, "20x": 0.188}, SETUP, {"10x": D10, "20x": D20}
    )
    assert blockers == ["Objectives disagree on camera orientation; this indicates a configuration error."]


def test_a_rotation_spread_only_warns():
    merged = merge_pixel_records(
        None, {"10x": _record(_summary(px=0.376, rotation=0.0), declared=D10), "20x": _record(_summary(rotation=0.5))}
    )
    blockers, warnings = review_save(merged, {"20x": 0.188}, {"20x": 0.188}, SETUP, {"10x": D10, "20x": D20})
    assert blockers == []
    assert warnings == [
        "Camera rotation differs between objectives by 0.50° (over 0.3°); the saved camera angle is their mean."
    ]


def test_an_unreadable_file_raises_and_is_never_treated_as_absent(tmp_path):
    path = tmp_path / "objective_calibration.yaml"
    path.write_text("objectives: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(ObjectiveCalibrationFileError, match="Fix or delete it"):
        load_objective_calibration(path)
    assert load_objective_calibration(tmp_path / "absent.yaml") is None


def test_a_failed_publish_keeps_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "objective_calibration.yaml"
    first = merge_pixel_records(None, {"20x": _record()})
    save_objective_calibration(first, path)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        save_objective_calibration(merge_pixel_records(first, {"10x": _record(declared=D10)}), path)
    assert load_objective_calibration(path) == first
    assert list(tmp_path.iterdir()) == [path]


def test_repository_reads_and_writes_machine_configs(tmp_path):
    repo = ConfigRepository(base_path=tmp_path)
    assert repo.get_objective_calibration() is None
    config = merge_pixel_records(None, {"20x": _record()})
    repo.save_objective_calibration(config)
    assert (tmp_path / "machine_configs" / "objective_calibration.yaml").exists()
    assert repo.get_objective_calibration() == config


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("factor", float("nan"), "finite number"),
        ("pixel_size_um", -0.19, "greater than 0"),
        ("matrix_norm", [[0.05]], "must be 2x2"),
        ("matrix_norm", [[0.05, 0.1], [0.025, 0.05]], "singular"),
    ],
)
def test_an_implausible_record_fails_to_load_and_is_never_overwritten(tmp_path, field, value, reason):
    path = tmp_path / "objective_calibration.yaml"
    data = _record().model_dump(mode="json")
    data[field] = value
    path.write_text(yaml.safe_dump({"objectives": {"20x": {"pixel_size": data}}}), encoding="utf-8")
    with pytest.raises(ObjectiveCalibrationFileError, match=reason):
        load_objective_calibration(path)


def test_a_newer_file_version_fails_to_load(tmp_path):
    path = tmp_path / "objective_calibration.yaml"
    path.write_text("version: 2\nobjectives: {}\n", encoding="utf-8")
    with pytest.raises(ObjectiveCalibrationFileError):
        load_objective_calibration(path)
