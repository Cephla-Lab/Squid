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
from squid.backend.controllers.autofocus.auto_focus_worker import AutofocusWorker
from squid.backend.controllers.live_controller import LiveController
from squid.core.state_machine import StateMachine
from squid.core.mode_gate import GlobalMode, GlobalModeGate
from squid.core.events import (
    AutofocusWorkerFinished,
    SetAutofocusParamsCommand,
    StartAutofocusCommand,
    StopAutofocusCommand,
    auto_subscribe,
    auto_unsubscribe,
    handles,
)

if TYPE_CHECKING:
    from squid.backend.services import (
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
        self._focus_map_surface: Optional[object] = None
        self.was_live_before_autofocus: bool = False
        self.callback_was_enabled_before_autofocus: bool = False

        self._subscriptions = []
        if self._event_bus:
            self._subscriptions = auto_subscribe(self, self._event_bus)

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

    def set_focus_map_surface(self, focus_map: Optional[object]) -> None:
        """Attach an optional focus map surface for interpolation."""
        self._focus_map_surface = focus_map

    @property
    def focus_map_surface(self) -> Optional[object]:
        """Return the currently configured focus map surface, if any."""
        return self._focus_map_surface

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

            target_z: Optional[float] = None
            if self._focus_map_surface is not None:
                try:
                    target_z = float(self._focus_map_surface.interpolate(pos.x_mm, pos.y_mm))
                    self._log.info(
                        f"Interpolated target z as {target_z} mm from focus surface map, moving there."
                    )
                except Exception:
                    self._log.exception(
                        "Focus map surface interpolation failed; falling back to plane interpolation"
                    )
                    target_z = None

            if target_z is None:
                if len(self.focus_map_coords) < 3:
                    self._log.error(
                        "Not enough focus map coordinates for plane interpolation; skipping focus map move."
                    )
                    target_z = pos.z_mm
                else:
                    target_z = utils.interpolate_plane(
                        *self.focus_map_coords[:3], (pos.x_mm, pos.y_mm)
                    )
                    self._log.info(
                        f"Interpolated target z as {target_z} mm from focus map plane, moving there."
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

    @handles(AutofocusWorkerFinished)
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

    def wait_till_autofocus_has_completed(self, timeout_s: Optional[float] = None) -> bool:
        deadline = None
        if timeout_s is not None:
            deadline = time.monotonic() + timeout_s
        while self.autofocus_in_progress:
            if deadline is not None and time.monotonic() > deadline:
                self._log.warning("autofocus wait timed out")
                return False
            time.sleep(0.005)
        self._log.info("autofocus wait has completed, exit wait")
        return True

    # ============================================================
    # EventBus command handlers
    # ============================================================
    @handles(StartAutofocusCommand)
    def _on_start_command(self, _cmd) -> None:
        """Handle StartAutofocusCommand."""
        if self.autofocus_in_progress:
            self._log.info("Autofocus already in progress; ignoring start command")
            return
        self.autofocus()

    @handles(StopAutofocusCommand)
    def _on_stop_command(self, _cmd) -> None:
        """Handle StopAutofocusCommand."""
        self.stop_autofocus()

    @handles(SetAutofocusParamsCommand)
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
        if self._focus_map_surface is not None:
            if getattr(self._focus_map_surface, "is_fitted", True):
                self._log.info("Enabling focus map surface.")
                self.use_focus_map = True
                return
            self._log.warning("Focus map surface is not fitted; falling back to plane checks.")

        if len(self.focus_map_coords) < 3:
            self._log.error(
                "Not enough coordinates (less than 3) for focus map generation, disabling focus map."
            )
            self.use_focus_map = False
            return

        non_collinear_indices = None
        for i in range(len(self.focus_map_coords) - 2):
            x1, y1, _ = self.focus_map_coords[i]
            for j in range(i + 1, len(self.focus_map_coords) - 1):
                x2, y2, _ = self.focus_map_coords[j]
                for k in range(j + 1, len(self.focus_map_coords)):
                    x3, y3, _ = self.focus_map_coords[k]
                    detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
                    if detT != 0:
                        non_collinear_indices = (i, j, k)
                        break
                if non_collinear_indices is not None:
                    break
            if non_collinear_indices is not None:
                break

        if non_collinear_indices is None:
            self._log.error(
                "Your x-y coordinates are linear, cannot use to interpolate, disabling focus map."
            )
            self.use_focus_map = False
            return

        i, j, k = non_collinear_indices
        if (i, j, k) != (0, 1, 2):
            reordered = [
                self.focus_map_coords[i],
                self.focus_map_coords[j],
                self.focus_map_coords[k],
            ]
            reordered.extend(
                coord
                for idx, coord in enumerate(self.focus_map_coords)
                if idx not in (i, j, k)
            )
            self.focus_map_coords = reordered

        self._log.info("Enabling focus map.")
        self.use_focus_map = True

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []
        self._focus_map_surface = None
        self.set_focus_map_use(False)

    def sample_focus_point(
        self, x_mm: float, y_mm: float, timeout_s: Optional[float] = None
    ) -> Tuple[float, float, float]:
        """Move to a coordinate, autofocus, and return the focused position."""
        self._log.info(f"Navigating to coordinates ({x_mm},{y_mm}) to sample for focus map")
        self._stage_service.move_x_to(x_mm)
        self._stage_service.move_y_to(y_mm)

        self._log.info("Autofocusing")
        self.autofocus(True)
        if not self.wait_till_autofocus_has_completed(timeout_s=timeout_s):
            self._log.warning("Autofocus did not complete while sampling focus map")

        pos = self._stage_service.get_position()
        self._log.info(
            f"Adding coordinates ({pos.x_mm},{pos.y_mm},{pos.z_mm}) to focus map"
        )
        return (pos.x_mm, pos.y_mm, pos.z_mm)

    def sample_focus_map_points(
        self,
        coords: List[Tuple[float, float]],
        timeout_s: Optional[float] = None,
    ) -> List[Tuple[float, float, float]]:
        """Sample multiple focus points by moving and autofocusing at each coordinate."""
        self.focus_map_coords = []
        for x_mm, y_mm in coords:
            self.focus_map_coords.append(
                self.sample_focus_point(x_mm, y_mm, timeout_s=timeout_s)
            )
        self._log.info("Generated focus map.")
        return list(self.focus_map_coords)

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

        self._focus_map_surface = None
        self.sample_focus_map_points([coord1, coord2, coord3])

    def add_current_coords_to_focus_map(self) -> None:
        self._focus_map_surface = None
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

    def shutdown(self) -> None:
        if self._event_bus:
            auto_unsubscribe(self._subscriptions, self._event_bus)
        self._subscriptions = []
