"""Service wrapper for objective changer hardware."""
from __future__ import annotations

from squid.backend.services.base import BaseService
from squid.core.events import EventBus


class ObjectiveChangerService(BaseService):
    """Thin, thread-safe wrapper around an objective changer."""

    def __init__(self, objective_changer, event_bus: EventBus):
        super().__init__(event_bus)
        self._objective_changer = objective_changer
        self._lock = self._make_lock()

    def _make_lock(self):
        import threading

        return threading.RLock()

    def set_position(self, position: int) -> None:
        """Move to the requested objective position."""
        with self._lock:
            self._objective_changer.set_position(position)

    def get_current_position(self) -> int:
        """Return current objective position."""
        with self._lock:
            return self._objective_changer.current_position

    def get_objective_info(self, position: int):
        """Return metadata about an objective position."""
        with self._lock:
            return self._objective_changer.get_objective_info(position)

    def home(self) -> None:
        """Home the objective changer if supported."""
        home_fn = getattr(self._objective_changer, "home", None)
        if home_fn:
            with self._lock:
                home_fn()
