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
POSITION_TOLERANCE_PULSES = 50

# NiMotion Modbus register map
REG_SAVE_PARAMS = 0x0008
REG_DI_FUNCTION = 0x002C
REG_MICROSTEP = 0x001A
REG_LOW_SPEED_OPT = 0x001F  # holding-register view; same address as status word on input side
REG_STATUS_WORD = 0x001F
REG_CURRENT_POSITION = 0x0021
REG_RUN_MODE = 0x0039
REG_CONTROL_WORD = 0x0051
REG_TARGET_POSITION = 0x0053
REG_MAX_SPEED = 0x005B
REG_MIN_SPEED = 0x005D
REG_ACCEL = 0x005F
REG_DECEL = 0x0061
REG_HOMING_OFFSET = 0x0069
REG_HOMING_METHOD = 0x006B
REG_HOMING_SEARCH_SPEED = 0x006C
REG_HOMING_ZERO_SPEED = 0x006E
REG_ZERO_RETURN = 0x0072
REG_CLEAR_ERROR_STORAGE = 0x0073
REG_SET_ZERO = 0x0047

# Control word values
CW_DISABLE = 0x0000
CW_STARTUP = 0x0006
CW_ENABLE = 0x0007
CW_RUN_ABSOLUTE = 0x000F
CW_TRIGGER_ABSOLUTE = 0x001F
CW_CLEAR_FAULT = 0x0080

# Magic values
SAVE_PARAMS_MAGIC = 0x7376
CLEAR_ERROR_STORAGE_MAGIC = 0x6C64
SET_ZERO_MAGIC = 0x535A

# Run modes
MODE_POSITION = 1
MODE_HOMING = 3

# Status word bits
STATUS_BIT_FAULT = 1 << 3
STATUS_BIT_RUNNING = 1 << 12

# Motion parameter defaults (auto-calibrated on first connect).
# 200/200 accel/decel avoids step loss under the turret's inertial load (sharper
# ramps skip steps mid-move).
EXPECTED_ACCEL = 200
EXPECTED_DECEL = 200
EXPECTED_MAX_SPEED = 250
# NiMotion ramps cruise → min_speed at end of position moves, then continues at
# min_speed for final-approach pulses. Default 16 step/s makes that segment audibly
# slow; 150 keeps it brief while leaving decel range.
EXPECTED_MIN_SPEED = 150
# 0x001F low-speed optimization. Manual says 0 = disabled; empirically a no-op on
# this firmware (motion is identical at 0 or 1). Set to 0 for documented-disabled.
DRIVE_PARAM = 0

# Homing defaults (auto-calibrated on first connect). Slot 1 sits at the limit
# on this turret, so counter=0 lands at the switch — HOMING_ORIGIN_OFFSET=0
# enforces that against any tool that may have written a non-zero offset.
#
# Values match the known-good NiMotion reference (ServoMotors/SingleMotor,
# HomingConfig defaults). Earlier high speeds (search=1000, zero=200) plus
# zero_return=1 made method-17 homing never assert motion-done — the turret
# spun without the status word's RUNNING bit ever clearing. The reference's
# search=50 / zero=20 / zero_return=0 home reliably against the neg-limit switch.
HOMING_METHOD = 17
HOMING_ORIGIN_OFFSET = 0
HOMING_SEARCH_SPEED = 50
HOMING_ZERO_SPEED = 20
HOMING_ZERO_RETURN = 0
DI1_FUNCTION_NEG_LIMIT = 1

# Polling
POLL_INTERVAL_S = 0.05
# At accel=200/max_speed=250, slot 1 → slot 4 (~6600 pulses) takes ~25s. 30s
# matches SingleMotor's UI timeout (_MOVING_TIMEOUT_MS in turret_panel.py).
DEFAULT_MOVE_TIMEOUT_S = 30.0
DEFAULT_HOME_TIMEOUT_S = 30.0

# Settle time after a control-word transition before the next write.
CONTROL_WORD_SETTLE_S = 0.1

