# Phase 1: Safety Foundation

**Goal**: Create utilities for error containment and thread-safe state management.

**Impact**: Immediate crash reduction by containing exceptions and fixing race conditions.

**Estimated Effort**: 1 week

---

## Checklist

### Task 1.1: Create safe_callback utility
- [ ] Create directory `software/squid/utils/`
- [ ] Create `software/squid/utils/__init__.py`
- [ ] Create test file `software/tests/squid/utils/__init__.py`
- [ ] Create test file `software/tests/squid/utils/test_safe_callback.py`
- [ ] Run tests (should fail)
- [ ] Create `software/squid/utils/safe_callback.py`
- [ ] Run tests (should pass)
- [ ] Commit: "Add safe_callback utility for error containment"

### Task 1.2: Create thread_safe_state utility
- [ ] Create test file `software/tests/squid/utils/test_thread_safe_state.py`
- [ ] Run tests (should fail)
- [ ] Create `software/squid/utils/thread_safe_state.py`
- [ ] Run tests (should pass)
- [ ] Commit: "Add ThreadSafeValue and ThreadSafeFlag utilities"

### Task 1.3: Apply safe_callback to MultiPointWorker._image_callback
- [ ] Read `software/control/core/multi_point_worker.py` lines 553-608
- [ ] Add import for safe_callback
- [ ] Extract `_process_camera_frame` method
- [ ] Add `_handle_callback_error` method
- [ ] Wrap callback with safe_callback
- [ ] Run tests
- [ ] Manual smoke test with simulation
- [ ] Commit: "Apply safe_callback to MultiPointWorker._image_callback"

### Task 1.4: Apply ThreadSafeValue to _current_capture_info
- [ ] Read `software/control/core/multi_point_worker.py` lines 127-137
- [ ] Add import for ThreadSafeValue, ThreadSafeFlag
- [ ] Replace `threading.Event` with `ThreadSafeFlag` (lines 127-133)
- [ ] Replace `_current_capture_info` with `ThreadSafeValue` (line 135)
- [ ] Update all usages of `_current_capture_info` (use `.get()`, `.set()`, `.get_and_clear()`)
- [ ] Run tests
- [ ] Manual smoke test
- [ ] Commit: "Use ThreadSafeValue for _current_capture_info"

### Task 1.5: Fix bare except clauses in _def.py
- [ ] Read `software/control/_def.py` lines 24-46
- [ ] Replace bare `except:` with `except (ValueError, TypeError, AttributeError):`
- [ ] Run config tests
- [ ] Commit: "Replace bare except clauses in _def.py"

---

## Task 1.1: Create safe_callback utility

### Test File (Create First - TDD)

**File**: `software/tests/squid/utils/test_safe_callback.py`

```python
"""Tests for safe_callback utility."""
import pytest
from squid.utils.safe_callback import safe_callback, CallbackResult


class TestSafeCallback:
    """Test suite for safe_callback function."""

    def test_successful_callback_returns_value(self):
        """Successful callback should return value in result."""
        def add(a, b):
            return a + b

        result = safe_callback(add, 1, 2)

        assert result.success is True
        assert result.value == 3
        assert result.error is None
        assert result.stack_trace is None

    def test_failed_callback_contains_error(self):
        """Failed callback should contain exception and stack trace."""
        def explode():
            raise ValueError("boom")

        result = safe_callback(explode)

        assert result.success is False
        assert result.value is None
        assert isinstance(result.error, ValueError)
        assert "boom" in str(result.error)
        assert result.stack_trace is not None
        assert "ValueError" in result.stack_trace

    def test_on_error_callback_is_called(self):
        """on_error handler should be called with exception and traceback."""
        errors = []

        def explode():
            raise ValueError("boom")

        def on_error(e, tb):
            errors.append((e, tb))

        result = safe_callback(explode, on_error=on_error)

        assert len(errors) == 1
        assert isinstance(errors[0][0], ValueError)
        assert "boom" in str(errors[0][0])
        assert errors[0][1] is not None  # stack trace

    def test_on_error_callback_failure_doesnt_crash(self):
        """If on_error handler fails, safe_callback should still return."""
        def explode():
            raise ValueError("original error")

        def bad_handler(e, tb):
            raise RuntimeError("handler also explodes")

        # Should not raise - handler failure is logged but contained
        result = safe_callback(explode, on_error=bad_handler)

        assert result.success is False
        assert isinstance(result.error, ValueError)

    def test_kwargs_are_passed(self):
        """Keyword arguments should be passed to callback."""
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = safe_callback(greet, "World", greeting="Hi")

        assert result.success is True
        assert result.value == "Hi, World!"

    def test_raise_if_error_raises(self):
        """raise_if_error should re-raise the exception."""
        def explode():
            raise ValueError("boom")

        result = safe_callback(explode)

        with pytest.raises(ValueError) as exc_info:
            result.raise_if_error()

        assert "boom" in str(exc_info.value)

    def test_raise_if_error_noop_on_success(self):
        """raise_if_error should do nothing on success."""
        def ok():
            return 42

        result = safe_callback(ok)

        # Should not raise
        result.raise_if_error()
```

