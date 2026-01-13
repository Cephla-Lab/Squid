"""
Progress and coordinate tracking for multipoint acquisitions.

This module provides:
- ProgressTracker: Event publishing for acquisition progress updates
- CoordinateTracker: DataFrame management for recording scan coordinates

These classes encapsulate the progress reporting and coordinate logging logic
previously embedded in MultiPointWorker.
"""

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Dict, Optional, Tuple, TYPE_CHECKING

import pandas as pd

import squid.core.abc
import squid.core.logging

if TYPE_CHECKING:
    from squid.core.events import EventBus

_log = squid.core.logging.get_logger(__name__)


@dataclass
class ProgressState:
    """Current progress state during acquisition."""

    current_fov: int
    total_fovs: int
    current_region: int
    total_regions: int
    current_timepoint: int
    total_timepoints: int
    current_channel: str = ""
    progress_percent: float = 0.0
    eta_seconds: Optional[float] = None


class ProgressTracker:
    """
    Tracks and publishes acquisition progress events.

    Handles progress calculation and event publishing for:
    - AcquisitionStarted/Finished
    - AcquisitionProgress (for UI updates)
    - AcquisitionWorkerProgress (for controller state tracking)
    - CurrentFOVRegistered (for navigation view updates)

    Usage:
        tracker = ProgressTracker(event_bus, experiment_id)

        # At acquisition start
        tracker.start()

        # During acquisition
        tracker.update(
            current_fov=5,
            total_fovs=100,
            current_region=2,
            total_regions=4,
            current_timepoint=1,
            total_timepoints=10,
            current_channel="DAPI",
        )

        # At acquisition end
        tracker.finish(success=True)
    """

    def __init__(
        self,
        event_bus: Optional["EventBus"],
        experiment_id: str,
        base_path: str = "",
    ):
        """
        Initialize the progress tracker.

        Args:
            event_bus: EventBus for publishing events (can be None for silent operation)
            experiment_id: Unique identifier for this acquisition
            base_path: Base directory path for acquisition data
        """
        self._event_bus = event_bus
        self._experiment_id = experiment_id
        self._base_path = base_path
        self._start_time: Optional[float] = None
        self._af_fov_count: int = 0

    @property
    def experiment_id(self) -> str:
        """Get the experiment ID."""
        return self._experiment_id

    @property
    def start_time(self) -> Optional[float]:
        """Get the acquisition start time."""
        return self._start_time

    @property
    def af_fov_count(self) -> int:
        """Get/set the autofocus FOV count."""
        return self._af_fov_count

    @af_fov_count.setter
    def af_fov_count(self, value: int) -> None:
        self._af_fov_count = value

    def start(self) -> None:
        """Publish acquisition started event and record start time."""
        import time

        self._start_time = time.time()

        if self._event_bus is None:
            return

        from squid.core.events import AcquisitionStarted

        self._event_bus.publish(
            AcquisitionStarted(
                experiment_id=self._experiment_id,
                timestamp=self._start_time,
                base_path=self._base_path,
            )
        )

    def finish(self, success: bool, error: Optional[Exception] = None) -> None:
        """
        Publish acquisition finished event.

        Args:
            success: Whether acquisition completed successfully
            error: Optional exception if failed
        """
        if self._event_bus is None:
            return

        from squid.core.events import AcquisitionWorkerFinished

        self._event_bus.publish(
            AcquisitionWorkerFinished(
                experiment_id=self._experiment_id,
                success=success,
                error=str(error) if error else None,
                final_fov_count=self._af_fov_count,
            )
        )

    def update(
        self,
        current_fov: int,
        total_fovs: int,
        current_region: int,
        total_regions: int,
        current_timepoint: int,
        total_timepoints: int,
        current_channel: str = "",
    ) -> ProgressState:
        """
        Update progress and publish progress events.

        Args:
            current_fov: Current FOV index (1-based)
            total_fovs: Total FOVs in current region
            current_region: Current region index (1-based)
            total_regions: Total number of regions
            current_timepoint: Current timepoint (1-based)
            total_timepoints: Total number of timepoints
            current_channel: Current channel name

        Returns:
            ProgressState with calculated progress percentage and ETA
        """
        # Calculate overall progress percentage
        progress_percent = self._calculate_progress(
            current_fov, total_fovs, current_region, total_regions
        )

        # Calculate ETA
        eta_seconds = self._calculate_eta(progress_percent)

        state = ProgressState(
            current_fov=current_fov,
            total_fovs=total_fovs,
            current_region=current_region,
            total_regions=total_regions,
            current_timepoint=current_timepoint,
            total_timepoints=total_timepoints,
            current_channel=current_channel,
            progress_percent=progress_percent,
            eta_seconds=eta_seconds,
        )

        # Publish events
        self._publish_acquisition_progress(state)
        self._publish_worker_progress(state)

        return state

    def register_fov(
        self,
        x_mm: float,
        y_mm: float,
        fov_width_mm: float = 0.0,
        fov_height_mm: float = 0.0,
    ) -> None:
        """
        Publish CurrentFOVRegistered event for navigation view updates.

        Args:
            x_mm: FOV X position in mm
            y_mm: FOV Y position in mm
            fov_width_mm: FOV width in mm
            fov_height_mm: FOV height in mm
        """
        if self._event_bus is None:
            return

        from squid.core.events import CurrentFOVRegistered

        self._event_bus.publish(
            CurrentFOVRegistered(
                x_mm=x_mm,
                y_mm=y_mm,
                fov_width_mm=fov_width_mm,
                fov_height_mm=fov_height_mm,
            )
        )

    def _calculate_progress(
        self,
        current_fov: int,
        total_fovs: int,
        current_region: int,
        total_regions: int,
    ) -> float:
        """Calculate overall progress percentage."""
        if total_fovs <= 0 or total_regions <= 0:
            return 0.0

        # Progress across all regions and FOVs
        region_progress = (current_region - 1) / total_regions
        fov_progress_in_region = current_fov / total_fovs
        return (region_progress + fov_progress_in_region / total_regions) * 100.0

    def _calculate_eta(self, progress_percent: float) -> Optional[float]:
        """Calculate estimated time remaining in seconds."""
        import time

        if self._start_time is None or progress_percent <= 0:
            return None

        elapsed = time.time() - self._start_time
        total_estimated = elapsed * 100.0 / progress_percent
        return total_estimated - elapsed

    def _publish_acquisition_progress(self, state: ProgressState) -> None:
        """Publish AcquisitionProgress event for UI updates."""
        if self._event_bus is None:
            return

        from squid.core.events import AcquisitionProgress

        self._event_bus.publish(
            AcquisitionProgress(
                current_fov=state.current_fov,
                total_fovs=state.total_fovs,
                current_round=state.current_region,
                total_rounds=state.total_regions,
                current_channel=state.current_channel,
                progress_percent=state.progress_percent,
                experiment_id=self._experiment_id,
                eta_seconds=state.eta_seconds,
            )
        )

    def _publish_worker_progress(self, state: ProgressState) -> None:
        """Publish AcquisitionWorkerProgress event for controller tracking."""
        if self._event_bus is None:
            return

        from squid.core.events import AcquisitionWorkerProgress

        self._event_bus.publish(
            AcquisitionWorkerProgress(
                experiment_id=self._experiment_id,
                current_region=state.current_region,
                total_regions=state.total_regions,
                current_fov=state.current_fov,
                total_fovs=state.total_fovs,
                current_timepoint=state.current_timepoint,
                total_timepoints=state.total_timepoints,
            )
        )


