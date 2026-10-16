"""The IlluminationController on a laser engine v2 instrument (spec §6): which calibration applies to an engine
channel, the identity check (files and candidates), the cap, the description, and re-sending an unchanged % whenever
the applicable calibration changes."""

import os
from unittest.mock import MagicMock

import pytest

from control.core.config import ConfigRepository
from control.laser_engine_v2_link import EngineLink
from control.lighting import IlluminationController, IntensityControlMode, LightSourceType, ShutterControlMode
from squid.intensity_calibration import write_calibration
from tests.control.engine_calibration_fixtures import engine_instrument
from tests.squid.calibration_fixtures import (
    DAC,
    FakeMicrocontroller,
    bench_488_led_mw,
    make_calibration,
    make_engine_calibration,
    write_legacy_csv,
)


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


@pytest.fixture
def instrument(tmp_path):
    inst = engine_instrument(tmp_path)
    yield inst
    inst.engine.close()


def _install(instrument, calibration, channel_name="Fluorescence 488 nm Ex"):
    """Write the file and point the channel at it, as Save does."""
    write_calibration(calibration, instrument.calibrations / calibration.file_name)
    config = instrument.repo.get_illumination_config(for_edit=True)
    next(c for c in config.channels if c.name == channel_name).intensity_calibration_file = calibration.file_name
    instrument.repo.save_illumination_config(config)


def _ready(instrument, *keys):
    assert instrument.engine.wait_until_ready(list(keys), timeout_s=10.0)


def _sets(instrument, line, since):
    return [c for c in instrument.fake.sent[since:] if c.startswith(f"LINE{line}:SET")]


def _drive_amps(calibration, percent, ceiling=2.045):
    return "LINE2:SET %.4f" % (calibration.commanded_percent(percent, 1.0, 1.0)[0] / 100.0 * ceiling)


def test_a_calibration_for_this_engine_line_is_applied(instrument):
    c = make_engine_calibration()  # 488 nm, D2, L2 of engine SN-1, ceiling 2.045 A
    _install(instrument, c)
    _ready(instrument, "L2")
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == [_drive_amps(c, 50.0)]


def test_a_calibration_from_another_engine_runs_the_line_linear(instrument):
    _install(instrument, make_engine_calibration(serial="SN-OTHER"))
    _ready(instrument, "L2")
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]
    lookup = instrument.illumination.lookup_calibration(488)
    assert lookup.calibration is None and "SN-OTHER" in lookup.error and "this is engine SN-1" in lookup.error


def test_a_controller_dac_file_and_an_old_file_are_not_applied_to_an_engine_line(instrument):
    _install(instrument, make_calibration(wavelength_nm=488, port="D2", channel="Fluorescence 488 nm Ex"))
    assert "measured through the controller DAC" in instrument.illumination.lookup_calibration(488).error
    write_legacy_csv(instrument.calibrations / "405.csv", DAC, DAC * 2.0)  # found by wavelength, as master did
    assert "no laser engine identity" in instrument.illumination.lookup_calibration(405).error


def test_a_calibration_for_a_channel_moved_to_another_port_is_not_applied(instrument):
    _install(instrument, make_engine_calibration())
    config = instrument.repo.get_illumination_config(for_edit=True)
    next(c for c in config.channels if c.name == "Fluorescence 488 nm Ex").controller_port = "D5"
    instrument.repo.save_illumination_config(config)
    lookup = instrument.illumination.lookup_calibration(488)
    assert lookup.calibration is None and "now 488 nm on D5" in lookup.error


def test_a_560_calibration_still_applies_after_the_laser_power_changes(instrument):
    c = make_engine_calibration(
        wavelength_nm=560,
        port="D3",
        line=3,
        ceiling=5.0,
        unit="V",
        channel="Fluorescence 560 nm Ex",
        source_power_mw=(300.0, 300.0),
    )
    _install(instrument, c, "Fluorescence 560 nm Ex")
    _ready(instrument, "L3")
    instrument.engine.set_source_power_mw(700.0)
    assert instrument.illumination.lookup_calibration(560).calibration is not None


def test_engine_channels_offer_the_full_range_whatever_max_output_says(instrument):
    assert instrument.illumination.get_intensity_cap_percent(488, 0.5) == 100.0
    _install(instrument, make_engine_calibration())
    assert instrument.illumination.get_intensity_cap_percent(488, 0.5) == 100.0


