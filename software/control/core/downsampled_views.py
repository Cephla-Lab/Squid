"""Downsampled well and plate view generation for Select Well Mode imaging.

This module provides utilities for generating downsampled views during acquisition:
- Per-well images at multiple resolutions (e.g., 5, 10, 20 µm)
- Compact plate view with wells arranged in a grid

The plate view uses grid indexing (not stage coordinates) so wells are immediately
adjacent with no empty space between them.
"""

import os
import time
from typing import List, Tuple, Dict, Optional, Union

import cv2
import numpy as np
import tifffile

from control._def import ZProjectionMode
import squid.logging


def calculate_overlap_pixels(
    fov_width: int,
    fov_height: int,
    dx_mm: float,
    dy_mm: float,
    pixel_size_um: float,
) -> Tuple[int, int, int, int]:
    """Calculate overlap pixels to crop from each tile edge.

    Args:
        fov_width: FOV width in pixels
        fov_height: FOV height in pixels
        dx_mm: Step size in x direction (mm)
        dy_mm: Step size in y direction (mm)
        pixel_size_um: Pixel size in micrometers

    Returns:
        Tuple of (top_crop, bottom_crop, left_crop, right_crop) in pixels
    """
    # Convert FOV dimensions to mm
    fov_width_mm = fov_width * pixel_size_um / 1000.0
    fov_height_mm = fov_height * pixel_size_um / 1000.0

    # Calculate overlap in mm
    overlap_x_mm = max(0, fov_width_mm - dx_mm)
    overlap_y_mm = max(0, fov_height_mm - dy_mm)

    # Convert to pixels and divide by 2 (crop from each side)
    overlap_x_pixels = int(round(overlap_x_mm * 1000.0 / pixel_size_um))
    overlap_y_pixels = int(round(overlap_y_mm * 1000.0 / pixel_size_um))

    left_crop = overlap_x_pixels // 2
    right_crop = overlap_x_pixels - left_crop
    top_crop = overlap_y_pixels // 2
    bottom_crop = overlap_y_pixels - top_crop

    return (top_crop, bottom_crop, left_crop, right_crop)


def crop_overlap(
    tile: np.ndarray,
    overlap: Tuple[int, int, int, int],
) -> np.ndarray:
    """Crop overlap region from tile edges.

    Args:
        tile: Image tile (2D or 3D for RGB)
        overlap: Tuple of (top_crop, bottom_crop, left_crop, right_crop) in pixels

    Returns:
        Cropped tile
    """
    top, bottom, left, right = overlap

    # Handle zero crops
    bottom_idx = tile.shape[0] - bottom if bottom > 0 else tile.shape[0]
    right_idx = tile.shape[1] - right if right > 0 else tile.shape[1]

    return tile[top:bottom_idx, left:right_idx]


def downsample_tile(
    tile: np.ndarray,
    source_pixel_size_um: float,
    target_pixel_size_um: float,
) -> np.ndarray:
    """Downsample a tile to target pixel size.

    Uses cv2.INTER_AREA for quality downsampling.

    Args:
        tile: Image tile
        source_pixel_size_um: Source pixel size in micrometers
        target_pixel_size_um: Target pixel size in micrometers

    Returns:
        Downsampled tile, or original if target <= source
    """
    log = squid.logging.get_logger(__name__)
    t_start = time.perf_counter()

    factor = int(round(target_pixel_size_um / source_pixel_size_um))

    if factor <= 1:
        return tile

    new_width = tile.shape[1] // factor
    new_height = tile.shape[0] // factor

    if new_width < 1 or new_height < 1:
        return tile

    downsampled = cv2.resize(
        tile,
        (new_width, new_height),
        interpolation=cv2.INTER_AREA,
    )

    t_resize = time.perf_counter()

    # Preserve dtype
    if downsampled.dtype != tile.dtype:
        downsampled = downsampled.astype(tile.dtype)

    t_end = time.perf_counter()

    # Log timing for performance analysis
    log.debug(
        f"[PERF] downsample_tile: {tile.shape} -> ({new_height}, {new_width}) factor={factor} | "
        f"resize={t_resize - t_start:.4f}s, dtype={t_end - t_resize:.4f}s, TOTAL={t_end - t_start:.4f}s"
    )

    return downsampled


