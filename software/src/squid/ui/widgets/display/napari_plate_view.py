# Napari plate view widget for downsampled well/plate display
"""Widget for displaying downsampled plate view with multi-channel support.

This widget displays downsampled well images in a grid layout during acquisition,
providing an overview of the entire plate. It's specifically for plate-based
acquisitions in Select Wells mode.
"""

from __future__ import annotations
import gc
from typing import Dict, List, Optional, Tuple

import numpy as np
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS

from qtpy.QtCore import Signal
from qtpy.QtGui import QIcon, QWheelEvent
from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton

from _def import (
    CHANNEL_COLORS_MAP,
    PLATE_VIEW_MIN_VISIBLE_PIXELS,
    PLATE_VIEW_MAX_ZOOM_FACTOR,
    SQUID_ICON_PATH,
)
from squid.backend.managers import ContrastManager
from squid.backend.controllers.multipoint.downsampled_views import format_well_id

import squid.core.logging


class NapariPlateViewWidget(QWidget):
    """Widget for displaying downsampled plate view with multi-channel support.

    Similar to NapariMosaicDisplayWidget but specifically for plate-based acquisitions.
    Displays downsampled well images in a grid layout.
    """

    _log = squid.core.logging.get_logger(__name__)
    signal_well_fov_clicked = Signal(str, int)  # well_id, fov_index

    def __init__(
        self,
        contrastManager: ContrastManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        _layout = QVBoxLayout()
        _layout.addWidget(self.viewer.window._qt_window)

        # Clear button
        self.clear_button = QPushButton("Clear Plate View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        _layout.addWidget(self.clear_button)

        self.setLayout(_layout)
        self._customizeViewer()

        # Plate layout info (set by initPlateLayout)
        self.num_rows = 0
        self.num_cols = 0
        self.well_slot_shape: Tuple[int, int] = (0, 0)  # (height, width) pixels per well
        self.fov_grid_shape: Tuple[int, int] = (1, 1)  # (ny, nx) FOVs per well
        self.channel_names: List[str] = []
        self.plate_dtype: Optional[np.dtype] = None
        self.layers_initialized = False

        # Zoom limits (updated in initPlateLayout based on plate size)
        self.min_zoom = 0.1  # Prevent zooming out too far
        self.max_zoom: Optional[float] = None  # No max limit until plate size is known

        # Flag to prevent recursive zoom clamping. This is safe because Qt's event
        # loop processes events sequentially on the main thread - _custom_wheel_event
        # and _on_zoom_changed cannot run concurrently, so no lock is needed.
        self._clamping_zoom = False

        # Override wheel event on vispy canvas to enforce zoom limits
        canvas_widget = self.viewer.window._qt_viewer.canvas.native
        self._original_wheel_event = canvas_widget.wheelEvent
        canvas_widget.wheelEvent = self._custom_wheel_event

        # Clamp zoom for programmatic changes (e.g., reset_view)
        self.viewer.camera.events.zoom.connect(self._on_zoom_changed)

    def _customizeViewer(self) -> None:
        """Set Squid/Cephla branding on napari viewer."""
        self.viewer.window._qt_window.setWindowIcon(QIcon(str(SQUID_ICON_PATH)))
        self.viewer.window._qt_window.setWindowTitle("Squid Microscope - Plate View")
        # Hide the napari menu bar (clear it for macOS global menu bar)
        self.viewer.window.main_menu.clear()

    def initPlateLayout(
        self,
        num_rows: int,
        num_cols: int,
        well_slot_shape: Tuple[int, int],
        fov_grid_shape: Optional[Tuple[int, int]] = None,
        channel_names: Optional[List[str]] = None,
    ) -> None:
        """Initialize plate layout for click coordinate calculations.

        Args:
            num_rows: Number of rows in the plate
            num_cols: Number of columns in the plate
            well_slot_shape: (height, width) of each well slot in pixels
            fov_grid_shape: (ny, nx) FOVs per well for click mapping
            channel_names: List of channel names
        """
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.well_slot_shape = well_slot_shape
        self.fov_grid_shape = fov_grid_shape or (1, 1)
        self.channel_names = channel_names or []
        self.layers_initialized = False

        # Calculate zoom limits based on plate size
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]
        if plate_height > 0 and plate_width > 0:
            # Max zoom: ensure at least MIN_VISIBLE_PIXELS visible, capped at MAX_ZOOM_FACTOR
            min_plate_dim = min(plate_height, plate_width)
            self.max_zoom = min(
                max(1.0, min_plate_dim / PLATE_VIEW_MIN_VISIBLE_PIXELS),
                PLATE_VIEW_MAX_ZOOM_FACTOR,
            )

        # Draw plate boundaries
        self._draw_plate_boundaries()

        # Reset view to fit plate
        self.viewer.reset_view()

        # Set min_zoom to allow viewing entire plate with margin
        # Use a very low floor (0.01) to ensure user can always zoom out enough
        # The reset_view zoom is a good starting point but shouldn't be the limit
        reset_zoom = self.viewer.camera.zoom
        self.min_zoom = min(reset_zoom * 0.5, 0.01)  # Allow zooming out 2x beyond reset, floor at 0.01

        self._log.info(
            f"Plate layout initialized: {num_rows}x{num_cols} wells, "
            f"slot_shape={well_slot_shape}, zoom range=[{self.min_zoom:.3f}, {self.max_zoom:.3f}]"
        )

    def _custom_wheel_event(self, event: QWheelEvent) -> None:
        """Custom wheel event handler that enforces zoom limits."""
        # Block ALL wheel events from reaching vispy - we handle zoom ourselves
        event.accept()

        delta = event.angleDelta().y()
        if delta == 0:
            return

        # Calculate new zoom with our own factor
        zoom = self.viewer.camera.zoom
        zoom_factor = 1.1 ** (delta / 120.0)  # Standard wheel: 120 units per notch
        new_zoom = zoom * zoom_factor

        # Clamp to limits
        new_zoom = max(self.min_zoom, new_zoom)
        if self.max_zoom is not None:
            new_zoom = min(self.max_zoom, new_zoom)

        # Apply clamped zoom
        if new_zoom != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = new_zoom
            self._clamping_zoom = False

    def _on_zoom_changed(self, event) -> None:
        """Clamp zoom to limits after any zoom change (e.g., reset_view)."""
        if self._clamping_zoom:
            return
        zoom = self.viewer.camera.zoom
        target_zoom = zoom
        if zoom < self.min_zoom:
            target_zoom = self.min_zoom
        elif self.max_zoom is not None and zoom > self.max_zoom:
            target_zoom = self.max_zoom
        if target_zoom != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = target_zoom
            self._clamping_zoom = False

    def _draw_plate_boundaries(self) -> None:
        """Draw boundary rectangles around each well."""
        if self.num_rows == 0 or self.num_cols == 0:
            return
        if self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return

        # Remove existing boundary layer
        if "_plate_boundaries" in self.viewer.layers:
            self.viewer.layers.remove("_plate_boundaries")

        rectangles = []
        slot_h, slot_w = self.well_slot_shape

        for row in range(self.num_rows):
            for col in range(self.num_cols):
                y0 = row * slot_h
                x0 = col * slot_w
                # Rectangle corners: top-left, top-right, bottom-right, bottom-left
                rect = [
                    [y0, x0],
                    [y0, x0 + slot_w],
                    [y0 + slot_h, x0 + slot_w],
                    [y0 + slot_h, x0],
                ]
                rectangles.append(rect)

        if rectangles:
            self.viewer.add_shapes(
                rectangles,
                shape_type="polygon",
                edge_color="white",
                edge_width=2,
                face_color="transparent",
                name="_plate_boundaries",
            )
            # Move boundaries layer to bottom so it doesn't interfere with clicks
            self.viewer.layers.move(len(self.viewer.layers) - 1, 0)

    def _extractWavelength(self, name: str) -> Optional[str]:
        """Extract wavelength from channel name for colormap selection."""
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def _generateColormap(self, channel_info: Dict) -> Colormap:
        """Generate colormap from hex value."""
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updatePlateView(
        self,
        channel_idx: int,
        channel_name: str,
        plate_image: np.ndarray,
    ) -> None:
        """Update a single channel's plate view.

        Args:
            channel_idx: Channel index (0-based)
            channel_name: Name of the channel
            plate_image: 2D numpy array with the channel's plate view
        """
        if plate_image is None:
            return

        if not self.layers_initialized:
            self.layers_initialized = True
            self.plate_dtype = plate_image.dtype

        if channel_name not in self.viewer.layers:
            # Create layer with appropriate colormap
            wavelength = self._extractWavelength(channel_name)
            channel_info = (
                CHANNEL_COLORS_MAP.get(wavelength, {"hex": 0xFFFFFF, "name": "gray"})
                if wavelength is not None
                else {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self._generateColormap(channel_info)

            layer = self.viewer.add_image(
                plate_image,
                name=channel_name,
                colormap=color,
                visible=True,
                blending="additive",
            )
            layer.mouse_double_click_callbacks.append(self._onDoubleClick)
            layer.events.contrast_limits.connect(self._signalContrastLimits)
        else:
            self.viewer.layers[channel_name].data = plate_image

        # Apply contrast from contrastManager
        layer = self.viewer.layers[channel_name]
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        layer.contrast_limits = (min_val, max_val)
        layer.refresh()

    def _signalContrastLimits(self, event) -> None:
        """Handle contrast limit changes and propagate to contrastManager."""
        layer = event.source
        min_val, max_val = layer.contrast_limits
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def _onDoubleClick(self, layer, event) -> None:
        """Handle double-click: calculate well_id and fov_index."""
        coords = layer.world_to_data(event.position)
        if coords is None or self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return

        y, x = int(coords[-2]), int(coords[-1])

        # Calculate well position
        well_row = y // self.well_slot_shape[0]
        well_col = x // self.well_slot_shape[1]

        # Validate well position
        if (
            well_row < 0
            or well_row >= self.num_rows
            or well_col < 0
            or well_col >= self.num_cols
        ):
            self._log.debug(f"Clicked outside plate bounds: row={well_row}, col={well_col}")
            return

        # Generate well ID using shared utility (inverse of parse_well_id)
        well_id = format_well_id(well_row, well_col)

        # Calculate FOV within well
        y_in_well = y % self.well_slot_shape[0]
        x_in_well = x % self.well_slot_shape[1]

        fov_ny, fov_nx = self.fov_grid_shape
        if fov_ny > 0 and fov_nx > 0:
            fov_height = self.well_slot_shape[0] // fov_ny
            fov_width = self.well_slot_shape[1] // fov_nx
            if fov_height > 0 and fov_width > 0:
                # Clamp to valid range to handle clicks at edge of well slot
                fov_row = min(y_in_well // fov_height, fov_ny - 1)
                fov_col = min(x_in_well // fov_width, fov_nx - 1)
                fov_index = fov_row * fov_nx + fov_col
            else:
                fov_index = 0
        else:
            fov_index = 0

        self._log.info(f"Clicked: Well {well_id}, FOV {fov_index}")
        self.signal_well_fov_clicked.emit(well_id, fov_index)

    def resetView(self) -> None:
        """Reset the viewer to fit all data."""
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clearAllLayers(self) -> None:
        """Clear all layers to free memory."""
        layers_to_remove = list(self.viewer.layers)
        for layer in layers_to_remove:
            self.viewer.layers.remove(layer)

        self.layers_initialized = False
        self.plate_dtype = None
        gc.collect()

    def activate(self) -> None:
        """Activate the viewer window."""
        self.viewer.window.activate()
