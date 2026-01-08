"""Memory profiling utilities for debugging RAM usage during HCS acquisition.

Usage:
    from control.core.memory_profiler import log_memory, MemoryTracker

    # One-off logging
    log_memory("before mosaic update")

    # Track memory over time
    tracker = MemoryTracker()
    tracker.snapshot("start")
    # ... do work ...
    tracker.snapshot("after allocation")
    tracker.report()
"""

import gc
import os
import psutil
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import squid.logging


def get_process_memory_mb() -> float:
    """Get current process memory usage in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)


def get_system_memory_info() -> Dict[str, float]:
    """Get system memory info in MB."""
    mem = psutil.virtual_memory()
    return {
        "total_mb": mem.total / (1024 * 1024),
        "available_mb": mem.available / (1024 * 1024),
        "used_mb": mem.used / (1024 * 1024),
        "percent": mem.percent,
    }


def log_memory(context: str = "", level: str = "INFO") -> float:
    """Log current memory usage with context.

    Args:
        context: Description of what's happening
        level: Log level (DEBUG, INFO, WARNING)

    Returns:
        Current process memory in MB
    """
    log = squid.logging.get_logger("MemoryProfiler")

    proc_mb = get_process_memory_mb()
    sys_info = get_system_memory_info()

    msg = (
        f"[MEM] {context}: process={proc_mb:.1f}MB, "
        f"system_used={sys_info['used_mb']:.1f}MB ({sys_info['percent']:.1f}%), "
        f"available={sys_info['available_mb']:.1f}MB"
    )

    if level == "DEBUG":
        log.debug(msg)
    elif level == "WARNING":
        log.warning(msg)
    else:
        log.info(msg)

    return proc_mb


def format_size(size_bytes: int) -> str:
    """Format byte size to human readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"


@dataclass
class MemorySnapshot:
    """A single memory snapshot."""

    label: str
    timestamp: datetime
    process_mb: float
    system_used_mb: float
    system_available_mb: float
    system_percent: float


class MemoryTracker:
    """Track memory usage over time with labeled snapshots."""

    def __init__(self, name: str = "default"):
        self.name = name
        self.snapshots: List[MemorySnapshot] = []
        self._log = squid.logging.get_logger(f"MemoryTracker.{name}")

    def snapshot(self, label: str) -> MemorySnapshot:
        """Take a memory snapshot with a label."""
        proc_mb = get_process_memory_mb()
        sys_info = get_system_memory_info()

        snap = MemorySnapshot(
            label=label,
            timestamp=datetime.now(),
            process_mb=proc_mb,
            system_used_mb=sys_info["used_mb"],
            system_available_mb=sys_info["available_mb"],
            system_percent=sys_info["percent"],
        )
        self.snapshots.append(snap)

        self._log.debug(f"[SNAP] {label}: process={proc_mb:.1f}MB, " f"system={sys_info['percent']:.1f}%")
        return snap

    def delta(self, label1: str, label2: str) -> Optional[float]:
        """Get memory delta between two labeled snapshots."""
        snap1 = next((s for s in self.snapshots if s.label == label1), None)
        snap2 = next((s for s in self.snapshots if s.label == label2), None)

        if snap1 and snap2:
            return snap2.process_mb - snap1.process_mb
        return None

    def report(self) -> str:
        """Generate a report of all snapshots."""
        if not self.snapshots:
            return "No snapshots recorded."

        lines = [f"Memory Tracker Report: {self.name}", "=" * 60]

        prev_mb = None
        for snap in self.snapshots:
            delta_str = ""
            if prev_mb is not None:
                delta = snap.process_mb - prev_mb
                delta_str = f" (Δ{delta:+.1f}MB)"

            lines.append(f"  {snap.label}: {snap.process_mb:.1f}MB{delta_str} | " f"sys={snap.system_percent:.1f}%")
            prev_mb = snap.process_mb

        # Overall delta
        if len(self.snapshots) >= 2:
            total_delta = self.snapshots[-1].process_mb - self.snapshots[0].process_mb
            lines.append("-" * 60)
            lines.append(f"Total change: {total_delta:+.1f}MB")

        report = "\n".join(lines)
        self._log.info(report)
        return report

    def clear(self):
        """Clear all snapshots."""
        self.snapshots.clear()


def get_large_objects(threshold_mb: float = 10.0) -> List[Tuple[str, float]]:
    """Find large objects in memory (requires tracemalloc to be running).

    Note: This is expensive and should only be used for debugging.
    """
    try:
        import tracemalloc

        if not tracemalloc.is_tracing():
            return []

        snapshot = tracemalloc.take_snapshot()
        stats = snapshot.statistics("lineno")

        large = []
        for stat in stats[:20]:  # Top 20
            size_mb = stat.size / (1024 * 1024)
            if size_mb >= threshold_mb:
                large.append((str(stat.traceback), size_mb))

        return large
    except Exception:
        return []


def force_gc_and_log(context: str = ""):
    """Force garbage collection and log memory before/after."""
    log = squid.logging.get_logger("MemoryProfiler")

    before = get_process_memory_mb()
    gc.collect()
    after = get_process_memory_mb()

    freed = before - after
    log.info(f"[GC] {context}: before={before:.1f}MB, after={after:.1f}MB, freed={freed:.1f}MB")

    return freed
