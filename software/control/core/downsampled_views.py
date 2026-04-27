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

from control._def import ZProjectionMode, DownsamplingMethod
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


def _pyrdown_chain(tile: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
    """Fast downsampling using Gaussian pyramid (cv2.pyrDown chain).

    Uses repeated 2x reductions via cv2.pyrDown (highly optimized with SIMD),
    then INTER_AREA for final sizing. ~18x faster than pure INTER_AREA with
    similar quality.

    Args:
        tile: Input image
        target_width: Target width
        target_height: Target height

    Returns:
        Downsampled image
    """
    result = tile
    # Apply pyrDown until we're close to target size (within 2x)
    while result.shape[0] > target_height * 2 and result.shape[1] > target_width * 2:
        result = cv2.pyrDown(result)

    # Final resize to exact target size
    if result.shape[0] != target_height or result.shape[1] != target_width:
        result = cv2.resize(result, (target_width, target_height), interpolation=cv2.INTER_AREA)

    return result


def downsample_tile(
    tile: np.ndarray,
    source_pixel_size_um: float,
    target_pixel_size_um: float,
    method: DownsamplingMethod = DownsamplingMethod.INTER_AREA_FAST,
) -> np.ndarray:
    """Downsample a tile to target pixel size.

    Args:
        tile: Image tile
        source_pixel_size_um: Source pixel size in micrometers
        target_pixel_size_um: Target pixel size in micrometers
        method: Interpolation method:
            - INTER_LINEAR: Fast (~0.05ms), good for real-time previews
            - INTER_AREA_FAST: Balanced (~1ms), pyrDown chain + INTER_AREA
            - INTER_AREA: Highest quality (~18ms), pure area averaging

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

    # Select downsampling strategy
    if method == DownsamplingMethod.INTER_LINEAR:
        downsampled = cv2.resize(tile, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        mode = "LINEAR"
    elif method == DownsamplingMethod.INTER_AREA_FAST:
        downsampled = _pyrdown_chain(tile, new_width, new_height)
        mode = "AREA_FAST"
    else:  # INTER_AREA
        downsampled = cv2.resize(tile, (new_width, new_height), interpolation=cv2.INTER_AREA)
        mode = "AREA"

    t_resize = time.perf_counter()

    # Preserve dtype
    if downsampled.dtype != tile.dtype:
        downsampled = downsampled.astype(tile.dtype)

    t_end = time.perf_counter()

    # Log timing for performance analysis
    log.debug(
        f"[PERF] downsample_tile: {tile.shape} -> ({new_height}, {new_width}) factor={factor} mode={mode} | "
        f"resize={t_resize - t_start:.4f}s, dtype={t_end - t_resize:.4f}s, TOTAL={t_end - t_start:.4f}s"
    )

    return downsampled


def downsample_to_resolutions(
    tile: np.ndarray,
    source_pixel_size_um: float,
    target_resolutions_um: List[float],
    method: DownsamplingMethod = DownsamplingMethod.INTER_AREA_FAST,
) -> Dict[float, np.ndarray]:
    """Downsample a tile to multiple target resolutions.

    For INTER_LINEAR and INTER_AREA_FAST: Each resolution is computed directly
        from the original (parallel) since these methods are already fast.
    For INTER_AREA: Resolutions are computed in cascade (sorted finest to coarsest)
        to improve performance (~3x faster than parallel INTER_AREA).

    Args:
        tile: Image tile
        source_pixel_size_um: Source pixel size in micrometers
        target_resolutions_um: List of target resolutions in micrometers
        method: Interpolation method

    Returns:
        Dictionary mapping resolution to downsampled tile
    """
    log = squid.logging.get_logger(__name__)
    t_start = time.perf_counter()

    results: Dict[float, np.ndarray] = {}

    # Sort resolutions finest to coarsest for cascading
    sorted_resolutions = sorted(target_resolutions_um)

    if method in (DownsamplingMethod.INTER_LINEAR, DownsamplingMethod.INTER_AREA_FAST):
        # Parallel: each from original (both methods are fast enough)
        for resolution in sorted_resolutions:
            results[resolution] = downsample_tile(tile, source_pixel_size_um, resolution, method)
    else:
        # Cascaded for INTER_AREA: original → finest → ... → coarsest
        current = tile
        current_resolution = source_pixel_size_um
        for resolution in sorted_resolutions:
            current = downsample_tile(current, current_resolution, resolution, method)
            results[resolution] = current
            current_resolution = resolution

    t_end = time.perf_counter()
    log.debug(
        f"[PERF] downsample_to_resolutions: {len(target_resolutions_um)} resolutions, "
        f"method={method.value}, TOTAL={t_end - t_start:.4f}s"
    )

    return results


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
