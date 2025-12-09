"""Service for camera trigger control."""

from __future__ import annotations

from typing import TYPE_CHECKING

from squid.events import (
    EventBus,
    SetTriggerFPSCommand,
    SetTriggerModeCommand,
    TriggerFPSChanged,
    TriggerModeChanged,
)
from squid.services.base import BaseService

if TYPE_CHECKING:
    from control.core.display import LiveController


class TriggerService(BaseService):
    """
    Service for camera trigger operations.

    Handles trigger mode (Software/Hardware/Continuous) and FPS settings.
    """

    def __init__(self, live_controller: "LiveController", event_bus: EventBus):
        super().__init__(event_bus)
        self._live_controller = live_controller

        self.subscribe(SetTriggerModeCommand, self._on_set_trigger_mode)
        self.subscribe(SetTriggerFPSCommand, self._on_set_trigger_fps)

    def _on_set_trigger_mode(self, event: SetTriggerModeCommand) -> None:
        """Handle SetTriggerModeCommand."""
        self._log.info(f"Setting trigger mode to {event.mode}")
        self._live_controller.set_trigger_mode(event.mode)
        self.publish(TriggerModeChanged(mode=event.mode))

    def _on_set_trigger_fps(self, event: SetTriggerFPSCommand) -> None:
        """Handle SetTriggerFPSCommand."""
        self._log.info(f"Setting trigger FPS to {event.fps}")
        self._live_controller.set_trigger_fps(event.fps)
        self.publish(TriggerFPSChanged(fps=event.fps))
