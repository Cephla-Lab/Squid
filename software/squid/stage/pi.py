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
import time
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


class C414FocusStage:
    """Single-axis closed-loop focus drive (V-308 on a C-414), GCS 2.0 via pipython.

    SAFETY: the voice coil has no self-locking. ``reference()`` and ``autozero()`` MOVE the
    stage -- run them with the objective clear of the sample.
    """

    def __init__(self, axis: str = "1"):
        GCSDevice, GCSError, pitools = _import_pipython()
        self._GCSError = GCSError
        self._pitools = pitools
        self.gcs = GCSDevice(CONTROLLERNAME)
        self.axis = axis

    # --- connection ----------------------------------------------------------
    def connect_serial(self, comport, baudrate: int = 115200) -> None:
        """Connect over the FTDI virtual COM port (115200 8-N-1) -- the default path."""
        self.gcs.ConnectRS232(comport=comport, baudrate=baudrate)
        self._after_connect()

    def connect_tcpip(self, ipaddress: str, ipport: int = 50000) -> None:
        self.gcs.ConnectTCPIP(ipaddress=ipaddress, ipport=ipport)
        self._after_connect()

    def connect_usb(self, serialnum: Optional[str] = None) -> None:
        """Connect over USB via PI's GCS DLL (requires the PI software install)."""
        if serialnum is None:
            found = self.gcs.EnumerateUSB(mask=CONTROLLERNAME)
            if not found:
                raise RuntimeError("No C-414 found on USB.")
            serialnum = found[0].split()[-1]
        self.gcs.ConnectUSB(serialnum=serialnum)
        self._after_connect()

    def _after_connect(self) -> None:
        if self.axis not in self.gcs.axes:
            self.axis = self.gcs.axes[0]

    # --- bring-up ------------------------------------------------------------
    def initialize(self, reference: bool = True, ref_timeout: float = 60.0) -> None:
        """Enable closed loop and (optionally) reference the axis (referencing MOVES it)."""
        self.gcs.RON(self.axis, [True])
        self.gcs.SVO(self.axis, [True])
        if reference and not self.is_referenced():
            self.reference(timeout=ref_timeout)

    def is_referenced(self) -> bool:
        return bool(self.gcs.qFRF(self.axis)[self.axis])

    def reference(self, timeout: float = 60.0) -> None:
        """Reference move to the optical reference switch (MOVES the stage)."""
        self.gcs.FRF(self.axis)
        self._pitools.waitonreferencing(self.gcs, axes=self.axis, timeout=timeout)
        if not self.is_referenced():
            raise RuntimeError("Reference move did not complete.")

    def autozero(self, low_mm: float, timeout: float = 60.0) -> None:
        """Compensate residual weight force so servo-off is safe (vertical mount; MOVES)."""
        if not self.is_referenced():
            raise RuntimeError("Axis must be referenced before autozero.")
        self.gcs.ATZ(self.axis, [float(low_mm)])
        self._pitools.waitonready(self.gcs, timeout=timeout)
        if not bool(self.gcs.qATZ(self.axis)[self.axis]):
            raise RuntimeError("Autozero did not succeed.")

    # --- limits / config -----------------------------------------------------
    def hardware_limits_mm(self) -> Tuple[float, float]:
        return self.gcs.qTMN(self.axis)[self.axis], self.gcs.qTMX(self.axis)[self.axis]

    def set_travel_limits(self, min_mm: float, max_mm: float, persist: bool = False) -> None:
        """Fence the reachable Z range (Position Range Limit min/max). Requires command level 1."""
        self.gcs.CCL(1, CCL_PASSWORD)
        self.gcs.SPA(self.axis, PARAM_RANGE_LIMIT_MIN, min_mm)
        self.gcs.SPA(self.axis, PARAM_RANGE_LIMIT_MAX, max_mm)
        if persist:
            self.gcs.WPA(WPA_PASSWORD)
        self.gcs.CCL(0)

    def set_velocity(self, vel_mm_s: float) -> None:
        self.gcs.VEL(self.axis, [vel_mm_s])

    def get_velocity(self) -> float:
        return self.gcs.qVEL(self.axis)[self.axis]

    # --- motion --------------------------------------------------------------
    def get_position_mm(self) -> float:
        return self.gcs.qPOS(self.axis)[self.axis]

    def on_target(self) -> bool:
        return bool(self.gcs.qONT(self.axis)[self.axis])

    def is_moving(self) -> bool:
        return bool(self.gcs.IsMoving(self.axis)[self.axis])

    def move_to(self, z_mm: float, wait: bool = True, timeout: float = 10.0, settle_s: float = 0.0) -> float:
        """Absolute move (mm). Returns the actual on-target position."""
        self.gcs.MOV(self.axis, z_mm)
        if wait:
            self._pitools.waitontarget(self.gcs, axes=self.axis, timeout=timeout)
            if settle_s:
                time.sleep(settle_s)
        return self.get_position_mm()

    def move_relative(self, dz_mm: float, wait: bool = True, timeout: float = 10.0) -> float:
        self.gcs.MVR(self.axis, dz_mm)
        if wait:
            self._pitools.waitontarget(self.gcs, axes=self.axis, timeout=timeout)
        return self.get_position_mm()

    def stop(self) -> None:
        with suppress(self._GCSError):
            self.gcs.StopAll(noraise=True)

    # --- teardown ------------------------------------------------------------
    def close(self) -> None:
        with suppress(Exception):
            self.gcs.CloseConnection()

    def __enter__(self) -> "C414FocusStage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _resolve_port_by_sn(serialnum: str) -> str:
    """Resolve an FTDI/USB serial number (e.g. '1UETR6I!') to a serial device path."""
    import serial.tools.list_ports

    matches = [p.device for p in serial.tools.list_ports.comports() if p.serial_number == serialnum]
    if not matches:
        raise RuntimeError(
            f"No serial port with serial_number={serialnum!r}. On Linux the C-414's custom-VID "
            f"FTDI needs the ftdi_sio bind rule (98-pi-c414-bind.rules) installed so /dev/ttyUSB* "
            f"appears; verify it is present and the controller is powered."
        )
    return matches[0]


def connect_pi_focus_stage(
    simulated: bool = False,
    serialnum: Optional[str] = None,
    serial_port: Optional[str] = None,
    baudrate: int = 115200,
    axis: str = "1",
    reference: bool = True,
    stage_config: Optional[StageConfig] = None,
) -> PIFocusStage:
    """Open the C-414 over serial (or a simulated backend) and wrap it as a PIFocusStage.

    With reference=True the bring-up references the axis, which MOVES the stage -- run with the
    objective clear of the sample.
    """
    if simulated:
        backend = _SimulatedC414(axis=axis)
        backend.initialize(reference=reference)
        return PIFocusStage(backend, stage_config=stage_config)

    backend = C414FocusStage(axis=axis)
    if serial_port:
        port = serial_port
    elif serialnum:
        port = _resolve_port_by_sn(serialnum)
    else:
        raise RuntimeError("Set PI_FOCUS_STAGE_SN or PI_FOCUS_SERIAL_PORT to locate the C-414.")
    backend.connect_serial(port, baudrate=baudrate)
    backend.initialize(reference=reference)
    return PIFocusStage(backend, stage_config=stage_config)
