"""Backpressure controller for acquisition throttling.

Prevents RAM exhaustion by tracking pending jobs/bytes and throttling
acquisition when limits are exceeded.

Ported from upstream commits:
- 081fd7e9: Core backpressure controller
- c3322bb1: Deadlock fix (immediate byte release)
- 6bffd2d3: Simplification
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.synchronize
import time
from dataclasses import dataclass

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


@dataclass(frozen=True)
class BackpressureStats:
    """Current backpressure statistics for monitoring."""

    pending_jobs: int
    pending_bytes_mb: float
    max_pending_jobs: int
    max_pending_mb: float
    is_throttled: bool


class BackpressureController:
    """Manages backpressure across multiple job runners.

    Uses multiprocessing-safe shared values for cross-process tracking.
    The controller tracks:
    - Number of pending jobs
    - Bytes of image data pending in queues

    When either limit is exceeded, acquisition is throttled until
    capacity becomes available or a timeout is reached.

    Usage:
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        # Pass shared values to JobRunner
        job_runner = JobRunner(
            backpressure_jobs=controller.pending_jobs_value,
            backpressure_bytes=controller.pending_bytes_value,
            backpressure_event=controller.capacity_event,
        )

        # In acquisition loop
        if controller.should_throttle():
            controller.wait_for_capacity()

        # At acquisition end
        controller.close()
    """

    def __init__(
        self,
        max_jobs: int = 10,
        max_mb: float = 500.0,
        timeout_s: float = 30.0,
        enabled: bool = True,
    ):
        """Initialize backpressure controller.

        Args:
            max_jobs: Maximum number of pending jobs before throttling.
            max_mb: Maximum MB of image data pending before throttling.
            timeout_s: Timeout in seconds when waiting for capacity.
            enabled: Whether backpressure is enabled.
        """
        self._enabled = enabled
        self._max_jobs = max_jobs
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._timeout_s = timeout_s
        self._closed = False

        # Shared counters (work across processes)
        self._pending_jobs = multiprocessing.Value("i", 0)
        self._pending_bytes = multiprocessing.Value("q", 0)  # long long for large values

        # Event for signaling capacity available
        self._capacity_event = multiprocessing.Event()

    @property
    def enabled(self) -> bool:
        """Whether backpressure is enabled."""
        return self._enabled

    @property
    def pending_jobs_value(self) -> multiprocessing.synchronize.Event | None:  # noqa: Y034
        """Shared value for pending jobs (pass to JobRunner).

        Returns the multiprocessing.Value for pending jobs count.
        Note: Type annotation is approximate; actual type is SynchronizedBase[c_int].
        """
        return self._pending_jobs  # type: ignore[return-value]

    @property
    def pending_bytes_value(self) -> multiprocessing.synchronize.Event | None:  # noqa: Y034
        """Shared value for pending bytes (pass to JobRunner).

        Returns the multiprocessing.Value for pending bytes count.
        Note: Type annotation is approximate; actual type is SynchronizedBase[c_longlong].
        """
        return self._pending_bytes  # type: ignore[return-value]

    @property
    def capacity_event(self) -> multiprocessing.synchronize.Event | None:
        """Event signaled when capacity becomes available."""
        return self._capacity_event

    def get_pending_jobs(self) -> int:
        """Get current number of pending jobs."""
        if self._pending_jobs is None:
            return 0
        with self._pending_jobs.get_lock():
            return self._pending_jobs.value

    def get_pending_mb(self) -> float:
        """Get current pending bytes in MB."""
        if self._pending_bytes is None:
            return 0.0
        with self._pending_bytes.get_lock():
            return self._pending_bytes.value / (1024 * 1024)

    def should_throttle(self) -> bool:
        """Check if acquisition should wait (either limit exceeded)."""
        if not self._enabled:
            return False
        if self._pending_jobs is None or self._pending_bytes is None:
            return False

        with self._pending_jobs.get_lock():
            jobs_over = self._pending_jobs.value >= self._max_jobs
        with self._pending_bytes.get_lock():
            bytes_over = self._pending_bytes.value >= self._max_bytes

        return jobs_over or bytes_over

    def wait_for_capacity(self) -> bool:
        """Wait until capacity available or timeout.

        Returns:
            True if capacity became available, False if timed out.
        """
        if not self._enabled or not self.should_throttle():
            return True

        _log.info(
            f"Backpressure throttling: jobs={self.get_pending_jobs()}/{self._max_jobs}, "
            f"MB={self.get_pending_mb():.1f}/{self._max_bytes / (1024 * 1024):.1f}"
        )

        deadline = time.time() + self._timeout_s
        while self.should_throttle():
            if time.time() > deadline:
                _log.warning(f"Backpressure timeout after {self._timeout_s}s, continuing")
                return False
            # Clear before waiting, re-check after to avoid race with job completion
            if self._capacity_event is None:
                return False
            self._capacity_event.clear()
            if self.should_throttle():
                self._capacity_event.wait(timeout=0.1)

        _log.debug("Backpressure released")
        return True

    def job_dispatched(self, image_bytes: int) -> None:
        """Manually increment counters.

        NOTE: This method is for unit testing and debugging only. In production,
        counter tracking is handled automatically by JobRunner.dispatch() and the
        JobRunner.run() finally block. Do not call this method during normal
        acquisition - it would double-count jobs.

        Args:
            image_bytes: Size of the image data in bytes.
        """
        if not self._enabled:
            return
        if self._pending_jobs is None or self._pending_bytes is None:
            return
        with self._pending_jobs.get_lock():
            self._pending_jobs.value += 1
        with self._pending_bytes.get_lock():
            self._pending_bytes.value += image_bytes

    def get_stats(self) -> BackpressureStats:
        """Get current backpressure statistics."""
        return BackpressureStats(
            pending_jobs=self.get_pending_jobs(),
            pending_bytes_mb=self.get_pending_mb(),
            max_pending_jobs=self._max_jobs,
            max_pending_mb=self._max_bytes / (1024 * 1024),
            is_throttled=self.should_throttle(),
        )

    def reset(self) -> None:
        """Reset counters (call at acquisition start)."""
        if self._pending_jobs is None or self._pending_bytes is None:
            return
        with self._pending_jobs.get_lock():
            self._pending_jobs.value = 0
        with self._pending_bytes.get_lock():
            self._pending_bytes.value = 0

    def close(self) -> None:
        """Release multiprocessing resources.

        Call this when the controller is no longer needed to prevent
        leaked semaphore warnings.
        """
        if self._closed:
            return
        self._closed = True
        # Release references to trigger deallocation
        self._pending_jobs = None  # type: ignore
        self._pending_bytes = None  # type: ignore
        self._capacity_event = None  # type: ignore
