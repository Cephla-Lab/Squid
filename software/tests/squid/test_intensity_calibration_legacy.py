"""Files from before 2026-10 (design §6, §6.3): healthy ones keep master's lookup bit for bit; broken ones are
repaired in memory; the file on disk is never touched."""

import numpy as np
import pytest

from squid.intensity_calibration import (
    CalibrationFileError,
    LegacyCalibration,
    calibration_status,
    intensity_tooltip,
    load_calibration,
    resolve_calibration_path,
    write_calibration,
)
from squid.power_meter import simulated_laser_mw, simulated_led_mw
from tests.squid.calibration_fixtures import DAC, make_calibration, master_lookup, write_legacy_csv

GRID = np.round(np.arange(0.0, 100.0001, 0.1), 6)


def _healthy_curves():
    rng = np.random.default_rng(0)
    x = DAC / 100.0 * 0.6
    return {
        "laser": simulated_laser_mw(x) * (1 + 0.003 * rng.standard_normal(DAC.size)) + 0.002,
        "led": 310 * (1 - np.exp(-DAC / 60)) / (1 - np.exp(-100 / 60)) * (1 + 0.005 * rng.standard_normal(DAC.size)),
    }


def _broken_curves():
    """name -> (file's power column, start of the repair reason, the true source curve)."""
    rollover_3 = np.where(DAC <= 85, 312 * (1 - (1 - DAC / 85) ** 2), 312 - 9 * (np.maximum(DAC - 85, 0) / 15) ** 1.5)
    rollover_7 = simulated_led_mw(DAC / 100.0 * 0.6)
    linear = 300.0 * DAC / 100.0
    spiky = linear.copy()
    spiky[100] *= 2.0
    spiky[140] *= 1.6
    return {
        "rollover 3 %": (rollover_3, "output falls above DAC", rollover_3),
        "rollover 7 %": (rollover_7, "output falls above DAC", rollover_7),
        "spikes": (spiky, "the old lookup is off by", linear),  # two bad readings in a linear source
    }


@pytest.mark.parametrize("name", ["laser", "led"])
def test_healthy_legacy_file_is_bit_identical_to_master(tmp_path, name):
    path = tmp_path / "405.csv"
    write_legacy_csv(path, DAC, _healthy_curves()[name])
    calibration = load_calibration(path)
    assert isinstance(calibration, LegacyCalibration) and calibration.repair_reason is None
    for r in GRID:
        assert calibration.commanded_percent(r, factor=0.6, max_output=1.0) == (float(master_lookup(path, r)), False)
    assert calibration.cap_percent(1.0) == 100.0 and calibration.cap_percent(0.4) == 40.0
    assert calibration.describe() == {
        "intensity_unit": "power_percent",
        "calibration_format": "legacy",
        "calibration_file": "405.csv",
    }
    assert calibration.status(0.6, 1.0) == "legacy"


@pytest.mark.parametrize("name", ["rollover 3 %", "rollover 7 %", "spikes"])
def test_broken_legacy_file_is_repaired_in_memory_only(tmp_path, name):
    power, reason, truth = _broken_curves()[name]
    path = tmp_path / "730.csv"
    write_legacy_csv(path, DAC, power)
    before = path.read_bytes()
    calibration = load_calibration(path)

    assert path.read_bytes() == before
    assert calibration.repair_reason.startswith(reason)
    assert calibration.describe()["calibration_format"] == "legacy-repaired"
    assert calibration.status(0.6, 1.0).startswith("legacy, repaired (optically unverified):")
    assert any("repaired" in note for note in calibration.notes(0.6, 1.0))
    # the repaired lookup is linear against the true source, and never past a peak
    for r in (10, 30, 50, 70, 90, 100):
        dac = calibration.commanded_percent(r, factor=0.6, max_output=1.0)[0]
        measured = np.interp(dac, DAC, truth)
        assert measured == pytest.approx(r / 100 * calibration._fit.p_max_mw, rel=0.05)
    if name.startswith("rollover"):
        assert calibration.commanded_percent(100, 0.6, 1.0)[0] <= DAC[np.argmax(power)] + 1.0


def test_noise_alone_never_triggers_a_repair(tmp_path):
    rng = np.random.default_rng(3)
    for i in range(100):
        threshold = rng.uniform(0, 50)
        curve = np.where(DAC < threshold, 0.0, 300.0 * (DAC / 100.0) ** rng.uniform(0.5, 1.8))
        path = tmp_path / f"{i}.csv"
        write_legacy_csv(path, DAC, curve * (1 + rng.uniform(0, 0.01) * rng.standard_normal(DAC.size)))
        assert load_calibration(path).repair_reason is None


def test_healthy_legacy_file_is_clamped_at_a_lowered_max_output(tmp_path):
    path = tmp_path / "405.csv"
    write_legacy_csv(path, DAC, simulated_laser_mw(DAC / 100.0 * 0.6))  # threshold at 30 % DAC
    calibration = load_calibration(path)
    for r in GRID:
        master = float(master_lookup(path, r))
        over = master > 50.0 + 1e-9  # the same tolerance as _clamp_to_ceiling
        commanded, clamped = calibration.commanded_percent(r, factor=0.6, max_output=0.5)
        assert commanded == (50.0 if over else master)
        assert clamped == over
    assert "clamped" in calibration.status(0.6, 0.5)


