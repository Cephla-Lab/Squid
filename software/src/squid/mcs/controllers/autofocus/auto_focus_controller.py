from __future__ import annotations

import threading
import time
from enum import Enum, auto
from threading import Thread
from typing import Optional, TYPE_CHECKING, List, Set, Tuple

import numpy as np

import squid.core.logging
import squid.core.utils.hardware_utils as utils
import _def
from squid.mcs.controllers.autofocus.auto_focus_worker import AutofocusWorker
from squid.mcs.controllers.live_controller import LiveController
from squid.core.state_machine import StateMachine
from squid.core.mode_gate import GlobalMode, GlobalModeGate

if TYPE_CHECKING:
    from squid.mcs.services import (
        CameraService,
        StageService,
        PeripheralService,
        NL5Service,
        IlluminationService,
    )
    from squid.core.events import EventBus


class AutofocusControllerState(Enum):
    """State machine states for AutoFocusController."""

    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


class AutoFocusController(StateMachine[AutofocusControllerState]):
    def __init__(
        self,
        liveController: LiveController,
        camera_service: "CameraService",
        stage_service: "StageService",
        peripheral_service: "PeripheralService",
        event_bus: "EventBus",
        nl5_service: Optional["NL5Service"] = None,
        illumination_service: Optional["IlluminationService"] = None,
        stream_handler: Optional[object] = None,
        mode_gate: Optional[GlobalModeGate] = None,
    ):
        # Initialize state machine with transitions
        # IDLE -> RUNNING: normal autofocus start
        # RUNNING -> COMPLETED: success
        # RUNNING -> FAILED: abort or error
        # COMPLETED/FAILED -> IDLE: reset for next run
        transitions = {
            AutofocusControllerState.IDLE: {AutofocusControllerState.RUNNING},
            AutofocusControllerState.RUNNING: {
                AutofocusControllerState.COMPLETED,
                AutofocusControllerState.FAILED,
            },
            AutofocusControllerState.COMPLETED: {AutofocusControllerState.IDLE},
            AutofocusControllerState.FAILED: {AutofocusControllerState.IDLE},
        }
        super().__init__(
            initial_state=AutofocusControllerState.IDLE,
            transitions=transitions,
            event_bus=event_bus,
            name="AutoFocusController",
        )

        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._autofocus_worker: Optional[AutofocusWorker] = None
        self._focus_thread: Optional[Thread] = None
        self._keep_running = threading.Event()

        self.liveController: LiveController = liveController
        self._stream_handler = stream_handler

        self._camera_service: "CameraService" = camera_service
        self._stage_service: "StageService" = stage_service
        self._peripheral_service: "PeripheralService" = peripheral_service
        self._nl5_service: Optional["NL5Service"] = nl5_service
        self._illumination_service: Optional["IlluminationService"] = illumination_service
        self._mode_gate = mode_gate
        self._previous_mode: Optional[GlobalMode] = None

        # Start with "Reasonable" defaults.
        self.N: int = 10
        self.deltaZ: float = 1.524
        self.crop_width: int = _def.AF.CROP_WIDTH
        self.crop_height: int = _def.AF.CROP_HEIGHT
        self._autofocus_abort_requested: bool = False
        self.focus_map_coords: List[Tuple[float, float, float]] = []
        self.use_focus_map: bool = False
        self.was_live_before_autofocus: bool = False
        self.callback_was_enabled_before_autofocus: bool = False

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if self._event_bus is None:
            return
        from squid.core.events import (
            StartAutofocusCommand,
            StopAutofocusCommand,
            SetAutofocusParamsCommand,
            AutofocusWorkerFinished,
        )

        self._event_bus.subscribe(StartAutofocusCommand, self._on_start_command)
        self._event_bus.subscribe(StopAutofocusCommand, self._on_stop_command)
        self._event_bus.subscribe(
            SetAutofocusParamsCommand, self._on_set_params_command
        )
        self._event_bus.subscribe(AutofocusWorkerFinished, self._on_worker_finished)

    def _publish_state_changed(
        self, old_state: AutofocusControllerState, new_state: AutofocusControllerState
    ) -> None:
        """Publish state change event (StateMachine abstract method)."""
        if self._event_bus:
            from squid.core.events import AutofocusStateChanged

            self._event_bus.publish(
                AutofocusStateChanged(
                    old_state=old_state.name,
                    new_state=new_state.name,
                    is_running=new_state == AutofocusControllerState.RUNNING,
                )
            )

    @property
    def autofocus_in_progress(self) -> bool:
        """Check if autofocus is running (backwards compatibility property)."""
        return self._is_in_state(AutofocusControllerState.RUNNING)

    def set_N(self, N: int) -> None:
        self.N = N

    def set_deltaZ(self, delta_z_um: float) -> None:
        self.deltaZ = delta_z_um / 1000

    def set_crop(self, crop_width: int, crop_height: int) -> None:
        self.crop_width = crop_width
        self.crop_height = crop_height

    def autofocus(self, focus_map_override: bool = False) -> None:
        # Check we're in IDLE state
        if not self._is_in_state(AutofocusControllerState.IDLE):
            self._log.info(f"Cannot start autofocus: state is {self.state.name}")
            return

        if self.use_focus_map and (not focus_map_override):
            previous_mode = self._mode_gate.get_mode() if self._mode_gate else None
            if self._mode_gate:
                self._mode_gate.set_mode(GlobalMode.ACQUIRING, reason="autofocus focus-map")

            # Focus map path - quick synchronous operation
            self._transition_to(AutofocusControllerState.RUNNING)

            self._stage_service.wait_for_idle(1.0)
            pos = self._stage_service.get_position()

            # z here is in mm because that's how the navigation controller stores it
            target_z = utils.interpolate_plane(
                *self.focus_map_coords[:3], (pos.x_mm, pos.y_mm)
            )
            self._log.info(
                f"Interpolated target z as {target_z} mm from focus map, moving there."
            )
            self._stage_service.move_z_to(target_z)
            self._transition_to(AutofocusControllerState.COMPLETED)
            self._publish_completed(success=True, error=None)
            self._transition_to(AutofocusControllerState.IDLE)
            if self._mode_gate and previous_mode is not None:
                self._mode_gate.restore_mode(previous_mode, reason="autofocus focus-map complete")
            return

        # Full autofocus: set global mode while worker runs
        if self._mode_gate:
            self._previous_mode = self._mode_gate.get_mode()
            self._mode_gate.set_mode(GlobalMode.ACQUIRING, reason="autofocus start")

        # Transition to RUNNING
        self._transition_to(AutofocusControllerState.RUNNING)

        # stop live
        if self.liveController.is_live:
            self.was_live_before_autofocus = True
            self.liveController.stop_live()
        else:
            self.was_live_before_autofocus = False

        # temporarily disable callbacks -> image does not go through StreamHandler
        callbacks_enabled = self._camera_service.get_callbacks_enabled()

        if callbacks_enabled:
            self.callback_was_enabled_before_autofocus = True
            self._camera_service.enable_callbacks(False)
        else:
            self.callback_was_enabled_before_autofocus = False

        # create a QThread object
        if self._focus_thread and self._focus_thread.is_alive():
            self._keep_running.clear()
            try:
                self._focus_thread.join(1.0)
            except RuntimeError as e:
                self._log.exception("Critical error joining previous autofocus thread.")
                self._transition_to(AutofocusControllerState.FAILED)
                self._publish_completed(
                    success=False,
                    error="Critical error joining previous autofocus thread",
                )
                if self._mode_gate and self._previous_mode is not None:
                    self._mode_gate.restore_mode(self._previous_mode, reason="autofocus failed")
                self._transition_to(AutofocusControllerState.IDLE)
                raise e
            if self._focus_thread.is_alive():
                self._log.error("Previous focus thread failed to join!")
                self._transition_to(AutofocusControllerState.FAILED)
                self._publish_completed(
                    success=False,
                    error="Previous focus thread failed to join",
                )
                if self._mode_gate and self._previous_mode is not None:
                    self._mode_gate.restore_mode(self._previous_mode, reason="autofocus failed")
                self._transition_to(AutofocusControllerState.IDLE)
                raise RuntimeError("Previous focus thread failed to join")

        self._keep_running.set()
        trigger_mode = getattr(self.liveController, "trigger_mode", None)
        configuration = getattr(self.liveController, "currentConfiguration", None)
        self._autofocus_worker = AutofocusWorker(
            camera_service=self._camera_service,
            stage_service=self._stage_service,
            peripheral_service=self._peripheral_service,
            nl5_service=self._nl5_service,
            illumination_service=self._illumination_service,
            trigger_mode=trigger_mode,
            configuration=configuration,
            keep_running=self._keep_running,
            event_bus=self._event_bus,
            stream_handler=self._stream_handler,
            n_planes=self.N,
            delta_z_mm=self.deltaZ,
            crop_width=self.crop_width,
            crop_height=self.crop_height,
        )
        self._focus_thread = Thread(target=self._autofocus_worker.run, daemon=True)
        self._focus_thread.start()

    def stop_autofocus(self) -> None:
        """Request autofocus stop and emit failure event when aborted."""
        if not self.autofocus_in_progress:
            return

        self._autofocus_abort_requested = True
        self._keep_running.clear()

    def _on_worker_finished(self, event) -> None:
        if not self.autofocus_in_progress:
            return
        # re-enable callback
        if self.callback_was_enabled_before_autofocus:
            self._camera_service.enable_callbacks(True)

        # re-enable live if it's previously on
        if self.was_live_before_autofocus:
            self.liveController.start_live()

        aborted = self._autofocus_abort_requested or getattr(event, "aborted", False)
        if aborted:
            # Transition to FAILED, emit failure, then reset to IDLE
            self._transition_to(AutofocusControllerState.FAILED)
            self._publish_completed(success=False, error="Autofocus aborted")
            self._log.info("autofocus aborted")
        elif not getattr(event, "success", False):
            self._transition_to(AutofocusControllerState.FAILED)
            self._publish_completed(success=False, error=getattr(event, "error", None) or "Autofocus failed")
            self._log.info("autofocus failed")
        else:
            # Transition to COMPLETED, emit success, then reset to IDLE
            self._transition_to(AutofocusControllerState.COMPLETED)
            self._publish_completed(success=True, error=None)
            self._log.info("autofocus finished")

        # Restore mode and reset state
        if self._mode_gate and self._previous_mode is not None:
            self._mode_gate.restore_mode(self._previous_mode, reason="autofocus complete")
        self._previous_mode = None
        self._transition_to(AutofocusControllerState.IDLE)
        self._autofocus_abort_requested = False

    def _publish_completed(self, success: bool, error: Optional[str]) -> None:
        if self._event_bus is None:
            return
        from squid.core.events import AutofocusCompleted

        z_pos: Optional[float]
        if success:
            pos = self._stage_service.get_position()
            z_pos = pos.z_mm
        else:
            z_pos = None

        self._event_bus.publish(
            AutofocusCompleted(
                success=success,
                z_position=z_pos,
                score=None,
                error=error,
            )
        )

    def wait_till_autofocus_has_completed(self) -> None:
        while self.autofocus_in_progress:
            time.sleep(0.005)
        self._log.info("autofocus wait has completed, exit wait")

    # ============================================================
    # EventBus command handlers
    # ============================================================
    def _on_start_command(self, _cmd) -> None:
        """Handle StartAutofocusCommand."""
        if self.autofocus_in_progress:
            self._log.info("Autofocus already in progress; ignoring start command")
            return
        self.autofocus()

    def _on_stop_command(self, _cmd) -> None:
        """Handle StopAutofocusCommand."""
        self.stop_autofocus()

    def _on_set_params_command(self, cmd) -> None:
        """Handle SetAutofocusParamsCommand."""
        if cmd.n_planes is not None:
            self.set_N(int(cmd.n_planes))
        if cmd.delta_z_um is not None:
            self.set_deltaZ(cmd.delta_z_um)

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
            self._stage_service.move_x_to(coord[0])
            self._stage_service.move_y_to(coord[1])

            self._log.info("Autofocusing")
            self.autofocus(True)
            self.wait_till_autofocus_has_completed()

            pos = self._stage_service.get_position()

            self._log.info(
                f"Adding coordinates ({pos.x_mm},{pos.y_mm},{pos.z_mm}) to focus map"
            )
            self.focus_map_coords.append((pos.x_mm, pos.y_mm, pos.z_mm))

        self._log.info("Generated focus map.")

    def add_current_coords_to_focus_map(self) -> None:
        if len(self.focus_map_coords) >= 3:
            self._log.info("Replacing last coordinate on focus map.")
        self._stage_service.wait_for_idle(timeout_s=0.5)
        self._log.info("Autofocusing")
        self.autofocus(True)
        self.wait_till_autofocus_has_completed()
        pos = self._stage_service.get_position()
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
