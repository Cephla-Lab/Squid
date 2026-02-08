"""Tests for BackpressureController lifecycle tracking and thread safety.

Ported from upstream commit be019383 to arch_v2 branch.
Tests verify:
- Lifecycle tracking (is_closed property, _warn_if_closed helper)
- Operations on closed controller return safe defaults
- Constructor with bp_values parameter (pre-warming path)
- reset() warning when jobs pending
- close() wakes blocked wait_for_capacity() threads
- Concurrent close() thread safety
"""

import logging
import threading
import time

import pytest

from squid.backend.controllers.multipoint.backpressure import (
    BackpressureController,
    BackpressureStats,
    BackpressureValues,
    create_backpressure_values,
)


class TestBackpressureControllerLifecycle:
    """Tests for BackpressureController lifecycle management."""

    def test_is_closed_property(self):
        """is_closed tracks lifecycle state."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        # Initially not closed
        assert controller.is_closed is False

        # After close, is_closed is True
        controller.close()
        assert controller.is_closed is True

        # Remains True after multiple closes
        controller.close()
        assert controller.is_closed is True

    def test_properties_return_none_after_close(self):
        """Shared value properties return None after close()."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        # Verify not None before close
        assert controller.pending_jobs_value is not None
        assert controller.pending_bytes_value is not None
        assert controller.capacity_event is not None

        controller.close()

        assert controller.pending_jobs_value is None
        assert controller.pending_bytes_value is None
        assert controller.capacity_event is None

    def test_close_is_idempotent(self):
        """close() can be called multiple times safely."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.close()
        controller.close()
        controller.close()
        assert controller.is_closed is True

    def test_should_throttle_on_closed_controller_returns_false(self):
        """should_throttle() returns False on closed controller."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001)
        controller.job_dispatched(10000)  # Exceed limits
        assert controller.should_throttle() is True

        controller.close()

        assert controller.should_throttle() is False

    def test_wait_for_capacity_returns_immediately_when_closed(self):
        """wait_for_capacity() returns True immediately on closed controller."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001, timeout_s=30.0)
        controller.job_dispatched(10000)  # Exceed limits
        controller.close()

        start = time.time()
        result = controller.wait_for_capacity()
        elapsed = time.time() - start

        assert result is True  # Should not block
        assert elapsed < 0.5  # Should return immediately

    def test_reset_on_closed_controller_is_noop(self):
        """reset() on closed controller is a no-op (no crash)."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.close()

        # Should not raise
        controller.reset()

    def test_get_pending_jobs_on_closed_controller_returns_zero(self):
        """get_pending_jobs() returns 0 on closed controller."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(1000)
        assert controller.get_pending_jobs() == 1

        controller.close()

        assert controller.get_pending_jobs() == 0

    def test_get_pending_mb_on_closed_controller_returns_zero(self):
        """get_pending_mb() returns 0.0 on closed controller."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(1024 * 1024)  # 1 MiB
        assert controller.get_pending_mb() >= 1.0

        controller.close()

        assert controller.get_pending_mb() == 0.0

    def test_get_stats_on_closed_controller_returns_zeroed_stats(self):
        """get_stats() returns zeroed stats on closed controller."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(5 * 1024 * 1024)

        # Verify stats before close
        stats_before = controller.get_stats()
        assert stats_before.pending_jobs == 1
        assert stats_before.pending_bytes_mb > 0

        controller.close()

        stats_after = controller.get_stats()
        assert stats_after.pending_jobs == 0
        assert stats_after.pending_bytes_mb == 0.0
        assert stats_after.is_throttled is False
        # Config values should still be available
        assert stats_after.max_pending_jobs == 10
        assert stats_after.max_pending_mb == 500.0

    def test_job_dispatched_on_closed_controller_is_noop(self):
        """job_dispatched() on closed controller is a no-op."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.close()

        # Should not raise
        controller.job_dispatched(1000)

        # Should remain at 0 (closed returns early)
        assert controller.get_pending_jobs() == 0


class TestBackpressureControllerPreWarming:
    """Tests for BackpressureController pre-warming with bp_values."""

    def test_constructor_with_bp_values_uses_provided_values(self):
        """Constructor uses pre-created bp_values instead of creating new ones."""
        bp_values = create_backpressure_values()
        jobs, bytes_, event = bp_values

        controller = BackpressureController(max_jobs=10, max_mb=500.0, bp_values=bp_values)

        # Should be using the same objects (not copies)
        assert controller.pending_jobs_value is jobs
        assert controller.pending_bytes_value is bytes_
        assert controller.capacity_event is event

        controller.close()

    def test_create_backpressure_values_returns_tuple(self):
        """create_backpressure_values() returns a tuple of 3 elements."""
        bp_values = create_backpressure_values()
        assert isinstance(bp_values, tuple)
        assert len(bp_values) == 3

    def test_bp_values_counters_start_at_zero(self):
        """Pre-created bp_values have zero counters."""
        bp_values = create_backpressure_values()
        controller = BackpressureController(max_jobs=10, max_mb=500.0, bp_values=bp_values)

        assert controller.get_pending_jobs() == 0
        assert controller.get_pending_mb() == 0.0

        controller.close()


