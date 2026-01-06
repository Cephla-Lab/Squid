"""Tests for JobRunner.has_pending() and pending count tracking.

These tests verify the fix for the race condition where has_pending() could return
False while jobs were still being processed, causing _finish_jobs() to exit early
and lose the last few images of an acquisition.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pytest

import squid.abc
from control.core.job_processing import Job, JobRunner, JobResult, JobImage, CaptureInfo
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


def make_test_capture_info() -> CaptureInfo:
    """Create a minimal CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            illumination_settings=IlluminationSettings(
                illumination_channels=["BF LED matrix full"],
                intensity={"BF LED matrix full": 50.0},
                z_offset_um=0.0,
            ),
            camera_settings={
                "camera_1": CameraSettings(
                    display_color="#FFFFFF",
                    exposure_time_ms=10.0,
                    gain_mode=1.0,
                )
            },
        ),
        save_directory="/tmp/test",
        file_id="test_0_0",
        region_id="A1",
        fov=0,
        configuration_idx=0,
    )


def make_test_job_image() -> JobImage:
    """Create a minimal JobImage for testing."""
    return JobImage(image_array=np.zeros((10, 10), dtype=np.uint16))


@dataclass
class SlowJob(Job):
    """A job that takes a configurable amount of time to run."""

    duration_s: float = 0.1
    result_value: str = "done"

    def run(self):
        time.sleep(self.duration_s)
        return self.result_value


@dataclass
class NoneResultJob(Job):
    """A job that returns None (like DownsampledViewJob for intermediate FOVs)."""

    duration_s: float = 0.05

    def run(self):
        time.sleep(self.duration_s)
        return None


@dataclass
class FailingJob(Job):
    """A job that raises an exception."""

    exception: Optional[Exception] = field(default_factory=lambda: RuntimeError("Job failed intentionally"))

    def run(self):
        raise self.exception


def make_slow_job(duration_s: float = 0.1, result_value: str = "done") -> SlowJob:
    """Create a SlowJob with test capture info."""
    return SlowJob(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(),
        duration_s=duration_s,
        result_value=result_value,
    )


def make_none_result_job(duration_s: float = 0.05) -> NoneResultJob:
    """Create a NoneResultJob with test capture info."""
    return NoneResultJob(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(),
        duration_s=duration_s,
    )


def make_failing_job(exception: Exception = None) -> FailingJob:
    """Create a FailingJob with test capture info."""
    return FailingJob(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(),
        exception=exception or RuntimeError("Job failed intentionally"),
    )


class TestJobRunnerHasPending:
    """Tests for has_pending() tracking."""

    def test_has_pending_false_initially(self):
        """has_pending() returns False when no jobs are dispatched."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_has_pending_true_after_dispatch(self):
        """has_pending() returns True immediately after dispatch."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            job = make_slow_job(duration_s=1.0)  # Long enough to check
            runner.dispatch(job)

            # Should be True immediately after dispatch
            assert runner.has_pending() is True
        finally:
            runner.shutdown(timeout_s=2.0)

    def test_has_pending_false_after_job_completes(self):
        """has_pending() returns False after job completes."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            job = make_slow_job(duration_s=0.1)
            runner.dispatch(job)

            # Wait for job to complete
            result = runner.output_queue().get(timeout=5.0)
            assert result.result == "done"

            # Give a small buffer for the finally block to decrement counter
            time.sleep(0.05)

            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_has_pending_tracks_multiple_jobs(self):
        """has_pending() correctly tracks multiple jobs."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            # Dispatch 3 jobs
            for i in range(3):
                runner.dispatch(make_slow_job(duration_s=0.1))

            assert runner.has_pending() is True

            # Collect all results
            for _ in range(3):
                runner.output_queue().get(timeout=5.0)

            # Give buffer for counter updates
            time.sleep(0.1)

            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_has_pending_with_none_result_job(self):
        """has_pending() correctly decrements for jobs returning None."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            # Wait for worker process to fully initialize (loads config ~0.5s)
            time.sleep(1.0)

            job = make_none_result_job(duration_s=0.1)
            runner.dispatch(job)

            assert runner.has_pending() is True

            # Job returns None, so nothing in output queue
            # Wait for job to complete
            time.sleep(0.5)

            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_has_pending_with_failing_job(self):
        """has_pending() correctly decrements when job raises exception."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            job = make_failing_job()
            runner.dispatch(job)

            assert runner.has_pending() is True

            # Get the exception result from queue
            result = runner.output_queue().get(timeout=5.0)
            assert result.exception is not None

            # Give buffer for counter update
            time.sleep(0.05)

            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_has_pending_true_while_job_processing(self):
        """has_pending() returns True while job is being processed (not just in queue).

        This is the key test for the race condition fix. Previously, has_pending()
        only checked if the input queue was empty, so it returned False as soon as
        the worker took the job from the queue, even while processing.
        """
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            # Use a slow job so we can check while it's processing
            job = make_slow_job(duration_s=0.5)
            runner.dispatch(job)

            # Wait a bit for worker to take job from queue
            time.sleep(0.1)

            # Job should be processing (not in input queue anymore)
            # But has_pending() should still return True
            assert runner.has_pending() is True

            # Wait for completion
            runner.output_queue().get(timeout=5.0)
            time.sleep(0.05)

            assert runner.has_pending() is False
        finally:
            runner.shutdown(timeout_s=1.0)


class TestDispatchRollback:
    """Tests for dispatch() counter rollback on exception."""

    def test_dispatch_rollback_on_queue_exception(self):
        """Counter is rolled back if put_nowait() fails."""
        runner = JobRunner()
        # Don't start the runner - we'll mock the queue

        def failing_put(job):
            raise RuntimeError("Queue error")

        runner._input_queue.put_nowait = failing_put

        job = make_slow_job()

        # Dispatch should raise and rollback counter
        with pytest.raises(RuntimeError, match="Queue error"):
            runner.dispatch(job)

        # Counter should be 0 (rolled back)
        assert runner.has_pending() is False

    def test_dispatch_success_increments_counter(self):
        """Successful dispatch increments counter."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        try:
            assert runner.has_pending() is False

            runner.dispatch(make_slow_job(duration_s=1.0))
            assert runner._pending_count.value == 1

            runner.dispatch(make_slow_job(duration_s=1.0))
            assert runner._pending_count.value == 2
        finally:
            runner.shutdown(timeout_s=0.1)
