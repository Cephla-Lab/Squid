import abc
from typing import Optional

import pydantic

from squid.config import AxisConfig, StageConfig


class Pos(pydantic.BaseModel):
    x_mm: float
    y_mm: float
    z_mm: float
    theta_rad: float

class StageStage(pydantic.BaseModel):
    busy: bool

class AbstractStage(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def move_x(self, rel_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def move_y(self, rel_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def move_z(self, rel_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def move_x_to(self, abs_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def move_y_to(self, abs_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def move_z_to(self, abs_mm: float, blocking: bool=True):
        pass

    @abc.abstractmethod
    def get_pos(self) -> Pos:
        pass

    @abc.abstractmethod
    def get_state(self) -> StageStage:
        pass

    @abc.abstractmethod
    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool=True):
        pass

    @abc.abstractmethod
    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool=True):
        pass

    @abc.abstractmethod
    def set_limits(self,
                   x_pos_mm: Optional[float] = None,
                   x_neg_mm: Optional[float] = None,
                   y_pos_mm: Optional[float] = None,
                   y_neg_mm: Optional[float] = None,
                   z_pos_mm: Optional[float] = None,
                   z_neg_mm: Optional[float] = None,
                   theta_pos_rad: Optional[float] = None,
                   theta_neg_rad: Optional[float] = None):
        pass

    def get_config(self) -> StageConfig:
        pass
