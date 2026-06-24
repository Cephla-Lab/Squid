# tests/acquisition_watchdog/test_monitor.py
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
        lambda p: SlackConfig(True, "xoxb", "C1", True),
    )
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1
    # Fresh Monitor (simulated restart) must not re-alert the same run_id.
    Monitor(state_dir=tmp_path).check_once(time.time())
    assert len(sent) == 1
