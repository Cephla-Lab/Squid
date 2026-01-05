from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import yaml

from control.models import LaserAFConfig


class LaserAFSettingManager:
    """Manages YAML-based laser autofocus configurations."""

    def __init__(self):
        self.autofocus_configurations: Dict[str, LaserAFConfig] = {}
        self.current_profile_path = None

    def set_profile_path(self, profile_path: Path) -> None:
        self.current_profile_path = profile_path

    def _get_yaml_path(self, objective: str) -> Path:
        """Get the YAML config path for an objective."""
        return self.current_profile_path / "laser_af_configs" / f"{objective}.yaml"

    def _get_legacy_json_path(self, objective: str) -> Path:
        """Get the legacy JSON config path for an objective."""
        return self.current_profile_path / objective / "laser_af_settings.json"

    def load_configurations(self, objective: str) -> None:
        """Load autofocus configurations for a specific objective.

        Tries new YAML path first, falls back to legacy JSON path for migration.
        """
        yaml_path = self._get_yaml_path(objective)
        legacy_json_path = self._get_legacy_json_path(objective)

        if yaml_path.exists():
            # Load from new YAML format
            with open(yaml_path, "r") as f:
                config_dict = yaml.safe_load(f)
                self.autofocus_configurations[objective] = LaserAFConfig(**config_dict)
        elif legacy_json_path.exists():
            # Fallback to legacy JSON format (for migration)
            import json

            with open(legacy_json_path, "r") as f:
                config_dict = json.load(f)
                self.autofocus_configurations[objective] = LaserAFConfig(**config_dict)

    def save_configurations(self, objective: str) -> None:
        """Save autofocus configurations for a specific objective in YAML format."""
        if objective not in self.autofocus_configurations:
            return

        yaml_path = self._get_yaml_path(objective)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)

        config_dict = self.autofocus_configurations[objective].model_dump(serialize=True)
        with open(yaml_path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def get_settings_for_objective(self, objective: str) -> LaserAFConfig:
        if objective not in self.autofocus_configurations:
            raise ValueError(f"No configuration found for objective {objective}")
        return self.autofocus_configurations[objective]

    def get_laser_af_settings(self) -> Dict[str, Any]:
        return self.autofocus_configurations

    def update_laser_af_settings(
        self, objective: str, updates: Dict[str, Any], crop_image: Optional[np.ndarray] = None
    ) -> None:
        if objective not in self.autofocus_configurations:
            self.autofocus_configurations[objective] = LaserAFConfig(**updates)
        else:
            config = self.autofocus_configurations[objective]
            self.autofocus_configurations[objective] = config.model_copy(update=updates)
        if crop_image is not None:
            self.autofocus_configurations[objective].set_reference_image(crop_image)
