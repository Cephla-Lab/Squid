import json
from pathlib import Path
from typing import Any, List, Dict, Optional

from control.utils_config import ChannelConfig, ChannelMode
from control._def import *
import squid.logging


class AdditionalParamsManager:
    """
    Manages additional configuration parameters for each objective. These configurations are saved in JSON files
    and should be optional for acquisitions. Right now these parameters will not be saved in real time and should be
    edited directly in the JSON files.
    """

    def __init__(self):
        self.additional_params: Dict[str, Dict[str, Any]] = {}  # Dict[str, Dict[str, Any]]
        self.current_profile_path = None

    def set_profile_path(self, profile_path: Path) -> None:
        self.current_profile_path = profile_path

    def load_configurations(self, objective: str) -> None:
        """Load additional parameters for a specific objective."""
        config_file = self.current_profile_path / objective / "additional_params.json"
        if config_file.exists():
            with open(config_file, "r") as f:
                config_dict = json.load(f)
                self.additional_params[objective] = config_dict

    def save_configurations(self, objective: str) -> None:
        """Save additional parameters for a specific objective."""
        if objective not in self.additional_params:
            return

        # Check if the original file exists - if not, don't create it
        config_file = self.current_profile_path / objective / "additional_params.json"
        if not config_file.exists():
            return

        objective_path = self.current_profile_path / objective
        if not objective_path.exists():
            objective_path.mkdir(parents=True)

        with open(config_file, "w") as f:
            json.dump(self.additional_params[objective], f, indent=4)

    def get_settings_for_objective(self, objective: str) -> Optional[Dict[str, Any]]:
        """Get additional parameters for a specific objective. Returns None if not found."""
        return self.additional_params.get(objective, None)

    def get_additional_params(self) -> Dict[str, Dict[str, Any]]:
        """Get all additional parameters."""
        return self.additional_params

    def update_additional_params(self, objective: str, updates: Dict[str, Any]) -> None:
        """Update additional parameters for a specific objective."""
        if objective not in self.additional_params:
            # If file doesn't exist, initialize with null values for unknown fields
            config_file = self.current_profile_path / objective / "additional_params.json"
            if not config_file.exists():
                # Initialize with the provided updates, other fields will be null
                self.additional_params[objective] = updates
            else:
                self.additional_params[objective] = updates
        else:
            # Update existing configuration
            self.additional_params[objective].update(updates)
