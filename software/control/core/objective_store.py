from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import control._def
from control.models.objective_calibration_config import (
    Mounting,
    ObjectiveCalibrationConfig,
    OffsetCameraKey,
    OffsetValidity,
    offset_validity,
    parfocal_residuals_um,
    xeryon_frame_um,
    z_frames_um,
)

UM_PER_MM = 1000.0


@dataclass(frozen=True)
class CalibrationChange:
    """What a calibration update changed (spec B §4.6 payload, extended by spec C §7.1): which
    quantities' effective values differ from before, and whether their validity flipped."""

    pixel_size: bool = False
    xy_offsets: bool = False
    z_offsets: bool = False
    validity_flipped: bool = False
    reason: str = ""  # the validity reason after the change, "" when everything is valid

    @property
    def any(self) -> bool:
        return self.pixel_size or self.xy_offsets or self.z_offsets


def default_xeryon_frame_mm(objective_name) -> float:
    """Today's Z frame of an objective (spec C §4), read from the live configuration: the Xeryon
    2-position switcher parks the stage XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM lower while a
    position-2 objective is in use (objective_changer_2_pos_controller.moveToPosition2). 0 for
    position-1 objectives, names not in the position lists, and machines without the switcher."""
    if not control._def.USE_XERYON:
        return 0.0
    if control._def.xeryon_objective_position(objective_name) == 2:
        return -float(control._def.XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM)
    return 0.0


def xeryon_pos2_offset_mm() -> float:
    """The changer's own mechanical Z, as the offset calibration subtracts it at use time (spec C §4)."""
    return float(control._def.XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM) if control._def.USE_XERYON else 0.0


def current_mountings(objective_names) -> Dict[str, Mounting]:
    """Every installed objective's mounting now (spec A's get_mounting plus its serial), for the
    validity check of spec C §4. Names unknown to the objective list are skipped."""
    mountings = {}
    for name in objective_names:
        try:
            changer, position = control._def.get_mounting(name)
            serial = control._def.get_declared(name)["serial"]
        except KeyError:
            continue
        mountings[name] = Mounting(changer=changer, position=position, serial=serial)
    return mountings


