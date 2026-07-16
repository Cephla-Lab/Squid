"""
Utilities for parsing and validating acquisition YAML files.
"""

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

    # Flexible-specific
    nx: int = 1
    ny: int = 1
    delta_x_mm: float = 0.9
    delta_y_mm: float = 0.9
    flexible_positions: Optional[List[Dict]] = None  # [{name, center_mm}, ...]


@dataclass
class RecordZStackYAMLData:
    """Parsed record/z-stack acquisition YAML data structure."""

    widget_type: str  # "record_zstack"
    xy_mode: str = "Select Wells"

    objective_name: Optional[str] = None
    camera_binning: Optional[Tuple[int, int]] = None

    nt: int = 1
    delta_t_s: float = 0.0

    laser_af: bool = False

    recording_enabled: bool = False
    recording_channel: Optional[Dict] = None
    fps: float = 10.0
    duration_s: float = 1.0
    recording_bottom_z_offset_um: float = 0.0
    recording_nz: int = 1
    recording_dz_um: float = 1.0

    zstack_enabled: bool = False
    zstack_channels: List[Dict] = field(default_factory=list)
    z_min_um: float = -3.0
    z_max_um: float = 3.0
    z_step_um: float = 1.0

    scan_size_mm: Optional[float] = None
    overlap_percent: float = 10.0
    wellplate_regions: Optional[List[Dict]] = None


def _parse_camera_binning(obj: dict) -> Optional[Tuple[int, int]]:
    binning = obj.get("camera_binning")
    if binning and isinstance(binning, list) and len(binning) == 2:
        return tuple(binning)
    return None


def _parse_record_zstack_yaml_data(data: dict, acq: dict) -> RecordZStackYAMLData:
    obj = data.get("objective", {})
    time_series = data.get("time_series", {})
    autofocus = data.get("autofocus", {})
    recording = data.get("recording", {})
    z_stack = data.get("z_stack", {})
    wellplate_scan = data.get("wellplate_scan", {})

    return RecordZStackYAMLData(
        widget_type="record_zstack",
        xy_mode=acq.get("xy_mode", "Select Wells"),
        objective_name=obj.get("name"),
        camera_binning=_parse_camera_binning(obj),
        nt=time_series.get("nt", 1),
        delta_t_s=time_series.get("delta_t_s", 0.0),
        laser_af=autofocus.get("laser_af", False),
        recording_enabled=recording.get("enabled", False),
        recording_channel=recording.get("channel"),
        fps=recording.get("fps", 10.0),
        duration_s=recording.get("duration_s", 1.0),
        recording_bottom_z_offset_um=recording.get("bottom_z_offset_um", 0.0),
        recording_nz=recording.get("nz", 1),
        recording_dz_um=recording.get("dz_um", 1.0),
        zstack_enabled=z_stack.get("enabled", False),
        zstack_channels=z_stack.get("channels", []),
        z_min_um=z_stack.get("z_min_um", -3.0),
        z_max_um=z_stack.get("z_max_um", 3.0),
        z_step_um=z_stack.get("z_step_um", 1.0),
        scan_size_mm=wellplate_scan.get("scan_size_mm"),
        overlap_percent=wellplate_scan.get("overlap_percent", 10.0),
        wellplate_regions=wellplate_scan.get("regions"),
    )


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
    VALID_WIDGET_TYPES = ("wellplate", "flexible", "record_zstack")
    widget_type = acq.get("widget_type", "wellplate")
    if widget_type not in VALID_WIDGET_TYPES:
        raise ValueError(f"Invalid widget_type '{widget_type}'. Must be one of: {VALID_WIDGET_TYPES}")

    if widget_type == "record_zstack":
        return _parse_record_zstack_yaml_data(data, acq)

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
