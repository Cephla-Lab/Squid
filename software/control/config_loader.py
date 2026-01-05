"""
Configuration loader for YAML-based acquisition configs.

This module handles loading and saving all configuration types:
- IlluminationChannelConfig (machine_configs/illumination_channel_config.yaml)
- ConfocalConfig (machine_configs/confocal_config.yaml)
- CameraMappingsConfig (machine_configs/camera_mappings.yaml)
- GeneralChannelConfig (user_profiles/{profile}/channel_configs/general.yaml)
- ObjectiveChannelConfig (user_profiles/{profile}/channel_configs/{objective}.yaml)
- LaserAFConfig (user_profiles/{profile}/laser_af_configs/{objective}.yaml)
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Type, TypeVar

import yaml

from control.models import (
    CameraMappingsConfig,
    ConfocalConfig,
    GeneralChannelConfig,
    IlluminationChannelConfig,
    LaserAFConfig,
    ObjectiveChannelConfig,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ConfigLoader:
    """
    Handles loading and saving YAML configuration files.

    Directory structure:
        software/
        ├── machine_configs/
        │   ├── illumination_channel_config.yaml
        │   ├── confocal_config.yaml (optional)
        │   ├── camera_mappings.yaml
        │   └── intensity_calibrations/
        │       └── *.csv
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
        Initialize the config loader.

        Args:
            base_path: Base path for configuration files. Defaults to the
                      'software' directory containing this module.
        """
        if base_path is None:
            # Default to software/ directory
            base_path = Path(__file__).parent.parent
        self.base_path = Path(base_path)
        self.machine_configs_path = self.base_path / "machine_configs"
        self.user_profiles_path = self.base_path / "user_profiles"

    def _load_yaml(self, path: Path, model_class: Type[T]) -> Optional[T]:
        """Load a YAML file and parse it into a Pydantic model."""
        if not path.exists():
            logger.debug(f"Config file not found: {path}")
            return None

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
            if data is None:
                data = {}
            return model_class(**data)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML file {path}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load config from {path}: {e}")
            raise

    def _save_yaml(self, path: Path, model: T) -> None:
        """Save a Pydantic model to a YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert model to dict, using mode="json" to ensure Enums are serialized as strings
        if hasattr(model, "model_dump"):
            data = model.model_dump(exclude_none=False, mode="json")
        else:
            data = dict(model)

        try:
            with open(path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.debug(f"Saved config to {path}")
        except Exception as e:
            logger.error(f"Failed to save config to {path}: {e}")
            raise

    # Machine configs

    def load_illumination_config(self) -> Optional[IlluminationChannelConfig]:
        """Load illumination channel configuration."""
        path = self.machine_configs_path / "illumination_channel_config.yaml"
        return self._load_yaml(path, IlluminationChannelConfig)

    def save_illumination_config(self, config: IlluminationChannelConfig) -> None:
        """Save illumination channel configuration."""
        path = self.machine_configs_path / "illumination_channel_config.yaml"
        self._save_yaml(path, config)

    def load_confocal_config(self) -> Optional[ConfocalConfig]:
        """
        Load confocal configuration.

        Returns None if confocal_config.yaml doesn't exist (system has no confocal).
        """
        path = self.machine_configs_path / "confocal_config.yaml"
        return self._load_yaml(path, ConfocalConfig)

    def save_confocal_config(self, config: ConfocalConfig) -> None:
        """Save confocal configuration."""
        path = self.machine_configs_path / "confocal_config.yaml"
        self._save_yaml(path, config)

    def has_confocal(self) -> bool:
        """Check if confocal configuration exists."""
        path = self.machine_configs_path / "confocal_config.yaml"
        return path.exists()

    def load_camera_mappings(self) -> Optional[CameraMappingsConfig]:
        """Load camera mappings configuration."""
        path = self.machine_configs_path / "camera_mappings.yaml"
        return self._load_yaml(path, CameraMappingsConfig)

    def save_camera_mappings(self, config: CameraMappingsConfig) -> None:
        """Save camera mappings configuration."""
        path = self.machine_configs_path / "camera_mappings.yaml"
        self._save_yaml(path, config)

    # User profile configs

    def get_profile_path(self, profile: str) -> Path:
        """Get the path for a user profile."""
        return self.user_profiles_path / profile

    def get_available_profiles(self) -> List[str]:
        """Get list of available user profiles."""
        if not self.user_profiles_path.exists():
            return []
        return [
            d.name
            for d in self.user_profiles_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

    def get_available_objectives(self, profile: str) -> List[str]:
        """Get list of available objectives for a profile."""
        channel_configs_path = self.user_profiles_path / profile / "channel_configs"
        if not channel_configs_path.exists():
            return []
        objectives = []
        for f in channel_configs_path.iterdir():
            if f.suffix == ".yaml" and f.stem != "general":
                objectives.append(f.stem)
        return sorted(objectives)

    def load_general_config(self, profile: str) -> Optional[GeneralChannelConfig]:
        """Load general channel configuration for a profile."""
        path = self.user_profiles_path / profile / "channel_configs" / "general.yaml"
        return self._load_yaml(path, GeneralChannelConfig)

    def save_general_config(self, profile: str, config: GeneralChannelConfig) -> None:
        """Save general channel configuration for a profile."""
        path = self.user_profiles_path / profile / "channel_configs" / "general.yaml"
        self._save_yaml(path, config)

    def load_objective_config(
        self, profile: str, objective: str
    ) -> Optional[ObjectiveChannelConfig]:
        """Load objective-specific channel configuration."""
        path = self.user_profiles_path / profile / "channel_configs" / f"{objective}.yaml"
        return self._load_yaml(path, ObjectiveChannelConfig)

    def save_objective_config(
        self, profile: str, objective: str, config: ObjectiveChannelConfig
    ) -> None:
        """Save objective-specific channel configuration."""
        path = self.user_profiles_path / profile / "channel_configs" / f"{objective}.yaml"
        self._save_yaml(path, config)

    def load_laser_af_config(
        self, profile: str, objective: str
    ) -> Optional[LaserAFConfig]:
        """Load laser AF configuration for an objective."""
        path = self.user_profiles_path / profile / "laser_af_configs" / f"{objective}.yaml"
        return self._load_yaml(path, LaserAFConfig)

    def save_laser_af_config(
        self, profile: str, objective: str, config: LaserAFConfig
    ) -> None:
        """Save laser AF configuration for an objective."""
        path = self.user_profiles_path / profile / "laser_af_configs" / f"{objective}.yaml"
        self._save_yaml(path, config)

    # Utility methods

    def profile_has_configs(self, profile: str) -> bool:
        """Check if a profile has any configuration files."""
        general_path = self.user_profiles_path / profile / "channel_configs" / "general.yaml"
        return general_path.exists()

    def ensure_profile_directories(self, profile: str) -> None:
        """Create profile directories if they don't exist."""
        channel_configs_path = self.user_profiles_path / profile / "channel_configs"
        laser_af_path = self.user_profiles_path / profile / "laser_af_configs"
        channel_configs_path.mkdir(parents=True, exist_ok=True)
        laser_af_path.mkdir(parents=True, exist_ok=True)

    def ensure_machine_configs_directory(self) -> None:
        """Create machine_configs directory if it doesn't exist."""
        self.machine_configs_path.mkdir(parents=True, exist_ok=True)
        (self.machine_configs_path / "intensity_calibrations").mkdir(exist_ok=True)
