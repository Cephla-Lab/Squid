"""Qt-free application of saved acquisition settings to the MultiPointController and ScanCoordinates.

One function for every programmatic caller — the TCP/MCP `run_acquisition_from_yaml` command and the
fluidics protocol runner's imaging steps — so they all set the same fields the Wellplate tab's Start
button sets (z range, focus map, skip saving, widget type, scan size/overlap, xy mode, piezo, AF) and
rebuild Load-Coordinates regions from explicit FOV lists. Widgets read their own controls instead.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import control._def
from control.acquisition_yaml_loader import AcquisitionYAMLData
from control.utils import serialize_for_yaml


@dataclass(frozen=True)
class AppliedSettings:
    regions: int
    fovs: int
    channels: List[str]
    nz: int


def parse_wells(wells: str, wellplate_settings: dict) -> Dict[str, Tuple[float, float]]:
    """'A1:B3' (range) or 'A1,A2,B1' (list) -> {well_id: (x_mm, y_mm)} from a1_x_mm/a1_y_mm/well_spacing_mm.

    Descriptors that do not look like a well are skipped (the TCP command's historical behaviour).
    """

    def row_to_index(row: str) -> int:
        index = 0
        for char in row.upper():
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index - 1

    def index_to_row(index: int) -> str:
        index += 1
        row = ""
        while index > 0:
            index -= 1
            row = chr(index % 26 + ord("A")) + row
            index //= 26
        return row

    a1_x = wellplate_settings.get("a1_x_mm", 0)
    a1_y = wellplate_settings.get("a1_y_mm", 0)
    spacing = wellplate_settings.get("well_spacing_mm", 9)

    well_coords: Dict[str, Tuple[float, float]] = {}
    pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"

    for desc in wells.split(","):
        match = re.match(pattern, desc.strip())
        if not match:
            continue

        start_row, start_col, end_row, end_col = match.groups()
        start_row_idx = row_to_index(start_row)
        start_col_idx = int(start_col) - 1

        if end_row and end_col:
            # Range like A1:B3
            end_row_idx = row_to_index(end_row)
            end_col_idx = int(end_col) - 1
            for row_idx in range(start_row_idx, end_row_idx + 1):
                for col_idx in range(start_col_idx, end_col_idx + 1):
                    well_id = index_to_row(row_idx) + str(col_idx + 1)
                    well_coords[well_id] = (a1_x + col_idx * spacing, a1_y + row_idx * spacing)
        else:
            # Single well like A1
            well_id = start_row.upper() + start_col
            well_coords[well_id] = (a1_x + start_col_idx * spacing, a1_y + start_row_idx * spacing)

    return well_coords


def _validate_channels(microscope, channel_names: List[str]) -> None:
    current_objective = microscope.objective_store.current_objective
    available = microscope.config_repo.get_merged_channels(current_objective)
    available_names = [ch.name for ch in available] if available else []
    invalid = [ch for ch in channel_names if ch not in available_names]
    if invalid:
        raise ValueError(f"Invalid channels: {invalid}. Available: {available_names}")


def _z_from_center(center, default_z: float) -> float:
    return float(center[2]) if center is not None and len(center) > 2 else default_z


def _configure_regions(scan_coordinates, microscope, data: AcquisitionYAMLData, wells: Optional[str]) -> None:
    scan_coordinates.clear_regions()
    current_z = microscope.stage.get_pos().z_mm
    scan_size_mm = data.scan_size_mm or 2.0
    scan_shape = data.scan_shape or "Square"
    regions = data.wellplate_regions if data.widget_type == "wellplate" else data.flexible_positions

    if wells:
        wellplate_settings = control._def.get_wellplate_settings(data.wellplate_format or "96 well plate")
        well_coords = parse_wells(wells, wellplate_settings)
        if not well_coords:
            raise ValueError(f"Could not parse wells: {wells}")
        for well_id, (well_x, well_y) in well_coords.items():
            scan_coordinates.add_region(
                well_id=well_id,
                center_x=well_x,
                center_y=well_y,
                scan_size_mm=scan_size_mm,
                overlap_percent=data.overlap_percent,
                shape=scan_shape,
            )
            if well_id in scan_coordinates.region_centers:
                scan_coordinates.region_centers[well_id][2] = current_z
    elif regions:
        for region in regions:
            name = region.get("name", "region")
            center = region.get("center_mm")
            fovs = region.get("fovs")
            if fovs:
                scan_coordinates.add_region_from_fovs(name, fovs, shape=region.get("shape") or "Manual")
            elif data.widget_type == "wellplate":
                if center is None:
                    raise ValueError(f"Region {name} has neither fovs nor center_mm")
                scan_coordinates.add_region(
                    well_id=name,
                    center_x=center[0],
                    center_y=center[1],
                    scan_size_mm=scan_size_mm,
                    overlap_percent=data.overlap_percent,
                    shape=region.get("shape", scan_shape),
                )
                if name in scan_coordinates.region_centers:
                    scan_coordinates.region_centers[name][2] = _z_from_center(center, current_z)
            else:
                if center is None:
                    raise ValueError(f"Position {name} has neither fovs nor center_mm")
                scan_coordinates.add_flexible_region(
                    region_id=name,
                    center_x=center[0],
                    center_y=center[1],
                    center_z=_z_from_center(center, current_z),
                    Nx=data.nx,
                    Ny=data.ny,
                    overlap_percent=data.overlap_percent,
                )
    else:
        raise ValueError("No wells or regions specified in YAML and no wells override provided")

    scan_coordinates.sort_coordinates()


def apply_acquisition_settings(
    controller, scan_coordinates, microscope, data: AcquisitionYAMLData, wells: Optional[str] = None
) -> AppliedSettings:
    """Configure `controller` and `scan_coordinates` from `data` exactly as the Wellplate tab's Start would.

    Order: channels are validated first (nothing is touched on failure), then regions are rebuilt
    (explicit FOV lists win over centers; `wells` overrides both), then every controller field is set
    through its setter — including the sticky ones a previous GUI run may have left behind. Does not
    call start_new_experiment or run_acquisition. Raises ValueError with a user-readable message.
    """
    _validate_channels(microscope, data.channel_names)
    _configure_regions(scan_coordinates, microscope, data, wells)

    controller.set_NX(1)  # grids are expanded into FOVs by the region builders
    controller.set_NY(1)
    controller.set_NZ(data.nz)
    controller.set_deltaZ(data.delta_z_um)
    controller.set_Nt(data.nt)
    controller.set_deltat(data.delta_t_s)
    controller.set_af_flag(data.contrast_af)
    controller.set_reflection_af_flag(data.laser_af)
    controller.set_use_piezo(data.use_piezo)
    if data.z_stacking_config in control._def.Z_STACKING_CONFIG_MAP.values():
        controller.z_stacking_config = data.z_stacking_config
    if data.z_range_mm:
        controller.set_z_range(float(data.z_range_mm[0]), float(data.z_range_mm[1]))
    else:
        controller.z_range = None  # run_acquisition derives it from the current z and the stack
    controller.set_focus_map(None)
    controller.set_region_laser_af_offsets({})
    controller.set_skip_saving(data.skip_saving)
    controller.set_widget_type(data.widget_type)
    controller.set_scan_size(float(data.scan_size_mm or 0.0))
    controller.set_overlap_percent(float(data.overlap_percent))
    controller.set_xy_mode(data.xy_mode)
    controller.set_selected_configurations(data.channel_names)
    selected = [c.name for c in controller.selected_configurations]
    if len(selected) != len(data.channel_names):
        missing = sorted(set(data.channel_names) - set(selected))
        raise ValueError(f"Channels not available for the current objective: {missing}")

    fov_lists = scan_coordinates.region_fov_coordinates
    return AppliedSettings(
        regions=len(fov_lists),
        fovs=sum(len(coords) for coords in fov_lists.values()),
        channels=list(data.channel_names),
        nz=data.nz,
    )


def export_acquisition_settings(controller, scan_coordinates, objective_store, camera) -> Tuple[dict, dict]:
    """The controller's current settings and ScanCoordinates as two plain dicts (a protocol's
    `imaging.settings` / `imaging.coordinates` blocks). Read after the widget pushed its controls."""
    current_objective = objective_store.current_objective
    objective_dict = objective_store.objectives_dict.get(current_objective, {})
    pixel_size_um = objective_store.get_pixel_size_factor() * camera.get_pixel_size_binned_um()
    settings = {
        "objective": {
            "name": current_objective,
            "magnification": objective_dict.get("magnification"),
            "NA": objective_dict.get("NA"),
            "pixel_size_um": pixel_size_um,
            "camera_binning": list(camera.get_binning()) if hasattr(camera, "get_binning") else None,
            "sensor_pixel_size_um": camera.get_pixel_size_binned_um(),
        },
        "channels": [ch.name for ch in controller.selected_configurations],
        "z_stack": {
            "nz": int(controller.NZ),
            "delta_z_um": float(controller.deltaZ) * 1000.0,
            "config": serialize_for_yaml(controller.z_stacking_config),
            "use_piezo": bool(controller.use_piezo),
            "z_range_mm": [float(v) for v in controller.z_range] if controller.z_range else None,
        },
        "autofocus": {"contrast_af": bool(controller.do_autofocus), "laser_af": bool(controller.do_reflection_af)},
        "widget_type": controller.widget_type,
        "xy_mode": controller.xy_mode,
        "scan_size_mm": float(controller.scan_size_mm),
        "overlap_percent": float(controller.overlap_percent),
        "skip_saving": bool(controller.skip_saving),
    }
    coordinates = {
        "wellplate_format": getattr(scan_coordinates, "format", None),
        "regions": [
            {
                "name": name,
                "center_mm": [float(c) for c in scan_coordinates.region_centers.get(name, [])],
                "shape": scan_coordinates.region_shapes.get(name, "Manual"),
                "fovs": [[float(v) for v in fov] for fov in fovs],
            }
            for name, fovs in scan_coordinates.region_fov_coordinates.items()
        ],
    }
    return serialize_for_yaml(settings), serialize_for_yaml(coordinates)


def acquisition_data_from_blocks(settings: dict, coordinates: dict) -> AcquisitionYAMLData:
    """Turn a settings block + a coordinates block into the loader's dataclass. Imaging steps are single
    timepoints, so nt is forced to 1."""
    objective = settings.get("objective") or {}
    z_stack = settings.get("z_stack") or {}
    autofocus = settings.get("autofocus") or {}
    binning = objective.get("camera_binning")
    z_range = z_stack.get("z_range_mm")
    widget_type = settings.get("widget_type", "wellplate")
    regions = [dict(r) for r in (coordinates.get("regions") or [])]
    return AcquisitionYAMLData(
        widget_type=widget_type,
        xy_mode=settings.get("xy_mode", "Load Coordinates"),
        objective_name=objective.get("name"),
        objective_magnification=objective.get("magnification"),
        objective_pixel_size_um=objective.get("pixel_size_um"),
        camera_binning=tuple(binning) if binning and len(binning) == 2 else None,
        nz=int(z_stack.get("nz", 1)),
        delta_z_um=float(z_stack.get("delta_z_um", 1.0)),
        z_stacking_config=str(z_stack.get("config", "FROM CENTER")),
        use_piezo=bool(z_stack.get("use_piezo", False)),
        nt=1,
        delta_t_s=0.0,
        channel_names=list(settings.get("channels") or []),
        contrast_af=bool(autofocus.get("contrast_af", False)),
        laser_af=bool(autofocus.get("laser_af", False)),
        scan_size_mm=settings.get("scan_size_mm"),
        overlap_percent=float(settings.get("overlap_percent", 10.0)),
        scan_shape=None,
        wellplate_regions=regions if widget_type == "wellplate" else None,
        flexible_positions=regions if widget_type != "wellplate" else None,
        wellplate_format=coordinates.get("wellplate_format"),
        z_range_mm=(float(z_range[0]), float(z_range[1])) if z_range and len(z_range) == 2 else None,
        skip_saving=bool(settings.get("skip_saving", False)),
    )
