"""The Laser Engine tab's DF 560 nm settings, kept across sessions in Squid's cache folder.

They are operator settings, changed in the tab, so they live next to Squid's other GUI state rather than in the machine
.ini (machine configuration). cache/laser_engine_v2.yaml (relative to software/, like Squid's other cache files):

    power_560_mw: 600       # the 560 nm laser power; absent or null = the source's minimum
    idle_off_560_min: 30    # the source switches off after this many minutes unused; 0 = 24 h

load_settings() never fails: a missing or unreadable file, or a bad value, gives the default (logged). The savers never
raise: they log and return False when the setting was not saved, and keep any other key already in the file.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

import squid.logging

_log = squid.logging.get_logger(__name__)

DEFAULT_CACHE_PATH = Path("cache/laser_engine_v2.yaml")  # read at call time: tests point it at a temporary directory
DEFAULT_IDLE_OFF_560_MIN = 30.0
POWER_KEY = "power_560_mw"
IDLE_OFF_KEY = "idle_off_560_min"


@dataclass(frozen=True)
class LaserEngineV2Settings:
    power_560_mw: Optional[float] = None  # None = the source's minimum
    idle_off_560_min: float = DEFAULT_IDLE_OFF_560_MIN  # 0 = 24 h, so the source is never left on indefinitely


def _path(cache_path: Optional[Path]) -> Path:
    return Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH


def _is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _read(path: Path) -> dict:
    """The file's mapping; {} when it is missing, empty or unreadable (logged)."""
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        _log.error(f"laser engine settings {path} not read ({e}): using the defaults")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        _log.error(f"laser engine settings {path} is not a mapping: using the defaults")
        return {}
    return data


def load_settings(cache_path: Optional[Path] = None) -> LaserEngineV2Settings:
    """The saved settings, each one at its default when it is missing or not usable."""
    path = _path(cache_path)
    data = _read(path)
    power = data.get(POWER_KEY)
    if power is not None and not _is_number(power):
        _log.error(f"{path}: {POWER_KEY} = {power!r} is not a number of mW: using the source's minimum")
        power = None
    idle = data.get(IDLE_OFF_KEY)
    if idle is not None and (not _is_number(idle) or idle < 0):
        _log.error(
            f"{path}: {IDLE_OFF_KEY} = {idle!r} is not a number of minutes >= 0: using {DEFAULT_IDLE_OFF_560_MIN:g}"
        )
        idle = None
    return LaserEngineV2Settings(
        power_560_mw=None if power is None else float(power),
        idle_off_560_min=DEFAULT_IDLE_OFF_560_MIN if idle is None else float(idle),
    )


def _save(key: str, value: float, cache_path: Optional[Path]) -> bool:
    path = _path(cache_path)
    try:
        data = _read(path)
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False)
    except Exception as e:  # never raises: the setting still applies to this session
        _log.error(f"laser engine setting {key} not saved to {path}: {e}")
        return False
    return True


def save_power_560_mw(mw: float, cache_path: Optional[Path] = None) -> bool:
    """Remember the operator's 560 nm laser power (mW). False (logged) when it was not saved."""
    try:
        value = float(mw)
        if not math.isfinite(value):
            raise ValueError(f"{mw!r} is not a number of mW")
    except (TypeError, ValueError) as e:
        _log.error(f"laser engine setting {POWER_KEY} not saved: {e}")
        return False
    return _save(POWER_KEY, value, cache_path)


def save_idle_off_560_min(minutes: float, cache_path: Optional[Path] = None) -> bool:
    """Remember the 560 idle-off time (minutes, 0 = 24 h). False (logged) when it was not saved."""
    try:
        value = float(minutes)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{minutes!r} is not a number of minutes >= 0")
    except (TypeError, ValueError) as e:
        _log.error(f"laser engine setting {IDLE_OFF_KEY} not saved: {e}")
        return False
    return _save(IDLE_OFF_KEY, value, cache_path)
