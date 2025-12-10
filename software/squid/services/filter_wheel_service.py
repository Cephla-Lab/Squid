from __future__ import annotations

import threading
from typing import Optional

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetFilterPositionCommand,
    HomeFilterWheelCommand,
    FilterPositionChanged,
)
from squid.abc import AbstractFilterWheelController


class FilterWheelService(BaseService):
    """Thread-safe wrapper around a filter wheel controller."""

    def __init__(
        self,
        filter_wheel: Optional[AbstractFilterWheelController],
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._wheel = filter_wheel
        self._lock = threading.RLock()

        if self._wheel is not None:
            self.subscribe(SetFilterPositionCommand, self._on_set_position)
            self.subscribe(HomeFilterWheelCommand, self._on_home)
            self._publish_initial_position()

    def _publish_initial_position(self) -> None:
        """Publish current position on startup so UI reflects hardware state."""
        if self._wheel is None:
            return
        for wheel_index in (1, 0):
            try:
                with self._lock:
                    position = self._wheel.get_filter_wheel_position(wheel_index)
            except Exception:
                continue
            self.publish(
                FilterPositionChanged(position=position, wheel_index=wheel_index)
            )
            break

    def _on_set_position(self, cmd: SetFilterPositionCommand) -> None:
        """Handle SetFilterPositionCommand from EventBus."""
        if self._wheel is None:
            return
        with self._lock:
            self._wheel.set_filter_wheel_position(cmd.position, cmd.wheel_index)
            actual = self._wheel.get_filter_wheel_position(cmd.wheel_index)
        self.publish(
            FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index)
        )

    def _on_home(self, cmd: HomeFilterWheelCommand) -> None:
        """Handle HomeFilterWheelCommand from EventBus."""
        if self._wheel is None:
            return
        with self._lock:
            self._wheel.home(cmd.wheel_index)
            actual = self._wheel.get_filter_wheel_position(cmd.wheel_index)
        self.publish(
            FilterPositionChanged(position=actual, wheel_index=cmd.wheel_index)
        )

    def set_position(self, position: int, wheel_index: int = 0) -> int:
        """Direct call for controllers."""
        if self._wheel is None:
            raise ValueError("Filter wheel hardware not available")
        with self._lock:
            self._wheel.set_filter_wheel_position(position, wheel_index)
            return self._wheel.get_filter_wheel_position(wheel_index)

    def get_position(self, wheel_index: int = 0) -> int:
        if self._wheel is None:
            raise ValueError("Filter wheel hardware not available")
        with self._lock:
            return self._wheel.get_filter_wheel_position(wheel_index)

    def is_available(self) -> bool:
        return self._wheel is not None
