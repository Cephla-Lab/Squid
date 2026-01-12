"""Tests for acquisition backpressure/throttling.

These tests verify the BackpressureController and JobRunner integration for
preventing RAM exhaustion when acquisition speed exceeds disk write speed.
"""

import time
from dataclasses import dataclass

import numpy as np
import pytest

import squid.abc
from control.core.backpressure import BackpressureController, BackpressureStats
from control.core.job_processing import (
    Job,
    JobRunner,
    JobImage,
    CaptureInfo,
    DownsampledViewJob,
)
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


def make_test_capture_info(region_id: str = "A1", fov: int = 0, z_index: int = 0, config_idx: int = 0) -> CaptureInfo:
    """Create a minimal CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=z_index,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            illumination_settings=IlluminationSettings(
                illumination_channels=["LED"],
                intensity={"LED": 50.0},
                z_offset_um=0.0,
            ),
            camera_settings={
                "main": CameraSettings(
                    exposure_time_ms=10.0,
                    gain_mode=1.0,
                )
            },
        ),
        save_directory="/tmp/test",
        file_id=f"test_{fov}_{z_index}",
        region_id=region_id,
        fov=fov,
        configuration_idx=config_idx,
    )


def make_test_job_image(size_bytes: int = 1000) -> JobImage:
    """Create a JobImage with specified approximate size (uint16 array)."""
    side = int(np.sqrt(max(1, size_bytes // 2)))
    return JobImage(image_array=np.zeros((side, side), dtype=np.uint16))


@dataclass
class SlowJob(Job):
    """A job that takes a configurable amount of time to run."""

    duration_s: float = 0.1
    result_value: str = "done"

    def run(self):
        time.sleep(self.duration_s)
        return self.result_value


def make_slow_job(duration_s: float = 0.1, result_value: str = "done", size_bytes: int = 1000) -> SlowJob:
    """Create a SlowJob with test capture info."""
    return SlowJob(
        capture_info=make_test_capture_info(),
        capture_image=make_test_job_image(size_bytes),
        duration_s=duration_s,
        result_value=result_value,
    )


def make_downsampled_view_job(
    well_id: str = "A1",
    fov_index: int = 0,
    total_fovs: int = 1,
    channel_idx: int = 0,
    total_channels: int = 1,
    z_index: int = 0,
    total_z_levels: int = 1,
    size_bytes: int = 10000,
) -> DownsampledViewJob:
    """Create a DownsampledViewJob for testing."""
    return DownsampledViewJob(
        capture_info=make_test_capture_info(region_id=well_id, fov=fov_index, z_index=z_index, config_idx=channel_idx),
        capture_image=make_test_job_image(size_bytes),
        well_id=well_id,
        well_row=0,
        well_col=0,
        fov_index=fov_index,
        total_fovs_in_well=total_fovs,
        channel_idx=channel_idx,
        total_channels=total_channels,
        z_index=z_index,
        total_z_levels=total_z_levels,
        channel_name=f"Channel_{channel_idx}",
        fov_position_in_well=(0.0, 0.0),
        overlap_pixels=(0, 0, 0, 0),
        pixel_size_um=1.0,
        target_resolutions_um=[10.0],
        plate_resolution_um=10.0,
        output_dir="/tmp/test",
        channel_names=[f"Channel_{i}" for i in range(total_channels)],
        skip_saving=True,
    )


class TestBackpressureController:
    """Tests for BackpressureController in isolation."""

    def test_initial_state(self):
        """Controller starts with zero pending jobs and bytes."""
        controller = BackpressureController(max_jobs=10, max_mb=100.0)

        assert controller.get_pending_jobs() == 0
        assert controller.get_pending_mb() == 0.0
        assert controller.should_throttle() is False
        assert controller.enabled is True

    def test_disabled_controller_never_throttles(self):
        """Disabled controller never reports throttling needed."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001, enabled=False)

        # Manually set counters high (simulating external tracking)
        with controller._pending_jobs.get_lock():
            controller._pending_jobs.value = 100
        with controller._pending_bytes.get_lock():
            controller._pending_bytes.value = 1024 * 1024 * 1024  # 1 GB

        assert controller.should_throttle() is False

    def test_throttle_triggers_at_job_limit(self):
        """Throttling triggers when job count reaches limit."""
        controller = BackpressureController(max_jobs=5, max_mb=1000.0)

        # Add jobs up to limit
        for i in range(5):
            controller.job_dispatched(1000)

        assert controller.get_pending_jobs() == 5
        assert controller.should_throttle() is True

    def test_throttle_triggers_at_byte_limit(self):
        """Throttling triggers when byte count reaches limit."""
        controller = BackpressureController(max_jobs=100, max_mb=10.0)

        # Add bytes up to limit (10 MB = 10 * 1024 * 1024 bytes)
        controller.job_dispatched(10 * 1024 * 1024)

        assert controller.get_pending_jobs() == 1
        assert controller.get_pending_mb() >= 10.0
        assert controller.should_throttle() is True

    def test_throttle_triggers_on_either_limit(self):
        """Throttling triggers if EITHER limit is exceeded."""
        # Test job limit exceeded
        controller1 = BackpressureController(max_jobs=2, max_mb=1000.0)
        controller1.job_dispatched(100)
        controller1.job_dispatched(100)
        assert controller1.should_throttle() is True

        # Test byte limit exceeded
        controller2 = BackpressureController(max_jobs=100, max_mb=0.001)
        controller2.job_dispatched(2000)  # 2000 bytes > 0.001 MB
        assert controller2.should_throttle() is True

    def test_get_stats(self):
        """get_stats() returns accurate BackpressureStats."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(5 * 1024 * 1024)  # 5 MB
        controller.job_dispatched(5 * 1024 * 1024)  # 5 MB

        stats = controller.get_stats()

        assert isinstance(stats, BackpressureStats)
        assert stats.pending_jobs == 2
        assert abs(stats.pending_bytes_mb - 10.0) < 0.1
        assert stats.max_pending_jobs == 10
        assert stats.max_pending_mb == 500.0
        assert stats.is_throttled is False

    def test_reset_clears_counters(self):
        """reset() clears all pending counters."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(10 * 1024 * 1024)
        controller.job_dispatched(10 * 1024 * 1024)

        assert controller.get_pending_jobs() == 2

        controller.reset()

        assert controller.get_pending_jobs() == 0
        assert controller.get_pending_mb() == 0.0

    def test_wait_for_capacity_returns_immediately_when_not_throttled(self):
        """wait_for_capacity() returns True immediately when not throttled."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        start = time.time()
        result = controller.wait_for_capacity()
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.1  # Should be nearly instant

    def test_wait_for_capacity_returns_immediately_when_disabled(self):
        """wait_for_capacity() returns True immediately when disabled, even if limits exceeded."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001, enabled=False)

        # Manually set counters high
        with controller._pending_jobs.get_lock():
            controller._pending_jobs.value = 100

        start = time.time()
        result = controller.wait_for_capacity()
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.1

    def test_wait_for_capacity_timeout(self):
        """wait_for_capacity() returns False after timeout."""
        controller = BackpressureController(max_jobs=1, max_mb=500.0, timeout_s=0.3)

        # Exceed limit
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)

        start = time.time()
        result = controller.wait_for_capacity()
        elapsed = time.time() - start

        assert result is False
        assert 0.2 < elapsed < 0.5  # Should timeout around 0.3s

    def test_wait_for_capacity_releases_when_capacity_available(self):
        """wait_for_capacity() returns when job completes and signals event."""
        controller = BackpressureController(max_jobs=2, max_mb=500.0, timeout_s=5.0)

        # Exceed limit (3 jobs, limit is 2)
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)
        assert controller.should_throttle() is True
        assert controller.get_pending_jobs() == 3

        # Simulate job completion in background thread (decrement from 3 to 1)
        def complete_jobs():
            time.sleep(0.2)
            with controller._pending_jobs.get_lock():
                controller._pending_jobs.value -= 2  # Go from 3 to 1 (below threshold)
            controller.capacity_event.set()

        import threading

        thread = threading.Thread(target=complete_jobs)
        thread.start()

        start = time.time()
        result = controller.wait_for_capacity()
        elapsed = time.time() - start

        thread.join()

        assert result is True
        assert elapsed < 1.0  # Should release quickly after job completion

    def test_close_releases_resources(self):
        """close() releases multiprocessing resources to avoid semaphore leaks."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        # Verify resources exist before close
        assert controller._pending_jobs is not None
        assert controller._pending_bytes is not None
        assert controller._capacity_event is not None

        controller.close()

        # Verify resources are released after close
        assert controller._pending_jobs is None
        assert controller._pending_bytes is None
        assert controller._capacity_event is None

    def test_close_is_idempotent(self):
        """close() can be called multiple times safely."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        # First close
        controller.close()
        assert controller._pending_jobs is None

        # Second close should not raise
        controller.close()
        assert controller._pending_jobs is None

        # Third close should also be safe
        controller.close()


