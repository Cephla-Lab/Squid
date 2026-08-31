import json
import os
from datetime import datetime

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
        heartbeat_at=1.0,
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