class CoordinateTracker:
    """
    Tracks and records scan coordinates during acquisition.

    Manages a DataFrame of scan coordinates and supports:
    - Recording position at each FOV/z-level
    - Tracking last z-position per FOV for multi-timepoint acquisitions
    - Saving coordinates to CSV

    Usage:
        tracker = CoordinateTracker(use_piezo=True)

        # Record each position
        tracker.record(
            region_id="region_0",
            fov=1,
            z_level=0,
            pos=stage_position,
            z_piezo_um=50.0,
        )

        # Get last z position for returning to focus
        z_mm = tracker.get_last_z_position("region_0", 1)

        # Save to file
        tracker.save("/path/to/coordinates.csv")
    """

    def __init__(self, use_piezo: bool = False):
        """
        Initialize the coordinate tracker.

        Args:
            use_piezo: Whether piezo z positions should be tracked
        """
        self._use_piezo = use_piezo
        self._coordinates_df: Optional[pd.DataFrame] = None
        self._last_z_positions: Dict[Tuple[str, int], float] = {}

    @property
    def dataframe(self) -> Optional[pd.DataFrame]:
        """Get the coordinates DataFrame."""
        return self._coordinates_df

    def initialize(self) -> None:
        """Initialize the coordinates DataFrame for a new timepoint."""
        base_columns = ["z_level", "x (mm)", "y (mm)", "z (um)", "time"]
        piezo_column = ["z_piezo (um)"] if self._use_piezo else []
        columns = ["region", "fov"] + base_columns + piezo_column
        self._coordinates_df = pd.DataFrame(columns=columns)

    def record(
        self,
        region_id: str,
        z_level: int,
        pos: squid.core.abc.Pos,
        fov: Optional[int] = None,
        z_piezo_um: Optional[float] = None,
    ) -> None:
        """
        Record a coordinate position.

        Args:
            region_id: Region identifier
            z_level: Current z-level index
            pos: Stage position (x_mm, y_mm, z_mm)
            fov: FOV index (optional)
            z_piezo_um: Piezo z position in um (optional)
        """
        if self._coordinates_df is None:
            self.initialize()

        base_data = {
            "z_level": [z_level],
            "x (mm)": [pos.x_mm],
            "y (mm)": [pos.y_mm],
            "z (um)": [pos.z_mm * 1000],
            "time": [datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")],
        }

        piezo_data = {}
        if self._use_piezo and z_piezo_um is not None:
            piezo_data = {"z_piezo (um)": [z_piezo_um]}

        new_row = pd.DataFrame(
            {"region": [region_id], "fov": [fov], **base_data, **piezo_data}
        )

        if self._coordinates_df is None:
            self._coordinates_df = new_row
        else:
            self._coordinates_df = pd.concat(
                [self._coordinates_df, new_row], ignore_index=True
            )

    def record_last_z_position(
        self,
        region_id: str,
        fov: int,
        z_mm: float,
    ) -> None:
        """
        Record the last z position for a FOV.

        Used to return to focus position on subsequent timepoints.

        Args:
            region_id: Region identifier
            fov: FOV index
            z_mm: Z position in mm
        """
        self._last_z_positions[(region_id, fov)] = z_mm

    def get_last_z_position(
        self,
        region_id: str,
        fov: int,
    ) -> Optional[float]:
        """
        Get the last recorded z position for a FOV.

        Args:
            region_id: Region identifier
            fov: FOV index

        Returns:
            Z position in mm, or None if not recorded
        """
        return self._last_z_positions.get((region_id, fov))

    def has_last_z_position(self, region_id: str, fov: int) -> bool:
        """
        Check if a last z position is recorded for a FOV.

        Args:
            region_id: Region identifier
            fov: FOV index

        Returns:
            True if position is recorded
        """
        return (region_id, fov) in self._last_z_positions

    def clear_last_z_positions(self) -> None:
        """Clear all recorded last z positions."""
        self._last_z_positions.clear()

    def save(self, path: str) -> None:
        """
        Save coordinates to CSV file.

        Args:
            path: Path to save CSV file
        """
        if self._coordinates_df is None:
            _log.warning("No coordinates to save")
            return

        self._coordinates_df.to_csv(path, index=False, header=True)
        _log.debug(f"Saved coordinates to {path}")

    def save_to_directory(self, directory: str, filename: str = "coordinates.csv") -> None:
        """
        Save coordinates to a CSV file in the specified directory.

        Args:
            directory: Directory path
            filename: CSV filename (default: coordinates.csv)
        """
        path = os.path.join(directory, filename)
        self.save(path)
