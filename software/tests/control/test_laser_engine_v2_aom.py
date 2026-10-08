"""DF 560 nm intensity = the AOM amplitude (line 3 analog, 0-5 V): linear in volts by default,
through intensity_calibrations/560_aom.csv when it exists. The laser power is the operator's and never changes here."""

from pathlib import Path

import pytest

from control.laser_engine_v2 import LaserEngineV2, LaserEngineV2Error, load_aom_calibration
from control.laser_engine_v2_link import EngineLink
from control.laser_engine_v2_sim import FakeEngine, FakeSource
from control.laser_engine_v2_status import LineState

CAL = "AOM Volts,Transmission\n0,0\n1,0.1\n2,0.4\n3,0.8\n4,1.0\n5,0.95\n"  # peak at 4 V
NO_CALIBRATIONS = Path(__file__).parent / "no_such_calibration_dir"


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _engine(calibration_dir=NO_CALIBRATIONS):
    fake, source = FakeEngine(tok_delay_polls=0), FakeSource()  # 200-1000 mW
    engine = LaserEngineV2(
        link_factory=lambda: EngineLink(fake),
        source_factory=lambda: source,
        query_interval_s=0.01,
        calibration_dir=calibration_dir,
    )
    engine.open()
    return engine, fake, source


def _ready_engine(calibration_dir=NO_CALIBRATIONS):
    engine, fake, source = _engine(calibration_dir)
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    return engine, fake, source


def _aom_sets(sent, since=0):
    return [c for c in sent[since:] if c.startswith("LINE3:SET")]


def test_aom_calibration_uses_the_rising_part_only(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    trans, volts = load_aom_calibration(tmp_path / "560_aom.csv")
    assert list(volts) == [0, 1, 2, 3, 4] and trans[-1] == pytest.approx(1.0)
    assert load_aom_calibration(tmp_path / "missing.csv") is None


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
    assert any("no AOM calibration (560_aom.csv)" in n for n in engine.notices)


def test_intensity_through_the_aom_calibration(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    engine, fake, source = _ready_engine(tmp_path)
    assert any("AOM calibration 560_aom.csv loaded" in n for n in engine.notices)
    mark, power = len(fake.sent), source.power_mw
    for pct in (50.0, 100.0, 0.0):  # transmission 0.5 -> 2.25 V; 1.0 -> 4 V (the peak, not 5 V); 0 -> 0 V
        engine.set_wavelength_intensity(560, pct)
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 2.250", "LINE3:SET 4.000", "LINE3:SET 0.000"]
    assert source.power_mw == power and engine.aom_percent_for_volts(2.25) == pytest.approx(50.0)


def test_zero_percent_is_0_v_even_when_the_calibration_starts_transmitting(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL.replace("\n0,0\n", "\n0.5,0.05\n"))  # the first row already transmits 5 %
    engine, fake, source = _ready_engine(tmp_path)
    engine.set_line_intensity(3, 50.0)
    mark = len(fake.sent)
    engine.set_line_intensity(3, 0.0)
    assert _aom_sets(fake.sent, mark) == ["LINE3:SET 0.000"]  # dark, not the first row's 0.5 V
    assert engine.aom_percent_for_volts(0.0) == 0.0  # and the tab shows 0 %
    assert source.enabled and engine.poll_once().channels["L3"].state == LineState.READY


def test_unreadable_calibration_is_linear_and_says_why(tmp_path):
    (tmp_path / "560_aom.csv").write_text("not,a,calibration\n1,2,3\n")
    engine, fake, source = _ready_engine(tmp_path)
    assert any("unreadable: 560 nm intensity is linear in AOM volts" in n for n in engine.notices)
    engine.set_line_intensity(3, 50.0)
    assert _aom_sets(fake.sent)[-1] == "LINE3:SET 2.500"


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
