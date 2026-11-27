"""A run whose last save fails ends "completed_with_errors", even when that failure's result is still on its way.

A JobRunner puts each job's result on a multiprocessing.Queue with put_nowait(), which only hands it to the queue's
feeder thread, then stops counting the job as pending. So the run's last result can still be in the feeder when
has_pending() turns False. _finish_jobs() reads only what is already there and then closes the queue, so a failure
still in transit was never counted: the run ended "completed". (So did a failure whose error the feeder thread could
not pickle: it drops that result.) The runner now counts each failed job itself, before the result is queued, and
_finish_jobs() counts every failure whose result never arrived.
"""

import collections
import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

import squid.logging
from control.core.job_processing import Job, JobResult, JobRunner, ZarrWriteResult
from control.core.multi_point_worker import MultiPointWorker
from tests.control.core.test_job_runner_pending import (
    make_failing_job,
    make_slow_job,
    make_test_capture_info,
    make_test_job_image,
)

# How long the in-transit failure takes to pickle, so how long it stays in the runner's feeder thread after the job
# stopped being pending. _finish_jobs() polls has_pending() every 0.1 s, so 1 s keeps it in transit with room to spare.
PICKLE_DELAY_S = 1.0


class SlowToSendError(RuntimeError):
    """Takes PICKLE_DELAY_S to pickle. A multiprocessing.Queue pickles in its feeder thread, after put_nowait() has
    returned, so a result holding this error is still in transit for that long. It arrives as a plain RuntimeError."""

    def __reduce__(self):
        time.sleep(PICKLE_DELAY_S)
        return (RuntimeError, self.args)


@dataclass
class FailsSlowToSendJob(Job):
    """Raises in the subprocess (the error is made there, so pickling it is what lags, not dispatching the job)."""

    def run(self):
        raise SlowToSendError("the save failed")


class UnpicklableError(RuntimeError):
    def __reduce__(self):
        raise TypeError("this error cannot be pickled")


@dataclass
class FailsUnpicklablyJob(Job):
    """Raises an error the output queue's feeder thread cannot pickle, so it drops the result: it never arrives."""

    def run(self):
        raise UnpicklableError("the save failed")


class _FakeRunner:
    """A job runner with no job pending, that reports `failed` failed jobs, and whose output queue holds `results`.
    `arrives_after_count` is put on the queue the moment the worker reads the failure count."""

    def __init__(self, failed, results=(), arrives_after_count=()):
        self._failed = failed
        self._queue = queue.Queue()
        for r in results:
            self._queue.put(r)
        self._arrives_after_count = list(arrives_after_count)
        self.shut_down = False

    def has_pending(self):
        return False

    def output_queue(self):
        return self._queue

    def failed_job_count(self):
        for r in self._arrives_after_count:
            self._queue.put(r)
        self._arrives_after_count = []
        return self._failed

    def shutdown(self, timeout_s=1.0):
        self.shut_down = True

    def kill(self):
        raise AssertionError("no job is pending, so nothing should be killed")


def _worker(job_runners):
    """A worker with only what _finish_jobs() and _compute_end_reason() use: a run that ended normally."""
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = squid.logging.get_logger("test_job_runner_failure_count")
    w._job_runners = job_runners
    w._job_failures_received = collections.Counter()
    w._acquisition_error_count = 0
    w.callbacks = MagicMock()
    w._slack_notifier = None
    w._backpressure = MagicMock()
    w._run_state_fatal = False
    w._abort_cause = None
    w.abort_requested_fn = lambda: False
    return w


def _failure(job_id="last-save"):
    return JobResult(job_id=job_id, result=None, exception=RuntimeError("the save failed"))


# --- the worker's count, with the feeder lag simulated ----------------------------------------------------------------


def test_a_last_failure_still_in_transit_is_counted():
    """The runner reports one failed job, but its result is not readable yet (still in the feeder thread)."""
    w = _worker([(Job, _FakeRunner(failed=1))])

    w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 1
    assert w._compute_end_reason() == "completed_with_errors"


