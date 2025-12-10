"""Microscope mode/channel controller.

Manages microscope channel/configuration switching. When switching modes,
coordinates camera settings, illumination, and filters.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from squid.events import (
    SetMicroscopeModeCommand,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    MicroscopeModeChanged,
)

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.services import CameraService
    from squid.services.illumination_service import IlluminationService
    from squid.services.filter_wheel_service import FilterWheelService


@dataclass
class MicroscopeModeState:
    """State managed by MicroscopeModeController."""

    current_mode: Optional[str] = None
    available_modes: tuple[str, ...] = ()


class MicroscopeModeController:
    """Manages microscope channel/mode switching.

    Coordinates camera settings, illumination, and filters when switching modes.

    Subscribes to: SetMicroscopeModeCommand
    Publishes: MicroscopeModeChanged
    """

    def __init__(
        self,
        camera_service: "CameraService",
        channel_configs: dict,
        event_bus: "EventBus",
        filter_wheel_service: Optional["FilterWheelService"] = None,
        illumination_service: Optional["IlluminationService"] = None,
    ) -> None:
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._channel_configs = channel_configs
        self._bus = event_bus
        self._lock = threading.RLock()

        self._state = MicroscopeModeState(
            current_mode=None,
            available_modes=tuple(channel_configs.keys()),
        )

        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_mode)

    @property
    def state(self) -> MicroscopeModeState:
        """Get current state."""
        return self._state

    def _on_set_mode(self, cmd: SetMicroscopeModeCommand) -> None:
        """Handle SetMicroscopeModeCommand."""
        mode = cmd.configuration_name

        with self._lock:
            if mode not in self._channel_configs:
                return

            config = self._channel_configs[mode]

            # Apply camera settings via events (so CameraService handles validation)
            if hasattr(config, "exposure_time") or hasattr(config, "exposure_ms"):
                exposure = getattr(config, "exposure_time", None) or getattr(
                    config, "exposure_ms", None
                )
                if exposure is not None:
                    self._bus.publish(SetExposureTimeCommand(exposure_time_ms=exposure))

            if hasattr(config, "analog_gain"):
                self._bus.publish(SetAnalogGainCommand(gain=config.analog_gain))

            # Apply illumination via service if available
            if self._illumination:
                if hasattr(config, "illumination_source") and hasattr(config, "intensity"):
                    self._illumination.set_channel_power(
                        config.illumination_source,
                        config.intensity,
                    )

            # Apply filter wheel if available
            if (
                self._filter_wheel
                and hasattr(config, "emission_filter_position")
                and config.emission_filter_position is not None
                and self._filter_wheel.is_available()
            ):
                self._filter_wheel.set_position(config.emission_filter_position)

            # Update state inside lock
            self._state = replace(self._state, current_mode=mode)

        # Publish outside lock
        self._bus.publish(MicroscopeModeChanged(configuration_name=mode))

    def apply_mode_for_acquisition(self, mode: str) -> None:
        """Apply mode settings for acquisition (direct calls for speed).

        Used during acquisition when event round-trips would be too slow.
        """
        with self._lock:
            if mode not in self._channel_configs:
                return

            config = self._channel_configs[mode]

            # Direct service calls for efficiency
            if hasattr(config, "exposure_time") or hasattr(config, "exposure_ms"):
                exposure = getattr(config, "exposure_time", None) or getattr(
                    config, "exposure_ms", None
                )
                if exposure is not None:
                    self._camera.set_exposure_time(exposure)

            if hasattr(config, "analog_gain"):
                self._camera.set_analog_gain(config.analog_gain)

            if self._illumination:
                if hasattr(config, "illumination_source") and hasattr(config, "intensity"):
                    self._illumination.set_channel_power(
                        config.illumination_source,
                        config.intensity,
                    )

            if (
                self._filter_wheel
                and hasattr(config, "emission_filter_position")
                and config.emission_filter_position is not None
                and self._filter_wheel.is_available()
            ):
                self._filter_wheel.set_position(config.emission_filter_position)

            # Update state inside lock
            self._state = replace(self._state, current_mode=mode)

    def get_available_modes(self) -> tuple[str, ...]:
        """Get list of available mode names."""
        return self._state.available_modes

    def update_channel_configs(self, channel_configs: dict) -> None:
        """Replace channel configuration mapping and update available modes."""
        with self._lock:
            self._channel_configs = channel_configs
            self._state = replace(
                self._state, available_modes=tuple(channel_configs.keys())
            )
