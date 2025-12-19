"""Image click to stage movement controller.

Converts image pixel coordinates to stage movement commands.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

import squid.core.logging
from squid.core.events import (
    ImageCoordinateClickedCommand,
    ClickToMoveEnabledChanged,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.managers import ObjectiveStore
    from squid.backend.services import CameraService
    from squid.backend.services import StageService

_log = squid.core.logging.get_logger(__name__)


class ImageClickController:
    """Handles image click-to-move functionality.

    Subscribes to ImageCoordinateClickedCommand and converts pixel coordinates
    to stage movement commands based on objective magnification and camera binning.

    Subscribes to: ImageCoordinateClickedCommand
    Moves stage via StageService (x and y)
    """

    def __init__(
        self,
        objective_store: "ObjectiveStore",
        camera_service: "CameraService",
        stage_service: Optional["StageService"],
        event_bus: "EventBus",
        inverted_objective: bool = False,
    ) -> None:
        """Initialize the ImageClickController.

        Args:
            objective_store: Store for objective information and pixel size factor.
            camera_service: Camera service for getting binned pixel size.
            stage_service: Stage service used to perform the move synchronously.
            event_bus: Event bus for publishing/subscribing.
            inverted_objective: Whether the objective is inverted (affects Y sign).
        """
        self._objective_store = objective_store
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._bus = event_bus
        self._inverted_objective = inverted_objective
        self._lock = threading.RLock()

        self._click_to_move_enabled = True  # Default enabled

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        """Subscribe to relevant events."""
        self._bus.subscribe(ImageCoordinateClickedCommand, self._on_image_clicked)
        self._bus.subscribe(ClickToMoveEnabledChanged, self._on_click_to_move_changed)

    def set_click_to_move_enabled(self, enabled: bool) -> None:
        """Set whether click-to-move is enabled.

        Args:
            enabled: Whether to enable click-to-move functionality.
        """
        with self._lock:
            self._click_to_move_enabled = enabled

    def _on_click_to_move_changed(self, event: ClickToMoveEnabledChanged) -> None:
        """Handle ClickToMoveEnabledChanged event."""
        self.set_click_to_move_enabled(event.enabled)

    def _on_image_clicked(self, cmd: ImageCoordinateClickedCommand) -> None:
        """Handle ImageCoordinateClickedCommand.

        Converts pixel coordinates to stage movement and moves via StageService.

        Important: we perform the move before returning so that any subsequent queued
        commands (e.g. StartAcquisitionCommand(acquire_current_fov=True)) observe the
        updated stage position.
        """
        with self._lock:
            if not self._click_to_move_enabled:
                _log.debug(
                    f"Click to move disabled, ignoring click at x={cmd.x_pixel}, y={cmd.y_pixel}"
                )
                return
            if self._stage_service is None:
                _log.warning("No StageService available; ignoring image click move")
                return
            blocked_fn = getattr(self._stage_service, "_blocked_for_ui_hardware_commands", None)
            if callable(blocked_fn) and blocked_fn():
                _log.info("Ignoring image click move due to global mode gate")
                return

            # Calculate pixel size in um
            pixel_size_factor = self._objective_store.get_pixel_size_factor()
            pixel_size_binned_um = self._camera_service.get_pixel_size_binned_um()
            pixel_size_um = pixel_size_factor * pixel_size_binned_um

            # Sign corrections
            pixel_sign_x = 1
            pixel_sign_y = 1 if self._inverted_objective else -1

            # Convert pixels to mm
            delta_x_mm = pixel_sign_x * pixel_size_um * cmd.x_pixel / 1000.0
            delta_y_mm = pixel_sign_y * pixel_size_um * cmd.y_pixel / 1000.0

            _log.debug(
                f"Click to move: click at x={cmd.x_pixel}, y={cmd.y_pixel} -> "
                f"delta_x={delta_x_mm:.4f}mm, delta_y={delta_y_mm:.4f}mm "
                f"(pixel_size={pixel_size_um:.3f}um, factor={pixel_size_factor:.3f})"
            )

        # Perform movement outside our lock (StageService has its own lock).
        # StageService moves are blocking by default; this avoids the "one click behind"
        # behavior when an acquisition starts immediately after click-to-move.
        try:
            self._stage_service.move_x(delta_x_mm)
            self._stage_service.move_y(delta_y_mm)
        except Exception:
            _log.exception("Failed to move stage from image click")
