# acquisition_watchdog/config.py
"""Resolve the active Squid .ini and read its [SlackNotifications] section,
without importing the heavy control._def module.
"""
import configparser
import os
from pathlib import Path
from typing import NamedTuple, Optional


class SlackConfig(NamedTuple):
    enabled: bool
    bot_token: Optional[str]
    channel_id: Optional[str]
    watchdog_enabled: bool


def resolve_config_path(cli_config: Optional[str], run_record: Optional[dict]) -> Optional[Path]:
    """Priority: --config > $SQUID_CONFIG > run.json config_path > cache pointer."""
    if cli_config:
        return Path(cli_config)
    env = os.environ.get("SQUID_CONFIG")
    if env:
        return Path(env)
    if run_record and run_record.get("config_path"):
        return Path(run_record["config_path"])
    cache = Path("cache/config_file_path.txt")
    if cache.exists():
        first = cache.read_text().splitlines()
        if first:
            return Path(first[0].strip())
    return None


def load_slack_config(config_path: Optional[Path]) -> SlackConfig:
    if not config_path or not Path(config_path).exists():
        return SlackConfig(False, None, None, True)
    cp = configparser.ConfigParser()
    try:
        cp.read(config_path)
    except configparser.Error:
        return SlackConfig(False, None, None, True)
    if not cp.has_section("SLACKNOTIFICATIONS"):
        return SlackConfig(False, None, None, True)
    sec = cp["SLACKNOTIFICATIONS"]

    def getbool(key: str, default: bool) -> bool:
        try:
            return sec.getboolean(key, default)
        except ValueError:
            return default

    token = sec.get("bot_token", fallback=None) or None
    channel = sec.get("channel_id", fallback=None) or None
    return SlackConfig(
        enabled=getbool("enabled", False),
        bot_token=token,
        channel_id=channel,
        watchdog_enabled=getbool("watchdog_enabled", True),
    )
