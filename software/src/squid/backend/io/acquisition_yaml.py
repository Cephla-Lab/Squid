"""
Acquisition YAML save/load utilities.

This module provides functionality for saving acquisition parameters to YAML when
acquisitions start, and loading them back via drag-and-drop on multipoint widgets.

Ported from upstream commit 88db4da8.

Typical usage:
    # Saving (called automatically by MultiPointController)
    save_acquisition_yaml(acquisition_params, experiment_path, ...)

    # Loading (called by widget drag-drop handler)
    yaml_data = parse_acquisition_yaml(file_path)
    validation = validate_hardware(yaml_data, current_objective, current_binning)
    if validation.is_valid:
        widget._apply_yaml_settings(yaml_data)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import yaml

import squid.core.logging
from squid.core.events import AutofocusMode, FocusLockSettings

if TYPE_CHECKING:
    from squid.backend.controllers.multipoint.multi_point_utils import AcquisitionParameters

_log = squid.core.logging.get_logger(__name__)


# =============================================================================
# Data Structures for Parsed YAML
# =============================================================================


@dataclass
class AcquisitionYAMLData:
    """Parsed acquisition YAML data structure.

    This dataclass represents the parsed contents of an acquisition.yaml file.
    All fields have sensible defaults for graceful handling of missing data.
    """

    widget_type: str  # "wellplate" or "flexible"
    xy_mode: str = "Select Wells"

    # Objective info
    objective_name: Optional[str] = None
    objective_magnification: Optional[float] = None
    objective_pixel_size_um: Optional[float] = None
    camera_binning: Optional[Tuple[int, int]] = None

    # Z-stack
    nz: int = 1
    delta_z_um: float = 1.0  # Stored in um
    z_stacking_config: str = "FROM BOTTOM"
    use_piezo: bool = False

    # Time series
    nt: int = 1
    delta_t_s: float = 0.0

    # Channels
    channel_names: List[str] = field(default_factory=list)

    # Autofocus
    autofocus_mode: str = AutofocusMode.NONE.value
    autofocus_interval_fovs: int = 1

    # Focus lock (continuous focus tracking during acquisition)
    focus_lock_enabled: bool = False
    focus_lock_buffer_length: int = 5
    focus_lock_recovery_attempts: int = 3
    focus_lock_min_spot_snr: float = 10.0
    focus_lock_acquire_threshold_um: float = 0.25
    focus_lock_maintain_threshold_um: float = 0.5

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
class ValidationResult:
    """Result of hardware validation against YAML settings."""

    is_valid: bool
    objective_mismatch: bool = False
    binning_mismatch: bool = False
    current_objective: str = ""
    yaml_objective: str = ""
    current_binning: Tuple[int, int] = (1, 1)
    yaml_binning: Tuple[int, int] = (1, 1)
    message: str = ""


# =============================================================================
# YAML Parsing
# =============================================================================

VALID_WIDGET_TYPES = ("wellplate", "flexible")


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
    focus_lock = data.get("focus_lock", {})
    wellplate_scan = data.get("wellplate_scan", {})
    flexible_scan = data.get("flexible_scan", {})

    # Validate widget_type
    widget_type = acq.get("widget_type", "wellplate")
    if widget_type not in VALID_WIDGET_TYPES:
        raise ValueError(f"Invalid widget_type '{widget_type}'. Must be one of: {VALID_WIDGET_TYPES}")

    # Parse camera binning
    binning = obj.get("camera_binning")
    if binning and isinstance(binning, list) and len(binning) == 2:
        camera_binning = (int(binning[0]), int(binning[1]))
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

    # Extract channel names, filtering out entries without names
    channel_names = [ch.get("name") for ch in channels if ch.get("name")]

    return AcquisitionYAMLData(
        widget_type=widget_type,
        xy_mode=acq.get("xy_mode", "Select Wells"),
        # Objective info
        objective_name=obj.get("name"),
        objective_magnification=obj.get("magnification"),
        objective_pixel_size_um=obj.get("pixel_size_um"),
        camera_binning=camera_binning,
        # Z-stack (convert mm to um if stored in mm)
        nz=z_stack.get("nz", 1),
        delta_z_um=_parse_z_delta(z_stack),
        z_stacking_config=z_stack.get("config", z_stack.get("stacking_direction", "FROM BOTTOM")),
        use_piezo=z_stack.get("use_piezo", False),
        # Time series
        nt=time_series.get("nt", 1),
        delta_t_s=time_series.get("delta_t_s", time_series.get("dt_s", 0.0)),
        # Channels
        channel_names=channel_names,
        # Autofocus
        autofocus_mode=autofocus.get("mode", AutofocusMode.NONE.value),
        autofocus_interval_fovs=autofocus.get("interval_fovs", 1),
        # Focus lock (continuous focus tracking during acquisition)
        focus_lock_enabled=focus_lock.get(
            "enabled", autofocus.get("mode") == AutofocusMode.FOCUS_LOCK.value
        ),
        focus_lock_buffer_length=focus_lock.get("buffer_length", 5),
        focus_lock_recovery_attempts=focus_lock.get("recovery_attempts", 3),
        focus_lock_min_spot_snr=focus_lock.get("min_spot_snr", 10.0),
        focus_lock_acquire_threshold_um=focus_lock.get("acquire_threshold_um", 0.25),
        focus_lock_maintain_threshold_um=focus_lock.get("maintain_threshold_um", 0.5),
        # Wellplate-specific
        scan_size_mm=wellplate_scan.get("scan_size_mm"),
        overlap_percent=overlap,
        scan_shape=scan_shape,
        wellplate_regions=wellplate_regions,
        # Flexible-specific
        nx=flexible_scan.get("nx", 1),
        ny=flexible_scan.get("ny", 1),
        delta_x_mm=flexible_scan.get("delta_x_mm", flexible_scan.get("dx_mm", 0.9)),
        delta_y_mm=flexible_scan.get("delta_y_mm", flexible_scan.get("dy_mm", 0.9)),
        flexible_positions=flexible_scan.get("positions"),
    )


def _parse_z_delta(z_stack: Dict) -> float:
    """Parse Z delta from z_stack section, handling both um and mm formats."""
    # Prefer delta_z_um if present
    if "delta_z_um" in z_stack:
        return float(z_stack["delta_z_um"])
    # Fall back to delta_z_mm converted to um
    if "delta_z_mm" in z_stack:
        return float(z_stack["delta_z_mm"]) * 1000.0
    # Default
    return 1.0


# =============================================================================
# Hardware Validation
# =============================================================================


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


# =============================================================================
# YAML Serialization Helpers
# =============================================================================


def _serialize_for_yaml(obj: Any) -> Any:
    """Recursively serialize objects to YAML-compatible types.

    Handles:
    - Enums → their .value
    - numpy arrays → lists
    - numpy scalars → Python scalars
    - dataclasses → dicts
    - Pydantic models → dicts (via model_dump)
    """
    if obj is None:
        return None
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, np.ndarray):
        return [_serialize_for_yaml(item) for item in obj.tolist()]
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif hasattr(obj, "__dataclass_fields__"):
        # dataclass
        import dataclasses

        return {k: _serialize_for_yaml(v) for k, v in dataclasses.asdict(obj).items()}
    elif hasattr(obj, "model_dump"):
        # Pydantic model
        return _serialize_for_yaml(obj.model_dump())
    elif isinstance(obj, dict):
        return {k: _serialize_for_yaml(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_yaml(item) for item in obj]
    else:
        return obj


# =============================================================================
# YAML Saving
# =============================================================================


def save_acquisition_yaml(
    params: AcquisitionParameters,
    experiment_path: str,
    region_shapes: Optional[Dict[str, str]] = None,
    widget_type: str = "wellplate",
    objective_info: Optional[Dict[str, Any]] = None,
    wellplate_format: Optional[str] = None,
    scan_size_mm: float = 0.0,
    overlap_percent: float = 10.0,
    focus_lock_settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Save acquisition parameters to YAML file.

    This is called automatically when an acquisition starts, saving all parameters
    to `acquisition.yaml` in the experiment folder. The saved file can later be
    loaded via drag-and-drop to restore settings.

    Args:
        params: AcquisitionParameters dataclass from build_params()
        experiment_path: Path to experiment folder
        region_shapes: Optional dict of {region_id: shape} from ScanCoordinates
        widget_type: "wellplate" or "flexible"
        objective_info: Dict with objective name, magnification, pixel_size_um
        wellplate_format: String like "384 well plate" or None
        scan_size_mm: Scan size in mm (for wellplate mode)
        overlap_percent: FOV overlap percentage
        focus_lock_settings: Optional dict with focus lock parameters:
            - enabled: bool
            - buffer_length: int
            - recovery_attempts: int
            - min_spot_snr: float
            - acquire_threshold_um: float
            - maintain_threshold_um: float
    """
    # Build acquisition section
    yaml_dict: Dict[str, Any] = {
        "acquisition": {
            "experiment_id": params.experiment_ID,
            "start_time": params.acquisition_start_time,
            "widget_type": widget_type,
            "xy_mode": params.xy_mode,
            "skip_saving": params.skip_saving,
        },
        "objective": objective_info or {},
        "sample": {
            "wellplate_format": wellplate_format,
        },
        "z_stack": {
            "nz": params.NZ,
            "delta_z_um": params.deltaZ,  # Already in um
            "config": params.z_stacking_config,
            "z_range": _serialize_for_yaml(params.z_range) if params.z_range else None,
            "use_piezo": params.use_piezo,
        },
        "time_series": {
            "nt": params.Nt,
            "delta_t_s": params.deltat,
        },
        "autofocus": {
            "mode": _serialize_for_yaml(params.autofocus_mode),
            "interval_fovs": params.autofocus_interval_fovs,
        },
        "channels": [_serialize_for_yaml(ch) for ch in params.selected_configurations],
    }

    # Add focus lock section (optional override + acquisition parameters)
    fl_settings = focus_lock_settings
    if fl_settings is None and isinstance(params.focus_lock_settings, FocusLockSettings):
        fl_settings = {
            "buffer_length": params.focus_lock_settings.buffer_length,
            "recovery_attempts": params.focus_lock_settings.recovery_attempts,
            "min_spot_snr": params.focus_lock_settings.min_spot_snr,
            "acquire_threshold_um": params.focus_lock_settings.acquire_threshold_um,
            "maintain_threshold_um": params.focus_lock_settings.maintain_threshold_um,
            "auto_search_enabled": params.focus_lock_settings.auto_search_enabled,
            "lock_timeout_s": params.focus_lock_settings.lock_timeout_s,
        }
    if fl_settings:
        yaml_dict["focus_lock"] = {
            "enabled": params.autofocus_mode == AutofocusMode.FOCUS_LOCK,
            "buffer_length": fl_settings.get("buffer_length", 5),
            "recovery_attempts": fl_settings.get("recovery_attempts", 3),
            "min_spot_snr": fl_settings.get("min_spot_snr", 10.0),
            "acquire_threshold_um": fl_settings.get("acquire_threshold_um", 0.25),
            "maintain_threshold_um": fl_settings.get("maintain_threshold_um", 0.5),
            "auto_search_enabled": fl_settings.get("auto_search_enabled", False),
            "lock_timeout_s": fl_settings.get("lock_timeout_s", 5.0),
        }

    # Add widget-specific scan section
    scan_info = params.scan_position_information
    if widget_type == "wellplate":
        yaml_dict["wellplate_scan"] = {
            "scan_size_mm": scan_size_mm,
            "overlap_percent": overlap_percent,
            "regions": [
                {
                    "name": name,
                    "center_mm": _serialize_for_yaml(center),
                    "shape": region_shapes.get(name) if region_shapes else None,
                }
                for name, center in zip(
                    scan_info.scan_region_names,
                    scan_info.scan_region_coords_mm,
                )
            ],
        }
    else:  # flexible
        yaml_dict["flexible_scan"] = {
            "nx": params.NX,
            "ny": params.NY,
            "delta_x_mm": params.deltaX,
            "delta_y_mm": params.deltaY,
            "overlap_percent": overlap_percent,
            "positions": [
                {
                    "name": name,
                    "center_mm": _serialize_for_yaml(center),
                }
                for name, center in zip(
                    scan_info.scan_region_names,
                    scan_info.scan_region_coords_mm,
                )
            ],
        }

    # Add remaining common sections
    yaml_dict["downsampled_views"] = {
        "enabled": params.generate_downsampled_views,
        "save_well_images": params.save_downsampled_well_images,
        "well_resolutions_um": _serialize_for_yaml(params.downsampled_well_resolutions_um),
        "plate_resolution_um": params.downsampled_plate_resolution_um,
        "z_projection": _serialize_for_yaml(params.downsampled_z_projection),
        "interpolation_method": _serialize_for_yaml(params.downsampled_interpolation_method),
    }
    yaml_dict["plate"] = {
        "num_rows": params.plate_num_rows,
        "num_cols": params.plate_num_cols,
    }
    yaml_dict["fluidics"] = {
        "enabled": params.use_fluidics,
    }

    yaml_path = os.path.join(experiment_path, "acquisition.yaml")
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(f"# Acquisition Parameters - {params.experiment_ID}\n")
            f.write(f"# Saved automatically when acquisition started\n\n")
            yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        _log.info(f"Saved acquisition parameters to: {yaml_path}")
    except (OSError, yaml.YAMLError) as exc:
        _log.error(f"Failed to write acquisition YAML file '{yaml_path}': {exc}")


