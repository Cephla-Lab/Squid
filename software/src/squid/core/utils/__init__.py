"""Squid utilities package."""

from squid.core.utils.safe_callback import safe_callback, CallbackResult
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
]