def test_a_failure_whose_result_arrived_is_counted_once():
    w = _worker([(Job, _FakeRunner(failed=1, results=[_failure()]))])

    w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 1


def test_a_failure_whose_result_arrives_during_shutdown_is_counted_once():
    """The result lands after the failure count was read, and the last drain (after shutdown starts) reads it."""
    w = _worker([(Job, _FakeRunner(failed=1, arrives_after_count=[_failure()]))])

    w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 1


def test_failures_in_transit_are_counted_for_each_runner():
    """One runner's failure arrived; the other's two are still in transit."""
    arrived = _FakeRunner(failed=1, results=[_failure("a")])
    in_transit = _FakeRunner(failed=2)
    w = _worker([(Job, arrived), (FailsSlowToSendJob, in_transit)])

    w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 3
    assert arrived.shut_down and in_transit.shut_down


def test_a_run_with_no_failures_still_ends_completed():
    w = _worker([(Job, _FakeRunner(failed=0))])

    w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 0
    assert w._compute_end_reason() == "completed"


def test_every_result_in_one_drain_is_summarized(caplog):
    """Two failures and then a success come off the queue in the same drain. Each is summarized: both failures are
    counted and logged (the second one too, although the drain already saw a failure), and the success still tells the
    viewer its frame is written."""
    frame = ZarrWriteResult(fov=3, time_point=0, z_index=1, channel_name="BF", region_idx=2)
    results = [_failure("save-a"), _failure("save-b"), JobResult(job_id="save-c", result=frame, exception=None)]
    w = _worker([(Job, _FakeRunner(failed=2, results=results))])

    with caplog.at_level(logging.ERROR):
        w._finish_jobs(timeout_s=5)

    assert w._acquisition_error_count == 2
    assert w._compute_end_reason() == "completed_with_errors"
    assert "save-a" in caplog.text and "save-b" in caplog.text
    w.callbacks.signal_zarr_frame_written.assert_called_once_with(3, 0, 1, "BF", 2)


# --- a failure count whose lock is never released ---------------------------------------------------------------------


@contextlib.contextmanager
def _lock_held_elsewhere(counter):
    """Hold `counter`'s lock in another thread for as long as the block runs, as a runner that _finish_jobs() killed
    while it was counting a failure leaves it held. (The thread stays alive so that no other thread can reuse its
    ident and pass the lock's owner check; it releases the lock when the block ends, so nothing stays blocked.)"""
    taken, done = threading.Event(), threading.Event()

    def hold():
        with counter.get_lock():
            taken.set()
            done.wait()

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert taken.wait(5)
    try:
        yield
    finally:
        done.set()
        holder.join(5)


def _call_within(fn, timeout_s):
    """Return fn(), failing instead of hanging if it is still blocked after timeout_s."""
    outcome = {}

    def call():
        try:
            outcome["value"] = fn()
        except BaseException as e:
            outcome["error"] = e

    caller = threading.Thread(target=call, daemon=True)
    caller.start()
    caller.join(timeout_s)
    assert not caller.is_alive(), f"still blocked after {timeout_s} s"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def test_failed_job_count_returns_when_its_lock_is_never_released(caplog):
    runner = JobRunner()
    runner._failed_job_count.value = 2

    with _lock_held_elsewhere(runner._failed_job_count), caplog.at_level(logging.WARNING):
        assert _call_within(runner.failed_job_count, timeout_s=5) == 2

    assert "without its lock" in caplog.text


# --- a real JobRunner subprocess --------------------------------------------------------------------------------------