class ObjectiveStore:
    def __init__(self, objectives_dict=control._def.OBJECTIVES, default_objective=control._def.DEFAULT_OBJECTIVE):
        self.objectives_dict = objectives_dict
        self.default_objective = default_objective
        self.current_objective = default_objective
        objective = self.objectives_dict[self.current_objective]
        self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, control._def.TUBE_LENS_MM)
        # The saved objective offset calibration (spec C §7.1) and what it is evaluated against. Until
        # set_offset_calibration is called, nothing is calibrated and every frame is today's.
        self._offset_config: Optional[ObjectiveCalibrationConfig] = None
        self._offset_mountings: Dict[str, Mounting] = {}
        self._offset_camera_key: Optional[OffsetCameraKey] = None
        self._pos2_offset_mm: float = 0.0
        self.offset_validity = OffsetValidity(False, False, "not calibrated")
        self._xy_offsets_mm: Dict[str, Tuple[float, float]] = {}  # effective: {} unless XY is valid
        self._z_frames_mm: Dict[str, float] = {}  # every installed objective, once mountings are known
        self._z_residuals_mm: Dict[str, float] = {}  # frame minus the changer's own part; the switch step
        self._calibration_listeners: List[Callable[[CalibrationChange], None]] = []

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
            objective = self.objectives_dict[objective_name]
            self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, control._def.TUBE_LENS_MM)
        else:
            raise ValueError(f"Objective {objective_name} not found in the store.")

    def get_current_objective_info(self):
        return self.objectives_dict[self.current_objective]

    # ------------------------------------------------------------------ objective offsets (spec C §7.1)
    def add_calibration_listener(self, listener: Callable[[CalibrationChange], None]) -> None:
        """Called, on the thread that changed the calibration, whenever an effective value changes."""
        self._calibration_listeners.append(listener)

    def set_offset_calibration(
        self,
        config: Optional[ObjectiveCalibrationConfig],
        mountings: Dict[str, Mounting],
        camera_key: OffsetCameraKey,
        pos2_offset_mm: float,
    ) -> CalibrationChange:
        """Install the saved calibration (at load, after Apply and save, after Clear offsets) and
        evaluate its validity against the current mountings and camera key (spec C §4-5)."""
        self._offset_config = config
        self._offset_mountings = dict(mountings)
        self._offset_camera_key = camera_key
        self._pos2_offset_mm = float(pos2_offset_mm)
        return self._recompute_offsets()

    def refresh_offset_camera_key(self, camera_key: OffsetCameraKey) -> CalibrationChange:
        """Re-evaluate XY validity after the camera ROI or binning changed through the GUI (spec C §7.1)."""
        self._offset_camera_key = camera_key
        return self._recompute_offsets()

    def _recompute_offsets(self) -> CalibrationChange:
        config, mountings = self._offset_config, self._offset_mountings
        if config is None or self._offset_camera_key is None or not mountings:
            validity = OffsetValidity(False, False, "not calibrated")
        else:
            validity = offset_validity(config, mountings, self._offset_camera_key)
        pos2_um = self._pos2_offset_mm * UM_PER_MM
        # z_frames_um is only meaningful while the calibration is valid: an invalid one is not applied at
        # all (spec C §4), so every objective keeps today's frame and every residual is 0.
        z_config = config if validity.z else None
        frames_mm = {n: z / UM_PER_MM for n, z in z_frames_um(z_config, mountings, pos2_um).items()}
        residuals_mm = {n: z / UM_PER_MM for n, z in parfocal_residuals_um(z_config, mountings, pos2_um).items()}
        xy_mm: Dict[str, Tuple[float, float]] = {}
        if validity.xy:
            for name, entry in config.objectives.items():
                if entry.offset is not None and entry.offset.dx_um is not None and name in mountings:
                    xy_mm[name] = (entry.offset.dx_um / UM_PER_MM, entry.offset.dy_um / UM_PER_MM)
        change = CalibrationChange(
            xy_offsets=xy_mm != self._xy_offsets_mm,
            z_offsets=residuals_mm != self._z_residuals_mm,
            validity_flipped=(validity.z, validity.xy) != (self.offset_validity.z, self.offset_validity.xy),
            reason=validity.reason,
        )
        self.offset_validity = validity
        self._xy_offsets_mm = xy_mm
        self._z_frames_mm = frames_mm
        self._z_residuals_mm = residuals_mm
        if change.any:
            for listener in list(self._calibration_listeners):
                listener(change)
        return change

    def xy_offset_mm(self, objective_name) -> Tuple[float, float]:
        """offset_k of spec C §4 in mm: the stage move that keeps the same sample point centred under this
        objective instead of the reference. Zeros for the reference, an uncalibrated objective, or while XY
        is invalid."""
        return self._xy_offsets_mm.get(objective_name, (0.0, 0.0))

    def z_frame_mm(self, objective_name) -> float:
        """The objective's Z frame (spec C §4): the calibrated frame while Z is valid, else today's Xeryon frame."""
        if objective_name in self._z_frames_mm:
            return self._z_frames_mm[objective_name]
        if objective_name in self._offset_mountings:
            return xeryon_frame_um(self._offset_mountings[objective_name], self._pos2_offset_mm * UM_PER_MM) / UM_PER_MM
        return default_xeryon_frame_mm(objective_name)

    def z_switch_step_mm(self, old_objective, new_objective) -> float:
        """The Z move a switch old -> new owes after the changer's own mechanical move (spec C §7.2):
        (z_frame(new) - z_frame(old)) - (xeryon_frame(new) - xeryon_frame(old)), i.e. residual[new] -
        residual[old] from the C1 helper. 0 on every machine without a valid calibration."""
        return self._z_residuals_mm.get(new_objective, 0.0) - self._z_residuals_mm.get(old_objective, 0.0)