### Implementation File

**File**: `software/squid/utils/__init__.py`

```python
"""Squid utilities package."""
from squid.utils.safe_callback import safe_callback, CallbackResult
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
]
```

**File**: `software/squid/utils/safe_callback.py`

```python
"""
Safe callback wrapper for error containment.

Instead of letting exceptions propagate and crash the application,
this module provides utilities to catch exceptions and return them
as part of a result object.

Usage:
    from squid.utils.safe_callback import safe_callback

    def risky_operation():
        # ... might raise ...

    result = safe_callback(risky_operation)
    if not result.success:
        log.error(f"Operation failed: {result.error}")
        # Handle gracefully instead of crashing
"""
from typing import Callable, TypeVar, Generic, Optional, Any
from dataclasses import dataclass
import traceback
import squid.logging

T = TypeVar('T')

_log = squid.logging.get_logger("squid.utils.safe_callback")


@dataclass
class CallbackResult(Generic[T]):
    """
    Result of a callback execution with error handling.

    Attributes:
        success: True if callback completed without exception
        value: Return value of callback (None if failed)
        error: Exception that was raised (None if success)
        stack_trace: Formatted stack trace (None if success)
    """
    success: bool
    value: Optional[T] = None
    error: Optional[Exception] = None
    stack_trace: Optional[str] = None

    def raise_if_error(self) -> None:
        """Re-raise the exception if one occurred."""
        if self.error is not None:
            raise self.error


def safe_callback(
    callback: Callable[..., T],
    *args: Any,
    on_error: Optional[Callable[[Exception, str], None]] = None,
    **kwargs: Any
) -> CallbackResult[T]:
    """
    Execute a callback with error containment.

    Instead of letting exceptions propagate and crash the app,
    this catches them and returns a result object.

    Args:
        callback: The function to execute
        *args: Positional arguments to pass to callback
        on_error: Optional handler called with (exception, stack_trace) on failure
        **kwargs: Keyword arguments to pass to callback

    Returns:
        CallbackResult with success status, value or error

    Example:
        result = safe_callback(risky_function, arg1, arg2, kwarg=value)
        if result.success:
            use(result.value)
        else:
            log.error(f"Failed: {result.error}")
            handle_error()
    """
    try:
        result = callback(*args, **kwargs)
        return CallbackResult(success=True, value=result)
    except Exception as e:
        stack = traceback.format_exc()
        _log.error(f"Callback {callback.__name__} failed: {e}\n{stack}")

        if on_error is not None:
            try:
                on_error(e, stack)
            except Exception as handler_error:
                _log.error(f"Error handler also failed: {handler_error}")

        return CallbackResult(
            success=False,
            error=e,
            stack_trace=stack
        )
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/utils/test_safe_callback.py -v
```

### Commit

```bash
git add software/squid/utils/ software/tests/squid/utils/
git commit -m "Add safe_callback utility for error containment

Provides CallbackResult dataclass and safe_callback() function that wraps
callbacks to catch exceptions and return them as result objects instead
of crashing the application.

Supports optional on_error handler for custom error handling.

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 3.
"
```

---

## Task 1.2: Create thread_safe_state utility

### Test File

**File**: `software/tests/squid/utils/test_thread_safe_state.py`

