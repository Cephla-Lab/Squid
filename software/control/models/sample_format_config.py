"""User sample-format geometry: overrides and custom formats.

Lives in objective_and_sample_formats/sample_formats_user.yaml, next to the
shipped catalog it overlays - NOT machine_configs/ (that directory describes
the microscope hardware; a lab's plate catalog is not the instrument).

The shipped sample_formats.csv is byte-frozen. This file carries only what a
lab deliberately changed:

* ``overrides``: sparse per-field edits to a SHIPPED format's nominal geometry
  (vendor pitch differences on the non-SLAS 6/12/24/48 formats, measured well
  size, number_of_skip preference, well_shape). Only the fields present are
  applied; everything absent stays shipped, so a future shipped correction
  reaches every machine except on exactly the field a lab changed.
* ``custom_formats``: complete new formats ("Add New Format", chamber slides,
  slide carriers).

Deliberately NOT here: a1_x/y_mm (measured placement, lives in the placement
sidecar), rotation (holder record), a1_x/y_pixel for shipped formats (display
asset registration).
"""

import os
from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator

from control.models.yaml_store import load_yaml_model, save_yaml_model_atomic

import squid.logging

log = squid.logging.get_logger(__name__)

USER_SAMPLE_FORMATS_PATH = os.path.join("objective_and_sample_formats", "sample_formats_user.yaml")

# Fields an override may carry. a1_* are excluded by construction.
_OVERRIDABLE = (
    "well_spacing_mm",
    "well_spacing_x_mm",
    "well_spacing_y_mm",
    "well_size_mm",
    "well_size_x_mm",
    "well_size_y_mm",
    "number_of_skip",
    "well_shape",
)


class SampleFormatOverride(BaseModel):
    """Sparse per-field edit of a shipped format. All fields optional."""

    well_spacing_mm: Optional[float] = Field(None, gt=0)
    well_spacing_x_mm: Optional[float] = Field(None, gt=0)
    well_spacing_y_mm: Optional[float] = Field(None, gt=0)
    well_size_mm: Optional[float] = Field(None, gt=0)
    well_size_x_mm: Optional[float] = Field(None, gt=0)
    well_size_y_mm: Optional[float] = Field(None, gt=0)
    number_of_skip: Optional[int] = Field(None, ge=0)
    well_shape: Optional[str] = Field(None, pattern="^(circle|rectangle)$")

    @model_validator(mode="after")
    def _scalar_xor_per_axis(self):
        # Ambiguity fails loudly, not by precedence rule.
        if self.well_spacing_mm is not None and (
            self.well_spacing_x_mm is not None or self.well_spacing_y_mm is not None
        ):
            raise ValueError("set either well_spacing_mm OR well_spacing_x/y_mm, not both")
        if self.well_size_mm is not None and (self.well_size_x_mm is not None or self.well_size_y_mm is not None):
            raise ValueError("set either well_size_mm OR well_size_x/y_mm, not both")
        return self

    def applied_fields(self) -> Dict[str, object]:
        out = {}
        for name in _OVERRIDABLE:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
                # A scalar edit re-broadcasts to both axes (matching the derived
                # defaults); a per-axis edit updates the scalar for legacy readers.
                if name == "well_spacing_mm":
                    out["well_spacing_x_mm"] = value
                    out["well_spacing_y_mm"] = value
                if name == "well_size_mm":
                    out["well_size_x_mm"] = value
                    out["well_size_y_mm"] = value
        return out


