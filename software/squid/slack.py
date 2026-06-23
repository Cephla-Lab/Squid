# squid/slack.py
"""Dependency-free Slack chat.postMessage sender.

Shared by the in-process SlackNotifier (control/slack_notifier.py) and the
standalone acquisition watchdog. Stdlib only — safe to import without the
control/Qt/hardware stack.
"""
import json
import urllib.error
import urllib.request
from typing import Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)

SLACK_API_BASE = "https://slack.com/api"


def post_message(
    bot_token: Optional[str],
    channel_id: Optional[str],
    text: str,
    blocks: Optional[list] = None,
    timeout: float = 15.0,
) -> Tuple[bool, Optional[str]]:
    """Post a message to Slack. Returns (ok, message_ts)."""
    if not bot_token or not channel_id:
        _log.debug("No Slack bot token or channel configured")
        return False, None

    payload = {"channel": channel_id, "text": text}
    if blocks:
        payload["blocks"] = blocks
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{SLACK_API_BASE}/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {bot_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            return True, result.get("ts")
        _log.warning(f"Slack API error: {result.get('error')}")
        return False, None
    except urllib.error.URLError as e:
        _log.warning(f"Failed to send Slack message: {e}")
        return False, None
    except Exception as e:
        _log.warning(f"Unexpected error sending Slack message: {e}")
        return False, None
