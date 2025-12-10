# Qt-based controller wrappers
from typing import Any, Optional, TYPE_CHECKING

import numpy as np
from qtpy.QtCore import QObject, Signal

from control.core.autofocus import AutoFocusController
from control.core.configuration import ChannelConfigurationManager
from control.core.acquisition import CaptureInfo
from control.core.autofocus import LaserAutofocusController
from control.core.display import LiveController
from control.core.acquisition import MultiPointController
from control.core.acquisition.multi_point_utils import (
    MultiPointControllerFunctions,
    AcquisitionParameters,
    OverallProgressUpdate,
    RegionProgressUpdate,
)
from control.core.navigation import ObjectiveStore
from control.core.navigation import ScanCoordinates
from control.microcontroller import Microcontroller
from control.microscope import Microscope
from control.peripherals.piezo import PiezoStage
from control.utils_config import ChannelMode
from squid.abc import AbstractCamera, AbstractStage
import control.microscope
import squid.abc

if TYPE_CHECKING:
    from squid.services import (
        CameraService,
        StageService,
        PeripheralService,
        PiezoService,
        NL5Service,
    )
    from squid.events import EventBus


class MovementUpdater(QObject):
    position_after_move = Signal(squid.abc.Pos)
    position = Signal(squid.abc.Pos)
    piezo_z_um = Signal(float)

    def __init__(
        self,
        stage: AbstractStage,
        piezo: Optional[PiezoStage],
        movement_threshhold_mm=0.0001,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.stage: AbstractStage = stage
        self.piezo: Optional[PiezoStage] = piezo
        self.movement_threshhold_mm = movement_threshhold_mm
        self.previous_pos: Optional[squid.abc.Pos] = None
        self.previous_piezo_pos: Optional[float] = None
        self.sent_after_stopped = False

    def do_update(self):
        if self.piezo:
            if not self.previous_piezo_pos:
                self.previous_piezo_pos = self.piezo.position
            else:
                current_piezo_position = self.piezo.position
                if self.previous_piezo_pos != current_piezo_position:
                    self.previous_piezo_pos = current_piezo_position
                    self.piezo_z_um.emit(current_piezo_position)

        pos = self.stage.get_pos()
        # Doing previous_pos initialization like this means we technically miss the first real update,
        # but that's okay since this is intended to be run frequently in the background.
        if not self.previous_pos:
            self.previous_pos = pos
            return

        abs_delta_x = abs(self.previous_pos.x_mm - pos.x_mm)
        abs_delta_y = abs(self.previous_pos.y_mm - pos.y_mm)

        if (
            abs_delta_y < self.movement_threshhold_mm
            and abs_delta_x < self.movement_threshhold_mm
            and not self.stage.get_state().busy
        ):
            # In here, send all the signals that must be sent once per stop of movement.  AKA once per arriving at a
            # new position for a while.
            self.sent_after_stopped = True
            self.position_after_move.emit(pos)
        else:
            self.sent_after_stopped = False

        # Here, emit all the signals that want higher fidelity movement updates.
        self.position.emit(pos)

        self.previous_pos = pos


class QtAutoFocusController(AutoFocusController, QObject):
    autofocusFinished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        liveController: LiveController,
        microcontroller: Microcontroller,
        nl5: Optional[control.microscope.NL5],
        # Service-based parameters (optional for backwards compatibility)
        camera_service: Optional["CameraService"] = None,
        stage_service: Optional["StageService"] = None,
        peripheral_service: Optional["PeripheralService"] = None,
        event_bus: Optional["EventBus"] = None,
    ):
        QObject.__init__(self)
        AutoFocusController.__init__(
            self,
            camera,
            stage,
            liveController,
            microcontroller,
            lambda: self.autofocusFinished.emit(),
            lambda image: self.image_to_display.emit(image),
            nl5,
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            event_bus=event_bus,
        )


