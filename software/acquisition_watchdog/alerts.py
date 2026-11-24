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


_PROTOCOL_KIND_TITLE = {
    "protocol_held": ":large_orange_circle: Fluidics protocol run HELD, waiting for someone",
    "protocol_held_reminder": ":red_circle: Fluidics protocol run still HELD",
    "protocol_stalled": ":red_circle: Fluidics protocol run stopped responding (no heartbeat)",
}


def _fmt_duration(seconds: float) -> str:
    minutes = max(0, int(seconds)) // 60
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _protocol_step_line(manifest: dict) -> str:
    steps = manifest.get("steps") or []
    index = (manifest.get("cursor") or {}).get("step")
    if index is None or not 0 <= index < len(steps):
        return "unknown"
    step = steps[index] or {}
    label = step.get("label") or step.get("round") or ""
    return f"{index + 1}/{len(steps)} ({step.get('kind', '?')}{', ' + label if label else ''})"


def format_protocol_alert(kind: str, manifest: dict, pointer: dict, now: float) -> Tuple[str, list]:
    title = _PROTOCOL_KIND_TITLE.get(kind, f"Fluidics protocol alert: {kind}")
    run_name = manifest.get("run_name") or pointer.get("run_name") or "unknown"
    machine = pointer.get("machine", "unknown")
    text = f"{title}: {run_name} on {machine}"

    lines = [f"*Run:* {run_name}", f"*Machine:* {machine}", f"*Step:* {_protocol_step_line(manifest)}"]
    if kind in ("protocol_held", "protocol_held_reminder"):
        held_at = manifest.get("held_at")
        lines.append(f"*Held for:* {_fmt_duration(now - held_at) if held_at else 'unknown'} (since {_fmt_ts(held_at)})")
        reason = manifest.get("hold_reason") or "unknown"
        message = manifest.get("hold_message")
        lines.append(f"*Reason:* {reason}" + (f": {message}" if message else ""))
    else:
        lines.append(f"*Status:* {manifest.get('status', 'unknown')}")
        lines.append(f"*Last heartbeat:* {_fmt_ts(manifest.get('heartbeat_at'))}")
    lines.append(f"*Run folder:* {manifest.get('run_dir') or pointer.get('run_dir', 'unknown')}")
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    return text, blocks


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
