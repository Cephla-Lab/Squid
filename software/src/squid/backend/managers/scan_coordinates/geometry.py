"""Pure geometry functions for scan coordinate calculations.

These are stateless, pure functions that can be easily tested independently.
"""

from typing import Tuple

import numpy as np


def _point_on_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float, eps: float = 1e-9
) -> bool:
    """Return True when point P lies on segment AB (with tolerance)."""
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    return (
        min(x1, x2) - eps <= px <= max(x1, x2) + eps
        and min(y1, y2) - eps <= py <= max(y1, y2) + eps
    )


def _segment_orientation(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float, eps: float = 1e-9
) -> int:
    """Return orientation of triplet (a, b, c): 1 ccw, -1 cw, 0 collinear."""
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(cross) <= eps:
        return 0
    return 1 if cross > 0 else -1


def _segments_intersect(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    q1: Tuple[float, float],
    q2: Tuple[float, float],
    eps: float = 1e-9,
) -> bool:
    """Return True if two 2D segments intersect (including touching)."""
    p1x, p1y = p1
    p2x, p2y = p2
    q1x, q1y = q1
    q2x, q2y = q2

    o1 = _segment_orientation(p1x, p1y, p2x, p2y, q1x, q1y, eps)
    o2 = _segment_orientation(p1x, p1y, p2x, p2y, q2x, q2y, eps)
    o3 = _segment_orientation(q1x, q1y, q2x, q2y, p1x, p1y, eps)
    o4 = _segment_orientation(q1x, q1y, q2x, q2y, p2x, p2y, eps)

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and _point_on_segment(q1x, q1y, p1x, p1y, p2x, p2y, eps):
        return True
    if o2 == 0 and _point_on_segment(q2x, q2y, p1x, p1y, p2x, p2y, eps):
        return True
    if o3 == 0 and _point_on_segment(p1x, p1y, q1x, q1y, q2x, q2y, eps):
        return True
    if o4 == 0 and _point_on_segment(p2x, p2y, q1x, q1y, q2x, q2y, eps):
        return True

    return False


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

    # Treat points on polygon boundary as inside for scan inclusion logic.
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True

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
    if len(vertices) < 3:
        return False

    # Check if center is in polygon.
    if point_in_polygon(fov_x, fov_y, vertices):
        return True

    # Check if any corner is in polygon.
    half_width = fov_width / 2
    half_height = fov_height / 2

    corners = [
        (fov_x - half_width, fov_y - half_height),
        (fov_x + half_width, fov_y - half_height),
        (fov_x - half_width, fov_y + half_height),
        (fov_x + half_width, fov_y + half_height),
    ]
    if any(point_in_polygon(cx, cy, vertices) for cx, cy in corners):
        return True

    # Check if any polygon vertex is inside the FOV rectangle.
    x_min = fov_x - half_width
    x_max = fov_x + half_width
    y_min = fov_y - half_height
    y_max = fov_y + half_height
    for vx, vy in vertices:
        if x_min <= vx <= x_max and y_min <= vy <= y_max:
            return True

    # Check edge intersections between rectangle and polygon.
    rect_edges = [
        (corners[0], corners[1]),
        (corners[1], corners[3]),
        (corners[3], corners[2]),
        (corners[2], corners[0]),
    ]
    n = len(vertices)
    for i in range(n):
        poly_edge = (tuple(vertices[i]), tuple(vertices[(i + 1) % n]))
        if any(_segments_intersect(poly_edge[0], poly_edge[1], r0, r1) for r0, r1 in rect_edges):
            return True

    return False


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
