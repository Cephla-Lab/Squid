# Napari mosaic display widget
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import cv2
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS

from qtpy.QtCore import QObject, QThread, QTimer, Signal, Slot
from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton

from _def import CHANNEL_COLORS_MAP, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
from squid.backend.managers import ContrastManager
from squid.backend.controllers.multipoint.job_processing import CaptureInfo
from squid.core.events import (
    EventBus,
    ClickToMoveEnabledChanged,
    MoveStageToCommand,
    ManualShapeDrawingEnabledChanged,
    ManualShapesChanged,
    MosaicLayersInitialized,
    SetAcquisitionChannelsCommand,
)

import squid.core.logging


@dataclass
class TileUpdate:
    """Processed tile ready for GUI insertion."""
    channel: str
    mosaic: np.ndarray
    extents: Tuple[float, float, float, float]  # (min_y, max_y, min_x, max_x)
    top_left: Tuple[float, float]  # (y_mm, x_mm)
    pixel_size_mm: float
    contrast_min: Optional[float] = None
    contrast_max: Optional[float] = None


class MosaicWorker(QObject):
    """Worker that runs in QThread, composites tiles into mosaic arrays."""

    # Signal emitted when mosaic is updated (channel, TileUpdate)
    mosaic_updated = Signal(object)

    def __init__(self, target_pixel_size_um: float):
        super().__init__()
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._target_pixel_size_um = target_pixel_size_um
        self._mosaics: Dict[str, np.ndarray] = {}
        self._extents: Dict[str, List[float]] = {}  # channel -> [min_y, max_y, min_x, max_x]
        self._top_left: Dict[str, List[float]] = {}  # channel -> [y_mm, x_mm]
        self._mosaic_dtype: Optional[np.dtype] = None
        self._pixel_size_mm: float = 0.0
        self._lock = RLock()
        self._contrast_limits: Dict[str, Tuple[float, float]] = {}

    @Slot(object, object, str)
    def process_tile(self, image: np.ndarray, info: CaptureInfo, channel: str) -> None:
        """Process a tile on the worker thread."""
        try:
            if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
                return
            if image.shape[0] <= 0 or image.shape[1] <= 0:
                return

            pixel_size_um = info.physical_size_x_um or info.physical_size_y_um
            if pixel_size_um is None or pixel_size_um <= 0:
                return

            # Stage position is tile center
            center_x_mm = info.position.x_mm
            center_y_mm = info.position.y_mm
            self._log.info(f"process_tile: center=({center_x_mm:.4f}, {center_y_mm:.4f}) mm, channel={channel}")
            original_shape = image.shape

            # Compute initial contrast limits on first image per channel
            if channel not in self._contrast_limits:
                try:
                    sample = image
                    if sample.ndim == 3 and sample.shape[-1] == 3:
                        sample_vals = sample.reshape(-1, 3).astype(np.float32)
                        sample = (
                            0.2126 * sample_vals[:, 0]
                            + 0.7152 * sample_vals[:, 1]
                            + 0.0722 * sample_vals[:, 2]
                        )
                    else:
                        # Subsample for speed (every 4th pixel)
                        sample = sample[::4, ::4].astype(np.float32).ravel()
                    if sample.size > 0:
                        lo, hi = float(np.percentile(sample, 0.5)), float(np.percentile(sample, 99.5))
                        if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                            self._contrast_limits[channel] = (lo, hi)
                except Exception:
                    pass

            # Downsample
            downsample_factor = max(1, int(round(self._target_pixel_size_um / pixel_size_um)))
            image_pixel_size_um = pixel_size_um * downsample_factor
            image_pixel_size_mm = image_pixel_size_um / 1000

            # Calculate tile size before downsampling
            original_tile_width_mm = original_shape[1] * pixel_size_um / 1000
            original_tile_height_mm = original_shape[0] * pixel_size_um / 1000

            if downsample_factor != 1:
                image = cv2.resize(
                    image,
                    (
                        max(1, int(round(image.shape[1] / downsample_factor))),
                        max(1, int(round(image.shape[0] / downsample_factor))),
                    ),
                    interpolation=cv2.INTER_AREA,
                )

            with self._lock:
                # Initialize mosaic dtype from first tile
                if self._mosaic_dtype is None:
                    self._mosaic_dtype = image.dtype
                    self._pixel_size_mm = image_pixel_size_mm

                # Convert dtype if needed (use float32, not float64)
                if image.dtype != self._mosaic_dtype:
                    image = self._convert_dtype_fast(image, self._mosaic_dtype)

                # Handle scale mismatch
                if image_pixel_size_mm != self._pixel_size_mm:
                    scale_factor = image_pixel_size_mm / self._pixel_size_mm
                    if math.isfinite(scale_factor) and scale_factor > 0:
                        target_w = max(1, int(round(image.shape[1] * scale_factor)))
                        target_h = max(1, int(round(image.shape[0] * scale_factor)))
                        image = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

                # Convert center to top-left corner
                x_mm = center_x_mm - original_tile_width_mm / 2
                y_mm = center_y_mm - original_tile_height_mm / 2

                # Initialize or update channel mosaic
                if channel not in self._mosaics:
                    self._extents[channel] = [
                        y_mm,
                        y_mm + image.shape[0] * self._pixel_size_mm,
                        x_mm,
                        x_mm + image.shape[1] * self._pixel_size_mm,
                    ]
                    self._top_left[channel] = [y_mm, x_mm]
                    if image.ndim == 3 and image.shape[2] == 3:
                        self._mosaics[channel] = np.zeros_like(image)
                    else:
                        self._mosaics[channel] = np.zeros_like(image)
                else:
                    # Update extents
                    ext = self._extents[channel]
                    ext[0] = min(ext[0], y_mm)
                    ext[1] = max(ext[1], y_mm + image.shape[0] * self._pixel_size_mm)
                    ext[2] = min(ext[2], x_mm)
                    ext[3] = max(ext[3], x_mm + image.shape[1] * self._pixel_size_mm)

                # Resize mosaic if needed
                self._ensure_mosaic_size(channel, image)

                # Insert tile
                self._insert_tile(channel, image, x_mm, y_mm)

                # Prepare update - COPY the mosaic array for thread safety
                # The GUI thread will receive its own copy that won't be modified by the worker
                contrast = self._contrast_limits.get(channel)
                update = TileUpdate(
                    channel=channel,
                    mosaic=self._mosaics[channel].copy(),
                    extents=tuple(self._extents[channel]),
                    top_left=tuple(self._top_left[channel]),
                    pixel_size_mm=self._pixel_size_mm,
                    contrast_min=contrast[0] if contrast else None,
                    contrast_max=contrast[1] if contrast else None,
                )

            # Emit update (Qt delivers to GUI thread)
            self.mosaic_updated.emit(update)

        except Exception as e:
            self._log.error(f"MosaicWorker.process_tile error: {e}")
            import traceback
            traceback.print_exc()

    def _convert_dtype_fast(self, image: np.ndarray, target_dtype: np.dtype) -> np.ndarray:
        """Fast dtype conversion using float32 intermediate (not float64)."""
        if image.dtype == target_dtype:
            return image

        if np.issubdtype(image.dtype, np.integer):
            src_max = np.float32(np.iinfo(image.dtype).max)
        else:
            src_max = np.float32(1.0)

        if np.issubdtype(target_dtype, np.integer):
            dst_max = np.float32(np.iinfo(target_dtype).max)
        else:
            dst_max = np.float32(1.0)

        scale = dst_max / src_max
        return (image.astype(np.float32) * scale).astype(target_dtype)

    def _ensure_mosaic_size(self, channel: str, image: np.ndarray) -> None:
        """Expand mosaic if extents have grown."""
        ext = self._extents[channel]
        mosaic_height = int(math.ceil((ext[1] - ext[0]) / self._pixel_size_mm))
        mosaic_width = int(math.ceil((ext[3] - ext[2]) / self._pixel_size_mm))

        mosaic = self._mosaics[channel]
        if mosaic.shape[0] >= mosaic_height and mosaic.shape[1] >= mosaic_width:
            return  # No resize needed

        # Create new mosaic with padding for future growth
        pad_factor = 1.2
        new_height = int(mosaic_height * pad_factor)
        new_width = int(mosaic_width * pad_factor)

        is_rgb = mosaic.ndim == 3 and mosaic.shape[2] == 3
        if is_rgb:
            new_mosaic = np.zeros((new_height, new_width, 3), dtype=mosaic.dtype)
        else:
            new_mosaic = np.zeros((new_height, new_width), dtype=mosaic.dtype)

        # Copy existing data
        old_top_left = self._top_left[channel]
        new_top_left = [ext[0], ext[2]]

        y_offset = int(round((old_top_left[0] - new_top_left[0]) / self._pixel_size_mm))
        x_offset = int(round((old_top_left[1] - new_top_left[1]) / self._pixel_size_mm))

        y_offset = max(0, y_offset)
        x_offset = max(0, x_offset)

        y_end = min(y_offset + mosaic.shape[0], new_mosaic.shape[0])
        x_end = min(x_offset + mosaic.shape[1], new_mosaic.shape[1])

        if y_end > y_offset and x_end > x_offset:
            if is_rgb:
                new_mosaic[y_offset:y_end, x_offset:x_end, :] = mosaic[:y_end - y_offset, :x_end - x_offset, :]
            else:
                new_mosaic[y_offset:y_end, x_offset:x_end] = mosaic[:y_end - y_offset, :x_end - x_offset]

        self._mosaics[channel] = new_mosaic
        self._top_left[channel] = new_top_left

    def _insert_tile(self, channel: str, image: np.ndarray, x_mm: float, y_mm: float) -> None:
        """Insert tile into mosaic at correct position."""
        mosaic = self._mosaics[channel]
        top_left = self._top_left[channel]

        y_pos = int(round((y_mm - top_left[0]) / self._pixel_size_mm))
        x_pos = int(round((x_mm - top_left[1]) / self._pixel_size_mm))

        # Clip to bounds
        y_img0, x_img0 = 0, 0
        if y_pos < 0:
            y_img0 = -y_pos
            y_pos = 0
        if x_pos < 0:
            x_img0 = -x_pos
            x_pos = 0

        y_end = min(y_pos + (image.shape[0] - y_img0), mosaic.shape[0])
        x_end = min(x_pos + (image.shape[1] - x_img0), mosaic.shape[1])

        if y_end <= y_pos or x_end <= x_pos:
            return

        is_rgb = image.ndim == 3 and image.shape[2] == 3
        if is_rgb:
            mosaic[y_pos:y_end, x_pos:x_end, :] = image[
                y_img0:y_img0 + (y_end - y_pos),
                x_img0:x_img0 + (x_end - x_pos),
                :
            ]
        else:
            mosaic[y_pos:y_end, x_pos:x_end] = image[
                y_img0:y_img0 + (y_end - y_pos),
                x_img0:x_img0 + (x_end - x_pos)
            ]

    def clear(self) -> None:
        """Clear all mosaics (called when clearing the view)."""
        with self._lock:
            self._mosaics.clear()
            self._extents.clear()
            self._top_left.clear()
            self._mosaic_dtype = None
            self._pixel_size_mm = 0.0
            self._contrast_limits.clear()


