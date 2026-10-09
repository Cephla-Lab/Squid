"""ObjectiveStore's objective offsets (spec C §7.1): the effective XY offsets and Z frames, their
validity, the switch step (§7.2, §4) and the calibration-changed notification."""

import pytest

import control._def
from control.core.objective_store import CalibrationChange, ObjectiveStore, default_xeryon_frame_mm
from control.models.objective_calibration_config import (
    ImageTransform,
    Mounting,
    ObjectiveCalibrationConfig,
    ObjectiveRecords,
    OffsetCalibrationSection,
    OffsetCameraKey,
    OffsetRecord,
)

OBJECTIVES = {
    "4x": {"magnification": 4, "NA": 0.13, "tube_lens_f_mm": 180},
    "20x": {"magnification": 20, "NA": 0.8, "tube_lens_f_mm": 180},
    "40x": {"magnification": 40, "NA": 0.95, "tube_lens_f_mm": 180},
}
TURRET = {
    "4x": Mounting(changer="nimotion_turret", position=1),
    "20x": Mounting(changer="nimotion_turret", position=3),
    "40x": Mounting(changer="nimotion_turret", position=4),
}
XERYON = {
    "4x": Mounting(changer="xeryon", position=1),
    "20x": Mounting(changer="xeryon", position=2),
    "40x": Mounting(changer="xeryon", position=2),
}
# A camera driver whose ROI units are verified (unbinned): the key carries the ROI centre (spec C §5)
CAMERA = OffsetCameraKey(
    camera="fake/camera/0",
    binning=1,
    roi=[0, 0, 2048, 2048],
    roi_centre_px=[1024.0, 1024.0],
    image_transform=ImageTransform(),
)
POS2_MM = 2.0


def _record(mounting, dx_um, dy_um, dz_um):
    return OffsetRecord(
        mounting=mounting,
        dx_um=dx_um,
        dy_um=dy_um,
        dz_um=dz_um,
        match_score=0.9,
        runner_up_ratio=0.4,
        focus_peak_rise=3.0,
    )


def _config(reference, reference_mounting, records, camera=CAMERA):
    """A calibration with `records` = {name: (mounting, dx_um, dy_um, dz_um)} for the non-reference objectives."""
    section = OffsetCalibrationSection(
        reference_objective=reference,
        reference_mounting=reference_mounting,
        measured_at="2026-10-09T10:00:00",
        channel="BF",
        cycles=3,
        camera_key=camera,
    )
    objectives = {name: ObjectiveRecords(offset=_record(*values)) for name, values in records.items()}
    return ObjectiveCalibrationConfig(offset_calibration=section, objectives=objectives)


# Turret: 20x calibrated at +6 um parfocal residual and (+12, -7) um parcentric; 40x uncalibrated.
TURRET_CONFIG = _config("4x", TURRET["4x"], {"20x": (TURRET["20x"], 12.0, -7.0, 6.0)})


def _store(config=TURRET_CONFIG, mountings=TURRET, camera=CAMERA, pos2_mm=0.0):
    store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
    store.set_offset_calibration(config, mountings, camera, pos2_mm)
    return store


def _spec_step(store, old, new):
    """Spec C §4 / §7.2, written out: the part of the frame difference the changer did not do."""
    xeryon = {name: default_xeryon_frame_mm(name) for name in (old, new)}
    return (store.z_frame_mm(new) - store.z_frame_mm(old)) - (xeryon[new] - xeryon[old])


class TestUncalibrated:
    def test_a_fresh_store_applies_nothing(self):
        store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
        assert store.xy_offset_mm("20x") == (0.0, 0.0)
        assert store.z_frame_mm("20x") == 0.0
        assert store.z_switch_step_mm("4x", "20x") == 0.0
        assert not store.offset_validity.z and not store.offset_validity.xy

    def test_no_saved_calibration_means_todays_frames_and_zero_steps(self):
        store = _store(config=None, mountings=XERYON, pos2_mm=POS2_MM)
        assert store.z_frame_mm("4x") == 0.0
        assert store.z_frame_mm("20x") == pytest.approx(-POS2_MM)  # today's Xeryon frame, position 2
        assert store.z_switch_step_mm("4x", "20x") == 0.0
        assert store.xy_offset_mm("20x") == (0.0, 0.0)

    def test_default_frame_follows_the_live_xeryon_configuration(self, monkeypatch):
        monkeypatch.setattr(control._def, "USE_XERYON", True)
        monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_1", ["4x"])
        monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2", ["20x"])
        monkeypatch.setattr(control._def, "XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM", 2)
        store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
        assert store.z_frame_mm("20x") == pytest.approx(-2.0)
        assert store.z_frame_mm("4x") == 0.0
        assert store.z_frame_mm("40x") == 0.0  # not in either list
        monkeypatch.setattr(control._def, "USE_XERYON", False)
        assert store.z_frame_mm("20x") == 0.0


