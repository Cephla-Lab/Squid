"""
Utilities for parsing and validating acquisition YAML files.
"""

import csv
import math
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
    z_stack_order: str = "CHANNEL FIRST"
    use_piezo: bool = False

    # Time series
    nt: int = 1
    delta_t_s: float = 0.0

    # Channels
    channel_names: List[str] = field(default_factory=list)

    # Autofocus
    contrast_af: bool = False
    laser_af: bool = False
    laser_af_per_stack: bool = False  # Z First only: laser AF before each later channel's stack

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
    # Set Z-range on a piezo z-stack, in piezo µm; files written before it existed have none.
    piezo_z_range_um: Optional[Tuple[float, float]] = None
    # Set Range with laser AF on: (z_min, z_max) in µm from the laser AF reference plane (never with piezo_z_range_um).
    laser_af_relative_range_um: Optional[Tuple[float, float]] = None
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
    if regions and any(not r.get("fovs") for r in regions) and os.path.isfile(csv_path):
        try:
            by_name = {r["name"]: r["fovs"] for r in read_coordinates_csv(csv_path)}
        except (ValueError, OSError) as e:
            # An empty or malformed sibling CSV (an aborted run) must not fail an otherwise valid acquisition.yaml.
            squid.logging.get_logger(__name__).warning(f"Ignoring {csv_path}: {e}")
            by_name = {}
        for region in regions:
            if not region.get("fovs") and region.get("name") in by_name:
                region["fovs"] = by_name[region["name"]]
    return parsed


def parse_z_stack_order(value) -> str:
    """Normalize a saved z_stack.order value to one of control._def.Z_STACK_ORDERS.

    Missing or null means "CHANNEL FIRST": files written before the setting existed were all acquired
    channel first. Case and "_"/"-" separators are ignored; anything else raises ValueError, so every
    loader (GUI drop, TCP, fluidics protocol) rejects a bad value up front instead of guessing.
    """
    import control._def

    if value is None:
        return "CHANNEL FIRST"
    normalized = " ".join(str(value).replace("_", " ").replace("-", " ").upper().split())
    if normalized not in control._def.Z_STACK_ORDERS:
        raise ValueError(f"Invalid z_stack.order '{value}'. Must be one of: {list(control._def.Z_STACK_ORDERS)}")
    return normalized


def parse_range(value) -> Optional[Tuple[float, float]]:
    """A saved (min, max) pair as two floats; None when missing or not a pair."""
    return (float(value[0]), float(value[1])) if value and len(value) == 2 else None


def parse_laser_af_relative_range(
    z_stack: dict, laser_af: bool, config: str, delta_z_um: float
) -> Optional[Tuple[float, float]]:
    """The saved z_stack.laser_af_relative_range_um as (min, max) µm from the laser AF reference, or None.

    Raises ValueError for a combination the GUI never saves: an absolute piezo range with laser AF on,
    both ranges together, a relative range with laser AF off, a config other than From Bottom, or a dz
    that is not finite and positive.
    """
    from control.core.relative_z_range import relative_range_problem

    relative = parse_range(z_stack.get("laser_af_relative_range_um"))
    absolute = parse_range(z_stack.get("piezo_z_range_um"))
    if relative is None:
        if absolute is not None and laser_af:
            raise ValueError(
                "An absolute piezo Z-range can't run with laser AF: with laser AF on, Set Range is relative to "
                "the laser AF reference. Set the range again, or turn laser AF off."
            )
        return None
    if absolute is not None:
        raise ValueError("The saved z_stack has both piezo_z_range_um and laser_af_relative_range_um: use one.")
    if not laser_af:
        raise ValueError("A relative Z-range needs laser AF: turn laser AF on, or set the range again.")
    problem = relative_range_problem(relative, delta_z_um, math.inf, str(config).upper())
    if problem:
        raise ValueError(problem)
    return relative


_FLAG_ON = {"true", "yes", "on", "1"}
_FLAG_OFF = {"false", "no", "off", "0", "none", ""}


