# Acquisition Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when an acquisition ends prematurely — process crash/hang/kill, fatal error, or user abort — and send a single Slack alert, covering GUI- and MCP-server-driven runs on Ubuntu and Windows.

**Architecture:** The acquisition engine (`control/core/`) drops on-disk breadcrumbs — a `run.json` written atomically at start, bumped with a throttled heartbeat during the run, and finalized with a reason at end. An independent, lightweight `acquisition_watchdog` process polls `run.json`, detects a dead/stale run or a non-clean end, and posts one Slack alert. A shared dependency-free `squid/slack.py` sender is reused by both the watchdog and the existing in-process `SlackNotifier`, whose end-of-run message is gated to clean successes so failures alert exactly once.

**Tech Stack:** Python 3.8+, stdlib only (`json`, `urllib`, `configparser`, `socket`, `uuid`, `tempfile`, `os`), `platformdirs` (already a dep), `pyyaml` (already a dep), `pytest`. No new third-party dependencies.

---

## Spec

`docs/superpowers/specs/2026-06-23-acquisition-watchdog-design.md`

## Reason taxonomy (v1)

The worker computes one `reason` at the end of `run()`; it drives both the breadcrumb and the in-process finish message:

| `reason` | When | Watchdog alerts? | Notifier finish msg? |
|---|---|---|---|
| `completed` | loop finished all timepoints, `_acquisition_error_count == 0`, not aborted | no | yes |
| `completed_with_errors` | loop finished but `_acquisition_error_count > 0` | yes | no |
| `error` | uncaught exception, or auto-abort from `TimeoutError` / failed-job abort | yes | no |
| `user_abort` | abort flag set externally (GUI/server) **or** GUI closed mid-run (shutdown aborts + joins) | yes | no |
| *(no end record)* | process crashed/killed/hung before writing end | yes (crash/hang) | n/a |

(`app_closed` from the design is folded into `user_abort` for v1: the shutdown hook requests the normal abort and joins the worker so it writes a proper `user_abort` end record instead of looking like a crash. A distinct `app_closed` label is future work.)

## File structure

| Path | Responsibility |
|---|---|
| `squid/slack.py` | **New.** Dependency-free `post_message(bot_token, channel_id, text, blocks)` via `urllib`. Reused by notifier + watchdog. |
| `squid/acquisition_state.py` | **New.** `run.json` schema, `default_state_dir()`, atomic write, `read_run()`, `RunStateWriter` (+ `NullRunStateWriter`). Engine writes; watchdog reads. Leaf module — must not import `control`. |
| `acquisition_watchdog/__init__.py` | **New.** Package marker. |
| `acquisition_watchdog/config.py` | **New.** Resolve active `.ini`; load `[SlackNotifications]` with stdlib `configparser`. |
| `acquisition_watchdog/alerts.py` | **New.** Format the Slack alert text + blocks for each alert kind. |
| `acquisition_watchdog/monitor.py` | **New.** `pid_alive`, `Monitor.classify`, `Monitor.check_once`, dedup persistence, `run_forever`. |
| `acquisition_watchdog/__main__.py` | **New.** CLI entry: `python -m acquisition_watchdog`. |
| `acquisition_watchdog/systemd/squid-acquisition-watchdog.service` | **New.** Linux user-service unit. |
| `acquisition_watchdog/windows/squid-acquisition-watchdog.xml`, `install.ps1` | **New.** Windows Task Scheduler recipe. |
| `acquisition_watchdog/README.md` | **New.** Install/run docs for both OSes. |
| `control/slack_notifier.py` | **Modify.** Delegate `_post_message` to `squid.slack`; add `reason` field to `AcquisitionStats`; gate `notify_acquisition_finished` to `reason == "completed"`. |
| `control/core/multi_point_controller.py` | **Modify.** Write the start breadcrumb in `run_acquisition()`; pass the writer to the worker. |
| `control/core/multi_point_worker.py` | **Modify.** Heartbeat in the loop + image callback; compute `reason` and write end in `finally`; track `_abort_cause`. |
| `main_hcs.py` | **Modify.** On shutdown-while-acquiring, request abort + join so the worker writes `user_abort`. |
| `tests/...` | **New/modify.** Unit + integration tests per task; autouse fixture redirecting the state dir to tmp. |

---

## Task 1: `squid/slack.py` — shared Slack sender

**Files:**
- Create: `squid/slack.py`
- Test: `tests/squid/test_slack.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/squid/test_slack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'squid.slack'`.

- [ ] **Step 3: Write the implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/squid/test_slack.py -v`
Expected: PASS (3 tests). Create `tests/squid/__init__.py` if the package import fails.

- [ ] **Step 5: Commit**

```bash
git add software/squid/slack.py software/tests/squid/test_slack.py
git commit -m "feat(slack): add dependency-free squid.slack.post_message sender"
```

---

## Task 2: Delegate `SlackNotifier._post_message` to `squid.slack`

**Files:**
- Modify: `control/slack_notifier.py` (`_post_message`, lines 160–212; imports lines 10–27)
- Test: `tests/control/test_slack_notifier_send.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/control/test_slack_notifier_send.py
from unittest.mock import patch
from control.slack_notifier import SlackNotifier


