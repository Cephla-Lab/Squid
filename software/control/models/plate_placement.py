"""Measured plate placement: per-format a1 deltas (and, when measured, rotation).

Lives in cache/plate_placement.yaml - per-load measured state, not hardware
description (machine_configs/) and not nominal geometry (the catalog + user
YAML). Losing the file costs one A1 touch, consistent with the other
session-scoped files in cache/.

Storage rule (design doc): store the raw measurements and the answer; never
store what can be recomputed. The delta is a DELTA on the catalog's a1 - the
catalog keeps sole ownership of the absolute origin, so "absence of the file
== identity" is literally true and precedence questions are unrepresentable.
"""

import os
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

import squid.logging

log = squid.logging.get_logger(__name__)

PLATE_PLACEMENT_PATH = os.path.join("cache", "plate_placement.yaml")


class MeasuredPoint(BaseModel):
    well: str
    x_mm: float
    y_mm: float


class PlacementFit(BaseModel):
    """Raw facts only; sigma-hat / predicted errors are recomputed on load."""

    points: List[MeasuredPoint] = Field(default_factory=list)
    timestamp: str = ""
    note: str = ""  # e.g. "migrated_from_cache_csv (mtime 2026-07-11)"


class PlatePlacement(BaseModel):
    a1_dx_mm: float = 0.0  # DELTA on the catalog a1_x_mm
    a1_dy_mm: float = 0.0
    # null/absent => inherit the holder angle (0.0 until the holder record
    # lands). When set, it is the ABSOLUTE total angle for this format -
    # never a delta, never a summand.
    rotation_deg: Optional[float] = None
    fit: PlacementFit = Field(default_factory=PlacementFit)


class PlatePlacements(BaseModel):
    version: int = 1
    placements: Dict[str, PlatePlacement] = Field(default_factory=dict)


def load_plate_placements(path: str = PLATE_PLACEMENT_PATH) -> Optional[PlatePlacements]:
    """None when absent (identity). Damage logs loudly, returns None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            return None
        return PlatePlacements.model_validate(data)
    except Exception:
        log.exception(
            f"Plate placements at {path} are unreadable; ignoring the file - "
            f"YOUR A1 CALIBRATION IS NOT BEING APPLIED, so stage positions will "
            f"differ from your last session. Recalibrate or move the file aside."
        )
        return None


def save_plate_placements(placements: PlatePlacements, path: str = PLATE_PLACEMENT_PATH) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            yaml.safe_dump(placements.model_dump(exclude_none=True), f, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
