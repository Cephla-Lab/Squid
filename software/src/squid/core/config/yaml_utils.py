"""
Utility functions for configuration management.

Pure functions that operate on config models without side effects.

Ported from upstream commit 13eff115 (control.core.config.utils).
"""

import shutil
from typing import List, TYPE_CHECKING

from squid.core.config.models import (
    AcquisitionChannel,
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    merge_channel_configs,
    validate_illumination_references,
    get_illumination_channel_names,
)

if TYPE_CHECKING:
    from squid.core.config.repository import ConfigRepository

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
    If confocal_mode is True, calls get_effective_settings() on each channel.
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

    Combines merge_channel_configs() and apply_confocal_override().
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
    """
    if not repo.profile_exists(source_profile):
        raise ValueError(f"Source profile '{source_profile}' does not exist")
    if not repo.profile_exists(dest_profile):
        raise ValueError(f"Destination profile '{dest_profile}' does not exist")

    source_path = repo.user_profiles_path / source_profile
    dest_path = repo.user_profiles_path / dest_profile

    for subdir in ["channel_configs", "laser_af_configs"]:
        source_dir = source_path / subdir
        dest_dir = dest_path / subdir
        if source_dir.exists():
            for yaml_file in source_dir.glob("*.yaml"):
                shutil.copy2(yaml_file, dest_dir / yaml_file.name)
