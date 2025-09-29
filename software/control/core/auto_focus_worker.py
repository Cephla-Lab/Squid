import threading
from typing import Callable, Optional, TypeVar

import time
import numpy as np

import squid.logging
from control import utils
import control._def
from control.core.live_controller import LiveController
from control.microcontroller import Microcontroller
from control.microscope import NL5
from squid.abc import AbstractCamera, AbstractStage

AutoFocusController = TypeVar("AutoFocusController")


class AutofocusWorker:
    def __init__(
        self,
        autofocusController,
        finished_fn: Callable[[], None],
        image_to_display_fn: Callable[[np.ndarray], None],
        keep_running: threading.Event,
    ):
        self.autofocusController: AutoFocusController = autofocusController
        self._finished_fn = finished_fn
        self._image_to_display_fn = image_to_display_fn
        self._keep_running: threading.Event = keep_running
        self._log = squid.logging.get_logger(self.__class__.__name__)

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
        try:
            self.run_autofocus()
        finally:
            self._finished_fn()

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
            if not self._keep_running.is_set():
                self._log.warning("Signal to abort autofocus received, aborting!")
                # This aborts and then we report our best focus so far
                break
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
            self._image_to_display_fn(image)

            timestamp_0 = time.time()
            focus_measure = utils.calculate_focus_measure(image, control._def.FOCUS_MEASURE_OPERATOR)
            timestamp_1 = time.time()
            self._log.info("             calculating focus measure took " + str(timestamp_1 - timestamp_0) + " second")
            focus_measure_vs_z[i] = focus_measure
            self._log.debug(f"{i} {focus_measure}")
            focus_measure_max = max(focus_measure, focus_measure_max)
            if focus_measure < focus_measure_max * control._def.AF.STOP_THRESHOLD:
                break

        # maneuver for achiving uniform step size and repeatability when using open-loop control
        self.stage.move_z(-steps_moved * self.deltaZ)
        # determine the in-focus position
        idx_in_focus = focus_measure_vs_z.index(max(focus_measure_vs_z))
        self.stage.move_z((idx_in_focus + 1) * self.deltaZ)

        # move to the calculated in-focus position
        if idx_in_focus == 0:
            self._log.info("moved to the bottom end of the AF range")
        if idx_in_focus == self.N - 1:
            self._log.info("moved to the top end of the AF range")
