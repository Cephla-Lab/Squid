#!/usr/bin/env python3
"""Migrate arch_v2 JSON/XML channel configs to upstream YAML format.

Converts:
  configurations/channel_definitions.json
    → machine_configs/illumination_channel_config.yaml (if not already present)

  acquisition_configurations/{profile}/{objective}/channel_settings.json
    → user_profiles/{profile}/channel_configs/general.yaml
    → user_profiles/{profile}/channel_configs/{objective}.yaml

  acquisition_configurations/{profile}/{objective}/laser_af_settings.json
    → user_profiles/{profile}/laser_af_configs/{objective}.yaml

Creates new files without deleting old ones. Idempotent — skips files
that already exist in the target locations.

Usage:
    python tools/migrate_to_yaml_configs.py [--base-path /path/to/software]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# ── Channel Definitions → Illumination Config ────────────────────────────


# Map from numeric_channel_mapping illumination_source to controller port
_ILLUMINATION_SOURCE_TO_PORT = {
    11: "D1",
    12: "D2",
    13: "D3",
    14: "D4",
    15: "D5",
    0: "USB1",
    1: "USB2",
    2: "USB3",
    3: "USB4",
    4: "USB5",
    7: "USB7",
    8: "USB8",
}

# Map from illumination source int to controller port name
_CONTROLLER_PORT_MAPPING = {
    "USB1": 0,
    "USB2": 1,
    "USB3": 2,
    "USB4": 3,
    "USB5": 4,
    "USB7": 7,
    "USB8": 8,
    "D1": 11,
    "D2": 12,
    "D3": 13,
    "D4": 14,
    "D5": 15,
}

# Wavelength lookup for D-port sources
_PORT_WAVELENGTHS = {
    "D1": 405,
    "D2": 488,
    "D3": 638,
    "D4": 561,
    "D5": 730,
}


def _channel_type_from_def(ch_def: Dict[str, Any]) -> str:
    """Determine illumination type from channel definition."""
    ch_type = ch_def.get("type", "")
    if ch_type == "fluorescence":
        return "epi_illumination"
    return "transillumination"


def _get_controller_port(ch_def: Dict[str, Any], numeric_mapping: Dict[str, Any]) -> Optional[str]:
    """Get controller port for a channel definition."""
    ill_source = ch_def.get("illumination_source")

    if ill_source is not None:
        return _ILLUMINATION_SOURCE_TO_PORT.get(ill_source)

    # Fluorescence: look up numeric_channel in the mapping
    numeric_ch = ch_def.get("numeric_channel")
    if numeric_ch is not None:
        mapping = numeric_mapping.get(str(numeric_ch), {})
        source = mapping.get("illumination_source")
        if source is not None:
            return _ILLUMINATION_SOURCE_TO_PORT.get(source)

    return None


def migrate_channel_definitions(
    definitions_path: Path,
    output_path: Path,
) -> bool:
    """Convert channel_definitions.json to illumination_channel_config.yaml.

    Returns True if migration was performed, False if skipped.
    """
    if output_path.exists():
        logger.info(f"Illumination config already exists at {output_path}, skipping")
        return False

    if not definitions_path.exists():
        logger.warning(f"Channel definitions not found at {definitions_path}")
        return False

    with open(definitions_path) as f:
        defs_data = json.load(f)

    channels_data = defs_data.get("channels", [])
    numeric_mapping = defs_data.get("numeric_channel_mapping", {})

    ill_channels = []
    for ch_def in channels_data:
        port = _get_controller_port(ch_def, numeric_mapping)
        if port is None:
            logger.warning(f"Could not determine port for channel '{ch_def['name']}', skipping")
            continue

        ill_channel = {
            "name": ch_def["name"],
            "type": _channel_type_from_def(ch_def),
            "controller_port": port,
        }

        wavelength = _PORT_WAVELENGTHS.get(port)
        if wavelength is not None:
            ill_channel["wavelength_nm"] = wavelength

        ill_channels.append(ill_channel)

    config = {
        "version": 1.0,
        "controller_port_mapping": _CONTROLLER_PORT_MAPPING,
        "channels": ill_channels,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Migrated {len(ill_channels)} illumination channels to {output_path}")
    return True


# ── Channel Settings → YAML Acquisition Configs ─────────────────────────


def _build_acquisition_channel(
    name: str,
    settings: Dict[str, Any],
    ch_def: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a YAML acquisition channel from JSON settings + definition."""
    channel = {
        "name": name,
        "enabled": True,
        "camera_settings": {
            "exposure_time_ms": settings.get("exposure_time", 25.0),
            "gain_mode": settings.get("analog_gain", 0.0),
        },
        "illumination_settings": {
            "intensity": settings.get("illumination_intensity", 20.0),
        },
        "z_offset_um": settings.get("z_offset", 0.0),
    }

    if ch_def:
        channel["display_color"] = ch_def.get("display_color", "#FFFFFF")
        channel["enabled"] = ch_def.get("enabled", True)
        channel["filter_position"] = ch_def.get("emission_filter_position", 1)

    # Confocal overrides
    confocal = settings.get("confocal")
    if confocal:
        override = {}
        if confocal.get("exposure_time") is not None:
            override.setdefault("camera_settings", {})["exposure_time_ms"] = confocal["exposure_time"]
        if confocal.get("analog_gain") is not None:
            override.setdefault("camera_settings", {})["gain_mode"] = confocal["analog_gain"]
        if confocal.get("illumination_intensity") is not None:
            override.setdefault("illumination_settings", {})["intensity"] = confocal["illumination_intensity"]
        if override:
            channel["confocal_override"] = override

    return channel