def _wait_until_not_pending(runner, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while runner.has_pending():
        assert time.monotonic() < deadline, "the job runner never finished its jobs"
        time.sleep(0.01)


def _record_shutdown(runner):
    """Return an Event that is set once runner.shutdown() has returned. Call it after runner.start() (spawn pickles
    the runner, and this puts a local function on it).

    _finish_jobs() shuts its runners down in background threads, and shutdown() waits for the runner's process to end.
    A test waits for that shutdown to return, not on the process itself: when two threads wait on one
    multiprocessing.Process, one thread's os.waitpid() reaps it and the other's fails with ECHILD, which Popen.poll()
    takes for "still running" until the first thread stores the exit code. Until then join() and kill() return at once,
    and is_alive() says True although the process has ended.
    """
    shut_down = threading.Event()
    shutdown = runner.shutdown

    def shutdown_then_record(timeout_s=1.0):
        try:
            shutdown(timeout_s)
        finally:
            shut_down.set()

    runner.shutdown = shutdown_then_record
    return shut_down


def _ended_by_its_shutdown(runner, shut_down, timeout_s=20.0):
    """Whether _finish_jobs()' shutdown of `runner` returned within timeout_s with the runner's process ended. If not,
    kill the process, so that none is left behind."""
    ended = shut_down.wait(timeout_s) and not runner.is_alive()
    if not ended:
        runner.kill()
        runner.join(timeout=5)
    return ended


def test_the_runner_counts_its_failed_jobs():
    runner = JobRunner()
    runner.start()
    try:
        assert runner.wait_ready(timeout_s=30)
        assert runner.failed_job_count() == 0

        runner.dispatch(make_slow_job(duration_s=0.0))
        runner.dispatch(make_failing_job())
        runner.dispatch(make_failing_job())
        _wait_until_not_pending(runner)

        assert runner.failed_job_count() == 2
    finally:
        runner.shutdown(timeout_s=5.0)
    assert not runner.is_alive()


@pytest.mark.parametrize(
    "job_class",
    [
        # The failure's result is still being pickled in the runner's feeder thread when has_pending() turns False,
        # and the worker's last drain comes before it arrives.
        FailsSlowToSendJob,
        # The feeder thread cannot pickle the failure's result, so it drops it.
        FailsUnpicklablyJob,
    ],
)
def test_finish_jobs_counts_a_real_runners_last_failure_that_never_arrives(job_class):
    """A real spawned runner whose last job raises."""
    runner = JobRunner()
    runner.start()
    shut_down = _record_shutdown(runner)
    try:
        assert runner.wait_ready(timeout_s=30)
        w = _worker([(job_class, runner)])
        runner.dispatch(job_class(capture_info=make_test_capture_info(), capture_image=make_test_job_image()))

        w._finish_jobs(timeout_s=10)

        assert w._acquisition_error_count == 1
        assert w._compute_end_reason() == "completed_with_errors"
    finally:
        # _finish_jobs() shuts the runner down in the background: wait for that shutdown, not on the process.
        ended = _ended_by_its_shutdown(runner, shut_down)
    assert ended, "_finish_jobs() did not shut the runner's process down"


def test_finish_jobs_returns_when_a_real_runners_failure_count_lock_is_never_released(caplog):
    """A real runner's last job failed and its result never arrives; then the lock of its failure count is left held,
    as a kill while it counts a failure leaves it. _finish_jobs() still returns, counts the failure, and logs both."""
    runner = JobRunner()
    runner.start()
    shut_down = _record_shutdown(runner)
    try:
        assert runner.wait_ready(timeout_s=30)
        w = _worker([(FailsUnpicklablyJob, runner)])
        runner.dispatch(FailsUnpicklablyJob(capture_info=make_test_capture_info(), capture_image=make_test_job_image()))
        _wait_until_not_pending(runner)

        with _lock_held_elsewhere(runner._failed_job_count), caplog.at_level(logging.WARNING):
            _call_within(lambda: w._finish_jobs(timeout_s=10), timeout_s=20)

        assert w._acquisition_error_count == 1
        assert w._compute_end_reason() == "completed_with_errors"
        assert "without its lock" in caplog.text
        assert "never arrived" in caplog.text
    finally:
        ended = _ended_by_its_shutdown(runner, shut_down)
    assert ended, "_finish_jobs() did not shut the runner's process down"
