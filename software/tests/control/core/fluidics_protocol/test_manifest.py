import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime

import psutil
import pytest

from control.core.fluidics_protocol import manifest as m
from control.models.fluidics_run import RunCursor, RunManifest


def _manifest(run_dir, status="running", pid=None):
    return RunManifest(
        run_name="liver",
        run_dir=str(run_dir),
        status=status,
        cursor=RunCursor(step=2, attempt=1, sequence=3),
        pid=pid if pid is not None else os.getpid(),
        heartbeat_at=time.time(),  # the writer stamps every write: a live writer's heartbeat falls in its lifetime
        started_at=1.0,
    )


def test_run_folder_name_and_create_run_dir(tmp_path):
    when = datetime(2026, 8, 30, 14, 2, 11)
    assert m.run_folder_name("liver s3", when) == "liver_s3_2026-08-30_14-02-11"
    run_dir = m.create_run_dir(tmp_path, "liver s3", when)
    assert run_dir == tmp_path / "liver_s3_2026-08-30_14-02-11" and run_dir.is_dir()
    with pytest.raises(FileExistsError):
        m.create_run_dir(tmp_path, "liver s3", when)


def test_manifest_write_is_atomic_and_readable(tmp_path):
    path = m.write_manifest(tmp_path, _manifest(tmp_path))
    assert path.name == m.MANIFEST_NAME
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert m.read_manifest(tmp_path).cursor.sequence == 3


def test_markers_and_sha(tmp_path):
    folder = tmp_path / "R01_image"
    folder.mkdir()
    m.write_aborted_marker(folder, {"reason": "user_abort"})
    m.write_step_info(folder, {"round": "R01"})
    assert json.loads((folder / m.ABORTED_MARKER).read_text())["reason"] == "user_abort"
    assert json.loads((folder / m.STEP_INFO_NAME).read_text())["round"] == "R01"
    (tmp_path / "p.yaml").write_text("a: 1\n")
    digest = m.sha256_of_file(tmp_path / "p.yaml")
    assert len(digest) == 64 and digest == m.sha256_of_file(tmp_path / "p.yaml")


def test_find_unfinished_runs_reports_dead_non_terminal_runs_newest_first(tmp_path):
    dead_pid = 2**22 - 7  # very unlikely to exist
    for name, status, pid, started in [
        ("a", "running", dead_pid, 1.0),
        ("b", "finished", dead_pid, 2.0),
        ("c", "held", dead_pid, 3.0),
        ("d", "running", os.getpid(), 4.0),
    ]:
        run_dir = tmp_path / name
        run_dir.mkdir()
        man = _manifest(run_dir, status=status, pid=pid)
        man.started_at = started
        m.write_manifest(run_dir, man)
    (tmp_path / "noise").mkdir()

    found = m.find_unfinished_runs(tmp_path)

    assert [os.path.basename(x.run_dir) for x in found] == ["c", "a"]
    assert m.pid_alive(os.getpid()) is True and m.pid_alive(dead_pid) is False


def test_run_names_must_be_a_single_filesystem_safe_component(tmp_path):
    for bad in ("../evil", "/tmp/abs", "a/b", "", ".hidden"):
        with pytest.raises(ValueError):
            m.create_run_dir(tmp_path, bad)
    assert not list(tmp_path.iterdir())


def test_pid_alive_never_signals_the_process(monkeypatch):
    # On Windows os.kill(pid, 0) sends CTRL_C_EVENT to the pid's console instead of checking that the pid exists.
    signalled = []

    def no_kill(pid, sig):
        signalled.append((pid, sig))
        raise AssertionError("pid_alive must not signal the process")

    asked = []

    def fake_pid_exists(pid):
        asked.append(pid)
        return pid == os.getpid()

    monkeypatch.setattr(os, "kill", no_kill)
    monkeypatch.setattr(psutil, "pid_exists", fake_pid_exists)

    assert m.pid_alive(os.getpid()) is True
    assert m.pid_alive(os.getpid() + 1) is False
    assert signalled == [] and asked == [os.getpid(), os.getpid() + 1]


