import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from control.utils_config import LaserAFConfig


class LaserAFSettingManager:
    """Manages JSON-based laser autofocus configurations."""

    def __init__(self):
        self.autofocus_configurations: Dict[str, LaserAFConfig] = {}  # Dict[str, Dict[str, Any]]
        self.current_profile_path = None

    def set_profile_path(self, profile_path: Path) -> None:
        self.current_profile_path = profile_path

    def load_configurations(self, objective: str) -> None:
        """Load autofocus configurations for a specific objective."""
        config_file = self.current_profile_path / objective / "laser_af_settings.json"
        if config_file.exists():
            with open(config_file, "r") as f:
                config_dict = json.load(f)
                self.autofocus_configurations[objective] = LaserAFConfig(**config_dict)

    def save_configurations(self, objective: str) -> None:
        """Save autofocus configurations for a specific objective."""
        if objective not in self.autofocus_configurations:
            return

        objective_path = self.current_profile_path / objective
        if not objective_path.exists():
            objective_path.mkdir(parents=True)
        config_file = objective_path / "laser_af_settings.json"

        config_dict = self.autofocus_configurations[objective].model_dump(serialize=True)
        with open(config_file, "w") as f:
            json.dump(config_dict, f, indent=4)

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
