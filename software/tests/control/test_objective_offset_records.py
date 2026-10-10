"""C1's records: the offset section and blocks, their validity, and the Z frames (spec C §4-6.5)."""

import math

import pytest
import yaml

from control.models.objective_calibration_config import (
    CurrentSetup,
    ImageTransform,
    Mounting,
    ObjectiveCalibrationConfig,
    ObjectiveCalibrationFileError,
    OffsetCalibrationSection,
    OffsetCameraKey,
    OffsetValidity,
    build_offset_records,
    clear_offset_records,
    clear_pixel_records,
    implied_steps_um,
    load_objective_calibration,
    merge_offset_records,
    merge_pixel_records,
    offset_camera_key,
    offset_validity,
    parfocal_residuals_um,
    save_objective_calibration,
    step_cap_blockers,
    z_frames_um,
)
from squid.objective_calibration.offsets import OffsetSummary
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective
from tests.control.test_objective_calibration_config import D10, SETUP, _record

TURRET = {"4x": Mounting(changer="nimotion_turret", position=1), "10x": Mounting(changer="nimotion_turret", position=2)}
TURRET["20x"] = Mounting(changer="nimotion_turret", position=3)
# A camera driver whose ROI units are verified (unbinned): the key carries the ROI centre
CAMERA = OffsetCameraKey(
    camera="fake/camera/0", binning=1, roi=[0, 0, 2048, 2048], roi_centre_px=[1024.0, 1024.0], image_transform={}
)
# One whose ROI units are not verified: no centre, so the raw ROI and the binning key XY
UNVERIFIED = OffsetCameraKey(camera="fake/camera/0", binning=2, roi=[0, 0, 1024, 1024], image_transform={})
POS2_UM = 2000.0


def _summary(name, dx=12.3, dy=-4.1, dz=6.8, closure=0.6):
    return OffsetSummary(name, 3, dx, dy, dz, 0.3, 0.2, 0.9, 0.93, 0.41, 3.8, closure)


def _section(reference="4x", mounting=TURRET["4x"], camera=CAMERA, cycles=3):
    return OffsetCalibrationSection(
        reference_objective=reference,
        reference_mounting=mounting,
        measured_at="2026-09-28T14:03:11",
        channel="BF LED matrix full",
        cycles=cycles,
        camera_key=camera,
    )


def _calibrated(mountings=TURRET, save_xy=True, dz=None, pixel=True, camera=CAMERA):
    dz = dz or {"10x": 6.8, "20x": -3.2}
    summaries = {name: _summary(name, dz=value) for name, value in dz.items()}
    config = merge_pixel_records(None, {"10x": _record(declared=D10), "20x": _record()}) if pixel else None
    return merge_offset_records(
        config,
        _section(mounting=mountings["4x"], camera=camera),
        build_offset_records(summaries, mountings, save_xy=save_xy),
    )


