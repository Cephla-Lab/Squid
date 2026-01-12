from dataclasses import dataclass
import itertools
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

import _def
from squid.backend.managers.objective_store import ObjectiveStore
from squid.backend.managers.scan_coordinates.grid import (
    GridConfig,
    filter_coordinates_in_bounds,
    generate_circular_grid,
    generate_grid_by_count,
    generate_grid_by_step_size,
    generate_polygon_grid,
    generate_rectangular_grid,
    generate_square_grid,
)
from squid.backend.managers.scan_coordinates.geometry import point_in_circle
from squid.backend.managers.scan_coordinates.wellplate import (
    row_col_to_well_id,
    well_id_to_position,
)
from squid.core.events import (
    AddFlexibleRegionCommand,
    AddFlexibleRegionWithStepSizeCommand,
    AddTemplateRegionCommand,
    ClearScanCoordinatesCommand,
    Event,
    LoadScanCoordinatesCommand,
    RemoveScanCoordinateRegionCommand,
    RenameScanCoordinateRegionCommand,
    RequestScanCoordinatesSnapshotCommand,
    ScanCoordinatesSnapshot,
    ScanCoordinatesUpdated,
    SelectedWellsChanged,
    SetLiveScanCoordinatesCommand,
    SetManualScanCoordinatesCommand,
    SetWellSelectionScanCoordinatesCommand,
    SortScanCoordinatesCommand,
    UpdateScanCoordinateRegionZCommand,
    WellplateFormatChanged,
    auto_subscribe,
    auto_unsubscribe,
    handles,
)
from squid.core.abc import AbstractStage, AbstractCamera
import squid.core.logging

if TYPE_CHECKING:
    from squid.core.events import EventBus


@dataclass
class ScanCoordinatesUpdate(Event):
    pass


@dataclass
class FovCenter:
    x_mm: float
    y_mm: float
    fov_width_mm: float = 0.0  # FOV width at time of registration
    fov_height_mm: float = 0.0  # FOV height at time of registration

    @staticmethod
    def from_scan_coordinates(
        scan_coordinates: List[Tuple[float, float]],
        fov_width_mm: float = 0.0,
        fov_height_mm: float = 0.0,
    ) -> List["FovCenter"]:
        return [
            FovCenter(x_mm=sc[0], y_mm=sc[1], fov_width_mm=fov_width_mm, fov_height_mm=fov_height_mm)
            for sc in scan_coordinates
        ]


@dataclass
class RemovedScanCoordinateRegion(ScanCoordinatesUpdate):
    fov_centers: List[FovCenter]


@dataclass
class AddScanCoordinateRegion(ScanCoordinatesUpdate):
    fov_centers: List[FovCenter]


@dataclass
class ClearedScanCoordinates(ScanCoordinatesUpdate):
    pass


