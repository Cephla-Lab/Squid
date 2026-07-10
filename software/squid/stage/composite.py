"""Vendor-neutral composite stage: an XY stage plus a Z-only focus stage as one AbstractStage.

``CombinedStage`` routes X / Y / theta to the wrapped XY stage (Cephla or Prior) and Z to a
Z-only external focus stage (e.g. the PI V-308 in ``squid.stage.pi`` or the ASI LS50 in
``squid.stage.asi``). Consumers keep programming against the single ``AbstractStage``
interface; the informal contract a z_stage must satisfy is documented on the class.
"""

from __future__ import annotations

from typing import Optional

import squid.logging
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig

_log = squid.logging.get_logger(__name__)


class CombinedStage(AbstractStage):
    """AbstractStage routing X / Y / theta to xy_stage and Z to z_stage (an external Z-only focus stage, e.g. the V-308 or the ASI LS50)."""

    def __init__(self, xy_stage: AbstractStage, z_stage: AbstractStage, stage_config: Optional[StageConfig] = None):
        super().__init__(stage_config or xy_stage.get_config())
        self._xy = xy_stage
        self._z = z_stage
        self._scanning_position_z_mm = None  # set/read by squid.stage.utils loading/scanning flow

        # The GUI snaps Z step sizes through get_config().Z_AXIS (AutoFocus / multipoint) and via
        # z_mm_to_usteps (navigation). Present a Z axis whose resolution is the Z stage's own
        # (continuous ~10 nm) grid instead of the wrapped XY stepper grid, so Z-stack/autofocus
        # steps are not snapped to the coarse stepper microstep grid. Only the resolution fields
        # are overridden; range/speed/sign are preserved.
        z_usteps_per_mm = abs(self._z.z_mm_to_usteps(1.0)) if hasattr(self._z, "z_mm_to_usteps") else 0.0
        if z_usteps_per_mm:
            fine_z = self._config.Z_AXIS.model_copy(
                update={"SCREW_PITCH": 1.0, "MICROSTEPS_PER_STEP": 1, "FULL_STEPS_PER_REV": float(z_usteps_per_mm)}
            )
            self._config = self._config.model_copy(update={"Z_AXIS": fine_z})

    @property
    def z_stage(self) -> AbstractStage:
        """The wrapped Z-only stage (public: addons sharing its transport walk through here)."""
        return self._z

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._xy.move_x(rel_mm, blocking)

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._xy.move_y(rel_mm, blocking)

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._z.move_z(rel_mm, blocking)

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._xy.move_x_to(abs_mm, blocking)

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._xy.move_y_to(abs_mm, blocking)

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self._z.move_z_to(abs_mm, blocking)

    def get_pos(self) -> Pos:
        xy, z = self._xy.get_pos(), self._z.get_pos()
        return Pos(x_mm=xy.x_mm, y_mm=xy.y_mm, z_mm=z.z_mm, theta_rad=xy.theta_rad)

    def get_state(self) -> StageStage:
        return StageStage(busy=self._xy.get_state().busy or self._z.get_state().busy)

    def is_referenced(self) -> bool:
        return self._z.is_referenced()

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        xy_requested = x or y or theta
        if z:
            # Z must finish retracting before any XY sweep (e.g. the V-308 voice coil is not self-locking).
            self._z.home(False, False, True, False, blocking or xy_requested)
        if xy_requested:
            self._xy.home(x, y, False, theta, blocking)

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if x or y or theta:
            self._xy.zero(x, y, False, theta, blocking)
        if z:
            self._z.zero(False, False, True, False, blocking)

    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
        theta_pos_rad: Optional[float] = None,
        theta_neg_rad: Optional[float] = None,
    ):
        self._xy.set_limits(
            x_pos_mm=x_pos_mm,
            x_neg_mm=x_neg_mm,
            y_pos_mm=y_pos_mm,
            y_neg_mm=y_neg_mm,
            theta_pos_rad=theta_pos_rad,
            theta_neg_rad=theta_neg_rad,
        )
        self._z.set_limits(z_pos_mm=z_pos_mm, z_neg_mm=z_neg_mm)

    # The GUI (NavigationWidget.set_deltaX/Y/Z) calls these stepper-style helpers on the stage, so
    # the wrapper must expose them. X/Y come from the wrapped XY stage; Z comes from the external
    # Z stage's own grid, not the XY stepper grid.
    def x_mm_to_usteps(self, mm: float):
        return self._xy.x_mm_to_usteps(mm)

    def y_mm_to_usteps(self, mm: float):
        return self._xy.y_mm_to_usteps(mm)

    def z_mm_to_usteps(self, mm: float):
        return self._z.z_mm_to_usteps(mm)

    def close(self):
        self._z.close()  # the external Z stage's serial handle; Cephla/Prior XY close() is a no-op
        self._xy.close()
