"""Microscope mode/channel controller.

Manages microscope channel/configuration switching. When switching modes,
coordinates camera settings, illumination, and filters.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from squid.core.events import (
    SetMicroscopeModeCommand,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    MicroscopeModeChanged,
    UpdateChannelConfigurationCommand,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.services import CameraService
    from squid.backend.services.illumination_service import IlluminationService
    from squid.backend.services.filter_wheel_service import FilterWheelService
    from squid.backend.managers.objective_store import ObjectiveStore


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
        objective_store: Optional["ObjectiveStore"] = None,
    ) -> None:
        self._camera = camera_service
        self._illumination = illumination_service
        self._filter_wheel = filter_wheel_service
        self._channel_configs = channel_configs
        self._bus = event_bus
        self._objective_store = objective_store
        self._lock = threading.RLock()

        self._state = MicroscopeModeState(
            current_mode=None,
            available_modes=tuple(channel_configs.keys()),
        )

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if self._bus is None:
            return
        self._bus.subscribe(SetMicroscopeModeCommand, self._on_set_mode)
        self._bus.subscribe(UpdateChannelConfigurationCommand, self._on_update_config)

    @property
    def state(self) -> MicroscopeModeState:
        """Get current state."""
        return self._state

    def _objective_matches(self, objective: Optional[str]) -> bool:
        if self._objective_store is None or objective is None:
            return True
        current = getattr(self._objective_store, "current_objective", None)
        if current is None:
            return True
        return objective == current

    def _on_update_config(self, cmd: UpdateChannelConfigurationCommand) -> None:
        """Handle UpdateChannelConfigurationCommand - update internal config cache."""
        if not self._objective_matches(cmd.objective_name):
            return
        with self._lock:
            config_name = cmd.config_name
            if config_name not in self._channel_configs:
                return

            config = self._channel_configs[config_name]

            # Update the config object with new values
            if cmd.exposure_time_ms is not None and hasattr(config, "exposure_time"):
                config.exposure_time = cmd.exposure_time_ms
            if cmd.analog_gain is not None and hasattr(config, "analog_gain"):
                config.analog_gain = cmd.analog_gain
            if cmd.illumination_intensity is not None:
                if hasattr(config, "illumination_intensity"):
                    config.illumination_intensity = cmd.illumination_intensity
                elif hasattr(config, "intensity"):
                    config.intensity = cmd.illumination_intensity

    def _on_set_mode(self, cmd: SetMicroscopeModeCommand) -> None:
        """Handle SetMicroscopeModeCommand."""
        if not self._objective_matches(cmd.objective):
            return
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

            # Extract config details for the event
            exposure = getattr(config, "exposure_time", None) or getattr(config, "exposure_ms", None)
            gain = getattr(config, "analog_gain", None)
            intensity = getattr(config, "illumination_intensity", None) or getattr(config, "intensity", None)

        # Publish outside lock
        self._bus.publish(MicroscopeModeChanged(
            configuration_name=mode,
            exposure_time_ms=exposure,
            analog_gain=gain,
            illumination_intensity=intensity,
        ))

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
