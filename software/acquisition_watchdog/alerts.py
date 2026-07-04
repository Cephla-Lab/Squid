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
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
    ]
    return text, blocks