def stitch_tiles(
    tiles: List[Tuple[np.ndarray, Tuple[float, float]]],
    pixel_size_um: float,
) -> np.ndarray:
    """Stitch tiles together using their stage coordinates.

    Args:
        tiles: List of (tile, (x_mm, y_mm)) tuples with tile images and positions
        pixel_size_um: Pixel size in micrometers

    Returns:
        Stitched image
    """
    log = squid.logging.get_logger(__name__)
    t_start = time.perf_counter()

    if len(tiles) == 0:
        raise ValueError("No tiles to stitch")

    if len(tiles) == 1:
        return tiles[0][0].copy()

    # Find bounding box in mm
    min_x_mm = min(pos[0] for _, pos in tiles)
    min_y_mm = min(pos[1] for _, pos in tiles)
    max_x_mm = max(pos[0] for _, pos in tiles)
    max_y_mm = max(pos[1] for _, pos in tiles)

    # Get tile dimensions (assume all tiles same size)
    tile_height, tile_width = tiles[0][0].shape[:2]
    tile_width_mm = tile_width * pixel_size_um / 1000.0
    tile_height_mm = tile_height * pixel_size_um / 1000.0

    # Calculate canvas size
    canvas_width_mm = max_x_mm - min_x_mm + tile_width_mm
    canvas_height_mm = max_y_mm - min_y_mm + tile_height_mm

    canvas_width = int(round(canvas_width_mm * 1000.0 / pixel_size_um))
    canvas_height = int(round(canvas_height_mm * 1000.0 / pixel_size_um))

    t_calc = time.perf_counter()

    # Handle RGB images
    dtype = tiles[0][0].dtype
    if len(tiles[0][0].shape) == 3:
        canvas = np.zeros((canvas_height, canvas_width, tiles[0][0].shape[2]), dtype=dtype)
    else:
        canvas = np.zeros((canvas_height, canvas_width), dtype=dtype)

    t_alloc = time.perf_counter()

    # Place tiles
    for tile, (x_mm, y_mm) in tiles:
        x_pixel = int(round((x_mm - min_x_mm) * 1000.0 / pixel_size_um))
        y_pixel = int(round((y_mm - min_y_mm) * 1000.0 / pixel_size_um))

        h, w = tile.shape[:2]
        y_end = min(y_pixel + h, canvas_height)
        x_end = min(x_pixel + w, canvas_width)

        canvas[y_pixel:y_end, x_pixel:x_end] = tile[: y_end - y_pixel, : x_end - x_pixel]

    t_place = time.perf_counter()

    # Log detailed timing for performance analysis
    log.debug(
        f"[PERF] stitch_tiles: {len(tiles)} tiles -> ({canvas_height}, {canvas_width}) | "
        f"calc={t_calc - t_start:.4f}s, alloc={t_alloc - t_calc:.4f}s, place={t_place - t_alloc:.4f}s, "
        f"TOTAL={t_place - t_start:.4f}s"
    )

    return canvas


def parse_well_id(well_id: str) -> Tuple[int, int]:
    """Parse well ID string to (row, col) indices.

    Args:
        well_id: Well ID string (e.g., "A1", "B12", "AA1")

    Returns:
        Tuple of (row_index, col_index), 0-based

    Raises:
        ValueError: If well_id is empty, missing letters, missing numbers,
                   or contains invalid characters in the number part.
    """
    if not well_id:
        raise ValueError("Well ID cannot be empty")

    well_id = well_id.upper()

    # Find where letters end and numbers begin
    letter_part = ""
    number_part = ""
    for char in well_id:
        if char.isalpha():
            letter_part += char
        else:
            number_part += char

    # Validate parts
    if not letter_part:
        raise ValueError(f"Well ID '{well_id}' missing row letter(s) (e.g., 'A', 'B', 'AA')")
    if not number_part:
        raise ValueError(f"Well ID '{well_id}' missing column number (e.g., '1', '12')")

    # Convert letter part to row index (A=0, B=1, ..., Z=25, AA=26, AB=27, ...)
    row = 0
    for char in letter_part:
        row = row * 26 + (ord(char) - ord("A") + 1)
    row -= 1  # Convert to 0-based

    # Convert number part to column index (1=0, 2=1, ...)
    try:
        col = int(number_part) - 1
    except ValueError:
        raise ValueError(f"Well ID '{well_id}' has invalid column number '{number_part}'")

    if col < 0:
        raise ValueError(f"Well ID '{well_id}' has invalid column number '{number_part}' (must be >= 1)")

    return (row, col)


