from __future__ import annotations

import threading
import time
from threading import Thread
from typing import Optional, Callable, TYPE_CHECKING, List, Tuple

import numpy as np

import squid.logging
from control import utils
import control._def
from control.core.autofocus.auto_focus_worker import AutofocusWorker
from control.core.display import LiveController
from control.microcontroller import Microcontroller
from squid.abc import AbstractCamera, AbstractStage

if TYPE_CHECKING:
    from control.microscope import NL5
    from squid.services import CameraService, StageService, PeripheralService
    from squid.events import EventBus


class AutoFocusController:
    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        liveController: LiveController,
        microcontroller: Microcontroller,
        finished_fn: Callable[[], None],
        image_to_display_fn: Callable[[np.ndarray], None],
        nl5: Optional[NL5],
        # Service-based parameters (optional for backwards compatibility)
        camera_service: Optional["CameraService"] = None,
        stage_service: Optional["StageService"] = None,
        peripheral_service: Optional["PeripheralService"] = None,
        event_bus: Optional["EventBus"] = None,
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._autofocus_worker: Optional[AutofocusWorker] = None
        self._focus_thread: Optional[Thread] = None
        self._keep_running = threading.Event()

        # Direct hardware references (for fallback)
        self.camera: AbstractCamera = camera
        self.stage: AbstractStage = stage
        self.microcontroller: Microcontroller = microcontroller
        self.liveController: LiveController = liveController
        self._finished_fn = finished_fn
        self._image_to_display_fn = image_to_display_fn
        self.nl5: Optional[NL5] = nl5

        # Service references
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._peripheral_service = peripheral_service
        self._event_bus = event_bus

        # Start with "Reasonable" defaults.
        self.N: int = 10
        self.deltaZ: float = 1.524
        self.crop_width: int = control._def.AF.CROP_WIDTH
        self.crop_height: int = control._def.AF.CROP_HEIGHT
        self.autofocus_in_progress: bool = False
        self.focus_map_coords: List[Tuple[float, float, float]] = []
        self.use_focus_map: bool = False
        self.was_live_before_autofocus: bool = False
        self.callback_was_enabled_before_autofocus: bool = False

    def set_N(self, N: int) -> None:
        self.N = N

    def set_deltaZ(self, delta_z_um: float) -> None:
        self.deltaZ = delta_z_um / 1000

    def set_crop(self, crop_width: int, crop_height: int) -> None:
        self.crop_width = crop_width
        self.crop_height = crop_height

    def autofocus(self, focus_map_override: bool = False) -> None:
        if self.use_focus_map and (not focus_map_override):
            self.autofocus_in_progress = True

            # Use service if available, otherwise direct access
            if self._stage_service:
                self._stage_service.wait_for_idle(1.0)
                pos = self._stage_service.get_position()
            else:
                self.stage.wait_for_idle(1.0)
                pos = self.stage.get_pos()

            # z here is in mm because that's how the navigation controller stores it
            target_z = utils.interpolate_plane(
                *self.focus_map_coords[:3], (pos.x_mm, pos.y_mm)
            )
            self._log.info(
                f"Interpolated target z as {target_z} mm from focus map, moving there."
            )
            if self._stage_service:
                self._stage_service.move_z_to(target_z)
            else:
                self.stage.move_z_to(target_z)
            self.autofocus_in_progress = False
            self._finished_fn()
            return
        # stop live
        if self.liveController.is_live:
            self.was_live_before_autofocus = True
            self.liveController.stop_live()
        else:
            self.was_live_before_autofocus = False

        # temporarily disable call back -> image does not go through streamHandler
        if self._camera_service:
            callbacks_enabled = self._camera_service.get_callbacks_enabled()
        else:
            callbacks_enabled = self.camera.get_callbacks_enabled()

        if callbacks_enabled:
            self.callback_was_enabled_before_autofocus = True
            if self._camera_service:
                self._camera_service.enable_callbacks(False)
            else:
                self.camera.enable_callbacks(False)
        else:
            self.callback_was_enabled_before_autofocus = False

        self.autofocus_in_progress = True

        # create a QThread object
        if self._focus_thread and self._focus_thread.is_alive():
            self._keep_running.clear()
            try:
                self._focus_thread.join(1.0)
            except RuntimeError as e:
                self._log.exception("Critical error joining previous autofocus thread.")
                self._finished_fn()
                raise e
            if self._focus_thread.is_alive():
                self._log.error("Previous focus thread failed to join!")
                self._finished_fn()
                raise RuntimeError("Previous focus thread failed to join")

        self._keep_running.set()
        self._autofocus_worker = AutofocusWorker(
            self,
            self._on_autofocus_completed,
            self._image_to_display_fn,
            self._keep_running,
        )
        self._focus_thread = Thread(target=self._autofocus_worker.run, daemon=True)
        self._focus_thread.start()

    def _on_autofocus_completed(self) -> None:
        # re-enable callback
        if self.callback_was_enabled_before_autofocus:
            if self._camera_service:
                self._camera_service.enable_callbacks(True)
            else:
                self.camera.enable_callbacks(True)

        # re-enable live if it's previously on
        if self.was_live_before_autofocus:
            self.liveController.start_live()

        # emit the autofocus finished signal to enable the UI
        self._finished_fn()
        self._log.info("autofocus finished")

        # update the state
        self.autofocus_in_progress = False

    def wait_till_autofocus_has_completed(self) -> None:
        while self.autofocus_in_progress:
            time.sleep(0.005)
        self._log.info("autofocus wait has completed, exit wait")

    def set_focus_map_use(self, enable: bool) -> None:
        if not enable:
            self._log.info("Disabling focus map.")
            self.use_focus_map = False
            return
        if len(self.focus_map_coords) < 3:
            self._log.error(
                "Not enough coordinates (less than 3) for focus map generation, disabling focus map."
            )
            self.use_focus_map = False
            return
        x1, y1, _ = self.focus_map_coords[0]
        x2, y2, _ = self.focus_map_coords[1]
        x3, y3, _ = self.focus_map_coords[2]

        detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if detT == 0:
            self._log.error(
                "Your 3 x-y coordinates are linear, cannot use to interpolate, disabling focus map."
            )
            self.use_focus_map = False
            return

        if enable:
            self._log.info("Enabling focus map.")
            self.use_focus_map = True

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []
        self.set_focus_map_use(False)

    def gen_focus_map(
        self,
        coord1: Tuple[float, float],
        coord2: Tuple[float, float],
        coord3: Tuple[float, float],
    ) -> None:
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
            self._log.info(
                f"Navigating to coordinates ({coord[0]},{coord[1]}) to sample for focus map"
            )
            if self._stage_service:
                self._stage_service.move_x_to(coord[0])
                self._stage_service.move_y_to(coord[1])
            else:
                self.stage.move_x_to(coord[0])
                self.stage.move_y_to(coord[1])

            self._log.info("Autofocusing")
            self.autofocus(True)
            self.wait_till_autofocus_has_completed()

            if self._stage_service:
                pos = self._stage_service.get_position()
            else:
                pos = self.stage.get_pos()

            self._log.info(
                f"Adding coordinates ({pos.x_mm},{pos.y_mm},{pos.z_mm}) to focus map"
            )
            self.focus_map_coords.append((pos.x_mm, pos.y_mm, pos.z_mm))

        self._log.info("Generated focus map.")

    def add_current_coords_to_focus_map(self) -> None:
        if len(self.focus_map_coords) >= 3:
            self._log.info("Replacing last coordinate on focus map.")
        if self._stage_service:
            self._stage_service.wait_for_idle(timeout_s=0.5)
        else:
            self.stage.wait_for_idle(timeout_s=0.5)
        self._log.info("Autofocusing")
        self.autofocus(True)
        self.wait_till_autofocus_has_completed()
        if self._stage_service:
            pos = self._stage_service.get_position()
        else:
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
        self._log.info(f"Added triple ({x},{y},{z}) to focus map")
