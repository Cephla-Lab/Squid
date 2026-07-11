"""6-position ASI objective turret on an MS-2000-family controller.

Shares the controller -- and, when USE_ASI_Z_STAGE is enabled, the serial connection -- with
the LS50 Z stage (squid.stage.asi). Command set: ``M T=<slot>`` rotates to a RAW slot index
1..6 (NOT scaled by the 0.1 um unit factor used for linear axes); ``W T`` presumably reads
the slot back (UNVERIFIED on hardware -- treated as best-effort, degrading to software
tracking of the last commanded slot); ``/`` is the controller-GLOBAL busy byte, shared with
the Z axis. ``MS2000Serial``'s internal lock keeps turret and Z commands frame-safe when
interleaved.

The turret has NO homing: ``home()`` never moves; it only refreshes the tracked slot. The
startup flow's ``move_to_objective(DEFAULT_OBJECTIVE)`` establishes a known slot at boot.
"""

from contextlib import suppress
from typing import Dict, Optional

import squid.abc
import squid.logging
from control.objective_turret_controller import (  # shared changer helpers (same KeyError the GUI dialog shows)
    _resolve_position,
    restore_z_if_captured,
    retract_z_if_possible,
)
from squid.stage.asi import MS2000Serial, parse_ms2000_number

_log = squid.logging.get_logger(__name__)

TURRET_SLOT_COUNT = 6
DEFAULT_MOVE_TIMEOUT_S = 30.0


def _validate_positions(positions: Optional[dict]) -> Dict[str, int]:
    if positions is None:
        from control._def import ASI_OBJECTIVE_TURRET_POSITIONS

        positions = ASI_OBJECTIVE_TURRET_POSITIONS
    # Fail fast: a bad slot value would otherwise go straight to the hardware as 'M T=<junk>'.
    for name, slot in positions.items():
        if not isinstance(slot, int) or not 1 <= slot <= TURRET_SLOT_COUNT:
            raise ValueError(
                f"ASI turret position for {name!r} must be an integer slot 1..{TURRET_SLOT_COUNT}, got {slot!r}"
            )
    return dict(positions)