def migrate_profile(
    profile_path: Path,
    output_profile_path: Path,
    channel_defs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[int, int]:
    """Migrate a single profile from JSON to YAML.

    Args:
        profile_path: Path to acquisition_configurations/{profile}/
        output_profile_path: Path to user_profiles/{profile}/
        channel_defs: Optional channel definitions lookup by name

    Returns:
        Tuple of (objectives_migrated, objectives_skipped)
    """
    channel_configs_dir = output_profile_path / "channel_configs"
    channel_configs_dir.mkdir(parents=True, exist_ok=True)

    migrated = 0
    skipped = 0

    # Collect all unique channels across objectives for general.yaml
    all_channel_names: List[str] = []
    first_objective_settings: Optional[Dict[str, Any]] = None

    # First pass: collect channel names
    for obj_dir in sorted(profile_path.iterdir()):
        if not obj_dir.is_dir():
            continue
        settings_file = obj_dir / "channel_settings.json"
        if settings_file.exists():
            with open(settings_file) as f:
                data = json.load(f)
            if first_objective_settings is None:
                first_objective_settings = data
            for name in data:
                if name not in all_channel_names:
                    all_channel_names.append(name)

    # Generate general.yaml if it doesn't exist
    general_path = channel_configs_dir / "general.yaml"
    if not general_path.exists() and first_objective_settings:
        general_channels = []
        for name in all_channel_names:
            settings = first_objective_settings.get(name, {})
            ch_def = channel_defs.get(name) if channel_defs else None
            ch = _build_acquisition_channel(name, settings, ch_def)
            # For general, set the illumination_channel name
            ch["illumination_settings"]["illumination_channel"] = name
            general_channels.append(ch)

        general_config = {
            "version": 1.0,
            "channels": general_channels,
        }
        with open(general_path, "w") as f:
            yaml.dump(general_config, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Created general.yaml with {len(general_channels)} channels")

    # Second pass: per-objective YAML
    for obj_dir in sorted(profile_path.iterdir()):
        if not obj_dir.is_dir():
            continue

        objective = obj_dir.name
        objective_yaml = channel_configs_dir / f"{objective}.yaml"

        if objective_yaml.exists():
            logger.debug(f"Objective config {objective_yaml} exists, skipping")
            skipped += 1
            continue

        settings_file = obj_dir / "channel_settings.json"
        if not settings_file.exists():
            continue

        with open(settings_file) as f:
            settings_data = json.load(f)

        channels = []
        for name, settings in settings_data.items():
            ch = _build_acquisition_channel(name, settings)
            # Objective configs don't set illumination_channel (comes from general)
            channels.append(ch)

        obj_config = {
            "version": 1.0,
            "channels": channels,
        }
        with open(objective_yaml, "w") as f:
            yaml.dump(obj_config, f, default_flow_style=False, sort_keys=False)

        migrated += 1

    # Migrate laser AF configs
    laser_af_dir = output_profile_path / "laser_af_configs"
    for obj_dir in sorted(profile_path.iterdir()):
        if not obj_dir.is_dir():
            continue

        af_file = obj_dir / "laser_af_settings.json"
        if not af_file.exists():
            continue

        objective = obj_dir.name
        af_yaml = laser_af_dir / f"{objective}.yaml"
        if af_yaml.exists():
            continue

        laser_af_dir.mkdir(parents=True, exist_ok=True)
        with open(af_file) as f:
            af_data = json.load(f)

        with open(af_yaml, "w") as f:
            yaml.dump(af_data, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Migrated laser AF config for {objective}")

    return migrated, skipped


def migrate(base_path: Path) -> Dict[str, Any]:
    """Run full migration from JSON/XML to YAML configs.

    Args:
        base_path: Path to the software/ directory

    Returns:
        Summary dict with migration results
    """
    summary = {
        "illumination_migrated": False,
        "profiles": {},
        "errors": [],
    }

    # 1. Migrate illumination config
    definitions_file = base_path / "configurations" / "channel_definitions.json"
    ill_output = base_path / "machine_configs" / "illumination_channel_config.yaml"

    try:
        summary["illumination_migrated"] = migrate_channel_definitions(
            definitions_file, ill_output
        )
    except Exception as e:
        msg = f"Failed to migrate illumination config: {e}"
        logger.error(msg)
        summary["errors"].append(msg)

    # 2. Load channel definitions for metadata (display_color, enabled, etc.)
    channel_defs: Dict[str, Dict[str, Any]] = {}
    if definitions_file.exists():
        try:
            with open(definitions_file) as f:
                defs = json.load(f)
            for ch in defs.get("channels", []):
                channel_defs[ch["name"]] = ch
        except Exception as e:
            logger.warning(f"Could not load channel definitions for metadata: {e}")

    # 3. Migrate each profile
    acq_configs = base_path / "acquisition_configurations"
    if acq_configs.exists():
        for profile_dir in sorted(acq_configs.iterdir()):
            if not profile_dir.is_dir():
                continue

            profile_name = profile_dir.name
            output_path = base_path / "user_profiles" / profile_name

            try:
                migrated, skipped = migrate_profile(
                    profile_dir, output_path, channel_defs
                )
                summary["profiles"][profile_name] = {
                    "objectives_migrated": migrated,
                    "objectives_skipped": skipped,
                }
                logger.info(
                    f"Profile '{profile_name}': "
                    f"{migrated} migrated, {skipped} skipped"
                )
            except Exception as e:
                msg = f"Failed to migrate profile '{profile_name}': {e}"
                logger.error(msg)
                summary["errors"].append(msg)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Migrate JSON/XML configs to YAML")
    parser.add_argument(
        "--base-path",
        type=Path,
        default=Path(__file__).parent.parent,
        help="Path to the software/ directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    result = migrate(args.base_path)

    if result["errors"]:
        print(f"\nMigration completed with {len(result['errors'])} error(s):")
        for err in result["errors"]:
            print(f"  - {err}")
        sys.exit(1)
    else:
        total = sum(p["objectives_migrated"] for p in result["profiles"].values())
        print(f"\nMigration complete: {total} objective(s) migrated across {len(result['profiles'])} profile(s)")


if __name__ == "__main__":
    main()
