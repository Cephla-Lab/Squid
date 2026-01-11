"""Tests for the memory_profiler module.

This module tests the memory profiling utilities used for RAM monitoring
during HCS acquisitions.
"""

import gc
import os
import platform
import threading
import time
from unittest.mock import MagicMock, patch

import psutil
import pytest

from control.core.memory_profiler import (
    MemoryMonitor,
    MemoryReport,
    MemorySnapshot,
    force_gc_and_log,
    get_all_python_processes_mb,
    get_all_squid_memory_mb,
    get_memory_footprint_mb,
    get_peak_rss_mb,
    get_process_memory_mb,
    log_memory,
    set_worker_operation,
    start_worker_monitoring,
    stop_worker_monitoring,
)


# =============================================================================
# Data class tests
# =============================================================================


class TestMemorySnapshot:
    """Tests for MemorySnapshot dataclass."""

    def test_create_with_defaults(self):
        """Test creating MemorySnapshot with default values."""
        snapshot = MemorySnapshot(timestamp=1234.5, rss_mb=100.0)
        assert snapshot.timestamp == 1234.5
        assert snapshot.rss_mb == 100.0
        assert snapshot.operation == ""
        assert snapshot.process_name == "main"

    def test_create_with_all_values(self):
        """Test creating MemorySnapshot with all values specified."""
        snapshot = MemorySnapshot(
            timestamp=1234.5,
            rss_mb=100.0,
            operation="STITCH_A1",
            process_name="worker",
        )
        assert snapshot.timestamp == 1234.5
        assert snapshot.rss_mb == 100.0
        assert snapshot.operation == "STITCH_A1"
        assert snapshot.process_name == "worker"


class TestMemoryReport:
    """Tests for MemoryReport dataclass."""

    def test_create_with_required_values(self):
        """Test creating MemoryReport with required values only."""
        report = MemoryReport(
            start_time=1000.0,
            end_time=2000.0,
            peak_rss_mb=500.0,
            peak_timestamp=1500.0,
            peak_operation="ACQUISITION",
            samples_count=100,
            process_name="main",
        )
        assert report.start_time == 1000.0
        assert report.end_time == 2000.0
        assert report.peak_rss_mb == 500.0
        assert report.peak_timestamp == 1500.0
        assert report.peak_operation == "ACQUISITION"
        assert report.samples_count == 100
        assert report.process_name == "main"
        # Defaults
        assert report.children_peak_mb == 0.0
        assert report.total_peak_mb == 0.0
        assert report.kernel_peak_mb == 0.0

    def test_create_with_all_values(self):
        """Test creating MemoryReport with all values specified."""
        report = MemoryReport(
            start_time=1000.0,
            end_time=2000.0,
            peak_rss_mb=500.0,
            peak_timestamp=1500.0,
            peak_operation="ACQUISITION",
            samples_count=100,
            process_name="main",
            children_peak_mb=200.0,
            total_peak_mb=700.0,
            kernel_peak_mb=550.0,
        )
        assert report.children_peak_mb == 200.0
        assert report.total_peak_mb == 700.0
        assert report.kernel_peak_mb == 550.0


# =============================================================================
# Utility function tests
# =============================================================================


class TestGetProcessMemoryMb:
    """Tests for get_process_memory_mb function."""

    def test_returns_positive_value_for_current_process(self):
        """Test that current process memory is a positive value."""
        memory_mb = get_process_memory_mb()
        assert memory_mb > 0
        # Should be at least a few MB for a Python process
        assert memory_mb >= 1.0

    def test_returns_positive_value_with_explicit_pid(self):
        """Test that memory can be retrieved with explicit PID."""
        memory_mb = get_process_memory_mb(os.getpid())
        assert memory_mb > 0

    def test_returns_zero_for_nonexistent_pid(self):
        """Test that nonexistent PID returns 0."""
        # Use a very high PID that's unlikely to exist
        memory_mb = get_process_memory_mb(99999999)
        assert memory_mb == 0.0