class ASIObjectiveTurret:
    """ObjectiveChangerProtocol implementation for the MS-2000 turret (T) axis.

    Pass ``shared_serial`` (the Z stage's transport, via squid.stage.asi.find_shared_ms2000)
    when the LS50 Z stage runs on the same controller -- the turret then never closes it.
    Without it, the turret opens (and owns) its own connection by serial number or port.
    """

    def __init__(
        self,
        shared_serial: Optional[MS2000Serial] = None,
        serial_number: Optional[str] = None,
        serial_port: Optional[str] = None,
        baudrate: int = 115200,
        axis: str = "T",
        positions: Optional[Dict[str, int]] = None,
        stage: Optional[squid.abc.AbstractStage] = None,
    ):
        self._positions = _validate_positions(positions)
        self._axis = axis
        self._stage = stage
        self._current_objective: Optional[str] = None
        self._is_open = False

        if shared_serial is not None:
            self._serial = shared_serial
            self._owns_serial = False
            _log.info("ASI turret reusing the Z stage's MS-2000 connection.")
        else:
            import squid.stage.utils

            port = squid.stage.utils.resolve_port(
                serial_port,
                serial_number,
                missing_hint="Verify the MS-2000 controller is powered and enumerates as a USB serial device.",
                unset_message=(
                    "Set ASI_OBJECTIVE_TURRET_SN/ASI_OBJECTIVE_TURRET_SERIAL_PORT (or the ASI_Z_* "
                    "equivalents) to locate the MS-2000 controller."
                ),
            )
            _log.info(f"ASI turret opening its own MS-2000 connection on {port} at {baudrate} baud.")
            self._serial = MS2000Serial.open(port, baudrate=baudrate)
            self._owns_serial = True

        try:
            # require_reply only when this is our own connection (then it doubles as the comms
            # sanity check); on a shared connection the Z stage already proved the link.
            self._current_slot = self._probe_slot(require_reply=self._owns_serial)
        except Exception:
            if self._owns_serial:
                self._serial.close()  # no leaked handle on a failed bring-up
            raise
        self._is_open = True

    # --- ObjectiveChangerProtocol ---------------------------------------------

    def home(self, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S) -> None:
        """The ASI turret has no homing: refresh the tracked slot; NEVER moves."""
        self._require_open()
        self._current_objective = None
        slot = self._probe_slot()
        if slot is not None:
            self._current_slot = slot
        _log.info("ASI turret has no homing; home() refreshed the tracked slot without motion.")

    def move_to_objective(
        self, objective_name: str, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S, restore_z: bool = True
    ) -> None:
        self._require_open()
        target_slot = _resolve_position(objective_name, self._positions)
        if self._current_slot == target_slot:
            # Already at the slot (alias name, or the W-T-seeded position). An unknown slot
            # (None) never equals an int, so uncertainty always rotates.
            self._current_objective = objective_name
            return
        captured_z = self._retract_z_if_possible()
        try:
            self._rotate_to_slot(target_slot, timeout_s)
            self._current_objective = objective_name
        finally:
            if restore_z:
                self._restore_z_if_captured(captured_z)

    def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        if self._owns_serial:
            self._serial.close()
        # A shared transport belongs to the Z stage and is released by stage.close().

    def __enter__(self) -> "ASIObjectiveTurret":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- properties -------------------------------------------------------------

    @property
    def current_objective(self) -> Optional[str]:
        return self._current_objective

    @property
    def current_slot(self) -> Optional[int]:
        return self._current_slot

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def owns_serial(self) -> bool:
        return self._owns_serial

    # --- internals ---------------------------------------------------------------

    def _require_open(self) -> None:
        if not self._is_open:
            raise RuntimeError("ASI turret is closed")

    def _probe_slot(self, require_reply: bool = False) -> Optional[int]:
        """Best-effort 'W T' read. Returns the slot only for a plausible 1..N integer reply.

        check_error=False: an ':N-...' ack still proves comms -- it just means 'W T' is
        unsupported on this controller build, and we fall back to tracking the last
        commanded slot.
        """
        reply = self._serial.command_with_settle_retry(f"W {self._axis}", check_error=False)
        if require_reply and not reply:
            # Our own connection: this probe doubles as the comms sanity check.
            raise RuntimeError(
                f"MS-2000 did not answer 'W {self._axis}'. Check the baud rate (ASI RS-232 "
                f"DIP default is 9600), the port, and that the controller is powered."
            )
        _log.info(f"ASI turret 'W {self._axis}' reply: {reply!r}")
        value = parse_ms2000_number(reply)
        if value is not None and value == int(value) and 1 <= int(value) <= TURRET_SLOT_COUNT:
            return int(value)
        return None

    def _rotate_to_slot(self, slot: int, timeout_s: float) -> None:
        wt_was_supported = self._current_slot is not None
        # RAW slot index -- turret positions are not scaled like linear-axis 0.1 um units.
        self._serial.command(f"M {self._axis}={int(slot)}")
        self._wait_idle(timeout_s)
        self._current_slot = slot
        if wt_was_supported:
            # Verification only; tracked state stays authoritative.
            with suppress(Exception):
                readback = self._probe_slot()
                if readback is not None and readback != slot:
                    _log.warning(f"ASI turret readback disagrees: commanded slot {slot}, 'W' reports {readback}.")

    def _wait_idle(self, timeout_s: float) -> None:
        # '/' is controller-global: a concurrently moving Z also reads busy. Acceptable --
        # objective changes and Z scans are not concurrent flows.
        self._serial.wait_idle(timeout_s, "ASI turret")

    def _retract_z_if_possible(self) -> Optional[float]:
        return retract_z_if_possible(self._stage)

    def _restore_z_if_captured(self, captured_z: Optional[float]) -> None:
        restore_z_if_captured(self._stage, captured_z)


class ASIObjectiveTurretSimulation:
    """Hardware-free stand-in with the same public surface (SIMULATE_OBJECTIVE_CHANGER)."""

    def __init__(
        self,
        positions: Optional[Dict[str, int]] = None,
        stage: Optional[squid.abc.AbstractStage] = None,
        axis: str = "T",
    ):
        self._positions = _validate_positions(positions)
        self._axis = axis
        self._stage = stage
        self._current_objective: Optional[str] = None
        self._current_slot: Optional[int] = None  # unknown until commanded, like power-on
        self._is_open = True

    def home(self, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S) -> None:
        self._require_open()
        self._current_objective = None  # slot is retained: no homing, nothing moved

    def move_to_objective(
        self, objective_name: str, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S, restore_z: bool = True
    ) -> None:
        self._require_open()
        target_slot = _resolve_position(objective_name, self._positions)
        if self._current_slot == target_slot:
            self._current_objective = objective_name
            return
        captured_z = self._retract_z_if_possible()
        try:
            self._current_slot = target_slot
            self._current_objective = objective_name
        finally:
            if restore_z:
                self._restore_z_if_captured(captured_z)

    def close(self) -> None:
        self._is_open = False

    def __enter__(self) -> "ASIObjectiveTurretSimulation":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def current_objective(self) -> Optional[str]:
        return self._current_objective

    @property
    def current_slot(self) -> Optional[int]:
        return self._current_slot

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _require_open(self) -> None:
        if not self._is_open:
            raise RuntimeError("ASI turret (simulated) is closed")

    def _retract_z_if_possible(self) -> Optional[float]:
        return retract_z_if_possible(self._stage)

    def _restore_z_if_captured(self, captured_z: Optional[float]) -> None:
        restore_z_if_captured(self._stage, captured_z)