def format_well_id(row: int, col: int) -> str:
    """Format row and column indices to well ID string.

    This is the inverse of parse_well_id. Supports rows 0-701 (A through ZZ).
    Standard plates only use up to row 31 (AF for 1536-well plates).

    Args:
        row: Row index (0-based, A=0, B=1, ..., Z=25, AA=26, ..., ZZ=701)
        col: Column index (0-based, 1=0, 2=1, ...)

    Returns:
        Well ID string (e.g., "A1", "B12", "AA1")
    """
    if row < 26:
        letter_part = chr(ord("A") + row)
    else:
        # For rows >= 26, use AA, AB, etc.
        first_letter = chr(ord("A") + (row // 26) - 1)
        second_letter = chr(ord("A") + (row % 26))
        letter_part = f"{first_letter}{second_letter}"

    return f"{letter_part}{col + 1}"


def ensure_plate_resolution_in_well_resolutions(
    well_resolutions: List[float],
    plate_resolution: float,
) -> List[float]:
    """Ensure plate resolution is in the list of well resolutions.

    Args:
        well_resolutions: List of well resolution values in µm
        plate_resolution: Plate resolution value in µm

    Returns:
        Sorted list of resolutions including plate resolution
    """
    result = list(well_resolutions)
    if plate_resolution not in result:
        result.append(plate_resolution)
    return sorted(result)


class DownsampledViewManager:
    """Manages plate view array and well slot dimensions.

    The plate view is a compact grid where wells are placed immediately adjacent
    to each other based on their grid position (row, col), not their stage coordinates.
    Supports multi-channel plate views.
    """

    def __init__(
        self,
        num_rows: int,
        num_cols: int,
        well_slot_shape: Tuple[int, int],
        num_channels: int = 1,
        channel_names: Optional[List[str]] = None,
        dtype: np.dtype = np.uint16,
    ):
        """Initialize the plate view manager.

        Args:
            num_rows: Number of rows in the plate (e.g., 8 for 96-well)
            num_cols: Number of columns in the plate (e.g., 12 for 96-well)
            well_slot_shape: (height, width) of each well slot in pixels
            num_channels: Number of imaging channels
            channel_names: List of channel names for metadata
            dtype: Data type for the plate view array
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.well_slot_shape = well_slot_shape
        self.num_channels = num_channels
        self.channel_names = channel_names or [f"Channel_{i}" for i in range(num_channels)]
        self.dtype = dtype

        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]

        # Shape: (C, H, W) for multi-channel
        self.plate_view = np.zeros((num_channels, plate_height, plate_width), dtype=dtype)
        self._log.info(
            f"Initialized plate view: {num_rows}x{num_cols} wells, "
            f"{num_channels} channels, slot shape {well_slot_shape}, total shape {self.plate_view.shape}"
        )

    def update_well(self, row: int, col: int, well_images: Dict[int, np.ndarray]) -> None:
        """Copy stitched well images into plate view grid for all channels.

        Args:
            row: 0-based row index
            col: 0-based column index
            well_images: Dict mapping channel_idx -> downsampled well image
        """
        # Validate row/col bounds
        if row < 0 or row >= self.num_rows:
            self._log.warning(f"Well row {row} out of bounds (0-{self.num_rows - 1}), skipping update")
            return
        if col < 0 or col >= self.num_cols:
            self._log.warning(f"Well col {col} out of bounds (0-{self.num_cols - 1}), skipping update")
            return

        y_start = row * self.well_slot_shape[0]
        x_start = col * self.well_slot_shape[1]

        for ch_idx, well_image in well_images.items():
            if ch_idx >= self.num_channels:
                self._log.warning(f"Channel index {ch_idx} exceeds num_channels {self.num_channels}")
                continue

            h, w = well_image.shape[:2]
            y_end = y_start + h
            x_end = x_start + w

            # Clip to plate bounds
            y_end = min(y_end, self.plate_view.shape[1])
            x_end = min(x_end, self.plate_view.shape[2])

            self.plate_view[ch_idx, y_start:y_end, x_start:x_end] = well_image[: y_end - y_start, : x_end - x_start]

        self._log.debug(
            f"Updated well ({row}, {col}) at position ({y_start}, {x_start}) for {len(well_images)} channels"
        )

    def save_plate_view(self, path: str) -> None:
        """Save plate view to disk as multi-channel TIFF.

        Args:
            path: Output file path
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tifffile.imwrite(
            path,
            self.plate_view,
            metadata={
                "axes": "CYX",
                "Channel": {"Name": self.channel_names[: self.num_channels]},
            },
        )
        self._log.info(f"Saved plate view to {path} with {self.num_channels} channels")

    def get_plate_view(self) -> np.ndarray:
        """Get a copy of the plate view array.

        Returns:
            Copy of the plate view array with shape (C, H, W)
        """
        return self.plate_view.copy()

    def get_channel_view(self, channel_idx: int) -> np.ndarray:
        """Get a copy of a single channel's plate view.

        Args:
            channel_idx: Channel index (0-based)

        Returns:
            Copy of the channel's plate view with shape (H, W)
        """
        return self.plate_view[channel_idx].copy()

    def clear(self) -> None:
        """Clear the plate view (fill with zeros)."""
        self.plate_view.fill(0)


class WellTileAccumulator:
    """Accumulates tiles for a single well during acquisition.

    This class is used within the job process to collect tiles
    until all FOVs for a well are received (for all channels), then stitches them.
    Supports multi-channel acquisitions and z-stack projections.

    Z-projection modes:
    - "middle": Only uses the middle z-layer (z_index == total_z // 2)
    - "mip": Maximum intensity projection across all z-layers (memory efficient - running max)
    """

    def __init__(
        self,
        well_id: str,
        total_fovs: int,
        total_channels: int,
        pixel_size_um: float,
        channel_names: Optional[List[str]] = None,
        total_z_levels: int = 1,
        z_projection_mode: Union[ZProjectionMode, str] = ZProjectionMode.MIP,
    ):
        """Initialize accumulator for a well.

        Args:
            well_id: Well identifier (e.g., "A1")
            total_fovs: Total number of FOVs expected for this well
            total_channels: Total number of channels per FOV
            pixel_size_um: Pixel size in micrometers
            channel_names: Optional list of channel names for metadata
            total_z_levels: Total number of z-levels in the stack
            z_projection_mode: ZProjectionMode enum or string ("mip" or "middle")
        """
        self.well_id = well_id
        self.total_fovs = total_fovs
        self.total_channels = total_channels
        self.pixel_size_um = pixel_size_um
        self.channel_names = channel_names or [f"Channel_{i}" for i in range(total_channels)]
        self.total_z_levels = total_z_levels

        # Convert and validate z_projection_mode
        self.z_projection_mode = ZProjectionMode.convert_to_enum(z_projection_mode)
        self.middle_z = total_z_levels // 2

        # For "middle" mode: Dict mapping channel_idx -> list of (tile, position) tuples
        # For "mip" mode: Dict mapping (channel_idx, fov_idx) -> (running_max_tile, position)
        self.tiles_by_channel: Dict[int, List[Tuple[np.ndarray, Tuple[float, float]]]] = {}

        # For MIP: track z-levels received per (channel, fov) to know when complete
        self.mip_tiles: Dict[Tuple[int, int], Tuple[np.ndarray, Tuple[float, float]]] = {}
        self.z_counts: Dict[Tuple[int, int], int] = {}

    def add_tile(
        self,
        tile: np.ndarray,
        position_mm: Tuple[float, float],
        channel_idx: int,
        fov_idx: int = 0,
        z_index: int = 0,
    ) -> None:
        """Add a tile to the accumulator.

        Args:
            tile: Cropped tile image
            position_mm: (x_mm, y_mm) position of the tile
            channel_idx: Channel index (0-based)
            fov_idx: FOV index within the well (0-based)
            z_index: Z-level index (0-based)
        """
        if self.z_projection_mode == ZProjectionMode.MIDDLE:
            # Only accept tiles from the middle z-level
            if z_index != self.middle_z:
                return
            if channel_idx not in self.tiles_by_channel:
                self.tiles_by_channel[channel_idx] = []
            self.tiles_by_channel[channel_idx].append((tile, position_mm))

        elif self.z_projection_mode == ZProjectionMode.MIP:
            # Running maximum intensity projection
            key = (channel_idx, fov_idx)
            if key not in self.mip_tiles:
                # First z-level for this (channel, fov)
                self.mip_tiles[key] = (tile.copy(), position_mm)
                self.z_counts[key] = 1
            else:
                # Update running maximum
                current_tile, pos = self.mip_tiles[key]
                if current_tile.shape != tile.shape:
                    raise ValueError(
                        f"Incompatible tile shapes for MIP at {key}: " f"current={current_tile.shape}, new={tile.shape}"
                    )
                np.maximum(current_tile, tile, out=current_tile)  # In-place to save memory
                self.z_counts[key] += 1

    def is_complete(self) -> bool:
        """Check if all FOVs for all channels have been received."""
        if self.z_projection_mode == ZProjectionMode.MIDDLE:
            # For middle mode: need all FOVs for all channels (only middle z)
            if len(self.tiles_by_channel) < self.total_channels:
                return False
            return all(len(tiles) >= self.total_fovs for tiles in self.tiles_by_channel.values())
        elif self.z_projection_mode == ZProjectionMode.MIP:
            # For MIP: need all FOVs * channels * z-levels
            expected_keys = self.total_fovs * self.total_channels
            if len(self.z_counts) < expected_keys:
                return False
            return all(count >= self.total_z_levels for count in self.z_counts.values())
        return False

    def stitch_all_channels(self) -> Dict[int, np.ndarray]:
        """Stitch all accumulated tiles for each channel.

        Returns:
            Dict mapping channel_idx -> stitched image
        """
        result = {}

        if self.z_projection_mode == ZProjectionMode.MIDDLE:
            # Middle mode: tiles are already in tiles_by_channel
            for channel_idx, tiles in self.tiles_by_channel.items():
                if tiles:
                    result[channel_idx] = stitch_tiles(tiles, self.pixel_size_um)
        elif self.z_projection_mode == ZProjectionMode.MIP:
            # MIP mode: convert mip_tiles to list format for stitching
            tiles_by_channel: Dict[int, List[Tuple[np.ndarray, Tuple[float, float]]]] = {}
            for (channel_idx, fov_idx), (tile, position) in self.mip_tiles.items():
                if channel_idx not in tiles_by_channel:
                    tiles_by_channel[channel_idx] = []
                tiles_by_channel[channel_idx].append((tile, position))

            for channel_idx, tiles in tiles_by_channel.items():
                if tiles:
                    result[channel_idx] = stitch_tiles(tiles, self.pixel_size_um)

        return result

    def stitch_channel(self, channel_idx: int) -> Optional[np.ndarray]:
        """Stitch tiles for a specific channel.

        Args:
            channel_idx: Channel index to stitch

        Returns:
            Stitched image for that channel, or None if no tiles
        """
        if self.z_projection_mode == ZProjectionMode.MIDDLE:
            tiles = self.tiles_by_channel.get(channel_idx, [])
            if not tiles:
                return None
            return stitch_tiles(tiles, self.pixel_size_um)
        elif self.z_projection_mode == ZProjectionMode.MIP:
            # Collect tiles for this channel from mip_tiles
            tiles = []
            for (ch_idx, fov_idx), (tile, position) in self.mip_tiles.items():
                if ch_idx == channel_idx:
                    tiles.append((tile, position))
            if not tiles:
                return None
            return stitch_tiles(tiles, self.pixel_size_um)
        return None

    def get_channel_count(self) -> int:
        """Get the number of channels with tiles."""
        if self.z_projection_mode == ZProjectionMode.MIP:
            return len(set(ch_idx for ch_idx, _ in self.mip_tiles.keys()))
        return len(self.tiles_by_channel)

    def get_fov_count(self, channel_idx: int) -> int:
        """Get the number of FOVs received for a channel."""
        if self.z_projection_mode == ZProjectionMode.MIP:
            return sum(1 for ch_idx, _ in self.mip_tiles.keys() if ch_idx == channel_idx)
        return len(self.tiles_by_channel.get(channel_idx, []))

    def clear(self) -> None:
        """Clear accumulated tiles."""
        self.tiles_by_channel.clear()
        self.mip_tiles.clear()
        self.z_counts.clear()
