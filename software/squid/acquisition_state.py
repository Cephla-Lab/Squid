# squid/acquisition_state.py
"""On-disk acquisition run-state breadcrumbs, shared by the acquisition engine
(writer) and the standalone acquisition watchdog (reader).

Stdlib-only leaf module: must NOT import anything from `control`.
"""
import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import platformdirs

import squid.logging

_log = squid.logging.get_logger(__name__)

SCHEMA_VERSION = 1
HEARTBEAT_INTERVAL_S = 5.0
RUN_FILE_NAME = "run.json"
PROTOCOL_RUN_FILE_NAME = "fluidics_protocol_run.json"
WATCHDOG_FILE_NAME = "watchdog.json"


def default_state_dir() -> Path:
    """Per-user watchdog state dir, shared by writer and reader.

    Overridable via SQUID_WATCHDOG_STATE_DIR (honored by both processes).
    """
    override = os.environ.get("SQUID_WATCHDOG_STATE_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_state_path("squid", "cephla")) / "watchdog"


def run_file_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir) / RUN_FILE_NAME if state_dir else default_state_dir() / RUN_FILE_NAME


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_run(state_dir: Optional[Path] = None) -> Optional[dict]:
    try:
        with open(run_file_path(state_dir)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def protocol_run_file_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir or default_state_dir()) / PROTOCOL_RUN_FILE_NAME


def write_protocol_run(
    *,
    run_dir: str,
    manifest_path: str,
    run_name: str,
    pid: int,
    machine: Optional[str] = None,
    state_dir: Optional[Path] = None,
) -> None:
    """Point the watchdog at the fluidics protocol run this process is driving. The run's own
    manifest (status, hold, heartbeat; rewritten by the runner) is the state: this only says where it is.
    Never raises on a write failure, so a breadcrumb problem cannot break a run."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": run_dir,
        "manifest_path": manifest_path,
        "run_name": run_name,
        "machine": machine or socket.gethostname(),
        "pid": pid,
        "written_at": time.time(),
    }
    try:
        _atomic_write_json(protocol_run_file_path(state_dir), record)
    except OSError as e:
        _log.warning(f"Failed to write the fluidics protocol run breadcrumb: {e}")


def read_protocol_run(state_dir: Optional[Path] = None) -> Optional[dict]:
    try:
        with open(protocol_run_file_path(state_dir)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def watchdog_file_path(state_dir: Optional[Path] = None) -> Path:
    return Path(state_dir or default_state_dir()) / WATCHDOG_FILE_NAME


def write_watchdog_heartbeat(
    *, pid: int, poll_interval: float, machine: Optional[str] = None, state_dir: Optional[Path] = None
) -> None:
    """The watchdog's own heartbeat, rewritten every poll: the GUI reads it to tell whether a watchdog is
    watching this state dir. Never raises."""
    record = {
        "schema_version": SCHEMA_VERSION,
        "pid": pid,
        "machine": machine or socket.gethostname(),
        "poll_interval": poll_interval,
        "written_at": time.time(),
    }
    try:
        _atomic_write_json(watchdog_file_path(state_dir), record)
    except OSError as e:
        _log.warning(f"Failed to write the watchdog heartbeat: {e}")


def watchdog_problem(state_dir: Optional[Path] = None, now: Optional[float] = None) -> Optional[str]:
    """None when a watchdog has polled this state dir recently (within 3 polls, at least 60 s); else what is wrong."""
    path = watchdog_file_path(state_dir)
    try:
        with open(path) as f:
            beat = json.load(f)
        written_at = float(beat["written_at"])
        poll_interval = float(beat.get("poll_interval") or 5.0)
    except (OSError, ValueError, KeyError, TypeError):  # missing, unreadable, or not a heartbeat
        return f"the acquisition watchdog is not running (no heartbeat in {path.parent})"
    age = (time.time() if now is None else now) - written_at
    if age > max(60.0, 3 * poll_interval):
        return f"the acquisition watchdog is not running (last heartbeat {int(age // 60)} min ago in {path.parent})"
    return None


class RunStateWriter:
    """Writes/updates the single run.json for the current acquisition."""

    def __init__(self, record: dict, state_dir: Optional[Path] = None):
        self._record = record
        self._state_dir = state_dir
        self._last_beat = 0.0

    @classmethod
    def start(
        cls,
        *,
        experiment_id: str,
        pid: int,
        config_path: Optional[str],
        output_path: str,
        expected: dict,
        machine: Optional[str] = None,
        state_dir: Optional[Path] = None,
    ) -> "RunStateWriter":
        now = time.time()
        record = {
            "schema_version": SCHEMA_VERSION,
            "run_id": uuid.uuid4().hex,
            "experiment_id": experiment_id,
            "machine": machine or socket.gethostname(),
            "pid": pid,
            "config_path": config_path,
            "output_path": output_path,
            "started_at": now,
            "heartbeat_at": now,
            "progress": {},
            "expected": expected,
            "status": "running",
            "reason": None,
            "ended_at": None,
            "stats": None,
        }
        writer = cls(record, state_dir=state_dir)
        writer._flush()
        writer._last_beat = now
        return writer

    @property
    def run_id(self) -> Optional[str]:
        return self._record.get("run_id")

    def beat(self, progress: Optional[dict] = None, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_beat) < HEARTBEAT_INTERVAL_S:
            return
        if progress:
            self._record["progress"] = progress
        self._last_beat = now
        self._record["heartbeat_at"] = now
        self._flush()

    def end(self, reason: str, stats: Optional[dict] = None) -> None:
        self._record["status"] = "ended"
        self._record["reason"] = reason
        self._record["ended_at"] = time.time()
        if stats is not None:
            self._record["stats"] = stats
        self._flush()

    def _flush(self) -> None:
        try:
            _atomic_write_json(run_file_path(self._state_dir), dict(self._record))
        except OSError as e:
            _log.warning(f"Failed to write acquisition run state: {e}")


class NullRunStateWriter:
    """No-op writer used when breadcrumbs are not wired (tests, side paths)."""

    run_id = None

    def beat(self, progress: Optional[dict] = None, force: bool = False) -> None:
        pass

    def end(self, reason: str, stats: Optional[dict] = None) -> None:
        pass
