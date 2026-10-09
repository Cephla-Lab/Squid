"""The objective in use and its pixel size.

The pixel-size seam (AI-docs objective-pixel-size design §4.4-4.5): get_pixel_size_factor() returns the
calibrated, binning-free factor for an objective whose saved pixel-size record is valid for the camera
and optics running now, and the nominal tube_lens_f_mm / magnification / TUBE_LENS_MM factor otherwise.
Consumers multiply it by the live binned sensor pixel, so a binning change needs no re-evaluation.
"""

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Tuple

import numpy as np

import control._def
import squid.logging
from control.models.objective_calibration_config import (
    CurrentSetup,
    ObjectiveCalibrationConfig,
    PixelSizeRecord,
    Validity,
    pixel_size_validity,
)
from squid.objective_calibration.pixel_size import decompose

PIXEL_SIZE = "pixel_size"  # the quantity B saves; C adds "xy_offsets" and "z_offsets"


@dataclass(frozen=True)
class CalibrationChange:
    """The calibration-changed notification's payload (spec B §4.6): which quantities' effective values
    changed for any objective, and whether any objective's validity flipped."""

    quantities: FrozenSet[str]
    validity_flipped: bool


class ObjectiveStore:
    def __init__(
        self,
        objectives_dict=control._def.OBJECTIVES,
        default_objective=control._def.DEFAULT_OBJECTIVE,
        *,
        calibration: Optional[ObjectiveCalibrationConfig] = None,
        current_setup: Optional[Callable[[], CurrentSetup]] = None,
        get_declared: Optional[Callable[[str], dict]] = None,
        binned_sensor_pixel_um: Optional[Callable[[], float]] = None,
    ):
        """Without `calibration` and `current_setup` (tests, tools) every objective is nominal.

        current_setup: what the machine runs now (camera key, sensor pixel, TUBE_LENS_MM, image
            transform), read at every validity evaluation.
        get_declared: the declared optics of an objective (spec A §4.5; raises KeyError).
        binned_sensor_pixel_um: the camera's live binned pixel, for pixel_matrix().
        """
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.objectives_dict = objectives_dict
        self.default_objective = default_objective
        self.current_objective = default_objective
        self._current_setup = current_setup
        self._get_declared = get_declared or control._def.get_declared
        self._binned_sensor_pixel_um = binned_sensor_pixel_um
        self._calibration: Optional[ObjectiveCalibrationConfig] = None
        self._records: Dict[str, PixelSizeRecord] = {}
        self._validity: Dict[str, Validity] = {}
        self._warnings: List[str] = []
        self._setup_error = ""
        self.pixel_size_factor = self._nominal_factor(self.current_objective)
        self.set_calibration(calibration)

    # ---------------------------------------------------------------- the seam
    def get_pixel_size_factor(self):
        return self.pixel_size_factor

    @staticmethod
    def calculate_pixel_size_factor(objective, tube_lens_mm):
        """pixel_size_um = sensor_pixel_size * binning_factor * lens_factor"""
        magnification = objective["magnification"]
        objective_tube_lens_mm = objective["tube_lens_f_mm"]
        lens_factor = objective_tube_lens_mm / magnification / tube_lens_mm
        return lens_factor

    def set_current_objective(self, objective_name):
        if objective_name in self.objectives_dict:
            self.current_objective = objective_name
            self.pixel_size_factor = self._effective_factor(objective_name)
        else:
            raise ValueError(f"Objective {objective_name} not found in the store.")

    def get_current_objective_info(self):
        return self.objectives_dict[self.current_objective]

    def pixel_size_source(self, name: str) -> str:
        """ "calibrated" when the objective's saved scalar is valid, "nominal" otherwise."""
        return "calibrated" if self._scalar_valid(name) else "nominal"

    def pixel_calibration_measured_at(self, name: str) -> Optional[str]:
        """When the calibration in use was measured; None for a nominal pixel size."""
        return self._records[name].measured_at if self._scalar_valid(name) else None

    def pixel_matrix(self, name: str) -> Optional[np.ndarray]:
        """The pixel->stage matrix for the current image pixels (um per binned pixel): matrix_norm x
        the live binned sensor pixel. None when the directional part is invalid or uncalibrated."""
        validity = self._validity.get(name)
        if validity is None or not validity.directional or self._binned_sensor_pixel_um is None:
            return None
        return np.asarray(self._records[name].matrix_norm, dtype=float) * self._binned_sensor_pixel_um()

    def orientation_matches_mosaic(self) -> Optional[bool]:
        """Whether the valid matrices share the mosaic's orientation (F = I); None without any."""
        flips = [
            decompose(np.asarray(self._records[name].matrix_norm, dtype=float))[2]
            for name, validity in self._validity.items()
            if validity.directional
        ]
        if not flips:
            return None
        return all(np.array_equal(flip, np.eye(2)) for flip in flips)

    def invalid_calibrations(self) -> Dict[str, str]:
        """Objectives with a saved record that is not (fully) valid, with the reason."""
        return {
            name: validity.reason
            for name, validity in self._validity.items()
            if not (validity.scalar and validity.directional)
        }

    def calibration_warnings(self) -> List[str]:
        """One line per saved record not (fully) in use, as logged at the last evaluation (spec B §4.4:
        reported loudly; the GUI shows them once at startup)."""
        return list(self._warnings)

    # ---------------------------------------------------------------- validity triggers
    def set_calibration(self, calibration: Optional[ObjectiveCalibrationConfig]) -> CalibrationChange:
        """Replace the saved calibration (after a load, save or clear) and re-evaluate validity."""
        self._calibration = calibration
        return self.refresh_validity()

    def refresh_validity(self) -> CalibrationChange:
        """Re-evaluate every record against the current setup (spec B §4.4 triggers)."""
        before = self._effective_snapshot()
        self._evaluate()
        self.pixel_size_factor = self._effective_factor(self.current_objective)
        after = self._effective_snapshot()
        absent = (None, None, False, False)
        names = set(before) | set(after)
        changed = any(before.get(n, absent) != after.get(n, absent) for n in names)
        flipped = any(before.get(n, absent)[2:] != after.get(n, absent)[2:] for n in names)
        return CalibrationChange(frozenset({PIXEL_SIZE}) if changed else frozenset(), flipped)

    # ---------------------------------------------------------------- internals
    def _nominal_factor(self, name: str) -> float:
        return ObjectiveStore.calculate_pixel_size_factor(self.objectives_dict[name], control._def.TUBE_LENS_MM)

    def _scalar_valid(self, name: str) -> bool:
        validity = self._validity.get(name)
        return validity is not None and validity.scalar

    def _effective_factor(self, name: str) -> float:
        return self._records[name].factor if self._scalar_valid(name) else self._nominal_factor(name)

    def _effective_snapshot(self) -> Dict[str, Tuple]:
        """(factor, matrix_norm, scalar valid, directional valid) per objective, for change detection."""
        snapshot = {}
        for name in set(self.objectives_dict) | set(self._records):
            validity = self._validity.get(name, Validity(False, False))
            record = self._records.get(name)
            matrix = tuple(map(tuple, record.matrix_norm)) if record is not None and validity.directional else None
            factor = self._effective_factor(name) if name in self.objectives_dict else None
            snapshot[name] = (factor, matrix, validity.scalar, validity.directional)
        return snapshot

    def _evaluate(self) -> None:
        self._records = {}
        self._validity = {}
        self._warnings = []
        if self._calibration is None:
            return
        records = {
            name: entry.pixel_size
            for name, entry in self._calibration.objectives.items()
            if entry.pixel_size is not None
        }
        if not records:
            return
        self._records = records
        setup = self._setup_or_none()
        for name, record in records.items():
            if setup is None:
                validity = Validity(False, False, self._setup_error)
            else:
                validity = pixel_size_validity(record, setup, self._declared(name))
            self._validity[name] = validity
            if validity.scalar and validity.directional:
                continue
            if validity.scalar:
                message = f"{name}: pixel size in use, XY matrix not ({validity.reason})"
            else:
                message = f"{name}: nominal pixel size in use ({validity.reason})"
            self._warnings.append(message)
            self._log.warning(f"Pixel-size calibration: {message}")

    def _setup_or_none(self) -> Optional[CurrentSetup]:
        """None, with the reason in _setup_error, when the machine's setup cannot be read: without an
        injected provider (nominal-only use), or when the camera has no known sensor pixel size
        (control/camera.py raises NotImplementedError for unknown models)."""
        if self._current_setup is None:
            self._setup_error = "no camera setup available to validate the calibration against"
            return None
        try:
            return self._current_setup()
        except Exception as e:  # noqa: BLE001 - an uncalibrated machine must keep starting
            self._setup_error = f"camera setup unavailable ({e})"
            self._log.error(f"Cannot validate the pixel-size calibration: {e}")
            return None

    def _declared(self, name: str) -> Optional[dict]:
        try:
            return self._get_declared(name)
        except KeyError:  # spec A §4.5: an unknown name raises, never a default
            return None
