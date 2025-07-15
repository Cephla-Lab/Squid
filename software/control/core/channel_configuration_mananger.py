from enum import Enum
from typing import Any, List, Dict

from control.utils_config import ChannelConfig, ChannelMode
import control.utils_config as utils_config
from control._def import *
import squid.logging


class ConfigType(Enum):
    CHANNEL = "channel"
    CONFOCAL = "confocal"
    WIDEFIELD = "widefield"


class ChannelConfigurationManager:
    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_root = None
        self.all_configs: Dict[ConfigType, Dict[str, ChannelConfig]] = {
            ConfigType.CHANNEL: {},
            ConfigType.CONFOCAL: {},
            ConfigType.WIDEFIELD: {},
        }
        self.active_config_type = ConfigType.CHANNEL if not ENABLE_SPINNING_DISK_CONFOCAL else ConfigType.CONFOCAL

    def set_profile_path(self, profile_path: Path) -> None:
        """Set the root path for configurations"""
        self.config_root = profile_path

    def _load_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Load XML configuration for a specific config type, generating default if needed"""
        config_file = self.config_root / objective / f"{config_type.value}_configurations.xml"

        if not config_file.exists():
            utils_config.generate_default_configuration(str(config_file))

        xml_content = config_file.read_bytes()
        self.all_configs[config_type][objective] = ChannelConfig.from_xml(xml_content)

    def load_configurations(self, objective: str) -> None:
        """Load available configurations for an objective"""
        if ENABLE_SPINNING_DISK_CONFOCAL:
            # Load both confocal and widefield configurations
            self._load_xml_config(objective, ConfigType.CONFOCAL)
            self._load_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            # Load only channel configurations
            self._load_xml_config(objective, ConfigType.CHANNEL)

    def _save_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Save XML configuration for a specific config type"""
        if objective not in self.all_configs[config_type]:
            return

        config = self.all_configs[config_type][objective]
        save_path = self.config_root / objective / f"{config_type.value}_configurations.xml"

        if not save_path.parent.exists():
            save_path.parent.mkdir(parents=True)

        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        save_path.write_bytes(xml_str)

    def save_configurations(self, objective: str) -> None:
        """Save configurations based on spinning disk configuration"""
        if ENABLE_SPINNING_DISK_CONFOCAL:
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

    def update_configuration(self, objective: str, config_id: str, attr_name: str, value: Any) -> None:
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

    def get_channel_configurations_for_objective(self, objective: str) -> List[ChannelMode]:
        """Get Configuration objects for current active type (alias for get_configurations)"""
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(self, objective: str, name: str) -> ChannelMode:
        """Get Configuration object by name"""
        return next((mode for mode in self.get_configurations(objective) if mode.name == name), None)

    def toggle_confocal_widefield(self, confocal: bool) -> None:
        """Toggle between confocal and widefield configurations"""
        self.active_config_type = ConfigType.CONFOCAL if confocal else ConfigType.WIDEFIELD
