from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional, Callable, TYPE_CHECKING
import time

from squid.backend.controllers.multipoint.job_processing import CaptureInfo
from squid.backend.managers import ScanCoordinates
from squid.core.utils.config_utils import ChannelMode
from squid.core.abc import CameraFrame
from _def import ZProjectionMode



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

    # Downsampled view generation parameters
    generate_downsampled_views: bool = False
    downsampled_well_resolutions_um: List[float] = field(default_factory=list)
    downsampled_plate_resolution_um: float = 10.0
    downsampled_z_projection: ZProjectionMode = ZProjectionMode.MIP
    plate_num_rows: int = 8  # Default for 96-well
    plate_num_cols: int = 12
    xy_mode: str = "Current Position"


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

