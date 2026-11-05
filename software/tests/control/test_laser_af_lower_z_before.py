"""Lowering Z before the laser autofocus (LASER_AF_LOWER_Z_BEFORE_UM).

A downward Z move is two commands (past the target and back up), an upward move is one. With the setting above 0,
move_to_coordinate lowers Z while the stage settles after the XY move, so that the autofocus correction that follows
is an upward move. Off by default; not used when the autofocus moves a piezo; undone when the autofocus fails.

The worker is a stub with just what the two real methods read, as in test_per_region_laser_af_offset.py.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control.core.multi_point_worker as mpw
from control.core.multi_point_worker import MultiPointWorker

Z_MIN_MM = 0.05


class _Stage:
    """Records the Z calls; moves where it is told."""

    def __init__(self, z_mm):
        self.z_mm = z_mm
        self.calls = []

    def get_pos(self):
        return SimpleNamespace(z_mm=self.z_mm)

    def get_config(self):
        return SimpleNamespace(Z_AXIS=SimpleNamespace(MIN_POSITION=Z_MIN_MM))

    def move_x_to(self, mm):
        pass

    def move_y_to(self, mm):
        pass

    def move_z(self, rel_mm):
        self.calls.append(("move_z", round(rel_mm, 6)))
        self.z_mm += rel_mm

    def move_z_to(self, abs_mm):
        self.calls.append(("move_z_to", round(abs_mm, 6)))
        self.z_mm = abs_mm


class _Worker:
    """MultiPointWorker-shaped stub for move_to_coordinate and the first lines of acquire_at_position."""

    move_to_coordinate = MultiPointWorker.move_to_coordinate
    _laser_af_lowering_mm = MultiPointWorker._laser_af_lowering_mm
    _move_to_field_z = MultiPointWorker._move_to_field_z
    move_to_z_level = MultiPointWorker.move_to_z_level
    acquire_at_position = MultiPointWorker.acquire_at_position

    def __init__(self, z_mm=1.0, laser_af=True, piezo=None, af_succeeds=True, time_point=0):
        self.stage = _Stage(z_mm)
        self._log = MagicMock()
        self._alignment_widget = None
        self.do_reflection_af = laser_af
        self.do_autofocus = False
        self.laser_auto_focus_controller = SimpleNamespace(piezo=piezo)
        self.time_point = time_point
        self._last_time_point_z_pos = {}
        self._z_before_lowering_mm = None
        self.slept = []
        self._af_succeeds = af_succeeds
        self.NZ = 0  # acquire_at_position: no planes, so only its first lines run
        self.use_piezo = False

    def _sleep(self, sec):
        self.slept.append(sec)

    def perform_autofocus(self, region_id, fov):
        self.stage.calls.append(("autofocus", round(self.stage.z_mm, 6)))
        return self._af_succeeds

    def field(self, coordinate_mm=(10.0, 20.0)):
        self.move_to_coordinate(coordinate_mm, "A1", 0)
        try:
            self.acquire_at_position("A1", "unused", 0)
        except Exception:  # the rest of acquire_at_position needs a real worker; its first lines have run
            pass
        return self.stage.calls


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """Every setting the code reads is pinned here; a test changes what it is about."""
    monkeypatch.setattr(mpw, "LASER_AF_LOWER_Z_BEFORE_UM", 3.0)
    monkeypatch.setattr(mpw, "Z_BACKLASH_COMPENSATION_UM", 5.0)
    monkeypatch.setattr(mpw, "SCAN_STABILIZATION_TIME_MS_X", 160)
    monkeypatch.setattr(mpw, "SCAN_STABILIZATION_TIME_MS_Y", 160)
    monkeypatch.setattr(mpw, "SCAN_STABILIZATION_TIME_MS_Z", 20)


def test_default_is_off():
    import control._def as _def

    lines = open(_def.__file__, encoding="utf-8").read().splitlines()
    assert "LASER_AF_LOWER_Z_BEFORE_UM = 0.0" in lines


def test_off_is_master_s_sequence(monkeypatch):
    monkeypatch.setattr(mpw, "LASER_AF_LOWER_Z_BEFORE_UM", 0.0)
    w = _Worker()
    assert w.field() == [("autofocus", 1.0)]
    assert w.slept == [0.16, 0.16]


def test_off_with_a_field_z_is_master_s_sequence(monkeypatch):
    monkeypatch.setattr(mpw, "LASER_AF_LOWER_Z_BEFORE_UM", 0.0)
    w = _Worker()
    assert w.field((10.0, 20.0, 1.2)) == [("move_z_to", 1.2), ("autofocus", 1.2)]
    assert w.slept == [0.16, 0.16, 0.02]


def test_on_lowers_once_before_the_autofocus():
    w = _Worker()
    assert w.field() == [("move_z", -0.003), ("autofocus", 0.997)]
    assert w._z_before_lowering_mm is None  # cleared once the autofocus has run


def test_on_the_lowering_is_inside_the_y_wait():
    w = _Worker()
    w.field()
    assert w.slept[0] == 0.16  # X as before
    assert 0.0 <= w.slept[1] <= 0.16  # Y: what is left of it
    assert len(w.slept) == 2


def test_on_the_lowering_comes_after_the_field_s_own_z():
    w = _Worker()
    assert w.field((10.0, 20.0, 1.2)) == [("move_z_to", 1.2), ("move_z", -0.003), ("autofocus", 1.197)]


def test_on_the_lowering_comes_after_the_last_time_point_s_z():
    w = _Worker(time_point=1)
    w._last_time_point_z_pos[("A1", 0)] = 1.1
    assert w.field() == [("move_z_to", 1.1), ("move_z", -0.003), ("autofocus", 1.097)]


def test_no_lowering_with_a_piezo():
    w = _Worker(piezo=object())
    assert w.field() == [("autofocus", 1.0)]
    assert w.slept == [0.16, 0.16]


def test_no_lowering_without_laser_autofocus():
    w = _Worker(laser_af=False)
    assert w.field() == [("autofocus", 1.0)]


def test_no_lowering_without_an_autofocus_controller():
    w = _Worker()
    w.laser_auto_focus_controller = None
    w.move_to_coordinate((10.0, 20.0), "A1", 0)
    assert w.stage.calls == []


def test_skipped_near_the_z_minimum():
    # 3 um of lowering and 5 um of backlash move need 8 um above the minimum
    w = _Worker(z_mm=Z_MIN_MM + 0.007)
    assert w.field() == [("autofocus", pytest.approx(Z_MIN_MM + 0.007))]


def test_not_skipped_with_just_enough_room():
    w = _Worker(z_mm=Z_MIN_MM + 0.0081)
    assert w.field()[0] == ("move_z", -0.003)


def test_undone_when_the_autofocus_fails():
    w = _Worker(af_succeeds=False)
    assert w.field() == [("move_z", -0.003), ("autofocus", 0.997), ("move_z_to", 1.0)]
    assert w.stage.z_mm == pytest.approx(1.0)
    assert w._z_before_lowering_mm is None


def test_a_failed_autofocus_without_lowering_moves_nothing(monkeypatch):
    monkeypatch.setattr(mpw, "LASER_AF_LOWER_Z_BEFORE_UM", 0.0)
    w = _Worker(af_succeeds=False)
    assert w.field() == [("autofocus", 1.0)]


def test_below_zero_is_off(monkeypatch):
    monkeypatch.setattr(mpw, "LASER_AF_LOWER_Z_BEFORE_UM", -3.0)
    w = _Worker()
    assert w.field() == [("autofocus", 1.0)]
