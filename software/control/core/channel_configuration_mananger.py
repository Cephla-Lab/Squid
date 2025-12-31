from enum import Enum
from pathlib import Path
from typing import Any, List, Dict, Optional, Union
import hashlib
import json
import shutil

from control.utils_config import (
    ChannelConfig,
    ChannelMode,
    ChannelDefinitionsConfig,
    ChannelDefinition,
    ObjectiveChannelSettings,
    ConfocalOverrides,
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

        # Confocal mode flag - when True, use confocal overrides from ObjectiveChannelSettings
        # Default to False (widefield). For systems with spinning disk confocal, the actual state
        # is synced from hardware via sync_confocal_mode_from_hardware() after GUI initialization.
        self.confocal_mode: bool = False

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
        """Load global channel definitions from JSON file

        Uses a default + user file pattern:
        - channel_definitions.default.json: tracked in git, provides defaults
        - channel_definitions.json: gitignored, user edits go here
        """
        if not self.configurations_path:
            return

        user_file = self.configurations_path / "channel_definitions.json"
        default_file = self.configurations_path / "channel_definitions.default.json"

        if not user_file.exists():
            # Copy from default if available, otherwise generate
            if default_file.exists():
                shutil.copy(default_file, user_file)
                self._log.info(f"Copied default channel definitions to {user_file}")
            else:
                # Generate default and save
                self.channel_definitions = ChannelDefinitionsConfig.generate_default()
                self.channel_definitions.save(user_file)
                self._log.info(f"Generated default channel definitions at {user_file}")
                return

        self.channel_definitions = ChannelDefinitionsConfig.load(user_file)
        self._log.info(f"Loaded channel definitions from {user_file}")

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

            # Initialize objective settings dict if needed
            if objective not in self.objective_settings:
                self.objective_settings[objective] = {}

            for mode in legacy_config.modes:
                self.objective_settings[objective][mode.name] = ObjectiveChannelSettings(
                    exposure_time=mode.exposure_time,
                    analog_gain=mode.analog_gain,
                    illumination_intensity=mode.illumination_intensity,
                    z_offset=mode.z_offset,
                )

            self._save_objective_settings(objective)

    def migrate_all_profiles(self, base_config_path: Path) -> None:
        """Migrate all profiles and objectives from XML to JSON at once.

        Should be called once at app startup to ensure all existing
        XML configs are migrated to the new JSON format. Uses a marker
        file to skip scanning on subsequent runs.
        """
        if not base_config_path.exists():
            return

        # Check for migration complete marker
        marker_file = base_config_path / ".migration_complete"
        if marker_file.exists():
            return

        migrated_any = False
        for profile_dir in base_config_path.iterdir():
            if not profile_dir.is_dir():
                continue

            for objective_dir in profile_dir.iterdir():
                if not objective_dir.is_dir():
                    continue

                objective = objective_dir.name
                json_file = objective_dir / "channel_settings.json"
                xml_file = objective_dir / "channel_configurations.xml"

                # Only migrate if JSON doesn't exist but XML does
                if not json_file.exists() and xml_file.exists():
                    old_root = self.config_root
                    try:
                        self._log.info(f"Migrating {profile_dir.name}/{objective}")
                        self.config_root = profile_dir
                        self._migrate_from_xml_if_needed(objective)
                        migrated_any = True
                    except Exception as e:
                        self._log.warning(f"Failed to migrate {profile_dir.name}/{objective}: {type(e).__name__}: {e}")
                    finally:
                        self.config_root = old_root

        # Create marker file to skip scanning on future runs
        try:
            marker_file.touch()
            if migrated_any:
                self._log.info("Migration complete, marker file created")
        except Exception as e:
            self._log.warning(f"Failed to create migration marker: {e}")

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

    def save_configurations(self, objective: str) -> None:
        """Save per-objective channel settings to JSON.

        Note: XML is no longer written here. XML is only written at acquisition
        start via write_configuration_selected() to the experiment folder.
        """
        self._save_objective_settings(objective)

    def save_current_configuration_to_path(self, objective: str, path: Path) -> None:
        """Only used in TrackingController. Might be temporary."""
        config = self.all_configs[self.active_config_type][objective]
        xml_str = config.to_xml(pretty_print=True, encoding="utf-8")
        path.write_bytes(xml_str)

    def _build_channel_mode(self, channel_def: ChannelDefinition, objective: str) -> ChannelMode:
        """Build a ChannelMode from channel definition and objective settings.

        Uses effective settings based on current confocal_mode - if confocal mode is active
        and the channel has confocal overrides, those values are used instead of base settings.

        Note: Settings are lazily initialized with defaults when not found. These in-memory
        defaults are NOT persisted to disk until the user explicitly changes a value.
        This avoids creating files for channels the user hasn't configured yet.
        """
        base_settings = self.objective_settings.get(objective, {}).get(channel_def.name, ObjectiveChannelSettings())

        # Get effective settings based on current mode (applies confocal overrides if applicable)
        settings = base_settings.get_effective_settings(self.confocal_mode)

        # Get illumination source from channel definition
        if self.channel_definitions:
            illumination_source = channel_def.get_illumination_source(self.channel_definitions.numeric_channel_mapping)
        else:
            illumination_source = channel_def.illumination_source or 0

        # Generate ID from channel name using first 16 chars of SHA-256 for readability.
        #
        # KNOWN LIMITATION: ID changes if channel is renamed, which breaks references in saved
        # acquisition configurations. A future enhancement could add a stable UUID to
        # ChannelDefinition that persists across renames. For now, users should prefer
        # disabling unused channels rather than renaming (documented in channel_configuration.md).
        # TODO(future): Consider adding stable UUID field to ChannelDefinition for rename-safe IDs.
        channel_id = hashlib.sha256(channel_def.name.encode()).hexdigest()[:16]

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
                self.channel_definitions.get_enabled_channels() if enabled_only else self.channel_definitions.channels
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
        """Update a specific configuration in current active type.

        When in confocal mode, updates are stored in confocal overrides.
        When in widefield mode (or confocal disabled), updates go to base settings.
        """
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
                settings = self.objective_settings[objective][channel_name]
                pydantic_attr = attr_mapping[attr_name]

                if self.confocal_mode:
                    # In confocal mode, store in confocal overrides
                    if settings.confocal is None:
                        settings.confocal = ConfocalOverrides()
                    setattr(settings.confocal, pydantic_attr, value)
                else:
                    # In widefield mode, store in base settings
                    setattr(settings, pydantic_attr, value)
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
                ch_id = hashlib.sha256(ch.name.encode()).hexdigest()[:16]
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
        """Backward-compatible alias for :meth:`get_configurations`.

        This method exists to support legacy code that expects the name
        `get_channel_configurations_for_objective`. New code should call
        :meth:`get_configurations` directly. The behavior is identical.

        Args:
            objective: The objective name (e.g., "10x", "20x")

        Returns:
            List of ChannelMode objects for the objective
        """
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(self, objective: str, name: str) -> Optional[ChannelMode]:
        """Get a channel configuration by its name.

        Args:
            objective: The objective name (e.g., "10x", "20x")
            name: The channel name to look up (e.g., "Fluorescence 488 nm Ex")

        Returns:
            The ChannelMode if found, or None if no channel with that name exists.
            Callers should handle the None case appropriately.
        """
        return next((mode for mode in self.get_configurations(objective) if mode.name == name), None)

    def toggle_confocal_widefield(self, confocal: Union[bool, int]) -> None:
        """Toggle between confocal and widefield configurations.

        This sets both:
        - confocal_mode: Used by new JSON format to apply confocal overrides
        - active_config_type: Used by legacy XML format for backward compatibility

        Args:
            confocal: Whether to enable confocal mode. Accepts bool or int (0=widefield, 1=confocal)
                      for compatibility with hardware APIs that return int.
        """
        # Convert to bool for type safety (XLight returns int 0/1, Dragonfly returns bool)
        self.confocal_mode = bool(confocal)
        self.active_config_type = ConfigType.CONFOCAL if self.confocal_mode else ConfigType.WIDEFIELD
        self._log.info(f"Imaging mode set to: {'confocal' if self.confocal_mode else 'widefield'}")

    def is_confocal_mode(self) -> bool:
        """Check if currently in confocal mode."""
        return self.confocal_mode

    def sync_confocal_mode_from_hardware(self, confocal: Union[bool, int]) -> None:
        """Sync confocal mode state from hardware.

        Call this after signal connections are established to ensure
        the manager state matches the actual hardware state.

        Args:
            confocal: Current hardware state. Accepts bool or int (0=widefield, 1=confocal).
        """
        self.toggle_confocal_widefield(confocal)

    def get_channel_definitions(self) -> Optional[ChannelDefinitionsConfig]:
        """Get the global channel definitions"""
        return self.channel_definitions

    def update_channel_definition(self, channel_name: str, **kwargs) -> None:
        """Update a channel definition"""
        if not self.channel_definitions:
            self._log.warning("update_channel_definition called but channel_definitions not initialized")
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
            self._log.warning("add_channel_definition called but channel_definitions not initialized")
            return

        self.channel_definitions.channels.append(channel)
        self.save_channel_definitions()

    def remove_channel_definition(self, channel_name: str, base_config_path: Path = None) -> List[str]:
        """Remove a channel definition and clean up orphaned settings.

        Args:
            channel_name: Name of the channel to remove
            base_config_path: Path to acquisition_configurations folder for cleanup.
                              If None, only removes from definitions without cleanup.

        Returns:
            List of error messages from cleanup. Empty if no cleanup was performed
            or all cleanups succeeded. Errors are also logged individually.
        """
        if not self.channel_definitions:
            self._log.warning("remove_channel_definition called but channel_definitions not initialized")
            return []

        self.channel_definitions.channels = [ch for ch in self.channel_definitions.channels if ch.name != channel_name]
        self.save_channel_definitions()

        # Clean up orphaned settings from all profile/objective channel_settings.json files
        if base_config_path and base_config_path.exists():
            return self._cleanup_orphaned_settings(base_config_path, channel_name)
        return []

    def _cleanup_orphaned_settings(self, base_config_path: Path, channel_name: str) -> List[str]:
        """Remove orphaned channel settings from all profiles and objectives.

        Uses best-effort strategy: continues processing remaining files even if some fail.
        All errors are logged individually and collected for the caller.

        Args:
            base_config_path: Path to acquisition_configurations folder
            channel_name: Name of the channel to remove

        Returns:
            List of error messages for any files that failed to clean up.
            Empty list if all cleanups succeeded. Callers can check the list
            to decide whether to warn the user about partial cleanup.
        """
        errors = []
        cleaned_count = 0

        for profile_dir in base_config_path.iterdir():
            if not profile_dir.is_dir():
                continue

            for objective_dir in profile_dir.iterdir():
                if not objective_dir.is_dir():
                    continue

                settings_file = objective_dir / "channel_settings.json"
                if not settings_file.exists():
                    continue

                try:
                    with open(settings_file, "r") as f:
                        data = json.load(f)

                    if channel_name in data:
                        del data[channel_name]
                        with open(settings_file, "w") as f:
                            json.dump(data, f, indent=2)
                        cleaned_count += 1
                except json.JSONDecodeError as e:
                    error_msg = f"{settings_file}: Invalid JSON - {e}"
                    errors.append(error_msg)
                    self._log.error(f"Failed to parse JSON in {settings_file}: {e}")
                except PermissionError as e:
                    error_msg = f"{settings_file}: Permission denied"
                    errors.append(error_msg)
                    self._log.error(f"Permission denied accessing {settings_file}: {e}")
                except Exception as e:
                    error_msg = f"{settings_file}: {type(e).__name__}: {e}"
                    errors.append(error_msg)
                    # Use exception() to capture full stack trace for debugging unexpected errors
                    self._log.exception(f"Unexpected error cleaning up {settings_file}")

        if cleaned_count > 0:
            self._log.info(f"Cleaned up orphaned settings for '{channel_name}' from {cleaned_count} file(s)")

        if errors:
            self._log.warning(f"Cleanup completed with {len(errors)} error(s) for channel '{channel_name}'")

        return errors

    def set_channel_enabled(self, channel_name: str, enabled: bool) -> None:
        """Enable or disable a channel"""
        self.update_channel_definition(channel_name, enabled=enabled)
