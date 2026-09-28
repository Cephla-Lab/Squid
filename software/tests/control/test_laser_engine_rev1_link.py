import pytest

from control.laser_engine_rev1_link import EngineCommandError, EngineLink, EngineLinkError
from control.laser_engine_rev1_sim import FakeEngine


def test_query_returns_one_line():
    assert EngineLink(FakeEngine()).query("*IDN?").startswith("Cephla,LaserEngineCarrier-rev1,")


def test_command_ok_value_and_bare_ok():
    fake = FakeEngine()
    link = EngineLink(fake)
    assert link.command("HOST:TIMEOUT 5") == ""
    assert link.command("LINE1:SET 0.2") == "0.2000"
    assert fake.sent[-1] == "LINE1:SET 0.2"


def test_command_err_raises_with_reason():
    with pytest.raises(EngineCommandError) as ei:
        EngineLink(FakeEngine()).command("LINE1:EN 1")  # not armed
    assert ei.value.reason == "not armed"
    assert ei.value.command == "LINE1:EN 1"


def test_status_is_parsed_json():
    stat = EngineLink(FakeEngine()).status()
    assert stat["var"] == "DF" and len(stat["lines"]) == 5 and stat["lines"][2]["kind"] == "VOLT"


def test_empty_reply_is_a_link_error():
    fake = FakeEngine()
    fake.unplug()
    with pytest.raises(EngineLinkError):
        EngineLink(fake).query("STAT?")


def test_resync_drops_a_stale_reply():
    fake = FakeEngine()
    fake.queue_stale_reply("OK leftover from a crashed session")
    link = EngineLink(fake)
    link.resync()
    assert link.query("VAR?") == "DF"


def test_open_matches_a_numeric_serial_number(monkeypatch):
    import control.laser_engine_rev1_link as link_mod

    port = type("Port", (), {"device": "/dev/ttyACM7", "serial_number": "12345670"})()
    monkeypatch.setattr(link_mod.list_ports, "comports", lambda: [port])
    monkeypatch.setattr(link_mod.serial, "Serial", lambda path, **kw: FakeEngine())
    assert isinstance(
        EngineLink.open(sn=12345670), EngineLink
    )  # the .ini reader turns an all-digit serial number into an int


def test_out_of_step_reply_resyncs_and_retries_once():
    fake = FakeEngine()
    fake.queue_stale_reply('{"stale": 1}')  # a late STAT? reply still in the stream
    link = EngineLink(fake)
    assert link.command("LINE1:SET 0.2") == "0.2000"
    assert fake.sent.count("LINE1:SET 0.2") == 2  # every engine command is idempotent, so the repeat is harmless
