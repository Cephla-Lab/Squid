# Napari mosaic display widget
from __future__ import annotations
import math
import numpy as np
from typing import Any, Optional

import cv2
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton

from _def import CHANNEL_COLORS_MAP, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
from squid.ops.configuration import ContrastManager
from squid.ops.acquisition.job_processing import CaptureInfo
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
        import time
        t_start = time.perf_counter()

        if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
            self._log.error("updateMosaic: invalid image (None or missing shape)")
            return
        if image.shape[0] <= 0 or image.shape[1] <= 0:
            self._log.error(f"updateMosaic: empty image shape={getattr(image, 'shape', None)}")
            return

        # Stage position is interpreted as the tile center in mm.
        center_x_mm, center_y_mm = info.position.x_mm, info.position.y_mm
        original_shape = image.shape

        # Use authoritative per-capture physical pixel size.
        pixel_size_um = info.physical_size_x_um or info.physical_size_y_um
        if pixel_size_um is None or pixel_size_um <= 0:
            self._log.error(
                "updateMosaic: CaptureInfo missing/invalid physical pixel size; "
                f"physical_size_x_um={info.physical_size_x_um}, physical_size_y_um={info.physical_size_y_um}"
            )
            return

        # If no per-channel contrast has been set yet, seed it from the actual image.
        # Using full dtype range for 16-bit fluorescence often renders as "all black".
        if channel_name not in self.contrastManager.contrast_limits:
            try:
                sample = image
                if sample is not None and hasattr(sample, "shape"):
                    if sample.ndim == 3 and sample.shape[-1] == 3:
                        # RGB: use luminance-like aggregation for robust limits.
                        sample_vals = sample.reshape(-1, 3).astype(np.float64)
                        sample = (
                            0.2126 * sample_vals[:, 0]
                            + 0.7152 * sample_vals[:, 1]
                            + 0.0722 * sample_vals[:, 2]
                        )
                    else:
                        sample = sample.astype(np.float64).ravel()

                    if sample.size > 0:
                        lo, hi = np.percentile(sample, (0.5, 99.5))
                        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
                            lo = float(np.min(sample))
                            hi = float(np.max(sample))
                        if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                            self.contrastManager.update_limits(channel_name, float(lo), float(hi))
            except Exception:  # pragma: no cover - best effort
                pass

        downsample_factor = max(
            1, int(round(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        )
        image_pixel_size_um = pixel_size_um * downsample_factor
        image_pixel_size_mm = image_pixel_size_um / 1000
        image_dtype = image.dtype

        # Calculate tile size BEFORE downsampling (this should match acquisition FOV).
        original_tile_width_mm = original_shape[1] * pixel_size_um / 1000
        original_tile_height_mm = original_shape[0] * pixel_size_um / 1000

        # downsample image
        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (
                    max(1, int(round(image.shape[1] / downsample_factor))),
                    max(1, int(round(image.shape[0] / downsample_factor))),
                ),
                interpolation=cv2.INTER_AREA,
            )

        if not self._has_mosaic_image_layers():
            # initialize first layer
            self.layers_initialized = True
            self.signal_layers_initialized.emit()
            if self._event_bus is not None:
                self._event_bus.publish(MosaicLayersInitialized())
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.mosaic_dtype = image_dtype
        else:
            # convert image dtype and scale if necessary
            image = self.convertImageDtype(image, self.mosaic_dtype)
            if image_pixel_size_mm != self.viewer_pixel_size_mm:
                scale_factor = image_pixel_size_mm / self.viewer_pixel_size_mm
                if not math.isfinite(scale_factor) or scale_factor <= 0:
                    self._log.error(
                        "updateMosaic: invalid scale factor for resize; "
                        f"image_pixel_size_mm={image_pixel_size_mm}, viewer_pixel_size_mm={self.viewer_pixel_size_mm}, "
                        f"scale_factor={scale_factor}"
                    )
                    return
                target_w = max(1, int(round(image.shape[1] * scale_factor)))
                target_h = max(1, int(round(image.shape[0] * scale_factor)))
                image = cv2.resize(
                    image,
                    (
                        target_w,
                        target_h,
                    ),
                    interpolation=cv2.INTER_LINEAR,
                )

        # Convert center position to top-left corner for array placement.
        # Use original tile dimensions (pre-downsample) to avoid systematic drift from
        # integer resize rounding across a large mosaic.
        x_mm = center_x_mm - original_tile_width_mm / 2
        y_mm = center_y_mm - original_tile_height_mm / 2

        if self.top_left_coordinate is None:
            self.viewer_extents = [
                y_mm,
                y_mm + image.shape[0] * self.viewer_pixel_size_mm,
                x_mm,
                x_mm + image.shape[1] * self.viewer_pixel_size_mm,
            ]
            self.top_left_coordinate = [y_mm, x_mm]

        self._log.debug(
            "updateMosaic: "
            f"center=({center_x_mm:.4f}, {center_y_mm:.4f})mm, "
            f"pixel_size={pixel_size_um:.3f}um, downsample={downsample_factor}, "
            f"orig_shape={original_shape}, display_shape={image.shape}, "
            f"top_left=({x_mm:.4f}, {y_mm:.4f})mm"
        )

        if channel_name not in self.viewer.layers:
            # create new layer for channel
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            layer = self.viewer.add_image(
                np.zeros_like(image),
                name=channel_name,
                rgb=len(image.shape) == 3,
                colormap=color,
                visible=True,
                blending="additive",
                scale=(
                    self.viewer_pixel_size_mm * 1000,
                    self.viewer_pixel_size_mm * 1000,
                ),
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

        # get layer for channel
        layer = self.viewer.layers[channel_name]

        # update extents
        self.viewer_extents[0] = min(self.viewer_extents[0], y_mm)
        self.viewer_extents[1] = max(
            self.viewer_extents[1], y_mm + image.shape[0] * self.viewer_pixel_size_mm
        )
        self.viewer_extents[2] = min(self.viewer_extents[2], x_mm)
        self.viewer_extents[3] = max(
            self.viewer_extents[3], x_mm + image.shape[1] * self.viewer_pixel_size_mm
        )

        # store previous top-left coordinate
        prev_top_left = (
            self.top_left_coordinate.copy() if self.top_left_coordinate else None
        )
        self.top_left_coordinate = [self.viewer_extents[0], self.viewer_extents[2]]

        # update layer
        t_layer_start = time.perf_counter()
        self.updateLayer(layer, image, x_mm, y_mm, prev_top_left)
        t_layer_end = time.perf_counter()

        # update contrast limits
        min_val, max_val = self.contrastManager.get_limits(channel_name, dtype=image_dtype)
        from_dtype = self.contrastManager.acquisition_dtype
        if from_dtype is None:
            layer.contrast_limits = (min_val, max_val)
        else:
            scaled_min = self.convertValue(min_val, from_dtype, self.mosaic_dtype)
            scaled_max = self.convertValue(max_val, from_dtype, self.mosaic_dtype)
            layer.contrast_limits = (scaled_min, scaled_max)
        t_refresh_start = time.perf_counter()
        layer.refresh()
        t_refresh_end = time.perf_counter()

        t_total = t_refresh_end - t_start
        if t_total > 0.1:  # Log if > 100ms
            self._log.warning(
                f"updateMosaic SLOW: total={t_total*1000:.1f}ms, "
                f"updateLayer={(t_layer_end - t_layer_start)*1000:.1f}ms, "
                f"refresh={(t_refresh_end - t_refresh_start)*1000:.1f}ms, "
                f"layer.data.shape={layer.data.shape}"
            )
        else:
            self._log.debug(
                f"updateMosaic: total={t_total*1000:.1f}ms"
            )

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

        self.channels = set()

        for layer in self.viewer.layers:
            layer.refresh()

        self.signal_clear_viewer.emit()


    def activate(self) -> None:
        self.viewer.window.activate()
