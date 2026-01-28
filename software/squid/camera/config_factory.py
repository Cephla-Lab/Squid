"""
Camera configuration factory for multi-camera support.

This module generates per-camera CameraConfig instances from the camera registry,
allowing each camera to have its own configuration while sharing common settings.
"""

import logging
from typing import Dict, Optional

from control.models import CameraRegistryConfig
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
    """Generate per-camera configs from INI settings + optional registry.

    Serial numbers come from INI (MULTI_CAMERA_SNS). The camera registry
    (cameras.yaml) is optional and provides friendly names for the UI.

    All cameras use the same type (CAMERA_TYPE in INI).

    Behavior is controlled by INI settings:
    - USE_MULTI_CAMERA=False: Returns single camera config (ID 1)
    - USE_MULTI_CAMERA=True: Creates configs for each camera in MULTI_CAMERA_IDS
      using serial numbers from MULTI_CAMERA_SNS

    Args:
        camera_registry: Optional camera registry (from cameras.yaml) for names.
            Not required - serial numbers come from INI.
        base_config: Base camera configuration template (from _def.py).

    Returns:
        Dict mapping camera ID to CameraConfig.
        For single camera systems: {1: config}
        For multi-camera systems: {cam_id: config for each camera in MULTI_CAMERA_IDS}
    """
    import control._def

    # Check if multi-camera mode is enabled via INI settings
    use_multi_camera = getattr(control._def, "USE_MULTI_CAMERA", False)

    if not use_multi_camera:
        # Single-camera mode: return base config with default ID
        logger.debug(f"USE_MULTI_CAMERA=False, using single camera with ID {DEFAULT_SINGLE_CAMERA_ID}")
        return {DEFAULT_SINGLE_CAMERA_ID: base_config}

    # Get camera IDs and serial numbers from INI
    configured_ids = list(getattr(control._def, "MULTI_CAMERA_IDS", [1]))
    camera_sns = getattr(control._def, "MULTI_CAMERA_SNS", {})

    # Convert string keys to int (INI parser may give us strings)
    camera_sns = {int(k): v for k, v in camera_sns.items()}

    logger.debug(f"Multi-camera mode enabled, IDs: {configured_ids}, SNs: {camera_sns}")

    # Validate: MULTI_CAMERA_SNS must not be empty
    if not camera_sns:
        example_sns = ", ".join(f'"{cid}": "YOUR_SN_{cid}"' for cid in configured_ids[:2])
        logger.error(
            f"MULTI_CAMERA_SNS is empty but USE_MULTI_CAMERA=True. "
            f"Either add serial numbers: multi_camera_sns = {{{example_sns}}}, "
            f"or disable multi-camera: use_multi_camera = False"
        )
        raise ValueError(
            f"MULTI_CAMERA_SNS is empty. When USE_MULTI_CAMERA=True, you must provide "
            f"serial numbers for each camera ID. Example: multi_camera_sns = {{{example_sns}}}"
        )

    # Validate: each camera ID must have a serial number in INI
    missing_sns = [cid for cid in configured_ids if cid not in camera_sns]
    if missing_sns:
        logger.error(
            f"MULTI_CAMERA_SNS missing serial numbers for camera IDs: {missing_sns}. "
            f'Add them to the INI file, e.g., multi_camera_sns = {{"{missing_sns[0]}": "YOUR_SN"}}'
        )
        raise ValueError(
            f"Missing serial numbers in MULTI_CAMERA_SNS for camera IDs: {missing_sns}. "
            f"All cameras in MULTI_CAMERA_IDS must have a serial number in MULTI_CAMERA_SNS."
        )

    configs: Dict[int, CameraConfig] = {}

    for camera_id in configured_ids:
        serial_number = camera_sns[camera_id]

        # Create a copy of the base config for this camera
        cam_config = base_config.model_copy(deep=True)
        cam_config.serial_number = serial_number

        # Get friendly name from registry if available
        camera_name = f"Camera {camera_id}"
        if camera_registry:
            camera_def = camera_registry.get_camera_by_id(camera_id)
            if camera_def and camera_def.name:
                camera_name = camera_def.name

        configs[camera_id] = cam_config
        logger.debug(f"Created config for camera {camera_id} ('{camera_name}', SN: {serial_number})")

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
