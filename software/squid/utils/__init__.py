"""Squid utilities package."""
from squid.utils.safe_callback import safe_callback, CallbackResult
from squid.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
]
