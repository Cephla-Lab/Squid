"""
Default configuration generator.

Generates default acquisition configuration files when a user has no
existing configs. Uses illumination_channel_config.yaml as the source
for available channels and creates appropriate defaults.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from control.core.config import ConfigRepository
from control.models import (
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
from control.models.illumination_config import (
    DEFAULT_LED_COLOR,
    DEFAULT_WAVELENGTH_COLORS,
    IlluminationType,
)

logger = logging.getLogger(__name__)

# Default values for acquisition settings
DEFAULT_EXPOSURE_TIME_MS = 20.0
DEFAULT_GAIN_MODE = 10.0
DEFAULT_ILLUMINATION_INTENSITY = 20.0
DEFAULT_LED_ILLUMINATION_INTENSITY = 5.0  # Lower intensity for USB LED sources
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
    camera_id: str = "1",
) -> AcquisitionChannel:
    """
    Create an acquisition channel for general.yaml.

    general.yaml defines channel identity and shared settings:
    - illumination_channels: Which illumination channels to use
    - display_color: Color for visualization
    - z_offset_um: Z offset (shared across objectives)
    - emission_filter_wheel_position: Filter wheel position

    Default values are included for exposure, gain, and intensity but these
    are expected to be overridden by objective-specific files.

    Args:
        illumination_channel: The illumination channel to create from
        include_confocal: Whether to include confocal settings
        camera_id: Camera ID to use

    Returns:
        AcquisitionChannel for general.yaml
    """
    display_color = get_display_color_for_channel(illumination_channel)

    # general.yaml: display_color is the key field here
    # exposure/gain are included as defaults but will be overridden by objective files
    camera_settings = {
        camera_id: CameraSettings(
            display_color=display_color,
            exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,  # Default, overridden by objective
            gain_mode=DEFAULT_GAIN_MODE,  # Default, overridden by objective
        )
    }

    # general.yaml: illumination_channels and z_offset_um are the key fields
    # intensity is included as default but will be overridden by objective files
    illumination_settings = IlluminationSettings(
        illumination_channels=[illumination_channel.name],
        intensity={illumination_channel.name: DEFAULT_ILLUMINATION_INTENSITY},  # Default, overridden by objective
        z_offset_um=DEFAULT_Z_OFFSET_UM,
    )

    # Default emission filter wheel: wheel 1, position 1
    emission_filter_wheel_position = {1: 1}

    confocal_settings = None
    if include_confocal:
        confocal_settings = ConfocalSettings(
            filter_wheel_id=1,
            emission_filter_wheel_position=1,
        )

    return AcquisitionChannel(
        name=illumination_channel.name,
        illumination_settings=illumination_settings,
        camera_settings=camera_settings,
        emission_filter_wheel_position=emission_filter_wheel_position,
        confocal_settings=confocal_settings,
        confocal_override=None,  # No confocal_override in general.yaml
    )


def create_objective_acquisition_channel(
    illumination_channel: IlluminationChannel,
    include_confocal: bool = False,
    camera_id: str = "1",
) -> AcquisitionChannel:
    """
    Create an acquisition channel for objective-specific YAML files.

    Objective files define per-objective settings: intensity, exposure, gain,
    confocal iris settings. Does NOT include illumination_channels, display_color,
    z_offset_um (those are in general.yaml).

    Args:
        illumination_channel: The illumination channel to create from
        include_confocal: Whether to include confocal settings
        camera_id: Camera ID to use

    Returns:
        AcquisitionChannel for objective YAML
    """
    display_color = get_display_color_for_channel(illumination_channel)

    # Use lower intensity for transillumination (LED), higher for epi (lasers)
    if illumination_channel.type == IlluminationType.TRANSILLUMINATION:
        default_intensity = DEFAULT_LED_ILLUMINATION_INTENSITY
    else:
        default_intensity = DEFAULT_ILLUMINATION_INTENSITY

    # objective.yaml: exposure, gain, pixel_format (display_color is in general.yaml)
    camera_settings = {
        camera_id: CameraSettings(
            display_color=display_color,
            exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,
            gain_mode=DEFAULT_GAIN_MODE,
            pixel_format=None,  # Can be set per objective if needed
        )
    }

    # objective.yaml: intensity only, NO illumination_channels or z_offset_um
    illumination_settings = IlluminationSettings(
        illumination_channels=None,  # Not in objective files
        intensity={illumination_channel.name: default_intensity},
        z_offset_um=0.0,  # Placeholder, z_offset is in general.yaml
    )

    confocal_settings = None
    confocal_override = None

    if include_confocal:
        confocal_settings = ConfocalSettings(
            filter_wheel_id=1,
            emission_filter_wheel_position=1,
        )
        # Create confocal override with same default values
        confocal_override = AcquisitionChannelOverride(
            illumination_settings=IlluminationSettings(
                illumination_channels=None,
                intensity={illumination_channel.name: default_intensity},
                z_offset_um=0.0,  # Placeholder
            ),
            camera_settings={
                camera_id: CameraSettings(
                    display_color=display_color,
                    exposure_time_ms=DEFAULT_EXPOSURE_TIME_MS,
                    gain_mode=DEFAULT_GAIN_MODE,
                    pixel_format=None,
                )
            },
            confocal_settings=ConfocalSettings(
                filter_wheel_id=1,
                emission_filter_wheel_position=1,
            ),
        )

    return AcquisitionChannel(
        name=illumination_channel.name,
        illumination_settings=illumination_settings,
        camera_settings=camera_settings,
        emission_filter_wheel_position=None,  # Not in objective files
        confocal_settings=confocal_settings,
        confocal_override=confocal_override,
    )


def generate_general_config(
    illumination_config: IlluminationChannelConfig,
    include_confocal: bool = False,
    camera_id: str = "1",
) -> GeneralChannelConfig:
    """
    Generate a general.yaml configuration from illumination channels.

    general.yaml defines channel identity: illumination_channels, display_color,
    emission_filter_wheel_position, base confocal settings.

    Args:
        illumination_config: Available illumination channels
        include_confocal: Whether to include confocal settings
        camera_id: Camera ID to use

    Returns:
        GeneralChannelConfig with default channels
    """
    channels = []
    for ill_channel in illumination_config.channels:
        acq_channel = create_general_acquisition_channel(
            ill_channel, include_confocal=include_confocal, camera_id=camera_id
        )
        channels.append(acq_channel)

    return GeneralChannelConfig(version=1, channels=channels)


def generate_objective_config(
    illumination_config: IlluminationChannelConfig,
    include_confocal: bool = False,
    camera_id: str = "1",
) -> ObjectiveChannelConfig:
    """
    Generate an objective-specific configuration.

    Objective files define per-objective settings: intensity, exposure, gain,
    confocal iris settings, confocal_override. Does NOT include
    illumination_channels, emission_filter_wheel_position, or z_offset_um (those are in general.yaml).

    Args:
        illumination_config: Available illumination channels
        include_confocal: Whether to include confocal settings
        camera_id: Camera ID to use

    Returns:
        ObjectiveChannelConfig with default channels (no illumination_channels)
    """
    channels = []
    for ill_channel in illumination_config.channels:
        acq_channel = create_objective_acquisition_channel(
            ill_channel, include_confocal=include_confocal, camera_id=camera_id
        )
        channels.append(acq_channel)

    return ObjectiveChannelConfig(version=1, channels=channels)


def generate_default_configs(
    illumination_config: IlluminationChannelConfig,
    confocal_config: Optional[ConfocalConfig],
    objectives: Optional[List[str]] = None,
    camera_id: str = "1",
) -> Tuple[GeneralChannelConfig, Dict[str, ObjectiveChannelConfig]]:
    """
    Generate default acquisition configs for all objectives.

    Args:
        illumination_config: Available illumination channels
        confocal_config: Confocal configuration (None if no confocal)
        objectives: List of objectives to generate configs for (default: standard set)
        camera_id: Camera ID to use

    Returns:
        Tuple of (general_config, {objective: objective_config})
    """
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
    """
    Check if there are legacy configs (XML/JSON) that need migration.

    Legacy configs are in acquisition_configurations/{profile}/{objective}/ with:
    - channel_configurations.xml (required)
    - laser_af_settings.json (optional)

    If these exist, we should NOT generate default configs - migration should run first.

    Args:
        profile: Profile name to check
        base_path: Base path to software directory (auto-detected if None)

    Returns:
        True if legacy configs exist that need migration
    """
    if base_path is None:
        base_path = Path(__file__).parent.parent

    legacy_path = base_path / "acquisition_configurations" / profile

    if not legacy_path.exists():
        return False

    # Check for channel_configurations.xml in any subdirectory (objective folders)
    for item in legacy_path.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            if (item / "channel_configurations.xml").exists():
                return True

    return False


def ensure_default_configs(
    config_repo: ConfigRepository,
    profile: str,
    objectives: Optional[List[str]] = None,
) -> bool:
    """
    Ensure a profile has default configurations.

    If the profile doesn't have a general.yaml, generates default configs
    for all objectives based on the illumination_channel_config.

    NOTE: This function will NOT generate defaults if there are legacy
    configs (XML/JSON) that need migration. The migration script should run first.

    Args:
        config_repo: ConfigRepository instance
        profile: Profile name
        objectives: List of objectives (default: standard set)

    Returns:
        True if configs were generated, False if they already existed or migration is pending
    """
    # Check if configs already exist
    if config_repo.profile_has_configs(profile):
        logger.debug(f"Profile '{profile}' already has configs")
        return False

    # Check if there are legacy configs to migrate - don't generate defaults if so
    if has_legacy_configs_to_migrate(profile):
        logger.info(
            f"Profile '{profile}' has legacy configs pending migration. "
            "Skipping default generation - run migration first."
        )
        return False

    # Load illumination config
    illumination_config = config_repo.get_illumination_config()
    if illumination_config is None:
        logger.error("Cannot generate defaults: illumination_channel_config.yaml not found")
        raise FileNotFoundError("illumination_channel_config.yaml is required to generate default configs")

    # Check for confocal
    confocal_config = config_repo.get_confocal_config()

    # Generate configs
    logger.info(f"Generating default configs for profile '{profile}'")
    general_config, objective_configs = generate_default_configs(illumination_config, confocal_config, objectives)

    # Ensure directories exist
    config_repo.ensure_profile_directories(profile)

    # Save configs
    config_repo.save_general_config(profile, general_config)
    for objective, obj_config in objective_configs.items():
        config_repo.save_objective_config(profile, objective, obj_config)

    logger.info(
        f"Generated default configs for profile '{profile}': "
        f"general.yaml + {len(objective_configs)} objective files"
    )
    return True
