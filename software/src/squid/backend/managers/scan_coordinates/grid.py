"""Pure grid generation functions for scan coordinates.

These are stateless, pure functions that generate FOV grids for scanning.
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from squid.backend.managers.scan_coordinates.geometry import (
    fov_corners_in_circle,
    fov_overlaps_polygon,
    bounding_box,
)


@dataclass
class GridConfig:
    """Configuration for grid generation."""

    fov_width_mm: float
    fov_height_mm: float
    overlap_percent: float = 10.0
    fov_pattern: str = "S-Pattern"  # "S-Pattern" or "Raster"

    @property
    def step_x_mm(self) -> float:
        """Calculate X step size based on FOV width and overlap."""
        return self.fov_width_mm * (1 - self.overlap_percent / 100)

    @property
    def step_y_mm(self) -> float:
        """Calculate Y step size based on FOV height and overlap."""
        return self.fov_height_mm * (1 - self.overlap_percent / 100)


def generate_rectangular_grid(
    center_x: float,
    center_y: float,
    width_mm: float,
    height_mm: float,
    config: GridConfig,
) -> List[Tuple[float, float]]:
    """Generate a rectangular grid of FOV positions.

    Args:
        center_x: X coordinate of grid center
        center_y: Y coordinate of grid center
        width_mm: Width of the scan area
        height_mm: Height of the scan area
        config: Grid configuration

    Returns:
        List of (x, y) tuples for FOV center positions.
    """
    step_x = config.step_x_mm
    step_y = config.step_y_mm

    if step_x <= 0 or step_y <= 0:
        return [(center_x, center_y)]

    # Calculate number of tiles to cover the scan area
    # n tiles cover: (n-1) * step + fov >= scan_size
    # n = ceil((scan_size - fov) / step) + 1
    tiles_x = max(1, math.ceil((width_mm - config.fov_width_mm) / step_x) + 1)
    tiles_y = max(1, math.ceil((height_mm - config.fov_height_mm) / step_y) + 1)

    half_tiles_x = (tiles_x - 1) / 2
    half_tiles_y = (tiles_y - 1) / 2

    coords: List[Tuple[float, float]] = []
    for i in range(tiles_y):
        row: List[Tuple[float, float]] = []
        y = center_y + (i - half_tiles_y) * step_y
        for j in range(tiles_x):
            x = center_x + (j - half_tiles_x) * step_x
            row.append((x, y))

        if config.fov_pattern == "S-Pattern" and i % 2 == 1:
            row.reverse()
        coords.extend(row)

    return coords


def generate_square_grid(
    center_x: float,
    center_y: float,
    scan_size_mm: float,
    config: GridConfig,
) -> List[Tuple[float, float]]:
    """Generate a square grid of FOV positions.

    Args:
        center_x: X coordinate of grid center
        center_y: Y coordinate of grid center
        scan_size_mm: Size of the square scan area
        config: Grid configuration

    Returns:
        List of (x, y) tuples for FOV center positions.
    """
    return generate_rectangular_grid(
        center_x, center_y, scan_size_mm, scan_size_mm, config
    )


def generate_circular_grid(
    center_x: float,
    center_y: float,
    diameter_mm: float,
    config: GridConfig,
) -> List[Tuple[float, float]]:
    """Generate a grid of FOV positions within a circular region.

    Only includes FOVs where all four corners are inside the circle.

    Args:
        center_x: X coordinate of circle center
        center_y: Y coordinate of circle center
        diameter_mm: Diameter of the circular region
        config: Grid configuration

    Returns:
        List of (x, y) tuples for FOV center positions.
    """
    step_x = config.step_x_mm
    step_y = config.step_y_mm
    radius = diameter_mm / 2

    if step_x <= 0 or step_y <= 0:
        return [(center_x, center_y)]

    # Calculate tiles needed to potentially cover the circle
    tiles_x = max(1, math.ceil((diameter_mm - config.fov_width_mm) / step_x) + 1)
    tiles_y = max(1, math.ceil((diameter_mm - config.fov_height_mm) / step_y) + 1)

    half_tiles_x = (tiles_x - 1) / 2
    half_tiles_y = (tiles_y - 1) / 2

    # Use the larger FOV dimension for circle boundary checking
    fov_max = max(config.fov_width_mm, config.fov_height_mm)

    coords: List[Tuple[float, float]] = []
    for i in range(tiles_y):
        row: List[Tuple[float, float]] = []
        y = center_y + (i - half_tiles_y) * step_y
        for j in range(tiles_x):
            x = center_x + (j - half_tiles_x) * step_x
            # Check if all FOV corners are inside the circle
            if fov_corners_in_circle(x, y, fov_max, fov_max, center_x, center_y, radius):
                row.append((x, y))

        if config.fov_pattern == "S-Pattern" and i % 2 == 1:
            row.reverse()
        coords.extend(row)

    # Ensure at least the center position if nothing else fits
    if not coords:
        coords.append((center_x, center_y))

    return coords


def generate_polygon_grid(
    vertices: np.ndarray,
    config: GridConfig,
) -> List[Tuple[float, float]]:
    """Generate a grid of FOV positions within a polygon region.

    Includes FOVs where the center or any corner is inside the polygon.

    Args:
        vertices: Nx2 numpy array of polygon vertices
        config: Grid configuration

    Returns:
        List of (x, y) tuples for FOV center positions.
    """
    if len(vertices) < 3:
        return []

    step_x = config.step_x_mm
    step_y = config.step_y_mm

    if step_x <= 0 or step_y <= 0:
        # Return centroid
        centroid_x = float(np.mean(vertices[:, 0]))
        centroid_y = float(np.mean(vertices[:, 1]))
        return [(centroid_x, centroid_y)]

    # Get bounding box
    x_min, y_min, x_max, y_max = bounding_box(vertices)

    # Create a grid of points within the bounding box
    x_range = np.arange(x_min, x_max + step_x, step_x)
    y_range = np.arange(y_min, y_max + step_y, step_y)

    valid_points: List[Tuple[float, float]] = []
    for y in y_range:
        row: List[Tuple[float, float]] = []
        for x in x_range:
            if fov_overlaps_polygon(x, y, config.fov_width_mm, config.fov_height_mm, vertices):
                row.append((float(x), float(y)))

        # Sort by x for consistent ordering
        row.sort(key=lambda p: p[0])
        valid_points.extend(row)

    # Apply S-pattern if needed
    if config.fov_pattern == "S-Pattern" and valid_points:
        valid_points = apply_s_pattern(valid_points)

    return valid_points


def generate_grid_by_count(
    center_x: float,
    center_y: float,
    center_z: Optional[float],
    nx: int,
    ny: int,
    config: GridConfig,
) -> List[Tuple[float, ...]]:
    """Generate a grid with specified number of tiles in X and Y.

    Args:
        center_x: X coordinate of grid center
        center_y: Y coordinate of grid center
        center_z: Z coordinate (included in output if provided)
        nx: Number of tiles in X direction
        ny: Number of tiles in Y direction
        config: Grid configuration

    Returns:
        List of (x, y) or (x, y, z) tuples for FOV center positions.
    """
    step_x = config.step_x_mm
    step_y = config.step_y_mm

    # Calculate total grid size
    grid_width = (nx - 1) * step_x
    grid_height = (ny - 1) * step_y

    coords: List[Tuple[float, ...]] = []
    for i in range(ny):
        row: List[Tuple[float, ...]] = []
        y = center_y - grid_height / 2 + i * step_y
        for j in range(nx):
            x = center_x - grid_width / 2 + j * step_x
            if center_z is not None:
                row.append((x, y, center_z))
            else:
                row.append((x, y))

        if config.fov_pattern == "S-Pattern" and i % 2 == 1:
            row.reverse()
        coords.extend(row)

    return coords


def generate_grid_by_step_size(
    center_x: float,
    center_y: float,
    center_z: Optional[float],
    nx: int,
    ny: int,
    dx: float,
    dy: float,
    fov_pattern: str = "S-Pattern",
) -> List[Tuple[float, ...]]:
    """Generate a grid with specified step sizes.

    Args:
        center_x: X coordinate of grid center
        center_y: Y coordinate of grid center
        center_z: Z coordinate (included in output if provided)
        nx: Number of tiles in X direction
        ny: Number of tiles in Y direction
        dx: Step size in X direction
        dy: Step size in Y direction
        fov_pattern: "S-Pattern" or "Raster"

    Returns:
        List of (x, y) or (x, y, z) tuples for FOV center positions.
    """
    grid_width = (nx - 1) * dx
    grid_height = (ny - 1) * dy

    x_steps = [center_x - grid_width / 2 + j * dx for j in range(nx)]
    y_steps = [center_y - grid_height / 2 + i * dy for i in range(ny)]

    coords: List[Tuple[float, ...]] = []
    for i, y in enumerate(y_steps):
        row: List[Tuple[float, ...]] = []
        x_range = x_steps if i % 2 == 0 or fov_pattern != "S-Pattern" else reversed(x_steps)
        for x in x_range:
            if center_z is not None:
                row.append((x, y, center_z))
            else:
                row.append((x, y))
        coords.extend(row)

    return coords


def apply_s_pattern(
    coords: List[Tuple[float, float]], row_axis: int = 1
) -> List[Tuple[float, float]]:
    """Apply S-pattern to a list of coordinates.

    Reverses every other row to create a serpentine/snake pattern.

    Args:
        coords: List of (x, y) tuples
        row_axis: Which axis defines rows (0=x, 1=y)

    Returns:
        List of (x, y) tuples with S-pattern applied.
    """
    if len(coords) <= 1:
        return coords

    # Convert to numpy for easier manipulation
    arr = np.array(coords)

    # Find unique values on row axis and sort
    unique_rows = np.unique(arr[:, row_axis])

    result: List[Tuple[float, float]] = []
    for i, row_val in enumerate(unique_rows):
        mask = arr[:, row_axis] == row_val
        row_coords = arr[mask]
        # Sort by the other axis
        sort_axis = 1 - row_axis
        sorted_indices = np.argsort(row_coords[:, sort_axis])
        row_sorted = row_coords[sorted_indices]

        if i % 2 == 1:
            row_sorted = row_sorted[::-1]

        for coord in row_sorted:
            result.append((float(coord[0]), float(coord[1])))

    return result


def filter_coordinates_in_bounds(
    coords: List[Tuple[float, ...]],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> List[Tuple[float, ...]]:
    """Filter coordinates to only include those within specified bounds.

    Args:
        coords: List of coordinate tuples (x, y) or (x, y, z)
        x_min: Minimum X value
        x_max: Maximum X value
        y_min: Minimum Y value
        y_max: Maximum Y value

    Returns:
        Filtered list of coordinate tuples.
    """
    return [
        coord for coord in coords
        if x_min <= coord[0] <= x_max and y_min <= coord[1] <= y_max
    ]
