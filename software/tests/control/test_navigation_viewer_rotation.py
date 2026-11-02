"""NavigationViewer stage<->pixel paths under a measured plate rotation.

The background image is a NOMINAL drawing (wells on an unrotated grid), so the
mapping must pass through the plate frame: forward (stage -> pixel) applies
R(-theta) about A1, inverse (pixel click -> stage) applies R(+theta). With no
rotation anywhere, both paths must be bit-for-bit the legacy arithmetic.
"""

import math
from unittest.mock import MagicMock

import pytest

import control._def as _def
from control.core.plate_transform import plate_transform_for, WellplateSettings
from control.models.plate_holder import HolderMeasuredPoint, HolderMeasurement, PlateHolder, save_plate_holder


# qapp comes from pytest-qt; catalog_tree/design_travel_limits from conftest
# (the composed-pivot equivalence below needs the zero legacy offset).
@pytest.fixture
def repo_tree(catalog_tree, design_travel_limits):
    return catalog_tree


def measured_holder(rotation_deg):
    return PlateHolder(
        rotation_deg=rotation_deg,
        measured=HolderMeasurement(
            on="96 well plate",
            points=[
                HolderMeasuredPoint(well="A1", x_mm=11.31, y_mm=10.75),
                HolderMeasuredPoint(well="H12", x_mm=110.3, y_mm=74.1),
            ],
            timestamp="2026-08-15T00:00:00",
        ),
    )


def make_viewer(qapp, sample="96 well plate"):
    from control.core.core import NavigationViewer

    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 0.9
    objective_store = MagicMock()
    objective_store.get_pixel_size_factor.return_value = 1.0
    viewer = NavigationViewer(objective_store, camera, sample=sample)
    # update_wellplate_settings redraws the current FOV; give it a position
    # (in the app, a stage position update arrives before the format signal).
    viewer.x_mm, viewer.y_mm = 0.0, 0.0
    if sample not in ("glass slide", "4 glass slide"):
        viewer.update_wellplate_settings(WellplateSettings.from_format(sample))
    return viewer


def legacy_fov_pixels(viewer, x_mm, y_mm):
    """The pre-rotation arithmetic, verbatim (plate branch)."""
    top_left = (
        round(viewer.origin_x_pixel + x_mm / viewer.mm_per_pixel - viewer.fov_size_mm / 2 / viewer.mm_per_pixel),
        round(viewer.origin_y_pixel + y_mm / viewer.mm_per_pixel - viewer.fov_size_mm / 2 / viewer.mm_per_pixel),
    )
    bottom_right = (
        round(viewer.origin_x_pixel + x_mm / viewer.mm_per_pixel + viewer.fov_size_mm / 2 / viewer.mm_per_pixel),
        round(viewer.origin_y_pixel + y_mm / viewer.mm_per_pixel + viewer.fov_size_mm / 2 / viewer.mm_per_pixel),
    )
    return top_left, bottom_right


def center_of(corners):
    (x0, y0), (x1, y1) = corners
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def test_identity_path_is_bit_for_bit_legacy(qapp, repo_tree):
    viewer = make_viewer(qapp)
    for x_mm, y_mm in [(11.31, 10.75), (35.0, 42.5), (110.31, 74.13), (0.0, 0.0)]:
        assert viewer.get_FOV_pixel_coordinates(x_mm, y_mm) == legacy_fov_pixels(viewer, x_mm, y_mm)
        assert viewer.pixel_to_stage_mm(400.0, 300.0) == (
            (400.0 - viewer.origin_x_pixel) * viewer.mm_per_pixel,
            (300.0 - viewer.origin_y_pixel) * viewer.mm_per_pixel,
        )


def test_planned_well_fov_lands_on_drawn_well(qapp, repo_tree):
    """The whole point: an FOV planned at a ROTATED well center must draw on
    that well's NOMINAL (drawn) position - not smeared off the plate image."""
    save_plate_holder(measured_holder(0.4))
    viewer = make_viewer(qapp)

    tf = plate_transform_for("96 well plate")
    assert tf.rotation_deg == 0.4
    for row, col in [(0, 0), (7, 11), (0, 11), (7, 0)]:
        x_mm, y_mm = tf.well_center_mm(row, col)  # rotated stage position
        cx, cy = center_of(viewer.get_FOV_pixel_coordinates(x_mm, y_mm))
        nominal_px = viewer.a1_x_pixel + col * 9.0 / viewer.mm_per_pixel
        nominal_py = viewer.a1_y_pixel + row * 9.0 / viewer.mm_per_pixel
        assert cx == pytest.approx(nominal_px, abs=1.0), (row, col)  # rounding only
        assert cy == pytest.approx(nominal_py, abs=1.0), (row, col)
        # and WITHOUT the rotation-aware path it would visibly miss (H12 is
        # ~119 mm of lever arm; 0.4 deg = ~0.8 mm = ~10 px):
        if (row, col) == (7, 11):
            legacy_cx, _ = center_of(legacy_fov_pixels(viewer, x_mm, y_mm))
            assert abs(legacy_cx - nominal_px) > 5


def test_pixel_round_trip_under_rotation(qapp, repo_tree):
    save_plate_holder(measured_holder(0.4))
    viewer = make_viewer(qapp)
    for px, py in [(200.0, 180.0), (700.0, 500.0), (1300.0, 900.0)]:
        x_mm, y_mm = viewer.pixel_to_stage_mm(px, py)
        cx, cy = center_of(viewer.get_FOV_pixel_coordinates(x_mm, y_mm))
        assert cx == pytest.approx(px, abs=1.0)  # corner rounding only
        assert cy == pytest.approx(py, abs=1.0)


def test_click_on_drawn_well_navigates_to_rotated_center(qapp, repo_tree):
    save_plate_holder(measured_holder(0.4))
    viewer = make_viewer(qapp)
    tf = plate_transform_for("96 well plate")
    # the drawn position of H12 in the nominal image:
    px = viewer.a1_x_pixel + 11 * 9.0 / viewer.mm_per_pixel
    py = viewer.a1_y_pixel + 7 * 9.0 / viewer.mm_per_pixel
    x_mm, y_mm = viewer.pixel_to_stage_mm(px, py)
    expect_x, expect_y = tf.well_center_mm(7, 11)
    assert x_mm == pytest.approx(expect_x, abs=1e-9)
    assert y_mm == pytest.approx(expect_y, abs=1e-9)


def test_glass_slide_ignores_holder_rotation(qapp, repo_tree):
    save_plate_holder(measured_holder(0.4))
    viewer = make_viewer(qapp, sample="glass slide")
    assert viewer._current_rotation_deg() == 0.0
    assert viewer.pixel_to_stage_mm(400.0, 300.0) == (
        (400.0 - viewer.origin_x_pixel) * viewer.mm_per_pixel,
        (300.0 - viewer.origin_y_pixel) * viewer.mm_per_pixel,
    )
