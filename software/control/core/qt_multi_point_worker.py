from qtpy import QtCore
import numpy as np
from typing import Callable

import control.core.multi_point_worker as multi_point_worker
from control._def import *
from control import utils
from control.utils_config import ChannelMode


class QtMultiPointWorker(multi_point_worker.MultiPointWorker):
    finished = QtCore.Signal()
    image_to_display = QtCore.Signal(np.ndarray)
    spectrum_to_display = QtCore.Signal(np.ndarray)
    image_to_display_multi = QtCore.Signal(np.ndarray, int)
    signal_current_configuration = QtCore.Signal(ChannelMode)
    signal_register_current_fov = QtCore.Signal(float, float)
    signal_z_piezo_um = QtCore.Signal(float)
    napari_layers_init = QtCore.Signal(int, int, object)
    napari_layers_update = QtCore.Signal(np.ndarray, float, float, int, str)  # image, x_mm, y_mm, k, channel
    signal_acquisition_progress = QtCore.Signal(int, int, int)
    signal_region_progress = QtCore.Signal(int, int)

    def _build_interaction_fns(self, abort_test_fn: Callable[[], bool]):
        def capture_update_fn(info: multi_point_worker.CaptureInfo):
            image_to_display = utils.crop_image(
                info.image,
                round(self.crop_width * self.display_resolution_scaling),
                round(self.crop_height * self.display_resolution_scaling))

            self.image_to_display.emit(image_to_display)
            self.image_to_display_multi.emit(image_to_display, info.channel_mode.illumination_source)

            if not self.performance_mode and (USE_NAPARI_FOR_MOSAIC_DISPLAY or USE_NAPARI_FOR_MULTIPOINT):
                if not self.init_napari_layers:
                    self.init_napari_layers = True
                    shape = info.image.shape
                    self.napari_layers_init.emit(shape[0], shape[1], info.image.dtype)
                self.napari_layers_update.emit(info.image, info.position.x_mm, info.position.y_mm, info.z_level, info.label)

        _interaction_fns = multi_point_worker.MultiPointWorkerInteractionFunctions(
            finished=lambda: self.finished.emit(),
            acquisition_progress=lambda prog: self.signal_acquisition_progress.emit(prog.current_region, prog.total_regions, prog.time_point),
            region_progress=lambda prog: self.signal_region_progress.emit(prog.current_image, prog.total_images),
            new_capture=capture_update_fn,
            new_channel_mode=lambda channel_mode: self.signal_current_configuration.emit(channel_mode),
            new_fov_position=lambda pos: self.signal_register_current_fov.emit(pos.x_mm, pos.y_mm),
            new_piezo_z_position=lambda new_z: self.signal_z_piezo_um.emit(new_z),
            abort_requested=abort_test_fn)

        return _interaction_fns

    def __init__(self, multi_point_controller):
        super().__init__()
