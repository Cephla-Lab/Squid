"""Unified mosaic/plate view widget.

Replaces NapariMosaicDisplayWidget and NapariPlateViewWidget with a single
widget that supports two display modes sharing one canvas per channel.
"""

import enum
import gc
import math
import sys
from typing import Dict, List, Tuple

import cv2
import numpy as np

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

import napari
from napari.utils import Colormap
from napari.utils.colormaps import AVAILABLE_COLORMAPS

import control._def
from control._def import CHANNEL_COLORS_MAP, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
import squid.logging


# Zoom limit constants (ported from NapariPlateViewWidget)
PLATE_VIEW_MIN_VISIBLE_PIXELS = 50
PLATE_VIEW_MAX_ZOOM_FACTOR = 50.0


class DisplayMode(enum.Enum):
    MOSAIC = "mosaic"
    PLATE = "plate"


def blit_tiles_to_canvas(
    canvas: np.ndarray,
    tiles: List[Tuple[np.ndarray, int, int]],
) -> None:
    """Blit tiles into canvas at given positions. Clips to canvas bounds."""
    canvas_h, canvas_w = canvas.shape[:2]
    for tile, y_px, x_px in tiles:
        tile_h, tile_w = tile.shape[:2]
        y_end = min(y_px + tile_h, canvas_h)
        x_end = min(x_px + tile_w, canvas_w)
        src_h = y_end - y_px
        src_w = x_end - x_px
        if src_h <= 0 or src_w <= 0:
            continue
        canvas[y_px:y_end, x_px:x_end] = tile[:src_h, :src_w]


