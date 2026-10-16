"""Calibrations of laser engine v2 lines (spec §5): the engine identity they record, when they apply, and what the
status and the tooltip say."""

import pytest

from squid.intensity_calibration import (
    CalibrationFileError,
    EngineLineIdentity,
    EngineUnavailable,
    calibration_status,
    intensity_tooltip,
    load_calibration,
    write_calibration,
)
from tests.squid.calibration_fixtures import DAC, make_calibration, make_engine_calibration, write_legacy_csv

L2 = EngineLineIdentity("SN-1", "DF", 2, 2.045, "A")


def _written(tmp_path, calibration):
    path = tmp_path / calibration.file_name
    write_calibration(calibration, path)
    return path


def test_an_engine_calibration_round_trips_through_its_file(tmp_path):
    c = make_engine_calibration()
    path = _written(tmp_path, c)
    text = path.read_text()
    for line in (
        "# light_source: laser_engine_v2",
        "# engine_serial: SN-1",
        "# engine_variant: DF",
        "# engine_line: 2",
        "# line_ceiling: 2.045 A",
        "# illumination_intensity_factor: 1",
        "# max_output: 1",
    ):
        assert line in text
    back = load_calibration(path)
    assert back.engine == L2 and back.source_power_mw is None
    assert back.commanded_percent(50.0, 1.0, 1.0) == pytest.approx(c.commanded_percent(50.0, 1.0, 1.0))


def test_a_560_calibration_records_the_laser_power(tmp_path):
    c = make_engine_calibration(
        wavelength_nm=560, port="D3", line=3, ceiling=5.0, unit="V", source_power_mw=(300.0, 298.7)
    )
    path = _written(tmp_path, c)
    assert "# source_power_mw: 300 / 298.7" in path.read_text()
    back = load_calibration(path)
    assert back.engine == EngineLineIdentity("SN-1", "DF", 3, 5.0, "V") and back.source_power_mw == (300.0, 298.7)


def test_a_controller_dac_file_records_no_engine(tmp_path):
    path = _written(tmp_path, make_calibration())
    assert "light_source" not in path.read_text()
    assert load_calibration(path).engine is None


def test_an_unknown_light_source_is_refused(tmp_path):
    path = _written(tmp_path, make_engine_calibration())
    path.write_text(path.read_text().replace("laser_engine_v2", "laser_engine_v9"))
    with pytest.raises(CalibrationFileError, match="laser_engine_v9"):
        load_calibration(path)


@pytest.mark.parametrize(
    "now, expected",
    [
        (L2, None),
        (EngineLineIdentity("SN-1", "DF", 2, 2.0450001, "A"), None),  # the ceiling as STAT? prints it
        (
            EngineLineIdentity("SN-2", "DF", 2, 2.045, "A"),
            "; this is engine SN-2 (DF) L2, ceiling 2.045 A: recalibrate",
        ),
        (EngineLineIdentity("SN-1", "400HP", 2, 2.045, "A"), "this is engine SN-1 (400HP)"),
        (EngineLineIdentity("SN-1", "DF", 4, 2.045, "A"), "this is engine SN-1 (DF) L4"),
        (EngineLineIdentity("SN-1", "DF", 2, 1.5, "A"), "ceiling 1.5 A: recalibrate"),
        (
            EngineUnavailable("laser engine connection lost"),
            "cannot be checked against the laser engine: laser engine connection lost",
        ),
        (None, "this channel is not on a laser engine: recalibrate"),
    ],
)
def test_an_engine_calibration_applies_only_to_the_line_it_was_measured_on(now, expected):
    mismatch = make_engine_calibration().identity_mismatch(488, "D2", None, now)
    if expected is None:
        assert mismatch is None
    else:
        assert mismatch.startswith("488nm_D2.csv ") and expected in mismatch


def test_a_controller_dac_calibration_never_applies_to_an_engine_line():
    dac = make_calibration(wavelength_nm=488, port="D2")
    assert dac.identity_mismatch(488, "D2", None, None) is None
    assert "measured through the controller DAC; this channel drives laser engine SN-1" in dac.identity_mismatch(
        488, "D2", None, L2
    )
    assert "this instrument's light source is a laser engine" in dac.identity_mismatch(
        488, "D2", None, EngineUnavailable("not connected")
    )


def test_an_old_format_file_is_not_used_for_an_engine_line(tmp_path):
    write_legacy_csv(tmp_path / "488.csv", DAC, DAC * 2.0)
    legacy = load_calibration(tmp_path / "488.csv")
    assert legacy.identity_mismatch(488, "D2", None, None) is None
    assert "no laser engine identity" in legacy.identity_mismatch(488, "D2", None, L2)


def test_the_status_of_an_engine_calibration_ignores_the_controller_factor_and_max_output(tmp_path):
    c = make_engine_calibration()
    _written(tmp_path, c)
    same = calibration_status(tmp_path, c.file_name, 488, 0.6, 0.5, controller_port="D2", engine=L2)
    assert same == "488nm_D2.csv: calibrated 2026-10-08"
    other = calibration_status(
        tmp_path,
        c.file_name,
        488,
        0.6,
        0.5,
        controller_port="D2",
        engine=EngineLineIdentity("SN-2", "DF", 2, 2.045, "A"),
    )
    assert other.startswith("488nm_D2.csv: not applied: ") and "SN-2" in other


def test_description_and_tooltip_of_engine_lines():
    d = make_engine_calibration().describe()
    assert d["light_source"] == "laser_engine_v2" and d["engine_serial"] == "SN-1" and d["engine_line"] == 2
    assert d["line_ceiling"] == "2.045 A" and "source_power_mw_at_calibration" not in d
    d560 = make_engine_calibration(
        wavelength_nm=560, port="D3", line=3, ceiling=5.0, unit="V", source_power_mw=(300.0, 298.7)
    ).describe()
    assert d560["source_power_mw_at_calibration"] == 300.0
    assert "at the 300 mW 560 nm laser power it was measured at" in intensity_tooltip(d560)
    current = intensity_tooltip({"intensity_unit": "engine_drive_percent", "engine_drive": "current"})
    assert current.startswith("Percent of the line's maximum current, not linear in optical power.")
    aom = intensity_tooltip({"intensity_unit": "engine_drive_percent", "engine_drive": "aom"})
    assert aom.startswith("Percent of the AOM's 0-5 V range, not linear in optical power.")
