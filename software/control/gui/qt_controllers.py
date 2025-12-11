# Qt-based signal bridges for controller-to-widget communication
#
# This module provides Qt signal bridges that allow non-Qt controllers
# (AutoFocusController, MultiPointController) to communicate with Qt widgets.
# The actual controller logic is in the plain Python controllers; these bridges
# just marshal the callbacks/events to Qt signals for thread-safe widget updates.
#
# Note: MovementUpdater, QtAutoFocusController, and QtMultiPointController have been
# removed as part of Phase 8 cleanup. Use the plain controllers with signal bridges.
from typing import Optional, TYPE_CHECKING

import numpy as np
from qtpy.QtCore import QObject, Signal

import control._def
from control.core.acquisition import CaptureInfo
from control.core.acquisition.multi_point_utils import (
    MultiPointControllerFunctions,
    AcquisitionParameters,
    OverallProgressUpdate,
    RegionProgressUpdate,
)
from control.core.navigation import ObjectiveStore
from control.utils_config import ChannelMode
import squid.abc

if TYPE_CHECKING:
    from control.core.acquisition import MultiPointController


class ImageSignalBridge(QObject):
    """Bridge to emit images as Qt signals for thread-safe display.

    This class provides a way for non-Qt controllers (like AutoFocusController)
    to emit images to Qt widgets via signals. The controller calls emit_image(),
    which emits the Qt signal that can be connected to widget slots.

    Usage:
        bridge = ImageSignalBridge()
        controller = AutoFocusController(..., image_to_display_fn=bridge.emit_image)
        bridge.image_to_display.connect(widget.display_image)
    """
    image_to_display = Signal(np.ndarray)

    def emit_image(self, image: np.ndarray) -> None:
        """Emit an image via the Qt signal."""
        self.image_to_display.emit(image)


