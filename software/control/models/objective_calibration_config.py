"""Per-objective calibration records in machine_configs/objective_calibration.yaml.

AI-docs objective-pixel-size design §4.3-4.4. B owns the `pixel_calibration` summary and every
objective's `pixel_size` block. Every other key (C's offset data) is carried through B's saves
untouched. Until B2, nothing outside the calibration dialog reads this file.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Tuple

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from control.atomic_file import write_atomically
from control.objectives_config import serial_matches
from squid.objective_calibration.pixel_size import decompose

MAX_NOMINAL_DEVIATION = 0.10
MAX_ROTATION_SPREAD_DEG = 0.3
TRANSFORM_CHANGED = "camera image rotation/flip changed since calibration; recalibrate for XY"


class ObjectiveCalibrationFileError(Exception):
    """objective_calibration.yaml exists but cannot be read. It must never be overwritten: it may hold C's data."""


# Every record rejects NaN and infinity: a damaged file must fail to load (and so is never
# overwritten), not load and crash later in the summary or the seam.
_STRICT = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImageTransform(BaseModel):
    model_config = _STRICT
    rotate_deg: Optional[float] = None  # CAMERA_CONFIG.ROTATE_IMAGE_ANGLE
    flip: Optional[str] = None  # CAMERA_CONFIG.FLIP_IMAGE, as the FlipVariant value


class DeclaredOptics(BaseModel):
    model_config = _STRICT
    magnification: float = Field(gt=0)
    tube_lens_f_mm: float = Field(gt=0)
    serial: str = ""


class PixelSizeRecord(BaseModel):
    model_config = _STRICT
    measured_at: str
    cycles: int = Field(ge=1)
    camera_key: str
    unbinned_sensor_pixel_um: float = Field(gt=0)
    binning: int = Field(ge=1)
    image_transform: ImageTransform
    tube_lens_mm: float = Field(gt=0)
    declared: DeclaredOptics
    factor: float = Field(gt=0)  # measured um/px / binned sensor um/px at calibration: binning-free
    matrix_norm: List[List[float]]  # M / binned sensor um/px at calibration: binning-free
    pixel_size_um: float = Field(gt=0)  # at the calibration binning, for display
    std_pixel_size_um: Optional[float] = Field(None, ge=0)
    anisotropy: float = Field(gt=0)
    rotation_deg: float
    fit_residual_um: float = Field(ge=0)

    @field_validator("matrix_norm")
    @classmethod
    def _invertible_2x2(cls, matrix: List[List[float]]) -> List[List[float]]:
        if len(matrix) != 2 or any(len(row) != 2 for row in matrix):
            raise ValueError("matrix_norm must be 2x2")
        if abs(np.linalg.det(np.asarray(matrix))) < 1e-12:
            raise ValueError("matrix_norm is singular")
        return matrix


class ObjectiveRecords(BaseModel):
    model_config = ConfigDict(extra="allow")  # C1 keeps its own blocks next to pixel_size
    pixel_size: Optional[PixelSizeRecord] = None


class PixelCalibrationSummary(BaseModel):
    model_config = _STRICT
    camera_rotation_deg: float
    orientation: Dict[str, str]
    orientation_matches_mosaic: bool


class ObjectiveCalibrationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # C's offset_calibration section is carried through
    version: Literal[1] = 1  # a newer file fails to load, so this version never overwrites it
    pixel_calibration: Optional[PixelCalibrationSummary] = None
    objectives: Dict[str, ObjectiveRecords] = Field(default_factory=dict)


@dataclass(frozen=True)
class CurrentSetup:
    """What the machine runs now; each record is compared against it (spec B §4.4)."""

    camera_key: str
    unbinned_sensor_pixel_um: float
    tube_lens_mm: float
    image_transform: ImageTransform


@dataclass(frozen=True)
class Validity:
    scalar: bool
    directional: bool
    reason: str = ""  # why a part is invalid; "" when both are valid


