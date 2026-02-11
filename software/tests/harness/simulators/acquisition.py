"""
Acquisition workflow simulator.

Simulates GUI-driven multipoint acquisition workflows by publishing
the same EventBus commands as the wellplate multipoint widget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from squid.core.events import (
    AutofocusMode,
    # Commands
    AddFlexibleRegionCommand,
    ClearScanCoordinatesCommand,
    SortScanCoordinatesCommand,
    SetLiveScanCoordinatesCommand,
    SetWellSelectionScanCoordinatesCommand,
    SetManualScanCoordinatesCommand,
    LoadScanCoordinatesCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetAcquisitionChannelsCommand,
    StartNewExperimentCommand,
    StartAcquisitionCommand,
    StopAcquisitionCommand,
    SelectedWellsChanged,
    # Events
    AcquisitionStateChanged,
    AcquisitionProgress,
    AcquisitionWorkerProgress,
    AcquisitionWorkerFinished,
    AcquisitionCoordinates,
    CurrentFOVRegistered,
)

from tests.harness.simulators.base import BaseSimulator

if TYPE_CHECKING:
    from tests.harness.core.backend_context import BackendContext


@dataclass
class AcquisitionResult:
    """Result from an acquisition run."""

    success: bool = False
    error: Optional[str] = None
    total_images: int = 0
    total_fovs: int = 0
    elapsed_time_s: float = 0.0
    progress_events: List[AcquisitionProgress] = field(default_factory=list)
    worker_progress_events: List[AcquisitionWorkerProgress] = field(default_factory=list)
    state_changes: List[AcquisitionStateChanged] = field(default_factory=list)
    coordinate_events: List[AcquisitionCoordinates] = field(default_factory=list)
    fov_registrations: List[CurrentFOVRegistered] = field(default_factory=list)


class AcquisitionSimulator(BaseSimulator):
    """
    Simulates GUI-driven multipoint acquisition workflows.

    This simulator publishes the same EventBus commands as the wellplate
    multipoint widget, allowing programmatic testing of the full acquisition
    pipeline.

    Usage:
        with BackendContext() as ctx:
            sim = AcquisitionSimulator(ctx)

            # Set up coordinates
            sim.add_single_fov("test", x=10, y=10, z=1)

            # Configure acquisition
            sim.set_channels(["DAPI", "GFP"])
            sim.set_zstack(n_z=5, delta_z_um=2.0)
            sim.set_timelapse(n_t=3, delta_t_s=5.0)

            # Run and wait for completion
            result = sim.run_and_wait()

            assert result.success
            assert result.total_images == 30  # 1 FOV × 2 channels × 5 z × 3 t
    """

    def __init__(
        self,
        ctx: "BackendContext",
        *,
        bus_only: bool = False,
        auto_set_base_path: bool = True,
    ):
        super().__init__(ctx)

        self._bus_only = bus_only
        self._auto_set_base_path = auto_set_base_path

        # Configuration state
        self._selected_channels: List[str] = []
        self._n_z = 1
        self._delta_z_um = 1.0
        self._z_stacking_config = "FROM BOTTOM"
        self._n_t = 1
        self._delta_t_s = 0.0
        self._autofocus_mode = AutofocusMode.NONE
        self._use_piezo = False
        self._skip_saving = True

        # Force creation of controllers/managers so they subscribe to EventBus
        # before we publish commands. This is needed because they're created lazily.
        _ = ctx.multipoint_controller
        _ = ctx.scan_coordinates
        self.sleep(0.1)  # Allow subscriptions to complete

        # Set up base path via EventBus (like the real GUI does)
        if self._auto_set_base_path:
            self.publish(SetAcquisitionPathCommand(base_path=ctx.base_path))
            self.sleep(0.2)  # Allow EventBus handler to process

        # Subscribe to acquisition events
        self.monitor.subscribe(
            AcquisitionStateChanged,
            AcquisitionProgress,
            AcquisitionWorkerProgress,
            AcquisitionWorkerFinished,
            AcquisitionCoordinates,
            CurrentFOVRegistered,
        )

        # Clear any existing coordinates from previous tests
        self.clear_coordinates()

    # =========================================================================
    # Coordinate Setup
    # =========================================================================

    def clear_coordinates(self) -> "AcquisitionSimulator":
        """Clear all scan coordinates via EventBus."""
        self.publish(ClearScanCoordinatesCommand())
        self.sleep(0.2)  # Allow EventBus handler to process
        return self

    def add_single_fov(
        self,
        region_id: str,
        x: float,
        y: float,
        z: float,
    ) -> "AcquisitionSimulator":
        """
        Add a single-FOV region via EventBus.

        Args:
            region_id: Unique identifier for the region
            x: X position in mm
            y: Y position in mm
            z: Z position in mm

        Returns:
            self for chaining
        """
        # Single FOV is a 1x1 grid with any overlap
        self.publish(
            AddFlexibleRegionCommand(
                region_id=region_id,
                center_x_mm=x,
                center_y_mm=y,
                center_z_mm=z,
                n_x=1,
                n_y=1,
                overlap_percent=0.0,
            )
        )
        self.sleep(0.2)  # Allow EventBus handler to process
        return self

    def add_grid_region(
        self,
        region_id: str,
        center: Tuple[float, float, float],
        n_x: int = 1,
        n_y: int = 1,
        overlap_pct: float = 10.0,
    ) -> "AcquisitionSimulator":
        """
        Add a grid region via EventBus.

        Args:
            region_id: Unique identifier for the region
            center: (x, y, z) center position in mm
            n_x: Number of FOVs in X direction
            n_y: Number of FOVs in Y direction
            overlap_pct: Overlap percentage between FOVs

        Returns:
            self for chaining
        """
        x, y, z = center
        self.publish(
            AddFlexibleRegionCommand(
                region_id=region_id,
                center_x_mm=x,
                center_y_mm=y,
                center_z_mm=z,
                n_x=n_x,
                n_y=n_y,
                overlap_percent=overlap_pct,
            )
        )
        self.sleep(0.2)  # Allow EventBus handler to process
        return self

    def select_wells(
        self,
        well_ids: List[str],
        scan_size_mm: float = 1.0,
        overlap_pct: float = 10.0,
        shape: str = "Square",
    ) -> "AcquisitionSimulator":
        """
        Select wells for acquisition (like clicking wells in GUI).

        Args:
            well_ids: List of well IDs (e.g., ["A1", "A2", "B1"])
            scan_size_mm: Size of scan area per well
            overlap_pct: FOV overlap percentage
            shape: Scan pattern shape

        Returns:
            self for chaining
        """
        # Convert well IDs to row/col tuples
        selected_cells = []
        for well_id in well_ids:
            row = ord(well_id[0].upper()) - ord("A")
            col = int(well_id[1:]) - 1
            selected_cells.append((row, col))

        # Publish well selection
        self.publish(
            SelectedWellsChanged(
                format_name="96-well",
                selected_cells=tuple(selected_cells),
            )
        )

        # Publish scan coordinates command
        self.publish(
            SetWellSelectionScanCoordinatesCommand(
                scan_size_mm=scan_size_mm,
                overlap_percent=overlap_pct,
                shape=shape,
            )
        )
        self.sleep(0.1)
        return self

    def set_current_position_scan(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        scan_size_mm: float = 1.0,
        overlap_pct: float = 10.0,
        shape: str = "Square",
    ) -> "AcquisitionSimulator":
        """
        Set up a scan grid around the current or specified position.

        Args:
            x: X position (uses stage center if None)
            y: Y position (uses stage center if None)
            scan_size_mm: Size of scan area
            overlap_pct: FOV overlap percentage
            shape: Scan pattern shape

        Returns:
            self for chaining
        """
        if x is None or y is None:
            center = self._ctx.get_stage_center()
            x = x or center[0]
            y = y or center[1]

        self.publish(ClearScanCoordinatesCommand())
        self.publish(
            SetLiveScanCoordinatesCommand(
                x_mm=x,
                y_mm=y,
                scan_size_mm=scan_size_mm,
                overlap_percent=overlap_pct,
                shape=shape,
            )
        )
        self.sleep(0.1)
        return self

    def load_coordinates(
        self,
        coordinates: Dict[str, List[Tuple[float, float, float]]],
    ) -> "AcquisitionSimulator":
        """
        Load explicit coordinates (simulates loading from CSV).

        Args:
            coordinates: Dict mapping region_id to list of (x, y, z) positions

        Returns:
            self for chaining
        """
        # Convert to the format expected by LoadScanCoordinatesCommand
        region_fov_coords = {
            region_id: tuple(tuple(pos) for pos in positions)
            for region_id, positions in coordinates.items()
        }

        self.publish(
            LoadScanCoordinatesCommand(region_fov_coordinates=region_fov_coords)
        )
        # Wait for the event to be processed
        self.sleep(0.5)
        return self

    def set_manual_scan(
        self,
        shapes_mm: Tuple[Tuple[Tuple[float, float], ...], ...],
        overlap_pct: float = 10.0,
    ) -> "AcquisitionSimulator":
        """
        Set manual scan coordinates (simulates manual ROI drawing).

        Args:
            shapes_mm: Tuple of shapes, each a tuple of (x, y) points in mm
            overlap_pct: FOV overlap percentage

        Returns:
            self for chaining
        """
        self.publish(
            SetManualScanCoordinatesCommand(
                manual_shapes_mm=shapes_mm,
                overlap_percent=overlap_pct,
            )
        )
        self.sleep(0.2)  # Allow EventBus handler to process
        return self

    # =========================================================================
    # Configuration
    # =========================================================================

    def set_channels(self, channel_names: List[str]) -> "AcquisitionSimulator":
        """
        Select imaging channels via EventBus.

        Args:
            channel_names: List of channel configuration names

        Returns:
            self for chaining
        """
        self._selected_channels = channel_names
        self.publish(SetAcquisitionChannelsCommand(channel_names=channel_names))
        self.sleep(0.2)  # Allow EventBus handler to process
        return self

    def set_zstack(
        self,
        n_z: int = 1,
        delta_z_um: float = 1.0,
        mode: str = "FROM BOTTOM",
        use_piezo: bool = False,
    ) -> "AcquisitionSimulator":
        """
        Configure z-stack parameters.

        These are stored locally and applied via SetAcquisitionParametersCommand
        when start() is called, matching how the real GUI batches parameter updates.

        Args:
            n_z: Number of z-planes
            delta_z_um: Z-step size in micrometers
            mode: "FROM BOTTOM", "FROM CENTER", or "FROM TOP"
            use_piezo: Use piezo stage for z-movement

        Returns:
            self for chaining
        """
        self._n_z = n_z
        self._delta_z_um = delta_z_um
        self._z_stacking_config = mode
        self._use_piezo = use_piezo
        return self

    def set_timelapse(
        self,
        n_t: int = 1,
        delta_t_s: float = 0.0,
    ) -> "AcquisitionSimulator":
        """
        Configure time-lapse parameters.

        These are stored locally and applied via SetAcquisitionParametersCommand
        when start() is called, matching how the real GUI batches parameter updates.

        Args:
            n_t: Number of timepoints
            delta_t_s: Time interval between timepoints in seconds

        Returns:
            self for chaining
        """
        self._n_t = n_t
        self._delta_t_s = delta_t_s
        return self

    def set_autofocus(
        self,
        contrast_af: bool = False,
        laser_af: bool = False,
    ) -> "AcquisitionSimulator":
        """
        Configure autofocus settings.

        These are stored locally and applied via SetAcquisitionParametersCommand
        when start() is called, matching how the real GUI batches parameter updates.

        Note: For laser AF, we still need to set up the reference image directly
        as there's no EventBus command for this hardware-specific operation.

        Args:
            contrast_af: Enable contrast-based autofocus
            laser_af: Enable reflection/laser autofocus

        Returns:
            self for chaining
        """
        if laser_af:
            self._autofocus_mode = AutofocusMode.LASER_REFLECTION
        elif contrast_af:
            self._autofocus_mode = AutofocusMode.CONTRAST
        else:
            self._autofocus_mode = AutofocusMode.NONE

        # Set up laser AF reference if needed (hardware setup, not controller config)
        if laser_af and not self._bus_only:
            scope = self._ctx.microscope
            if hasattr(scope.addons, "camera_focus") and scope.addons.camera_focus:
                scope.addons.camera_focus.send_trigger()
                ref_image = scope.addons.camera_focus.read_frame()
                if ref_image is not None:
                    mpc = self._ctx.multipoint_controller
                    mpc.laserAutoFocusController.laser_af_properties.set_reference_image(
                        ref_image
                    )

        return self

    def set_skip_saving(self, skip: bool = True) -> "AcquisitionSimulator":
        """
        Configure whether to skip saving images to disk.

        Args:
            skip: If True, don't save images (faster for tests)

        Returns:
            self for chaining
        """
        self._skip_saving = skip
        return self

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def get_available_channels(self) -> List[str]:
        """Get list of available channel names."""
        return self._ctx.get_available_channels()

    def get_stage_limits(self) -> Dict[str, Tuple[float, float]]:
        """Get stage movement limits."""
        return self._ctx.get_stage_limits()

    def get_stage_center(self) -> Tuple[float, float, float]:
        """Get center position of the stage."""
        return self._ctx.get_stage_center()

    # =========================================================================
    # Execution
    # =========================================================================

    def start(
        self,
        experiment_id: Optional[str] = None,
        xy_mode: str = "Current Position",
    ) -> str:
        """
        Start the acquisition (non-blocking).

        Args:
            experiment_id: Unique experiment identifier
            xy_mode: "Current Position", "Select Wells", "Manual", "Load Coordinates"

        Returns:
            The experiment_id used for this acquisition
        """
        import uuid
        exp_id = experiment_id or f"test_{uuid.uuid4().hex[:8]}"

        # Get Z range from current position
        center = self._ctx.get_stage_center()
        z_center = center[2]
        z_range_um = self._n_z * self._delta_z_um
        z_min = z_center - z_range_um / 2000
        z_max = z_center + z_range_um / 2000

        # Map z-stacking mode string to index
        mode_map = {"FROM BOTTOM": 0, "FROM CENTER": 1, "FROM TOP": 2}
        z_stacking_config = mode_map.get(self._z_stacking_config, 0)

        # Publish acquisition parameters via EventBus (like the real GUI does)
        self.publish(
            SetAcquisitionParametersCommand(
                n_z=self._n_z,
                delta_z_um=self._delta_z_um,
                n_t=self._n_t,
                delta_t_s=self._delta_t_s,
                use_piezo=self._use_piezo,
                autofocus_mode=self._autofocus_mode,
                autofocus_interval_fovs=1,
                skip_saving=self._skip_saving,
                z_range=(z_min, z_max),
                z_stacking_config=z_stacking_config,
            )
        )
        self.sleep(0.2)  # Allow EventBus handler to process

        # Sort coordinates
        self.publish(SortScanCoordinatesCommand())
        self.sleep(0.1)  # Allow EventBus handler to process

        # Start experiment (creates directories, sets experiment_id)
        self.publish(StartNewExperimentCommand(experiment_id=exp_id))
        self.sleep(0.2)  # Allow EventBus handler to process

        # Start acquisition
        self.publish(
            StartAcquisitionCommand(
                experiment_id=exp_id,
                acquire_current_fov=False,
                xy_mode=xy_mode,
            )
        )
        # Don't wait here - acquisition starts asynchronously

        return exp_id

    def stop(self) -> None:
        """Stop/abort the current acquisition."""
        self.publish(StopAcquisitionCommand())

    def run_and_wait(
        self,
        experiment_id: Optional[str] = None,
        xy_mode: str = "Current Position",
        timeout_s: float = 60.0,
    ) -> AcquisitionResult:
        """
        Run acquisition and wait for completion.

        Args:
            experiment_id: Unique experiment identifier
            xy_mode: "Current Position", "Select Wells", "Manual", "Load Coordinates"
            timeout_s: Maximum time to wait for completion

        Returns:
            AcquisitionResult with success status and collected events
        """
        # Clear previous events
        self.monitor.clear()

        # Start acquisition and get the experiment_id
        start_time = time.time()
        exp_id = self.start(experiment_id=experiment_id, xy_mode=xy_mode)

        # Wait for completion, filtering by experiment_id prefix to avoid stale events
        # The controller appends a timestamp to the experiment_id, so we match by prefix
        finish_event = self.wait_for(
            AcquisitionWorkerFinished,
            timeout_s=timeout_s,
            predicate=lambda e: e.experiment_id.startswith(exp_id),
        )

        elapsed = time.time() - start_time

        # Allow time for final state events to be delivered
        # The IDLE state is published just before the finish event, but due to
        # async EventBus processing, it may not be delivered to the monitor yet
        self.sleep(0.3)

        # Filter all collected events to only those matching our experiment_id prefix
        def filter_by_exp_id(events, exp_id):
            """Filter events that have experiment_id starting with ours."""
            filtered = []
            for e in events:
                if hasattr(e, 'experiment_id'):
                    if e.experiment_id.startswith(exp_id):
                        filtered.append(e)
                else:
                    # Events without experiment_id are included (e.g., FOV registrations)
                    filtered.append(e)
            return filtered

        # Build result with filtered events
        result = AcquisitionResult(
            success=finish_event.success if finish_event else False,
            error=finish_event.error if finish_event else "Timeout waiting for completion",
            elapsed_time_s=elapsed,
            progress_events=filter_by_exp_id(
                self.monitor.get_events(AcquisitionProgress), exp_id
            ),
            worker_progress_events=filter_by_exp_id(
                self.monitor.get_events(AcquisitionWorkerProgress), exp_id
            ),
            state_changes=filter_by_exp_id(
                self.monitor.get_events(AcquisitionStateChanged), exp_id
            ),
            coordinate_events=self.monitor.get_events(AcquisitionCoordinates),
            fov_registrations=self.monitor.get_events(CurrentFOVRegistered),
        )

        result.total_images = len(result.coordinate_events)
        result.total_fovs = len(result.fov_registrations)

        return result

    def reset(self) -> None:
        """Reset simulator state."""
        super().reset()
        self.clear_coordinates()
        self._selected_channels = []
        self._n_z = 1
        self._delta_z_um = 1.0
        self._n_t = 1
        self._delta_t_s = 0.0
        self._autofocus_mode = AutofocusMode.NONE
        self._use_piezo = False