```python
"""Tests for thread-safe state utilities."""
import pytest
import threading
import time
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag


class TestThreadSafeValue:
    """Test suite for ThreadSafeValue."""

    def test_get_set(self):
        """Basic get and set operations."""
        v = ThreadSafeValue(initial_value=42)
        assert v.get() == 42
        v.set(100)
        assert v.get() == 100

    def test_initial_none(self):
        """Default initial value is None."""
        v = ThreadSafeValue()
        assert v.get() is None

    def test_get_and_clear(self):
        """get_and_clear returns value and sets to None atomically."""
        v = ThreadSafeValue(initial_value="hello")
        assert v.get_and_clear() == "hello"
        assert v.get() is None

    def test_update_atomic(self):
        """update() applies function atomically."""
        v = ThreadSafeValue(initial_value=0)

        def increment(x):
            return x + 1

        result = v.update(increment)
        assert result == 1
        assert v.get() == 1

    def test_concurrent_updates(self):
        """Concurrent updates should not lose any increments."""
        v = ThreadSafeValue(initial_value=0)

        def increment_many():
            for _ in range(1000):
                v.update(lambda x: x + 1)

        threads = [threading.Thread(target=increment_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # If there were race conditions, we'd get less than 10000
        assert v.get() == 10000

    def test_locked_context_manager(self):
        """locked() context manager provides exclusive access."""
        v = ThreadSafeValue(initial_value={"count": 0})

        with v.locked() as value:
            # Can safely modify mutable value
            value["count"] += 1

        assert v.get()["count"] == 1


class TestThreadSafeFlag:
    """Test suite for ThreadSafeFlag."""

    def test_initial_state_false(self):
        """Default initial state is False."""
        f = ThreadSafeFlag()
        assert f.is_set() is False

    def test_initial_state_true(self):
        """Can set initial state to True."""
        f = ThreadSafeFlag(initial=True)
        assert f.is_set() is True

    def test_set_clear(self):
        """set() and clear() work correctly."""
        f = ThreadSafeFlag(initial=False)
        f.set()
        assert f.is_set() is True
        f.clear()
        assert f.is_set() is False

    def test_wait_returns_immediately_if_set(self):
        """wait() returns True immediately if flag is set."""
        f = ThreadSafeFlag(initial=True)
        start = time.time()
        result = f.wait(timeout=1.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.1  # Should be nearly instant

    def test_wait_times_out_if_not_set(self):
        """wait() returns False after timeout if flag not set."""
        f = ThreadSafeFlag(initial=False)
        start = time.time()
        result = f.wait(timeout=0.05)
        elapsed = time.time() - start

        assert result is False
        assert elapsed >= 0.05

    def test_wait_wakes_on_set(self):
        """wait() returns True when another thread sets the flag."""
        f = ThreadSafeFlag(initial=False)

        def setter():
            time.sleep(0.02)
            f.set()

        t = threading.Thread(target=setter)
        t.start()

        start = time.time()
        result = f.wait(timeout=1.0)
        elapsed = time.time() - start

        t.join()

        assert result is True
        assert elapsed < 0.5  # Should wake up quickly after set()

    def test_wait_and_clear(self):
        """wait_and_clear() waits, returns True, and clears atomically."""
        f = ThreadSafeFlag(initial=True)

        result = f.wait_and_clear(timeout=0.1)

        assert result is True
        assert f.is_set() is False

    def test_wait_and_clear_timeout(self):
        """wait_and_clear() returns False on timeout without clearing."""
        f = ThreadSafeFlag(initial=False)

        result = f.wait_and_clear(timeout=0.05)

        assert result is False
        # Flag should still be False (wasn't cleared because we timed out)
        assert f.is_set() is False
```

### Implementation File

**File**: `software/squid/utils/thread_safe_state.py`

