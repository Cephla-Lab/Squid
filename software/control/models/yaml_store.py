"""Shared YAML persistence for the small pydantic sidecar models.

One implementation of the two patterns plate_placement.py and plate_holder.py
each carried a copy of:

- guarded load: absent -> None; damage -> log the caller's message loudly and
  return None (the app keeps running on defaults rather than refusing to start)
- atomic save: tmp file + fsync + os.replace, so an interrupted write can never
  leave a truncated file behind.

Loads are stat-guarded: the parsed model is cached per path and re-parsed only
when (mtime_ns, size, inode) changes. This is NOT a snapshot cache - the result
still changes the moment the file does, preserving the resolver's
"read at call time, never cache across calls" contract - it only removes the
redundant re-parse of an unchanged file, which matters because rotation
resolution runs on the GUI thread at stage-position-update rate. Callers get a
deep copy, so mutating a loaded model (edit -> save flows) cannot poison the
cache. A damaged file logs once per file version, not once per call.
"""

import os
from typing import Dict, Optional, Tuple, Type, TypeVar

import yaml
from pydantic import BaseModel

import squid.logging

log = squid.logging.get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

_cache: Dict[str, Tuple[Tuple[int, int, int], Optional[BaseModel]]] = {}


def load_yaml_model(path: str, model_cls: Type[M], damage_message: str) -> Optional[M]:
    """None when absent; damage logs `damage_message` loudly and returns None."""
    try:
        stat = os.stat(path)
    except OSError:
        _cache.pop(path, None)
        return None
    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    cached = _cache.get(path)
    if cached is not None and cached[0] == signature:
        model = cached[1]
        return model.model_copy(deep=True) if model is not None else None

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        model = model_cls.model_validate(data) if data is not None else None
    except Exception:
        log.exception(damage_message)
        model = None
    _cache[path] = (signature, model)
    return model.model_copy(deep=True) if model is not None else None


def save_yaml_model_atomic(model: BaseModel, path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            yaml.safe_dump(model.model_dump(exclude_none=True), f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
