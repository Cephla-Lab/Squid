from __future__ import annotations
from typing import TYPE_CHECKING
from squid.services.base import BaseService
from squid.events import (
    EventBus,
    SetIlluminationCommand,
    IlluminationStateChanged,
)
from squid.abc import LightSource

class IlluminationService(BaseService):
    """Service for illumination operations."""
    def __init__(self, illumination: LightSource):
        super().__init__()
        self._illumination = illumination

    def set_channel_power(self, channel: int, power: float) -> None:
        """Set the power of a channel."""
        self._illumination.set_intensity(channel, power)

    def get_channel_power(self, channel: int) -> float:
        """Get the power of a channel."""
        return self._illumination.get_intensity(channel)

    def turn_on_channel(self, channel: int) -> None:
        """Turn on a channel."""
        self._illumination.set_shutter_state(channel, True)

    def turn_off_channel(self, channel: int) -> None:
        """Turn off a channel."""
        self._illumination.set_shutter_state(channel, False)

    