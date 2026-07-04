# tests/squid/test_slack.py
import json
from unittest.mock import patch, MagicMock

import squid.slack as slack


def test_post_message_returns_false_without_credentials():
    assert slack.post_message(None, "C123", "hi") == (False, None)
    assert slack.post_message("xoxb-1", None, "hi") == (False, None)


def test_post_message_builds_authorized_request_and_parses_ok():
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "ts": "111.222"}).encode()

    def fake_urlopen(request, timeout=15):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data.decode())
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok, ts = slack.post_message("xoxb-token", "C123", "hello", blocks=[{"type": "section"}])

    assert ok is True and ts == "111.222"
    assert captured["url"].endswith("/chat.postMessage")
    assert captured["headers"]["Authorization"] == "Bearer xoxb-token"
    assert captured["body"]["channel"] == "C123"
    assert captured["body"]["text"] == "hello"
    assert captured["body"]["blocks"] == [{"type": "section"}]


def test_post_message_handles_api_error():
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": False, "error": "channel_not_found"}).encode()

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        ok, ts = slack.post_message("xoxb", "C1", "x")
    assert ok is False and ts is None
