import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from squid.abc import AbstractCamera

_DEFAULT_CACHE_PATH = Path("cache/camera_settings.json")


@dataclass
class CachedCameraSettings:
    binning: Tuple[int, int]
    pixel_format: Optional[str]


def save_camera_settings(camera: AbstractCamera, cache_path: Path = _DEFAULT_CACHE_PATH) -> None:
    """Save current camera settings to cache file."""
    try:
        binning = camera.get_binning()
        pixel_format = camera.get_pixel_format()

        settings = {
            "binning": list(binning),
            "pixel_format": pixel_format.value if pixel_format else None,
        }

        resolved_path = Path(cache_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        with open(resolved_path, "w") as f:
            json.dump(settings, f, indent=2)

        logging.info(f"Camera settings saved: binning={binning}, pixel_format={pixel_format}")
    except Exception as e:
        logging.warning(f"Failed to save camera settings: {e}")


def load_camera_settings(
    cache_path: Path = _DEFAULT_CACHE_PATH,
) -> Optional[CachedCameraSettings]:
    """Load cached camera settings from file."""
    try:
        resolved_path = Path(cache_path)
        if not resolved_path.exists():
            return None

        with open(resolved_path, "r") as f:
            settings = json.load(f)

        return CachedCameraSettings(
            binning=tuple(settings.get("binning", [1, 1])),
            pixel_format=settings.get("pixel_format"),
        )
    except Exception as e:
        logging.warning(f"Failed to load camera settings: {e}")
        return None
