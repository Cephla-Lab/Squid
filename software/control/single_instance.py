"""Per-user single-instance enforcement for the Squid GUI.

Acquires an exclusive lock file at startup so a second launch by the same
user on the same machine can detect and refuse to run. Backed by Qt's
QLockFile, which embeds the holding process's PID and hostname and reclaims
the lock automatically if the recorded PID is no longer alive (stale-lock
recovery).
"""

import os

# Pin the Qt binding before importing qtpy, matching the convention used by
# every other Qt-using module in this codebase.
os.environ["QT_API"] = "pyqt5"

import getpass
from typing import NamedTuple, Optional

from qtpy.QtCore import QDir, QLockFile


class LockAcquireResult(NamedTuple):
    """Result of acquire_single_instance_lock().

    `lock` is the held QLockFile on success, otherwise None. `path` is the
    lock file path, returned in both outcomes so callers can show it in an
    error dialog. `busy` is True when failure was specifically "another
    instance owns the lock" — distinguishing it from permission/path errors,
    which deserve a different message.
    """

    lock: Optional[QLockFile]
    path: str
    busy: bool


def _default_lock_path() -> str:
    return os.path.join(QDir.tempPath(), f"squid-{getpass.getuser()}.lock")


def acquire_single_instance_lock(lock_path: Optional[str] = None) -> LockAcquireResult:
    """Try to acquire the Squid single-instance lock.

    The caller MUST keep the returned QLockFile alive for the app's lifetime;
    QLockFile releases the lock when its destructor runs.

    `lock_path` is for tests; production code calls this with no arguments.
    """
    if lock_path is None:
        lock_path = _default_lock_path()

    lock = QLockFile(lock_path)
    # 0 disables time-based staleness; rely only on PID liveness, so a slow
    # but alive instance is never reclaimed by a wall-clock heuristic.
    lock.setStaleLockTime(0)

    if lock.tryLock(0):
        return LockAcquireResult(lock=lock, path=lock_path, busy=False)

    busy = lock.error() == QLockFile.LockFailedError
    return LockAcquireResult(lock=None, path=lock_path, busy=busy)
