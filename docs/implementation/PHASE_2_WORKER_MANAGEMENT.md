# Phase 2: Worker Management

**Goal**: Add timeout detection and debugging capabilities for long-running operations.

**Impact**: Prevents application hangs by detecting stuck threads.

**Estimated Effort**: 3 days

---

## Checklist

### Task 2.1: Create worker_manager utility
- [ ] Create test file `software/tests/squid/utils/test_worker_manager.py`
- [ ] Run tests (should fail)
- [ ] Create `software/squid/utils/worker_manager.py`
- [ ] Run tests (should pass)
- [ ] Commit: "Add WorkerManager with timeout detection"

### Task 2.2: Enable faulthandler at startup
- [ ] Read `software/main_hcs.py`
- [ ] Add `import faulthandler; faulthandler.enable()` at startup
- [ ] Test by running application
- [ ] Commit: "Enable faulthandler for debugging hangs"

### Task 2.3: Add timeout detection to MultiPointWorker (Optional)
- [ ] Add WorkerManager to MultiPointWorker
- [ ] Configure acquisition timeout
- [ ] Connect timeout handler
- [ ] Test with simulation
- [ ] Commit: "Add timeout detection to MultiPointWorker"

---

## Task 2.1: Create worker_manager utility

### Test File

**File**: `software/tests/squid/utils/test_worker_manager.py`

```python
"""Tests for WorkerManager utility."""
import pytest
import time
from squid.utils.worker_manager import WorkerManager, WorkerResult


class TestWorkerManager:
    """Test suite for WorkerManager."""

    def test_successful_task(self, qtbot):
        """Successful task should complete with value."""
        manager = WorkerManager(max_workers=2)
        results = []

        def on_complete(result):
            results.append(result)

        manager.submit(
            task_name="test_task",
            task=lambda: 42,
            on_complete=on_complete
        )

        # Wait for completion
        qtbot.waitUntil(lambda: len(results) == 1, timeout=1000)

        assert results[0].success is True
        assert results[0].value == 42
        assert results[0].error is None
        manager.shutdown()

    def test_failed_task(self, qtbot):
        """Failed task should report error."""
        manager = WorkerManager(max_workers=2)
        errors = []

        def on_error(result):
            errors.append(result)

        manager.submit(
            task_name="failing_task",
            task=lambda: 1/0,  # ZeroDivisionError
            on_error=on_error
        )

        qtbot.waitUntil(lambda: len(errors) == 1, timeout=1000)

        assert errors[0].success is False
        assert isinstance(errors[0].error, ZeroDivisionError)
        assert errors[0].stack_trace is not None
        manager.shutdown()

    def test_timeout_detection(self, qtbot):
        """Timeout should be detected and signaled."""
        manager = WorkerManager(max_workers=2)
        timeouts = []

        manager.signals.timeout.connect(lambda name: timeouts.append(name))

        def slow_task():
            time.sleep(10)  # Very slow

        manager.submit(
            task_name="slow_task",
            task=slow_task,
            timeout_ms=100  # 100ms timeout
        )

        qtbot.waitUntil(lambda: len(timeouts) == 1, timeout=1000)

        assert "slow_task" in timeouts
        manager.shutdown()

    def test_signals_emitted(self, qtbot):
        """Signals should be emitted for task lifecycle."""
        manager = WorkerManager(max_workers=2)
        started = []
        completed = []

        manager.signals.started.connect(lambda name: started.append(name))
        manager.signals.completed.connect(lambda name, result: completed.append(name))

        manager.submit(
            task_name="signal_test",
            task=lambda: "done"
        )

        qtbot.waitUntil(lambda: len(completed) == 1, timeout=1000)

        assert "signal_test" in started
        assert "signal_test" in completed
        manager.shutdown()

    def test_shutdown_cancels_pending(self):
        """Shutdown should cancel pending futures."""
        manager = WorkerManager(max_workers=1)

        # Submit a slow task to block the worker
        manager.submit(
            task_name="blocking",
            task=lambda: time.sleep(10)
        )

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
        result = WorkerResult(
            success=False,
            error=error,
            stack_trace="traceback here"
        )
        assert result.success is False
        assert result.error is error
        assert result.stack_trace == "traceback here"

    def test_timeout_result(self):
        """Timeout result should have timed_out flag."""
        result = WorkerResult(
            success=False,
            error=TimeoutError("task timed out"),
            timed_out=True
        )
        assert result.success is False
        assert result.timed_out is True
```

### Implementation File

**File**: `software/squid/utils/worker_manager.py`