class TestBackpressureControllerResetWarning:
    """Tests for reset() warning when jobs are pending."""

    def test_reset_warns_when_jobs_pending(self, caplog):
        """reset() logs warning when called with pending jobs."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)

        with caplog.at_level(logging.WARNING):
            controller.reset()

        assert "2 jobs pending" in caplog.text
        # Should still reset
        assert controller.get_pending_jobs() == 0

        controller.close()

    def test_reset_no_warning_when_no_jobs_pending(self, caplog):
        """reset() does not warn when no jobs are pending."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        with caplog.at_level(logging.WARNING):
            controller.reset()

        assert "jobs pending" not in caplog.text

        controller.close()


class TestBackpressureControllerThreadSafety:
    """Tests for BackpressureController thread safety."""

    def test_close_is_thread_safe(self):
        """close() handles concurrent calls safely."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        errors = []

        def close_worker():
            try:
                for _ in range(100):
                    controller.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=close_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert controller.is_closed is True

    def test_close_wakes_blocked_wait_for_capacity(self):
        """close() wakes threads blocked in wait_for_capacity()."""
        controller = BackpressureController(max_jobs=1, max_mb=500.0, timeout_s=30.0)
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)  # Exceed limit

        result = [None]
        elapsed = [None]

        def wait_worker():
            start = time.time()
            result[0] = controller.wait_for_capacity()
            elapsed[0] = time.time() - start

        thread = threading.Thread(target=wait_worker)
        thread.start()

        time.sleep(0.2)  # Let thread start waiting
        controller.close()  # Should wake the thread

        thread.join(timeout=2.0)

        assert not thread.is_alive(), "Thread should have been woken by close()"
        assert result[0] is True  # closed controller returns True
        assert elapsed[0] < 5.0  # Should not have waited full 30s timeout


class TestBackpressureControllerBasic:
    """Basic functionality tests for BackpressureController."""

    def test_throttle_when_jobs_exceeded(self):
        """should_throttle() returns True when job limit is exceeded."""
        controller = BackpressureController(max_jobs=2, max_mb=500.0)
        controller.job_dispatched(100)
        controller.job_dispatched(100)

        assert controller.should_throttle() is True

        controller.close()

    def test_throttle_when_bytes_exceeded(self):
        """should_throttle() returns True when byte limit is exceeded."""
        controller = BackpressureController(max_jobs=100, max_mb=0.001)
        controller.job_dispatched(2000)  # Exceeds ~1 KB limit

        assert controller.should_throttle() is True

        controller.close()

    def test_no_throttle_when_disabled(self):
        """should_throttle() returns False when disabled."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001, enabled=False)
        controller.job_dispatched(10000)

        assert controller.should_throttle() is False

        controller.close()

    def test_get_stats_returns_correct_values(self):
        """get_stats() returns accurate statistics."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(1024 * 1024)  # 1 MiB

        stats = controller.get_stats()
        assert stats.pending_jobs == 1
        assert abs(stats.pending_bytes_mb - 1.0) < 0.01
        assert stats.max_pending_jobs == 10
        assert stats.max_pending_mb == 500.0
        assert stats.is_throttled is False

        controller.close()

    def test_get_stats_atomic(self):
        """get_stats() acquires both locks for atomic snapshot."""
        controller = BackpressureController(max_jobs=2, max_mb=500.0)
        controller.job_dispatched(1024 * 1024)
        controller.job_dispatched(2 * 1024 * 1024)

        stats = controller.get_stats()
        assert stats.pending_jobs == 2
        assert stats.is_throttled is True  # 2 >= 2

        controller.close()

    def test_reset_clears_counters(self):
        """reset() zeroes all counters."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)
        controller.job_dispatched(1000)
        controller.job_dispatched(1000)

        assert controller.get_pending_jobs() == 2

        controller.reset()

        assert controller.get_pending_jobs() == 0
        assert controller.get_pending_mb() == 0.0

        controller.close()

    def test_wait_for_capacity_returns_true_when_not_throttled(self):
        """wait_for_capacity() returns True immediately when not throttled."""
        controller = BackpressureController(max_jobs=10, max_mb=500.0)

        result = controller.wait_for_capacity()
        assert result is True

        controller.close()

    def test_wait_for_capacity_returns_true_when_disabled(self):
        """wait_for_capacity() returns True immediately when disabled."""
        controller = BackpressureController(max_jobs=1, max_mb=0.001, enabled=False)
        controller.job_dispatched(10000)

        result = controller.wait_for_capacity()
        assert result is True

        controller.close()
