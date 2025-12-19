"""Movement monitoring service.

Polls stage/piezo position and publishes events on movement changes.
Replaces MovementUpdater from qt_controllers.py with a pure-Python
implementation that doesn't depend on Qt.
"""
import threading
from typing import Optional

from squid.core.abc import AbstractStage
from squid.backend.services.base import BaseService
from squid.core.events import (
    EventBus,
    StagePositionChanged,
    StageMovementStopped,
    PiezoPositionChanged,
)
import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class MovementService(BaseService):
    """Monitors stage/piezo movement and publishes position events.

    This service polls the stage and piezo at regular intervals and publishes
    events when positions change. It distinguishes between continuous position
    updates (StagePositionChanged) and debounced "movement stopped" events
    (StageMovementStopped).

    Usage:
        service = MovementService(stage, piezo, event_bus)
        service.start()  # Begin polling
        # ... later ...
        service.stop()   # Stop polling
    """

    def __init__(
        self,
        stage: AbstractStage,
        piezo: Optional[object],  # PiezoStage, but avoid import cycle
        event_bus: EventBus,
        poll_interval_ms: int = 100,
        movement_threshold_mm: float = 0.0001,
    ):
        """Initialize the movement service.

        Args:
            stage: Stage to monitor for position changes
            piezo: Optional piezo stage to monitor for Z changes
            event_bus: EventBus to publish position events
            poll_interval_ms: How often to poll positions (default 100ms)
            movement_threshold_mm: Movement below this is considered stopped
        """
        super().__init__(event_bus)
        self._stage = stage
        self._piezo = piezo
        self._poll_interval_ms = poll_interval_ms
        self._movement_threshold_mm = movement_threshold_mm

        # State tracking
        self._previous_x_mm: Optional[float] = None
        self._previous_y_mm: Optional[float] = None
        self._previous_z_mm: Optional[float] = None
        self._previous_piezo_pos: Optional[float] = None
        self._sent_stopped = False

        # Timer control
        self._running = False
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start position polling."""
        with self._lock:
            if self._running:
                return
            self._running = True
            _log.info("MovementService started polling")
            self._schedule_poll()

    def stop(self) -> None:
        """Stop position polling."""
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            _log.info("MovementService stopped polling")

    def shutdown(self) -> None:
        """Shutdown the service."""
        self.stop()

    def _schedule_poll(self) -> None:
        """Schedule the next poll."""
        if self._running:
            self._timer = threading.Timer(
                self._poll_interval_ms / 1000.0,
                self._do_poll
            )
            self._timer.daemon = True
            self._timer.start()

    def _do_poll(self) -> None:
        """Execute one poll cycle."""
        try:
            self._poll_once()
        except Exception as e:
            _log.exception(f"Error polling position: {e}")
        finally:
            with self._lock:
                if self._running:
                    self._schedule_poll()

    def _poll_once(self) -> None:
        """Poll positions and emit events as needed."""
        # Poll piezo
        if self._piezo is not None:
            try:
                current_piezo = self._piezo.position
                if self._previous_piezo_pos is None:
                    self._previous_piezo_pos = current_piezo
                elif self._previous_piezo_pos != current_piezo:
                    self._previous_piezo_pos = current_piezo
                    self._event_bus.publish(PiezoPositionChanged(position_um=current_piezo))
            except Exception as e:
                _log.debug(f"Error reading piezo position: {e}")

        # Poll stage
        try:
            pos = self._stage.get_pos()
        except Exception as e:
            _log.debug(f"Error reading stage position: {e}")
            return

        # Initialize previous position on first poll
        if self._previous_x_mm is None:
            self._previous_x_mm = pos.x_mm
            self._previous_y_mm = pos.y_mm
            self._previous_z_mm = pos.z_mm
            # Emit initial position so UI has a baseline without waiting for movement
            self._event_bus.publish(
                StagePositionChanged(
                    x_mm=pos.x_mm,
                    y_mm=pos.y_mm,
                    z_mm=pos.z_mm,
                )
            )
            if self._piezo is not None:
                try:
                    if self._previous_piezo_pos is not None:
                        self._event_bus.publish(
                            PiezoPositionChanged(position_um=self._previous_piezo_pos)
                        )
                except Exception:
                    pass
            return

        # Check if we're still moving
        dx = abs(self._previous_x_mm - pos.x_mm)
        dy = abs(self._previous_y_mm - pos.y_mm)

        try:
            stage_busy = self._stage.get_state().busy
        except Exception:
            stage_busy = False

        is_moving = (
            dx > self._movement_threshold_mm or
            dy > self._movement_threshold_mm or
            stage_busy
        )

        # Only emit position changed if position actually changed
        position_changed = (
            dx > self._movement_threshold_mm or
            dy > self._movement_threshold_mm or
            abs(self._previous_z_mm - pos.z_mm) > self._movement_threshold_mm
        )

        if position_changed:
            self._event_bus.publish(StagePositionChanged(
                x_mm=pos.x_mm,
                y_mm=pos.y_mm,
                z_mm=pos.z_mm,
            ))
            # Update previous position
            self._previous_x_mm = pos.x_mm
            self._previous_y_mm = pos.y_mm
            self._previous_z_mm = pos.z_mm

        # Emit movement stopped event once per stop
        if not is_moving and not self._sent_stopped:
            self._sent_stopped = True
            self._event_bus.publish(StageMovementStopped(
                x_mm=pos.x_mm,
                y_mm=pos.y_mm,
                z_mm=pos.z_mm,
            ))
        elif is_moving:
            self._sent_stopped = False
