"""Controller for a motorized 4-position objective turret (NiMotion RS-485 stepper).

The real controller talks Modbus-RTU to the motor. A simulation twin mirrors
the public API for CI and offline development.
"""

from __future__ import annotations

import time
from typing import Optional

from serial.tools import list_ports
from control.modbus_rtu import ModbusRTUClient

import squid.abc
import squid.logging

logger = squid.logging.get_logger(__name__)

# Turret mechanics
GEAR_RATIO = 132 / 48
MOTOR_STEPS_PER_REV = 200
POSITIONS_PER_REV = 4  # 90 degrees per objective
MICROSTEP_REG_VALUE = 4  # 2^4 = 16 microsteps; register takes effect after power cycle
# 90 degrees of turret travel in motor pulses (2200); drives with a different microstep are rejected in __init__.
PULSES_PER_SLOT = int(MOTOR_STEPS_PER_REV * 2**MICROSTEP_REG_VALUE * GEAR_RATIO / POSITIONS_PER_REV)
POSITION_TOLERANCE_PULSES = 50
# Upper bound for backlash compensation, in turret degrees. Real gear backlash is a
# small fraction of a degree; anything larger indicates a misconfigured .ini.
BACKLASH_MAX_DEG = 1.0

# NiMotion Modbus register map
REG_SAVE_PARAMS = 0x0008
REG_CURRENT_DECEL = 0x0015  # unused: the SDM42 drive has no decel current (writes silently dropped, reads 0)
REG_CURRENT_IDLE = 0x0016  # x10mA; writable only while disabled
REG_CURRENT_ACCEL = 0x0017  # x10mA
REG_CURRENT_RUN = 0x0018  # x10mA
REG_CURRENT_OVERLOAD = 0x0019  # x100mA — unit differs from 0x15..0x18!
REG_MICROSTEP = 0x001A
REG_STATUS_WORD = 0x001F  # input-register side; holding side at this address is unrelated
REG_CURRENT_POSITION = 0x0021
REG_DI_FUNCTION = 0x002C
REG_RUN_MODE = 0x0039
REG_SET_ZERO = 0x0047
REG_CONTROL_WORD = 0x0051
REG_DIRECTION = 0x0052  # 0=reverse, 1=forward; also sets the sign of relative moves
REG_TARGET_POSITION = 0x0053
REG_TARGET_SPEED = 0x0055
REG_MAX_SPEED = 0x005B
REG_MIN_SPEED = 0x005D
REG_ACCEL = 0x005F
REG_DECEL = 0x0061
REG_CLEAR_ERROR_STORAGE = 0x0073

# Batch status snapshot (FC 0x04 input registers 0x17..0x26, same block SingleMotor
# polls): value offsets within the 16-register read.
STATUS_BLOCK_START = 0x0017
STATUS_BLOCK_COUNT = 16
_OFS_DI = 1  # 0x18: raw DI level, bit0 = DI1
_OFS_STATUS_WORD = 8  # 0x1F
_OFS_POSITION = 10  # 0x21..0x22, INT32
_OFS_ALARM = 15  # 0x26

# Control word values
CW_DISABLE = 0x0000
CW_STARTUP = 0x0006
CW_ENABLE = 0x0007
CW_RUN_ABSOLUTE = 0x000F
CW_TRIGGER_ABSOLUTE = 0x001F
CW_RUN_RELATIVE = 0x004F
CW_TRIGGER_RELATIVE = 0x005F
CW_CLEAR_FAULT = 0x0080

# Magic values
SAVE_PARAMS_MAGIC = 0x7376
CLEAR_ERROR_STORAGE_MAGIC = 0x6C64
SET_ZERO_MAGIC = 0x535A

# Run modes
MODE_POSITION = 1
MODE_SPEED = 2

# Status word bits
STATUS_BIT_FAULT = 1 << 3
STATUS_BIT_RUNNING = 1 << 12

