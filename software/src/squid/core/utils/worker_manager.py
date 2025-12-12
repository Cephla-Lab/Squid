"""
Centralized worker management with timeout detection.

Provides a managed thread pool for long-running operations with:
- Automatic timeout detection
- Error containment
- Qt signal integration
- Debugging output on timeout (via faulthandler)

Based on storm-control's runWorkerTask() pattern.

Usage:
    from squid.core.utils.worker_manager import WorkerManager

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
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Optional, Any, Dict
from dataclasses import dataclass
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import squid.core.logging

_log = squid.core.logging.get_logger("squid.utils.worker_manager")


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
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
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
                    success=False, error=e, stack_trace=traceback.format_exc()
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
        on_error: Optional[Callable],
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
                success=False, error=e, stack_trace=traceback.format_exc()
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
        print(f"\n{'=' * 60}")
        print(f"TIMEOUT: Task '{task_name}' exceeded time limit")
        print("Full thread dump follows:")
        print(f"{'=' * 60}")
        faulthandler.dump_traceback()
        print(f"{'=' * 60}\n")

        # Emit timeout signal
        self.signals.timeout.emit(task_name)

        # Create timeout result
        result = WorkerResult(
            success=False,
            error=TimeoutError(f"Task '{task_name}' timed out"),
            timed_out=True,
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
