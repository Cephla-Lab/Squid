from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, TYPE_CHECKING

import squid.core.logging
from squid.core.events import (
    EventBus,
    StagePositionChanged,
    ObjectiveChanged,
    BinningChanged,
    WellplateFormatChanged,
    NavigationViewerStateChanged,
)

if TYPE_CHECKING:
    from squid.ops.navigation import ObjectiveStore
    from squid.mcs.services.camera_service import CameraService


@dataclass(frozen=True)
class _NavState:
    x_mm: float
    y_mm: float
    fov_width_mm: float
    fov_height_mm: float
    wellplate_format: Optional[str]


class NavigationViewerStateService:
    """Backend publisher for NavigationViewerStateChanged.

    This keeps other UI widgets from needing direct references to NavigationViewer
    for coarse-grained state (position + FOV size + format).
    """

    def __init__(
        self,
        objective_store: "ObjectiveStore",
        camera_service: "CameraService",
        event_bus: EventBus,
    ) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._objective_store = objective_store
        self._camera_service = camera_service
        self._bus = event_bus

        self._x_mm: float = 0.0
        self._y_mm: float = 0.0
        self._pixel_size_binned_um: Optional[float] = None
        self._wellplate_format: Optional[str] = None

        self._last_published: Optional[_NavState] = None

        self._bus.subscribe(StagePositionChanged, self._on_stage_position_changed)
        self._bus.subscribe(ObjectiveChanged, lambda _e: self._publish_if_changed())
        self._bus.subscribe(BinningChanged, self._on_binning_changed)
        self._bus.subscribe(WellplateFormatChanged, self._on_wellplate_format_changed)

        self._publish_if_changed()

    def _on_stage_position_changed(self, event: StagePositionChanged) -> None:
        self._x_mm = float(event.x_mm)
        self._y_mm = float(event.y_mm)
        self._publish_if_changed()

    def _on_binning_changed(self, event: BinningChanged) -> None:
        if event.pixel_size_binned_um is not None:
            self._pixel_size_binned_um = float(event.pixel_size_binned_um)
        self._publish_if_changed()

    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        self._wellplate_format = str(event.format_name)
        self._publish_if_changed()

    def _get_fov_size_mm(self) -> Tuple[float, float]:
        try:
            width_px, height_px = self._camera_service.get_resolution()
        except Exception:
            width_px, height_px = (0, 0)
        if width_px <= 0 or height_px <= 0:
            return (0.0, 0.0)

        if self._pixel_size_binned_um is None:
            try:
                self._pixel_size_binned_um = float(self._camera_service.get_pixel_size_binned_um())
            except Exception:
                self._pixel_size_binned_um = 0.0

        try:
            pixel_size_factor = float(self._objective_store.get_pixel_size_factor())
        except Exception:
            pixel_size_factor = 1.0

        pixel_size_um = pixel_size_factor * float(self._pixel_size_binned_um or 0.0)
        fov_width_mm = (pixel_size_um * float(width_px)) / 1000.0
        fov_height_mm = (pixel_size_um * float(height_px)) / 1000.0
        return (fov_width_mm, fov_height_mm)

    def _publish_if_changed(self) -> None:
        fov_width_mm, fov_height_mm = self._get_fov_size_mm()
        state = _NavState(
            x_mm=self._x_mm,
            y_mm=self._y_mm,
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
            wellplate_format=self._wellplate_format,
        )
        if state == self._last_published:
            return
        self._last_published = state
        self._bus.publish(
            NavigationViewerStateChanged(
                x_mm=state.x_mm,
                y_mm=state.y_mm,
                fov_width_mm=state.fov_width_mm,
                fov_height_mm=state.fov_height_mm,
                wellplate_format=state.wellplate_format,
            )
        )

