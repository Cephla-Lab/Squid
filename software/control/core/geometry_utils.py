"""
Geometric utility functions for scan calculations.

These functions are pure geometry calculations with no UI dependencies.
"""

import math


def get_effective_well_size(well_size_mm, fov_size_mm, shape, is_round_well=True):
    """Calculate the default scan size for a well based on shape.

    Args:
        well_size_mm: Well diameter (round) or side length (square)
        fov_size_mm: Field of view size in mm
        shape: Scan shape ("Circle", "Square", or "Rectangle")
        is_round_well: True for round wells, False for square wells

    Returns:
        Effective scan size in mm that provides ~100% coverage
    """
    if shape == "Circle":
        return well_size_mm + fov_size_mm * (1 + math.sqrt(2))
    elif shape == "Square" and is_round_well:
        # Inscribed square side length = diameter / sqrt(2)
        return well_size_mm / math.sqrt(2)
    elif shape == "Rectangle" and is_round_well:
        # Rectangle with 0.6 aspect ratio inscribed in circle
        # h = diameter / sqrt(1 + 0.6²) = diameter / sqrt(1.36)
        return well_size_mm / math.sqrt(1.36)
    return well_size_mm


def get_tile_positions(scan_size_mm, fov_size_mm, overlap_percent, shape):
    """Get tile center positions for a scan pattern.

    Args:
        scan_size_mm: Total scan size in mm
        fov_size_mm: Field of view size in mm
        overlap_percent: Overlap between adjacent tiles (%)
        shape: Scan shape ("Circle", "Square", or "Rectangle")

    Returns:
        List of (x, y) tile center positions in mm
    """
    step_size = fov_size_mm * (1 - overlap_percent / 100)
    if step_size <= 0 or scan_size_mm <= 0:
        return [(0, 0)]

    steps = math.floor(scan_size_mm / step_size)

    if shape == "Circle":
        tile_diagonal = math.sqrt(2) * fov_size_mm
        if steps % 2 == 1:
            actual = (steps - 1) * step_size + tile_diagonal
        else:
            actual = math.sqrt(((steps - 1) * step_size + fov_size_mm) ** 2 + (step_size + fov_size_mm) ** 2)
        if actual > scan_size_mm and steps > 1:
            steps -= 1

    steps = max(1, steps)
    half_steps = (steps - 1) / 2
    scan_radius_sq = (scan_size_mm / 2) ** 2
    fov_half = fov_size_mm / 2

    tiles = []
    for i in range(steps):
        y = (i - half_steps) * step_size
        for j in range(steps):
            x = (j - half_steps) * step_size
            if shape == "Circle":
                corners_in = all(
                    (x + dx) ** 2 + (y + dy) ** 2 <= scan_radius_sq
                    for dx, dy in [
                        (-fov_half, -fov_half),
                        (fov_half, -fov_half),
                        (-fov_half, fov_half),
                        (fov_half, fov_half),
                    ]
                )
                if corners_in:
                    tiles.append((x, y))
            else:
                tiles.append((x, y))

    return tiles if tiles else [(0, 0)]


def calculate_well_coverage(scan_size_mm, fov_size_mm, overlap_percent, shape, well_size_mm, is_round_well=True):
    """Calculate what fraction of the well is covered by FOV tiles.

    Uses grid sampling to determine coverage.

    Args:
        scan_size_mm: Total scan size in mm
        fov_size_mm: Field of view size in mm
        overlap_percent: Overlap between adjacent tiles (%)
        shape: Scan shape ("Circle", "Square", or "Rectangle")
        well_size_mm: Well diameter (round) or side length (square)
        is_round_well: True for round wells, False for square wells

    Returns:
        Coverage percentage (0-100)
    """
    step_size = fov_size_mm * (1 - overlap_percent / 100)
    if step_size <= 0 or scan_size_mm <= 0 or well_size_mm <= 0:
        return 0

    tiles = get_tile_positions(scan_size_mm, fov_size_mm, overlap_percent, shape)
    if not tiles:
        return 0

    well_radius = well_size_mm / 2
    fov_half = fov_size_mm / 2

    # Grid sampling to calculate coverage
    resolution = 100
    covered = 0
    total = 0

    for i in range(resolution):
        for j in range(resolution):
            x = -well_radius + (2 * well_radius * i / resolution)
            y = -well_radius + (2 * well_radius * j / resolution)

            # Check if point is inside well
            if is_round_well:
                if x * x + y * y > well_radius * well_radius:
                    continue
            else:
                if abs(x) > well_radius or abs(y) > well_radius:
                    continue

            total += 1

            # Check if covered by any tile
            for tx, ty in tiles:
                if abs(x - tx) <= fov_half and abs(y - ty) <= fov_half:
                    covered += 1
                    break

    return round((covered / total) * 100, 2) if total > 0 else 0
