"""
Pydantic models for acquisition configuration (upstream YAML system).

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

from squid.core.config.models.illumination_config import (
    IlluminationType,
    IlluminationChannel,
    IlluminationChannelConfig,
)
from squid.core.config.models.confocal_config import ConfocalConfig
from squid.core.config.models.camera_config import (
    CameraHardwareInfo,
    CameraPropertyBindings,
    CameraMappingsConfig,
)
from squid.core.config.models.camera_registry import (
    CameraDefinition,
    CameraRegistryConfig,
)
from squid.core.config.models.filter_wheel_config import (
    FilterWheelType,
    FilterWheelDefinition,
    FilterWheelRegistryConfig,
)
from squid.core.config.models.hardware_bindings import (
    FilterWheelSource,
    FilterWheelReference,
    HardwareBindingsConfig,
    FILTER_WHEEL_SOURCE_CONFOCAL,
    FILTER_WHEEL_SOURCE_STANDALONE,
)
from squid.core.config.models.acquisition_config import (
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
from squid.core.config.models.laser_af_config import LaserAFConfig

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
