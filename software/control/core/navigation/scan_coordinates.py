from dataclasses import dataclass
import itertools
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

import control._def
from control.core.navigation.objective_store import ObjectiveStore
from squid.abc import AbstractStage, AbstractCamera
import squid.logging


@dataclass
class ScanCoordinatesUpdate:
    pass


@dataclass
class FovCenter:
    x_mm: float
    y_mm: float

    @staticmethod
    def from_scan_coordinates(
        scan_coordinates: List[Tuple[float, float]],
    ) -> List["FovCenter"]:
        return [FovCenter(x_mm=sc[0], y_mm=sc[1]) for sc in scan_coordinates]


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
    def __init__(
        self,
        objectiveStore: ObjectiveStore,
        stage: AbstractStage,
        camera: AbstractCamera,
        update_callback: Optional[Callable[[ScanCoordinatesUpdate], None]] = None,
    ) -> None:
        self._log = squid.logging.get_logger(self.__class__.__name__)
        # Wellplate settings
        self.objectiveStore: ObjectiveStore = objectiveStore
        self.stage: AbstractStage = stage
        self.camera: AbstractCamera = camera
        self._update_callback: Callable[[ScanCoordinatesUpdate], None] = (
            update_callback if update_callback else lambda update: None
        )
        self.well_selector: Optional[Any] = None
        self.acquisition_pattern: str = control._def.ACQUISITION_PATTERN
        self.fov_pattern: str = control._def.FOV_PATTERN
        self.format: str = control._def.WELLPLATE_FORMAT
        self.a1_x_mm: float = control._def.A1_X_MM
        self.a1_y_mm: float = control._def.A1_Y_MM
        self.wellplate_offset_x_mm: float = control._def.WELLPLATE_OFFSET_X_mm
        self.wellplate_offset_y_mm: float = control._def.WELLPLATE_OFFSET_Y_mm
        self.well_spacing_mm: float = control._def.WELL_SPACING_MM
        self.well_size_mm: float = control._def.WELL_SIZE_MM
        self.a1_x_pixel: Optional[float] = None
        self.a1_y_pixel: Optional[float] = None
        self.number_of_skip: Optional[int] = None

        # Centralized region management
        self.region_centers: Dict[str, List[float]] = {}  # {region_id: [x, y, z]}
        self.region_shapes: Dict[str, str] = {}  # {region_id: "Square"}
        self.region_fov_coordinates: Dict[
            str, List[Tuple[float, ...]]
        ] = {}  # {region_id: [(x,y,z), ...]}

    def add_well_selector(self, well_selector: Any) -> None:
        self.well_selector = well_selector

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

    def _index_to_row(self, index: int) -> str:
        index += 1
        row = ""
        while index > 0:
            index -= 1
            row = chr(index % 26 + ord("A")) + row
            index //= 26
        return row

    def get_selected_wells(self) -> Optional[Dict[str, Tuple[float, float]]]:
        # get selected wells from the widget
        self._log.info("getting selected wells for acquisition")
        if not self.well_selector or self.format == "glass slide":
            return None

        selected_wells = np.array(self.well_selector.get_selected_cells())
        well_centers = {}

        # if no well selected
        if len(selected_wells) == 0:
            return well_centers
        # populate the coordinates
        rows = np.unique(selected_wells[:, 0])
        _increasing = True
        for row in rows:
            items = selected_wells[selected_wells[:, 0] == row]
            columns = items[:, 1]
            columns = np.sort(columns)
            if not _increasing:
                columns = np.flip(columns)
            for column in columns:
                x_mm = (
                    self.a1_x_mm
                    + (column * self.well_spacing_mm)
                    + self.wellplate_offset_x_mm
                )
                y_mm = (
                    self.a1_y_mm
                    + (row * self.well_spacing_mm)
                    + self.wellplate_offset_y_mm
                )
                well_id = self._index_to_row(row) + str(column + 1)
                well_centers[well_id] = (x_mm, y_mm)
            _increasing = not _increasing
        return well_centers

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
        new_region_centers = self.get_selected_wells()

        if self.format == "glass slide":
            pos = self.stage.get_pos()
            self.set_live_scan_coordinates(
                pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape
            )

        elif bool(new_region_centers):
            # Remove regions that are no longer selected
            for well_id in list(self.region_centers.keys()):
                if well_id not in new_region_centers.keys():
                    self.remove_region(well_id)

            # Add regions for selected wells
            for well_id, (x, y) in new_region_centers.items():
                if well_id not in self.region_centers:
                    self.add_region(well_id, x, y, scan_size_mm, overlap_percent, shape)
        else:
            self.clear_regions()

    def set_manual_coordinates(
        self, manual_shapes: Optional[List[np.ndarray]], overlap_percent: float
    ) -> None:
        self.clear_regions()
        if manual_shapes is not None:
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
                    self._update_callback(
                        AddScanCoordinateRegion(
                            fov_centers=FovCenter.from_scan_coordinates(
                                scan_coordinates
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
        self._log.info(
            f"add_region: scan_size={scan_size_mm:.3f}mm, overlap={overlap_percent}%, "
            f"pixel_size_factor={pixel_size_factor:.4f}, "
            f"raw_camera_FOV={raw_fov_width}x{raw_fov_height}mm, "
            f"effective_FOV={fov_width_mm:.3f}x{fov_height_mm:.3f}mm, "
            f"step={step_x_mm:.3f}x{step_y_mm:.3f}mm"
        )

        scan_coordinates = []

        if shape == "Rectangle":
            # Use scan_size_mm as height, width is 0.6 * height
            height_mm = scan_size_mm
            width_mm = scan_size_mm * 0.6

            # Calculate number of tiles to cover the scan area
            # n tiles cover: (n-1) * step + fov >= scan_size
            # n = ceil((scan_size - fov) / step) + 1
            tiles_x = max(1, math.ceil((width_mm - fov_width_mm) / step_x_mm) + 1) if step_x_mm > 0 else 1
            tiles_y = max(1, math.ceil((height_mm - fov_height_mm) / step_y_mm) + 1) if step_y_mm > 0 else 1

            # Calculate actual coverage
            actual_width = (tiles_x - 1) * step_x_mm + fov_width_mm
            actual_height = (tiles_y - 1) * step_y_mm + fov_height_mm

            self._log.info(
                f"Rectangle: {tiles_x}x{tiles_y} tiles, "
                f"actual coverage={actual_width:.3f}x{actual_height:.3f}mm"
            )

            half_tiles_x = (tiles_x - 1) / 2
            half_tiles_y = (tiles_y - 1) / 2

            for i in range(tiles_y):
                row = []
                y = center_y + (i - half_tiles_y) * step_y_mm
                for j in range(tiles_x):
                    x = center_x + (j - half_tiles_x) * step_x_mm
                    if self.validate_coordinates(x, y):
                        row.append((x, y))
                if self.fov_pattern == "S-Pattern" and i % 2 == 1:
                    row.reverse()
                scan_coordinates.extend(row)
        else:
            # For Square and Circle shapes
            # Calculate number of tiles to cover the scan area in each dimension
            # n tiles cover: (n-1) * step + fov >= scan_size
            # n = ceil((scan_size - fov) / step) + 1
            tiles_x = max(1, math.ceil((scan_size_mm - fov_width_mm) / step_x_mm) + 1) if step_x_mm > 0 else 1
            tiles_y = max(1, math.ceil((scan_size_mm - fov_height_mm) / step_y_mm) + 1) if step_y_mm > 0 else 1

            # Calculate actual coverage
            actual_width = (tiles_x - 1) * step_x_mm + fov_width_mm
            actual_height = (tiles_y - 1) * step_y_mm + fov_height_mm

            if shape == "Circle":
                # For circles, we need to ensure tiles fit within the circular area
                # Use the larger of the two tile counts to ensure coverage
                # but filter out tiles outside the circle
                pass  # The circle filtering happens in the loop below

            self._log.info(
                f"{shape}: {tiles_x}x{tiles_y} tiles, "
                f"actual coverage={actual_width:.3f}x{actual_height:.3f}mm"
            )

            half_tiles_x = (tiles_x - 1) / 2
            half_tiles_y = (tiles_y - 1) / 2
            radius_squared = (scan_size_mm / 2) ** 2
            # Use the larger FOV dimension for circle boundary checking
            fov_size_mm_half = max(fov_width_mm, fov_height_mm) / 2

            for i in range(tiles_y):
                row = []
                y = center_y + (i - half_tiles_y) * step_y_mm
                for j in range(tiles_x):
                    x = center_x + (j - half_tiles_x) * step_x_mm
                    if (
                        shape == "Square"
                        or (
                            shape == "Circle"
                            and self._is_in_circle(
                                x,
                                y,
                                center_x,
                                center_y,
                                radius_squared,
                                fov_size_mm_half,
                            )
                        )
                    ):
                        if self.validate_coordinates(x, y):
                            row.append((x, y))

                if self.fov_pattern == "S-Pattern" and i % 2 == 1:
                    row.reverse()
                scan_coordinates.extend(row)

        if not scan_coordinates and shape == "Circle":
            if self.validate_coordinates(center_x, center_y):
                scan_coordinates.append((center_x, center_y))

        self.region_shapes[well_id] = shape
        self.region_centers[well_id] = [
            float(center_x),
            float(center_y),
            float(self.stage.get_pos().z_mm),
        ]
        self.region_fov_coordinates[well_id] = scan_coordinates
        self._update_callback(
            AddScanCoordinateRegion(
                fov_centers=FovCenter.from_scan_coordinates(scan_coordinates)
            )
        )
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
            self._update_callback(
                RemovedScanCoordinateRegion(fov_centers=removed_fov_centers)
            )

    def clear_regions(self) -> None:
        self.region_centers.clear()
        self.region_shapes.clear()
        self.region_fov_coordinates.clear()
        self._update_callback(ClearedScanCoordinates())
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

        step_x_mm = fov_width_mm * (1 - overlap_percent / 100)
        step_y_mm = fov_height_mm * (1 - overlap_percent / 100)

        # Calculate total grid size
        grid_width_mm = (Nx - 1) * step_x_mm
        grid_height_mm = (Ny - 1) * step_y_mm

        scan_coordinates = []
        for i in range(Ny):
            row = []
            y = center_y - grid_height_mm / 2 + i * step_y_mm
            for j in range(Nx):
                x = center_x - grid_width_mm / 2 + j * step_x_mm
                if self.validate_coordinates(x, y):
                    row.append((x, y, center_z))

            if self.fov_pattern == "S-Pattern" and i % 2 == 1:  # reverse even rows
                row.reverse()
            scan_coordinates.extend(row)

        # Region coordinates are already centered since center_x, center_y is grid center
        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            self._update_callback(
                AddScanCoordinateRegion(
                    fov_centers=FovCenter.from_scan_coordinates(scan_coordinates)
                )
            )
        else:
            self._log.info(f"Region Out of Bounds: {region_id}")

    def add_single_fov_region(
        self, region_id: str, center_x: float, center_y: float, center_z: float
    ) -> None:
        # Clamp to software limits to avoid errors in simulation or user input
        x_min = control._def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        x_max = control._def.SOFTWARE_POS_LIMIT.X_POSITIVE
        y_min = control._def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
        y_max = control._def.SOFTWARE_POS_LIMIT.Y_POSITIVE

        clamped_x = min(max(center_x, x_min), x_max)
        clamped_y = min(max(center_y, y_min), y_max)
        if clamped_x != center_x or clamped_y != center_y:
            self._log.warning(
                f"FOV center ({center_x},{center_y}) clamped to ({clamped_x},{clamped_y}) due to limits."
            )

        self.region_centers[region_id] = [clamped_x, clamped_y, center_z]
        self.region_fov_coordinates[region_id] = [(clamped_x, clamped_y)]
        self._update_callback(
            AddScanCoordinateRegion(
                fov_centers=[FovCenter(x_mm=clamped_x, y_mm=clamped_y)]
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
        grid_width_mm = (Nx - 1) * dx
        grid_height_mm = (Ny - 1) * dy

        # Pre-calculate step sizes and ranges
        x_steps = [center_x - grid_width_mm / 2 + j * dx for j in range(Nx)]
        y_steps = [center_y - grid_height_mm / 2 + i * dy for i in range(Ny)]

        scan_coordinates = []
        for i, y in enumerate(y_steps):
            row = []
            x_range = x_steps if i % 2 == 0 else reversed(x_steps)
            for x in x_range:
                if self.validate_coordinates(x, y):
                    row.append((x, y))
            scan_coordinates.extend(row)

        if scan_coordinates:  # Only add region if there are valid coordinates
            self._log.info(f"Added Flexible Region: {region_id}")
            self.region_centers[region_id] = [center_x, center_y, center_z]
            self.region_fov_coordinates[region_id] = scan_coordinates
            self._update_callback(
                AddScanCoordinateRegion(
                    fov_centers=FovCenter.from_scan_coordinates(scan_coordinates)
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

        step_x_mm = fov_width_mm * (1 - overlap_percent / 100)
        step_y_mm = fov_height_mm * (1 - overlap_percent / 100)

        # Ensure shape_coords is a numpy array
        shape_coords = np.array(shape_coords)
        if shape_coords.ndim == 1:
            shape_coords = shape_coords.reshape(-1, 2)
        elif shape_coords.ndim > 2:
            self._log.error(f"Unexpected shape of manual_shape: {shape_coords.shape}")
            return []

        # Calculate bounding box
        x_min, y_min = np.min(shape_coords, axis=0)
        x_max, y_max = np.max(shape_coords, axis=0)

        # Create a grid of points within the bounding box
        x_range = np.arange(x_min, x_max + step_x_mm, step_x_mm)
        y_range = np.arange(y_min, y_max + step_y_mm, step_y_mm)
        xx, yy = np.meshgrid(x_range, y_range)
        grid_points = np.column_stack((xx.ravel(), yy.ravel()))

        # # Use Delaunay triangulation for efficient point-in-polygon test
        # # hull = Delaunay(shape_coords)
        # # mask = hull.find_simplex(grid_points) >= 0
        # # or
        # # Use Ray Casting for point-in-polygon test
        # mask = np.array([self._is_in_polygon(x, y, shape_coords) for x, y in grid_points])

        # # Filter points inside the polygon
        # valid_points = grid_points[mask]

        def corners(x_mm, y_mm, fov_w, fov_h):
            half_w = fov_w / 2
            half_h = fov_h / 2
            return (
                (x_mm + half_w, y_mm + half_h),
                (x_mm - half_w, y_mm + half_h),
                (x_mm - half_w, y_mm - half_h),
                (x_mm + half_w, y_mm - half_h),
            )

        valid_points = []
        for x_center, y_center in grid_points:
            if not self.validate_coordinates(x_center, y_center):
                self._log.debug(
                    f"Manual coords: ignoring {x_center=},{y_center=} because it is outside our movement range."
                )
                continue
            if not self._is_in_polygon(x_center, y_center, shape_coords) and not any(
                [
                    self._is_in_polygon(x_corner, y_corner, shape_coords)
                    for (x_corner, y_corner) in corners(x_center, y_center, fov_width_mm, fov_height_mm)
                ]
            ):
                self._log.debug(
                    f"Manual coords: ignoring {x_center=},{y_center=} because no corners or center are in poly. (corners={corners(x_center, y_center, fov_width_mm, fov_height_mm)}"
                )
                continue

            valid_points.append((x_center, y_center))
        if not valid_points:
            return []
        valid_points = np.array(valid_points)

        # Sort points
        sorted_indices = np.lexsort((valid_points[:, 0], valid_points[:, 1]))
        sorted_points = valid_points[sorted_indices]

        # Apply S-Pattern if needed
        if self.fov_pattern == "S-Pattern":
            unique_y = np.unique(sorted_points[:, 1])
            for i in range(1, len(unique_y), 2):
                mask = sorted_points[:, 1] == unique_y[i]
                sorted_points[mask] = sorted_points[mask][::-1]

        return sorted_points.tolist()

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
        self._update_callback(
            AddScanCoordinateRegion(
                fov_centers=FovCenter.from_scan_coordinates(scan_coordinates)
            )
        )

    def region_contains_coordinate(self, region_id: str, x: float, y: float) -> bool:
        # TODO: check for manual region
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

        # For circle regions
        if shape == "Circle":
            center_x = (bounds["max_x"] + bounds["min_x"]) / 2
            center_y = (bounds["max_y"] + bounds["min_y"]) / 2
            radius = (bounds["max_x"] - bounds["min_x"]) / 2
            if (x - center_x) ** 2 + (y - center_y) ** 2 > radius**2:
                return False

        return True

    def _is_in_polygon(self, x: float, y: float, poly: np.ndarray) -> bool:
        n = len(poly)
        inside = False
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def _is_in_circle(
        self,
        x: float,
        y: float,
        center_x: float,
        center_y: float,
        radius_squared: float,
        fov_size_mm_half: float,
    ) -> bool:
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
            control._def.SOFTWARE_POS_LIMIT.X_NEGATIVE
            <= x
            <= control._def.SOFTWARE_POS_LIMIT.X_POSITIVE
            and control._def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
            <= y
            <= control._def.SOFTWARE_POS_LIMIT.Y_POSITIVE
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
        update_callback: Callable[[ScanCoordinatesUpdate], None],
    ) -> None:
        super().__init__(
            objectiveStore=objectiveStore,
            stage=stage,
            camera=camera,
            update_callback=update_callback,
        )

    def get_scan_coordinates_from_selected_wells(
        self,
        wellplate_format: str,
        well_name: str,
        scan_size_mm: Optional[float] = None,
        overlap_percent: float = 10,
    ) -> None:
        wellplate_settings = control._def.get_wellplate_settings(wellplate_format)
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
                                + control._def.WELLPLATE_OFFSET_X_mm
                            )
                            y_mm = (
                                wellplate_settings["a1_y_mm"]
                                + row * wellplate_settings["well_spacing_mm"]
                                + control._def.WELLPLATE_OFFSET_Y_mm
                            )
                            self.region_centers[index_to_row(row) + str(col + 1)] = (
                                x_mm,
                                y_mm,
                            )
                else:
                    x_mm = (
                        wellplate_settings["a1_x_mm"]
                        + start_col_index * wellplate_settings["well_spacing_mm"]
                        + control._def.WELLPLATE_OFFSET_X_mm
                    )
                    y_mm = (
                        wellplate_settings["a1_y_mm"]
                        + start_row_index * wellplate_settings["well_spacing_mm"]
                        + control._def.WELLPLATE_OFFSET_Y_mm
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

            if control._def.FOV_PATTERN == "S-Pattern" and i % 2 == 1:
                row.reverse()
            scan_coordinates.extend(row)

        if not scan_coordinates and shape == "Circle":
            scan_coordinates.append((center_x, center_y))

        return scan_coordinates
