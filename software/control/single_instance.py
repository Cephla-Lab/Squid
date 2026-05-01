"""Per-user single-instance enforcement for the Squid GUI.

Acquires an exclusive lock file at startup so a second launch by the same
user on the same machine can detect and refuse to run. Backed by Qt's
QLockFile, which embeds the holding process's PID and hostname and reclaims
the lock automatically if the recorded PID is no longer alive (stale-lock
recovery).
"""

import getpass
import os
from typing import Optional, Tuple

from qtpy.QtCore import QDir, QLockFile


def _default_lock_path() -> str:
    return os.path.join(QDir.tempPath(), f"squid-{getpass.getuser()}.lock")


def acquire_single_instance_lock(
    lock_path: Optional[str] = None,
) -> Tuple[Optional[QLockFile], str]:
    """Try to acquire the Squid single-instance lock.

    Returns the held QLockFile and its path on success, or (None, path) if
    another instance owns the lock — the path is returned in both cases so
    the caller can show it in an error dialog as a manual escape hatch.

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
        return lock, lock_path
    return None, lock_path
