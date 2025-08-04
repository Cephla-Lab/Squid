from typing import Optional, TypeVar

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication
from qtpy.QtCore import Signal
import time

import numpy as np

from control import utils
import control._def
from control.core.live_controller import LiveController
from control.microcontroller import Microcontroller
from control.microscope import NL5
from squid.abc import AbstractCamera, AbstractStage

AutoFocusController = TypeVar("AutoFocusController")


class AutofocusWorker(QObject):
    finished = Signal()
    image_to_display = Signal(np.ndarray)

    def __init__(self, autofocusController):
        QObject.__init__(self)
        self.autofocusController: AutoFocusController = autofocusController

        self.camera: AbstractCamera = self.autofocusController.camera
        self.microcontroller: Microcontroller = self.autofocusController.microcontroller
        self.stage: AbstractStage = self.autofocusController.stage
        self.liveController: LiveController = self.autofocusController.liveController
        self.nl5: Optional[NL5] = self.autofocusController.nl5

        self.N = self.autofocusController.N
        self.deltaZ = self.autofocusController.deltaZ

        self.crop_width = self.autofocusController.crop_width
        self.crop_height = self.autofocusController.crop_height

    def run(self):
        self.run_autofocus()
        self.finished.emit()

    def wait_till_operation_is_completed(self):
        while self.microcontroller.is_busy():
            time.sleep(control._def.SLEEP_TIME_S)

    def run_autofocus(self):
        # @@@ to add: increase gain, decrease exposure time
        # @@@ can move the execution into a thread - done 08/21/2021
        focus_measure_vs_z = [0] * self.N
        focus_measure_max = 0

        z_af_offset = self.deltaZ * round(self.N / 2)

        self.stage.move_z(-z_af_offset)

        steps_moved = 0
        image = None
        for i in range(self.N):
            self.stage.move_z(self.deltaZ)
            steps_moved = steps_moved + 1
            # trigger acquisition (including turning on the illumination) and read frame
            if self.liveController.trigger_mode == control._def.TriggerMode.SOFTWARE:
                self.liveController.turn_on_illumination()
                self.wait_till_operation_is_completed()
                self.camera.send_trigger()
                image = self.camera.read_frame()
            elif self.liveController.trigger_mode == control._def.TriggerMode.HARDWARE:
                if (
                    "Fluorescence" in self.liveController.currentConfiguration.name
                    and control._def.ENABLE_NL5
                    and control._def.NL5_USE_DOUT
                ):
                    self.nl5.start_acquisition()
                    # TODO(imo): This used to use the "reset_image_ready_flag=False" arg, but oinly the toupcam camera implementation had the
                    #  "reset_image_ready_flag" arg, so this is broken for all other cameras.
                    image = self.camera.read_frame()
                else:
                    self.microcontroller.send_hardware_trigger(
                        control_illumination=True, illumination_on_time_us=self.camera.get_exposure_time() * 1000
                    )
                    image = self.camera.read_frame()
            if image is None:
                continue
            # tunr of the illumination if using software trigger
            if self.liveController.trigger_mode == control._def.TriggerMode.SOFTWARE:
                self.liveController.turn_off_illumination()

            image = utils.crop_image(image, self.crop_width, self.crop_height)
            self.image_to_display.emit(image)

            QApplication.processEvents()
            timestamp_0 = time.time()
            focus_measure = utils.calculate_focus_measure(image, control._def.FOCUS_MEASURE_OPERATOR)
            timestamp_1 = time.time()
            print("             calculating focus measure took " + str(timestamp_1 - timestamp_0) + " second")
            focus_measure_vs_z[i] = focus_measure
            print(i, focus_measure)
            focus_measure_max = max(focus_measure, focus_measure_max)
            if focus_measure < focus_measure_max * control._def.AF.STOP_THRESHOLD:
                break

        QApplication.processEvents()

        # maneuver for achiving uniform step size and repeatability when using open-loop control
        self.stage.move_z(-steps_moved * self.deltaZ)
        # determine the in-focus position
        idx_in_focus = focus_measure_vs_z.index(max(focus_measure_vs_z))
        self.stage.move_z((idx_in_focus + 1) * self.deltaZ)

        QApplication.processEvents()

        # move to the calculated in-focus position
        if idx_in_focus == 0:
            print("moved to the bottom end of the AF range")
        if idx_in_focus == self.N - 1:
            print("moved to the top end of the AF range")
