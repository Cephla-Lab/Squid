# tests/squid/test_acquisition_state.py
import json

import squid.acquisition_state as ast


def _expected():
    return {"timepoints": 3, "regions": 1, "fovs": 4, "channels": 2, "z": 1}


def test_start_writes_running_record(tmp_path):
    w = ast.RunStateWriter.start(
        experiment_id="exp1",
        pid=4321,
        config_path="/cfg.ini",
        output_path=str(tmp_path / "exp1"),
        expected=_expected(),
        machine="micro-1",
        state_dir=tmp_path,
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
        experiment_id="e",
        pid=1,
        config_path=None,
        output_path="o",
        expected=_expected(),
        state_dir=tmp_path,
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
        experiment_id="e",
        pid=1,
        config_path=None,
        output_path="o",
        expected=_expected(),
        state_dir=tmp_path,
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