def test_the_stored_block_matches_the_spec_and_survives_a_file_round_trip(tmp_path):
    config = _calibrated()
    path = tmp_path / "objective_calibration.yaml"
    save_objective_calibration(config, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["offset_calibration"] == {
        "reference_objective": "4x",
        "reference_mounting": {"changer": "nimotion_turret", "position": 1, "serial": ""},
        "measured_at": "2026-09-28T14:03:11",
        "channel": "BF LED matrix full",
        "cycles": 3,
        "camera_key": {
            "camera": "fake/camera/0",
            "binning": 1,
            "roi": [0, 0, 2048, 2048],
            "roi_centre_px": [1024.0, 1024.0],
            "image_transform": {},  # no rotation, no flip
        },
    }
    assert raw["objectives"]["10x"]["offset"] == {
        "mounting": {"changer": "nimotion_turret", "position": 2, "serial": ""},
        "dx_um": 12.3,
        "dy_um": -4.1,
        "dz_um": 6.8,
        "std_dx_um": 0.3,
        "std_dy_um": 0.2,
        "std_dz_um": 0.9,
        "match_score": 0.93,
        "runner_up_ratio": 0.41,
        "focus_peak_rise": 3.8,
        "closure_error_um": 0.6,
    }
    assert "offset" not in raw["objectives"].get("4x", {})  # the reference has no block: its offset is (0, 0, 0)
    assert load_objective_calibration(path) == config


def test_a_pixel_size_save_or_clear_never_touches_the_offset_data():
    config = _calibrated()
    resaved = merge_pixel_records(config, {"20x": _record()})
    assert resaved.offset_calibration == config.offset_calibration
    assert resaved.objectives["10x"].offset == config.objectives["10x"].offset
    cleared = clear_pixel_records(config, ["10x", "20x"])
    assert cleared.objectives["10x"].pixel_size is None and cleared.objectives["10x"].offset is not None


FOUR = {**TURRET, "40x": Mounting(changer="nimotion_turret", position=4)}


def test_saving_offsets_replaces_the_section_and_every_offset_block():
    old = _calibrated(mountings=FOUR, dz={"10x": 6.8, "20x": -3.2, "40x": 1.0})
    new = merge_offset_records(
        old, _section(cycles=1), build_offset_records({"10x": _summary("10x", dz=7.0)}, TURRET, save_xy=True)
    )
    assert new.offset_calibration.cycles == 1
    assert new.objectives["10x"].offset.dz_um == 7.0
    assert new.objectives["20x"].offset is None and new.objectives["20x"].pixel_size is not None  # B's block kept
    assert "40x" not in new.objectives  # nothing else was recorded for it
    assert old.objectives["40x"].offset is not None  # the input is untouched


def test_clear_offsets_removes_the_section_and_every_block_and_drops_empty_entries():
    cleared = clear_offset_records(_calibrated(mountings=FOUR, dz={"10x": 6.8, "40x": 1.0}))
    assert cleared.offset_calibration is None
    assert cleared.objectives["10x"].offset is None and cleared.objectives["10x"].pixel_size is not None
    assert "40x" not in cleared.objectives
    assert clear_offset_records(None) == ObjectiveCalibrationConfig()


def test_the_orientation_gate_saves_blocks_without_xy():
    config = _calibrated(save_xy=False)
    block = config.objectives["10x"].offset
    assert block.dx_um is None and block.dy_um is None and block.std_dx_um is None
    assert block.dz_um == 6.8 and block.std_dz_um == 0.9
    validity = offset_validity(config, TURRET, CAMERA)
    assert validity.z and not validity.xy and "XY offsets were not saved" in validity.reason


def test_uncalibrated_is_invalid():
    assert offset_validity(None, TURRET, CAMERA) == OffsetValidity(False, False, "not calibrated")
    assert offset_validity(ObjectiveCalibrationConfig(), TURRET, CAMERA) == OffsetValidity(
        False, False, "not calibrated"
    )
    assert offset_validity(_calibrated(), TURRET, CAMERA) == OffsetValidity(True, True, "")


@pytest.mark.parametrize(
    "current, reason",
    [
        ({"10x": TURRET["10x"], "20x": TURRET["20x"]}, "reference objective 4x is no longer installed"),
        ({**TURRET, "4x": Mounting(changer="nimotion_turret", position=4)}, "reference objective 4x was remounted"),
        ({"4x": TURRET["4x"], "20x": TURRET["20x"]}, "10x is no longer installed"),
        ({**TURRET, "20x": Mounting(changer="nimotion_turret", position=4)}, "20x was remounted"),
    ],
)
def test_any_mounting_mismatch_invalidates_z_and_xy(current, reason):
    validity = offset_validity(_calibrated(), current, CAMERA)
    assert not validity.z and not validity.xy and reason in validity.reason


def test_the_serial_counts_only_when_one_was_recorded():
    with_serial = {**TURRET, "20x": Mounting(changer="nimotion_turret", position=3, serial="A123")}
    assert offset_validity(_calibrated(), with_serial, CAMERA).z  # recorded empty: ignored
    recorded = _calibrated(mountings=with_serial)
    assert offset_validity(recorded, with_serial, CAMERA).z
    changed = {**TURRET, "20x": Mounting(changer="nimotion_turret", position=3, serial="B456")}
    assert not offset_validity(recorded, changed, CAMERA).z
    cleared = {**TURRET, "20x": Mounting(changer="nimotion_turret", position=3, serial="")}
    assert not offset_validity(recorded, cleared, CAMERA).z


@pytest.mark.parametrize(
    "change, xy, reason",
    [
        (dict(camera="other/camera/1"), False, "camera changed"),
        (dict(roi=[200, 0, 2048, 2048], roi_centre_px=[1224.0, 1024.0]), False, "camera ROI centre changed"),
        # the physical centre is unchanged: binning and ROI size do not move XY (spec C §5)
        (dict(binning=2), True, ""),
        (dict(roi=[512, 512, 1024, 1024]), True, ""),
        # the external review's case: ROI and binning changed together, and the centre moved 1024 -> 512
        (dict(binning=2, roi=[0, 0, 1024, 1024], roi_centre_px=[512.0, 512.0]), False, "camera ROI centre changed"),
        (dict(image_transform=ImageTransform(flip="Vertical")), False, "camera image rotation/flip changed"),
        (dict(image_transform=ImageTransform(rotate_deg=90.0)), False, "camera image rotation/flip changed"),
    ],
)
def test_the_camera_key_governs_xy_only(change, xy, reason):
    validity = offset_validity(_calibrated(), TURRET, CAMERA.model_copy(update=change))
    assert validity.z and validity.xy == xy and reason in validity.reason


@pytest.mark.parametrize(
    "change",
    [
        dict(binning=1),  # even with the same physical centre, if this driver's ROI were binned pixels
        dict(binning=1, roi=[0, 0, 2048, 2048]),
        dict(roi=[0, 0, 1024, 1020]),
        dict(roi=[512, 0, 1024, 1024]),
    ],
)
def test_with_unverified_roi_units_any_roi_or_binning_change_invalidates_xy(change):
    config = _calibrated(camera=UNVERIFIED)
    assert offset_validity(config, TURRET, UNVERIFIED) == OffsetValidity(True, True, "")
    validity = offset_validity(config, TURRET, UNVERIFIED.model_copy(update=change))
    assert validity.z and not validity.xy and "camera ROI or binning changed" in validity.reason


def test_the_key_is_taken_from_the_hardware_seam():
    hw = FakeCalibrationHardware({"20x": FakeObjective("20x", 20, 0.8, pixel_um=0.32)}, shape=(100, 200))
    hw.set_roi(40, 0, 200, 100)
    assert offset_camera_key(hw) == OffsetCameraKey(
        camera="fake/camera/0", binning=1, roi=[40, 0, 200, 100], roi_centre_px=[140.0, 50.0], image_transform={}
    )
    hw.roi_centre_px = lambda: None  # a driver whose ROI units are unverified
    hw.image_transform = lambda: (90.0, "Vertical")
    key = offset_camera_key(hw)
    assert key.roi_centre_px is None and key.image_transform == ImageTransform(rotate_deg=90.0, flip="Vertical")


def test_a_pixel_size_only_save_after_a_flip_leaves_xy_invalid_until_offsets_are_recalibrated():
    flipped = CAMERA.model_copy(update=dict(image_transform=ImageTransform(flip="Vertical")))
    setup = CurrentSetup(SETUP.camera_key, SETUP.unbinned_sensor_pixel_um, SETUP.tube_lens_mm, flipped.image_transform)
    after_b_only = merge_pixel_records(
        _calibrated(), {"10x": _record(declared=D10, setup=setup), "20x": _record(setup=setup)}
    )
    validity = offset_validity(after_b_only, TURRET, flipped)
    assert validity.z and not validity.xy and "rotation/flip changed" in validity.reason  # B's save kept C's key
    recalibrated = merge_offset_records(
        after_b_only, _section(camera=flipped), build_offset_records({"10x": _summary("10x")}, TURRET, save_xy=True)
    )
    assert offset_validity(recalibrated, TURRET, flipped) == OffsetValidity(True, True, "")


def test_a_pixel_size_only_save_after_an_roi_change_leaves_xy_invalid_until_offsets_are_recalibrated():
    moved = CAMERA.model_copy(update=dict(roi=[200, 0, 2048, 2048], roi_centre_px=[1224.0, 1024.0]))
    config = _calibrated()
    after_b_only = merge_pixel_records(config, {"10x": _record(declared=D10), "20x": _record()})
    assert not offset_validity(after_b_only, TURRET, moved).xy  # B's save does not rewrite C's key
    recalibrated = merge_offset_records(
        after_b_only, _section(camera=moved), build_offset_records({"10x": _summary("10x")}, TURRET, save_xy=True)
    )
    assert offset_validity(recalibrated, TURRET, moved) == OffsetValidity(True, True, "")


XERYON = {"4x": Mounting(changer="xeryon", position=1), "20x": Mounting(changer="xeryon", position=2)}


def test_stored_dz_is_raw_and_the_switch_step_subtracts_the_xeryon_frame():
    # The 20x parks 2 mm lower; its raw focus difference is -1994 um, its parfocal residual +6 um
    config = _calibrated(mountings=XERYON, dz={"20x": -1994.0}, pixel=False)
    assert config.objectives["20x"].offset.dz_um == -1994.0
    frames = z_frames_um(config, XERYON, POS2_UM)
    assert frames == {"4x": 0.0, "20x": -1994.0}
    residuals = parfocal_residuals_um(config, XERYON, POS2_UM)
    assert residuals == {"4x": 0.0, "20x": pytest.approx(6.0)}
    assert implied_steps_um(config, XERYON, POS2_UM) == {("4x", "20x"): pytest.approx(6.0)}


def test_uncalibrated_objectives_keep_todays_frame_and_steps_are_path_independent():
    three = {**XERYON, "40x": Mounting(changer="xeryon", position=2)}
    config = _calibrated(mountings=XERYON, dz={"20x": -1994.0}, pixel=False)
    residuals = parfocal_residuals_um(config, three, POS2_UM)
    assert residuals["40x"] == 0.0  # today's frame: assumed parfocal after the changer's own move
    steps = implied_steps_um(config, three, POS2_UM)
    assert steps[("4x", "20x")] == pytest.approx(6.0)
    assert steps[("20x", "40x")] == pytest.approx(-6.0)  # the calibrated one's correction is carried
    assert steps[("4x", "40x")] == pytest.approx(0.0)
    assert steps[("4x", "20x")] + steps[("20x", "40x")] - steps[("4x", "40x")] == pytest.approx(0.0)


def test_a_reference_dropped_from_the_xeryon_lists_is_invalid_not_a_2_mm_step():
    # The review's hazard: a position-2 reference no longer in the POS lists must invalidate, never imply a step
    config = _calibrated(
        mountings={"4x": Mounting(changer="xeryon", position=2), "20x": XERYON["20x"]}, dz={"20x": 3.0}, pixel=False
    )
    now = {"4x": Mounting(changer="xeryon", position=None), "20x": XERYON["20x"]}
    validity = offset_validity(config, now, CAMERA)
    assert not validity.z and "reference objective 4x was remounted" in validity.reason


def test_the_step_cap_blocks_a_save():
    config = _calibrated(dz={"10x": 6.8, "20x": 620.0})  # a 0.62 mm implied step: the changer is misconfigured
    steps = implied_steps_um(config, TURRET, 0.0)
    assert steps[("4x", "20x")] == pytest.approx(620.0)
    blockers = step_cap_blockers(steps, 500.0)
    assert blockers == [
        "Implied Z step 0.620 mm between 4x and 20x exceeds the cap (0.5 mm); check the changer configuration.",
        "Implied Z step 0.613 mm between 10x and 20x exceeds the cap (0.5 mm); check the changer configuration.",
    ]
    assert step_cap_blockers(implied_steps_um(_calibrated(), TURRET, 0.0), 500.0) == []


def test_an_implausible_offset_record_fails_to_load(tmp_path):
    path = tmp_path / "objective_calibration.yaml"
    save_objective_calibration(_calibrated(), path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["objectives"]["10x"]["offset"]["dz_um"] = math.nan
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ObjectiveCalibrationFileError, match="finite"):
        load_objective_calibration(path)
