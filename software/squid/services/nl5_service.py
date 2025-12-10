"""Service wrapper for NL5 laser hardware."""
from __future__ import annotations

from squid.services.base import BaseService
from squid.events import EventBus


class NL5Service(BaseService):
    """Thread-safe wrapper around NL5 addon operations."""

    def __init__(self, nl5, event_bus: EventBus):
        super().__init__(event_bus)
        self._nl5 = nl5
        self._lock = self._make_lock()

    def _make_lock(self):
        import threading

        return threading.RLock()

    def set_active_channel(self, channel: int) -> None:
        with self._lock:
            self._nl5.set_active_channel(channel)

    def set_laser_power(self, channel: int, power: int) -> None:
        with self._lock:
            self._nl5.set_laser_power(channel, power)

    def start_acquisition(self) -> None:
        with self._lock:
            self._nl5.start_acquisition()
