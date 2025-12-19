"""
Safe callback wrapper for error containment.

Instead of letting exceptions propagate and crash the application,
this module provides utilities to catch exceptions and return them
as part of a result object.

Usage:
    from squid.core.utils.safe_callback import safe_callback

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
import squid.core.logging

T = TypeVar("T")

_log = squid.core.logging.get_logger("squid.utils.safe_callback")


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
    **kwargs: Any,
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

        return CallbackResult(success=False, error=e, stack_trace=stack)
