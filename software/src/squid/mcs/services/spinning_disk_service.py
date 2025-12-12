"""Service wrapper for spinning disk hardware (e.g., XLight)."""
from __future__ import annotations

from squid.mcs.services.base import BaseService
from squid.core.events import EventBus


class SpinningDiskService(BaseService):
    """Thread-safe wrapper for spinning disk control."""

    def __init__(self, spinning_disk, event_bus: EventBus):
        super().__init__(event_bus)
        self._disk = spinning_disk
        self._lock = self._make_lock()

    def _make_lock(self):
        import threading

        return threading.RLock()

    # Control methods
    def set_disk_position(self, in_beam: bool) -> None:
        with self._lock:
            self._disk.set_disk_position(in_beam)

    def set_spinning(self, spinning: bool) -> None:
        with self._lock:
            self._disk.set_spinning(spinning)

    def set_dichroic(self, position: int) -> None:
        with self._lock:
            self._disk.set_dichroic(position)

    def set_emission_filter(self, position: int) -> None:
        with self._lock:
            self._disk.set_emission_filter(position)

    # State accessors
    def is_available(self) -> bool:
        return self._disk is not None

    def is_disk_in(self) -> bool:
        with self._lock:
            return getattr(self._disk, "is_disk_in", False)

    def is_spinning(self) -> bool:
        with self._lock:
            return getattr(self._disk, "is_spinning", False)

    def motor_speed(self) -> int:
        with self._lock:
            return getattr(self._disk, "disk_motor_speed", 0)

    def current_dichroic(self) -> int:
        with self._lock:
            return getattr(self._disk, "current_dichroic", 0)

    def current_emission_filter(self) -> int:
        with self._lock:
            return getattr(self._disk, "current_emission_filter", 0)