# Factory parameter set, ported from SingleMotor MotorService.INIT_PARAMS
# (2026-07-24 acceptance values, max speed revised 2026-07-25). Speed registers are
# in Step/s (full steps); pulses/s = Step/s x microstep.
EXPECTED_ACCEL = 1000  # Step/s^2 (hardware limit ~2000; >=3000 is rejected)
EXPECTED_DECEL = 1000
EXPECTED_MIN_SPEED = 16  # manual default start/stop speed
# 200 -> 150 (2026-07-28): the 0.95 A driver current cap leaves no margin at speed
# 200 and the loaded turret was observed losing steps; 150 x16 = 2400 pulses/s
# ~= 98 deg/s at the turret.
EXPECTED_MAX_SPEED = 150
# Currents: with 2 objectives loaded the factory 0.51 A run current loses steps;
# 0.85 A and up does not. Driver firmware caps 0x16..0x18 at 95 (=0.95 A).
EXPECTED_CURRENT_OVERLOAD = 13  # x100mA = 1.3 A
EXPECTED_CURRENT_IDLE = 21  # displayed 0.69 A (manager-specified conversion, 2026-07-27; not a literal x10mA)
EXPECTED_CURRENT_ACCEL = 95  # x10mA = 0.95 A
EXPECTED_CURRENT_RUN = 95  # x10mA = 0.95 A
# Decel current (0x15) is deliberately NOT calibrated: the SDM42 drive has no such
# parameter — the firmware silently drops writes and always reads back 0.
# DI1 is permanently "origin switch" (3). The homing sensor must NEVER be configured
# as a limit switch: the turret is a disc with no travel limits, and a limit-mapped
# sensor faults FF0E whenever a normal move passes it (the pre-2026-07-24 scheme).
DI1_FUNCTION_ORIGIN_SWITCH = 3

# Software homing (ported from SingleMotor HomeSearch, 2026-07-24, params revised
# 2026-07-28): sweep toward the sensor in velocity mode while polling the DI level,
# decel-stop on trigger, back off until the switch releases, then fine-step back to
# the trigger edge and SET_ZERO there. The driver's built-in homing modes are no
# longer used — the sensed window is only ~50 pulses wide and the sweep speed/poll
# period pair below guarantees the window cannot be crossed between two polls
# (52 ms crossing >= 2.6 poll periods). Worst-case overshoot past the trigger edge is
# stop distance 29 + detection lag 20 = 49 pulses < HOMING_BACKOFF_STEP, so a single
# backoff jog clears the window (the backoff loop is only a fallback).
HOMING_SWEEP_SPEED = 60  # Step/s, velocity-mode sweep toward the sensor (x16 = 960 pulses/s)
HOMING_POLL_S = 0.02  # DI poll period during the sweep
HOMING_STOP_SETTLE_S = 0.4  # settle after the sweep decel-stop
HOMING_BACKOFF_STEP = 60  # pulses per backoff jog (release the switch)
HOMING_FINE_STEP = 2  # pulses per fine-search jog; sets home repeatability (+/-2)
HOMING_MAX_TRAVEL = 10000  # pulses; > one turret revolution (8800 at microstep 16)
HOMING_FINE_TRAVEL_LIMIT = 200  # backoff 60 + window 50 + margin; bounds a bad trigger
HOMING_JOG_SPEED = 60  # Step/s; max speed is temporarily lowered to this for jogs
HOMING_FINE_ACCEL = 50  # Step/s^2; accel is temporarily lowered for the fine search
HOMING_SETTLE_MARGIN_S = 0.3  # fixed margin on top of the per-jog travel-time estimate

# Polling
POLL_INTERVAL_S = 0.05
# At accel=1000/max_speed=150, a worst-case 3-slot move stays well inside 30s.
DEFAULT_MOVE_TIMEOUT_S = 30.0
# Software homing worst case: sweep up to one revolution at 960 pulses/s plus tens of
# ~0.3s fine/backoff jogs. Matches SingleMotor's 120s watchdog.
DEFAULT_HOME_TIMEOUT_S = 120.0

# Settle time after a control-word transition before the next write.
CONTROL_WORD_SETTLE_S = 0.1

# Init calibration table: (register, expected value, label, kwargs-for-_calibrate_one).
# Order matters: min_speed must be written before max_speed (the firmware rejects a
# max-speed write below the current min speed).
_INIT_PARAMS = [
    (REG_ACCEL, EXPECTED_ACCEL, "accel", {"is_32bit": True}),
    (REG_DECEL, EXPECTED_DECEL, "decel", {"is_32bit": True}),
    (REG_MIN_SPEED, EXPECTED_MIN_SPEED, "min_speed", {"is_32bit": True}),
    (REG_MAX_SPEED, EXPECTED_MAX_SPEED, "max_speed", {"is_32bit": True}),
    (REG_CURRENT_OVERLOAD, EXPECTED_CURRENT_OVERLOAD, "overload_current", {}),
    (REG_CURRENT_IDLE, EXPECTED_CURRENT_IDLE, "idle_current", {}),
    (REG_CURRENT_ACCEL, EXPECTED_CURRENT_ACCEL, "accel_current", {}),
    (REG_CURRENT_RUN, EXPECTED_CURRENT_RUN, "run_current", {}),
    (REG_DI_FUNCTION, DI1_FUNCTION_ORIGIN_SWITCH, "DI1_function", {"is_32bit": True, "mask": 0xF}),
]