class TestOffsetsAndFrames:
    def test_xy_offsets_in_mm_for_calibrated_objectives_only(self):
        store = _store()
        assert store.xy_offset_mm("20x") == pytest.approx((0.012, -0.007))
        assert store.xy_offset_mm("4x") == (0.0, 0.0)  # the reference
        assert store.xy_offset_mm("40x") == (0.0, 0.0)  # uncalibrated: assumed parcentric with the reference

    def test_z_frames(self):
        store = _store()
        assert store.z_frame_mm("4x") == 0.0
        assert store.z_frame_mm("20x") == pytest.approx(0.006)
        assert store.z_frame_mm("40x") == 0.0  # uncalibrated: today's frame

    def test_step_matches_the_spec_formula_and_the_calibrated_direction(self):
        store = _store()
        # 20x focuses 6 um higher than 4x: the switch moves Z up by 6 um.
        assert store.z_switch_step_mm("4x", "20x") == pytest.approx(0.006)
        assert store.z_switch_step_mm("20x", "4x") == pytest.approx(-0.006)
        # calibrated -> uncalibrated carries the calibrated one's correction (spec C §4)
        assert store.z_switch_step_mm("20x", "40x") == pytest.approx(-0.006)
        for old, new in [("4x", "20x"), ("20x", "40x"), ("40x", "4x"), ("4x", "40x")]:
            assert store.z_switch_step_mm(old, new) == pytest.approx(_spec_step(store, old, new))

    def test_path_independence_with_an_uncalibrated_objective(self):
        store = _store()
        loop = store.z_switch_step_mm("4x", "20x") + store.z_switch_step_mm("20x", "40x")
        loop += store.z_switch_step_mm("40x", "4x")
        assert loop == pytest.approx(0.0, abs=1e-12)


class TestXeryonFrame:
    """Stored dz is the raw focus difference, Xeryon frame included; the frame is subtracted at use (spec C §4)."""

    def test_step_subtracts_the_changers_own_move(self):
        # Reference 4x at position 1; 20x at position 2 sits 2 mm lower by the changer, and 6 um higher than that.
        config = _config("4x", XERYON["4x"], {"20x": (XERYON["20x"], 0.0, 0.0, -2000.0 + 6.0)})
        store = _store(config=config, mountings=XERYON, pos2_mm=POS2_MM)
        assert store.offset_validity.z
        assert store.z_frame_mm("20x") == pytest.approx(-2.0 + 0.006)
        assert store.z_frame_mm("40x") == pytest.approx(-2.0)  # uncalibrated, position 2: today's frame
        assert store.z_switch_step_mm("4x", "20x") == pytest.approx(0.006)  # not -1.994
        assert store.z_switch_step_mm("20x", "40x") == pytest.approx(-0.006)
        assert store.z_switch_step_mm("40x", "4x") == pytest.approx(0.0)
        loop = sum(store.z_switch_step_mm(a, b) for a, b in [("4x", "20x"), ("20x", "40x"), ("40x", "4x")])
        assert loop == pytest.approx(0.0, abs=1e-12)

    def test_pos2_reference_dropped_from_the_lists_applies_no_step(self):
        """The round-2 hazard (spec C §9): a POS_2 reference removed from the Xeryon lists must give no
        step at all, never the +2.003 mm step a name lookup returning frame 0 would imply."""
        # Reference 20x at position 2 (frame -2 mm); 4x at position 1 focuses 2.003 mm higher: 2 mm is the changer's.
        config = _config("20x", XERYON["20x"], {"4x": (XERYON["4x"], 0.0, 0.0, 2003.0)})
        valid = _store(config=config, mountings=XERYON, pos2_mm=POS2_MM)
        assert valid.z_switch_step_mm("20x", "4x") == pytest.approx(0.003)
        dropped = dict(XERYON, **{"20x": Mounting(changer="xeryon", position=None)})
        store = _store(config=config, mountings=dropped, pos2_mm=POS2_MM)
        assert not store.offset_validity.z
        assert "remounted" in store.offset_validity.reason
        assert store.z_switch_step_mm("20x", "4x") == 0.0
        assert store.z_switch_step_mm("4x", "20x") == 0.0
        assert store.xy_offset_mm("4x") == (0.0, 0.0)


