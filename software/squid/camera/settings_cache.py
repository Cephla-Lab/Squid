"""Camera settings persistence for session continuity.

This module provides save/load functionality for camera settings (binning, pixel format)
to maintain user preferences across application restarts. Settings are stored as YAML
in the cache directory.

Typical usage:
    # On application close
    save_camera_settings(camera)

    # On application startup
    settings = load_camera_settings()
    if settings:
        camera.set_binning(*settings.binning)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import yaml

import squid.logging
from squid.abc import AbstractCamera

_log = squid.logging.get_logger(__name__)

_DEFAULT_CACHE_PATH = Path("cache/camera_settings.yaml")
DEFAULT_BINNING: Tuple[int, int] = (1, 1)


@dataclass(frozen=True)
class CachedCameraSettings:
    """Container for cached camera settings loaded from disk.

    Attributes:
        binning: Tuple of (x, y) binning factors. Must be positive integers.
        pixel_format: String representation of CameraPixelFormat enum value,
            or None if not cached.
    """

    binning: Tuple[int, int]
    pixel_format: Optional[str]

    def __post_init__(self):
        if len(self.binning) != 2:
            raise ValueError(f"Binning must be a 2-tuple, got {self.binning}")
        if self.binning[0] < 1 or self.binning[1] < 1:
            raise ValueError(f"Binning values must be positive, got {self.binning}")


def save_camera_settings(camera: AbstractCamera, cache_path: Path = _DEFAULT_CACHE_PATH) -> None:
    """Save current camera settings (binning and pixel format) to a YAML cache file.

    Creates parent directories if they do not exist. This function is fail-safe -
    errors are logged but do not raise exceptions, allowing application shutdown
    to continue.

    Args:
        camera: Camera instance to read settings from.
        cache_path: Path to the cache file. Defaults to 'cache/camera_settings.yaml'
            relative to the current working directory.
    """
    try:
        binning = camera.get_binning()
        pixel_format = camera.get_pixel_format()
    except (AttributeError, RuntimeError) as e:
        _log.error(f"Cannot read camera settings - camera may be disconnected: {e}")
        return

    settings = {
        "binning": list(binning),
        "pixel_format": pixel_format.value if pixel_format else None,
    }

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            yaml.safe_dump(settings, f, default_flow_style=False)
        _log.info(f"Camera settings saved: binning={binning}, pixel_format={pixel_format}")
    except PermissionError as e:
        _log.error(f"Cannot save camera settings - permission denied for {cache_path}: {e}")
    except OSError as e:
        _log.error(f"Cannot save camera settings - file system error: {e}")


def load_camera_settings(cache_path: Path = _DEFAULT_CACHE_PATH) -> Optional[CachedCameraSettings]:
    """Load cached camera settings from a YAML cache file.

    This function is fail-safe - returns None on any error condition.

    Args:
        cache_path: Path to the cache file. Defaults to 'cache/camera_settings.yaml'
            relative to the current working directory.

    Returns:
        CachedCameraSettings if the file exists and contains valid data, None otherwise.
        Returns None if the file doesn't exist (expected on first run).
    """
    if not cache_path.exists():
        _log.debug("No camera settings cache file found - using defaults")
        return None

    try:
        with open(cache_path, "r") as f:
            settings = yaml.safe_load(f)
    except yaml.YAMLError as e:
        _log.error(
            f"Camera settings cache file is corrupted at {cache_path}: {e}. Delete this file to reset to defaults."
        )
        return None
    except PermissionError as e:
        _log.error(f"Cannot read camera settings cache - permission denied: {e}")
        return None
    except OSError as e:
        _log.error(f"Cannot read camera settings cache - file system error: {e}")
        return None

    try:
        binning_raw = settings.get("binning")
        if not isinstance(binning_raw, list) or len(binning_raw) != 2:
            if binning_raw is not None:
                _log.warning(f"Invalid binning format in cache: {binning_raw} - using default")
            else:
                _log.warning("Camera settings cache missing 'binning' key - using default")
            binning_raw = list(DEFAULT_BINNING)

        return CachedCameraSettings(
            binning=(int(binning_raw[0]), int(binning_raw[1])),
            pixel_format=settings.get("pixel_format"),
        )
    except (TypeError, ValueError) as e:
        _log.error(f"Camera settings cache contains invalid data: {e}")
        return None
