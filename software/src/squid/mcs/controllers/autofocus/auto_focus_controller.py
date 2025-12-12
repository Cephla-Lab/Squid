from __future__ import annotations

import threading
import time
from enum import Enum, auto
from threading import Thread
from typing import Optional, Callable, TYPE_CHECKING, List, Set, Tuple

import numpy as np

import squid.core.logging
import squid.core.utils.hardware_utils as utils
import _def
from squid.mcs.controllers.autofocus.auto_focus_worker import AutofocusWorker
from squid.mcs.controllers.live_controller import LiveController
from squid.mcs.microcontroller import Microcontroller
from squid.core.abc import AbstractCamera, AbstractStage
from squid.core.state_machine import StateMachine
from squid.core.coordinator import ResourceCoordinator, Resource, GlobalMode, ResourceLease

if TYPE_CHECKING:
    from squid.mcs.microscope import NL5
    from squid.mcs.services import CameraService, StageService, PeripheralService
    from squid.core.events import EventBus


class AutofocusControllerState(Enum):
    """State machine states for AutoFocusController."""

    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


# Resources required by AutoFocusController
AUTOFOCUS_REQUIRED_RESOURCES: Set[Resource] = {
    Resource.CAMERA_CONTROL,
    Resource.STAGE_CONTROL,
    Resource.FOCUS_AUTHORITY,
}