# =============================================================================
# Acquisition Preset Save/Load (before acquisition starts)
# =============================================================================


def save_acquisition_preset(
    experiment_path: str,
    experiment_id: str,
    widget_type: str,
    objective_info: Optional[Dict[str, Any]] = None,
    z_stack_settings: Optional[Dict[str, Any]] = None,
    time_series_settings: Optional[Dict[str, Any]] = None,
    channel_names: Optional[List[str]] = None,
    autofocus_settings: Optional[Dict[str, Any]] = None,
    focus_lock_settings: Optional[Dict[str, Any]] = None,
    flexible_scan_settings: Optional[Dict[str, Any]] = None,
    wellplate_scan_settings: Optional[Dict[str, Any]] = None,
) -> str:
    """Save acquisition preset settings to YAML file before acquisition starts.

    This allows users to save their acquisition configuration for later reuse.
    The preset can be loaded and used as a starting point for new acquisitions.

    Args:
        experiment_path: Path to experiment folder
        experiment_id: Experiment identifier
        widget_type: "wellplate" or "flexible"
        objective_info: Dict with objective name, magnification, pixel_size_um, binning
        z_stack_settings: Dict with nz, delta_z_um, config, use_piezo
        time_series_settings: Dict with nt, delta_t_s
        channel_names: List of channel names (in imaging order)
        autofocus_settings: Dict with mode and interval_fovs
        focus_lock_settings: Dict with enabled, buffer_length, recovery_attempts, etc.
        flexible_scan_settings: Dict with nx, ny, delta_x_mm, delta_y_mm, overlap_percent, positions
        wellplate_scan_settings: Dict with scan_size_mm, overlap_percent, shape, regions

    Returns:
        Path to the saved YAML file.

    Raises:
        OSError: If directory doesn't exist or file cannot be written.
    """
    yaml_dict: Dict[str, Any] = {
        "acquisition": {
            "experiment_id": experiment_id,
            "widget_type": widget_type,
        },
        "objective": objective_info or {},
    }

    # Z-stack settings
    if z_stack_settings:
        yaml_dict["z_stack"] = {
            "nz": z_stack_settings.get("nz", 1),
            "delta_z_um": z_stack_settings.get("delta_z_um", 1.0),
            "config": z_stack_settings.get("config", "FROM BOTTOM"),
            "use_piezo": z_stack_settings.get("use_piezo", False),
        }

    # Time series settings
    if time_series_settings:
        yaml_dict["time_series"] = {
            "nt": time_series_settings.get("nt", 1),
            "delta_t_s": time_series_settings.get("delta_t_s", 0.0),
        }

    # Channels (order is preserved - list order is imaging order)
    if channel_names:
        yaml_dict["channels"] = [{"name": name} for name in channel_names]

    # Autofocus settings
    if autofocus_settings:
        yaml_dict["autofocus"] = {
            "mode": autofocus_settings.get("mode", AutofocusMode.NONE.value),
            "interval_fovs": autofocus_settings.get("interval_fovs", 1),
        }

    # Focus lock settings
    if focus_lock_settings:
        yaml_dict["focus_lock"] = {
            "enabled": focus_lock_settings.get("enabled", False),
            "buffer_length": focus_lock_settings.get("buffer_length", 5),
            "recovery_attempts": focus_lock_settings.get("recovery_attempts", 3),
            "min_spot_snr": focus_lock_settings.get("min_spot_snr", 10.0),
            "acquire_threshold_um": focus_lock_settings.get("acquire_threshold_um", 0.25),
            "maintain_threshold_um": focus_lock_settings.get("maintain_threshold_um", 0.5),
        }

    # Widget-specific scan settings
    if widget_type == "flexible" and flexible_scan_settings:
        yaml_dict["flexible_scan"] = {
            "nx": flexible_scan_settings.get("nx", 1),
            "ny": flexible_scan_settings.get("ny", 1),
            "delta_x_mm": flexible_scan_settings.get("delta_x_mm", 0.9),
            "delta_y_mm": flexible_scan_settings.get("delta_y_mm", 0.9),
            "overlap_percent": flexible_scan_settings.get("overlap_percent", 10.0),
            "positions": flexible_scan_settings.get("positions", []),
        }
    elif widget_type == "wellplate" and wellplate_scan_settings:
        yaml_dict["wellplate_scan"] = {
            "scan_size_mm": wellplate_scan_settings.get("scan_size_mm", 0.0),
            "overlap_percent": wellplate_scan_settings.get("overlap_percent", 10.0),
            "regions": wellplate_scan_settings.get("regions", []),
        }

    # Create directory if it doesn't exist
    os.makedirs(experiment_path, exist_ok=True)

    yaml_path = os.path.join(experiment_path, "acquisition.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"# Acquisition Preset - {experiment_id}\n")
        f.write("# Saved before acquisition for reuse\n\n")
        yaml.dump(yaml_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    _log.info(f"Saved acquisition preset to: {yaml_path}")
    return yaml_path


def load_acquisition_preset(file_path: str) -> AcquisitionYAMLData:
    """Load acquisition preset from YAML file.

    This is a convenience wrapper around parse_acquisition_yaml() that
    provides a clearer name for the preset loading use case.

    Args:
        file_path: Path to the acquisition.yaml file

    Returns:
        AcquisitionYAMLData with parsed values

    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If file is not valid YAML
        ValueError: If file is empty or has invalid widget_type
    """
    return parse_acquisition_yaml(file_path)