def _create_runner_with_backpressure(controller: BackpressureController) -> JobRunner:
    """Create a JobRunner connected to a BackpressureController."""
    return JobRunner(
        bp_pending_jobs=controller.pending_jobs_value,
        bp_pending_bytes=controller.pending_bytes_value,
        bp_capacity_event=controller.capacity_event,
    )


class TestJobRunnerBackpressureTracking:
    """Tests for JobRunner backpressure tracking integration."""

    def test_dispatch_increments_backpressure_counters(self):
        """dispatch() increments both pending_jobs and pending_bytes."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            job = make_slow_job(duration_s=1.0, size_bytes=20000)
            runner.dispatch(job)

            assert controller.get_pending_jobs() == 1
            assert controller.get_pending_mb() > 0
        finally:
            runner.shutdown(timeout_s=0.5)

    def test_job_completion_decrements_backpressure_counters(self):
        """Job completion decrements backpressure counters and signals event."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            job = make_slow_job(duration_s=0.1, size_bytes=10000)
            runner.dispatch(job)

            assert controller.get_pending_jobs() == 1

            runner.output_queue().get(timeout=5.0)
            time.sleep(0.1)

            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() == 0.0
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_dispatch_rollback_on_failure(self):
        """Backpressure counters are rolled back if dispatch fails."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        # Don't start the runner - mock the queue to fail
        runner._input_queue.put_nowait = lambda job: (_ for _ in ()).throw(RuntimeError("Queue error"))

        with pytest.raises(RuntimeError, match="Queue error"):
            runner.dispatch(make_slow_job(size_bytes=10000))

        assert controller.get_pending_jobs() == 0
        assert controller.get_pending_mb() == 0.0

    def test_no_backpressure_tracking_without_shared_values(self):
        """JobRunner works normally when backpressure values not provided."""
        runner = JobRunner()
        runner.start()

        try:
            time.sleep(0.5)
            runner.dispatch(make_slow_job(duration_s=0.1))
            result = runner.output_queue().get(timeout=5.0)
            assert result.result == "done"
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_dispatch_handles_none_capture_image(self):
        """dispatch() handles jobs with None capture_image gracefully."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            job = SlowJob(
                capture_info=make_test_capture_info(),
                capture_image=None,
                duration_s=0.1,
                result_value="done",
            )
            runner.dispatch(job)

            assert controller.get_pending_jobs() == 1
            assert controller.get_pending_mb() == 0.0

            result = runner.output_queue().get(timeout=5.0)
            time.sleep(0.1)

            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() == 0.0
            assert result.result == "done"
        finally:
            runner.shutdown(timeout_s=1.0)


