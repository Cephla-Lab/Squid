# Service Layer Synchronization V2 - Implementation Guide

## Overview

This document describes how to fix UI freezes and crashes by properly using existing utilities that are already in the codebase but underutilized:

| Utility | Purpose | Current Usage | This Guide |
|---------|---------|---------------|------------|
| `safe_callback` | Catch exceptions in callbacks | 1 place | Apply everywhere |
| `WorkerManager` | Run long ops with timeout | Instantiated but unused | Actually use it |
| `ThreadSafeState` | Protect shared state | MultiPointWorker only | Apply to streaming |

**Principles:**
- **TDD**: Write tests first, then implementation
- **DRY**: Use existing utilities, don't reinvent
- **YAGNI**: Fix actual problems, don't over-engineer
- **Frequent commits**: One commit per task

---

## The Problem

### UI Freezes

**NavigationWidget** (`control/widgets/stage.py:483-499`):
```python
def move_x_forward(self):
    self._service.move_x(self.entry_dX.value())  # BLOCKS MAIN THREAD
```

The `move_x()` call is blocking by default. While the stage moves, the entire Qt UI is frozen.

### Crashes

**StageUtils** (`control/widgets/stage.py:195-205`):
```python
def _callback_loading_position_reached(self, success, error_message):
    self.btn_load_slide.setStyleSheet("...")  # UI UPDATE FROM WORKER THREAD!
```

This callback runs on a daemon thread but updates Qt widgets directly. Qt crashes when UI is modified from non-main thread.

**ImageDisplay** (`control/core/image_display.py:52-59`):
```python
try:
    [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
    # ... process ...
except:
    pass  # SILENTLY SWALLOWS ALL ERRORS
```

Exceptions are silently ignored, causing mysterious failures.

---

## Existing Utilities Reference

### safe_callback (`squid/utils/safe_callback.py`)

Wraps a function call to catch exceptions and return a result object.

```python
from squid.utils.safe_callback import safe_callback, CallbackResult

# Instead of:
try:
    result = risky_function(arg1, arg2)
except Exception as e:
    log.error(f"Failed: {e}")
    result = None

# Use:
result = safe_callback(risky_function, arg1, arg2)
if result.success:
    use(result.value)
else:
    log.error(f"Failed: {result.error}")
```

**API:**
```python
def safe_callback(
    callback: Callable[..., T],
    *args: Any,
    on_error: Optional[Callable[[Exception, str], None]] = None,
    **kwargs: Any
) -> CallbackResult[T]

@dataclass
class CallbackResult(Generic[T]):
    success: bool
    value: Optional[T] = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None

    def raise_if_error(self) -> None: ...
```

### WorkerManager (`squid/utils/worker_manager.py`)

Runs tasks in a thread pool with timeout detection and Qt signal integration.

```python
from squid.utils.worker_manager import WorkerManager, WorkerResult

manager = WorkerManager(max_workers=4)

# Submit a task
manager.submit(
    task_name="move_stage",
    task=lambda: stage.move_x(10.0),
    timeout_ms=60000,  # 1 minute
    on_complete=lambda r: print(f"Done: {r.value}"),
    on_error=lambda r: print(f"Failed: {r.error}")
)

# Connect to signals for UI feedback
manager.signals.timeout.connect(show_timeout_warning)
manager.signals.completed.connect(re_enable_buttons)
```

**API:**
```python
class WorkerManager:
    signals: WorkerSignals  # Qt signals: started, completed, error, timeout

    def submit(
        self,
        task_name: str,
        task: Callable[[], Any],
        timeout_ms: int = -1,
        on_complete: Optional[Callable[[WorkerResult], None]] = None,
        on_error: Optional[Callable[[WorkerResult], None]] = None,
    ) -> str: ...

    def shutdown(self, wait: bool = True, timeout: float = 5.0) -> None: ...

@dataclass
class WorkerResult:
    success: bool
    value: Any = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None
    timed_out: bool = False
```

### ThreadSafeState (`squid/utils/thread_safe_state.py`)

Thread-safe wrappers for shared state.

```python
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

# Thread-safe value
image_buffer = ThreadSafeValue[np.ndarray](None)
image_buffer.set(new_image)      # From camera thread
image = image_buffer.get()        # From display thread
image = image_buffer.get_and_clear()  # Atomic get + clear

# Thread-safe flag with wait
ready = ThreadSafeFlag(initial=False)
ready.wait(timeout=5.0)  # Block until set or timeout
ready.set()              # Wake up waiters
ready.wait_and_clear(timeout=5.0)  # Wait, then clear atomically
```

