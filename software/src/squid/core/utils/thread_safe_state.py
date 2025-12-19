"""
Thread-safe state management utilities.

Provides wrappers for shared state that is accessed from multiple threads,
ensuring proper synchronization to prevent race conditions.

Usage:
    from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

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

T = TypeVar("T")


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
