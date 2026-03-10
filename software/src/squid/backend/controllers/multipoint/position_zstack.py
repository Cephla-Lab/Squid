"""
Position control and z-stack execution for multipoint acquisitions.

This module provides:
- PositionController: Coordinate-based stage movement with stabilization
- ZStackConfig: Configuration dataclass for z-stack acquisition
- ZStackExecutor: Z-stack lifecycle management (init, step, return)

These classes encapsulate position movement patterns previously embedded
in MultiPointWorker.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING

import squid.core.abc
import squid.core.logging
from squid.core.config.test_timing import scale_duration
from _def import (
    TriggerMode,
    SCAN_STABILIZATION_TIME_MS_X,
    SCAN_STABILIZATION_TIME_MS_Y,
    SCAN_STABILIZATION_TIME_MS_Z,
    MULTIPOINT_PIEZO_DELAY_MS,
)

if TYPE_CHECKING:
    from squid.backend.services import StageService, PiezoService


_log = squid.core.logging.get_logger(__name__)


@dataclass
class ZStackConfig:
    """Configuration for z-stack acquisition."""

    num_z_levels: int
    delta_z_um: float
    z_range: Tuple[float, float]  # (start_z_mm, end_z_mm)
    stacking_direction: str = "FROM BOTTOM"  # "FROM BOTTOM", "FROM TOP", "FROM CENTER"
    use_piezo: bool = False


class PositionController:
    """
    Controls stage position with stabilization delays.

    Consolidates duplicated movement patterns from MultiPointWorker into
    a single source of truth for coordinate-based stage movement.

    Usage:
        controller = PositionController(stage_service)

        # Move to coordinate
        controller.move_to_coordinate(x_mm=1.0, y_mm=2.0)

        # Move to specific z level
        controller.move_to_z(z_mm=0.05)

        # Get current position
        pos = controller.get_position()
    """

    def __init__(
        self,
        stage_service: "StageService",
        stabilization_time_x_ms: float = SCAN_STABILIZATION_TIME_MS_X,
        stabilization_time_y_ms: float = SCAN_STABILIZATION_TIME_MS_Y,
        stabilization_time_z_ms: float = SCAN_STABILIZATION_TIME_MS_Z,
    ):
        """
        Initialize the position controller.

        Args:
            stage_service: Stage service for movement commands
            stabilization_time_x_ms: Delay after X movement in ms
            stabilization_time_y_ms: Delay after Y movement in ms
            stabilization_time_z_ms: Delay after Z movement in ms
        """
        self._stage = stage_service
        self._stab_x_s = stabilization_time_x_ms / 1000.0
        self._stab_y_s = stabilization_time_y_ms / 1000.0
        self._stab_z_s = stabilization_time_z_ms / 1000.0

    def move_to_coordinate(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
    ) -> None:
        """
        Move stage to specified coordinate with stabilization.

        Args:
            x_mm: X position in mm (optional)
            y_mm: Y position in mm (optional)
            z_mm: Z position in mm (optional)
        """
        import time

        if x_mm is not None:
            _log.debug(f"Moving X to {x_mm} mm")
            self._stage.move_x_to(x_mm)
            self._stage.wait_for_idle()
            time.sleep(scale_duration(self._stab_x_s, min_seconds=1e-6))

        if y_mm is not None:
            _log.debug(f"Moving Y to {y_mm} mm")
            self._stage.move_y_to(y_mm)
            self._stage.wait_for_idle()
            time.sleep(scale_duration(self._stab_y_s, min_seconds=1e-6))

        if z_mm is not None:
            self.move_to_z(z_mm)

    def move_to_z(self, z_mm: float) -> None:
        """
        Move stage Z to absolute position with stabilization.

        Args:
            z_mm: Z position in mm
        """
        import time

        _log.debug(f"Moving Z to {z_mm} mm")
        self._stage.move_z_to(z_mm)
        self._stage.wait_for_idle()
        time.sleep(scale_duration(self._stab_z_s, min_seconds=1e-6))

    def move_z_relative(self, delta_mm: float) -> None:
        """
        Move stage Z by relative amount with stabilization.

        Args:
            delta_mm: Relative Z movement in mm
        """
        import time

        _log.debug(f"Moving Z by {delta_mm} mm")
        self._stage.move_z(delta_mm)
        self._stage.wait_for_idle()
        time.sleep(scale_duration(self._stab_z_s, min_seconds=1e-6))

    def get_position(self) -> squid.core.abc.Pos:
        """
        Get current stage position.

        Returns:
            Current position as Pos(x_mm, y_mm, z_mm)
        """
        return self._stage.get_position()


class ZStackExecutor:
    """
    Manages z-stack acquisition lifecycle.

    Handles:
    - Z-stack initialization (move to start position)
    - Z-step movement during stack acquisition
    - Return to start position after stack

    Supports both stage-based and piezo-based z-movement.

    Usage:
        executor = ZStackExecutor(
            stage_service=stage,
            piezo_service=piezo,
            config=ZStackConfig(
                num_z_levels=10,
                delta_z_um=1.0,
                z_range=(0.04, 0.05),
                use_piezo=True,
            ),
        )

        # Initialize z-stack (moves to start position)
        executor.initialize()

        # During stack acquisition
        for z_level in range(executor.num_z_levels):
            # ... capture image ...
            if z_level < executor.num_z_levels - 1:
                executor.step()

        # Return to start position
        executor.return_to_start()
    """

    def __init__(
        self,
        stage_service: Optional["StageService"] = None,
        config: Optional[ZStackConfig] = None,
        piezo_service: Optional["PiezoService"] = None,
        trigger_mode: str = TriggerMode.SOFTWARE,
        piezo_delay_ms: float = MULTIPOINT_PIEZO_DELAY_MS,
        stabilization_time_z_ms: float = SCAN_STABILIZATION_TIME_MS_Z,
    ):
        """
        Initialize the z-stack executor.

        Args:
            stage_service: Unused, kept for caller compatibility (will be removed)
            config: Z-stack configuration
            piezo_service: Piezo service for z-movement
            trigger_mode: Trigger mode (affects piezo delay behavior)
            piezo_delay_ms: Delay after piezo movement in ms
            stabilization_time_z_ms: Delay after stage z-movement in ms
        """
        self._piezo = piezo_service
        self._config = config
        self._trigger_mode = trigger_mode
        self._piezo_delay_s = piezo_delay_ms / 1000.0
        self._stab_z_s = stabilization_time_z_ms / 1000.0

        # State
        self._z_home_um: float = 0.0
        self._z_piezo_um: float = 0.0
        self._delta_z_mm: float = config.delta_z_um / 1000.0 if config else 0.0
        self._current_z_level: int = 0

    @property
    def num_z_levels(self) -> int:
        """Get number of z-levels."""
        return self._config.num_z_levels

    @property
    def delta_z_um(self) -> float:
        """Get z-step size in um."""
        return self._config.delta_z_um

    @property
    def use_piezo(self) -> bool:
        """Check if using piezo for z-movement."""
        return self._config.use_piezo and self._piezo is not None

    @property
    def current_z_level(self) -> int:
        """Get current z-level index."""
        return self._current_z_level

    @property
    def z_piezo_um(self) -> float:
        """Get current piezo z position in um."""
        return self._z_piezo_um

    @z_piezo_um.setter
    def z_piezo_um(self, value: float) -> None:
        """Set piezo z position (for external updates)."""
        self._z_piezo_um = value

    def initialize(self) -> None:
        """
        Initialize z-stack by recording the current piezo position as home.

        Does NOT move stage Z — Z control is piezo-only.
        """
        # Adjust delta direction based on stacking config
        if self._config.stacking_direction == "FROM TOP":
            self._delta_z_mm = -abs(self._delta_z_mm)
        else:
            self._delta_z_mm = abs(self._delta_z_mm)

        # Record current piezo position as home
        if self._piezo is not None:
            self._z_home_um = self._piezo.get_position()
        else:
            self._z_home_um = 0.0
        self._z_piezo_um = self._z_home_um
        self._current_z_level = 0

    def step(self) -> None:
        """
        Move to next z-level in stack using piezo.

        Should be called between image captures within a z-stack.
        """
        import time

        if self._piezo is None:
            raise RuntimeError("Piezo service required for z-stack stepping")
        self._z_piezo_um += self._delta_z_mm * 1000  # Convert mm to um
        self._piezo.move_to(self._z_piezo_um)
        if self._trigger_mode == TriggerMode.SOFTWARE:
            time.sleep(scale_duration(self._piezo_delay_s, min_seconds=1e-6))
        self._current_z_level += 1

    def return_to_start(self) -> None:
        """
        Return piezo to home position recorded during initialize().

        Should be called after completing all z-levels in a stack.
        """
        import time

        if self._piezo is None:
            raise RuntimeError("Piezo service required for z-stack return")
        self._z_piezo_um = self._z_home_um
        self._piezo.move_to(self._z_piezo_um)
        if self._trigger_mode == TriggerMode.SOFTWARE:
            time.sleep(scale_duration(self._piezo_delay_s, min_seconds=1e-6))
        self._current_z_level = 0

    def reset_piezo(self, initial_position_um: float = 0.0) -> None:
        """
        Reset piezo position to initial value.

        Args:
            initial_position_um: Initial piezo position in um
        """
        self._z_piezo_um = initial_position_um
        if self._piezo is not None:
            self._piezo.move_to(self._z_piezo_um)