```python
"""
Centralized worker management with timeout detection.

Provides a managed thread pool for long-running operations with:
- Automatic timeout detection
- Error containment
- Qt signal integration
- Debugging output on timeout (via faulthandler)

Based on storm-control's runWorkerTask() pattern.

Usage:
    from squid.utils.worker_manager import WorkerManager

    manager = WorkerManager(max_workers=4)

    manager.submit(
        task_name="my_task",
        task=lambda: do_something(),
        timeout_ms=5000,
        on_complete=lambda r: print(f"Done: {r.value}"),
        on_error=lambda r: print(f"Failed: {r.error}")
    )
"""
import faulthandler
import traceback
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError as FuturesTimeoutError
from typing import Callable, Optional, Any, Dict
from dataclasses import dataclass
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import squid.logging

_log = squid.logging.get_logger("squid.utils.worker_manager")


@dataclass
class WorkerResult:
    """
    Result of a worker task.

    Attributes:
        success: True if task completed without error
        value: Return value of task (None if failed)
        error: Exception if task failed
        stack_trace: Formatted traceback if task failed
        timed_out: True if task exceeded timeout
    """
    success: bool
    value: Any = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None
    timed_out: bool = False


class WorkerSignals(QObject):
    """Qt signals emitted by WorkerManager."""
    started = pyqtSignal(str)  # task_name
    completed = pyqtSignal(str, object)  # task_name, WorkerResult
    error = pyqtSignal(str, object)  # task_name, WorkerResult
    timeout = pyqtSignal(str)  # task_name


class WorkerManager:
    """
    Centralized worker management with timeout detection.

    Based on storm-control's runWorkerTask() pattern, but adapted
    for Squid's architecture.

    Example:
        manager = WorkerManager(max_workers=4)

        # Submit a task with timeout
        manager.submit(
            task_name="acquisition",
            task=lambda: acquire_images(),
            timeout_ms=60000,  # 1 minute
            on_complete=handle_success,
            on_error=handle_failure
        )

        # Connect to timeout signal for UI feedback
        manager.signals.timeout.connect(show_timeout_warning)
    """

    def __init__(self, max_workers: int = 4):
        """
        Initialize the worker manager.

        Args:
            max_workers: Maximum concurrent workers
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._active_tasks: Dict[str, Future] = {}
        self._timers: Dict[str, QTimer] = {}
        self.signals = WorkerSignals()

    def submit(
        self,
        task_name: str,
        task: Callable[[], Any],
        timeout_ms: int = -1,
        on_complete: Optional[Callable[[WorkerResult], None]] = None,
        on_error: Optional[Callable[[WorkerResult], None]] = None,
    ) -> str:
        """
        Submit a task for execution with optional timeout.

        Args:
            task_name: Unique identifier for this task
            task: The callable to execute (takes no arguments)
            timeout_ms: Timeout in milliseconds (-1 = no timeout)
            on_complete: Callback when task completes successfully
            on_error: Callback when task fails or times out

        Returns:
            task_name for tracking
        """
        self._log.info(f"Submitting task: {task_name}")
        self.signals.started.emit(task_name)

        def wrapped_task():
            """Wrap task to catch exceptions."""
            try:
                result = task()
                return WorkerResult(success=True, value=result)
            except Exception as e:
                return WorkerResult(
                    success=False,
                    error=e,
                    stack_trace=traceback.format_exc()
                )

        future = self._executor.submit(wrapped_task)
        self._active_tasks[task_name] = future

        # Set up completion callback
        def on_done(f: Future):
            self._handle_completion(task_name, f, on_complete, on_error)
        future.add_done_callback(on_done)

        # Set up timeout if requested
        if timeout_ms > 0:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._handle_timeout(task_name))
            timer.start(timeout_ms)
            self._timers[task_name] = timer

        return task_name

    def _handle_completion(
        self,
        task_name: str,
        future: Future,
        on_complete: Optional[Callable],
        on_error: Optional[Callable]
    ):
        """Handle task completion."""
        # Cancel timeout timer if it exists
        if task_name in self._timers:
            self._timers[task_name].stop()
            del self._timers[task_name]

        # Clean up active tasks
        if task_name in self._active_tasks:
            del self._active_tasks[task_name]

        try:
            result = future.result(timeout=0)
        except Exception as e:
            result = WorkerResult(
                success=False,
                error=e,
                stack_trace=traceback.format_exc()
            )

        if result.success:
            self._log.info(f"Task completed: {task_name}")
            self.signals.completed.emit(task_name, result)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as e:
                    self._log.error(f"on_complete callback failed: {e}")
        else:
            self._log.error(f"Task failed: {task_name}: {result.error}")
            self.signals.error.emit(task_name, result)
            if on_error:
                try:
                    on_error(result)
                except Exception as e:
                    self._log.error(f"on_error callback failed: {e}")

    def _handle_timeout(self, task_name: str):
        """
        Handle a timed-out task.

        Like storm-control, we dump full thread state for debugging.
        """
        self._log.error(f"Task timed out: {task_name}")

        # Dump full thread state for debugging
        print(f"\n{'='*60}")
        print(f"TIMEOUT: Task '{task_name}' exceeded time limit")
        print(f"Full thread dump follows:")
        print(f"{'='*60}")
        faulthandler.dump_traceback()
        print(f"{'='*60}\n")

        # Emit timeout signal
        self.signals.timeout.emit(task_name)

        # Create timeout result
        result = WorkerResult(
            success=False,
            error=TimeoutError(f"Task '{task_name}' timed out"),
            timed_out=True
        )
        self.signals.error.emit(task_name, result)

        # Clean up
        if task_name in self._timers:
            del self._timers[task_name]

    def shutdown(self, wait: bool = True, timeout: float = 5.0):
        """
        Shut down the worker pool.

        Args:
            wait: Whether to wait for pending tasks
            timeout: Maximum time to wait
        """
        self._log.info("Shutting down worker manager")

        # Cancel all timers
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()

        # Shutdown executor
        self._executor.shutdown(wait=wait, cancel_futures=True)
```

