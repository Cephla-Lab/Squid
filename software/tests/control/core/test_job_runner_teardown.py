"""Tests for shutdown_all_job_runners (exit-time reaping of JobRunner children).

main_hcs.py exits via os._exit(), which skips multiprocessing's atexit hook, so
any JobRunner subprocess still alive at that point (its shutdown event never
set — e.g. the exit-time abort join timed out, or the worker died before
_finish_jobs) would be orphaned and leak its queue/event semaphores.
shutdown_all_job_runners() is called right before os._exit() to close that hole.
"""

import multiprocessing
import time

from control.core.job_processing import JobRunner, shutdown_all_job_runners


def _wait_dead(runner, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not runner.is_alive():
            return True
        time.sleep(0.1)
    return not runner.is_alive()


def test_reaps_runner_that_was_never_told_to_stop():
    runner = JobRunner()
    runner.start()
    assert runner.wait_ready(timeout_s=15.0), "job runner subprocess never became ready"
    assert runner.is_alive()

    shutdown_all_job_runners(timeout_s=5.0)

    assert _wait_dead(runner), "job runner still alive after shutdown_all_job_runners()"
    assert not any(isinstance(p, JobRunner) for p in multiprocessing.active_children())


def test_noop_when_no_runners():
    shutdown_all_job_runners(timeout_s=1.0)


def test_safe_after_runner_already_shut_down():
    runner = JobRunner()
    runner.start()
    assert runner.wait_ready(timeout_s=15.0)
    runner.shutdown(timeout_s=5.0)
    assert _wait_dead(runner)

    shutdown_all_job_runners(timeout_s=2.0)
    assert not any(isinstance(p, JobRunner) for p in multiprocessing.active_children())