def parse_laser_af_per_stack(value, laser_af: bool, z_stack_order: str) -> bool:
    """Normalize a saved autofocus.laser_af_per_stack value (missing or null means off).

    On, it needs laser AF and Z First: anything else is a configuration mistake the author should see,
    so every loader (GUI drop, TCP, fluidics protocol) raises ValueError instead of silently dropping it.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        on = value
    else:
        text = str(value).strip().lower()
        if text in _FLAG_ON:
            on = True
        elif text in _FLAG_OFF:
            on = False
        else:
            raise ValueError(f"Invalid autofocus.laser_af_per_stack '{value}'. Must be true or false")
    if on and not laser_af:
        raise ValueError("autofocus.laser_af_per_stack: true requires autofocus.laser_af: true")
    if on and z_stack_order != "Z FIRST":
        raise ValueError("autofocus.laser_af_per_stack: true requires z_stack.order: Z FIRST")
    return on


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

    z_stack_order = parse_z_stack_order(z_stack.get("order"))
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
        z_stack_order=z_stack_order,
        use_piezo=z_stack.get("use_piezo", False),
        # Time series
        nt=time_series.get("nt", 1),
        delta_t_s=time_series.get("delta_t_s", 0.0),
        # Channels
        channel_names=[ch.get("name") for ch in channels if ch.get("name")],
        # Autofocus
        contrast_af=autofocus.get("contrast_af", False),
        laser_af=autofocus.get("laser_af", False),
        laser_af_per_stack=parse_laser_af_per_stack(
            autofocus.get("laser_af_per_stack"),
            laser_af=bool(autofocus.get("laser_af", False)),
            z_stack_order=z_stack_order,
        ),
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
        z_range_mm=parse_range(z_stack.get("z_range_mm")),
        piezo_z_range_um=parse_range(z_stack.get("piezo_z_range_um")),
        laser_af_relative_range_um=parse_laser_af_relative_range(
            z_stack,
            laser_af=bool(autofocus.get("laser_af", False)),
            config=z_stack.get("config", "FROM BOTTOM"),
            delta_z_um=z_stack.get("delta_z_mm", 0.001) * 1000,
        ),
        skip_saving=bool(acq.get("skip_saving", False)),
    )


def read_coordinates_csv(path: str) -> List[dict]:
    """Read a Squid coordinates.csv into [{"name": region, "fovs": [[x, y(, z)], ...]}, ...] in file order.

    The z column is used only when present and filled for every row (matching
    control.widgets.load_coordinate_regions_from_dataframe). A provenance stamp line written by
    "Save Coordinates" (control.core.coordinate_provenance) is skipped, so a saved-coordinates file
    can stand in for coordinates.csv. Raises ValueError on missing columns.
    """
    from control.core.coordinate_provenance import STAMP_PREFIX

    with open(path, newline="", encoding="utf-8") as f:
        first = f.readline()
        if not first.startswith(STAMP_PREFIX):
            f.seek(0)
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
    piezo_mismatch: bool = False
    current_objective: str = ""
    yaml_objective: str = ""
    current_binning: Tuple[int, int] = (1, 1)
    yaml_binning: Tuple[int, int] = (1, 1)
    message: str = ""


def validate_hardware(
    yaml_data: AcquisitionYAMLData,
    current_objective: str,
    current_binning: Tuple[int, int],
    piezo_travel_um: Optional[float] = None,
    stepper_travel_um: Optional[float] = None,
) -> ValidationResult:
    """Validate that YAML settings match current hardware configuration.

    Args:
        yaml_data: Parsed YAML data
        current_objective: Currently selected objective name
        current_binning: Current camera binning as (x, y) tuple
        piezo_travel_um: This microscope's piezo travel, or None when it has no piezo
        stepper_travel_um: The stepper Z span in µm (default: the software position limits), used for a
            relative range on a scope without a piezo

    Returns:
        ValidationResult indicating whether hardware matches
    """
    objective_mismatch = False
    binning_mismatch = False
    piezo_mismatch = False
    messages = []

    if yaml_data.objective_name and yaml_data.objective_name != current_objective:
        objective_mismatch = True
        messages.append(f"Objective mismatch:\n  YAML: '{yaml_data.objective_name}'\n  Current: '{current_objective}'")

    if yaml_data.camera_binning and tuple(yaml_data.camera_binning) != tuple(current_binning):
        binning_mismatch = True
        messages.append(
            f"Camera binning mismatch:\n  YAML: {list(yaml_data.camera_binning)}\n  Current: {list(current_binning)}"
        )

    if yaml_data.use_piezo and piezo_travel_um is None:
        piezo_mismatch = True
        messages.append("Piezo mismatch:\n  YAML: z-stack on the piezo\n  Current: this microscope has no piezo")
    elif yaml_data.use_piezo and yaml_data.piezo_z_range_um:
        z_min_um, z_max_um = yaml_data.piezo_z_range_um
        if not (math.isfinite(z_min_um) and math.isfinite(z_max_um) and z_min_um <= z_max_um):
            piezo_mismatch = True
            messages.append(
                f"Piezo Z-range not in order:\n  YAML: {z_min_um:.1f} to {z_max_um:.1f} µm\n"
                "  Z-min must be a number at or below Z-max"
            )
        elif z_min_um < 0 or z_max_um > piezo_travel_um:
            piezo_mismatch = True
            messages.append(
                f"Piezo Z-range outside the travel:\n  YAML: {z_min_um:.1f} to {z_max_um:.1f} µm\n"
                f"  Current: piezo travel 0 to {piezo_travel_um:.0f} µm"
            )

    rel = yaml_data.laser_af_relative_range_um
    if rel is not None:
        import control._def as _def
        from control.core.relative_z_range import nz_for_range

        span_um = (nz_for_range(rel[0], rel[1], yaml_data.delta_z_um) - 1) * yaml_data.delta_z_um
        if piezo_travel_um is not None and not yaml_data.use_piezo:
            # Laser AF drives the piezo on this scope, so the stack must be on it.
            piezo_mismatch = True
            messages.append(
                "Piezo mismatch:\n  YAML: relative Z-range on the stepper\n"
                "  Current: this microscope's laser AF drives the piezo"
            )
        else:
            if piezo_travel_um is not None:
                travel_um = piezo_travel_um
            elif stepper_travel_um is not None:
                travel_um = stepper_travel_um
            else:
                travel_um = (_def.SOFTWARE_POS_LIMIT.Z_POSITIVE - _def.SOFTWARE_POS_LIMIT.Z_NEGATIVE) * 1000
            if span_um > travel_um:
                piezo_mismatch = True
                messages.append(
                    f"Relative Z-range longer than the travel:\n  YAML: {span_um:.1f} µm\n"
                    f"  Current: travel {travel_um:.0f} µm"
                )

    return ValidationResult(
        is_valid=not (objective_mismatch or binning_mismatch or piezo_mismatch),
        objective_mismatch=objective_mismatch,
        binning_mismatch=binning_mismatch,
        piezo_mismatch=piezo_mismatch,
        current_objective=current_objective,
        yaml_objective=yaml_data.objective_name or "",
        current_binning=current_binning,
        yaml_binning=yaml_data.camera_binning or (1, 1),
        message="\n\n".join(messages) if messages else "",
    )
