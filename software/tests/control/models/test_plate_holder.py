"""Holder rotation record + the override-with-inherit resolution chain."""

import logging
import os

import pytest
import yaml

import control._def as _def
from control.core.plate_transform import plate_transform_for, resolve_rotation_deg
from control.models.plate_holder import (
    HolderMeasuredPoint,
    HolderMeasurement,
    load_plate_holder,
    PLATE_HOLDER_PATH,
    PlateHolder,
    save_plate_holder,
)


def measured_96():
    return HolderMeasurement(
        on="96 well plate",
        feature="center",
        points=[
            HolderMeasuredPoint(well="A1", x_mm=11.310, y_mm=10.752),
            HolderMeasuredPoint(well="A12", x_mm=110.311, y_mm=11.129),
            HolderMeasuredPoint(well="H1", x_mm=10.930, y_mm=73.751),
            HolderMeasuredPoint(well="H12", x_mm=109.933, y_mm=74.130),
        ],
        timestamp="2026-08-15T00:00:00",
    )


@pytest.fixture
def holder_tree(tmp_path, monkeypatch):
    """tmp cwd so machine_configs/ and cache/ writes stay out of the repo."""
    import shutil

    repo = os.getcwd()
    (tmp_path / "objective_and_sample_formats").mkdir()
    (tmp_path / "machine_configs").mkdir()
    (tmp_path / "cache").mkdir()
    for f in ("sample_formats.csv", "objectives.csv"):
        shutil.copy(
            os.path.join(repo, "objective_and_sample_formats", f), tmp_path / "objective_and_sample_formats" / f
        )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bare_nonzero_angle_is_rejected():
    """No-arbitrary-numbers: an angle with no provenance must not validate."""
    with pytest.raises(ValueError, match="provenance"):
        PlateHolder(rotation_deg=0.21)
    # zero angle without points is fine (the .example skeleton)
    PlateHolder(rotation_deg=0.0)
    # nonzero WITH points is fine
    PlateHolder(rotation_deg=0.21, measured=measured_96())


def test_round_trip(holder_tree):
    save_plate_holder(PlateHolder(rotation_deg=0.21, measured=measured_96()))
    loaded = load_plate_holder()
    assert loaded.rotation_deg == 0.21
    assert loaded.measured.points[0].well == "A1"
    assert loaded.measured.feature == "center"


def test_absent_file_means_zero(holder_tree):
    assert load_plate_holder() is None
    angle, source = resolve_rotation_deg("1536 well plate")
    assert (angle, source) == (0.0, "none")


def test_damaged_file_ignored_loudly(holder_tree, caplog):
    os.makedirs("machine_configs", exist_ok=True)
    with open(PLATE_HOLDER_PATH, "w") as f:
        f.write("rotation_deg: 0.21\n")  # nonzero without provenance -> invalid
    with caplog.at_level(logging.ERROR):
        assert load_plate_holder() is None
    assert any("NOT BEING APPLIED" in r.getMessage() for r in caplog.records)


def test_inherit_chain(holder_tree):
    """placement override (measured) > holder record > 0.0."""
    # 1. nothing anywhere -> 0
    assert resolve_rotation_deg("1536 well plate") == (0.0, "none")

    # 2. holder record -> inherited by every plate format
    save_plate_holder(PlateHolder(rotation_deg=0.21, measured=measured_96()))
    assert resolve_rotation_deg("1536 well plate") == (0.21, "holder")
    assert resolve_rotation_deg("6 well plate") == (0.21, "holder")

    # 3. a measured per-format override wins, without any arithmetic
    from control.models.plate_placement import PlatePlacement, PlatePlacements, save_plate_placements

    save_plate_placements(PlatePlacements(placements={"1536 well plate": PlatePlacement(rotation_deg=0.31)}))
    assert resolve_rotation_deg("1536 well plate") == (0.31, "measured")
    # other formats still inherit
    assert resolve_rotation_deg("96 well plate") == (0.21, "holder")

    # 4. a placement entry with rotation UNSET (a1-only touch) still inherits
    save_plate_placements(PlatePlacements(placements={"1536 well plate": PlatePlacement(a1_dx_mm=0.1)}))
    assert resolve_rotation_deg("1536 well plate") == (0.21, "holder")


def test_glass_slide_is_always_zero(holder_tree):
    """Pitch-0 formats short-circuit: with a 1x1 grid the only well IS the
    pivot, so rotation could not move anything - and well_index_at has no
    inverse to offer."""
    save_plate_holder(PlateHolder(rotation_deg=0.5, measured=measured_96()))
    assert resolve_rotation_deg("glass slide") == (0.0, "none")


def test_transform_carries_the_resolved_rotation(holder_tree):
    save_plate_holder(PlateHolder(rotation_deg=0.21, measured=measured_96()))
    tf = plate_transform_for("1536 well plate")
    assert tf.rotation_deg == 0.21
    # rotation pivots on A1: A1 itself does not move
    s = _def.get_wellplate_settings("1536 well plate")
    assert tf.well_center_mm(0, 0) == (
        s["a1_x_mm"] + _def.WELLPLATE_OFFSET_X_mm,
        s["a1_y_mm"] + _def.WELLPLATE_OFFSET_Y_mm,
    )


def test_example_skeleton_parses():
    data = yaml.safe_load(open("machine_configs/plate_holder.yaml.example"))
    holder = PlateHolder.model_validate(data)
    assert holder.rotation_deg == 0.0  # deliberately unset in the example