def test_description_of_engine_channels(instrument):
    _ready(instrument, "L2", "L3")
    assert instrument.illumination.describe_intensity(488) == {
        "intensity_unit": "engine_drive_percent",
        "engine_drive": "current",
    }
    assert instrument.illumination.describe_intensity(560)["engine_drive"] == "aom"
    _install(instrument, make_engine_calibration())
    described = instrument.illumination.describe_intensity(488)
    assert described["intensity_unit"] == "power_percent" and described["engine_serial"] == "SN-1"


def test_a_candidate_applies_ahead_of_the_file_until_cleared(instrument):
    _ready(instrument, "L2")  # the file and the candidate are checked against the engine's status
    _install(instrument, make_engine_calibration())
    candidate = make_engine_calibration(model=bench_488_led_mw)
    instrument.illumination.install_candidate(488, candidate)
    assert instrument.illumination.lookup_calibration(488).calibration is candidate
    instrument.illumination.clear_candidate(488)
    on_file = instrument.illumination.lookup_calibration(488).calibration
    assert on_file is not None and on_file is not candidate


def test_a_candidate_measured_on_another_engine_is_not_applied(instrument):
    _ready(instrument, "L2")
    instrument.illumination.install_candidate(488, make_engine_calibration(serial="SN-OTHER"))
    lookup = instrument.illumination.lookup_calibration(488)
    assert lookup.calibration is None and "SN-OTHER" in lookup.error


def test_an_edited_file_resends_an_unchanged_percent(instrument):
    _install(instrument, make_engine_calibration())
    _ready(instrument, "L2")
    instrument.illumination.set_intensity(488, 50.0)
    path = instrument.calibrations / "488nm_D2.csv"
    edited = make_engine_calibration(model=bench_488_led_mw)
    write_calibration(edited, path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))  # a later mtime, as an edit a second later has
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == [_drive_amps(edited, 50.0)]


def test_an_engine_identity_change_resends_an_unchanged_percent(instrument):
    _install(instrument, make_engine_calibration())
    _ready(instrument, "L2")
    instrument.illumination.set_intensity(488, 50.0)
    instrument.engine.serial_number = "SN-2"  # as if another engine had been connected
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]  # no longer applies: linear


def test_installing_and_clearing_a_candidate_resend_an_unchanged_percent(instrument):
    _ready(instrument, "L2")
    instrument.illumination.set_intensity(488, 50.0)
    candidate = make_engine_calibration()
    instrument.illumination.install_candidate(488, candidate)
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == [_drive_amps(candidate, 50.0)]
    instrument.illumination.clear_candidate(488)
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 50.0)
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.5 * 2.045)]


def test_a_forgotten_intensity_is_sent_again(instrument):
    _ready(instrument, "L2")
    instrument.illumination.set_intensity(488, 40.0)
    mark = len(instrument.fake.sent)
    instrument.illumination.set_intensity(488, 40.0)
    assert _sets(instrument, 2, mark) == []  # nothing changed: not sent again
    instrument.illumination.forget_intensities([488])
    instrument.illumination.set_intensity(488, 40.0)
    assert _sets(instrument, 2, mark) == ["LINE2:SET %.4f" % (0.4 * 2.045)]


def test_other_software_light_sources_still_skip_an_unchanged_percent(tmp_path):
    light_source = MagicMock()
    light_source.channel_mappings = {405: 405}
    light_source.get_intensity.return_value = 0.0
    controller = IlluminationController(
        FakeMicrocontroller(),
        IntensityControlMode.Software,
        ShutterControlMode.TTL,
        LightSourceType.LDI,
        light_source,
        config_repo=ConfigRepository(base_path=tmp_path),
    )
    controller.set_intensity(405, 50.0)
    controller.set_intensity(405, 50.0)
    assert light_source.set_intensity.call_count == 1


def test_a_controller_dac_instrument_has_no_engine_drive():
    assert IlluminationController(FakeMicrocontroller()).engine_drive(488) is None


def test_a_channel_not_on_an_engine_line_keeps_its_max_output_and_description(instrument):
    # final review I1: on an engine instrument, a wavelength whose port is not an engine line is not an engine channel
    assert instrument.illumination.get_intensity_cap_percent(999, 0.5) == 50.0
    assert instrument.illumination.describe_intensity(999) == {"intensity_unit": "source_percent"}
