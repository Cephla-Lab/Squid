"""Tests for the Squid single-instance lock helper."""

import subprocess
import sys
import textwrap
from pathlib import Path

from control.single_instance import acquire_single_instance_lock


def test_acquire_returns_lock_and_path(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    lock, returned_path = acquire_single_instance_lock(lock_path=lock_path)

    assert lock is not None
    assert returned_path == lock_path
    assert Path(lock_path).exists()

    lock.unlock()


def test_second_acquire_in_same_process_returns_none(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    first_lock, _ = acquire_single_instance_lock(lock_path=lock_path)
    second_lock, second_path = acquire_single_instance_lock(lock_path=lock_path)

    assert first_lock is not None
    assert second_lock is None
    assert second_path == lock_path

    first_lock.unlock()


def test_release_allows_subsequent_acquire(tmp_path):
    lock_path = str(tmp_path / "squid-test.lock")

    first_lock, _ = acquire_single_instance_lock(lock_path=lock_path)
    assert first_lock is not None
    first_lock.unlock()

    second_lock, _ = acquire_single_instance_lock(lock_path=lock_path)
    assert second_lock is not None

    second_lock.unlock()


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

    result = subprocess.run(
        [sys.executable, "-c", child_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        result.returncode == 0
    ), f"child exited {result.returncode}; stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "acquired" in result.stdout

    # The lock file is on disk with a dead PID. Acquiring should succeed via
    # QLockFile's PID-based stale-lock recovery.
    lock, _ = acquire_single_instance_lock(lock_path=lock_path)
    assert lock is not None

    lock.unlock()
