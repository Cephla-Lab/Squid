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

from control._def import CHANNEL_COLORS_MAP, MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
from control.core.configuration import ContrastManager
from control.core.navigation import ObjectiveStore

from squid.services import CameraService
import squid.logging


class NapariMosaicDisplayWidget(QWidget):
    _log = squid.logging.get_logger(__name__)
    signal_coordinates_clicked = Signal(float, float)  # x, y in mm
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal()
    signal_shape_drawn = Signal(list)

    def __init__(
        self,
        objectiveStore: ObjectiveStore,
        camera_service: CameraService,
        contrastManager: ContrastManager,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.objectiveStore = objectiveStore
        self._camera_service = camera_service
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

    def customizeViewer(self) -> None:
        self.viewer.bind_key("D", self.toggle_draw_mode)

    def toggle_draw_mode(self, viewer: napari.Viewer) -> None:
        self.is_drawing_shape = not self.is_drawing_shape

        if "Manual ROI" not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name="Manual ROI",
                edge_width=40,
                edge_color="red",
                face_color="transparent",
            )
            self.shape_layer.events.data.connect(self.on_shape_change)
        else:
            self.shape_layer = self.viewer.layers["Manual ROI"]

        if self.is_drawing_shape:
            # if there are existing shapes, switch to vertex select mode
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                # if no shapes exist, switch to add polygon mode
                self.shape_layer.mode = "add_polygon"
        else:
            # if no shapes exist, switch to pan/zoom mode
            self.shape_layer.mode = "pan_zoom"

        self.on_shape_change()

    def enable_shape_drawing(self, enable: bool) -> None:
        if enable:
            self.toggle_draw_mode(self.viewer)
        else:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.mode = "pan_zoom"

    def on_shape_change(self, event: Optional[Any] = None) -> None:
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # convert shapes to mm coordinates
            self.shapes_mm = [
                self.convert_shape_to_mm(shape) for shape in self.shape_layer.data
            ]
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

    def convert_shape_to_mm(self, shape_data: np.ndarray) -> np.ndarray:
        shape_data_mm = []
        for point in shape_data:
            coords = self.viewer.layers[0].world_to_data(point)
            x_mm = self.top_left_coordinate[1] + coords[1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[0] * self.viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        return np.array(shape_data_mm)

    def convert_mm_to_viewer_shapes(
        self, shapes_mm: list[np.ndarray]
    ) -> list[list[np.ndarray]]:
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
                world_coords = self.viewer.layers[0].data_to_world([y_data, x_data])
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

    def updateMosaic(
        self, image: np.ndarray, x_mm: float, y_mm: float, k: int, channel_name: str
    ) -> None:
        # Store original center position for logging
        center_x_mm, center_y_mm = x_mm, y_mm
        original_shape = image.shape

        # calculate pixel size
        pixel_size_factor = self.objectiveStore.get_pixel_size_factor()
        binned_pixel_size_um = self._camera_service.get_pixel_size_binned_um()
        pixel_size_um = pixel_size_factor * binned_pixel_size_um
        downsample_factor = max(
            1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um)
        )
        image_pixel_size_um = pixel_size_um * downsample_factor
        image_pixel_size_mm = image_pixel_size_um / 1000
        image_dtype = image.dtype

        # Calculate tile size BEFORE downsampling (this should match acquisition FOV)
        # Note: image_pixel_size_mm already includes downsample_factor, so we need
        # to account for that when calculating tile size from original shape
        original_tile_width_mm = original_shape[1] * pixel_size_um / 1000  # Without downsample
        original_tile_height_mm = original_shape[0] * pixel_size_um / 1000

        self._log.info(
            f"updateMosaic: original_shape={original_shape}, center=({center_x_mm:.4f}, {center_y_mm:.4f})mm, "
            f"pixel_size_factor={pixel_size_factor:.4f}, binned_px={binned_pixel_size_um:.2f}um, "
            f"effective_px={pixel_size_um:.3f}um, downsample={downsample_factor}, "
            f"original_tile_size={original_tile_width_mm:.4f}x{original_tile_height_mm:.4f}mm"
        )

        # downsample image
        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (
                    image.shape[1] // downsample_factor,
                    image.shape[0] // downsample_factor,
                ),
                interpolation=cv2.INTER_AREA,
            )

        # Calculate tile size in mm for the downsampled image (used for extent calculations)
        tile_width_mm = image.shape[1] * image_pixel_size_mm
        tile_height_mm = image.shape[0] * image_pixel_size_mm

        # Adjust image position (from center to top-left) using ORIGINAL dimensions.
        # This is critical because:
        # 1. The acquisition step size is based on original FOV dimensions
        # 2. Downsampling uses integer division which truncates (e.g., 1500//7=214, not 214.29)
        # 3. Using downsampled dimensions would place tiles at slightly wrong positions
        # 4. The error accumulates across the mosaic, causing visible gaps/overlaps
        x_mm -= original_tile_width_mm / 2
        y_mm -= original_tile_height_mm / 2

        self._log.info(
            f"  -> after downsample: shape={image.shape}, downsampled_tile={tile_width_mm:.4f}x{tile_height_mm:.4f}mm, "
            f"position_adj_used={original_tile_width_mm:.4f}x{original_tile_height_mm:.4f}mm, "
            f"top_left=({x_mm:.4f}, {y_mm:.4f})mm"
        )

        if not self.viewer.layers:
            # initialize first layer
            self.layers_initialized = True
            self.signal_layers_initialized.emit()
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.viewer_extents = [
                y_mm,
                y_mm + image.shape[0] * image_pixel_size_mm,
                x_mm,
                x_mm + image.shape[1] * image_pixel_size_mm,
            ]
            self.top_left_coordinate = [y_mm, x_mm]
            self.mosaic_dtype = image_dtype
        else:
            # convert image dtype and scale if necessary
            image = self.convertImageDtype(image, self.mosaic_dtype)
            if image_pixel_size_mm != self.viewer_pixel_size_mm:
                scale_factor = image_pixel_size_mm / self.viewer_pixel_size_mm
                image = cv2.resize(
                    image,
                    (
                        int(image.shape[1] * scale_factor),
                        int(image.shape[0] * scale_factor),
                    ),
                    interpolation=cv2.INTER_LINEAR,
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
        self.updateLayer(layer, image, x_mm, y_mm, k, prev_top_left)

        # update contrast limits
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        scaled_min = self.convertValue(
            min_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype
        )
        scaled_max = self.convertValue(
            max_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype
        )
        layer.contrast_limits = (scaled_min, scaled_max)
        layer.refresh()

    def updateLayer(
        self,
        layer: napari.layers.Image,
        image: np.ndarray,
        x_mm: float,
        y_mm: float,
        k: int,
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
            y_offset = int(
                math.floor(
                    (prev_top_left[0] - self.top_left_coordinate[0])
                    / self.viewer_pixel_size_mm
                )
            )
            x_offset = int(
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

                    # ensure offsets don't exceed bounds
                    y_end = min(y_offset + mosaic.data.shape[0], new_data.shape[0])
                    x_end = min(x_offset + mosaic.data.shape[1], new_data.shape[1])

                    # shift existing data
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data[y_offset:y_end, x_offset:x_end, :] = mosaic.data[
                            : y_end - y_offset, : x_end - x_offset, :
                        ]
                    else:
                        new_data[y_offset:y_end, x_offset:x_end] = mosaic.data[
                            : y_end - y_offset, : x_end - x_offset
                        ]
                    mosaic.data = new_data

            if "Manual ROI" in self.viewer.layers:
                self.update_shape_layer_position(
                    prev_top_left, self.top_left_coordinate
                )

            self.resetView()

        # insert new image
        y_pos = int(
            math.floor((y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm)
        )
        x_pos = int(
            math.floor((x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm)
        )

        # ensure indices are within bounds
        y_end = min(y_pos + image.shape[0], layer.data.shape[0])
        x_end = min(x_pos + image.shape[1], layer.data.shape[1])

        # insert image data
        if is_rgb:
            layer.data[y_pos:y_end, x_pos:x_end, :] = image[
                : y_end - y_pos, : x_end - x_pos, :
            ]
        else:
            layer.data[y_pos:y_end, x_pos:x_end] = image[
                : y_end - y_pos, : x_end - x_pos
            ]
        layer.refresh()

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
        # Convert value from one dtype range to another
        from_info = np.iinfo(from_dtype)
        to_info = np.iinfo(to_dtype)

        # Normalize the value to [0, 1] range
        normalized = (value - from_info.min) / (from_info.max - from_info.min)

        # Scale to the target dtype range
        return normalized * (to_info.max - to_info.min) + to_info.min

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
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

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
