"""Cell field renderers for simulated microscope images.

This module provides protocols and implementations for rendering simulated
cell fields. The renderers are designed to be pluggable, allowing different
cell simulation strategies to be used with the SimulatedMainCamera.

Renderers:
- SimpleCellFieldRenderer: Default implementation with Gaussian cells
- (Future) FluorescentCellRenderer: More realistic fluorescent cells
- (Future) BrightfieldCellRenderer: Brightfield-style cells with halos
"""

from typing import Protocol, Tuple, Dict, List, Optional
import numpy as np


class CellFieldRenderer(Protocol):
    """Protocol for cell field renderers.

    Renderers generate simulated cell images based on stage position,
    allowing the simulated camera to show a consistent field of cells
    that can be panned in X, Y, and Z.

    Implementations should:
    - Generate deterministic cell positions (same position = same view)
    - Support focus/defocus based on Z position
    - Be efficient enough for real-time rendering
    """

    def render_frame(
        self,
        frame: np.ndarray,
        stage_x_um: float,
        stage_y_um: float,
        stage_z_um: float,
        pixel_size_um: float,
        max_val: int,
        brightness_scale: float = 1.0,
    ) -> np.ndarray:
        """Render cells into the frame based on stage position.

        Args:
            frame: Pre-allocated frame array with background
            stage_x_um: Stage X position in micrometers
            stage_y_um: Stage Y position in micrometers
            stage_z_um: Stage Z position in micrometers
            pixel_size_um: Size of each pixel in micrometers
            max_val: Maximum pixel value for the frame dtype
            brightness_scale: Scale factor for brightness (from exposure/gain)

        Returns:
            Frame with cells rendered
        """
        ...


