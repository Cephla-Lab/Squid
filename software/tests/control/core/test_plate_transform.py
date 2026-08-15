"""Unit tests for the shared plate transform (no call sites yet)."""

import math

import pytest

import control._def as _def
from control.core.plate_transform import PlateGeometryError, PlateTransform, plate_transform_for

PLATE_FORMATS = [f for f in _def.WELLPLATE_FORMAT_SETTINGS if f != "glass slide"]


@pytest.mark.parametrize("format_", PLATE_FORMATS)
def test_identity_matches_legacy_arithmetic_exactly(format_, monkeypatch):
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 1.375)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", -0.625)
    s = _def.WELLPLATE_FORMAT_SETTINGS[format_]

    tf = plate_transform_for(format_)

    for row in range(s["rows"]):
        for col in range(s["cols"]):
            expected = (
                s["a1_x_mm"] + (col * s["well_spacing_mm"]) + 1.375,
                s["a1_y_mm"] + (row * s["well_spacing_mm"]) + -0.625,
            )
            assert tf.well_center_mm(row, col) == expected  # exact ==


def test_resolver_reads_offsets_at_call_time(monkeypatch):
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 0.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 0.0)
    before = plate_transform_for("96 well plate")
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 2.0)
    after = plate_transform_for("96 well plate")
    assert before.offset_x_mm == 0.0 and after.offset_x_mm == 2.0


def test_nominal_strips_offset_and_rotation():
    tf = PlateTransform(11.31, 10.75, 9.0, 9.0, rotation_deg=0.3, offset_x_mm=1.0, offset_y_mm=2.0)
    n = tf.nominal()
    assert (n.rotation_deg, n.offset_x_mm, n.offset_y_mm) == (0.0, 0.0, 0.0)
    assert (n.a1_x_mm, n.a1_y_mm, n.pitch_x_mm) == (11.31, 10.75, 9.0)


def test_round_trip_inverse():
    tf = PlateTransform(11.01, 7.87, 2.25, 2.25, rotation_deg=0.21, offset_x_mm=0.4, offset_y_mm=-0.2)
    for row, col in [(0, 0), (0, 46), (30, 0), (30, 46), (15, 23)]:
        x, y = tf.well_center_mm(row, col)
        r, c = tf.well_index_at(x, y)
        assert math.isclose(r, row, abs_tol=1e-9)
        assert math.isclose(c, col, abs_tol=1e-9)


def test_rotation_pivots_on_a1():
    """rotation_deg must not move A1 itself - that is what keeps the new
    parameter orthogonal to the existing a1 calibration."""
    flat = PlateTransform(11.01, 7.87, 2.25, 2.25, rotation_deg=0.0)
    rot = PlateTransform(11.01, 7.87, 2.25, 2.25, rotation_deg=0.5)
    assert rot.well_center_mm(0, 0) == flat.well_center_mm(0, 0)


def test_rotation_direction_is_ccw_in_stage_frame():
    tf = PlateTransform(0.0, 0.0, 10.0, 10.0, rotation_deg=90.0)
    x, y = tf.well_center_mm(0, 1)  # one pitch along +x rotates onto +y
    assert math.isclose(x, 0.0, abs_tol=1e-12)
    assert math.isclose(y, 10.0, abs_tol=1e-12)


def test_anisotropic_pitch_scales_before_rotation():
    """Column index scales by pitch_x in the PLATE frame; rotation then maps it
    into the stage frame. If rotation were applied before the anisotropic
    scaling, this well would land at (0, 12) instead of (0, 7)."""
    tf = PlateTransform(0.0, 0.0, 12.0, 7.0, rotation_deg=90.0)
    x, y = tf.well_center_mm(1, 0)  # one ROW step: dy = pitch_y = 7 -> rotated onto -x
    assert math.isclose(x, -7.0, abs_tol=1e-12)
    assert math.isclose(y, 0.0, abs_tol=1e-12)


def test_glass_slide_inverse_raises():
    tf = plate_transform_for("glass slide")
    assert tf.pitch_x_mm == 0.0
    with pytest.raises(PlateGeometryError):
        tf.well_index_at(10.0, 10.0)


def test_unknown_format_raises():
    with pytest.raises(ValueError):
        plate_transform_for("no such plate")


def test_apply_legacy_offset_false_is_plate_frame(monkeypatch):
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 5.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 5.0)
    tf = plate_transform_for("96 well plate", apply_legacy_offset=False)
    s = _def.WELLPLATE_FORMAT_SETTINGS["96 well plate"]
    assert tf.well_center_mm(0, 0) == (s["a1_x_mm"], s["a1_y_mm"])
