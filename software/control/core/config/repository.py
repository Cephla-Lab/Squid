"""
Centralized configuration repository.

Single source of truth for all config I/O and caching.
Pure Python - NO Qt dependencies.

Organization:
- Generic I/O: save_to_path() for saving any Pydantic model
- Profile Management: profile CRUD operations
- Machine Configs: global hardware configs (illumination, confocal, camera mappings)
- Channel Configs: per-profile acquisition channel settings
- Channel Config Convenience: higher-level helpers (merge, update settings)
- Laser AF Configs: per-profile laser autofocus settings
- Acquisition Output: saving settings to experiment directories
- Cache Management: cache control
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

import yaml
from pydantic import BaseModel, ValidationError

from control.models import (
    AcquisitionChannel,
    AcquisitionOutputConfig,
    CameraMappingsConfig,
    ConfocalConfig,
    GeneralChannelConfig,
    IlluminationChannelConfig,
    IlluminationSettings,
    CameraSettings,
    LaserAFConfig,
    ObjectiveChannelConfig,
    merge_channel_configs,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ConfigRepository:
    """
    Centralized configuration repository.

    Handles loading, saving, and caching for all Pydantic config models.
    Supports machine configs (global) and profile configs (per-user).

    Directory structure:
        software/
        ├── machine_configs/
        │   ├── illumination_channel_config.yaml
        │   ├── confocal_config.yaml (optional)
        │   └── camera_mappings.yaml
        └── user_profiles/
            └── {profile}/
                ├── channel_configs/
                │   ├── general.yaml
                │   └── {objective}.yaml
                └── laser_af_configs/
                    └── {objective}.yaml
    """

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize the config repository.

        Args:
            base_path: Base path for configuration files. Defaults to the
                      'software' directory containing this module.
        """
        if base_path is None:
            # Default to software/ directory (4 levels up from this file)
            base_path = Path(__file__).parent.parent.parent.parent
        self.base_path = Path(base_path)
        self.machine_configs_path = self.base_path / "machine_configs"
        self.user_profiles_path = self.base_path / "user_profiles"

        self._current_profile: Optional[str] = None
        self._machine_cache: Dict[str, Any] = {}
        self._profile_cache: Dict[str, Any] = {}

    # ═══════════════════════════════════════════════════════════════════════════
    # GENERIC I/O
    # Methods that work with any Pydantic model
    # ═══════════════════════════════════════════════════════════════════════════

    def save_to_path(self, path: Path, model: BaseModel) -> None:
        """
        Save any Pydantic model to an arbitrary path.

        This is the generic save method - use it when you need to save a model
        to a location outside the standard config directories.

        Args:
            path: Target file path (parent directories created if needed)
            model: Pydantic model to save
        """
        self._save_yaml(path, model)

    # ═══════════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_yaml(self, path: Path, model_class: Type[T]) -> Optional[T]:
        """
        Load a YAML file and parse it into a Pydantic model.

        Error handling:
        - File not found: return None
        - YAML parse error: log warning, return None
        - Pydantic validation error: log warning, return None
        - Permission error: raise (real problem)
        """
        if not path.exists():
            logger.debug(f"Config file not found: {path}")
            return None

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
            if data is None:
                data = {}
            return model_class(**data)
        except PermissionError:
            logger.error(f"Permission denied reading {path}")
            raise
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML file {path}: {e}")
            return None
        except ValidationError as e:
            logger.warning(f"Config validation failed for {path}: {e}")
            return None

    def _save_yaml(self, path: Path, model: BaseModel) -> None:
        """
        Save a Pydantic model to a YAML file.

        Creates parent directories if needed.
        Raises on permission or disk errors.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert model to dict, using mode="json" to ensure Enums are serialized as strings
        data = model.model_dump(exclude_none=False, mode="json")

        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.debug(f"Saved config to {path}")

    def _get_profile_path(self, profile: Optional[str] = None) -> Path:
        """Get path for a profile, defaulting to current profile."""
        profile = profile or self._current_profile
        if profile is None:
            raise ValueError("No profile set. Call set_profile() first.")
        return self.user_profiles_path / profile

    # ═══════════════════════════════════════════════════════════════════════════
    # PROFILE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════

    @property
    def current_profile(self) -> Optional[str]:
        """Get the current profile name."""
        return self._current_profile

    def set_profile(self, profile: str) -> None:
        """
        Set the current profile. Clears profile cache.

        Args:
            profile: Profile name (directory name under user_profiles/)

        Raises:
            ValueError: If profile doesn't exist
        """
        profile_path = self.user_profiles_path / profile
        if not profile_path.exists():
            raise ValueError(f"Profile '{profile}' does not exist at {profile_path}")

        self._current_profile = profile
        self._profile_cache.clear()
        logger.debug(f"Switched to profile: {profile}")

    def load_profile(self, profile: str, objectives: Optional[List[str]] = None) -> None:
        """
        Load a profile, ensuring default configs exist.

        This is the high-level method for switching profiles that:
        1. Ensures the profile has default configs if needed
        2. Sets the profile as current

        Args:
            profile: Profile name
            objectives: Optional list of objectives for default config generation

        Raises:
            ValueError: If profile doesn't exist
        """
        profile_path = self.user_profiles_path / profile
        if not profile_path.exists():
            raise ValueError(f"Profile '{profile}' does not exist")

        # Ensure default configs exist (lazy import to avoid circular dependency)
        try:
            from control.default_config_generator import ensure_default_configs
            import control._def

            obj_list = objectives or (list(control._def.OBJECTIVES) if hasattr(control._def, "OBJECTIVES") else None)
            if ensure_default_configs(self, profile, obj_list):
                logger.info(f"Generated default configs for profile '{profile}'")
        except Exception as e:
            logger.warning(f"Could not generate default configs: {e}")

        self.set_profile(profile)

    def get_available_profiles(self) -> List[str]:
        """Get list of available user profiles."""
        if not self.user_profiles_path.exists():
            return []
        return sorted([d.name for d in self.user_profiles_path.iterdir() if d.is_dir() and not d.name.startswith(".")])

    def get_available_objectives(self, profile: Optional[str] = None) -> List[str]:
        """
        Get list of available objectives for a profile.

        Args:
            profile: Profile name. Defaults to current profile.
        """
        profile_path = self._get_profile_path(profile)
        channel_configs_path = profile_path / "channel_configs"
        if not channel_configs_path.exists():
            return []
        objectives = []
        for f in channel_configs_path.iterdir():
            if f.suffix == ".yaml" and f.stem != "general":
                objectives.append(f.stem)
        return sorted(objectives)

    def create_profile(self, name: str) -> None:
        """
        Create a new empty profile with directory structure.

        Args:
            name: Profile name

        Raises:
            ValueError: If profile already exists
        """
        profile_path = self.user_profiles_path / name
        if profile_path.exists():
            raise ValueError(f"Profile '{name}' already exists")

        (profile_path / "channel_configs").mkdir(parents=True)
        (profile_path / "laser_af_configs").mkdir(parents=True)
        logger.info(f"Created profile: {name}")

    def copy_profile(self, source: str, dest: str) -> None:
        """
        Create a new profile by copying all configs from an existing profile.

        Args:
            source: Source profile name to copy from
            dest: Destination profile name to create

        Raises:
            ValueError: If dest profile already exists or source doesn't exist
        """
        import shutil

        source_path = self.user_profiles_path / source
        dest_path = self.user_profiles_path / dest

        if not source_path.exists():
            raise ValueError(f"Source profile '{source}' does not exist")
        if dest_path.exists():
            raise ValueError(f"Profile '{dest}' already exists")

        # Create directory structure
        (dest_path / "channel_configs").mkdir(parents=True)
        (dest_path / "laser_af_configs").mkdir(parents=True)

        # Copy all YAML files from source to dest
        for subdir in ["channel_configs", "laser_af_configs"]:
            source_dir = source_path / subdir
            dest_dir = dest_path / subdir
            if source_dir.exists():
                for yaml_file in source_dir.glob("*.yaml"):
                    shutil.copy2(yaml_file, dest_dir / yaml_file.name)

        logger.info(f"Created profile '{dest}' by copying from '{source}'")

    def profile_exists(self, name: str) -> bool:
        """Check if a profile exists."""
        return (self.user_profiles_path / name).exists()

    def profile_has_configs(self, profile: Optional[str] = None) -> bool:
        """Check if a profile has any configuration files (general.yaml exists)."""
        profile_path = self._get_profile_path(profile)
        general_path = profile_path / "channel_configs" / "general.yaml"
        return general_path.exists()

    def ensure_profile_directories(self, profile: Optional[str] = None) -> None:
        """Create profile directories if they don't exist."""
        profile_path = self._get_profile_path(profile)
        (profile_path / "channel_configs").mkdir(parents=True, exist_ok=True)
        (profile_path / "laser_af_configs").mkdir(parents=True, exist_ok=True)

    def get_profile_path(self, profile: Optional[str] = None) -> Path:
        """Get the path for a user profile (public API)."""
        return self._get_profile_path(profile)

    # ═══════════════════════════════════════════════════════════════════════════
    # MACHINE CONFIGS
    # Global hardware configuration (cached indefinitely)
    # ═══════════════════════════════════════════════════════════════════════════

    def get_illumination_config(self) -> Optional[IlluminationChannelConfig]:
        """Load illumination channel configuration (cached)."""
        cache_key = "illumination"
        if cache_key not in self._machine_cache:
            path = self.machine_configs_path / "illumination_channel_config.yaml"
            self._machine_cache[cache_key] = self._load_yaml(path, IlluminationChannelConfig)
        return self._machine_cache[cache_key]

    def get_confocal_config(self) -> Optional[ConfocalConfig]:
        """
        Load confocal configuration (cached).

        Returns None if confocal_config.yaml doesn't exist (system has no confocal).
        """
        cache_key = "confocal"
        if cache_key not in self._machine_cache:
            path = self.machine_configs_path / "confocal_config.yaml"
            self._machine_cache[cache_key] = self._load_yaml(path, ConfocalConfig)
        return self._machine_cache[cache_key]

    def get_camera_mappings(self) -> Optional[CameraMappingsConfig]:
        """Load camera mappings configuration (cached)."""
        cache_key = "camera_mappings"
        if cache_key not in self._machine_cache:
            path = self.machine_configs_path / "camera_mappings.yaml"
            self._machine_cache[cache_key] = self._load_yaml(path, CameraMappingsConfig)
        return self._machine_cache[cache_key]

    def has_confocal(self) -> bool:
        """Check if system has confocal hardware."""
        return self.get_confocal_config() is not None

    def save_illumination_config(self, config: IlluminationChannelConfig) -> None:
        """Save illumination channel configuration and update cache."""
        path = self.machine_configs_path / "illumination_channel_config.yaml"
        self._save_yaml(path, config)
        self._machine_cache["illumination"] = config

    def save_confocal_config(self, config: ConfocalConfig) -> None:
        """Save confocal configuration and update cache."""
        path = self.machine_configs_path / "confocal_config.yaml"
        self._save_yaml(path, config)
        self._machine_cache["confocal"] = config

    def save_camera_mappings(self, config: CameraMappingsConfig) -> None:
        """Save camera mappings configuration and update cache."""
        path = self.machine_configs_path / "camera_mappings.yaml"
        self._save_yaml(path, config)
        self._machine_cache["camera_mappings"] = config

    def ensure_machine_configs_directory(self) -> None:
        """Create machine_configs directory if it doesn't exist."""
        self.machine_configs_path.mkdir(parents=True, exist_ok=True)
        (self.machine_configs_path / "intensity_calibrations").mkdir(exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # CHANNEL CONFIGS (per-profile)
    # Core CRUD operations for acquisition channel settings
    # ═══════════════════════════════════════════════════════════════════════════

    def get_general_config(self, profile: Optional[str] = None) -> Optional[GeneralChannelConfig]:
        """Load general channel configuration (cached when using current profile)."""
        if profile is None or profile == self._current_profile:
            cache_key = "general"
            if cache_key not in self._profile_cache:
                profile_path = self._get_profile_path()
                path = profile_path / "channel_configs" / "general.yaml"
                self._profile_cache[cache_key] = self._load_yaml(path, GeneralChannelConfig)
            return self._profile_cache[cache_key]
        else:
            # Explicit profile - load directly without caching
            path = self.user_profiles_path / profile / "channel_configs" / "general.yaml"
            return self._load_yaml(path, GeneralChannelConfig)

    def get_objective_config(self, objective: str, profile: Optional[str] = None) -> Optional[ObjectiveChannelConfig]:
        """Load objective-specific channel configuration (cached when using current profile)."""
        if profile is None or profile == self._current_profile:
            cache_key = f"objective:{objective}"
            if cache_key not in self._profile_cache:
                profile_path = self._get_profile_path()
                path = profile_path / "channel_configs" / f"{objective}.yaml"
                self._profile_cache[cache_key] = self._load_yaml(path, ObjectiveChannelConfig)
            return self._profile_cache[cache_key]
        else:
            # Explicit profile - load directly without caching
            path = self.user_profiles_path / profile / "channel_configs" / f"{objective}.yaml"
            return self._load_yaml(path, ObjectiveChannelConfig)

    def save_general_config(self, profile: str, config: GeneralChannelConfig) -> None:
        """Save general channel configuration and update cache if current profile."""
        if profile == self._current_profile:
            profile_path = self._get_profile_path()
            path = profile_path / "channel_configs" / "general.yaml"
            self._save_yaml(path, config)
            self._profile_cache["general"] = config
        else:
            # Different profile - save without caching
            path = self.user_profiles_path / profile / "channel_configs" / "general.yaml"
            self._save_yaml(path, config)

    def save_objective_config(self, profile: str, objective: str, config: ObjectiveChannelConfig) -> None:
        """Save objective-specific channel configuration and update cache if current profile."""
        if profile == self._current_profile:
            profile_path = self._get_profile_path()
            path = profile_path / "channel_configs" / f"{objective}.yaml"
            self._save_yaml(path, config)
            self._profile_cache[f"objective:{objective}"] = config
        else:
            # Different profile - save without caching
            path = self.user_profiles_path / profile / "channel_configs" / f"{objective}.yaml"
            self._save_yaml(path, config)

    # ═══════════════════════════════════════════════════════════════════════════
    # CHANNEL CONFIG CONVENIENCE METHODS
    # Higher-level helpers for common channel config operations
    # ═══════════════════════════════════════════════════════════════════════════

    def get_merged_channels(
        self,
        objective: str,
        profile: Optional[str] = None,
        confocal_mode: bool = False,
    ) -> List[AcquisitionChannel]:
        """
        Get merged acquisition channels for an objective.

        Merges general.yaml with objective.yaml and optionally applies confocal overrides.

        Args:
            objective: Objective name
            profile: Profile name (defaults to current profile)
            confocal_mode: Whether to apply confocal overrides

        Returns:
            List of merged AcquisitionChannel objects
        """
        general_config = self.get_general_config(profile)
        if not general_config:
            return []

        obj_config = self.get_objective_config(objective, profile)

        if obj_config:
            channels = merge_channel_configs(general_config, obj_config)
        else:
            channels = list(general_config.channels)

        if confocal_mode:
            channels = [ch.get_effective_settings(confocal_mode=True) for ch in channels]

        return channels

    def update_channel_setting(
        self,
        objective: str,
        channel_name: str,
        setting: str,
        value: Any,
        profile: Optional[str] = None,
    ) -> bool:
        """
        Update a specific setting of a channel configuration and save.

        This is a convenience method that handles the mapping from UI setting names
        to model fields, creates objective configs if needed, and saves automatically.

        Supported settings:
        - "ExposureTime" -> camera_settings.exposure_time_ms
        - "AnalogGain" -> camera_settings.gain_mode
        - "IlluminationIntensity" -> illumination_settings.intensity

        Args:
            objective: Objective name
            channel_name: Name of the channel to update
            setting: Setting name ("ExposureTime", "AnalogGain", "IlluminationIntensity")
            value: New value for the setting
            profile: Profile name (defaults to current profile)

        Returns:
            True if update was successful, False otherwise
        """
        profile = profile or self._current_profile
        if not profile:
            logger.warning("Cannot update: no profile set")
            return False

        # Setting name to model field mapping
        setting_mapping = {
            "ExposureTime": ("camera", "exposure_time_ms"),
            "AnalogGain": ("camera", "gain_mode"),
            "IlluminationIntensity": ("illumination", "intensity"),
        }

        if setting not in setting_mapping:
            logger.warning(f"Unknown setting: {setting}")
            return False

        location, field = setting_mapping[setting]

        # Get or create objective config
        obj_config = self.get_objective_config(objective, profile)
        general_config = self.get_general_config(profile)

        if obj_config is None:
            if general_config is None:
                logger.warning("No general config to create objective config from")
                return False
            # Create objective config from general config
            obj_config = ObjectiveChannelConfig(
                version=1,
                channels=[
                    AcquisitionChannel(
                        name=ch.name,
                        illumination_settings=IlluminationSettings(
                            illumination_channels=None,
                            intensity=dict(ch.illumination_settings.intensity),
                            z_offset_um=0.0,
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
                    for ch in general_config.channels
                ],
            )

        # Find the channel
        acq_channel = obj_config.get_channel_by_name(channel_name)
        if not acq_channel:
            logger.warning(f"Channel '{channel_name}' not found in objective config")
            return False

        # Update the field
        if location == "camera":
            for cam_settings in acq_channel.camera_settings.values():
                setattr(cam_settings, field, value)
                break
        elif location == "illumination":
            for key in acq_channel.illumination_settings.intensity:
                acq_channel.illumination_settings.intensity[key] = value

        # Save
        self.save_objective_config(profile, objective, obj_config)

        # Update cache if current profile
        effective_profile = profile if profile else self._current_profile
        if effective_profile == self._current_profile:
            self._profile_cache[f"objective:{objective}"] = obj_config

        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # LASER AF CONFIGS (per-profile)
    # Laser autofocus settings per objective
    # ═══════════════════════════════════════════════════════════════════════════

    def get_laser_af_config(self, objective: str, profile: Optional[str] = None) -> Optional[LaserAFConfig]:
        """Load laser AF configuration for an objective (cached when using current profile)."""
        if profile is None or profile == self._current_profile:
            cache_key = f"laser_af:{objective}"
            if cache_key not in self._profile_cache:
                profile_path = self._get_profile_path()
                path = profile_path / "laser_af_configs" / f"{objective}.yaml"
                self._profile_cache[cache_key] = self._load_yaml(path, LaserAFConfig)
            return self._profile_cache[cache_key]
        else:
            # Explicit profile - load directly without caching
            path = self.user_profiles_path / profile / "laser_af_configs" / f"{objective}.yaml"
            return self._load_yaml(path, LaserAFConfig)

    def save_laser_af_config(self, profile: str, objective: str, config: LaserAFConfig) -> None:
        """Save laser AF configuration and update cache if current profile."""
        if profile == self._current_profile:
            profile_path = self._get_profile_path()
            path = profile_path / "laser_af_configs" / f"{objective}.yaml"
            self._save_yaml(path, config)
            self._profile_cache[f"laser_af:{objective}"] = config
        else:
            # Different profile - save without caching
            path = self.user_profiles_path / profile / "laser_af_configs" / f"{objective}.yaml"
            self._save_yaml(path, config)

    # ═══════════════════════════════════════════════════════════════════════════
    # ACQUISITION OUTPUT
    # Saving acquisition settings to experiment directories
    # ═══════════════════════════════════════════════════════════════════════════

    def save_acquisition_output(
        self,
        output_dir: Union[Path, str],
        objective: str,
        channels: List[AcquisitionChannel],
        confocal_mode: bool = False,
    ) -> None:
        """
        Save acquisition settings to an experiment output directory.

        Creates acquisition_channels.yaml in the output directory to record
        what settings were used during acquisition. This is separate from
        profile configs - it's a snapshot of settings used for a specific run.

        Args:
            output_dir: Experiment output directory
            objective: Objective used for acquisition
            channels: List of acquisition channels used
            confocal_mode: Whether confocal mode was active
        """
        output_config = AcquisitionOutputConfig(
            objective=objective,
            confocal_mode=confocal_mode,
            channels=channels,
        )
        output_path = Path(output_dir) / "acquisition_channels.yaml"
        self._save_yaml(output_path, output_config)

    # ═══════════════════════════════════════════════════════════════════════════
    # CACHE MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════

    def clear_profile_cache(self) -> None:
        """Clear profile cache (called on profile switch)."""
        self._profile_cache.clear()

    def clear_all_cache(self) -> None:
        """Clear all caches (rarely needed)."""
        self._machine_cache.clear()
        self._profile_cache.clear()
