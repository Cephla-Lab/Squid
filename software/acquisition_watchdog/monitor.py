# acquisition_watchdog/monitor.py
"""Poll the acquisition run-state and alert on premature ends; poll the fluidics protocol run and alert
when it has been HELD too long or has stopped responding."""
import json
import os
import time
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import squid.acquisition_state as acquisition_state
import squid.logging
import squid.slack
from acquisition_watchdog import alerts, config

_log = squid.logging.get_logger("acquisition_watchdog")

ALERT_REASONS = {"completed_with_errors", "error", "user_abort"}
# run_manifest.json statuses of a protocol run that has not ended (finished / stopped / failed)
PROTOCOL_ACTIVE_STATUSES = {"running", "paused", "held"}


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
        protocol_held_alert_s: float = 600.0,
        protocol_held_reminder_s: float = 3600.0,
    ):
        self._state_dir = Path(state_dir) if state_dir else None
        self._slack_settings = slack_settings
        self._poll = poll_interval
        self._timeout = heartbeat_timeout
        self._held_alert_s = protocol_held_alert_s
        self._held_reminder_s = protocol_held_reminder_s  # after the first held alert; 0 = no reminder
        self._base = self._state_dir or acquisition_state.default_state_dir()
        self._alerted_path = self._base / "alerted.json"
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

    def classify_protocol(self, manifest: Optional[dict], now: float) -> Optional[Tuple[str, List[str]]]:
        """Return (alert kind, the de-dup keys sending it settles) for a protocol run's manifest, or None.

        'protocol_stalled': not ended, and its process is gone or its heartbeat is older than the timeout
        (a held or paused run still beats). 'protocol_held': HELD for longer than the alert delay;
        'protocol_held_reminder': still the same hold, the reminder delay after that. One of each per hold.
        """
        if not manifest or manifest.get("status") not in PROTOCOL_ACTIVE_STATUSES:
            return None
        run_key = f"protocol:{manifest.get('run_dir')}"
        heartbeat_at = manifest.get("heartbeat_at") or 0
        if not pid_alive(manifest.get("pid")) or (now - heartbeat_at) > self._timeout:
            key = f"{run_key}:stalled:{heartbeat_at}"
            return None if key in self._alerted else ("protocol_stalled", [key])
        held_at = manifest.get("held_at")
        if manifest.get("status") != "held" or not held_at:
            return None
        held_for = now - held_at
        held_key, reminder_key = f"{run_key}:held:{held_at}", f"{run_key}:held-reminder:{held_at}"
        reminder_due = self._held_reminder_s > 0 and held_for >= self._held_alert_s + self._held_reminder_s
        if held_key not in self._alerted:
            if held_for < self._held_alert_s:
                return None
            # found late (watchdog started mid-hold): one alert, not an alert and a reminder seconds apart
            return "protocol_held", [held_key] + ([reminder_key] if reminder_due else [])
        if reminder_due and reminder_key not in self._alerted:
            return "protocol_held_reminder", [reminder_key]
        return None

    def check_once(self, now: float) -> None:
        self._check_acquisition(now)
        self._check_protocol_run(now)

    def _check_acquisition(self, now: float) -> None:
        run = acquisition_state.read_run(self._state_dir)
        kind = self.classify(run, now)
        if kind is None:
            return
        text, blocks = alerts.format_alert(kind, run)
        self._send(kind, [run["run_id"]], text, blocks, f"run_id={run.get('run_id')}")

    def _check_protocol_run(self, now: float) -> None:
        pointer = acquisition_state.read_protocol_run(self._state_dir)
        if not pointer or not pointer.get("manifest_path"):
            return
        try:
            with open(pointer["manifest_path"], encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            return  # moved, deleted, or caught mid-write by a non-atomic copy: look again next poll
        verdict = self.classify_protocol(manifest, now)
        if verdict is None:
            return
        kind, keys = verdict
        text, blocks = alerts.format_protocol_alert(kind, manifest, pointer, now)
        self._send(kind, keys, text, blocks, f"protocol run {manifest.get('run_dir')}")

    def _send(self, kind: str, keys: List[str], text: str, blocks: list, what: str) -> None:
        cfg_path = config.resolve_slack_settings_path(self._slack_settings)
        slack_cfg = config.load_slack_config(cfg_path)
        if not (slack_cfg.bot_token and slack_cfg.channel_id and slack_cfg.watchdog_enabled):
            _log.warning(f"Alert ({kind}) for {what} but Slack is not configured/enabled; not alerting.")
            self._mark_alerted(keys)
            return

        ok, _ = squid.slack.post_message(slack_cfg.bot_token, slack_cfg.channel_id, text, blocks)
        if ok:
            _log.info(f"Sent watchdog alert ({kind}) for {what}")
            self._mark_alerted(keys)
        else:
            # Leave unmarked so a transient Slack failure retries on the next poll.
            _log.warning(f"Failed to send watchdog alert ({kind}) for {what}; will retry")

    def _mark_alerted(self, keys: Iterable[str]) -> None:
        self._alerted.update(keys)
        self._save_alerted()

    def run_forever(self) -> None:
        _log.info(
            f"Acquisition watchdog started. state_dir={self._base} heartbeat_timeout={self._timeout}s "
            f"protocol_held_alert={self._held_alert_s}s protocol_held_reminder={self._held_reminder_s}s"
        )
        while True:
            try:
                self.check_once(time.time())
            except Exception as e:
                _log.exception(f"Watchdog poll error: {e}")
            time.sleep(self._poll)
