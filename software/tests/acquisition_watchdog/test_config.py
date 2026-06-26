# tests/acquisition_watchdog/test_config.py
from pathlib import Path

import yaml

from acquisition_watchdog import config as wdconfig


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data))
    return path


def test_resolve_prefers_cli_then_env_then_default(monkeypatch):
    monkeypatch.delenv("SQUID_SLACK_SETTINGS", raising=False)
    assert wdconfig.resolve_slack_settings_path("/cli.yaml") == Path("/cli.yaml")
    monkeypatch.setenv("SQUID_SLACK_SETTINGS", "/env.yaml")
    assert wdconfig.resolve_slack_settings_path(None) == Path("/env.yaml")
    monkeypatch.delenv("SQUID_SLACK_SETTINGS", raising=False)
    assert wdconfig.resolve_slack_settings_path(None) == Path(wdconfig.DEFAULT_SLACK_SETTINGS)


def test_load_reads_credentials_from_yaml(tmp_path):
    p = _write_yaml(
        tmp_path / "slack_settings.yaml",
        {"enabled": True, "bot_token": "xoxb-xyz", "channel_id": "C42"},
    )
    cfg = wdconfig.load_slack_config(p)
    assert cfg.bot_token == "xoxb-xyz"
    assert cfg.channel_id == "C42"
    assert cfg.watchdog_enabled is True  # default when key absent


def test_load_watchdog_enabled_false(tmp_path):
    p = _write_yaml(tmp_path / "s.yaml", {"bot_token": "x", "channel_id": "C", "watchdog_enabled": False})
    assert wdconfig.load_slack_config(p).watchdog_enabled is False


def test_load_defaults_when_missing(tmp_path):
    cfg = wdconfig.load_slack_config(tmp_path / "nope.yaml")
    assert cfg.bot_token is None and cfg.channel_id is None
    assert cfg.watchdog_enabled is True
    assert wdconfig.load_slack_config(None).bot_token is None


def test_load_empty_strings_become_none(tmp_path):
    p = _write_yaml(tmp_path / "s.yaml", {"bot_token": "", "channel_id": ""})
    cfg = wdconfig.load_slack_config(p)
    assert cfg.bot_token is None and cfg.channel_id is None
