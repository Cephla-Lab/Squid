# tests/acquisition_watchdog/test_config.py
from pathlib import Path

from acquisition_watchdog import config as wdconfig


def _write_ini(path, body):
    path.write_text(body)
    return path


def test_resolve_prefers_cli_then_env_then_run_record(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUID_CONFIG", raising=False)
    assert wdconfig.resolve_config_path("/cli.ini", {"config_path": "/run.ini"}) == Path("/cli.ini")
    monkeypatch.setenv("SQUID_CONFIG", "/env.ini")
    assert wdconfig.resolve_config_path(None, {"config_path": "/run.ini"}) == Path("/env.ini")
    monkeypatch.delenv("SQUID_CONFIG", raising=False)
    assert wdconfig.resolve_config_path(None, {"config_path": "/run.ini"}) == Path("/run.ini")


def test_load_slack_config_reads_section(tmp_path):
    ini = _write_ini(
        tmp_path / "c.ini",
        "[SLACKNOTIFICATIONS]\nenabled = True\nbot_token = xoxb-xyz\nchannel_id = C42\nwatchdog_enabled = True\n",
    )
    cfg = wdconfig.load_slack_config(ini)
    assert cfg.enabled is True
    assert cfg.bot_token == "xoxb-xyz"
    assert cfg.channel_id == "C42"
    assert cfg.watchdog_enabled is True


def test_load_slack_config_defaults_when_missing(tmp_path):
    ini = _write_ini(tmp_path / "c.ini", "[GENERAL]\nfoo = 1\n")
    cfg = wdconfig.load_slack_config(ini)
    assert cfg.bot_token is None and cfg.channel_id is None
    assert cfg.watchdog_enabled is True  # defaults to on when section absent

    assert wdconfig.load_slack_config(None).bot_token is None
