"""
Squid stage support for the PI V-308 voice-coil focus drive on a C-414 controller.

Provides (1) ``C414FocusStage``, a GCS-2.0 driver over a serial (FTDI VCP) link, with
``pipython`` imported lazily so this module imports fine without it; (2) ``_SimulatedC414``,
a pure-Python stand-in for hardware-free / CI use; and (3) two ``squid.abc.AbstractStage``
adapters -- ``PIFocusStage`` (Z-only) and ``CombinedStage`` (XY delegate + V-308 Z).

Z is pure pass-through mm: the controller's native absolute mm is Squid's Z mm, with no sign
flip or offset and no use of ``Z_AXIS.MOVEMENT_SIGN`` (that is a Cephla-stepper calibration).
"""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Optional, Tuple

from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig

CONTROLLERNAME = "C-414"
CCL_PASSWORD = "advanced"
WPA_PASSWORD = "100"
PARAM_RANGE_LIMIT_MIN = 0x07000000
PARAM_RANGE_LIMIT_MAX = 0x07000001


def _import_pipython():
    """Import pipython on demand. Real-hardware path only; keeps module import light."""
    try:
        from pipython import GCSDevice, GCSError, pitools
    except ImportError as exc:
        raise ImportError(
            "The PI V-308 focus stage requires the optional 'pipython' package "
            "(pip install pipython); it is imported only when connecting to hardware."
        ) from exc
    return GCSDevice, GCSError, pitools


class _SimulatedC414:
    """In-memory stand-in for C414FocusStage: instant, always on-target, no pipython."""

    _LO_MM = -3.5
    _HI_MM = 3.5

    def __init__(self, axis: str = "1"):
        self.axis = axis
        self._pos_mm = 0.0
        self._referenced = False
        self._lo_mm = self._LO_MM
        self._hi_mm = self._HI_MM

    def connect_serial(self, *args, **kwargs):
        pass

    def initialize(self, reference: bool = True, **kwargs):
        if reference:
            self.reference()

    def is_referenced(self) -> bool:
        return self._referenced

    def reference(self, **kwargs):
        self._pos_mm = 0.0
        self._referenced = True

    def hardware_limits_mm(self) -> Tuple[float, float]:
        return (self._lo_mm, self._hi_mm)

    def set_travel_limits(self, min_mm: float, max_mm: float, persist: bool = False):
        self._lo_mm, self._hi_mm = float(min_mm), float(max_mm)

    def get_position_mm(self) -> float:
        return self._pos_mm

    def is_moving(self) -> bool:
        return False

    def on_target(self) -> bool:
        return True

    def move_to(self, z_mm: float, wait: bool = True, **kwargs) -> float:
        self._pos_mm = min(max(float(z_mm), self._lo_mm), self._hi_mm)
        return self._pos_mm

    def move_relative(self, dz_mm: float, wait: bool = True, **kwargs) -> float:
        return self.move_to(self._pos_mm + float(dz_mm), wait=wait)

    def stop(self):
        pass

    def close(self):
        pass


class PIFocusStage(AbstractStage):
    """Z-only AbstractStage backed by a C-414 / V-308. X / Y / theta are no-ops.

    Z is pure pass-through: the backend's native mm is Squid's Z mm (no sign/offset,
    no Z_AXIS.MOVEMENT_SIGN).
    """

    def __init__(self, c414, stage_config: Optional[StageConfig] = None):
        super().__init__(stage_config)
        self._c414 = c414

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._c414.move_relative(rel_mm, wait=blocking)

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self._c414.move_to(abs_mm, wait=blocking)

    def get_pos(self) -> Pos:
        return Pos(x_mm=0.0, y_mm=0.0, z_mm=self._c414.get_position_mm(), theta_rad=None)

    def get_state(self) -> StageStage:
        return StageStage(busy=self._c414.is_moving())

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if not z:
            return
        if blocking:
            self._c414.reference()
        else:
            threading.Thread(target=self._c414.reference, daemon=True, name="pi-z-home").start()

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if z:
            self._log.warning(
                "PIFocusStage.zero(z=True) is a no-op: the V-308 uses an absolute optical "
                "reference. Use home() to re-reference."
            )

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
        if z_pos_mm is not None and z_neg_mm is not None:
            self._c414.set_travel_limits(z_neg_mm, z_pos_mm)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._no_xy("move_x")

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._no_xy("move_y")

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._no_xy("move_x_to")

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._no_xy("move_y_to")

    def _no_xy(self, name: str):
        self._log.warning(f"{name} ignored: PIFocusStage is a Z-only focus drive (pair via CombinedStage).")


class CombinedStage(AbstractStage):
    """AbstractStage routing X / Y / theta to xy_stage and Z to z_stage (the V-308)."""

    def __init__(self, xy_stage: AbstractStage, z_stage: AbstractStage, stage_config: Optional[StageConfig] = None):
        super().__init__(stage_config or xy_stage.get_config())
        self._xy = xy_stage
        self._z = z_stage

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

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if x or y or theta:
            self._xy.home(x, y, False, theta, blocking)
        if z:
            self._z.home(False, False, True, False, blocking)

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
