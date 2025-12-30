from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable, TYPE_CHECKING
import time

from squid.backend.controllers.multipoint.job_processing import CaptureInfo
from squid.backend.managers import ScanCoordinates
from squid.core.utils.config_utils import ChannelMode
from squid.core.abc import CameraFrame



@dataclass
class ScanPositionInformation:
    scan_region_coords_mm: List[Tuple[float, float]]
    scan_region_names: List[str]
    scan_region_fov_coords_mm: Dict[str, List[Tuple[float, float, float]]]

    @staticmethod
    def from_scan_coordinates(scan_coordinates: ScanCoordinates):
        return ScanPositionInformation(
            scan_region_coords_mm=list(scan_coordinates.region_centers.values()),
            scan_region_names=list(scan_coordinates.region_centers.keys()),
            scan_region_fov_coords_mm=dict(scan_coordinates.region_fov_coordinates),
        )


@dataclass
class AcquisitionParameters:
    experiment_ID: Optional[str]
    base_path: Optional[str]
    selected_configurations: List[ChannelMode]
    acquisition_start_time: float
    scan_position_information: ScanPositionInformation

    # NOTE(imo): I'm pretty sure NX and NY are broken?  They are not used in MPW anywhere.
    NX: int
    deltaX: float
    NY: int
    deltaY: float

    NZ: int
    deltaZ: float
    Nt: int
    deltat: float

    do_autofocus: bool
    do_reflection_autofocus: bool

    use_piezo: bool
    display_resolution_scaling: float

    z_stacking_config: str
    z_range: Tuple[float, float]

    use_fluidics: bool
    skip_saving: bool = False


@dataclass
class OverallProgressUpdate:
    current_region: int
    total_regions: int

    current_timepoint: int
    total_timepoints: int


@dataclass
class RegionProgressUpdate:
    current_fov: int
    region_fovs: int

