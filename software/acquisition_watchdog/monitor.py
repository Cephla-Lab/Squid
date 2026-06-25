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
        slack_settings: Optional[str] = None,
        poll_interval: float = 5.0,
        heartbeat_timeout: float = 120.0,
    ):
        self._state_dir = Path(state_dir) if state_dir else None
        self._slack_settings = slack_settings
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

        cfg_path = config.resolve_slack_settings_path(self._slack_settings)
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
