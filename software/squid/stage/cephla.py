import math
from typing import Optional

import control.microcontroller
import control._def as _def
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig, AxisConfig


class CephlaStage(AbstractStage):
    @staticmethod
    def _calc_move_timeout(distance, max_speed):
        # We arbitrarily guess that if a move takes 3x the naive "infinite acceleration" time, then it
        # probably timed out.  But always use a minimum timeout of at least 3 seconds.
        #
        # Also protect against divide by zero.
        return max((3, 3 * abs(distance) / min(0.1, abs(max_speed))))

    def __init__(self, microcontroller: control.microcontroller.Microcontroller, stage_config: StageConfig):
        super().__init__(stage_config)
        self._microcontroller = microcontroller

        # TODO(imo): configure theta here?  Do we ever have theta?
        self._configure_axis(_def.AXIS.X, stage_config.X_AXIS)
        self._configure_axis(_def.AXIS.Y, stage_config.Y_AXIS)
        self._configure_axis(_def.AXIS.Z, stage_config.Z_AXIS)

    def _configure_axis(self, microcontroller_axis_number: int, axis_config: AxisConfig):
        if axis_config.USE_ENCODER:
            # TODO(imo): The original navigationController had a "flip_direction" on configure_encoder, but it was unused in the implementation?
            self._microcontroller.configure_stage_pid(
                axis=microcontroller_axis_number,
                transitions_per_revolution = axis_config.SCREW_PITCH / axis_config.ENCODER_STEP_SIZE)
            if axis_config.PID and axis_config.PID.ENABLED:
                self._microcontroller.set_pid_arguments(microcontroller_axis_number, axis_config.PID.P, axis_config.PID.I, axis_config.PID.D)
                self._microcontroller.turn_on_stage_pid(microcontroller_axis_number)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_x_usteps(self._config.X_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(rel_mm, self.get_config().X_AXIS.MAX_SPEED))

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_y_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(rel_mm, self.get_config().Y_AXIS.MAX_SPEED))

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_z_usteps(self._config.Z_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(rel_mm, self.get_config().Z_AXIS.MAX_SPEED))

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_x_to_usteps(self._config.X_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().x_mm, self.get_config().X_AXIS.MAX_SPEED))

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_y_to_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().y_mm, self.get_config().Y_AXIS.MAX_SPEED))

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().z_mm, self.get_config().Z_AXIS.MAX_SPEED))

    def get_pos(self) -> Pos:
        pos_usteps = self._microcontroller.get_pos()
        x_mm = self._config.X_AXIS.convert_to_real_units(pos_usteps[0])
        y_mm = self._config.Y_AXIS.convert_to_real_units(pos_usteps[1])
        z_mm = self._config.Z_AXIS.convert_to_real_units(pos_usteps[2])
        theta_rad = self._config.THETA_AXIS.convert_to_real_units(pos_usteps[3])

        return Pos(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm, theta_rad=theta_rad)

    def get_state(self) -> StageStage:
        return StageStage(busy=self._microcontroller.is_busy())

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        # NOTE(imo): Arbitrarily use max speed / 5 for homing speed.  It'd be better to have it exactly!
        x_timeout = self._calc_move_timeout(
            self.get_config().X_AXIS.MAX_POSITION - self.get_config().X_AXIS.MIN_POSITION,
            self.get_config().X_AXIS.MAX_SPEED / 5.0)
        y_timeout = self._calc_move_timeout(
            self.get_config().Y_AXIS.MAX_POSITION - self.get_config().Y_AXIS.MIN_POSITION,
            self.get_config().Y_AXIS.MAX_SPEED / 5.0)
        z_timeout = self._calc_move_timeout(
            self.get_config().Z_AXIS.MAX_POSITION - self.get_config().Z_AXIS.MIN_POSITION,
            self.get_config().Z_AXIS.MAX_SPEED / 5.0)
        theta_timeout = self._calc_move_timeout(2.0 * math.pi, self.get_config().THETA_AXIS.MAX_SPEED / 5.0)
        if x and y:
            self._microcontroller.home_xy()
        elif x:
            self._microcontroller.home_x()
        elif y:
            self._microcontroller.home_y()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(max(x_timeout, y_timeout))

        if z:
            self._microcontroller.home_z()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(z_timeout)

        if theta:
            self._microcontroller.home_theta()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(theta_timeout)

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        if x:
            self._microcontroller.zero_x()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

        if y:
            self._microcontroller.zero_y()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

        if z:
            self._microcontroller.zero_z()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

        if theta:
            self._microcontroller.zero_theta()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def set_limits(self, x_pos_mm: Optional[float] = None, x_neg_mm: Optional[float] = None,
                   y_pos_mm: Optional[float] = None, y_neg_mm: Optional[float] = None, z_pos_mm: Optional[float] = None,
                   z_neg_mm: Optional[float] = None, theta_pos_rad: Optional[float] = None,
                   theta_neg_rad: Optional[float] = None):
        if x_pos_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.X_POSITIVE,
                                          self._config.X_AXIS.convert_real_units_to_ustep(x_pos_mm))

        if x_neg_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.X_NEGATIVE,
                                          self._config.X_AXIS.convert_real_units_to_ustep(x_neg_mm))

        if y_pos_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.Y_POSITIVE,
                                          self._config.Y_AXIS.convert_real_units_to_ustep(y_pos_mm))

        if y_neg_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.Y_NEGATIVE,
                                          self._config.Y_AXIS.convert_real_units_to_ustep(y_neg_mm))

        if z_pos_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.Z_POSITIVE,
                                          self._config.Z_AXIS.convert_real_units_to_ustep(z_pos_mm))

        if z_neg_mm is not None:
            self._microcontroller.set_lim(_def.LIMIT_CODE.Z_NEGATIVE,
                                          self._config.Z_AXIS.convert_real_units_to_ustep(z_neg_mm))

        if theta_neg_rad or theta_pos_rad:
            raise ValueError("Setting limits for the theta axis is not supported on the CephlaStage")
