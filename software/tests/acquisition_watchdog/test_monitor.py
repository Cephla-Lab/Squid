# tests/acquisition_watchdog/test_monitor.py
import json
import os
import time

import squid.acquisition_state as ast
from acquisition_watchdog.config import SlackConfig
from acquisition_watchdog.monitor import Monitor


def _running(tmp_path, pid, heartbeat_age=0.0, run_id="r1"):
    rec = {
        "schema_version": 1,
        "run_id": run_id,
        "experiment_id": "e",
        "machine": "m",
        "pid": pid,
        "config_path": None,
        "output_path": "o",
        "started_at": time.time() - 100,
        "heartbeat_at": time.time() - heartbeat_age,
        "progress": {},
        "expected": {},
        "status": "running",
        "reason": None,
        "ended_at": None,
        "stats": None,
    }
    ast._atomic_write_json(ast.run_file_path(tmp_path), rec)
    return rec


def _mon(tmp_path):
    return Monitor(state_dir=tmp_path, heartbeat_timeout=120.0)


def test_running_with_live_pid_and_fresh_heartbeat_is_silent(tmp_path):
    _running(tmp_path, pid=os.getpid(), heartbeat_age=1.0)
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) is None


def test_dead_pid_is_crash(tmp_path):
    _running(tmp_path, pid=2_000_000_000, heartbeat_age=1.0)  # impossible pid
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) == "crash"


def test_stale_heartbeat_with_live_pid_is_hang(tmp_path):
    _running(tmp_path, pid=os.getpid(), heartbeat_age=999.0)
    assert _mon(tmp_path).classify(ast.read_run(tmp_path), time.time()) == "hang"


def test_ended_reasons(tmp_path):
    mon = _mon(tmp_path)
    for reason, expect in [
        ("completed", None),
        ("completed_with_errors", "completed_with_errors"),
        ("error", "error"),
        ("user_abort", "user_abort"),
    ]:
        run = {"run_id": f"x-{reason}", "status": "ended", "reason": reason}
        assert mon.classify(run, time.time()) == expect


def test_dedup_persists_across_restart(tmp_path, monkeypatch):
    _running(tmp_path, pid=2_000_000_000, run_id="dup1")
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a) or (True, "1")))
    monkeypatch.setattr(
        "acquisition_watchdog.config.load_slack_config",
        lambda p: SlackConfig("xoxb", "C1", True),
    )
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1
    # Fresh Monitor (simulated restart) must not re-alert the same run_id.
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1


# ---- fluidics protocol runs (run_manifest.json, found through the fluidics_protocol_run.json breadcrumb) ----

HELD_ALERT_S, REMINDER_S = 600.0, 3600.0


def _protocol_run(tmp_path, status="held", held_age=None, heartbeat_age=1.0, pid=None, run="liver_2026-11-22"):
    """A run folder with a run_manifest.json, and the breadcrumb that points the watchdog at it."""
    now = time.time()
    run_dir = tmp_path / run
    run_dir.mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "run_name": "liver",
        "run_dir": str(run_dir),
        "status": status,
        "hold_reason": "error" if status == "held" else None,
        "hold_message": "ValueError: not enough values to unpack" if status == "held" else None,
        "held_at": now - held_age if held_age is not None else None,
        "cursor": {"step": 1, "attempt": 1, "sequence": None},
        "steps": [{"index": 0, "kind": "fluidics", "label": "R20"}, {"index": 1, "kind": "imaging", "label": "R20"}],
        "pid": pid or os.getpid(),
        "heartbeat_at": now - heartbeat_age,
        "started_at": now - 100_000,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest))
    ast.write_protocol_run(
        run_dir=str(run_dir),
        manifest_path=str(run_dir / "run_manifest.json"),
        run_name="liver",
        pid=manifest["pid"],
        machine="m",
        state_dir=tmp_path,
    )
    return manifest


def _beat(manifest, at):
    """The runner keeps beating while a run is held: the manifest as rewritten at time `at`."""
    manifest["heartbeat_at"] = at - 1.0
    with open(os.path.join(manifest["run_dir"], "run_manifest.json"), "w") as f:
        json.dump(manifest, f)
    return at


def _protocol_mon(tmp_path):
    return Monitor(
        state_dir=tmp_path,
        heartbeat_timeout=120.0,
        protocol_held_alert_s=HELD_ALERT_S,
        protocol_held_reminder_s=REMINDER_S,
    )


def _slack(monkeypatch):
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a) or (True, "1")))
    monkeypatch.setattr("acquisition_watchdog.config.load_slack_config", lambda p: SlackConfig("xoxb", "C1", True))
    return sent


