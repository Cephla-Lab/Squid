"""Pure geometry functions for scan coordinate calculations.

These are stateless, pure functions that can be easily tested independently.
"""

from typing import Tuple

import numpy as np


def point_in_polygon(x: float, y: float, vertices: np.ndarray) -> bool:
    """Check if a point (x, y) is inside a polygon defined by vertices.

    Uses ray casting algorithm for point-in-polygon test.

    Args:
        x: X coordinate of the point
        y: Y coordinate of the point
        vertices: Nx2 numpy array of polygon vertices

    Returns:
        True if point is inside the polygon, False otherwise.
    """
    n = len(vertices)
    if n < 3:
        return False

    inside = False
    p1x, p1y = vertices[0]
    for i in range(n + 1):
        p2x, p2y = vertices[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    xinters = p1x  # Default to p1x
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def point_in_circle(
    x: float, y: float, center_x: float, center_y: float, radius: float
) -> bool:
    """Check if a point is inside a circle.

    Args:
        x: X coordinate of the point
        y: Y coordinate of the point
        center_x: X coordinate of circle center
        center_y: Y coordinate of circle center
        radius: Radius of the circle

    Returns:
        True if point is inside or on the circle, False otherwise.
    """
    return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2


def fov_corners_in_circle(
    fov_x: float,
    fov_y: float,
    fov_width: float,
    fov_height: float,
    center_x: float,
    center_y: float,
    radius: float,
) -> bool:
    """Check if all four corners of a FOV rectangle are inside a circle.

    Used to determine if a FOV tile should be included in a circular scan region.

    Args:
        fov_x: X coordinate of FOV center
        fov_y: Y coordinate of FOV center
        fov_width: Width of the FOV
        fov_height: Height of the FOV
        center_x: X coordinate of circle center
        center_y: Y coordinate of circle center
        radius: Radius of the circle

    Returns:
        True if all four corners are inside the circle, False otherwise.
    """
    half_width = fov_width / 2
    half_height = fov_height / 2

    corners = [
        (fov_x - half_width, fov_y - half_height),
        (fov_x + half_width, fov_y - half_height),
        (fov_x - half_width, fov_y + half_height),
        (fov_x + half_width, fov_y + half_height),
    ]

    radius_squared = radius**2
    return all(
        (cx - center_x) ** 2 + (cy - center_y) ** 2 <= radius_squared
        for cx, cy in corners
    )


def fov_overlaps_polygon(
    fov_x: float,
    fov_y: float,
    fov_width: float,
    fov_height: float,
    vertices: np.ndarray,
) -> bool:
    """Check if a FOV rectangle overlaps with a polygon.

    Returns True if the FOV center or any of its corners is inside the polygon.

    Args:
        fov_x: X coordinate of FOV center
        fov_y: Y coordinate of FOV center
        fov_width: Width of the FOV
        fov_height: Height of the FOV
        vertices: Nx2 numpy array of polygon vertices

    Returns:
        True if FOV overlaps with the polygon, False otherwise.
    """
    # Check if center is in polygon
    if point_in_polygon(fov_x, fov_y, vertices):
        return True

    # Check if any corner is in polygon
    half_width = fov_width / 2
    half_height = fov_height / 2

    corners = [
        (fov_x - half_width, fov_y - half_height),
        (fov_x + half_width, fov_y - half_height),
        (fov_x - half_width, fov_y + half_height),
        (fov_x + half_width, fov_y + half_height),
    ]

    return any(point_in_polygon(cx, cy, vertices) for cx, cy in corners)


def bounding_box(vertices: np.ndarray) -> Tuple[float, float, float, float]:
    """Calculate bounding box of a set of vertices.

    Args:
        vertices: Nx2 numpy array of vertices

    Returns:
        Tuple of (x_min, y_min, x_max, y_max)
    """
    if len(vertices) == 0:
        return (0.0, 0.0, 0.0, 0.0)

    x_min = float(np.min(vertices[:, 0]))
    y_min = float(np.min(vertices[:, 1]))
    x_max = float(np.max(vertices[:, 0]))
    y_max = float(np.max(vertices[:, 1]))

    return (x_min, y_min, x_max, y_max)
