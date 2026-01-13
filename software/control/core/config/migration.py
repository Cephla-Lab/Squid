"""
Configuration migration utilities.

This module provides functions to migrate configuration files between schema versions.

v1.0 -> v1.1 changes:
- display_color moved from camera_settings to channel level
- camera_settings flattened from Dict[str, CameraSettings] to single CameraSettings
- camera field added for camera name reference
- emission_filter_wheel_position replaced with filter_wheel/filter_position
- ConfocalSettings: filter_wheel_id/emission_filter_wheel_position -> confocal_filter_wheel/confocal_filter_position
"""

import copy
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from control.models.filter_wheel_config import FilterWheelRegistryConfig

logger = logging.getLogger(__name__)


def _map_wheel_id_to_name(
    wheel_id: Any,
    filter_wheel_registry: Optional["FilterWheelRegistryConfig"],
    context: str = "",
) -> Optional[str]:
    """Map a wheel ID to its name using the registry.

    Args:
        wheel_id: The wheel ID to map (may be int, str, or other)
        filter_wheel_registry: Registry to look up wheel names
        context: Description for warning messages (e.g., "confocal", "override confocal")

    Returns:
        Wheel name if found, None otherwise
    """
    if wheel_id is None or filter_wheel_registry is None:
        return None

    try:
        wheel_id_int = int(wheel_id)
        wheel = filter_wheel_registry.get_wheel_by_id(wheel_id_int)
        return wheel.name if wheel else None
    except (ValueError, TypeError):
        context_msg = f" ({context})" if context else ""
        logger.warning(f"Invalid wheel_id '{wheel_id}'{context_msg} during migration, skipping wheel mapping")
        return None


def get_config_version(config: Dict[str, Any]) -> float:
    """
    Get the version number from a configuration dictionary.

    Args:
        config: Configuration dictionary

    Returns:
        Version number as float (e.g., 1.0, 1.1). Returns 1.0 for invalid input.
    """
    if config is None:
        logger.warning("Config is None, defaulting to version 1.0")
        return 1.0

    version = config.get("version", 1)

    try:
        return float(version)
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid version '{version}', defaulting to 1.0: {e}")
        return 1.0


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
    default_camera: Optional[str] = None,
    filter_wheel_registry: Optional["FilterWheelRegistryConfig"] = None,
) -> Dict[str, Any]:
    """
    Migrate channel configuration from v1.0 to v1.1.

    Changes:
    - display_color moved from camera_settings to channel level
    - camera_settings flattened from Dict to single object
    - camera field added for camera name reference
    - emission_filter_wheel_position -> filter_wheel/filter_position
    - ConfocalSettings filter_wheel_id/emission_filter_wheel_position ->
      confocal_filter_wheel/confocal_filter_position
    - channel_groups list added (for GeneralChannelConfig)

    Args:
        config: v1.0 configuration dictionary
        default_camera: Default camera name for channels (optional for single-camera systems)
        filter_wheel_registry: Optional registry to map filter wheel IDs to names

    Returns:
        v1.1 configuration dictionary
    """
    if get_config_version(config) >= 1.1:
        logger.debug("Config already at v1.1 or higher, no migration needed")
        return config

    # Create a deep copy to avoid modifying the original
    migrated = copy.deepcopy(config)

    # Migrate each channel
    for channel in migrated.get("channels", []):
        _migrate_channel_v1_to_v1_1(channel, default_camera, filter_wheel_registry)

    # Add channel_groups if not present (for general.yaml)
    if "channel_groups" not in migrated:
        migrated["channel_groups"] = []

    # Update version
    migrated["version"] = 1.1

    logger.info("Migrated channel config from v1.0 to v1.1")
    return migrated