class TestDownsampledViewJobBackpressure:
    """Tests for DownsampledViewJob special backpressure handling.

    DownsampledViewJob holds images in accumulator until well completes,
    so bytes should not be decremented until the final FOV.
    """

    def test_intermediate_fov_does_not_decrement_bytes(self):
        """Intermediate FOVs should NOT decrement bytes (still in accumulator)."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            job = make_downsampled_view_job(fov_index=0, total_fovs=2, size_bytes=10000)
            runner.dispatch(job)

            assert controller.get_pending_mb() > 0
            time.sleep(0.5)

            # Job count decrements, but bytes stay (image in accumulator)
            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() > 0
        finally:
            runner.shutdown(timeout_s=1.0)
            DownsampledViewJob.clear_accumulators()

    def test_final_fov_decrements_all_accumulated_bytes(self):
        """Final FOV should decrement ALL accumulated bytes for the well."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            runner.dispatch(make_downsampled_view_job(fov_index=0, total_fovs=2, size_bytes=10000))
            time.sleep(0.3)
            runner.dispatch(make_downsampled_view_job(fov_index=1, total_fovs=2, size_bytes=10000))

            runner.output_queue().get(timeout=5.0)
            time.sleep(0.2)

            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() == 0.0
        finally:
            runner.shutdown(timeout_s=1.0)
            DownsampledViewJob.clear_accumulators()

    def test_final_fov_with_multiple_channels_and_z(self):
        """Final FOV must be last FOV + last channel + last z-level."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)

            # 2 FOVs x 2 channels x 2 z-levels = 8 images total
            for fov in range(2):
                for ch in range(2):
                    for z in range(2):
                        job = make_downsampled_view_job(
                            fov_index=fov,
                            total_fovs=2,
                            channel_idx=ch,
                            total_channels=2,
                            z_index=z,
                            total_z_levels=2,
                            size_bytes=5000,
                        )
                        runner.dispatch(job)
                        time.sleep(0.1)

            runner.output_queue().get(timeout=10.0)
            time.sleep(0.3)

            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() == 0.0
        finally:
            runner.shutdown(timeout_s=2.0)
            DownsampledViewJob.clear_accumulators()


class TestDownsampledViewJobExceptionHandling:
    """Tests for DownsampledViewJob exception handling during backpressure tracking.

    When a DownsampledViewJob throws an exception on the final FOV, the accumulator
    is still cleared (via finally block), so bytes must still be decremented.
    """

    def test_final_fov_exception_still_decrements_bytes(self):
        """When final FOV throws exception, bytes should still decrement (accumulator cleared)."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            runner.dispatch(make_downsampled_view_job(fov_index=0, total_fovs=2, size_bytes=10000))
            time.sleep(0.3)
            assert controller.get_pending_mb() > 0

            runner.dispatch(make_downsampled_view_job(fov_index=1, total_fovs=2, size_bytes=10000))

            try:
                runner.output_queue().get(timeout=5.0)
            except Exception:
                pass
            time.sleep(0.3)

            # Bytes decrement regardless of success/exception (based on indices, not result)
            assert controller.get_pending_jobs() == 0
            assert controller.get_pending_mb() == 0.0
        finally:
            runner.shutdown(timeout_s=1.0)
            DownsampledViewJob.clear_accumulators()