def _copy(config: Optional[ObjectiveCalibrationConfig]) -> ObjectiveCalibrationConfig:
    if config is None:
        return ObjectiveCalibrationConfig()
    return ObjectiveCalibrationConfig.model_validate(config.model_dump(mode="json"))


def build_pixel_record(
    summary,
    *,
    measured_at: str,
    camera_key: str,
    unbinned_sensor_pixel_um: float,
    binned_sensor_pixel_um: float,
    binning: int,
    image_transform: ImageTransform,
    tube_lens_mm: float,
    declared: dict,
) -> PixelSizeRecord:
    """`summary` is the engine's PixelSizeSummary. factor and matrix_norm are divided by the binned
    sensor pixel at measurement time, so they hold at any binning."""
    return PixelSizeRecord(
        measured_at=measured_at,
        cycles=summary.cycles,
        camera_key=camera_key,
        unbinned_sensor_pixel_um=unbinned_sensor_pixel_um,
        binning=binning,
        image_transform=image_transform,
        tube_lens_mm=tube_lens_mm,
        declared=DeclaredOptics(
            magnification=declared["magnification"],
            tube_lens_f_mm=declared["tube_lens_f_mm"],
            serial=declared["serial"],
        ),
        factor=summary.pixel_size_um / binned_sensor_pixel_um,
        matrix_norm=(np.asarray(summary.matrix_um_per_px, dtype=float) / binned_sensor_pixel_um).tolist(),
        pixel_size_um=summary.pixel_size_um,
        std_pixel_size_um=summary.std_pixel_size_um,
        anisotropy=summary.anisotropy,
        rotation_deg=summary.rotation_deg,
        fit_residual_um=summary.fit_residual_um,
    )


def merge_pixel_records(
    config: Optional[ObjectiveCalibrationConfig], records: Dict[str, PixelSizeRecord]
) -> ObjectiveCalibrationConfig:
    """A copy with only these objectives' pixel_size blocks replaced; every other key is kept."""
    merged = _copy(config)
    for name, record in records.items():
        merged.objectives.setdefault(name, ObjectiveRecords()).pixel_size = record
    return merged


def clear_pixel_records(
    config: Optional[ObjectiveCalibrationConfig], names: Iterable[str]
) -> ObjectiveCalibrationConfig:
    """A copy without these objectives' pixel_size blocks. An objective left with no data is dropped."""
    cleared = _copy(config)
    for name in names:
        entry = cleared.objectives.get(name)
        if entry is None:
            continue
        entry.pixel_size = None
        if not entry.model_extra:
            del cleared.objectives[name]
    return cleared


def pixel_size_validity(record: Optional[PixelSizeRecord], setup: CurrentSetup, declared: Optional[dict]) -> Validity:
    """Spec B §4.4: the scalar holds while the camera, sensor pixel, tube lens and declared optics are
    unchanged (the serial under spec A's rule); the matrix also needs the same image transform."""
    if record is None:
        return Validity(False, False, "not calibrated")
    if declared is None:
        return Validity(False, False, "objective is not in the objective list")
    reasons = []
    if record.camera_key != setup.camera_key:
        reasons.append(f"camera changed ({record.camera_key} -> {setup.camera_key})")
    if not math.isclose(record.unbinned_sensor_pixel_um, setup.unbinned_sensor_pixel_um, rel_tol=1e-9):
        reasons.append("camera sensor pixel size changed")
    if not math.isclose(record.tube_lens_mm, setup.tube_lens_mm, rel_tol=1e-9):
        reasons.append("TUBE_LENS_MM changed")
    if not (
        math.isclose(record.declared.magnification, declared["magnification"], rel_tol=1e-9)
        and math.isclose(record.declared.tube_lens_f_mm, declared["tube_lens_f_mm"], rel_tol=1e-9)
    ):
        reasons.append("declared magnification or tube lens changed")
    if not serial_matches(record.declared.serial, declared["serial"]):
        reasons.append("objective serial changed")
    if reasons:
        return Validity(False, False, "; ".join(reasons))
    if record.image_transform != setup.image_transform:
        return Validity(True, False, TRANSFORM_CHANGED)
    return Validity(True, True)