def test_pid_alive_is_false_for_an_exited_and_reaped_process():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()

    assert m.pid_alive(child.pid) is False
    assert m.pid_alive(child.pid, written_at=time.time()) is False


def test_pid_alive_is_true_for_the_current_process():
    assert m.pid_alive(os.getpid()) is True
    assert m.pid_alive(os.getpid(), written_at=time.time()) is True


def test_pid_alive_is_false_for_a_process_born_after_the_writer_last_wrote():
    # A crashed writer's pid reused by a later process: that process cannot have written the manifest.
    born = psutil.Process(os.getpid()).create_time()

    assert m.pid_alive(os.getpid(), written_at=born - 60) is False
    assert m.pid_alive(os.getpid(), written_at=born - 1) is True  # clock granularity tolerance


def test_pid_alive_treats_an_uninspectable_process_as_the_writer(monkeypatch):
    class _Denied:
        def __init__(self, pid):
            raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", _Denied)

    assert m.pid_alive(os.getpid(), written_at=0.0) is True


def test_pid_alive_is_false_when_the_process_ends_during_the_check(monkeypatch):
    class _Gone:
        def __init__(self, pid):
            raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", _Gone)

    assert m.pid_alive(os.getpid(), written_at=time.time()) is False


def test_find_unfinished_runs_reports_a_run_whose_pid_was_reused(tmp_path):
    run_dir = tmp_path / "crashed"
    run_dir.mkdir()
    man = _manifest(run_dir, pid=os.getpid())  # the pid now belongs to this (later-born) process
    man.heartbeat_at = psutil.Process(os.getpid()).create_time() - 3600
    man.started_at = man.heartbeat_at - 600
    m.write_manifest(run_dir, man)

    assert [os.path.basename(x.run_dir) for x in m.find_unfinished_runs(tmp_path)] == ["crashed"]


def test_find_unfinished_runs_does_not_report_a_resumed_run_that_is_still_running(tmp_path):
    # A resume rewrites pid but keeps the run's original started_at, so the live writer was born after started_at.
    run_dir = tmp_path / "resumed"
    run_dir.mkdir()
    man = _manifest(run_dir, pid=os.getpid())
    man.started_at = psutil.Process(os.getpid()).create_time() - 3600
    m.write_manifest(run_dir, man)

    assert m.find_unfinished_runs(tmp_path) == []


@pytest.mark.parametrize("error", [OSError("cannot query the process"), OverflowError("pid out of range")])
def test_find_unfinished_runs_skips_a_run_whose_pid_check_fails_and_reports_the_rest(
    tmp_path, monkeypatch, caplog, error
):
    for name, pid, started in [("ok", 101, 1.0), ("bad", 202, 2.0), ("ok2", 303, 3.0)]:
        run_dir = tmp_path / name
        run_dir.mkdir()
        man = _manifest(run_dir, pid=pid)
        man.started_at = started
        m.write_manifest(run_dir, man)

    def flaky_pid_alive(pid, written_at=None):
        if pid == 202:
            raise error
        return False

    monkeypatch.setattr(m, "pid_alive", flaky_pid_alive)
    caplog.set_level(logging.WARNING)

    found = m.find_unfinished_runs(tmp_path)

    assert [os.path.basename(x.run_dir) for x in found] == ["ok2", "ok"]
    assert any(str(tmp_path / "bad") in r.getMessage() and r.exc_info for r in caplog.records)


def test_find_unfinished_runs_logs_and_skips_an_unreadable_manifest(tmp_path, caplog):
    good = tmp_path / "good"
    good.mkdir()
    m.write_manifest(good, _manifest(good, pid=2**22 - 7))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / m.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    caplog.set_level(logging.WARNING)

    found = m.find_unfinished_runs(tmp_path)

    assert [os.path.basename(x.run_dir) for x in found] == ["good"]
    assert any(str(broken / m.MANIFEST_NAME) in r.getMessage() and r.exc_info for r in caplog.records)
