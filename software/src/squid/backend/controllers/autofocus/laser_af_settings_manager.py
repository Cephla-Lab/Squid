from typing import Any, Dict, Optional

import numpy as np

from squid.core.config.models import LaserAFConfig
from squid.core.config.repository import ConfigRepository


class LaserAFSettingManager:
    """Manages laser autofocus configurations via YAML ConfigRepository."""

    def __init__(self, config_repo: Optional[ConfigRepository] = None):
        self.autofocus_configurations: Dict[str, LaserAFConfig] = {}
        self._config_repo = config_repo

    def load_configurations(self, objective: str) -> None:
        """Load autofocus configurations for a specific objective."""
        if self._config_repo is None:
            return
        config = self._config_repo.get_laser_af_config(objective)
        if config is not None:
            self.autofocus_configurations[objective] = config

    def save_configurations(self, objective: str) -> None:
        """Save autofocus configurations for a specific objective."""
        if objective not in self.autofocus_configurations:
            return
        if self._config_repo is None:
            return
        profile = self._config_repo.current_profile
        if profile is None:
            return
        self._config_repo.save_laser_af_config(
            profile, objective, self.autofocus_configurations[objective]
        )

    def get_settings_for_objective(self, objective: str) -> LaserAFConfig:
        if objective not in self.autofocus_configurations:
            raise ValueError(f"No configuration found for objective {objective}")
        return self.autofocus_configurations[objective]

    def get_laser_af_settings(self) -> Dict[str, Any]:
        return self.autofocus_configurations

    def update_laser_af_settings(
        self,
        objective: str,
        updates: Dict[str, Any],
        crop_image: Optional[np.ndarray] = None,
    ) -> None:
        if objective not in self.autofocus_configurations:
            self.autofocus_configurations[objective] = LaserAFConfig(**updates)
        else:
            config = self.autofocus_configurations[objective]
            self.autofocus_configurations[objective] = config.model_copy(update=updates)
        if crop_image is not None:
            self.autofocus_configurations[objective].set_reference_image(crop_image)

    # Legacy compatibility — called by ConfigurationManager but is a no-op
    # when using ConfigRepository (profile is managed by the repository).
    def set_profile_path(self, profile_path: Any) -> None:
        pass
