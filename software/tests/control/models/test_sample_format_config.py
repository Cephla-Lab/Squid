"""User sample-format YAML: sparse overrides + custom formats.

The shipped CSV is byte-frozen; this file carries only deliberate deviations.
Absence of the file is the identity default.
"""

import logging

import pytest
import yaml

import control._def as _def
from control.models.sample_format_config import (
    apply_user_sample_formats,
    CustomSampleFormat,
    load_user_sample_formats,
    SampleFormatOverride,
    save_user_sample_formats,
    UserSampleFormats,
)


def shipped_catalog():
    import copy

    return copy.deepcopy(
        {k: dict(_def.get_wellplate_settings(k)) for k in _def.WELLPLATE_FORMAT_SETTINGS if k != "glass slide"}
    )


def test_absent_file_is_identity(tmp_path):
    assert load_user_sample_formats(str(tmp_path / "nope.yaml")) is None
    catalog = shipped_catalog()
    before = {k: dict(v) for k, v in catalog.items()}
    apply_user_sample_formats(catalog, None)
    assert catalog == before


def test_override_changes_only_named_fields():
    catalog = shipped_catalog()
    shipped = dict(catalog["24 well plate"])

    user = UserSampleFormats(overrides={"24 well plate": SampleFormatOverride(well_spacing_mm=18.90)})
    apply_user_sample_formats(catalog, user)

    edited = catalog["24 well plate"]
    assert edited["well_spacing_mm"] == 18.90
    assert edited["well_spacing_x_mm"] == 18.90  # scalar re-broadcasts
    assert edited["well_spacing_y_mm"] == 18.90
    for key, value in shipped.items():
        if not key.startswith("well_spacing"):
            assert edited[key] == value, key
    # ...and no other format was touched.
    assert catalog["96 well plate"] == shipped_catalog()["96 well plate"]


def test_scalar_and_per_axis_together_is_an_error():
    with pytest.raises(ValueError):
        SampleFormatOverride(well_spacing_mm=9.0, well_spacing_x_mm=9.0)
    with pytest.raises(ValueError):
        CustomSampleFormat(rows=2, cols=4, well_spacing_mm=9.0, well_spacing_x_mm=9.0, well_size_mm=6.0)


def test_custom_format_requires_complete_spacing_and_size():
    with pytest.raises(ValueError):
        CustomSampleFormat(rows=2, cols=4, well_spacing_x_mm=12.0, well_size_mm=6.0)  # missing y
    with pytest.raises(ValueError):
        CustomSampleFormat(rows=2, cols=4, well_spacing_mm=12.0)  # missing size


def test_anisotropic_custom_format_round_trips(tmp_path):
    path = str(tmp_path / "sample_formats_user.yaml")
    user = UserSampleFormats(
        custom_formats={
            "ibidi 8 well": CustomSampleFormat(
                rows=2,
                cols=4,
                well_spacing_x_mm=12.57,
                well_spacing_y_mm=11.50,
                well_size_x_mm=10.75,
                well_size_y_mm=9.40,
                well_shape="rectangle",
            )
        }
    )
    save_user_sample_formats(user, path)
    loaded = load_user_sample_formats(path)

    catalog = shipped_catalog()
    apply_user_sample_formats(catalog, loaded)
    s = catalog["ibidi 8 well"]
    assert (s["well_spacing_x_mm"], s["well_spacing_y_mm"]) == (12.57, 11.50)
    assert (s["well_size_x_mm"], s["well_size_y_mm"]) == (10.75, 9.40)
    assert s["well_shape"] == "rectangle"
    assert s["rows"] == 2 and s["cols"] == 4
    # scalar mirrors exist for legacy readers
    assert s["well_spacing_mm"] == 12.57


def test_unknown_override_warns_and_is_skipped(caplog):
    catalog = shipped_catalog()
    before = {k: dict(v) for k, v in catalog.items()}
    user = UserSampleFormats(overrides={"no such plate": SampleFormatOverride(well_spacing_mm=1.0)})

    with caplog.at_level(logging.WARNING):
        apply_user_sample_formats(catalog, user)

    assert catalog == before
    assert any("no such plate" in r.getMessage() for r in caplog.records)


def test_damaged_file_is_ignored_loudly(tmp_path, caplog):
    path = tmp_path / "sample_formats_user.yaml"
    path.write_text("overrides: {'96 well plate': {well_spacing_mm: -5}}")

    with caplog.at_level(logging.ERROR):
        assert load_user_sample_formats(str(path)) is None
    assert any("NOT BEING APPLIED" in r.getMessage() for r in caplog.records)


def test_save_is_atomic_on_failure(tmp_path, monkeypatch):
    import os as os_module

    path = tmp_path / "sample_formats_user.yaml"
    good = "version: 1\noverrides: {}\ncustom_formats: {}\n"
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
    assert "24 well plate" in parsed.overrides
    assert parsed.custom_formats["ibidi 8 well"].well_shape == "rectangle"


def test_override_edits_a_custom_format():
    """A user's own custom format is as editable as a shipped one: custom
    formats insert before overrides apply, so an override naming a custom
    format lands instead of being warned away (the old order dropped it)."""
    user = UserSampleFormats(
        custom_formats={
            "my chamber slide": CustomSampleFormat(rows=2, cols=4, well_spacing_mm=12.5, well_size_mm=10.0)
        },
        overrides={"my chamber slide": SampleFormatOverride(well_spacing_mm=12.8)},
    )
    formats = {}
    apply_user_sample_formats(formats, user)

    assert formats["my chamber slide"]["well_spacing_mm"] == 12.8
    assert formats["my chamber slide"]["well_spacing_x_mm"] == 12.8  # scalar re-broadcasts
    assert formats["my chamber slide"]["well_size_mm"] == 10.0  # untouched fields keep the definition
