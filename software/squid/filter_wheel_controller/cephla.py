import time
from typing import List, Dict, Optional, Union

from control._def import *
from control.microcontroller import Microcontroller
from squid.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.config import SquidFilterWheelConfig


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
            # Initialize filter wheel hardware
            self.microcontroller.init_filter_wheel()
            time.sleep(0.5)

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

    def _configure_wheel(self, wheel_id: int, config: SquidFilterWheelConfig):
        """Configure a single filter wheel motor."""
        motor_slot = config.motor_slot_index

        if motor_slot == 3:
            # W axis (first filter wheel)
            self.microcontroller.configure_squidfilter()
            time.sleep(0.5)
            if HAS_ENCODER_W:
                self.microcontroller.set_pid_arguments(motor_slot, PID_P_W, PID_I_W, PID_D_W)
                self.microcontroller.configure_stage_pid(
                    motor_slot, config.transitions_per_revolution, ENCODER_FLIP_DIR_W
                )
                self.microcontroller.turn_on_stage_pid(motor_slot, ENABLE_PID_W)
        elif motor_slot == 4:
            # W2 axis (second filter wheel)
            self.microcontroller.init_filter_wheel_w2()
            time.sleep(0.5)
            self.microcontroller.configure_squidfilter_w2()
            time.sleep(0.5)
            # W2 uses same encoder settings as W if present
            if HAS_ENCODER_W:
                self.microcontroller.set_pid_arguments(motor_slot, PID_P_W, PID_I_W, PID_D_W)
                self.microcontroller.configure_stage_pid(
                    motor_slot, config.transitions_per_revolution, ENCODER_FLIP_DIR_W
                )
                self.microcontroller.turn_on_stage_pid(motor_slot, ENABLE_PID_W)
        else:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}. Expected 3 (W) or 4 (W2).")

    def _move_wheel(self, wheel_id: int, delta: float):
        """Move a specific wheel by delta distance.

        Args:
            wheel_id: The ID of the wheel to move.
            delta: The distance to move (in mm, typically fraction of screw pitch).
        """
        config = self._configs[wheel_id]
        motor_slot = config.motor_slot_index
        usteps = int(
            STAGE_MOVEMENT_SIGN_W * delta / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W))
        )

        if motor_slot == 3:
            self.microcontroller.move_w_usteps(usteps)
        elif motor_slot == 4:
            self.microcontroller.move_w2_usteps(usteps)
        else:
            raise ValueError(f"Unsupported motor_slot_index: {motor_slot}")

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

    def next_position(self, wheel_id: int = 1):
        """Move to the next position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        if wheel_id not in self._configs:
            raise ValueError(f"Filter wheel index {wheel_id} not found")

        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]

        if current_pos < config.max_index:
            step_size = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
            self._move_wheel(wheel_id, step_size)
            self.microcontroller.wait_till_operation_is_completed()
            self._positions[wheel_id] = current_pos + 1

    def previous_position(self, wheel_id: int = 1):
        """Move to the previous position on a wheel.

        Args:
            wheel_id: The wheel to move (defaults to 1 for backward compatibility).
        """
        if wheel_id not in self._configs:
            raise ValueError(f"Filter wheel index {wheel_id} not found")

        config = self._configs[wheel_id]
        current_pos = self._positions[wheel_id]

        if current_pos > config.min_index:
            step_size = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
            self._move_wheel(wheel_id, -step_size)
            self.microcontroller.wait_till_operation_is_completed()
            self._positions[wheel_id] = current_pos - 1

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

            current_pos = self._positions[wheel_id]
            if pos != current_pos:
                step_size = SCREW_PITCH_W_MM / (config.max_index - config.min_index + 1)
                delta = (pos - current_pos) * step_size
                self._move_wheel(wheel_id, delta)
                self.microcontroller.wait_till_operation_is_completed()
                self._positions[wheel_id] = pos

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
