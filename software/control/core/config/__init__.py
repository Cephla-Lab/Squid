"""
Configuration management for Squid microscope.

This module provides:
- ConfigRepository: Centralized config I/O and caching
- Utility functions for config manipulation

Example usage:
    from control.core.config import ConfigRepository, get_effective_channels

    repo = ConfigRepository()
    repo.set_profile("default")

    general = repo.get_general_config()
    objective = repo.get_objective_config("20x")

    # Get channels with confocal overrides applied
    channels = get_effective_channels(general, objective, confocal_mode=True)
"""

from control.core.config.repository import ConfigRepository
from control.core.config.utils import (
    # Re-exports from models
    merge_channel_configs,
    validate_illumination_references,
    get_illumination_channel_names,
    # Utilities
    apply_confocal_override,
    copy_profile_configs,
    get_effective_channels,
)

__all__ = [
    "ConfigRepository",
    # Re-exports from models
    "merge_channel_configs",
    "validate_illumination_references",
    "get_illumination_channel_names",
    # Utilities
    "apply_confocal_override",
    "copy_profile_configs",
    "get_effective_channels",
]
