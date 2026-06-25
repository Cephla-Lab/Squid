# acquisition_watchdog/config.py
"""Load Slack credentials from the same source the Squid GUI uses.

The GUI stores Slack settings (bot token, channel, enabled) in
`cache/slack_settings.yaml` (written by the Slack settings dialog and loaded at
GUI startup via control.widgets_slack.load_slack_settings_from_cache). This module
reads that same YAML so the watchdog alerts to the same workspace — without
importing the heavy control stack.
"""
import os
from pathlib import Path
from typing import NamedTuple, Optional

import yaml


class SlackConfig(NamedTuple):
    enabled: bool
    bot_token: Optional[str]
    channel_id: Optional[str]
    watchdog_enabled: bool


DEFAULT_SLACK_SETTINGS = "cache/slack_settings.yaml"


def resolve_slack_settings_path(cli_path: Optional[str]) -> Path:
    """Priority: --slack-settings > $SQUID_SLACK_SETTINGS > cache/slack_settings.yaml (cwd-relative)."""
    if cli_path:
        return Path(cli_path)
    env = os.environ.get("SQUID_SLACK_SETTINGS")
    if env:
        return Path(env)
    return Path(DEFAULT_SLACK_SETTINGS)


def load_slack_config(path: Optional[Path]) -> SlackConfig:
    p = Path(path) if path else Path(DEFAULT_SLACK_SETTINGS)
    if not p.exists():
        return SlackConfig(False, None, None, True)
    try:
        with open(p) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return SlackConfig(False, None, None, True)
    if not isinstance(data, dict):
        return SlackConfig(False, None, None, True)
    return SlackConfig(
        enabled=bool(data.get("enabled", False)),
        bot_token=(data.get("bot_token") or None),
        channel_id=(data.get("channel_id") or None),
        watchdog_enabled=bool(data.get("watchdog_enabled", True)),
    )
