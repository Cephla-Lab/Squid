from enum import Enum
from pathlib import Path
from typing import Any, List, Dict, Optional
import hashlib
import json

from control.utils_config import (
    ChannelConfig,
    ChannelMode,
    ChannelDefinitionsConfig,
    ChannelDefinition,
    ChannelType,
    ObjectiveChannelSettings,
)
import control.utils_config as utils_config
import control._def
import squid.logging


class ConfigType(Enum):
    CHANNEL = "channel"
    CONFOCAL = "confocal"
    WIDEFIELD = "widefield"


class ChannelConfigurationManager:
    def __init__(self, configurations_path: Optional[Path] = None):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_root = None
        self.configurations_path = configurations_path  # Path to configurations folder (for channel_definitions.json)

        # New format: global channel definitions
        self.channel_definitions: Optional[ChannelDefinitionsConfig] = None

        # Per-objective settings: {objective: {channel_name: ObjectiveChannelSettings}}
        self.objective_settings: Dict[str, Dict[str, ObjectiveChannelSettings]] = {}

        # Legacy format support (kept for backward compatibility)
        self.all_configs: Dict[ConfigType, Dict[str, ChannelConfig]] = {
            ConfigType.CHANNEL: {},
            ConfigType.CONFOCAL: {},
            ConfigType.WIDEFIELD: {},
        }
        self.active_config_type = (
            ConfigType.CHANNEL if not control._def.ENABLE_SPINNING_DISK_CONFOCAL else ConfigType.CONFOCAL
        )

        # Load global channel definitions if configurations_path is provided
        if configurations_path:
            self._load_channel_definitions()

    def set_configurations_path(self, configurations_path: Path) -> None:
        """Set the path to the configurations folder"""
        self.configurations_path = configurations_path
        self._load_channel_definitions()

    def _load_channel_definitions(self) -> None:
        """Load global channel definitions from JSON file"""
        if not self.configurations_path:
            return

        definitions_file = self.configurations_path / "channel_definitions.json"

        if definitions_file.exists():
            self.channel_definitions = ChannelDefinitionsConfig.load(definitions_file)
            self._log.info(f"Loaded channel definitions from {definitions_file}")
        else:
            # Generate default and save
            self.channel_definitions = ChannelDefinitionsConfig.generate_default()
            self.channel_definitions.save(definitions_file)
            self._log.info(f"Generated default channel definitions at {definitions_file}")

    def save_channel_definitions(self) -> None:
        """Save global channel definitions to JSON file"""
        if not self.configurations_path or not self.channel_definitions:
            return

        definitions_file = self.configurations_path / "channel_definitions.json"
        self.channel_definitions.save(definitions_file)
        self._log.info(f"Saved channel definitions to {definitions_file}")

    def set_profile_path(self, profile_path: Path) -> None:
        """Set the root path for configurations"""
        self.config_root = profile_path

    def _get_objective_settings_path(self, objective: str) -> Path:
        """Get path to per-objective settings file"""
        return self.config_root / objective / "channel_settings.json"

    def _load_objective_settings(self, objective: str) -> None:
        """Load per-objective channel settings from JSON file"""
        settings_path = self._get_objective_settings_path(objective)

        if settings_path.exists():
            with open(settings_path, "r") as f:
                data = json.load(f)
            self.objective_settings[objective] = {
                name: ObjectiveChannelSettings(**settings) for name, settings in data.items()
            }
        else:
            # Initialize with defaults or migrate from existing XML
            self.objective_settings[objective] = {}
            self._migrate_from_xml_if_needed(objective)

    def _save_objective_settings(self, objective: str) -> None:
        """Save per-objective channel settings to JSON file"""
        settings_path = self._get_objective_settings_path(objective)

        if not settings_path.parent.exists():
            settings_path.parent.mkdir(parents=True)

        settings = self.objective_settings.get(objective, {})
        data = {name: s.model_dump() for name, s in settings.items()}

        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)

    def _migrate_from_xml_if_needed(self, objective: str) -> None:
        """Migrate settings from existing XML file if it exists"""
        xml_file = self.config_root / objective / "channel_configurations.xml"

        if xml_file.exists():
            self._log.info(f"Migrating settings from {xml_file}")
            xml_content = xml_file.read_bytes()
            legacy_config = ChannelConfig.from_xml(xml_content)

            for mode in legacy_config.modes:
                self.objective_settings[objective][mode.name] = ObjectiveChannelSettings(
                    exposure_time=mode.exposure_time,
                    analog_gain=mode.analog_gain,
                    illumination_intensity=mode.illumination_intensity,
                    z_offset=mode.z_offset,
                )

            self._save_objective_settings(objective)

    def _load_xml_config(self, objective: str, config_type: ConfigType) -> None:
        """Load XML configuration for a specific config type, generating default if needed"""
        config_file = self.config_root / objective / f"{config_type.value}_configurations.xml"

        if not config_file.exists():
            utils_config.generate_default_configuration(str(config_file))

        xml_content = config_file.read_bytes()
        self.all_configs[config_type][objective] = ChannelConfig.from_xml(xml_content)

    def load_configurations(self, objective: str) -> None:
        """Load available configurations for an objective"""
        # Load per-objective settings (new format)
        self._load_objective_settings(objective)

        # Also load legacy XML for backward compatibility
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL:
            self._load_xml_config(objective, ConfigType.CONFOCAL)
            self._load_xml_config(objective, ConfigType.WIDEFIELD)
        else:
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
        # Save per-objective settings (new format)
        self._save_objective_settings(objective)

        # Also save legacy XML for backward compatibility
        if control._def.ENABLE_SPINNING_DISK_CONFOCAL:
            self._save_xml_config(objective, ConfigType.CONFOCAL)
            self._save_xml_config(objective, ConfigType.WIDEFIELD)
        else:
            self._save_xml_config(objective, ConfigType.CHANNEL)

    def save_current_configuration_to_path(self, objective: str, path: Path) -> None:
        """Only used in TrackingController. Might be temporary."""
        config = self.all_configs[self.active_config_type][objective]
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        path.write_bytes(xml_str)

    def _build_channel_mode(self, channel_def: ChannelDefinition, objective: str) -> ChannelMode:
        """Build a ChannelMode from channel definition and objective settings"""
        settings = self.objective_settings.get(objective, {}).get(
            channel_def.name, ObjectiveChannelSettings()
        )

        # Get illumination source from channel definition
        if self.channel_definitions:
            illumination_source = channel_def.get_illumination_source(
                self.channel_definitions.numeric_channel_mapping
            )
        else:
            illumination_source = channel_def.illumination_source or 0

        # Generate a stable ID based on channel name (using MD5 for cross-session stability)
        channel_id = str(int(hashlib.md5(channel_def.name.encode()).hexdigest()[:8], 16) % 100000)

        return ChannelMode(
            id=channel_id,
            name=channel_def.name,
            exposure_time=settings.exposure_time,
            analog_gain=settings.analog_gain,
            illumination_source=illumination_source,
            illumination_intensity=settings.illumination_intensity,
            camera_sn="",
            z_offset=settings.z_offset,
            emission_filter_position=channel_def.emission_filter_position,
            selected=False,
        )

    def get_configurations(self, objective: str, enabled_only: bool = False) -> List[ChannelMode]:
        """Get channel modes for current active type"""
        # If using new format and channel definitions are loaded
        if self.channel_definitions:
            channels = (
                self.channel_definitions.get_enabled_channels()
                if enabled_only
                else self.channel_definitions.channels
            )
            return [self._build_channel_mode(ch, objective) for ch in channels]

        # Fall back to legacy format
        config = self.all_configs[self.active_config_type].get(objective)
        if not config:
            return []
        return config.modes

    def get_enabled_configurations(self, objective: str) -> List[ChannelMode]:
        """Get only enabled channel modes"""
        return self.get_configurations(objective, enabled_only=True)

    def update_configuration(self, objective: str, config_id: str, attr_name: str, value: Any) -> None:
        """Update a specific configuration in current active type"""
        # Update in per-objective settings (new format)
        channel_name = self._get_channel_name_by_id(objective, config_id)
        if channel_name:
            if objective not in self.objective_settings:
                self.objective_settings[objective] = {}
            if channel_name not in self.objective_settings[objective]:
                self.objective_settings[objective][channel_name] = ObjectiveChannelSettings()

            attr_mapping = {
                "ExposureTime": "exposure_time",
                "AnalogGain": "analog_gain",
                "IlluminationIntensity": "illumination_intensity",
                "ZOffset": "z_offset",
            }
            if attr_name in attr_mapping:
                setattr(self.objective_settings[objective][channel_name], attr_mapping[attr_name], value)
            else:
                self._log.warning(f"Unknown attribute '{attr_name}' for channel '{channel_name}', ignoring")

        # Also update legacy format for backward compatibility
        config = self.all_configs[self.active_config_type].get(objective)
        if config:
            for mode in config.modes:
                if mode.id == config_id:
                    setattr(mode, utils_config.get_attr_name(attr_name), value)
                    break

        self.save_configurations(objective)

    def _get_channel_name_by_id(self, objective: str, config_id: str) -> Optional[str]:
        """Get channel name by its ID"""
        # First check if using new format
        if self.channel_definitions:
            for ch in self.channel_definitions.channels:
                ch_id = str(int(hashlib.md5(ch.name.encode()).hexdigest()[:8], 16) % 100000)
                if ch_id == config_id:
                    return ch.name

        # Fall back to legacy format
        config = self.all_configs[self.active_config_type].get(objective)
        if config:
            for mode in config.modes:
                if mode.id == config_id:
                    return mode.name
        return None

    def write_configuration_selected(
        self, objective: str, selected_configurations: List[ChannelMode], filename: str
    ) -> None:
        """Write selected configurations to a file (legacy XML format for acquisition)"""
        # Generate legacy XML format for backward compatibility with downstream processing
        modes = []
        for i, config in enumerate(selected_configurations):
            mode = ChannelMode(
                id=config.id,
                name=config.name,
                exposure_time=config.exposure_time,
                analog_gain=config.analog_gain,
                illumination_source=config.illumination_source,
                illumination_intensity=config.illumination_intensity,
                camera_sn=config.camera_sn or "",
                z_offset=config.z_offset,
                emission_filter_position=config.emission_filter_position,
                selected=True,
            )
            modes.append(mode)

        config = ChannelConfig(modes=modes)
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        filename = Path(filename)
        filename.write_bytes(xml_str)

    def get_channel_configurations_for_objective(self, objective: str) -> List[ChannelMode]:
        """Get Configuration objects for current active type (alias for get_configurations)"""
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(self, objective: str, name: str) -> Optional[ChannelMode]:
        """Get Configuration object by name"""
        return next((mode for mode in self.get_configurations(objective) if mode.name == name), None)

    def toggle_confocal_widefield(self, confocal: bool) -> None:
        """Toggle between confocal and widefield configurations"""
        self.active_config_type = ConfigType.CONFOCAL if confocal else ConfigType.WIDEFIELD

    def get_channel_definitions(self) -> Optional[ChannelDefinitionsConfig]:
        """Get the global channel definitions"""
        return self.channel_definitions

    def update_channel_definition(self, channel_name: str, **kwargs) -> None:
        """Update a channel definition"""
        if not self.channel_definitions:
            return

        for ch in self.channel_definitions.channels:
            if ch.name == channel_name:
                for key, value in kwargs.items():
                    if hasattr(ch, key):
                        setattr(ch, key, value)
                break

        self.save_channel_definitions()

    def add_channel_definition(self, channel: ChannelDefinition) -> None:
        """Add a new channel definition"""
        if not self.channel_definitions:
            return

        self.channel_definitions.channels.append(channel)
        self.save_channel_definitions()

    def remove_channel_definition(self, channel_name: str) -> None:
        """Remove a channel definition"""
        if not self.channel_definitions:
            return

        self.channel_definitions.channels = [
            ch for ch in self.channel_definitions.channels if ch.name != channel_name
        ]
        self.save_channel_definitions()

    def set_channel_enabled(self, channel_name: str, enabled: bool) -> None:
        """Enable or disable a channel"""
        self.update_channel_definition(channel_name, enabled=enabled)
