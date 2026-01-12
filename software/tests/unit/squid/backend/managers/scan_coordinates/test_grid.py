"""Tests for the grid module."""

import numpy as np
import pytest

from squid.backend.managers.scan_coordinates.grid import (
    GridConfig,
    apply_s_pattern,
    filter_coordinates_in_bounds,
    generate_circular_grid,
    generate_grid_by_count,
    generate_grid_by_step_size,
    generate_polygon_grid,
    generate_rectangular_grid,
    generate_square_grid,
)


class TestGridConfig:
    """Tests for GridConfig dataclass."""

    def test_step_size_calculation(self):
        """Step sizes are calculated correctly from FOV and overlap."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        assert config.step_x_mm == pytest.approx(0.9)
        assert config.step_y_mm == pytest.approx(0.9)

    def test_step_size_with_zero_overlap(self):
        """Step sizes equal FOV size with zero overlap."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=0.5, overlap_percent=0.0)
        assert config.step_x_mm == pytest.approx(1.0)
        assert config.step_y_mm == pytest.approx(0.5)

    def test_step_size_with_50_percent_overlap(self):
        """Step sizes are half FOV with 50% overlap."""
        config = GridConfig(fov_width_mm=2.0, fov_height_mm=2.0, overlap_percent=50.0)
        assert config.step_x_mm == pytest.approx(1.0)
        assert config.step_y_mm == pytest.approx(1.0)


class TestGenerateRectangularGrid:
    """Tests for generate_rectangular_grid function."""

    def test_single_tile(self):
        """Single tile when scan area equals FOV size."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_rectangular_grid(0, 0, 1.0, 1.0, config)
        assert len(coords) == 1
        assert coords[0] == (0, 0)

    def test_2x2_grid(self):
        """2x2 grid generation."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0)
        # For 2x2 grid, we need scan area > FOV
        coords = generate_rectangular_grid(0, 0, 2.0, 2.0, config)
        assert len(coords) == 4

    def test_grid_is_centered(self):
        """Grid is centered on the specified center point."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0)
        coords = generate_rectangular_grid(5, 10, 3.0, 3.0, config)
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        assert pytest.approx(np.mean(xs), abs=0.01) == 5
        assert pytest.approx(np.mean(ys), abs=0.01) == 10

    def test_s_pattern_applied(self):
        """S-pattern reverses alternate rows."""
        config = GridConfig(
            fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0, fov_pattern="S-Pattern"
        )
        coords = generate_rectangular_grid(0, 0, 3.0, 3.0, config)
        # Should be at least 3x3 = 9 tiles
        assert len(coords) >= 9

        # Check that second row is reversed (goes right-to-left)
        # Get unique y values
        ys = sorted(set(c[1] for c in coords))
        if len(ys) >= 2:
            # First row: should be left to right
            first_row = [c for c in coords if c[1] == ys[0]]
            second_row = [c for c in coords if c[1] == ys[1]]

            # Second row x values should be decreasing
            second_row_xs = [c[0] for c in second_row]
            assert second_row_xs == sorted(second_row_xs, reverse=True)


class TestGenerateSquareGrid:
    """Tests for generate_square_grid function."""

    def test_square_grid(self):
        """Square grid generates correctly."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_square_grid(0, 0, 2.0, config)
        assert len(coords) >= 1


