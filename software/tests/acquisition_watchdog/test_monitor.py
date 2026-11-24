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
    alerted_at = time.time()
    kind, keys = mon.classify_protocol(manifest, alerted_at)
    assert kind == "protocol_held" and len(keys) == 1
    mon._mark_alerted(keys, alerted_at)
    assert mon.classify_protocol(manifest, time.time()) is None  # alerted: quiet until the reminder

    later = _beat(manifest, alerted_at + REMINDER_S + 1)
    kind, keys = mon.classify_protocol(manifest, later)
    assert kind == "protocol_held_reminder"
    mon._mark_alerted(keys)
    much_later = _beat(manifest, later + 10 * REMINDER_S)
    assert mon.classify_protocol(manifest, much_later) is None  # one reminder, not a stream


def test_the_reminder_comes_the_reminder_delay_after_the_held_alert_not_after_the_hold_began(tmp_path, monkeypatch):
    # the watchdog first sees the hold at 69 min (alert at 10, reminder 60 later): the reminder must not follow
    # the alert one minute later, and its timing survives a watchdog restart
    sent = _slack(monkeypatch)
    manifest = _protocol_run(tmp_path, held_age=69 * 60)
    first = time.time()
    _protocol_mon(tmp_path).check_once(first)
    assert len(sent) == 1 and "HELD" in sent[0][2]
    _protocol_mon(tmp_path).check_once(_beat(manifest, first + 60))
    assert len(sent) == 1  # 70 min held, 1 min after the alert
    _protocol_mon(tmp_path).check_once(_beat(manifest, first + REMINDER_S - 5))
    assert len(sent) == 1
    _protocol_mon(tmp_path).check_once(_beat(manifest, first + REMINDER_S + 1))
    assert len(sent) == 2 and "still HELD" in sent[1][2]


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
    _protocol_mon(tmp_path).check_once(_beat(manifest, time.time() + REMINDER_S + 1))
    assert len(sent) == 2 and "still HELD" in sent[1][2]
    _protocol_mon(tmp_path).check_once(_beat(manifest, time.time() + 3 * REMINDER_S))
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


# ---- the watchdog's own heartbeat (read by the GUI's Start run… pre-flight) ----


def test_a_running_watchdog_leaves_a_heartbeat_the_gui_can_check(tmp_path, monkeypatch):
    assert "not running (no heartbeat in" in ast.watchdog_problem(tmp_path)
    mon = Monitor(state_dir=tmp_path, poll_interval=5.0)
    polls = []

    def check_once(now):
        polls.append(now)
        if len(polls) == 2:
            raise KeyboardInterrupt  # leave run_forever after the second poll

    monkeypatch.setattr(mon, "check_once", check_once)
    monkeypatch.setattr("acquisition_watchdog.monitor.time.sleep", lambda s: None)
    try:
        mon.run_forever()
    except KeyboardInterrupt:
        pass
    beat = json.loads((tmp_path / ast.WATCHDOG_FILE_NAME).read_text())
    assert beat["pid"] == os.getpid() and beat["poll_interval"] == 5.0
    assert ast.watchdog_problem(tmp_path) is None
    assert ast.watchdog_problem(tmp_path, now=beat["written_at"] + 59) is None
    assert "last heartbeat 3 min ago" in ast.watchdog_problem(tmp_path, now=beat["written_at"] + 200)


# ---- review follow-up: late-ness, and the de-dup state written as one atomic record ----