class MultiPointSignalBridge(QObject):
    """Bridge Qt signals for MultiPointController display.

    This class provides Qt signals for the display layer of multi-point acquisition.
    It handles the callback functions from MultiPointController and emits appropriate
    Qt signals for widget updates.

    The controller publishes control-plane events (start/stop/progress) via EventBus.
    This bridge handles data-plane signals (images, napari updates) via Qt signals.

    Usage:
        bridge = MultiPointSignalBridge(objective_store)
        callbacks = bridge.get_callbacks()
        controller = MultiPointController(..., callbacks=callbacks, ...)
        bridge.image_to_display.connect(widget.display_image)
    """
    # Control signals (for widgets that haven't migrated to EventBus yet)
    acquisition_finished = Signal()
    signal_acquisition_start = Signal()
    signal_acquisition_progress = Signal(int, int, int)  # region, total_regions, timepoint
    signal_region_progress = Signal(int, int)  # current_fov, region_fovs

    # Display signals (data plane - must remain Qt signals)
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)  # image, illumination_source
    signal_current_configuration = Signal(ChannelMode)
    signal_register_current_fov = Signal(float, float)  # x_mm, y_mm

    # Napari-specific signals
    napari_layers_init = Signal(int, int, object)  # height, width, dtype
    napari_layers_update = Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel_name

    # Display config signals
    signal_set_display_tabs = Signal(list, int)  # configurations, NZ
    signal_coordinates = Signal(float, float, float, int)  # x, y, z, region

    def __init__(self, objective_store: ObjectiveStore):
        super().__init__()
        self._objective_store = objective_store
        self._controller: Optional["MultiPointController"] = None
        self._napari_inited_for_this_acquisition = False
        self._mosaic_emit_count: int = 0
        self._pending_frames: list[tuple[np.ndarray, CaptureInfo]] = []

    def set_controller(self, controller: "MultiPointController") -> None:
        """Set the controller reference.

        Must be called after controller creation to enable the bridge
        to query controller state (run_acquisition_current_fov, etc.).
        """
        self._controller = controller

    def get_callbacks(self) -> MultiPointControllerFunctions:
        """Get callback functions to pass to MultiPointController."""
        return MultiPointControllerFunctions(
            signal_acquisition_start=self._on_acquisition_start,
            signal_acquisition_finished=self._on_acquisition_finished,
            signal_new_image=self._on_new_image,
            signal_current_configuration=self._on_current_configuration,
            signal_current_fov=self._on_current_fov,
            signal_overall_progress=self._on_overall_progress,
            signal_region_progress=self._on_region_progress,
        )

    def _on_acquisition_start(self, parameters: AcquisitionParameters) -> None:
        """Handle acquisition start callback."""
        self._napari_inited_for_this_acquisition = False
        self._mosaic_emit_count = 0
        self._pending_frames.clear()

        # Query controller for display parameters
        if self._controller is not None:
            run_current_fov = self._controller.run_acquisition_current_fov
            selected_configs = self._controller.selected_configurations
            nz = self._controller.NZ
        else:
            # Fallback to parameters
            run_current_fov = False
            selected_configs = parameters.selected_configurations
            nz = parameters.NZ

        if not run_current_fov:
            self.signal_set_display_tabs.emit(selected_configs, nz)
        else:
            self.signal_set_display_tabs.emit(selected_configs, 2)
        self.signal_acquisition_start.emit()

    def _on_acquisition_finished(self) -> None:
        """Handle acquisition finished callback."""
        # Flush any remaining buffered frames
        if self._pending_frames:
            for frame_array, info in self._pending_frames:
                self._emit_frame(frame_array, info)
            self._pending_frames.clear()

        self.acquisition_finished.emit()

    def _on_new_image(self, frame: squid.abc.CameraFrame, info: CaptureInfo) -> None:
        """Handle new image callback with throttling logic."""
        self._mosaic_emit_count += 1
        emit_every_n = control._def.MULTIPOINT_DISPLAY_EVERY_NTH or 0

        # Query controller for current FOV mode
        run_current_fov = False
        if self._controller is not None:
            run_current_fov = self._controller.run_acquisition_current_fov

        # Always emit for single-FOV snaps
        should_emit = run_current_fov or control._def.MULTIPOINT_DISPLAY_IMAGES
        if not should_emit and emit_every_n > 0:
            should_emit = self._mosaic_emit_count % emit_every_n == 0

        if should_emit:
            # Emit any buffered frames first
            if self._pending_frames:
                for buffered_frame, buffered_info in self._pending_frames:
                    self._emit_frame(buffered_frame, buffered_info)
            self._emit_frame(frame.frame, info)
            self._pending_frames.clear()
        else:
            # Buffer frames when throttling
            if emit_every_n > 0:
                max_buffer = max(emit_every_n - 1, 1)
                self._pending_frames.append((frame.frame, info))
                if len(self._pending_frames) > max_buffer:
                    self._pending_frames.pop(0)

        self.signal_coordinates.emit(
            info.position.x_mm, info.position.y_mm, info.position.z_mm, info.region_id
        )

    def _emit_frame(self, frame: np.ndarray, info: CaptureInfo) -> None:
        """Emit frame to display widgets."""
        self.image_to_display.emit(frame)
        self.image_to_display_multi.emit(frame, info.configuration.illumination_source)

        if not self._napari_inited_for_this_acquisition:
            self._napari_inited_for_this_acquisition = True
            self.napari_layers_init.emit(frame.shape[0], frame.shape[1], frame.dtype)

        objective_magnification = str(
            int(self._objective_store.get_current_objective_info()["magnification"])
        )
        napari_layer_name = objective_magnification + "x " + info.configuration.name
        self.napari_layers_update.emit(
            frame,
            info.position.x_mm,
            info.position.y_mm,
            info.z_index,
            napari_layer_name,
        )

    def _on_current_configuration(self, channel_mode: ChannelMode) -> None:
        """Handle current configuration callback."""
        self.signal_current_configuration.emit(channel_mode)

    def _on_current_fov(self, x_mm: float, y_mm: float) -> None:
        """Handle current FOV callback."""
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def _on_overall_progress(self, progress: OverallProgressUpdate) -> None:
        """Handle overall progress callback."""
        self.signal_acquisition_progress.emit(
            progress.current_region,
            progress.total_regions,
            progress.current_timepoint,
        )

    def _on_region_progress(self, progress: RegionProgressUpdate) -> None:
        """Handle region progress callback."""
        self.signal_region_progress.emit(progress.current_fov, progress.region_fovs)