**API:**
```python
class ThreadSafeValue(Generic[T]):
    def get(self) -> Optional[T]: ...
    def set(self, value: T) -> None: ...
    def update(self, updater: Callable[[Optional[T]], T]) -> T: ...
    def get_and_clear(self) -> Optional[T]: ...
    def locked(self) -> ContextManager: ...  # For complex operations

class ThreadSafeFlag:
    def set(self) -> None: ...
    def clear(self) -> None: ...
    def is_set(self) -> bool: ...
    def wait(self, timeout: Optional[float] = None) -> bool: ...
    def wait_and_clear(self, timeout: Optional[float] = None) -> bool: ...
```

---

## Implementation Tasks

### Phase 1: Fix Critical Race Conditions (ThreadSafeState)

These are the highest priority - actual data corruption and crashes.

---

#### Task 1.1: Fix Xeryon readyToSend Race Condition

**File:** `control/peripherals/xeryon.py`

**Problem (lines 1221, 1259, 1281-1282):**
```python
self.readyToSend = []  # Line 1221 - Plain list, NOT thread-safe

# From main thread:
self.readyToSend.append(command)  # Line 1259

# From worker thread:
dataToSend = list(self.readyToSend[0:10])  # Line 1281 - RACE CONDITION
self.readyToSend = self.readyToSend[10:]   # Line 1282 - RACE CONDITION
```

A list is being modified from multiple threads simultaneously. This can cause data corruption, lost commands, or crashes.

**Fix:**
```python
from queue import Queue

class Xeryon:
    def __init__(self, ...):
        # ...
        self._command_queue = Queue()  # Thread-safe queue

    def send(self, command):
        self._command_queue.put(command)

    def __processData(self):
        while self.run:
            # Get up to 10 commands
            commands = []
            for _ in range(10):
                try:
                    cmd = self._command_queue.get_nowait()
                    commands.append(cmd)
                except Empty:
                    break

            if commands:
                # Process commands...
```

**Commit:** `Fix race condition in Xeryon command queue using Queue`

---

#### Task 1.2: Fix IDS Camera Frame State

**File:** `control/peripherals/cameras/ids.py`

**Problem (lines 67-68, 209-210):**
```python
self.image_locked = False   # Line 67 - NO PROTECTION
self.current_frame = None   # Line 68 - NO PROTECTION

# In callback thread (line 209-210):
self.current_frame = image          # Written from callback thread
self.frame_ID_software += 1         # Incremented from callback thread
```

**Fix:**
```python
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

class Camera_IDS:
    def __init__(self, ...):
        # ...
        self._image_locked = ThreadSafeFlag(initial=False)
        self._current_frame = ThreadSafeValue[np.ndarray](None)
        self._frame_id = ThreadSafeValue[int](0)

    @property
    def image_locked(self) -> bool:
        return self._image_locked.is_set()

    @property
    def current_frame(self):
        return self._current_frame.get()

    def _wait_and_callback(self):
        # ...
        self._current_frame.set(image)
        self._frame_id.update(lambda x: (x or 0) + 1)
```

**Commit:** `Fix race condition in IDS camera frame state using ThreadSafeState`

---

#### Task 1.3: Fix TIS Camera Frame State

**File:** `control/peripherals/cameras/tis.py`

**Problem (lines 36-41, 169-176):**
```python
self.samplelocked = False   # Line 36
self.gotimage = False       # Line 38
self.image_locked = False   # Line 41

# In callback:
self.samplelocked = True    # Line 169 - NO LOCK
self.gotimage = True        # Line 176 - NO LOCK
```

**Fix:** Same pattern as Task 1.2 - use `ThreadSafeFlag` for each state flag.

**Commit:** `Fix race condition in TIS camera frame state using ThreadSafeFlag`

---

#### Task 1.4: Fix Prior Stage is_busy Flag

**File:** `control/peripherals/stage/prior.py`

**Problem (lines 47, 279-285):**
```python
self.serial_lock = threading.Lock()  # Line 46
self.is_busy = False                 # Line 47 - NOT protected by serial_lock!

# In thread:
self.is_busy = True   # Line 279 - Written without lock
self.is_busy = False  # Line 285 - Written without lock
```

**Fix:**
```python
from squid.utils.thread_safe_state import ThreadSafeFlag

class PriorStage:
    def __init__(self, ...):
        # ...
        self._is_busy = ThreadSafeFlag(initial=False)

    @property
    def is_busy(self) -> bool:
        return self._is_busy.is_set()

    def wait_for_stop(self):
        self._is_busy.set()
        try:
            # ... wait logic ...
        finally:
            self._is_busy.clear()
```

