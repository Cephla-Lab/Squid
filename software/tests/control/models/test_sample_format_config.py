"""User sample formats: one complete definition per format.

Shipped rows are examples; a user entry replaces one wholesale (owner
decision, 2026-08-16). Absence of the file leaves the examples untouched.
"""

import logging

import pytest
import yaml

import control._def as _def
from control.models.sample_format_config import (
    apply_user_sample_formats,
    FormatMeasurement,
    load_user_sample_formats,
    MeasuredPoint,
    SampleFormat,
    save_user_sample_formats,
    UserSampleFormats,
)


def shipped_catalog():
    import copy

    return copy.deepcopy(
        {k: dict(_def.get_wellplate_settings(k)) for k in _def.WELLPLATE_FORMAT_SETTINGS if k != "glass slide"}
    )


def a1_touch(x_mm=11.604, y_mm=10.638):
    return FormatMeasurement(
        points=[MeasuredPoint(well="A1", x_mm=x_mm, y_mm=y_mm)],
        method="center point",
        timestamp="2026-08-16T12:00:00",
    )


def test_absent_file_leaves_examples_untouched(tmp_path):
    assert load_user_sample_formats(str(tmp_path / "nope.yaml")) is None
    catalog = shipped_catalog()
    before = {k: dict(v) for k, v in catalog.items()}
    apply_user_sample_formats(catalog, None)
    assert catalog == before


def test_user_definition_replaces_the_example_wholesale():
    """The core rule: no per-field merging - what the lab stored IS the format."""
    catalog = shipped_catalog()
    shipped = dict(catalog["24 well plate"])

    user = UserSampleFormats(
        formats={
            "24 well plate": SampleFormat(
                rows=4,
                cols=6,
                a1_x_mm=17.05,  # measured, absolute - no delta anywhere
                a1_y_mm=13.67,
                well_spacing_mm=18.90,
                well_size_mm=15.54,
                measured=a1_touch(17.05, 13.67),
            )
        }
    )
    apply_user_sample_formats(catalog, user)
    entry = catalog["24 well plate"]

    assert entry["well_spacing_mm"] == 18.90
    assert entry["well_spacing_x_mm"] == entry["well_spacing_y_mm"] == 18.90  # scalar broadcasts
    assert (entry["a1_x_mm"], entry["a1_y_mm"]) == (17.05, 13.67)
    assert entry["rows"] == 4 and entry["cols"] == 6
    assert entry["a1_x_mm"] != shipped["a1_x_mm"]  # the example is gone, not merged


def test_shipped_and_custom_formats_are_handled_identically():
    """A user's own plate and a calibrated shipped plate are the same kind of
    entry - the only difference is whether an example existed first."""
    catalog = shipped_catalog()
    user = UserSampleFormats(
        formats={
            "96 well plate": SampleFormat(
                rows=8,
                cols=12,
                a1_x_mm=11.6,
                a1_y_mm=10.6,
                well_spacing_mm=9.0,
                well_size_mm=6.21,
                measured=a1_touch(11.6, 10.6),
            ),
            "my chamber slide": SampleFormat(
                rows=2,
                cols=4,
                a1_x_mm=20.0,
                a1_y_mm=15.0,
                well_spacing_mm=12.5,
                well_size_mm=10.0,
                measured=a1_touch(20.0, 15.0),
            ),
        }
    )
    apply_user_sample_formats(catalog, user)

    assert catalog["96 well plate"]["a1_x_mm"] == 11.6
    assert catalog["my chamber slide"]["a1_x_mm"] == 20.0
    assert catalog["my chamber slide"]["rows"] == 2
    # both are "measured", so both suppress the legacy offset - and the flag
    # rides in the settings dict, where every transform builder can see it
    assert catalog["96 well plate"]["a1_measured"] and catalog["my chamber slide"]["a1_measured"]


def test_is_measured_distinguishes_edited_from_calibrated():
    """A geometry edit alone does not claim the A1 was measured - the legacy
    offset must keep applying until someone actually touches A1."""
    edited = SampleFormat(rows=8, cols=12, well_spacing_mm=9.0, well_size_mm=6.5)
    assert not edited.is_measured
    assert edited.to_settings()["a1_measured"] is False

    calibrated = SampleFormat(rows=8, cols=12, well_spacing_mm=9.0, well_size_mm=6.5, measured=a1_touch())
    assert calibrated.is_measured
    assert calibrated.to_settings()["a1_measured"] is True


def test_unknown_schema_version_is_refused_loudly(tmp_path, caplog):
    """A file from another schema must not load as ZERO formats - pydantic
    ignores unknown keys, so a v1 file would silently discard every entry."""
    path = tmp_path / "sample_formats_user.yaml"
    path.write_text("version: 1\noverrides: {'96 well plate': {well_spacing_mm: 18.0}}\ncustom_formats: {}\n")

    with caplog.at_level(logging.ERROR):
        assert load_user_sample_formats(str(path)) is None
    assert any("version 1" in r.getMessage() for r in caplog.records)


