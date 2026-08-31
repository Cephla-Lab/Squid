"""Turn a ProtocolFile into what the runner executes: steps, and for every included imaging row its
SettingsBlock and CoordinatesBlock - from the header or from a file (a saved acquisition folder or
acquisition.yaml for either; a coordinates.csv for coordinates). File sources are inlined into a copy
of the protocol so the run folder's protocol.yaml is self-contained. Every problem is collected and
reported at once (ProtocolProblems) - the pre-flight dialog shows the list."""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from control.acquisition_yaml_loader import AcquisitionYAMLData, parse_acquisition_yaml, read_coordinates_csv
from control.core.fluidics_protocol.ports import FluidicsPort, plan_seconds
from control.models.fluidics_protocol import (
    CoordinatesBlock,
    ImagingRow,
    ProtocolFile,
    SettingsBlock,
    Step,
    folder_problems,
    split_into_steps,
    strip_for_library,
)

FILE_KEY_PREFIX = "file:"


class ProtocolProblems(ValueError):
    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class ResolvedImaging:
    row: ImagingRow
    settings: SettingsBlock
    coordinates: CoordinatesBlock


@dataclass
class ResolvedProtocol:
    protocol: ProtocolFile  # copy with file sources inlined
    steps: List[Step]
    imaging: Dict[int, ResolvedImaging]  # by row index
    fluidics_estimate_s: Optional[float]


def settings_block_from_acquisition(data: AcquisitionYAMLData, source_path: str) -> SettingsBlock:
    return SettingsBlock(
        source="acquisition file",
        source_path=source_path,
        objective={
            "name": data.objective_name,
            "magnification": data.objective_magnification,
            "pixel_size_um": data.objective_pixel_size_um,
            "camera_binning": list(data.camera_binning) if data.camera_binning else None,
        },
        channels=list(data.channel_names),
        z_stack={
            "nz": data.nz,
            "delta_z_um": data.delta_z_um,
            "config": data.z_stacking_config,
            "use_piezo": data.use_piezo,
            "z_range_mm": list(data.z_range_mm) if data.z_range_mm else None,
        },
        autofocus={"contrast_af": data.contrast_af, "laser_af": data.laser_af},
        widget_type=data.widget_type,
        xy_mode=data.xy_mode,
        scan_size_mm=float(data.scan_size_mm or 0.0),
        overlap_percent=float(data.overlap_percent),
        skip_saving=data.skip_saving,
    )


def coordinates_block_from_acquisition(data: AcquisitionYAMLData, source_path: str) -> CoordinatesBlock:
    regions = data.wellplate_regions if data.widget_type == "wellplate" else data.flexible_positions
    return CoordinatesBlock(
        source="acquisition file",
        source_path=source_path,
        wellplate_format=data.wellplate_format,
        regions=[
            {
                "name": r.get("name", "region"),
                "fovs": r.get("fovs") or [],
                "center_mm": r.get("center_mm"),
                "shape": r.get("shape"),
            }
            for r in (regions or [])
        ],
    )


def _load_settings_source(ref: str, base_dir: str) -> SettingsBlock:
    path = os.path.normpath(os.path.join(base_dir, ref))
    return settings_block_from_acquisition(parse_acquisition_yaml(path), path)


def _load_coordinates_source(ref: str, base_dir: str) -> CoordinatesBlock:
    path = os.path.normpath(os.path.join(base_dir, ref))
    if path.lower().endswith(".csv"):
        return CoordinatesBlock(source="coordinates CSV", source_path=path, regions=read_coordinates_csv(path))
    return coordinates_block_from_acquisition(parse_acquisition_yaml(path), path)


def resolve_protocol(protocol: ProtocolFile, base_dir, fluidics: Optional[FluidicsPort] = None) -> ResolvedProtocol:
    """Bind every included imaging row; validate the fluidics rows through `fluidics` when given.

    Raises ProtocolProblems listing every problem found (folders, missing blocks, unreadable files,
    zero FOVs, library validation)."""
    base_dir = str(base_dir)
    problems: List[str] = list(folder_problems(protocol))
    work = protocol.model_copy(deep=True)
    imaging: Dict[int, ResolvedImaging] = {}

    for row_index, row in work.imaging_rows():
        if not row.include:
            continue
        label = f"{row.round or 'no round'}/{row.name or row.folder or f'row {row_index + 1}'}"
        settings = coordinates = None
        for field_name, header, loader in (
            ("settings", work.imaging.settings, _load_settings_source),
            ("coordinates", work.imaging.coordinates, _load_coordinates_source),
        ):
            ref = getattr(row, field_name)
            if not ref:
                problems.append(f"{label}: no {field_name}")
                continue
            block = header.get(ref)
            if block is None:
                candidate = os.path.join(base_dir, ref)
                if os.path.exists(candidate):
                    try:
                        block = loader(ref, base_dir)
                    except Exception as e:
                        problems.append(f"{label}: cannot read {field_name} from '{ref}': {e}")
                        continue
                    key = FILE_KEY_PREFIX + ref
                    header[key] = block
                    work.sequences[row_index][field_name] = key
                else:
                    problems.append(f"{label}: {field_name} '{ref}' is neither a header block nor a file")
                    continue
            if field_name == "settings":
                settings = block
            else:
                coordinates = block
                if block.fov_count == 0:
                    problems.append(f"{label}: coordinates '{ref}' have no FOVs")
        if settings is not None and coordinates is not None and coordinates.fov_count > 0:
            imaging[row_index] = ResolvedImaging(
                ImagingRow.model_validate(work.sequences[row_index]), settings, coordinates
            )

    steps = split_into_steps(work)
    estimate: Optional[float] = None
    if fluidics is not None:
        estimate = 0.0
        for step in steps:
            if step.kind != "fluidics":
                continue
            rows = strip_for_library(step.rows)
            try:
                fluidics.validate(rows)
            except ValueError as e:
                problems.append(f"{step.label}: {e}")
                continue
            estimate += plan_seconds(fluidics.plan(rows))

    if problems:
        raise ProtocolProblems(problems)
    return ResolvedProtocol(protocol=work, steps=steps, imaging=imaging, fluidics_estimate_s=estimate)
