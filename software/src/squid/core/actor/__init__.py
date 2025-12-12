"""Actor model infrastructure for Squid.

This package provides the backend actor and related utilities for
enforcing thread-safe communication between the UI and control layers.
"""

from squid.core.actor.thread_assertions import (
    assert_backend_thread,
    assert_not_backend_thread,
    clear_backend_thread,
    get_backend_thread,
    set_backend_thread,
)
from squid.core.actor.backend_actor import (
    BackendActor,
    CommandEnvelope,
    Priority,
    PriorityCommandQueue,
)
from squid.core.actor.command_router import BackendCommandRouter

__all__ = [
    # Thread assertions
    "set_backend_thread",
    "get_backend_thread",
    "clear_backend_thread",
    "assert_backend_thread",
    "assert_not_backend_thread",
    # Backend actor
    "BackendActor",
    "CommandEnvelope",
    "Priority",
    "PriorityCommandQueue",
    # Command router
    "BackendCommandRouter",
]
