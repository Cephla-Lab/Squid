"""Simple file-based cache utilities for persisting UI state.

These utilities provide lightweight persistence for user preferences
like last-used directories, without requiring database or config management.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


def _get_cache_dir() -> Path:
    """Get the cache directory, creating it if necessary.

    Returns:
        Path to the cache directory (software/cache/).
    """
    # Cache is in software/cache/ relative to the source tree
    cache_dir = Path(__file__).parent.parent.parent.parent.parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_last_used_saving_path(default: str) -> str:
    """Get the last used saving path from cache, or return the default.

    Args:
        default: Default path to return if no cached path exists or is invalid.

    Returns:
        The cached path if valid and exists, otherwise the default.
    """
    cache_file = _get_cache_dir() / "last_saving_path.txt"
    try:
        if cache_file.exists():
            path = cache_file.read_text().strip()
            if path and os.path.isdir(path):
                return path
    except OSError as e:
        _log.debug(f"Could not read cached saving path: {e}")
    return default


def save_last_used_saving_path(path: str) -> None:
    """Save the last used saving path to cache file.

    Args:
        path: The path to save. Empty paths are ignored.
    """
    if not path:
        return

    cache_file = _get_cache_dir() / "last_saving_path.txt"
    try:
        cache_file.write_text(path)
        _log.debug(f"Saved last saving path to cache: {path}")
    except OSError as e:
        _log.debug(f"Could not save saving path to cache: {e}")
        # Silently fail - caching is a convenience feature


def get_cached_value(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a cached string value by key.

    Args:
        key: Cache key (used as filename, so should be filesystem-safe).
        default: Default value to return if not cached.

    Returns:
        The cached value or default.
    """
    cache_file = _get_cache_dir() / f"{key}.txt"
    try:
        if cache_file.exists():
            return cache_file.read_text().strip() or default
    except OSError as e:
        _log.debug(f"Could not read cached value for {key}: {e}")
    return default


def set_cached_value(key: str, value: str) -> None:
    """Set a cached string value.

    Args:
        key: Cache key (used as filename).
        value: Value to cache.
    """
    if not value:
        return

    cache_file = _get_cache_dir() / f"{key}.txt"
    try:
        cache_file.write_text(value)
    except OSError as e:
        _log.debug(f"Could not save cached value for {key}: {e}")
