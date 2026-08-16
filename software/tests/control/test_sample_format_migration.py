"""Legacy calibration-cache migration and placement composition.

The legacy cache/sample_formats.csv was a whole-table shadow of the shipped
catalog. The migration splits it once - a1 differences become placement deltas,
every row that differs from its shipped example becomes a COMPLETE user
formats - and the composed coordinates must be NUMERICALLY IDENTICAL to what
the cache produced. That equivalence is the whole safety argument.
"""

import os
import shutil

import pytest

import control._def as _def
from control.core.plate_transform import plate_transform_for, WellplateSettings
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


def test_a1_recalibration_becomes_a_measured_definition(migration_tree):
    write_legacy_cache(migration_tree, lambda f: f["96 well plate"].update({"a1_x_mm": 11.604, "a1_y_mm": 10.638}))

    _def.load_formats()

    definition = load_user_sample_formats().formats["96 well plate"]
    assert definition.a1_x_mm == 11.604  # stored ABSOLUTELY, no delta
    assert definition.a1_y_mm == 10.638
    assert definition.measured is not None  # provenance carried over
    assert definition.measured.method == "migrated"
    assert definition.measured.points[0].x_mm == 11.604
    # the rest of the definition came along, so the entry is self-contained
    assert definition.rows == 8 and definition.cols == 12 and definition.well_spacing_mm == 9.0


def test_composed_coordinates_equal_legacy_cache(migration_tree, monkeypatch):
    """The safety property: after migration, every consumer computes exactly
    what the legacy cache said - bit-for-bit, not approximately."""
    calibrated = (11.482, 10.913)

    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = calibrated[0]
        formats["96 well plate"]["a1_y_mm"] = calibrated[1]

    write_legacy_cache(migration_tree, edit)
    # Rebind the module global the way import time does: definitions now live
    # in the settings table, so consumers read the migrated values from there.
    _, formats = _def.load_formats()
    monkeypatch.setattr(_def, "WELLPLATE_FORMAT_SETTINGS", formats)

    # The stored definition must reproduce the calibrated values EXACTLY - the
    # migration carries a1 across verbatim, so this is equality, not tolerance.
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


def test_measured_definition_suppresses_legacy_offset(migration_tree, monkeypatch):
    calibrated = (11.482, 10.913)

    def edit(formats):
        formats["96 well plate"]["a1_x_mm"] = calibrated[0]
        formats["96 well plate"]["a1_y_mm"] = calibrated[1]

    write_legacy_cache(migration_tree, edit)
    _, formats = _def.load_formats()
    monkeypatch.setattr(_def, "WELLPLATE_FORMAT_SETTINGS", formats)

    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_X_mm", 3.0)
    monkeypatch.setattr(_def, "WELLPLATE_OFFSET_Y_mm", 3.0)

    # Calibrated format: the measured a1 is the whole correction; offset suppressed.
    assert plate_transform_for("96 well plate").well_center_mm(0, 0) == calibrated
    # Uncalibrated format: legacy offset still applies as before.
    s384 = _def.get_wellplate_settings("384 well plate")
    assert plate_transform_for("384 well plate").well_center_mm(0, 0) == (
        s384["a1_x_mm"] + 3.0,
        s384["a1_y_mm"] + 3.0,
    )


def test_geometry_edit_becomes_a_definition_without_measured(migration_tree, monkeypatch):
    """A spacing edit with an untouched a1 still produces a complete
    definition - but no `measured` block, so the legacy offset keeps
    applying to that format."""
    write_legacy_cache(migration_tree, lambda f: f["24 well plate"].update({"well_spacing_mm": 18.90}))

    _, formats = _def.load_formats()
    monkeypatch.setattr(_def, "WELLPLATE_FORMAT_SETTINGS", formats)

    definition = load_user_sample_formats().formats["24 well plate"]
    assert definition.well_spacing_mm == 18.90
    assert definition.a1_x_mm == formats["24 well plate"]["a1_x_mm"]
    assert definition.measured is None  # a1 was never touched


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
    custom = user.formats["my custom plate"]
    assert (custom.rows, custom.cols, custom.a1_x_mm) == (2, 4, 15.0)


def test_migration_is_idempotent_and_preserves_newer_entries(migration_tree):
    write_legacy_cache(migration_tree, lambda f: f["96 well plate"].update({"a1_x_mm": 11.604}))
    _def.load_formats()

    # a later, newer definition for the same format must survive a re-run
    from control.models.sample_format_config import save_user_sample_formats

    stored = load_user_sample_formats()
    stored.formats["96 well plate"].a1_x_mm = 12.9
    save_user_sample_formats(stored)

    # the cache was renamed, so a second load cannot migrate again
    assert not os.path.exists(os.path.join("cache", CSV))
    _def.load_formats()

    assert load_user_sample_formats().formats["96 well plate"].a1_x_mm == 12.9


def test_identical_cache_migrates_to_nothing(migration_tree):
    """A cache that matches the shipped examples carries no user intent, so
    nothing is written - those formats keep tracking the examples."""
    write_legacy_cache(migration_tree, lambda f: None)

    _def.load_formats()

    user = load_user_sample_formats()
    assert user is None or not user.formats


def test_load_formats_survives_migration_failure(migration_tree, monkeypatch):
    """If the migration itself blows up, the legacy cache keeps working."""
    write_legacy_cache(migration_tree, lambda formats: None)
    monkeypatch.setattr(_def, "_migrate_legacy_format_cache", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

    _, sample_formats = _def.load_formats()

    assert "96 well plate" in sample_formats
    assert os.path.exists(os.path.join("cache", CSV))  # untouched
