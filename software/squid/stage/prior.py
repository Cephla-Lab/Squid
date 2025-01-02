from typing import Optional

from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig


# NOTE/TODO(imo): We want to unblock getting the interface code implemented and in use, so to start we only
# implemented the cephla stage.  As soon as we roll the interface out and get past the point of major refactors
# to use it (we want to get past that point as fast as possible!), we'll come back to implement this.
class PriorStage(AbstractStage):
    def __init__(self, stage_config: StageConfig):
        self._not_impl()

    def _not_impl(self):
        raise NotImplementedError("The Prior Stage is not yet implemented!")

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._not_impl()

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._not_impl()

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._not_impl()

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._not_impl()

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._not_impl()

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self._not_impl()

    def get_pos(self) -> Pos:
        self._not_impl()

    def get_state(self) -> StageStage:
        self._not_impl()

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        self._not_impl()

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        self._not_impl()

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
        self._not_impl()

    def get_config(self) -> StageConfig:
        return super().get_config()
