"""User sample formats: the lab's own plate definitions, complete.

Lives in objective_and_sample_formats/sample_formats_user.yaml, next to the
shipped catalog it REPLACES entries in - NOT machine_configs/ (that directory
describes the microscope hardware; a lab's plate catalog is not the
instrument).

The design rule (owner decision, 2026-08-16): **shipped formats are examples.**
The moment a lab calibrates or edits a format - shipped or their own - the
result is stored here as a COMPLETE definition that replaces the example
wholesale. There are no sparse overrides and no separate placement sidecar:
one entry per format, holding everything about that plate including its
measured A1.

Consequences, deliberate:

* Every format is handled identically. "Calibrate Existing Format" and "Add
  New Format" write the same kind of entry; the only difference is whether the
  starting values came from an example or from the user.
* This file is the portable one. Copy it to another machine and every format
  arrives complete - geometry and A1 together. What must NOT travel lives in
  machine_configs/plate_holder.yaml: the holder rotation, which is bolted to
  one instrument.
* A calibrated format stops tracking shipped catalog updates. That is the
  point: a measurement outranks an example.

Provenance rides along in ``measured``: the raw points and timestamp behind
the stored A1. Its presence is also what suppresses the legacy
WELLPLATE_OFFSET for that format, so exactly one correction is ever live.
"""

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from control.models.yaml_store import load_yaml_model, save_yaml_model_atomic

import squid.logging

log = squid.logging.get_logger(__name__)

USER_SAMPLE_FORMATS_PATH = os.path.join("objective_and_sample_formats", "sample_formats_user.yaml")


class MeasuredPoint(BaseModel):
    well: str
    x_mm: float
    y_mm: float


class FormatMeasurement(BaseModel):
    """Raw facts behind a stored A1 (and rotation, when measured per format).

    Derived numbers are never stored - they are recomputed from the points.
    """

    points: List[MeasuredPoint] = Field(default_factory=list)
    method: str = ""  # "3 edge points" | "center point" | "migrated" | ...
    timestamp: str = ""
    note: str = ""


class SampleFormat(BaseModel):
    """A complete plate definition. Replaces the shipped example, if any."""

    rows: int = Field(..., ge=1)
    cols: int = Field(..., ge=1)
    a1_x_mm: float = 0.0
    a1_y_mm: float = 0.0
    a1_x_pixel: int = 0
    a1_y_pixel: int = 0
    # Scalar OR per-axis; the validator requires exactly one form of each.
    well_spacing_mm: Optional[float] = Field(None, gt=0)
    well_spacing_x_mm: Optional[float] = Field(None, gt=0)
    well_spacing_y_mm: Optional[float] = Field(None, gt=0)
    well_size_mm: Optional[float] = Field(None, gt=0)
    well_size_x_mm: Optional[float] = Field(None, gt=0)
    well_size_y_mm: Optional[float] = Field(None, gt=0)
    well_shape: str = Field("circle", pattern="^(circle|rectangle)$")
    number_of_skip: int = Field(0, ge=0)
    # Per-format rotation override: null/absent => inherit the holder angle.
    # When set it is the ABSOLUTE total angle for this format, never a delta.
    rotation_deg: Optional[float] = None
    # Provenance, one block per measurement - they come from different
    # gestures and must not overwrite each other: `measured` is the A1 touch,
    # `rotation_measured` the multi-well fit behind rotation_deg.
    measured: Optional[FormatMeasurement] = None
    rotation_measured: Optional[FormatMeasurement] = None

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
        if self.rotation_deg is not None and (self.rotation_measured is None or len(self.rotation_measured.points) < 2):
            # Same no-arbitrary-numbers rule the holder record enforces: an
            # angle needs the multi-well fit behind it, and a single A1 touch
            # (which lands in `measured`) cannot produce one.
            raise ValueError(
                "rotation_deg is set but 'rotation_measured.points' is missing - "
                "an angle with no provenance is rejected"
            )
        return self

    @staticmethod
    def from_settings(settings: Dict[str, object], **extra) -> "SampleFormat":
        """Build a complete definition from a resolved settings dict.

        The path every calibration takes: start from what the app currently
        knows about the format (shipped example or previous user entry),
        overlay the newly measured values, store the whole thing.
        """
        fields = {
            "rows": settings["rows"],
            "cols": settings["cols"],
            "a1_x_mm": settings["a1_x_mm"],
            "a1_y_mm": settings["a1_y_mm"],
            "a1_x_pixel": settings.get("a1_x_pixel", 0),
            "a1_y_pixel": settings.get("a1_y_pixel", 0),
            "well_spacing_x_mm": settings.get("well_spacing_x_mm", settings.get("well_spacing_mm")),
            "well_spacing_y_mm": settings.get("well_spacing_y_mm", settings.get("well_spacing_mm")),
            "well_size_x_mm": settings.get("well_size_x_mm", settings.get("well_size_mm")),
            "well_size_y_mm": settings.get("well_size_y_mm", settings.get("well_size_mm")),
            "well_shape": settings.get("well_shape", "circle"),
            "number_of_skip": settings.get("number_of_skip", 0),
        }
        fields.update(extra)
        return SampleFormat(**fields)

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
    version: int = 2  # v1 carried overrides + custom_formats; v2 is one map
    formats: Dict[str, SampleFormat] = Field(default_factory=dict)


def load_user_sample_formats(path: str = USER_SAMPLE_FORMATS_PATH) -> Optional[UserSampleFormats]:
    """None when absent (shipped examples only). Damage logs loudly and returns
    None rather than raising - this runs at import time via load_formats()."""
    return load_yaml_model(
        path,
        UserSampleFormats,
        f"User sample formats at {path} are unreadable; ignoring the file - "
        f"YOUR FORMAT DEFINITIONS AND CALIBRATIONS ARE NOT BEING APPLIED. "
        f"Fix or move the file aside to clear this.",
    )


def save_user_sample_formats(user_formats: UserSampleFormats, path: str = USER_SAMPLE_FORMATS_PATH) -> None:
    save_yaml_model_atomic(user_formats, path)


def apply_user_sample_formats(sample_formats: Dict[str, dict], user_formats: Optional[UserSampleFormats]) -> None:
    """Layer the user file onto the shipped examples, in place.

    A user entry REPLACES the example wholesale - no per-field merging. That is
    the whole point: what the lab measured or authored is the definition, and
    the shipped row was only ever a starting example.
    """
    if user_formats is None:
        return
    for format_key, definition in user_formats.formats.items():
        sample_formats[format_key] = definition.to_settings()


def is_measured(format_key: str, user_formats: Optional[UserSampleFormats]) -> bool:
    """Whether this format's A1 was measured on this machine.

    The legacy WELLPLATE_OFFSET applies only to formats where it was NOT, so
    exactly one correction is live per format and a double-apply is
    unrepresentable.
    """
    if user_formats is None:
        return False
    definition = user_formats.formats.get(format_key)
    return definition is not None and definition.measured is not None
