"""
Utilities for parsing and validating acquisition YAML files.
"""

import math
import yaml
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
    wellplate_regions: Optional[List[Dict]] = None  # [{name, center_mm, shape}, ...]
    # Wells-by-name (additive): X/Y derived from the plate definition at run time.
    # Mutually exclusive with wellplate_regions. Normalized to a comma-separated string
    # ("A1:B3" or "A1,B2,C3"); None when the method uses explicit regions instead.
    wells: Optional[str] = None

    # Schema v2 (wellplate): per-well FOV pattern. None = legacy coverage behavior
    # driven by flat scan_size_mm/overlap_percent. When present, always normalized
    # to a dict with a "type" key; see _normalize_fov_pattern for per-type keys.
    fov_pattern: Optional[Dict] = None
    # Schema v2: per-well laser-AF target offsets (µm from the AF reference plane).
    # Optional "default" key applies to wells not listed. Requires laser_af.
    well_z_offsets_um: Optional[Dict[str, float]] = None
    # Schema v2: pre-AF Z plan for tilted plates: {"type": "focus_map",
    # "generate": bool, "points": [[x,y,z]*3] | None} (generate XOR points).
    z_plan: Optional[Dict] = None

    # Flexible-specific
    nx: int = 1
    ny: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    flexible_positions: Optional[List[Dict]] = None  # [{name, center_mm}, ...]


_FOV_PATTERN_TYPES = ("coverage", "centered_grid", "grid_subset", "random")


def _normalize_fov_pattern(raw: Optional[dict], overlap_default: float) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "type" not in raw:
        raise ValueError("fov_pattern must be a mapping with a 'type' key")
    ptype = raw["type"]
    if ptype not in _FOV_PATTERN_TYPES:
        raise ValueError(f"fov_pattern type {ptype!r} not one of {_FOV_PATTERN_TYPES}")
    if ptype == "coverage":
        return {
            "type": "coverage",
            "scan_size_mm": raw.get("scan_size_mm"),
            "overlap_percent": float(raw.get("overlap_percent", overlap_default)),
            "shape": raw.get("shape", "Square"),
        }
    if ptype in ("centered_grid", "grid_subset"):
        nx, ny = raw.get("nx"), raw.get("ny")
        if not (isinstance(nx, int) and isinstance(ny, int) and nx >= 1 and ny >= 1):
            raise ValueError(f"fov_pattern {ptype}: nx and ny must be integers >= 1")
        out = {"type": ptype, "nx": nx, "ny": ny, "overlap_percent": float(raw.get("overlap_percent", overlap_default))}
        if ptype == "grid_subset":
            tiles = raw.get("tiles")
            if not isinstance(tiles, list) or not tiles:
                raise ValueError("fov_pattern grid_subset: 'tiles' must be a non-empty list of [row, col]")
            norm_tiles = []
            for t in tiles:
                if not (isinstance(t, (list, tuple)) and len(t) == 2):
                    raise ValueError(f"fov_pattern grid_subset: bad tile entry {t!r} (expected [row, col])")
                row, col = int(t[0]), int(t[1])
                if not (0 <= row < ny and 0 <= col < nx):
                    raise ValueError(
                        f"fov_pattern grid_subset: tile [{row}, {col}] outside {ny}x{nx} grid (rows 0..{ny-1}, cols 0..{nx-1})"
                    )
                norm_tiles.append([row, col])
            if len({tuple(t) for t in norm_tiles}) != len(norm_tiles):
                raise ValueError("fov_pattern grid_subset: duplicate tiles")
            out["tiles"] = norm_tiles
        return out
    # random
    n_fovs = raw.get("n_fovs")
    if not (isinstance(n_fovs, int) and n_fovs >= 1):
        raise ValueError("fov_pattern random: n_fovs must be an integer >= 1")
    seed = raw.get("seed")
    if seed is not None and not isinstance(seed, int):
        raise ValueError("fov_pattern random: seed must be an integer")
    return {"type": "random", "n_fovs": n_fovs, "seed": seed}