def test_a_protocol_run_held_past_the_alert_delay_alerts_then_reminds_once(tmp_path):
    mon = _protocol_mon(tmp_path)
    assert mon.classify_protocol(_protocol_run(tmp_path, held_age=HELD_ALERT_S - 30), time.time()) is None

    manifest = _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    kind, keys = mon.classify_protocol(manifest, time.time())
    assert kind == "protocol_held" and len(keys) == 1
    mon._mark_alerted(keys)
    assert mon.classify_protocol(manifest, time.time()) is None  # alerted: quiet until the reminder

    later = _beat(manifest, manifest["held_at"] + HELD_ALERT_S + REMINDER_S + 1)
    kind, keys = mon.classify_protocol(manifest, later)
    assert kind == "protocol_held_reminder"
    mon._mark_alerted(keys)
    much_later = _beat(manifest, later + 10 * REMINDER_S)
    assert mon.classify_protocol(manifest, much_later) is None  # one reminder, not a stream


def test_a_hold_found_late_gets_one_alert_not_an_alert_and_a_reminder(tmp_path):
    mon = _protocol_mon(tmp_path)
    manifest = _protocol_run(tmp_path, held_age=7.5 * 3600)  # the watchdog came up hours into the hold
    kind, keys = mon.classify_protocol(manifest, time.time())
    assert kind == "protocol_held" and len(keys) == 2
    mon._mark_alerted(keys)
    assert mon.classify_protocol(manifest, time.time()) is None


def test_a_second_hold_of_the_same_run_alerts_again(tmp_path):
    mon = _protocol_mon(tmp_path)
    first = _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    mon._mark_alerted(mon.classify_protocol(first, time.time())[1])
    second = _protocol_run(tmp_path, held_age=HELD_ALERT_S + 5)  # resumed, then held again later
    assert mon.classify_protocol(second, time.time())[0] == "protocol_held"


def test_a_protocol_run_whose_heartbeat_stops_or_whose_process_died_is_stalled(tmp_path):
    mon = _protocol_mon(tmp_path)
    assert mon.classify_protocol(_protocol_run(tmp_path, status="running"), time.time()) is None
    assert mon.classify_protocol(_protocol_run(tmp_path, status="paused"), time.time()) is None
    stale = _protocol_run(tmp_path, status="running", heartbeat_age=999.0)
    assert mon.classify_protocol(stale, time.time())[0] == "protocol_stalled"
    dead = _protocol_run(tmp_path, status="held", held_age=5.0, pid=2_000_000_000)
    assert mon.classify_protocol(dead, time.time())[0] == "protocol_stalled"


def test_an_ended_protocol_run_is_silent(tmp_path):
    mon = _protocol_mon(tmp_path)
    for status in ("finished", "stopped", "failed"):
        ended = _protocol_run(tmp_path, status=status, heartbeat_age=99_999.0, pid=2_000_000_000)
        assert mon.classify_protocol(ended, time.time()) is None


def test_protocol_alerts_are_sent_once_across_a_watchdog_restart(tmp_path, monkeypatch):
    sent = _slack(monkeypatch)
    manifest = _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    _protocol_mon(tmp_path).check_once(time.time())
    assert len(sent) == 1 and "HELD" in sent[0][2]
    _protocol_mon(tmp_path).check_once(time.time())  # simulated restart
    assert len(sent) == 1
    _protocol_mon(tmp_path).check_once(_beat(manifest, manifest["held_at"] + HELD_ALERT_S + REMINDER_S + 1))
    assert len(sent) == 2 and "still HELD" in sent[1][2]
    _protocol_mon(tmp_path).check_once(_beat(manifest, manifest["held_at"] + HELD_ALERT_S + 3 * REMINDER_S))
    assert len(sent) == 2


def test_a_failed_protocol_alert_is_retried(tmp_path, monkeypatch):
    attempts = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (attempts.append(a) or (False, None)))
    monkeypatch.setattr("acquisition_watchdog.config.load_slack_config", lambda p: SlackConfig("xoxb", "C1", True))
    _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    mon = _protocol_mon(tmp_path)
    mon.check_once(time.time())
    mon.check_once(time.time())
    assert len(attempts) == 2


def test_no_breadcrumb_or_a_missing_run_folder_is_silent(tmp_path, monkeypatch):
    sent = _slack(monkeypatch)
    _protocol_mon(tmp_path).check_once(time.time())  # no breadcrumb at all
    manifest = _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    os.remove(os.path.join(manifest["run_dir"], "run_manifest.json"))
    _protocol_mon(tmp_path).check_once(time.time())
    assert sent == []