class ScanCoordinates:
    """Manages scan coordinates for multi-point acquisitions.

    Publishes scan coordinate updates via EventBus (when configured).
    """

    def __init__(
        self,
        objectiveStore: ObjectiveStore,
        stage: AbstractStage,
        camera: AbstractCamera,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        # Wellplate settings
        self.objectiveStore: ObjectiveStore = objectiveStore
        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self._event_bus: Optional["EventBus"] = event_bus
        self._commands_subscribed: bool = False
        self._subscriptions: List[Tuple[type, Callable]] = []
        self.acquisition_pattern: str = _def.ACQUISITION_PATTERN
        self.fov_pattern: str = _def.FOV_PATTERN
        self.format: str = _def.WELLPLATE_FORMAT
        self.a1_x_mm: float = _def.A1_X_MM
        self.a1_y_mm: float = _def.A1_Y_MM
        self.wellplate_offset_x_mm: float = _def.WELLPLATE_OFFSET_X_mm
        self.wellplate_offset_y_mm: float = _def.WELLPLATE_OFFSET_Y_mm
        self.well_spacing_mm: float = _def.WELL_SPACING_MM
        self.well_size_mm: float = _def.WELL_SIZE_MM
        self.a1_x_pixel: Optional[float] = None
        self.a1_y_pixel: Optional[float] = None
        self.number_of_skip: Optional[int] = None

        # State for event-driven well selection / scan settings (UI never injected)
        self._selected_well_cells: Tuple[Tuple[int, int], ...] = tuple()
        self._well_selection_scan_size_mm: float = 0.0
        self._well_selection_overlap_percent: float = 10.0
        self._well_selection_shape: str = "Square"

        # Centralized region management
        self.region_centers: Dict[str, List[float]] = {}  # {region_id: [x, y, z]}
        self.region_shapes: Dict[str, str] = {}  # {region_id: "Square"}
        self.region_fov_coordinates: Dict[
            str, List[Tuple[float, ...]]
        ] = {}  # {region_id: [(x,y,z), ...]}

        self._subscribe_to_commands()

    def set_event_bus(self, event_bus: Optional["EventBus"]) -> None:
        """Set the event bus for publishing ScanCoordinatesUpdated events."""
        if self._event_bus is event_bus:
            return
        if self._event_bus is not None and self._subscriptions:
            auto_unsubscribe(self._subscriptions, self._event_bus)
            self._subscriptions = []
            self._commands_subscribed = False
        self._event_bus = event_bus
        self._subscribe_to_commands()

    def _get_current_fov_dimensions(self) -> Tuple[float, float]:
        """Get current FOV dimensions from camera and objective."""
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        if pixel_size_factor is None:
            pixel_size_factor = 1.0

        fov_width_mm = pixel_size_factor * self.camera.get_fov_size_mm()
        if hasattr(self.camera, "get_fov_height_mm") and self.camera.get_fov_height_mm() is not None:
            fov_height_mm = pixel_size_factor * self.camera.get_fov_height_mm()
        else:
            fov_height_mm = fov_width_mm

        return fov_width_mm, fov_height_mm

    def _subscribe_to_commands(self) -> None:
        if self._event_bus is None:
            return
        if self._commands_subscribed:
            return
        self._subscriptions = auto_subscribe(self, self._event_bus)
        self._commands_subscribed = True

    def shutdown(self) -> None:
        """Unsubscribe handlers from the EventBus."""
        if self._event_bus is None or not self._subscriptions:
            return
        auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
        self._commands_subscribed = False

    @handles(ClearScanCoordinatesCommand)
    def _on_clear_scan_coordinates(self, _cmd: Event) -> None:
        self.clear_regions()

    @handles(SortScanCoordinatesCommand)
    def _on_sort_scan_coordinates(self, _cmd: Event) -> None:
        self.sort_coordinates()

    @handles(SetLiveScanCoordinatesCommand)
    def _on_set_live_scan_coordinates(self, cmd: Event) -> None:
        assert isinstance(cmd, SetLiveScanCoordinatesCommand)
        self.set_live_scan_coordinates(
            cmd.x_mm, cmd.y_mm, cmd.scan_size_mm, cmd.overlap_percent, cmd.shape
        )

    @handles(AddTemplateRegionCommand)
    def _on_add_template_region(self, cmd: Event) -> None:
        assert isinstance(cmd, AddTemplateRegionCommand)
        if len(cmd.x_offsets_mm) != len(cmd.y_offsets_mm):
            self._log.warning(
                "AddTemplateRegionCommand ignored due to length mismatch: "
                f"{len(cmd.x_offsets_mm)=} {len(cmd.y_offsets_mm)=}"
            )
            return
        self.add_template_region(
            cmd.center_x_mm,
            cmd.center_y_mm,
            cmd.center_z_mm,
            np.array(cmd.x_offsets_mm, dtype=float),
            np.array(cmd.y_offsets_mm, dtype=float),
            cmd.region_id,
        )

    @handles(SelectedWellsChanged)
    def _on_selected_wells_changed(self, event: Event) -> None:
        assert isinstance(event, SelectedWellsChanged)
        self._selected_well_cells = tuple(event.selected_cells)
        self._log.info(f"_on_selected_wells_changed: {len(self._selected_well_cells)} wells selected")
        # If we already have scan settings, recompute immediately.
        if self._well_selection_scan_size_mm > 0:
            self._apply_well_selection_coordinates()
        else:
            self._log.info("_on_selected_wells_changed: scan_size_mm=0, not applying coordinates yet")

    @handles(SetWellSelectionScanCoordinatesCommand)
    def _on_set_well_selection_scan_coordinates(self, cmd: Event) -> None:
        assert isinstance(cmd, SetWellSelectionScanCoordinatesCommand)
        self._log.info(f"_on_set_well_selection_scan_coordinates: scan_size={cmd.scan_size_mm}mm, overlap={cmd.overlap_percent}%, shape={cmd.shape}")
        self._well_selection_scan_size_mm = float(cmd.scan_size_mm)
        self._well_selection_overlap_percent = float(cmd.overlap_percent)
        self._well_selection_shape = str(cmd.shape)
        self._apply_well_selection_coordinates()

    def _apply_well_selection_coordinates(self) -> None:
        # Only applies to wellplate selection mode; empty selection clears.
        self._log.info(f"_apply_well_selection_coordinates: {len(self._selected_well_cells)} selected cells, scan_size={self._well_selection_scan_size_mm}mm")
        if not self._selected_well_cells:
            self._log.info("_apply_well_selection_coordinates: no cells selected, clearing regions")
            self.clear_regions()
            return
        self.set_well_coordinates_from_selected_cells(
            selected_cells=list(self._selected_well_cells),
            scan_size_mm=self._well_selection_scan_size_mm,
            overlap_percent=self._well_selection_overlap_percent,
            shape=self._well_selection_shape,
        )

    @handles(SetManualScanCoordinatesCommand)
    def _on_set_manual_scan_coordinates(self, cmd: Event) -> None:
        assert isinstance(cmd, SetManualScanCoordinatesCommand)
        manual_shapes = None
        if cmd.manual_shapes_mm is not None:
            manual_shapes = [np.array(shape, dtype=float) for shape in cmd.manual_shapes_mm]
        self.set_manual_coordinates(manual_shapes, cmd.overlap_percent)

    @handles(LoadScanCoordinatesCommand)
    def _on_load_scan_coordinates(self, cmd: Event) -> None:
        assert isinstance(cmd, LoadScanCoordinatesCommand)
        self.clear_regions()

        for region_id, coords_tuple in cmd.region_fov_coordinates.items():
            coords: List[Tuple[float, ...]] = []
            for c in coords_tuple:
                coords.append(tuple(float(v) for v in c))
            if not coords:
                continue
            self.region_fov_coordinates[str(region_id)] = coords
            if cmd.region_centers and region_id in cmd.region_centers:
                center_raw = cmd.region_centers[region_id]
                center = [float(center_raw[0]), float(center_raw[1])]
                if len(center_raw) > 2:
                    center.append(float(center_raw[2]))
            else:
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                z = float(coords[0][2]) if len(coords[0]) > 2 else float(self.stage.get_pos().z_mm)
                center = [float(np.mean(xs)), float(np.mean(ys)), z]
            if len(center) == 2:
                center.append(float(self.stage.get_pos().z_mm))
            self.region_centers[str(region_id)] = center
            self.region_shapes[str(region_id)] = "Loaded"
            fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
            self._publish_update(
                AddScanCoordinateRegion(
                    fov_centers=FovCenter.from_scan_coordinates(coords, fov_width_mm, fov_height_mm)
                )
            )

    @handles(RequestScanCoordinatesSnapshotCommand)
    def _on_request_scan_coordinates_snapshot(self, cmd: Event) -> None:
        assert isinstance(cmd, RequestScanCoordinatesSnapshotCommand)
        region_fov_coordinates = {
            region_id: tuple(tuple(float(v) for v in coord) for coord in coords)
            for region_id, coords in self.region_fov_coordinates.items()
        }
        region_centers = {
            region_id: tuple(float(v) for v in center)
            for region_id, center in self.region_centers.items()
        }
        if self._event_bus is not None:
            self._event_bus.publish(
                ScanCoordinatesSnapshot(
                    request_id=cmd.request_id,
                    region_fov_coordinates=region_fov_coordinates,
                    region_centers=region_centers,
                )
            )

    @handles(AddFlexibleRegionCommand)
    def _on_add_flexible_region(self, cmd: Event) -> None:
        assert isinstance(cmd, AddFlexibleRegionCommand)
        self.add_flexible_region(
            region_id=cmd.region_id,
            center_x=cmd.center_x_mm,
            center_y=cmd.center_y_mm,
            center_z=cmd.center_z_mm,
            Nx=cmd.n_x,
            Ny=cmd.n_y,
            overlap_percent=cmd.overlap_percent,
        )

    @handles(AddFlexibleRegionWithStepSizeCommand)
    def _on_add_flexible_region_with_step_size(self, cmd: Event) -> None:
        assert isinstance(cmd, AddFlexibleRegionWithStepSizeCommand)
        self.add_flexible_region_with_step_size(
            region_id=cmd.region_id,
            center_x=cmd.center_x_mm,
            center_y=cmd.center_y_mm,
            center_z=cmd.center_z_mm,
            Nx=cmd.n_x,
            Ny=cmd.n_y,
            dx=cmd.delta_x_mm,
            dy=cmd.delta_y_mm,
        )

    @handles(RemoveScanCoordinateRegionCommand)
    def _on_remove_region_command(self, cmd: Event) -> None:
        assert isinstance(cmd, RemoveScanCoordinateRegionCommand)
        self.remove_region(cmd.region_id)

    @handles(RenameScanCoordinateRegionCommand)
    def _on_rename_region_command(self, cmd: Event) -> None:
        assert isinstance(cmd, RenameScanCoordinateRegionCommand)
        self.rename_region(cmd.old_region_id, cmd.new_region_id)

    @handles(UpdateScanCoordinateRegionZCommand)
    def _on_update_region_z_command(self, cmd: Event) -> None:
        assert isinstance(cmd, UpdateScanCoordinateRegionZCommand)
        self.update_region_z_level(cmd.region_id, cmd.z_mm)

    @handles(WellplateFormatChanged)
    def _on_wellplate_format_changed(self, event: Event) -> None:
        assert isinstance(event, WellplateFormatChanged)
        self.update_wellplate_settings(
            format_=event.format_name,
            a1_x_mm=event.a1_x_mm,
            a1_y_mm=event.a1_y_mm,
            a1_x_pixel=event.a1_x_pixel,
            a1_y_pixel=event.a1_y_pixel,
            size_mm=event.well_size_mm,
            spacing_mm=event.well_spacing_mm,
            number_of_skip=event.number_of_skip,
        )
        # Conservative: format changes invalidate existing regions.
        self.clear_regions()

    def _publish_update(self, update: ScanCoordinatesUpdate) -> None:
        if self._event_bus is None:
            return
        if not isinstance(update, Event):  # pragma: no cover - defensive
            raise TypeError("ScanCoordinatesUpdate must inherit from Event")
        self._event_bus.publish(update)
        self._publish_coordinates_updated()

    def _publish_coordinates_updated(self) -> None:
        """Publish ScanCoordinatesUpdated event if event_bus is configured."""
        if self._event_bus is None:
            return
        self._event_bus.publish(
            ScanCoordinatesUpdated(
                total_regions=len(self.region_fov_coordinates),
                total_fovs=sum(len(coords) for coords in self.region_fov_coordinates.values()),
                region_ids=tuple(self.region_fov_coordinates.keys()),
            )
        )

    def update_wellplate_settings(
        self,
        format_: str,
        a1_x_mm: float,
        a1_y_mm: float,
        a1_x_pixel: float,
        a1_y_pixel: float,
        size_mm: float,
        spacing_mm: float,
        number_of_skip: int,
    ) -> None:
        self.format = format_
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = size_mm
        self.well_spacing_mm = spacing_mm
        self.number_of_skip = number_of_skip

    def rename_region(self, old_region_id: str, new_region_id: str) -> None:
        if old_region_id == new_region_id:
            return
        if old_region_id not in self.region_centers:
            return
        if new_region_id in self.region_centers:
            self._log.warning(f"Cannot rename {old_region_id} -> {new_region_id}: target exists")
            return
        self.region_centers[new_region_id] = self.region_centers.pop(old_region_id)
        if old_region_id in self.region_shapes:
            self.region_shapes[new_region_id] = self.region_shapes.pop(old_region_id)
        if old_region_id in self.region_fov_coordinates:
            self.region_fov_coordinates[new_region_id] = self.region_fov_coordinates.pop(old_region_id)
        self._publish_coordinates_updated()

    def update_region_z_level(self, region_id: str, new_z: float) -> None:
        if region_id not in self.region_centers:
            return
        center = self.region_centers[region_id]
        if len(center) >= 3:
            center[2] = new_z
        else:
            while len(center) < 2:
                center.append(0.0)
            center.append(new_z)
        self.region_centers[region_id] = center

        if region_id in self.region_fov_coordinates:
            updated: List[Tuple[float, ...]] = []
            for coord in self.region_fov_coordinates[region_id]:
                if len(coord) >= 2:
                    updated.append((float(coord[0]), float(coord[1]), float(new_z)))
            self.region_fov_coordinates[region_id] = updated
        self._publish_coordinates_updated()

    def set_well_coordinates_from_selected_cells(
        self,
        selected_cells: List[Tuple[int, int]],
        scan_size_mm: float,
        overlap_percent: float,
        shape: str,
    ) -> None:
        if self.format == "glass slide":
            pos = self.stage.get_pos()
            self.set_live_scan_coordinates(
                pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape
            )
            return

        # Replace entire region set for "select wells" mode.
        selected_ids: Dict[str, Tuple[float, float]] = {}
        for row, col in selected_cells:
            well_id = row_col_to_well_id(int(row), int(col))
            position = well_id_to_position(
                well_id,
                a1_x_mm=float(self.a1_x_mm),
                a1_y_mm=float(self.a1_y_mm),
                well_spacing_mm=float(self.well_spacing_mm),
                offset_x_mm=float(self.wellplate_offset_x_mm),
                offset_y_mm=float(self.wellplate_offset_y_mm),
            )
            if position is None:
                self._log.warning("Invalid well identifier for row=%s col=%s", row, col)
                continue
            selected_ids[well_id] = position

        # Remove regions not selected (including any non-well regions)
        for region_id in list(self.region_centers.keys()):
            if region_id not in selected_ids:
                self.remove_region(region_id)

        # Add/update selected wells
        for well_id, (x_mm, y_mm) in selected_ids.items():
            z_mm: Optional[float] = None
            if well_id in self.region_centers and len(self.region_centers[well_id]) >= 3:
                z_mm = float(self.region_centers[well_id][2])
            if well_id in self.region_centers:
                self.remove_region(well_id)
            self.add_region(
                well_id,
                x_mm,
                y_mm,
                scan_size_mm,
                overlap_percent,
                shape,
                center_z_mm=z_mm,
            )

    def set_live_scan_coordinates(
        self,
        x_mm: float,
        y_mm: float,
        scan_size_mm: float,
        overlap_percent: float,
        shape: str,
    ) -> None:
        if self.region_centers:
            self.clear_regions()
        self.add_region("current", x_mm, y_mm, scan_size_mm, overlap_percent, shape)

    def set_well_coordinates(
        self, scan_size_mm: float, overlap_percent: float, shape: str
    ) -> None:
        self.set_well_coordinates_from_selected_cells(
            selected_cells=list(self._selected_well_cells),
            scan_size_mm=scan_size_mm,
            overlap_percent=overlap_percent,
            shape=shape,
        )

    def set_manual_coordinates(
        self, manual_shapes: Optional[List[np.ndarray]], overlap_percent: float
    ) -> None:
        self.clear_regions()
        if manual_shapes is not None:
            # Get current FOV dimensions for manual regions
            pixel_size_factor = self.objectiveStore.get_pixel_size_factor() or 1.0
            fov_width_mm = pixel_size_factor * self.camera.get_fov_size_mm()
            if hasattr(self.camera, 'get_fov_height_mm') and self.camera.get_fov_height_mm() is not None:
                fov_height_mm = pixel_size_factor * self.camera.get_fov_height_mm()
            else:
                fov_height_mm = fov_width_mm

            # Handle manual ROIs
            scan_coordinates = None
            for i, shape_coords in enumerate(manual_shapes):
                scan_coordinates = self.get_points_for_manual_region(
                    shape_coords, overlap_percent
                )
                if scan_coordinates:
                    if len(manual_shapes) <= 1:
                        region_name = "manual"
                    else:
                        region_name = f"manual{i}"
                    center = np.mean(shape_coords, axis=0)
                    self.region_centers[region_name] = [center[0], center[1]]
                    self.region_shapes[region_name] = "Manual"
                    self.region_fov_coordinates[region_name] = scan_coordinates
                    self._log.info(f"Added Manual Region: {region_name}")
                    self._publish_update(
                        AddScanCoordinateRegion(
                            fov_centers=FovCenter.from_scan_coordinates(
                                scan_coordinates,
                                fov_width_mm=fov_width_mm,
                                fov_height_mm=fov_height_mm,
                            )
                        )
                    )
        else:
            self._log.info("No Manual ROI found")

    def add_region(
        self,
        well_id: str,
        center_x: float,
        center_y: float,
        scan_size_mm: float,
        overlap_percent: float = 10,
        shape: str = "Square",
        center_z_mm: Optional[float] = None,
    ) -> None:
        """Add region based on user inputs.

        The scan_size_mm specifies the area to cover. The number of tiles is calculated
        to ensure the entire scan area is covered with the specified overlap.

        Coverage calculation:
        - n tiles cover: (n-1) * step + fov
        - To cover scan_size: n = ceil((scan_size - fov) / step) + 1
        """
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        if pixel_size_factor is None:
            pixel_size_factor = 1.0

        # Get raw camera FOV for debugging
        raw_fov_width = self.camera.get_fov_size_mm()
        raw_fov_height = self.camera.get_fov_height_mm() if hasattr(self.camera, 'get_fov_height_mm') else None

        # Get FOV dimensions - use width/height methods if available for non-square cameras
        fov_width_mm = pixel_size_factor * self.camera.get_fov_size_mm()
        if hasattr(self.camera, 'get_fov_height_mm') and self.camera.get_fov_height_mm() is not None:
            fov_height_mm = pixel_size_factor * self.camera.get_fov_height_mm()
        else:
            fov_height_mm = fov_width_mm  # Fall back to square FOV

        # Calculate step sizes for X and Y separately (distance between tile centers)
        step_x_mm = fov_width_mm * (1 - overlap_percent / 100)
        step_y_mm = fov_height_mm * (1 - overlap_percent / 100)

        # Log FOV info for debugging
        overlap_x_mm = fov_width_mm - step_x_mm
        overlap_y_mm = fov_height_mm - step_y_mm
        self._log.info(
            f"add_region: scan_size={scan_size_mm:.3f}mm, overlap={overlap_percent}%, "
            f"pixel_size_factor={pixel_size_factor:.4f}, "
            f"raw_camera_FOV={raw_fov_width}x{raw_fov_height}mm, "
            f"effective_FOV={fov_width_mm:.3f}x{fov_height_mm:.3f}mm, "
            f"step={step_x_mm:.3f}x{step_y_mm:.3f}mm, "
            f"actual_overlap={overlap_x_mm:.3f}x{overlap_y_mm:.3f}mm ({100*overlap_x_mm/fov_width_mm:.1f}%x{100*overlap_y_mm/fov_height_mm:.1f}%)"
        )

        config = GridConfig(
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
            overlap_percent=overlap_percent,
            fov_pattern=self.fov_pattern,
        )

        if shape == "Rectangle":
            width_mm = scan_size_mm
            height_mm = scan_size_mm * 0.6
            scan_coordinates = generate_rectangular_grid(
                center_x, center_y, width_mm, height_mm, config
            )
        elif shape == "Circle":
            scan_coordinates = generate_circular_grid(
                center_x, center_y, scan_size_mm, config
            )
        else:
            scan_coordinates = generate_square_grid(
                center_x, center_y, scan_size_mm, config
            )

        x_min = _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = _def.SOFTWARE_POS_LIMIT.Y_POSITIVE
        scan_coordinates = filter_coordinates_in_bounds(
            scan_coordinates, x_min, x_max, y_min, y_max
        )

        if not scan_coordinates and shape == "Circle":
            if self.validate_coordinates(center_x, center_y):
                scan_coordinates.append((center_x, center_y))

        self.region_shapes[well_id] = shape
        self.region_centers[well_id] = [
            float(center_x),
            float(center_y),
            float(self.stage.get_pos().z_mm if center_z_mm is None else center_z_mm),
        ]
        self.region_fov_coordinates[well_id] = scan_coordinates
        self._publish_update(
            AddScanCoordinateRegion(
                fov_centers=FovCenter.from_scan_coordinates(
                    scan_coordinates,
                    fov_width_mm=fov_width_mm,
                    fov_height_mm=fov_height_mm,
                )
            )
        )
        # Log positions summary for overlap verification
        if len(scan_coordinates) > 0:
            x_positions = sorted(set(c[0] for c in scan_coordinates))
            y_positions = sorted(set(c[1] for c in scan_coordinates))
            if len(x_positions) > 1:
                x_spacing = x_positions[1] - x_positions[0]
                self._log.info(f"  X positions: {[f'{x:.3f}' for x in x_positions[:4]]}{'...' if len(x_positions)>4 else ''} (spacing={x_spacing:.3f}mm)")
            if len(y_positions) > 1:
                y_spacing = y_positions[1] - y_positions[0]
                self._log.info(f"  Y positions: {[f'{y:.3f}' for y in y_positions[:4]]}{'...' if len(y_positions)>4 else ''} (spacing={y_spacing:.3f}mm)")
        self._log.info(f"Added Region: {well_id} with {len(scan_coordinates)} FOV positions")

    def remove_region(self, well_id: str) -> None:
        if well_id in self.region_centers:
            removed_fov_centers: List[FovCenter] = []
            del self.region_centers[well_id]

            if well_id in self.region_shapes:
                del self.region_shapes[well_id]

            if well_id in self.region_fov_coordinates:
                region_scan_coordinates = self.region_fov_coordinates.pop(well_id)
                for coord in region_scan_coordinates:
                    removed_fov_centers.append(FovCenter(x_mm=coord[0], y_mm=coord[1]))

            self._log.info(f"Removed Region: {well_id}")
            self._publish_update(
                RemovedScanCoordinateRegion(fov_centers=removed_fov_centers)
            )

    def clear_regions(self) -> None:
        self.region_centers.clear()
        self.region_shapes.clear()
        self.region_fov_coordinates.clear()
        self._publish_update(ClearedScanCoordinates())
        self._log.info("Cleared All Regions")

    def add_flexible_region(
        self,
        region_id: str,
        center_x: float,
        center_y: float,
        center_z: float,
        Nx: int,
        Ny: int,
        overlap_percent: float = 10,
    ) -> None:
        """Convert grid parameters NX, NY to FOV coordinates based on overlap"""
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        if pixel_size_factor is None:
            pixel_size_factor = 1.0

        # Get FOV dimensions - use width/height methods if available for non-square cameras
        fov_width_mm = pixel_size_factor * self.camera.get_fov_size_mm()
        if hasattr(self.camera, 'get_fov_height_mm') and self.camera.get_fov_height_mm() is not None:
            fov_height_mm = pixel_size_factor * self.camera.get_fov_height_mm()
        else:
            fov_height_mm = fov_width_mm

        config = GridConfig(
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
            overlap_percent=overlap_percent,
            fov_pattern=self.fov_pattern,
        )
        scan_coordinates = generate_grid_by_count(
            center_x, center_y, center_z, Nx, Ny, config
        )
        x_min = _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = _def.SOFTWARE_POS_LIMIT.Y_POSITIVE
        scan_coordinates = filter_coordinates_in_bounds(
            scan_coordinates, x_min, x_max, y_min, y_max
        )

        # Region coordinates are already centered since center_x, center_y is grid center
        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            self._publish_update(
                AddScanCoordinateRegion(
                    fov_centers=FovCenter.from_scan_coordinates(
                        scan_coordinates,
                        fov_width_mm=fov_width_mm,
                        fov_height_mm=fov_height_mm,
                    )
                )
            )
        else:
            self._log.info(f"Region Out of Bounds: {region_id}")

    def add_single_fov_region(
        self, region_id: str, center_x: float, center_y: float, center_z: float
    ) -> None:
        # Clamp to software limits to avoid errors in simulation or user input
        x_min = _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = _def.SOFTWARE_POS_LIMIT.Y_POSITIVE

        clamped_x = min(max(center_x, x_min), x_max)
        clamped_y = min(max(center_y, y_min), y_max)
        if clamped_x != center_x or clamped_y != center_y:
            self._log.warning(
                f"FOV center ({center_x},{center_y}) clamped to ({clamped_x},{clamped_y}) due to limits."
            )

        self.region_centers[region_id] = [clamped_x, clamped_y, center_z]
        self.region_fov_coordinates[region_id] = [(clamped_x, clamped_y)]
        fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
        self._publish_update(
            AddScanCoordinateRegion(
                fov_centers=[
                    FovCenter(
                        x_mm=clamped_x,
                        y_mm=clamped_y,
                        fov_width_mm=fov_width_mm,
                        fov_height_mm=fov_height_mm,
                    )
                ]
            )
        )

    def add_flexible_region_with_step_size(
        self,
        region_id: str,
        center_x: float,
        center_y: float,
        center_z: float,
        Nx: int,
        Ny: int,
        dx: float,
        dy: float,
    ) -> None:
        """Convert grid parameters NX, NY to FOV coordinates based on dx, dy"""
        scan_coordinates = generate_grid_by_step_size(
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
            nx=Nx,
            ny=Ny,
            dx=dx,
            dy=dy,
            fov_pattern=self.fov_pattern,
        )
        x_min = _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = _def.SOFTWARE_POS_LIMIT.Y_POSITIVE
        scan_coordinates = filter_coordinates_in_bounds(
            scan_coordinates, x_min, x_max, y_min, y_max
        )

        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
            self._publish_update(
                AddScanCoordinateRegion(
                    fov_centers=FovCenter.from_scan_coordinates(
                        scan_coordinates, fov_width_mm, fov_height_mm
                    )
                )
            )
        else:
            print(f"Region Out of Bounds: {region_id}")

    def get_points_for_manual_region(
        self, shape_coords: np.ndarray, overlap_percent: float
    ) -> List[Tuple[float, float]]:
        """Add region from manually drawn polygon shape"""
        if shape_coords is None or len(shape_coords) < 3:
            self._log.error("Invalid manual ROI data")
            return []

        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        if pixel_size_factor is None:
            pixel_size_factor = 1.0

        # Get FOV dimensions - use width/height methods if available for non-square cameras
        fov_width_mm = pixel_size_factor * self.camera.get_fov_size_mm()
        if hasattr(self.camera, 'get_fov_height_mm') and self.camera.get_fov_height_mm() is not None:
            fov_height_mm = pixel_size_factor * self.camera.get_fov_height_mm()
        else:
            fov_height_mm = fov_width_mm

        # Ensure shape_coords is a numpy array
        shape_coords = np.array(shape_coords)
        if shape_coords.ndim == 1:
            shape_coords = shape_coords.reshape(-1, 2)
        elif shape_coords.ndim > 2:
            self._log.error(f"Unexpected shape of manual_shape: {shape_coords.shape}")
            return []
        config = GridConfig(
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
            overlap_percent=overlap_percent,
            fov_pattern=self.fov_pattern,
        )
        scan_coordinates = generate_polygon_grid(shape_coords, config)
        if not scan_coordinates:
            return []

        x_min = _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = _def.SOFTWARE_POS_LIMIT.Y_POSITIVE
        return filter_coordinates_in_bounds(scan_coordinates, x_min, x_max, y_min, y_max)

    def add_template_region(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        template_x_mm: np.ndarray,
        template_y_mm: np.ndarray,
        region_id: str,
    ) -> None:
        """Add a region based on a template of x and y coordinates"""
        scan_coordinates = []
        for i in range(len(template_x_mm)):
            x = float(x_mm + template_x_mm[i])
            y = float(y_mm + template_y_mm[i])
            if self.validate_coordinates(x, y):
                scan_coordinates.append((x, y))
        self.region_centers[region_id] = [x_mm, y_mm, z_mm]
        self.region_fov_coordinates[region_id] = scan_coordinates
        fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
        self._publish_update(
            AddScanCoordinateRegion(
                fov_centers=FovCenter.from_scan_coordinates(
                    scan_coordinates, fov_width_mm, fov_height_mm
                )
            )
        )

    def region_contains_coordinate(self, region_id: str, x: float, y: float) -> bool:
        if not self.validate_region(region_id):
            return False

        bounds = self.get_region_bounds(region_id)
        shape = self.get_region_shape(region_id)

        # For square regions
        if not (
            bounds["min_x"] <= x <= bounds["max_x"]
            and bounds["min_y"] <= y <= bounds["max_y"]
        ):
            return False

        if shape == "Manual":
            fov_width_mm, fov_height_mm = self._get_current_fov_dimensions()
            half_w = fov_width_mm / 2
            half_h = fov_height_mm / 2
            for coord in self.region_fov_coordinates.get(region_id, []):
                if abs(x - coord[0]) <= half_w and abs(y - coord[1]) <= half_h:
                    return True
            return False

        # For circle regions
        if shape == "Circle":
            center_x = (bounds["max_x"] + bounds["min_x"]) / 2
            center_y = (bounds["max_y"] + bounds["min_y"]) / 2
            radius = (bounds["max_x"] - bounds["min_x"]) / 2
            if not point_in_circle(x, y, center_x, center_y, radius):
                return False

        return True

    def has_regions(self) -> bool:
        """Check if any regions exist"""
        return len(self.region_centers) > 0

    def validate_region(self, region_id: str) -> bool:
        """Validate a region exists"""
        return (
            region_id in self.region_centers
            and region_id in self.region_fov_coordinates
        )

    def validate_coordinates(self, x: float, y: float) -> bool:
        return (
            _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
            <= x
            <= _def.SOFTWARE_POS_LIMIT.X_POSITIVE
            and _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
            <= y
            <= _def.SOFTWARE_POS_LIMIT.Y_POSITIVE
        )

    def sort_coordinates(self) -> None:
        self._log.info(f"Acquisition pattern: {self.acquisition_pattern}")

        if len(self.region_centers) <= 1:
            return

        def sort_key(item):
            key, coord = item
            if "manual" in key:
                return (0, coord[1], coord[0])  # Manual coords: sort by y, then x
            else:
                letters = "".join(c for c in key if c.isalpha())
                numbers = "".join(c for c in key if c.isdigit())

                letter_value = 0
                for i, letter in enumerate(reversed(letters)):
                    letter_value += (ord(letter) - ord("A")) * (26**i)

                return (
                    1,
                    letter_value,
                    int(numbers),
                )  # Well coords: sort by letter value, then number

        sorted_items = sorted(self.region_centers.items(), key=sort_key)

        if self.acquisition_pattern == "S-Pattern":
            # Group by row and reverse alternate rows
            rows = itertools.groupby(
                sorted_items, key=lambda x: x[1][1] if "manual" in x[0] else x[0][0]
            )
            sorted_items = []
            for i, (_, group) in enumerate(rows):
                row = list(group)
                if i % 2 == 1:
                    row.reverse()
                sorted_items.extend(row)

        # Update dictionaries efficiently
        self.region_centers = {k: v for k, v in sorted_items}
        self.region_fov_coordinates = {
            k: self.region_fov_coordinates[k]
            for k, _ in sorted_items
            if k in self.region_fov_coordinates
        }

    def get_region_bounds(self, region_id: str) -> Optional[Dict[str, float]]:
        """Get region boundaries"""
        if not self.validate_region(region_id):
            return None
        fovs = np.array(self.region_fov_coordinates[region_id])
        if fovs.size == 0:
            return None
        if fovs.ndim == 1:
            # Single point -> reshape to (1, N)
            fovs = fovs.reshape(1, -1)
        return {
            "min_x": np.min(fovs[:, 0]),
            "max_x": np.max(fovs[:, 0]),
            "min_y": np.min(fovs[:, 1]),
            "max_y": np.max(fovs[:, 1]),
        }

    def get_region_shape(self, region_id: str) -> Optional[str]:
        if not self.validate_region(region_id):
            return None
        return self.region_shapes[region_id]

    def get_scan_bounds(self) -> Optional[Dict[str, Tuple[float, float]]]:
        """Get bounds of all scan regions with margin"""
        if not self.has_regions():
            return None

        min_x = float("inf")
        max_x = float("-inf")
        min_y = float("inf")
        max_y = float("-inf")

        # Find global bounds across all regions
        for region_id in self.region_fov_coordinates.keys():
            bounds = self.get_region_bounds(region_id)
            if bounds:
                min_x = min(min_x, bounds["min_x"])
                max_x = max(max_x, bounds["max_x"])
                min_y = min(min_y, bounds["min_y"])
                max_y = max(max_y, bounds["max_y"])

        if min_x == float("inf"):
            return None

        # Add margin around bounds (5% of larger dimension)
        width = max_x - min_x
        height = max_y - min_y
        margin = max(width, height) * 0.00  # 0.05

        return {
            "x": (min_x - margin, max_x + margin),
            "y": (min_y - margin, max_y + margin),
        }

    def update_fov_z_level(self, region_id: str, fov: int, new_z: float) -> None:
        """Update z-level for a specific FOV and its region center"""
        if not self.validate_region(region_id):
            print(f"Region {region_id} not found")
            return

        # Update FOV coordinates
        fov_coords = self.region_fov_coordinates[region_id]
        if fov < len(fov_coords):
            # Handle both (x,y) and (x,y,z) cases
            x, y = fov_coords[fov][:2]  # Takes first two elements regardless of length
            self.region_fov_coordinates[region_id][fov] = (x, y, new_z)

        # If first FOV, update region center coordinates
        if fov == 0:
            if len(self.region_centers[region_id]) == 3:
                self.region_centers[region_id][2] = new_z
            else:
                self.region_centers[region_id].append(new_z)

        self._log.info(f"Updated z-level to {new_z} for region:{region_id}, fov:{fov}")


class ScanCoordinatesSiLA2(ScanCoordinates):
    def __init__(
        self,
        objectiveStore: ObjectiveStore,
        stage: AbstractStage,
        camera: AbstractCamera,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        super().__init__(
            objectiveStore=objectiveStore,
            stage=stage,
            camera=camera,
            event_bus=event_bus,
        )

    def get_scan_coordinates_from_selected_wells(
        self,
        wellplate_format: str,
        well_name: str,
        scan_size_mm: Optional[float] = None,
        overlap_percent: float = 10,
    ) -> None:
        wellplate_settings = _def.get_wellplate_settings(wellplate_format)
        self.get_selected_well_coordinates(well_name, wellplate_settings)

        if wellplate_format in ["384 well plate", "1536 well plate"]:
            well_shape = "Square"
        else:
            well_shape = "Circle"

        if scan_size_mm is None:
            scan_size_mm = wellplate_settings["well_size_mm"]

        for k, v in self.region_centers.items():
            coords = self.create_region_coordinates(
                v[0], v[1], scan_size_mm, overlap_percent, well_shape
            )
            self.region_fov_coordinates[k] = coords

    def get_selected_well_coordinates(
        self, well_names: str, wellplate_settings: Dict[str, Any]
    ) -> None:
        """
        Given a comma separated list of well names in A1 format, return the coordinates for the wells (wrt the A1 corner)
        """
        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        descriptions = well_names.split(",")

        def row_to_index(row):
            index = 0
            for char in row:
                index = index * 26 + (ord(char.upper()) - ord("A") + 1)
            return index - 1

        def index_to_row(index):
            index += 1
            row = ""
            while index > 0:
                index -= 1
                row = chr(index % 26 + ord("A")) + row
                index //= 26
            return row

        for desc in descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(
                        min(start_row_index, end_row_index),
                        max(start_row_index, end_row_index) + 1,
                    ):
                        cols = range(
                            min(start_col_index, end_col_index),
                            max(start_col_index, end_col_index) + 1,
                        )
                        # Reverse column order for alternating rows if needed
                        if (row - start_row_index) % 2 == 1:
                            cols = reversed(cols)

                        for col in cols:
                            x_mm = (
                                wellplate_settings["a1_x_mm"]
                                + col * wellplate_settings["well_spacing_mm"]
                                + _def.WELLPLATE_OFFSET_X_mm
                            )
                            y_mm = (
                                wellplate_settings["a1_y_mm"]
                                + row * wellplate_settings["well_spacing_mm"]
                                + _def.WELLPLATE_OFFSET_Y_mm
                            )
                            self.region_centers[index_to_row(row) + str(col + 1)] = (
                                x_mm,
                                y_mm,
                            )
                else:
                    x_mm = (
                        wellplate_settings["a1_x_mm"]
                        + start_col_index * wellplate_settings["well_spacing_mm"]
                        + _def.WELLPLATE_OFFSET_X_mm
                    )
                    y_mm = (
                        wellplate_settings["a1_y_mm"]
                        + start_row_index * wellplate_settings["well_spacing_mm"]
                        + _def.WELLPLATE_OFFSET_Y_mm
                    )
                    self.region_centers[start_row + start_col] = (x_mm, y_mm)
            else:
                raise ValueError(
                    f"Invalid well format: {desc}. Expected format is 'A1' or 'A1:B2' for ranges."
                )

    def create_region_coordinates(
        self,
        center_x: float,
        center_y: float,
        scan_size_mm: float,
        overlap_percent: float = 10,
        shape: str = "Square",
    ) -> List[Tuple[float, float]]:
        fov_size_mm = self.camera.get_fov_size_mm()
        # We are not taking software cropping into account here. Need to fix it when we merge this into ScanCoordinates.
        step_size_mm = fov_size_mm * (1 - overlap_percent / 100)

        steps = math.floor(scan_size_mm / step_size_mm)
        if shape == "Circle":
            tile_diagonal = math.sqrt(2) * fov_size_mm
            if steps % 2 == 1:  # for odd steps
                actual_scan_size_mm = (steps - 1) * step_size_mm + tile_diagonal
            else:  # for even steps
                actual_scan_size_mm = math.sqrt(
                    ((steps - 1) * step_size_mm + fov_size_mm) ** 2
                    + (step_size_mm + fov_size_mm) ** 2
                )

            if actual_scan_size_mm > scan_size_mm:
                actual_scan_size_mm -= step_size_mm
                steps -= 1
        else:
            actual_scan_size_mm = (steps - 1) * step_size_mm + fov_size_mm

        steps = max(1, steps)  # Ensure at least one step

        scan_coordinates = []
        half_steps = (steps - 1) / 2
        radius_squared = (scan_size_mm / 2) ** 2
        fov_size_mm_half = fov_size_mm / 2

        def is_in_circle(x, y, center_x, center_y, radius_squared, fov_size_mm_half):
            corners = [
                (x - fov_size_mm_half, y - fov_size_mm_half),
                (x + fov_size_mm_half, y - fov_size_mm_half),
                (x - fov_size_mm_half, y + fov_size_mm_half),
                (x + fov_size_mm_half, y + fov_size_mm_half),
            ]
            return all(
                (cx - center_x) ** 2 + (cy - center_y) ** 2 <= radius_squared
                for cx, cy in corners
            )

        for i in range(steps):
            row = []
            y = center_y + (i - half_steps) * step_size_mm
            for j in range(steps):
                x = center_x + (j - half_steps) * step_size_mm
                if shape == "Square" or (
                    shape == "Circle"
                    and is_in_circle(
                        x, y, center_x, center_y, radius_squared, fov_size_mm_half
                    )
                ):
                    row.append((x, y))

            if _def.FOV_PATTERN == "S-Pattern" and i % 2 == 1:
                row.reverse()
            scan_coordinates.extend(row)

        if not scan_coordinates and shape == "Circle":
            scan_coordinates.append((center_x, center_y))

        return scan_coordinates
