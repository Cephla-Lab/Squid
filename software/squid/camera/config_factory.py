"""
Camera configuration factory for multi-camera support.

This module generates per-camera CameraConfig instances from the camera registry,
allowing each camera to have its own configuration while sharing common settings.
"""

import logging
from typing import Dict, Optional

from control.models import CameraRegistryConfig, CameraDefinition
from squid.config import CameraConfig

logger = logging.getLogger(__name__)

# Default camera ID for single-camera systems or fallback scenarios.
# This is used when no camera registry is configured, maintaining backward
# compatibility with single-camera workflows.
DEFAULT_SINGLE_CAMERA_ID = 1


def create_camera_configs(
    camera_registry: Optional[CameraRegistryConfig],
    base_config: CameraConfig,
) -> Dict[int, CameraConfig]:
    """Generate per-camera configs from registry + base template.

    For each camera in the registry, creates a copy of the base config with
    the camera's serial number. This allows each camera to have its own
    configuration while sharing common settings.

    Args:
        camera_registry: Camera registry configuration (from cameras.yaml).
            If None or empty, returns a single camera config with ID 1.
        base_config: Base camera configuration template (from _def.py).

    Returns:
        Dict mapping camera ID to CameraConfig.
        For single camera systems: {1: config}
        For multi-camera systems: {cam.id: config for each camera}
    """
    if not camera_registry or not camera_registry.cameras:
        # Single-camera system: return base config with default ID
        logger.debug(f"No camera registry, using single camera with ID {DEFAULT_SINGLE_CAMERA_ID}")
        return {DEFAULT_SINGLE_CAMERA_ID: base_config}

    configs: Dict[int, CameraConfig] = {}

    for camera_def in camera_registry.cameras:
        # Create a copy of the base config for this camera
        cam_config = base_config.model_copy(deep=True)

        # Override serial number from registry
        cam_config.serial_number = camera_def.serial_number

        # Use camera ID from registry (guaranteed to be set for multi-camera)
        camera_id = camera_def.id
        if camera_id is None:
            # Shouldn't happen due to registry validation, but handle gracefully
            logger.warning(
                f"Camera '{camera_def.name}' has no ID, skipping. " "This indicates a registry validation bug."
            )
            continue

        configs[camera_id] = cam_config
        logger.debug(f"Created config for camera {camera_id} ('{camera_def.name}', " f"SN: {camera_def.serial_number})")

    if not configs:
        # Fallback if all cameras were skipped
        logger.warning(f"No valid camera configs created, using base config with ID {DEFAULT_SINGLE_CAMERA_ID}")
        return {DEFAULT_SINGLE_CAMERA_ID: base_config}

    logger.info(f"Created {len(configs)} camera configurations: IDs {sorted(configs.keys())}")
    return configs


def get_primary_camera_id(camera_ids: list[int]) -> int:
    """Get the primary camera ID from a list of camera IDs.

    The primary camera is the one with the lowest ID, which is used for
    backward compatibility with single-camera code paths.

    Args:
        camera_ids: List of camera IDs.

    Returns:
        The lowest camera ID.

    Raises:
        ValueError: If camera_ids is empty.
    """
    if not camera_ids:
        raise ValueError("No camera IDs provided")
    return min(camera_ids)