class SimpleCellFieldRenderer:
    """Simple cell field renderer with deterministic 3D cell positions.

    Generates a field of Gaussian-shaped cells that can span any area.
    Cells are generated lazily per chunk using deterministic seeding,
    so the same area always shows the same cells.

    The cell positions are deterministic (seeded), so the same area will
    always show the same cells, enabling consistent tiling and navigation.
    """

    def __init__(
        self,
        cell_density_per_um2: float = 0.000003,  # ~3 cells/mm² - sparse
        cell_radius_um: float = 30.0,  # Large cells (~8 pixels at 2x2 binning)
        z_focus_range_um: float = 20.0,  # Wide focus range
        z_range_um: float = 50.0,  # Z range for cell distribution
        z_center_um: float = 1200.0,  # Center Z around typical stage start
        seed: int = 42,
        chunk_size_um: float = 2000.0,  # Larger chunks = fewer lookups
    ):
        """Initialize the cell field renderer.

        Args:
            cell_density_per_um2: Average cells per square micrometer
            cell_radius_um: Base radius of cells in micrometers
            z_focus_range_um: Z range where cells appear in focus
            z_range_um: Total Z range for cell distribution
            z_center_um: Center Z position for cell distribution
            seed: Random seed for deterministic cell positions
            chunk_size_um: Size of spatial chunks for efficient lookup
        """
        self._cell_density = cell_density_per_um2
        self._cell_radius_um = cell_radius_um
        self._z_focus_range_um = z_focus_range_um
        self._z_range_um = z_range_um
        self._z_center_um = z_center_um
        self._seed = seed
        self._chunk_size_um = chunk_size_um

        # Cell storage: dict[chunk_key] -> list of (x, y, z, radius, intensity)
        # Chunks are generated lazily on first access
        self._cell_chunks: Dict[Tuple[int, int], List[Tuple[float, float, float, float, float]]] = {}

    def _get_chunk(self, chunk_x: int, chunk_y: int) -> List[Tuple[float, float, float, float, float]]:
        """Get or generate cells for a chunk (lazy generation)."""
        chunk_key = (chunk_x, chunk_y)

        if chunk_key not in self._cell_chunks:
            self._cell_chunks[chunk_key] = self._generate_chunk(chunk_x, chunk_y)

        return self._cell_chunks[chunk_key]

    def _generate_chunk(self, chunk_x: int, chunk_y: int) -> List[Tuple[float, float, float, float, float]]:
        """Generate cells for a specific chunk deterministically."""
        # Create a seed unique to this chunk but deterministic
        chunk_seed = self._seed + chunk_x * 73856093 + chunk_y * 19349663
        rng = np.random.default_rng(chunk_seed % (2**31))

        # Calculate chunk bounds in world coordinates
        x_min = chunk_x * self._chunk_size_um
        y_min = chunk_y * self._chunk_size_um

        # Number of cells in this chunk
        chunk_area = self._chunk_size_um * self._chunk_size_um
        num_cells = rng.poisson(chunk_area * self._cell_density)

        cells = []
        for _ in range(num_cells):
            x = x_min + rng.uniform(0, self._chunk_size_um)
            y = y_min + rng.uniform(0, self._chunk_size_um)
            z = rng.uniform(
                self._z_center_um - self._z_range_um / 2,
                self._z_center_um + self._z_range_um / 2
            )
            radius = rng.uniform(
                self._cell_radius_um * 0.7,
                self._cell_radius_um * 1.3
            )
            intensity = rng.uniform(0.5, 1.0)
            cells.append((x, y, z, radius, intensity))

        return cells

    def _get_cells_in_region(
        self,
        x_min_um: float,
        x_max_um: float,
        y_min_um: float,
        y_max_um: float,
    ) -> List[Tuple[float, float, float, float, float]]:
        """Get all cells within the specified region.

        Args:
            x_min_um, x_max_um: X range in micrometers
            y_min_um, y_max_um: Y range in micrometers

        Returns:
            List of (x, y, z, radius, intensity) tuples for cells in region
        """
        cells = []

        # Find relevant chunks
        chunk_x_min = int(x_min_um / self._chunk_size_um) - 1
        chunk_x_max = int(x_max_um / self._chunk_size_um) + 1
        chunk_y_min = int(y_min_um / self._chunk_size_um) - 1
        chunk_y_max = int(y_max_um / self._chunk_size_um) + 1

        for cx in range(chunk_x_min, chunk_x_max + 1):
            for cy in range(chunk_y_min, chunk_y_max + 1):
                # Get or generate cells for this chunk (lazy)
                chunk_cells = self._get_chunk(cx, cy)
                for cell in chunk_cells:
                    x, y, z, r, intensity = cell
                    # Check if cell is within region (with margin for cell radius)
                    margin = r * 3  # 3 sigma margin
                    if (x_min_um - margin <= x <= x_max_um + margin and
                        y_min_um - margin <= y <= y_max_um + margin):
                        cells.append(cell)

        return cells

    def _calculate_defocus(
        self,
        cell_z_um: float,
        stage_z_um: float,
    ) -> Tuple[float, float]:
        """Calculate defocus parameters for a cell.

        Args:
            cell_z_um: Z position of the cell
            stage_z_um: Current stage Z position

        Returns:
            (sigma_scale, intensity_scale): Multipliers for blur and intensity
        """
        z_distance = abs(cell_z_um - stage_z_um)

        if z_distance <= self._z_focus_range_um:
            # In focus
            return 1.0, 1.0
        else:
            # Defocused: wider blur, reduced intensity
            defocus_factor = (z_distance - self._z_focus_range_um) / self._z_focus_range_um
            sigma_scale = 1.0 + defocus_factor * 2.0  # Blur increases with defocus
            intensity_scale = 1.0 / (1.0 + defocus_factor * 0.5)  # Intensity decreases
            return sigma_scale, intensity_scale

    def render_frame(
        self,
        frame: np.ndarray,
        stage_x_um: float,
        stage_y_um: float,
        stage_z_um: float,
        pixel_size_um: float,
        max_val: int,
        brightness_scale: float = 1.0,
    ) -> np.ndarray:
        """Render cells into the frame based on stage position.

        Args:
            frame: Pre-allocated frame array with background
            stage_x_um: Stage X position in micrometers
            stage_y_um: Stage Y position in micrometers
            stage_z_um: Stage Z position in micrometers
            pixel_size_um: Size of each pixel in micrometers
            max_val: Maximum pixel value for the frame dtype
            brightness_scale: Scale factor for brightness (from exposure/gain)

        Returns:
            Frame with cells rendered
        """
        height, width = frame.shape
        dtype = frame.dtype

        # Calculate visible region in world coordinates
        # Stage position is at the center of the frame
        half_width_um = (width / 2) * pixel_size_um
        half_height_um = (height / 2) * pixel_size_um

        x_min_um = stage_x_um - half_width_um
        x_max_um = stage_x_um + half_width_um
        y_min_um = stage_y_um - half_height_um
        y_max_um = stage_y_um + half_height_um

        # Get cells in visible region
        cells = self._get_cells_in_region(x_min_um, x_max_um, y_min_um, y_max_um)

        # Limit number of cells for performance
        MAX_CELLS_PER_FRAME = 40
        if len(cells) > MAX_CELLS_PER_FRAME:
            # Use deterministic seed so same position shows same cells
            sample_seed = int(abs(stage_x_um * 1000 + stage_y_um * 7)) % (2**31)
            rng = np.random.default_rng(sample_seed)
            indices = rng.choice(len(cells), MAX_CELLS_PER_FRAME, replace=False)
            cells = [cells[i] for i in sorted(indices)]

        # Render each cell efficiently (local patch only, not full frame)
        for cell_x, cell_y, cell_z, cell_radius, cell_intensity in cells:
            # Convert cell position to pixel coordinates
            # X: stage_x increases → view shifts right → cells move left in image
            pixel_x = (cell_x - stage_x_um) / pixel_size_um + width / 2
            # Y: stage_y increases → view shifts up → cells move down in image (higher row)
            # Note: numpy row 0 is at top, so we negate to match stage convention
            pixel_y = (stage_y_um - cell_y) / pixel_size_um + height / 2

            # Calculate defocus
            sigma_scale, intensity_scale = self._calculate_defocus(cell_z, stage_z_um)

            # Calculate sigma in pixels
            sigma_pixels = (cell_radius / pixel_size_um) * sigma_scale

            # Skip if sigma is too large (very defocused) or too small
            if sigma_pixels > 100 or sigma_pixels < 1:
                continue

            # Determine patch bounds (3 sigma radius)
            patch_radius = int(sigma_pixels * 4) + 1
            x_min_px = max(0, int(pixel_x) - patch_radius)
            x_max_px = min(width, int(pixel_x) + patch_radius + 1)
            y_min_px = max(0, int(pixel_y) - patch_radius)
            y_max_px = min(height, int(pixel_y) + patch_radius + 1)

            # Skip if patch is outside frame
            if x_min_px >= x_max_px or y_min_px >= y_max_px:
                continue

            # Create local coordinate grids for patch
            y_local = np.arange(y_min_px, y_max_px)[:, np.newaxis]
            x_local = np.arange(x_min_px, x_max_px)[np.newaxis, :]

            # Render Gaussian in patch only
            gaussian = np.exp(-(
                (x_local - pixel_x) ** 2 +
                (y_local - pixel_y) ** 2
            ) / (2 * sigma_pixels ** 2))

            # Scale intensity - cap peak to avoid saturation artifacts
            # brightness_scale affects how bright cells appear (exposure/gain)
            raw_intensity = cell_intensity * intensity_scale * 0.6 * brightness_scale
            # Cap at 0.7 so peak never saturates (Gaussian peak = 1.0)
            capped_intensity = min(raw_intensity, 0.7)
            scaled_intensity = max_val * capped_intensity

            # Add to frame patch (simple additive)
            frame[y_min_px:y_max_px, x_min_px:x_max_px] += (gaussian * scaled_intensity).astype(dtype)

        # Clip to valid range (handles overlapping cells)
        frame = np.clip(frame, 0, max_val).astype(dtype)

        return frame