class CustomSampleFormat(BaseModel):
    """A complete user-defined format (values from vendor drawings - never invented)."""

    rows: int = Field(..., ge=1)
    cols: int = Field(..., ge=1)
    well_spacing_mm: Optional[float] = Field(None, gt=0)
    well_spacing_x_mm: Optional[float] = Field(None, gt=0)
    well_spacing_y_mm: Optional[float] = Field(None, gt=0)
    well_size_mm: Optional[float] = Field(None, gt=0)
    well_size_x_mm: Optional[float] = Field(None, gt=0)
    well_size_y_mm: Optional[float] = Field(None, gt=0)
    well_shape: str = Field("circle", pattern="^(circle|rectangle)$")
    number_of_skip: int = Field(0, ge=0)
    # Measured at creation time by the calibration flow; the placement sidecar
    # carries later refinements as deltas on top of these.
    a1_x_mm: float = 0.0
    a1_y_mm: float = 0.0
    a1_x_pixel: int = 0
    a1_y_pixel: int = 0

    @model_validator(mode="after")
    def _resolve_axes(self):
        if self.well_spacing_mm is not None and (
            self.well_spacing_x_mm is not None or self.well_spacing_y_mm is not None
        ):
            raise ValueError("set either well_spacing_mm OR well_spacing_x/y_mm, not both")
        if self.well_spacing_mm is None and (self.well_spacing_x_mm is None or self.well_spacing_y_mm is None):
            raise ValueError("well_spacing_mm, or both well_spacing_x_mm and well_spacing_y_mm, is required")
        if self.well_size_mm is not None and (self.well_size_x_mm is not None or self.well_size_y_mm is not None):
            raise ValueError("set either well_size_mm OR well_size_x/y_mm, not both")
        if self.well_size_mm is None and (self.well_size_x_mm is None or self.well_size_y_mm is None):
            raise ValueError("well_size_mm, or both well_size_x_mm and well_size_y_mm, is required")
        return self

    def to_settings(self) -> Dict[str, object]:
        spacing_x = self.well_spacing_x_mm if self.well_spacing_x_mm is not None else self.well_spacing_mm
        spacing_y = self.well_spacing_y_mm if self.well_spacing_y_mm is not None else self.well_spacing_mm
        size_x = self.well_size_x_mm if self.well_size_x_mm is not None else self.well_size_mm
        size_y = self.well_size_y_mm if self.well_size_y_mm is not None else self.well_size_mm
        return {
            "a1_x_mm": self.a1_x_mm,
            "a1_y_mm": self.a1_y_mm,
            "a1_x_pixel": self.a1_x_pixel,
            "a1_y_pixel": self.a1_y_pixel,
            # Scalar mirrors of the per-axis values for legacy readers; the
            # x value is the (arbitrary but documented) scalar representative.
            "well_size_mm": size_x,
            "well_spacing_mm": spacing_x,
            "number_of_skip": self.number_of_skip,
            "rows": self.rows,
            "cols": self.cols,
            "well_spacing_x_mm": spacing_x,
            "well_spacing_y_mm": spacing_y,
            "well_size_x_mm": size_x,
            "well_size_y_mm": size_y,
            "well_shape": self.well_shape,
        }


class UserSampleFormats(BaseModel):
    version: int = 1
    overrides: Dict[str, SampleFormatOverride] = Field(default_factory=dict)
    custom_formats: Dict[str, CustomSampleFormat] = Field(default_factory=dict)


def load_user_sample_formats(path: str = USER_SAMPLE_FORMATS_PATH) -> Optional[UserSampleFormats]:
    """None when absent (the identity default). Damage logs loudly and returns
    None rather than raising - this runs at import time via load_formats()."""
    return load_yaml_model(
        path,
        UserSampleFormats,
        f"User sample formats at {path} are unreadable; ignoring the file - "
        f"YOUR FORMAT EDITS AND CUSTOM FORMATS ARE NOT BEING APPLIED. "
        f"Fix or move the file aside to clear this.",
    )


def save_user_sample_formats(user_formats: UserSampleFormats, path: str = USER_SAMPLE_FORMATS_PATH) -> None:
    save_yaml_model_atomic(user_formats, path)


def apply_user_sample_formats(sample_formats: Dict[str, dict], user_formats: Optional[UserSampleFormats]) -> None:
    """Layer the user file onto the catalog, in place.

    Overrides apply per-field to existing formats; custom formats insert whole
    entries. An override naming an absent format warns (catalog changed, or a
    typo) and is skipped.
    """
    if user_formats is None:
        return
    # Custom formats insert FIRST so overrides can then edit them like any
    # other format - a user adjusting their own custom plate's spacing takes
    # the same per-field path as adjusting a shipped one. (The old order
    # silently dropped overrides that named a custom format.)
    for format_key, custom in user_formats.custom_formats.items():
        if format_key in sample_formats:
            log.warning(
                f"sample_formats_user.yaml custom format {format_key!r} shadows an existing catalog entry; "
                f"the custom definition wins."
            )
        sample_formats[format_key] = custom.to_settings()
    for format_key, override in user_formats.overrides.items():
        if format_key not in sample_formats:
            log.warning(f"sample_formats_user.yaml overrides {format_key!r}, which is not in the catalog; ignoring it.")
            continue
        sample_formats[format_key].update(override.applied_fields())
