# Squid Stability Improvements: Lessons from Storm-Control

This document extends [IMPROVEMENTS.md](IMPROVEMENTS.md) with architectural patterns from storm-control that address Squid's stability issues. Storm-control is 15 years old with verbose, outdated code—yet it rarely crashes. Squid is modern with clean abstractions—yet it crashes frequently. The difference is **architectural robustness**, not code quality.

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Root Cause Analysis](#2-root-cause-analysis)
3. [Pattern 1: Error Containment](#3-pattern-1-error-containment)
4. [Pattern 2: Worker Management with Timeouts](#4-pattern-2-worker-management-with-timeouts)
5. [Pattern 3: Thread-Safe Shared State](#5-pattern-3-thread-safe-shared-state)
6. [Pattern 4: GUI Decoupling](#6-pattern-4-gui-decoupling)
7. [Pattern 5: Operation Completion Tracking](#7-pattern-5-operation-completion-tracking)
8. [Incremental Migration Path](#8-incremental-migration-path)
9. [Appendix: Storm-Control Reference](#9-appendix-storm-control-reference)

---

## 1. Executive Summary

### The Paradox

| Aspect | Storm-Control | Squid |
|--------|---------------|-------|
| Age | ~15 years | ~5 years |
| Code style | Verbose, dated | Modern, clean |
| Type hints | 0% | ~12% |
| ABCs | None | Good (`squid/abc.py`) |
| **Crash frequency** | **Rare** | **Frequent** |

### Why Storm-Control is Stable

Storm-control's stability comes from five architectural patterns:

1. **Error Containment**: Exceptions are caught and attached to messages, not thrown
2. **Worker Timeout Detection**: Hung threads are detected with `faulthandler` debugging
3. **Thread-Safe State**: All shared state protected by mutexes
4. **GUI Decoupling**: GUI is just another module, not the owner of controllers
5. **Operation Completion Tracking**: Reference counting ensures operations complete

### Why Squid Crashes

Squid's crashes stem from:

1. **Uncontained Exceptions**: Exceptions propagate up to GUI and crash the app
2. **No Timeout Detection**: Hung threads go unnoticed until user kills the app
3. **Race Conditions**: Shared state accessed from multiple threads without locks
4. **Tight GUI Coupling**: GUI creates/owns controllers; any exception crashes everything
5. **Fire-and-Forget Operations**: No tracking of whether operations complete

---

## 2. Root Cause Analysis

### 2.1 Crash Category: Uncontained Exceptions

**Location**: Throughout codebase, especially `multi_point_worker.py`

**Problem**: Exceptions in callbacks or worker threads propagate and crash the application.

```python
# multi_point_worker.py:553-608 - _image_callback runs in camera thread
def _image_callback(self, camera_frame: CameraFrame):
    try:
        # ... processing ...
        if not info:
            self._log.error("...")
            self.request_abort_fn()  # But exception already propagating!
            return
    finally:
        self._image_callback_idle.set()
```

**Storm-control solution** (`halModule.py:315-329`):
```python
def nextMessage(self):
    message = self.queued_messages.popleft()
    try:
        self.processMessage(message)
    except Exception as exception:
        # Attach error to message instead of crashing
        message.addError(HalMessageError(
            source=self.module_name,
            message=str(exception),
            m_exception=exception,
            stack_trace=traceback.format_exc()
        ))
    # ALWAYS decrement ref count - operation completes even on error
    message.decRefCount(name=self.module_name)
```

### 2.2 Crash Category: Silent Failures Leading to Corruption

**Location**: `multi_point_worker.py:87-95`, `_def.py:27-45`, many camera files

**Problem**: Bare `except:` clauses swallow errors, leaving state corrupted.

```python
# multi_point_worker.py:87-95
try:
    pixel_factor = self.objectiveStore.get_pixel_size_factor()
    # ...
except Exception:  # Silent failure!
    self._pixel_size_um = None  # State now corrupted, will cause crash later
```

```python
# _def.py:27-45 - Nested bare excepts
try:
    actualvalue = json.loads(actualvalue)
except:  # Bare except
    try:
        actualvalue = int(str(actualvalue))
    except:  # Bare except
        try:
            actualvalue = float(actualvalue)
        except:  # Bare except
            actualvalue = str(actualvalue)
```

### 2.3 Crash Category: Race Conditions

**Location**: `multi_point_worker.py:127-137`

**Problem**: Shared state accessed from multiple threads without proper synchronization.

```python
# multi_point_worker.py:127-137
self._ready_for_next_trigger = threading.Event()
self._ready_for_next_trigger.set()
self._image_callback_idle = threading.Event()
self._image_callback_idle.set()
# This is protected by the threading event above (aka set after clear, take copy before set)
self._current_capture_info: Optional[CaptureInfo] = None  # <-- NOT PROTECTED!
```

The comment claims protection via Events, but Events don't provide mutual exclusion. Between `_image_callback_idle.clear()` and accessing `_current_capture_info`, another thread can modify it.

**Evidence of known issue** (`multi_point_worker.py:125`):
```python
# NOTE(imo): Once we do overlapping triggering, we'll want to keep a queue of images
```

### 2.4 Crash Category: Hung Threads

**Location**: All camera implementations, `multi_point_worker.py`

**Problem**: Threads can hang forever with no detection or recovery.

```python
# No timeout detection anywhere
# If camera.read_frame() hangs, the entire acquisition hangs forever
# User must kill the application
```

**Storm-control solution** (`halModule.py:289-304`):
```python
def handleWorkerTimer(self):
    """
    If this timer fires that means the worker took longer than
    expected to complete a task, so it is probably hung.
    """
    # Print complete traceback including all threads
    print("Full Traceback With Threads:")
    faulthandler.dump_traceback()

    e_string = f"HALWorker for '{self.module_name}' timed out!"
    raise halExceptions.HalException(e_string)  # Controlled crash with debug info
```

### 2.5 Crash Category: GUI Owns Everything

**Location**: `gui_hcs.py:267-400`

**Problem**: GUI creates and owns all controllers. Any exception anywhere crashes the entire app.

```python
# gui_hcs.py:330-338
self.multipointController: QtMultiPointController = None
self.streamHandler: core.QtStreamHandler = None
self.autofocusController: AutoFocusController = None
self.imageSaver: core.ImageSaver = core.ImageSaver()
self.imageDisplay: core.ImageDisplay = core.ImageDisplay()
self.trackingController: core.TrackingController = None
# GUI owns everything - no isolation
```

**Storm-control solution**: GUI is just another `HalModule` that receives messages. It doesn't create or own other modules.

---

## 3. Pattern 1: Error Containment

### The Pattern

Every operation that can fail should:
1. Catch all exceptions
2. Log the error with full context
3. Attach error to an operation/result object
4. Allow the caller to decide how to handle it
5. Never crash the application

### Implementation for Squid

Create a `SafeCallback` wrapper:

```python
# software/squid/utils/safe_callback.py
from typing import Callable, TypeVar, Generic, Optional
from dataclasses import dataclass, field
import traceback
import squid.logging

T = TypeVar('T')

@dataclass
class CallbackResult(Generic[T]):
    """Result of a callback execution with error handling."""
    success: bool
    value: Optional[T] = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None

    def raise_if_error(self):
        if self.error:
            raise self.error

def safe_callback(
    callback: Callable[..., T],
    *args,
    on_error: Optional[Callable[[Exception, str], None]] = None,
    **kwargs
) -> CallbackResult[T]:
    """
    Execute a callback with error containment.

    Instead of letting exceptions propagate and crash the app,
    this catches them and returns a result object.
    """
    log = squid.logging.get_logger("safe_callback")
    try:
        result = callback(*args, **kwargs)
        return CallbackResult(success=True, value=result)
    except Exception as e:
        stack = traceback.format_exc()
        log.error(f"Callback {callback.__name__} failed: {e}\n{stack}")

        if on_error:
            try:
                on_error(e, stack)
            except Exception as handler_error:
                log.error(f"Error handler also failed: {handler_error}")

        return CallbackResult(
            success=False,
            error=e,
            stack_trace=stack
        )
```

### Apply to MultiPointWorker

```python
# multi_point_worker.py - BEFORE
def _image_callback(self, camera_frame: CameraFrame):
    try:
        # ... processing that can crash ...
    finally:
        self._image_callback_idle.set()

# multi_point_worker.py - AFTER
def _image_callback(self, camera_frame: CameraFrame):
    result = safe_callback(
        self._process_camera_frame,
        camera_frame,
        on_error=lambda e, tb: self._handle_callback_error(e, tb)
    )
    self._image_callback_idle.set()

    if not result.success:
        # Don't crash - abort acquisition gracefully
        self._log.error(f"Image callback failed, aborting: {result.error}")
        self.request_abort_fn()

def _handle_callback_error(self, error: Exception, stack_trace: str):
    """Called when callback fails - can notify GUI, save state, etc."""
    # Save debug info for later analysis
    self._last_error = error
    self._last_stack_trace = stack_trace
```

---

## 4. Pattern 2: Worker Management with Timeouts

### The Pattern

All long-running operations should:
1. Run in a managed thread pool
2. Have configurable timeouts
3. Emit signals on completion/error (not throw exceptions)
4. Provide debugging output if they hang

### Implementation for Squid

```python
# software/squid/utils/worker_manager.py
import faulthandler
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, Future, TimeoutError
from typing import Callable, Optional, Any
from dataclasses import dataclass
from PyQt5.QtCore import QObject, pyqtSignal, QTimer
import squid.logging

@dataclass
class WorkerResult:
    """Result of a worker task."""
    success: bool
    value: Any = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None
    timed_out: bool = False

class WorkerSignals(QObject):
    """Signals emitted by workers."""
    started = pyqtSignal(str)  # task_name
    completed = pyqtSignal(str, object)  # task_name, WorkerResult
    error = pyqtSignal(str, object)  # task_name, WorkerResult
    timeout = pyqtSignal(str)  # task_name

class WorkerManager:
    """
    Centralized worker management with timeout detection.

    Based on storm-control's runWorkerTask() pattern.
    """

    def __init__(self, max_workers: int = 4):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._active_tasks: dict[str, Future] = {}
        self._timers: dict[str, QTimer] = {}
        self.signals = WorkerSignals()

    def submit(
        self,
        task_name: str,
        task: Callable[[], Any],
        timeout_ms: int = -1,  # -1 = no timeout
        on_complete: Optional[Callable[[WorkerResult], None]] = None,
        on_error: Optional[Callable[[WorkerResult], None]] = None,
    ) -> str:
        """
        Submit a task for execution with optional timeout.

        Args:
            task_name: Unique identifier for this task
            task: The callable to execute
            timeout_ms: Timeout in milliseconds (-1 for no timeout)
            on_complete: Callback when task completes successfully
            on_error: Callback when task fails or times out

        Returns:
            task_name for tracking
        """
        self._log.info(f"Submitting task: {task_name}")
        self.signals.started.emit(task_name)

        def wrapped_task():
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
        # Cancel timeout timer if it exists
        if task_name in self._timers:
            self._timers[task_name].stop()
            del self._timers[task_name]

        # Clean up active tasks
        if task_name in self._active_tasks:
            del self._active_tasks[task_name]

        try:
            result = future.result(timeout=0)  # Should be immediate
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
                on_complete(result)
        else:
            self._log.error(f"Task failed: {task_name}: {result.error}")
            self.signals.error.emit(task_name, result)
            if on_error:
                on_error(result)

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

        # Note: We can't actually kill the thread, but we can:
        # 1. Stop waiting for it
        # 2. Mark the operation as failed
        # 3. Allow the user to decide (abort acquisition, restart, etc.)

    def shutdown(self, wait: bool = True, timeout: float = 5.0):
        """Shut down the worker pool."""
        self._log.info("Shutting down worker manager")

        # Cancel all timers
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()

        # Shutdown executor
        self._executor.shutdown(wait=wait, cancel_futures=True)
```

### Qt threading stance (PyQt5)

- For GUI-adjacent work, stay inside the Qt threading model: use `QThreadPool/QRunnable` or `QtConcurrent.run` + `QFutureWatcher`, and rely on queued connections for all cross-thread UI updates.  
- Avoid mixing `QTimer` with `ThreadPoolExecutor` on the UI thread. If you need a timeout with Qt objects, host the `QTimer` in the main thread and keep workers in `QThreadPool`.  
- Keep imports going through `qtpy` so a future PyQt6 move is mechanical; target PyQt5 APIs today and avoid PyQt6-only features.  
- Apply bounded queues/backpressure between worker emissions and UI consumption: drop/merge display frames when full; block or abort acquisition frames on overflow.

### Apply to MultiPointWorker

```python
# In MultiPointWorker.__init__
self._worker_manager = WorkerManager()
self._worker_manager.signals.timeout.connect(self._on_worker_timeout)

# Replace direct thread spawning with managed workers
def run(self):
    # Instead of: threading.Thread(target=self._acquisition_loop).start()
    self._worker_manager.submit(
        task_name="acquisition_loop",
        task=self._acquisition_loop,
        timeout_ms=self._calculate_acquisition_timeout(),
        on_complete=lambda r: self.callbacks.signal_acquisition_finished(),
        on_error=lambda r: self._handle_acquisition_error(r)
    )

def _on_worker_timeout(self, task_name: str):
    """Handle worker timeout - abort gracefully instead of hanging."""
    self._log.error(f"Worker '{task_name}' timed out, aborting acquisition")
    self.request_abort_fn()
```

---

## 5. Pattern 3: Thread-Safe Shared State

### The Pattern

All state accessed from multiple threads must be:
1. Protected by a lock
2. Accessed through thread-safe methods
3. Never directly modified

### Implementation for Squid

```python
# software/squid/utils/thread_safe_state.py
from threading import Lock, RLock
from typing import TypeVar, Generic, Optional, Callable
from dataclasses import dataclass
from contextlib import contextmanager

T = TypeVar('T')

class ThreadSafeValue(Generic[T]):
    """
    Thread-safe wrapper for a single value.

    Usage:
        capture_info = ThreadSafeValue[CaptureInfo](None)

        # Set from one thread
        capture_info.set(new_info)

        # Get from another thread
        info = capture_info.get()

        # Atomic update
        capture_info.update(lambda x: x.with_timestamp(now()))
    """

    def __init__(self, initial_value: T = None):
        self._value: T = initial_value
        self._lock = Lock()

    def get(self) -> T:
        with self._lock:
            return self._value

    def set(self, value: T) -> None:
        with self._lock:
            self._value = value

    def update(self, updater: Callable[[T], T]) -> T:
        """Atomically update the value and return the new value."""
        with self._lock:
            self._value = updater(self._value)
            return self._value

    def get_and_clear(self) -> T:
        """Atomically get the value and set to None."""
        with self._lock:
            value = self._value
            self._value = None
            return value

    @contextmanager
    def locked(self):
        """Context manager for complex operations needing the lock."""
        with self._lock:
            yield self._value


class ThreadSafeFlag:
    """
    Thread-safe boolean flag with wait capability.

    Replaces patterns like:
        self._ready = threading.Event()
        self._ready.set()
        # ... later ...
        self._ready.wait()

    With clearer semantics and timeout handling.
    """

    def __init__(self, initial: bool = False):
        self._flag = initial
        self._lock = Lock()
        self._condition = threading.Condition(self._lock)

    def set(self) -> None:
        with self._condition:
            self._flag = True
            self._condition.notify_all()

    def clear(self) -> None:
        with self._condition:
            self._flag = False

    def is_set(self) -> bool:
        with self._lock:
            return self._flag

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for flag to be set. Returns True if set, False if timed out."""
        with self._condition:
            if self._flag:
                return True
            return self._condition.wait(timeout=timeout)

    def wait_and_clear(self, timeout: Optional[float] = None) -> bool:
        """Wait for flag, then clear it. Returns True if was set."""
        with self._condition:
            if not self._flag:
                if not self._condition.wait(timeout=timeout):
                    return False
            self._flag = False
            return True
```

### Apply to MultiPointWorker

```python
# BEFORE: multi_point_worker.py:127-137
self._ready_for_next_trigger = threading.Event()
self._image_callback_idle = threading.Event()
self._current_capture_info: Optional[CaptureInfo] = None  # NOT THREAD-SAFE!

# AFTER:
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

self._ready_for_next_trigger = ThreadSafeFlag(initial=True)
self._image_callback_idle = ThreadSafeFlag(initial=True)
self._current_capture_info = ThreadSafeValue[CaptureInfo](None)

# In _image_callback:
def _image_callback(self, camera_frame: CameraFrame):
    if self._ready_for_next_trigger.is_set():
        self._log.warning("Got image without trigger, ignoring")
        return

    self._image_callback_idle.clear()
    try:
        # Atomically get and clear the capture info
        info = self._current_capture_info.get_and_clear()
        self._ready_for_next_trigger.set()

        if not info:
            self._log.error("No capture info!")
            self.request_abort_fn()
            return

        # ... rest of processing ...
    finally:
        self._image_callback_idle.set()

# In acquire_camera_image:
def acquire_camera_image(self, ...):
    # ...
    # Thread-safe set of capture info before trigger
    self._current_capture_info.set(current_capture_info)
    self.camera.send_trigger(...)
```

---

## 6. Pattern 4: GUI Decoupling

### The Pattern

The GUI should:
1. NOT create controllers
2. NOT own hardware references
3. Only receive events and display state
4. Send user actions to a mediator

### Implementation for Squid

Create an `ApplicationContext` that builds everything:

```python
# software/squid/application.py
from dataclasses import dataclass
from typing import Optional
import squid.logging
from control.microscope import Microscope
from control.core.live_controller import LiveController
from control.core.multi_point_controller import MultiPointController
from control.core.auto_focus_controller import AutoFocusController
# ... other imports ...

@dataclass
class Controllers:
    """All controllers, pre-built and ready to use."""
    live: LiveController
    multipoint: MultiPointController
    autofocus: Optional[AutoFocusController]
    laser_autofocus: Optional[LaserAutofocusController]
    stream_handler: StreamHandler
    # ... etc ...

class ApplicationContext:
    """
    Application-level context that owns all components.

    This replaces the pattern where GUI creates everything.
    Now: Application creates everything, GUI just displays.
    """

    def __init__(self, config_path: str, simulation: bool = False):
        self._log = squid.logging.get_logger(self.__class__.__name__)

        # Build hardware
        self._log.info("Building microscope...")
        self.microscope = Microscope.build_from_global_config(simulation=simulation)

        # Build controllers
        self._log.info("Building controllers...")
        self.controllers = self._build_controllers()

        # GUI will be set later
        self.gui: Optional[HighContentScreeningGui] = None

    def _build_controllers(self) -> Controllers:
        """Build all controllers with proper dependency injection."""

        live = LiveController(
            camera=self.microscope.camera,
            microcontroller=self.microscope.low_level_drivers.microcontroller,
            illumination_controller=self.microscope.illumination_controller,
        )

        stream_handler = StreamHandler(
            accept_new_frame_fn=lambda: live.is_live
        )

        autofocus = AutoFocusController(
            camera=self.microscope.camera,
            stage=self.microscope.stage,
            live_controller=live,
            microcontroller=self.microscope.low_level_drivers.microcontroller,
            # ... callbacks will be connected later ...
        ) if ENABLE_AUTOFOCUS else None

        multipoint = MultiPointController(
            microscope=self.microscope,
            live_controller=live,
            autofocus_controller=autofocus,
            # ...
        )

        return Controllers(
            live=live,
            multipoint=multipoint,
            autofocus=autofocus,
            stream_handler=stream_handler,
        )

    def create_gui(self) -> 'HighContentScreeningGui':
        """Create GUI with pre-built controllers."""
        from control.gui_hcs import HighContentScreeningGui

        self.gui = HighContentScreeningGui(
            controllers=self.controllers,
            microscope=self.microscope,  # For display only, not control
        )
        return self.gui

    def run(self):
        """Run the application."""
        self.create_gui()
        self.gui.show()
        # ... Qt event loop ...

    def shutdown(self):
        """Clean shutdown of all components."""
        self._log.info("Shutting down application...")

        if self.gui:
            self.gui.close()

        # Shutdown controllers in reverse order
        if self.controllers.multipoint:
            self.controllers.multipoint.shutdown()
        if self.controllers.live:
            self.controllers.live.stop()

        # Shutdown hardware
        self.microscope.shutdown()

# New entry point
def main():
    app = QApplication(sys.argv)

    context = ApplicationContext(
        config_path="configurations/configuration.ini",
        simulation="--simulation" in sys.argv
    )

    try:
        context.run()
        return app.exec_()
    finally:
        context.shutdown()
```

### Modified GUI

```python
# gui_hcs.py - AFTER
class HighContentScreeningGui(QMainWindow):

    def __init__(
        self,
        controllers: Controllers,  # Receive pre-built controllers
        microscope: Microscope,    # For display info only
        *args, **kwargs
    ):
        super().__init__(*args, **kwargs)

        # Store references (but don't own/create them)
        self.controllers = controllers
        self.microscope = microscope

        # Connect to controller signals for display
        self.controllers.multipoint.acquisition_finished.connect(
            self._on_acquisition_finished
        )
        self.controllers.live.frame_ready.connect(
            self._on_frame_ready
        )

        # Build UI
        self._setup_ui()

    def _on_start_acquisition_clicked(self):
        """User clicked start - delegate to controller."""
        # GUI doesn't do the work, just tells controller
        params = self._build_acquisition_params_from_ui()
        self.controllers.multipoint.start_acquisition(params)

    def _on_acquisition_finished(self):
        """Controller says acquisition done - update UI."""
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("Acquisition complete")
```

---

## 7. Pattern 5: Operation Completion Tracking

### The Pattern

Every operation should:
1. Have a unique ID
2. Track its state (pending, running, completed, failed)
3. Notify when complete
4. Allow waiting for completion

Storm-control uses reference counting on messages. For Squid, we can use a simpler operation tracker.

### Implementation for Squid

```python
# software/squid/utils/operation_tracker.py
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any
from threading import Lock, Event
from datetime import datetime
import uuid
import squid.logging

class OperationState(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

@dataclass
class Operation:
    """Tracks a single operation through its lifecycle."""
    id: str
    name: str
    state: OperationState = OperationState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[Exception] = None
    result: Any = None

    # Event for waiting on completion
    _completion_event: Event = field(default_factory=Event)

    def mark_started(self):
        self.state = OperationState.RUNNING
        self.started_at = datetime.now()

    def mark_completed(self, result: Any = None):
        self.state = OperationState.COMPLETED
        self.completed_at = datetime.now()
        self.result = result
        self._completion_event.set()

    def mark_failed(self, error: Exception):
        self.state = OperationState.FAILED
        self.completed_at = datetime.now()
        self.error = error
        self._completion_event.set()

    def mark_cancelled(self):
        self.state = OperationState.CANCELLED
        self.completed_at = datetime.now()
        self._completion_event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for operation to complete. Returns True if completed, False if timed out."""
        return self._completion_event.wait(timeout=timeout)

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            OperationState.COMPLETED,
            OperationState.FAILED,
            OperationState.CANCELLED
        )

class OperationTracker:
    """
    Tracks all operations and ensures they complete.

    Usage:
        tracker = OperationTracker()

        # Start an operation
        op = tracker.start("capture_image")

        try:
            image = camera.capture()
            tracker.complete(op.id, result=image)
        except Exception as e:
            tracker.fail(op.id, error=e)

        # Wait for all operations
        tracker.wait_all(timeout=10.0)
    """

    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._operations: Dict[str, Operation] = {}
        self._lock = Lock()
        self._on_complete_callbacks: list[Callable[[Operation], None]] = []

    def start(self, name: str) -> Operation:
        """Start tracking a new operation."""
        op_id = str(uuid.uuid4())[:8]
        op = Operation(id=op_id, name=name)
        op.mark_started()

        with self._lock:
            self._operations[op_id] = op

        self._log.debug(f"Operation started: {name} ({op_id})")
        return op

    def complete(self, op_id: str, result: Any = None):
        """Mark an operation as completed."""
        with self._lock:
            if op_id not in self._operations:
                self._log.warning(f"Unknown operation: {op_id}")
                return
            op = self._operations[op_id]

        op.mark_completed(result)
        self._log.debug(f"Operation completed: {op.name} ({op_id})")
        self._notify_complete(op)

    def fail(self, op_id: str, error: Exception):
        """Mark an operation as failed."""
        with self._lock:
            if op_id not in self._operations:
                self._log.warning(f"Unknown operation: {op_id}")
                return
            op = self._operations[op_id]

        op.mark_failed(error)
        self._log.error(f"Operation failed: {op.name} ({op_id}): {error}")
        self._notify_complete(op)

    def cancel(self, op_id: str):
        """Cancel an operation."""
        with self._lock:
            if op_id not in self._operations:
                return
            op = self._operations[op_id]

        op.mark_cancelled()
        self._log.info(f"Operation cancelled: {op.name} ({op_id})")
        self._notify_complete(op)

    def get(self, op_id: str) -> Optional[Operation]:
        """Get an operation by ID."""
        with self._lock:
            return self._operations.get(op_id)

    def wait_all(self, timeout: Optional[float] = None) -> bool:
        """Wait for all operations to complete."""
        with self._lock:
            pending = [op for op in self._operations.values() if not op.is_terminal]

        for op in pending:
            remaining = timeout  # Simplified - should track elapsed time
            if not op.wait(timeout=remaining):
                return False
        return True

    def get_pending(self) -> list[Operation]:
        """Get all pending/running operations."""
        with self._lock:
            return [op for op in self._operations.values() if not op.is_terminal]

    def on_complete(self, callback: Callable[[Operation], None]):
        """Register a callback for when any operation completes."""
        self._on_complete_callbacks.append(callback)

    def _notify_complete(self, op: Operation):
        for callback in self._on_complete_callbacks:
            try:
                callback(op)
            except Exception as e:
                self._log.error(f"Completion callback failed: {e}")

    def cleanup_completed(self, max_age_seconds: float = 300):
        """Remove old completed operations to prevent memory growth."""
        now = datetime.now()
        with self._lock:
            to_remove = [
                op_id for op_id, op in self._operations.items()
                if op.is_terminal and
                   (now - op.completed_at).total_seconds() > max_age_seconds
            ]
            for op_id in to_remove:
                del self._operations[op_id]
```

---

## 8. Incremental Migration Path

### Phase 1: Error Containment (Week 1)
**Impact: Immediate crash reduction**

1. Add `safe_callback.py` utility
2. Wrap all camera callbacks in `safe_callback()`
3. Wrap all signal handlers in `safe_callback()`
4. Fix all bare `except:` clauses (50+ locations)

**Files to modify:**
- `multi_point_worker.py` - `_image_callback`
- `stream_handler.py` - frame callbacks
- `camera_*.py` - all frame callbacks
- `_def.py` - config parsing

### Phase 2: Worker Management (Week 2)
**Impact: Prevents hangs, enables debugging**

1. Add `worker_manager.py` utility
2. Replace direct `threading.Thread()` with `WorkerManager.submit()`
3. Add timeouts to all long operations
4. Add `faulthandler` dump on timeout

**Files to modify:**
- `multi_point_worker.py` - acquisition loop
- `auto_focus_controller.py` - autofocus routine
- `camera_*.py` - frame reading threads

### Phase 3: Thread-Safe State (Week 3)
**Impact: Eliminates race condition crashes**

1. Add `thread_safe_state.py` utilities
2. Replace all shared state with `ThreadSafeValue`
3. Replace `threading.Event` patterns with `ThreadSafeFlag`
4. Audit all cross-thread state access

**Files to modify:**
- `multi_point_worker.py` - `_current_capture_info`, Events
- `live_controller.py` - `is_live` flag
- `camera_*.py` - frame buffers, flags

### Phase 4: GUI Decoupling (Week 4-5)
**Impact: Long-term stability, testability**

1. Create `ApplicationContext` class
2. Create `Controllers` dataclass
3. Modify `gui_hcs.py` to receive controllers
4. Move controller creation out of GUI
5. Update entry point

**Files to modify:**
- New: `squid/application.py`
- `gui_hcs.py` - major refactor
- `main_hcs.py` - use new entry point

### Phase 5: Operation Tracking (Week 6)
**Impact: Ensures operations complete**

1. Add `operation_tracker.py` utility
2. Track all acquisitions
3. Track all stage movements
4. Add completion verification

**Files to modify:**
- `multi_point_controller.py`
- `stage/*.py`
- `camera_*.py`

---

## 9. Appendix: Storm-Control Reference

### Key Files to Study

| File | Pattern | Lines |
|------|---------|-------|
| `halModule.py` | Worker management, error containment | 29-58, 262-278, 315-329 |
| `halMessage.py` | Reference counting, completion tracking | 293-301 |
| `hal4000.py` | Message broker, module isolation | 379-463 |

### Key Code Snippets

**Error Containment** (`halModule.py:315-329`):
```python
def nextMessage(self):
    message = self.queued_messages.popleft()
    try:
        self.processMessage(message)
    except Exception as exception:
        message.addError(HalMessageError(
            source=self.module_name,
            message=str(exception),
            m_exception=exception,
            stack_trace=traceback.format_exc()
        ))
    message.decRefCount(name=self.module_name)
```

**Worker with Timeout** (`halModule.py:29-58, 289-304`):
```python
def runWorkerTask(module, message, task, job_time_ms=None):
    ct_task = HalWorker(job_time_ms=job_time_ms, message=message, task=task)
    ct_task.hwsignaler.workerDone.connect(module.handleWorkerDone)
    ct_task.hwsignaler.workerError.connect(module.handleWorkerError)
    threadpool.start(ct_task)

def handleWorkerTimer(self):
    """Fires when worker exceeds time limit."""
    faulthandler.dump_traceback()
    raise halExceptions.HalException(f"Worker timed out!")
```

**Reference Counting** (`halMessage.py:293-301`):
```python
def decRefCount(self, name=None):
    hdebug.logText(f"handled by,{self.m_id},{name},{self.m_type}")
    self.ref_count -= 1
    if self.ref_count == 0:
        self.processed.emit(self)  # All modules done, finalize
```

---

## Summary

Squid crashes not because its code is bad, but because it lacks architectural robustness. Storm-control's 15-year-old patterns provide battle-tested solutions:

1. **Catch and contain exceptions** instead of letting them propagate
2. **Detect hung operations** with timeouts and debugging output
3. **Protect shared state** with proper synchronization
4. **Decouple GUI from business logic** so failures are isolated
5. **Track operation completion** to ensure nothing is lost

Implementing these patterns incrementally will dramatically reduce crashes while maintaining Squid's modern, clean codebase.
