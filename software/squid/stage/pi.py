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