class TestGetPeakRssMb:
    """Tests for get_peak_rss_mb function."""

    def test_returns_non_negative_value(self):
        """Test that peak RSS returns a non-negative value."""
        peak_mb = get_peak_rss_mb()
        # On Windows, returns 0.0 since resource module is unavailable
        assert peak_mb >= 0.0

    @pytest.mark.skipif(platform.system() == "Windows", reason="resource module not available on Windows")
    def test_returns_positive_on_unix(self):
        """Test that peak RSS is positive on Unix systems."""
        peak_mb = get_peak_rss_mb()
        assert peak_mb > 0


class TestGetAllSquidMemoryMb:
    """Tests for get_all_squid_memory_mb function."""

    def test_returns_dict_with_expected_keys(self):
        """Test that the return dict has all expected keys."""
        result = get_all_squid_memory_mb()
        assert "main" in result
        assert "children" in result
        assert "total" in result
        assert "child_details" in result

    def test_main_is_positive(self):
        """Test that main process memory is positive."""
        result = get_all_squid_memory_mb()
        assert result["main"] > 0

    def test_total_equals_main_plus_children(self):
        """Test that total equals main + children."""
        result = get_all_squid_memory_mb()
        assert result["total"] == result["main"] + result["children"]

    def test_children_is_non_negative(self):
        """Test that children memory is non-negative."""
        result = get_all_squid_memory_mb()
        assert result["children"] >= 0.0

    def test_child_details_is_list(self):
        """Test that child_details is a list."""
        result = get_all_squid_memory_mb()
        assert isinstance(result["child_details"], list)


class TestGetMemoryFootprintMb:
    """Tests for get_memory_footprint_mb function."""

    def test_returns_non_negative_for_current_process(self):
        """Test that footprint returns non-negative value."""
        # May return 0.0 if platform-specific method fails
        footprint_mb = get_memory_footprint_mb(os.getpid())
        assert footprint_mb >= 0.0

    def test_returns_zero_for_nonexistent_pid(self):
        """Test that nonexistent PID returns 0."""
        footprint_mb = get_memory_footprint_mb(99999999)
        assert footprint_mb == 0.0

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-specific test")
    def test_macos_footprint_positive(self):
        """Test that macOS footprint returns positive value."""
        footprint_mb = get_memory_footprint_mb(os.getpid())
        # Should be positive on macOS with footprint command available
        assert footprint_mb > 0


class TestGetAllPythonProcessesMb:
    """Tests for get_all_python_processes_mb function."""

    def test_returns_dict_with_expected_keys(self):
        """Test that the return dict has all expected keys."""
        result = get_all_python_processes_mb()
        assert "total" in result
        assert "footprint_total" in result
        assert "count" in result
        assert "processes" in result

    def test_count_is_at_least_one(self):
        """Test that at least one Python process is found (the test runner)."""
        result = get_all_python_processes_mb()
        assert result["count"] >= 1

    def test_total_is_positive(self):
        """Test that total memory is positive."""
        result = get_all_python_processes_mb()
        assert result["total"] > 0

    def test_processes_list_contains_current_process(self):
        """Test that current process is in the list."""
        result = get_all_python_processes_mb()
        current_pid = os.getpid()
        pids = [p["pid"] for p in result["processes"]]
        assert current_pid in pids


class TestLogMemory:
    """Tests for log_memory function."""

    def test_returns_positive_value(self):
        """Test that log_memory returns positive memory value."""
        result = log_memory("test context")
        assert result > 0

    def test_returns_value_without_children(self):
        """Test that log_memory works without children tracking."""
        result = log_memory("test context", include_children=False)
        assert result > 0

    def test_different_log_levels(self):
        """Test that different log levels work."""
        # These should not raise
        log_memory("debug test", level="debug")
        log_memory("info test", level="info")
        log_memory("warning test", level="warning")


# =============================================================================
# MemoryMonitor class tests
# =============================================================================