**Commit:** `Fix race condition in Prior stage is_busy flag using ThreadSafeFlag`

---

#### Task 1.5: Fix ToupCam Trigger State

**File:** `control/peripherals/cameras/toupcam.py`

**Problem (line 278-289):**
```python
self._trigger_sent = False              # Set from main thread
self._raw_camera_stream_started = True  # Set from callback thread
```

**Fix:** Use `ThreadSafeFlag` for both flags.

**Commit:** `Fix race condition in ToupCam trigger state using ThreadSafeFlag`

---

#### Task 1.6: Fix Prior Stage Position Polling Race

**File:** `control/peripherals/stage/prior.py`

**Problem (lines 55-76):**
```python
def _pos_polling_thread_fn(self):
    while True:
        self._get_pos_poll_stage()  # Modifies x_pos/y_pos

def _get_pos_poll_stage(self):
    response = self._send_command("P")
    x, y, z = map(int, response.split(","))
    self.x_pos = x  # NO LOCK - polling thread
    self.y_pos = y  # NO LOCK - polling thread

def get_pos(self) -> Pos:
    x_mm = self._steps_to_mm(self.x_pos)  # NO LOCK - UI thread reads
    y_mm = self._steps_to_mm(self.y_pos)  # Race condition!
```

**Fix:**
```python
from squid.utils.thread_safe_state import ThreadSafeValue

class PriorStage:
    def __init__(self, ...):
        self._position = ThreadSafeValue[tuple](None)  # (x, y, z) in steps

    def _get_pos_poll_stage(self):
        response = self._send_command("P")
        x, y, z = map(int, response.split(","))
        self._position.set((x, y, z))  # Atomic update

    def get_pos(self) -> Pos:
        pos = self._position.get()
        if pos is None:
            return Pos(0, 0, 0, 0)
        x, y, z = pos
        return Pos(
            x_mm=self._steps_to_mm(x),
            y_mm=self._steps_to_mm(y),
            z_mm=self._steps_to_mm(z),
            theta_rad=0
        )
```

**Commit:** `Fix race condition in Prior stage position polling using ThreadSafeValue`

---

#### Task 1.7: Fix Hamamatsu Trigger State

**File:** `control/peripherals/cameras/hamamatsu.py`

**Problem (line 138):**
```python
self._trigger_sent.clear()  # Called from read thread - NO LOCK
```

While `threading.Event` is thread-safe, the pattern of checking and clearing is not atomic.

**Fix:** Use `ThreadSafeFlag.wait_and_clear()` for atomic check-and-clear.

**Commit:** `Fix race condition in Hamamatsu trigger state`

---

#### Task 1.8: Fix Andor Timestamp Race

**File:** `control/peripherals/cameras/andor.py`

**Problem (line 90):**
```python
self._last_trigger_timestamp = time.time()  # Set in send_trigger (UI thread)
# Read in get_ready_for_trigger (could be called from any thread)
```

**Fix:** Use `ThreadSafeValue[float]` for timestamp.

**Commit:** `Fix race condition in Andor trigger timestamp`

---

#### Task 1.9: Fix Daheng/GxiPy Frame Propagation

**File:** `control/peripherals/cameras/base.py`

**Problem (lines 148-178):**
```python
def _frame_callback(self, unused_user_param, raw_image):
    with self._frame_lock:
        # ... frame processing ...
        self._current_frame = current_frame
    # Lock released!
    self._propogate_frame(current_frame)  # Called OUTSIDE lock!
```

Frame is propagated after lock is released - another thread could modify `_current_frame`.

**Fix:** Either propagate inside lock, or pass frame directly (not via self._current_frame).

**Commit:** `Fix race condition in Daheng camera frame propagation`

---

#### Task 1.10: Fix Laser Autofocus Callback Cleanup

**File:** `control/core/laser_auto_focus_controller.py`

**Problem (line 521):**
```python
def _get_laser_spot_centroid(self, ...):
    self.camera.enable_callbacks(False)  # Disables callbacks
    # ... processing that might throw ...
    # NO FINALLY BLOCK TO RE-ENABLE!
```

If an exception occurs, callbacks are never re-enabled.

**Fix:**
```python
def _get_laser_spot_centroid(self, ...):
    self.camera.enable_callbacks(False)
    try:
        # ... processing ...
    finally:
        self.camera.enable_callbacks(True)  # Always re-enable
```

**Commit:** `Fix missing callback cleanup in laser autofocus`

---

#### Task 1.11: Fix Laser Autofocus Image Race

**File:** `control/core/laser_auto_focus_controller.py`

