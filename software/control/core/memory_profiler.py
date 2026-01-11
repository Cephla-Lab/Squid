"""Memory profiling utilities for faithful RAM monitoring during HCS acquisition.

This module provides continuous background memory sampling with peak tracking,
designed to capture actual peak memory usage that matches Activity Monitor readings.

Platform support:
    - macOS: Full support (RSS, kernel peak via ru_maxrss, physical footprint)
    - Linux: Partial support (RSS, kernel peak via ru_maxrss, no footprint)
    - Windows: Basic support (RSS only, no kernel peak or footprint)

Usage:
    # In main process
    from control.core.memory_profiler import MemoryMonitor, log_memory

    monitor = MemoryMonitor(sample_interval_ms=200, track_children=True)
    monitor.start("ACQUISITION_START")
    # ... acquisition runs ...
    report = monitor.stop()  # Returns MemoryReport with peak stats

    # In worker process
    from control.core.memory_profiler import (
        start_worker_monitoring,
        stop_worker_monitoring,
        set_worker_operation,
    )

    start_worker_monitoring()
    set_worker_operation("STITCH_A1")
    # ... work ...
    report = stop_worker_monitoring()

TODO: Add per-component memory logging to pinpoint biggest memory consumers
      (e.g., SaveImageJob queue, napari display buffers, Qt/GUI overhead)
"""

import gc
import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import psutil

import squid.logging

# resource module is not available on Windows
try:
    import resource
except ImportError:
    resource = None  # type: ignore


@dataclass
class MemorySnapshot:
    """A single memory measurement."""

    timestamp: float
    rss_mb: float
    operation: str = ""
    process_name: str = "main"


@dataclass
class MemoryReport:
    """Summary of memory usage during a monitoring period."""

    start_time: float
    end_time: float
    peak_rss_mb: float  # Peak from sampling
    peak_timestamp: float
    peak_operation: str
    samples_count: int
    process_name: str
    children_peak_mb: float = 0.0  # Only tracked by main process
    total_peak_mb: float = 0.0  # main + children peak from sampling
    kernel_peak_mb: float = 0.0  # ru_maxrss - true peak tracked by kernel


def get_process_memory_mb(pid: Optional[int] = None) -> float:
    """Get RSS memory for a process in MB.

    Args:
        pid: Process ID. If None, uses current process.

    Returns:
        Resident Set Size in megabytes.
    """
    try:
        process = psutil.Process(pid) if pid else psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
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


def get_all_squid_memory_mb() -> Dict[str, float]:
    """Get memory for main process and all child processes.

    Returns:
        Dict with 'main', 'children', 'total', and 'child_pids' keys.
        Memory values are in MB.
    """
    try:
        process = psutil.Process()
        main_mb = process.memory_info().rss / (1024 * 1024)
        children_mb = 0.0
        child_details = []

        for child in process.children(recursive=True):
            try:
                child_mb = child.memory_info().rss / (1024 * 1024)
                children_mb += child_mb
                child_details.append((child.pid, child_mb))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return {
            "main": main_mb,
            "children": children_mb,
            "total": main_mb + children_mb,
            "child_details": child_details,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"main": 0.0, "children": 0.0, "total": 0.0, "child_details": []}


