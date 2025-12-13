from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Dict, Optional, Protocol, runtime_checkable, Any

from squid.mcs.services.base import BaseService
from squid.core.events import (
    EventBus,
    SetIlluminationCommand,
    IlluminationStateChanged,
)
from squid.core.abc import LightSource


@runtime_checkable
class _LegacyIlluminationController(Protocol):
    def set_intensity(self, channel: int, intensity: float) -> Any: ...
    def turn_on_illumination(self, channel: Optional[int] = None) -> Any: ...
    def turn_off_illumination(self, channel: Optional[int] = None) -> Any: ...


class IlluminationService(BaseService):
    """Thread-safe service for illumination operations.

    Subscribes to: SetIlluminationCommand
    Publishes: IlluminationStateChanged
    """

    def __init__(
        self,
        illumination: LightSource | Dict[int, LightSource] | _LegacyIlluminationController,
        event_bus: EventBus,
        mode_gate=None,
    ) -> None:
        super().__init__(event_bus, mode_gate=mode_gate)
        self._sources: Optional[Dict[int, LightSource]] = (
            illumination if isinstance(illumination, dict) else None
        )
        self._legacy_controller: Optional[_LegacyIlluminationController] = (
            illumination if isinstance(illumination, _LegacyIlluminationController) else None
        )
        self._single_source: Optional[LightSource] = (
            illumination
            if (not isinstance(illumination, dict) and self._legacy_controller is None)
            else None
        )
        self._lock = threading.RLock()

        # Subscribe to commands
        self.subscribe(SetIlluminationCommand, self._on_set_illumination)

    def _on_set_illumination(self, cmd: SetIlluminationCommand) -> None:
        """Handle SetIlluminationCommand."""
        if self._blocked_for_ui_hardware_commands():
            self._log.info("Ignoring %s due to global mode gate", type(cmd).__name__)
            return
        with self._lock:
            self._set_intensity(cmd.channel, cmd.intensity)
            self._set_on(cmd.channel, cmd.on)
        # Publish outside lock to avoid deadlocks
        self.publish(
            IlluminationStateChanged(
                channel=cmd.channel,
                intensity=cmd.intensity,
                on=cmd.on,
            )
        )

    def _set_intensity(self, channel: int, intensity: float) -> None:
        if self._legacy_controller is not None:
            self._legacy_controller.set_intensity(channel, intensity)
            return
        source = self._get_source(channel)
        source.set_intensity(channel, intensity)

    def _set_on(self, channel: int, on: bool) -> None:
        if self._legacy_controller is not None:
            if on:
                self._legacy_controller.turn_on_illumination(channel)
            else:
                self._legacy_controller.turn_off_illumination(channel)
            return
        source = self._get_source(channel)
        source.set_shutter_state(channel, on)

    def _get_source(self, channel: int) -> LightSource:
        """Return a LightSource for the given channel or raise."""
        if self._single_source is not None:
            return self._single_source
        if self._sources is not None:
            try:
                return self._sources[channel]
            except KeyError:
                msg = f"Illumination channel {channel} not configured"
                self._log.error(msg)
                raise ValueError(msg)
        msg = "Illumination service not configured"
        self._log.error(msg)
        raise ValueError(msg)

    def set_channel_power(self, channel: int, power: float) -> None:
        """Set the power of a channel. Thread-safe."""
        with self._lock:
            self._set_intensity(channel, power)

    def get_channel_power(self, channel: int) -> float:
        """Get the power of a channel. Thread-safe."""
        with self._lock:
            if self._legacy_controller is not None:
                # Legacy controller does not provide a consistent get_intensity contract.
                return 0.0
            return self._get_source(channel).get_intensity(channel)

    def turn_on_channel(self, channel: int) -> None:
        """Turn on a channel. Thread-safe."""
        with self._lock:
            self._set_on(channel, True)

    def turn_off_channel(self, channel: int) -> None:
        """Turn off a channel. Thread-safe."""
        with self._lock:
            self._set_on(channel, False)

    def get_shutter_state(self, channel: int) -> bool:
        """Get the shutter state of a channel. Thread-safe."""
        with self._lock:
            if self._legacy_controller is not None:
                state = getattr(self._legacy_controller, "get_shutter_state", None)
                if callable(state):
                    # Some legacy implementations return a mapping of channel->bool.
                    value = state()
                    if isinstance(value, dict):
                        return bool(value.get(channel, False))
                return False
            return self._get_source(channel).get_shutter_state(channel)

    def has_channel(self, channel: int) -> bool:
        """Return True if the service knows about the given channel."""
        with self._lock:
            if self._legacy_controller is not None:
                mappings = getattr(self._legacy_controller, "channel_mappings_TTL", None)
                if isinstance(mappings, dict):
                    return channel in mappings
                return True
            if self._single_source is not None:
                return True
            if self._sources is None:
                return False
            return channel in self._sources

    
