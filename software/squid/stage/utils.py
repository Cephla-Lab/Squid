from typing import Optional
import os

import squid.logging
from squid.abc import Pos
from squid.config import StageConfig

_log = squid.logging.get_logger(__package__)
_DEFAULT_CACHE_PATH = "cache/last_coords.txt"
"""
Attempts to load a cached stage position and return it.
"""
def get_cached_position(cache_path=_DEFAULT_CACHE_PATH) -> Optional[Pos]:
    if not os.path.isfile(cache_path):
        _log.debug(f"Cache file '{cache_path}' not found, no cached pos found.")
        return None
    with open(cache_path, "r") as f:
        for line in f:
            try:
                x, y, z = line.strip("\n").strip().split(",")
                x = float(x)
                y = float(y)
                z = float(z)
                return Pos(x_mm=x, y_mm=y, z_mm=z, theta_rad=None)
            except RuntimeError as e:
                raise e
                pass
    return None

"""
Write out the current x, y, z position, in mm, so we can use it later as a cached position.
"""
def cache_position(pos: Pos, stage_config: StageConfig, cache_path=_DEFAULT_CACHE_PATH):
    x_min = stage_config.X_AXIS.MIN_POSITION
    x_max = stage_config.X_AXIS.MAX_POSITION
    y_min = stage_config.Y_AXIS.MIN_POSITION
    y_max = stage_config.Y_AXIS.MAX_POSITION
    z_min = stage_config.Z_AXIS.MIN_POSITION
    z_max = stage_config.Z_AXIS.MAX_POSITION
    if not (x_min <= pos.x_mm <= x_max and
            y_min <= pos.y_mm <= y_max and
            z_min <= pos.z_mm <= z_max):
        raise ValueError(f"Position {pos} is not cacheable because it is outside of the min/max of at least one axis. x_range=({x_min}, {x_max}), y_range=({y_min}, {y_max}), z_range=({z_min}, {z_max})")
    with open(cache_path, "w") as f:
        _log.debug(f"Writing position={pos} to cache path='{cache_path}'")
        f.write(",".join([str(pos.x_mm), str(pos.y_mm), str(pos.z_mm)]))