# Calibration tables: (register, expected value, label, kwargs-for-_calibrate_one).
_MOTION_PARAMS = [
    (REG_ACCEL, EXPECTED_ACCEL, "accel", {"is_32bit": True}),
    (REG_DECEL, EXPECTED_DECEL, "decel", {"is_32bit": True}),
    (REG_MAX_SPEED, EXPECTED_MAX_SPEED, "max_speed", {"is_32bit": True}),
    (REG_MIN_SPEED, EXPECTED_MIN_SPEED, "min_speed", {"is_32bit": True}),
    (REG_LOW_SPEED_OPT, DRIVE_PARAM, "drive_param", {}),
]
_HOMING_PARAMS = [
    (REG_HOMING_METHOD, HOMING_METHOD, "homing_method", {}),
    (REG_HOMING_OFFSET, HOMING_ORIGIN_OFFSET, "homing_offset", {"is_32bit": True, "signed": True}),
    (REG_HOMING_SEARCH_SPEED, HOMING_SEARCH_SPEED, "homing_search_speed", {"is_32bit": True}),
    (REG_HOMING_ZERO_SPEED, HOMING_ZERO_SPEED, "homing_zero_speed", {"is_32bit": True}),
    (REG_ZERO_RETURN, HOMING_ZERO_RETURN, "zero_return", {}),
    (REG_DI_FUNCTION, DI1_FUNCTION_NEG_LIMIT, "DI1_function", {"is_32bit": True, "mask": 0xF}),
]


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
    ) -> None:
        from control._def import OBJECTIVE_TURRET_POSITIONS

        self._slave_id = slave_id
        self._positions = dict(positions) if positions is not None else dict(OBJECTIVE_TURRET_POSITIONS)
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
            self._microstep = 2**microstep_raw
            self._pulses_per_position = int(MOTOR_STEPS_PER_REV * self._microstep * GEAR_RATIO / POSITIONS_PER_REV)

            changed = [self._calibrate_motion_params(), self._calibrate_homing_config()]
            if any(changed):
                self._save_to_eeprom()

            logger.info(
                "Turret controller ready: port=%s microstep=%d pulses/position=%d calibrated=%s",
                port,
                self._microstep,
                self._pulses_per_position,
                any(changed),
            )

            # Leave the motor de-energized; home()/move_to_objective() energize on demand.
            self._deenergize()
            self._is_open = True
        except Exception:
            self._modbus.disconnect()
            raise

    def home(self, timeout_s: float = DEFAULT_HOME_TIMEOUT_S) -> None:
        self._require_open()
        self._write_control(CW_DISABLE)
        self._write_holding(REG_RUN_MODE, MODE_HOMING)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_ABSOLUTE)
        self._write_control(CW_TRIGGER_ABSOLUTE)
        try:
            self._wait_until_idle(timeout_s)
            # Reset counter to 0 at the post-homing position so absolute slot targets
            # land at the correct physical angle.
            pre = self.current_position_pulses
            self._write_holding(REG_SET_ZERO, SET_ZERO_MAGIC)
            time.sleep(0.05)
            post = self.current_position_pulses
        finally:
            self._deenergize()
        self._current_objective = None
        logger.info("Homed: pre_set_zero=%d, post_set_zero=%d", pre, post)

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
        return self._pulses_per_position

    @property
    def current_position_pulses(self) -> int:
        return self._modbus.read_input_register_32bit(self._slave_id, REG_CURRENT_POSITION, signed=True)

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

    def _rotate_to(self, objective_name: str, timeout_s: float) -> None:
        position_index = _resolve_position(objective_name, self._positions)
        target_pulses = (position_index - 1) * self._pulses_per_position

        logger.info(
            "Rotating to %s: start=%d, target=%d",
            objective_name,
            self.current_position_pulses,
            target_pulses,
        )

        self._write_control(CW_DISABLE)
        self._write_holding(REG_RUN_MODE, MODE_POSITION)
        self._modbus.write_register_32bit(self._slave_id, REG_TARGET_POSITION, target_pulses, signed=True)
        self._write_control(CW_STARTUP)
        self._write_control(CW_ENABLE)
        self._write_control(CW_RUN_ABSOLUTE)
        self._write_control(CW_TRIGGER_ABSOLUTE)
        try:
            self._wait_for_position(target_pulses, timeout_s)
        finally:
            self._deenergize()
        logger.info(
            "Rotated to %s: target=%d, actual=%d",
            objective_name,
            target_pulses,
            self.current_position_pulses,
        )

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

    def _calibrate_motion_params(self) -> bool:
        return any([self._calibrate_one(*a, **kw) for *a, kw in _MOTION_PARAMS])

    def _calibrate_homing_config(self) -> bool:
        return any([self._calibrate_one(*a, **kw) for *a, kw in _HOMING_PARAMS])

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

    def _wait_until_idle(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        time.sleep(POLL_INTERVAL_S)
        while time.monotonic() < deadline:
            status = self._read_status_word()
            self._check_fault(status)
            if not (status & STATUS_BIT_RUNNING):
                return
            time.sleep(POLL_INTERVAL_S)
        raise TimeoutError(f"Motion did not finish within {timeout_s:.1f}s")

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
