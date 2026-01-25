"""Test timing helpers for scaling sleeps and polling intervals."""

from __future__ import annotations

import os
import time
from typing import Optional

_DEFAULT_SPEEDUP = 5.0


def _parse_speedup(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        speedup = float(value)
    except (TypeError, ValueError):
        return None
    if speedup <= 0:
        return None
    return speedup


def get_test_speedup() -> float:
    """Return test speedup factor from environment (>= 1.0)."""
    speedup = _parse_speedup(os.environ.get("SQUID_TEST_SPEEDUP"))
    if speedup is not None:
        return max(speedup, 1.0)

    fast_flag = os.environ.get("SQUID_TEST_FAST")
    if fast_flag is None:
        return 1.0

    if fast_flag.strip().lower() in ("1", "true", "yes", "on"):
        return _DEFAULT_SPEEDUP

    speedup = _parse_speedup(fast_flag)
    if speedup is not None:
        return max(speedup, 1.0)

    return 1.0


def scale_duration(seconds: float, *, min_seconds: float = 0.0) -> float:
    """Scale a duration by the test speedup factor."""
    if seconds <= 0:
        return seconds

    speedup = get_test_speedup()
    if speedup <= 1.0:
        return seconds

    scaled = seconds / speedup
    if min_seconds > 0:
        return max(scaled, min_seconds)
    return scaled


def sleep(seconds: float, *, min_seconds: float = 0.0) -> None:
    """Sleep for a scaled duration when test speedup is enabled."""
    time.sleep(scale_duration(seconds, min_seconds=min_seconds))
