"""
Default configuration generator.

Generates default acquisition configuration files when a user has no
existing configs. Uses illumination_channel_config.yaml as the source
for available channels and creates appropriate defaults.

Ported from upstream with import path adjustments.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from squid.core.config.models import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraSettings,
    ConfocalConfig,
    ConfocalSettings,
    GeneralChannelConfig,
    IlluminationChannel,
    IlluminationChannelConfig,
    IlluminationSettings,
    ObjectiveChannelConfig,
)
from squid.core.config.models.illumination_config import (
    DEFAULT_LED_COLOR,
    DEFAULT_WAVELENGTH_COLORS,
    IlluminationType,
)

logger = logging.getLogger(__name__)

# Default values for acquisition settings
DEFAULT_EXPOSURE_TIME_MS = 20.0
DEFAULT_GAIN_MODE = 10.0
DEFAULT_ILLUMINATION_INTENSITY = 20.0
DEFAULT_LED_ILLUMINATION_INTENSITY = 5.0
DEFAULT_Z_OFFSET_UM = 0.0

# Standard objectives
DEFAULT_OBJECTIVES = ["2x", "4x", "10x", "20x", "40x", "50x", "60x"]


def get_display_color_for_channel(channel: IlluminationChannel) -> str:
    """Get the display color for an illumination channel based on wavelength."""
    if channel.wavelength_nm is not None:
        return DEFAULT_WAVELENGTH_COLORS.get(channel.wavelength_nm, DEFAULT_LED_COLOR)
    return DEFAULT_LED_COLOR


def create_general_acquisition_channel(
    illumination_channel: IlluminationChannel,
    include_confocal: bool = False,
    camera_id: Optional[int] = None,
) -> AcquisitionChannel:
    """Create an acquisition channel for general.yaml (v1.0 schema)."""
    display_color = get_display_color_for_channel(illumination_channel)

    camera_settings = CameraSettings(
        exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,
        gain_mode=DEFAULT_GAIN_MODE,
    )

    illumination_settings = IlluminationSettings(
        illumination_channel=illumination_channel.name,
        intensity=DEFAULT_ILLUMINATION_INTENSITY,
    )

    return AcquisitionChannel(
        name=illumination_channel.name,
        display_color=display_color,
        camera=camera_id,
        camera_settings=camera_settings,
        filter_wheel=None,
        filter_position=1,
        z_offset_um=DEFAULT_Z_OFFSET_UM,
        illumination_settings=illumination_settings,
        confocal_override=None,
    )


def create_objective_acquisition_channel(
    illumination_channel: IlluminationChannel,
    include_confocal: bool = False,
    camera_id: Optional[int] = None,
) -> AcquisitionChannel:
    """Create an acquisition channel for objective-specific YAML files."""
    display_color = get_display_color_for_channel(illumination_channel)

    if illumination_channel.type == IlluminationType.TRANSILLUMINATION:
        default_intensity = DEFAULT_LED_ILLUMINATION_INTENSITY
    else:
        default_intensity = DEFAULT_ILLUMINATION_INTENSITY

    camera_settings = CameraSettings(
        exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,
        gain_mode=DEFAULT_GAIN_MODE,
        pixel_format=None,
    )

    illumination_settings = IlluminationSettings(
        illumination_channel=None,
        intensity=default_intensity,
    )

    confocal_override = None

    if include_confocal:
        confocal_override = AcquisitionChannelOverride(
            illumination_settings=IlluminationSettings(
                illumination_channel=None,
                intensity=default_intensity,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,
                gain_mode=DEFAULT_GAIN_MODE,
                pixel_format=None,
            ),
            confocal_settings=ConfocalSettings(
                illumination_iris=None,
                emission_iris=None,
            ),
        )

    return AcquisitionChannel(
        name=illumination_channel.name,
        display_color=display_color,
        camera=camera_id,
        camera_settings=camera_settings,
        filter_wheel=None,
        filter_position=None,
        z_offset_um=DEFAULT_Z_OFFSET_UM,
        illumination_settings=illumination_settings,
        confocal_override=confocal_override,
    )


def generate_general_config(
    illumination_config: IlluminationChannelConfig,
    include_confocal: bool = False,
    camera_id: Optional[int] = None,
) -> GeneralChannelConfig:
    """Generate a general.yaml configuration from illumination channels."""
    channels = []
    for ill_channel in illumination_config.channels:
        acq_channel = create_general_acquisition_channel(
            ill_channel, include_confocal=include_confocal, camera_id=camera_id
        )
        channels.append(acq_channel)

    return GeneralChannelConfig(version=1.0, channels=channels, channel_groups=[])


def generate_objective_config(
    illumination_config: IlluminationChannelConfig,
    include_confocal: bool = False,
    camera_id: Optional[int] = None,
) -> ObjectiveChannelConfig:
    """Generate an objective-specific configuration."""
    channels = []
    for ill_channel in illumination_config.channels:
        acq_channel = create_objective_acquisition_channel(
            ill_channel, include_confocal=include_confocal, camera_id=camera_id
        )
        channels.append(acq_channel)

    return ObjectiveChannelConfig(version=1.0, channels=channels)


def generate_default_configs(
    illumination_config: IlluminationChannelConfig,
    confocal_config: Optional[ConfocalConfig],
    objectives: Optional[List[str]] = None,
    camera_id: Optional[int] = None,
) -> Tuple[GeneralChannelConfig, Dict[str, ObjectiveChannelConfig]]:
    """Generate default acquisition configs for all objectives."""
    if objectives is None:
        objectives = DEFAULT_OBJECTIVES

    include_confocal = confocal_config is not None

    general_config = generate_general_config(
        illumination_config, include_confocal=include_confocal, camera_id=camera_id
    )

    objective_configs = {}
    for objective in objectives:
        objective_configs[objective] = generate_objective_config(
            illumination_config, include_confocal=include_confocal, camera_id=camera_id
        )

    return general_config, objective_configs


def has_legacy_configs_to_migrate(profile: str, base_path: Optional[Path] = None) -> bool:
    """Check if there are legacy configs (XML/JSON) that need migration."""
    if base_path is None:
        # software/ directory
        base_path = Path(__file__).parent.parent.parent.parent.parent

    legacy_path = base_path / "acquisition_configurations" / profile

    if not legacy_path.exists():
        return False

    for item in legacy_path.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            if (item / "channel_configurations.xml").exists():
                return True

    return False


def ensure_default_configs(
    config_repo: "ConfigRepository",
    profile: str,
    objectives: Optional[List[str]] = None,
) -> bool:
    """
    Ensure a profile has default configurations.

    Will NOT generate defaults if there are legacy configs pending migration.
    """
    from squid.core.config.repository import ConfigRepository

    if config_repo.profile_has_configs(profile):
        logger.debug(f"Profile '{profile}' already has configs")
        return False

    if has_legacy_configs_to_migrate(profile):
        logger.info(
            f"Profile '{profile}' has legacy configs pending migration. "
            "Skipping default generation - run migration first."
        )
        return False

    illumination_config = config_repo.get_illumination_config()
    if illumination_config is None:
        logger.error("Cannot generate defaults: illumination_channel_config.yaml not found")
        raise FileNotFoundError("illumination_channel_config.yaml is required to generate default configs")

    confocal_config = config_repo.get_confocal_config()

    logger.info(f"Generating default configs for profile '{profile}'")
    general_config, objective_configs = generate_default_configs(illumination_config, confocal_config, objectives)

    config_repo.ensure_profile_directories(profile)

    config_repo.save_general_config(profile, general_config)
    for objective, obj_config in objective_configs.items():
        config_repo.save_objective_config(profile, objective, obj_config)

    logger.info(
        f"Generated default configs for profile '{profile}': "
        f"general.yaml + {len(objective_configs)} objective files"
    )
    return True
