"""
Cancellation token for cooperative task cancellation.

Provides a thread-safe mechanism for cancelling, pausing, and resuming
long-running operations. Workers check the token at cancellation points
to determine if they should abort or pause.

Usage:
    # Create a token
    token = CancelToken()

    # Pass to worker
    def worker(token: CancelToken):
        for i in range(1000):
            token.raise_if_cancelled()  # Check for cancellation

            # Check for pause and wait if paused
            token.wait_if_paused()

            # Do work...

    # Control from main thread
    token.pause()   # Pause the worker
    token.resume()  # Resume the worker
    token.cancel()  # Cancel the worker
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class CancellationError(Exception):
    """Raised when a CancelToken is cancelled."""

    pass


class TokenState(Enum):
    """State of a CancelToken."""

    RUNNING = auto()
    PAUSED = auto()
    CANCELLED = auto()


@dataclass
class CancelToken:
    """Thread-safe cancellation token for cooperative cancellation.

    Supports:
    - Cancellation: Stop the operation entirely
    - Pause/Resume: Temporarily halt the operation
    - Timeout waiting for pause to complete
    - Chaining: Child tokens that cancel when parent cancels

    Thread Safety:
    - All state changes are protected by a lock
    - Pause waiting uses a threading.Event for efficient blocking
    """

    _state: TokenState = field(default=TokenState.RUNNING, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _pause_event: threading.Event = field(default_factory=threading.Event, init=False)
    _cancel_reason: Optional[str] = field(default=None, init=False)
    _parent: Optional["CancelToken"] = field(default=None)

    def __post_init__(self) -> None:
        """Initialize the pause event as set (not paused)."""
        self._pause_event.set()

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        with self._lock:
            if self._state == TokenState.CANCELLED:
                return True
            # Check parent chain
            if self._parent is not None:
                return self._parent.is_cancelled
            return False

    @property
    def is_paused(self) -> bool:
        """Check if pause was requested."""
        with self._lock:
            if self._state == TokenState.PAUSED:
                return True
            # Check parent chain
            if self._parent is not None:
                return self._parent.is_paused
            return False

    @property
    def is_running(self) -> bool:
        """Check if in normal running state (not paused or cancelled)."""
        return not self.is_cancelled and not self.is_paused

    @property
    def cancel_reason(self) -> Optional[str]:
        """Get the cancellation reason, if any."""
        with self._lock:
            if self._cancel_reason:
                return self._cancel_reason
            if self._parent is not None and self._parent.is_cancelled:
                return self._parent.cancel_reason
            return None

    @property
    def state(self) -> TokenState:
        """Get the current token state."""
        with self._lock:
            # Cancelled state overrides everything
            if self._state == TokenState.CANCELLED:
                return TokenState.CANCELLED
            if self._parent is not None and self._parent.is_cancelled:
                return TokenState.CANCELLED

            # Check for pause
            if self._state == TokenState.PAUSED:
                return TokenState.PAUSED
            if self._parent is not None and self._parent.is_paused:
                return TokenState.PAUSED

            return TokenState.RUNNING

    # ========================================================================
    # Control Methods
    # ========================================================================

    def cancel(self, reason: Optional[str] = None) -> None:
        """Request cancellation.

        Args:
            reason: Optional reason for cancellation
        """
        with self._lock:
            self._state = TokenState.CANCELLED
            self._cancel_reason = reason
            # Unblock any paused waiters so they can see the cancellation
            self._pause_event.set()

    def pause(self) -> None:
        """Request pause. Workers will block at wait_if_paused()."""
        with self._lock:
            if self._state == TokenState.RUNNING:
                self._state = TokenState.PAUSED
                self._pause_event.clear()

    def resume(self) -> None:
        """Resume from pause."""
        with self._lock:
            if self._state == TokenState.PAUSED:
                self._state = TokenState.RUNNING
                self._pause_event.set()

    def reset(self) -> None:
        """Reset to running state. Use with caution."""
        with self._lock:
            self._state = TokenState.RUNNING
            self._cancel_reason = None
            self._pause_event.set()

    # ========================================================================
    # Worker Methods
    # ========================================================================

    def raise_if_cancelled(self) -> None:
        """Raise CancellationError if cancelled.

        Call this at cancellation points in your worker code.

        Raises:
            CancellationError: If cancellation was requested
        """
        if self.is_cancelled:
            raise CancellationError(self.cancel_reason or "Operation cancelled")

    def wait_if_paused(self, timeout: Optional[float] = None) -> bool:
        """Block if paused, waiting for resume or cancel.

        Args:
            timeout: Maximum time to wait in seconds. None = wait forever.

        Returns:
            True if resumed, False if still paused (timeout expired)

        Raises:
            CancellationError: If cancelled while waiting
        """
        # Fast path: not paused
        if not self.is_paused:
            return True

        # Wait on event
        deadline = time.time() + timeout if timeout is not None else None

        while True:
            # Check for cancellation first
            self.raise_if_cancelled()

            # Wait for resume
            remaining = None
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False

            # Wait with small timeout to allow cancel checks
            wait_time = min(0.1, remaining) if remaining else 0.1
            if self._pause_event.wait(timeout=wait_time):
                # Event was set - either resumed or cancelled
                self.raise_if_cancelled()
                return True

            # Check parent pause state
            if self._parent is not None:
                if not self._parent.wait_if_paused(timeout=0):
                    # Parent is still paused
                    continue

    def check_point(self) -> None:
        """Combined check: raises if cancelled, waits if paused.

        Convenience method for common worker pattern.

        Raises:
            CancellationError: If cancelled
        """
        self.raise_if_cancelled()
        self.wait_if_paused()

    # ========================================================================
    # Child Tokens
    # ========================================================================

    def create_child(self) -> "CancelToken":
        """Create a child token that cancels when this token cancels.

        Child tokens can be independently cancelled or paused, but will
        also respond to parent cancellation/pause.

        Returns:
            New CancelToken linked to this parent
        """
        child = CancelToken()
        child._parent = self
        return child


from typing import Callable as CallableType


def run_with_timeout(
    func: CallableType,
    token: CancelToken,
    timeout_s: float,
    *args,
    **kwargs,
) -> bool:
    """Run a function with cancellation token and timeout.

    Args:
        func: Function to run (should check token periodically)
        token: CancelToken for cancellation
        timeout_s: Maximum time to wait
        *args: Arguments to pass to func
        **kwargs: Keyword arguments to pass to func

    Returns:
        True if function completed, False if timed out
    """
    result_container = {"completed": False, "error": None}

    def wrapper():
        try:
            func(*args, **kwargs)
            result_container["completed"] = True
        except CancellationError:
            pass
        except Exception as e:
            result_container["error"] = e

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        # Timeout - cancel the token
        token.cancel("Timeout")
        thread.join(timeout=1.0)
        return False

    if result_container["error"]:
        raise result_container["error"]

    return result_container["completed"]
