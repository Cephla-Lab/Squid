"""
Utility functions for configuration management.

Pure functions that operate on config models without side effects.
"""

import shutil
from typing import List, TYPE_CHECKING

from control.models import (
    AcquisitionChannel,
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    merge_channel_configs,
    validate_illumination_references,
    get_illumination_channel_names,
)

if TYPE_CHECKING:
    from control.core.config.repository import ConfigRepository

# Re-export from models for convenience
__all__ = [
    # Re-exports from models
    "merge_channel_configs",
    "validate_illumination_references",
    "get_illumination_channel_names",
    # New utilities
    "apply_confocal_override",
    "copy_profile_configs",
    "get_effective_channels",
]


def apply_confocal_override(
    channels: List[AcquisitionChannel],
    confocal_mode: bool,
) -> List[AcquisitionChannel]:
    """
    Apply confocal overrides to a list of acquisition channels.

    If confocal_mode is False, returns channels unchanged.
    If confocal_mode is True, calls get_effective_settings() on each channel
    to apply any confocal_override settings.

    Args:
        channels: List of acquisition channels
        confocal_mode: Whether confocal mode is active

    Returns:
        List of channels with confocal overrides applied (if applicable)
    """
    if not confocal_mode:
        return channels
    return [ch.get_effective_settings(confocal_mode=True) for ch in channels]


def get_effective_channels(
    general: GeneralChannelConfig,
    objective: ObjectiveChannelConfig,
    confocal_mode: bool = False,
) -> List[AcquisitionChannel]:
    """
    Get the effective acquisition channels for a given objective and mode.

    This is a convenience function that combines merge_channel_configs()
    and apply_confocal_override() into a single call.

    Args:
        general: General channel configuration
        objective: Objective-specific channel configuration
        confocal_mode: Whether confocal mode is active

    Returns:
        List of merged and mode-adjusted acquisition channels
    """
    merged = merge_channel_configs(general, objective)
    return apply_confocal_override(merged, confocal_mode)


def copy_profile_configs(
    repo: "ConfigRepository",
    source_profile: str,
    dest_profile: str,
) -> None:
    """
    Copy all configuration files from source profile to destination profile.

    Copies both channel_configs/ and laser_af_configs/ directories.
    The destination profile must already exist (created via repo.create_profile()).

    Args:
        repo: ConfigRepository instance
        source_profile: Name of source profile
        dest_profile: Name of destination profile

    Raises:
        ValueError: If source or destination profile doesn't exist
    """
    if not repo.profile_exists(source_profile):
        raise ValueError(f"Source profile '{source_profile}' does not exist")
    if not repo.profile_exists(dest_profile):
        raise ValueError(f"Destination profile '{dest_profile}' does not exist")

    source_path = repo.user_profiles_path / source_profile
    dest_path = repo.user_profiles_path / dest_profile

    # Copy channel_configs
    source_channels = source_path / "channel_configs"
    dest_channels = dest_path / "channel_configs"
    if source_channels.exists():
        for yaml_file in source_channels.glob("*.yaml"):
            shutil.copy2(yaml_file, dest_channels / yaml_file.name)

    # Copy laser_af_configs
    source_laser_af = source_path / "laser_af_configs"
    dest_laser_af = dest_path / "laser_af_configs"
    if source_laser_af.exists():
        for yaml_file in source_laser_af.glob("*.yaml"):
            shutil.copy2(yaml_file, dest_laser_af / yaml_file.name)