**Problem (line 535):**
```python
self.image = image  # Written from autofocus thread
# GUI reads self.image for display - NO SYNCHRONIZATION
```

**Fix:** Use `ThreadSafeValue[np.ndarray]` for image buffer.

**Commit:** `Fix race condition in laser autofocus image buffer`

---

#### Task 1.12: Fix Microcontroller Callback Registration

**File:** `control/microcontroller.py`

**Problem (line 119):**
```python
self.new_packet_callback_external = None  # Set from UI thread
# Called from read thread without synchronization
```

**Fix:** Use `ThreadSafeValue[Callable]` for callback registration.

**Commit:** `Fix race condition in microcontroller callback registration`

---

### Phase 2: Fix Silent Failures (safe_callback)

These tasks fix crashes caused by unhandled exceptions in callbacks.

---

#### Task 2.1: Fix ImageDisplay.process_queue()

**File:** `control/core/image_display.py`

**Problem (lines 46-59):**
```python
def process_queue(self):
    while True:
        if self.stop_signal_received:
            return
        try:
            [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
            self.image_lock.acquire(True)
            self.image_to_display.emit(image)
            self.image_lock.release()
            self.queue.task_done()
        except:
            pass  # SWALLOWS ALL ERRORS SILENTLY
```

**Test first** (`tests/unit/control/core/test_image_display.py`):
```python
import pytest
from unittest.mock import Mock, patch
from queue import Queue

def test_process_queue_logs_errors_instead_of_swallowing():
    """Errors in process_queue should be logged, not silently swallowed."""
    from control.core.image_display import ImageDisplay

    display = ImageDisplay()

    # Inject a mock that raises
    with patch.object(display, 'image_to_display') as mock_signal:
        mock_signal.emit.side_effect = RuntimeError("Test error")

        # Put an image in the queue
        display.queue.put([np.zeros((10, 10)), None, None])

        # Give it time to process
        import time
        time.sleep(0.2)

        # The thread should still be running (not crashed)
        assert display.thread.is_alive()

    display.close()
```

**Fix:**
```python
from squid.utils.safe_callback import safe_callback
import squid.logging

_log = squid.logging.get_logger("control.core.image_display")

def process_queue(self):
    while True:
        if self.stop_signal_received:
            return
        try:
            [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
            self.image_lock.acquire(True)
            try:
                self.image_to_display.emit(image)
            finally:
                self.image_lock.release()
            self.queue.task_done()
        except Empty:
            pass  # Queue timeout is expected
        except Exception as e:
            _log.error(f"Error processing image: {e}", exc_info=True)
```

**Run tests:**
```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/unit/control/core/test_image_display.py -v
```

**Commit:** `Fix silent error swallowing in ImageDisplay.process_queue()`

---

#### Task 2.2: Fix ImageSaver.process_queue()

**File:** `control/core/stream_handler.py`

**Problem (lines 181-215):**
```python
def process_queue(self):
    while True:
        # ...
        try:
            # ... save image ...
        except:
            pass  # SAME PROBLEM
```

**Test first:**
```python
def test_image_saver_logs_save_errors():
    """ImageSaver should log errors when saving fails."""
    from control.core.stream_handler import ImageSaver

    saver = ImageSaver()
    saver.set_base_path("/nonexistent/path")
    saver.start_new_experiment("test", add_timestamp=False)

    # This should fail but not crash
    saver.enqueue(np.zeros((10, 10), dtype=np.uint8), 0, 0.0)

    import time
    time.sleep(0.2)

    # Thread should still be alive
    assert saver.thread.is_alive()

    saver.close()
```

**Fix:** Same pattern as Task 2.1 - replace `except: pass` with proper logging.

**Commit:** `Fix silent error swallowing in ImageSaver.process_queue()`

---

#### Task 2.3: Fix ImageSaver_Tracking.process_queue()

**File:** `control/core/stream_handler.py`

**Problem (lines 268-299):** Same pattern - bare `except: pass`.

**Commit:** `Fix silent error swallowing in ImageSaver_Tracking.process_queue()`

---

#### Task 2.4: Fix USBSpectrometer Queue Processing

**File:** `control/core/usb_spectrometer.py`

**Problem (lines 123-124, 134-135, 154-155):** Multiple bare `except: pass` blocks.

**Commit:** `Fix silent error swallowing in USBSpectrometer`

---

#### Task 2.5: Fix Tracking Thread Termination

**File:** `control/core/tracking.py`

**Problem (lines 102-103):**
```python
except:
    pass  # Hides thread termination errors
```

**Commit:** `Fix silent error swallowing in tracking thread cleanup`

---

#### Task 2.6: Fix IDS Camera Callback

