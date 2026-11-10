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


@dataclass
class RecordZStackYAMLData:
    """Parsed "Time at XY" (widget_type record_zstack) acquisition YAML data structure."""

    widget_type: str  # "record_zstack"
    xy_mode: str = "Select Wells"

    objective_name: Optional[str] = None
    camera_binning: Optional[Tuple[int, int]] = None

    nt: int = 1
    delta_t_s: float = 0.0

    laser_af: bool = False
    contrast_af: bool = False
    use_focus_map: bool = False

    # Channel table rows: [{channel: {...}, fps, duration_s, zi, r, zf, camera?}, ...]
    channels: List[Dict] = field(default_factory=list)

    # Which of the shared z planes the recordings visit (preset roles + explicit indices)
    record_slice_roles: List[str] = field(default_factory=lambda: ["bottom", "mid", "top"])
    record_slice_indices: List[int] = field(default_factory=list)

    # Shared z-stack geometry (mode labels: control.core.zstack_modes.Z_MODES)
    z_mode: str = "From Bottom"
    z_dz_um: float = 1.0
    z_nz: int = 1
    z_range_um: float = 4.0
    z_min_mm: float = 0.0
    z_max_mm: float = 0.0

    scan_size_mm: Optional[float] = None
    overlap_percent: float = 10.0
    wellplate_regions: Optional[List[Dict]] = None

    # True when the file used the pre-table schema (recording/z_stack phases)
    # and was converted; the widget logs it so the user knows to re-save.
    legacy_schema: bool = False
    # What the conversion could not carry over exactly (empty when the converted
    # acquisition visits the same planes with the same settings as the old one).
    legacy_notes: List[str] = field(default_factory=list)


def _parse_camera_binning(obj: dict) -> Optional[Tuple[int, int]]:
    binning = obj.get("camera_binning")
    if binning and isinstance(binning, list) and len(binning) == 2:
        return tuple(binning)
    return None


_LEGACY_Z_TOLERANCE_UM = 1e-6