```python
"""
Thread-safe state management utilities.

Provides wrappers for shared state that is accessed from multiple threads,
ensuring proper synchronization to prevent race conditions.

Usage:
    from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

    # Thread-safe value
    capture_info = ThreadSafeValue[CaptureInfo](None)
    capture_info.set(new_info)  # From thread A
    info = capture_info.get()   # From thread B

    # Thread-safe flag with wait capability
    ready = ThreadSafeFlag(initial=False)
    ready.wait(timeout=5.0)  # Block until set or timeout
    ready.set()              # Wake up waiters
"""
from threading import Lock, Condition
from typing import TypeVar, Generic, Optional, Callable
from contextlib import contextmanager

T = TypeVar('T')


class ThreadSafeValue(Generic[T]):
    """
    Thread-safe wrapper for a single value.

    All operations are atomic and protected by a lock.

    Example:
        capture_info = ThreadSafeValue[CaptureInfo](None)

        # Set from one thread
        capture_info.set(new_info)

        # Get from another thread
        info = capture_info.get()

        # Atomic update
        capture_info.update(lambda x: x.with_timestamp(now()))

        # Atomic get and clear
        info = capture_info.get_and_clear()
    """

    def __init__(self, initial_value: Optional[T] = None):
        """
        Initialize with optional initial value.

        Args:
            initial_value: Initial value (default: None)
        """
        self._value: Optional[T] = initial_value
        self._lock = Lock()

    def get(self) -> Optional[T]:
        """Get the current value (thread-safe)."""
        with self._lock:
            return self._value

    def set(self, value: T) -> None:
        """Set the value (thread-safe)."""
        with self._lock:
            self._value = value

    def update(self, updater: Callable[[Optional[T]], T]) -> T:
        """
        Atomically update the value using a function.

        Args:
            updater: Function that takes current value and returns new value

        Returns:
            The new value after update
        """
        with self._lock:
            self._value = updater(self._value)
            return self._value

    def get_and_clear(self) -> Optional[T]:
        """
        Atomically get the value and set to None.

        Returns:
            The value before clearing
        """
        with self._lock:
            value = self._value
            self._value = None
            return value

    @contextmanager
    def locked(self):
        """
        Context manager for complex operations needing the lock.

        Yields the current value while holding the lock.

        Example:
            with value.locked() as v:
                # Can safely modify mutable value
                v["key"] = "new_value"
        """
        with self._lock:
            yield self._value


class ThreadSafeFlag:
    """
    Thread-safe boolean flag with wait capability.

    Provides a cleaner interface than threading.Event with explicit
    timeout handling and atomic wait-and-clear.

    Example:
        ready = ThreadSafeFlag(initial=False)

        # In worker thread
        ready.wait(timeout=5.0)  # Block until set or timeout

        # In main thread
        ready.set()  # Wake up waiter
    """

    def __init__(self, initial: bool = False):
        """
        Initialize the flag.

        Args:
            initial: Initial state (default: False)
        """
        self._flag = initial
        self._lock = Lock()
        self._condition = Condition(self._lock)

    def set(self) -> None:
        """Set the flag to True and wake all waiters."""
        with self._condition:
            self._flag = True
            self._condition.notify_all()

    def clear(self) -> None:
        """Clear the flag (set to False)."""
        with self._condition:
            self._flag = False

    def is_set(self) -> bool:
        """Check if the flag is set."""
        with self._lock:
            return self._flag

    def wait(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for flag to be set.

        Args:
            timeout: Maximum time to wait in seconds (None = wait forever)

        Returns:
            True if flag was set, False if timed out
        """
        with self._condition:
            if self._flag:
                return True
            return self._condition.wait(timeout=timeout)

    def wait_and_clear(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for flag, then clear it atomically.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if flag was set (and is now cleared), False if timed out
        """
        with self._condition:
            if not self._flag:
                if not self._condition.wait(timeout=timeout):
                    return False
            self._flag = False
            return True
```

### Run Tests

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/utils/test_thread_safe_state.py -v
```

### Commit

```bash
git add software/squid/utils/thread_safe_state.py software/tests/squid/utils/test_thread_safe_state.py
git commit -m "Add ThreadSafeValue and ThreadSafeFlag utilities

ThreadSafeValue: Thread-safe wrapper for any value with atomic get/set/update.
ThreadSafeFlag: Thread-safe boolean with wait capability and timeout handling.

Replaces patterns like:
  self._current_capture_info = None  # Race condition!

With:
  self._current_capture_info = ThreadSafeValue(None)  # Thread-safe

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 5.
"
```

---

## Task 1.3: Apply safe_callback to MultiPointWorker._image_callback

### Current Code Location

**File**: `software/control/core/multi_point_worker.py`
**Lines**: 553-608

### Changes

1. Add import at top of file (around line 30):

```python
from squid.utils.safe_callback import safe_callback
```

2. Replace `_image_callback` method:

**BEFORE** (lines 553-608):
```python
def _image_callback(self, camera_frame: CameraFrame):
    try:
        if self._ready_for_next_trigger.is_set():
            self._log.warning(
                "Got an image in the image callback, but we didn't send a trigger.  Ignoring the image."
            )
            return

        self._image_callback_idle.clear()
        with self._timing.get_timer("_image_callback"):
            # ... rest of processing ...
    finally:
        self._image_callback_idle.set()