class TestGenerateCircularGrid:
    """Tests for generate_circular_grid function."""

    def test_single_tile_at_center(self):
        """Small circle returns at least center position."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_circular_grid(0, 0, 0.5, config)
        assert len(coords) == 1
        assert coords[0] == (0, 0)

    def test_tiles_inside_circle(self):
        """All returned tiles have corners inside the circle."""
        config = GridConfig(fov_width_mm=0.5, fov_height_mm=0.5, overlap_percent=10.0)
        coords = generate_circular_grid(0, 0, 3.0, config)
        assert len(coords) > 1

        # All tiles should have corners inside the circle
        radius = 3.0 / 2
        fov_half = 0.5 / 2
        for x, y in coords:
            # Check all corners
            for dx in [-fov_half, fov_half]:
                for dy in [-fov_half, fov_half]:
                    dist_sq = (x + dx) ** 2 + (y + dy) ** 2
                    assert dist_sq <= radius**2 + 0.001  # Small tolerance

    def test_circular_grid_is_centered(self):
        """Circular grid is centered on the specified point."""
        config = GridConfig(fov_width_mm=0.3, fov_height_mm=0.3, overlap_percent=10.0)
        coords = generate_circular_grid(5, 10, 2.0, config)

        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        # Center of mass should be close to the center
        assert pytest.approx(np.mean(xs), abs=0.5) == 5
        assert pytest.approx(np.mean(ys), abs=0.5) == 10


class TestGeneratePolygonGrid:
    """Tests for generate_polygon_grid function."""

    def test_square_polygon(self):
        """Grid within a square polygon."""
        square = np.array([[0, 0], [4, 0], [4, 4], [0, 4]], dtype=float)
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0)
        coords = generate_polygon_grid(square, config)
        assert len(coords) >= 1

    def test_empty_for_small_polygon(self):
        """Empty result for polygon smaller than FOV."""
        tiny = np.array([[0, 0], [0.1, 0], [0.05, 0.1]], dtype=float)
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0)
        # This should return centroid since no grid points overlap
        coords = generate_polygon_grid(tiny, config)
        # May return empty or single point (centroid)
        assert len(coords) <= 1

    def test_invalid_polygon(self):
        """Empty result for invalid polygon (< 3 vertices)."""
        line = np.array([[0, 0], [1, 1]], dtype=float)
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=0.0)
        coords = generate_polygon_grid(line, config)
        assert len(coords) == 0


class TestGenerateGridByCount:
    """Tests for generate_grid_by_count function."""

    def test_1x1_grid(self):
        """1x1 grid returns single point."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_grid_by_count(0, 0, None, 1, 1, config)
        assert len(coords) == 1
        assert coords[0] == (0, 0)

    def test_2x3_grid(self):
        """2x3 grid returns 6 points."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_grid_by_count(0, 0, None, 2, 3, config)
        assert len(coords) == 6

    def test_includes_z_when_provided(self):
        """Z coordinate is included when provided."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_grid_by_count(0, 0, 5.0, 2, 2, config)
        assert len(coords) == 4
        for coord in coords:
            assert len(coord) == 3
            assert coord[2] == 5.0

    def test_no_z_when_none(self):
        """Z coordinate is excluded when None."""
        config = GridConfig(fov_width_mm=1.0, fov_height_mm=1.0, overlap_percent=10.0)
        coords = generate_grid_by_count(0, 0, None, 2, 2, config)
        for coord in coords:
            assert len(coord) == 2


class TestGenerateGridByStepSize:
    """Tests for generate_grid_by_step_size function."""

    def test_step_size_spacing(self):
        """Grid points are separated by specified step size."""
        coords = generate_grid_by_step_size(0, 0, None, 3, 1, 1.0, 1.0, "Raster")
        assert len(coords) == 3
        xs = sorted([c[0] for c in coords])
        assert xs[1] - xs[0] == pytest.approx(1.0)
        assert xs[2] - xs[1] == pytest.approx(1.0)

    def test_s_pattern(self):
        """S-pattern is applied correctly."""
        coords = generate_grid_by_step_size(0, 0, None, 3, 2, 1.0, 1.0, "S-Pattern")
        # Should have 6 points
        assert len(coords) == 6

        # First row should go left-to-right, second row right-to-left
        ys = sorted(set(c[1] for c in coords))
        second_row = [c for c in coords if c[1] == ys[1]]
        second_row_xs = [c[0] for c in second_row]
        assert second_row_xs == sorted(second_row_xs, reverse=True)


class TestApplySPattern:
    """Tests for apply_s_pattern function."""

    def test_empty_list(self):
        """Empty list returns empty."""
        result = apply_s_pattern([])
        assert result == []

    def test_single_point(self):
        """Single point returns unchanged."""
        result = apply_s_pattern([(1.0, 2.0)])
        assert result == [(1.0, 2.0)]

    def test_reverses_alternate_rows(self):
        """Alternate rows are reversed."""
        coords = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
        result = apply_s_pattern(coords)

        # First row should be left-to-right
        first_row = [c for c in result if c[1] == 0]
        first_row_xs = [c[0] for c in first_row]
        assert first_row_xs == [0, 1, 2]

        # Second row should be right-to-left
        second_row = [c for c in result if c[1] == 1]
        second_row_xs = [c[0] for c in second_row]
        assert second_row_xs == [2, 1, 0]


class TestFilterCoordinatesInBounds:
    """Tests for filter_coordinates_in_bounds function."""

    def test_all_in_bounds(self):
        """All coordinates within bounds are kept."""
        coords = [(0, 0), (1, 1), (2, 2)]
        result = filter_coordinates_in_bounds(coords, -1, 3, -1, 3)
        assert len(result) == 3

    def test_filters_out_of_bounds(self):
        """Coordinates outside bounds are filtered."""
        coords = [(0, 0), (5, 0), (0, 5), (-1, 0)]
        result = filter_coordinates_in_bounds(coords, 0, 2, 0, 2)
        assert len(result) == 1
        assert result[0] == (0, 0)

    def test_boundary_values_included(self):
        """Coordinates exactly on boundary are included."""
        coords = [(0, 0), (2, 0), (0, 2), (2, 2)]
        result = filter_coordinates_in_bounds(coords, 0, 2, 0, 2)
        assert len(result) == 4

    def test_3d_coordinates(self):
        """Works with (x, y, z) tuples."""
        coords = [(0, 0, 1), (5, 0, 2), (0, 5, 3)]
        result = filter_coordinates_in_bounds(coords, 0, 2, 0, 2)
        assert len(result) == 1
        assert result[0] == (0, 0, 1)
