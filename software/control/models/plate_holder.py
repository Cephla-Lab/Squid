"""The plate holder's rotation: one angle per machine.

Lives in machine_configs/plate_holder.yaml because the holder is bolted to the
stage - it IS hardware. The angle is shared by every plate format on the
holder (the dominant yaw contributors - holder mount angle and stage axis
non-orthogonality - are format-independent); a format that genuinely needs its
own angle carries a measured override in its placement entry, which wins over
this default (override-with-inherit, never additive).

Minimal schema, per the design review: only the answer (rotation_deg) and the
raw facts that make it auditable and re-fittable (measured.on/feature/points/
timestamp). Derived numbers (sigma_theta etc.) are recomputed from the points
on load, never stored. No staleness counters: a rotation change cannot
invalidate a measured A1 (rotation pivots ON A1), and re-mounting ends in the
wizard, which handles overrides at write time.
"""

import os
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from control.models.yaml_store import load_yaml_model, save_yaml_model_atomic

PLATE_HOLDER_PATH = os.path.join("machine_configs", "plate_holder.yaml")


class HolderMeasuredPoint(BaseModel):
    well: str
    x_mm: float
    y_mm: float


class HolderMeasurement(BaseModel):
    """The raw facts. Everything else is derivable by re-running the fit."""

    on: str = ""  # format the angle was measured on, e.g. "96 well plate"
    feature: str = "center"  # "center" | e.g. "corner_top_left" (same on every well)
    points: List[HolderMeasuredPoint] = Field(default_factory=list)
    timestamp: str = ""
    # OPTIONAL: only present if the reload-stability experiment was run.
    reload_spread_deg: Optional[float] = None


class PlateHolder(BaseModel):
    version: int = 1
    rotation_deg: float = 0.0  # + = CCW in the stage XY math frame; pivot = A1
    measured: HolderMeasurement = Field(default_factory=HolderMeasurement)

    @model_validator(mode="after")
    def _no_arbitrary_angles(self):
        # A bare nonzero angle is indistinguishable from a typo or a copied
        # example - per the no-arbitrary-numbers rule, it must carry the raw
        # points that produced it.
        if self.rotation_deg != 0.0 and len(self.measured.points) < 2:
            raise ValueError(
                "rotation_deg is set but 'measured.points' is missing - an angle "
                "with no provenance is rejected. Run the holder alignment instead "
                "of hand-editing the value."
            )
        return self


def load_plate_holder(path: str = PLATE_HOLDER_PATH) -> Optional[PlateHolder]:
    """None when absent (rotation 0.0 - pre-feature behaviour). Damage logs
    loudly and returns None rather than raising."""
    return load_yaml_model(
        path,
        PlateHolder,
        f"Plate holder record at {path} is unreadable; ignoring the file - "
        f"HOLDER ROTATION IS NOT BEING APPLIED (0.00 deg assumed). Re-run the "
        f"holder alignment or fix the file to clear this.",
    )


def save_plate_holder(holder: PlateHolder, path: str = PLATE_HOLDER_PATH) -> None:
    save_yaml_model_atomic(holder, path)
