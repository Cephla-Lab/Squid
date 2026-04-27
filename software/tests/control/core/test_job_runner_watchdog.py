"""Tests for JobRunner watchdog (unexpected subprocess death detection).

These tests cover the watchdog thread that distinguishes intentional shutdown
from unexpected subprocess death (segfault, SIGKILL, OOM kill) and invokes a
registered handler so an acquisition can abort instead of silently rotting.
"""

import os
import signal
import threading
import time

import pytest

from control.core.job_processing import JobRunner


@pytest.fixture
def runner():
    """Provide an unstarted JobRunner; ensure cleanup even if the test crashes mid-run."""
    r = JobRunner()
    r.daemon = True
    yield r
    if r.is_alive():
        try:
            r.kill()
            r.join(timeout=2.0)
        except Exception:
            pass


# Watchdog runs in a daemon thread; allow it to finish after the sentinel fires.
_WATCHDOG_GRACE_S = 0.3


class TestWatchdogUnexpectedDeath:
    """Verify the watchdog detects unexpected subprocess death and invokes the handler."""

    def test_sigkill_fires_handler_with_negative_exitcode(self, runner):
        handler_fired = threading.Event()
        received_exitcode = []

        def handler(exitcode):
            received_exitcode.append(exitcode)
            handler_fired.set()

        runner.set_unexpected_exit_handler(handler)
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        os.kill(runner.pid, signal.SIGKILL)

        assert handler_fired.wait(timeout=5.0), "Watchdog handler did not fire after SIGKILL"
        assert received_exitcode == [-signal.SIGKILL]


class TestWatchdogIntentionalExit:
    """Verify intentional stop paths (kill/terminate/shutdown) do NOT fire the handler."""

    def test_kill_does_not_fire_handler(self, runner):
        handler_fired = threading.Event()
        runner.set_unexpected_exit_handler(lambda ec: handler_fired.set())
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        runner.kill()
        runner.join(timeout=2.0)
        time.sleep(_WATCHDOG_GRACE_S)

        assert not handler_fired.is_set(), "Handler fired despite intentional kill()"

    def test_terminate_does_not_fire_handler(self, runner):
        handler_fired = threading.Event()
        runner.set_unexpected_exit_handler(lambda ec: handler_fired.set())
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        runner.terminate()
        runner.join(timeout=2.0)
        time.sleep(_WATCHDOG_GRACE_S)

        assert not handler_fired.is_set(), "Handler fired despite intentional terminate()"

    def test_shutdown_does_not_fire_handler(self, runner):
        handler_fired = threading.Event()
        runner.set_unexpected_exit_handler(lambda ec: handler_fired.set())
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        runner.shutdown(timeout_s=2.0)
        time.sleep(_WATCHDOG_GRACE_S)

        assert not handler_fired.is_set(), "Handler fired despite intentional shutdown()"


class TestWatchdogResilience:
    """Verify the watchdog is robust to handler misbehavior and shutdown ordering."""

    def test_handler_exception_does_not_propagate(self, runner):
        # The watchdog daemon thread must catch handler exceptions (it logs them).
        # If propagation happened, the test process would not reach the post-join asserts.
        runner.set_unexpected_exit_handler(lambda ec: (_ for _ in ()).throw(RuntimeError("boom")))
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        os.kill(runner.pid, signal.SIGKILL)
        runner.join(timeout=5.0)
        time.sleep(_WATCHDOG_GRACE_S)

        assert not runner.is_alive()

    def test_intentional_exit_survives_shutdown_cleanup(self, runner):
        """Regression: shutdown() nulls _shutdown_event during cleanup. The intent flag
        must be a separate attribute that survives that nullification, or the watchdog
        could read None and misclassify intentional shutdown as unexpected death.
        """
        handler_fired = threading.Event()
        runner.set_unexpected_exit_handler(lambda ec: handler_fired.set())
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        runner.shutdown(timeout_s=2.0)

        assert runner._intentional_exit is True
        assert runner._shutdown_event is None

        time.sleep(_WATCHDOG_GRACE_S)
        assert not handler_fired.is_set()


class TestPreWarmedAdoption:
    """Document the load-bearing assumption behind the is_alive() check at adoption."""

    def test_is_ready_returns_true_for_dead_subprocess(self, runner):
        """is_ready() reads a multiprocessing.Event the subprocess sets early in run().
        After SIGKILL the Event remains set in shared memory, so is_ready() alone cannot
        distinguish a live runner from a corpse. is_alive() must also be checked before
        adopting a pre-warmed runner.
        """
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)

        os.kill(runner.pid, signal.SIGKILL)
        runner.join(timeout=5.0)

        assert runner.is_ready() is True, "is_ready() should still report True even after death"
        assert runner.is_alive() is False, "is_alive() should report False after death"