def test_a_hold_seen_on_time_whose_alert_only_got_through_late_still_gets_its_reminder(tmp_path, monkeypatch):
    # Slack fails from the alert's due time (10 min) until 71 min: the alert that finally goes out is not a
    # "found late" alert, so the reminder still comes, the reminder delay after that send
    slack_up = {"v": False}
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a[2]) or (slack_up["v"], "1")))
    monkeypatch.setattr("acquisition_watchdog.config.load_slack_config", lambda p: SlackConfig("xoxb", "C1", True))
    manifest = _protocol_run(tmp_path, held_age=0)
    t0 = manifest["held_at"]
    mon = _protocol_mon(tmp_path)
    for minute in range(10, 71):
        mon.check_once(_beat(manifest, t0 + minute * 60 + 1))
    failed = len(sent)
    slack_up["v"] = True
    mon.check_once(_beat(manifest, t0 + 71 * 60))
    assert len(sent) == failed + 1 and "still HELD" not in sent[-1]
    _protocol_mon(tmp_path).check_once(_beat(manifest, t0 + 71 * 60 + REMINDER_S - 5))  # survives a restart
    assert len(sent) == failed + 1
    _protocol_mon(tmp_path).check_once(_beat(manifest, t0 + 71 * 60 + REMINDER_S + 1))
    assert len(sent) == failed + 2 and "still HELD" in sent[-1]


def test_a_hold_first_seen_late_stays_late_across_a_restart_while_slack_is_down(tmp_path, monkeypatch):
    slack_up = {"v": False}
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a[2]) or (slack_up["v"], "1")))
    monkeypatch.setattr("acquisition_watchdog.config.load_slack_config", lambda p: SlackConfig("xoxb", "C1", True))
    manifest = _protocol_run(tmp_path, held_age=7.5 * 3600)
    now = time.time()
    _protocol_mon(tmp_path).check_once(now)
    slack_up["v"] = True
    _protocol_mon(tmp_path).check_once(_beat(manifest, now + 60))
    for hour in range(1, 6):
        _protocol_mon(tmp_path).check_once(_beat(manifest, now + hour * REMINDER_S + 60))
    assert len(sent) == 2 and all("still HELD" not in text for text in sent)  # one failed try, one alert


def test_the_dedup_keys_and_send_times_are_one_record_so_a_restart_keeps_the_reminder_timing(tmp_path, monkeypatch):
    sent = _slack(monkeypatch)
    manifest = _protocol_run(tmp_path, held_age=69 * 60)
    first = time.time()
    _protocol_mon(tmp_path).check_once(first)
    assert len(sent) == 1
    assert sorted(p.name for p in tmp_path.iterdir() if "alert" in p.name) == ["alerted.json"]
    record = json.loads((tmp_path / "alerted.json").read_text())
    held_key = f"protocol:{manifest['run_dir']}:held:{manifest['held_at']}"
    assert held_key in record["keys"] and record["held_alerted_at"][held_key] == first
    _protocol_mon(tmp_path).check_once(_beat(manifest, first + 61))  # 70 min held, 1 min after the alert
    assert len(sent) == 1


def test_the_dedup_record_is_written_atomically(tmp_path, monkeypatch):
    _slack(monkeypatch)
    writes = []
    real = ast._atomic_write_json
    monkeypatch.setattr(ast, "_atomic_write_json", lambda path, data: (writes.append(path), real(path, data))[1])
    _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    _protocol_mon(tmp_path).check_once(time.time())
    assert tmp_path / "alerted.json" in writes


def test_an_alerted_json_in_the_old_list_format_still_suppresses_the_alert(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("squid.slack.post_message", lambda *a, **k: (sent.append(a) or (True, "1")))
    monkeypatch.setattr("acquisition_watchdog.config.load_slack_config", lambda p: SlackConfig("xoxb", "C1", True))
    (tmp_path / "alerted.json").write_text(json.dumps(["r1"]))  # written by a watchdog before this version
    _running(tmp_path, pid=2_000_000_000)
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert sent == []
    _protocol_run(tmp_path, held_age=HELD_ALERT_S + 30)
    _protocol_mon(tmp_path).check_once(time.time())  # an alert rewrites the record in the new format
    assert len(sent) == 1
    _protocol_mon(tmp_path).check_once(time.time())
    assert len(sent) == 1  # neither the old run nor the hold is alerted again