class TestMemoryMonitorInit:
    """Tests for MemoryMonitor initialization."""

    def test_default_initialization(self):
        """Test MemoryMonitor with default parameters."""
        monitor = MemoryMonitor()
        assert monitor._sample_interval_s == 0.2  # 200ms
        assert monitor._process_name == "main"
        assert monitor._track_children is True
        assert monitor._log_interval_s == 30.0

    def test_custom_parameters(self):
        """Test MemoryMonitor with custom parameters."""
        monitor = MemoryMonitor(
            sample_interval_ms=500,
            process_name="worker",
            track_children=False,
            log_interval_s=60.0,
            enable_signals=False,
        )
        assert monitor._sample_interval_s == 0.5
        assert monitor._process_name == "worker"
        assert monitor._track_children is False
        assert monitor._log_interval_s == 60.0
        assert monitor.signals is None

    def test_invalid_sample_interval_zero(self):
        """Test that zero sample interval raises ValueError."""
        with pytest.raises(ValueError, match="sample_interval_ms must be a positive integer"):
            MemoryMonitor(sample_interval_ms=0)

    def test_invalid_sample_interval_negative(self):
        """Test that negative sample interval raises ValueError."""
        with pytest.raises(ValueError, match="sample_interval_ms must be a positive integer"):
            MemoryMonitor(sample_interval_ms=-100)


class TestMemoryMonitorStartStop:
    """Tests for MemoryMonitor start/stop lifecycle."""

    def test_start_creates_thread(self):
        """Test that start creates a background thread."""
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)
        try:
            monitor.start("TEST_START")
            assert monitor._thread is not None
            assert monitor._thread.is_alive()
        finally:
            monitor.stop()

    def test_stop_returns_report(self):
        """Test that stop returns a MemoryReport."""
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)
        monitor.start("TEST_START")
        time.sleep(0.15)  # Let it take at least one sample
        report = monitor.stop()

        assert isinstance(report, MemoryReport)
        assert report.process_name == "main"
        assert report.samples_count >= 1
        assert report.peak_rss_mb > 0

    def test_stop_stops_thread(self):
        """Test that stop terminates the background thread."""
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)
        monitor.start("TEST_START")
        assert monitor._thread.is_alive()

        monitor.stop()
        assert monitor._thread is None

    def test_multiple_start_warns(self):
        """Test that starting an already running monitor logs a warning."""
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)
        try:
            monitor.start("FIRST_START")
            # Second start should not create a new thread
            thread_before = monitor._thread
            monitor.start("SECOND_START")
            assert monitor._thread is thread_before
        finally:
            monitor.stop()


class TestMemoryMonitorSampling:
    """Tests for MemoryMonitor sampling functionality."""

    def test_samples_are_taken(self):
        """Test that samples are taken over time."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("TEST")
        time.sleep(0.2)  # Should get ~4 samples
        report = monitor.stop()

        assert report.samples_count >= 2

    def test_peak_tracking(self):
        """Test that peak RSS is tracked."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("TEST")
        time.sleep(0.15)
        report = monitor.stop()

        assert report.peak_rss_mb > 0
        assert report.peak_timestamp > 0

    def test_get_current_peak_while_running(self):
        """Test getting current peak while monitor is running."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("TEST")
        time.sleep(0.1)

        peak_rss, total_peak = monitor.get_current_peak()
        assert peak_rss > 0

        monitor.stop()

    def test_footprint_peak_property(self):
        """Test footprint_peak property."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("TEST")
        time.sleep(0.1)

        # footprint_peak may be 0 if platform doesn't support it
        peak = monitor.footprint_peak
        assert peak >= 0.0

        monitor.stop()


class TestMemoryMonitorOperations:
    """Tests for MemoryMonitor operation tracking."""

    def test_initial_operation(self):
        """Test that initial operation is recorded."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("INITIAL_OP")
        time.sleep(0.1)
        report = monitor.stop()

        # Peak operation should be from the initial operation (or empty if updated)
        assert report.peak_operation in ["INITIAL_OP", ""]

    def test_set_current_operation(self):
        """Test updating current operation."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("INITIAL")
        time.sleep(0.1)

        monitor.set_current_operation("NEW_OPERATION")
        with monitor._lock:
            assert monitor._current_operation == "NEW_OPERATION"

        monitor.stop()


