# squid/version.py
"""The running software's version, as `git describe` reports it for the software checkout."""

import subprocess
from pathlib import Path
from typing import Optional

import squid.logging

_log = squid.logging.get_logger(__name__)

# The software folder: the parent of the squid package.
_SOFTWARE_DIR = Path(__file__).resolve().parent.parent
_GIT_TIMEOUT_S = 2

_cached_version: Optional[str] = None


def software_version() -> str:
    """`git describe --tags --always --dirty` of the software folder, cached; "unknown" when git is missing or fails."""
    global _cached_version
    if _cached_version is None:
        _cached_version = _describe()
    return _cached_version


def _describe() -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(_SOFTWARE_DIR),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        _log.debug(f"Could not read the software version with git: {e}")
        return "unknown"
    version = (result.stdout or "").strip()
    if result.returncode != 0 or not version:
        _log.debug(f"git describe failed ({result.returncode}): {(result.stderr or '').strip()}")
        return "unknown"
    return version
