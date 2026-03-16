"""
Helper functions for protocol initialization and metadata.

Extracted from OrchestratorController to reduce controller size.
These are pure utility functions with no persistent state.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.events import ClearScanCoordinatesCommand, EventBus, LoadScanCoordinatesCommand
from squid.core.protocol import (
    ExperimentProtocol,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
)

if TYPE_CHECKING:
    from squid.backend.managers.scan_coordinates import ScanCoordinates

_log = squid.core.logging.get_logger(__name__)


def parse_fov_set(
    csv_path: str,
    ) -> Tuple[Dict[str, Tuple[Tuple[float, ...], ...]], Dict[str, Tuple[float, ...]]]:
    """Parse FOV positions from CSV.

    Expected columns: region, x (mm), y (mm) (optional: z (mm))

    Args:
        csv_path: Path to CSV file with FOV positions

    Returns:
        Tuple of ``(region_fov_coordinates, region_centers)``.
    """
    import pandas as pd
    from pathlib import Path

    if not Path(csv_path).exists():
        raise FileNotFoundError(f"FOV CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Normalize column names (handle variations)
    col_map: Dict[str, str] = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if "region" in col_lower:
            col_map["region"] = col
        elif col_lower in ("x", "x_mm", "x (mm)") or ("x" in col_lower and "mm" in col_lower):
            col_map["x"] = col
        elif col_lower in ("y", "y_mm", "y (mm)") or ("y" in col_lower and "mm" in col_lower):
            col_map["y"] = col
        elif col_lower in ("z", "z_mm", "z (mm)") or ("z" in col_lower and "mm" in col_lower):
            col_map["z"] = col

    if not all(k in col_map for k in ["region", "x", "y"]):
        raise ValueError(
            f"CSV must have region, x (mm), y (mm) columns. Found: {list(df.columns)}"
        )

    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]] = {}
    region_centers: Dict[str, Tuple[float, ...]] = {}

    for region_id in df[col_map["region"]].unique():
        region_points = df[df[col_map["region"]] == region_id]
        if "z" in col_map:
            coords = tuple(
                (float(x), float(y), float(z))
                for x, y, z in zip(
                    region_points[col_map["x"]],
                    region_points[col_map["y"]],
                    region_points[col_map["z"]],
                )
            )
            region_centers[str(region_id)] = (
                float(region_points[col_map["x"]].mean()),
                float(region_points[col_map["y"]].mean()),
                float(region_points[col_map["z"]].mean()),
            )
        else:
            coords = tuple(
                (float(x), float(y))
                for x, y in zip(region_points[col_map["x"]], region_points[col_map["y"]])
            )
            region_centers[str(region_id)] = (
                float(region_points[col_map["x"]].mean()),
                float(region_points[col_map["y"]].mean()),
            )
        region_fov_coordinates[str(region_id)] = coords

    _log.info(
        "Parsed %d FOVs from %d regions in %s",
        sum(len(c) for c in region_fov_coordinates.values()),
        len(region_fov_coordinates),
        csv_path,
    )
    return region_fov_coordinates, region_centers


def create_detached_scan_coordinates(
    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]],
    region_centers: Dict[str, Tuple[float, ...]],
    template_scan_coordinates: "ScanCoordinates",
) -> "ScanCoordinates":
    """Create an acquisition-local scan-coordinate snapshot."""
    from squid.backend.managers.scan_coordinates import ScanCoordinates

    detached = ScanCoordinates(
        objectiveStore=template_scan_coordinates.objectiveStore,
        stage=template_scan_coordinates.stage,
        camera=template_scan_coordinates.camera,
        event_bus=None,
    )
    for attr in (
        "acquisition_pattern",
        "fov_pattern",
        "format",
        "a1_x_mm",
        "a1_y_mm",
        "wellplate_offset_x_mm",
        "wellplate_offset_y_mm",
        "well_spacing_mm",
        "well_size_mm",
        "a1_x_pixel",
        "a1_y_pixel",
        "number_of_skip",
    ):
        if hasattr(template_scan_coordinates, attr):
            setattr(detached, attr, getattr(template_scan_coordinates, attr))

    detached.load_coordinates(
        region_fov_coordinates=region_fov_coordinates,
        region_centers=region_centers,
    )
    return detached


def scan_coordinates_payload(
    scan_coordinates: "ScanCoordinates",
) -> Tuple[Dict[str, Tuple[Tuple[float, ...], ...]], Dict[str, Tuple[float, ...]]]:
    """Convert ScanCoordinates to serializable tuple payloads."""
    region_fov_coordinates = {
        region_id: tuple(tuple(float(v) for v in coord) for coord in coords)
        for region_id, coords in scan_coordinates.region_fov_coordinates.items()
    }
    region_centers = {
        region_id: tuple(float(v) for v in center)
        for region_id, center in scan_coordinates.region_centers.items()
    }
    return region_fov_coordinates, region_centers


def publish_scan_coordinates(
    event_bus: EventBus,
    region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]],
    region_centers: Dict[str, Tuple[float, ...]],
    *,
    clear_existing: bool = False,
) -> None:
    """Publish scan-coordinate commands so the live GUI reflects a run plan."""
    if clear_existing:
        event_bus.publish(ClearScanCoordinatesCommand(clear_displayed_fovs=True))
    event_bus.publish(
        LoadScanCoordinatesCommand(
            region_fov_coordinates=region_fov_coordinates,
            region_centers=region_centers,
        )
    )


def load_fov_set(
    csv_path: str,
    scan_coordinates: Optional["ScanCoordinates"],
    event_bus: EventBus,
) -> None:
    """Legacy helper that loads and publishes FOV positions from CSV."""
    region_fov_coordinates, region_centers = parse_fov_set(csv_path)
    if scan_coordinates is not None and hasattr(scan_coordinates, "load_coordinates"):
        scan_coordinates.load_coordinates(
            region_fov_coordinates=region_fov_coordinates,
            region_centers=region_centers,
        )
        event_bus.publish(
            LoadScanCoordinatesCommand(
                region_fov_coordinates=region_fov_coordinates,
                region_centers=region_centers,
                apply=False,
            )
        )
        return
    publish_scan_coordinates(
        event_bus,
        region_fov_coordinates,
        region_centers,
        clear_existing=False,
    )


def collect_experiment_configurations(
    protocol: ExperimentProtocol,
    multipoint_controller: Any,
) -> list:
    """Collect channel configurations referenced by protocol imaging configs.

    Args:
        protocol: The experiment protocol
        multipoint_controller: MultiPointController for channel lookups

    Returns:
        List of channel configurations matching the protocol's imaging channels
    """
    if multipoint_controller is None or not hasattr(multipoint_controller, "channelConfigurationManager"):
        return []
    channel_manager = multipoint_controller.channelConfigurationManager

    current_objective = None
    if hasattr(multipoint_controller, "objectiveStore") and multipoint_controller.objectiveStore:
        current_objective = multipoint_controller.objectiveStore.current_objective
    if current_objective is None:
        _log.warning("Cannot collect configurations: no current objective available")
        return []

    channel_names: List[str] = []
    seen: set = set()
    for config in protocol.imaging_protocols.values():
        for name in config.get_channel_names():
            if name not in seen:
                seen.add(name)
                channel_names.append(name)

    configurations = []
    for name in channel_names:
        config = channel_manager.get_channel_configuration_by_name(current_objective, name)
        if config is not None:
            configurations.append(config)

    if not configurations and channel_names:
        _log.warning("Protocol channels did not match any configured channels")

    return configurations


def build_experiment_metadata(
    protocol: ExperimentProtocol,
    protocol_path: Optional[str],
) -> dict:
    """Build protocol metadata for experiment folder.

    Args:
        protocol: The experiment protocol
        protocol_path: Path to the protocol YAML file

    Returns:
        Metadata dictionary
    """
    rounds_meta = []
    for idx, round_ in enumerate(protocol.rounds):
        step_types = []
        for step in round_.steps:
            if isinstance(step, FluidicsStep):
                step_types.append("fluidics")
            elif isinstance(step, ImagingStep):
                step_types.append("imaging")
            elif isinstance(step, InterventionStep):
                step_types.append("intervention")
            else:
                step_types.append("unknown")
        rounds_meta.append(
            {
                "index": idx,
                "name": round_.name,
                "steps": step_types,
            }
        )

    return {
        "protocol": {
            "name": protocol.name,
            "version": protocol.version,
            "path": protocol_path,
        },
        "rounds": rounds_meta,
        "created_at": datetime.now().isoformat(),
    }
