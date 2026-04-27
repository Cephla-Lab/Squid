"""Unified mosaic/plate view widget.

Replaces NapariMosaicDisplayWidget and NapariPlateViewWidget with a single
widget that supports two display modes sharing one canvas per channel.
"""

import enum
import math
import sys
from typing import List, Tuple

import numpy as np

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

import napari
from napari.utils import Colormap
from napari.utils.colormaps import AVAILABLE_COLORMAPS

import control._def
from control._def import CHANNEL_COLORS_MAP, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
from control.core.downsampled_views import downsample_tile
from control.utils_channel import extract_wavelength_from_config_name
import squid.logging


PLATE_VIEW_MIN_VISIBLE_PIXELS = 50
PLATE_VIEW_MAX_ZOOM_FACTOR = 50.0
PLATE_BOUNDARIES_LAYER = "_plate_boundaries"
MANUAL_ROI_LAYER = "Manual ROI"
NON_IMAGE_LAYERS = (PLATE_BOUNDARIES_LAYER, MANUAL_ROI_LAYER)


class DisplayMode(enum.Enum):
    MOSAIC = "mosaic"
    PLATE = "plate"


# User-facing labels for the two modes inside the Mosaic View tab. The enum
# keeps the historical "MOSAIC" name (it's internal); the UI calls it
# "Full View" since "Mosaic View" is now the umbrella tab title.
DISPLAY_MODE_LABELS = {
    DisplayMode.MOSAIC: "Full View",
    DisplayMode.PLATE: "Plate View",
}


