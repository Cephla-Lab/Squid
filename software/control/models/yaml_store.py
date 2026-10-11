"""Shared YAML persistence for the small pydantic sidecar models.

- guarded load: absent -> None; damage -> log the caller's message loudly and
  return None (the app keeps running on defaults rather than refusing to start).
  An existing but empty file is the model's defaults, as in every other YAML
  loader here (nothing legitimately depends on an empty sidecar, and nothing
  is lost by one: the atomic save precludes truncation).
- load for edit: the write-side question. A load->edit->save flow that took
  the guarded None for "empty store" would replace a damaged file wholesale
  and lose every definition still recoverable from it, so write paths ask
  this variant: absent -> the model's defaults, damaged -> YamlStoreDamaged.
- schema version: a model with a `version` field declares, through that field's
  default, the one version this build reads. The schema IS the version: any
  key added or removed bumps it, so a file from another build is refused with
  the version message (which names the build to restore) rather than as a
  typo by the unknown-key rule below; the version check still earns its place
  for a changed meaning behind an unchanged key, which no key check can see.
  A file without the key is taken to be the current version.
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
from pydantic import BaseModel, ConfigDict

import squid.logging

log = squid.logging.get_logger(__name__)

M = TypeVar("M", bound=BaseModel)


class SidecarModel(BaseModel):
    """Base for every sidecar model - model_config does not reach nested models,
    so a base class is the one place a new model or field cannot forget:

    - no NaN/inf: YAML accepts `.nan`/`.inf`, and a definition replaces the
      shipped entry (or an angle applies to every plate) wholesale, so one such
      number would poison every resolved stage position;
    - no unknown keys: these files are hand-edited, and pydantic would otherwise
      drop a typo (`a1_x_mn`) and use the field's default - 0.0 for A1, which
      moves every well of that plate. (A key from another build is a different
      version, caught first by the version check above.)"""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")


_cache: Dict[str, Tuple[Tuple[int, int, int], Optional[BaseModel]]] = {}


def load_yaml_model(path: str, model_cls: Type[M], damage_message: str, *, copy: bool = True) -> Optional[M]:
    """None when absent; damage logs `damage_message` loudly and returns None.

    copy=False returns the CACHED object itself - callers must treat it as
    read-only. It exists because the rotation resolver runs at stage-update
    rate and reads two scalars: deep-copying the whole store for that was 97%
    of its cost and grew with every format a lab calibrates. Edit->save flows
    keep the default deep copy, so a mutation can never poison the cache.
    """
    cache_key = os.path.abspath(path)  # callers pass cwd-relative paths; tests chdir
    try:
        stat = os.stat(path)
    except OSError:
        _cache.pop(cache_key, None)
        return None
    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == signature:
        model = cached[1]
        if model is None:
            return None
        return model.model_copy(deep=True) if copy else model

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            data = {}  # exists but empty: the defaults, not damage
        model = None if _declares_another_version(path, model_cls, data) else model_cls.model_validate(data)
    except Exception:
        log.exception(damage_message)
        model = None
    _cache[cache_key] = (signature, model)
    if model is None:
        return None
    return model.model_copy(deep=True) if copy else model


class YamlStoreDamaged(ValueError):
    """A write path asked for a file that exists but cannot be read; .args[0]
    is user-facing copy."""


def load_yaml_model_for_edit(path: str, model_cls: Type[M], damage_message: str) -> M:
    """The store to edit and save back: the file's contents, or the model's
    defaults when there is no file. Raises YamlStoreDamaged when the file
    exists but does not load - overwriting it would destroy whatever it still
    holds, and the guarded load already said why it does not load."""
    loaded = load_yaml_model(path, model_cls, damage_message)
    if loaded is not None:
        return loaded
    if os.path.exists(path):
        raise YamlStoreDamaged(
            f"{path} exists but cannot be read; refusing to write over it (its contents may still be "
            f"recoverable). Fix or move the file aside, then try again."
        )
    return model_cls()


def _declares_another_version(path: str, model_cls: Type[BaseModel], data) -> bool:
    field = model_cls.model_fields.get("version")
    if field is None or not isinstance(data, dict):
        return False
    declared = data.get("version", field.default)
    if declared == field.default:
        return False
    log.error(
        f"{path} declares version {declared!r}, but this build reads version {field.default} - IGNORING THE FILE "
        f"rather than silently misreading it; ITS CONTENTS ARE NOT BEING APPLIED. Restore a build that reads "
        f"version {declared!r}, or move the file aside and re-create it."
    )
    return True


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
