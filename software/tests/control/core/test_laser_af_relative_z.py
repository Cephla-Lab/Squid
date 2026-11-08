"""Reference identity, last lock-on record and the relative-z anchor rule of the laser AF controller."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from qtpy.QtCore import QObject

from control.core.laser_auto_focus_controller import (
    LOCK_ON_FAILED_MESSAGE,
    LaserAutofocusController,
    RelativeZError,
)
from control.models import LaserAFConfig
from squid.abc import Pos


class FakePiezo:
    def __init__(self, position=100.0):
        self.position = position
        self.range_um = (0.0, 300.0)

    def move_to(self, um):
        self.position = um

    def move_relative(self, um):
        self.position += um


class FakeStage:
    def __init__(self):
        self.x_mm, self.y_mm, self.z_mm = 10.0, 20.0, 1.0

    def get_pos(self):
        return Pos(x_mm=self.x_mm, y_mm=self.y_mm, z_mm=self.z_mm, theta_rad=None)

    def move_z(self, mm):
        self.z_mm += mm

    def move_z_to(self, mm):
        self.z_mm = mm


def make_config(image=None, x_reference=50.0, **kw):
    cfg = LaserAFConfig(x_offset=0, y_offset=0, width=64, height=32, pixel_to_um=6.0, calibration_timestamp="t0", **kw)
    cfg.set_reference_image(np.arange(16, dtype=np.float32).reshape(4, 4) if image is None else image)
    return cfg.model_copy(update={"x_reference": x_reference})


def _build(piezo):
    c = LaserAutofocusController.__new__(LaserAutofocusController)
    QObject.__init__(c)
    c._log = MagicMock()
    c.piezo = piezo
    c.stage = FakeStage()
    c.objectiveStore = SimpleNamespace(current_objective="20x")
    c.camera = MagicMock()
    c.microcontroller = MagicMock()
    c.liveController = SimpleNamespace(
        microscope=SimpleNamespace(config_repo=SimpleNamespace(current_profile="p1", save_laser_af_config=MagicMock()))
    )
    c.laser_af_properties = LaserAFConfig()
    c.reference_crop = None
    c.is_initialized = False
    c.last_lock_on = None
    c.image = None
    c.initialize_manual(make_config())

    c.next_reference = 100.0  # actuator position of the reference plane
    c.fail_mode = None  # None | "false" | "raise_after_move"
    c.move_to_target_calls = []
    c.move_to_target_started_at = []

    def fake_move_to_target(target):
        c.move_to_target_calls.append(target)
        c.move_to_target_started_at.append(c.actuator_position_um())
        if c.fail_mode == "false":
            return False
        c.move_actuator_to_um(c.next_reference)
        if c.fail_mode == "raise_after_move":
            raise RuntimeError("boom")
        return True

    c.move_to_target = fake_move_to_target
    return c


@pytest.fixture
def ctrl():
    return _build(FakePiezo())


@pytest.fixture
def ctrl_without_piezo():
    return _build(None)


def reload_config(c, **kw):
    c.initialize_manual(make_config(**kw))


def clone(c):
    d = _build(c.piezo)
    d.laser_af_properties = c.laser_af_properties.model_copy()
    d.reference_crop = c.reference_crop
    return d


def test_identity_is_none_without_reference(ctrl):
    ctrl.laser_af_properties = ctrl.laser_af_properties.model_copy(update={"has_reference": False})
    assert ctrl.reference_identity is None


def test_identity_changes_with_objective_profile_x_image_calibration_and_roi(ctrl):
    base = ctrl.reference_identity
    assert base is not None

    def switch_objective(c):
        c.objectiveStore.current_objective = "40x"

    def switch_profile(c):
        c.liveController.microscope.config_repo.current_profile = "p2"

    def update(**kw):
        return lambda c: setattr(c, "laser_af_properties", c.laser_af_properties.model_copy(update=kw))

    def new_reference_image(c):
        c.reference_crop = np.ones((4, 4), dtype=np.float32)

    for mutate in (
        switch_objective,
        switch_profile,
        update(x_reference=51.0),
        new_reference_image,
        update(pixel_to_um=7.0),
        update(calibration_timestamp="t1"),
        update(x_offset=8),
        update(y_offset=2),
        update(width=128),
        update(height=64),
    ):
        c = clone(ctrl)
        mutate(c)
        assert c.reference_identity != base, mutate


def test_identical_reload_keeps_the_record(ctrl):
    ctrl.record_lock_on()
    reload_config(ctrl)
    assert ctrl.valid_lock_on() is not None


def test_reload_with_new_reference_image_same_x_clears_record(ctrl):
    ctrl.record_lock_on()
    reload_config(ctrl, image=np.ones((4, 4), dtype=np.float32))
    assert ctrl.valid_lock_on() is None


def test_reload_with_different_x_clears_record(ctrl):
    ctrl.record_lock_on()
    reload_config(ctrl, x_reference=60.0)
    assert ctrl.valid_lock_on() is None


def test_set_reference_records_a_lock_on_at_the_current_position(ctrl):
    ctrl.piezo.position = 123.0
    ctrl._get_laser_spot_centroid = lambda *a, **k: (30.0, 16.0)
    ctrl.image = np.random.default_rng(0).random((32, 64)).astype(np.float32) + 1.0
    ctrl.laser_af_properties = ctrl.laser_af_properties.model_copy(update={"spot_crop_size": 8})
    assert ctrl.set_reference() is True
    rec = ctrl.valid_lock_on()
    assert rec is not None and rec.anchor_um == 123.0
    assert (rec.x_mm, rec.y_mm, rec.stage_z_mm) == (10.0, 20.0, 1.0)


def test_failed_set_reference_records_nothing(ctrl):
    ctrl._get_laser_spot_centroid = lambda *a, **k: None
    assert ctrl.set_reference() is False
    assert ctrl.last_lock_on is None


def test_measure_reuses_the_anchor_without_moving_when_the_stage_is_still(ctrl):
    ctrl.piezo.position = 100.0
    ctrl.record_lock_on()
    ctrl.piezo.position = 115.0
    m = ctrl.measure_relative_z_um()
    assert (m.z_rel_um, m.anchor_reused) == (15.0, True)
    assert ctrl.move_to_target_calls == []
    assert ctrl.piezo.position == 115.0


def test_measure_after_a_stage_move_relocks_from_the_last_anchor_and_returns(ctrl):
    ctrl.piezo.position = 100.0
    ctrl.record_lock_on()
    ctrl.stage.x_mm += 1.0
    ctrl.piezo.position = 140.0  # far above: spot unreadable here
    ctrl.next_reference = 103.0
    m = ctrl.measure_relative_z_um()
    assert ctrl.move_to_target_started_at == [100.0]  # started at the old anchor, not 140
    assert ctrl.move_to_target_calls == [0.0]
    assert (m.z_rel_um, m.anchor_reused, ctrl.piezo.position) == (37.0, False, 140.0)
    # the new anchor is recorded at the new XY
    rec = ctrl.valid_lock_on()
    assert rec.anchor_um == 103.0 and rec.x_mm == ctrl.stage.x_mm


def test_stage_z_change_on_a_piezo_scope_counts_as_moved(ctrl):
    ctrl.record_lock_on()
    ctrl.stage.z_mm += 0.01
    m = ctrl.measure_relative_z_um()
    assert m.anchor_reused is False


def test_small_stage_jitter_within_tolerance_reuses(ctrl):
    ctrl.record_lock_on()
    ctrl.stage.x_mm += 0.0005
    ctrl.stage.z_mm += 0.0002
    assert ctrl.measure_relative_z_um().anchor_reused is True


def test_measure_without_a_record_locks_on_from_z_now(ctrl):
    ctrl.piezo.position = 130.0
    ctrl.next_reference = 100.0
    m = ctrl.measure_relative_z_um()
    assert ctrl.move_to_target_started_at == [130.0]
    assert (m.z_rel_um, m.anchor_reused, ctrl.piezo.position) == (30.0, False, 130.0)
    assert ctrl.valid_lock_on().anchor_um == 100.0


@pytest.mark.parametrize("mode", ["false", "raise_after_move"])
def test_failed_measure_stores_nothing_and_leaves_the_actuator_at_z_now(ctrl, mode):
    ctrl.piezo.position = 100.0
    ctrl.record_lock_on()
    before = ctrl.last_lock_on
    ctrl.stage.x_mm += 1.0
    ctrl.piezo.position = 140.0
    ctrl.fail_mode = mode
    with pytest.raises(RelativeZError) as e:
        ctrl.measure_relative_z_um()
    assert str(e.value) == LOCK_ON_FAILED_MESSAGE
    assert ctrl.piezo.position == 140.0
    assert ctrl.last_lock_on is before


def test_failed_measure_without_record_leaves_actuator_at_z_now(ctrl):
    ctrl.piezo.position = 130.0
    ctrl.fail_mode = "raise_after_move"
    with pytest.raises(RelativeZError):
        ctrl.measure_relative_z_um()
    assert ctrl.piezo.position == 130.0
    assert ctrl.last_lock_on is None


def test_measure_refuses_without_a_reference(ctrl):
    ctrl.laser_af_properties = ctrl.laser_af_properties.model_copy(update={"has_reference": False})
    with pytest.raises(RelativeZError) as e:
        ctrl.measure_relative_z_um()
    assert str(e.value) == "Set the laser AF reference first."


def test_measure_refuses_when_not_initialized(ctrl):
    ctrl.is_initialized = False
    with pytest.raises(RelativeZError) as e:
        ctrl.measure_relative_z_um()
    assert str(e.value) == "Set the laser AF reference first."


def test_stepper_scope_measures_and_moves_the_stage(ctrl_without_piezo):
    c = ctrl_without_piezo
    assert c.actuator_position_um() == pytest.approx(1000.0)
    c.next_reference = 1000.0
    c.record_lock_on()
    assert c.last_lock_on.stage_z_mm is None
    c.stage.z_mm = 1.02  # stepper moved: actuator is the stage, so the XY-only check still reuses
    m = c.measure_relative_z_um()
    assert m.anchor_reused is True and m.z_rel_um == pytest.approx(20.0)
    # moved in XY: re-lock from the anchor, then back
    c.stage.x_mm += 1.0
    c.next_reference = 1005.0
    m = c.measure_relative_z_um()
    assert c.move_to_target_started_at[-1] == pytest.approx(1000.0)
    assert (m.anchor_reused, m.z_rel_um) == (False, pytest.approx(15.0))
    assert c.stage.z_mm == pytest.approx(1.02)


def test_failed_pre_move_raises_relative_z_error_and_restores(ctrl):
    ctrl.piezo.position = 100.0
    ctrl.record_lock_on()
    ctrl.stage.x_mm += 1.0
    ctrl.piezo.position = 140.0
    real_move_to = ctrl.piezo.move_to
    calls = []

    def flaky(um):
        calls.append(um)
        if len(calls) == 1:
            raise OSError("piezo offline")
        real_move_to(um)

    ctrl.piezo.move_to = flaky
    with pytest.raises(RelativeZError) as e:
        ctrl.measure_relative_z_um()
    assert str(e.value) == LOCK_ON_FAILED_MESSAGE
    assert isinstance(e.value.__cause__, OSError)
    assert ctrl.move_to_target_calls == []
    assert ctrl.piezo.position == 140.0  # restore to z_now still attempted


def test_failed_restore_does_not_mask_the_original_error(ctrl):
    ctrl.record_lock_on()
    ctrl.stage.x_mm += 1.0
    ctrl.fail_mode = "false"
    ctrl.piezo.move_to = MagicMock(side_effect=OSError("restore failed"))
    with pytest.raises(RelativeZError) as e:
        ctrl.measure_relative_z_um()
    assert str(e.value) == LOCK_ON_FAILED_MESSAGE


def _non_round_trip_pair():
    for x in np.arange(10.0, 11.0, 0.0173):
        for off in (8, 16, 24, 40):
            if (float(x) + off) - off != float(x):
                return float(x), off
    raise AssertionError("no non-round-tripping pair found")


def test_set_reference_then_identical_reload_keeps_the_record(ctrl):
    x, off = _non_round_trip_pair()
    ctrl.laser_af_properties = ctrl.laser_af_properties.model_copy(update={"x_offset": off, "spot_crop_size": 8})
    ctrl.image = np.random.default_rng(1).random((32, 64)).astype(np.float32) + 1.0
    ctrl._get_laser_spot_centroid = lambda *a, **k: (x, 16.0)
    assert ctrl.set_reference() is True
    assert ctrl.valid_lock_on() is not None
    saved = ctrl._config_repo.save_laser_af_config.call_args[0][2]
    ctrl.initialize_manual(saved)
    assert ctrl.valid_lock_on() is not None
