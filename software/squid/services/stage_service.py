# squid/services/stage_service.py
"""Service for stage operations."""
from typing import Optional, TYPE_CHECKING

from squid.services.base import BaseService
from squid.events import (
    EventBus,
    MoveStageCommand,
    MoveStageToCommand,
    HomeStageCommand,
    StagePositionChanged,
)

if TYPE_CHECKING:
    from squid.abc import AbstractStage, Pos


class StageService(BaseService):
    """
    Service layer for stage operations.

    Handles movement, homing, zeroing.
    Widgets should use this service instead of calling stage directly.
    """

    def __init__(self, stage: "AbstractStage", event_bus: EventBus):
        super().__init__(event_bus)
        self._stage = stage

        self.subscribe(MoveStageCommand, self._on_move_command)
        self.subscribe(MoveStageToCommand, self._on_move_to_command)
        self.subscribe(HomeStageCommand, self._on_home_command)

    def _on_move_command(self, event: MoveStageCommand):
        if event.axis == 'x':
            self.move_x(event.distance_mm)
        elif event.axis == 'y':
            self.move_y(event.distance_mm)
        elif event.axis == 'z':
            self.move_z(event.distance_mm)

    def _on_move_to_command(self, event: MoveStageToCommand):
        self.move_to(event.x_mm, event.y_mm, event.z_mm)

    def _on_home_command(self, event: HomeStageCommand):
        self.home(event.x, event.y, event.z)

    def move_x(self, distance_mm: float, blocking: bool = True):
        """Move X axis by relative distance."""
        self._stage.move_x(distance_mm, blocking)
        self._publish_position()

    def move_y(self, distance_mm: float, blocking: bool = True):
        """Move Y axis by relative distance."""
        self._stage.move_y(distance_mm, blocking)
        self._publish_position()

    def move_z(self, distance_mm: float, blocking: bool = True):
        """Move Z axis by relative distance."""
        self._stage.move_z(distance_mm, blocking)
        self._publish_position()

    def move_to(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
        blocking: bool = True
    ):
        """Move to absolute position."""
        if x_mm is not None:
            self._stage.move_x_to(x_mm, blocking)
        if y_mm is not None:
            self._stage.move_y_to(y_mm, blocking)
        if z_mm is not None:
            self._stage.move_z_to(z_mm, blocking)
        self._publish_position()

    def get_position(self) -> "Pos":
        """Get current position."""
        return self._stage.get_pos()

    def home(self, x: bool = False, y: bool = False, z: bool = False):
        """Home specified axes."""
        self._stage.home(x, y, z)
        self._publish_position()

    def zero(self, x: bool = False, y: bool = False, z: bool = False):
        """Zero specified axes."""
        self._stage.zero(x, y, z)
        self._publish_position()

    def _publish_position(self):
        """Publish current position."""
        pos = self._stage.get_pos()
        self.publish(StagePositionChanged(
            x_mm=pos.x_mm,
            y_mm=pos.y_mm,
            z_mm=pos.z_mm
        ))