def _validate_backlash_deg(backlash_deg) -> float:
    if isinstance(backlash_deg, bool) or not isinstance(backlash_deg, (int, float)):
        raise ValueError(f"OBJECTIVE_TURRET_BACKLASH_DEG must be a number of degrees, got {backlash_deg!r}")
    deg = float(backlash_deg)
    if not 0.0 <= deg <= BACKLASH_MAX_DEG:
        raise ValueError(
            f"OBJECTIVE_TURRET_BACKLASH_DEG={deg} out of range 0..{BACKLASH_MAX_DEG} degrees; check the machine .ini"
        )
    return deg


def _resolve_position(objective_name: str, positions: dict) -> int:
    try:
        return positions[objective_name]
    except KeyError:
        raise KeyError(f"Unknown objective '{objective_name}'. Valid names: {sorted(positions)}") from None


def _is_alias_for_current(current: Optional[str], target_name: str, positions: dict) -> bool:
    """True if `target_name` maps to the same physical slot as `current` under a different name."""
    if current is None:
        return False
    return _resolve_position(current, positions) == _resolve_position(target_name, positions)


def _find_port(serial_number: str) -> str:
    matches = [p.device for p in list_ports.comports() if p.serial_number == serial_number]
    if not matches:
        raise ValueError(f"No serial device found with serial number: {serial_number}")
    if len(matches) > 1:
        logger.warning(
            "Multiple devices match serial number %s: %s. Using %s.",
            serial_number,
            matches,
            matches[0],
        )
    return matches[0]


