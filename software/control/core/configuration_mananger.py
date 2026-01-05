import os
from pathlib import Path
from typing import List, Optional

from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.config_loader import ConfigLoader
from control.default_config_generator import ensure_default_configs
import control._def
import squid.logging

log = squid.logging.get_logger(__name__)


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
        """Get all available user profile names in the base config path.

        Creates default profile structure if no profiles exist. Uses new YAML-based
        directory structure under user_profiles/.
        """
        if not self.base_config_path.exists():
            os.makedirs(self.base_config_path)

        # Get list of profile directories (exclude hidden files like .migration_complete)
        profiles = [d.name for d in self.base_config_path.iterdir() if d.is_dir() and not d.name.startswith(".")]

        # Create default profile if no profiles exist
        if not profiles:
            default_profile = self.base_config_path / "default_profile"
            os.makedirs(default_profile / "channel_configs", exist_ok=True)
            os.makedirs(default_profile / "laser_af_configs", exist_ok=True)
            profiles = ["default_profile"]

        return profiles

    def _get_available_objectives(self, profile_path: Path) -> List[str]:
        """Get all available objective names in a profile.

        Looks for YAML files in channel_configs/ directory.
        """
        channel_configs_path = profile_path / "channel_configs"
        if not channel_configs_path.exists():
            return []
        objectives = []
        for f in channel_configs_path.iterdir():
            if f.suffix == ".yaml" and f.stem != "general":
                objectives.append(f.stem)
        # If no objective configs exist yet, return default objectives
        if not objectives:
            return list(control._def.OBJECTIVES)
        return objectives

    def load_profile(self, profile_name: str) -> None:
        """Load all configurations from a specific profile."""
        profile_path = self.base_config_path / profile_name
        if not profile_path.exists():
            raise ValueError(f"Profile {profile_name} does not exist")

        # Ensure default configs exist for this profile
        try:
            config_loader = ConfigLoader()
            objectives = list(control._def.OBJECTIVES) if hasattr(control._def, "OBJECTIVES") else None
            if ensure_default_configs(config_loader, profile_name, objectives):
                log.info(f"Generated default configs for profile '{profile_name}'")
        except Exception as e:
            log.warning(f"Could not generate default configs: {e}")

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
        """Create a new profile using current configurations.

        Uses new YAML-based directory structure:
        user_profiles/{profile}/channel_configs/{objective}.yaml
        user_profiles/{profile}/laser_af_configs/{objective}.yaml
        """
        new_profile_path = self.base_config_path / profile_name
        if new_profile_path.exists():
            raise ValueError(f"Profile {profile_name} already exists")

        # Create new directory structure
        os.makedirs(new_profile_path / "channel_configs", exist_ok=True)
        os.makedirs(new_profile_path / "laser_af_configs", exist_ok=True)

        objectives = control._def.OBJECTIVES

        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(new_profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(new_profile_path)

        for objective in objectives:
            if self.channel_manager:
                self.channel_manager.save_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.save_configurations(objective)

        self.available_profiles = self._get_available_profiles()
