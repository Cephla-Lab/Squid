"""
Channel configuration manager using YAML-based acquisition configs.

This module manages acquisition channel configurations using:
- illumination_channel_config.yaml: Hardware-level illumination channels
- general.yaml: Shared acquisition settings across all objectives
- {objective}.yaml: Objective-specific acquisition settings

The manager loads and merges these configs, providing AcquisitionChannel objects
for UI and acquisition use.
"""

from pathlib import Path
from typing import Any, List, Dict, Optional, Union

import yaml

import squid.logging
from control.core.config import ConfigRepository
from control.models import (
    AcquisitionChannel,
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    IlluminationChannelConfig,
    CameraSettings,
    IlluminationSettings,
    merge_channel_configs,
    validate_illumination_references,
)


class ChannelConfigurationManager:
    """Manages acquisition channel configurations using YAML format.

    This manager:
    - Loads illumination_channel_config.yaml for hardware channel definitions
    - Loads and merges general.yaml + {objective}.yaml for acquisition settings
    - Provides AcquisitionChannel objects for UI and acquisition use
    - Saves configuration updates back to YAML files
    """

    def __init__(self, configurations_path: Optional[Path] = None, initialize: bool = False):
        """Initialize the channel configuration manager.

        Args:
            configurations_path: Deprecated. Use initialize=True instead.
                Kept for backward compatibility - if provided, acts as initialize=True.
            initialize: If True, initialize the config loader and load illumination config.
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_root: Optional[Path] = None

        # Confocal mode flag - when True, use confocal_override from acquisition configs
        self.confocal_mode: bool = False

        # YAML-based acquisition configs
        self._general_config: Optional[GeneralChannelConfig] = None
        self._objective_configs: Dict[str, ObjectiveChannelConfig] = {}
        self._merged_channels: Dict[str, List[AcquisitionChannel]] = {}
        self._illumination_config: Optional[IlluminationChannelConfig] = None
        self._config_repo: Optional[ConfigRepository] = None
        self._current_profile: Optional[str] = None

        # Initialize config repo if requested
        if configurations_path or initialize:
            self._config_repo = ConfigRepository()
            self._illumination_config = self._config_repo.get_illumination_config()

    def set_configurations_path(self, configurations_path: Optional[Path] = None) -> None:
        """Initialize the config repo and load illumination config.

        Args:
            configurations_path: Deprecated, ignored. ConfigRepository auto-detects paths.
        """
        self._config_repo = ConfigRepository()
        self._illumination_config = self._config_repo.get_illumination_config()

    def set_profile_path(self, profile_path: Path) -> None:
        """Set the root path for configurations.

        Extracts profile name and loads general.yaml.
        """
        self.config_root = profile_path

        if not profile_path:
            return

        # Extract profile name from path (e.g., user_profiles/default -> default)
        if "user_profiles" in profile_path.parts:
            try:
                idx = profile_path.parts.index("user_profiles")
                if idx + 1 < len(profile_path.parts):
                    profile_name = profile_path.parts[idx + 1]
                    self.set_profile(profile_name)
            except (ValueError, IndexError):
                self._log.debug(f"Could not extract profile name from path: {profile_path}")

    def set_profile(self, profile: str) -> None:
        """Set current profile and load general.yaml.

        Args:
            profile: Profile name (e.g., "default", "user1")
        """
        self._current_profile = profile
        if not self._config_repo:
            self._config_repo = ConfigRepository()

        # Load illumination config if not already loaded
        if not self._illumination_config:
            self._illumination_config = self._config_repo.get_illumination_config()

        # Load general.yaml
        self._general_config = self._config_repo.get_general_config(profile)

        # Validate illumination references
        if self._general_config and self._illumination_config:
            errors = validate_illumination_references(self._general_config, self._illumination_config)
            for error in errors:
                self._log.warning(error)

        # Clear cached configs
        self._objective_configs.clear()
        self._merged_channels.clear()

    def load_configurations(self, objective: str) -> None:
        """Load acquisition configurations for an objective.

        Loads {objective}.yaml and merges with general.yaml.
        """
        if not self._config_repo or not self._current_profile:
            self._log.debug("No profile set, skipping config load")
            return

        if not self._general_config:
            self._log.debug("No general.yaml loaded, skipping config load")
            return

        # Load objective config
        obj_config = self._config_repo.get_objective_config(objective, self._current_profile)

        if obj_config:
            self._objective_configs[objective] = obj_config
            merged = merge_channel_configs(self._general_config, obj_config)
            self._merged_channels[objective] = merged
            self._log.debug(f"Loaded config for objective '{objective}'")
        else:
            # No objective config - use general config channels
            self._merged_channels[objective] = list(self._general_config.channels)
            self._log.debug(f"No config for '{objective}', using general.yaml")

    def save_configurations(self, objective: str) -> None:
        """Save acquisition configurations for an objective.

        Saves the current objective config to {objective}.yaml.
        """
        if not self._config_repo or not self._current_profile:
            self._log.warning("Cannot save: no profile set")
            return

        obj_config = self._objective_configs.get(objective)
        if obj_config:
            self._config_repo.save_objective_config(self._current_profile, objective, obj_config)
            self._log.debug(f"Saved config for objective '{objective}'")

    def get_merged_acquisition_channels(
        self, objective: str, confocal_mode: Optional[bool] = None
    ) -> List[AcquisitionChannel]:
        """Get merged acquisition channels for an objective.

        Args:
            objective: Objective name
            confocal_mode: Override confocal mode (uses self.confocal_mode if None)

        Returns:
            List of merged AcquisitionChannel objects
        """
        if objective not in self._merged_channels:
            self.load_configurations(objective)

        channels = self._merged_channels.get(objective, [])

        # Apply confocal mode if needed
        use_confocal = confocal_mode if confocal_mode is not None else self.confocal_mode
        if use_confocal:
            channels = [ch.get_effective_settings(confocal_mode=True) for ch in channels]

        return channels

    def get_configurations(self, objective: str) -> List[AcquisitionChannel]:
        """Get channel configurations for an objective.

        Args:
            objective: Objective name

        Returns:
            List of AcquisitionChannel objects
        """
        return self.get_merged_acquisition_channels(objective)

    def get_enabled_configurations(self, objective: str) -> List[AcquisitionChannel]:
        """Backward-compatible alias for get_configurations.

        Note: In the YAML-based system, all channels are available.
        Selection happens at acquisition time via selected_configurations.
        """
        return self.get_configurations(objective)

    def get_channel_configurations_for_objective(self, objective: str) -> List[AcquisitionChannel]:
        """Backward-compatible alias for get_configurations."""
        return self.get_configurations(objective)

    def get_channel_configuration_by_name(self, objective: str, name: str) -> Optional[AcquisitionChannel]:
        """Get a channel configuration by its name."""
        return next((ch for ch in self.get_configurations(objective) if ch.name == name), None)

    def update_configuration(self, objective: str, config_id: str, attr_name: str, value: Any) -> None:
        """Update a specific configuration attribute.

        Updates the YAML config and saves to disk.
        """
        channel_name = self._get_channel_name_by_id(objective, config_id)
        if not channel_name:
            self._log.warning(f"Channel not found for ID: {config_id}")
            return

        # Get or create objective config
        if objective not in self._objective_configs:
            if self._general_config:
                # Create objective config from general config
                # Note: objective configs only contain intensity, exposure, gain, pixel_format
                # z_offset_um is in general.yaml only, so we use 0.0 as placeholder
                self._objective_configs[objective] = ObjectiveChannelConfig(
                    version=1,
                    channels=[
                        AcquisitionChannel(
                            name=ch.name,
                            illumination_settings=IlluminationSettings(
                                illumination_channels=None,  # Not in objective files
                                intensity=dict(ch.illumination_settings.intensity),
                                z_offset_um=0.0,  # Placeholder, z_offset is in general.yaml
                            ),
                            camera_settings={
                                cam_id: CameraSettings(
                                    display_color=cam.display_color,
                                    exposure_time_ms=cam.exposure_time_ms,
                                    gain_mode=cam.gain_mode,
                                    pixel_format=cam.pixel_format,
                                )
                                for cam_id, cam in ch.camera_settings.items()
                            },
                        )
                        for ch in self._general_config.channels
                    ],
                )
            else:
                self._log.warning("No general config to create objective config from")
                return

        obj_config = self._objective_configs[objective]
        acq_channel = obj_config.get_channel_by_name(channel_name)
        if not acq_channel:
            self._log.warning(f"Channel '{channel_name}' not found in objective config")
            return

        # Map attribute names to config locations
        # Note: ZOffset is not here because z_offset is in general.yaml, not objective files
        attr_mapping = {
            "ExposureTime": ("camera", "exposure_time_ms"),
            "AnalogGain": ("camera", "gain_mode"),
            "IlluminationIntensity": ("illumination", "intensity"),
        }

        if attr_name not in attr_mapping:
            self._log.warning(f"Unknown attribute: {attr_name}")
            return

        location, field = attr_mapping[attr_name]

        if location == "camera":
            # Update camera settings (use first camera)
            for cam_settings in acq_channel.camera_settings.values():
                setattr(cam_settings, field, value)
                break
        elif location == "illumination":
            # Only intensity is editable per-objective (z_offset is in general.yaml)
            for key in acq_channel.illumination_settings.intensity:
                acq_channel.illumination_settings.intensity[key] = value

        # Re-merge configs
        if self._general_config:
            self._merged_channels[objective] = merge_channel_configs(self._general_config, obj_config)

        # Save to disk
        self.save_configurations(objective)

    def _get_channel_name_by_id(self, objective: str, config_id: str) -> Optional[str]:
        """Get channel name by its ID."""
        acq_channels = self.get_merged_acquisition_channels(objective)
        for ch in acq_channels:
            if ch.id == config_id:
                return ch.name
        return None

    def write_configuration_selected(
        self, objective: str, selected_configurations: List[AcquisitionChannel], filename: str
    ) -> None:
        """Write selected configurations to YAML file for acquisition.

        Saves acquisition channel settings as YAML in the acquisition output directory.
        The filename parameter determines the output directory (filename itself is ignored,
        YAML is saved as acquisition_channels.yaml).
        """
        output_dir = Path(filename).parent

        # Use provided configurations directly (they are already AcquisitionChannel objects)
        selected_acq_channels = selected_configurations

        if not selected_acq_channels:
            return

        # Build YAML output
        yaml_data = {
            "version": 1,
            "objective": objective,
            "confocal_mode": self.confocal_mode,
            "channels": [],
        }

        for acq_ch in selected_acq_channels:
            channel_data = {
                "name": acq_ch.name,
                "illumination_settings": {
                    "illumination_channels": acq_ch.illumination_settings.illumination_channels,
                    "intensity": acq_ch.illumination_settings.intensity,
                    "z_offset_um": acq_ch.illumination_settings.z_offset_um,
                },
                "camera_settings": {
                    cam_id: {
                        "display_color": cam.display_color,
                        "exposure_time_ms": cam.exposure_time_ms,
                        "gain_mode": cam.gain_mode,
                        "pixel_format": cam.pixel_format,
                    }
                    for cam_id, cam in acq_ch.camera_settings.items()
                },
            }

            if acq_ch.emission_filter_wheel_position:
                channel_data["emission_filter_wheel_position"] = acq_ch.emission_filter_wheel_position

            if acq_ch.confocal_settings:
                channel_data["confocal_settings"] = {
                    "filter_wheel_id": acq_ch.confocal_settings.filter_wheel_id,
                    "emission_filter_wheel_position": acq_ch.confocal_settings.emission_filter_wheel_position,
                    "illumination_iris": acq_ch.confocal_settings.illumination_iris,
                    "emission_iris": acq_ch.confocal_settings.emission_iris,
                }

            yaml_data["channels"].append(channel_data)

        # Write YAML file
        output_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = output_dir / "acquisition_channels.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def toggle_confocal_widefield(self, confocal: Union[bool, int]) -> None:
        """Toggle between confocal and widefield modes.

        Args:
            confocal: Whether to enable confocal mode
        """
        self.confocal_mode = bool(confocal)
        self._log.info(f"Imaging mode set to: {'confocal' if self.confocal_mode else 'widefield'}")

    def is_confocal_mode(self) -> bool:
        """Check if currently in confocal mode."""
        return self.confocal_mode

    def sync_confocal_mode_from_hardware(self, confocal: Union[bool, int]) -> None:
        """Sync confocal mode state from hardware."""
        self.toggle_confocal_widefield(confocal)

    def has_yaml_configs(self) -> bool:
        """Check if YAML acquisition configs are available."""
        return self._general_config is not None

    def get_general_config(self) -> Optional[GeneralChannelConfig]:
        """Get the loaded general.yaml config."""
        return self._general_config

    def get_objective_config(self, objective: str) -> Optional[ObjectiveChannelConfig]:
        """Get the loaded objective config."""
        return self._objective_configs.get(objective)

    def get_illumination_config(self) -> Optional[IlluminationChannelConfig]:
        """Get the loaded illumination channel config."""
        return self._illumination_config
