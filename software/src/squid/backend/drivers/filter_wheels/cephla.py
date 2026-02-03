import time
from typing import List, Dict, Optional

from _def import (
    AXIS,
    ENABLE_PID_W,
    ENCODER_FLIP_DIR_W,
    FULLSTEPS_PER_REV_W,
    HAS_ENCODER_W,
    MICROSTEPPING_DEFAULT_W,
    PID_D_W,
    PID_I_W,
    PID_P_W,
    SCREW_PITCH_W_MM,
    STAGE_MOVEMENT_SIGN_W,
    STAGE_MOVEMENT_SIGN_W2,
)
from squid.backend.microcontroller import CommandAborted, Microcontroller
from squid.core.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.core.config import SquidFilterWheelConfig

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)

# Mapping from wheel index to axis constant and movement functions
_WHEEL_AXIS = {1: AXIS.W, 2: AXIS.W2}
_WHEEL_MOVEMENT_SIGN = {1: STAGE_MOVEMENT_SIGN_W, 2: STAGE_MOVEMENT_SIGN_W2}


class SquidFilterWheel(AbstractFilterWheelController):
    def __init__(
        self, microcontroller: Microcontroller, config: SquidFilterWheelConfig
    ):
        if microcontroller is None:
            raise Exception(
                "Error, microcontroller is needed by the SquidFilterWheelWrapper"
            )

        self._config = config
        self.microcontroller = microcontroller

        # Per-wheel state tracking: {wheel_index: position_index}
        self._pos_index: Dict[int, int] = {}
        self._available_filter_wheels: List[int] = []

    def _configure_wheel(self, wheel_index: int):
        """Initialize and configure a single filter wheel axis."""
        axis = _WHEEL_AXIS.get(wheel_index)
        if axis is None:
            raise ValueError(f"Unsupported wheel index: {wheel_index}. Must be 1 or 2.")

        self.microcontroller.init_filter_wheel(axis)
        time.sleep(0.5)
        self.microcontroller.configure_squidfilter(axis)
        time.sleep(0.5)

        self._pos_index[wheel_index] = self._config.min_index

        if HAS_ENCODER_W:
            self.microcontroller.set_pid_arguments(
                axis, PID_P_W, PID_I_W, PID_D_W
            )
            self.microcontroller.configure_stage_pid(
                axis,
                self._config.transitions_per_revolution,
                ENCODER_FLIP_DIR_W,
            )
            if ENABLE_PID_W:
                self.microcontroller.turn_on_stage_pid(axis)

    def _move_wheel(self, wheel_index: int, delta: float):
        """Move a filter wheel by delta (in mm)."""
        sign = _WHEEL_MOVEMENT_SIGN.get(wheel_index, STAGE_MOVEMENT_SIGN_W)
        usteps = int(
            sign
            * delta
            / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W))
        )
        if wheel_index == 2:
            self.microcontroller.move_w2_usteps(usteps)
        else:
            self.microcontroller.move_w_usteps(usteps)

    def _move_to_position(self, wheel_index: int, target_pos: int):
        """Move a filter wheel to target position with automatic re-home on failure.

        If the initial movement fails (timeout or command abort), this method
        re-homes the wheel and retries the movement once.

        Args:
            wheel_index: Wheel index (1 or 2)
            target_pos: Target position index
        """
        num_slots = self._config.max_index - self._config.min_index + 1
        current_pos = self._pos_index.get(wheel_index, self._config.min_index)
        if target_pos == current_pos:
            return

        delta = (target_pos - current_pos) * SCREW_PITCH_W_MM / num_slots
        try:
            self._move_wheel(wheel_index, delta)
            self.microcontroller.wait_till_operation_is_completed()
            self._pos_index[wheel_index] = target_pos
        except (TimeoutError, CommandAborted) as e:
            _log.warning(
                f"Filter wheel {wheel_index} movement to position {target_pos} failed: {e}. "
                "Re-homing and retrying."
            )
            self.home(wheel_index)
            # After re-home, position is at min_index; move to target from there
            delta = (target_pos - self._config.min_index) * SCREW_PITCH_W_MM / num_slots
            self._move_wheel(wheel_index, delta)
            self.microcontroller.wait_till_operation_is_completed()
            self._pos_index[wheel_index] = target_pos

    def initialize(self, filter_wheel_indices: List[int]):
        for idx in filter_wheel_indices:
            if idx not in _WHEEL_AXIS:
                raise ValueError(f"Unsupported filter wheel index: {idx}. Must be 1 or 2.")
            self._configure_wheel(idx)
        self._available_filter_wheels = list(filter_wheel_indices)

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        if index not in self._available_filter_wheels:
            raise ValueError(f"Filter wheel index {index} not found")
        return FilterWheelInfo(
            index=index,
            number_of_slots=self._config.max_index - self._config.min_index + 1,
            slot_names=[
                str(i)
                for i in range(self._config.min_index, self._config.max_index + 1)
            ],
        )

    def home(self, index: Optional[int] = None):
        indices = [index] if index is not None else self._available_filter_wheels
        for idx in indices:
            if idx not in _WHEEL_AXIS:
                raise ValueError(f"Filter wheel index {idx} not found")
            if idx == 2:
                self.microcontroller.home_w2()
            else:
                self.microcontroller.home_w()
            self.microcontroller.wait_till_operation_is_completed(15)
            self._move_wheel(idx, self._config.offset)
            self.microcontroller.wait_till_operation_is_completed()
            self._pos_index[idx] = self._config.min_index

    def next_position(self, wheel_index: int = 1):
        """Advance filter wheel by one slot.

        Args:
            wheel_index: Wheel index (1 or 2). Defaults to 1 for backward compatibility.
        """
        if wheel_index not in self._pos_index:
            return
        if self._pos_index[wheel_index] < self._config.max_index:
            self._move_wheel(
                wheel_index,
                SCREW_PITCH_W_MM / (self._config.max_index - self._config.min_index + 1),
            )
            self.microcontroller.wait_till_operation_is_completed()
            self._pos_index[wheel_index] += 1

    def previous_position(self, wheel_index: int = 1):
        """Move filter wheel back by one slot.

        Args:
            wheel_index: Wheel index (1 or 2). Defaults to 1 for backward compatibility.
        """
        if wheel_index not in self._pos_index:
            return
        if self._pos_index[wheel_index] > self._config.min_index:
            self._move_wheel(
                wheel_index,
                -(SCREW_PITCH_W_MM / (self._config.max_index - self._config.min_index + 1)),
            )
            self.microcontroller.wait_till_operation_is_completed()
            self._pos_index[wheel_index] -= 1

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """Set the filter wheels to the specified positions (1-indexed)."""
        for index, pos in positions.items():
            if index not in self._available_filter_wheels:
                raise ValueError(f"Filter wheel index {index} not found")
            if pos not in range(self._config.min_index, self._config.max_index + 1):
                raise ValueError(
                    f"Filter wheel index {index} position {pos} is out of range"
                )
            self._move_to_position(index, pos)

    def get_filter_wheel_position(self) -> Dict[int, int]:
        return dict(self._pos_index)

    def set_delay_offset_ms(self, delay_offset_ms: float):
        pass

    def get_delay_offset_ms(self) -> Optional[float]:
        return 0

    def set_delay_ms(self, delay_ms: float):
        pass

    def get_delay_ms(self) -> Optional[float]:
        return 0

    def close(self):
        pass