class ObjectiveTurret4PosControllerSimulation:
    """In-memory stand-in for ObjectiveTurret4PosController.

    Mirrors the real controller's public API for tests and offline use.
    Implements the Z retract/restore dance when a stage reference is provided.
    """

    def __init__(
        self,
        serial_number: Optional[str] = None,
        slave_id: int = 1,
        baudrate: int = 115200,
        timeout: float = 0.5,
        positions: Optional[dict] = None,
        stage: Optional[squid.abc.AbstractStage] = None,
        # Accepted for constructor parity with the real controller; the simulation
        # tracks objectives by name and never computes pulses, so they are all unused.
        offset_pulses: Optional[int] = None,
        backlash_deg: Optional[float] = None,
        direction_inverted: Optional[bool] = None,
        di_invert: Optional[bool] = None,
    ):
        from control._def import OBJECTIVE_TURRET_POSITIONS

        self._is_open = True
        self._current_objective: Optional[str] = None
        self._positions = dict(positions) if positions is not None else dict(OBJECTIVE_TURRET_POSITIONS)
        self._stage = stage
        logger.info("Simulated turret opened (sn=%s)", serial_number)

    def home(self, timeout_s: float = DEFAULT_HOME_TIMEOUT_S) -> None:
        self._require_open()
        self._current_objective = None
        logger.info("Simulated turret homed")

    def enable(self) -> None:
        """Mirror of the real controller's disable -> startup -> enable state-machine cycle."""
        self._require_open()
        logger.info("Simulated turret enabled")

    def move_to_objective(
        self, objective_name: str, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S, restore_z: bool = True
    ) -> None:
        self._require_open()
        if _is_alias_for_current(self._current_objective, objective_name, self._positions):
            self._current_objective = objective_name
            return
        target_position = _resolve_position(objective_name, self._positions)

        captured_z = self._retract_z_if_possible()
        self._current_objective = objective_name
        if restore_z:
            self._restore_z_if_captured(captured_z)

        logger.info(
            "Simulated turret moved to %s (position %d)",
            objective_name,
            target_position,
        )

    def clear_alarm(self) -> None:
        self._require_open()
        logger.info("Simulated turret alarm cleared")

    def close(self) -> None:
        if self._is_open:
            self._is_open = False
            logger.info("Simulated turret closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def current_objective(self) -> Optional[str]:
        return self._current_objective

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _require_open(self) -> None:
        if not self._is_open:
            raise RuntimeError("Turret controller is closed")

    def _retract_z_if_possible(self) -> Optional[float]:
        """If stage + Z homing are usable, capture Z and move to safe retract. Return captured z, else None."""
        from control._def import HOMING_ENABLED_Z, OBJECTIVE_RETRACTED_POS_MM

        if self._stage is None or not HOMING_ENABLED_Z:
            return None
        z_mm = self._stage.get_pos().z_mm
        self._stage.move_z_to(OBJECTIVE_RETRACTED_POS_MM)
        return z_mm

    def _restore_z_if_captured(self, captured_z: Optional[float]) -> None:
        if captured_z is None or self._stage is None:
            return
        self._stage.move_z_to(captured_z)


class ObjectiveTurret4PosController:
    """Synchronous controller for a 4-position objective turret over Modbus-RTU."""

    def __init__(
        self,
        serial_number: str,
        slave_id: int = 1,
        baudrate: int = 115200,
        timeout: float = 0.5,
        positions: Optional[dict] = None,
        stage: Optional[squid.abc.AbstractStage] = None,
        offset_pulses: Optional[int] = None,
        backlash_deg: Optional[float] = None,
        direction_inverted: Optional[bool] = None,
        di_invert: Optional[bool] = None,
    ) -> None:
        from control._def import (
            OBJECTIVE_TURRET_POSITIONS,
            OBJECTIVE_TURRET_OFFSET_PULSES,
            OBJECTIVE_TURRET_BACKLASH_DEG,
            OBJECTIVE_TURRET_DIRECTION_INVERTED,
            OBJECTIVE_TURRET_DI_INVERT,
        )

        self._slave_id = slave_id
        self._positions = dict(positions) if positions is not None else dict(OBJECTIVE_TURRET_POSITIONS)
        offset = offset_pulses if offset_pulses is not None else OBJECTIVE_TURRET_OFFSET_PULSES
        # Comes from per-machine .ini parsing; reject non-int so misconfiguration fails
        # fast here instead of deep in the signed Modbus write (bool is an int subclass).
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError(f"OBJECTIVE_TURRET_OFFSET_PULSES must be an integer number of pulses, got {offset!r}")
        self._offset_pulses = offset
        self._backlash_deg = _validate_backlash_deg(
            backlash_deg if backlash_deg is not None else OBJECTIVE_TURRET_BACKLASH_DEG
        )
        # Direction inversion for motor models wired with the opposite phase order.
        # Applied only at the register boundary (move targets, jog signs, sweep
        # direction bit, position readbacks); everything above works in logical
        # coordinates, so calibration/backlash/homing logic is inversion-agnostic.
        inverted = direction_inverted if direction_inverted is not None else OBJECTIVE_TURRET_DIRECTION_INVERTED
        if not isinstance(inverted, bool):
            raise ValueError(f"OBJECTIVE_TURRET_DIRECTION_INVERTED must be a boolean, got {inverted!r}")
        self._direction_inverted = inverted
        # Origin-switch (DI1) polarity inversion for changers whose sensor triggers
        # on the opposite logic level (SingleMotor 2026-08-12). Applied only to the
        # DI trigger verdict in the status snapshot; the homing state machine,
        # direction logic and calibration all stay in the same logical frame.
        di_inv = di_invert if di_invert is not None else OBJECTIVE_TURRET_DI_INVERT
        if not isinstance(di_inv, bool):
            raise ValueError(f"OBJECTIVE_TURRET_DI_INVERT must be a boolean, got {di_inv!r}")
        self._di_invert = di_inv
        self._stage = stage
        self._current_objective: Optional[str] = None
        self._is_open = False

        port = _find_port(serial_number)
        self._modbus = ModbusRTUClient(port=port, baudrate=baudrate, timeout=timeout)
        self._modbus.connect()
        try:
            self.clear_alarm()
            # Some parameter registers (notably homing config) reject writes while the
            # motor is in OPERATION_ENABLED — which can persist across crashed sessions
            # where close() never ran. Force the device into SWITCH_ON_DISABLED first.
            self._write_control(CW_DISABLE)
            time.sleep(CONTROL_WORD_SETTLE_S)

            microstep_raw = self._modbus.read_register(self._slave_id, REG_MICROSTEP)
            if not 0 <= microstep_raw <= 7:
                raise ValueError(f"Invalid microstep register value {microstep_raw} (expected 0..7)")
            if microstep_raw != MICROSTEP_REG_VALUE:
                # The register reads back the pending value (vendor-confirmed), so a
                # corrective write here would let the next start pass this check while
                # the drive still runs the old scale until power-cycled: never write.
                raise RuntimeError(
                    f"Turret microstep register reads {microstep_raw} (2^{microstep_raw} microsteps) but "
                    f"{MICROSTEP_REG_VALUE} (16 microsteps) is required. Set it with the SingleMotor setup "
                    "tool, save to EEPROM, power-cycle the turret, then restart."
                )
            self._microstep = 2**microstep_raw

            # A real slot-1 misalignment is always smaller than the 90-degree slot spacing;
            # a larger value means the slot mapping itself is wrong, not that slot 1 is off.
            # Bounding it also keeps every target inside the signed 32-bit register (no wrap).
            if abs(self._offset_pulses) > PULSES_PER_SLOT:
                raise ValueError(
                    f"OBJECTIVE_TURRET_OFFSET_PULSES={self._offset_pulses} exceeds one slot "
                    f"(±{PULSES_PER_SLOT} pulses ≈ 90°); check the machine .ini"
                )

            # Backlash compensation in motor pulses (one turret revolution = POSITIONS_PER_REV slots).
            self._backlash_pulses = round(self._backlash_deg / 360.0 * POSITIONS_PER_REV * PULSES_PER_SLOT)

            changed = self._calibrate_init_params()
            if changed:
                self._save_to_eeprom()
            # RAM-only runtime parameter, written AFTER the EEPROM save so it is not
            # persisted (the save command snapshots current RAM; the manual forbids
            # persisting the direction register, which moves rewrite dynamically).
            self._calibrate_one(REG_DIRECTION, self._physical_direction(1), "direction")

            logger.info(
                "Turret controller ready: port=%s microstep=%d pulses/position=%d calibrated=%s",
                port,
                self._microstep,
                PULSES_PER_SLOT,
                changed,
            )

            # Leave the motor de-energized; home()/move_to_objective() energize on demand.
            self._deenergize()
            self._is_open = True
        except Exception:
            self._modbus.disconnect()
            raise

    def home(self, timeout_s: float = DEFAULT_HOME_TIMEOUT_S) -> None:
        """Software homing, ported from SingleMotor HomeSearch.start_homing.

        sweep -> backoff -> fine-search, then SET_ZERO at the sensor's trigger edge
        and clamp at home with holding torque. The driver's built-in homing modes
        are not used. Repeatability is +/-HOMING_FINE_STEP pulses (hardware-measured
        0-pulse deviation between runs).
        """
        self._require_open()
        deadline = time.monotonic() + timeout_s
        # Parameter writes require the disabled state.
        self._write_control(CW_DISABLE)
        # Zero the counter at the start so the sweep travel bound is relative to it.
        self._write_holding(REG_SET_ZERO, SET_ZERO_MAGIC)
        # Temporarily lower max speed for backoff/fine jog precision; restore after.
        orig_max_speed = self._modbus.read_register_32bit(self._slave_id, REG_MAX_SPEED)
        orig_accel = self._modbus.read_register_32bit(self._slave_id, REG_ACCEL)
        accel_lowered = False
        if orig_max_speed != HOMING_JOG_SPEED:
            self._modbus.write_register_32bit(self._slave_id, REG_MAX_SPEED, HOMING_JOG_SPEED)
        try:
            di1, _, _, alarm = self._read_status_snapshot()
            self._check_alarm(alarm)
            if not di1:  # off the sensor -> sweep to it; already in the window skips straight to backoff
                self._sweep_to_sensor(deadline)
            self._backoff_off_sensor(deadline)
            # Fine search only: lower the acceleration to soften the microstep approach
            # to the trigger edge (SingleMotor 2026-07-28); restored in the finally.
            if orig_accel != HOMING_FINE_ACCEL:
                self._write_control(CW_DISABLE)  # parameter writes require the disabled state
                self._modbus.write_register_32bit(self._slave_id, REG_ACCEL, HOMING_FINE_ACCEL)
                accel_lowered = True
            self._fine_search_to_edge(deadline)
            # At the trigger edge: establish the home reference.
            self._write_holding(REG_SET_ZERO, SET_ZERO_MAGIC)
            time.sleep(0.05)
        finally:
            # Stop before restoring parameters (writes are rejected while enabled).
            # Best-effort so cleanup never masks the fault/timeout that got us here.
            self._deenergize()
            if orig_max_speed != HOMING_JOG_SPEED:
                try:
                    self._modbus.write_register_32bit(self._slave_id, REG_MAX_SPEED, orig_max_speed)
                except Exception as exc:
                    logger.warning("Failed to restore max speed after homing: %s", exc)
            if accel_lowered:
                try:
                    self._modbus.write_register_32bit(self._slave_id, REG_ACCEL, orig_accel)
                except Exception as exc:
                    logger.warning("Failed to restore acceleration after homing: %s", exc)
        # Success: hold the turret at home with torque until the next move.
        self._hold_position_clamp()
        self._current_objective = None
        logger.info("Homed at sensor edge (repeatability +/-%d pulses)", HOMING_FINE_STEP)

    def enable(self) -> None:
        """Run the disable -> startup -> enable state-machine cycle."""
        self._write_control(CW_DISABLE)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)

    def move_to_objective(
        self, objective_name: str, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S, restore_z: bool = True
    ) -> None:
        self._require_open()
        if _is_alias_for_current(self._current_objective, objective_name, self._positions):
            self._current_objective = objective_name
            return

        captured_z = self._retract_z_if_possible()
        try:
            self._rotate_to(objective_name, timeout_s)
            self._current_objective = objective_name
        finally:
            if restore_z:
                self._restore_z_if_captured(captured_z)

    def clear_alarm(self) -> None:
        self._write_control(CW_CLEAR_FAULT)
        self._write_holding(REG_CLEAR_ERROR_STORAGE, CLEAR_ERROR_STORAGE_MAGIC)

    def close(self) -> None:
        if not self._is_open and not self._modbus.is_connected:
            return
        if self._modbus.is_connected:
            try:
                self._write_control(CW_DISABLE)
            except Exception as exc:
                logger.warning("Failed to disable motor during close: %s", exc)
            self._modbus.disconnect()
        self._is_open = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def pulses_per_position(self) -> int:
        return PULSES_PER_SLOT

    @property
    def current_position_pulses(self) -> int:
        raw = self._modbus.read_input_register_32bit(self._slave_id, REG_CURRENT_POSITION, signed=True)
        return -raw if self._direction_inverted else raw

    @property
    def current_objective(self) -> Optional[str]:
        return self._current_objective

    @property
    def is_open(self) -> bool:
        return self._is_open

    # --- internal helpers ---

    def _require_open(self) -> None:
        if not self._is_open:
            raise RuntimeError("Turret controller is closed")

    def _physical_direction(self, logical_direction: int) -> int:
        """Map a logical direction bit (1=positive) to the physical register value."""
        return logical_direction ^ 1 if self._direction_inverted else logical_direction

    def _rotate_to(self, objective_name: str, timeout_s: float) -> None:
        position_index = _resolve_position(objective_name, self._positions)
        target_pulses = (position_index - 1) * PULSES_PER_SLOT + self._offset_pulses

        logger.info(
            "Rotating to %s: start=%d, target=%d, backlash_comp=%d",
            objective_name,
            self.current_position_pulses,
            target_pulses,
            self._backlash_pulses,
        )

        try:
            # Backlash compensation: overshoot below the target first, then approach it
            # from below, so the final approach direction is the same for every slot
            # change and gear backlash cancels out. comp=0 moves directly.
            if self._backlash_pulses > 0:
                self._run_absolute_move(target_pulses - self._backlash_pulses, timeout_s)
            self._run_absolute_move(target_pulses, timeout_s)
        finally:
            self._deenergize()
        logger.info(
            "Rotated to %s: target=%d, actual=%d",
            objective_name,
            target_pulses,
            self.current_position_pulses,
        )

    def _run_absolute_move(self, target_pulses: int, timeout_s: float) -> None:
        self._write_control(CW_DISABLE)
        self._write_holding(REG_RUN_MODE, MODE_POSITION)
        # The register takes physical coordinates; the wait below compares logical
        # target against the logical position readback, so it stays un-negated.
        physical_target = -target_pulses if self._direction_inverted else target_pulses
        self._modbus.write_register_32bit(self._slave_id, REG_TARGET_POSITION, physical_target, signed=True)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_ABSOLUTE)
        self._write_control(CW_TRIGGER_ABSOLUTE)
        self._wait_for_position(target_pulses, timeout_s)

    # --- software homing internals ---

    def _read_status_snapshot(self) -> tuple:
        """One batched input-register read -> (di1_triggered, status_word, position, alarm).

        A single FC 0x04 frame keeps the sweep poll period tight and the DI/position
        values consistent with each other (same block SingleMotor polls)."""
        vals = self._modbus.read_input_registers(self._slave_id, STATUS_BLOCK_START, STATUS_BLOCK_COUNT)
        di1 = bool(vals[_OFS_DI] & 1)
        if self._di_invert:
            di1 = not di1
        position = (vals[_OFS_POSITION] << 16) | vals[_OFS_POSITION + 1]
        if position >= 0x80000000:
            position -= 0x100000000
        if self._direction_inverted:
            position = -position
        return di1, vals[_OFS_STATUS_WORD], position, vals[_OFS_ALARM]

    @staticmethod
    def _check_alarm(alarm: int) -> None:
        # Fail fast: an alarm (undervoltage etc.) interrupts motion, so waiting for
        # the timeout would only hide the cause.
        if alarm != 0:
            raise RuntimeError(f"Drive alarm 0x{alarm:04X} during homing")

    @staticmethod
    def _check_deadline(deadline: float, phase: str) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Homing timed out during {phase}")

    def _sweep_to_sensor(self, deadline: float) -> None:
        """Velocity-mode sweep toward the sensor, polling the DI level; decel-stop on
        trigger. The sweep-speed/poll-period pairing guarantees the ~50-pulse sensor
        window cannot be skipped between two polls."""
        self._write_control(CW_DISABLE)
        self._write_holding(REG_RUN_MODE, MODE_SPEED)
        self._write_holding(REG_DIRECTION, self._physical_direction(0))  # logical negative, toward the sensor
        self._modbus.write_register_32bit(self._slave_id, REG_TARGET_SPEED, HOMING_SWEEP_SPEED)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_ABSOLUTE)
        try:
            while True:
                self._check_deadline(deadline, "sweep")
                di1, _, position, alarm = self._read_status_snapshot()
                self._check_alarm(alarm)
                if di1:
                    return
                if abs(position) > HOMING_MAX_TRAVEL:
                    raise RuntimeError("Homing sweep found no sensor within one revolution (direction/wiring?)")
                time.sleep(HOMING_POLL_S)
        finally:
            self._write_control(CW_ENABLE)  # decelerate-stop
            time.sleep(HOMING_STOP_SETTLE_S)

    def _jog(self, pulses: int) -> None:
        """Relative move: direction from REG_DIRECTION, REG_TARGET_POSITION takes the
        positive magnitude only (a negative value is rejected as invalid). Settle is
        time-based because short jogs do not reliably assert the RUNNING bit.
        `pulses` is logical; inversion negates it before the direction/magnitude split."""
        if self._direction_inverted:
            pulses = -pulses
        self._write_control(CW_DISABLE)
        self._write_holding(REG_RUN_MODE, MODE_POSITION)
        self._write_holding(REG_DIRECTION, 1 if pulses >= 0 else 0)
        self._modbus.write_register_32bit(self._slave_id, REG_TARGET_POSITION, abs(pulses))
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_RELATIVE)
        self._write_control(CW_TRIGGER_RELATIVE)
        jog_pps = HOMING_JOG_SPEED * self._microstep
        time.sleep(abs(pulses) / jog_pps * 1.3 + HOMING_SETTLE_MARGIN_S)

    def _backoff_off_sensor(self, deadline: float) -> None:
        """Back away (positive direction) until the switch releases."""
        while True:
            self._check_deadline(deadline, "backoff")
            di1, _, _, alarm = self._read_status_snapshot()
            self._check_alarm(alarm)
            if not di1:
                return
            self._jog(+HOMING_BACKOFF_STEP)

    def _fine_search_to_edge(self, deadline: float) -> None:
        """Approach the sensor again in HOMING_FINE_STEP jogs until it triggers; that
        quantized edge is the home reference."""
        travel = 0
        while True:
            self._check_deadline(deadline, "fine search")
            self._jog(-HOMING_FINE_STEP)
            travel += HOMING_FINE_STEP
            di1, _, _, alarm = self._read_status_snapshot()
            self._check_alarm(alarm)
            if di1:
                return
            if travel > HOMING_FINE_TRAVEL_LIMIT:
                raise RuntimeError("Homing fine search overran the sensor window (trigger signal wiring?)")

    def _hold_position_clamp(self) -> None:
        """Re-enable to OPERATION_ENABLED in position mode without a trigger bit: no
        motion results, but only 0x0F applies holding current — this clamps the
        turret at home against external disturbance."""
        self._write_holding(REG_RUN_MODE, MODE_POSITION)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_ABSOLUTE)

    def _retract_z_if_possible(self) -> Optional[float]:
        from control._def import HOMING_ENABLED_Z, OBJECTIVE_RETRACTED_POS_MM

        if self._stage is None or not HOMING_ENABLED_Z:
            return None
        z_mm = self._stage.get_pos().z_mm
        self._stage.move_z_to(OBJECTIVE_RETRACTED_POS_MM)
        return z_mm

    def _restore_z_if_captured(self, captured_z: Optional[float]) -> None:
        if captured_z is None or self._stage is None:
            return
        self._stage.move_z_to(captured_z)

    def _calibrate_one(
        self,
        addr: int,
        expected: int,
        label: str,
        *,
        is_32bit: bool = False,
        signed: bool = False,
        mask: Optional[int] = None,
    ) -> bool:
        if is_32bit:
            current = self._modbus.read_register_32bit(self._slave_id, addr, signed=signed)
        else:
            current = self._modbus.read_register(self._slave_id, addr)
        desired = (current & ~mask) | (expected & mask) if mask is not None else expected
        fmt = "0x%08X" if mask is not None else "%d"
        current_str, desired_str = fmt % current, fmt % desired
        if current == desired:
            logger.debug("%s @ 0x%04X: device=%s matches desired (no write)", label, addr, current_str)
            return False
        if is_32bit:
            self._modbus.write_register_32bit(self._slave_id, addr, desired, signed=signed)
        else:
            self._modbus.write_register(self._slave_id, addr, desired)
        logger.info("%s @ 0x%04X: %s -> %s (wrote)", label, addr, current_str, desired_str)
        return True

    def _calibrate_init_params(self) -> bool:
        """Bring every factory parameter in line with _INIT_PARAMS; return whether a
        write happened (i.e. whether the set should be persisted to EEPROM)."""
        changed = False
        for addr, expected, label, kwargs in _INIT_PARAMS:
            changed = self._calibrate_one(addr, expected, label, **kwargs) or changed
        return changed

    def _save_to_eeprom(self) -> None:
        self._write_holding(REG_SAVE_PARAMS, SAVE_PARAMS_MAGIC)
        logger.info("Saved parameters to EEPROM")

    def _write_control(self, value: int) -> None:
        self._modbus.write_register(self._slave_id, REG_CONTROL_WORD, value)

    def _deenergize(self) -> None:
        """Remove holding current so the motor idles cold. The turret holds its slot
        mechanically and the controller retains its position counter while powered.

        Best-effort: this runs from finally blocks after a move/home, so a failed disable
        must not replace the real timeout/fault that triggered the cleanup.
        """
        try:
            self._write_control(CW_DISABLE)
        except Exception as exc:
            logger.warning("Failed to de-energize turret motor: %s", exc)

    def _write_holding(self, address: int, value: int) -> None:
        self._modbus.write_register(self._slave_id, address, value)

    def _read_status_word(self) -> int:
        return self._modbus.read_input_register(self._slave_id, REG_STATUS_WORD)

    @staticmethod
    def _check_fault(status_word: int) -> None:
        if status_word & STATUS_BIT_FAULT:
            raise RuntimeError(f"Motor reported fault (status word=0x{status_word:04X})")

    def _wait_for_position(self, target_pulses: int, timeout_s: float) -> None:
        # No leading sleep: seen_running prevents stall detection before the motor asserts RUNNING.
        deadline = time.monotonic() + timeout_s
        seen_running = False
        last_pos: Optional[int] = None
        while time.monotonic() < deadline:
            status = self._read_status_word()
            self._check_fault(status)
            running = bool(status & STATUS_BIT_RUNNING)
            last_pos = self.current_position_pulses
            in_tolerance = abs(last_pos - target_pulses) <= POSITION_TOLERANCE_PULSES

            if running:
                seen_running = True
            if in_tolerance and not running:
                return
            if seen_running and not running and not in_tolerance:
                raise RuntimeError(
                    f"Motor stopped at {last_pos} pulses, target {target_pulses} "
                    f"(tolerance ±{POSITION_TOLERANCE_PULSES})"
                )
            time.sleep(POLL_INTERVAL_S)
        raise TimeoutError(
            f"Move to {target_pulses} pulses timed out after {timeout_s:.1f}s " f"(last position={last_pos})"
        )
