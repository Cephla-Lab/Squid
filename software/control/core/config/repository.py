"""
Centralized configuration repository.

Single source of truth for all config I/O and caching.
Pure Python - NO Qt dependencies.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from control.models import (
    CameraMappingsConfig,
    ConfocalConfig,
    GeneralChannelConfig,
    IlluminationChannelConfig,
    LaserAFConfig,
    ObjectiveChannelConfig,
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

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Profile Management
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Machine Configs (global, cached indefinitely)
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Profile Configs - Channel Configs (cached until profile change or save)
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Profile Configs - Laser AF (cached until profile change or save)
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # Cache Management
    # ─────────────────────────────────────────────────────────────────────────

    def clear_profile_cache(self) -> None:
        """Clear profile cache (called on profile switch)."""
        self._profile_cache.clear()

    def clear_all_cache(self) -> None:
        """Clear all caches (rarely needed)."""
        self._machine_cache.clear()
        self._profile_cache.clear()