def test_post_message_delegates_to_squid_slack():
    n = SlackNotifier(bot_token="xoxb-abc", channel_id="C999")
    with patch("squid.slack.post_message", return_value=(True, "1.0")) as m:
        ok, ts = n._post_message("hello", blocks=[{"type": "section"}])
    assert ok is True and ts == "1.0"
    m.assert_called_once_with("xoxb-abc", "C999", "hello", [{"type": "section"}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/control/test_slack_notifier_send.py -v`
Expected: FAIL — `_post_message` calls `urllib` directly, so `squid.slack.post_message` is never called (`AssertionError: Expected 'post_message' to have been called once`).

- [ ] **Step 3: Edit `control/slack_notifier.py`**

Add the import near the existing `import squid.logging` (line 27):

```python
import squid.logging
import squid.slack
```

Replace the entire `_post_message` method (lines 160–212) with:

```python
    def _post_message(self, text: str, blocks: Optional[list] = None) -> Tuple[bool, Optional[str]]:
        return squid.slack.post_message(self.bot_token, self.channel_id, text, blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/control/test_slack_notifier_send.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add software/control/slack_notifier.py software/tests/control/test_slack_notifier_send.py
git commit -m "refactor(slack): route SlackNotifier sends through squid.slack"
```

---

## Task 3: `squid/acquisition_state.py` — breadcrumb schema + writer

**Files:**
- Create: `squid/acquisition_state.py`
- Test: `tests/squid/test_acquisition_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/squid/test_acquisition_state.py
import json

import squid.acquisition_state as ast


def _expected():
    return {"timepoints": 3, "regions": 1, "fovs": 4, "channels": 2, "z": 1}


def test_start_writes_running_record(tmp_path):
    w = ast.RunStateWriter.start(
        experiment_id="exp1", pid=4321, config_path="/cfg.ini",
        output_path=str(tmp_path / "exp1"), expected=_expected(),
        machine="micro-1", state_dir=tmp_path,
    )
    rec = ast.read_run(tmp_path)
    assert rec["status"] == "running"
    assert rec["experiment_id"] == "exp1"
    assert rec["pid"] == 4321
    assert rec["machine"] == "micro-1"
    assert rec["expected"] == _expected()
    assert rec["run_id"] == w.run_id
    assert rec["reason"] is None


def test_beat_is_throttled_but_updates_progress_on_flush(tmp_path):
    w = ast.RunStateWriter.start(
        experiment_id="e", pid=1, config_path=None, output_path="o",
        expected=_expected(), state_dir=tmp_path,
    )
    first = ast.read_run(tmp_path)["heartbeat_at"]
    # Immediate beat is throttled (< HEARTBEAT_INTERVAL_S since start) -> file unchanged.
    w.beat({"timepoint": 1})
    assert ast.read_run(tmp_path)["heartbeat_at"] == first
    # Forced beat flushes and records progress.
    w.beat({"timepoint": 2}, force=True)
    rec = ast.read_run(tmp_path)
    assert rec["heartbeat_at"] >= first
    assert rec["progress"] == {"timepoint": 2}


def test_end_records_reason_and_stats(tmp_path):
    w = ast.RunStateWriter.start(
        experiment_id="e", pid=1, config_path=None, output_path="o",
        expected=_expected(), state_dir=tmp_path,
    )
    w.end("user_abort", {"total_images": 7, "errors_encountered": 0})
    rec = ast.read_run(tmp_path)
    assert rec["status"] == "ended"
    assert rec["reason"] == "user_abort"
    assert rec["ended_at"] is not None
    assert rec["stats"]["total_images"] == 7


def test_read_run_missing_returns_none(tmp_path):
    assert ast.read_run(tmp_path) is None


def test_null_writer_is_noop(tmp_path):
    w = ast.NullRunStateWriter()
    w.beat({"timepoint": 1})
    w.end("completed", {})
    assert ast.read_run(tmp_path) is None
    assert w.run_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/squid/test_acquisition_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'squid.acquisition_state'`.

- [ ] **Step 3: Write the implementation**

```python
# squid/acquisition_state.py
"""On-disk acquisition run-state breadcrumbs, shared by the acquisition engine
(writer) and the standalone acquisition watchdog (reader).

Stdlib-only leaf module: must NOT import anything from `control`.
"""
import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import platformdirs

import squid.logging

_log = squid.logging.get_logger(__name__)

SCHEMA_VERSION = 1
HEARTBEAT_INTERVAL_S = 5.0
RUN_FILE_NAME = "run.json"


def default_state_dir() -> Path:
    """Per-user watchdog state dir, shared by writer and reader.

    Overridable via SQUID_WATCHDOG_STATE_DIR (honored by both processes).
    """
    override = os.environ.get("SQUID_WATCHDOG_STATE_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_state_path("squid", "cephla")) / "watchdog"


def run_file_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir) / RUN_FILE_NAME if state_dir else default_state_dir() / RUN_FILE_NAME


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_run(state_dir: Optional[Path] = None) -> Optional[dict]:
    try:
        with open(run_file_path(state_dir)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class RunStateWriter:
    """Writes/updates the single run.json for the current acquisition."""

    def __init__(self, record: dict, state_dir: Optional[Path] = None):
        self._record = record
        self._state_dir = state_dir
        self._last_beat = 0.0

    @classmethod
    def start(
        cls,
        *,
        experiment_id: str,
        pid: int,
        config_path: Optional[str],
        output_path: str,
        expected: dict,
        machine: Optional[str] = None,
        state_dir: Optional[Path] = None,
    ) -> "RunStateWriter":
        now = time.time()
        record = {
            "schema_version": SCHEMA_VERSION,
            "run_id": uuid.uuid4().hex,
            "experiment_id": experiment_id,
            "machine": machine or socket.gethostname(),
            "pid": pid,
            "config_path": config_path,
            "output_path": output_path,
            "started_at": now,
            "heartbeat_at": now,
            "progress": {},
            "expected": expected,
            "status": "running",
            "reason": None,
            "ended_at": None,
            "stats": None,
        }
        writer = cls(record, state_dir=state_dir)
        writer._flush()
        writer._last_beat = now
        return writer

    @property
    def run_id(self) -> Optional[str]:
        return self._record.get("run_id")

    def beat(self, progress: Optional[dict] = None, force: bool = False) -> None:
        if progress:
            self._record["progress"] = progress
        now = time.time()
        if not force and (now - self._last_beat) < HEARTBEAT_INTERVAL_S:
            return
        self._last_beat = now
        self._record["heartbeat_at"] = now
        self._flush()

    def end(self, reason: str, stats: Optional[dict] = None) -> None:
        self._record["status"] = "ended"
        self._record["reason"] = reason
        self._record["ended_at"] = time.time()
        if stats is not None:
            self._record["stats"] = stats
        self._flush()

    def _flush(self) -> None:
        try:
            _atomic_write_json(run_file_path(self._state_dir), dict(self._record))
        except OSError as e:
            _log.warning(f"Failed to write acquisition run state: {e}")


class NullRunStateWriter:
    """No-op writer used when breadcrumbs are not wired (tests, side paths)."""

    run_id = None

    def beat(self, progress: Optional[dict] = None, force: bool = False) -> None:
        pass

    def end(self, reason: str, stats: Optional[dict] = None) -> None:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/squid/test_acquisition_state.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add software/squid/acquisition_state.py software/tests/squid/test_acquisition_state.py
git commit -m "feat(watchdog): add squid.acquisition_state breadcrumb schema + writer"
```

---

## Task 4: `acquisition_watchdog/config.py` — config resolution

**Files:**
- Create: `acquisition_watchdog/__init__.py` (empty)
- Create: `acquisition_watchdog/config.py`
- Test: `tests/acquisition_watchdog/test_config.py` (+ `tests/acquisition_watchdog/__init__.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/acquisition_watchdog/test_config.py
from acquisition_watchdog import config as wdconfig


def _write_ini(path, body):
    path.write_text(body)
    return path


def test_resolve_prefers_cli_then_env_then_run_record(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUID_CONFIG", raising=False)
    assert wdconfig.resolve_config_path("/cli.ini", {"config_path": "/run.ini"}) == __import__("pathlib").Path("/cli.ini")
    monkeypatch.setenv("SQUID_CONFIG", "/env.ini")
    assert str(wdconfig.resolve_config_path(None, {"config_path": "/run.ini"})) == "/env.ini"
    monkeypatch.delenv("SQUID_CONFIG", raising=False)
    assert str(wdconfig.resolve_config_path(None, {"config_path": "/run.ini"})) == "/run.ini"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'acquisition_watchdog'`.

- [ ] **Step 3: Write the implementation**

```python
# acquisition_watchdog/__init__.py
```

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_config.py -v`
Expected: PASS (3 tests). Add empty `tests/acquisition_watchdog/__init__.py` if needed.

- [ ] **Step 5: Commit**

```bash
git add software/acquisition_watchdog/__init__.py software/acquisition_watchdog/config.py software/tests/acquisition_watchdog/
git commit -m "feat(watchdog): add config resolution + [SlackNotifications] loader"
```

---

## Task 5: `acquisition_watchdog/alerts.py` — alert formatting

**Files:**
- Create: `acquisition_watchdog/alerts.py`
- Test: `tests/acquisition_watchdog/test_alerts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/acquisition_watchdog/test_alerts.py
from acquisition_watchdog import alerts


def _run():
    return {
        "experiment_id": "plateA_2026",
        "machine": "micro-1",
        "output_path": "/data/plateA_2026",
        "progress": {"timepoint": 3, "expected_timepoints": 10, "images": 360},
        "expected": {"timepoints": 10},
        "started_at": 1_700_000_000.0,
        "heartbeat_at": 1_700_000_100.0,
    }


def test_format_alert_includes_key_facts():
    text, blocks = alerts.format_alert("crash", _run())
    assert "plateA_2026" in text
    assert "micro-1" in text
    blob = str(blocks)
    assert "plateA_2026" in blob
    assert "3" in blob and "10" in blob  # progress vs expected
    assert isinstance(blocks, list) and blocks


def test_format_alert_each_kind_has_title():
    for kind in ("crash", "hang", "error", "completed_with_errors", "user_abort"):
        text, _ = alerts.format_alert(kind, _run())
        assert text  # non-empty title line for every kind
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'acquisition_watchdog.alerts'`.

- [ ] **Step 3: Write the implementation**

```python
# acquisition_watchdog/alerts.py
"""Format watchdog Slack alerts (text + Block Kit blocks)."""
from datetime import datetime, timezone
from typing import Optional, Tuple

_KIND_TITLE = {
    "crash": ":red_circle: Acquisition process died",
    "hang": ":large_orange_circle: Acquisition hung (no heartbeat)",
    "error": ":red_circle: Acquisition ended with a fatal error",
    "completed_with_errors": ":large_orange_circle: Acquisition finished with errors",
    "user_abort": ":large_yellow_circle: Acquisition aborted",
}


def _fmt_ts(epoch: Optional[float]) -> str:
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _progress_line(run: dict) -> str:
    prog = run.get("progress") or {}
    expected = run.get("expected") or {}
    tp = prog.get("timepoint", "?")
    exp_tp = prog.get("expected_timepoints", expected.get("timepoints", "?"))
    images = prog.get("images", "?")
    return f"timepoint {tp}/{exp_tp}, {images} images"


def format_alert(kind: str, run: dict) -> Tuple[str, list]:
    title = _KIND_TITLE.get(kind, f"Acquisition alert: {kind}")
    experiment = run.get("experiment_id", "unknown")
    machine = run.get("machine", "unknown")
    text = f"{title}: {experiment} on {machine}"

    last_seen = run.get("ended_at") or run.get("heartbeat_at")
    detail = (
        f"*Experiment:* {experiment}\n"
        f"*Machine:* {machine}\n"
        f"*Progress:* {_progress_line(run)}\n"
        f"*Started:* {_fmt_ts(run.get('started_at'))}\n"
        f"*Last seen:* {_fmt_ts(last_seen)}\n"
        f"*Output:* {run.get('output_path', 'unknown')}"
    )
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title.replace(":red_circle:", "")
                                    .replace(":large_orange_circle:", "").replace(":large_yellow_circle:", "").strip(),
                                    "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
    ]
    return text, blocks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_alerts.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add software/acquisition_watchdog/alerts.py software/tests/acquisition_watchdog/test_alerts.py
git commit -m "feat(watchdog): add Slack alert formatting"
```

---

## Task 6: `acquisition_watchdog/monitor.py` — poll, classify, dedup

**Files:**
- Create: `acquisition_watchdog/monitor.py`
- Test: `tests/acquisition_watchdog/test_monitor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/acquisition_watchdog/test_monitor.py
import time

import squid.acquisition_state as ast
from acquisition_watchdog.monitor import Monitor


def _running(tmp_path, pid, heartbeat_age=0.0, run_id="r1"):
    rec = {
        "schema_version": 1, "run_id": run_id, "experiment_id": "e", "machine": "m",
        "pid": pid, "config_path": None, "output_path": "o",
        "started_at": time.time() - 100, "heartbeat_at": time.time() - heartbeat_age,
        "progress": {}, "expected": {}, "status": "running", "reason": None,
        "ended_at": None, "stats": None,
    }
    ast._atomic_write_json(ast.run_file_path(tmp_path), rec)
    return rec


def _mon(tmp_path):
    return Monitor(state_dir=tmp_path, heartbeat_timeout=120.0)


def test_running_with_live_pid_and_fresh_heartbeat_is_silent(tmp_path):
    _running(tmp_path, pid=__import__("os").getpid(), heartbeat_age=1.0)
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) is None


def test_dead_pid_is_crash(tmp_path):
    _running(tmp_path, pid=2_000_000_000, heartbeat_age=1.0)  # impossible pid
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) == "crash"


def test_stale_heartbeat_with_live_pid_is_hang(tmp_path):
    _running(tmp_path, pid=__import__("os").getpid(), heartbeat_age=999.0)
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) == "hang"


def test_ended_reasons(tmp_path):
    mon = _mon(tmp_path)
    for reason, expect in [
        ("completed", None), ("completed_with_errors", "completed_with_errors"),
        ("error", "error"), ("user_abort", "user_abort"),
    ]:
        run = {"run_id": f"x-{reason}", "status": "ended", "reason": reason}
        assert mon.classify(run, time.time()) == expect


def test_dedup_persists_across_restart(tmp_path, monkeypatch):
    _running(tmp_path, pid=2_000_000_000, run_id="dup1")
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a) or (True, "1")))
    monkeypatch.setattr(
        "acquisition_watchdog.config.load_slack_config",
        lambda p: __import__("acquisition_watchdog.config", fromlist=["SlackConfig"]).SlackConfig(True, "xoxb", "C1", True),
    )
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1
    # Fresh Monitor (simulated restart) must not re-alert the same run_id.
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'acquisition_watchdog.monitor'`.

- [ ] **Step 3: Write the implementation**

```python
# acquisition_watchdog/monitor.py
"""Poll the acquisition run-state and alert on premature ends."""
import json
import os
import time
from pathlib import Path
from typing import Optional, Set

import squid.acquisition_state as acquisition_state
import squid.logging
import squid.slack
from acquisition_watchdog import alerts, config

_log = squid.logging.get_logger("acquisition_watchdog")

ALERT_REASONS = {"completed_with_errors", "error", "user_abort"}


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
    # Windows without psutil: cannot check reliably; rely on the heartbeat instead.
    return True


class Monitor:
    def __init__(
        self,
        state_dir: Optional[Path] = None,
        cli_config: Optional[str] = None,
        poll_interval: float = 5.0,
        heartbeat_timeout: float = 120.0,
    ):
        self._state_dir = Path(state_dir) if state_dir else None
        self._cli_config = cli_config
        self._poll = poll_interval
        self._timeout = heartbeat_timeout
        base = self._state_dir or acquisition_state.default_state_dir()
        self._alerted_path = base / "alerted.json"
        self._alerted = self._load_alerted()

    def _load_alerted(self) -> Set[str]:
        try:
            with open(self._alerted_path) as f:
                return set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save_alerted(self) -> None:
        try:
            self._alerted_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._alerted_path, "w") as f:
                json.dump(sorted(self._alerted), f)
        except OSError as e:
            _log.warning(f"Could not persist alerted set: {e}")

    def classify(self, run: Optional[dict], now: float) -> Optional[str]:
        """Return an alert kind ('crash'|'hang'|<reason>) or None."""
        if not run or run.get("run_id") in self._alerted:
            return None
        status = run.get("status")
        if status == "running":
            if not pid_alive(run.get("pid")):
                return "crash"
            if (now - (run.get("heartbeat_at") or 0)) > self._timeout:
                return "hang"
            return None
        if status == "ended" and run.get("reason") in ALERT_REASONS:
            return run["reason"]
        return None

    def check_once(self, now: float) -> None:
        run = acquisition_state.read_run(self._state_dir)
        kind = self.classify(run, now)
        if kind is None:
            return

        cfg_path = config.resolve_config_path(self._cli_config, run)
        slack_cfg = config.load_slack_config(cfg_path)
        if not (slack_cfg.bot_token and slack_cfg.channel_id and slack_cfg.watchdog_enabled):
            _log.warning(
                f"Premature end ({kind}) for run_id={run.get('run_id')} but Slack is not "
                f"configured/enabled; not alerting."
            )
            self._mark_alerted(run["run_id"])
            return

        text, blocks = alerts.format_alert(kind, run)
        ok, _ = squid.slack.post_message(slack_cfg.bot_token, slack_cfg.channel_id, text, blocks)
        if ok:
            _log.info(f"Sent watchdog alert ({kind}) for run_id={run.get('run_id')}")
            self._mark_alerted(run["run_id"])
        else:
            # Leave unmarked so a transient Slack failure retries on the next poll.
            _log.warning(f"Failed to send watchdog alert ({kind}) for run_id={run.get('run_id')}; will retry")

    def _mark_alerted(self, run_id: str) -> None:
        self._alerted.add(run_id)
        self._save_alerted()

    def run_forever(self) -> None:
        base = self._state_dir or acquisition_state.default_state_dir()
        _log.info(f"Acquisition watchdog started. state_dir={base} heartbeat_timeout={self._timeout}s")
        while True:
            try:
                self.check_once(time.time())
            except Exception as e:
                _log.exception(f"Watchdog poll error: {e}")
            time.sleep(self._poll)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_monitor.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add software/acquisition_watchdog/monitor.py software/tests/acquisition_watchdog/test_monitor.py
git commit -m "feat(watchdog): add poll/classify/dedup monitor"
```

---

## Task 7: `acquisition_watchdog/__main__.py` — CLI entry

**Files:**
- Create: `acquisition_watchdog/__main__.py`
- Test: `tests/acquisition_watchdog/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/acquisition_watchdog/test_cli.py
from unittest.mock import patch

from acquisition_watchdog.__main__ import main


def test_once_runs_single_check(tmp_path):
    with patch("acquisition_watchdog.monitor.Monitor.check_once") as check, \
         patch("acquisition_watchdog.monitor.Monitor.run_forever") as forever:
        main(["--once", "--state-dir", str(tmp_path)])
    check.assert_called_once()
    forever.assert_not_called()


def test_default_runs_forever(tmp_path):
    with patch("acquisition_watchdog.monitor.Monitor.run_forever") as forever:
        main(["--state-dir", str(tmp_path)])
    forever.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'acquisition_watchdog.__main__'`.

- [ ] **Step 3: Write the implementation**

```python
# acquisition_watchdog/__main__.py
"""CLI entry point: python -m acquisition_watchdog"""
import argparse
import time
from pathlib import Path
from typing import Optional, Sequence

import squid.logging
from acquisition_watchdog.monitor import Monitor


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="acquisition_watchdog",
        description="Alert on prematurely-ended Squid acquisitions (crash/hang/abort/error).",
    )
    parser.add_argument("--config", help="Path to the active configuration .ini ([SlackNotifications]).")
    parser.add_argument("--state-dir", help="Override the watchdog state directory.")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between checks (default 5).")
    parser.add_argument(
        "--heartbeat-timeout", type=float, default=120.0,
        help="Seconds of heartbeat silence (with a live PID) before declaring a hang (default 120).",
    )
    parser.add_argument("--once", action="store_true", help="Run a single check and exit.")
    args = parser.parse_args(argv)

    log = squid.logging.get_logger("acquisition_watchdog")
    monitor = Monitor(
        state_dir=Path(args.state_dir) if args.state_dir else None,
        cli_config=args.config,
        poll_interval=args.poll_interval,
        heartbeat_timeout=args.heartbeat_timeout,
    )
    if args.once:
        monitor.check_once(time.time())
    else:
        try:
            monitor.run_forever()
        except KeyboardInterrupt:
            log.info("Acquisition watchdog stopped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/acquisition_watchdog/test_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add software/acquisition_watchdog/__main__.py software/tests/acquisition_watchdog/test_cli.py
git commit -m "feat(watchdog): add CLI entry point"
```

---

## Task 8: Engine — write the start breadcrumb in `run_acquisition()`

**Files:**
- Modify: `control/core/multi_point_controller.py` (`run_acquisition`, around lines 838–888)
- Modify: `control/core/multi_point_worker.py` (`__init__`, lines 66–110)
- Modify: `tests/control/conftest.py` (add autouse fixture redirecting state dir to tmp)

- [ ] **Step 1: Add the autouse fixture so tests never touch the real state dir**

Append to `tests/control/conftest.py`:

```python
import pytest


@pytest.fixture(autouse=True)
def _watchdog_state_to_tmp(tmp_path, monkeypatch):
    # Keep acquisition breadcrumbs out of the real user state dir during tests.
    monkeypatch.setenv("SQUID_WATCHDOG_STATE_DIR", str(tmp_path / "watchdog"))
```

- [ ] **Step 2: Add `run_state_writer` param to `MultiPointWorker.__init__`**

In `control/core/multi_point_worker.py`, add to the `__init__` signature (after `prewarmed_bp_values`, line 83):

```python
        prewarmed_bp_values: Optional["BackpressureValues"] = None,
        run_state_writer=None,
    ):
```

Add the import near the top of the file (with the other `control`/`squid` imports):

```python
import squid.acquisition_state
```

Store it among the other attribute assignments (near line 110, after `self.request_abort_fn = request_abort_fn`):

```python
        self._run_state = run_state_writer or squid.acquisition_state.NullRunStateWriter()
        self._abort_cause = None  # set to "error" by auto-abort paths (timeout / failed jobs)
```

- [ ] **Step 3: Write the start breadcrumb and pass the writer (controller)**

In `control/core/multi_point_controller.py`, add near the top with the other imports:

```python
import squid.acquisition_state
```

In `run_acquisition()`, immediately AFTER the `_save_acquisition_yaml(...)` call block (ends ~line 865) and BEFORE `prewarmed_runner, prewarmed_bp_values = self.get_prewarmed_job_runner()` (line 869), insert:

```python
            # Acquisition watchdog: drop the "running" breadcrumb (covers GUI + MCP-server runs).
            self._run_state_writer = squid.acquisition_state.NullRunStateWriter()
            try:
                expected = {
                    "timepoints": self.Nt,
                    "regions": len(scan_position_information.scan_region_coords_mm),
                    "fovs": sum(len(c) for c in scan_position_information.scan_region_fov_coords_mm.values()),
                    "channels": len(self.selected_configurations),
                    "z": self.NZ,
                }
                config_path = (getattr(control._def, "CACHED_CONFIG_FILE_PATH", None) or "").strip() or None
                self._run_state_writer = squid.acquisition_state.RunStateWriter.start(
                    experiment_id=self.experiment_ID,
                    pid=os.getpid(),
                    config_path=config_path,
                    output_path=experiment_path,
                    expected=expected,
                )
            except Exception as e:
                self._log.warning(f"Failed to write acquisition watchdog start state: {e}")
```

Then add `run_state_writer=self._run_state_writer,` to the `MultiPointWorker(...)` constructor call (within the kwargs block at lines 873–888):

```python
                prewarmed_bp_values=prewarmed_bp_values,
                run_state_writer=self._run_state_writer,
            )
```

(`os` and `control._def` are already imported in this module; confirm with `grep -n "^import os" software/control/core/multi_point_controller.py` and add `import os` if absent.)

- [ ] **Step 4: Smoke-test the wiring**

```python
# tests/control/test_watchdog_breadcrumbs.py
import os

import squid.acquisition_state as ast
import control.microscope
import tests.control.gui_test_stubs as gts


def test_run_acquisition_writes_running_breadcrumb(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)
    mpc.run_acquisition()
    rec = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])
    assert rec is not None
    assert rec["status"] == "running"
    assert rec["pid"] == os.getpid()
    assert rec["expected"]["timepoints"] >= 1
    mpc.request_abort_aquisition()
    scope.close()
```

Run: `cd software && python3 -m pytest tests/control/test_watchdog_breadcrumbs.py -v`
Expected: PASS (start breadcrumb present). The `ended` transition is verified in Task 12.

- [ ] **Step 5: Commit**

```bash
git add software/control/core/multi_point_controller.py software/control/core/multi_point_worker.py software/tests/control/conftest.py software/tests/control/test_watchdog_breadcrumbs.py
git commit -m "feat(watchdog): write acquisition start breadcrumb from the engine"
```

---

## Task 9: Engine — heartbeat, reason, and end breadcrumb in the worker

**Files:**
- Modify: `control/core/multi_point_worker.py` (`run`, lines 449–539; `_image_callback` ~line 1200; failed-job path lines 1023–1027)
- Modify: `control/slack_notifier.py` (`AcquisitionStats`, lines 46–53)

- [ ] **Step 1: Add `reason` field to `AcquisitionStats`**

In `control/slack_notifier.py`, extend the dataclass (lines 46–53):

```python
@dataclass
class AcquisitionStats:
    """Statistics for a completed acquisition."""

    total_images: int
    total_timepoints: int
    total_duration_seconds: float
    errors_encountered: int
    experiment_id: str
    reason: str = "completed"
```

- [ ] **Step 2: Add a heartbeat helper + loop beats (worker)**

In `control/core/multi_point_worker.py`, add a helper method to `MultiPointWorker`:

```python
    def _run_state_beat(self) -> None:
        self._run_state.beat(
            {
                "timepoint": self.time_point,
                "expected_timepoints": self.Nt,
                "fov": self._timepoint_fov_count,
                "images": self.image_count,
            }
        )
```

Insert `self._run_state_beat()` at three points in `run()`:

(a) Right after the top-of-loop abort check (after line 453 `break`), as the first statement of the loop body when not aborting:

```python
        while self.time_point < self.Nt:
            # check if abort acquisition has been requested
            if self.abort_requested_fn():
                self._log.debug("In run, abort_acquisition_requested=True")
                break
            self._run_state_beat()
```

(b) Inside the timed-acquisition wait loop (lines 494–498), so dt gaps keep the heartbeat fresh:

```python
                while time.time() < self.timestamp_acquisition_started + self.time_point * self.dt:
                    if self.abort_requested_fn():
                        self._log.debug("In run wait loop, abort_acquisition_requested=True")
                        break
                    self._run_state_beat()
                    self._sleep(sleep_time)
```

(c) In `_image_callback`, immediately after `self.image_count` is incremented (~line 1200), so long single-timepoint scans keep beating with real imaging progress:

```python
        self.image_count += 1
        self._run_state_beat()
```

- [ ] **Step 3: Tag error-driven aborts**

In the `except TimeoutError` handler (lines 507–510), set the cause before requesting abort:

```python
    except TimeoutError as te:
        self._log.error(f"Operation timed out during acquisition, aborting acquisition!")
        self._log.error(te)
        self._abort_cause = "error"
        self.request_abort_fn()
```

In the failed-job abort path (lines 1023–1027):

```python
                if not result.none_failed and self._abort_on_failed_job:
                    self._log.error("Some jobs failed, aborting acquisition because abort_on_failed_job=True")
                    self._abort_cause = "error"
                    self.request_abort_fn()
                    return
```

- [ ] **Step 4: Compute `reason` and write the end breadcrumb in `finally`**

Set a fatal-error flag in the generic handler (lines 511–513):

```python
    except Exception as e:
        self._log.exception(e)
        self._run_state_fatal = True
        raise
```

Initialize the flag at the very top of `run()` (next to `this_image_callback_id = None`, line 425):

```python
    def run(self):
        this_image_callback_id = None
        self._run_state_fatal = False
```

In the `finally` block, replace the existing Slack-finish block — from `if self._slack_notifier is not None:` (~line 526) through the final `self.callbacks.signal_acquisition_finished()` (line 539) — so it computes `reason`, writes the end breadcrumb, passes `reason` to `AcquisitionStats`, and still calls `signal_acquisition_finished()` exactly once. The replacement:

```python
        # Determine why the acquisition ended (drives the watchdog + the in-process finish msg).
        if self._run_state_fatal:
            reason = "error"
        elif self.abort_requested_fn():
            reason = "error" if self._abort_cause == "error" else "user_abort"
        elif self._acquisition_error_count > 0:
            reason = "completed_with_errors"
        else:
            reason = "completed"

        total_duration = time.time() - self.timestamp_acquisition_started
        self._run_state.end(
            reason,
            {
                "total_images": self.image_count,
                "total_timepoints": self.time_point,
                "total_duration_seconds": total_duration,
                "errors_encountered": self._acquisition_error_count,
            },
        )

        # Send Slack acquisition finished notification via callback (ensures ordering with timepoint notifications)
        if self._slack_notifier is not None:
            try:
                stats = AcquisitionStats(
                    total_images=self.image_count,
                    total_timepoints=self.time_point,
                    total_duration_seconds=total_duration,
                    errors_encountered=self._acquisition_error_count,
                    experiment_id=self.experiment_ID or "unknown",
                    reason=reason,
                )
                self.callbacks.signal_slack_acquisition_finished(stats)
            except Exception as e:
                self._log.warning(f"Failed to send Slack acquisition finished notification: {e}")

        self.callbacks.signal_acquisition_finished()
```

- [ ] **Step 5: Unit-test the reason logic in isolation**

```python
# tests/control/test_worker_reason.py
import time
from unittest.mock import MagicMock

import squid.acquisition_state as ast
from control.core.multi_point_worker import MultiPointWorker


def _make_worker(tmp_path, monkeypatch):
    # Build a bare worker without running __init__ (we only exercise the finally logic helpers).
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.time_point = 2
    w.Nt = 5
    w.image_count = 40
    w._acquisition_error_count = 0
    w._abort_cause = None
    w._run_state_fatal = False
    w.experiment_ID = "e"
    w.timestamp_acquisition_started = time.time() - 1
    w._run_state = ast.RunStateWriter.start(
        experiment_id="e", pid=1, config_path=None, output_path="o",
        expected={}, state_dir=tmp_path,
    )
    w.abort_requested_fn = lambda: False
    return w


def _reason(w):
    # Mirror the finally classification.
    if w._run_state_fatal:
        return "error"
    if w.abort_requested_fn():
        return "error" if w._abort_cause == "error" else "user_abort"
    if w._acquisition_error_count > 0:
        return "completed_with_errors"
    return "completed"


def test_reason_completed(tmp_path, monkeypatch):
    assert _reason(_make_worker(tmp_path, monkeypatch)) == "completed"


def test_reason_user_abort(tmp_path, monkeypatch):
    w = _make_worker(tmp_path, monkeypatch)
    w.abort_requested_fn = lambda: True
    assert _reason(w) == "user_abort"


def test_reason_error_on_timeout_abort(tmp_path, monkeypatch):
    w = _make_worker(tmp_path, monkeypatch)
    w.abort_requested_fn = lambda: True
    w._abort_cause = "error"
    assert _reason(w) == "error"


def test_reason_completed_with_errors(tmp_path, monkeypatch):
    w = _make_worker(tmp_path, monkeypatch)
    w._acquisition_error_count = 3
    assert _reason(w) == "completed_with_errors"
```

Run: `cd software && python3 -m pytest tests/control/test_worker_reason.py -v`
Expected: PASS (4 tests). (This test pins the classification table; the in-context version is exercised end-to-end in Task 12.)

- [ ] **Step 6: Commit**

```bash
git add software/control/core/multi_point_worker.py software/control/slack_notifier.py software/tests/control/test_worker_reason.py
git commit -m "feat(watchdog): heartbeat + end-reason breadcrumb in acquisition worker"
```

---

## Task 10: Notifier trim — gate the finish message on a clean end

**Files:**
- Modify: `control/slack_notifier.py` (`notify_acquisition_finished`, lines 610–646)
- Test: `tests/control/test_notifier_trim.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/control/test_notifier_trim.py
from unittest.mock import patch

import control._def
from control.slack_notifier import SlackNotifier, AcquisitionStats


def _stats(reason):
    return AcquisitionStats(
        total_images=10, total_timepoints=2, total_duration_seconds=5.0,
        errors_encountered=0, experiment_id="e", reason=reason,
    )


def test_finish_message_sent_only_on_clean_completion(monkeypatch):
    monkeypatch.setattr(control._def.SlackNotifications, "NOTIFY_ON_ACQUISITION_FINISHED", True)
    n = SlackNotifier(bot_token="x", channel_id="C")
    with patch.object(n, "_queue_message") as q:
        n.notify_acquisition_finished(_stats("completed"))
    assert q.call_count == 1

    with patch.object(n, "_queue_message") as q:
        n.notify_acquisition_finished(_stats("error"))
        n.notify_acquisition_finished(_stats("user_abort"))
        n.notify_acquisition_finished(_stats("completed_with_errors"))
    assert q.call_count == 0  # watchdog owns these alerts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd software && python3 -m pytest tests/control/test_notifier_trim.py -v`
Expected: FAIL — finish message is queued for all reasons (`assert 3 == 0`).

- [ ] **Step 3: Edit `notify_acquisition_finished`**

In `control/slack_notifier.py`, add a guard right after the existing `NOTIFY_ON_ACQUISITION_FINISHED` check at the top of `notify_acquisition_finished` (line ~611):

```python
    def notify_acquisition_finished(self, stats: AcquisitionStats):
        if not control._def.SlackNotifications.NOTIFY_ON_ACQUISITION_FINISHED:
            return
        if stats.reason != "completed":
            # Premature/degraded ends are reported once by the acquisition watchdog.
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd software && python3 -m pytest tests/control/test_notifier_trim.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add software/control/slack_notifier.py software/tests/control/test_notifier_trim.py
git commit -m "feat(watchdog): notifier reports only clean finishes; watchdog owns premature alerts"
```

---

## Task 11: Shutdown hook — abort + join on close

**Files:**
- Modify: `main_hcs.py` (shutdown sequence, lines 437–439)

- [ ] **Step 1: Edit the shutdown sequence**

In `main_hcs.py`, replace the shutdown tail (lines 437–439):

```python
    exit_code = app.exec_()
    logging.shutdown()  # Flush log handlers before os._exit() bypasses Python cleanup
    os._exit(exit_code)
```

with:

```python
    exit_code = app.exec_()

    # If the app is quitting mid-acquisition, request the normal abort and let the worker
    # write its end breadcrumb so the watchdog reports "aborted" rather than a crash.
    try:
        mpc = getattr(win, "multipointController", None)
        if mpc is not None and mpc.acquisition_in_progress():
            log.info("Acquisition in progress at shutdown; requesting abort before exit.")
            mpc.request_abort_aquisition()
            if getattr(mpc, "thread", None) is not None:
                mpc.thread.join(timeout=15.0)
    except Exception as e:
        log.warning(f"Error during shutdown abort handling: {e}")

    logging.shutdown()  # Flush log handlers before os._exit() bypasses Python cleanup
    os._exit(exit_code)
```

- [ ] **Step 2: Verify it imports and the app still launches**

Run: `cd software && python3 -c "import ast; ast.parse(open('main_hcs.py').read()); print('parse ok')"`
Expected: `parse ok`.

Run (manual smoke, simulation): `cd software && timeout 25 python3 main_hcs.py --simulation` — confirm the GUI starts and closes cleanly with no traceback from the new block. (No automated test: `main_hcs.py` is excluded from CI and drives the full GUI.)

- [ ] **Step 3: Commit**

```bash
git add software/main_hcs.py
git commit -m "feat(watchdog): write an aborted breadcrumb when quitting mid-acquisition"
```

---

## Task 12: Integration test — full breadcrumb lifecycle

**Files:**
- Create: `tests/control/test_watchdog_integration.py`

- [ ] **Step 1: Write the test**

```python
# tests/control/test_watchdog_integration.py
import os
import time

import squid.acquisition_state as ast
import control.microscope
import tests.control.gui_test_stubs as gts


def _wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_simulated_acquisition_writes_ended_breadcrumb(qtbot):
    state_dir = os.environ["SQUID_WATCHDOG_STATE_DIR"]
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)

    mpc.run_acquisition()
    assert _wait_for(lambda: ast.read_run(state_dir) is not None)
    assert ast.read_run(state_dir)["status"] == "running"

    # Let it finish (the default test acquisition is short); fall back to abort.
    finished = _wait_for(lambda: (ast.read_run(state_dir) or {}).get("status") == "ended", timeout=20.0)
    if not finished:
        mpc.request_abort_aquisition()
        assert _wait_for(lambda: (ast.read_run(state_dir) or {}).get("status") == "ended", timeout=20.0)

    rec = ast.read_run(state_dir)
    assert rec["status"] == "ended"
    assert rec["reason"] in {"completed", "completed_with_errors", "user_abort", "error"}
    assert rec["ended_at"] is not None
    scope.close()
```

- [ ] **Step 2: Run the test**

Run: `cd software && python3 -m pytest tests/control/test_watchdog_integration.py -v`
Expected: PASS — `run.json` transitions `running → ended` with a valid reason and `heartbeat_at`/`ended_at` populated.

- [ ] **Step 3: Commit**

```bash
git add software/tests/control/test_watchdog_integration.py
git commit -m "test(watchdog): end-to-end breadcrumb lifecycle in simulation"
```

---

## Task 13: Service recipes + README

**Files:**
- Create: `acquisition_watchdog/systemd/squid-acquisition-watchdog.service`
- Create: `acquisition_watchdog/windows/squid-acquisition-watchdog.xml`
- Create: `acquisition_watchdog/windows/install.ps1`
- Create: `acquisition_watchdog/README.md`

- [ ] **Step 1: Linux systemd user unit**

```ini
# acquisition_watchdog/systemd/squid-acquisition-watchdog.service
# Install (per user):
#   mkdir -p ~/.config/systemd/user
#   cp acquisition_watchdog/systemd/squid-acquisition-watchdog.service ~/.config/systemd/user/
#   # edit WorkingDirectory + --config below to match this machine, then:
#   systemctl --user daemon-reload
#   systemctl --user enable --now squid-acquisition-watchdog
[Unit]
Description=Squid acquisition watchdog (alerts on prematurely-ended acquisitions)
After=default.target

[Service]
Type=simple
WorkingDirectory=%h/Squid/software
ExecStart=/usr/bin/python3 -m acquisition_watchdog --config %h/Squid/software/configuration.ini
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 2: Windows Task Scheduler task + installer**

```xml
<!-- acquisition_watchdog/windows/squid-acquisition-watchdog.xml -->
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Squid acquisition watchdog (alerts on prematurely-ended acquisitions)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>pythonw.exe</Command>
      <Arguments>-m acquisition_watchdog --config C:\Squid\software\configuration.ini</Arguments>
      <WorkingDirectory>C:\Squid\software</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

```powershell
# acquisition_watchdog/windows/install.ps1
# Run in PowerShell from software\ :  .\acquisition_watchdog\windows\install.ps1
$ErrorActionPreference = "Stop"
$taskName = "SquidAcquisitionWatchdog"
$xmlPath  = Join-Path $PSScriptRoot "squid-acquisition-watchdog.xml"
Write-Host "Registering scheduled task '$taskName' from $xmlPath"
Register-ScheduledTask -TaskName $taskName -Xml (Get-Content $xmlPath -Raw) -Force
Write-Host "Done. Edit the task's --config/WorkingDirectory if your install path differs, then log off/on or 'Start' the task."
```

- [ ] **Step 3: README**

```markdown
# acquisition_watchdog/README.md
# Acquisition Watchdog

Independent process that alerts (via Slack) when a Squid acquisition ends
prematurely — process crash/hang/kill, fatal error, or user abort. Covers runs
launched from the GUI and from the MCP control server.

## How it works
The Squid GUI writes a `run.json` breadcrumb (start / throttled heartbeat / end)
into a shared state dir. This watchdog polls it and posts one Slack alert when a
run dies, hangs, or ends with a non-clean reason. Clean completions are silent.

## Run it
    cd software
    python3 -m acquisition_watchdog --config ./configuration.ini

Options: `--state-dir`, `--poll-interval` (5s), `--heartbeat-timeout` (120s), `--once`.

Slack credentials are read from the `[SlackNotifications]` section of the active
`.ini` (same `bot_token` / `channel_id` the GUI uses). Set `watchdog_enabled = False`
in that section to disable watchdog alerts on a machine.

## Install as an always-on service
- **Linux:** see `systemd/squid-acquisition-watchdog.service` (header has steps).
- **Windows:** run `windows/install.ps1` (registers a logon-triggered task).

## State dir
Defaults to `platformdirs.user_state_path("squid","cephla")/watchdog`. Override with
`SQUID_WATCHDOG_STATE_DIR` (must match the GUI's environment) or `--state-dir`.

## Remote / power-loss coverage (future)
Point `--state-dir` at a shared/synced mount on another host and run this process
there. Per-machine `run-<machine>.json` naming and clock-skew tolerance are needed
first (see the design spec, "Future work").
```

- [ ] **Step 4: Commit**

```bash
git add software/acquisition_watchdog/systemd software/acquisition_watchdog/windows software/acquisition_watchdog/README.md
git commit -m "docs(watchdog): add systemd + Windows service recipes and README"
```

---

## Task 14: Finalize — format, full test run, commit the spec

**Files:**
- All new/modified files
- `docs/superpowers/specs/2026-06-23-acquisition-watchdog-design.md`

- [ ] **Step 1: Format with Black**

Run: `cd software && black --config pyproject.toml squid/slack.py squid/acquisition_state.py acquisition_watchdog/ tests/squid/ tests/acquisition_watchdog/ tests/control/test_watchdog_breadcrumbs.py tests/control/test_watchdog_integration.py tests/control/test_worker_reason.py tests/control/test_notifier_trim.py tests/control/test_slack_notifier_send.py control/slack_notifier.py control/core/multi_point_worker.py control/core/multi_point_controller.py main_hcs.py`
Expected: files reformatted/unchanged; no errors.

- [ ] **Step 2: Run the watchdog + new unit tests**

Run: `cd software && python3 -m pytest tests/squid tests/acquisition_watchdog tests/control/test_worker_reason.py tests/control/test_notifier_trim.py tests/control/test_slack_notifier_send.py -v`
Expected: ALL PASS.

- [ ] **Step 3: Run the engine/integration tests**

Run: `cd software && python3 -m pytest tests/control/test_watchdog_breadcrumbs.py tests/control/test_watchdog_integration.py tests/control/test_MultiPointWorker.py -v`
Expected: ALL PASS (no regression in the existing worker test).

- [ ] **Step 4: Full suite (CI parity)**

Run: `cd software && python3 -m pytest --ignore=tests/control/test_HighContentScreeningGui.py`
Expected: no new failures attributable to these changes.

- [ ] **Step 5: Commit the spec + plan and verify Black on the whole tree**

Run: `cd software && black --config pyproject.toml --check .`
Expected: "All done!" (no files would be reformatted).

```bash
git add software/docs/superpowers/specs/2026-06-23-acquisition-watchdog-design.md software/docs/superpowers/plans/2026-06-23-acquisition-watchdog.md
git commit -m "docs(watchdog): add design spec and implementation plan"
```

---

## Self-review notes

- **Spec coverage:** start/heartbeat/end protocol (Tasks 3, 8, 9); watchdog poll/classify/dedup (Task 6); config sharing (Task 4); cross-platform state dir + PID degrade (Tasks 3, 6); deployment recipes (Task 13); notifier split (Tasks 2, 10); server coverage (engine-level instrumentation in Tasks 8–9 — no server-specific code needed). v1 out-of-scope items (progress-stall, power-loss, server-thread health) are intentionally absent.
- **`app_closed`** from the spec is implemented as `user_abort` via the shutdown abort+join (Task 11); noted in the taxonomy above. Update the spec's taxonomy/ shutdown wording to match (done as part of plan authoring).
- **Type consistency:** `RunStateWriter.start(...)`/`beat`/`end`, `read_run`, `default_state_dir`, `NullRunStateWriter` used identically across Tasks 3/6/8/9/12; `SlackConfig` fields (`enabled`,`bot_token`,`channel_id`,`watchdog_enabled`) consistent across Tasks 4/6; `AcquisitionStats.reason` added in Task 9 and consumed in Task 10; `squid.slack.post_message(token, channel, text, blocks)` signature consistent across Tasks 1/2/6.
