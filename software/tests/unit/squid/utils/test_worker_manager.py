"""Tests for WorkerManager utility."""

import pytest
import time
from squid.core.utils.worker_manager import WorkerManager, WorkerResult


class TestWorkerManager:
    """Test suite for WorkerManager."""

    @pytest.fixture
    def manager(self):
        """Create a WorkerManager for testing."""
        mgr = WorkerManager(max_workers=2)
        yield mgr
        mgr.shutdown(wait=False, timeout=0.1)

    def test_successful_task(self, manager, qtbot):
        """Successful task should complete with value."""
        results = []

        def on_complete(result):
            results.append(result)

        manager.submit(task_name="test_task", task=lambda: 42, on_complete=on_complete)

        # Wait for completion
        qtbot.waitUntil(lambda: len(results) == 1, timeout=1000)

        assert results[0].success is True
        assert results[0].value == 42
        assert results[0].error is None

    def test_failed_task(self, manager, qtbot):
        """Failed task should report error."""
        errors = []

        def on_error(result):
            errors.append(result)

        manager.submit(
            task_name="failing_task",
            task=lambda: 1 / 0,  # ZeroDivisionError
            on_error=on_error,
        )

        qtbot.waitUntil(lambda: len(errors) == 1, timeout=1000)

        assert errors[0].success is False
        assert isinstance(errors[0].error, ZeroDivisionError)
        assert errors[0].stack_trace is not None

    def test_timeout_detection(self, manager, qtbot):
        """Timeout should be detected and signaled."""
        timeouts = []

        manager.signals.timeout.connect(lambda name: timeouts.append(name))

        def slow_task():
            time.sleep(10)  # Very slow

        manager.submit(
            task_name="slow_task",
            task=slow_task,
            timeout_ms=100,  # 100ms timeout
        )

        qtbot.waitUntil(lambda: len(timeouts) == 1, timeout=1000)

        assert "slow_task" in timeouts

    def test_signals_emitted(self, manager, qtbot):
        """Signals should be emitted for task lifecycle."""
        started = []
        completed = []

        manager.signals.started.connect(lambda name: started.append(name))
        manager.signals.completed.connect(lambda name, result: completed.append(name))

        manager.submit(task_name="signal_test", task=lambda: "done")

        qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)

        assert "signal_test" in started
        assert "signal_test" in completed

    def test_shutdown_cancels_pending(self):
        """Shutdown should cancel pending futures."""
        manager = WorkerManager(max_workers=1)

        # Submit a slow task to block the worker
        manager.submit(task_name="blocking", task=lambda: time.sleep(10))

        # Shutdown should not hang
        manager.shutdown(wait=False, timeout=0.1)


class TestWorkerResult:
    """Test suite for WorkerResult dataclass."""

    def test_success_result(self):
        """Success result should have value, no error."""
        result = WorkerResult(success=True, value=42)
        assert result.success is True
        assert result.value == 42
        assert result.error is None
        assert result.timed_out is False

    def test_error_result(self):
        """Error result should have exception and trace."""
        error = ValueError("test")
        result = WorkerResult(success=False, error=error, stack_trace="traceback here")
        assert result.success is False
        assert result.error is error
        assert result.stack_trace == "traceback here"

    def test_timeout_result(self):
        """Timeout result should have timed_out flag."""
        result = WorkerResult(
            success=False, error=TimeoutError("task timed out"), timed_out=True
        )
        assert result.success is False
        assert result.timed_out is True
