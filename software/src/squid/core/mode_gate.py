"""Global mode gate for backend safety.

This is the minimal backend-owned arbitration mechanism used to prevent unsafe
UI-originated commands (published via EventBus) from touching hardware during
critical modes like acquisition.
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Optional

from squid.core.events import EventBus, GlobalModeChanged


class GlobalMode(Enum):
    IDLE = auto()
    LIVE = auto()
    ACQUIRING = auto()
    ABORTING = auto()
    ERROR = auto()


class GlobalModeGate:
    """Backend-owned global mode state + notifier."""

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._lock = threading.RLock()
        self._mode = GlobalMode.IDLE

    def get_mode(self) -> GlobalMode:
        with self._lock:
            return self._mode

    def set_mode(self, new_mode: GlobalMode, reason: str) -> None:
        with self._lock:
            old_mode = self._mode
            if new_mode is old_mode:
                return
            self._mode = new_mode
        self._bus.publish(
            GlobalModeChanged(old_mode=old_mode.name, new_mode=new_mode.name, reason=reason)
        )

    def try_set_mode(self, expected_mode: GlobalMode, new_mode: GlobalMode, reason: str) -> bool:
        with self._lock:
            if self._mode is not expected_mode:
                return False
            self._mode = new_mode
        self._bus.publish(
            GlobalModeChanged(old_mode=expected_mode.name, new_mode=new_mode.name, reason=reason)
        )
        return True

    def restore_mode(self, previous_mode: GlobalMode, reason: str) -> None:
        self.set_mode(previous_mode, reason=reason)

    def blocked_for_ui_hardware_commands(self) -> bool:
        """Return True if UI-originated hardware commands should be rejected."""
        mode = self.get_mode()
        return mode in (GlobalMode.ACQUIRING, GlobalMode.ABORTING)

