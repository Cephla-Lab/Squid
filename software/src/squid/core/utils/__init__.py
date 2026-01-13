"""Squid utilities package."""

from squid.core.utils.safe_callback import safe_callback, CallbackResult
from squid.core.utils.thread_safe_state import ThreadSafeValue, ThreadSafeFlag
from squid.core.utils.geometry_utils import (
    get_effective_well_size,
    get_tile_positions,
    calculate_well_coverage,
)
from squid.core.utils.cache import (
    get_last_used_saving_path,
    save_last_used_saving_path,
    get_cached_value,
    set_cached_value,
)
from squid.core.utils.cancel_token import (
    CancelToken,
    CancellationError,
    TokenState,
)

__all__ = [
    "safe_callback",
    "CallbackResult",
    "ThreadSafeValue",
    "ThreadSafeFlag",
    "get_effective_well_size",
    "get_tile_positions",
    "calculate_well_coverage",
    "get_last_used_saving_path",
    "save_last_used_saving_path",
    "get_cached_value",
    "set_cached_value",
    # Cancellation
    "CancelToken",
    "CancellationError",
    "TokenState",
]
