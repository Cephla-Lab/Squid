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

    # Errors raised by `_move_and_verify` that the re-home + retry path can recover from.
    _RECOVERABLE_MOVE_ERRORS = (TimeoutError, CommandAborted)

    def _configure_wheel(self, wheel_id: int, config: SquidFilterWheelConfig):
        """Configure a single filter wheel motor."""
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
    def _delta_to_usteps(delta: float) -> int:
        """Microsteps the firmware will be commanded to step for `delta` mm."""
        return int(STAGE_MOVEMENT_SIGN_W * delta / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W)))

    def _move_wheel(self, wheel_id: int, delta: float):
        """Move a specific wheel by delta distance.

        Args:
            wheel_id: The ID of the wheel to move.
            delta: The distance to move (in mm, typically fraction of screw pitch).
        """
        config = self._configs[wheel_id]
        motor_slot = config.motor_slot_index
        usteps = self._delta_to_usteps(delta)

        if motor_slot == 3:
            self.microcontroller.move_w_usteps(usteps)
        elif motor_slot == 4:
            self.microcontroller.move_w2_usteps(usteps)
        else:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}")

    def _verify_w_move(self, wheel_id: int, w_pos_before: int, expected_usteps_delta: int) -> None:
        """Compare actual broadcast W position against the commanded delta.

        Allows ±_W_POS_TOLERANCE_USTEPS of jitter; anything larger means the
        move was dropped or partial. Raises TimeoutError on mismatch so
        callers fall into the re-home + retry path.
        """
        actual_delta = self.microcontroller.w_pos - w_pos_before
        if abs(actual_delta - expected_usteps_delta) > W_POS_TOLERANCE_USTEPS:
            _log.warning(
                f"Filter wheel {wheel_id} W position mismatch after move "
                f"(expected delta {expected_usteps_delta} usteps, observed {actual_delta}); "
                "treating as silent failure."
            )
            raise TimeoutError(f"Filter wheel {wheel_id} did not move as commanded")

    def _move_and_verify(self, wheel_id: int, delta: float, target_pos: int) -> None:
        """Single move attempt: command the move, wait for completion, verify
        the motor actually moved (when firmware supports W broadcast for the
        wheel's axis), update tracked position. Raises TimeoutError /
        CommandAborted on failure so callers can re-home and retry.
        """
        config = self._configs[wheel_id]
        can_verify = config.motor_slot_index == 3 and self.microcontroller.supports_w_pos_broadcast()
        if can_verify:
            w_pos_before = self.microcontroller.w_pos
            expected_usteps_delta = self._delta_to_usteps(delta)
            self._move_wheel(wheel_id, delta)
            self.microcontroller.wait_till_operation_is_completed()
            self._verify_w_move(wheel_id, w_pos_before, expected_usteps_delta)
        else:
            self._move_wheel(wheel_id, delta)
            self.microcontroller.wait_till_operation_is_completed()
        self._positions[wheel_id] = target_pos

    def _move_to_position(self, wheel_id: int, target_pos: int):
        """Move wheel to target position with progressive recovery on failure.

        Recovery ladder: initial attempt → software resend → re-home + retry.
        The software resend is tried before re-homing because the most common
        failure (firmware ack glitch, like the 5.9 ms incident) leaves the
        motor unmoved, so the wheel is still at the tracked position and a
        plain resend usually succeeds without paying the ~4 s home cost.

        Raises:
            TimeoutError or CommandAborted: If all attempts fail.
        """
        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]

        if target_pos == current_pos:
            return

        step_size = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
        delta = (target_pos - current_pos) * step_size

        try:
            self._move_and_verify(wheel_id, delta, target_pos)
            return
        except self._RECOVERABLE_MOVE_ERRORS as e:
            _log.warning(f"Filter wheel {wheel_id} movement failed ({e}); retrying without re-home...")

        try:
            self._move_and_verify(wheel_id, delta, target_pos)
            _log.info(f"Filter wheel {wheel_id} software retry succeeded, now at position {target_pos}")
            return
        except self._RECOVERABLE_MOVE_ERRORS as e:
            _log.warning(f"Filter wheel {wheel_id} software retry failed ({e}); re-homing to re-sync...")

        self._home_wheel(wheel_id)
        # Position is now at min_index after homing; recompute delta.
        current_pos = self._positions[wheel_id]
        delta = (target_pos - current_pos) * step_size
        try:
            self._move_and_verify(wheel_id, delta, target_pos)
            _log.info(f"Filter wheel {wheel_id} recovery via re-home succeeded, now at position {target_pos}")
        except self._RECOVERABLE_MOVE_ERRORS:
            _log.error(f"Filter wheel {wheel_id} movement failed even after re-home. Hardware may need attention.")
            raise

    def _home_wheel(self, wheel_id: int):
        """Home a specific wheel.

        Args:
            wheel_id: The ID of the wheel to home.
        """
        config = self._configs[wheel_id]
        motor_slot = config.motor_slot_index

        if motor_slot == 3:
            self.microcontroller.home_w()
        elif motor_slot == 4:
            self.microcontroller.home_w2()
        else:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}")

        # Wait for homing to complete (needs longer timeout)
        self.microcontroller.wait_till_operation_is_completed(15)

        # Move to offset position
        self._move_wheel(wheel_id, config.offset)
        self.microcontroller.wait_till_operation_is_completed()

        # Reset position tracking
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

    # Backward compatibility methods
    def move_w(self, delta: float):
        """Move the first wheel by delta. For backward compatibility."""
        self._move_wheel(1, delta)