def test_scalar_and_per_axis_together_is_an_error():
    with pytest.raises(ValueError, match="not both"):
        SampleFormat(rows=2, cols=4, well_spacing_mm=9.0, well_spacing_x_mm=9.0, well_size_mm=6.0)


def test_definition_requires_complete_spacing_and_size():
    with pytest.raises(ValueError, match="well_spacing"):
        SampleFormat(rows=2, cols=4, well_spacing_x_mm=12.0, well_size_mm=6.0)  # missing y
    with pytest.raises(ValueError, match="well_size"):
        SampleFormat(rows=2, cols=4, well_spacing_mm=12.0)  # missing size


def test_rotation_without_provenance_is_rejected():
    """No-arbitrary-numbers: an angle needs the multi-well fit behind it, and
    a single A1 touch (which lands in `measured`) cannot produce one."""
    with pytest.raises(ValueError, match="provenance"):
        SampleFormat(rows=8, cols=12, well_spacing_mm=9.0, well_size_mm=6.21, rotation_deg=0.21)
    with pytest.raises(ValueError, match="provenance"):
        SampleFormat(rows=8, cols=12, well_spacing_mm=9.0, well_size_mm=6.21, rotation_deg=0.21, measured=a1_touch())
    SampleFormat(
        rows=8,
        cols=12,
        well_spacing_mm=9.0,
        well_size_mm=6.21,
        rotation_deg=0.21,
        rotation_measured=FormatMeasurement(
            points=[
                MeasuredPoint(well="A1", x_mm=11.3, y_mm=10.7),
                MeasuredPoint(well="H12", x_mm=110.3, y_mm=74.1),
            ],
            timestamp="2026-08-16T12:00:00",
        ),
    )


def test_anisotropic_definition_round_trips(tmp_path):
    path = str(tmp_path / "sample_formats_user.yaml")
    user = UserSampleFormats(
        formats={
            "ibidi 8 well": SampleFormat(
                rows=2,
                cols=4,
                a1_x_mm=20.0,
                a1_y_mm=15.0,
                well_spacing_x_mm=12.5,
                well_spacing_y_mm=11.2,
                well_size_x_mm=10.4,
                well_size_y_mm=9.4,
                well_shape="rectangle",
            )
        }
    )
    save_user_sample_formats(user, path)
    loaded = load_user_sample_formats(path)
    settings = loaded.formats["ibidi 8 well"].to_settings()

    assert settings["well_spacing_x_mm"] == 12.5 and settings["well_spacing_y_mm"] == 11.2
    assert settings["well_size_x_mm"] == 10.4 and settings["well_size_y_mm"] == 9.4
    assert settings["well_shape"] == "rectangle"
    assert settings["a1_x_mm"] == 20.0


def test_from_settings_carries_the_whole_definition():
    """The writer's path: start from what the app knows, overlay the new
    measurement, store everything."""
    settings = dict(_def.get_wellplate_settings("96 well plate"))
    settings["a1_x_mm"] = 11.604
    definition = SampleFormat.from_settings(settings, measured=a1_touch())

    assert definition.rows == 8 and definition.cols == 12
    assert definition.a1_x_mm == 11.604
    assert definition.well_spacing_x_mm == 9.0
    assert definition.measured.points[0].well == "A1"
    # round trip preserves every consumer-visible key
    assert definition.to_settings()["well_size_mm"] == settings["well_size_mm"]


def test_damaged_file_is_ignored_loudly(tmp_path, caplog):
    path = tmp_path / "sample_formats_user.yaml"
    path.write_text("formats: {'96 well plate': {rows: 8, cols: 12, well_spacing_mm: -5}}")

    with caplog.at_level(logging.ERROR):
        assert load_user_sample_formats(str(path)) is None
    assert any("NOT BEING APPLIED" in r.getMessage() for r in caplog.records)


def test_save_is_atomic_on_failure(tmp_path, monkeypatch):
    import os as os_module

    path = tmp_path / "sample_formats_user.yaml"
    good = "version: 2\nformats: {}\n"
    path.write_text(good)

    monkeypatch.setattr(os_module, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        save_user_sample_formats(UserSampleFormats(), str(path))
    assert path.read_text() == good
    assert not (tmp_path / "sample_formats_user.yaml.tmp").exists()


def test_shipped_example_parses():
    example = "objective_and_sample_formats/sample_formats_user.yaml.example"
    data = yaml.safe_load(open(example))
    parsed = UserSampleFormats.model_validate(data)
    assert parsed.formats["ibidi 8 well"].well_shape == "rectangle"
