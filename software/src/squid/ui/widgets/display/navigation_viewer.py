from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import cv2
import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QFrame, QPushButton, QVBoxLayout

from _def import (
    A1_X_MM,
    A1_X_PIXEL,
    A1_Y_MM,
    A1_Y_PIXEL,
    INVERTED_OBJECTIVE,
    IS_HCS,
    NUMBER_OF_SKIP,
    WELL_SIZE_MM,
    WELL_SPACING_MM,
    PROJECT_ROOT,
)
from squid.backend.managers.objective_store import ObjectiveStore
from squid.backend.managers.scan_coordinates import (
    AddScanCoordinateRegion,
    ClearedScanCoordinates,
    FovCenter,
    RemovedScanCoordinateRegion,
)
from squid.core.abc import Pos
from squid.core.events import (
    BinningChanged,
    ClearScanCoordinatesCommand,
    ClickToMoveEnabledChanged,
    CurrentFOVRegistered,
    FocusPointOverlaySet,
    FocusPointOverlayVisibilityChanged,
    MoveStageToCommand,
    ObjectiveChanged,
    StageMovementStopped,
    WellplateFormatChanged,
)
import squid.core.abc
import squid.core.logging

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class NavigationViewer(QFrame):
    def __init__(
        self,
        objectivestore: ObjectiveStore,
        camera: squid.core.abc.AbstractCamera,
        sample: str = "glass slide",
        invertX: bool = False,
        event_bus: Optional["UIEventBus"] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self._subscriptions: List[tuple] = []
        self._click_to_move_enabled: bool = True
        self._pending_focus_points: Optional[Tuple[Tuple[float, float], ...]] = None
        self._pending_focus_overlay_visible: Optional[bool] = None

        # Subscribe to stage movement events via UIEventBus (thread-safe)
        if self._event_bus is not None:
            self._subscribe(StageMovementStopped, self._on_stage_movement_stopped)
            self._subscribe(ClickToMoveEnabledChanged, self._on_click_to_move_enabled_changed)
            self._subscribe(WellplateFormatChanged, self._on_wellplate_format_changed)
            self._subscribe(ObjectiveChanged, self._on_redraw_trigger)
            self._subscribe(BinningChanged, self._on_redraw_trigger)
            self._subscribe(CurrentFOVRegistered, self._on_current_fov_registered)
            self._subscribe(
                AddScanCoordinateRegion,
                lambda update: self.register_fovs_to_image(update.fov_centers),
            )
            self._subscribe(
                RemovedScanCoordinateRegion,
                lambda update: self.deregister_fovs_from_image(update.fov_centers),
            )
            self._subscribe(ClearedScanCoordinates, lambda _u: self._clear_pending_fovs())
            self._subscribe(FocusPointOverlaySet, self._on_focus_point_overlay_set)
            self._subscribe(
                FocusPointOverlayVisibilityChanged,
                self._on_focus_point_overlay_visibility_changed,
            )

        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.sample: str = sample
        self.objectiveStore: ObjectiveStore = objectivestore
        self.camera: squid.core.abc.AbstractCamera = camera
        self.well_size_mm: float = WELL_SIZE_MM
        self.well_spacing_mm: float = WELL_SPACING_MM
        self.number_of_skip: int = NUMBER_OF_SKIP
        self.a1_x_mm: float = A1_X_MM
        self.a1_y_mm: float = A1_Y_MM
        self.a1_x_pixel: float = A1_X_PIXEL
        self.a1_y_pixel: float = A1_Y_PIXEL
        self.location_update_threshold_mm: float = 0.2
        self.box_color: Tuple[int, int, int] = (255, 0, 0)
        self.box_line_thickness: int = 1
        self.x_mm: Optional[float] = None
        self.y_mm: Optional[float] = None
        self.mm_per_pixel: float = 0.0
        self.origin_x_pixel: float = 0.0
        self.origin_y_pixel: float = 0.0
        self.fov_size_mm: float = 0.0
        self.fov_width_mm: float = 0.0
        self.fov_height_mm: float = 0.0
        self._pending_fovs: List[FovCenter] = []  # Pending FOV positions (red)
        self._completed_fovs: List[FovCenter] = []  # Completed FOV positions (blue)
        self._base_line_thickness: int = 1  # Base thickness at 1:1 zoom
        self._current_thickness: int = 1  # Track current thickness to avoid unnecessary redraws
        # Debounce timer for batching FOV registration redraws
        self._redraw_timer: QTimer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(50)  # 50ms debounce
        self._redraw_timer.timeout.connect(self._redraw_scan_overlay)
        self._redraw_pending: bool = False
        self.image_height: int = 0
        self.image_width: int = 0
        self.rows: int = 0
        self.cols: int = 0
        self.image_paths: Dict[str, str] = {
            "glass slide": "assets/images/slide carrier_828x662.png",
            "4 glass slide": "assets/images/4 slide carrier_1509x1010.png",
            "6 well plate": "assets/images/6 well plate_1509x1010.png",
            "12 well plate": "assets/images/12 well plate_1509x1010.png",
            "24 well plate": "assets/images/24 well plate_1509x1010.png",
            "96 well plate": "assets/images/96 well plate_1509x1010.png",
            "384 well plate": "assets/images/384 well plate_1509x1010.png",
            "1536 well plate": "assets/images/1536 well plate_1509x1010.png",
        }
        self.slide: Optional[np.ndarray] = None
        self.background_item: Optional[pg.ImageItem] = None
        self.current_location_item: Optional[pg.ImageItem] = None
        self.scan_overlay_item: Optional[pg.ImageItem] = None
        self.scan_overlay: Optional[np.ndarray] = None
        self.focus_point_overlay_item: Optional[pg.ImageItem] = None

        self.init_ui(invertX)
        self.update_display_properties(sample)

        if self._pending_focus_overlay_visible is not None and hasattr(self, "focus_point_overlay_item"):
            self.focus_point_overlay_item.setVisible(self._pending_focus_overlay_visible)
            self._pending_focus_overlay_visible = None
        if self._pending_focus_points is not None:
            self._apply_focus_point_overlay_set(self._pending_focus_points)
            self._pending_focus_points = None

    def _on_focus_point_overlay_set(self, event: FocusPointOverlaySet) -> None:
        if not hasattr(self, "focus_point_overlay_item"):
            self._pending_focus_points = event.points
            return
        self._apply_focus_point_overlay_set(event.points)

    def _apply_focus_point_overlay_set(self, points: Tuple[Tuple[float, float], ...]) -> None:
        self.clear_focus_points()
        for x_mm, y_mm in points:
            self.register_focus_point(float(x_mm), float(y_mm))

    def _on_focus_point_overlay_visibility_changed(self, event: FocusPointOverlayVisibilityChanged) -> None:
        if not hasattr(self, "focus_point_overlay_item"):
            self._pending_focus_overlay_visible = event.enabled
            return
        self.focus_point_overlay_item.setVisible(event.enabled)

    def init_ui(self, invertX: bool) -> None:
        pg.setConfigOptions(imageAxisOrder="row-major")
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setBackground("w")

        self.view = self.graphics_widget.addViewBox(invertX=not INVERTED_OBJECTIVE, invertY=True)
        self.view.setAspectLocked(True)

        self.btn_clear_coordinates = QPushButton("Clear Scan Grid", self.graphics_widget)
        self.btn_clear_coordinates.clicked.connect(self._publish_clear_scan_grid)
        self.btn_clear_coordinates.setCursor(Qt.PointingHandCursor)
        self.btn_clear_coordinates.adjustSize()
        self._position_button()

        self.grid = QVBoxLayout()
        self.grid.addWidget(self.graphics_widget)
        self.setLayout(self.grid)

        self.view.scene().sigMouseClicked.connect(self.handle_mouse_click)
        self.view.sigRangeChanged.connect(self._on_view_range_changed)

    def _publish_clear_scan_grid(self) -> None:
        # Always clear the overlay fully (including completed FOVs) when user clicks button
        self.clear_overlay()
        if self._event_bus is not None:
            self._event_bus.publish(ClearScanCoordinatesCommand())

    def _position_button(self) -> None:
        margin = 10
        button_width = self.btn_clear_coordinates.sizeHint().width()
        button_height = self.btn_clear_coordinates.sizeHint().height()

        x = self.graphics_widget.width() - button_width - margin
        y = self.graphics_widget.height() - button_height - margin
        self.btn_clear_coordinates.move(x, y)
        self.btn_clear_coordinates.raise_()

    def _subscribe(self, event_type: type, handler: Callable) -> None:
        if self._event_bus is not None:
            self._event_bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))

    def _cleanup_subscriptions(self) -> None:
        if self._event_bus is not None:
            for event_type, handler in self._subscriptions:
                self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    def _on_stage_movement_stopped(self, event: StageMovementStopped) -> None:
        pos = Pos(
            x_mm=event.x_mm,
            y_mm=event.y_mm,
            z_mm=event.z_mm,
            theta_rad=getattr(event, "theta_rad", None),
        )
        self.draw_fov_current_location(pos)

    def _on_click_to_move_enabled_changed(self, event: ClickToMoveEnabledChanged) -> None:
        self._click_to_move_enabled = event.enabled

    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        # Clear all FOVs when wellplate format changes - old positions are no longer valid
        self._pending_fovs.clear()
        self._completed_fovs.clear()
        self.update_wellplate_settings(
            event.format_name,
            event.a1_x_mm,
            event.a1_y_mm,
            event.a1_x_pixel,
            event.a1_y_pixel,
            event.well_size_mm,
            event.well_spacing_mm,
            event.number_of_skip,
        )

    def _on_redraw_trigger(self, _event: object) -> None:
        self.update_display_properties(self.sample)

    def _on_current_fov_registered(self, event: CurrentFOVRegistered) -> None:
        """Mark an FOV as completed (moves from red to blue)."""
        pos = (event.x_mm, event.y_mm)
        self._log.info(
            f"CurrentFOVRegistered received: {pos}, "
            f"size=({event.fov_width_mm}, {event.fov_height_mm}), "
            f"pending={len(self._pending_fovs)}, completed={len(self._completed_fovs)}"
        )
        # Remove from pending if present (use tolerance for floating point comparison)
        tolerance = 1e-6
        self._pending_fovs = [
            f for f in self._pending_fovs
            if abs(f.x_mm - pos[0]) > tolerance or abs(f.y_mm - pos[1]) > tolerance
        ]
        # Add to completed with FOV dimensions from the event
        completed_fov = FovCenter(
            x_mm=event.x_mm,
            y_mm=event.y_mm,
            fov_width_mm=event.fov_width_mm,
            fov_height_mm=event.fov_height_mm,
        )
        # Check if this position is already in completed (use tolerance)
        already_completed = any(
            abs(f.x_mm - pos[0]) <= tolerance and abs(f.y_mm - pos[1]) <= tolerance
            for f in self._completed_fovs
        )
        if not already_completed:
            self._completed_fovs.append(completed_fov)
        self._log.info(f"After update: pending={len(self._pending_fovs)}, completed={len(self._completed_fovs)}")
        self._redraw_scan_overlay()

    def clear_slide(self) -> None:
        if self.background_item is not None:
            self.view.removeItem(self.background_item)
            self.background_item = None

        if self.scan_overlay_item is not None:
            self.view.removeItem(self.scan_overlay_item)
            self.scan_overlay_item = None

        if self.focus_point_overlay_item is not None:
            self.view.removeItem(self.focus_point_overlay_item)
            self.focus_point_overlay_item = None

        if self.current_location_item is not None:
            self.view.removeItem(self.current_location_item)
            self.current_location_item = None

        self.slide = None
        self.scan_overlay = None

    def update_display_properties(self, sample: str) -> None:
        self.sample = sample
        self.clear_slide()

        img_path = self.image_paths.get(sample, self.image_paths["glass slide"])
        full_path = str(Path(PROJECT_ROOT) / img_path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"NavigationViewer image not found: {full_path}")

        self.slide = cv2.imread(full_path, cv2.IMREAD_COLOR)
        if self.slide is None:
            raise RuntimeError(f"Failed to load navigation image: {full_path}")

        self.slide = cv2.cvtColor(self.slide, cv2.COLOR_BGR2RGB)
        self.image_height, self.image_width = self.slide.shape[:2]

        self._update_scale_and_origin()
        self._create_background_layer()
        self._create_overlays()
        self._redraw_scan_overlay()

    def _update_scale_and_origin(self) -> None:
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor() or 1.0
        fov_width_mm = pixel_size_factor * (self.camera.get_fov_width_mm() or self.camera.get_fov_size_mm())
        fov_height_mm = pixel_size_factor * (self.camera.get_fov_height_mm() or self.camera.get_fov_size_mm())
        self.fov_width_mm = float(fov_width_mm or 0.0)
        self.fov_height_mm = float(fov_height_mm or 0.0)
        self.fov_size_mm = max(self.fov_width_mm, self.fov_height_mm)

        if self.a1_x_pixel <= 0:
            self.mm_per_pixel = 0.0
            return

        # Calculate mm_per_pixel from physical plate dimensions and image size.
        # SBS standard plate footprint is 127.76mm x 85.48mm for all microplates.
        # The wellplate images represent this footprint.
        SBS_PLATE_WIDTH_MM = 127.76
        SBS_PLATE_HEIGHT_MM = 85.48

        if self.image_width > 0 and self.image_height > 0:
            # Use average of X and Y scale factors for mm_per_pixel
            mm_per_pixel_x = SBS_PLATE_WIDTH_MM / self.image_width
            mm_per_pixel_y = SBS_PLATE_HEIGHT_MM / self.image_height
            self.mm_per_pixel = (mm_per_pixel_x + mm_per_pixel_y) / 2.0
        else:
            # Fallback to old calculation if image dimensions unknown
            self.mm_per_pixel = self.a1_x_mm / max(1.0, float(self.a1_x_pixel))

        self.origin_x_pixel = float(self.a1_x_pixel)
        self.origin_y_pixel = float(self.a1_y_pixel)

    def _create_background_layer(self) -> None:
        assert self.slide is not None
        self.background_item = pg.ImageItem(self.slide)
        self.view.addItem(self.background_item)

        self.background_item.setZValue(0)

    def _create_overlays(self) -> None:
        assert self.slide is not None
        overlay_shape = (self.slide.shape[0], self.slide.shape[1], 4)
        self.scan_overlay = np.zeros(overlay_shape, dtype=np.uint8)
        self.scan_overlay_item = pg.ImageItem(self.scan_overlay)
        self.scan_overlay_item.setZValue(10)
        self.view.addItem(self.scan_overlay_item)

        self.focus_point_overlay_item = pg.ImageItem(np.zeros(overlay_shape, dtype=np.uint8))
        self.focus_point_overlay_item.setZValue(15)
        self.view.addItem(self.focus_point_overlay_item)

        self.current_location_item = pg.ImageItem(np.zeros(overlay_shape, dtype=np.uint8))
        self.current_location_item.setZValue(20)
        self.view.addItem(self.current_location_item)

    def update_wellplate_settings(
        self,
        format_name: str,
        a1_x_mm: float,
        a1_y_mm: float,
        a1_x_pixel: float,
        a1_y_pixel: float,
        well_size_mm: float,
        well_spacing_mm: float,
        number_of_skip: int,
    ) -> None:
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = well_size_mm
        self.well_spacing_mm = well_spacing_mm
        self.number_of_skip = number_of_skip
        self.update_display_properties(format_name)

    def _clear_pending_fovs(self) -> None:
        """Clear only pending FOVs (keeps completed FOVs visible)."""
        self._log.info(f"_clear_pending_fovs called, clearing {len(self._pending_fovs)} pending (keeping {len(self._completed_fovs)} completed)")
        self._pending_fovs.clear()
        self._redraw_scan_overlay()

    def clear_overlay(self) -> None:
        """Clear all FOVs (both pending and completed). Used by Clear Scan Grid button."""
        self._log.info(f"clear_overlay called, clearing {len(self._pending_fovs)} pending and {len(self._completed_fovs)} completed FOVs")
        if self.scan_overlay is not None:
            self.scan_overlay[:] = 0
        if self.scan_overlay_item is not None:
            self.scan_overlay_item.setImage(self.scan_overlay)
        self._pending_fovs.clear()
        self._completed_fovs.clear()

    def clear_focus_points(self) -> None:
        if self.focus_point_overlay_item is not None:
            self.focus_point_overlay_item.setImage(np.zeros_like(self.scan_overlay))

    def register_focus_point(self, x_mm: float, y_mm: float) -> None:
        if self.focus_point_overlay_item is None:
            return
        overlay = self.focus_point_overlay_item.image
        if overlay is None:
            overlay = np.zeros_like(self.scan_overlay)
        # Convert mm to pixels: A1 position (a1_x_mm, a1_y_mm) maps to (a1_x_pixel, a1_y_pixel)
        x_px = int(round(self.origin_x_pixel + (x_mm - self.a1_x_mm) / self.mm_per_pixel))
        y_px = int(round(self.origin_y_pixel + (y_mm - self.a1_y_mm) / self.mm_per_pixel))
        cv2.circle(overlay, (x_px, y_px), 6, (0, 255, 0, 200), thickness=-1)
        self.focus_point_overlay_item.setImage(overlay)

    def _get_zoom_adjusted_thickness(self) -> int:
        try:
            x_range, y_range = self.view.viewRange()
            view_width = abs(x_range[1] - x_range[0])
            zoom_factor = max(1.0, self.image_width / max(1.0, view_width))
            # Scale thickness more gently - use sqrt for sublinear scaling
            thickness = max(1, int(self._base_line_thickness * (zoom_factor ** 0.3)))
            return min(thickness, 8)
        except Exception:
            return self._base_line_thickness

    def register_fovs_to_image(self, fov_centers: List[FovCenter]) -> None:
        """Register multiple FOVs at once, using debounced redraw to batch rapid updates."""
        if not fov_centers:
            return
        # Add all FOVs first without redrawing
        for center in fov_centers:
            self._pending_fovs.append(center)
        self._log.debug(
            f"Registered {len(fov_centers)} pending FOVs, total pending={len(self._pending_fovs)}"
        )
        # Schedule debounced redraw - coalesces rapid region updates into single redraw
        self._schedule_redraw()

    def register_fov_to_image(self, fov: FovCenter) -> None:
        """Add a pending FOV position (drawn in red)."""
        self._pending_fovs.append(fov)
        self._log.info(
            f"Registered pending FOV: ({fov.x_mm}, {fov.y_mm}), "
            f"size=({fov.fov_width_mm}, {fov.fov_height_mm}), total pending={len(self._pending_fovs)}"
        )
        self._redraw_scan_overlay()

    def deregister_fovs_from_image(self, fov_centers: List[FovCenter]) -> None:
        # Only remove from pending - completed FOVs stay visible until explicitly cleared
        to_remove = {(c.x_mm, c.y_mm) for c in fov_centers}
        self._pending_fovs = [f for f in self._pending_fovs if (f.x_mm, f.y_mm) not in to_remove]
        self._schedule_redraw()

    def draw_fov_current_location(self, pos: Pos) -> None:
        if self.current_location_item is None or self.scan_overlay is None:
            return
        overlay = np.zeros_like(self.scan_overlay)
        self._draw_fov_box(overlay, pos.x_mm, pos.y_mm, color=(0, 0, 255, 255), thickness=2)
        self.current_location_item.setImage(overlay)

    def _draw_fov_box(
        self,
        overlay: np.ndarray,
        x_mm: float,
        y_mm: float,
        *,
        color: Tuple[int, int, int, int],
        thickness: int,
        fov_width_mm: Optional[float] = None,
        fov_height_mm: Optional[float] = None,
    ) -> None:
        if self.mm_per_pixel <= 0:
            return
        # Use provided FOV dimensions or fall back to current objective dimensions
        width_mm = fov_width_mm if fov_width_mm and fov_width_mm > 0 else self.fov_width_mm
        height_mm = fov_height_mm if fov_height_mm and fov_height_mm > 0 else self.fov_height_mm

        # Convert mm to pixels: A1 position (a1_x_mm, a1_y_mm) maps to (a1_x_pixel, a1_y_pixel)
        x_px = int(round(self.origin_x_pixel + (x_mm - self.a1_x_mm) / self.mm_per_pixel))
        y_px = int(round(self.origin_y_pixel + (y_mm - self.a1_y_mm) / self.mm_per_pixel))

        half_w = int(round((width_mm / max(self.mm_per_pixel, 1e-6)) / 2))
        half_h = int(round((height_mm / max(self.mm_per_pixel, 1e-6)) / 2))
        top_left = (x_px - half_w, y_px - half_h)
        bottom_right = (x_px + half_w, y_px + half_h)
        cv2.rectangle(overlay, top_left, bottom_right, color, thickness=thickness)

    def _schedule_redraw(self) -> None:
        """Schedule a debounced redraw. Multiple calls within 50ms are coalesced."""
        # Restart the timer on each call to batch rapid updates
        self._redraw_timer.start()

    def _redraw_scan_overlay(self) -> None:
        if self.scan_overlay is None or self.scan_overlay_item is None:
            return
        self.scan_overlay[:] = 0
        thickness = self._get_zoom_adjusted_thickness()
        # Draw pending FOVs in red
        for fov in self._pending_fovs:
            self._draw_fov_box(
                self.scan_overlay,
                fov.x_mm,
                fov.y_mm,
                color=(255, 0, 0, 200),
                thickness=thickness,
                fov_width_mm=fov.fov_width_mm,
                fov_height_mm=fov.fov_height_mm,
            )
        # Draw completed FOVs in blue
        for fov in self._completed_fovs:
            self._draw_fov_box(
                self.scan_overlay,
                fov.x_mm,
                fov.y_mm,
                color=(0, 0, 255, 200),
                thickness=thickness,
                fov_width_mm=fov.fov_width_mm,
                fov_height_mm=fov.fov_height_mm,
            )
        self.scan_overlay_item.setImage(self.scan_overlay)

    def _on_view_range_changed(self, *args: Any) -> None:
        if self._pending_fovs or self._completed_fovs:
            new_thickness = self._get_zoom_adjusted_thickness()
            if self._current_thickness != new_thickness:
                self._current_thickness = new_thickness
                self._redraw_scan_overlay()

    def handle_mouse_click(self, evt: Any) -> None:
        if not evt.double():
            return
        try:
            mouse_point = self.background_item.mapFromScene(evt.scenePos())  # type: ignore[union-attr]
            # Convert pixels to mm: (a1_x_pixel, a1_y_pixel) maps to (a1_x_mm, a1_y_mm)
            x_mm = (mouse_point.x() - self.origin_x_pixel) * self.mm_per_pixel + self.a1_x_mm
            y_mm = (mouse_point.y() - self.origin_y_pixel) * self.mm_per_pixel + self.a1_y_mm

            if not self._click_to_move_enabled:
                return
            if self._event_bus is not None:
                self._event_bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))
        except Exception:
            return
