from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Dict

from squid.mcs.services.base import BaseService
from squid.core.events import (
    EventBus,
    SetIlluminationCommand,
    IlluminationStateChanged,
)
from squid.core.abc import LightSource


class IlluminationService(BaseService):
    """Thread-safe service for illumination operations.

    Subscribes to: SetIlluminationCommand
    Publishes: IlluminationStateChanged
    """

    def __init__(
        self,
        illumination: LightSource | Dict[int, LightSource],
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._sources: Dict[int, LightSource] = (
            illumination if isinstance(illumination, dict) else {0: illumination}
        )
        self._lock = threading.RLock()

        # Subscribe to commands
        self.subscribe(SetIlluminationCommand, self._on_set_illumination)

    def _on_set_illumination(self, cmd: SetIlluminationCommand) -> None:
        """Handle SetIlluminationCommand."""
        with self._lock:
            source = self._get_source(cmd.channel)
            source.set_intensity(cmd.channel, cmd.intensity)
            source.set_shutter_state(cmd.channel, cmd.on)
        # Publish outside lock to avoid deadlocks
        self.publish(
            IlluminationStateChanged(
                channel=cmd.channel,
                intensity=cmd.intensity,
                on=cmd.on,
            )
        )

    def _get_source(self, channel: int) -> LightSource:
        """Return the LightSource for the given channel or raise."""
        try:
            return self._sources[channel]
        except KeyError:
            msg = f"Illumination channel {channel} not configured"
            self._log.error(msg)
            raise ValueError(msg)

    def set_channel_power(self, channel: int, power: float) -> None:
        """Set the power of a channel. Thread-safe."""
        with self._lock:
            self._get_source(channel).set_intensity(channel, power)

    def get_channel_power(self, channel: int) -> float:
        """Get the power of a channel. Thread-safe."""
        with self._lock:
            return self._get_source(channel).get_intensity(channel)

    def turn_on_channel(self, channel: int) -> None:
        """Turn on a channel. Thread-safe."""
        with self._lock:
            self._get_source(channel).set_shutter_state(channel, True)

    def turn_off_channel(self, channel: int) -> None:
        """Turn off a channel. Thread-safe."""
        with self._lock:
            self._get_source(channel).set_shutter_state(channel, False)

    def get_shutter_state(self, channel: int) -> bool:
        """Get the shutter state of a channel. Thread-safe."""
        with self._lock:
            return self._get_source(channel).get_shutter_state(channel)

    def has_channel(self, channel: int) -> bool:
        """Return True if the service knows about the given channel."""
        with self._lock:
            return channel in self._sources

    
