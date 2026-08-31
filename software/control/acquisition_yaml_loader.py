"""
Utilities for parsing and validating acquisition YAML files.
"""

import csv
import os
import yaml

import squid.logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class AcquisitionYAMLData:
    """Parsed acquisition YAML data structure."""

    widget_type: str  # "wellplate" or "flexible"
    xy_mode: str = "Select Wells"

    # Objective info
    objective_name: Optional[str] = None
    objective_magnification: Optional[float] = None
    objective_pixel_size_um: Optional[float] = None
    camera_binning: Optional[Tuple[int, int]] = None

    # Z-stack
    nz: int = 1
    delta_z_um: float = 1.0  # Stored in um (converted from mm when loading)
    z_stacking_config: str = "FROM BOTTOM"
    use_piezo: bool = False

    # Time series
    nt: int = 1
    delta_t_s: float = 0.0

    # Channels
    channel_names: List[str] = field(default_factory=list)

    # Autofocus
    contrast_af: bool = False
    laser_af: bool = False

    # Wellplate-specific
    scan_size_mm: Optional[float] = None
    overlap_percent: float = 10.0
    scan_shape: Optional[str] = None
    wellplate_regions: Optional[List[Dict]] = None  # [{name, center_mm, shape, fovs?}, ...]

    # Flexible-specific
    nx: int = 1
    ny: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    flexible_positions: Optional[List[Dict]] = None  # [{name, center_mm, fovs?}, ...]

    # Added for saved-acquisition reuse (fluidics protocol, TCP server)
    experiment_id: Optional[str] = None
    wellplate_format: Optional[str] = None
    z_range_mm: Optional[Tuple[float, float]] = None
    skip_saving: bool = False


def parse_acquisition_yaml(file_path: str) -> AcquisitionYAMLData:
    """Parse a saved acquisition.yaml - or a saved acquisition folder containing one.

    For a folder, regions that carry no per-FOV list are completed from the sibling coordinates.csv
    (older acquisition.yaml files only record region centers), so a saved acquisition folder is a
    self-sufficient source of settings *and* coordinates.

    Raises ValueError on an empty file or an unknown widget_type.
    """
    folder = None
    if os.path.isdir(file_path):
        folder = file_path
        file_path = os.path.join(folder, "acquisition.yaml")
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"YAML file is empty or invalid: {file_path}")

    parsed = parse_acquisition_dict(data, source=file_path)

    csv_path = os.path.join(folder or os.path.dirname(file_path), "coordinates.csv")
    regions = parsed.wellplate_regions if parsed.widget_type == "wellplate" else parsed.flexible_positions
    if regions and not any(r.get("fovs") for r in regions) and os.path.isfile(csv_path):
        try:
            by_name = {r["name"]: r["fovs"] for r in read_coordinates_csv(csv_path)}
        except (ValueError, OSError) as e:
            # An empty or malformed sibling CSV (an aborted run) must not fail an otherwise valid acquisition.yaml.
            squid.logging.get_logger(__name__).warning(f"Ignoring {csv_path}: {e}")
            by_name = {}
        for region in regions:
            if region.get("name") in by_name:
                region["fovs"] = by_name[region["name"]]
    return parsed


