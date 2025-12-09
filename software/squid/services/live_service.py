# squid/services/live_service.py
"""Service for live view control."""
from __future__ import annotations
from typing import TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
)

if TYPE_CHECKING:
    from control.core.display import LiveController


class LiveService(BaseService):
    """
    Service layer for live view operations.

    Handles starting/stopping live view through the event bus.
    Widgets publish StartLiveCommand/StopLiveCommand, this service
    handles them and publishes LiveStateChanged notifications.
    """

    def __init__(self, live_controller: "LiveController", event_bus: EventBus):
        """
        Initialize live service.

        Args:
            live_controller: LiveController instance for camera streaming
            event_bus: EventBus for communication
        """
        super().__init__(event_bus)
        self._live_controller = live_controller

        self.subscribe(StartLiveCommand, self._on_start_live)
        self.subscribe(StopLiveCommand, self._on_stop_live)

    def _on_start_live(self, event: StartLiveCommand) -> None:
        """Handle StartLiveCommand event."""
        self._log.info(f"Starting live view (configuration={event.configuration})")
        self._live_controller.start_live()
        self.publish(LiveStateChanged(is_live=True, configuration=event.configuration))

    def _on_stop_live(self, event: StopLiveCommand) -> None:
        """Handle StopLiveCommand event."""
        self._log.info("Stopping live view")
        self._live_controller.stop_live()
        self.publish(LiveStateChanged(is_live=False, configuration=None))
