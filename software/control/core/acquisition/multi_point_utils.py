from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable, TYPE_CHECKING
import time

from control.core.acquisition.job_processing import CaptureInfo
from control.core.navigation import ScanCoordinates
from control.utils_config import ChannelMode
from squid.abc import CameraFrame

if TYPE_CHECKING:
    from squid.events import EventBus


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


def create_eventbus_callbacks(event_bus: "EventBus") -> MultiPointControllerFunctions:
    """Create MultiPointControllerFunctions that publish events to EventBus.

    This allows MultiPointController to work without Qt signals by publishing
    events that widgets can subscribe to via UIEventBus.

    Note: Image data (signal_new_image) does NOT go through EventBus as that
    would overwhelm the event system. Images should go through StreamHandler.
    """
    from squid.events import (
        AcquisitionStarted,
        AcquisitionFinished,
        AcquisitionProgress,
        AcquisitionRegionProgress,
        MicroscopeModeChanged,
        CurrentFOVRegistered,
    )

    def on_acquisition_start(params: AcquisitionParameters) -> None:
        event_bus.publish(
            AcquisitionStarted(
                experiment_id=params.experiment_ID or "",
                timestamp=time.time(),
            )
        )

    def on_acquisition_finished() -> None:
        event_bus.publish(AcquisitionFinished(success=True))

    def on_new_image(frame: CameraFrame, info: CaptureInfo) -> None:
        # Images go through StreamHandler, not EventBus
        # This is intentionally a no-op for EventBus callbacks
        pass

    def on_current_configuration(channel_mode: ChannelMode) -> None:
        event_bus.publish(
            MicroscopeModeChanged(
                configuration_name=channel_mode.name,
                exposure_time_ms=channel_mode.exposure_time,
                analog_gain=channel_mode.analog_gain,
                illumination_intensity=channel_mode.illumination_intensity,
            )
        )

    def on_current_fov(x_mm: float, y_mm: float) -> None:
        event_bus.publish(CurrentFOVRegistered(x_mm=x_mm, y_mm=y_mm))

    def on_overall_progress(progress: OverallProgressUpdate) -> None:
        # Map overall progress into AcquisitionProgress fields. Values are approximate
        # but keep the event compatible with the dataclass.
        total_regions = progress.total_regions or 1
        total_timepoints = progress.total_timepoints or 1

        current_region = max(progress.current_region, 1)
        current_timepoint = max(progress.current_timepoint, 1)

        # Simple percentage across timepoints and regions
        region_fraction = (current_region - 1) / total_regions
        timepoint_fraction = (current_timepoint - 1) / total_timepoints
        progress_percent = min(
            100.0,
            max(0.0, (region_fraction + timepoint_fraction / total_regions) * 100.0),
        )

        event_bus.publish(
            AcquisitionProgress(
                current_fov=current_region,
                total_fovs=total_regions,
                current_round=current_timepoint,
                total_rounds=total_timepoints,
                current_channel="",
                progress_percent=progress_percent,
            )
        )

    def on_region_progress(progress: RegionProgressUpdate) -> None:
        event_bus.publish(
            AcquisitionRegionProgress(
                current_region=progress.current_fov,
                total_regions=progress.region_fovs,
            )
        )

    return MultiPointControllerFunctions(
        signal_acquisition_start=on_acquisition_start,
        signal_acquisition_finished=on_acquisition_finished,
        signal_new_image=on_new_image,
        signal_current_configuration=on_current_configuration,
        signal_current_fov=on_current_fov,
        signal_overall_progress=on_overall_progress,
        signal_region_progress=on_region_progress,
    )
