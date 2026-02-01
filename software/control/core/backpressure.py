"""Backpressure controller for acquisition throttling.

Prevents RAM exhaustion by tracking pending jobs/bytes and throttling
acquisition when limits are exceeded.

Ownership Model:
    BackpressureValues (the tuple of multiprocessing primitives) can be created
    either by create_backpressure_values() or internally by BackpressureController.

    The values are shared between:
    - JobRunner subprocess (increments/decrements counters)
    - BackpressureController in main process (checks throttling)

    Cleanup: multiprocessing.Value and Event don't have close() methods.
    They're garbage collected when all references are dropped. Ensure:
    1. JobRunner.shutdown() is called (terminates subprocess, releases its refs)
    2. BackpressureController.close() is called (clears main process refs)

    After both, the primitives will be GC'd and underlying semaphores released.
"""

import multiprocessing
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import squid.logging

log = squid.logging.get_logger(__name__)

__all__ = [
    "BackpressureController",
    "BackpressureStats",
    "BackpressureValues",
    "create_backpressure_values",
]

# Conversion constant: 1 MiB = 1,048,576 bytes (binary prefix, not SI megabyte)
_BYTES_PER_MB = 1024 * 1024

# Type alias for the backpressure values tuple: (pending_jobs, pending_bytes, capacity_event)
BackpressureValues = Tuple[multiprocessing.Value, multiprocessing.Value, multiprocessing.Event]


@dataclass
class BackpressureStats:
    """Current backpressure statistics for monitoring."""

    pending_jobs: int
    pending_bytes_mb: float
    max_pending_jobs: int
    max_pending_mb: float
    is_throttled: bool


def create_backpressure_values() -> BackpressureValues:
    """Create multiprocessing primitives for cross-process backpressure tracking.

    Returns:
        Tuple of (pending_jobs Value, pending_bytes Value, capacity_event Event)

    These values should be:
    1. Passed to JobRunner at construction (subprocess uses them)
    2. Passed to BackpressureController at construction (main process uses them)

    The values are process-safe and can be shared between main process and subprocess.

    Cleanup: The returned values don't need explicit cleanup. They're garbage collected
    when all references are dropped (after JobRunner.shutdown() and BackpressureController.close()).
    """
    pending_jobs = multiprocessing.Value("i", 0)
    pending_bytes = multiprocessing.Value("q", 0)
    capacity_event = multiprocessing.Event()
    return (pending_jobs, pending_bytes, capacity_event)


