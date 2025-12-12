from enum import Enum
from pathlib import Path
from typing import Any, List, Dict, Optional, TYPE_CHECKING
import logging

from squid.core.utils.config_utils import ChannelConfig, ChannelMode
import squid.core.utils.config_utils as utils_config
import _def
import squid.core.logging
from squid.core.events import (
    UpdateChannelConfigurationCommand,
    ChannelConfigurationsChanged,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus


class ConfigType(Enum):
    CHANNEL = "channel"
    CONFOCAL = "confocal"
    WIDEFIELD = "widefield"


class ChannelConfigurationManager:
    _log: logging.Logger
    config_root: Optional[Path]
    all_configs: Dict[ConfigType, Dict[str, ChannelConfig]]
    active_config_type: ConfigType
    _event_bus: Optional["EventBus"]

    def __init__(self, event_bus: Optional["EventBus"] = None) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self.config_root = None
        self.all_configs: Dict[ConfigType, Dict[str, ChannelConfig]] = {
            ConfigType.CHANNEL: {},
            ConfigType.CONFOCAL: {},
            ConfigType.WIDEFIELD: {},
        }
        self.active_config_type = (
            ConfigType.CHANNEL
            if not _def.ENABLE_SPINNING_DISK_CONFOCAL
            else ConfigType.CONFOCAL
        )

        # Subscribe to events if event_bus is provided
        if self._event_bus:
            self._event_bus.subscribe(
                UpdateChannelConfigurationCommand,
                self._on_update_configuration_command
            )

    def set_profile_path(self, profile_path: Path) -> None:
        """Set the root path for configurations"""
        self.config_root = profile_path

    def _load_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Load XML configuration for a specific config type, generating default if needed"""
        if self.config_root is None:
            raise ValueError("config_root is not set. Call set_profile_path() first.")
        config_file = (
            self.config_root / objective / f"{config_type.value}_configurations.xml"
        )

        if not config_file.exists():
            utils_config.generate_default_configuration(str(config_file))

        xml_content = config_file.read_bytes()
        self.all_configs[config_type][objective] = ChannelConfig.from_xml(xml_content)

    def load_configurations(self, objective: str) -> None:
        """Load available configurations for an objective"""
        if _def.ENABLE_SPINNING_DISK_CONFOCAL:
            # Load both confocal and widefield configurations
            self._load_xml_config(objective, ConfigType.CONFOCAL)
            self._load_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            # Load only channel configurations
            self._load_xml_config(objective, ConfigType.CHANNEL)

        # Publish event with available configuration names
        self._publish_configurations_changed(objective)

    def _save_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Save XML configuration for a specific config type"""
        if objective not in self.all_configs[config_type]:
            return
        if self.config_root is None:
            raise ValueError("config_root is not set. Call set_profile_path() first.")

        config = self.all_configs[config_type][objective]
        save_path = (
            self.config_root / objective / f"{config_type.value}_configurations.xml"
        )

        if not save_path.parent.exists():
            save_path.parent.mkdir(parents=True)

        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        save_path.write_bytes(xml_str)

    def save_configurations(self, objective: str) -> None:
        """Save configurations based on spinning disk configuration"""
        if _def.ENABLE_SPINNING_DISK_CONFOCAL:
            # Save both confocal and widefield configurations
            self._save_xml_config(objective, ConfigType.CONFOCAL)
            self._save_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            # Save only channel configurations
            self._save_xml_config(objective, ConfigType.CHANNEL)

    def save_current_configuration_to_path(self, objective: str, path: Path) -> None:
        """Only used in TrackingController. Might be temporary."""
        config = self.all_configs[self.active_config_type][objective]
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        path.write_bytes(xml_str)

    def get_configurations(self, objective: str) -> List[ChannelMode]:
        """Get channel modes for current active type"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            return []
        return config.modes

    def update_configuration(
        self, objective: str, config_id: str, attr_name: str, value: Any
    ) -> None:
        """Update a specific configuration in current active type"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            self._log.error(f"Objective {objective} not found")
            return

        for mode in config.modes:
            if mode.id == config_id:
                setattr(mode, utils_config.get_attr_name(attr_name), value)
                break

        self.save_configurations(objective)

    def write_configuration_selected(
        self, objective: str, selected_configurations: List[ChannelMode], filename: str
    ) -> None:
        """Write selected configurations to a file"""
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            raise ValueError(f"Objective {objective} not found")

        # Update selected status
        for mode in config.modes:
            mode.selected = any(conf.id == mode.id for conf in selected_configurations)

        # Save to specified file
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        filename = Path(filename)
        filename.write_bytes(xml_str)

        # Reset selected status
        for mode in config.modes:
            mode.selected = False
        self.save_configurations(objective)

    def get_channel_configurations_for_objective(
        self, objective: str
    ) -> List[ChannelMode]:
        """Get Configuration objects for current active type (alias for get_configurations)"""
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(
        self, objective: str, name: str
    ) -> Optional[ChannelMode]:
        """Get Configuration object by name"""
        return next(
            (mode for mode in self.get_configurations(objective) if mode.name == name),
            None,
        )

    def toggle_confocal_widefield(self, confocal: bool) -> None:
        """Toggle between confocal and widefield configurations"""
        self.active_config_type = (
            ConfigType.CONFOCAL if confocal else ConfigType.WIDEFIELD
        )

    def _publish_configurations_changed(self, objective: str) -> None:
        """Publish ChannelConfigurationsChanged event."""
        if not self._event_bus:
            return
        configs = self.get_configurations(objective)
        config_names = [mode.name for mode in configs]
        self._event_bus.publish(ChannelConfigurationsChanged(
            objective_name=objective,
            configuration_names=config_names,
        ))

    def _on_update_configuration_command(
        self, cmd: UpdateChannelConfigurationCommand
    ) -> None:
        """Handle UpdateChannelConfigurationCommand event."""
        # Find the configuration by name
        mode = self.get_channel_configuration_by_name(cmd.objective_name, cmd.config_name)
        if not mode:
            self._log.error(
                f"Configuration '{cmd.config_name}' not found for objective '{cmd.objective_name}'"
            )
            return

        # Update the fields that are provided
        if cmd.exposure_time_ms is not None:
            self.update_configuration(
                cmd.objective_name, mode.id, "ExposureTime", cmd.exposure_time_ms
            )
        if cmd.analog_gain is not None:
            self.update_configuration(
                cmd.objective_name, mode.id, "AnalogGain", cmd.analog_gain
            )
        if cmd.illumination_intensity is not None:
            self.update_configuration(
                cmd.objective_name, mode.id, "IlluminationIntensity", cmd.illumination_intensity
            )
