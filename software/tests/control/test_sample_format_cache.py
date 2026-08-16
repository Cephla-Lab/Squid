"""Resilience of the sample-format CSV cache.

`control._def.load_formats()` runs at module import time (`_def.py` bottom), so any
exception it raises means `import control._def` fails and the application cannot
start at all. A partially-written `cache/sample_formats.csv` is a real way to get
there: the writer is invoked from the calibration dialog, and an interrupted write
(disk full, power loss, kill) leaves a truncated file.

These tests pin two behaviours:
  * a damaged cache falls back to the shipped geometry, loudly, instead of raising;
  * the writer is atomic, so an interrupted write cannot produce that damaged cache
    in the first place.
"""

import csv
import logging
import os
import shutil
from types import SimpleNamespace

import pytest

import control._def as _def


SHIPPED_DIR = "objective_and_sample_formats"
CACHE_DIR = "cache"
CSV_NAME = "sample_formats.csv"

HEADER = "format,a1_x_mm,a1_y_mm,a1_x_pixel,a1_y_pixel,well_size_mm,well_spacing_mm,number_of_skip,rows,cols"


@pytest.fixture
def formats_tree(catalog_tree):
    """`load_formats()` resolves paths relative to the process cwd, so tests
    run chdir'd into the shared catalog tree rather than injecting paths."""
    return catalog_tree


def _shipped_96_a1_x(tmp_path):
    with open(tmp_path / SHIPPED_DIR / CSV_NAME) as f:
        for row in csv.DictReader(f):
            if row["format"] == "96":
                return float(row["a1_x_mm"])
    raise AssertionError("shipped sample_formats.csv has no 96 row")


def _write_cache(tmp_path, text):
    (tmp_path / CACHE_DIR / CSV_NAME).write_text(text)


# --- reading a damaged cache must not be fatal ----------------------------------


@pytest.mark.parametrize(
    "label,content",
    [
        # Truncated mid-row: trailing fields become '' -> float('') raises ValueError.
        ("truncated mid-row", HEADER + "\nglass slide,0,0,0,0,"),
        # Truncated mid-number: missing fields become None -> int(None) raises TypeError.
        ("truncated mid-number", HEADER + "\n96,11.31,10.75,171,135,6.21,9.0,0,8"),
        # Header survived but every data row was lost: parses "fine" into {}.
        ("header only", HEADER + "\n"),
        # Nothing at all was flushed.
        ("empty file", ""),
        # Garbage where a number belongs.
        ("non-numeric field", HEADER + "\n96,not-a-number,10.75,171,135,6.21,9.0,0,8,12"),
    ],
)
def test_damaged_cache_falls_back_to_shipped(formats_tree, caplog, label, content):
    _write_cache(formats_tree, content)

    with caplog.at_level(logging.ERROR):
        _, sample_formats = _def.load_formats()

    assert "96 well plate" in sample_formats, f"{label}: shipped formats were not loaded"
    assert sample_formats["96 well plate"]["a1_x_mm"] == _shipped_96_a1_x(formats_tree)
    assert caplog.records, f"{label}: fell back silently — an operator must be able to find this"


def test_valid_cache_calibration_survives_migration(formats_tree):
    """EXPECTATION FLIPPED by the placement-sidecar commit: a valid legacy cache
    is migrated on load - the catalog dict returns to the SHIPPED a1 and the
    calibration lives on as a placement delta, composing to the exact same
    coordinates. Real calibrations still win; they just have one owner now."""
    from control.core.plate_transform import plate_transform_for

    shipped = _shipped_96_a1_x(formats_tree)
    calibrated = shipped + 1.234
    _write_cache(
        formats_tree,
        HEADER + f"\n96,{calibrated},10.75,171,135,6.21,9.0,0,8,12",
    )

    _, sample_formats = _def.load_formats()

    assert sample_formats["96 well plate"]["a1_x_mm"] == shipped  # catalog = shipped
    assert plate_transform_for("96 well plate").well_center_mm(0, 0)[0] == calibrated  # composed
    assert not os.path.exists(formats_tree / CACHE_DIR / CSV_NAME)  # cache retired
    assert os.path.exists(formats_tree / CACHE_DIR / (CSV_NAME + ".migrated"))