def get_macos_memory_footprint_mb(pid: int) -> float:
    """Get macOS physical footprint for a process (matches Activity Monitor).

    On macOS, Activity Monitor shows 'Memory' which is the physical footprint,
    not RSS. This can be significantly larger than RSS.

    Args:
        pid: Process ID

    Returns:
        Physical footprint in MB, or 0.0 if unavailable.
    """
    if platform.system() != "Darwin":
        return 0.0

    try:
        result = subprocess.run(
            ["footprint", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Parse output like "python [17890]: 64-bit    Footprint: 6641 KB"
            for line in result.stdout.split("\n"):
                # Look for "Footprint: XXXX KB" or "Footprint: XXXX MB" or "Footprint: X.X GB"
                match = re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", line)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2)
                    if unit == "KB":
                        return value / 1024
                    elif unit == "MB":
                        return value
                    elif unit == "GB":
                        return value * 1024
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, AttributeError):
        pass

    return 0.0


def get_all_python_processes_mb() -> Dict[str, float]:
    """Get memory for ALL Python processes on the system.

    This helps compare with Activity Monitor which may show combined Python memory.

    Returns:
        Dict with 'total', 'count', 'footprint_total', and 'processes' list.
    """
    total_mb = 0.0
    footprint_total_mb = 0.0
    processes = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info["name"] or ""
            cmdline = proc.info["cmdline"] or []
            cmdline_str = " ".join(cmdline)[:80]

            if "python" in name.lower() or "Python" in name:
                mem_mb = proc.memory_info().rss / (1024 * 1024)
                footprint_mb = get_macos_memory_footprint_mb(proc.info["pid"])
                total_mb += mem_mb
                footprint_total_mb += footprint_mb if footprint_mb > 0 else mem_mb
                processes.append(
                    {
                        "pid": proc.info["pid"],
                        "name": name,
                        "mem_mb": mem_mb,
                        "footprint_mb": footprint_mb,
                        "cmdline": cmdline_str,
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "total": total_mb,
        "footprint_total": footprint_total_mb,
        "count": len(processes),
        "processes": processes,
    }


def log_memory(context: str = "", level: str = "info", include_children: bool = True) -> float:
    """Log current memory usage with [MEM] tag.

    Args:
        context: Description of current operation/state.
        level: Log level ("debug", "info", "warning").
        include_children: If True, include child process memory.

    Returns:
        Total memory in MB (main + children if include_children).
    """
    log = squid.logging.get_logger("MemoryProfiler")

    if include_children:
        mem = get_all_squid_memory_mb()
        msg = f"[MEM] {context}: main={mem['main']:.1f}MB, children={mem['children']:.1f}MB, total={mem['total']:.1f}MB"
        result = mem["total"]
    else:
        main_mb = get_process_memory_mb()
        msg = f"[MEM] {context}: process={main_mb:.1f}MB"
        result = main_mb

    if level == "debug":
        log.debug(msg)
    elif level == "warning":
        log.warning(msg)
    else:
        log.info(msg)

    return result


class MemoryMonitor:
    """Continuous memory sampler with peak tracking.

    Runs a daemon background thread that samples memory at regular intervals,
    tracking the peak RSS observed during the monitoring period.
    """

    def __init__(
        self,
        sample_interval_ms: int = 200,
        process_name: str = "main",
        track_children: bool = True,
    ):
        """Initialize memory monitor.

        Args:
            sample_interval_ms: Sampling interval in milliseconds (default 200ms = 5/sec).
            process_name: Name for this monitor (used in logs).
            track_children: If True, also track child process memory (main process only).
        """
        self._sample_interval_s = sample_interval_ms / 1000.0
        self._process_name = process_name
        self._track_children = track_children

        self._log = squid.logging.get_logger(f"MemoryMonitor.{process_name}")

        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Peak tracking (thread-safe via lock)
        self._lock = threading.Lock()
        self._current_operation = ""
        self._peak_rss_mb = 0.0
        self._peak_timestamp = 0.0
        self._peak_operation = ""
        self._children_peak_mb = 0.0
        self._total_peak_mb = 0.0
        self._all_python_peak_mb = 0.0  # Track ALL Python processes (RSS)
        self._footprint_peak_mb = 0.0  # Track physical footprint (Activity Monitor)
        self._samples_count = 0
        self._start_time = 0.0

    def start(self, initial_operation: str = "") -> None:
        """Start continuous memory sampling in background thread.

        Args:
            initial_operation: Initial operation context for logging.
        """
        if self._thread is not None and self._thread.is_alive():
            self._log.warning("Memory monitor already running")
            return

        self._stop_event.clear()

        with self._lock:
            self._current_operation = initial_operation
            self._peak_rss_mb = 0.0
            self._peak_timestamp = 0.0
            self._peak_operation = ""
            self._children_peak_mb = 0.0
            self._total_peak_mb = 0.0
            self._all_python_peak_mb = 0.0
            self._footprint_peak_mb = 0.0
            self._samples_count = 0
            self._start_time = time.time()

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

        log_memory(f"{self._process_name} MONITOR START", include_children=self._track_children)
        self._log.info(
            f"Memory monitor started (interval={self._sample_interval_s*1000:.0f}ms, "
            f"track_children={self._track_children})"
        )

    def stop(self) -> MemoryReport:
        """Stop sampling and return memory report with peak stats.

        Returns:
            MemoryReport with peak memory statistics.
        """
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        end_time = time.time()
        kernel_peak = get_peak_rss_mb()

        with self._lock:
            report = MemoryReport(
                start_time=self._start_time,
                end_time=end_time,
                peak_rss_mb=self._peak_rss_mb,
                peak_timestamp=self._peak_timestamp,
                peak_operation=self._peak_operation,
                samples_count=self._samples_count,
                process_name=self._process_name,
                children_peak_mb=self._children_peak_mb,
                total_peak_mb=self._total_peak_mb,
                kernel_peak_mb=kernel_peak,
            )

        duration = end_time - self._start_time
        if self._track_children:
            # Log all Python processes for Activity Monitor comparison
            all_python = get_all_python_processes_mb()
            self._log.info(
                f"[MEM] {self._process_name} REPORT: sampled_peak={report.peak_rss_mb:.1f}MB "
                f"at {report.peak_operation}, kernel_peak={report.kernel_peak_mb:.1f}MB, "
                f"duration={duration:.1f}s, samples={report.samples_count}, "
                f"total_peak={report.total_peak_mb:.1f}MB (main+children), "
                f"ALL_PYTHON_RSS_PEAK={self._all_python_peak_mb:.1f}MB, "
                f"FOOTPRINT_PEAK={self._footprint_peak_mb:.1f}MB"
            )
            self._log.info(
                f"[MEM] ALL_PYTHON_PROCESSES (current): count={all_python['count']}, "
                f"rss_total={all_python['total']:.1f}MB, footprint_total={all_python['footprint_total']:.1f}MB"
            )
            for p in all_python["processes"]:
                self._log.info(
                    f"[MEM]   PID={p['pid']}: rss={p['mem_mb']:.1f}MB, footprint={p['footprint_mb']:.1f}MB - {p['cmdline']}"
                )
        else:
            self._log.info(
                f"[MEM] {self._process_name} REPORT: sampled_peak={report.peak_rss_mb:.1f}MB "
                f"at {report.peak_operation}, kernel_peak={report.kernel_peak_mb:.1f}MB, "
                f"duration={duration:.1f}s, samples={report.samples_count}"
            )

        return report

    def set_current_operation(self, operation: str) -> None:
        """Update current operation context for peak attribution.

        Args:
            operation: Name of the current operation (e.g., "STITCH_A1").
        """
        with self._lock:
            self._current_operation = operation

    def get_current_peak(self) -> Tuple[float, float]:
        """Get current peak RSS without stopping.

        Returns:
            Tuple of (peak_rss_mb, total_peak_mb).
        """
        with self._lock:
            return (self._peak_rss_mb, self._total_peak_mb)

    def _sample_loop(self) -> None:
        """Background thread loop that samples memory at regular intervals."""
        while not self._stop_event.is_set():
            try:
                self._take_sample()
            except Exception as e:
                self._log.debug(f"Sample error: {e}")

            # Use wait() instead of sleep() so we can respond to stop quickly
            self._stop_event.wait(timeout=self._sample_interval_s)

    def _take_sample(self) -> None:
        """Take a single memory sample and update peak if necessary."""
        timestamp = time.time()
        main_mb = get_process_memory_mb()

        children_mb = 0.0
        total_mb = main_mb
        all_python_mb = 0.0
        footprint_mb = 0.0
        if self._track_children:
            mem = get_all_squid_memory_mb()
            children_mb = mem["children"]
            total_mb = mem["total"]
            # Also track ALL Python processes (for Activity Monitor comparison)
            all_python = get_all_python_processes_mb()
            all_python_mb = all_python["total"]
            footprint_mb = all_python["footprint_total"]

        with self._lock:
            self._samples_count += 1
            operation = self._current_operation

            # Track peak for main process RSS
            if main_mb > self._peak_rss_mb:
                self._peak_rss_mb = main_mb
                self._peak_timestamp = timestamp
                self._peak_operation = operation

            # Track peak for children
            if children_mb > self._children_peak_mb:
                self._children_peak_mb = children_mb

            # Track peak for total (main + children)
            if total_mb > self._total_peak_mb:
                self._total_peak_mb = total_mb

            # Track peak for ALL Python processes (RSS)
            if all_python_mb > self._all_python_peak_mb:
                self._all_python_peak_mb = all_python_mb

            # Track peak for physical footprint (Activity Monitor metric)
            if footprint_mb > self._footprint_peak_mb:
                self._footprint_peak_mb = footprint_mb


# Module-level worker monitor for convenience
_worker_monitor: Optional[MemoryMonitor] = None


def start_worker_monitoring(sample_interval_ms: int = 200) -> None:
    """Start memory monitoring in worker process.

    Args:
        sample_interval_ms: Sampling interval in milliseconds.
    """
    global _worker_monitor
    if _worker_monitor is not None:
        return  # Already running

    _worker_monitor = MemoryMonitor(
        sample_interval_ms=sample_interval_ms,
        process_name="worker",
        track_children=False,  # Worker doesn't spawn children
    )
    _worker_monitor.start("WORKER_START")


def stop_worker_monitoring() -> Optional[MemoryReport]:
    """Stop worker monitoring and return report.

    Returns:
        MemoryReport with peak stats, or None if not monitoring.
    """
    global _worker_monitor
    if _worker_monitor is None:
        return None

    report = _worker_monitor.stop()
    _worker_monitor = None
    return report


def set_worker_operation(operation: str) -> None:
    """Set current operation in worker monitor.

    Args:
        operation: Name of the current operation (e.g., "STITCH_A1").
    """
    if _worker_monitor is not None:
        _worker_monitor.set_current_operation(operation)


def force_gc_and_log(context: str = "") -> float:
    """Force garbage collection and log memory before/after.

    Args:
        context: Description of current state.

    Returns:
        Amount of memory freed in MB.
    """
    log = squid.logging.get_logger("MemoryProfiler")

    before = get_process_memory_mb()
    gc.collect()
    after = get_process_memory_mb()

    freed = before - after
    log.info(f"[GC] {context}: before={before:.1f}MB, after={after:.1f}MB, freed={freed:.1f}MB")

    return freed
