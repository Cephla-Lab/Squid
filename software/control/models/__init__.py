"""
Pydantic models for acquisition configuration.

This package contains models for:
- IlluminationChannelConfig: Hardware-level illumination channel definitions
- ConfocalConfig: Optional confocal unit configuration
- CameraMappingsConfig: Camera to dichroic/filter wheel bindings
- AcquisitionConfig: User-facing acquisition channel settings (general + objective-specific)
- LaserAFConfig: Laser autofocus configuration
"""

from control.models.illumination_config import (
    IlluminationType,
    IlluminationChannel,
    IlluminationChannelConfig,
)
from control.models.confocal_config import ConfocalConfig
from control.models.camera_config import (
    CameraHardwareInfo,
    CameraPropertyBindings,
    CameraMappingsConfig,
)
from control.models.acquisition_config import (
    CameraSettings,
    ConfocalSettings,
    IlluminationSettings,
    AcquisitionChannel,
    AcquisitionChannelOverride,
    GeneralChannelConfig,
    ObjectiveChannelConfig,
    AcquisitionOutputConfig,
    merge_channel_configs,
    validate_illumination_references,
    get_illumination_channel_names,
)
from control.models.laser_af_config import LaserAFConfig

__all__ = [
    # Illumination
    "IlluminationType",
    "IlluminationChannel",
    "IlluminationChannelConfig",
    # Confocal
    "ConfocalConfig",
    # Camera
    "CameraHardwareInfo",
    "CameraPropertyBindings",
    "CameraMappingsConfig",
    # Acquisition
    "CameraSettings",
    "ConfocalSettings",
    "IlluminationSettings",
    "AcquisitionChannel",
    "AcquisitionChannelOverride",
    "GeneralChannelConfig",
    "ObjectiveChannelConfig",
    "AcquisitionOutputConfig",
    "merge_channel_configs",
    "validate_illumination_references",
    "get_illumination_channel_names",
    # Laser AF
    "LaserAFConfig",
]
