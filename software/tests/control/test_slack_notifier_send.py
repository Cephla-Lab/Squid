# tests/control/test_slack_notifier_send.py
from unittest.mock import patch
from control.slack_notifier import SlackNotifier


def test_post_message_delegates_to_squid_slack():
    n = SlackNotifier(bot_token="xoxb-abc", channel_id="C999")
    with patch("squid.slack.post_message", return_value=(True, "1.0")) as m:
        ok, ts = n._post_message("hello", blocks=[{"type": "section"}])
    assert ok is True and ts == "1.0"
    m.assert_called_once_with("xoxb-abc", "C999", "hello", [{"type": "section"}])
