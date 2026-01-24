import time
from typing import List, Dict, Optional

from control._def import *
from control.microcontroller import Microcontroller
from squid.abc import AbstractFilterWheelController, FilterWheelInfo
from squid.config import SquidFilterWheelConfig


class SquidFilterWheel(AbstractFilterWheelController):

    def __init__(self, microcontroller: Microcontroller, config: SquidFilterWheelConfig, skip_init: bool = False):
        # TODO: need to support two filter wheels in both hardware and software.

        if microcontroller is None:
            raise Exception("Error, microcontroller is need by the SquidFilterWheelWrapper")

        self._config = config
        self.microcontroller = microcontroller

        if not skip_init:
            self.microcontroller.init_filter_wheel()
            time.sleep(0.5)
            self.microcontroller.configure_squidfilter()
            time.sleep(0.5)

            if HAS_ENCODER_W:
                self.microcontroller.set_pid_arguments(self._config.motor_slot_index, PID_P_W, PID_I_W, PID_D_W)
                self.microcontroller.configure_stage_pid(
                    self._config.motor_slot_index, self._config.transitions_per_revolution, ENCODER_FLIP_DIR_W
                )
                self.microcontroller.turn_on_stage_pid(self._config.motor_slot_index, ENABLE_PID_W)

        # emission filter position
        self.w_pos_index = self._config.min_index
        self._available_filter_wheels = []

    def move_w(self, delta):
        self.microcontroller.move_w_usteps(
            int(STAGE_MOVEMENT_SIGN_W * delta / (SCREW_PITCH_W_MM / (MICROSTEPPING_DEFAULT_W * FULLSTEPS_PER_REV_W)))
        )

    def initialize(self, filter_wheel_indices: List[int]):
        if len(filter_wheel_indices) > 1:
            raise ValueError("Multiple filter wheels are not supported yet")
        self._available_filter_wheels = filter_wheel_indices

    @property
    def available_filter_wheels(self) -> List[int]:
        return self._available_filter_wheels

    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        if index != 1:
            raise ValueError(f"Filter wheel index {index} not found")
        return FilterWheelInfo(
            index=index,
            number_of_slots=self._config.max_index - self._config.min_index + 1,
            slot_names=[str(i) for i in range(self._config.min_index, self._config.max_index + 1)],
        )

    def home(self, index: Optional[int] = None):
        self.microcontroller.home_w()
        # for homing action, need much more timeout time
        self.microcontroller.wait_till_operation_is_completed(15)
        self.move_w(self._config.offset)

        self.w_pos_index = self._config.min_index

    def next_position(self):
        if self.w_pos_index < self._config.max_index:
            self.move_w(SCREW_PITCH_W_MM / (self._config.max_index - self._config.min_index + 1))
            self.microcontroller.wait_till_operation_is_completed()
            self.w_pos_index += 1

    def previous_position(self):
        if self.w_pos_index > self._config.min_index:
            self.move_w(-(SCREW_PITCH_W_MM / (self._config.max_index - self._config.min_index + 1)))
            self.microcontroller.wait_till_operation_is_completed()
            self.w_pos_index -= 1

    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """
        Set the filter wheels to the specified positions.
        pos from 1 to 8
        """
        for index, pos in positions.items():
            if index != 1:
                raise ValueError(f"Filter wheel index {index} not found")
            if pos not in range(self._config.min_index, self._config.max_index + 1):
                raise ValueError(f"Filter wheel index {index} position {pos} is out of range")
            if pos != self.w_pos_index:
                self.move_w(
                    (pos - self.w_pos_index) * SCREW_PITCH_W_MM / (self._config.max_index - self._config.min_index + 1)
                )
                self.microcontroller.wait_till_operation_is_completed()
                self.w_pos_index = pos

    def get_filter_wheel_position(self) -> Dict[int, int]:
        return {1: self.w_pos_index}

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
