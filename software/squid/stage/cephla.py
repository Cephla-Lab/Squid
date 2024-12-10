from typing import Optional

import control.microcontroller
import control._def as _def
import squid.logging
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig


class CephlaStage(AbstractStage):
    def __init__(self, microcontroller: control.microcontroller.Microcontroller, stage_config: StageConfig):
        self._microcontroller = microcontroller
        self._config = stage_config
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_x_usteps(self._config.X_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_y_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._microcontroller.move_z_usteps(self._config.Z_AXIS.convert_real_units_to_ustep(rel_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_x_to_usteps(self._config.X_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_y_to_usteps(self._config.Y_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self._microcontroller.move_z_to_usteps(self._config.Z_AXIS.convert_real_units_to_ustep(abs_mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

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
        if x and y:
            self._microcontroller.home_xy()
        elif x:
            self._microcontroller.home_x()
        elif y:
            self._microcontroller.home_y()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

        if z:
            self._microcontroller.home_z()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

        if theta:
            self._microcontroller.home_theta()
        if blocking:
            self._microcontroller.wait_till_operation_is_completed()

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
