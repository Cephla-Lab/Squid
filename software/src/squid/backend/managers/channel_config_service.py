"""ChannelConfigService — EventBus wrapper around ConfigRepository.

Replaces ChannelConfigurationManager with a service that delegates to
the upstream ConfigRepository (YAML-based) while maintaining the same
public API for consumers.

Key differences from ChannelConfigurationManager:
- Stores configs as YAML via ConfigRepository instead of JSON/XML
- Uses AcquisitionChannel (Pydantic) instead of ChannelMode (pydantic-xml)
- No legacy XML support for runtime storage (YAML acquisition output only)
- Resolves illumination_source on each channel from IlluminationChannelConfig
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

from squid.backend.managers.base import BaseManager
from squid.core.config.models.acquisition_config import AcquisitionChannel
from squid.core.config.repository import ConfigRepository
from squid.core.events import (
    ChannelConfigurationsChanged,
    ConfocalModeChanged,
    SetConfocalModeCommand,
    UpdateChannelConfigurationCommand,
    handles,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus


class ChannelConfigService(BaseManager):
    """EventBus-connected channel configuration service.

    Wraps ConfigRepository to provide the same public API as
    ChannelConfigurationManager, but backed by YAML configs.

    Attributes:
        config_repo: The underlying ConfigRepository for YAML I/O
        confocal_mode: Whether confocal overrides are active
    """

    config_repo: ConfigRepository
    confocal_mode: bool
    _current_objective: Optional[str]

    def __init__(
        self,
        config_repo: ConfigRepository,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        super().__init__(event_bus)
        self.config_repo = config_repo
        self.confocal_mode = False
        self._current_objective = None
        self._illumination_config = config_repo.get_illumination_config()

    # ── Public API ──────────────────────────────────────────────────────

    def get_configurations(
        self, objective: str, enabled_only: bool = False
    ) -> List[AcquisitionChannel]:
        """Get channel configurations for an objective.

        Returns merged channels (general + objective overrides) with
        confocal overrides applied if confocal_mode is active.
        """
        merged = self.config_repo.get_merged_channels(objective)
        if merged is None:
            return []

        if self.confocal_mode:
            result = []
            for ch in merged:
                effective = ch.get_effective_settings(confocal_mode=True)
                result.append(effective)
            merged = result

        if enabled_only:
            merged = [ch for ch in merged if ch.enabled]

        # Inject illumination_source for backward compat
        self._resolve_illumination_sources(merged)
        return merged

    def get_enabled_configurations(self, objective: str) -> List[AcquisitionChannel]:
        """Get only enabled channel configurations."""
        return self.get_configurations(objective, enabled_only=True)

    def get_channel_configuration_by_name(
        self, objective: str, name: str
    ) -> Optional[AcquisitionChannel]:
        """Get a single channel configuration by name."""
        for ch in self.get_configurations(objective):
            if ch.name == name:
                return ch
        return None

    def update_configuration(
        self, objective: str, channel_name: str, attr_name: str, value: Any
    ) -> None:
        """Update a channel setting for an objective.

        Args:
            objective: Objective name (e.g. "10x")
            channel_name: Channel name (e.g. "DAPI")
            attr_name: Setting name — one of "ExposureTime", "AnalogGain",
                       "IlluminationIntensity", "ZOffset"
            value: New value
        """
        success = self.config_repo.update_channel_setting(
            objective, channel_name, attr_name, value
        )
        if not success:
            self._log.warning(
                f"Failed to update {attr_name}={value} for "
                f"channel '{channel_name}' objective '{objective}'"
            )

    def toggle_confocal_widefield(self, confocal: Union[bool, int]) -> None:
        """Toggle between confocal and widefield mode.

        Args:
            confocal: Whether to enable confocal mode. Accepts bool or int
                      (0=widefield, 1=confocal) for hardware API compat.
        """
        new_mode = bool(confocal)
        self.confocal_mode = new_mode
        self._log.info(
            f"Imaging mode set to: {'confocal' if new_mode else 'widefield'}"
        )

    def is_confocal_mode(self) -> bool:
        """Check if currently in confocal mode."""
        return self.confocal_mode

    def sync_confocal_mode_from_hardware(self, confocal: Union[bool, int]) -> None:
        """Sync confocal mode state from hardware after connections established."""
        self.toggle_confocal_widefield(confocal)

    def apply_channel_overrides(
        self,
        objective: str,
        overrides: List[Dict[str, Any]],
    ) -> None:
        """Apply protocol-defined channel overrides before imaging.

        Args:
            objective: Objective name
            overrides: List of dicts with channel override info. Each dict:
                - name: str (required)
                - exposure_time_ms: Optional[float]
                - analog_gain: Optional[float]
                - illumination_intensity: Optional[float]
                - z_offset_um: Optional[float]
        """
        for override in overrides:
            channel_name = override.get("name")
            if not channel_name:
                self._log.warning("Channel override missing 'name', skipping")
                continue

            if override.get("exposure_time_ms") is not None:
                self.update_configuration(
                    objective, channel_name, "ExposureTime", override["exposure_time_ms"]
                )
            if override.get("analog_gain") is not None:
                self.update_configuration(
                    objective, channel_name, "AnalogGain", override["analog_gain"]
                )
            if override.get("illumination_intensity") is not None:
                self.update_configuration(
                    objective,
                    channel_name,
                    "IlluminationIntensity",
                    override["illumination_intensity"],
                )
            if override.get("z_offset_um") is not None:
                self.update_configuration(
                    objective, channel_name, "ZOffset", override["z_offset_um"]
                )

            self._log.debug(
                f"Applied overrides to channel '{channel_name}' for '{objective}'"
            )

    def save_acquisition_output(
        self,
        output_dir,
        objective: str,
        channels: List[AcquisitionChannel],
    ) -> None:
        """Save acquisition channel config to experiment output directory (YAML)."""
        self.config_repo.save_acquisition_output(output_dir, objective, channels)

    # ── Event Handlers ──────────────────────────────────────────────────

    @handles(UpdateChannelConfigurationCommand)
    def _on_update_configuration_command(
        self, cmd: UpdateChannelConfigurationCommand
    ) -> None:
        """Handle UpdateChannelConfigurationCommand from UI widgets."""
        if cmd.exposure_time_ms is not None:
            self.update_configuration(
                cmd.objective_name, cmd.config_name, "ExposureTime", cmd.exposure_time_ms
            )
        if cmd.analog_gain is not None:
            self.update_configuration(
                cmd.objective_name, cmd.config_name, "AnalogGain", cmd.analog_gain
            )
        if cmd.illumination_intensity is not None:
            self.update_configuration(
                cmd.objective_name,
                cmd.config_name,
                "IlluminationIntensity",
                cmd.illumination_intensity,
            )

        self._publish_configurations_changed(cmd.objective_name)

    @handles(SetConfocalModeCommand)
    def _on_set_confocal_mode_command(self, cmd: SetConfocalModeCommand) -> None:
        """Handle confocal/widefield mode toggle."""
        self.toggle_confocal_widefield(cmd.is_confocal)
        self._publish_configurations_changed(cmd.objective_name)
        if self._event_bus is not None:
            self._event_bus.publish(ConfocalModeChanged(is_confocal=cmd.is_confocal))

    # ── Internal ────────────────────────────────────────────────────────

    def _publish_configurations_changed(self, objective: str) -> None:
        """Publish ChannelConfigurationsChanged event."""
        if not self._event_bus:
            return
        configs = self.get_configurations(objective)
        config_names = [ch.name for ch in configs]
        self._event_bus.publish(
            ChannelConfigurationsChanged(
                objective_name=objective,
                configuration_names=config_names,
            )
        )

    def _resolve_illumination_sources(
        self, channels: List[AcquisitionChannel]
    ) -> None:
        """Inject illumination_source integer on each channel.

        The upstream AcquisitionChannel model uses extra='forbid', so we
        inject via __dict__ for backward compatibility with code that reads
        channel.illumination_source (an integer hardware code).
        """
        if not self._illumination_config:
            return

        for ch in channels:
            ill_name = ch.illumination_settings.illumination_channel
            if ill_name is None:
                continue
            ill_ch = self._illumination_config.get_channel_by_name(ill_name)
            if ill_ch is not None:
                source_code = self._illumination_config.get_source_code(ill_ch)
                ch.__dict__["illumination_source"] = source_code
