# Napari multi-channel Z-stack viewer widget
from __future__ import annotations

import numpy as np
from numpy.typing import DTypeLike
from typing import Optional, Set, Tuple, TYPE_CHECKING

import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
from napari.utils.events import Event

from qtpy.QtWidgets import QWidget, QVBoxLayout

from _def import CHANNEL_COLORS_MAP, USE_NAPARI_FOR_LIVE_VIEW

from squid.ops.configuration import ContrastManager
from squid.core.events import EventBus, BinningChanged, ObjectiveChanged

if TYPE_CHECKING:
    pass


class NapariMultiChannelWidget(QWidget):
    """Multi-channel Z-stack viewer using napari.

    Subscribes to BinningChanged and ObjectiveChanged events to update pixel size.
    No direct service or controller access - pure event-driven architecture.
    """

    contrastManager: ContrastManager
    image_width: int
    image_height: int
    dtype: np.dtype
    channels: Set[str]
    pixel_size_um: float
    dz_um: float
    Nz: int
    layers_initialized: bool
    acquisition_initialized: bool
    viewer_scale_initialized: bool
    update_layer_count: int
    grid_enabled: bool
    viewer: napari.Viewer
    viewerWidget: QWidget
    _layout: QVBoxLayout

    def __init__(
        self,
        event_bus: EventBus,
        contrastManager: ContrastManager,
        initial_pixel_size_factor: float = 1.0,
        initial_pixel_size_binned_um: float = 1.0,
        grid_enabled: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._event_bus = event_bus

        # Cached values from events (initialized with provided initial values)
        self._pixel_size_factor = initial_pixel_size_factor
        self._pixel_size_binned_um = initial_pixel_size_binned_um

        # Initialize placeholders for the acquisition parameters
        self.contrastManager = contrastManager
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.dtype(np.uint8)
        self.channels = set()
        self.pixel_size_um = self._pixel_size_factor * self._pixel_size_binned_um
        self.dz_um = 1.0
        self.Nz = 1
        self.layers_initialized = False
        self.acquisition_initialized = False
        self.viewer_scale_initialized = False
        self.update_layer_count = 0
        self.grid_enabled = grid_enabled

        # Subscribe to events for dynamic values
        self._event_bus.subscribe(BinningChanged, self._on_binning_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

        # Initialize a napari Viewer without showing its standalone window.
        self.initNapariViewer()

    def _on_binning_changed(self, event: BinningChanged) -> None:
        """Update cached pixel size when binning changes."""
        if event.pixel_size_binned_um is not None:
            self._pixel_size_binned_um = event.pixel_size_binned_um

    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        """Update cached pixel size factor when objective changes."""
        if event.pixel_size_um is not None:
            self._pixel_size_factor = event.pixel_size_um

    def initNapariViewer(self) -> None:
        self.viewer = napari.Viewer(show=False)
        if self.grid_enabled:
            self.viewer.grid.enabled = True
        self.viewer.dims.axis_labels = ["Z-axis", "Y-axis", "X-axis"]
        self.viewerWidget = self.viewer.window._qt_window
        self._layout = QVBoxLayout()
        self._layout.addWidget(self.viewerWidget)
        self.setLayout(self._layout)
        self.customizeViewer()

    def customizeViewer(self) -> None:
        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def initLayersShape(self, Nz: int, dz: float) -> None:
        # Use cached values from events
        pixel_size_um = self._pixel_size_factor * self._pixel_size_binned_um
        if self.Nz != Nz or self.dz_um != dz or self.pixel_size_um != pixel_size_um:
            self.acquisition_initialized = False
            self.Nz = Nz
            self.dz_um = dz if Nz > 1 and dz != 0 else 1.0
            self.pixel_size_um = pixel_size_um

    def initChannels(self, channels: Set[str]) -> None:
        self.channels = set(channels)

    def extractWavelength(self, name: str) -> Optional[str]:
        # Split the string and find the wavelength number immediately after "Fluorescence"
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]  # Assuming '488 nm Ex' and taking '488'
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info: dict) -> Colormap:
        """Convert a HEX value to a normalized RGB tuple."""
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,  # Normalize the Red component
            ((channel_info["hex"] >> 8) & 0xFF) / 255,  # Normalize the Green component
            (channel_info["hex"] & 0xFF) / 255,
        )  # Normalize the Blue component
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def initLayers(
        self, image_height: int, image_width: int, image_dtype: DTypeLike
    ) -> None:
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        if self.acquisition_initialized:
            for layer in list(self.viewer.layers):
                if layer.name not in self.channels:
                    self.viewer.layers.remove(layer)
        else:
            self.viewer.layers.clear()
            self.acquisition_initialized = True
            if self.dtype != np.dtype(image_dtype) and not USE_NAPARI_FOR_LIVE_VIEW:
                self.contrastManager.scale_contrast_limits(image_dtype)

        self.image_width = image_width
        self.image_height = image_height
        self.dtype = np.dtype(image_dtype)
        self.layers_initialized = True
        self.update_layer_count = 0

    def updateLayers(
        self, image: np.ndarray, x: int, y: int, k: int, channel_name: str
    ) -> None:
        """Updates the appropriate slice of the canvas with the new image data."""
        rgb = len(image.shape) == 3

        # Check if the layer exists and has a different dtype
        if self.dtype != np.dtype(image.dtype):
            # Remove the existing layer
            self.layers_initialized = False
            self.acquisition_initialized = False

        if not self.layers_initialized:
            self.initLayers(image.shape[0], image.shape[1], image.dtype)

        if channel_name not in self.viewer.layers:
            self.channels.add(channel_name)
            if rgb:
                color = None  # RGB images do not need a colormap
                canvas = np.zeros(
                    (self.Nz, self.image_height, self.image_width, 3), dtype=self.dtype
                )
            else:
                wavelength = self.extractWavelength(channel_name)
                channel_info = CHANNEL_COLORS_MAP.get(
                    wavelength if wavelength is not None else "",
                    {"hex": 0xFFFFFF, "name": "gray"},
                )
                if channel_info["name"] in AVAILABLE_COLORMAPS:
                    color = AVAILABLE_COLORMAPS[channel_info["name"]]
                else:
                    color = self.generateColormap(channel_info)
                canvas = np.zeros(
                    (self.Nz, self.image_height, self.image_width), dtype=self.dtype
                )

            limits = self.getContrastLimits(self.dtype)
            layer = self.viewer.add_image(
                canvas,
                name=channel_name,
                visible=True,
                rgb=rgb,
                colormap=color,
                contrast_limits=limits,
                blending="additive",
                scale=(self.dz_um, self.pixel_size_um, self.pixel_size_um),
            )

            layer.contrast_limits = self.contrastManager.get_limits(channel_name)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

            if not self.viewer_scale_initialized:
                self.resetView()
                self.viewer_scale_initialized = True
            else:
                layer.refresh()

        layer = self.viewer.layers[channel_name]
        layer.data[k] = image
        layer.contrast_limits = self.contrastManager.get_limits(channel_name)
        self.update_layer_count += 1
        if self.update_layer_count % len(self.channels) == 0:
            if self.Nz > 1:
                self.viewer.dims.set_point(0, k * self.dz_um)
            for layer in self.viewer.layers:
                layer.refresh()

    def signalContrastLimits(self, event: Event) -> None:
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def getContrastLimits(self, dtype: np.dtype) -> Tuple[float, float]:
        return self.contrastManager.get_default_limits()

    def resetView(self) -> None:
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def activate(self) -> None:
        self.viewer.window.activate()
