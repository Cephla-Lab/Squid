"""Per-objective calibration records in machine_configs/objective_calibration.yaml.

AI-docs objective-pixel-size design §4.3-4.4 and objective-offset design §4-6.5. B owns the
`pixel_calibration` summary and every objective's `pixel_size` block; C1 owns `offset_calibration`
and every objective's `offset` block. Each save rewrites only its own section. Until B2 and C2,
nothing outside the calibration dialog reads this file.
"""

import math
from dataclasses import dataclass
from itertools import combinations
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


class Mounting(BaseModel):
    """Where an objective sat at measurement: spec A's get_mounting plus its serial (spec C §4)."""

    model_config = _STRICT
    changer: str  # nimotion_turret | xeryon | none
    position: Optional[int] = None
    serial: str = ""


class OffsetCameraKey(BaseModel):
    """The offset calibration's own, immutable camera key (spec C §5): B's saves never touch it.

    XY holds while the camera, its image transform and the ROI centre are unchanged. roi_centre_px is
    recorded only for camera drivers whose ROI units are verified (unbinned sensor pixels); then binning
    and ROI size are irrelevant. Without it, XY is keyed on the raw ROI and the binning instead, and
    any change to either invalidates XY: extra recalibration, never a false "valid"."""

    model_config = _STRICT
    camera: str
    binning: int = Field(ge=1)
    roi: List[int]  # (x, y, width, height) as the camera driver reports it
    roi_centre_px: Optional[List[float]] = None  # (x, y) in unbinned sensor pixels, when the units are verified
    image_transform: ImageTransform  # the rotation and flip applied to every frame

    @field_validator("roi")
    @classmethod
    def _four_values(cls, roi: List[int]) -> List[int]:
        if len(roi) != 4:
            raise ValueError("roi must be [x, y, width, height]")
        return roi

    @field_validator("roi_centre_px")
    @classmethod
    def _two_coordinates(cls, centre: Optional[List[float]]) -> Optional[List[float]]:
        if centre is not None and len(centre) != 2:
            raise ValueError("roi_centre_px must be [x, y]")
        return centre


class OffsetCalibrationSection(BaseModel):
    model_config = _STRICT
    reference_objective: str
    reference_mounting: Mounting
    measured_at: str
    channel: str
    cycles: int = Field(ge=1)
    camera_key: OffsetCameraKey


class OffsetRecord(BaseModel):
    model_config = _STRICT
    mounting: Mounting
    dx_um: Optional[float] = None  # absent when the orientation gate kept XY from being saved (spec C §5)
    dy_um: Optional[float] = None
    dz_um: float  # raw z_focus_k - z_focus_ref; the changer's own frame is subtracted at use time (spec C §4)
    std_dx_um: Optional[float] = Field(None, ge=0)
    std_dy_um: Optional[float] = Field(None, ge=0)
    std_dz_um: Optional[float] = Field(None, ge=0)
    match_score: float
    runner_up_ratio: float
    focus_peak_rise: float
    closure_error_um: Optional[float] = Field(None, ge=0)


class ObjectiveRecords(BaseModel):
    model_config = ConfigDict(extra="allow")  # later sections keep their own blocks next to these
    pixel_size: Optional[PixelSizeRecord] = None
    offset: Optional[OffsetRecord] = None

    @property
    def is_empty(self) -> bool:
        return self.pixel_size is None and self.offset is None and not self.model_extra


class PixelCalibrationSummary(BaseModel):
    model_config = _STRICT
    camera_rotation_deg: float
    orientation: Dict[str, str]
    orientation_matches_mosaic: bool


class ObjectiveCalibrationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # later sections are carried through
    version: Literal[1] = 1  # a newer file fails to load, so this version never overwrites it
    pixel_calibration: Optional[PixelCalibrationSummary] = None
    offset_calibration: Optional[OffsetCalibrationSection] = None
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
        if entry.is_empty:
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


# ----------------------------------------------------------------------------- C1: objective offsets