def blit_tiles_to_canvas(
    canvas: np.ndarray,
    tiles: List[Tuple[np.ndarray, int, int]],
) -> None:
    """Blit tiles into canvas at given positions. Clips both negative and
    out-of-bounds offsets so a misplaced tile is dropped, never wrapped via
    NumPy negative slicing."""
    canvas_h, canvas_w = canvas.shape[:2]
    for tile, y_px, x_px in tiles:
        tile_h, tile_w = tile.shape[:2]
        dst_y_start = max(y_px, 0)
        dst_x_start = max(x_px, 0)
        dst_y_end = min(y_px + tile_h, canvas_h)
        dst_x_end = min(x_px + tile_w, canvas_w)
        if dst_y_start >= dst_y_end or dst_x_start >= dst_x_end:
            continue
        src_y_start = max(-y_px, 0)
        src_x_start = max(-x_px, 0)
        src_y_end = src_y_start + (dst_y_end - dst_y_start)
        src_x_end = src_x_start + (dst_x_end - dst_x_start)
        canvas[dst_y_start:dst_y_end, dst_x_start:dst_x_end] = tile[src_y_start:src_y_end, src_x_start:src_x_end]


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

        self.mode = DisplayMode.MOSAIC
        self.layers_initialized = False
        self.mosaic_dtype = None
        self.viewer_pixel_size_mm = None

        # Cached after first tile so the hot-path doesn't repeat objective/camera lookups.
        self._pixel_size_um: float = 0.0
        self._downsample_factor: int = 1

        self.viewer_extents = None  # [min_y, max_y, min_x, max_x] in mm
        self.top_left_coordinate = None  # [y_mm, x_mm] of canvas origin

        self.num_rows = 0
        self.num_cols = 0
        self.well_slot_shape: Tuple[int, int] = (0, 0)
        self.fov_grid_shape: Tuple[int, int] = (1, 1)
        # Well coverage from the most recent setPlateLayout. Used to detect when
        # a new acquisition scans a different set of wells, in which case the
        # plate canvas is wiped so old tiles don't linger at the wrong slots.
        self._plate_well_ids: frozenset = frozenset()

        self.shapes_mm: list = []
        self.shape_layer = None
        self.is_drawing_shape = False

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
        self.toggle_button = QPushButton(self._toggle_button_label())
        self.toggle_button.clicked.connect(self._toggle_mode)
        button_layout.addWidget(self.toggle_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clearAllLayers)
        button_layout.addWidget(self.clear_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    # --- Plate layout setup ---

    def setPlateLayout(
        self, num_rows, num_cols, well_slot_shape, fov_grid_shape=None, channel_names=None, well_ids=None
    ):
        """Configure plate layout for plate mode. Called at the start of every
        plate-based acquisition.

        Existing channel canvases are wiped to fresh zero-filled plate-sized
        arrays whenever the slot dimensions changed *or* the set of wells being
        scanned changed — both cases would otherwise leave tiles from the
        previous run sitting at coordinates that no longer match the current
        plate grid.
        """
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.well_slot_shape = tuple(well_slot_shape)
        self.fov_grid_shape = tuple(fov_grid_shape) if fov_grid_shape else (1, 1)
        plate_height = num_rows * self.well_slot_shape[0]
        plate_width = num_cols * self.well_slot_shape[1]
        if plate_height > 0 and plate_width > 0:
            min_plate_dim = min(plate_height, plate_width)
            self.max_zoom = min(
                max(1.0, min_plate_dim / PLATE_VIEW_MIN_VISIBLE_PIXELS),
                PLATE_VIEW_MAX_ZOOM_FACTOR,
            )

        if plate_height <= 0 or plate_width <= 0:
            return
        target_dims = (plate_height, plate_width)
        new_well_ids = frozenset(well_ids) if well_ids else frozenset()
        coverage_changed = new_well_ids != self._plate_well_ids
        self._plate_well_ids = new_well_ids
        for layer in self._image_layers():
            dims_changed = layer.data.shape[:2] != target_dims
            if dims_changed or coverage_changed:
                layer.data = np.zeros(target_dims + layer.data.shape[2:], dtype=layer.data.dtype)
        # Boundaries depend on slot dims — drop so they get redrawn on the next tile.
        if PLATE_BOUNDARIES_LAYER in self.viewer.layers:
            self.viewer.layers.remove(self.viewer.layers[PLATE_BOUNDARIES_LAYER])

    def _image_layers(self):
        """Iterate napari image layers, skipping shape/boundary overlays."""
        return [lyr for lyr in self.viewer.layers if lyr.name not in NON_IMAGE_LAYERS and hasattr(lyr, "data")]

    def enable_shape_drawing(self, enable):
        """Enable or disable manual ROI shape drawing (mosaic mode only)."""
        if self.mode != DisplayMode.MOSAIC:
            # Plate mode has no concept of stage-coordinate ROIs.
            return
        if enable:
            self._toggle_draw_mode()
        else:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.mode = "pan_zoom"

    def _toggle_draw_mode(self):
        """Internal toggle invoked by ``enable_shape_drawing(True)``."""
        self.is_drawing_shape = not self.is_drawing_shape

        if MANUAL_ROI_LAYER not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name=MANUAL_ROI_LAYER, edge_width=40, edge_color="red", face_color="transparent"
            )
            self.shape_layer.events.data.connect(self._on_shape_change)
        else:
            self.shape_layer = self.viewer.layers[MANUAL_ROI_LAYER]

        if self.is_drawing_shape:
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                self.shape_layer.mode = "add_polygon"
        else:
            self.shape_layer.mode = "pan_zoom"

        self._on_shape_change()

    def _on_shape_change(self, event=None):
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # Only convert shapes once we have a coordinate system.
            if self.layers_initialized and self.top_left_coordinate is not None:
                self.shapes_mm = [self._convert_shape_to_mm(shape) for shape in self.shape_layer.data]
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

    def _convert_shape_to_mm(self, shape_data):
        """Pixel-coords-on-canvas → mm in stage coordinate frame."""
        result = []
        scale = self.viewer_pixel_size_mm * 1000  # napari layer scale is in um
        for point in shape_data:
            y_data = point[0] / scale
            x_data = point[1] / scale
            x_mm = self.top_left_coordinate[1] + x_data * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + y_data * self.viewer_pixel_size_mm
            result.append([x_mm, y_mm])
        return np.array(result)

    def _convert_mm_to_viewer_shapes(self, shapes_mm):
        """mm in stage coordinate frame → world coordinates (um) for napari."""
        viewer_shapes = []
        scale = self.viewer_pixel_size_mm * 1000
        for shape_mm in shapes_mm:
            viewer_shape = []
            for point_mm in shape_mm:
                x_data = (point_mm[0] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm
                y_data = (point_mm[1] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm
                viewer_shape.append([y_data * scale, x_data * scale])
            viewer_shapes.append(viewer_shape)
        return viewer_shapes

    def _update_shape_layer_position(self):
        """Re-render shapes after the canvas origin shifts (mosaic mode canvas growth)."""
        if self.shape_layer is None or not self.shapes_mm:
            return
        try:
            self.shape_layer.data = self._convert_mm_to_viewer_shapes(self.shapes_mm)
        except Exception as e:
            self._log.warning(f"Failed to reposition shape layer after canvas shift: {e}")

    def _clear_shape(self):
        if self.shape_layer is not None:
            try:
                self.viewer.layers.remove(self.shape_layer)
            except Exception:
                pass
            self.shape_layer = None
            self.is_drawing_shape = False
            self.signal_shape_drawn.emit([])

    # --- Mode toggle ---

    def _toggle_button_label(self):
        """Text shown on the toggle button — names the *target* mode."""
        target = DisplayMode.PLATE if self.mode == DisplayMode.MOSAIC else DisplayMode.MOSAIC
        return f"Switch to {DISPLAY_MODE_LABELS[target]}"

    def maybe_switch_to_full_view(self, scan_label: str = "") -> None:
        """If currently in Plate View, prompt the user to switch to Full View
        and clear the canvas. Intended for acquisitions that don't produce a
        plate layout (everything except Select Wells).

        No-op when the widget is already in Full View. If the user declines,
        Plate View stays as-is and won't update during this acquisition — see
        the dialog body for the wording shown to the user.
        """
        if self.mode != DisplayMode.PLATE:
            return
        label_suffix = f" ('{scan_label}')" if scan_label else ""
        reply = QMessageBox.question(
            self,
            "Switch to Full View?",
            (
                f"Plate View only updates for Select Wells acquisitions. "
                f"This acquisition{label_suffix} won't be shown in Plate View.\n\n"
                "Switch to Full View now and clear the current canvas?\n"
                "(Choose No to keep the existing Plate View; it will not update.)"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return
        self.mode = DisplayMode.MOSAIC
        self.toggle_button.setText(self._toggle_button_label())
        self._clear_shape()
        self.clearAllLayers()

    def _toggle_mode(self):
        """Toggle between full-stage and plate-grid layout. Clears the canvas
        and ROI shapes — confirms with the user first when there's something
        on screen to lose.
        """
        target = DisplayMode.PLATE if self.mode == DisplayMode.MOSAIC else DisplayMode.MOSAIC
        target_label = DISPLAY_MODE_LABELS[target]
        if self.layers_initialized:
            reply = QMessageBox.question(
                self,
                f"Switch to {target_label}",
                f"Switching to {target_label} clears the current view. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.mode = target
        self.toggle_button.setText(self._toggle_button_label())
        # ROI shapes are stage-coord-based and only meaningful in MOSAIC mode.
        self._clear_shape()
        self.clearAllLayers()

    # --- Tile ingestion ---

    def updateTile(self, update):
        """Receive a new FOV image, downsample, and display.

        ``update`` is a ``MosaicTileUpdate`` (control.core.multi_point_utils).
        Single-arg signature so the widget receives a ``Signal(object)`` payload.
        Position is computed inline for the active mode only.
        """
        if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
            return

        image = update.image
        x_mm = update.x_mm
        y_mm = update.y_mm
        channel_name = update.channel_name

        if self._pixel_size_um == 0.0:
            self._pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
            self._downsample_factor = max(1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / self._pixel_size_um))

        image = downsample_tile(image, self._pixel_size_um, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM)
        image_pixel_size_mm = (self._pixel_size_um * self._downsample_factor) / 1000
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
            # Manual ROI survives clearAllLayers but its pixel-coord data is
            # stale relative to the freshly-initialized coordinate system.
            self._update_shape_layer_position()
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
            # well_origin_mm is None when the source acquisition isn't a plate
            # scan (anything other than Select Wells). We can't position the
            # tile in plate-grid space without an origin, so skip the blit
            # entirely — the user was already warned via the prompt at
            # acquisition start that Plate View won't update.
            if update.well_origin_mm is None:
                return
            slot_h, slot_w = self.well_slot_shape
            origin_x, origin_y = update.well_origin_mm
            fov_offset_x = int(round((tl_x_mm - origin_x) / self.viewer_pixel_size_mm))
            fov_offset_y = int(round((tl_y_mm - origin_y) / self.viewer_pixel_size_mm))
            y_px = update.well_row * slot_h + fov_offset_y
            x_px = update.well_col * slot_w + fov_offset_x

            layer = self.viewer.layers[channel_name]
            blit_tiles_to_canvas(layer.data, [(image, y_px, x_px)])
            layer.refresh()
            self._draw_plate_boundaries()

        # Contrast is per-monochrome-channel; RGB layers display the colour
        # image directly and don't go through the channel ContrastManager.
        # Skip the no-op-guarded update for them.
        if image.ndim != 3 or image.shape[2] != 3:
            new_limits = self.contrastManager.get_scaled_limits(channel_name, self.mosaic_dtype)
            layer = self.viewer.layers[channel_name]
            if tuple(layer.contrast_limits) != tuple(new_limits):
                layer.contrast_limits = new_limits

    def _update_mosaic_layer(self, layer, image, tl_x_mm, tl_y_mm, prev_top_left):
        """Place tile on the mosaic canvas, expanding and shifting if extents grew."""
        mosaic_height = int(math.ceil((self.viewer_extents[1] - self.viewer_extents[0]) / self.viewer_pixel_size_mm))
        mosaic_width = int(math.ceil((self.viewer_extents[3] - self.viewer_extents[2]) / self.viewer_pixel_size_mm))

        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            y_offset = int(math.floor((prev_top_left[0] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
            x_offset = int(math.floor((prev_top_left[1] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))
            for lyr in self._image_layers():
                # Preserve trailing dims (RGB layers carry shape (H, W, 3)).
                new_shape = (mosaic_height, mosaic_width) + lyr.data.shape[2:]
                new_data = np.zeros(new_shape, dtype=lyr.data.dtype)
                y_end = min(y_offset + lyr.data.shape[0], new_data.shape[0])
                x_end = min(x_offset + lyr.data.shape[1], new_data.shape[1])
                new_data[y_offset:y_end, x_offset:x_end] = lyr.data[: y_end - y_offset, : x_end - x_offset]
                lyr.data = new_data
            self.resetView()
            # Keep ROI vertices anchored to their stage-coordinate positions after the shift.
            self._update_shape_layer_position()

        y_pos = int(math.floor((tl_y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
        x_pos = int(math.floor((tl_x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))
        blit_tiles_to_canvas(layer.data, [(image, y_pos, x_pos)])
        layer.refresh()

    def _create_channel_layer(self, channel_name, reference_image):
        """Create a new napari image layer for a channel.

        In plate mode the canvas is pre-allocated to the full plate dimensions
        — known up front from setPlateLayout — so no per-tile resizing is needed.
        Mosaic mode starts at one tile and grows as canvas extents expand.

        RGB tiles (e.g. from MultiPointWorker.construct_rgb_image) get a
        ``(H, W, 3)`` canvas and ``rgb=True`` on the napari layer so it renders
        the colour image directly instead of running a colormap over channel-0.
        """
        is_rgb = reference_image.ndim == 3 and reference_image.shape[2] == 3
        if self.mode == DisplayMode.PLATE and self.num_rows > 0 and self.num_cols > 0:
            slot_h, slot_w = self.well_slot_shape
            shape = (self.num_rows * slot_h, self.num_cols * slot_w)
            if is_rgb:
                shape = shape + (3,)
            initial_data = np.zeros(shape, dtype=reference_image.dtype)
        else:
            initial_data = np.zeros_like(reference_image)

        scale_um = self.viewer_pixel_size_mm * 1000
        layer_kwargs = dict(name=channel_name, visible=True, scale=(scale_um, scale_um))
        if is_rgb:
            layer = self.viewer.add_image(initial_data, rgb=True, **layer_kwargs)
        else:
            wavelength = extract_wavelength_from_config_name(channel_name)
            channel_info = CHANNEL_COLORS_MAP.get(wavelength, {"hex": 0xFFFFFF, "name": "gray"})
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self._generate_colormap(channel_info)
            layer = self.viewer.add_image(initial_data, colormap=color, blending="additive", **layer_kwargs)
            layer.events.contrast_limits.connect(self._on_contrast_change)
        layer.mouse_double_click_callbacks.append(self._on_double_click)

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
        normalized = (image.astype(np.float32) - in_min) / max(in_max - in_min, 1)
        scaled = normalized * (out_max - out_min) + out_min
        return scaled.astype(target_dtype)

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
        """Wheel handler that enforces zoom limits in plate mode only.

        max_zoom may have been set by setPlateLayout from a previous plate-based
        config — we ignore it in mosaic mode so mosaic zooming stays unrestricted.
        """
        event.accept()
        delta = event.angleDelta().y()
        if delta == 0:
            return
        zoom = self.viewer.camera.zoom
        zoom_factor = 1.1 ** (delta / 120.0)
        new_zoom = zoom * zoom_factor

        if self.mode == DisplayMode.MOSAIC:
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
        if PLATE_BOUNDARIES_LAYER in self.viewer.layers:
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
        # Image layers carry scale=(um, um) so their world coords are µm. The
        # boundary lines were generated in canvas-pixel coordinates — match the
        # image-layer scale so they align in world space.
        scale_um = (self.viewer_pixel_size_mm or 0.0) * 1000
        self.viewer.add_shapes(
            lines,
            shape_type="line",
            edge_color="white",
            edge_width=2,
            name=PLATE_BOUNDARIES_LAYER,
            scale=(scale_um, scale_um) if scale_um else (1, 1),
        )
        boundaries = self.viewer.layers[PLATE_BOUNDARIES_LAYER]
        boundaries.mouse_pan = False
        boundaries.mouse_zoom = False
        self.viewer.layers.move(len(self.viewer.layers) - 1, 0)
        for layer in reversed(self.viewer.layers):
            if layer.name != PLATE_BOUNDARIES_LAYER:
                self.viewer.layers.selection.active = layer
                break

    def clearAllLayers(self):
        """Clear all layers and reset state. Preserves the Manual ROI layer."""
        for layer in [lyr for lyr in self.viewer.layers if lyr.name != MANUAL_ROI_LAYER]:
            self.viewer.layers.remove(layer)
        self.viewer_extents = None
        self.top_left_coordinate = None
        self.layers_initialized = False
        self.mosaic_dtype = None
        self._pixel_size_um = 0.0
        self._downsample_factor = 1
        self.signal_clear_viewer.emit()

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
