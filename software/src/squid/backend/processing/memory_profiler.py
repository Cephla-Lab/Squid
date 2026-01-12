"""Memory profiling utilities for RAM monitoring during acquisition.

Provides continuous background memory sampling with peak tracking,
designed to capture actual peak memory usage that matches Activity Monitor readings.

Platform support:
    - macOS: Full support (RSS, kernel peak via ru_maxrss)
    - Linux: Full support (RSS, kernel peak via ru_maxrss)
    - Windows: Partial support (RSS only; no kernel peak)

Ported from upstream commits:
- c28b372b: RAM usage monitoring
- 6bffd2d3: Simplification
"""

from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable

import psutil

import squid.core.logging

# resource module is not available on Windows
try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

_log = squid.core.logging.get_logger(__name__)


@dataclass(frozen=True)
class MemorySnapshot:
    """A single memory measurement."""

    timestamp: float
    rss_mb: float
    operation: str = ""
    process_name: str = "main"


@dataclass(frozen=True)
class MemoryReport:
    """Summary of memory usage during a monitoring period."""

    start_time: float
    end_time: float
    peak_rss_mb: float
    peak_timestamp: float
    samples_count: int
    process_name: str
    kernel_peak_mb: float = 0.0  # ru_maxrss - true peak tracked by kernel


def get_process_memory_mb(pid: int | None = None) -> float:
    """Get RSS memory for a process in MB.

    Args:
        pid: Process ID. If None, uses current process.

    Returns:
        Resident Set Size in megabytes.
    """
    try:
        process = psutil.Process(pid) if pid else psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except psutil.NoSuchProcess:
        _log.debug(f"Process {pid} no longer exists")
        return 0.0
    except psutil.AccessDenied:
        _log.warning(f"Access denied reading memory for process {pid}")
        return 0.0


def get_peak_rss_mb() -> float:
    """Get the peak RSS (maximum resident set size) for the current process.

    This uses resource.getrusage() which is tracked by the kernel and captures
    the true peak even for brief memory spikes that sampling might miss.

    Note: Not available on Windows (returns 0.0).

    Returns:
        Peak RSS in megabytes, or 0.0 if unavailable.
    """
    if resource is None:
        return 0.0  # Not available on Windows

    usage = resource.getrusage(resource.RUSAGE_SELF)
    # On macOS, ru_maxrss is in bytes; on Linux, it's in kilobytes
    if platform.system() == "Darwin":
        return usage.ru_maxrss / (1024 * 1024)
    else:
        return usage.ru_maxrss / 1024


def get_total_system_memory_mb() -> float:
    """Get total system memory in MB."""
    return psutil.virtual_memory().total / (1024 * 1024)


def get_available_memory_mb() -> float:
    """Get available system memory in MB."""
    return psutil.virtual_memory().available / (1024 * 1024)


class MemoryMonitor:
    """Background memory sampler with peak tracking.

    Samples memory at a fixed interval and tracks peak usage.
    Emits updates via optional callback for UI display.

    Usage:
        monitor = MemoryMonitor(sample_interval_ms=200)
        monitor.start("ACQUISITION_START")
        # ... acquisition runs ...
        report = monitor.stop()  # Returns MemoryReport with peak stats

    Thread Safety:
        This class is thread-safe. The sampling thread runs independently
        and updates are protected by internal synchronization.
    """

    def __init__(
        self,
        sample_interval_ms: int = 200,
        on_memory_update: Callable[[float, float], None] | None = None,
    ):
        """Initialize memory monitor.

        Args:
            sample_interval_ms: How often to sample memory in milliseconds.
            on_memory_update: Optional callback(current_mb, peak_mb) for UI updates.
        """
        self._sample_interval_s = sample_interval_ms / 1000.0
        self._on_memory_update = on_memory_update

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Tracking state
        self._start_time: float = 0.0
        self._peak_rss_mb: float = 0.0
        self._peak_timestamp: float = 0.0
        self._samples_count: int = 0
        self._current_operation: str = ""

    def start(self, operation: str = "") -> None:
        """Start background memory sampling.

        Args:
            operation: Label for this monitoring period (e.g., "ACQUISITION_START").
        """
        with self._lock:
            if self._running:
                _log.warning("MemoryMonitor already running")
                return

            self._running = True
            self._start_time = time.time()
            self._peak_rss_mb = 0.0
            self._peak_timestamp = 0.0
            self._samples_count = 0
            self._current_operation = operation

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        _log.debug(f"MemoryMonitor started: {operation}")

    def stop(self) -> MemoryReport:
        """Stop sampling and return memory report.

        Returns:
            MemoryReport with peak statistics.
        """
        with self._lock:
            self._running = False

        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        end_time = time.time()
        kernel_peak = get_peak_rss_mb()

        with self._lock:
            report = MemoryReport(
                start_time=self._start_time,
                end_time=end_time,
                peak_rss_mb=self._peak_rss_mb,
                peak_timestamp=self._peak_timestamp,
                samples_count=self._samples_count,
                process_name="main",
                kernel_peak_mb=kernel_peak,
            )

        _log.info(
            f"MemoryMonitor stopped: peak={report.peak_rss_mb:.1f}MB "
            f"(kernel={report.kernel_peak_mb:.1f}MB), samples={report.samples_count}"
        )
        return report

    def set_operation(self, operation: str) -> None:
        """Update the current operation label."""
        with self._lock:
            self._current_operation = operation

    def get_current_mb(self) -> float:
        """Get current memory usage in MB."""
        return get_process_memory_mb()

    def get_peak_mb(self) -> float:
        """Get peak memory seen so far in MB."""
        with self._lock:
            return self._peak_rss_mb

    def _sample_loop(self) -> None:
        """Background sampling loop."""
        while True:
            with self._lock:
                if not self._running:
                    break

            current_mb = get_process_memory_mb()
            now = time.time()

            with self._lock:
                self._samples_count += 1
                if current_mb > self._peak_rss_mb:
                    self._peak_rss_mb = current_mb
                    self._peak_timestamp = now

            if self._on_memory_update is not None:
                try:
                    self._on_memory_update(current_mb, self._peak_rss_mb)
                except Exception:
                    _log.exception("Error in memory update callback")

            time.sleep(self._sample_interval_s)


def log_memory(label: str = "") -> None:
    """Log current memory usage (convenience function for debugging)."""
    current = get_process_memory_mb()
    peak = get_peak_rss_mb()
    _log.info(f"Memory [{label}]: current={current:.1f}MB, peak={peak:.1f}MB")
