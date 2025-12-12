"""Thread assertions for actor model enforcement.

Provides utilities to ensure operations run on the correct thread,
enabling runtime verification of the actor model invariants.
"""

import threading
from typing import Optional

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)

_backend_thread: Optional[threading.Thread] = None


def set_backend_thread(thread: threading.Thread) -> None:
    """Set the backend actor thread for assertions.

    Args:
        thread: The thread that will be used as the backend actor thread.
    """
    global _backend_thread
    _backend_thread = thread
    _log.debug(f"Backend thread set to: {thread.name}")


def get_backend_thread() -> Optional[threading.Thread]:
    """Get the configured backend thread, if any."""
    return _backend_thread


def clear_backend_thread() -> None:
    """Clear the backend thread setting. Useful for testing."""
    global _backend_thread
    _backend_thread = None


def assert_backend_thread(operation: str) -> None:
    """Assert current thread is the backend thread.

    Args:
        operation: Description of the operation being performed, for error messages.

    Raises:
        RuntimeError: If not on the backend thread.
    """
    if _backend_thread is None:
        return  # Not configured yet - allow operation
    current = threading.current_thread()
    if current != _backend_thread:
        raise RuntimeError(
            f"{operation} must run on backend thread. "
            f"Current: {current.name}, Expected: {_backend_thread.name}"
        )


def assert_not_backend_thread(operation: str) -> None:
    """Assert current thread is NOT the backend thread.

    Args:
        operation: Description of the operation being performed, for error messages.

    Raises:
        RuntimeError: If on the backend thread.
    """
    if _backend_thread is None:
        return  # Not configured yet - allow operation
    current = threading.current_thread()
    if current == _backend_thread:
        raise RuntimeError(
            f"{operation} must NOT run on backend thread. "
            f"Current: {current.name}"
        )