class TestBackpressureSharedValues:
    """Tests for multiprocessing shared value behavior."""

    def test_shared_values_work_across_processes(self):
        """Verify shared values are properly updated by subprocess."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            runner.dispatch(make_slow_job(duration_s=0.1, size_bytes=10000))

            assert controller.get_pending_jobs() == 1

            runner.output_queue().get(timeout=5.0)
            time.sleep(0.1)

            assert controller.get_pending_jobs() == 0
        finally:
            runner.shutdown(timeout_s=1.0)

    def test_capacity_event_signaled_by_subprocess(self):
        """Verify capacity event is signaled when job completes in subprocess."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            controller.capacity_event.clear()
            assert not controller.capacity_event.is_set()

            runner.dispatch(make_slow_job(duration_s=0.1, size_bytes=10000))

            runner.output_queue().get(timeout=5.0)
            time.sleep(0.1)

            assert controller.capacity_event.is_set()
        finally:
            runner.shutdown(timeout_s=1.0)


class TestDownsampledViewJobShutdownCleanup:
    """Tests for DownsampledViewJob byte cleanup on shutdown.

    When acquisition is interrupted mid-well, accumulated bytes for incomplete
    wells must be released on shutdown to prevent backpressure leaks.
    """

    def test_shutdown_releases_incomplete_well_bytes(self):
        """Bytes for incomplete wells should be released on shutdown."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            for fov_idx in range(3):
                runner.dispatch(make_downsampled_view_job(fov_index=fov_idx, total_fovs=10, size_bytes=100000))
            time.sleep(0.5)

            assert controller.get_pending_jobs() == 0
            bytes_before_shutdown = controller.get_pending_mb()
            assert bytes_before_shutdown > 0.2

        finally:
            runner.shutdown(timeout_s=2.0)

        time.sleep(0.2)
        bytes_after_shutdown = controller.get_pending_mb()
        assert bytes_after_shutdown < 0.01, (
            f"Bytes not released on shutdown: before={bytes_before_shutdown:.3f}MB, "
            f"after={bytes_after_shutdown:.3f}MB"
        )

    def test_shutdown_releases_multiple_incomplete_wells(self):
        """Bytes from multiple incomplete wells should all be released on shutdown."""
        controller = BackpressureController(max_jobs=100, max_mb=1000.0)
        runner = _create_runner_with_backpressure(controller)
        runner.start()

        try:
            time.sleep(0.5)
            for well_id in ["A1", "A2", "B1"]:
                for fov_idx in range(2):
                    job = make_downsampled_view_job(
                        well_id=well_id,
                        fov_index=fov_idx,
                        total_fovs=5,  # 5 FOVs total, we only send 2
                        size_bytes=50000,  # ~50KB each
                    )
                    runner.dispatch(job)

            time.sleep(0.5)

            # Jobs should be done, but bytes from all wells still tracked
            assert controller.get_pending_jobs() == 0
            bytes_before_shutdown = controller.get_pending_mb()
            # 3 wells x 2 FOVs x ~50KB = ~300KB
            assert bytes_before_shutdown > 0.2

        finally:
            runner.shutdown(timeout_s=2.0)
            DownsampledViewJob.clear_accumulators()

        # After shutdown, bytes from ALL wells should be released
        time.sleep(0.2)
        bytes_after_shutdown = controller.get_pending_mb()
        assert bytes_after_shutdown < 0.01, (
            f"Bytes not released on shutdown: before={bytes_before_shutdown:.3f}MB, "
            f"after={bytes_after_shutdown:.3f}MB"
        )


class TestMultiPointControllerCloseMethod:
    """Tests for MultiPointController.close() method.

    These tests validate the defensive behavior of the close() method
    using mocks to avoid requiring the full controller dependencies.
    """

    @staticmethod
    def _create_mock_controller():
        """Create a minimal mock MultiPointController for testing close()."""
        from unittest.mock import MagicMock
        from control.core.multi_point_controller import MultiPointController

        controller = MagicMock(spec=MultiPointController)
        controller.multiPointWorker = None
        controller.thread = None
        controller._memory_monitor = None
        controller._log = MagicMock()
        controller._PROCESS_TERMINATE_TIMEOUT_S = 1.0
        return controller

    def test_close_handles_none_worker(self):
        """close() handles case where multiPointWorker is None."""
        from control.core.multi_point_controller import MultiPointController

        controller = self._create_mock_controller()
        MultiPointController.close(controller, timeout_s=1.0)

        controller._log.warning.assert_not_called()
        controller._log.error.assert_not_called()

    def test_close_handles_exception_in_abort(self):
        """close() continues cleanup even if abort raises exception."""
        from control.core.multi_point_controller import MultiPointController

        controller = self._create_mock_controller()
        controller.acquisition_in_progress.side_effect = RuntimeError("Test error")

        MultiPointController.close(controller, timeout_s=1.0)

        controller._log.exception.assert_called()

    def test_close_terminates_live_job_runners(self):
        """close() terminates job runners that are still alive."""
        from unittest.mock import MagicMock
        from control.core.multi_point_controller import MultiPointController

        controller = self._create_mock_controller()
        controller.acquisition_in_progress.return_value = False

        mock_job_runner = MagicMock()
        mock_job_runner.is_alive.side_effect = [True, False]
        controller.multiPointWorker = MagicMock()
        controller.multiPointWorker._job_runners = [(SlowJob, mock_job_runner)]

        MultiPointController.close(controller, timeout_s=1.0)

        mock_job_runner.terminate.assert_called_once()
        controller._log.warning.assert_called()

    def test_close_force_kills_stubborn_runners(self):
        """close() force kills job runners that don't respond to terminate."""
        from unittest.mock import MagicMock
        from control.core.multi_point_controller import MultiPointController

        controller = self._create_mock_controller()
        controller.acquisition_in_progress.return_value = False

        mock_job_runner = MagicMock()
        mock_job_runner.is_alive.side_effect = [True, True, False]
        controller.multiPointWorker = MagicMock()
        controller.multiPointWorker._job_runners = [(SlowJob, mock_job_runner)]

        MultiPointController.close(controller, timeout_s=1.0)

        mock_job_runner.terminate.assert_called_once()
        mock_job_runner.kill.assert_called_once()
