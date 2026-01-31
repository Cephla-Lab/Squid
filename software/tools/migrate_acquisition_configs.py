#!/usr/bin/env python3
"""
Migrate legacy acquisition configurations to new YAML format.

NOTE: This migration script only works for software versions before v0.5.8.
For newer versions, the configuration format is already YAML-based.

This script converts:
- channel_definitions.default.json -> machine_configs/illumination_channel_config.yaml
- {profile}/{obj}/widefield_configurations.xml -> user_profiles/{profile}/channel_configs/{obj}.yaml
- {profile}/{obj}/confocal_configurations.xml -> confocal_override in {obj}.yaml
- {profile}/{obj}/laser_af_settings.json -> user_profiles/{profile}/laser_af_configs/{obj}.yaml

Channel name mapping logic:
- The old channel name from XML is preserved as the acquisition channel "name"
- For fluorescence channels (name contains "Fluorescence" or wavelength pattern like "488 nm"),
  the script extracts the wavelength and finds the matching illumination channel
  in illumination_channel_config.yaml by wavelength_nm field
- The matched illumination channel name is used in "illumination_channel" field

Usage:
    python tools/migrate_acquisition_configs.py [options]

Options:
    --source PATH       Source directory (default: acquisition_configurations/)
    --machine-config    Also generate machine_configs/ from channel_definitions
    --backup            Create backup before migration (default: True)
    --dry-run           Show what would be migrated without making changes
    --force             Overwrite existing files
    --profile NAME      Migrate only a specific profile
"""

import argparse
import json
import logging
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from control.models import (
    AcquisitionChannel,
    AcquisitionChannelOverride,
    CameraSettings,
    ConfocalSettings,
    GeneralChannelConfig,
    IlluminationChannel,
    IlluminationChannelConfig,
    IlluminationSettings,
    LaserAFConfig,
    ObjectiveChannelConfig,
)
from control.models.illumination_config import IlluminationType

# Use squid logging when imported as module, standalone basicConfig when run directly
try:
    import squid.logging

    logger = squid.logging.get_logger("migrate_acquisition_configs")
except ImportError:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logger = logging.getLogger(__name__)


