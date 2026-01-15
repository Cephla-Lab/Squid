"""Tests for JobRunner.shutdown() and semaphore leak fix.

These tests verify that shutdown() properly cleans up multiprocessing primitives
(Queue, Event, Value) to prevent semaphore leaks on application exit.
"""

import multiprocessing
import time
from dataclasses import dataclass

import numpy as np
import pytest

import squid.abc
from control.core.job_processing import Job, JobRunner, JobImage, CaptureInfo
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


def make_test_capture_info() -> CaptureInfo:
    """Create a minimal CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            display_color="#FFFFFF",
            camera=1,  # v1.0: camera is int ID
            illumination_settings=IlluminationSettings(
                illumination_channel="BF LED matrix full",
                intensity=50.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=10.0,
                gain_mode=1.0,
            ),
            z_offset_um=0.0,  # v1.0: at channel level
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


def make_slow_job(duration_s: float = 0.1, result_value: str = "done") -> SlowJob:
    """Create a SlowJob with test capture info."""
    return SlowJob(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(),
        duration_s=duration_s,
        result_value=result_value,
    )


class TestJobRunnerShutdown:
    """Tests for shutdown() cleanup behavior."""

    def test_shutdown_clears_queue_references(self):
        """After shutdown, queue references should be None."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Verify queues exist before shutdown
        assert runner._input_queue is not None
        assert runner._output_queue is not None

        runner.shutdown(timeout_s=1.0)

        # After shutdown, references should be cleared
        assert runner._input_queue is None
        assert runner._output_queue is None
        assert runner._shutdown_event is None
        assert runner._pending_count is None

    def test_shutdown_double_call_safe(self):
        """Calling shutdown twice should not crash."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        runner.shutdown(timeout_s=1.0)

        # Second call should be safe (guard against double shutdown)
        runner.shutdown(timeout_s=1.0)  # Should not raise

    def test_output_queue_returns_none_after_shutdown(self):
        """output_queue() should return None after shutdown."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Before shutdown, output_queue returns a Queue
        assert runner.output_queue() is not None

        runner.shutdown(timeout_s=1.0)

        # After shutdown, output_queue returns None
        assert runner.output_queue() is None

    def test_shutdown_terminates_hung_process(self):
        """If process doesn't exit gracefully, shutdown should terminate it."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Dispatch a very long job
        runner.dispatch(make_slow_job(duration_s=10.0))

        # Shutdown with short timeout - process should be terminated
        start = time.time()
        runner.shutdown(timeout_s=0.1)
        elapsed = time.time() - start

        # Should complete quickly (not wait 10s for job)
        assert elapsed < 2.0

        # Process should be dead
        assert not runner.is_alive()

        # References should still be cleaned up
        assert runner._input_queue is None
        assert runner._output_queue is None

    def test_shutdown_allows_graceful_exit(self):
        """Shutdown should wait for process to exit gracefully if time permits."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Wait for process to fully start
        time.sleep(0.5)

        # No jobs dispatched, process should exit quickly
        runner.shutdown(timeout_s=2.0)

        # Process should have exited
        assert not runner.is_alive()

    def test_shutdown_with_pending_jobs(self):
        """Shutdown should clean up even with jobs in queue."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Dispatch multiple jobs
        for _ in range(5):
            runner.dispatch(make_slow_job(duration_s=0.5))

        # Shutdown immediately - some jobs may not complete
        runner.shutdown(timeout_s=0.2)

        # Process should be dead
        assert not runner.is_alive()

        # References should be cleaned up
        assert runner._input_queue is None
        assert runner._output_queue is None

    def test_shutdown_with_zero_timeout(self):
        """Shutdown with timeout=0 should still clean up properly."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Wait for process to start
        time.sleep(0.5)

        # Shutdown with zero timeout
        runner.shutdown(timeout_s=0)

        # References should be cleaned up
        assert runner._input_queue is None
        assert runner._output_queue is None


class TestSemaphoreCleanup:
    """Tests to verify semaphore cleanup behavior."""

    def test_queues_are_closed_before_clearing(self):
        """Verify that close() is called on queues before clearing references."""
        runner = JobRunner()
        runner.daemon = True
        runner.start()

        # Get references to the queues
        input_queue = runner._input_queue
        output_queue = runner._output_queue

        runner.shutdown(timeout_s=1.0)

        # After shutdown, the original queue objects should be closed
        # Attempting to put/get should raise ValueError
        with pytest.raises(ValueError):
            input_queue.put_nowait("test")

        with pytest.raises(ValueError):
            output_queue.get_nowait()

    def test_multiple_runners_cleanup(self):
        """Multiple runners should all clean up properly."""
        runners = []
        for _ in range(3):
            runner = JobRunner()
            runner.daemon = True
            runner.start()
            runners.append(runner)

        # Shutdown all runners
        for runner in runners:
            runner.shutdown(timeout_s=1.0)

        # All should be cleaned up
        for runner in runners:
            assert runner._input_queue is None
            assert runner._output_queue is None
            assert not runner.is_alive()
