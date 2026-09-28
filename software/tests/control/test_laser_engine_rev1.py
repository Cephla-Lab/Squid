from pathlib import Path

import pytest

from control.laser_engine_rev1 import EngineOptions, LaserEngineRev1, LaserEngineRev1Error
from control.laser_engine_rev1_link import EngineLink
from control.laser_engine_rev1_sim import FakeEngine
from control.laser_engine_rev1_status import LineState

NO_CALIBRATIONS = Path(__file__).parent / "no_such_calibration_dir"  # tests never read this machine's calibration CSVs


@pytest.fixture(autouse=True)
def _fast_resync(monkeypatch):
    monkeypatch.setattr(EngineLink, "RESYNC_WAIT_S", 0.0)


def _engine(fake=None, **kw):
    fake = fake or FakeEngine(tok_delay_polls=0)
    kw.setdefault("query_interval_s", 0.01)
    kw.setdefault("calibration_dir", NO_CALIBRATIONS)
    return LaserEngineRev1(link_factory=lambda: EngineLink(fake), **kw), fake


def test_open_runs_the_startup_sequence_without_arming():
    engine, fake = _engine()
    engine.open()
    assert engine.variant == "DF"
    assert "HOST:TIMEOUT 5" in fake.sent
    assert fake.sent.count("FAULT:RESET") == 1
    assert "SHUT:SRC TTL" in fake.sent
    assert all(f"LINE{n}:MOD INT" in fake.sent and f"LINE{n}:GATE 0" in fake.sent for n in range(1, 6))
    assert "ARM" not in fake.sent


def test_open_is_idempotent():
    engine, fake = _engine()
    engine.open()
    engine.open()
    assert fake.sent.count("*IDN?") == 1


def test_open_rejects_a_foreign_device():
    fake = FakeEngine()
    fake._reply = lambda cmd: "SomeOtherDevice,1.0"
    engine, _ = _engine(fake)
    with pytest.raises(RuntimeError, match="not a rev 1 laser engine"):
        engine.open()


def test_open_waits_for_the_expanders_at_cold_power_up():
    fake = FakeEngine()
    fake.i2c_fail_count = 3
    engine, _ = _engine(fake)
    engine.open()
    assert fake.sent.count("LINE1:MOD INT") >= 2  # retried after "ERR I2C write failed"


def test_poll_once_publishes_status():
    engine, fake = _engine()
    engine.open()
    seen = []
    engine.status_updated.connect(seen.append)
    status = engine.poll_once()
    assert status is engine.get_latest_status() and seen == [status]
    assert status.channels["L1"].state == LineState.NOT_ARMED


def test_connection_lost_on_serial_error():
    engine, fake = _engine()
    engine.open()
    lost = []
    engine.connection_lost.connect(lost.append)
    fake.unplug()
    assert engine.poll_once() is None
    assert engine.poll_once() is None
    assert engine.is_connection_lost() and len(lost) == 1


def test_close_disarms_and_is_safe_twice():
    engine, fake = _engine()
    engine.start()
    engine.close()
    engine.close()
    assert "DISARM" in fake.sent


def test_connect_reset_is_reported_with_the_engine_uptime():
    fake = FakeEngine()
    fake.faults, fake.last_event, fake.t_ms = ["OVERTEMP"], "OVERTEMP", 3 * 3600 * 1000
    engine, _ = _engine(fake)
    engine.open()
    assert fake.sent.count("FAULT:RESET") == 1
    assert engine.notices == [
        "cleared at connect: hardware fault latch; faults OVERTEMP; last event OVERTEMP, engine up 3.0 h"
    ]  # up 3 h: not the power-up latch - the operator can see that something happened before Squid connected


def test_connect_reset_refused_is_reported_and_never_retried():
    fake = FakeEngine()
    fake.reset_refusal = "OVERTEMP_N low - fix the cause first"
    engine, _ = _engine(fake)
    engine.open()
    for _ in range(3):
        engine.poll_once()
    assert fake.sent.count("FAULT:RESET") == 1
    assert engine.notices[0].startswith("NOT cleared at connect (OVERTEMP_N low")


def test_no_connect_reset_when_nothing_is_latched():
    fake = FakeEngine()
    fake.latch_ok = True
    engine, _ = _engine(fake)
    engine.open()
    assert "FAULT:RESET" not in fake.sent and engine.notices == []


def test_engine_options_are_validated():
    with pytest.raises(ValueError):
        EngineOptions(shutter_with_aom="sometimes")
    with pytest.raises(ValueError):
        EngineOptions(source_idle_off_min=-1)
    with pytest.raises(ValueError, match="AOM in the beam path"):
        EngineOptions(aom_attenuation=True)
    with pytest.raises(ValueError, match="AOM in the beam path"):
        EngineOptions(shutter_with_aom="open")
    EngineOptions(aom_in_path=True, shutter_with_aom="open", aom_attenuation=True)
