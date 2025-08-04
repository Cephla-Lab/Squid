from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable

from control.core.job_processing import CaptureInfo
from control.core.scan_coordinates import ScanCoordinates
from control.utils_config import ChannelMode
from squid.abc import CameraFrame


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


@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_start: Callable[[AcquisitionParameters], None]
    signal_acquisition_finished: Callable[[], None]
    signal_new_image: Callable[[CameraFrame, CaptureInfo], None]
    signal_current_configuration: Callable[[ChannelMode], None]
    signal_current_fov: Callable[[float, float], None]
    signal_overall_progress: Callable[[OverallProgressUpdate], None]
    signal_region_progress: Callable[[RegionProgressUpdate], None]