```

**AFTER**:
```python
def _image_callback(self, camera_frame: CameraFrame):
    """
    Handle incoming camera frame.

    Wrapped with safe_callback to contain exceptions and prevent crashes.
    """
    if self._ready_for_next_trigger.is_set():
        self._log.warning(
            "Got an image in the image callback, but we didn't send a trigger. Ignoring the image."
        )
        return

    self._image_callback_idle.clear()
    try:
        result = safe_callback(
            self._process_camera_frame,
            camera_frame,
            on_error=self._handle_callback_error
        )

        if not result.success:
            self._log.error(f"Image callback failed, aborting: {result.error}")
            self.request_abort_fn()
    finally:
        self._image_callback_idle.set()

def _process_camera_frame(self, camera_frame: CameraFrame):
    """
    Process a camera frame - extracted from _image_callback for error containment.
    """
    with self._timing.get_timer("_image_callback"):
        self._log.debug(f"In Image callback for frame_id={camera_frame.frame_id}")
        info = self._current_capture_info
        self._current_capture_info = None

        self._ready_for_next_trigger.set()
        if not info:
            raise RuntimeError("No current capture info! Something is wrong.")

        image = camera_frame.frame
        if not camera_frame or image is None:
            raise RuntimeError("Image in frame callback is None.")

        with self._timing.get_timer("job creation and dispatch"):
            for job_class, job_runner in self._job_runners:
                job = job_class(capture_info=info, capture_image=JobImage(image_array=image))
                if job_runner is not None:
                    if not job_runner.dispatch(job):
                        raise RuntimeError("Failed to dispatch multiprocessing job!")
                else:
                    # NOTE(imo): We don't have any way of people using results, so for now just
                    # grab and ignore it.
                    result = job.run()

        height, width = image.shape[:2]
        with self._timing.get_timer("image_to_display*.emit"):
            self.callbacks.signal_new_image(camera_frame, info)

def _handle_callback_error(self, error: Exception, stack_trace: str):
    """
    Handle errors from image callback - store for debugging.
    """
    self._last_error = error
    self._last_stack_trace = stack_trace
```

3. Add instance variables in `__init__` (around line 175):

```python
# Error tracking for debugging
self._last_error: Optional[Exception] = None
self._last_stack_trace: Optional[str] = None
```

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/control/test_MultiPointWorker.py -v
# Note: Some tests may be skipped - that's OK
```

### Manual Smoke Test

```bash
cd /Users/wea/src/allenlab/Squid/software
python main_hcs.py --simulation
# Run an acquisition
# Check for any errors in console
```

### Commit

```bash
git add software/control/core/multi_point_worker.py
git commit -m "Apply safe_callback to MultiPointWorker._image_callback

Wraps image processing in error containment to prevent crashes from
propagating. Errors now trigger graceful abort instead of crash.

Changes:
- Extract _process_camera_frame() for the actual processing
- Add _handle_callback_error() to store error info
- Wrap with safe_callback() for error containment
- Add _last_error and _last_stack_trace for debugging

Part of stability improvements - see docs/IMPROVEMENTS_V2.md Section 3.
"
```

---

## Task 1.4: Apply ThreadSafeValue to _current_capture_info

### Current Code Location

**File**: `software/control/core/multi_point_worker.py`
**Lines**: 127-137

### Changes

1. Add import at top of file:

```python
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag
```

2. Replace in `__init__` (lines 127-137):

**BEFORE**:
```python
self._ready_for_next_trigger = threading.Event()
# Set this to true so that the first frame capture can proceed.
self._ready_for_next_trigger.set()
# This is cleared when the image callback is no longer processing an image.
self._image_callback_idle = threading.Event()
self._image_callback_idle.set()
# This is protected by the threading event above (aka set after clear, take copy before set)
self._current_capture_info: Optional[CaptureInfo] = None
```

**AFTER**:
```python
# Thread-safe flags for synchronization
self._ready_for_next_trigger = ThreadSafeFlag(initial=True)
self._image_callback_idle = ThreadSafeFlag(initial=True)

# Thread-safe capture info - accessed from main thread and camera callback thread
self._current_capture_info: ThreadSafeValue[CaptureInfo] = ThreadSafeValue(None)
```

3. Update all usages of `_current_capture_info`:

In `_process_camera_frame` (or `_image_callback` if not yet extracted):
```python
# BEFORE:
info = self._current_capture_info
self._current_capture_info = None

# AFTER:
info = self._current_capture_info.get_and_clear()
```

