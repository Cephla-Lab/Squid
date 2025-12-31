import os
from pathlib import Path
from typing import List, Optional

from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
import control._def


class ConfigurationManager:
    """Main configuration manager that coordinates channel and autofocus configurations."""

    def __init__(
        self,
        channel_manager: ChannelConfigurationManager,
        laser_af_manager: Optional[LaserAFSettingManager] = None,
        base_config_path: Path = control._def.ACQUISITION_CONFIGURATIONS_PATH,
        profile: str = "default_profile",
    ):
        super().__init__()
        self.base_config_path = Path(base_config_path)
        self.current_profile = profile
        self.available_profiles = self._get_available_profiles()

        self.channel_manager = channel_manager
        self.laser_af_manager = laser_af_manager

        self.load_profile(profile)

    def _get_available_profiles(self) -> List[str]:
        """Get all available user profile names in the base config path. Use default profile if no other profiles exist."""
        if not self.base_config_path.exists():
            os.makedirs(self.base_config_path)
            os.makedirs(self.base_config_path / "default_profile")
            for objective in control._def.OBJECTIVES:
                os.makedirs(self.base_config_path / "default_profile" / objective)
        return [d.name for d in self.base_config_path.iterdir() if d.is_dir()]

    def _get_available_objectives(self, profile_path: Path) -> List[str]:
        """Get all available objective names in a profile."""
        return [d.name for d in profile_path.iterdir() if d.is_dir()]

    def load_profile(self, profile_name: str) -> None:
        """Load all configurations from a specific profile."""
        profile_path = self.base_config_path / profile_name
        if not profile_path.exists():
            raise ValueError(f"Profile {profile_name} does not exist")

        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(profile_path)

        # Load configurations for each objective
        for objective in self._get_available_objectives(profile_path):
            if self.channel_manager:
                self.channel_manager.load_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.load_configurations(objective)

    def create_new_profile(self, profile_name: str) -> None:
        """Create a new profile using current configurations."""
        new_profile_path = self.base_config_path / profile_name
        if new_profile_path.exists():
            raise ValueError(f"Profile {profile_name} already exists")
        os.makedirs(new_profile_path)

        objectives = control._def.OBJECTIVES

        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(new_profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(new_profile_path)

        for objective in objectives:
            os.makedirs(new_profile_path / objective)
            if self.channel_manager:
                self.channel_manager.save_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.save_configurations(objective)

        self.available_profiles = self._get_available_profiles()