**File:** `control/peripherals/cameras/ids.py`

**Problem (lines 195-196):**
```python
except Exception as e:
    pass  # Silently drops camera stream errors!
```

**Commit:** `Add error logging to IDS camera callback`

---

#### Task 2.7: Fix LDI LED Control

**File:** `control/peripherals/lighting/ldi.py`

**Problem (lines 93, 110):** Bare except blocks in LED daemon threads.

**Commit:** `Fix silent error swallowing in LDI LED control`

---

#### Task 2.8: Fix Fluidics Handler

**File:** `control/peripherals/fluidics.py`

**Problem (line 146):** Bare except hides serialization errors.

**Commit:** `Fix silent error swallowing in fluidics handler`

---

#### Task 2.9: Fix StreamHandler.on_new_frame()

**File:** `control/core/stream_handler.py`

**Problem (lines 82-122):** No error handling around callbacks.

**Test first:**
```python
def test_stream_handler_survives_callback_errors():
    """StreamHandler should survive errors in downstream callbacks."""
    from control.core.stream_handler import StreamHandler, StreamHandlerFunctions
    from squid.abc import CameraFrame

    error_callback = Mock(side_effect=RuntimeError("Test"))

    handler = StreamHandler(StreamHandlerFunctions(
        image_to_display=error_callback,
        packet_image_to_write=lambda *args: None,
        signal_new_frame_received=lambda: None,
        accept_new_frame=lambda: True,
    ))

    # Create a fake frame
    frame = Mock(spec=CameraFrame)
    frame.frame = np.zeros((10, 10))
    frame.is_color.return_value = False

    # Should not raise
    handler.on_new_frame(frame)

    # handler_busy should be reset even after error
    assert not handler.handler_busy
```

**Fix:**
```python
def on_new_frame(self, frame: CameraFrame):
    if not self._fns.accept_new_frame():
        return

    self.handler_busy = True
    try:
        self._fns.signal_new_frame_received()
        # ... rest of method ...
    except Exception as e:
        _log.error(f"Error in frame callback: {e}", exc_info=True)
    finally:
        self.handler_busy = False
```

**Commit:** `Add error handling to StreamHandler.on_new_frame()`

---

### Phase 3: Fix Thread Safety (ThreadSafeState)

These tasks fix race conditions on shared state.

---

#### Task 3.1: Protect StreamHandler.handler_busy

**File:** `control/core/stream_handler.py`

**Problem (line 52):**
```python
self.handler_busy = False  # Plain bool, not thread-safe
```

This flag is read from one thread and written from another without synchronization.

**Test first:**
```python
def test_handler_busy_is_thread_safe():
    """handler_busy should be readable from any thread."""
    from control.core.stream_handler import StreamHandler, StreamHandlerFunctions
    import threading

    handler = StreamHandler(StreamHandlerFunctions(
        image_to_display=lambda x: None,
        packet_image_to_write=lambda *args: None,
        signal_new_frame_received=lambda: None,
        accept_new_frame=lambda: True,
    ))

    # Should be accessible without race conditions
    results = []
    def read_flag():
        for _ in range(100):
            results.append(handler.handler_busy)

    threads = [threading.Thread(target=read_flag) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All reads should have succeeded
    assert len(results) == 1000
```

**Fix:**
```python
from squid.utils.thread_safe_state import ThreadSafeFlag

class StreamHandler:
    def __init__(self, ...):
        # ...
        self._handler_busy = ThreadSafeFlag(initial=False)

    @property
    def handler_busy(self) -> bool:
        return self._handler_busy.is_set()

    def on_new_frame(self, frame: CameraFrame):
        if not self._fns.accept_new_frame():
            return

        self._handler_busy.set()
        try:
            # ... process frame ...
        finally:
            self._handler_busy.clear()
```

**Commit:** `Use ThreadSafeFlag for StreamHandler.handler_busy`

---

#### Task 3.2: Protect StreamHandler timestamp/counter fields

**File:** `control/core/stream_handler.py`

**Problem (lines 55-57):**
```python
self.timestamp_last = 0
self.counter = 0
self.fps_real = 0
```

These are read from main thread (for display) and written from callback thread.

**Fix:**
```python
from squid.utils.thread_safe_state import ThreadSafeValue

class StreamHandler:
    def __init__(self, ...):
        # ...
        self._fps_real = ThreadSafeValue[int](0)

    @property
    def fps_real(self) -> int:
        return self._fps_real.get() or 0

    def on_new_frame(self, frame: CameraFrame):
        # ...
        if timestamp_now == self.timestamp_last:
            self.counter = self.counter + 1
        else:
            self.timestamp_last = timestamp_now
            self._fps_real.set(self.counter)
            self.counter = 0
```

