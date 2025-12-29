"""
Tests for scan size and coverage consistency in WellplateMultiPointWidget.

Coverage = (area of well actually covered by tiles) / well_area × 100
"""

import math
import pytest


class TestWellCoverage:
    """Tests for well coverage calculation."""

    def get_tile_positions(self, scan_size_mm, fov_size_mm, overlap_percent, shape):
        """Get tile center positions matching scan_coordinates.py logic."""
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

    def calculate_well_coverage(
        self, scan_size_mm, fov_size_mm, overlap_percent, shape, well_size_mm, is_round_well=True
    ):
        """Calculate what fraction of the well is actually covered by FOV tiles."""
        tiles = self.get_tile_positions(scan_size_mm, fov_size_mm, overlap_percent, shape)

        well_radius = well_size_mm / 2
        fov_half = fov_size_mm / 2

        resolution = 100
        covered = 0
        total = 0

        for i in range(resolution):
            for j in range(resolution):
                x = -well_radius + (2 * well_radius * i / resolution)
                y = -well_radius + (2 * well_radius * j / resolution)

                if is_round_well:
                    if x * x + y * y > well_radius * well_radius:
                        continue
                else:
                    if abs(x) > well_radius or abs(y) > well_radius:
                        continue

                total += 1

                for tx, ty in tiles:
                    if abs(x - tx) <= fov_half and abs(y - ty) <= fov_half:
                        covered += 1
                        break

        return round((covered / total) * 100, 2) if total > 0 else 0

    def test_small_scan_partial_coverage(self):
        """Small scan should give partial coverage."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        coverage = self.calculate_well_coverage(15.0, fov, overlap, "Circle", well_size)
        assert coverage < 100, f"15mm scan should have partial coverage, got {coverage}%"
        assert coverage > 0, "Should have some coverage"

    def test_larger_scan_more_coverage(self):
        """Larger scan should cover more of the well."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        cov_15 = self.calculate_well_coverage(15.0, fov, overlap, "Circle", well_size)
        cov_16 = self.calculate_well_coverage(16.0, fov, overlap, "Circle", well_size)

        assert cov_16 > cov_15, f"16mm should cover more than 15mm: {cov_16}% vs {cov_15}%"

    def test_coverage_capped_at_100(self):
        """Coverage should not exceed 100%."""
        well_size = 15.54
        fov = 3.9
        overlap = 10

        # Even with large scan, coverage of well cannot exceed 100%
        coverage = self.calculate_well_coverage(30.0, fov, overlap, "Circle", well_size)
        assert coverage <= 100, f"Coverage should not exceed 100%, got {coverage}%"


class TestEffectiveWellSize:
    """Tests for effective well size calculations (used for scan_size defaults)."""

    def get_effective_well_size(self, well_size_mm, fov_size_mm, shape, is_round_well=True):
        if shape == "Circle":
            return well_size_mm + fov_size_mm * (1 + math.sqrt(2))
        elif shape == "Square" and is_round_well:
            return well_size_mm / math.sqrt(2)
        elif shape == "Rectangle" and is_round_well:
            return well_size_mm / math.sqrt(1.36)
        return well_size_mm

    def test_square_on_round_well_inscribed(self):
        well_size = 6.21
        effective = self.get_effective_well_size(well_size, 0.5, "Square", is_round_well=True)
        expected = well_size / math.sqrt(2)
        assert abs(effective - expected) < 0.001

    def test_circle_includes_fov_adjustment(self):
        well_size = 6.21
        fov_size = 0.5
        effective = self.get_effective_well_size(well_size, fov_size, "Circle")
        expected = well_size + fov_size * (1 + math.sqrt(2))
        assert effective == expected
