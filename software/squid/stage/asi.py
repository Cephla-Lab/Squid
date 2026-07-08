"""
Squid stage support for the ASI LS50 Z-only linear stage on its own MS-2000-family controller.

Provides (1) ``MS2000Serial``, the CR-terminated ASI command transport; (2) ``LS50Controller``,
the single-axis driver; (3) ``_SimulatedLS50``, a pure-Python stand-in for hardware-free / CI
use; and (4) ``ASIZStage``, the Z-only ``squid.abc.AbstractStage`` adapter (pair with an XY
stage via ``squid.stage.pi.CombinedStage``).

Frame and units: the controller's native unit is 1/10 um (10000 per mm). Native 0 is the
power-on position, which by convention is the RETRACTED end; native positive is away from the
sample and the value goes negative as the stage approaches the sample. There is no absolute
reference or homing routine -- "home" simply retracts to native 0. Squid Z is the negation
(``squid_z = -native_z`` with the default ``invert_z=True`` wiring), so squid 0 is retracted
and squid Z increases toward the sample, matching the Cephla convention.

NOTE: branch ``dragonfly-andor`` adds an XYZ ``ASIStage`` at this same path. When merging that
branch, keep both: port ``ASIStage`` onto ``MS2000Serial`` below (its ``_send_command`` /
port-by-SN / ``:N``-check code duplicates this core).
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import suppress
from typing import Optional, Tuple

import squid.logging
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig

_log = squid.logging.get_logger(__name__)

STEPS_PER_MM = 10000  # ASI native unit = 0.1 um
# The protocol grid is the finest addressable step; the GUI's ustep-based Z step snapping uses
# this via z_mm_to_usteps (0.1 um is sub-slice for any real Z stack).
_Z_RESOLUTION_MM = 1e-4
_DEFAULT_MOVE_TIMEOUT_S = 30.0
_STATUS_POLL_PERIOD_S = 0.05


class MS2000Serial:
    """CR-terminated MS-2000 command transport: locked write/read with ':N' error-ack checking.

    Takes any pyserial-like object (write / read_until / close) so tests can inject a scripted
    fake; ``open()`` constructs the real ``serial.Serial``. This class is the shared ASI command
    core -- the dragonfly-andor ``ASIStage`` (XYZ) should adopt it when that branch merges.
    """

    def __init__(self, serial_conn):
        self._serial = serial_conn
        self._lock = threading.Lock()

    @classmethod
    def open(cls, port: str, baudrate: int = 115200, timeout_s: float = 0.5) -> "MS2000Serial":
        import serial  # lazy: real-hardware path only

        return cls(serial.Serial(port, baudrate=baudrate, timeout=timeout_s))

    def command(self, cmd: str, check_error: bool = True) -> str:
        """Send one command and return the stripped reply line.

        Raises RuntimeError on an ':N-<code>' error ack unless check_error=False (e.g. HALT,
        whose ':N-21' ack is expected).
        """
        with self._lock:
            self._serial.write(f"{cmd}\r".encode("ascii"))
            reply = self._serial.read_until(b"\n").decode("ascii", errors="ignore").strip()
        if check_error and reply.startswith(":N"):
            raise RuntimeError(f"MS-2000 error ack {reply!r} for command {cmd!r}")
        return reply

    def close(self):
        with suppress(Exception):
            self._serial.close()


class _SimulatedLS50:
    """In-memory stand-in for LS50Controller: instant moves, no serial.

    Mirrors the real backend's contract: until a fence is set the travel limits are unknown
    (native 0 is just the power-on position) and targets pass through unclamped; a fence set
    via set_travel_limits clamps. There is no referencing concept -- the power-on frame is
    always "valid".
    """

    def __init__(self):
        self._pos_mm = 0.0  # power-on zero
        self._lo_mm: Optional[float] = None
        self._hi_mm: Optional[float] = None
        self._closed = False
        self._zero_count = 0
        self._halt_count = 0

    def connect_serial(self, *args, **kwargs):
        pass

    def initialize(self):
        pass

    def is_referenced(self) -> bool:
        return True  # nothing to reference; position is always valid in the power-on frame

    def hardware_limits_mm(self) -> Tuple[Optional[float], Optional[float]]:
        return (self._lo_mm, self._hi_mm)

    def set_travel_limits(self, min_mm: float, max_mm: float):
        self._lo_mm, self._hi_mm = float(min_mm), float(max_mm)

    def get_position_mm(self) -> float:
        return self._pos_mm

    def is_moving(self) -> bool:
        return False

    def move_to(self, z_mm: float, wait: bool = True, **kwargs) -> float:
        target = float(z_mm)
        if self._lo_mm is not None:
            target = min(max(target, self._lo_mm), self._hi_mm)
        self._pos_mm = target
        return self._pos_mm

    def move_relative(self, dz_mm: float, wait: bool = True, **kwargs) -> float:
        return self.move_to(self._pos_mm + float(dz_mm), wait=wait)

    def zero_here(self):
        # 'H Z=0' capability: redefine the current position as native 0. Deliberately NOT
        # wired to AbstractStage.zero() -- see ASIZStage.zero().
        self._pos_mm = 0.0
        self._zero_count += 1

    def stop(self):
        self._halt_count += 1

    def close(self):
        self._closed = True
