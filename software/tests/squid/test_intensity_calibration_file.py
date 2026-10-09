"""New-format calibrations (design §6, §6.2): the lookup through the factor and the ceiling, staleness, the file."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from squid.intensity_calibration import (
    FORMAT,
    CalibrationFileError,
    load_calibration,
    read_header,
    write_calibration,
)
from squid.power_meter import simulated_led_mw
from tests.squid.calibration_fixtures import make_calibration


def test_calibrated_commands_follow_the_running_factor():
    calibration = make_calibration(factor=0.8)
    at_08, clamped = calibration.commanded_percent(50, factor=0.8, max_output=1.0)
    assert not clamped
    at_06, clamped = calibration.commanded_percent(50, factor=0.6, max_output=1.0)
    assert at_06 == pytest.approx(at_08 * 0.8 / 0.6) and not clamped
    at_10, clamped = calibration.commanded_percent(50, factor=1.0, max_output=1.0)
    assert at_10 == pytest.approx(at_08 * 0.8) and not clamped
    assert calibration.commanded_percent(100, factor=0.6, max_output=1.0) == (100.0, True)  # top unreachable


def test_commands_never_pass_max_output_and_zero_is_off():
    calibration = make_calibration()
    assert calibration.commanded_percent(0, factor=0.6, max_output=1.0) == (0.0, False)
    commanded, clamped = calibration.commanded_percent(100, factor=0.6, max_output=0.5)
    assert commanded == 50.0 and clamped
    # factor 0 (accepted by Preferences and the firmware): dark, flagged, no ZeroDivisionError
    assert calibration.commanded_percent(0, factor=0.0, max_output=1.0) == (0.0, False)
    assert calibration.commanded_percent(50, factor=0.0, max_output=1.0) == (100.0, True)


def test_calibrated_channel_slider_is_not_capped():
    assert make_calibration().cap_percent(0.3) == 100.0


def test_describe_names_the_unit_and_the_file():
    description = make_calibration().describe()
    assert description["intensity_unit"] == "power_percent"
    assert description["calibration_format"] == FORMAT
    assert description["calibration_file"] == "405nm_D1.csv"
    assert description["measured_in"] == "widefield"
    assert description["verification"] == "pass"
    assert description["max_power_mw"] == pytest.approx(300.0, rel=1e-3)


def test_staleness_notes():
    calibration = make_calibration(factor=0.8, max_output=1.0)
    assert calibration.notes(factor=0.8, max_output=1.0) == []
    assert calibration.notes(factor=1.0, max_output=1.0) == []  # a higher factor only adds headroom
    assert "lowered 0.8 -> 0.6" in calibration.notes(factor=0.6, max_output=1.0)[0]
    assert "Max Output changed 1 -> 0.5" in calibration.notes(factor=0.8, max_output=0.5)[0]
    assert calibration.status(0.8, 1.0) == "calibrated 2026-10-08"
    assert calibration.status(0.6, 1.0).startswith("stale (2026-10-08)")
    assert replace(calibration, verification="fail").status(0.8, 1.0) == "failed verification (2026-10-08)"


def test_file_round_trip_keeps_the_lookup(tmp_path):
    calibration = replace(
        make_calibration(model=simulated_led_mw, wavelength_nm=730, port="D5", channel="LED: 730 #2"),
        hold_s=10.0,
        hold_droop_fraction=-0.031,
    )
    path = tmp_path / calibration.file_name
    write_calibration(calibration, path)
    loaded = load_calibration(path)

    assert path.name == "730nm_D5.csv"
    assert read_header(path)["format"] == FORMAT
    assert loaded.channel == "LED: 730 #2" and loaded.file_name == "730nm_D5.csv"
    assert loaded.rollover == calibration.rollover is not None
    assert loaded.verification == "pass" and loaded.verification_points == calibration.verification_points
    assert (loaded.pulse_on_s, loaded.hold_s, loaded.hold_droop_fraction) == (0.1, 10.0, -0.031)
    assert loaded.describe()["continuous_hold_droop_percent"] == -3.1
    assert np.isnan(loaded.power_mw_fit[-1])  # nothing fitted past the rollover
    for r in np.linspace(0, 100, 401):
        assert loaded.commanded_percent(r, 0.6, 1.0)[0] == pytest.approx(calibration.commanded_percent(r, 0.6, 1.0)[0])


def test_unknown_format_and_broken_files_are_rejected(tmp_path):
    unknown = tmp_path / "a.csv"
    unknown.write_text("# format: something-else/9\nx\n1\n")
    with pytest.raises(CalibrationFileError, match="unknown format"):
        load_calibration(unknown)
    calibration = make_calibration()
    truncated = tmp_path / "b.csv"
    write_calibration(calibration, truncated)
    truncated.write_text(truncated.read_text().split("dac_percent_commanded")[0] + "dac_percent_commanded\n1\n")
    with pytest.raises(CalibrationFileError, match="b.csv"):
        load_calibration(truncated)


def _write_edited(tmp_path, edit):
    """A valid file whose table was then edited by hand."""
    calibration = make_calibration()
    path = tmp_path / calibration.file_name
    write_calibration(calibration, path)
    header = "".join(line for line in path.read_text().splitlines(keepends=True) if line.startswith("#"))
    table = pd.read_csv(path, comment="#")
    edit(table)
    path.write_text(header + table.to_csv(index=False))
    return path


def _set(column, row, value):
    return lambda t: t.__setitem__(column, t[column].where(t.index != row, value))


def _scale(column, factor):
    return lambda t: t.__setitem__(column, t[column] * factor)


@pytest.mark.parametrize(
    "edit, message",
    [
        (_set("dac_fraction_of_full_scale", 130, float("nan")), "blank or invalid DAC values"),
        (_set("dac_percent_commanded", 5, float("nan")), "blank or invalid DAC values"),
        (_set("dac_fraction_of_full_scale", 50, 0.0), "must increase"),
        (_scale("dac_fraction_of_full_scale", 2.0), "within 0-1"),
        (_scale("dac_fraction_of_full_scale", 0.9), "do not match"),
        (_set("power_mw_fit", 100, float("nan")), "must cover the sweep"),
        (_set("power_mw_fit", 150, 1.0), "not monotone"),
    ],
)
def test_unusable_lookup_numbers_are_rejected(tmp_path, edit, message):
    path = _write_edited(tmp_path, edit)
    with pytest.raises(CalibrationFileError, match=f"405nm_D1.csv: .*{message}"):
        load_calibration(path)