class AutoFocusController(StateMachine[AutofocusControllerState]):
    def __init__(
        self,
        camera: AbstractCamera,
        stage: AbstractStage,
        liveController: LiveController,
        microcontroller: Microcontroller,
        finished_fn: Optional[Callable[[], None]] = None,
        image_to_display_fn: Optional[Callable[[np.ndarray], None]] = None,
        nl5: Optional["NL5"] = None,
        # Service-based parameters (optional for backwards compatibility)
        camera_service: Optional["CameraService"] = None,
        stage_service: Optional["StageService"] = None,
        peripheral_service: Optional["PeripheralService"] = None,
        event_bus: Optional["EventBus"] = None,
        coordinator: Optional[ResourceCoordinator] = None,
        subscribe_to_bus: bool = True,
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

        # Direct hardware references (for fallback)
        self.camera: AbstractCamera = camera
        self.stage: AbstractStage = stage
        self.microcontroller: Microcontroller = microcontroller
        self.liveController: LiveController = liveController
        self._finished_fn = finished_fn
        self._image_to_display_fn = image_to_display_fn
        self.nl5: Optional["NL5"] = nl5

        # Service references
        self._camera_service = camera_service
        self._stage_service = stage_service
        self._peripheral_service = peripheral_service
        self._coordinator = coordinator
        self._resource_lease: Optional[ResourceLease] = None
        self._bus_enabled = subscribe_to_bus

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

        # Subscribe to EventBus commands for thread-safe UI control
        if self._event_bus is not None and subscribe_to_bus:
            self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if self._event_bus is None or not self._bus_enabled:
            return
        from squid.core.events import (
            StartAutofocusCommand,
            StopAutofocusCommand,
            SetAutofocusParamsCommand,
        )

        self._event_bus.subscribe(StartAutofocusCommand, self._on_start_command)
        self._event_bus.subscribe(StopAutofocusCommand, self._on_stop_command)
        self._event_bus.subscribe(
            SetAutofocusParamsCommand, self._on_set_params_command
        )

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

    def _acquire_resources(self) -> bool:
        """Acquire required resources from coordinator.

        Returns:
            True if resources acquired (or no coordinator), False if unavailable
        """
        if self._coordinator is None:
            return True  # No coordinator, proceed without resource tracking

        lease = self._coordinator.acquire(
            resources=AUTOFOCUS_REQUIRED_RESOURCES,
            owner="AutoFocusController",
            mode=GlobalMode.ACQUIRING,
        )
        if lease is None:
            self._log.warning("Could not acquire resources for autofocus")
            return False

        self._resource_lease = lease
        self._log.debug(f"Acquired resource lease: {lease.lease_id[:8]}")
        return True

    def _release_resources(self) -> None:
        """Release held resources back to coordinator."""
        if self._coordinator is None or self._resource_lease is None:
            return

        self._coordinator.release(self._resource_lease)
        self._log.debug(f"Released resource lease: {self._resource_lease.lease_id[:8]}")
        self._resource_lease = None

    @property
    def autofocus_in_progress(self) -> bool:
        """Check if autofocus is running (backwards compatibility property)."""
        return self._is_in_state(AutofocusControllerState.RUNNING)

    def detach_event_bus_commands(self) -> None:
        """Unsubscribe bus commands to allow actor routing."""
        if self._event_bus is None:
            return
        self._bus_enabled = False
        try:
            from squid.core.events import (
                StartAutofocusCommand,
                StopAutofocusCommand,
                SetAutofocusParamsCommand,
            )

            self._event_bus.unsubscribe(StartAutofocusCommand, self._on_start_command)
            self._event_bus.unsubscribe(StopAutofocusCommand, self._on_stop_command)
            self._event_bus.unsubscribe(
                SetAutofocusParamsCommand, self._on_set_params_command
            )
        except Exception:
            pass

    def set_finished_callback(self, finished_fn: Optional[Callable[[], None]]) -> None:
        """Attach or replace the completion callback."""
        self._finished_fn = finished_fn

    def set_image_callback(
        self, image_callback: Optional[Callable[[np.ndarray], None]]
    ) -> None:
        """Attach or replace the image callback."""
        self._image_to_display_fn = image_callback

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
            # Focus map path - quick synchronous operation
            self._transition_to(AutofocusControllerState.RUNNING)

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
            self._transition_to(AutofocusControllerState.COMPLETED)
            self._emit_finished()
            self._transition_to(AutofocusControllerState.IDLE)
            return

        # Acquire resources for full autofocus
        if not self._acquire_resources():
            self._log.warning("Could not acquire resources, aborting autofocus")
            return

        # Transition to RUNNING
        self._transition_to(AutofocusControllerState.RUNNING)

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

        # create a QThread object
        if self._focus_thread and self._focus_thread.is_alive():
            self._keep_running.clear()
            try:
                self._focus_thread.join(1.0)
            except RuntimeError as e:
                self._log.exception("Critical error joining previous autofocus thread.")
                self._transition_to(AutofocusControllerState.FAILED)
                self._emit_finished_failed("Critical error joining previous autofocus thread")
                self._release_resources()
                self._transition_to(AutofocusControllerState.IDLE)
                raise e
            if self._focus_thread.is_alive():
                self._log.error("Previous focus thread failed to join!")
                self._transition_to(AutofocusControllerState.FAILED)
                self._emit_finished_failed("Previous focus thread failed to join")
                self._release_resources()
                self._transition_to(AutofocusControllerState.IDLE)
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

    def stop_autofocus(self) -> None:
        """Request autofocus stop and emit failure event when aborted."""
        if not self.autofocus_in_progress:
            return

        self._autofocus_abort_requested = True
        self._keep_running.clear()

        if self._focus_thread and self._focus_thread.is_alive():
            try:
                self._focus_thread.join(timeout=1.0)
            except Exception as exc:  # pragma: no cover - defensive
                self._log.debug(f"Error joining autofocus thread: {exc}")

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

        if self._autofocus_abort_requested:
            # Transition to FAILED, emit failure, then reset to IDLE
            self._transition_to(AutofocusControllerState.FAILED)
            self._emit_finished_failed("Autofocus aborted")
            self._log.info("autofocus aborted")
        else:
            # Transition to COMPLETED, emit success, then reset to IDLE
            self._transition_to(AutofocusControllerState.COMPLETED)
            self._emit_finished()
            self._log.info("autofocus finished")

        # Release resources and reset state
        self._release_resources()
        self._transition_to(AutofocusControllerState.IDLE)
        self._autofocus_abort_requested = False

    def _emit_finished(self) -> None:
        """Emit autofocus finished via callback or EventBus."""
        if self._finished_fn is not None:
            self._finished_fn()
        elif self._event_bus is not None:
            from squid.core.events import AutofocusCompleted
            # Get final Z position
            if self._stage_service:
                pos = self._stage_service.get_position()
                z_pos = pos.z_mm
            else:
                pos = self.stage.get_pos()
                z_pos = pos.z_mm
            self._event_bus.publish(AutofocusCompleted(
                success=True,
                z_position=z_pos,
                score=None,  # Score tracking would need to be added to worker
            ))

    def _emit_image(self, image: np.ndarray) -> None:
        """Emit image for display via callback or StreamHandler.

        Note: Images go through StreamHandler, not EventBus, to avoid
        overwhelming the event system with high-frequency frame data.
        """
        if self._image_to_display_fn is not None:
            self._image_to_display_fn(image)

    def _emit_finished_failed(self, error: str) -> None:
        """Emit autofocus failed via callback or EventBus."""
        if self._finished_fn is not None:
            self._finished_fn()  # Legacy callback doesn't distinguish success/failure
        elif self._event_bus is not None:
            from squid.core.events import AutofocusCompleted
            self._event_bus.publish(AutofocusCompleted(
                success=False,
                z_position=None,
                score=None,
                error=error,
            ))

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
