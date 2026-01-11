"""Backpressure controller for acquisition throttling.

Prevents RAM exhaustion by tracking pending jobs/bytes and throttling
acquisition when limits are exceeded.
"""

import multiprocessing
import time
from dataclasses import dataclass

import squid.logging

log = squid.logging.get_logger(__name__)


@dataclass
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
    """

    def __init__(
        self,
        max_jobs: int = 10,
        max_mb: float = 500.0,
        timeout_s: float = 30.0,
        enabled: bool = True,
    ):
        self._enabled = enabled
        self._max_jobs = max_jobs
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._timeout_s = timeout_s

        # Shared counters (work across processes)
        self._pending_jobs = multiprocessing.Value("i", 0)
        self._pending_bytes = multiprocessing.Value("q", 0)  # long long for large values

        # Event for signaling capacity available (starts cleared)
        self._capacity_event = multiprocessing.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def pending_jobs_value(self) -> multiprocessing.Value:
        """Shared value for pending jobs (pass to JobRunner)."""
        return self._pending_jobs

    @property
    def pending_bytes_value(self) -> multiprocessing.Value:
        """Shared value for pending bytes (pass to JobRunner)."""
        return self._pending_bytes

    @property
    def capacity_event(self) -> multiprocessing.Event:
        """Event signaled when capacity becomes available."""
        return self._capacity_event

    def get_pending_jobs(self) -> int:
        with self._pending_jobs.get_lock():
            return self._pending_jobs.value

    def get_pending_mb(self) -> float:
        with self._pending_bytes.get_lock():
            return self._pending_bytes.value / (1024 * 1024)

    def should_throttle(self) -> bool:
        """Check if acquisition should wait (either limit exceeded)."""
        if not self._enabled:
            return False

        with self._pending_jobs.get_lock():
            jobs_over = self._pending_jobs.value >= self._max_jobs
        with self._pending_bytes.get_lock():
            bytes_over = self._pending_bytes.value >= self._max_bytes

        return jobs_over or bytes_over

    def wait_for_capacity(self) -> bool:
        """Wait until capacity available or timeout. Returns True if got capacity."""
        if not self._enabled or not self.should_throttle():
            return True

        log.info(
            f"Backpressure throttling: jobs={self.get_pending_jobs()}/{self._max_jobs}, "
            f"MB={self.get_pending_mb():.1f}/{self._max_bytes/(1024*1024):.1f}"
        )

        deadline = time.time() + self._timeout_s
        while self.should_throttle():
            if time.time() > deadline:
                log.warning(f"Backpressure timeout after {self._timeout_s}s, continuing")
                return False
            # Clear before waiting, re-check after to avoid race with job completion
            self._capacity_event.clear()
            if self.should_throttle():
                self._capacity_event.wait(timeout=0.1)

        log.debug("Backpressure released")
        return True

    def job_dispatched(self, image_bytes: int) -> None:
        """Manually increment counters. For testing only - production uses JobRunner."""
        if not self._enabled:
            return
        with self._pending_jobs.get_lock():
            self._pending_jobs.value += 1
        with self._pending_bytes.get_lock():
            self._pending_bytes.value += image_bytes

    def get_stats(self) -> BackpressureStats:
        return BackpressureStats(
            pending_jobs=self.get_pending_jobs(),
            pending_bytes_mb=self.get_pending_mb(),
            max_pending_jobs=self._max_jobs,
            max_pending_mb=self._max_bytes / (1024 * 1024),
            is_throttled=self.should_throttle(),
        )

    def reset(self) -> None:
        """Reset counters (call at acquisition start)."""
        with self._pending_jobs.get_lock():
            self._pending_jobs.value = 0
        with self._pending_bytes.get_lock():
            self._pending_bytes.value = 0
