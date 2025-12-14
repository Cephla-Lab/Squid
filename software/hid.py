"""
Stub implementation of the `hid` module for test environments without HID support.
"""

from typing import List, Dict, Any


def enumerate(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:  # pragma: no cover
    return []


class device:
    """Minimal hid.device stub."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def open_path(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def write(self, *args: Any, **kwargs: Any) -> int:  # pragma: no cover
        return 0

    def read(self, *args: Any, **kwargs: Any) -> list:  # pragma: no cover
        return []

    def close(self) -> None:  # pragma: no cover
        pass
