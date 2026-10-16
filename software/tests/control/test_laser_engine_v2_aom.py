"""DF 560 nm intensity = the AOM amplitude (line 3 analog, 0-5 V), linear in volts. The laser power is the operator's and
never changes here. (Engine lines are calibrated with Utils > Illumination Power Calibration, not here.)"""

import pytest

from control.laser_engine_v2 import LaserEngineV2, LaserEngineV2Error
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine, FakeSource
from control.laser_engine_v2_status import LineState
from control.lighting import CalibrationLookup
from tests.squid.calibration_fixtures import make_engine_calibration


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _engine():
    fake, source = FakeEngine(tok_delay_polls=0), FakeSource()  # 200-1000 mW
    engine = LaserEngineV2(link_factory=lambda: EngineLink(fake), source_factory=lambda: source, query_interval_s=0.01)
    engine.open()
    return engine, fake, source


def _ready_engine():
    engine, fake, source = _engine()
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    return engine, fake, source


def _aom_sets(sent, since=0):
    return [c for c in sent[since:] if c.startswith("LINE3:SET")]


def test_intensity_is_linear_in_aom_volts_and_never_touches_the_laser_power():
    engine, fake, source = _ready_engine()
    calls, power = list(source.calls), source.power_mw
    mark = len(fake.sent)
    for pct in (50.0, 100.0, 0.0):
        engine.light_source.set_intensity(560, pct)  # Squid's channel intensity
        engine.source_step()
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 2.500", "LINE3:SET 5.000", "LINE3:SET 0.000"]
    assert source.calls == calls and source.power_mw == power  # no "power" request: the operator's setting stays
    assert engine.get_wavelength_intensity(560) == 0.0 and engine.get_line_intensity(3) == 0.0
    assert engine.poll_once().channels["L3"].state == LineState.READY  # 0 % = dark at the AOM, the laser stays up
    assert engine.aom_percent_for_volts(2.5) == pytest.approx(50.0)


def test_an_old_560_aom_csv_is_not_used(tmp_path, monkeypatch):
    (tmp_path / "560_aom.csv").write_text("AOM Volts,Transmission\n0,0\n1,0.1\n2,0.4\n3,0.8\n4,1.0\n5,0.95\n")
    monkeypatch.setattr("control.laser_engine_v2.DEFAULT_CALIBRATION_DIR", tmp_path, raising=False)
    engine, fake, source = _ready_engine()
    mark = len(fake.sent)
    engine.set_wavelength_intensity(560, 100.0)
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 5.000"]  # linear: not the file's 4 V peak


def test_560_intensity_goes_through_the_calibration_the_provider_gives():
    engine, fake, source = _ready_engine()
    c = make_engine_calibration(wavelength_nm=560, port="D3", line=3, ceiling=5.0, unit="V")
    engine.calibration_provider = lambda wavelength: CalibrationLookup(c.file_name, c if wavelength == 560 else None)
    mark, power = len(fake.sent), source.power_mw
    engine.light_source.set_intensity(560, 50.0)
    volts = c.commanded_percent(50.0, 1.0, 1.0)[0] / 100.0 * 5.0
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET %.3f" % volts]
    assert source.power_mw == power


def test_aom_set_points_are_the_millivolts_sent():
    engine, fake, source = _ready_engine()
    engine.set_line_intensity(3, 40.0)  # 2.000 V
    mark = len(fake.sent)
    engine.set_line_intensity(3, 40.018)  # 2.0009 V: 2.001 V on the wire, a change, so it is sent
    engine.set_line_intensity(3, 40.0196)  # 2.00098 V: the same 2.001 V, not sent again
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 2.001"]
    assert fake.lines[2]["target"] == 2.001


def test_wake_sets_the_aom_to_the_requested_intensity_dark_until_asked():
    engine, fake, source = _engine()
    engine.wake_up("L3")  # nothing requested yet: the AOM at 0 V
    assert _aom_sets(fake.sent) == ["LINE3:SET 0.000"]
    engine.set_wavelength_intensity(560, 40.0)
    for _ in range(5):
        engine.source_step()
    engine.put_to_sleep("L3")
    engine.source_step()
    mark = len(fake.sent)
    engine.wake_up("L3")  # the next start takes the AOM back to the requested 40 %
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 2.000"]


def test_intensity_without_a_source_is_refused():
    fake = FakeEngine(tok_delay_polls=0)
    engine = LaserEngineV2(link_factory=lambda: EngineLink(fake), query_interval_s=0.01)
    engine.open()
    with pytest.raises(LaserEngineV2Error, match="560 nm source not configured"):
        engine.set_line_intensity(3, 50.0)
    assert not _aom_sets(fake.sent)
