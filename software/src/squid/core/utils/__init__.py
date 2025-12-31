"""Squid utilities package."""

from squid.core.utils.safe_callback import safe_callback, CallbackResult
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag
from squid.core.utils.geometry_utils import (
    get_effective_well_size,
    get_tile_positions,
    calculate_well_coverage,
)

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
    "get_effective_well_size",
    "get_tile_positions",
    "calculate_well_coverage",
]
