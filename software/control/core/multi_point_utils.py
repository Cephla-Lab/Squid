from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable, Union, TYPE_CHECKING

from control._def import ZProjectionMode, DownsamplingMethod
from control.core.job_processing import CaptureInfo
from control.core.scan_coordinates import ScanCoordinates
from control.models import AcquisitionChannel
from squid.abc import CameraFrame

if TYPE_CHECKING:
    from control.slack_notifier import TimepointStats, AcquisitionStats


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
    selected_configurations: List[AcquisitionChannel]
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
    save_downsampled_well_images: bool = False  # Save individual well TIFFs (wells/A1_5um.tiff)
    downsampled_well_resolutions_um: Optional[List[float]] = None
    downsampled_plate_resolution_um: float = 10.0
    downsampled_z_projection: Union[ZProjectionMode, str] = ZProjectionMode.MIP
    downsampled_interpolation_method: Union[DownsamplingMethod, str] = DownsamplingMethod.INTER_AREA_FAST
    plate_num_rows: int = 8  # For 96-well plate
    plate_num_cols: int = 12  # For 96-well plate

    # XY mode for determining scan type
    xy_mode: str = "Current Position"  # "Current Position", "Select Wells", "Manual", "Load Coordinates"


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
class PlateViewUpdate:
    """Data for plate view channel update."""

    channel_idx: int
    channel_name: str
    plate_image: "np.ndarray"  # Forward reference


@dataclass
class PlateViewInit:
    """Data for plate view initialization."""

    num_rows: int
    num_cols: int
    well_slot_shape: Tuple[int, int]
    fov_grid_shape: Tuple[int, int]
    channel_names: List[str]


@dataclass
class MultiPointControllerFunctions:
    signal_acquisition_start: Callable[[AcquisitionParameters], None]
    signal_acquisition_finished: Callable[[], None]
    signal_new_image: Callable[[CameraFrame, CaptureInfo], None]
    signal_current_configuration: Callable[[AcquisitionChannel], None]
    signal_current_fov: Callable[[float, float], None]
    signal_overall_progress: Callable[[OverallProgressUpdate], None]
    signal_region_progress: Callable[[RegionProgressUpdate], None]
    # Optional plate view callbacks. Default no-op lambdas avoid None checks at every call site.
    # Unlike mutable defaults (lists/dicts), lambdas are safe as defaults since they're not modified.
    signal_plate_view_init: Callable[[PlateViewInit], None] = lambda *a, **kw: None
    signal_plate_view_update: Callable[[PlateViewUpdate], None] = lambda *a, **kw: None
    # Optional Slack notification callbacks (allows main thread to capture screenshot and maintain ordering)
    signal_slack_timepoint_notification: Callable[["TimepointStats"], None] = lambda *a, **kw: None
    signal_slack_acquisition_finished: Callable[["AcquisitionStats"], None] = lambda *a, **kw: None
    # Zarr frame written callback - called when subprocess completes writing a frame
    # Args: (fov, time_point, z_index, channel_name, region_idx)
    signal_zarr_frame_written: Callable[[int, int, int, str, int], None] = lambda *a, **kw: None