**Commit:** `Use ThreadSafeValue for StreamHandler fps tracking`

---

### Phase 4: Fix UI Freezes (WorkerManager)

These tasks fix blocking calls on the main thread.

---

#### Task 4.1: Add WorkerManager to StageService

**File:** `squid/services/stage_service.py`

**Problem:** Stage movements block the main thread.

**Test first** (`tests/unit/squid/services/test_stage_service.py`):
```python
def test_move_x_async_does_not_block():
    """move_x_async should return immediately."""
    from squid.services.stage_service import StageService
    from squid.events import EventBus
    from unittest.mock import Mock
    import time

    mock_stage = Mock()
    # Make move_x take 1 second
    mock_stage.move_x.side_effect = lambda *args, **kwargs: time.sleep(1.0)

    bus = EventBus()
    service = StageService(mock_stage, bus)

    start = time.time()
    service.move_x_async(1.0)
    elapsed = time.time() - start

    # Should return in < 100ms (not wait for the 1 second move)
    assert elapsed < 0.1

    service.shutdown()
```

**Implementation:**
```python
from squid.utils.worker_manager import WorkerManager, WorkerResult

class StageService(BaseService):
    def __init__(self, stage: AbstractStage, event_bus: EventBus):
        super().__init__(event_bus)
        self._stage = stage
        self._worker = WorkerManager(max_workers=1)  # Serialize stage commands
        # ...

    def move_x_async(
        self,
        distance_mm: float,
        timeout_ms: int = 60000,
        on_complete: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Move X axis without blocking.

        Args:
            distance_mm: Distance to move
            timeout_ms: Timeout in milliseconds
            on_complete: Called on success (on main thread via Qt signal)
            on_error: Called on failure with error message

        Returns:
            Task name for tracking
        """
        def task():
            self._stage.move_x(distance_mm, blocking=True)
            return self._stage.get_pos()

        def handle_complete(result: WorkerResult):
            self._publish_position()
            if on_complete:
                on_complete()

        def handle_error(result: WorkerResult):
            self._log.error(f"move_x failed: {result.error}")
            if on_error:
                on_error(str(result.error))

        return self._worker.submit(
            task_name=f"move_x_{distance_mm}",
            task=task,
            timeout_ms=timeout_ms,
            on_complete=handle_complete,
            on_error=handle_error,
        )

    # Add similar methods: move_y_async, move_z_async, move_to_async

    def shutdown(self):
        """Clean shutdown."""
        self._worker.shutdown()
        super().shutdown()
```

**Commit:** `Add async move methods to StageService using WorkerManager`

---

#### Task 4.2: Update NavigationWidget to use async methods

**File:** `control/widgets/stage.py`

**Problem (lines 483-499):**
```python
def move_x_forward(self):
    self._service.move_x(self.entry_dX.value())  # BLOCKS
```

**Test first:**
```python
def test_navigation_widget_disables_buttons_during_move(qtbot):
    """Buttons should disable during async move."""
    from control.widgets.stage import NavigationWidget
    from unittest.mock import Mock

    mock_service = Mock()
    widget = NavigationWidget(stage_service=mock_service)
    qtbot.addWidget(widget)

    # Click move button
    qtbot.mouseClick(widget.btn_moveX_forward, Qt.LeftButton)

    # Button should be disabled
    assert not widget.btn_moveX_forward.isEnabled()

    # Simulate completion callback
    # (In real code, WorkerManager signals handle this)
```

**Fix:**
```python
def move_x_forward(self):
    self._disable_move_buttons()
    self._service.move_x_async(
        self.entry_dX.value(),
        on_complete=self._enable_move_buttons,
        on_error=self._handle_move_error,
    )

def move_x_backward(self):
    self._disable_move_buttons()
    self._service.move_x_async(
        -self.entry_dX.value(),
        on_complete=self._enable_move_buttons,
        on_error=self._handle_move_error,
    )

# Similar for move_y_forward, move_y_backward, move_z_forward, move_z_backward

def _disable_move_buttons(self):
    """Disable all move buttons during operation."""
    self.btn_moveX_forward.setEnabled(False)
    self.btn_moveX_backward.setEnabled(False)
    self.btn_moveY_forward.setEnabled(False)
    self.btn_moveY_backward.setEnabled(False)
    self.btn_moveZ_forward.setEnabled(False)
    self.btn_moveZ_backward.setEnabled(False)

def _enable_move_buttons(self):
    """Re-enable move buttons after operation."""
    self.btn_moveX_forward.setEnabled(True)
    self.btn_moveX_backward.setEnabled(True)
    self.btn_moveY_forward.setEnabled(True)
    self.btn_moveY_backward.setEnabled(True)
    self.btn_moveZ_forward.setEnabled(True)
    self.btn_moveZ_backward.setEnabled(True)

def _handle_move_error(self, error_msg: str):
    """Handle move error."""
    self._enable_move_buttons()
    self.log.error(f"Move failed: {error_msg}")
```