def _valid_matrices(
    config: ObjectiveCalibrationConfig, setup: CurrentSetup, declared_by_name: Dict[str, dict]
) -> Dict[str, PixelSizeRecord]:
    return {
        name: entry.pixel_size
        for name, entry in config.objectives.items()
        if pixel_size_validity(entry.pixel_size, setup, declared_by_name.get(name)).directional
    }


def _flip(record: PixelSizeRecord) -> np.ndarray:
    return decompose(np.asarray(record.matrix_norm, dtype=float))[2]


def _orientation(flip: np.ndarray) -> Dict[str, str]:
    """Which stage direction each image axis runs along, e.g. {"col": "+x", "row": "-y"}."""
    labels = {}
    for j, image_axis in enumerate(("col", "row")):
        i = int(np.argmax(np.abs(flip[:, j])))
        labels[image_axis] = ("+" if flip[i, j] > 0 else "-") + "xy"[i]
    return labels


def with_summary(
    config: ObjectiveCalibrationConfig, setup: CurrentSetup, declared_by_name: Dict[str, dict]
) -> ObjectiveCalibrationConfig:
    """A copy with pixel_calibration recomputed from the valid blocks: None when none is valid or
    they disagree on F (review_save blocks that save)."""
    out = _copy(config)
    valid = _valid_matrices(out, setup, declared_by_name)
    flips = [_flip(record) for record in valid.values()]
    if not flips or any(not np.array_equal(f, flips[0]) for f in flips):
        out.pixel_calibration = None
        return out
    out.pixel_calibration = PixelCalibrationSummary(
        camera_rotation_deg=float(np.mean([record.rotation_deg for record in valid.values()])),
        orientation=_orientation(flips[0]),
        orientation_matches_mosaic=bool(np.array_equal(flips[0], np.eye(2))),
    )
    return out


def review_save(
    merged: ObjectiveCalibrationConfig,
    measured_px_um: Dict[str, float],
    nominal_px_um: Dict[str, float],
    setup: CurrentSetup,
    declared_by_name: Dict[str, dict],
) -> Tuple[List[str], List[str]]:
    """Save blockers and warnings for a merged config (spec B §5.4)."""
    blockers, warnings = [], []
    for name, px in measured_px_um.items():
        deviation = px / nominal_px_um[name] - 1.0
        if abs(deviation) > MAX_NOMINAL_DEVIATION:
            blockers.append(
                f"Calibrated pixel size for {name} differs from nominal by {deviation:+.1%}; check the objective list."
            )
    valid = _valid_matrices(merged, setup, declared_by_name)
    flips = [_flip(record) for record in valid.values()]
    if any(not np.array_equal(f, flips[0]) for f in flips):
        blockers.append("Objectives disagree on camera orientation; this indicates a configuration error.")
    rotations = [record.rotation_deg for record in valid.values()]
    if len(rotations) > 1 and max(rotations) - min(rotations) > MAX_ROTATION_SPREAD_DEG:
        warnings.append(
            f"Camera rotation differs between objectives by {max(rotations) - min(rotations):.2f}° "
            f"(over {MAX_ROTATION_SPREAD_DEG}°); the saved camera angle is their mean."
        )
    return blockers, warnings


def load_objective_calibration(path) -> Optional[ObjectiveCalibrationConfig]:
    """None if the file is absent. Raises ObjectiveCalibrationFileError if it exists but cannot be read."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ObjectiveCalibrationConfig.model_validate(data)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError) as e:
        raise ObjectiveCalibrationFileError(
            f"{path} cannot be read ({e}). Fix or delete it; calibration will not overwrite it."
        ) from e


def save_objective_calibration(config: ObjectiveCalibrationConfig, path) -> None:
    """Publish through write_atomically: a crash or power cut leaves either the old file or the new
    one, never an empty or partial file, and a failed publish leaves no temp file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False).encode("utf-8"))
