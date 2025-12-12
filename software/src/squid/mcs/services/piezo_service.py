# squid/services/piezo_service.py
"""Service for piezo Z stage operations.

The piezo is integral to the microscope for:
- Z-stack acquisition (fine Z stepping)
- Real-time focus locking (fast feedback control)

This service provides both event-driven and direct synchronous access
to support both GUI interactions and real-time control loops.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional, Tuple

from squid.mcs.services.base import BaseService
from squid.core.events import (
    EventBus,
    SetPiezoPositionCommand,
    MovePiezoRelativeCommand,
    PiezoPositionChanged,
)

if TYPE_CHECKING:
    from squid.core.abc import PiezoStage


@dataclass(frozen=True)
class PiezoState:
    """Immutable state for PiezoService."""

    position_um: float = 0.0
    range_min_um: float = 0.0
    range_max_um: float = 300.0
    is_available: bool = False


class PiezoService(BaseService):
    """Thread-safe service for piezo Z stage operations.

    Provides two access patterns:
    1. Event-driven: Subscribe to commands, publish state changes
    2. Direct synchronous: For real-time focus locking and acquisition

    The direct methods are optimized for speed and do NOT publish events
    to avoid event bus overhead in tight control loops. Use the event-driven
    interface for GUI updates.

    Subscribes to: SetPiezoPositionCommand, MovePiezoRelativeCommand
    Publishes: PiezoPositionChanged
    """

    def __init__(
        self,
        piezo: Optional["PiezoStage"],
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._piezo = piezo
        self._lock = threading.RLock()

        # Initialize state
        if piezo is not None:
            try:
                pos = getattr(piezo, "position", 0.0)
                range_um = getattr(piezo, "range_um", 300.0)
                # Handle both tuple (min, max) and scalar range
                if isinstance(range_um, tuple):
                    range_min, range_max = range_um
                else:
                    range_min, range_max = 0.0, float(range_um)
                self._state = PiezoState(
                    position_um=pos,
                    range_min_um=range_min,
                    range_max_um=range_max,
                    is_available=True,
                )
            except Exception:
                self._state = PiezoState(is_available=False)
        else:
            # No hardware piezo - create simulated state for testing/simulation
            # Start at center of range so we can move in both directions
            self._state = PiezoState(
                position_um=150.0,  # Center of 0-300 range
                range_min_um=0.0,
                range_max_um=300.0,
                is_available=True,  # Available for simulation
            )

        # Subscribe to commands
        self.subscribe(SetPiezoPositionCommand, self._on_set_position_command)
        self.subscribe(MovePiezoRelativeCommand, self._on_move_relative_command)

        # Publish initial position so subscribers know the starting state
        self.publish(PiezoPositionChanged(position_um=self._state.position_um))

    @property
    def state(self) -> PiezoState:
        """Get current piezo state."""
        return self._state

    @property
    def is_available(self) -> bool:
        """Check if piezo is available."""
        return self._state.is_available

    # =========================================================================
    # Event-driven handlers (for GUI interactions)
    # =========================================================================

    def _on_set_position_command(self, cmd: SetPiezoPositionCommand) -> None:
        """Handle SetPiezoPositionCommand from EventBus."""
        with self._lock:
            clamped = self._clamp_position(cmd.position_um)
            if self._piezo:
                self._piezo.move_to(clamped)
                actual = getattr(self._piezo, "position", clamped)
            else:
                # Simulated mode - just track position internally
                actual = clamped
            self._state = replace(self._state, position_um=actual)

        # Publish outside lock
        self.publish(PiezoPositionChanged(position_um=actual))

    def _on_move_relative_command(self, cmd: MovePiezoRelativeCommand) -> None:
        """Handle MovePiezoRelativeCommand from EventBus."""
        with self._lock:
            if self._piezo:
                current = getattr(self._piezo, "position", 0.0)
                target = self._clamp_position(current + cmd.delta_um)
                self._piezo.move_to(target)
                actual = getattr(self._piezo, "position", target)
            else:
                # Simulated mode
                current = self._state.position_um
                actual = self._clamp_position(current + cmd.delta_um)
            self._state = replace(self._state, position_um=actual)

        # Publish outside lock
        self.publish(PiezoPositionChanged(position_um=actual))

    # =========================================================================
    # Direct synchronous access (for acquisition and focus locking)
    # =========================================================================

    def move_to(self, position_um: float) -> float:
        """Move piezo to absolute position (synchronous, for acquisition/focus lock).

        Args:
            position_um: Target position in micrometers

        Returns:
            Actual position after move
        """
        with self._lock:
            clamped = self._clamp_position(position_um)
            if self._piezo:
                self._piezo.move_to(clamped)
                actual = getattr(self._piezo, "position", clamped)
            else:
                # Simulated mode
                actual = clamped
            self._state = replace(self._state, position_um=actual)

        # Publish outside lock
        self.publish(PiezoPositionChanged(position_um=actual))
        return actual

    def move_relative(self, delta_um: float) -> float:
        """Move piezo by relative amount (synchronous, for acquisition/focus lock).

        Args:
            delta_um: Relative movement in micrometers

        Returns:
            Actual position after move
        """
        with self._lock:
            if self._piezo:
                current = getattr(self._piezo, "position", 0.0)
                target = self._clamp_position(current + delta_um)
                self._piezo.move_to(target)
                actual = getattr(self._piezo, "position", target)
            else:
                # Simulated mode
                current = self._state.position_um
                actual = self._clamp_position(current + delta_um)
            self._state = replace(self._state, position_um=actual)

        # Publish outside lock
        self.publish(PiezoPositionChanged(position_um=actual))
        return actual

    def get_position(self) -> float:
        """Get current piezo position (synchronous, for acquisition/focus lock).

        Returns:
            Current position in micrometers
        """
        with self._lock:
            if self._piezo:
                return getattr(self._piezo, "position", 0.0)
            else:
                return self._state.position_um

    def get_range(self) -> Tuple[float, float]:
        """Get piezo range (min, max) in micrometers."""
        return (self._state.range_min_um, self._state.range_max_um)

    # =========================================================================
    # Focus lock support - minimal latency methods
    # =========================================================================

    def move_to_fast(self, position_um: float) -> None:
        """Move piezo with minimal overhead (for focus lock control loop).

        This method skips state updates to minimize latency.
        Use sync_state() periodically to update state if needed.

        Args:
            position_um: Target position in micrometers
        """
        with self._lock:
            clamped = self._clamp_position(position_um)
            if self._piezo:
                self._piezo.move_to(clamped)
            else:
                # Simulated mode - update state directly
                self._state = replace(self._state, position_um=clamped)

    def sync_state(self) -> float:
        """Synchronize internal state with actual piezo position.

        Call this after a series of move_to_fast() calls to update state.

        Returns:
            Current position in micrometers
        """
        with self._lock:
            if self._piezo:
                actual = getattr(self._piezo, "position", 0.0)
                self._state = replace(self._state, position_um=actual)
                return actual
            else:
                return self._state.position_um

    # =========================================================================
    # Helper methods
    # =========================================================================

    def _clamp_position(self, position_um: float) -> float:
        """Clamp position to valid range."""
        return max(
            self._state.range_min_um,
            min(self._state.range_max_um, position_um),
        )

    def home(self) -> float:
        """Move piezo to home position (center of range).

        Returns:
            Actual position after homing
        """
        # Try hardware home first
        if self._piezo and hasattr(self._piezo, "home"):
            with self._lock:
                self._piezo.home()
                actual = getattr(self._piezo, "position", 0.0)
                self._state = replace(self._state, position_um=actual)
            self.publish(PiezoPositionChanged(position_um=actual))
            return actual

        # Otherwise move to center of range (works for both real and simulated)
        center = (self._state.range_min_um + self._state.range_max_um) / 2.0
        return self.move_to(center)
