"""
Pydantic models for acquisition configuration.

This package contains models for:
- IlluminationChannelConfig: Hardware-level illumination channel definitions
- ConfocalConfig: Optional confocal unit configuration
- CameraMappingsConfig: Camera to dichroic/filter wheel bindings (legacy)
- CameraRegistryConfig: Camera name to serial number mapping
- FilterWheelRegistryConfig: Filter wheel definitions
- HardwareBindingsConfig: Camera to filter wheel bindings with source-qualified refs
- AcquisitionConfig: User-facing acquisition channel settings (general + objective-specific)
- ChannelGroup: Multi-camera channel grouping
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
from control.models.camera_registry import (
    CameraDefinition,
    CameraRegistryConfig,
)
from control.models.filter_wheel_config import (
    FilterWheelType,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
)
from control.models.hardware_bindings import (
    FilterWheelSource,
    FilterWheelReference,
    HardwareBindingsConfig,
    FILTER_WHEEL_SOURCE_CONFOCAL,
    FILTER_WHEEL_SOURCE_STANDALONE,
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
    # Channel Groups
    SynchronizationMode,
    ChannelGroupEntry,
    ChannelGroup,
    validate_channel_group,
)
from control.models.laser_af_config import LaserAFConfig

__all__ = [
    # Illumination
    "IlluminationType",
    "IlluminationChannel",
    "IlluminationChannelConfig",
    # Confocal
    "ConfocalConfig",
    # Camera (legacy)
    "CameraHardwareInfo",
    "CameraPropertyBindings",
    "CameraMappingsConfig",
    # Camera Registry
    "CameraDefinition",
    "CameraRegistryConfig",
    # Filter Wheel Registry
    "FilterWheelType",
    "FilterWheelDefinition",
    "FilterWheelRegistryConfig",
    # Hardware Bindings
    "FilterWheelSource",
    "FilterWheelReference",
    "HardwareBindingsConfig",
    "FILTER_WHEEL_SOURCE_CONFOCAL",
    "FILTER_WHEEL_SOURCE_STANDALONE",
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
    # Channel Groups
    "SynchronizationMode",
    "ChannelGroupEntry",
    "ChannelGroup",
    "validate_channel_group",
    # Laser AF
    "LaserAFConfig",
]
