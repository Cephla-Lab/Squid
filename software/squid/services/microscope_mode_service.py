"""Service for microscope mode configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from squid.events import (
    EventBus,
    MicroscopeModeChanged,
    SetMicroscopeModeCommand,
)
from squid.services.base import BaseService

if TYPE_CHECKING:
    from control.core.configuration import ChannelConfigurationManager
    from control.core.display import LiveController


class MicroscopeModeService(BaseService):
    """
    Service for microscope mode/channel configuration.

    Handles setting the active channel configuration (exposure, gain, illumination).
    """

    def __init__(
        self,
        live_controller: "LiveController",
        channel_config_manager: "ChannelConfigurationManager",
        event_bus: EventBus,
    ):
        super().__init__(event_bus)
        self._live_controller = live_controller
        self._channel_config_manager = channel_config_manager

        self.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    def _on_set_mode(self, event: SetMicroscopeModeCommand) -> None:
        """Handle SetMicroscopeModeCommand."""
        self._log.info(f"Setting microscope mode to {event.configuration_name}")

        config = self._channel_config_manager.get_channel_configuration_by_name(
            event.objective, event.configuration_name
        )
        self._live_controller.set_microscope_mode(config)

        self.publish(MicroscopeModeChanged(configuration_name=event.configuration_name))
