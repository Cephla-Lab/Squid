import time
from typing import Optional

from PyQt5.QtCore import QObject, QThread
from PyQt5.QtWidgets import QApplication
from qtpy.QtCore import Signal

import numpy as np

from control import utils
import control._def
from control.core.auto_focus_worker import AutofocusWorker
from control.core.live_controller import LiveController
from control.microcontroller import Microcontroller
from control.microscope import NL5
from squid.abc import AbstractCamera, AbstractStage


class AutoFocusController(QObject):
    z_pos = Signal(float)
    autofocusFinished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        liveController: LiveController,
        microcontroller: Microcontroller,
        nl5: Optional[NL5],
    ):
        QObject.__init__(self)
        self.camera: AbstractCamera = camera
        self.stage: AbstractStage = stage
        self.microcontroller: Microcontroller = microcontroller
        self.liveController: LiveController = liveController
        self.nl5: Optional[NL5] = nl5

        # Start with "Reasonable" defaults.
        self.N: int = 10
        self.deltaZ: float = 1.524
        self.crop_width = control._def.AF.CROP_WIDTH
        self.crop_height = control._def.AF.CROP_HEIGHT
        self.autofocus_in_progress = False
        self.focus_map_coords = []
        self.use_focus_map = False

    def set_N(self, N):
        self.N = N

    def set_deltaZ(self, delta_z_um):
        self.deltaZ = delta_z_um / 1000

    def set_crop(self, crop_width, crop_height):
        self.crop_width = crop_width
        self.crop_height = crop_height

    def autofocus(self, focus_map_override=False):
        # TODO(imo): We used to have the joystick button wired up to autofocus, but took it out in a refactor.  It needs to be restored.
        if self.use_focus_map and (not focus_map_override):
            self.autofocus_in_progress = True

            self.stage.wait_for_idle(1.0)
            pos = self.stage.get_pos()

            # z here is in mm because that's how the navigation controller stores it
            target_z = utils.interpolate_plane(*self.focus_map_coords[:3], (pos.x_mm, pos.y_mm))
            print(f"Interpolated target z as {target_z} mm from focus map, moving there.")
            self.stage.move_z_to(target_z)
            self.autofocus_in_progress = False
            self.autofocusFinished.emit()
            return
        # stop live
        if self.liveController.is_live:
            self.was_live_before_autofocus = True
            self.liveController.stop_live()
        else:
            self.was_live_before_autofocus = False

        # temporarily disable call back -> image does not go through streamHandler
        if self.camera.get_callbacks_enabled():
            self.callback_was_enabled_before_autofocus = True
            self.camera.enable_callbacks(False)
        else:
            self.callback_was_enabled_before_autofocus = False

        self.autofocus_in_progress = True

        # create a QThread object
        try:
            if self.thread.isRunning():
                print("*** autofocus thread is still running ***")
                self.thread.terminate()
                self.thread.wait()
                print("*** autofocus threaded manually stopped ***")
        except:
            pass
        self.thread = QThread(parent=self)
        # create a worker object
        self.autofocusWorker = AutofocusWorker(self)
        # move the worker to the thread
        self.autofocusWorker.moveToThread(self.thread)
        # connect signals and slots
        self.thread.started.connect(self.autofocusWorker.run)
        self.autofocusWorker.finished.connect(self._on_autofocus_completed)
        self.autofocusWorker.finished.connect(self.autofocusWorker.deleteLater)
        self.autofocusWorker.finished.connect(self.thread.quit)
        self.autofocusWorker.image_to_display.connect(self.slot_image_to_display)
        self.thread.finished.connect(self.thread.quit)
        # start the thread
        self.thread.start()

    def _on_autofocus_completed(self):
        # re-enable callback
        if self.callback_was_enabled_before_autofocus:
            self.camera.enable_callbacks(True)

        # re-enable live if it's previously on
        if self.was_live_before_autofocus:
            self.liveController.start_live()

        # emit the autofocus finished signal to enable the UI
        self.autofocusFinished.emit()
        QApplication.processEvents()
        print("autofocus finished")

        # update the state
        self.autofocus_in_progress = False

    def slot_image_to_display(self, image):
        self.image_to_display.emit(image)

    def wait_till_autofocus_has_completed(self):
        while self.autofocus_in_progress:
            QApplication.processEvents()
            time.sleep(0.005)
        print("autofocus wait has completed, exit wait")

    def set_focus_map_use(self, enable):
        if not enable:
            print("Disabling focus map.")
            self.use_focus_map = False
            return
        if len(self.focus_map_coords) < 3:
            print("Not enough coordinates (less than 3) for focus map generation, disabling focus map.")
            self.use_focus_map = False
            return
        x1, y1, _ = self.focus_map_coords[0]
        x2, y2, _ = self.focus_map_coords[1]
        x3, y3, _ = self.focus_map_coords[2]

        detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if detT == 0:
            print("Your 3 x-y coordinates are linear, cannot use to interpolate, disabling focus map.")
            self.use_focus_map = False
            return

        if enable:
            print("Enabling focus map.")
            self.use_focus_map = True

    def clear_focus_map(self):
        self.focus_map_coords = []
        self.set_focus_map_use(False)

    def gen_focus_map(self, coord1, coord2, coord3):
        """
        Navigate to 3 coordinates and get your focus-map coordinates
        by autofocusing there and saving the z-values.
        :param coord1-3: Tuples of (x,y) values, coordinates in mm.
        :raise: ValueError if coordinates are all on the same line
        """
        x1, y1 = coord1
        x2, y2 = coord2
        x3, y3 = coord3
        detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if detT == 0:
            raise ValueError("Your 3 x-y coordinates are linear")

        self.focus_map_coords = []

        for coord in [coord1, coord2, coord3]:
            print(f"Navigating to coordinates ({coord[0]},{coord[1]}) to sample for focus map")
            self.stage.move_x_to(coord[0])
            self.stage.move_y_to(coord[1])

            print("Autofocusing")
            self.autofocus(True)
            self.wait_till_autofocus_has_completed()
            pos = self.stage.get_pos()

            print(f"Adding coordinates ({pos.x_mm},{pos.y_mm},{pos.z_mm}) to focus map")
            self.focus_map_coords.append((pos.x_mm, pos.y_mm, pos.z_mm))

        print("Generated focus map.")

    def add_current_coords_to_focus_map(self):
        if len(self.focus_map_coords) >= 3:
            print("Replacing last coordinate on focus map.")
        self.stage.wait_for_idle(timeout_s=0.5)
        print("Autofocusing")
        self.autofocus(True)
        self.wait_till_autofocus_has_completed()
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm
        z = pos.z_mm
        if len(self.focus_map_coords) >= 2:
            x1, y1, _ = self.focus_map_coords[0]
            x2, y2, _ = self.focus_map_coords[1]
            x3 = x
            y3 = y

            detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            if detT == 0:
                raise ValueError(
                    "Your 3 x-y coordinates are linear. Navigate to a different coordinate or clear and try again."
                )
        if len(self.focus_map_coords) >= 3:
            self.focus_map_coords.pop()
        self.focus_map_coords.append((x, y, z))
        print(f"Added triple ({x},{y},{z}) to focus map")