@dataclass(frozen=True)
class OffsetValidity:
    """Spec C §4-5: Z holds while the mountings hold; XY also needs the same camera key."""

    z: bool
    xy: bool
    reason: str = ""


def build_offset_records(
    summaries: Dict[str, object], mountings: Dict[str, Mounting], *, save_xy: bool
) -> Dict[str, OffsetRecord]:
    """`summaries` are the engine's OffsetSummary per non-reference objective. With save_xy False (the
    orientation gate, spec C §5) the blocks carry no dx/dy."""
    records = {}
    for name, s in summaries.items():
        records[name] = OffsetRecord(
            mounting=mountings[name],
            dx_um=s.dx_um if save_xy else None,
            dy_um=s.dy_um if save_xy else None,
            dz_um=s.dz_um,
            std_dx_um=s.std_dx_um if save_xy else None,
            std_dy_um=s.std_dy_um if save_xy else None,
            std_dz_um=s.std_dz_um,
            match_score=s.match_score,
            runner_up_ratio=s.runner_up_ratio,
            focus_peak_rise=s.focus_peak_rise,
            closure_error_um=s.closure_error_um,
        )
    return records


def merge_offset_records(
    config: Optional[ObjectiveCalibrationConfig],
    section: Optional[OffsetCalibrationSection],
    records: Dict[str, OffsetRecord],
) -> ObjectiveCalibrationConfig:
    """A copy with `offset_calibration` and EVERY objective's offset block replaced: a partial save across
    two references would mix frames (spec C §6.5). pixel_size blocks and other keys are kept."""
    merged = _copy(config)
    merged.offset_calibration = section
    for name in list(merged.objectives):
        merged.objectives[name].offset = None
        if merged.objectives[name].is_empty:
            del merged.objectives[name]
    for name, record in records.items():
        merged.objectives.setdefault(name, ObjectiveRecords()).offset = record
    return merged


def clear_offset_records(config: Optional[ObjectiveCalibrationConfig]) -> ObjectiveCalibrationConfig:
    """A copy without the offset section and every offset block; an objective left with no data is dropped."""
    return merge_offset_records(config, None, {})


def offset_camera_key(hardware) -> OffsetCameraKey:
    """The camera key of an offset calibration taken on this hardware now (any CalibrationHardware)."""
    rotate_deg, flip = hardware.image_transform()
    centre = hardware.roi_centre_px()
    return OffsetCameraKey(
        camera=hardware.camera_key(),
        binning=hardware.binning()[0],
        roi=list(hardware.roi()),
        roi_centre_px=list(centre) if centre is not None else None,
        image_transform=ImageTransform(rotate_deg=rotate_deg, flip=flip),
    )


def mounting_matches(recorded: Mounting, current: Mounting) -> bool:
    return (
        recorded.changer == current.changer
        and recorded.position == current.position
        and serial_matches(recorded.serial, current.serial)
    )


def offset_validity(
    config: Optional[ObjectiveCalibrationConfig], current_mountings: Dict[str, Mounting], camera: OffsetCameraKey
) -> OffsetValidity:
    """Spec C §4: any mounting mismatch, or a missing reference, invalidates the whole calibration.
    Spec C §5: XY is also invalid after a change of camera, image transform or ROI centre (binning and
    ROI size are irrelevant; with unverified ROI units, any ROI or binning change counts), and when
    the blocks were saved without XY."""
    section = config.offset_calibration if config is not None else None
    if section is None:
        return OffsetValidity(False, False, "not calibrated")
    reference = section.reference_objective
    if reference not in current_mountings:
        return OffsetValidity(False, False, f"reference objective {reference} is no longer installed")
    if not mounting_matches(section.reference_mounting, current_mountings[reference]):
        return OffsetValidity(False, False, f"reference objective {reference} was remounted since calibration")
    for name, entry in config.objectives.items():
        if entry.offset is None:
            continue
        if name not in current_mountings:
            return OffsetValidity(False, False, f"{name} is no longer installed")
        if not mounting_matches(entry.offset.mounting, current_mountings[name]):
            return OffsetValidity(False, False, f"{name} was remounted since calibration")
    reasons = []
    key = section.camera_key
    if camera.camera != key.camera:
        reasons.append(f"camera changed ({key.camera} -> {camera.camera})")
    if camera.image_transform != key.image_transform:
        reasons.append(TRANSFORM_CHANGED)
    if key.roi_centre_px is not None and camera.roi_centre_px is not None:
        if max(abs(a - b) for a, b in zip(key.roi_centre_px, camera.roi_centre_px)) > 0.5:
            reasons.append("camera ROI centre changed")
    elif (camera.roi, camera.binning) != (key.roi, key.binning):
        reasons.append("camera ROI or binning changed (this camera's ROI units are unverified)")
    if any(entry.offset is not None and entry.offset.dx_um is None for entry in config.objectives.values()):
        reasons.append("XY offsets were not saved (camera orientation did not match the mosaic)")
    return OffsetValidity(True, not reasons, "; ".join(reasons))