class TestValidity:
    def test_a_remounted_objective_invalidates_everything(self):
        remounted = dict(TURRET, **{"20x": Mounting(changer="nimotion_turret", position=2)})
        store = _store(mountings=remounted)
        assert not store.offset_validity.z and not store.offset_validity.xy
        assert store.z_switch_step_mm("4x", "20x") == 0.0
        assert store.xy_offset_mm("20x") == (0.0, 0.0)

    def test_a_missing_reference_invalidates_everything(self):
        store = _store(mountings={name: TURRET[name] for name in ("20x", "40x")})
        assert not store.offset_validity.z
        assert store.z_switch_step_mm("20x", "40x") == 0.0

    def test_roi_centre_change_keeps_z_and_drops_xy(self):
        store = _store()
        moved = CAMERA.model_copy(update={"roi": [200, 0, 1648, 2048], "roi_centre_px": [1024.0 + 100.0, 1024.0]})
        change = store.refresh_offset_camera_key(moved)
        assert store.offset_validity.z and not store.offset_validity.xy
        assert store.xy_offset_mm("20x") == (0.0, 0.0)
        assert store.z_switch_step_mm("4x", "20x") == pytest.approx(0.006)
        assert change.xy_offsets and not change.z_offsets and change.validity_flipped

    def test_binning_change_keeps_both(self):
        store = _store()
        binned = CAMERA.model_copy(update={"binning": 2, "roi": [0, 0, 1024, 1024]})
        change = store.refresh_offset_camera_key(binned)
        assert store.offset_validity.z and store.offset_validity.xy
        assert store.xy_offset_mm("20x") == pytest.approx((0.012, -0.007))
        assert change == CalibrationChange()

    def test_unverified_roi_units_drop_xy_on_a_binning_change(self):
        unverified = CAMERA.model_copy(update={"roi_centre_px": None})
        config = _config("4x", TURRET["4x"], {"20x": (TURRET["20x"], 12.0, -7.0, 6.0)}, camera=unverified)
        store = _store(config=config, camera=unverified)
        assert store.offset_validity.xy
        store.refresh_offset_camera_key(unverified.model_copy(update={"binning": 2}))
        assert store.offset_validity.z and not store.offset_validity.xy


class TestNotification:
    def test_listener_sees_a_save_a_clear_and_a_validity_flip(self):
        store = ObjectiveStore(objectives_dict=OBJECTIVES, default_objective="4x")
        changes = []
        store.add_calibration_listener(changes.append)
        store.set_offset_calibration(TURRET_CONFIG, TURRET, CAMERA, 0.0)
        assert changes[-1].xy_offsets and changes[-1].z_offsets and changes[-1].validity_flipped
        # the same calibration again: nothing effective changed, nothing reported
        store.set_offset_calibration(TURRET_CONFIG, TURRET, CAMERA, 0.0)
        assert len(changes) == 1
        # ROI centre moved: XY drops, Z stays
        moved = CAMERA.model_copy(update={"roi_centre_px": [1124.0, 1024.0]})
        store.refresh_offset_camera_key(moved)
        assert len(changes) == 2 and changes[-1].xy_offsets and not changes[-1].z_offsets
        assert "ROI centre" in changes[-1].reason
        # cleared: Z goes too
        store.set_offset_calibration(None, TURRET, moved, 0.0)
        assert len(changes) == 3 and changes[-1].z_offsets and not changes[-1].xy_offsets  # XY was already zero
        assert store.z_switch_step_mm("4x", "20x") == 0.0

    def test_a_z_only_change_does_not_report_xy(self):
        store = _store()
        changes = []
        store.add_calibration_listener(changes.append)
        config = _config("4x", TURRET["4x"], {"20x": (TURRET["20x"], 12.0, -7.0, 9.0)})
        store.set_offset_calibration(config, TURRET, CAMERA, 0.0)
        assert changes == [CalibrationChange(z_offsets=True)]
        assert store.z_switch_step_mm("4x", "20x") == pytest.approx(0.009)
