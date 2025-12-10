"""
Simulated stage implementation for testing without hardware.

This module provides a SimulatedStage class that directly implements AbstractStage
without requiring a microcontroller or serial connection.
"""

import threading
import time
from typing import Optional

from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig


class SimulatedStage(AbstractStage):
    """
    Simulated stage that directly implements AbstractStage without hardware dependencies.

    Useful for testing and development without requiring a microcontroller.
    Position is tracked internally in mm (x, y, z) and radians (theta).

    Features:
        - Internal position tracking
        - Software limit enforcement
        - Optional movement delays for realistic timing
        - Thread-safe busy state management
        - Test helper methods for setting position and state
    """

    def __init__(
        self,
        stage_config: StageConfig,
        simulate_delays: bool = False,
        move_delay_per_mm: float = 0.01,  # seconds per mm of movement
    ):
        """
        Initialize the simulated stage.

        Args:
            stage_config: Stage configuration containing axis limits
            simulate_delays: If True, simulate realistic movement delays
            move_delay_per_mm: Delay in seconds per mm of movement (when simulate_delays=True)
        """
        super().__init__(stage_config)

        # Internal position state (in real units: mm for linear, rad for rotary)
        self._x_mm: float = 0.0
        self._y_mm: float = 0.0
        self._z_mm: float = 0.0
        self._theta_rad: float = 0.0

        # Busy state management
        self._busy: bool = False
        self._busy_lock = threading.Lock()

        # Delay configuration
        self._simulate_delays = simulate_delays
        self._move_delay_per_mm = move_delay_per_mm

        # Software limits (can be modified via set_limits)
        self._x_pos_limit: Optional[float] = stage_config.X_AXIS.MAX_POSITION
        self._x_neg_limit: Optional[float] = stage_config.X_AXIS.MIN_POSITION
        self._y_pos_limit: Optional[float] = stage_config.Y_AXIS.MAX_POSITION
        self._y_neg_limit: Optional[float] = stage_config.Y_AXIS.MIN_POSITION
        self._z_pos_limit: Optional[float] = stage_config.Z_AXIS.MAX_POSITION
        self._z_neg_limit: Optional[float] = stage_config.Z_AXIS.MIN_POSITION
        self._theta_pos_limit: Optional[float] = None  # Usually unbounded
        self._theta_neg_limit: Optional[float] = None

        # For compatibility with stage_utils.py which accesses this attribute
        self._scanning_position_z_mm: Optional[float] = None

        self._log.info(
            f"SimulatedStage initialized with limits: "
            f"X=[{self._x_neg_limit}, {self._x_pos_limit}], "
            f"Y=[{self._y_neg_limit}, {self._y_pos_limit}], "
            f"Z=[{self._z_neg_limit}, {self._z_pos_limit}]"
        )

    # === Core Movement Methods ===

    def move_x(self, rel_mm: float, blocking: bool = True):
        """Move X axis by relative amount."""
        target = self._x_mm + rel_mm
        self._move_axis_to("x", target, blocking)

    def move_y(self, rel_mm: float, blocking: bool = True):
        """Move Y axis by relative amount."""
        target = self._y_mm + rel_mm
        self._move_axis_to("y", target, blocking)

    def move_z(self, rel_mm: float, blocking: bool = True):
        """Move Z axis by relative amount."""
        target = self._z_mm + rel_mm
        self._move_axis_to("z", target, blocking)

    def move_theta(self, rel_rad: float, blocking: bool = True):
        """Move theta axis by relative amount."""
        target = self._theta_rad + rel_rad
        self._move_axis_to("theta", target, blocking)

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        """Move X axis to absolute position."""
        self._move_axis_to("x", abs_mm, blocking)

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        """Move Y axis to absolute position."""
        self._move_axis_to("y", abs_mm, blocking)

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        """Move Z axis to absolute position."""
        self._move_axis_to("z", abs_mm, blocking)

    def move_theta_to(self, abs_rad: float, blocking: bool = True):
        """Move theta axis to absolute position."""
        self._move_axis_to("theta", abs_rad, blocking)

    def get_config(self) -> StageConfig:
        """Return stage configuration."""
        return self.config

    def _move_axis_to(self, axis: str, target: float, blocking: bool):
        """Internal method to move an axis with limit enforcement and delay simulation."""
        # Get current position and limits based on axis
        if axis == "x":
            current = self._x_mm
            pos_limit, neg_limit = self._x_pos_limit, self._x_neg_limit
        elif axis == "y":
            current = self._y_mm
            pos_limit, neg_limit = self._y_pos_limit, self._y_neg_limit
        elif axis == "z":
            current = self._z_mm
            pos_limit, neg_limit = self._z_pos_limit, self._z_neg_limit
        elif axis == "theta":
            current = self._theta_rad
            pos_limit, neg_limit = self._theta_pos_limit, self._theta_neg_limit
        else:
            raise ValueError(f"Unknown axis: {axis}")

        # Clamp to limits
        clamped_target = target
        if pos_limit is not None:
            clamped_target = min(clamped_target, pos_limit)
        if neg_limit is not None:
            clamped_target = max(clamped_target, neg_limit)

        if clamped_target != target:
            self._log.warning(
                f"Movement to {axis}={target:.6f} clamped to {clamped_target:.6f} due to limits"
            )

        distance = abs(clamped_target - current)

        if blocking:
            self._execute_move(axis, clamped_target, distance)
        else:
            thread = threading.Thread(
                target=self._execute_move,
                args=(axis, clamped_target, distance),
                daemon=True,
            )
            thread.start()

    def _execute_move(self, axis: str, target: float, distance: float):
        """Execute the actual move with optional delay."""
        with self._busy_lock:
            self._busy = True

        try:
            if self._simulate_delays and distance > 0:
                delay = distance * self._move_delay_per_mm
                time.sleep(delay)

            # Update position
            if axis == "x":
                self._x_mm = target
            elif axis == "y":
                self._y_mm = target
            elif axis == "z":
                self._z_mm = target
            elif axis == "theta":
                self._theta_rad = target
        finally:
            with self._busy_lock:
                self._busy = False

    # === State Query Methods ===

    def get_pos(self) -> Pos:
        """Get current position."""
        return Pos(
            x_mm=self._x_mm,
            y_mm=self._y_mm,
            z_mm=self._z_mm,
            theta_rad=self._theta_rad,
        )

    def get_state(self) -> StageStage:
        """Get current state including busy flag."""
        with self._busy_lock:
            return StageStage(busy=self._busy)

    # === Homing and Zeroing ===

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        """
        Home specified axes by moving them to position 0.

        Args:
            x: Home X axis
            y: Home Y axis
            z: Home Z axis
            theta: Home theta axis
            blocking: If True, wait for homing to complete
        """

        def do_home():
            with self._busy_lock:
                self._busy = True
            try:
                if self._simulate_delays:
                    # Homing takes longer - simulate travel from current to 0
                    max_travel = 0
                    if x:
                        max_travel = max(max_travel, abs(self._x_mm))
                    if y:
                        max_travel = max(max_travel, abs(self._y_mm))
                    if z:
                        max_travel = max(max_travel, abs(self._z_mm))
                    if theta:
                        max_travel = max(max_travel, abs(self._theta_rad))
                    time.sleep(max_travel * self._move_delay_per_mm)

                if x:
                    self._x_mm = 0.0
                if y:
                    self._y_mm = 0.0
                if z:
                    self._z_mm = 0.0
                if theta:
                    self._theta_rad = 0.0

                self._log.info(f"Homed axes: x={x}, y={y}, z={z}, theta={theta}")
            finally:
                with self._busy_lock:
                    self._busy = False

        if blocking:
            do_home()
        else:
            thread = threading.Thread(target=do_home, daemon=True)
            thread.start()

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        """
        Zero specified axes by setting current position as the origin.

        This does not cause physical movement - it just redefines the coordinate system.

        Args:
            x: Zero X axis
            y: Zero Y axis
            z: Zero Z axis
            theta: Zero theta axis
            blocking: If True, wait for operation to complete (typically instant)
        """

        def do_zero():
            with self._busy_lock:
                self._busy = True
            try:
                if x:
                    self._x_mm = 0.0
                if y:
                    self._y_mm = 0.0
                if z:
                    self._z_mm = 0.0
                if theta:
                    self._theta_rad = 0.0

                self._log.info(f"Zeroed axes: x={x}, y={y}, z={z}, theta={theta}")
            finally:
                with self._busy_lock:
                    self._busy = False

        if blocking:
            do_zero()
        else:
            thread = threading.Thread(target=do_zero, daemon=True)
            thread.start()

    # === Limit Setting ===

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
        """
        Set software limits for each axis.

        Pass None to leave a limit unchanged. Limits are enforced on all movements.
        """
        if x_pos_mm is not None:
            self._x_pos_limit = x_pos_mm
        if x_neg_mm is not None:
            self._x_neg_limit = x_neg_mm
        if y_pos_mm is not None:
            self._y_pos_limit = y_pos_mm
        if y_neg_mm is not None:
            self._y_neg_limit = y_neg_mm
        if z_pos_mm is not None:
            self._z_pos_limit = z_pos_mm
        if z_neg_mm is not None:
            self._z_neg_limit = z_neg_mm
        if theta_pos_rad is not None:
            self._theta_pos_limit = theta_pos_rad
        if theta_neg_rad is not None:
            self._theta_neg_limit = theta_neg_rad

        self._log.debug(
            f"Updated limits: X=[{self._x_neg_limit}, {self._x_pos_limit}], "
            f"Y=[{self._y_neg_limit}, {self._y_pos_limit}], "
            f"Z=[{self._z_neg_limit}, {self._z_pos_limit}]"
        )

    # === Test Helpers ===

    def set_position(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
        theta_rad: Optional[float] = None,
    ):
        """
        Test helper: directly set position without movement simulation.

        This bypasses limits and delays - useful for setting up test scenarios.
        """
        if x_mm is not None:
            self._x_mm = x_mm
        if y_mm is not None:
            self._y_mm = y_mm
        if z_mm is not None:
            self._z_mm = z_mm
        if theta_rad is not None:
            self._theta_rad = theta_rad

    def set_busy(self, busy: bool):
        """Test helper: directly set busy state."""
        with self._busy_lock:
            self._busy = busy

    def set_simulate_delays(self, simulate: bool):
        """Enable or disable delay simulation."""
        self._simulate_delays = simulate