def xeryon_frame_um(mounting: Mounting, pos2_offset_um: float) -> float:
    """Today's Z frame of an objective (spec C §4): position-2 objectives on a Xeryon sit
    POS_2_OFFSET lower; everything else is 0."""
    return -pos2_offset_um if mounting.changer == "xeryon" and mounting.position == 2 else 0.0


def z_frames_um(
    config: Optional[ObjectiveCalibrationConfig], current_mountings: Dict[str, Mounting], pos2_offset_um: float
) -> Dict[str, float]:
    """Spec C §4: a calibrated objective's frame is xeryon_frame(the reference's RECORDED mounting) + dz;
    an uncalibrated one keeps today's frame. Only meaningful while the calibration is valid."""
    frames = {name: xeryon_frame_um(m, pos2_offset_um) for name, m in current_mountings.items()}
    section = config.offset_calibration if config is not None else None
    if section is None:
        return frames
    reference_frame = xeryon_frame_um(section.reference_mounting, pos2_offset_um)
    if section.reference_objective in frames:
        frames[section.reference_objective] = reference_frame
    for name, entry in config.objectives.items():
        if entry.offset is not None and name in frames:
            frames[name] = reference_frame + entry.offset.dz_um
    return frames


def parfocal_residuals_um(
    config: Optional[ObjectiveCalibrationConfig], current_mountings: Dict[str, Mounting], pos2_offset_um: float
) -> Dict[str, float]:
    """Each objective's frame minus the changer's own part: the switch step old -> new is
    residual[new] - residual[old] (spec C §7.2), and the engine centres its first focus with it."""
    frames = z_frames_um(config, current_mountings, pos2_offset_um)
    return {name: frames[name] - xeryon_frame_um(m, pos2_offset_um) for name, m in current_mountings.items()}


def implied_steps_um(
    config: Optional[ObjectiveCalibrationConfig], current_mountings: Dict[str, Mounting], pos2_offset_um: float
) -> Dict[Tuple[str, str], float]:
    """The switch step for every pair of installed objectives, in the order of current_mountings."""
    residuals = parfocal_residuals_um(config, current_mountings, pos2_offset_um)
    return {(a, b): residuals[b] - residuals[a] for a, b in combinations(current_mountings, 2)}


def step_cap_blockers(steps: Dict[Tuple[str, str], float], cap_um: float) -> List[str]:
    """Spec C §4: a calibration in which any pair implies a step over MAX_OBJECTIVE_Z_STEP_MM cannot be saved."""
    return [
        f"Implied Z step {abs(step) / 1000:.3f} mm between {a} and {b} exceeds the cap "
        f"({cap_um / 1000:g} mm); check the changer configuration."
        for (a, b), step in steps.items()
        if abs(step) > cap_um
    ]


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
    # exclude_none: an absent block or field reads back as None, and the file shows only what was measured
    data = config.model_dump(mode="json", exclude_none=True)
    write_atomically(path, yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