def _legacy_stack_plan(z_min: float, z_max: float, step: float, notes: List[str]) -> Tuple[Dict, List[float]]:
    """The z plan for a pre-table z range (µm, relative to the reference plane) and its plane offsets.

    The old stack visited z_min, z_min + step, ... up to z_max.  It is reproduced exactly as
    Relative when it has a plane on the reference and is symmetric about it, and as From
    Bottom when it starts on the reference.  Anything else has no equivalent (the table's z
    block has no bottom offset): a roughly centred range becomes Relative and the rest From
    Bottom, with a note saying how the planes moved.
    """
    count = int(math.floor((z_max - z_min) / step + 1e-9)) + 1
    plan: Dict = {"z_dz_um": step, "z_nz": 1, "z_range_um": 4.0, "z_min_mm": 0.0, "z_max_mm": 0.0}
    top = z_min + (count - 1) * step
    if count >= 3 and count % 2 == 1 and abs(z_min + top) <= _LEGACY_Z_TOLERANCE_UM:
        plan["z_mode"] = "Relative"
        plan["z_range_um"] = top - z_min
        nz = count
    elif abs(z_min) <= _LEGACY_Z_TOLERANCE_UM:
        plan["z_mode"] = "From Bottom"
        plan["z_nz"] = nz = count
    elif count > 1 and abs(z_min + z_max) <= step / 2:
        from control.core.zstack_modes import relative_stack_Nz

        plan["z_mode"] = "Relative"
        plan["z_range_um"] = z_max - z_min
        nz = relative_stack_Nz(z_max - z_min, step)
        notes.append(
            f"z-stack {z_min:g}..{z_max:g} µm in {step:g} µm steps has no plane on the reference; loaded as a "
            f"Relative stack of {nz} planes (±{(nz - 1) * step / 2:g} µm), so the planes are not the same"
        )
    else:
        plan["z_mode"] = "From Bottom"
        plan["z_nz"] = nz = count
        notes.append(
            f"z-stack {z_min:g}..{z_max:g} µm is not centred on the reference; loaded as From Bottom with "
            f"{count} planes starting AT the reference (the {z_min:g} µm bottom offset is dropped)"
        )
    base = -((nz - 1) // 2) * step if plan["z_mode"] == "Relative" else 0.0
    return plan, [base + i * step for i in range(nz)]


def _legacy_recording_plan(bottom: float, nz: int, dz: float, notes: List[str]) -> Tuple[Dict, List[float], List[int]]:
    """A z plan that contains the pre-table recording planes (bottom + j*dz), and their indices in it.

    Used when the old file had no z-stack, so the plan only has to place the recordings:
    planes the recordings do not visit are never acquired.
    """
    plan: Dict = {"z_mode": "From Bottom", "z_dz_um": dz, "z_nz": nz, "z_range_um": 4.0, "z_min_mm": 0.0}
    plan["z_max_mm"] = 0.0
    tol = _LEGACY_Z_TOLERANCE_UM
    if abs(bottom) <= tol:
        return plan, [j * dz for j in range(nz)], list(range(nz))
    if nz == 1:
        # One plane off the reference: a two-plane From Bottom stack (record Top) when it is
        # above, a three-plane Relative stack (record Bottom) when it is below.
        plan["z_dz_um"] = abs(bottom)
        if bottom > 0:
            plan["z_nz"] = 2
            return plan, [0.0, bottom], [1]
        plan["z_mode"] = "Relative"
        plan["z_range_um"] = 2 * abs(bottom)
        plan["z_nz"] = 3
        return plan, [bottom, 0.0, -bottom], [0]
    steps = bottom / dz if dz > 0 else float("nan")
    k = int(round(steps)) if steps == steps else 0
    if dz > 0 and abs(steps - k) * dz <= tol:
        if k > 0:
            plan["z_nz"] = k + nz
            return plan, [i * dz for i in range(k + nz)], [k + j for j in range(nz)]
        # The planes straddle or lie below the reference: a Relative stack wide enough for both ends.
        below = -k
        half = max(below, nz - 1 - below, 1)
        plan["z_mode"] = "Relative"
        plan["z_range_um"] = 2 * half * dz
        plan["z_nz"] = 2 * half + 1
        return plan, [(i - half) * dz for i in range(2 * half + 1)], [half - below + j for j in range(nz)]
    notes.append(
        f"recording planes start {bottom:g} µm from the reference, which is not a whole number of {dz:g} µm "
        f"steps; loaded as From Bottom with {nz} planes starting AT the reference (the offset is dropped)"
    )
    return plan, [j * dz for j in range(nz)], list(range(nz))


def _convert_legacy_record_zstack(recording: dict, z_stack: dict) -> Tuple[List[Dict], Dict, Dict, List[str]]:
    """Map the pre-table ``recording:``/``z_stack:`` sections onto channel rows, a z plan and record planes.

    Returns ``(rows, z_plan, record_slices, notes)``.  The old tab ran (optional) recording then
    (optional) z-stack per FOV, so the old z-stack becomes the *final* stack (zf) and the old
    recording channel a recording row (r); a phase that was switched off keeps its rows, unticked,
    so the settings are not lost.  A channel present in both merges into one row (the recording
    copy's exposure/gain/illumination win, as the old snapshot dedupe did).

    The old tab had two independent z definitions, both relative to the reference plane: the
    recording planes (bottom offset, nz, dz) and the z-stack range (z_min, z_max, step).  The
    table has one z block for every phase plus a choice of which of its planes are recorded, so:
    with a z-stack, the block reproduces the stack and each recording plane is mapped to the stack
    plane nearest to it; without one, the block is built to contain the recording planes.  Set
    Range is never chosen because the old values were not absolute.  ``notes`` lists everything
    that could not be carried over exactly; it is empty when the converted acquisition visits the
    same planes as the old one.  ``effective_fps`` is informational and is resolved again per run.
    """
    notes: List[str] = []
    rows: List[Dict] = []
    by_name: Dict[str, Dict] = {}

    rec_channel = recording.get("channel")
    rec_on = bool(recording.get("enabled", False)) and bool(rec_channel)
    if rec_channel:
        row = {
            "channel": rec_channel,
            "fps": recording.get("fps", 10.0),
            "duration_s": recording.get("duration_s", 1.0),
            "zi": False,
            "r": rec_on,
            "zf": False,
        }
        rows.append(row)
        if isinstance(rec_channel, dict) and rec_channel.get("name"):
            by_name[rec_channel["name"]] = row

    stack_channels = z_stack.get("channels", []) or []
    stack_on = bool(z_stack.get("enabled", False)) and bool(stack_channels)
    for ch in stack_channels:
        name = ch.get("name") if isinstance(ch, dict) else None
        if name and name in by_name:
            by_name[name]["zf"] = by_name[name]["zf"] or stack_on
            if stack_on and by_name[name]["channel"] != ch:
                notes.append(
                    f"channel {name!r} was in both phases with different settings; merged into one row "
                    "(the recording exposure/gain/illumination are kept for the z-stack too)"
                )
            continue
        row = {"channel": ch, "fps": 10.0, "duration_s": 1.0, "zi": False, "r": False, "zf": stack_on}
        rows.append(row)
        if name:
            by_name[name] = row

    z_min = float(z_stack.get("z_min_um", -3.0))
    z_max = float(z_stack.get("z_max_um", 3.0))
    step = float(z_stack.get("z_step_um", 1.0))
    bottom = float(recording.get("bottom_z_offset_um", 0.0) or 0.0)
    rec_nz = max(1, int(recording.get("nz", 1) or 1))
    rec_dz = float(recording.get("dz_um", 1.0) or 1.0)
    rec_offsets = [bottom + j * rec_dz for j in range(rec_nz)]

    z_plan: Dict = {"z_mode": "From Bottom", "z_dz_um": 1.0, "z_nz": 1, "z_range_um": 4.0}
    z_plan.update({"z_min_mm": 0.0, "z_max_mm": 0.0})
    recorded: Optional[List[int]] = None  # None = the tab's default (Bottom / Mid / Top)
    if stack_on and step > 0 and z_max >= z_min:
        z_plan, offsets = _legacy_stack_plan(z_min, z_max, step, notes)
        if rec_on:
            recorded = sorted({min(range(len(offsets)), key=lambda i: abs(offsets[i] - off)) for off in rec_offsets})
            moved = [
                f"{off:+g} -> {offsets[min(range(len(offsets)), key=lambda i: abs(offsets[i] - off))]:+g}"
                for off in rec_offsets
                if min(abs(o - off) for o in offsets) > _LEGACY_Z_TOLERANCE_UM
            ]
            if moved or len(recorded) != len(rec_offsets):
                notes.append(
                    "the recording planes are not planes of the z-stack; each is recorded at the nearest "
                    f"stack plane instead (µm from the reference: {', '.join(moved) or 'planes merged'})"
                )
    elif rec_on:
        z_plan, offsets, recorded = _legacy_recording_plan(bottom, rec_nz, rec_dz, notes)
    elif any(k in z_stack for k in ("z_min_um", "z_max_um", "z_step_um")) and step > 0 and z_max >= z_min:
        # Nothing is acquired from this file as it stands; keep the z range for when a row is ticked.
        z_plan, offsets = _legacy_stack_plan(z_min, z_max, step, [])

    if recorded is None:
        slices = {"roles": ["bottom", "mid", "top"], "indices": []}
    else:
        nz = len(offsets)
        role_index = {"bottom": 0, "mid": (nz - 1) // 2, "top": nz - 1}
        slices = {
            "roles": [role for role, i in role_index.items() if i in recorded],
            "indices": [i for i in recorded if i not in role_index.values()],
        }
    return rows, z_plan, slices, notes


def _parse_record_zstack_yaml_data(data: dict, acq: dict) -> RecordZStackYAMLData:
    obj = data.get("objective", {})
    time_series = data.get("time_series", {})
    autofocus = data.get("autofocus", {})
    recording = data.get("recording", {}) or {}
    z_stack = data.get("z_stack", {}) or {}
    wellplate_scan = data.get("wellplate_scan", {})

    legacy = "channels" not in data
    legacy_notes: List[str] = []
    if legacy:
        channels, z_plan, record_slices, legacy_notes = _convert_legacy_record_zstack(recording, z_stack)
    else:
        channels = list(data.get("channels", []) or [])
        record_slices = data.get("record_slices", {}) or {}
        z_plan = {
            "z_mode": z_stack.get("mode", "From Bottom"),
            "z_dz_um": z_stack.get("dz_um", 1.0),
            "z_nz": z_stack.get("nz", 1),
            "z_range_um": z_stack.get("z_range_um", 4.0),
            "z_min_mm": z_stack.get("z_min_mm", 0.0),
            "z_max_mm": z_stack.get("z_max_mm", 0.0),
        }

    return RecordZStackYAMLData(
        widget_type="record_zstack",
        xy_mode=acq.get("xy_mode", "Select Wells"),
        objective_name=obj.get("name"),
        camera_binning=_parse_camera_binning(obj),
        nt=time_series.get("nt", 1),
        delta_t_s=time_series.get("delta_t_s", 0.0),
        laser_af=autofocus.get("laser_af", False),
        contrast_af=autofocus.get("contrast_af", False),
        use_focus_map=autofocus.get("use_focus_map", False),
        channels=channels,
        record_slice_roles=list(record_slices.get("roles", ["bottom", "mid", "top"]) or []),
        record_slice_indices=[int(i) for i in (record_slices.get("indices", []) or [])],
        z_mode=z_plan["z_mode"],
        z_dz_um=z_plan["z_dz_um"],
        z_nz=z_plan["z_nz"],
        z_range_um=z_plan["z_range_um"],
        z_min_mm=z_plan["z_min_mm"],
        z_max_mm=z_plan["z_max_mm"],
        scan_size_mm=wellplate_scan.get("scan_size_mm"),
        overlap_percent=wellplate_scan.get("overlap_percent", 10.0),
        wellplate_regions=wellplate_scan.get("regions"),
        legacy_schema=legacy,
        legacy_notes=legacy_notes,
    )


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
    # record_zstack YAMLs carry wellplate-style regions and no flexible_positions.
    regions = parsed.flexible_positions if parsed.widget_type == "flexible" else parsed.wellplate_regions
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
    VALID_WIDGET_TYPES = ("wellplate", "flexible", "record_zstack")
    widget_type = acq.get("widget_type", "wellplate")
    if widget_type not in VALID_WIDGET_TYPES:
        raise ValueError(f"Invalid widget_type '{widget_type}'. Must be one of: {VALID_WIDGET_TYPES}")

    if widget_type == "record_zstack":
        return _parse_record_zstack_yaml_data(data, acq)

    # Parse camera binning
    camera_binning = _parse_camera_binning(obj)

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
        z_stack_order=parse_z_stack_order(z_stack.get("order")),
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