def test_absent_cache_uses_shipped(formats_tree):
    _, sample_formats = _def.load_formats()

    assert sample_formats["96 well plate"]["a1_x_mm"] == _shipped_96_a1_x(formats_tree)
    assert "1536 well plate" in sample_formats


# --- writing must be atomic -----------------------------------------------------


def test_write_is_atomic_on_failure(formats_tree, monkeypatch):
    """An interrupted write must leave the previous cache byte-for-byte intact."""
    cache_file = formats_tree / CACHE_DIR / CSV_NAME
    good = HEADER + "\n96,99.0,10.75,171,135,6.21,9.0,0,8,12\n"
    cache_file.write_text(good)

    def boom(*args, **kwargs):
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        _def.write_sample_formats_csv(
            str(cache_file), {"96 well plate": _def.read_sample_formats_csv(str(cache_file))["96 well plate"]}
        )

    assert cache_file.read_text() == good, "a failed write corrupted the existing cache"
    leftovers = [p.name for p in (formats_tree / CACHE_DIR).iterdir() if p.name != CSV_NAME]
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_write_then_read_roundtrips(formats_tree):
    cache_file = formats_tree / CACHE_DIR / CSV_NAME
    settings = _def.read_sample_formats_csv(str(formats_tree / SHIPPED_DIR / CSV_NAME))

    _def.write_sample_formats_csv(str(cache_file), settings)
    reloaded = _def.read_sample_formats_csv(str(cache_file))

    assert reloaded == settings


def test_write_creates_parent_directory(formats_tree):
    target = formats_tree / "brand-new-dir" / CSV_NAME
    settings = _def.read_sample_formats_csv(str(formats_tree / SHIPPED_DIR / CSV_NAME))

    _def.write_sample_formats_csv(str(target), settings)

    assert _def.read_sample_formats_csv(str(target)) == settings


def test_write_rejects_settings_missing_a_column(formats_tree):
    """A settings dict missing a required column must fail loudly, not write a hole.

    The previous implementation used `{**{"format": f}, **settings}`, which silently
    emitted a blank cell for any absent key -- and a blank cell is exactly what
    read_sample_formats_csv chokes on at import time.
    """
    settings = _def.read_sample_formats_csv(str(formats_tree / SHIPPED_DIR / CSV_NAME))
    del settings["96 well plate"]["rows"]

    with pytest.raises(KeyError):
        _def.write_sample_formats_csv(str(formats_tree / CACHE_DIR / CSV_NAME), settings)


def test_write_warns_about_settings_with_no_column(formats_tree, caplog):
    """An extra key is dropped rather than raising -- but must say so."""
    settings = _def.read_sample_formats_csv(str(formats_tree / SHIPPED_DIR / CSV_NAME))
    settings["96 well plate"]["some_future_field_mm"] = 1.0

    with caplog.at_level(logging.WARNING):
        _def.write_sample_formats_csv(str(formats_tree / CACHE_DIR / CSV_NAME), settings)

    assert any("some_future_field_mm" in r.getMessage() for r in caplog.records)
    # ...and the rest of the row still round-trips.
    reloaded = _def.read_sample_formats_csv(str(formats_tree / CACHE_DIR / CSV_NAME))
    assert reloaded["96 well plate"]["a1_x_mm"] == settings["96 well plate"]["a1_x_mm"]


def test_widget_save_delegates_to_atomic_writer(monkeypatch):
    """WellplateFormatWidget must not carry its own copy of the write logic."""
    import control.widgets

    calls = []
    monkeypatch.setattr(
        _def, "write_sample_formats_csv", lambda path, settings: calls.append((path, settings)), raising=True
    )

    widget = SimpleNamespace(csv_path=CSV_NAME)
    control.widgets.WellplateFormatWidget.save_formats_to_csv(widget)

    assert len(calls) == 1
    path, settings = calls[0]
    assert path == os.path.join(CACHE_DIR, CSV_NAME)
    assert settings is _def.WELLPLATE_FORMAT_SETTINGS
