"""Legacy calibration-cache migration and placement composition.

The legacy cache/sample_formats.csv was a whole-table shadow of the shipped
catalog. The migration splits it once - a1 differences become placement deltas,
geometry differences become sparse overrides, unknown formats become custom
formats - and the composed coordinates must be NUMERICALLY IDENTICAL to what
the cache produced. That equivalence is the whole safety argument.
"""

import os
import shutil

import pytest

import control._def as _def
from control.core.plate_transform import plate_transform_for, WellplateSettings
from control.models.plate_placement import load_plate_placements, PLATE_PLACEMENT_PATH
from control.models.sample_format_config import load_user_sample_formats, USER_SAMPLE_FORMATS_PATH

SHIPPED_DIR = "objective_and_sample_formats"
CSV = "sample_formats.csv"


@pytest.fixture
def migration_tree(catalog_tree):
    return catalog_tree


def write_legacy_cache(tree, edit):
    """Build a legacy cache: the shipped table with `edit` applied."""
    formats = _def.read_sample_formats_csv(os.path.join(SHIPPED_DIR, CSV))
    edit(formats)
    # Legacy caches carry only the 10 CSV columns.
    ten = {
        k: {f: v[f] for f in _def.SAMPLE_FORMAT_CSV_FIELDNAMES if f != "format" and f in v} for k, v in formats.items()
    }
    _def.write_sample_formats_csv(os.path.join("cache", CSV), ten)


def run_migration():
    _def._migrate_legacy_format_cache(os.path.join("cache", CSV), os.path.join(SHIPPED_DIR, CSV))


def test_a1_recalibration_becomes_placement_delta(migration_tree):
    calibrated = (11.482, 10.913)  # a real-looking 96-well A1 measurement

    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = calibrated[0]
        formats["96 well plate"]["a1_y_mm"] = calibrated[1]

    write_legacy_cache(migration_tree, edit)
    shipped = _def.read_sample_formats_csv(os.path.join(SHIPPED_DIR, CSV))["96 well plate"]

    run_migration()

    placements = load_plate_placements()
    entry = placements.placements["96 well plate"]
    assert entry.a1_dx_mm == calibrated[0] - shipped["a1_x_mm"]
    assert entry.a1_dy_mm == calibrated[1] - shipped["a1_y_mm"]
    assert entry.fit.points[0].x_mm == calibrated[0]  # raw measurement preserved
    assert "migrated_from_cache_csv" in entry.fit.note
    # geometry untouched; no override written
    user = load_user_sample_formats()
    assert user is None or "96 well plate" not in user.overrides
    # cache retired
    assert not os.path.exists(os.path.join("cache", CSV))
    assert os.path.exists(os.path.join("cache", CSV + ".migrated"))


def test_composed_coordinates_equal_legacy_cache(migration_tree, monkeypatch):
    """The safety property: shipped + delta == what the cache said, exactly."""
    calibrated = (11.482, 10.913)

    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = calibrated[0]
        formats["96 well plate"]["a1_y_mm"] = calibrated[1]

    write_legacy_cache(migration_tree, edit)
    run_migration()

    # The composed transform must reproduce the calibrated values bit-for-bit:
    # shipped + (calibrated - shipped) == calibrated for IEEE-754 doubles.
    tf = plate_transform_for("96 well plate")
    s = _def.get_wellplate_settings("96 well plate")
    assert tf.well_center_mm(0, 0) == calibrated
    assert tf.well_center_mm(7, 11) == (
        calibrated[0] + 11 * s["well_spacing_mm"],
        calibrated[1] + 7 * s["well_spacing_mm"],
    )
    # ...and the viewer sees the same composed a1 as the planner.
    ws = WellplateSettings.from_format("96 well plate")
    assert (ws.a1_x_mm, ws.a1_y_mm) == calibrated


def test_placement_suppresses_legacy_offset(migration_tree, monkeypatch):
    calibrated = (11.482, 10.913)

    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = calibrated[0]
        formats["96 well plate"]["a1_y_mm"] = calibrated[1]

    write_legacy_cache(migration_tree, edit)
    run_migration()

    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 3.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 3.0)

    # Calibrated format: the delta is the whole correction; offset suppressed.
    assert plate_transform_for("96 well plate").well_center_mm(0, 0) == calibrated
    # Uncalibrated format: legacy offset still applies as before.
    s384 = _def.get_wellplate_settings("384 well plate")
    assert plate_transform_for("384 well plate").well_center_mm(0, 0) == (
        s384["a1_x_mm"] + 3.0,
        s384["a1_y_mm"] + 3.0,
    )


def test_geometry_edit_becomes_sparse_override(migration_tree):
    def edit(formats):
        formats["24 well plate"]["well_spacing_mm"] = 18.90  # Millipore, not Corning

    write_legacy_cache(migration_tree, edit)
    run_migration()

    user = load_user_sample_formats()
    assert user.overrides["24 well plate"].well_spacing_mm == 18.90
    assert user.overrides["24 well plate"].well_size_mm is None  # sparse
    placements = load_plate_placements()
    assert "24 well plate" not in placements.placements  # no a1 change


def test_custom_format_migrates(migration_tree):
    def edit(formats):
        formats["my custom plate"] = {
            "a1_x_mm": 15.0,
            "a1_y_mm": 12.0,
            "a1_x_pixel": 177,
            "a1_y_pixel": 142,
            "well_size_mm": 9.0,
            "well_spacing_mm": 12.0,
            "number_of_skip": 0,
            "rows": 2,
            "cols": 4,
        }

    write_legacy_cache(migration_tree, edit)
    run_migration()

    user = load_user_sample_formats()
    custom = user.custom_formats["my custom plate"]
    assert (custom.rows, custom.cols, custom.a1_x_mm) == (2, 4, 15.0)


def test_migration_is_idempotent_and_preserves_newer_entries(migration_tree):
    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = 11.482

    write_legacy_cache(migration_tree, edit)
    run_migration()

    # A NEWER calibration lands after migration...
    from control.models.plate_placement import PlatePlacement, PlatePlacements, save_plate_placements

    placements = load_plate_placements()
    placements.placements["96 well plate"] = PlatePlacement(a1_dx_mm=0.9, a1_dy_mm=0.9)
    save_plate_placements(placements)

    # ...and a stale cache reappearing (e.g. restored from backup) must not
    # clobber it when migration runs again.
    write_legacy_cache(migration_tree, edit)
    run_migration()

    assert load_plate_placements().placements["96 well plate"].a1_dx_mm == 0.9


def test_identical_cache_migrates_to_empty_stores(migration_tree):
    write_legacy_cache(migration_tree, lambda formats: None)
    run_migration()

    placements = load_plate_placements()
    user = load_user_sample_formats()
    assert placements is None or not placements.placements
    assert user is None or (not user.overrides and not user.custom_formats)
    assert os.path.exists(os.path.join("cache", CSV + ".migrated"))


def test_load_formats_survives_migration_failure(migration_tree, monkeypatch):
    """If the migration itself blows up, the legacy cache keeps working."""
    write_legacy_cache(migration_tree, lambda formats: None)
    monkeypatch.setattr(_def, "_migrate_legacy_format_cache", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

    _, sample_formats = _def.load_formats()

    assert "96 well plate" in sample_formats
    assert os.path.exists(os.path.join("cache", CSV))  # untouched
