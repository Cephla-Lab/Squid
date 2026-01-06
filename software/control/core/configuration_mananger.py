import os
from pathlib import Path
from typing import List, Optional

from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.core.config import ConfigRepository
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
            config_repo = ConfigRepository()
            objectives = list(control._def.OBJECTIVES) if hasattr(control._def, "OBJECTIVES") else None
            if ensure_default_configs(config_repo, profile_name, objectives):
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
        """Create a new profile by copying all configs from the current profile.

        Uses new YAML-based directory structure:
        user_profiles/{profile}/channel_configs/{objective}.yaml
        user_profiles/{profile}/laser_af_configs/{objective}.yaml
        """
        import shutil

        new_profile_path = self.base_config_path / profile_name
        if new_profile_path.exists():
            raise ValueError(f"Profile {profile_name} already exists")

        current_profile_path = self.base_config_path / self.current_profile

        # Create new directory structure
        os.makedirs(new_profile_path / "channel_configs", exist_ok=True)
        os.makedirs(new_profile_path / "laser_af_configs", exist_ok=True)

        # Copy all YAML files from current profile to new profile
        # This preserves ALL configs, including those not currently loaded in memory
        source_channel_configs = current_profile_path / "channel_configs"
        dest_channel_configs = new_profile_path / "channel_configs"
        if source_channel_configs.exists():
            for yaml_file in source_channel_configs.glob("*.yaml"):
                shutil.copy2(yaml_file, dest_channel_configs / yaml_file.name)

        source_laser_af = current_profile_path / "laser_af_configs"
        dest_laser_af = new_profile_path / "laser_af_configs"
        if source_laser_af.exists():
            for yaml_file in source_laser_af.glob("*.yaml"):
                shutil.copy2(yaml_file, dest_laser_af / yaml_file.name)

        # Switch to the new profile
        self.current_profile = profile_name
        if self.channel_manager:
            self.channel_manager.set_profile_path(new_profile_path)
        if self.laser_af_manager:
            self.laser_af_manager.set_profile_path(new_profile_path)

        # Load configurations for all available objectives
        objectives = control._def.OBJECTIVES
        for objective in objectives:
            if self.channel_manager:
                self.channel_manager.load_configurations(objective)
            if self.laser_af_manager:
                self.laser_af_manager.load_configurations(objective)

        self.available_profiles = self._get_available_profiles()
