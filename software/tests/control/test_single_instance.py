"""Tests for the Squid single-instance lock helper."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from control.single_instance import acquire_single_instance_lock


def test_acquire_returns_lock_and_path(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    result = acquire_single_instance_lock(lock_path=lock_path)

    assert result.lock is not None
    assert result.path == lock_path
    assert result.busy is False
    assert Path(lock_path).exists()

    result.lock.unlock()


def test_second_acquire_in_same_process_is_busy(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    first = acquire_single_instance_lock(lock_path=lock_path)
    second = acquire_single_instance_lock(lock_path=lock_path)

    assert first.lock is not None
    assert second.lock is None
    assert second.busy is True
    assert second.path == lock_path

    first.lock.unlock()


def test_release_allows_subsequent_acquire(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    first = acquire_single_instance_lock(lock_path=lock_path)
    assert first.lock is not None
    first.lock.unlock()

    second = acquire_single_instance_lock(lock_path=lock_path)
    assert second.lock is not None

    second.lock.unlock()


def test_stale_lock_from_dead_process_is_reclaimed(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    # Child acquires the lock, prints a marker, then dies without releasing.
    # os._exit() skips Python and Qt destructors, leaving the lock file on
    # disk holding a PID that is no longer alive.
    child_script = textwrap.dedent(
        f"""
        import os, sys
        from qtpy.QtCore import QLockFile

        lock = QLockFile({lock_path!r})
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            sys.exit(2)
        print("acquired", flush=True)
        os._exit(0)
        """
    )

    # Pin QT_API so qtpy in the child selects the same binding as the parent
    # regardless of what other Qt bindings are installed in the environment.
    child_env = {**os.environ, "QT_API": "pyqt5"}
    result = subprocess.run(
        [sys.executable, "-c", child_script],
        capture_output=True,
        text=True,
        timeout=30,
        env=child_env,
    )
    assert (
        result.returncode == 0
    ), f"child exited {result.returncode}; stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "acquired" in result.stdout

    # The lock file is on disk with a dead PID. Acquiring should succeed via
    # QLockFile's PID-based stale-lock recovery.
    acquired = acquire_single_instance_lock(lock_path=lock_path)
    assert acquired.lock is not None

    acquired.lock.unlock()
