import pytest

from control.laser_engine_rev1 import EngineOptions, LaserEngineRev1, load_aom_calibration
from control.laser_engine_rev1_link import EngineLink
from control.laser_engine_rev1_sim import FakeEngine, FakeSource
from control.laser_engine_rev1_status import LineState

CAL = "AOM Volts,Transmission\n0,0\n1,0.1\n2,0.4\n3,0.8\n4,1.0\n5,0.95\n"  # peak at 4 V
ATTENUATE = EngineOptions(aom_in_path=True, aom_attenuation=True)


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _ready_engine(calibration_dir, options=ATTENUATE):
    fake, source = FakeEngine(tok_delay_polls=0), FakeSource()  # 200-1000 mW
    engine = LaserEngineRev1(
        link_factory=lambda: EngineLink(fake),
        source_factory=lambda: source,
        query_interval_s=0.01,
        options=options,
        calibration_dir=calibration_dir,
    )
    engine.open()
    engine.wake_up("L3")
    for _ in range(5):
        engine.source_step()
    assert engine.poll_once().channels["L3"].state == LineState.READY
    return engine, fake, source


def test_aom_calibration_uses_the_rising_part_only(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    trans, volts = load_aom_calibration(tmp_path / "560_aom.csv")
    assert list(volts) == [0, 1, 2, 3, 4] and trans[-1] == pytest.approx(1.0)
    assert load_aom_calibration(tmp_path / "missing.csv") is None


def test_aom_starts_at_peak_transmission(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    engine, fake, source = _ready_engine(tmp_path)
    assert "LINE3:SET 4.000" in fake.sent and "LINE3:SET 5.000" not in fake.sent  # the peak (4 V), not full scale


def test_aom_dims_below_the_minimum(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    engine, fake, source = _ready_engine(tmp_path)
    engine.set_line_intensity(3, 10.0)  # 100 mW = half the 200 mW minimum -> transmission 0.5 -> 2.25 V
    engine.source_step()
    assert "LINE3:SET 2.250" in fake.sent and source.power_mw == pytest.approx(200.0)
    assert not any("below the 560 nm minimum" in n for n in engine.notices)  # dimmed, not clamped
    assert engine.poll_once().channels["L3"].state == LineState.READY


def test_aom_back_to_full_transmission_at_or_above_the_minimum(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    engine, fake, source = _ready_engine(tmp_path)
    engine.set_line_intensity(3, 10.0)
    engine.set_line_intensity(3, 50.0)  # 500 mW: the laser does it, the AOM at peak transmission
    engine.source_step()
    aom = [c for c in fake.sent if c.startswith("LINE3:SET")]
    assert aom[-2:] == ["LINE3:SET 2.250", "LINE3:SET 4.000"] and source.power_mw == pytest.approx(500.0)


def test_aom_attenuation_without_its_calibration_clamps_and_says_why(tmp_path):
    engine, fake, source = _ready_engine(tmp_path)  # no 560_aom.csv
    engine.set_line_intensity(3, 10.0)
    engine.source_step()
    assert source.power_mw == pytest.approx(200.0) and "LINE3:SET 2.250" not in fake.sent
    assert any("560_aom.csv" in n for n in engine.notices) and any(
        "below the 560 nm minimum" in n for n in engine.notices
    )


def test_aom_attenuation_is_off_by_default(tmp_path):
    (tmp_path / "560_aom.csv").write_text(CAL)
    engine, fake, source = _ready_engine(tmp_path, options=EngineOptions(aom_in_path=True))
    engine.set_line_intensity(3, 10.0)
    engine.source_step()
    assert source.power_mw == pytest.approx(200.0) and not any(c.startswith("LINE3:SET 2.") for c in fake.sent)