class UnifiedMosaicWidget(QWidget):
    """Single widget for mosaic and plate view display.

    Replaces NapariMosaicDisplayWidget and NapariPlateViewWidget.
    One canvas per channel, two display modes.

    Mosaic mode places tiles at stage coordinates with physical spacing.
    Plate mode places tiles in a compact grid with well boundary lines.
    Toggling between modes clears the canvas; new tiles fill in at new positions.
    Napari layers use scale=(um, um) so world coordinates are in micrometers.
    """

    signal_coordinates_clicked = Signal(float, float)  # x_mm, y_mm (mosaic mode)
    signal_well_fov_clicked = Signal(str, int)  # well_id, fov_index (plate mode)
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal()
    signal_shape_drawn = Signal(list)

    def __init__(self, objectiveStore, camera, contrastManager, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager

        # Display state
        self.mode = DisplayMode.MOSAIC
        self.layers_initialized = False
        self.mosaic_dtype = None
        self.viewer_pixel_size_mm = None

        # Mosaic mode state — tracks canvas origin and extents (same as existing updateMosaic)
        self.viewer_extents = None  # [min_y, max_y, min_x, max_x] in mm
        self.top_left_coordinate = None  # [y_mm, x_mm] of canvas origin

        # Plate mode state — per-well origins for FOV offset calculation
        self._well_origins: Dict[str, Tuple[float, float]] = {}

        # Plate layout info (set when starting a well-based acquisition)
        self.num_rows = 0
        self.num_cols = 0
        self.well_slot_shape: Tuple[int, int] = (0, 0)
        self.fov_grid_shape: Tuple[int, int] = (1, 1)

        # Per-well TIFF saving is deferred — see plan R2/R7.

        # Shape drawing state (for manual ROI in mosaic mode; impl follows in Task 8)
        self.shapes_mm: list = []
        self.shape_layer = None

        # Zoom limits (plate mode)
        self.min_zoom = 0.1
        self.max_zoom = None
        self._clamping_zoom = False

        self.viewer = napari.Viewer(show=False)
        if sys.platform == "darwin":
            self.viewer.window.main_menu.setNativeMenuBar(False)
        self.viewer.window.main_menu.hide()

        canvas_widget = self.viewer.window._qt_viewer.canvas.native
        canvas_widget.wheelEvent = self._custom_wheel_event
        self.viewer.camera.events.zoom.connect(self._on_zoom_changed)

        layout = QVBoxLayout()
        layout.addWidget(self.viewer.window._qt_window)

        button_layout = QHBoxLayout()
        self.toggle_button = QPushButton("Switch to Plate View")
        self.toggle_button.clicked.connect(self._toggle_mode)
        self.toggle_button.setEnabled(False)
        button_layout.addWidget(self.toggle_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clearAllLayers)
        button_layout.addWidget(self.clear_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    # --- Plate layout setup ---

    def setPlateLayout(self, num_rows, num_cols, well_slot_shape, fov_grid_shape=None, channel_names=None):
        """Configure plate layout for plate mode. Called at acquisition start."""
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.well_slot_shape = tuple(well_slot_shape)
        self.fov_grid_shape = tuple(fov_grid_shape) if fov_grid_shape else (1, 1)
        self.toggle_button.setEnabled(True)
        plate_height = num_rows * self.well_slot_shape[0]
        plate_width = num_cols * self.well_slot_shape[1]
        if plate_height > 0 and plate_width > 0:
            min_plate_dim = min(plate_height, plate_width)
            self.max_zoom = min(
                max(1.0, min_plate_dim / PLATE_VIEW_MIN_VISIBLE_PIXELS),
                PLATE_VIEW_MAX_ZOOM_FACTOR,
            )

    # --- Signal compatibility stubs ---

    def initChannels(self, channels):
        """Accept channel list from acquisition widget (compatibility stub)."""

    def initLayersShape(self, shape):
        """Accept layer shape from acquisition widget (compatibility stub)."""

    def enable_shape_drawing(self, enable):
        """Enable/disable manual ROI shape drawing.

        Compatibility stub; the full implementation is added in Task 8 (Chunk 5).
        Without this stub the gui_hcs signal connections would fail at startup.
        """

    # --- Mode toggle ---

    def _toggle_mode(self):
        """Toggle between mosaic and plate mode. Clears canvas."""
        if self.mode == DisplayMode.MOSAIC:
            self.mode = DisplayMode.PLATE
            self.toggle_button.setText("Switch to Mosaic View")
        else:
            self.mode = DisplayMode.MOSAIC
            self.toggle_button.setText("Switch to Plate View")
        self.clearAllLayers()

    # --- Tile ingestion ---

    def updateTile(self, update):
        """Receive a new FOV image, downsample, and display.

        ``update`` is a ``MosaicTileUpdate`` (control.core.multi_point_utils).
        Single-arg signature so the widget receives a ``Signal(object)`` payload.
        Position is computed inline for the active mode only.
        """
        image = update.image
        x_mm = update.x_mm
        y_mm = update.y_mm
        channel_name = update.channel_name
        well_id = update.well_id
        well_row = update.well_row
        well_col = update.well_col
        # update.channel_index and update.fov_index are unused after R2 dropped
        # well-completion tracking. Kept on the dataclass for the deferred save.

        if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
            return

        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        downsample_factor = max(1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        image_pixel_size_mm = (pixel_size_um * downsample_factor) / 1000

        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (image.shape[1] // downsample_factor, image.shape[0] // downsample_factor),
                interpolation=cv2.INTER_AREA,
            )

        tl_x_mm = x_mm - (image.shape[1] * image_pixel_size_mm) / 2
        tl_y_mm = y_mm - (image.shape[0] * image_pixel_size_mm) / 2

        if not self.layers_initialized:
            self.layers_initialized = True
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.mosaic_dtype = image.dtype
            self.signal_layers_initialized.emit()
            self.viewer_extents = [
                tl_y_mm,
                tl_y_mm + image.shape[0] * image_pixel_size_mm,
                tl_x_mm,
                tl_x_mm + image.shape[1] * image_pixel_size_mm,
            ]
            self.top_left_coordinate = [tl_y_mm, tl_x_mm]
        else:
            image = self._convert_image_dtype(image, self.mosaic_dtype)

        if channel_name not in self.viewer.layers:
            self._create_channel_layer(channel_name, image)

        if self.mode == DisplayMode.MOSAIC:
            prev_top_left = self.top_left_coordinate.copy()
            self.viewer_extents[0] = min(self.viewer_extents[0], tl_y_mm)
            self.viewer_extents[1] = max(self.viewer_extents[1], tl_y_mm + image.shape[0] * self.viewer_pixel_size_mm)
            self.viewer_extents[2] = min(self.viewer_extents[2], tl_x_mm)
            self.viewer_extents[3] = max(self.viewer_extents[3], tl_x_mm + image.shape[1] * self.viewer_pixel_size_mm)
            self.top_left_coordinate = [self.viewer_extents[0], self.viewer_extents[2]]
            self._update_mosaic_layer(self.viewer.layers[channel_name], image, tl_x_mm, tl_y_mm, prev_top_left)
        else:
            slot_h, slot_w = self.well_slot_shape
            grid_y = well_row * slot_h
            grid_x = well_col * slot_w
            if well_id not in self._well_origins:
                self._well_origins[well_id] = (tl_x_mm, tl_y_mm)
            else:
                ox, oy = self._well_origins[well_id]
                self._well_origins[well_id] = (min(ox, tl_x_mm), min(oy, tl_y_mm))
            origin_x, origin_y = self._well_origins[well_id]
            fov_offset_x = int(round((tl_x_mm - origin_x) / self.viewer_pixel_size_mm))
            fov_offset_y = int(round((tl_y_mm - origin_y) / self.viewer_pixel_size_mm))
            y_px = grid_y + fov_offset_y
            x_px = grid_x + fov_offset_x

            layer = self.viewer.layers[channel_name]
            needed_h = y_px + image.shape[0]
            needed_w = x_px + image.shape[1]
            canvas_h, canvas_w = layer.data.shape[:2]
            if needed_h > canvas_h or needed_w > canvas_w:
                new_h = max(canvas_h, needed_h)
                new_w = max(canvas_w, needed_w)
                for lyr in self.viewer.layers:
                    if lyr.name == "_plate_boundaries" or not hasattr(lyr, "data"):
                        continue
                    old = lyr.data
                    expanded = np.zeros((new_h, new_w), dtype=old.dtype)
                    expanded[: old.shape[0], : old.shape[1]] = old
                    lyr.data = expanded
            blit_tiles_to_canvas(layer.data, [(image, y_px, x_px)])
            layer.refresh()

        min_val, max_val = self.contrastManager.get_limits(channel_name)
        if self.mosaic_dtype != self.contrastManager.acquisition_dtype:
            min_val = self._convert_value(min_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
            max_val = self._convert_value(max_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        self.viewer.layers[channel_name].contrast_limits = (min_val, max_val)

        if self.mode == DisplayMode.PLATE:
            self._draw_plate_boundaries()

    def _update_mosaic_layer(self, layer, image, tl_x_mm, tl_y_mm, prev_top_left):
        """Place tile on mosaic canvas, expanding and shifting if needed.

        Ported from NapariMosaicDisplayWidget.updateLayer.
        """
        mosaic_height = int(math.ceil((self.viewer_extents[1] - self.viewer_extents[0]) / self.viewer_pixel_size_mm))
        mosaic_width = int(math.ceil((self.viewer_extents[3] - self.viewer_extents[2]) / self.viewer_pixel_size_mm))

        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            y_offset = int(math.floor((prev_top_left[0] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
            x_offset = int(math.floor((prev_top_left[1] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))
            for lyr in self.viewer.layers:
                if lyr.name == "_plate_boundaries" or not hasattr(lyr, "data"):
                    continue
                new_data = np.zeros((mosaic_height, mosaic_width), dtype=lyr.data.dtype)
                y_end = min(y_offset + lyr.data.shape[0], new_data.shape[0])
                x_end = min(x_offset + lyr.data.shape[1], new_data.shape[1])
                new_data[y_offset:y_end, x_offset:x_end] = lyr.data[: y_end - y_offset, : x_end - x_offset]
                lyr.data = new_data
            self.resetView()

        y_pos = int(math.floor((tl_y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
        x_pos = int(math.floor((tl_x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))
        blit_tiles_to_canvas(layer.data, [(image, y_pos, x_pos)])
        layer.refresh()

    # --- Channel layer creation ---

    def _create_channel_layer(self, channel_name, reference_image):
        """Create a new napari image layer for a channel."""
        wavelength = self._extract_wavelength(channel_name)
        channel_info = (
            CHANNEL_COLORS_MAP.get(wavelength, {"hex": 0xFFFFFF, "name": "gray"})
            if wavelength
            else {"hex": 0xFFFFFF, "name": "gray"}
        )
        if channel_info["name"] in AVAILABLE_COLORMAPS:
            color = AVAILABLE_COLORMAPS[channel_info["name"]]
        else:
            color = self._generate_colormap(channel_info)

        scale_um = self.viewer_pixel_size_mm * 1000
        layer = self.viewer.add_image(
            np.zeros_like(reference_image),
            name=channel_name,
            colormap=color,
            visible=True,
            blending="additive",
            scale=(scale_um, scale_um),
        )
        layer.mouse_double_click_callbacks.append(self._on_double_click)
        layer.events.contrast_limits.connect(self._on_contrast_change)

    # --- Dtype conversion (ported from NapariMosaicDisplayWidget) ---

    def _convert_image_dtype(self, image, target_dtype):
        """Convert image to target dtype with range scaling."""
        if image.dtype == target_dtype:
            return image
        if np.issubdtype(image.dtype, np.integer):
            info = np.iinfo(image.dtype)
            in_min, in_max = info.min, info.max
        else:
            in_min, in_max = float(np.min(image)), float(np.max(image))
        if np.issubdtype(target_dtype, np.integer):
            info = np.iinfo(target_dtype)
            out_min, out_max = info.min, info.max
        else:
            out_min, out_max = 0.0, 1.0
        normalized = (image.astype(np.float64) - in_min) / max(in_max - in_min, 1)
        scaled = normalized * (out_max - out_min) + out_min
        return scaled.astype(target_dtype)

    def _convert_value(self, value, from_dtype, to_dtype):
        """Convert a scalar value between dtype ranges."""
        from_info = np.iinfo(from_dtype)
        to_info = np.iinfo(to_dtype)
        normalized = (value - from_info.min) / max(from_info.max - from_info.min, 1)
        return normalized * (to_info.max - to_info.min) + to_info.min

    # --- Colormap helpers ---

    def _extract_wavelength(self, name):
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def _generate_colormap(self, channel_info):
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    # --- Double-click navigation ---

    def _on_double_click(self, layer, event):
        """Handle double-click for navigation."""
        coords = layer.world_to_data(event.position)
        if coords is None:
            return
        y, x = int(coords[-2]), int(coords[-1])

        if self.mode == DisplayMode.MOSAIC:
            if self.viewer_pixel_size_mm and self.top_left_coordinate:
                x_mm = self.top_left_coordinate[1] + x * self.viewer_pixel_size_mm
                y_mm = self.top_left_coordinate[0] + y * self.viewer_pixel_size_mm
                self.signal_coordinates_clicked.emit(x_mm, y_mm)
            return

        if self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return
        well_row = y // self.well_slot_shape[0]
        well_col = x // self.well_slot_shape[1]
        if well_row < 0 or well_row >= self.num_rows or well_col < 0 or well_col >= self.num_cols:
            return
        from control.core.downsampled_views import format_well_id

        well_id = format_well_id(well_row, well_col)
        y_in_well = y % self.well_slot_shape[0]
        x_in_well = x % self.well_slot_shape[1]
        fov_ny, fov_nx = self.fov_grid_shape
        if fov_ny > 0 and fov_nx > 0:
            fov_height = self.well_slot_shape[0] // fov_ny
            fov_width = self.well_slot_shape[1] // fov_nx
            if fov_height > 0 and fov_width > 0:
                fov_row = min(y_in_well // fov_height, fov_ny - 1)
                fov_col = min(x_in_well // fov_width, fov_nx - 1)
                fov_index = fov_row * fov_nx + fov_col
            else:
                fov_index = 0
        else:
            fov_index = 0
        self.signal_well_fov_clicked.emit(well_id, fov_index)

    def _on_contrast_change(self, event):
        layer = event.source
        min_val, max_val = layer.contrast_limits
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    # --- Zoom limits (active in plate mode) ---

    def _custom_wheel_event(self, event):
        """Custom wheel event handler that enforces zoom limits in plate mode."""
        event.accept()
        delta = event.angleDelta().y()
        if delta == 0:
            return
        zoom = self.viewer.camera.zoom
        zoom_factor = 1.1 ** (delta / 120.0)
        new_zoom = zoom * zoom_factor

        if self.mode == DisplayMode.MOSAIC and self.max_zoom is None:
            self.viewer.camera.zoom = new_zoom
            return

        new_zoom = max(self.min_zoom, new_zoom)
        if self.max_zoom is not None:
            new_zoom = min(self.max_zoom, new_zoom)
        if new_zoom != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = new_zoom
            self._clamping_zoom = False

    def _on_zoom_changed(self, event):
        """Clamp zoom to limits after any zoom change."""
        if self._clamping_zoom or self.mode == DisplayMode.MOSAIC:
            return
        zoom = self.viewer.camera.zoom
        target = zoom
        if zoom < self.min_zoom:
            target = self.min_zoom
        elif self.max_zoom is not None and zoom > self.max_zoom:
            target = self.max_zoom
        if target != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = target
            self._clamping_zoom = False

    # --- Plate grid lines ---

    def _draw_plate_boundaries(self):
        """Draw grid lines at well boundaries (plate mode only). Drawn once."""
        if self.num_rows == 0 or self.num_cols == 0:
            return
        if self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return
        if "_plate_boundaries" in self.viewer.layers:
            return

        lines = []
        slot_h, slot_w = self.well_slot_shape
        plate_height = self.num_rows * slot_h
        plate_width = self.num_cols * slot_w

        for row in range(self.num_rows + 1):
            y = row * slot_h
            lines.append([[y, 0], [y, plate_width]])
        for col in range(self.num_cols + 1):
            x = col * slot_w
            lines.append([[0, x], [plate_height, x]])

        if not lines:
            return
        self.viewer.add_shapes(
            lines,
            shape_type="line",
            edge_color="white",
            edge_width=2,
            name="_plate_boundaries",
        )
        boundaries = self.viewer.layers["_plate_boundaries"]
        boundaries.mouse_pan = False
        boundaries.mouse_zoom = False
        self.viewer.layers.move(len(self.viewer.layers) - 1, 0)
        for layer in reversed(self.viewer.layers):
            if layer.name != "_plate_boundaries":
                self.viewer.layers.selection.active = layer
                break

    # --- Public API ---

    # Per-well save (`_on_well_complete`, `set_job_runner`, `get_well_crop`) is
    # deferred — see plan R2/R7.

    def clearAllLayers(self):
        """Clear all layers and reset state."""
        for layer in list(self.viewer.layers):
            self.viewer.layers.remove(layer)
        self._well_origins.clear()
        self.viewer_extents = None
        self.top_left_coordinate = None
        self.layers_initialized = False
        self.mosaic_dtype = None
        self.signal_clear_viewer.emit()
        gc.collect()

    def resetView(self):
        self.viewer.reset_view()

    def get_screenshot(self):
        """Return RGB screenshot of the current view."""
        try:
            return self.viewer.screenshot(canvas_only=True)
        except Exception as e:
            self._log.warning(f"Screenshot failed: {e}")
            return None

    def activate(self):
        self.viewer.window.activate()
