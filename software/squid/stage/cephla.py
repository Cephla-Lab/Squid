import math
from typing import Optional

import control.microcontroller
import control._def as _def
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig, AxisConfig


class CephlaStage(AbstractStage):
    _BACKLASH_COMPENSATION_DISTANCE_MM = 0.005

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
                transitions_per_revolution=axis_config.SCREW_PITCH / axis_config.ENCODER_STEP_SIZE,
            )
            if axis_config.PID and axis_config.PID.ENABLED:
                self._microcontroller.set_pid_arguments(
                    microcontroller_axis_number, axis_config.PID.P, axis_config.PID.I, axis_config.PID.D
                )
                self._microcontroller.turn_on_stage_pid(microcontroller_axis_number)

    def x_mm_to_usteps(self, mm: float):
        return self._config.X_AXIS.convert_real_units_to_ustep(mm)

    def y_mm_to_usteps(self, mm: float):
        return self._config.Y_AXIS.convert_real_units_to_ustep(mm)

    def z_mm_to_usteps(self, mm: float):
        return self._config.Z_AXIS.convert_real_units_to_ustep(mm)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_x_usteps(self._config.X_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(rel_mm, self.get_config().X_AXIS.MAX_SPEED)
            )

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_y_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(rel_mm, self.get_config().Y_AXIS.MAX_SPEED)
            )

    def move_z(self, rel_mm: float, blocking: bool = True):
        # From Hongquan, we want the z axis to rest on the "up" (wrt gravity) direction of gravity. So if we
        # are moving in the negative (down) z direction, we need to move past our mark a bit then
        # back up.  If we are already moving in the "up" position, we can move straight there.
        need_clear_backlash = rel_mm < 0

        # NOTE(imo): It seems really tricky to only clear backlash if via the blocking call?
        final_rel_move_mm = rel_mm
        if blocking and need_clear_backlash:
            backlash_offset = -CephlaStage._BACKLASH_COMPENSATION_DISTANCE_MM
            final_rel_move_mm = -backlash_offset
            # Move past our final position, so we can move up to the final position and
            # rest on the downside of the drive mechanism.  But make sure we don't drive past the min position
            # to do this.
            rel_move_with_backlash_offset_mm = rel_mm + backlash_offset
            rel_move_with_backlash_offset_usteps = self._config.Z_AXIS.convert_real_units_to_ustep(
                rel_move_with_backlash_offset_mm
            )
            self._microcontroller.move_z_usteps(rel_move_with_backlash_offset_usteps)
            if blocking:
                self._microcontroller.wait_till_operation_is_completed(
                    self._calc_move_timeout(rel_move_with_backlash_offset_mm, self.get_config().Z_AXIS.MAX_SPEED)
                )

        self._microcontroller.move_z_usteps(self._config.Z_AXIS.convert_real_units_to_ustep(final_rel_move_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(final_rel_move_mm, self.get_config().Z_AXIS.MAX_SPEED)
            )

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_x_to_usteps(self._config.X_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().x_mm, self.get_config().X_AXIS.MAX_SPEED)
            )

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_y_to_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().y_mm, self.get_config().Y_AXIS.MAX_SPEED)
            )

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        # From Hongquan, we want the z axis to rest on the "up" (wrt gravity) direction of gravity. So if we
        # are moving in the negative (down) z direction, we need to move past our mark a bit then
        # back up.  If we are already moving in the "up" position, we can move straight there.
        need_clear_backlash = abs_mm < self.get_pos().z_mm

        # NOTE(imo): It seems really tricky to only clear backlash if via the blocking call?
        if blocking and need_clear_backlash:
            backlash_offset = -CephlaStage._BACKLASH_COMPENSATION_DISTANCE_MM
            # Move past our final position, so we can move up to the final position and
            # rest on the downside of the drive mechanism.  But make sure we don't drive past the min position
            # to do this.
            clamped_z_backlash_pos = max(abs_mm + backlash_offset, self.get_config().Z_AXIS.MIN_POSITION)
            clamped_z_backlash_pos_usteps = self._config.Z_AXIS.convert_real_units_to_ustep(clamped_z_backlash_pos)
            self._microcontroller.move_z_to_usteps(clamped_z_backlash_pos_usteps)
            if blocking:
                self._microcontroller.wait_till_operation_is_completed(
                    self._calc_move_timeout(
                        clamped_z_backlash_pos - self.get_pos().z_mm, self.get_config().Z_AXIS.MAX_SPEED
                    )
                )

        self._microcontroller.move_z_to_usteps(self._config.Z_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(
                self._calc_move_timeout(abs_mm - self.get_pos().z_mm, self.get_config().Z_AXIS.MAX_SPEED)
            )

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
            self.get_config().X_AXIS.MAX_SPEED / 5.0,
        )
        y_timeout = self._calc_move_timeout(
            self.get_config().Y_AXIS.MAX_POSITION - self.get_config().Y_AXIS.MIN_POSITION,
            self.get_config().Y_AXIS.MAX_SPEED / 5.0,
        )
        z_timeout = self._calc_move_timeout(
            self.get_config().Z_AXIS.MAX_POSITION - self.get_config().Z_AXIS.MIN_POSITION,
            self.get_config().Z_AXIS.MAX_SPEED / 5.0,
        )
        theta_timeout = self._calc_move_timeout(2.0 * math.pi, self.get_config().THETA_AXIS.MAX_SPEED / 5.0)

        x_dir = control.microcontroller.movement_sign_to_homing_direction(self.get_config().X_AXIS.MOVEMENT_SIGN)
        y_dir = control.microcontroller.movement_sign_to_homing_direction(self.get_config().Y_AXIS.MOVEMENT_SIGN)
        z_dir = control.microcontroller.movement_sign_to_homing_direction(self.get_config().Z_AXIS.MOVEMENT_SIGN)
        theta_dir = control.microcontroller.movement_sign_to_homing_direction(
            self.get_config().THETA_AXIS.MOVEMENT_SIGN
        )

        if x and y:
            self._microcontroller.home_xy(homing_direction_x=x_dir, homing_direction_y=y_dir)
        elif x:
            self._microcontroller.home_x(homing_direction=x_dir)
        elif y:
            self._microcontroller.home_y(homing_direction=y_dir)
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(max(x_timeout, y_timeout))

        if z:
            self._microcontroller.home_z(homing_direction=z_dir)
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(z_timeout)

        if theta:
            self._microcontroller.home_theta(homing_direction=theta_dir)
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

    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
        theta_pos_rad: Optional[float] = None,
        theta_neg_rad: Optional[float] = None,
    ):
        # Our underlying movement direction might be switched.  If it is, then the positive movements here at
        # the AbstractStage level will result in negative movements on the real hardware (and vice versa).  This means
        # that if our movement sign is swapped, we need to swap pos/neg limits at the lower level here.
        def limit_codes_for(movement_sign, non_inverted_neg, non_inverted_pos):
            # Return the limit codes to use for pos and neg limits in the form of (neg, pos)
            if movement_sign == 1:
                return non_inverted_neg, non_inverted_pos
            elif movement_sign == -1:
                return non_inverted_pos, non_inverted_neg
            else:
                raise ValueError(f"Only 1 and -1 are valid movement signs, but got: {movement_sign}")

        (x_neg_code, x_pos_code) = limit_codes_for(
            self._config.X_AXIS.MOVEMENT_SIGN, _def.LIMIT_CODE.X_NEGATIVE, _def.LIMIT_CODE.X_POSITIVE
        )
        (y_neg_code, y_pos_code) = limit_codes_for(
            self._config.Y_AXIS.MOVEMENT_SIGN, _def.LIMIT_CODE.Y_NEGATIVE, _def.LIMIT_CODE.Y_POSITIVE
        )
        (z_neg_code, z_pos_code) = limit_codes_for(
            self._config.Z_AXIS.MOVEMENT_SIGN, _def.LIMIT_CODE.Z_NEGATIVE, _def.LIMIT_CODE.Z_POSITIVE
        )

        if x_pos_mm is not None:
            self._microcontroller.set_lim(x_pos_code, self._config.X_AXIS.convert_real_units_to_ustep(x_pos_mm))

        if x_neg_mm is not None:
            self._microcontroller.set_lim(x_neg_code, self._config.X_AXIS.convert_real_units_to_ustep(x_neg_mm))

        if y_pos_mm is not None:
            self._microcontroller.set_lim(y_pos_code, self._config.Y_AXIS.convert_real_units_to_ustep(y_pos_mm))

        if y_neg_mm is not None:
            self._microcontroller.set_lim(y_neg_code, self._config.Y_AXIS.convert_real_units_to_ustep(y_neg_mm))

        if z_pos_mm is not None:
            self._microcontroller.set_lim(z_pos_code, self._config.Z_AXIS.convert_real_units_to_ustep(z_pos_mm))

        if z_neg_mm is not None:
            self._microcontroller.set_lim(z_neg_code, self._config.Z_AXIS.convert_real_units_to_ustep(z_neg_mm))

        if theta_neg_rad or theta_pos_rad:
            raise ValueError("Setting limits for the theta axis is not supported on the CephlaStage")