class BackpressureController:
    """Manages backpressure across multiple job runners.

    Uses multiprocessing-safe shared values for cross-process tracking.

    Usage:
        # Option 1: Let controller create its own values (no pre-warming)
        controller = BackpressureController(max_jobs=10, max_mb=500)
        runner = JobRunner(
            bp_pending_jobs=controller.pending_jobs_value,
            bp_pending_bytes=controller.pending_bytes_value,
            bp_capacity_event=controller.capacity_event,
        )

        # Option 2: Use pre-created values (for pre-warming)
        bp_values = create_backpressure_values()
        runner = JobRunner(
            bp_pending_jobs=bp_values[0],
            bp_pending_bytes=bp_values[1],
            bp_capacity_event=bp_values[2],
        )
        runner.start()  # Pre-warm
        # Later...
        controller = BackpressureController(max_jobs=10, bp_values=bp_values)

    Thread Safety:
        - All public methods are thread-safe (use locks on shared values)
        - close() can be called from any thread and wakes threads in wait_for_capacity()
    """

    def __init__(
        self,
        max_jobs: int = 10,
        max_mb: float = 500.0,
        timeout_s: float = 30.0,
        enabled: bool = True,
        # Pre-created backpressure values for sharing with pre-warmed JobRunner.
        # If provided, uses these instead of creating new ones.
        bp_values: Optional[BackpressureValues] = None,
    ):
        self._enabled = enabled
        self._max_jobs = max_jobs
        self._max_bytes = int(max_mb * _BYTES_PER_MB)
        self._timeout_s = timeout_s
        self._closed = False  # Lifecycle tracking

        # Use provided values or create new ones
        if bp_values is not None:
            self._pending_jobs, self._pending_bytes, self._capacity_event = bp_values
        else:
            self._pending_jobs = multiprocessing.Value("i", 0)
            self._pending_bytes = multiprocessing.Value("q", 0)
            self._capacity_event = multiprocessing.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_closed(self) -> bool:
        """True if close() has been called."""
        return self._closed

    def _warn_if_closed(self, method_name: str) -> bool:
        """Log warning if controller is closed. Returns True if closed."""
        if self._closed:
            log.warning(f"BackpressureController.{method_name}() called after close()")
            return True
        return False

    @property
    def pending_jobs_value(self) -> Optional[multiprocessing.Value]:
        """Shared value for pending jobs (pass to JobRunner).

        Returns None after close() has been called.
        """
        return self._pending_jobs

    @property
    def pending_bytes_value(self) -> Optional[multiprocessing.Value]:
        """Shared value for pending bytes (pass to JobRunner).

        Returns None after close() has been called.
        """
        return self._pending_bytes

    @property
    def capacity_event(self) -> Optional[multiprocessing.Event]:
        """Event signaled when capacity becomes available.

        Returns None after close() has been called.
        """
        return self._capacity_event

    def get_pending_jobs(self) -> int:
        pending_jobs = self._pending_jobs
        if pending_jobs is None:
            return 0
        with pending_jobs.get_lock():
            return pending_jobs.value

    def get_pending_mb(self) -> float:
        pending_bytes = self._pending_bytes
        if pending_bytes is None:
            return 0.0
        with pending_bytes.get_lock():
            return pending_bytes.value / _BYTES_PER_MB

    def should_throttle(self) -> bool:
        """Check if acquisition should wait (either limit exceeded)."""
        if not self._enabled:
            return False

        # Capture references to avoid race with close()
        pending_jobs = self._pending_jobs
        pending_bytes = self._pending_bytes

        # Guard against closed state (values set to None)
        if pending_jobs is None or pending_bytes is None:
            return False

        with pending_jobs.get_lock():
            jobs_over = pending_jobs.value >= self._max_jobs
        with pending_bytes.get_lock():
            bytes_over = pending_bytes.value >= self._max_bytes

        return jobs_over or bytes_over

    def wait_for_capacity(self) -> bool:
        """Wait until capacity available or timeout. Returns True if got capacity."""
        if self._warn_if_closed("wait_for_capacity"):
            return True  # Don't block on closed controller
        if not self._enabled or not self.should_throttle():
            return True

        log.info(
            f"Backpressure throttling: jobs={self.get_pending_jobs()}/{self._max_jobs}, "
            f"MB={self.get_pending_mb():.1f}/{self._max_bytes / _BYTES_PER_MB:.1f}"
        )

        deadline = time.monotonic() + self._timeout_s
        while self.should_throttle():
            if time.monotonic() > deadline:
                log.warning(f"Backpressure timeout after {self._timeout_s}s, continuing")
                return False
            # Capture reference to avoid race with close()
            event = self._capacity_event
            if event is None:
                break  # Controller was closed
            # Clear stale signals, then re-check condition before waiting.
            # If capacity frees between clear() and wait(), should_throttle()
            # returns False and we exit without blocking.
            event.clear()
            if self.should_throttle():
                event.wait(timeout=0.1)

        log.debug("Backpressure released")
        return True

    def job_dispatched(self, image_bytes: int) -> None:
        """Manually increment backpressure counters.

        Primarily for testing. In production, JobRunner automatically increments
        counters when dispatch() is called.

        No-op if controller is disabled or closed.
        """
        if not self._enabled:
            return
        # Capture references to avoid race with close()
        pending_jobs = self._pending_jobs
        pending_bytes = self._pending_bytes
        if pending_jobs is None or pending_bytes is None:
            return
        with pending_jobs.get_lock():
            pending_jobs.value += 1
        with pending_bytes.get_lock():
            pending_bytes.value += image_bytes

    def get_stats(self) -> BackpressureStats:
        """Get atomic snapshot of backpressure state."""
        # Capture references to avoid race with close()
        pending_jobs = self._pending_jobs
        pending_bytes = self._pending_bytes

        if pending_jobs is None or pending_bytes is None:
            # Controller is closed, return zeroed stats
            return BackpressureStats(
                pending_jobs=0,
                pending_bytes_mb=0.0,
                max_pending_jobs=self._max_jobs,
                max_pending_mb=self._max_bytes / _BYTES_PER_MB,
                is_throttled=False,
            )

        # Acquire both locks for atomic snapshot.
        # Lock ordering: pending_jobs before pending_bytes (consistent throughout module)
        with pending_jobs.get_lock():
            jobs = pending_jobs.value
            jobs_over = jobs >= self._max_jobs
            with pending_bytes.get_lock():
                bytes_val = pending_bytes.value
                bytes_over = bytes_val >= self._max_bytes

        return BackpressureStats(
            pending_jobs=jobs,
            pending_bytes_mb=bytes_val / _BYTES_PER_MB,
            max_pending_jobs=self._max_jobs,
            max_pending_mb=self._max_bytes / _BYTES_PER_MB,
            is_throttled=self._enabled and (jobs_over or bytes_over),
        )

    def reset(self) -> None:
        """Reset counters (call at acquisition start).

        WARNING: Only call when no jobs are pending. If jobs complete after reset,
        counters will go negative, which breaks throttling logic.
        """
        if self._warn_if_closed("reset"):
            return
        # Capture references to avoid race with close()
        pending_jobs = self._pending_jobs
        pending_bytes = self._pending_bytes
        if pending_jobs is None or pending_bytes is None:
            return

        # Check for pending jobs - warn if resetting with jobs in flight
        with pending_jobs.get_lock():
            current_jobs = pending_jobs.value
            if current_jobs > 0:
                log.warning(
                    f"Backpressure reset() called with {current_jobs} jobs pending. "
                    f"This may cause counter underflow."
                )
            pending_jobs.value = 0
        with pending_bytes.get_lock():
            pending_bytes.value = 0

    def close(self) -> None:
        """Release references to multiprocessing resources.

        Signals the capacity event to wake any threads blocked in wait_for_capacity(),
        then clears local references to allow garbage collection.

        Thread Safety: This method is safe to call from any thread. It captures
        references before use to avoid TOCTOU races with concurrent calls.

        This method is idempotent - safe to call multiple times.
        """
        if self._closed:
            return  # Already closed

        self._closed = True

        # Capture references first to avoid TOCTOU race
        pending_jobs = self._pending_jobs
        capacity_event = self._capacity_event

        if pending_jobs is None:
            return  # Values already cleared

        # Signal capacity event to wake any threads blocked in wait_for_capacity().
        # This must happen BEFORE clearing references to prevent AttributeError
        # when the woken thread calls should_throttle().
        if capacity_event is not None:
            try:
                capacity_event.set()
            except Exception as e:
                log.debug(f"Could not set capacity event during close (may be invalid): {e}")

        # Clear local references to allow GC
        self._pending_jobs = None
        self._pending_bytes = None
        self._capacity_event = None