### Update __init__.py

Add to `software/squid/utils/__init__.py`:

```python
from squid.utils.worker_manager import WorkerManager, WorkerResult, WorkerSignals

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
    "WorkerManager",
    "WorkerResult",
    "WorkerSignals",
]
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/utils/test_worker_manager.py -v
```

**Note**: Tests require `pytest-qt` for the `qtbot` fixture:
```bash
pip install pytest-qt
```

### Commit

```bash
git add software/squid/utils/worker_manager.py software/tests/squid/utils/test_worker_manager.py
git commit -m "Add WorkerManager with timeout detection

Provides centralized worker management with:
- ThreadPoolExecutor for managed execution
- Configurable timeouts per task
- faulthandler.dump_traceback() on timeout for debugging
- Qt signals for lifecycle events (started, completed, error, timeout)
- Error containment via WorkerResult

Based on storm-control's runWorkerTask() pattern.

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 4.
"
```

---

## Task 2.2: Enable faulthandler at startup

### File to Modify

**File**: `software/main_hcs.py`

### Changes

Add near the top of the file (after initial imports):

```python
import faulthandler

# Enable faulthandler to print traceback on segfault or deadlock
# This helps debug hung threads and crashes
faulthandler.enable()
```

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
# Application should start normally
# If it hangs, you'll now see a traceback
```

### Commit

```bash
git add software/main_hcs.py
git commit -m "Enable faulthandler for debugging hangs

Adds faulthandler.enable() at startup to print full thread
tracebacks on segfault or when triggered by WorkerManager timeout.

This makes hung threads much easier to debug.
"
```

---

## Task 2.3: Add timeout detection to MultiPointWorker (Optional)

This task is optional but recommended for critical acquisition paths.

### File to Modify

**File**: `software/control/core/multi_point_worker.py`

### Changes

1. Add import:
```python
from squid.utils.worker_manager import WorkerManager
```

2. In `__init__`, add:
```python
# Worker manager for timeout detection
self._worker_manager = WorkerManager(max_workers=2)
self._worker_manager.signals.timeout.connect(self._on_worker_timeout)

# Configurable acquisition timeout (default 5 minutes)
self._acquisition_timeout_ms = 300000
```

3. Add timeout handler:
```python
def _on_worker_timeout(self, task_name: str):
    """Handle worker timeout - abort gracefully instead of hanging."""
    self._log.error(f"Worker '{task_name}' timed out, aborting acquisition")
    self.request_abort_fn()
```

4. (Optional) Use WorkerManager for acquisition loop:
```python
def run(self):
    """Run the acquisition - wrapped with timeout detection."""
    self._worker_manager.submit(
        task_name="acquisition_loop",
        task=self._acquisition_loop,
        timeout_ms=self._calculate_acquisition_timeout(),
        on_complete=lambda r: self.callbacks.signal_acquisition_finished(),
        on_error=lambda r: self._handle_acquisition_error(r)
    )
```

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
# Run an acquisition
# Verify it completes normally
```

### Commit

```bash
git commit -m "Add timeout detection to MultiPointWorker

Adds WorkerManager to detect hung acquisitions. If acquisition
exceeds timeout, faulthandler prints thread dump and acquisition
aborts gracefully instead of hanging forever.
"
```

---

## Phase 2 Complete

After completing all tasks:

1. Run full test suite:
```bash
pytest --tb=short -v
```

2. Manual smoke test:
```bash
python main_hcs.py --simulation
# Run acquisition
# Verify timeout detection works (optional: add a deliberate hang)
```