def parse_acquisition_dict(data: dict, source: str = "<dict>") -> AcquisitionYAMLData:
    """Parse an already-loaded acquisition.yaml mapping (the file body, or a protocol header block)."""
    if not isinstance(data, dict):
        raise ValueError(f"Acquisition data must be a mapping ({source})")

    # Extract sections
    acq = data.get("acquisition", {})
    sample = data.get("sample", {}) or {}
    obj = data.get("objective", {})
    z_stack = data.get("z_stack", {})
    time_series = data.get("time_series", {})
    channels = data.get("channels", [])
    autofocus = data.get("autofocus", {})
    wellplate_scan = data.get("wellplate_scan", {})
    flexible_scan = data.get("flexible_scan", {})

    # Validate widget_type
    VALID_WIDGET_TYPES = ("wellplate", "flexible")
    widget_type = acq.get("widget_type", "wellplate")
    if widget_type not in VALID_WIDGET_TYPES:
        raise ValueError(f"Invalid widget_type '{widget_type}'. Must be one of: {VALID_WIDGET_TYPES}")

    # Parse camera binning
    binning = obj.get("camera_binning")
    if binning and isinstance(binning, list) and len(binning) == 2:
        camera_binning = tuple(binning)
    else:
        camera_binning = None

    # Determine overlap_percent from the appropriate section
    if wellplate_scan:
        overlap = wellplate_scan.get("overlap_percent", 10.0)
    elif flexible_scan:
        overlap = flexible_scan.get("overlap_percent", 10.0)
    else:
        overlap = 10.0

    # Get scan shape from first region if available
    scan_shape = None
    wellplate_regions = wellplate_scan.get("regions")
    if wellplate_regions and len(wellplate_regions) > 0:
        scan_shape = wellplate_regions[0].get("shape")

    z_range = z_stack.get("z_range_mm")
    z_range_mm = tuple(float(v) for v in z_range) if z_range and len(z_range) == 2 else None

    return AcquisitionYAMLData(
        widget_type=widget_type,
        xy_mode=acq.get("xy_mode", "Select Wells"),
        # Objective info
        objective_name=obj.get("name"),
        objective_magnification=obj.get("magnification"),
        objective_pixel_size_um=obj.get("pixel_size_um"),
        camera_binning=camera_binning,
        # Z-stack (convert mm to um)
        nz=z_stack.get("nz", 1),
        delta_z_um=z_stack.get("delta_z_mm", 0.001) * 1000,
        z_stacking_config=z_stack.get("config", "FROM BOTTOM"),
        use_piezo=z_stack.get("use_piezo", False),
        # Time series
        nt=time_series.get("nt", 1),
        delta_t_s=time_series.get("delta_t_s", 0.0),
        # Channels
        channel_names=[ch.get("name") for ch in channels if ch.get("name")],
        # Autofocus
        contrast_af=autofocus.get("contrast_af", False),
        laser_af=autofocus.get("laser_af", False),
        # Wellplate-specific
        scan_size_mm=wellplate_scan.get("scan_size_mm"),
        overlap_percent=overlap,
        scan_shape=scan_shape,
        wellplate_regions=wellplate_regions,
        # Flexible-specific
        nx=flexible_scan.get("nx", 1),
        ny=flexible_scan.get("ny", 1),
        delta_x_mm=flexible_scan.get("delta_x_mm", 0.9),
        delta_y_mm=flexible_scan.get("delta_y_mm", 0.9),
        flexible_positions=flexible_scan.get("positions"),
        # Saved-acquisition reuse
        experiment_id=acq.get("experiment_id"),
        wellplate_format=sample.get("wellplate_format"),
        z_range_mm=z_range_mm,
        skip_saving=bool(acq.get("skip_saving", False)),
    )


def read_coordinates_csv(path: str) -> List[dict]:
    """Read a Squid coordinates.csv into [{"name": region, "fovs": [[x, y(, z)], ...]}, ...] in file order.

    The z column is used only when present and filled for every row (matching
    control.widgets.load_coordinate_regions_from_dataframe). Raises ValueError on missing columns.
    """
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = ("region", "x (mm)", "y (mm)")
    fieldnames = rows[0].keys() if rows else ()
    if not rows or not all(col in fieldnames for col in required):
        raise ValueError("coordinates.csv must contain 'region', 'x (mm)' and 'y (mm)' columns")
    has_z = "z (mm)" in fieldnames and all((row.get("z (mm)") or "").strip() for row in rows)
    regions: Dict[str, dict] = {}
    for row in rows:
        fov = [float(row["x (mm)"]), float(row["y (mm)"])]
        if has_z:
            fov.append(float(row["z (mm)"]))
        regions.setdefault(str(row["region"]), {"name": str(row["region"]), "fovs": []})["fovs"].append(fov)
    return list(regions.values())


@dataclass
class ValidationResult:
    """Result of hardware validation."""

    is_valid: bool
    objective_mismatch: bool = False
    binning_mismatch: bool = False
    current_objective: str = ""
    yaml_objective: str = ""
    current_binning: Tuple[int, int] = (1, 1)
    yaml_binning: Tuple[int, int] = (1, 1)
    message: str = ""


def validate_hardware(
    yaml_data: AcquisitionYAMLData,
    current_objective: str,
    current_binning: Tuple[int, int],
) -> ValidationResult:
    """Validate that YAML settings match current hardware configuration.

    Args:
        yaml_data: Parsed YAML data
        current_objective: Currently selected objective name
        current_binning: Current camera binning as (x, y) tuple

    Returns:
        ValidationResult indicating whether hardware matches
    """
    objective_mismatch = False
    binning_mismatch = False
    messages = []

    if yaml_data.objective_name and yaml_data.objective_name != current_objective:
        objective_mismatch = True
        messages.append(f"Objective mismatch:\n  YAML: '{yaml_data.objective_name}'\n  Current: '{current_objective}'")

    if yaml_data.camera_binning and tuple(yaml_data.camera_binning) != tuple(current_binning):
        binning_mismatch = True
        messages.append(
            f"Camera binning mismatch:\n  YAML: {list(yaml_data.camera_binning)}\n  Current: {list(current_binning)}"
        )

    return ValidationResult(
        is_valid=not (objective_mismatch or binning_mismatch),
        objective_mismatch=objective_mismatch,
        binning_mismatch=binning_mismatch,
        current_objective=current_objective,
        yaml_objective=yaml_data.objective_name or "",
        current_binning=current_binning,
        yaml_binning=yaml_data.camera_binning or (1, 1),
        message="\n\n".join(messages) if messages else "",
    )