@pytest.mark.parametrize(
    "text", ["a,b\n1,2\n3,4\n5,6\n", "DAC Percent,Optical Power (mW)\n0,0\n100,5\n", "\x00garbage"]
)
def test_unusable_legacy_files_are_invalid(tmp_path, text):
    path = tmp_path / "405.csv"
    path.write_text(text)
    with pytest.raises(CalibrationFileError, match="405.csv"):
        load_calibration(path)


def test_blank_cells_and_row_order_are_tolerated(tmp_path):
    power = 300.0 * DAC / 100.0
    write_legacy_csv(tmp_path / "405.csv", DAC, power)
    with_blank = power.copy()
    with_blank[40] = np.nan
    write_legacy_csv(tmp_path / "488.csv", DAC, with_blank)
    write_legacy_csv(tmp_path / "561.csv", DAC[::-1], power[::-1])
    good, blank, reversed_rows = (load_calibration(tmp_path / name) for name in ("405.csv", "488.csv", "561.csv"))
    for r in (5, 50, 95):
        expected = good.commanded_percent(r, 0.6, 1.0)
        assert blank.commanded_percent(r, 0.6, 1.0)[0] == pytest.approx(expected[0], rel=0.01)
        assert reversed_rows.commanded_percent(r, 0.6, 1.0) == expected
    assert reversed_rows.repair_reason is None


def test_resolution_prefers_the_reference_then_falls_back_like_master(tmp_path):
    (tmp_path / "488.csv").write_text("x")
    assert resolve_calibration_path(tmp_path, None, 488) == tmp_path / "488.csv"
    assert resolve_calibration_path(tmp_path, "488nm_D2.csv", 488) == tmp_path / "488.csv"  # dangling reference
    (tmp_path / "488nm_D2.csv").write_text("x")
    assert resolve_calibration_path(tmp_path, "/old/place/488nm_D2.csv", 488) == tmp_path / "488nm_D2.csv"
    assert resolve_calibration_path(tmp_path, "nothing.csv", 561) is None


def test_calibration_status_for_the_channel_editor(tmp_path):
    assert calibration_status(tmp_path, None, 405, 0.6, 1.0) == ""
    (tmp_path / "405.csv").write_text("\x00garbage")
    assert calibration_status(tmp_path, None, 405, 0.6, 1.0).startswith("invalid file: 405.csv")
    write_calibration(make_calibration(), tmp_path / "405nm_D1.csv")
    assert calibration_status(tmp_path, "405nm_D1.csv", 405, 0.6, 1.0) == "405nm_D1.csv: calibrated 2026-10-08"


def test_intensity_tooltips():
    assert "not linear" in intensity_tooltip({"intensity_unit": "dac_percent"})
    assert intensity_tooltip({"intensity_unit": "source_percent"}) == ""
    tooltip = intensity_tooltip(make_calibration().describe())
    assert "Linear in power" in tooltip and "405nm_D1.csv" in tooltip and "widefield" in tooltip
    legacy = {
        "intensity_unit": "power_percent",
        "calibration_format": "legacy-repaired",
        "calibration_file": "405.csv",
        "repair": "x",
    }
    assert "repaired (optically unverified): x" in intensity_tooltip(legacy)


def test_calibration_status_says_when_the_channel_moved(tmp_path):
    write_calibration(make_calibration(), tmp_path / "405nm_D1.csv")
    status = calibration_status(tmp_path, "405nm_D1.csv", 405, 0.6, 1.0, controller_port="D2")
    assert status.startswith("405nm_D1.csv: not applied:") and "D1" in status and "D2" in status
    assert calibration_status(tmp_path, "405nm_D1.csv", 405, 0.6, 1.0, controller_port="D1") == (
        "405nm_D1.csv: calibrated 2026-10-08"
    )


def test_a_calibration_measured_on_another_controller_source_is_not_applied(tmp_path):
    # review 4: remapping D2 from source 12 to 13 kept the calibration, sending its DAC values to another source
    c = make_calibration(wavelength_nm=488, port="D2", channel="Fluorescence 488 nm Ex", source_code=12)
    path = tmp_path / c.file_name
    write_calibration(c, path)
    loaded = load_calibration(path)
    assert loaded.source_code == 12 and loaded.identity_mismatch(488, "D2", 12) is None
    mismatch = loaded.identity_mismatch(488, "D2", 13)
    assert "source 12" in mismatch and "source 13" in mismatch and mismatch.endswith("recalibrate")
    status = calibration_status(tmp_path, c.file_name, 488, 0.6, 1.0, controller_port="D2", source_code=13)
    assert status.startswith(f"{c.file_name}: not applied:")
    # a file written before the source was recorded is compared by wavelength and port, as before
    path.write_text("".join(line for line in path.read_text().splitlines(True) if "controller_source_code" not in line))
    assert load_calibration(path).identity_mismatch(488, "D2", 13) is None