def _migrate_channel_v1_to_v1_1(
    channel: Dict[str, Any],
    default_camera: Optional[str],
    filter_wheel_registry: Optional["FilterWheelRegistryConfig"],
) -> None:
    """
    Migrate a single channel from v1.0 to v1.1 schema (in-place).

    Args:
        channel: Channel dictionary to migrate (modified in place)
        default_camera: Default camera name
        filter_wheel_registry: Optional registry to map filter wheel IDs to names
    """
    # Extract camera_settings dict (v1.0: Dict[str, CameraSettings])
    old_camera_settings = channel.pop("camera_settings", {})

    # Get first camera's settings (single-camera assumption for v1.0)
    if isinstance(old_camera_settings, dict) and old_camera_settings:
        camera_id = next(iter(old_camera_settings.keys()))
        cam = old_camera_settings.get(camera_id, {})
    else:
        cam = old_camera_settings if isinstance(old_camera_settings, dict) else {}

    # Move display_color to channel level
    display_color = cam.pop("display_color", "#FFFFFF") if isinstance(cam, dict) else "#FFFFFF"
    channel["display_color"] = display_color

    # Add camera reference (optional for single-camera)
    channel["camera"] = default_camera

    # Flatten camera_settings to single object
    if isinstance(cam, dict):
        channel["camera_settings"] = {
            "exposure_time_ms": cam.get("exposure_time_ms", 20.0),
            "gain_mode": cam.get("gain_mode", 10.0),
            "pixel_format": cam.get("pixel_format"),
        }
    else:
        channel["camera_settings"] = {
            "exposure_time_ms": 20.0,
            "gain_mode": 10.0,
            "pixel_format": None,
        }

    # Convert emission_filter_wheel_position to filter_wheel/filter_position
    old_filter = channel.pop("emission_filter_wheel_position", None)
    if old_filter and isinstance(old_filter, dict):
        wheel_id = next(iter(old_filter.keys()), 1)
        position = old_filter.get(wheel_id, 1)
        channel["filter_wheel"] = _map_wheel_id_to_name(wheel_id, filter_wheel_registry, "channel")
        channel["filter_position"] = position
    else:
        channel["filter_wheel"] = None
        channel["filter_position"] = None

    # Migrate confocal_settings filter wheel fields
    confocal = channel.get("confocal_settings")
    if confocal and isinstance(confocal, dict):
        old_wheel_id = confocal.pop("filter_wheel_id", None)
        old_position = confocal.pop("emission_filter_wheel_position", None)
        confocal["confocal_filter_wheel"] = _map_wheel_id_to_name(old_wheel_id, filter_wheel_registry, "confocal")
        confocal["confocal_filter_position"] = old_position

    # Migrate confocal_override if present
    confocal_override = channel.get("confocal_override")
    if confocal_override and isinstance(confocal_override, dict):
        _migrate_confocal_override_v1_to_v1_1(confocal_override, filter_wheel_registry)


def _migrate_confocal_override_v1_to_v1_1(
    confocal_override: Dict[str, Any],
    filter_wheel_registry: Optional["FilterWheelRegistryConfig"],
) -> None:
    """Migrate confocal_override section from v1.0 to v1.1 schema (in-place).

    Args:
        confocal_override: Confocal override dictionary to migrate
        filter_wheel_registry: Optional registry to map filter wheel IDs to names
    """
    # Migrate override camera_settings
    override_cam_settings = confocal_override.get("camera_settings")
    if override_cam_settings and isinstance(override_cam_settings, dict):
        # Check if it's a Dict[str, CameraSettings] or already a single object
        first_key = next(iter(override_cam_settings.keys()), None)
        if first_key and isinstance(override_cam_settings.get(first_key), dict):
            # It's a Dict[str, CameraSettings] - flatten it
            cam_override = override_cam_settings.get(first_key, {})
            # Remove display_color if present (it's at channel level now)
            cam_override.pop("display_color", None)
            confocal_override["camera_settings"] = {
                "exposure_time_ms": cam_override.get("exposure_time_ms", 20.0),
                "gain_mode": cam_override.get("gain_mode", 10.0),
                "pixel_format": cam_override.get("pixel_format"),
            }

    # Migrate override confocal_settings
    override_confocal = confocal_override.get("confocal_settings")
    if override_confocal and isinstance(override_confocal, dict):
        old_wheel_id = override_confocal.pop("filter_wheel_id", None)
        old_position = override_confocal.pop("emission_filter_wheel_position", None)
        override_confocal["confocal_filter_wheel"] = _map_wheel_id_to_name(
            old_wheel_id, filter_wheel_registry, "override confocal"
        )
        override_confocal["confocal_filter_position"] = old_position


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

    migrated = copy.deepcopy(config)
    migrated["version"] = 1.1

    logger.info("Migrated illumination config from v1.0 to v1.1")
    return migrated
