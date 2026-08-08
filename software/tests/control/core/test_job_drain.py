"""Tests for progress-based job draining and JobRunner subprocess death detection.

These verify the fix for two failure modes found in the 2026-07-12 backpressure
investigation:

1. ``_finish_jobs`` used a fixed timeout to wait for pending jobs at the end of an
   acquisition (natural completion AND abort). Under saturation the pending count
   equals the backpressure job limit by construction, so whenever
   ``pending × per-job save time`` exceeded the fixed timeout the tail of the
   acquisition was killed and its images silently lost — even though jobs were
   completing steadily the whole time. The drain deadline must be progress-based:
   abandon only when NO job completes for the stall timeout.

2. A dead JobRunner subprocess was undetectable. Backpressure counters are only
   decremented by the subprocess, so its death froze them at the limit and the
   acquisition crawled at one throttle-timeout per FOV, presenting as "stuck with
   jobs queue full". Dead runners with pending jobs must be detectable so the
   worker can abort with a clear error instead.
"""

import time
from dataclasses import dataclass

import numpy as np
import pytest

import squid.abc
from control.core.job_processing import (
    CaptureInfo,
    Job,
    JobImage,
    JobRunner,
    drain_runners,
    find_dead_runners,
)
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

    def run(self):
        time.sleep(self.duration_s)
        return "done"


@dataclass
class HangingJob(Job):
    """A job that runs much longer than any test timeout (simulates a stuck save)."""

    duration_s: float = 60.0

    def run(self):
        time.sleep(self.duration_s)
        return "done"


def make_job(job_type, duration_s):
    return job_type(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(),
        duration_s=duration_s,
    )


def start_runner() -> JobRunner:
    runner = JobRunner()
    runner.daemon = True
    runner.start()
    assert runner.wait_ready(timeout_s=10.0), "JobRunner subprocess never became ready"
    return runner


def wait_until(predicate, timeout_s, interval_s=0.05):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


class TestPendingCount:
    def test_pending_count_tracks_dispatch_and_completion(self):
        """pending_count() reports dispatched-but-incomplete jobs, reaching 0 when drained."""
        runner = start_runner()
        try:
            for _ in range(3):
                runner.dispatch(make_job(SlowJob, duration_s=0.2))
            assert runner.pending_count() == 3

            assert wait_until(lambda: runner.pending_count() == 0, timeout_s=15.0)
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_pending_count_zero_after_shutdown(self):
        """pending_count() is safe to call after shutdown() clears the shared value."""
        runner = start_runner()
        runner.shutdown(timeout_s=2.0)
        assert runner.pending_count() == 0


class TestDrainRunners:
    def test_drains_fully_when_progress_is_slower_than_stall_timeout(self):
        """THE regression case: total drain time exceeds the stall timeout, but jobs
        complete steadily — nothing may be abandoned.

        With the old fixed-deadline behavior (timeout_s=1), the last jobs of this
        queue would have been killed and lost.
        """
        runner = start_runner()
        try:
            for _ in range(6):
                runner.dispatch(make_job(SlowJob, duration_s=0.4))

            start = time.monotonic()
            result = drain_runners([(SlowJob, runner)], stall_timeout_s=1.0)
            elapsed = time.monotonic() - start

            assert result.total_abandoned == 0
            assert result.abandoned == {}
            assert result.dead == []
            assert runner.pending_count() == 0
            # It must have kept waiting well past the stall timeout (a fixed
            # 1s deadline would have returned early and abandoned jobs).
            assert elapsed > 1.0
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_abandons_pending_jobs_after_stall(self):
        """A job making no progress for the stall timeout is abandoned and its runner killed."""
        runner = start_runner()
        try:
            runner.dispatch(make_job(HangingJob, duration_s=60.0))
            # Give the subprocess a moment to pick the job up.
            time.sleep(0.3)

            start = time.monotonic()
            result = drain_runners([(HangingJob, runner)], stall_timeout_s=0.7)
            elapsed = time.monotonic() - start

            assert result.abandoned == {"HangingJob": 1}
            assert result.total_abandoned == 1
            # Should return shortly after the stall timeout, not wait for the job.
            assert elapsed < 10.0
            # The stalled runner is killed so shutdown cannot hang on it.
            assert wait_until(lambda: not runner.is_alive(), timeout_s=5.0)
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_abandons_dead_runner_immediately(self):
        """A runner whose subprocess died is abandoned without waiting out the stall timeout."""
        runner = start_runner()
        try:
            for _ in range(3):
                runner.dispatch(make_job(HangingJob, duration_s=60.0))
            time.sleep(0.3)

            runner.kill()
            assert wait_until(lambda: not runner.is_alive(), timeout_s=5.0)

            start = time.monotonic()
            result = drain_runners([(HangingJob, runner)], stall_timeout_s=30.0)
            elapsed = time.monotonic() - start

            # Must not wait anywhere near the 30s stall timeout.
            assert elapsed < 10.0
            assert result.abandoned == {"HangingJob": 3}
            assert result.dead == ["HangingJob"]
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_no_pending_jobs_returns_immediately(self):
        """Draining runners with nothing pending is a no-op."""
        runner = start_runner()
        try:
            start = time.monotonic()
            result = drain_runners([(SlowJob, runner)], stall_timeout_s=5.0)
            elapsed = time.monotonic() - start

            assert result.total_abandoned == 0
            assert elapsed < 2.0
        finally:
            runner.shutdown(timeout_s=1.0)


class TestFindDeadRunners:
    def test_alive_runner_with_pending_jobs_is_not_reported(self):
        runner = start_runner()
        try:
            runner.dispatch(make_job(SlowJob, duration_s=1.0))
            assert find_dead_runners([(SlowJob, runner)]) == []
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_dead_runner_with_pending_jobs_is_reported(self):
        runner = start_runner()
        try:
            runner.dispatch(make_job(HangingJob, duration_s=60.0))
            time.sleep(0.2)
            runner.kill()
            assert wait_until(lambda: not runner.is_alive(), timeout_s=5.0)

            dead = find_dead_runners([(HangingJob, runner)])
            assert dead == [(HangingJob, runner)]
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_dead_runner_without_pending_jobs_is_not_reported(self):
        """A runner that exited with nothing pending is not an emergency."""
        runner = start_runner()
        try:
            runner.kill()
            assert wait_until(lambda: not runner.is_alive(), timeout_s=5.0)
            assert find_dead_runners([(SlowJob, runner)]) == []
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_none_runner_entries_are_skipped(self):
        assert find_dead_runners([(SlowJob, None)]) == []
