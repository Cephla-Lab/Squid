"""Tests for the geometry module."""

import numpy as np
import pytest

from squid.backend.managers.scan_coordinates.geometry import (
    bounding_box,
    fov_corners_in_circle,
    fov_overlaps_polygon,
    point_in_circle,
    point_in_polygon,
)


class TestPointInPolygon:
    """Tests for point_in_polygon function."""

    def test_point_inside_triangle(self):
        """Point inside a triangle returns True."""
        triangle = np.array([[0, 0], [4, 0], [2, 3]])
        assert point_in_polygon(2, 1, triangle) is True

    def test_point_outside_triangle(self):
        """Point outside a triangle returns False."""
        triangle = np.array([[0, 0], [4, 0], [2, 3]])
        assert point_in_polygon(5, 5, triangle) is False

    def test_point_inside_square(self):
        """Point inside a square returns True."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        assert point_in_polygon(2, 2, square) is True

    def test_point_outside_square(self):
        """Point outside a square returns False."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        assert point_in_polygon(5, 2, square) is False

    def test_point_inside_concave_polygon(self):
        """Point inside a concave L-shaped polygon returns True."""
        # L-shaped polygon
        l_shape = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]])
        assert point_in_polygon(0.5, 0.5, l_shape) is True
        assert point_in_polygon(0.5, 1.5, l_shape) is True

    def test_point_outside_concave_polygon(self):
        """Point in concave region of L-shaped polygon returns False."""
        l_shape = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]])
        # Point in the "cutout" of the L
        assert point_in_polygon(1.5, 1.5, l_shape) is False

    def test_point_on_edge(self):
        """Point on polygon edge may return True or False (implementation defined)."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        # Edge cases are algorithm-dependent, just verify no error
        result = point_in_polygon(2, 0, square)
        assert isinstance(result, bool)

    def test_empty_polygon(self):
        """Empty polygon returns False."""
        empty = np.array([]).reshape(0, 2)
        assert point_in_polygon(0, 0, empty) is False

    def test_two_point_polygon(self):
        """Polygon with less than 3 points returns False."""
        line = np.array([[0, 0], [1, 1]])
        assert point_in_polygon(0.5, 0.5, line) is False


class TestPointInCircle:
    """Tests for point_in_circle function."""

    def test_point_inside_circle(self):
        """Point inside circle returns True."""
        assert point_in_circle(0, 0, 0, 0, 1) is True
        assert point_in_circle(0.5, 0.5, 0, 0, 1) is True

    def test_point_outside_circle(self):
        """Point outside circle returns False."""
        assert point_in_circle(2, 0, 0, 0, 1) is False
        assert point_in_circle(1, 1, 0, 0, 1) is False  # sqrt(2) > 1

    def test_point_on_circle_boundary(self):
        """Point exactly on circle boundary returns True."""
        assert point_in_circle(1, 0, 0, 0, 1) is True
        assert point_in_circle(0, 1, 0, 0, 1) is True

    def test_offset_circle(self):
        """Works with non-origin centered circles."""
        assert point_in_circle(5, 5, 5, 5, 1) is True
        assert point_in_circle(5, 5, 10, 10, 1) is False


class TestFovCornersInCircle:
    """Tests for fov_corners_in_circle function."""

    def test_small_fov_at_center(self):
        """Small FOV at circle center is inside."""
        assert fov_corners_in_circle(0, 0, 0.5, 0.5, 0, 0, 1) is True

    def test_large_fov_exceeds_circle(self):
        """Large FOV exceeds circle boundary."""
        assert fov_corners_in_circle(0, 0, 2, 2, 0, 0, 1) is False

    def test_fov_at_edge(self):
        """FOV positioned at edge of circle."""
        # FOV center at (0.5, 0) with size 0.2x0.2 should fit in unit circle
        assert fov_corners_in_circle(0.5, 0, 0.2, 0.2, 0, 0, 1) is True

    def test_fov_partially_outside(self):
        """FOV with some corners outside returns False."""
        # FOV center at (0.9, 0) with size 0.4x0.4 - right edge extends past unit circle
        assert fov_corners_in_circle(0.9, 0, 0.4, 0.4, 0, 0, 1) is False

    def test_asymmetric_fov(self):
        """Works with non-square FOV."""
        # Wide but short FOV - 1.5x0.2 centered at origin
        # Corners at (+/-0.75, +/-0.1), 0.75^2 + 0.1^2 = 0.5725 < 1
        assert fov_corners_in_circle(0, 0, 1.5, 0.2, 0, 0, 1) is True
        # Wider FOV that extends past the circle
        assert fov_corners_in_circle(0, 0, 2.5, 0.2, 0, 0, 1) is False


class TestFovOverlapsPolygon:
    """Tests for fov_overlaps_polygon function."""

    def test_fov_center_inside_polygon(self):
        """FOV with center inside polygon overlaps."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        assert fov_overlaps_polygon(2, 2, 0.5, 0.5, square) is True

    def test_fov_completely_outside(self):
        """FOV completely outside polygon does not overlap."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        assert fov_overlaps_polygon(10, 10, 0.5, 0.5, square) is False

    def test_fov_corner_overlaps(self):
        """FOV with corner inside polygon overlaps."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        # FOV center outside but corner inside
        assert fov_overlaps_polygon(-0.1, 2, 0.5, 0.5, square) is True

    def test_fov_straddles_polygon_edge(self):
        """FOV straddling polygon edge."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        # FOV center outside but corners on both sides of edge
        result = fov_overlaps_polygon(-0.3, 2, 1, 1, square)
        # At least one corner should be inside
        assert result is True

    def test_polygon_inside_fov_without_center_or_corner_hit(self):
        """Overlap is detected when polygon lies inside FOV interior."""
        thin_strip = np.array(
            [[-0.2, 0.85], [0.2, 0.85], [0.2, 0.95], [-0.2, 0.95]],
            dtype=float,
        )
        # FOV rectangle is [-1, 1] x [-1, 1]. No FOV corner is inside this strip,
        # and the FOV center (0,0) is also outside, but shapes still overlap.
        assert fov_overlaps_polygon(0.0, 0.0, 2.0, 2.0, thin_strip) is True

    def test_polygon_crosses_fov_edge_without_corner_containment(self):
        """Overlap is detected when polygon edge intersects FOV edge."""
        sliver = np.array(
            [[-1.5, 0.95], [1.5, 0.95], [1.5, 1.05], [-1.5, 1.05]],
            dtype=float,
        )
        # This intersects the top FOV boundary y=1.0, but contains neither center nor corners.
        assert fov_overlaps_polygon(0.0, 0.0, 2.0, 2.0, sliver) is True


class TestBoundingBox:
    """Tests for bounding_box function."""

    def test_square_bounding_box(self):
        """Bounding box of a square."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]])
        x_min, y_min, x_max, y_max = bounding_box(square)
        assert x_min == 0
        assert y_min == 0
        assert x_max == 4
        assert y_max == 4

    def test_triangle_bounding_box(self):
        """Bounding box of a triangle."""
        triangle = np.array([[1, 1], [5, 1], [3, 4]])
        x_min, y_min, x_max, y_max = bounding_box(triangle)
        assert x_min == 1
        assert y_min == 1
        assert x_max == 5
        assert y_max == 4

    def test_single_point(self):
        """Bounding box of a single point."""
        point = np.array([[3, 5]])
        x_min, y_min, x_max, y_max = bounding_box(point)
        assert x_min == 3
        assert y_min == 5
        assert x_max == 3
        assert y_max == 5

    def test_empty_vertices(self):
        """Bounding box of empty array returns zeros."""
        empty = np.array([]).reshape(0, 2)
        x_min, y_min, x_max, y_max = bounding_box(empty)
        assert x_min == 0
        assert y_min == 0
        assert x_max == 0
        assert y_max == 0

    def test_negative_coordinates(self):
        """Works with negative coordinates."""
        vertices = np.array([[-5, -3], [2, 4], [-1, 6]])
        x_min, y_min, x_max, y_max = bounding_box(vertices)
        assert x_min == -5
        assert y_min == -3
        assert x_max == 2
        assert y_max == 6