In `acquire_camera_image` (around line 648):
```python
# BEFORE:
current_capture_info = CaptureInfo(...)
self._current_capture_info = current_capture_info

# AFTER:
current_capture_info = CaptureInfo(...)
self._current_capture_info.set(current_capture_info)
```

4. Note: `ThreadSafeFlag` has the same interface as `threading.Event` for:
   - `.set()`
   - `.clear()`
   - `.wait(timeout)`
   - `.is_set()`

   So most code using `_ready_for_next_trigger` and `_image_callback_idle` should work unchanged.

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/control/test_MultiPointWorker.py -v
```

### Commit

```bash
git add software/control/core/multi_point_worker.py
git commit -m "Use ThreadSafeValue for _current_capture_info

Fixes race condition where _current_capture_info was accessed from
main thread and camera callback thread without synchronization.

The comment claimed 'protected by threading event' but Events don't
provide mutual exclusion. Now using ThreadSafeValue with atomic
get_and_clear() operation.

Also replaced threading.Event with ThreadSafeFlag for consistency.

Fixes stability issue documented in IMPROVEMENTS_V2.md Section 5.
"
```

---

## Task 1.5: Fix bare except clauses in _def.py

### Current Code Location

**File**: `software/control/_def.py`
**Lines**: 24-46

### Changes

Replace all bare `except:` with specific exceptions:

**BEFORE** (lines 24-46):
```python
def conf_attribute_reader(string_value):
    actualvalue = str(string_value).strip()
    try:
        if str(actualvalue) == "None":
            return None
    except:
        pass
    try:
        if str(actualvalue) == "True" or str(actualvalue) == "true":
            return True
        if str(actualvalue) == "False" or str(actualvalue) == "false":
            return False
    except:
        pass
    try:
        actualvalue = json.loads(actualvalue)
    except:
        try:
            actualvalue = int(str(actualvalue))
        except:
            try:
                actualvalue = float(actualvalue)
            except:
                actualvalue = str(actualvalue)
    return actualvalue
```

**AFTER**:
```python
def conf_attribute_reader(string_value):
    """
    Standardized way for reading config entries that are strings.

    Priority order: None -> bool -> dict/list (via json) -> int -> float -> string

    REMEMBER TO ENCLOSE PROPERTY NAMES IN LISTS/DICTS IN DOUBLE QUOTES
    """
    actualvalue = str(string_value).strip()

    # Check for None
    try:
        if str(actualvalue) == "None":
            return None
    except (ValueError, TypeError, AttributeError):
        pass

    # Check for boolean
    try:
        if str(actualvalue) == "True" or str(actualvalue) == "true":
            return True
        if str(actualvalue) == "False" or str(actualvalue) == "false":
            return False
    except (ValueError, TypeError, AttributeError):
        pass

    # Try JSON (dict/list)
    try:
        actualvalue = json.loads(actualvalue)
    except (json.JSONDecodeError, ValueError, TypeError):
        # Try int
        try:
            actualvalue = int(str(actualvalue))
        except (ValueError, TypeError):
            # Try float
            try:
                actualvalue = float(actualvalue)
            except (ValueError, TypeError):
                # Fall back to string
                actualvalue = str(actualvalue)

    return actualvalue
```

### Test

```bash
cd /Users/wea/src/allenlab/Squid/software
pytest tests/squid/test_config.py -v
```

### Commit

```bash
git add software/control/_def.py
git commit -m "Replace bare except clauses in _def.py

Bare except catches SystemExit, KeyboardInterrupt, and other exceptions
that should propagate. Now catches only expected exceptions:
- ValueError, TypeError, AttributeError for type conversions
- json.JSONDecodeError for JSON parsing

This prevents masking of real errors during configuration loading.
"
```

---

## Phase 1 Complete

After completing all tasks:

1. Run full test suite:
```bash
cd /Users/wea/src/allenlab/Squid/software
pytest --tb=short -v
```

2. Manual smoke test:
```bash
python main_hcs.py --simulation
# Run an acquisition
# Verify no crashes
```

3. Review commits:
```bash
git log --oneline -5
```

Expected commits:
```
abc1234 Replace bare except clauses in _def.py
def5678 Use ThreadSafeValue for _current_capture_info
ghi9012 Apply safe_callback to MultiPointWorker._image_callback
jkl3456 Add ThreadSafeValue and ThreadSafeFlag utilities
mno7890 Add safe_callback utility for error containment
```