class QtMultiPointController(MultiPointController, QObject):
    acquisition_finished = Signal()
    signal_acquisition_start = Signal()
    image_to_display = Signal(np.ndarray)
    image_to_display_multi = Signal(np.ndarray, int)
    signal_current_configuration = Signal(ChannelMode)
    signal_register_current_fov = Signal(float, float)
    napari_layers_init = Signal(int, int, object)
    napari_layers_update = Signal(
        np.ndarray, float, float, int, str
    )  # image, x_mm, y_mm, k, channel
    signal_set_display_tabs = Signal(list, int)
    signal_acquisition_progress = Signal(int, int, int)
    signal_region_progress = Signal(int, int)
    signal_coordinates = Signal(float, float, float, int)  # x, y, z, region

    def __init__(
        self,
        microscope: Microscope,
        live_controller: LiveController,
        autofocus_controller: AutoFocusController,
        objective_store: ObjectiveStore,
        channel_configuration_manager: ChannelConfigurationManager,
        scan_coordinates: Optional[ScanCoordinates] = None,
        laser_autofocus_controller: Optional[LaserAutofocusController] = None,
        fluidics: Optional[Any] = None,
        # Service-based parameters
        camera_service: Optional["CameraService"] = None,
        stage_service: Optional["StageService"] = None,
        peripheral_service: Optional["PeripheralService"] = None,
        piezo_service: Optional["PiezoService"] = None,
        nl5_service: Optional["NL5Service"] = None,
        event_bus: Optional["EventBus"] = None,
    ):
        MultiPointController.__init__(
            self,
            microscope=microscope,
            live_controller=live_controller,
            autofocus_controller=autofocus_controller,
            objective_store=objective_store,
            channel_configuration_manager=channel_configuration_manager,
            callbacks=MultiPointControllerFunctions(
                signal_acquisition_start=self._signal_acquisition_start_fn,
                signal_acquisition_finished=self._signal_acquisition_finished_fn,
                signal_new_image=self._signal_new_image_fn,
                signal_current_configuration=self._signal_current_configuration_fn,
                signal_current_fov=self._signal_current_fov_fn,
                signal_overall_progress=self._signal_overall_progress_fn,
                signal_region_progress=self._signal_region_progress_fn,
            ),
            scan_coordinates=scan_coordinates,
            laser_autofocus_controller=laser_autofocus_controller,
            # Pass services and event bus to parent
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            piezo_service=piezo_service,
            nl5_service=nl5_service,
            event_bus=event_bus,
        )
        QObject.__init__(self)

        self._napari_inited_for_this_acquisition = False
        self._mosaic_emit_count: int = 0
        # Buffer of skipped frames (max emit_every_n-1) to flush at end.
        self._pending_frames: list[tuple[np.ndarray, CaptureInfo]] = []

    def _signal_acquisition_start_fn(self, parameters: AcquisitionParameters):
        # TODO mpc napari signals
        self._napari_inited_for_this_acquisition = False
        self._mosaic_emit_count = 0
        self._pending_frames.clear()
        if not self.run_acquisition_current_fov:
            self.signal_set_display_tabs.emit(self.selected_configurations, self.NZ)
        else:
            self.signal_set_display_tabs.emit(self.selected_configurations, 2)
        self.signal_acquisition_start.emit()

    def _signal_acquisition_finished_fn(self):
        # If we throttled updates, flush any remaining frames so the mosaic shows
        # the complete set for this acquisition.
        if self._pending_frames:
            for frame_array, info in self._pending_frames:
                self._emit_frame(frame_array, info)
            self._pending_frames.clear()

        self.acquisition_finished.emit()
        # Note: We don't emit signal_register_current_fov here because:
        # 1. The stage has returned to start position, not an acquired FOV
        # 2. The scan grid will be redrawn by reset_coordinates()
        # 3. Emitting here would add an extra blue rectangle at the return position

    def _signal_new_image_fn(self, frame: squid.abc.CameraFrame, info: CaptureInfo):
        # Avoid heavy UI updates during multipoint if disabled in config
        self._mosaic_emit_count += 1
        emit_every_n = control._def.MULTIPOINT_DISPLAY_EVERY_NTH or 0
        # Always emit for single-FOV snaps to keep the Mosaic/Multichannel panels
        # responsive, even when we throttle during large acquisitions.
        should_emit = self.run_acquisition_current_fov or control._def.MULTIPOINT_DISPLAY_IMAGES
        if not should_emit and emit_every_n > 0:
            should_emit = self._mosaic_emit_count % emit_every_n == 0

        if should_emit:
            # Emit any buffered frames first so none are dropped.
            if self._pending_frames:
                for buffered_frame, buffered_info in self._pending_frames:
                    self._emit_frame(buffered_frame, buffered_info)
            self._emit_frame(frame.frame, info)
            self._pending_frames.clear()
        else:
            # Only buffer when we know we'll eventually flush (emit_every_n > 0).
            if emit_every_n > 0:
                # Keep only the most recent emit_every_n-1 frames to cap memory.
                max_buffer = max(emit_every_n - 1, 1)
                self._pending_frames.append((frame.frame, info))
                if len(self._pending_frames) > max_buffer:
                    self._pending_frames.pop(0)

        self.signal_coordinates.emit(
            info.position.x_mm, info.position.y_mm, info.position.z_mm, info.region_id
        )

    def _emit_frame(self, frame: np.ndarray, info: CaptureInfo) -> None:
        self.image_to_display.emit(frame)
        self.image_to_display_multi.emit(
            frame, info.configuration.illumination_source
        )

        if not self._napari_inited_for_this_acquisition:
            self._napari_inited_for_this_acquisition = True
            self.napari_layers_init.emit(
                frame.shape[0], frame.shape[1], frame.dtype
            )

        objective_magnification = str(
            int(self.objectiveStore.get_current_objective_info()["magnification"])
        )
        napri_layer_name = objective_magnification + "x " + info.configuration.name
        self.napari_layers_update.emit(
            frame,
            info.position.x_mm,
            info.position.y_mm,
            info.z_index,
            napri_layer_name,
        )

    def _signal_current_configuration_fn(self, channel_mode: ChannelMode):
        self.signal_current_configuration.emit(channel_mode)

    def _signal_current_fov_fn(self, x_mm: float, y_mm: float):
        self.signal_register_current_fov.emit(x_mm, y_mm)

    def _signal_overall_progress_fn(self, overall_progress: OverallProgressUpdate):
        self.signal_acquisition_progress.emit(
            overall_progress.current_region,
            overall_progress.total_regions,
            overall_progress.current_timepoint,
        )

    def _signal_region_progress_fn(self, region_progress: RegionProgressUpdate):
        self.signal_region_progress.emit(
            region_progress.current_fov, region_progress.region_fovs
        )
