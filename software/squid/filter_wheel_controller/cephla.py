import time
from typing import List, Dict, Optional, Union

import squid.logging
from control._def import *
from control.microcontroller import CommandAborted, Microcontroller
from squid.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.config import SquidFilterWheelConfig


_log = squid.logging.get_logger(__name__)


class SquidFilterWheel(AbstractFilterWheelController):
    """SQUID filter wheel controller supporting multiple filter wheels.

    Each wheel is identified by a wheel_id (typically 1, 2, etc.) and has its own
    configuration including motor_slot_index which determines which hardware axis to use:
    - motor_slot_index 3 -> W axis (first filter wheel)
    - motor_slot_index 4 -> W2 axis (second filter wheel)

    Note: W and W2 share the same motor settings (microstepping, current, velocity,
    acceleration, screw pitch) as they use identical hardware.
    """

    def __init__(
        self,
        microcontroller: Microcontroller,
        configs: Union[SquidFilterWheelConfig, Dict[int, SquidFilterWheelConfig]],
        skip_init: bool = False,
    ):
        """Initialize the SQUID filter wheel controller.

        Args:
            microcontroller: The microcontroller instance for hardware control.
            configs: Either a single SquidFilterWheelConfig (backward compatible) or
                     a dict mapping wheel_id -> SquidFilterWheelConfig for multi-wheel support.
            skip_init: If True, skip hardware initialization (for restart after settings change).
        """
        if microcontroller is None:
            raise Exception("Error, microcontroller is needed by the SquidFilterWheel")

        self.microcontroller = microcontroller

        # Convert single config to dict format for uniform handling
        if isinstance(configs, SquidFilterWheelConfig):
            self._configs: Dict[int, SquidFilterWheelConfig] = {1: configs}
        else:
            self._configs = configs

        # Track per-wheel positions (wheel_id -> position index)
        self._positions: Dict[int, int] = {}

        if not skip_init:
            # Configure each wheel
            for wheel_id, config in self._configs.items():
                self._configure_wheel(wheel_id, config)
                # Initialize position tracking to min_index
                self._positions[wheel_id] = config.min_index
        else:
            # Just initialize position tracking without hardware init
            for wheel_id, config in self._configs.items():
                self._positions[wheel_id] = config.min_index

        self._available_filter_wheels: List[int] = []

    # Map motor_slot_index to AXIS protocol constants for MCU communication.
    # Note: These are PROTOCOL constants (AXIS.W=5, AXIS.W2=6), NOT firmware array indices.
    # The firmware has a separate mapping: w=3, w2=4 for internal arrays.
    # The protocol_axis_to_internal() function in firmware handles this conversion.
    _MOTOR_SLOT_TO_AXIS = {3: AXIS.W, 4: AXIS.W2}

    # Map motor_slot_index to the Microcontroller method names that drive
    # that axis. Keeps the slot→method dispatch in one place instead of
    # branching `if motor_slot == 3 / == 4` at every call site.
    _MOTOR_SLOT_MCU_METHODS = {
        3: {"home": "home_w", "move_to_usteps": "move_w_to_usteps"},
        4: {"home": "home_w2", "move_to_usteps": "move_w2_to_usteps"},
    }

    _RECOVERABLE_MOVE_ERRORS = (TimeoutError, CommandAborted)

    # Minimum firmware that anchors the W/W2 driver position to 0 at home
    # (finalize_homing_w/_w2) and reports CMD_EXECUTION_ERROR on failed
    # moves. Sending MOVETO_W against older firmware would target the
    # wrong absolute slot because X_ACTUAL would still be at the
    # limit-switch latch value.
    _MIN_FIRMWARE_VERSION = (1, 2)

    def _configure_wheel(self, wheel_id: int, config: SquidFilterWheelConfig):
        """Configure a single filter wheel motor."""
        fw = self.microcontroller.firmware_version
        if fw < self._MIN_FIRMWARE_VERSION:
            min_major, min_minor = self._MIN_FIRMWARE_VERSION
            raise RuntimeError(
                f"SquidFilterWheel requires firmware >= v{min_major}.{min_minor} "
                f"(got v{fw[0]}.{fw[1]}). Older firmware does not anchor the W "
                f"axis to 0 after homing, so absolute MOVETO_W targets would "
                f"land at the wrong slot. Re-flash firmware from firmware/controller."
            )

        motor_slot = config.motor_slot_index
        axis = self._MOTOR_SLOT_TO_AXIS.get(motor_slot)
        if axis is None:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}. Expected 3 (W) or 4 (W2).")

        self.microcontroller.init_filter_wheel(axis)
        time.sleep(0.5)
        self.microcontroller.configure_squidfilter(axis)
        time.sleep(0.5)

        # Common PID setup for both wheels (they share identical encoder settings)
        # Use protocol axis (AXIS.W / AXIS.W2), not motor_slot index (3 / 4),
        # because the firmware's protocol_axis_to_internal() handles mapping.
        if HAS_ENCODER_W:
            self.microcontroller.set_pid_arguments(axis, PID_P_W, PID_I_W, PID_D_W)
            self.microcontroller.configure_stage_pid(axis, config.transitions_per_revolution, ENCODER_FLIP_DIR_W)
            self.microcontroller.turn_on_stage_pid(axis, ENABLE_PID_W)

    @staticmethod
    def _delta_to_usteps(delta_mm: float) -> int:
        """Microsteps the firmware will be commanded to step for `delta_mm` mm.

        Includes STAGE_MOVEMENT_SIGN_W so the result already accounts for
        which direction the motor needs to drive to advance through slots.
        """
        return int(
            STAGE_MOVEMENT_SIGN_W * delta_mm / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W))
        )

    @staticmethod
    def _target_pos_to_usteps(config: SquidFilterWheelConfig, target_pos: int) -> int:
        """Absolute target microstep address for a given slot index.

        Assumes the firmware anchors X_ACTUAL to 0 at the home reference
        (see finalize_homing_w / finalize_homing_w2 in firmware).
        """
        step_size_mm = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
        target_mm_from_home = config.offset + (target_pos - config.min_index) * step_size_mm
        return SquidFilterWheel._delta_to_usteps(target_mm_from_home)

    def _mcu_method(self, wheel_id: int, action: str):
        """Resolve the Microcontroller method for `action` on this wheel's axis."""
        motor_slot = self._configs[wheel_id].motor_slot_index
        methods = self._MOTOR_SLOT_MCU_METHODS.get(motor_slot)
        if methods is None:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}. Expected 3 (W) or 4 (W2).")
        return getattr(self.microcontroller, methods[action])

    def _move_to_usteps(self, wheel_id: int, usteps: int):
        """Dispatch an absolute MOVETO_W / MOVETO_W2 by motor_slot_index."""
        self._mcu_method(wheel_id, "move_to_usteps")(usteps)

    def _move_to_position(self, wheel_id: int, target_pos: int):
        """Move wheel to target position using absolute MOVETO; recover on failure.

        Recovery is conditioned on failure type, because the absolute-move
        approach already self-corrects on the next *successful* command —
        the goal here is just to make sure that next command actually goes
        out, with the right re-home cost.

        - CommandAborted (CMD_EXECUTION_ERROR): firmware rejected the move
          before the motor moved (e.g. tmc4361A_moveTo returned non-zero
          or move arrived before INITFILTERWHEEL). The motor didn't move;
          a plain resend is safe and avoids the ~4 s re-home cost.
        - TimeoutError (ack never arrived): motor state is uncertain (could
          be partially moved). Re-home to re-anchor the coordinate frame,
          then retry to the same absolute target.

        Raises:
            TimeoutError or CommandAborted: If all attempts fail.
        """
        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]

        if target_pos == current_pos:
            return

        target_usteps = self._target_pos_to_usteps(config, target_pos)

        try:
            self._move_to_usteps(wheel_id, target_usteps)
            self.microcontroller.wait_till_operation_is_completed()
            self._positions[wheel_id] = target_pos
            return
        except CommandAborted as e:
            _log.warning(f"Filter wheel {wheel_id} command aborted ({e}); resending in software...")
            try:
                self._move_to_usteps(wheel_id, target_usteps)
                self.microcontroller.wait_till_operation_is_completed()
                self._positions[wheel_id] = target_pos
                _log.info(f"Filter wheel {wheel_id} software resend succeeded, now at position {target_pos}")
                return
            except self._RECOVERABLE_MOVE_ERRORS as e2:
                _log.warning(f"Filter wheel {wheel_id} resend also failed ({e2}); re-homing to re-sync...")
        except TimeoutError as e:
            _log.warning(f"Filter wheel {wheel_id} move uncertain ({e}); re-homing to re-sync...")

        self._home_wheel(wheel_id)
        target_usteps = self._target_pos_to_usteps(config, target_pos)
        try:
            self._move_to_usteps(wheel_id, target_usteps)
            self.microcontroller.wait_till_operation_is_completed()
            self._positions[wheel_id] = target_pos
            _log.info(f"Filter wheel {wheel_id} recovery via re-home succeeded, now at position {target_pos}")
        except self._RECOVERABLE_MOVE_ERRORS:
            _log.error(f"Filter wheel {wheel_id} movement failed even after re-home. Hardware may need attention.")
            raise

    def _home_wheel(self, wheel_id: int):
        """Home a wheel, then drive to slot 0 absolutely.

        The firmware anchors the driver's X_ACTUAL counter to 0 at the
        home reference, so the host can target absolute slot positions
        as `slot_index * usteps_per_slot + offset_usteps` thereafter.
        """
        config = self._configs[wheel_id]

        self._mcu_method(wheel_id, "home")()
        self.microcontroller.wait_till_operation_is_completed(15)

        self._move_to_usteps(wheel_id, self._delta_to_usteps(config.offset))
        self.microcontroller.wait_till_operation_is_completed()

        self._positions[wheel_id] = config.min_index

    def initialize(self, filter_wheel_indices: List[int]):
        """Initialize the filter wheel controller with the given wheel indices.

        Args:
            filter_wheel_indices: List of wheel indices to activate.
        """
        # Validate that all requested wheels are configured
        for idx in filter_wheel_indices:
            if idx not in self._configs:
                raise ValueError(f"Filter wheel index {idx} is not configured")
        self._available_filter_wheels = filter_wheel_indices

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        """Get information about a specific filter wheel.

        Args:
            index: The wheel index.

        Returns:
            FilterWheelInfo with slot count and names.
        """
        if index not in self._configs:
            raise ValueError(f"Filter wheel index {index} not found")

        config = self._configs[index]
        return FilterWheelInfo(
            index=index,
            number_of_slots=config.max_index - config.min_index + 1,
            slot_names=[str(i) for i in range(config.min_index, config.max_index + 1)],
        )

    def home(self, index: Optional[int] = None):
        """Home filter wheel(s).

        Args:
            index: Specific wheel index to home. If None, homes all configured wheels.
        """
        if index is not None:
            if index not in self._configs:
                raise ValueError(f"Filter wheel index {index} not found")
            self._home_wheel(index)
        else:
            # Home all wheels
            for wheel_id in self._configs.keys():
                self._home_wheel(wheel_id)

    def _step_position(self, wheel_id: int, direction: int):
        """Move position by one step in the given direction.

        Args:
            wheel_id: The ID of the wheel to move.
            direction: +1 for next position, -1 for previous position.
        """
        if wheel_id not in self._configs:
            raise ValueError(f"Filter wheel index {wheel_id} not found")

        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]
        new_pos = current_pos + direction

        if config.min_index <= new_pos <= config.max_index:
            self._move_to_position(wheel_id, new_pos)

    def next_position(self, wheel_id: int = 1):
        """Move to the next position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        self._step_position(wheel_id, 1)

    def previous_position(self, wheel_id: int = 1):
        """Move to the previous position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        self._step_position(wheel_id, -1)

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """Set filter wheel positions.

        Args:
            positions: Dict mapping wheel_id -> target position.
                       Position values are 1-indexed (typically 1-8).
        """
        for wheel_id, pos in positions.items():
            if wheel_id not in self._configs:
                raise ValueError(f"Filter wheel index {wheel_id} not found")

            config = self._configs[wheel_id]
            if pos not in range(config.min_index, config.max_index + 1):
                raise ValueError(f"Filter wheel {wheel_id} position {pos} is out of range")

            self._move_to_position(wheel_id, pos)

    def get_filter_wheel_position(self) -> Dict[int, int]:
        """Get current positions of all configured wheels.

        Returns:
            Dict mapping wheel_id -> current position.
        """
        return dict(self._positions)

    def set_delay_offset_ms(self, delay_offset_ms: float):
        """Set delay offset (not used by SQUID filter wheel)."""
        pass

    def get_delay_offset_ms(self) -> Optional[float]:
        """Get delay offset (always 0 for SQUID filter wheel)."""
        return 0

    def set_delay_ms(self, delay_ms: float):
        """Set delay (not used by SQUID filter wheel)."""
        pass

    def get_delay_ms(self) -> Optional[float]:
        """Get delay (always 0 for SQUID filter wheel)."""
        return 0

    def close(self):
        """Close the filter wheel controller (no-op for SQUID)."""
        pass
