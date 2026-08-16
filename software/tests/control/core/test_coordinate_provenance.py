"""Provenance stamps on scan-coordinate CSVs: absolute positions carry the
placement they were computed under, and loading under a different placement
says so instead of silently replaying stale positions."""

import pandas as pd
import pytest

from control.core.coordinate_provenance import (
    make_stamp,
    parse_stamp,
    read_scan_coordinates_csv,
    staleness_warning,
    STAMP_PREFIX,
    write_scan_coordinates_csv,
)
from control.models.plate_holder import HolderMeasuredPoint, HolderMeasurement, PlateHolder, save_plate_holder
from control.models.sample_format_config import load_user_sample_formats


@pytest.fixture
def tree(catalog_tree):
    return catalog_tree


def holder(rotation_deg):
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


def df_fixture():
    return pd.DataFrame(
        [["A1", 11.31, 10.75], ["A1", 12.21, 10.75]],
        columns=["region", "x (mm)", "y (mm)"],
    )


def test_csv_round_trip_with_stamp(tree):
    save_plate_holder(holder(0.21))
    write_scan_coordinates_csv("coords.csv", df_fixture(), "96 well plate")

    df, stamp = read_scan_coordinates_csv("coords.csv")
    pd.testing.assert_frame_equal(df, df_fixture())
    assert stamp["format"] == "96 well plate"
    assert stamp["rotation_deg"] == 0.21
    assert stamp["rotation_source"] == "holder"
    # unchanged placement -> no warning
    assert staleness_warning(stamp, "96 well plate") is None


def test_legacy_unstamped_csv_loads_without_stamp(tree):
    df_fixture().to_csv("legacy.csv", index=False)
    df, stamp = read_scan_coordinates_csv("legacy.csv")
    pd.testing.assert_frame_equal(df, df_fixture())
    assert stamp is None


def test_rotation_change_is_flagged(tree):
    save_plate_holder(holder(0.21))
    write_scan_coordinates_csv("coords.csv", df_fixture(), "96 well plate")
    save_plate_holder(holder(0.34))  # re-measured after the save

    _, stamp = read_scan_coordinates_csv("coords.csv")
    msg = staleness_warning(stamp, "96 well plate")
    assert msg is not None
    assert "0.21" in msg and "0.34" in msg and "rotation" in msg


def test_a1_change_is_flagged(tree, monkeypatch):
    write_scan_coordinates_csv("coords.csv", df_fixture(), "96 well plate")

    from control.models.sample_format_config import (
        FormatMeasurement,
        MeasuredPoint,
        SampleFormat,
        save_user_sample_formats,
        UserSampleFormats,
    )

    save_user_sample_formats(
        UserSampleFormats(
            formats={
                "96 well plate": SampleFormat(
                    rows=8,
                    cols=12,
                    well_spacing_mm=9.0,
                    well_size_mm=6.21,
                    a1_x_mm=11.81,
                    a1_y_mm=10.75,
                    measured=FormatMeasurement(
                        points=[MeasuredPoint(well="A1", x_mm=11.81, y_mm=10.75)],
                        timestamp="2026-08-16T00:00:00",
                    ),
                )
            }
        )
    )
    import control._def as _def_mod

    monkeypatch.setitem(
        _def_mod.WELLPLATE_FORMAT_SETTINGS,
        "96 well plate",
        load_user_sample_formats().formats["96 well plate"].to_settings(),
    )
    _, stamp = read_scan_coordinates_csv("coords.csv")
    msg = staleness_warning(stamp, "96 well plate")
    assert msg is not None and "A1 position changed" in msg


def test_sub_tolerance_drift_is_not_flagged(tree, monkeypatch):
    write_scan_coordinates_csv("coords.csv", df_fixture(), "96 well plate")

    from control.models.sample_format_config import (
        FormatMeasurement,
        MeasuredPoint,
        SampleFormat,
        save_user_sample_formats,
        UserSampleFormats,
    )

    save_user_sample_formats(
        UserSampleFormats(
            formats={
                "96 well plate": SampleFormat(
                    rows=8,
                    cols=12,
                    well_spacing_mm=9.0,
                    well_size_mm=6.21,
                    a1_x_mm=11.3105,
                    a1_y_mm=10.75,
                    measured=FormatMeasurement(
                        points=[MeasuredPoint(well="A1", x_mm=11.3105, y_mm=10.75)],
                        timestamp="2026-08-16T00:00:00",
                    ),
                )
            }
        )
    )
    import control._def as _def_mod

    monkeypatch.setitem(
        _def_mod.WELLPLATE_FORMAT_SETTINGS,
        "96 well plate",
        load_user_sample_formats().formats["96 well plate"].to_settings(),
    )
    _, stamp = read_scan_coordinates_csv("coords.csv")
    assert staleness_warning(stamp, "96 well plate") is None


def test_format_mismatch_is_flagged(tree):
    write_scan_coordinates_csv("coords.csv", df_fixture(), "96 well plate")
    _, stamp = read_scan_coordinates_csv("coords.csv")
    msg = staleness_warning(stamp, "384 well plate")
    assert msg is not None and "96 well plate" in msg and "384 well plate" in msg


def test_vanished_format_is_flagged(tree):
    stamp = make_stamp("96 well plate")
    stamp["format"] = "my old custom plate"
    msg = staleness_warning(stamp, "my old custom plate")
    assert msg is not None and "not in the current catalog" in msg


def test_garbage_stamp_is_ignored(tree):
    with open("bad.csv", "w") as f:
        f.write(STAMP_PREFIX + "{not json\n")
        df_fixture().to_csv(f, index=False)
    # unparseable stamp -> treated as a plain comment-less file would be...
    df, stamp = read_scan_coordinates_csv("bad.csv")
    assert stamp is None
    # ...which means pandas sees the malformed first line; the widget's
    # column validation rejects it with its normal error path.
    assert parse_stamp("region,x (mm),y (mm)") is None
