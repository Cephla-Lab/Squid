"""Unified mosaic/plate view widget.

Replaces NapariMosaicDisplayWidget and NapariPlateViewWidget with a single
widget that supports two display modes sharing one canvas per channel.
"""

import enum
from typing import List, Tuple

import numpy as np

import squid.logging  # noqa: F401  (used by UnifiedMosaicWidget once added)


class DisplayMode(enum.Enum):
    MOSAIC = "mosaic"
    PLATE = "plate"


def blit_tiles_to_canvas(
    canvas: np.ndarray,
    tiles: List[Tuple[np.ndarray, int, int]],
) -> None:
    """Blit tiles into canvas at given positions. Clips to canvas bounds."""
    canvas_h, canvas_w = canvas.shape[:2]
    for tile, y_px, x_px in tiles:
        tile_h, tile_w = tile.shape[:2]
        y_end = min(y_px + tile_h, canvas_h)
        x_end = min(x_px + tile_w, canvas_w)
        src_h = y_end - y_px
        src_w = x_end - x_px
        if src_h <= 0 or src_w <= 0:
            continue
        canvas[y_px:y_end, x_px:x_end] = tile[:src_h, :src_w]