**Commit:** `Update NavigationWidget to use async stage methods`

---

#### Task 4.3: Fix StageUtils callback thread safety

**File:** `control/widgets/stage.py`

**Problem (lines 195-217):**
```python
def _callback_loading_position_reached(self, success, error_message):
    self.slide_position = "loading"
    self.btn_load_slide.setStyleSheet("...")  # CALLED FROM WORKER THREAD!
```

The `move_to_loading_position` uses `threaded_operation_helper` which calls the callback from the worker thread, not the Qt main thread.

**Fix Option A - Use Qt Signal:**
```python
class StageUtils(QDialog):
    _signal_loading_done = Signal(bool, str)  # success, error_message
    _signal_scanning_done = Signal(bool, str)

    def __init__(self, ...):
        # ...
        self._signal_loading_done.connect(self._on_loading_position_reached)
        self._signal_scanning_done.connect(self._on_scanning_position_reached)

    def switch_position(self):
        # ...
        if self.slide_position != "loading":
            self._service.move_to_loading_position(
                blocking=False,
                callback=lambda s, e: self._signal_loading_done.emit(s, e),
                is_wellplate=self.is_wellplate,
            )
        else:
            self._service.move_to_scanning_position(
                blocking=False,
                callback=lambda s, e: self._signal_scanning_done.emit(s, e),
                is_wellplate=self.is_wellplate,
            )

    def _on_loading_position_reached(self, success: bool, error_message: str):
        """Handle loading position reached - RUNS ON MAIN THREAD."""
        self.slide_position = "loading"
        self.btn_load_slide.setStyleSheet("background-color: #C2FFC2")
        # ... rest of handler ...
```

**Commit:** `Fix thread safety in StageUtils position callbacks`

---

### Phase 5: Apply WorkerManager to Acquisition

---

#### Task 5.1: Actually use WorkerManager in MultiPointWorker

**File:** `control/core/multi_point_worker.py`

**Problem (lines 144-145):**
```python
self._worker_manager = WorkerManager(max_workers=2)
self._worker_manager.signals.timeout.connect(self._on_worker_timeout)
```

The WorkerManager is instantiated but **never used**. No tasks are ever submitted to it.

**Fix:** Use it for long-running operations that could hang:

```python
def _acquire_single_frame(self, ...):
    """Acquire a single frame with timeout protection."""
    return self._worker_manager.submit(
        task_name=f"acquire_frame_{frame_id}",
        task=lambda: self._do_acquire_frame(...),
        timeout_ms=30000,  # 30 second timeout per frame
        on_complete=self._on_frame_acquired,
        on_error=self._on_frame_error,
    )
```

**Commit:** `Use WorkerManager for frame acquisition with timeout`

---

## Testing Strategy

### Unit Tests

Each task should have tests that verify:
1. **Error containment** - Exceptions don't crash the app
2. **Thread safety** - No race conditions
3. **Non-blocking** - Async operations return immediately

**Run all tests:**
```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/unit/ -v
```

### Integration Tests

Test the full flow:
```python
def test_stage_move_does_not_freeze_ui(qtbot):
    """Stage movement should not freeze the UI."""
    # Start a long stage move
    # Verify UI is still responsive (can click other buttons)
    # Verify callback is called on completion
```

### Manual Testing Checklist

After completing all phases:

- [ ] Click stage move buttons - UI should remain responsive
- [ ] Move slider during stage move - slider should respond
- [ ] Cancel during long move - should work
- [ ] Error during move - should show error, not crash
- [ ] Run acquisition - should have timeout protection
- [ ] Switch loading/scanning position - should not crash

---

## File Summary

### Phase 1: Critical Race Conditions (12 Tasks)
| File | Changes |
|------|---------|
| `control/peripherals/xeryon.py` | Replace list with Queue for commands |
| `control/peripherals/cameras/ids.py` | Use ThreadSafeValue/Flag for frame state |
| `control/peripherals/cameras/tis.py` | Use ThreadSafeFlag for sample/image flags |
| `control/peripherals/cameras/toupcam.py` | Use ThreadSafeFlag for trigger state |
| `control/peripherals/stage/prior.py` | Use ThreadSafeFlag/Value for is_busy and position |
| `control/peripherals/cameras/hamamatsu.py` | Use ThreadSafeFlag.wait_and_clear() for trigger |
| `control/peripherals/cameras/andor.py` | Use ThreadSafeValue for trigger timestamp |
| `control/peripherals/cameras/base.py` | Fix frame propagation outside lock |
| `control/core/laser_auto_focus_controller.py` | Add finally block for callback cleanup, ThreadSafeValue for image |
| `control/microcontroller.py` | Use ThreadSafeValue for callback registration |