def _validate_well_z_offsets(raw: Optional[dict]) -> Optional[Dict[str, float]]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise ValueError("well_z_offsets_um must be a non-empty mapping of well name -> µm")
    out = {}
    for name, value in raw.items():
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"well_z_offsets_um[{name!r}]: not a number: {value!r}")
        if not math.isfinite(f):
            raise ValueError(f"well_z_offsets_um[{name!r}]: must be finite, got {value!r}")
        out[str(name)] = f
    return out


def _validate_z_plan(raw: Optional[dict]) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("type") != "focus_map":
        raise ValueError("z_plan: only {'type': 'focus_map', ...} is supported")
    generate = bool(raw.get("generate", False))
    points = raw.get("points")
    if generate == bool(points):
        raise ValueError("z_plan: specify exactly one of 'generate: true' or 'points'")
    norm_points = None
    if points is not None:
        if not (isinstance(points, list) and len(points) == 3):
            raise ValueError("z_plan: 'points' must be exactly 3 [x_mm, y_mm, z_mm] entries (a plane)")
        norm_points = []
        for p in points:
            if not (isinstance(p, (list, tuple)) and len(p) == 3):
                raise ValueError(f"z_plan: bad point {p!r} (expected [x_mm, y_mm, z_mm])")
            fx, fy, fz = (float(v) for v in p)
            if not all(math.isfinite(v) for v in (fx, fy, fz)):
                raise ValueError(f"z_plan: non-finite point {p!r}")
            norm_points.append([fx, fy, fz])
        # The 3 points must span a plane: reject XY-colinear sets here (a clean
        # loader ValueError -> INVALID_PARAM at preflight) instead of letting
        # interpolate_plane raise deep inside start_acquisition (a misleading 500).
        (x1, y1, _), (x2, y2, _), (x3, y3, _) = norm_points
        det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
        if abs(det) < 1e-9:
            raise ValueError("z_plan: the 3 points are colinear in XY and cannot define a plane")
    return {"type": "focus_map", "generate": generate, "points": norm_points}


def parse_acquisition_yaml(file_path: str) -> AcquisitionYAMLData:
    """Parse acquisition YAML file and return structured data.

    Args:
        file_path: Path to the acquisition.yaml file

    Returns:
        AcquisitionYAMLData with parsed values

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If file is not valid YAML
        ValueError: If file is empty or has invalid widget_type
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"YAML file is empty or invalid: {file_path}")

    # Extract sections
    acq = data.get("acquisition", {})
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

    # Wells-by-name (additive): accept a string ("A1:B3", "A1,B2") or a YAML list of
    # names (joined with ","). Normalize empty/absent to None. Mutually exclusive with
    # a non-empty regions list.
    wells_raw = wellplate_scan.get("wells")
    if isinstance(wells_raw, (list, tuple)):
        wells = ",".join(str(w).strip() for w in wells_raw)
    elif wells_raw is not None:
        wells = str(wells_raw).strip()
    else:
        wells = None
    if not wells:
        wells = None
    if wells and wellplate_regions:
        raise ValueError("wellplate_scan: specify either 'wells' or 'regions', not both")

    fov_pattern = _normalize_fov_pattern(wellplate_scan.get("fov_pattern"), overlap)
    if fov_pattern and fov_pattern["type"] != "coverage" and not wells:
        raise ValueError(f"fov_pattern {fov_pattern['type']!r} requires wellplate_scan 'wells' (per-well patterns)")
    well_z_offsets_um = _validate_well_z_offsets(wellplate_scan.get("well_z_offsets_um"))
    z_plan = _validate_z_plan(wellplate_scan.get("z_plan"))

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
        wells=wells,
        fov_pattern=fov_pattern,
        well_z_offsets_um=well_z_offsets_um,
        z_plan=z_plan,
        # Flexible-specific
        nx=flexible_scan.get("nx", 1),
        ny=flexible_scan.get("ny", 1),
        delta_x_mm=flexible_scan.get("delta_x_mm", 0.9),
        delta_y_mm=flexible_scan.get("delta_y_mm", 0.9),
        flexible_positions=flexible_scan.get("positions"),
    )


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
