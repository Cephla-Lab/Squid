"""Publish a file so that a crash or power cut leaves the old or the new content, never a partial one.

Leaf module: standard library only. control.objectives_config imports it, and control._def imports
that module while it is still initializing.
"""

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)  # the stdlib logger: this leaf module cannot import squid.logging


def write_atomically(path: Path, data: bytes) -> None:
    """Write `data` to a temp file in the same directory, fsync it, then os.replace() it onto `path`.

    os.replace is an atomic publish on POSIX and Windows for a same-filesystem rename, so a reader
    never sees a partial file. The fsync before it matters after a power cut: without it, NTFS or
    ext4 can keep the rename but lose the data, leaving an empty or zeroed file under `path`. On
    POSIX the directory is fsynced as well, so the rename itself is durable (Windows cannot open a
    directory to fsync it).

    A failure before the rename leaves the previous file untouched and no temp file behind, and
    raises the original error. A failure to fsync the directory raises after `path` already holds
    the new content.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as original_exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            _log.error(
                f"Failed to remove temp file {tmp} after a failed write: {cleanup_exc}. Original: {original_exc}"
            )
        raise original_exc
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