### Phase 2: Silent Failures (9 Tasks)
| File | Changes |
|------|---------|
| `control/core/image_display.py` | Replace `except: pass` with logging |
| `control/core/stream_handler.py` | Replace `except: pass` with logging (3 places), add error handling to on_new_frame() |
| `control/core/usb_spectrometer.py` | Replace `except: pass` with logging (3 places) |
| `control/core/tracking.py` | Replace `except: pass` with logging |
| `control/peripherals/cameras/ids.py` | Add error logging to callback |
| `control/peripherals/lighting/ldi.py` | Replace `except: pass` with logging (2 places) |
| `control/peripherals/fluidics.py` | Replace `except: pass` with logging |

### Phase 3: Thread Safety (2 Tasks)
| File | Changes |
|------|---------|
| `control/core/stream_handler.py` | Use ThreadSafeFlag/Value for handler_busy and fps tracking |

### Phase 4: UI Freezes (3 Tasks)
| File | Changes |
|------|---------|
| `squid/services/stage_service.py` | Add WorkerManager, async move methods |
| `control/widgets/stage.py` | Use async methods, fix callback thread safety |

### Phase 5: WorkerManager Acquisition (1 Task)
| File | Changes |
|------|---------|
| `control/core/multi_point_worker.py` | Actually use WorkerManager for timeouts |

---

## Commit Order

### Phase 1: Critical Race Conditions (Do First)
1. `Fix race condition in Xeryon command queue using Queue`
2. `Fix race condition in IDS camera frame state using ThreadSafeState`
3. `Fix race condition in TIS camera frame state using ThreadSafeFlag`
4. `Fix race condition in Prior stage is_busy flag using ThreadSafeFlag`
5. `Fix race condition in ToupCam trigger state using ThreadSafeFlag`
6. `Fix race condition in Prior stage position polling using ThreadSafeValue`
7. `Fix race condition in Hamamatsu trigger state`
8. `Fix race condition in Andor trigger timestamp`
9. `Fix race condition in Daheng camera frame propagation`
10. `Fix missing callback cleanup in laser autofocus`
11. `Fix race condition in laser autofocus image buffer`
12. `Fix race condition in microcontroller callback registration`

### Phase 2: Silent Failures
13. `Fix silent error swallowing in ImageDisplay.process_queue()`
14. `Fix silent error swallowing in ImageSaver.process_queue()`
15. `Fix silent error swallowing in ImageSaver_Tracking.process_queue()`
16. `Fix silent error swallowing in USBSpectrometer`
17. `Fix silent error swallowing in tracking thread cleanup`
18. `Add error logging to IDS camera callback`
19. `Fix silent error swallowing in LDI LED control`
20. `Fix silent error swallowing in fluidics handler`
21. `Add error handling to StreamHandler.on_new_frame()`

### Phase 3: Thread Safety
22. `Use ThreadSafeFlag for StreamHandler.handler_busy`
23. `Use ThreadSafeValue for StreamHandler fps tracking`

### Phase 4: UI Freezes
24. `Add async move methods to StageService using WorkerManager`
25. `Update NavigationWidget to use async stage methods`
26. `Fix thread safety in StageUtils position callbacks`

### Phase 5: WorkerManager Acquisition
27. `Use WorkerManager for frame acquisition with timeout`

---

## Summary Statistics

| Category | Files | Tasks |
|----------|-------|-------|
| Phase 1: Critical race conditions | 10 | 12 |
| Phase 2: Silent error swallowing | 7 | 9 |
| Phase 3: Thread-safe state | 1 | 2 |
| Phase 4: UI freeze fixes | 2 | 3 |
| Phase 5: WorkerManager acquisition | 1 | 1 |
| **Total** | **~19 unique** | **27** |

---

## Verification

After completing all tasks:

```bash
# 1. All tests pass
pytest tests/ -v

# 2. No silent exception swallowing
grep -r "except:" control/core/*.py | grep -v "except Exception"
# Should return no results

# 3. Run in simulation mode
python main_hcs.py --simulation

# 4. Test stage movement - UI should stay responsive
# 5. Test loading position switch - should not crash
```