def extract_wavelength_from_name(channel_name: str) -> Optional[int]:
    """
    Extract wavelength from a channel name.

    Looks for patterns like:
    - "Fluorescence 488 nm"
    - "488 nm"
    - "Fluorescence 488nm Ex"
    - "488nm"

    Returns the wavelength as int, or None if not found.
    """
    # Pattern: number followed by optional space and "nm"
    match = re.search(r"(\d{3,4})\s*nm", channel_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def find_illumination_channel_by_wavelength(
    illumination_config: IlluminationChannelConfig,
    wavelength: int,
) -> Optional[str]:
    """
    Find an illumination channel by its wavelength.

    Returns the illumination channel name if found, None otherwise.
    """
    for channel in illumination_config.channels:
        if channel.wavelength_nm == wavelength:
            return channel.name
    return None


def get_illumination_channel_name(
    xml_channel_name: str,
    illumination_source: int,
    illumination_config: IlluminationChannelConfig,
) -> str:
    """
    Determine the illumination channel name to use in illumination_channel field.

    Logic:
    1. If the channel name contains "Fluorescence" or a wavelength pattern,
       extract the wavelength and find matching illumination channel by wavelength_nm
    2. Otherwise, try to find by illumination source code
    3. Fall back to the XML channel name

    Args:
        xml_channel_name: Original channel name from XML
        illumination_source: Illumination source code from XML
        illumination_config: Illumination channel configuration

    Returns:
        The illumination channel name to use
    """
    # Try to extract wavelength from channel name
    wavelength = extract_wavelength_from_name(xml_channel_name)
    if wavelength is not None:
        ill_name = find_illumination_channel_by_wavelength(illumination_config, wavelength)
        if ill_name:
            return ill_name

    # Try to find by source code
    ill_channel = illumination_config.get_channel_by_source_code(illumination_source)
    if ill_channel:
        return ill_channel.name

    # Fall back to channel name lookup
    ill_channel = illumination_config.get_channel_by_name(xml_channel_name)
    if ill_channel:
        return ill_channel.name

    # Last resort: use the XML channel name as-is
    logger.warning(f"Could not find illumination channel for '{xml_channel_name}', using name as-is")
    return xml_channel_name


def int_to_hex_color(color_int: int) -> str:
    """Convert integer color value to hex string."""
    # Color int is typically BGR or RGB packed
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def parse_xml_config(xml_path: Path) -> List[Dict[str, Any]]:
    """
    Parse a legacy XML configuration file.

    Returns a list of channel configurations as dicts.
    """
    if not xml_path.exists():
        return []

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML {xml_path}: {e}")
        return []

    channels = []
    for mode in root.findall("mode"):
        channel = {
            "id": mode.get("ID"),
            "name": mode.get("Name"),
            "exposure_time_ms": float(mode.get("ExposureTime", 20)),
            "analog_gain": float(mode.get("AnalogGain", 0)),
            "illumination_source": int(mode.get("IlluminationSource", 0)),
            "illumination_intensity": float(mode.get("IlluminationIntensity", 20)),
            "z_offset": float(mode.get("ZOffset", 0)),
            "emission_filter_position": int(mode.get("EmissionFilterPosition", 1)),
            "camera_sn": mode.get("CameraSN", ""),
            "selected": mode.get("Selected", "false").lower() == "true",
            "color_int": int(mode.text) if mode.text else 16777215,
        }
        channel["display_color"] = int_to_hex_color(channel["color_int"])
        channels.append(channel)

    return channels


def load_channel_definitions_json(json_path: Path) -> Optional[Dict[str, Any]]:
    """Load channel_definitions.default.json file."""
    if not json_path.exists():
        return None

    try:
        with open(json_path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON file {json_path}: {e}")
        return None


def convert_channel_definitions_to_illumination_config(channel_defs: Dict[str, Any]) -> IlluminationChannelConfig:
    """
    Convert channel_definitions.default.json to IlluminationChannelConfig.

    Maps the numeric_channel_mapping to determine illumination source codes.
    """
    numeric_mapping = channel_defs.get("numeric_channel_mapping", {})

    # Build reverse mapping: illumination_source -> (wavelength, numeric_channel)
    source_to_wavelength = {}
    for num_ch, mapping in numeric_mapping.items():
        source_code = mapping.get("illumination_source")
        wavelength = mapping.get("ex_wavelength")
        if source_code is not None:
            source_to_wavelength[source_code] = wavelength

    channels = []
    for ch_def in channel_defs.get("channels", []):
        ch_type = ch_def.get("type", "led_matrix")
        is_fluorescence = ch_type == "fluorescence"

        # Determine source code and wavelength
        if is_fluorescence:
            numeric_ch = ch_def.get("numeric_channel")
            if numeric_ch and str(numeric_ch) in numeric_mapping:
                mapping = numeric_mapping[str(numeric_ch)]
                source_code = mapping.get("illumination_source", 0)
                wavelength = mapping.get("ex_wavelength")
            else:
                source_code = ch_def.get("illumination_source", 0)
                wavelength = ch_def.get("ex_wavelength")
        else:
            source_code = ch_def.get("illumination_source", 0)
            wavelength = None

        # Determine controller port from source code
        # D1-D8 for lasers (source codes 11-18), USB for LED matrix
        if source_code >= 11 and source_code <= 18:
            controller_port = f"D{source_code - 10}"
        elif source_code < 10:
            controller_port = "USB1"
        else:
            controller_port = f"D{source_code}"

        # Determine intensity calibration file
        calibration_file = None
        if wavelength:
            calibration_file = f"{wavelength}.csv"

        ill_channel = IlluminationChannel(
            name=ch_def.get("name", "Unknown"),
            type=IlluminationType.EPI_ILLUMINATION if is_fluorescence else IlluminationType.TRANSILLUMINATION,
            wavelength_nm=wavelength,
            controller_port=controller_port,
            source_code=source_code,
            intensity_calibration_file=calibration_file,
            enabled=ch_def.get("enabled", True),
        )
        channels.append(ill_channel)

    return IlluminationChannelConfig(version=1, channels=channels)


def convert_xml_channels_to_acquisition_config(
    xml_channels: List[Dict[str, Any]],
    illumination_config: IlluminationChannelConfig,
    include_confocal: bool = False,
    confocal_channels: Optional[List[Dict[str, Any]]] = None,
    include_illumination_channels: bool = False,
) -> ObjectiveChannelConfig:
    """
    Convert parsed XML channels to ObjectiveChannelConfig (v1.0 schema).

    Args:
        xml_channels: Channels from channel_configurations.xml
        illumination_config: Illumination channel config for name lookup
        include_confocal: Whether to include confocal_override with iris settings
        confocal_channels: Channels for confocal overrides
        include_illumination_channels: Whether to include illumination_channel field
            (True for general.yaml, False for objective-specific files)
    """
    # Build lookup for confocal overrides by name
    confocal_by_name = {}
    if confocal_channels:
        for ch in confocal_channels:
            confocal_by_name[ch["name"]] = ch

    acq_channels = []
    for xml_ch in xml_channels:
        # Use old channel name as the acquisition channel name
        name = xml_ch["name"]

        # Find the illumination channel name to use in illumination_channel field
        # For fluorescence channels, this extracts wavelength and finds matching channel
        ill_name = get_illumination_channel_name(
            xml_channel_name=name,
            illumination_source=xml_ch["illumination_source"],
            illumination_config=illumination_config,
        )

        # Create camera settings (v1.0: single object, no display_color)
        camera_settings = CameraSettings(
            exposure_time_ms=xml_ch["exposure_time_ms"],
            gain_mode=xml_ch["analog_gain"],
        )

        # Display color is now at channel level in v1.0
        display_color = xml_ch["display_color"]

        # Create illumination settings (v1.0: no z_offset_um here)
        # illumination_channel only in general.yaml, not in objective files
        illumination_settings = IlluminationSettings(
            illumination_channel=ill_name if include_illumination_channels else None,
            intensity=xml_ch["illumination_intensity"],
        )

        # filter_wheel/filter_position only in general.yaml (v1.0 format)
        # filter_wheel="auto" means single wheel (resolved via hardware_bindings), None means no wheel
        filter_wheel = "auto" if include_illumination_channels else None
        filter_position = xml_ch["emission_filter_position"] if include_illumination_channels else None

        # z_offset_um at channel level in v1.0
        z_offset_um = xml_ch["z_offset"] if include_illumination_channels else 0.0

        # Create confocal_override if needed (v1.0 format - iris settings only)
        confocal_override = None

        if include_confocal and name in confocal_by_name:
            conf_ch = confocal_by_name[name]
            # v1.0: confocal_override contains only iris settings (objective-specific)
            # Filter wheel resolved via hardware_bindings, not stored in config
            confocal_override = AcquisitionChannelOverride(
                illumination_settings=IlluminationSettings(
                    illumination_channel=None,  # Overrides don't need illumination_channel
                    intensity=conf_ch["illumination_intensity"],
                ),
                camera_settings=CameraSettings(
                    exposure_time_ms=conf_ch["exposure_time_ms"],
                    gain_mode=conf_ch["analog_gain"],
                ),
                confocal_settings=ConfocalSettings(
                    illumination_iris=None,  # No iris data in legacy XML
                    emission_iris=None,  # No iris data in legacy XML
                ),
            )

        # Create acquisition channel (v1.0 format)
        acq_channel = AcquisitionChannel(
            name=name,
            display_color=display_color,
            illumination_settings=illumination_settings,
            camera_settings=camera_settings,
            filter_wheel=filter_wheel,
            filter_position=filter_position,
            z_offset_um=z_offset_um,  # v1.0: at channel level
            confocal_override=confocal_override,
        )
        acq_channels.append(acq_channel)

    return ObjectiveChannelConfig(version=1.0, channels=acq_channels)


def convert_laser_af_json_to_yaml(json_path: Path) -> Optional[LaserAFConfig]:
    """Convert laser_af_settings.json to LaserAFConfig."""
    if not json_path.exists():
        return None

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON file {json_path}: {e}")
        return None

    # Convert to LaserAFConfig, handling field renames
    config_data = {
        "version": 1,
        "x_offset": data.get("x_offset", 0),
        "y_offset": data.get("y_offset", 0),
        "width": data.get("width", 1536),
        "height": data.get("height", 256),
        "pixel_to_um": data.get("pixel_to_um", 1.0),
        "x_reference": data.get("x_reference"),
        "has_reference": data.get("has_reference", False),
        "calibration_timestamp": data.get("calibration_timestamp", ""),
        "pixel_to_um_calibration_distance": data.get("pixel_to_um_calibration_distance", 6.0),
        "laser_af_range": data.get("laser_af_range", 100.0),
        "laser_af_averaging_n": data.get("laser_af_averaging_n", 3),
        "spot_detection_mode": data.get("spot_detection_mode", "dual_right"),
        "displacement_success_window_um": data.get("displacement_success_window_um", 1.0),
        "spot_crop_size": data.get("spot_crop_size", 100),
        "correlation_threshold": data.get("correlation_threshold", 0.9),
        "y_window": data.get("y_window", 96),
        "x_window": data.get("x_window", 20),
        "min_peak_width": data.get("min_peak_width", 10.0),
        "min_peak_distance": data.get("min_peak_distance", 10.0),
        "min_peak_prominence": data.get("min_peak_prominence", 0.25),
        "spot_spacing": data.get("spot_spacing", 100.0),
        "filter_sigma": data.get("filter_sigma"),
        "focus_camera_exposure_time_ms": data.get("focus_camera_exposure_time_ms", 0.2),
        "focus_camera_analog_gain": data.get("focus_camera_analog_gain", 0.0),
        "initialize_crop_width": data.get("initialize_crop_width", 1200),
        "initialize_crop_height": data.get("initialize_crop_height", 800),
        "reference_image": data.get("reference_image"),
        "reference_image_shape": data.get("reference_image_shape"),
        "reference_image_dtype": data.get("reference_image_dtype"),
    }

    return LaserAFConfig(**config_data)


def save_yaml(path: Path, model: Any, dry_run: bool = False) -> None:
    """Save a Pydantic model to YAML file."""
    if dry_run:
        logger.info(f"  [DRY RUN] Would create: {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(model, "model_dump"):
        data = model.model_dump(exclude_none=False, mode="json")
    else:
        data = dict(model)

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    logger.info(f"  Created: {path}")


def create_backup(source_dir: Path, dry_run: bool = False) -> Optional[Path]:
    """Create a backup of the source directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_dir.parent / f"{source_dir.name}_backup_{timestamp}"

    if dry_run:
        logger.info(f"[DRY RUN] Would create backup at: {backup_path}")
        return backup_path

    if source_dir.exists():
        shutil.copytree(source_dir, backup_path)
        logger.info(f"Created backup at: {backup_path}")
        return backup_path

    return None


def migrate_profile(
    source_path: Path,
    target_path: Path,
    illumination_config: IlluminationChannelConfig,
    profile_name: str,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Migrate a single profile from old format to new format.

    The migration creates:
    - general.yaml: Union of all channels across all objectives (defines channel identity)
    - {objective}.yaml: Objective-specific overrides (exposure, intensity, gain)

    Returns True if migration was successful.
    """
    logger.info(f"Migrating profile: {profile_name}")

    # Check if target already exists
    target_general = target_path / "channel_configs" / "general.yaml"
    if target_general.exists() and not force:
        logger.warning(f"  Target already exists: {target_general} (use --force to overwrite)")
        return False

    # Find objectives in source
    objectives = [item.name for item in source_path.iterdir() if item.is_dir() and not item.name.startswith(".")]

    if not objectives:
        logger.warning(f"  No objectives found in {source_path}")
        return False

    logger.info(f"  Found objectives: {', '.join(objectives)}")

    # First pass: Collect ALL channels from ALL objectives to build general.yaml
    # Use dict to deduplicate by channel name, keeping first occurrence's settings
    all_channels_by_name: Dict[str, Dict[str, Any]] = {}
    objective_channels: Dict[str, List[Dict[str, Any]]] = {}

    for objective in objectives:
        channel_xml = source_path / objective / "channel_configurations.xml"
        xml_channels = parse_xml_config(channel_xml)

        if not xml_channels and channel_xml.exists():
            logger.warning(f"  No channels extracted from {channel_xml} - file may be corrupt or empty")

        if xml_channels:
            objective_channels[objective] = xml_channels
            for xml_ch in xml_channels:
                ch_name = xml_ch["name"]
                if ch_name not in all_channels_by_name:
                    all_channels_by_name[ch_name] = xml_ch

    # Create general.yaml from union of all channels (v1.0 schema)
    if all_channels_by_name:
        general_channels = []
        for xml_ch in all_channels_by_name.values():
            # Use old channel name as the acquisition channel name
            name = xml_ch["name"]

            # Find the illumination channel name to use in illumination_channel field
            ill_name = get_illumination_channel_name(
                xml_channel_name=name,
                illumination_source=xml_ch["illumination_source"],
                illumination_config=illumination_config,
            )

            # general.yaml has illumination_channel, display_color, z_offset_um
            # filter_wheel/filter_position (v1.0 format)
            acq_channel = AcquisitionChannel(
                name=name,
                display_color=xml_ch["display_color"],
                illumination_settings=IlluminationSettings(
                    illumination_channel=ill_name,
                    intensity=xml_ch["illumination_intensity"],
                ),
                camera_settings=CameraSettings(
                    exposure_time_ms=xml_ch["exposure_time_ms"],
                    gain_mode=xml_ch["analog_gain"],
                ),
                filter_wheel="auto",  # Single wheel, resolved via hardware_bindings
                filter_position=xml_ch["emission_filter_position"],
                z_offset_um=xml_ch["z_offset"],  # v1.0: at channel level
            )
            general_channels.append(acq_channel)

        general_config = GeneralChannelConfig(version=1.0, channels=general_channels, channel_groups=[])
        save_yaml(target_general, general_config, dry_run)

    # Second pass: Create objective-specific override files and laser AF configs
    for objective in objectives:
        obj_source = source_path / objective
        xml_channels = objective_channels.get(objective, [])

        if xml_channels:
            obj_config = convert_xml_channels_to_acquisition_config(
                xml_channels,
                illumination_config,
                include_illumination_channels=False,
            )
            save_yaml(target_path / "channel_configs" / f"{objective}.yaml", obj_config, dry_run)

        # Migrate laser AF settings
        laser_af_json = obj_source / "laser_af_settings.json"
        if laser_af_json.exists():
            laser_af_config = convert_laser_af_json_to_yaml(laser_af_json)
            if laser_af_config:
                save_yaml(target_path / "laser_af_configs" / f"{objective}.yaml", laser_af_config, dry_run)

    return True


def migrate_machine_configs(
    software_path: Path,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Migrate channel_definitions.default.json to machine_configs.

    Returns True if migration was successful.
    """
    logger.info("Migrating machine configs...")

    machine_configs_path = software_path / "machine_configs"
    illumination_target = machine_configs_path / "illumination_channel_config.yaml"

    if illumination_target.exists() and not force:
        logger.warning(f"  Target already exists: {illumination_target} (use --force to overwrite)")
        return False

    # Load channel_definitions.default.json
    channel_defs_path = software_path / "configurations" / "channel_definitions.default.json"
    channel_defs = load_channel_definitions_json(channel_defs_path)

    if channel_defs is None:
        logger.error(f"  Source not found: {channel_defs_path}")
        return False

    # Convert to illumination config
    illumination_config = convert_channel_definitions_to_illumination_config(channel_defs)
    save_yaml(illumination_target, illumination_config, dry_run)

    # Move intensity calibrations if they exist
    old_calibrations = software_path / "intensity_calibrations"
    new_calibrations = machine_configs_path / "intensity_calibrations"

    if old_calibrations.exists() and old_calibrations.is_dir():
        if not dry_run:
            new_calibrations.mkdir(parents=True, exist_ok=True)
            for csv_file in old_calibrations.glob("*.csv"):
                target_file = new_calibrations / csv_file.name
                if not target_file.exists() or force:
                    shutil.copy2(csv_file, target_file)
                    logger.info(f"  Copied: {csv_file.name} -> {target_file}")
        else:
            logger.info(f"  [DRY RUN] Would copy calibration files from {old_calibrations}")

    # Move calibration tests if they exist
    old_tests = software_path / "calibration_tests"
    new_tests = machine_configs_path / "calibration_tests"

    if old_tests.exists() and old_tests.is_dir():
        if not dry_run:
            new_tests.mkdir(parents=True, exist_ok=True)
            for csv_file in old_tests.glob("*.csv"):
                target_file = new_tests / csv_file.name
                if not target_file.exists() or force:
                    shutil.copy2(csv_file, target_file)
                    logger.info(f"  Copied: {csv_file.name} -> {target_file}")
        else:
            logger.info(f"  [DRY RUN] Would copy calibration test files from {old_tests}")

    return True


def run_auto_migration(software_path: Optional[Path] = None, force: bool = False) -> bool:
    """
    Run automatic migration on software startup.

    This function is called from the main application to migrate legacy
    acquisition_configurations to user_profiles format.

    Args:
        software_path: Path to software directory (auto-detected if None)
        force: If True, overwrite existing files

    Returns:
        True if migration was performed, False if no migration needed
    """
    if software_path is None:
        software_path = Path(__file__).parent.parent

    source_path = software_path / "acquisition_configurations"
    target_path = software_path / "user_profiles"
    marker_file = source_path / ".migration_complete"

    # Check if migration already done
    if marker_file.exists():
        logger.debug("Migration already completed (marker file exists)")
        return False

    # Check if source exists
    if not source_path.exists():
        logger.debug("No legacy acquisition_configurations found")
        return False

    # Check if there are any profiles to migrate
    profiles = [item.name for item in source_path.iterdir() if item.is_dir() and not item.name.startswith(".")]

    if not profiles:
        logger.debug("No profiles found in acquisition_configurations")
        return False

    logger.info("Auto-migrating legacy acquisition configurations...")

    # Load illumination config
    illumination_yaml = software_path / "machine_configs" / "illumination_channel_config.yaml"
    if illumination_yaml.exists():
        with open(illumination_yaml, "r") as f:
            data = yaml.safe_load(f)
        illumination_config = IlluminationChannelConfig(**data)
    else:
        # Try to load from channel_definitions.json
        channel_defs_path = software_path / "configurations" / "channel_definitions.json"
        if not channel_defs_path.exists():
            channel_defs_path = software_path / "configurations" / "channel_definitions.default.json"

        channel_defs = load_channel_definitions_json(channel_defs_path)
        if channel_defs:
            illumination_config = convert_channel_definitions_to_illumination_config(channel_defs)
        else:
            logger.error("Cannot auto-migrate: no illumination config available")
            return False

    # Create backup
    create_backup(source_path, dry_run=False)

    # Migrate each profile
    success_count = 0
    for profile in profiles:
        profile_source = source_path / profile
        profile_target = target_path / profile

        if migrate_profile(
            profile_source,
            profile_target,
            illumination_config,
            profile,
            dry_run=False,
            force=force,
        ):
            success_count += 1

    # Mark migration as complete
    marker_file.write_text(f"Migration completed: {datetime.now().isoformat()}\n")

    logger.info(f"Auto-migration complete: {success_count}/{len(profiles)} profiles migrated")
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy acquisition configurations to new YAML format.")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Source directory (default: acquisition_configurations/)",
    )
    parser.add_argument(
        "--machine-config",
        action="store_true",
        help="Also generate machine_configs/ from channel_definitions",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create backup before migration (default: True)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup creation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Migrate only a specific profile",
    )

    args = parser.parse_args()

    # Determine paths
    software_path = Path(__file__).parent.parent
    source_path = args.source or (software_path / "acquisition_configurations")
    target_path = software_path / "user_profiles"

    if not source_path.exists():
        logger.error(f"Source directory not found: {source_path}")
        sys.exit(1)

    logger.info(f"Source: {source_path}")
    logger.info(f"Target: {target_path}")

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===")

    # Create backup
    if args.backup and not args.no_backup and not args.dry_run:
        create_backup(source_path, args.dry_run)

    # Migrate machine configs first (needed for illumination channel reference)
    illumination_config = None
    if args.machine_config:
        migrate_machine_configs(software_path, args.dry_run, args.force)

    # Load or create illumination config for channel name resolution
    illumination_yaml = software_path / "machine_configs" / "illumination_channel_config.yaml"
    if illumination_yaml.exists():
        with open(illumination_yaml, "r") as f:
            data = yaml.safe_load(f)
        illumination_config = IlluminationChannelConfig(**data)
    else:
        # Load from channel_definitions.default.json
        channel_defs_path = software_path / "configurations" / "channel_definitions.default.json"
        channel_defs = load_channel_definitions_json(channel_defs_path)
        if channel_defs:
            illumination_config = convert_channel_definitions_to_illumination_config(channel_defs)
        else:
            logger.error("No illumination config available. Run with --machine-config first.")
            sys.exit(1)

    # Find profiles to migrate
    profiles = []
    if args.profile:
        profiles = [args.profile]
    else:
        for item in source_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                profiles.append(item.name)

    if not profiles:
        logger.warning("No profiles found to migrate.")
        sys.exit(0)

    logger.info(f"Profiles to migrate: {', '.join(profiles)}")

    # Migrate each profile
    success_count = 0
    for profile in profiles:
        profile_source = source_path / profile
        profile_target = target_path / profile

        if migrate_profile(
            profile_source,
            profile_target,
            illumination_config,
            profile,
            args.dry_run,
            args.force,
        ):
            success_count += 1

    logger.info(f"Migration complete: {success_count}/{len(profiles)} profiles migrated")


if __name__ == "__main__":
    main()
