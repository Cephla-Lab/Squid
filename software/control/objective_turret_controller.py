"""Controller for a motorized 4-position objective turret (NiMotion RS-485 stepper).

The real controller talks Modbus-RTU to the motor. A simulation twin mirrors
the public API for CI and offline development.
"""

from __future__ import annotations

import logging
from typing import Optional

from serial.tools import list_ports

import squid.abc

logger = logging.getLogger(__name__)

# Turret mechanics
GEAR_RATIO = 132 / 48
MOTOR_STEPS_PER_REV = 200
POSITIONS_PER_REV = 4  # 90 degrees per objective
POSITION_TOLERANCE_PULSES = 50

# NiMotion Modbus register map
REG_SAVE_PARAMS = 0x0008
REG_DI_FUNCTION = 0x002C
REG_MICROSTEP = 0x001A
REG_STATUS_WORD = 0x001F
REG_CURRENT_POSITION = 0x0021
REG_RUN_MODE = 0x0039
REG_CONTROL_WORD = 0x0051
REG_TARGET_POSITION = 0x0053
REG_MAX_SPEED = 0x005B
REG_ACCEL = 0x005F
REG_DECEL = 0x0061
REG_HOMING_OFFSET = 0x0069
REG_HOMING_METHOD = 0x006B
REG_ZERO_RETURN = 0x0072
REG_CLEAR_ERROR_STORAGE = 0x0073

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

# Run modes
MODE_POSITION = 1
MODE_HOMING = 3

# Status word bits
STATUS_BIT_FAULT = 1 << 3
STATUS_BIT_RUNNING = 1 << 12

# Motion parameter defaults (auto-calibrated on first connect)
EXPECTED_ACCEL = 200
EXPECTED_DECEL = 200
EXPECTED_MAX_SPEED = 250

# Homing defaults (auto-calibrated on first connect)
HOMING_METHOD = 17
HOMING_ORIGIN_OFFSET = 500
HOMING_ZERO_RETURN = 1
DI1_FUNCTION_NEG_LIMIT = 1

# Polling
POLL_INTERVAL_S = 0.05
DEFAULT_MOVE_TIMEOUT_S = 10.0
DEFAULT_HOME_TIMEOUT_S = 30.0


def _resolve_position(objective_name: str, positions: dict) -> int:
    try:
        return positions[objective_name]
    except KeyError:
        raise KeyError(f"Unknown objective '{objective_name}'. Valid names: {sorted(positions)}") from None


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

    def move_to_objective(self, objective_name: str, timeout_s: float = DEFAULT_MOVE_TIMEOUT_S) -> None:
        self._require_open()
        _resolve_position(objective_name, self._positions)
        if self._current_objective == objective_name:
            return

        captured_z = self._retract_z_if_possible()
        self._current_objective = objective_name
        self._restore_z_if_captured(captured_z)

        logger.info(
            "Simulated turret moved to %s (position %d)",
            objective_name,
            self._positions[objective_name],
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
