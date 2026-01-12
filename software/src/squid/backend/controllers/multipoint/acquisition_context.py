from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from squid.core.abc import Pos
    from squid.backend.controllers.live_controller import LiveController
    from squid.backend.services import CameraService, StageService


@dataclass
class AcquisitionContext:
    """Capture acquisition state and restore it after completion."""

    live_controller: "LiveController"
    camera_service: "CameraService"
    stage_service: "StageService"
    was_live: bool
    callbacks_enabled: bool
    start_position: Optional["Pos"]

    def restore(self, resume_live: bool = True) -> None:
        """Restore stage position, camera callbacks, and optionally live mode."""
        if self.start_position is not None:
            self.stage_service.move_x_to(self.start_position.x_mm)
            self.stage_service.move_y_to(self.start_position.y_mm)
            self.stage_service.move_z_to(self.start_position.z_mm)

        self.camera_service.enable_callbacks(self.callbacks_enabled)

        if resume_live and self.was_live:
            self.live_controller.start_live()

    def __enter__(self) -> "AcquisitionContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.restore()
        return False


def acquisition_context(
    live_controller: "LiveController",
    camera_service: "CameraService",
    stage_service: "StageService",
) -> AcquisitionContext:
    """Prepare state for acquisition and return a context for restoration."""
    was_live = live_controller.is_live
    callbacks_enabled = camera_service.get_callbacks_enabled()
    start_position = stage_service.get_position()

    if was_live:
        live_controller.stop_live()

    # We need callbacks enabled during acquisition.
    camera_service.enable_callbacks(True)

    return AcquisitionContext(
        live_controller=live_controller,
        camera_service=camera_service,
        stage_service=stage_service,
        was_live=was_live,
        callbacks_enabled=callbacks_enabled,
        start_position=start_position,
    )
