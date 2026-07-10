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

STEPS_PER_MM = 10000  # ASI native unit = 0.1 um; also the finest addressable step (z_mm_to_usteps grid)
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

        conn = serial.Serial(port, baudrate=baudrate, timeout=timeout_s)
        conn.reset_input_buffer()  # drop any boot banner / stale bytes so the first reply parses
        return cls(conn)

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

    def connect_serial(self, *args, **kwargs):
        pass

    def initialize(self):
        pass

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

    def close(self):
        self._closed = True


class LS50Controller:
    """ASI LS50 Z linear stage on an MS-2000-family controller (CR text protocol, 1/10 um units).

    Native 0 is the power-on position (the retracted end by convention); native negative is
    toward the sample. There is no referencing routine, and bring-up performs no motion and
    writes no controller parameters.
    """

    def __init__(self, axis: str = "Z"):
        # Single-axis MS-2000 builds may label their lone axis X (or other) -- configurable
        # via ASI_Z_AXIS_LETTER.
        self._axis = axis
        self._serial: Optional[MS2000Serial] = None
        # Cached fence [lo, hi] in native mm so moves can clamp without a query each move;
        # None until set_travel_limits is called (limits are unknown at power-on).
        self._range_lo: Optional[float] = None
        self._range_hi: Optional[float] = None

    @property
    def serial(self) -> Optional[MS2000Serial]:
        """The MS-2000 transport (public: shared with same-controller addons, e.g. the turret)."""
        return self._serial

    def connect_serial(self, comport: str, baudrate: int = 115200) -> None:
        self._serial = MS2000Serial.open(comport, baudrate=baudrate)

    def initialize(self) -> None:
        # Comms sanity only: prove the controller answers. NO motion, NO parameter writes.
        # One retry after a settle+flush: a marginal RS-232 link or a just-opened adapter can
        # drop/garble the very first exchange without being actually broken.
        try:
            try:
                self.get_position_mm()
                return
            except RuntimeError:
                time.sleep(0.2)
                with suppress(Exception):
                    self._serial._serial.reset_input_buffer()
            self.get_position_mm()
        except RuntimeError as e:
            raise RuntimeError(
                f"LS50 controller did not answer a position query ({e}). Check the baud rate "
                f"(ASI controllers often ship at 9600; Squid defaults to 115200 -- set "
                f"ASI_Z_BAUDRATE in the machine config), that the resolved port really is the "
                f"ASI controller, and that it is powered. "
                f"`python3 tools/asi_z_bringup.py --sn <SN> --scan-bauds` can diagnose this."
            ) from e

    def hardware_limits_mm(self) -> Tuple[Optional[float], Optional[float]]:
        return (self._range_lo, self._range_hi)

    def set_travel_limits(self, min_mm: float, max_mm: float) -> None:
        # SL (lower) / SU (upper) take mm, unlike M/R/W which take tenths of microns.
        self._serial.command(f"SL {self._axis}={float(min_mm):.4f}")
        self._serial.command(f"SU {self._axis}={float(max_mm):.4f}")
        self._range_lo, self._range_hi = float(min_mm), float(max_mm)

    def get_position_mm(self) -> float:
        reply = self._serial.command(f"W {self._axis}")  # ':A -12345' or bare '-12345', in 0.1 um
        match = re.search(r"-?\d+(?:\.\d+)?", reply.replace(":A", "", 1))
        if not match:
            raise RuntimeError(f"Could not parse LS50 position from reply {reply!r}")
        return float(match.group(0)) / STEPS_PER_MM

    def is_moving(self) -> bool:
        return "B" in self._serial.command("/").upper()

    def _clamp_target(self, z_mm: float) -> float:
        """Clamp an absolute native target to the fence; pass through while unfenced."""
        lo, hi = self._range_lo, self._range_hi
        if lo is None:
            return z_mm
        clamped = min(max(z_mm, lo), hi)
        if abs(clamped - z_mm) > 1e-9:
            _log.warning(
                "LS50 Z target %.5f mm is outside the travel fence [%.5f, %.5f]; clamped to %.5f mm.",
                z_mm,
                lo,
                hi,
                clamped,
            )
        return clamped

    def move_to(self, z_mm: float, wait: bool = True, timeout: float = _DEFAULT_MOVE_TIMEOUT_S) -> float:
        target = self._clamp_target(float(z_mm))
        self._serial.command(f"M {self._axis}={round(target * STEPS_PER_MM)}")
        if wait:
            self._wait_idle(timeout)
            return self.get_position_mm()
        return target

    def move_relative(self, dz_mm: float, wait: bool = True, timeout: float = _DEFAULT_MOVE_TIMEOUT_S) -> float:
        # Resolve to an absolute target (M) rather than sending R Z=, so a jog past the fence
        # clamps instead of erroring or overdriving.
        return self.move_to(self.get_position_mm() + float(dz_mm), wait=wait, timeout=timeout)

    def _wait_idle(self, timeout_s: float) -> None:
        # '/' polling is the only settle signal (there is no on-target query in this command set).
        deadline = time.monotonic() + timeout_s
        while self.is_moving():
            if time.monotonic() > deadline:
                raise RuntimeError(f"LS50 did not reach idle within {timeout_s:.1f}s")
            time.sleep(_STATUS_POLL_PERIOD_S)

    def zero_here(self) -> None:
        # Redefine the current position as native 0. Deliberately NOT wired to
        # AbstractStage.zero() -- see ASIZStage.zero().
        self._serial.command(f"H {self._axis}=0")

    def stop(self) -> None:
        # HALT; the MS-2000 acks it with ':N-21', which is expected, not an error.
        self._serial.command("\\", check_error=False)

    def close(self) -> None:
        if self._serial:
            self._serial.close()

    def __enter__(self) -> "LS50Controller":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ASIZStage(AbstractStage):
    """Z-only AbstractStage backed by an ASI LS50. X / Y / theta are no-ops.

    With ``invert_z=True`` (the standard wiring) Squid Z is the negation of the controller's
    native mm: native + is away from the sample, so squid 0 is the retracted end and squid Z
    increases toward the sample. A pure sign flip -- unlike the PI V-308 there is no absolute
    positive travel limit to offset against.

    ``home_mm`` (Squid mm) is the retract target home() drives to -- native/squid 0 by
    convention. None disables home(z) entirely (warn no-op), guaranteeing no motion.

    A lock serialises every backend call so a non-blocking home() cannot interleave serial
    request/response framing with concurrent get_pos()/move_z().
    """

    def __init__(
        self,
        backend,
        stage_config: Optional[StageConfig] = None,
        home_mm: Optional[float] = None,
        invert_z: bool = False,
    ):
        super().__init__(stage_config)
        self._backend = backend
        self._lock = threading.RLock()
        self._closed = False
        self._busy = False  # set while an async home holds the lock, so get_state needn't block

        self._invert = invert_z
        self._home_mm = home_mm  # Squid-frame retract target; None = home(z) disabled

    @property
    def ms2000_serial(self) -> Optional[MS2000Serial]:
        # getattr: _SimulatedLS50 has no `serial` property -> None (simulation-aware).
        return getattr(self._backend, "serial", None)

    def _flip(self, mm: float) -> float:
        # squid_z = -native_z (and vice versa) when inverted; identity otherwise.
        return -mm if self._invert else mm

    def move_z(self, rel_mm: float, blocking: bool = True):
        with self._lock:
            self._backend.move_relative(self._flip(rel_mm), wait=blocking)

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        with self._lock:
            self._backend.move_to(self._flip(abs_mm), wait=blocking)

    def get_pos(self) -> Pos:
        with self._lock:
            return Pos(x_mm=0.0, y_mm=0.0, z_mm=self._flip(self._backend.get_position_mm()), theta_rad=None)

    def get_state(self) -> StageStage:
        # If an async home holds the lock, report busy without blocking on it.
        if self._busy:
            return StageStage(busy=True)
        with self._lock:
            return StageStage(busy=self._backend.is_moving())

    def is_referenced(self) -> bool:
        return True  # no referencing concept; the power-on frame is always valid

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        # Home = retract to the configured target (native 0 by convention). There is no
        # hardware referencing routine on this stage.
        if not z:
            return
        if self._home_mm is None:
            self._log.warning("ASIZStage home(z=True) is a no-op: no home target configured (ASI_Z_HOME_MM).")
            return
        if blocking:
            self._home_z_locked()
        else:
            threading.Thread(target=self._home_z_locked, daemon=True, name="asi-z-home").start()

    def _home_z_locked(self):
        self._busy = True
        try:
            with self._lock:
                if self._closed:  # close() won the race; do not touch the torn-down handle
                    return
                self._backend.move_to(self._flip(self._home_mm), wait=True)
        finally:
            self._busy = False

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if z:
            self._log.warning(
                "ASIZStage.zero(z=True) is a no-op: native 0 is the retract reference, and "
                "redefining it mid-session would shift the retract target and invalidate the "
                "travel fence. The controller supports it (LS50Controller.zero_here) if a "
                "re-zeroing flow is ever needed."
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
            with self._lock:
                # Map the software Z limits to native; inversion reverses order, so take
                # min/max after flipping both ends.
                n1, n2 = self._flip(z_pos_mm), self._flip(z_neg_mm)
                self._backend.set_travel_limits(min(n1, n2), max(n1, n2))
        elif z_pos_mm is not None or z_neg_mm is not None:
            self._log.warning("ASIZStage.set_limits ignored a one-sided Z limit; pass both z_pos_mm and z_neg_mm.")

    def close(self):
        with self._lock:
            self._closed = True
            self._backend.close()

    def z_mm_to_usteps(self, mm: float) -> int:
        # The 0.1 um protocol grid is the finest addressable step; the GUI's Z step snapping
        # uses 1 / z_mm_to_usteps(1.0). Rounds to an integer like the stepper conversion.
        return round(mm * STEPS_PER_MM)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._no_xy("move_x")

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._no_xy("move_y")

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._no_xy("move_x_to")

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._no_xy("move_y_to")

    def _no_xy(self, name: str):
        self._log.warning(f"{name} ignored: ASIZStage is a Z-only stage (pair via CombinedStage).")


def find_shared_ms2000(stage) -> Optional[MS2000Serial]:
    """Return the MS2000Serial of an ASI Z stage embedded in ``stage``, else None.

    Accepts an ASIZStage directly or a composite exposing a ``z_stage`` property (duck-typed
    to avoid an import cycle with squid.stage.pi). Returns None for simulated backends and
    non-ASI stages. Ownership stays with the Z stage: callers must NOT close the returned
    transport -- it is released by stage.close().
    """
    for candidate in (stage, getattr(stage, "z_stage", None)):
        if isinstance(candidate, ASIZStage):
            return candidate.ms2000_serial
    return None


def connect_asi_z_stage(
    simulated: bool = False,
    serialnum: Optional[str] = None,
    serial_port: Optional[str] = None,
    baudrate: int = 115200,
    axis: str = "Z",
    home_mm: Optional[float] = None,
    invert_z: bool = False,
    home_on_startup: bool = False,
    z_travel_mm: float = 0.0,
    stage_config: Optional[StageConfig] = None,
) -> ASIZStage:
    """Open the LS50 controller over serial (or a simulated backend) and wrap it as an ASIZStage.

    Bring-up performs NO motion unless home_on_startup=True (which requires home_mm and does one
    blocking retract). z_travel_mm > 0 sets a coarse sanity fence of native [-travel, +travel]
    around the power-on zero -- it can never exclude a reachable position, but stops absurd
    targets; the real fence arrives via set_limits from StageConfig.Z_AXIS at microscope init.
    """
    if simulated:
        backend, port = _SimulatedLS50(), None
    else:
        # Resolve the port BEFORE opening anything, so a missing controller never leaks a handle.
        if serial_port:
            port = serial_port
        elif serialnum:
            import squid.stage.utils

            port = squid.stage.utils.resolve_serial_port_by_sn(
                serialnum,
                missing_hint=(
                    "Verify the LS50 controller is powered and enumerates as a USB serial " "device (lsusb / dmesg)."
                ),
            )
        else:
            raise RuntimeError("Set ASI_Z_STAGE_SN or ASI_Z_SERIAL_PORT to locate the LS50 controller.")
        backend = LS50Controller(axis=axis)
        _log.info(f"Connecting to the ASI Z stage on {port} at {baudrate} baud (axis {axis!r}).")

    try:
        backend.connect_serial(port, baudrate=baudrate)
        backend.initialize()
        if z_travel_mm:
            backend.set_travel_limits(-z_travel_mm, z_travel_mm)
    except Exception:
        backend.close()  # release the serial handle on any connect/init failure
        raise
    stage = ASIZStage(backend, stage_config=stage_config, home_mm=home_mm, invert_z=invert_z)

    if home_on_startup:
        if home_mm is None:
            _log.warning("ASI_Z_HOME_ON_STARTUP is set but no home target is configured; skipping the retract.")
        else:
            stage.home(False, False, True, False, blocking=True)
    return stage
