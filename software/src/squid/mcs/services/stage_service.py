# squid/services/stage_service.py
"""Service for stage operations."""

import threading
from typing import Optional, Callable, TYPE_CHECKING
from threading import Thread

from squid.mcs.services.base import BaseService
from squid.core.events import (
    EventBus,
    MoveStageCommand,
    MoveStageToCommand,
    HomeStageCommand,
    ZeroStageCommand,
    MoveStageRelativeCommand,
    MoveStageToLoadingPositionCommand,
    MoveStageToScanningPositionCommand,
    StagePositionChanged,
)
import _def as _def
import squid.core.utils.hardware_utils

if TYPE_CHECKING:
    from squid.core.abc import AbstractStage, Pos


class StageService(BaseService):
    """
    Service layer for stage operations.

    Handles movement, homing, zeroing.
    Widgets should use this service instead of calling stage directly.
    """

    def __init__(self, stage: "AbstractStage", event_bus: EventBus):
        super().__init__(event_bus)
        self._stage = stage
        self._lock = threading.RLock()
        self._scanning_position_z_mm: Optional[float] = (
            None  # Track Z position for loading/scanning
        )

        self.subscribe(MoveStageCommand, self._on_move_command)
        self.subscribe(MoveStageRelativeCommand, self._on_move_relative_command)
        self.subscribe(MoveStageToCommand, self._on_move_to_command)
        self.subscribe(HomeStageCommand, self._on_home_command)
        self.subscribe(ZeroStageCommand, self._on_zero_command)
        self.subscribe(
            MoveStageToLoadingPositionCommand, self._on_move_to_loading_command
        )
        self.subscribe(
            MoveStageToScanningPositionCommand, self._on_move_to_scanning_command
        )

    def _on_move_command(self, event: MoveStageCommand):
        if event.axis == "x":
            self.move_x(event.distance_mm)
        elif event.axis == "y":
            self.move_y(event.distance_mm)
        elif event.axis == "z":
            self.move_z(event.distance_mm)

    def _on_move_relative_command(self, event: MoveStageRelativeCommand):
        """Handle relative move with per-axis values."""
        if event.x_mm is not None:
            self.move_x(event.x_mm)
        if event.y_mm is not None:
            self.move_y(event.y_mm)
        if event.z_mm is not None:
            self.move_z(event.z_mm)

    def _on_move_to_command(self, event: MoveStageToCommand):
        self.move_to(event.x_mm, event.y_mm, event.z_mm)

    def _on_home_command(self, event: HomeStageCommand):
        self.home(event.x, event.y, event.z, event.theta)

    def _on_zero_command(self, event: ZeroStageCommand):
        self.zero(event.x, event.y, event.z, event.theta)

    def _on_move_to_loading_command(self, event: MoveStageToLoadingPositionCommand):
        self.move_to_loading_position(
            blocking=event.blocking,
            callback=event.callback,
            is_wellplate=event.is_wellplate,
        )

    def _on_move_to_scanning_command(self, event: MoveStageToScanningPositionCommand):
        self.move_to_scanning_position(
            blocking=event.blocking,
            callback=event.callback,
            is_wellplate=event.is_wellplate,
        )

    def move_x(self, distance_mm: float, blocking: bool = True):
        """Move X axis by relative distance."""
        with self._lock:
            self._stage.move_x(distance_mm, blocking)
        self._publish_position()

    def move_y(self, distance_mm: float, blocking: bool = True):
        """Move Y axis by relative distance."""
        with self._lock:
            self._stage.move_y(distance_mm, blocking)
        self._publish_position()

    def move_z(self, distance_mm: float, blocking: bool = True):
        """Move Z axis by relative distance."""
        with self._lock:
            self._stage.move_z(distance_mm, blocking)
        self._publish_position()

    def move_to(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
        blocking: bool = True,
    ):
        """Move to absolute position."""
        with self._lock:
            if x_mm is not None:
                self._stage.move_x_to(x_mm, blocking)
            if y_mm is not None:
                self._stage.move_y_to(y_mm, blocking)
            if z_mm is not None:
                self._stage.move_z_to(z_mm, blocking)
        self._publish_position()

    def get_position(self) -> "Pos":
        """Get current position."""
        with self._lock:
            return self._stage.get_pos()

    def home(
        self, x: bool = False, y: bool = False, z: bool = False, theta: bool = False
    ):
        """Home specified axes."""
        with self._lock:
            self._stage.home(x, y, z, theta)
        self._publish_position()

    def zero(
        self, x: bool = False, y: bool = False, z: bool = False, theta: bool = False
    ):
        """Zero specified axes."""
        with self._lock:
            self._stage.zero(x, y, z, theta)
        self._publish_position()

    def _publish_position(self):
        """Publish current position."""
        with self._lock:
            pos = self._stage.get_pos()
        theta = getattr(pos, "theta_rad", None)
        self.publish(
            StagePositionChanged(
                x_mm=pos.x_mm,
                y_mm=pos.y_mm,
                z_mm=pos.z_mm,
                theta_rad=theta,
            )
        )

    # ============================================================
    # Task 2.1: Theta axis methods
    # ============================================================

    def move_theta(self, distance_rad: float, blocking: bool = True) -> None:
        """Move theta axis by relative distance."""
        with self._lock:
            self._stage.move_theta(distance_rad, blocking)  # type: ignore[attr-defined]
        self._publish_position()

    def move_theta_to(self, abs_rad: float, blocking: bool = True) -> None:
        """Move theta to absolute position."""
        with self._lock:
            self._stage.move_theta_to(abs_rad, blocking)  # type: ignore[attr-defined]
        self._publish_position()

    # ============================================================
    # Task 2.2: get_config method
    # ============================================================

    def get_config(self):
        """Get stage configuration."""
        with self._lock:
            return self._stage.get_config()

    # ============================================================
    # Task 3A: Synchronization and positioning methods
    # ============================================================

    def wait_for_idle(self, timeout: float = 10.0):
        """Wait for stage to finish movement."""
        with self._lock:
            self._stage.wait_for_idle(timeout)

    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
    ) -> None:
        """Set movement limits."""
        with self._lock:
            self._stage.set_limits(
                x_pos_mm=x_pos_mm,
                x_neg_mm=x_neg_mm,
                y_pos_mm=y_pos_mm,
                y_neg_mm=y_neg_mm,
                z_pos_mm=z_pos_mm,
                z_neg_mm=z_neg_mm,
            )

    def get_x_mm_per_ustep(self) -> float:
        """Get mm per microstep for X axis."""
        with self._lock:
            return 1.0 / self._stage.x_mm_to_usteps(1.0)  # type: ignore[attr-defined]

    def get_y_mm_per_ustep(self) -> float:
        """Get mm per microstep for Y axis."""
        with self._lock:
            return 1.0 / self._stage.y_mm_to_usteps(1.0)  # type: ignore[attr-defined]

    def get_z_mm_per_ustep(self) -> float:
        """Get mm per microstep for Z axis."""
        with self._lock:
            return 1.0 / self._stage.z_mm_to_usteps(1.0)  # type: ignore[attr-defined]

    def move_to_safety_position(self):
        """Move Z to safety position."""
        with self._lock:
            self._stage.move_z_to(int(_def.Z_HOME_SAFETY_POINT) / 1000.0)
        self._publish_position()

    def _move_to_loading_position_impl(self, is_wellplate: bool):
        """Internal: move to loading position."""
        if is_wellplate:
            a_large_limit_mm = 125
            self._stage.set_limits(
                x_pos_mm=a_large_limit_mm,
                x_neg_mm=-a_large_limit_mm,
                y_pos_mm=a_large_limit_mm,
                y_neg_mm=-a_large_limit_mm,
            )
            self._scanning_position_z_mm = self._stage.get_pos().z_mm
            self._stage.move_z_to(_def.OBJECTIVE_RETRACTED_POS_MM)
            self._stage.wait_for_idle(_def.SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S)
            self._stage.move_y_to(15)
            self._stage.move_x_to(35)
            self._stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
            self._stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)
            config = self._stage.get_config()
            self._stage.set_limits(
                x_pos_mm=config.X_AXIS.MAX_POSITION,
                x_neg_mm=config.X_AXIS.MIN_POSITION,
                y_pos_mm=config.Y_AXIS.MAX_POSITION,
                y_neg_mm=config.Y_AXIS.MIN_POSITION,
            )
        else:
            self._stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
            self._stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)
        self._publish_position()

    def _move_to_scanning_position_impl(self, is_wellplate: bool):
        """Internal: move to scanning position."""
        if is_wellplate:
            self._stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
            self._stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
            if self._scanning_position_z_mm is not None:
                self._stage.move_z_to(self._scanning_position_z_mm)
            self._scanning_position_z_mm = None
        else:
            self._stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
            self._stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
        self._publish_position()

    def move_to_loading_position(
        self,
        blocking: bool = True,
        callback: Optional[Callable] = None,
        is_wellplate: bool = True,
    ) -> Optional[Thread]:
        """Move stage to loading position."""
        if blocking and callback:
            raise ValueError("Callback not supported when blocking is True")
        if blocking:
            self._move_to_loading_position_impl(is_wellplate)
            return None
        return squid.core.utils.hardware_utils.threaded_operation_helper(
            self._move_to_loading_position_impl, callback, is_wellplate=is_wellplate
        )

    def move_to_scanning_position(
        self,
        blocking: bool = True,
        callback: Optional[Callable] = None,
        is_wellplate: bool = True,
    ) -> Optional[Thread]:
        """Move stage to scanning position."""
        if blocking and callback:
            raise ValueError("Callback not supported when blocking is True")
        if blocking:
            self._move_to_scanning_position_impl(is_wellplate)
            return None
        return squid.core.utils.hardware_utils.threaded_operation_helper(
            self._move_to_scanning_position_impl, callback, is_wellplate=is_wellplate
        )

    # ============================================================
    # Blocking move methods (for acquisition)
    # ============================================================

    def move_x_to(self, x_mm: float, blocking: bool = True) -> None:
        """Move X axis to absolute position."""
        with self._lock:
            self._stage.move_x_to(x_mm, blocking)
        self._publish_position()

    def move_y_to(self, y_mm: float, blocking: bool = True) -> None:
        """Move Y axis to absolute position."""
        with self._lock:
            self._stage.move_y_to(y_mm, blocking)
        self._publish_position()

    def move_z_to(self, z_mm: float, blocking: bool = True) -> None:
        """Move Z axis to absolute position."""
        with self._lock:
            self._stage.move_z_to(z_mm, blocking)
        self._publish_position()