class MosaicCompositor(QObject):
    """Manages the MosaicWorker thread lifecycle."""

    # Signal to send tiles to worker (image, info, channel)
    _tile_received = Signal(object, object, str)

    def __init__(self, target_pixel_size_um: float, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._thread = QThread()
        self._worker = MosaicWorker(target_pixel_size_um)
        self._worker.moveToThread(self._thread)

        # Connect signal to worker slot
        self._tile_received.connect(self._worker.process_tile)

        self._thread.start()
        self._log.info("MosaicCompositor started")

    def submit(self, image: np.ndarray, info: CaptureInfo, channel: str) -> None:
        """Submit tile from any thread - Qt handles delivery to worker thread."""
        # Copy image before emitting (releases camera buffer)
        self._tile_received.emit(image.copy(), info, channel)

    @property
    def mosaic_updated(self) -> Signal:
        """Expose worker's update signal for GUI connection."""
        return self._worker.mosaic_updated

    def clear(self) -> None:
        """Clear worker state."""
        self._worker.clear()

    def stop(self) -> None:
        """Stop the worker thread."""
        self._thread.quit()
        self._thread.wait()
        self._log.info("MosaicCompositor stopped")


class NapariMosaicDisplayWidget(QWidget):
    """Mosaic display widget using napari.

    Uses per-frame `CaptureInfo` metadata to place tiles; no controller/service access.
    """

    _log = squid.core.logging.get_logger(__name__)
    # Note: stage moves are published via MoveStageToCommand (no cross-widget Qt wiring).
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal()
    signal_shape_drawn = Signal(list)

    def __init__(
        self,
        contrastManager: ContrastManager,
        event_bus: Optional[EventBus] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)

        self._event_bus = event_bus
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        _layout = QVBoxLayout()
        _layout.addWidget(self.viewer.window._qt_window)
        self.layers_initialized = False
        self.shape_layer = None
        self.shapes_mm: list[Any] = []
        self.is_drawing_shape = False

        # add clear button
        self.clear_button = QPushButton("Clear Mosaic View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        _layout.addWidget(self.clear_button)

        self.setLayout(_layout)
        self.customizeViewer()
        self.viewer_pixel_size_mm = 1
        self.dz_um: Optional[float] = None
        self.Nz: Optional[int] = None
        self.channels: set[str] = set()
        self.viewer_extents: list[float] = []  # [min_y, max_y, min_x, max_x]
        self.top_left_coordinate = None  # [y, x] in mm
        self.mosaic_dtype = None
        self._click_to_move_enabled = True

        # Background compositor for tile processing (moves heavy work off GUI thread)
        self._compositor = MosaicCompositor(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM, parent=self)
        self._compositor.mosaic_updated.connect(self._on_mosaic_updated)

        # Throttle napari updates to max 10 FPS
        self._pending_updates: Dict[str, TileUpdate] = {}
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._flush_pending_updates)
        self._refresh_timer.start(100)  # 100ms = 10 FPS max

        if self._event_bus is not None:
            self._event_bus.subscribe(
                ManualShapeDrawingEnabledChanged,
                lambda e: self.enable_shape_drawing(e.enabled),
            )
            self._event_bus.subscribe(
                SetAcquisitionChannelsCommand,
                lambda e: self.initChannels(list(e.channel_names)),
            )
            self._event_bus.subscribe(
                ClickToMoveEnabledChanged,
                lambda e: setattr(self, "_click_to_move_enabled", bool(e.enabled)),
            )

    def customizeViewer(self) -> None:
        self.viewer.bind_key("D", self.toggle_draw_mode)

    def _has_mosaic_image_layers(self) -> bool:
        """Return True if any non-ROI image layers exist.

        The mosaic may have a pre-created "Manual ROI" shapes layer; that should
        not count as mosaic initialization.
        """
        try:
            return any(layer.name != "Manual ROI" for layer in self.viewer.layers)
        except Exception:  # pragma: no cover - defensive
            return False

    def _get_reference_layer(self) -> Optional[napari.layers.Layer]:
        """Layer used for coordinate transforms (ignore 'Manual ROI')."""
        for layer in self.viewer.layers:
            if layer.name != "Manual ROI":
                return layer
        return None

    def _set_draw_mode(self, enable: bool) -> None:
        self.is_drawing_shape = bool(enable)

        if "Manual ROI" not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name="Manual ROI",
                edge_width=40,
                edge_color="red",
                face_color="transparent",
                visible=self.is_drawing_shape,
            )
            self.shape_layer.events.data.connect(self.on_shape_change)
        else:
            self.shape_layer = self.viewer.layers["Manual ROI"]
            self.shape_layer.visible = self.is_drawing_shape

        if self.is_drawing_shape:
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                self.shape_layer.mode = "add_polygon"
        else:
            # Keep the layer hidden when not actively drawing.
            self.shape_layer.mode = "pan_zoom"
        self.on_shape_change()

    def toggle_draw_mode(self, viewer: napari.Viewer) -> None:
        self._set_draw_mode(not self.is_drawing_shape)

    def enable_shape_drawing(self, enable: bool) -> None:
        # When disabled, don't create the layer just to hide it; keep it invisible if it exists.
        if not enable:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.visible = False
                self.shape_layer.mode = "pan_zoom"
            elif "Manual ROI" in self.viewer.layers:
                try:
                    layer = self.viewer.layers["Manual ROI"]
                    layer.visible = False
                    layer.mode = "pan_zoom"
                    self.shape_layer = layer
                except Exception:  # pragma: no cover - defensive
                    pass
            return

        if enable == self.is_drawing_shape and self.shape_layer is not None:
            return
        self._set_draw_mode(enable)

    def on_shape_change(self, event: Optional[Any] = None) -> None:
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # convert shapes to mm coordinates
            self.shapes_mm = [
                self.convert_shape_to_mm(shape) for shape in self.shape_layer.data
            ]
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

        if self._event_bus is not None:
            shapes_mm_tuple: Optional[
                tuple[tuple[tuple[float, float], ...], ...]
            ] = None
            if self.shapes_mm:
                shapes_mm_tuple = tuple(
                    tuple(tuple((float(x), float(y)) for x, y in shape))
                    for shape in self.shapes_mm
                )
            self._event_bus.publish(ManualShapesChanged(shapes_mm=shapes_mm_tuple))

    def convert_shape_to_mm(self, shape_data: np.ndarray) -> np.ndarray:
        shape_data_mm = []
        ref = self._get_reference_layer()
        if ref is None or self.top_left_coordinate is None:
            return np.array(shape_data_mm)
        for point in shape_data:
            coords = ref.world_to_data(point)
            x_mm = self.top_left_coordinate[1] + coords[1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[0] * self.viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        return np.array(shape_data_mm)

    def convert_mm_to_viewer_shapes(
        self, shapes_mm: list[np.ndarray]
    ) -> list[list[np.ndarray]]:
        ref = self._get_reference_layer()
        if ref is None or self.top_left_coordinate is None:
            return []
        viewer_shapes = []
        for shape_mm in shapes_mm:
            viewer_shape = []
            for point_mm in shape_mm:
                x_data = (
                    point_mm[0] - self.top_left_coordinate[1]
                ) / self.viewer_pixel_size_mm
                y_data = (
                    point_mm[1] - self.top_left_coordinate[0]
                ) / self.viewer_pixel_size_mm
                world_coords = ref.data_to_world([y_data, x_data])
                viewer_shape.append(world_coords)
            viewer_shapes.append(viewer_shape)
        return viewer_shapes

    def update_shape_layer_position(
        self, prev_top_left: list[float], new_top_left: list[float]
    ) -> None:
        if self.shape_layer is None or len(self.shapes_mm) == 0:
            return
        try:
            # update top_left_coordinate
            self.top_left_coordinate = new_top_left

            # convert mm coordinates to viewer coordinates
            new_shapes = self.convert_mm_to_viewer_shapes(self.shapes_mm)

            # update shape layer data
            self.shape_layer.data = new_shapes
        except Exception as e:
            print(f"Error updating shape layer position: {e}")
            import traceback

            traceback.print_exc()

    def initChannels(self, channels: list[str]) -> None:
        self.channels = set(channels)

    def initLayersShape(self, Nz: int, dz: float) -> None:
        self.Nz = 1
        self.dz_um = dz

    @Slot(object)
    def _on_mosaic_updated(self, update: TileUpdate) -> None:
        """Called when compositor has a new tile ready. Queue for batch refresh."""
        self._pending_updates[update.channel] = update

    def _flush_pending_updates(self) -> None:
        """Apply pending updates to napari layers (runs on GUI thread via QTimer)."""
        if not self._pending_updates:
            return

        channels_updated = set()
        for channel, update in self._pending_updates.items():
            try:
                self._apply_tile_update(update)
                channels_updated.add(channel)
            except Exception as e:
                self._log.error(f"Error applying tile update for {channel}: {e}")

        self._pending_updates.clear()

        # Single batch refresh for all updated layers
        if channels_updated:
            for layer in self.viewer.layers:
                if layer.name in channels_updated:
                    layer.refresh()

    def _apply_tile_update(self, update: TileUpdate) -> None:
        """Apply a single tile update to the napari viewer."""
        channel = update.channel

        # Update widget state from the update
        self.viewer_pixel_size_mm = update.pixel_size_mm
        self.viewer_extents = list(update.extents)
        self.top_left_coordinate = list(update.top_left)

        if not self.layers_initialized and not self._has_mosaic_image_layers():
            self.layers_initialized = True
            self.mosaic_dtype = update.mosaic.dtype
            self.signal_layers_initialized.emit()
            if self._event_bus is not None:
                self._event_bus.publish(MosaicLayersInitialized())

        # Convert top_left from mm to um for napari translate (matching scale)
        translate_um = (
            update.top_left[0] * 1000,  # y in um
            update.top_left[1] * 1000,  # x in um
        )

        # Create layer if it doesn't exist
        if channel not in self.viewer.layers:
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            is_rgb = update.mosaic.ndim == 3 and update.mosaic.shape[2] == 3
            layer = self.viewer.add_image(
                update.mosaic,
                name=channel,
                rgb=is_rgb,
                colormap=color,
                visible=True,
                blending="additive",
                scale=(
                    self.viewer_pixel_size_mm * 1000,
                    self.viewer_pixel_size_mm * 1000,
                ),
                translate=translate_um,
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)
        else:
            # Update existing layer data and position
            layer = self.viewer.layers[channel]
            layer.data = update.mosaic
            layer.translate = translate_um

        # Update contrast limits
        if update.contrast_min is not None and update.contrast_max is not None:
            # Update ContrastManager
            self.contrastManager.update_limits(channel, update.contrast_min, update.contrast_max)
            layer.contrast_limits = (update.contrast_min, update.contrast_max)

    def extractWavelength(self, name: str) -> str:
        # extract wavelength from channel name
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info: dict) -> Colormap:
        # generate colormap from hex value
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updateMosaic(self, image: np.ndarray, info: CaptureInfo, channel_name: str) -> None:
        """Submit tile to background compositor for processing.

        Heavy work (downsample, dtype conversion, compositing) happens on a
        background QThread. The GUI thread is only updated via QTimer at 10 FPS.
        """
        if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
            self._log.error("updateMosaic: invalid image (None or missing shape)")
            return
        if image.shape[0] <= 0 or image.shape[1] <= 0:
            self._log.error(f"updateMosaic: empty image shape={getattr(image, 'shape', None)}")
            return

        # Submit to background compositor (image is copied in submit())
        self._compositor.submit(image, info, channel_name)

    def updateLayer(
        self,
        layer: napari.layers.Image,
        image: np.ndarray,
        x_mm: float,
        y_mm: float,
        prev_top_left: list[float],
    ) -> None:
        # calculate new mosaic size and position
        mosaic_height = int(
            math.ceil(
                (self.viewer_extents[1] - self.viewer_extents[0])
                / self.viewer_pixel_size_mm
            )
        )
        mosaic_width = int(
            math.ceil(
                (self.viewer_extents[3] - self.viewer_extents[2])
                / self.viewer_pixel_size_mm
            )
        )

        is_rgb = len(image.shape) == 3 and image.shape[2] == 3
        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            # calculate offsets for existing data
            y_offset_raw = int(
                math.floor(
                    (prev_top_left[0] - self.top_left_coordinate[0])
                    / self.viewer_pixel_size_mm
                )
            )
            x_offset_raw = int(
                math.floor(
                    (prev_top_left[1] - self.top_left_coordinate[1])
                    / self.viewer_pixel_size_mm
                )
            )

            for mosaic in self.viewer.layers:
                if mosaic.name != "Manual ROI":
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data = np.zeros(
                            (mosaic_height, mosaic_width, 3), dtype=mosaic.data.dtype
                        )
                    else:
                        new_data = np.zeros(
                            (mosaic_height, mosaic_width), dtype=mosaic.data.dtype
                        )

                    # Robust clipping: offsets can go negative due to float rounding.
                    y_src0 = 0
                    x_src0 = 0
                    y_offset = y_offset_raw
                    x_offset = x_offset_raw
                    if y_offset < 0:
                        y_src0 = -y_offset
                        y_offset = 0
                    if x_offset < 0:
                        x_src0 = -x_offset
                        x_offset = 0

                    y_end = min(y_offset + (mosaic.data.shape[0] - y_src0), new_data.shape[0])
                    x_end = min(x_offset + (mosaic.data.shape[1] - x_src0), new_data.shape[1])
                    if y_end <= y_offset or x_end <= x_offset:
                        mosaic.data = new_data
                        continue

                    # shift existing data
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data[y_offset:y_end, x_offset:x_end, :] = mosaic.data[
                            y_src0 : y_src0 + (y_end - y_offset),
                            x_src0 : x_src0 + (x_end - x_offset),
                            :,
                        ]
                    else:
                        new_data[y_offset:y_end, x_offset:x_end] = mosaic.data[
                            y_src0 : y_src0 + (y_end - y_offset),
                            x_src0 : x_src0 + (x_end - x_offset),
                        ]
                    mosaic.data = new_data

            if "Manual ROI" in self.viewer.layers:
                self.update_shape_layer_position(
                    prev_top_left, self.top_left_coordinate
                )

            self.resetView()

        # insert new image
        y_pos_raw = int(
            math.floor((y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm)
        )
        x_pos_raw = int(
            math.floor((x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm)
        )

        # Robust clipping: guard against tiny float rounding pushing us negative.
        y_img0 = 0
        x_img0 = 0
        y_pos = y_pos_raw
        x_pos = x_pos_raw
        if y_pos < 0:
            y_img0 = -y_pos
            y_pos = 0
        if x_pos < 0:
            x_img0 = -x_pos
            x_pos = 0
        self._log.info(
            f"  -> inserting tile at pixel pos=({x_pos}, {y_pos}), image.shape={image.shape}, "
            f"viewer_pixel_size_mm={self.viewer_pixel_size_mm:.6f}, "
            f"top_left_coord={self.top_left_coordinate}, layer.data.shape={layer.data.shape}"
        )

        # ensure indices are within bounds
        y_end = min(y_pos + (image.shape[0] - y_img0), layer.data.shape[0])
        x_end = min(x_pos + (image.shape[1] - x_img0), layer.data.shape[1])
        if y_end <= y_pos or x_end <= x_pos:
            return

        # insert image data
        if is_rgb:
            layer.data[y_pos:y_end, x_pos:x_end, :] = image[
                y_img0 : y_img0 + (y_end - y_pos),
                x_img0 : x_img0 + (x_end - x_pos),
                :,
            ]
        else:
            layer.data[y_pos:y_end, x_pos:x_end] = image[
                y_img0 : y_img0 + (y_end - y_pos),
                x_img0 : x_img0 + (x_end - x_pos),
            ]
        # Note: Don't refresh here - caller (updateMosaic) will refresh after setting contrast

    def convertImageDtype(
        self, image: np.ndarray, target_dtype: np.dtype
    ) -> np.ndarray:
        # convert image to target dtype
        if image.dtype == target_dtype:
            return image

        # get full range of values for both dtypes
        if np.issubdtype(image.dtype, np.integer):
            input_info = np.iinfo(image.dtype)
            input_min, input_max = input_info.min, input_info.max
        else:
            input_min, input_max = np.min(image), np.max(image)

        if np.issubdtype(target_dtype, np.integer):
            output_info = np.iinfo(target_dtype)
            output_min, output_max = output_info.min, output_info.max
        else:
            output_min, output_max = 0.0, 1.0

        # normalize and scale image
        image_normalized = (image.astype(np.float64) - input_min) / (
            input_max - input_min
        )
        image_scaled = image_normalized * (output_max - output_min) + output_min

        return image_scaled.astype(target_dtype)

    def convertValue(
        self, value: float, from_dtype: np.dtype, to_dtype: np.dtype
    ) -> float:
        # Convert a scalar from one dtype range to another.
        #
        # Notes:
        # - For integer dtypes, we map full integer range to full integer range.
        # - For float dtypes, we treat the "display" range as [0.0, 1.0] to avoid
        #   using finfo() huge ranges, which is not meaningful for contrast limits.
        if from_dtype is None or to_dtype is None:
            return float(value)

        from_dtype = np.dtype(from_dtype)
        to_dtype = np.dtype(to_dtype)

        if np.issubdtype(from_dtype, np.integer):
            from_info = np.iinfo(from_dtype)
            from_min, from_max = float(from_info.min), float(from_info.max)
        else:
            from_min, from_max = 0.0, 1.0

        if np.issubdtype(to_dtype, np.integer):
            to_info = np.iinfo(to_dtype)
            to_min, to_max = float(to_info.min), float(to_info.max)
        else:
            to_min, to_max = 0.0, 1.0

        denom = (from_max - from_min)
        if denom == 0:
            return float(to_min)

        normalized = (float(value) - from_min) / denom
        normalized = float(np.clip(normalized, 0.0, 1.0))
        return normalized * (to_max - to_min) + to_min

    def signalContrastLimits(self, event: Any) -> None:
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)

        # Convert the new limits from mosaic_dtype to acquisition_dtype
        acquisition_min = self.convertValue(
            min_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype
        )
        acquisition_max = self.convertValue(
            max_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype
        )

        # Update the ContrastManager with the new limits
        self.contrastManager.update_limits(layer.name, acquisition_min, acquisition_max)

    def getContrastLimits(self, dtype: np.dtype) -> tuple:
        return self.contrastManager.get_default_limits()

    def onDoubleClick(self, layer: napari.layers.Image, event: Any) -> None:
        coords = layer.world_to_data(event.position)
        if coords is not None:
            x_mm = self.top_left_coordinate[1] + coords[-1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[-2] * self.viewer_pixel_size_mm
            print(f"move from click: ({x_mm:.6f}, {y_mm:.6f})")
            if self._event_bus is not None and self._click_to_move_enabled:
                self._event_bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))

    def resetView(self) -> None:
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clear_shape(self) -> None:
        if self.shape_layer is not None:
            self.viewer.layers.remove(self.shape_layer)
            self.shape_layer = None
            self.is_drawing_shape = False
            self.signal_shape_drawn.emit([])

    def clearAllLayers(self) -> None:
        # Clear pending updates and compositor state
        self._pending_updates.clear()
        self._compositor.clear()

        # Reset widget state
        self.layers_initialized = False
        self.viewer_extents = []
        self.top_left_coordinate = None
        self.mosaic_dtype = None
        self.channels = set()

        # Keep the Manual ROI layer and clear the content of all other layers
        for layer in self.viewer.layers:
            if layer.name == "Manual ROI":
                continue

            if hasattr(layer, "data") and hasattr(layer.data, "shape"):
                # Create an empty array matching the layer's dimensions
                if len(layer.data.shape) == 3 and layer.data.shape[2] == 3:  # RGB
                    empty_data = np.zeros(
                        (layer.data.shape[0], layer.data.shape[1], 3),
                        dtype=layer.data.dtype,
                    )
                else:  # Grayscale
                    empty_data = np.zeros(
                        (layer.data.shape[0], layer.data.shape[1]),
                        dtype=layer.data.dtype,
                    )

                layer.data = empty_data

        for layer in self.viewer.layers:
            layer.refresh()

        self.signal_clear_viewer.emit()


    def activate(self) -> None:
        self.viewer.window.activate()
