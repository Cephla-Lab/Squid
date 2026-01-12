"""
Configuration migration utilities.

This module provides functions to migrate configuration files between schema versions.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_config_version(config: Dict[str, Any]) -> float:
    """
    Get the version number from a configuration dictionary.

    Args:
        config: Configuration dictionary

    Returns:
        Version number as float (e.g., 1.0, 1.1)
    """
    version = config.get("version", 1)
    return float(version)


def needs_migration(config: Dict[str, Any], target_version: float = 1.1) -> bool:
    """
    Check if a configuration needs migration to the target version.

    Args:
        config: Configuration dictionary
        target_version: Target version to migrate to

    Returns:
        True if migration is needed
    """
    current_version = get_config_version(config)
    return current_version < target_version


def migrate_channel_config_v1_to_v1_1(
    config: Dict[str, Any],
    default_camera: str = "Main Camera",
) -> Dict[str, Any]:
    """
    Migrate channel configuration from v1.0 to v1.1.

    Changes:
    - Adds empty channel_groups list (for GeneralChannelConfig)
    - Updates version to 1.1

    Note: This is a minimal migration that preserves backward compatibility.
    Full v1.1 features (camera field, flattened camera_settings, filter_wheel)
    require manual configuration or UI-assisted migration.

    Args:
        config: v1.0 configuration dictionary
        default_camera: Default camera name for channels (not applied in minimal migration)

    Returns:
        v1.1 configuration dictionary
    """
    if get_config_version(config) >= 1.1:
        logger.debug("Config already at v1.1 or higher, no migration needed")
        return config

    # Create a copy to avoid modifying the original
    migrated = dict(config)

    # Add channel_groups if not present (for general.yaml)
    if "channel_groups" not in migrated:
        migrated["channel_groups"] = []

    # Update version
    migrated["version"] = 1.1

    logger.info(f"Migrated channel config from v1.0 to v1.1")
    return migrated


def migrate_illumination_config_v1_to_v1_1(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate illumination channel configuration from v1.0 to v1.1.

    This is a simple version bump - the illumination config schema
    hasn't changed between versions.

    Args:
        config: v1.0 configuration dictionary

    Returns:
        v1.1 configuration dictionary
    """
    if get_config_version(config) >= 1.1:
        return config

    migrated = dict(config)
    migrated["version"] = 1.1

    logger.info("Migrated illumination config from v1.0 to v1.1")
    return migrated