class TestMemoryMonitorThreadSafety:
    """Tests for MemoryMonitor thread safety."""

    def test_concurrent_access(self):
        """Test that concurrent access to monitor is safe."""
        monitor = MemoryMonitor(sample_interval_ms=20, enable_signals=False, track_children=False)
        monitor.start("TEST")

        errors = []

        def reader():
            try:
                for _ in range(50):
                    monitor.get_current_peak()
                    _ = monitor.footprint_peak
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(50):
                    monitor.set_current_operation(f"OP_{i}")
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads.extend([threading.Thread(target=writer) for _ in range(2)])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        monitor.stop()

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"


# =============================================================================
# Worker monitoring function tests
# =============================================================================


class TestWorkerMonitoring:
    """Tests for worker monitoring convenience functions."""

    def test_start_and_stop_worker_monitoring(self):
        """Test starting and stopping worker monitoring."""
        # Ensure clean state
        stop_worker_monitoring()

        start_worker_monitoring(sample_interval_ms=100)
        time.sleep(0.15)
        report = stop_worker_monitoring()

        assert report is not None
        assert isinstance(report, MemoryReport)
        assert report.process_name == "worker"

    def test_stop_without_start_returns_none(self):
        """Test that stopping without starting returns None."""
        # Ensure clean state
        stop_worker_monitoring()

        result = stop_worker_monitoring()
        assert result is None

    def test_double_start_is_idempotent(self):
        """Test that double start doesn't create multiple monitors."""
        stop_worker_monitoring()

        start_worker_monitoring(sample_interval_ms=100)
        start_worker_monitoring(sample_interval_ms=100)  # Should be no-op

        report = stop_worker_monitoring()
        assert report is not None

        # Second stop should return None
        assert stop_worker_monitoring() is None

    def test_set_worker_operation_when_running(self):
        """Test setting operation while worker monitoring is running."""
        stop_worker_monitoring()

        start_worker_monitoring(sample_interval_ms=100)
        set_worker_operation("TEST_OP")  # Should not raise
        stop_worker_monitoring()

    def test_set_worker_operation_when_not_running(self):
        """Test setting operation when worker monitoring is not running."""
        stop_worker_monitoring()
        set_worker_operation("TEST_OP")  # Should not raise


# =============================================================================
# Helper function tests
# =============================================================================


class TestForceGcAndLog:
    """Tests for force_gc_and_log function."""

    def test_returns_float(self):
        """Test that force_gc_and_log returns a float."""
        result = force_gc_and_log("test context")
        assert isinstance(result, float)

    def test_gc_is_called(self):
        """Test that gc.collect is called."""
        with patch("control.core.memory_profiler.gc.collect") as mock_gc:
            force_gc_and_log("test")
            mock_gc.assert_called_once()

    def test_empty_context(self):
        """Test with empty context string."""
        result = force_gc_and_log()
        assert isinstance(result, float)


# =============================================================================
# Edge case and error handling tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in memory profiler."""

    def test_process_memory_handles_access_denied(self):
        """Test that get_process_memory_mb handles access denied gracefully."""
        with patch("psutil.Process") as mock_process:
            mock_process.side_effect = psutil.AccessDenied(pid=1234)
            result = get_process_memory_mb(1234)
            assert result == 0.0

    def test_process_memory_handles_no_such_process(self):
        """Test that get_process_memory_mb handles no such process gracefully."""
        with patch("psutil.Process") as mock_process:
            mock_process.side_effect = psutil.NoSuchProcess(pid=1234)
            result = get_process_memory_mb(1234)
            assert result == 0.0

    def test_all_squid_memory_handles_errors(self):
        """Test that get_all_squid_memory_mb handles errors gracefully."""
        with patch("psutil.Process") as mock_process:
            mock_process.side_effect = psutil.NoSuchProcess(pid=os.getpid())
            result = get_all_squid_memory_mb()
            assert result == {"main": 0.0, "children": 0.0, "total": 0.0, "child_details": []}

    def test_monitor_sample_error_recovery(self):
        """Test that monitor recovers from sampling errors."""
        monitor = MemoryMonitor(sample_interval_ms=50, enable_signals=False, track_children=False)
        monitor.start("TEST")

        # Even if there are errors, monitor should continue
        time.sleep(0.2)
        report = monitor.stop()

        # Should have some samples despite any errors
        assert report is not None
