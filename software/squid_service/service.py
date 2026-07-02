"""Transport-agnostic core service facade over the Microscope stack."""

import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import squid.logging
from squid_service import faults as F
from squid_service.events import EventBus
from squid_service.gui_bridge import GuiBridge
from squid_service.jobs import JobStore
from squid_service.models import (
    AcquireRequest,
    AutofocusCorrectRequest,
    AutofocusRunRequest,
    ExposureRequest,
    IntensityRequest,
    MoveRequest,
)
from squid_service.state import BUSY_STATES, InstrumentState, StateMachine
from squid_service.timeutil import utc_now_iso

API_VERSION = "v1"


class SquidCoreService:
    def __init__(
        self,
        microscope,
        multipoint_controller=None,
        scan_coordinates=None,
        gui_bridge: Optional[GuiBridge] = None,
        simulation: bool = False,
        initial_state: InstrumentState = InstrumentState.INITIALIZED,
        job_persist_path: Optional[Path] = None,
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._microscope = microscope
        self._mpc = multipoint_controller
        self._scan_coordinates = scan_coordinates
        self._gui_bridge = gui_bridge or GuiBridge(None)
        self._simulation = simulation
        self.events = EventBus()
        self.fault_log = F.FaultLog()
        self.jobs = JobStore(persist_path=job_persist_path)
        self._state = StateMachine(initial_state, on_transition=self._on_state_changed)
        self._command_lock = threading.Lock()
        self._python_exec_enabled = False
        self._acq_stats = None  # last AcquisitionStats from the worker
        if self._mpc is not None:
            self._wrap_controller_callbacks()  # implemented in Task 9

    # ---- infrastructure -------------------------------------------------

    @property
    def state(self) -> InstrumentState:
        return self._state.state

    def _on_state_changed(self, old: InstrumentState, new: InstrumentState) -> None:
        self.events.publish("state_changed", {"old": old.value, "new": new.value, "at": utc_now_iso()})

    def _record_fault(self, fault: F.Fault) -> F.Fault:
        stamped = self.fault_log.record(fault)
        self.events.publish("fault", stamped.model_dump())
        return stamped

    def _fail(self, fault: F.Fault):
        raise F.FaultError(self._record_fault(fault))

    @contextmanager
    def _exclusive(self, component: str):
        """Serialize state-changing commands; reject when the instrument is busy (spec §3)."""
        if not self._command_lock.acquire(blocking=False):
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_WRONG_STATE,
                    "Another state-changing command is in flight",
                    component=component,
                    detail={"current_state": self.state.value},
                )
            )
        try:
            if self.state in BUSY_STATES:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.PROTOCOL,
                        F.PROTOCOL_WRONG_STATE,
                        f"Command not allowed while {self.state.value}",
                        component=component,
                        detail={"current_state": self.state.value},
                    )
                )
            yield
        finally:
            self._command_lock.release()

    # ---- system ----------------------------------------------------------

    def status(self) -> dict:
        active = self.jobs.active
        last = self.jobs.last
        latest_fault = self.fault_log.latest
        result = {
            "state": self.state.value,
            "current_job_id": active.job_id if active else None,
            "latest_fault": latest_fault.model_dump() if latest_fault else None,
            "last_acquisition": (
                {
                    "job_id": last.job_id,
                    "outcome": last.outcome.value if last.outcome else None,
                    "completed_at": last.completed_at,
                }
                if last and last.completed_at
                else None
            ),
            "session_id": self.events.session_id,
            "server_time": utc_now_iso(),
        }
        if active is not None:
            result["acquisition"] = active.progress.model_dump()
        return result

    def heartbeat(self) -> dict:
        return {"alive": True, "monotonic_ns": time.monotonic_ns(), "state": self.state.value}

    def _firmware_version_str(self) -> str:
        try:
            firmware_version = self._microscope.low_level_drivers.microcontroller.firmware_version
            return f"{firmware_version[0]}.{firmware_version[1]}"
        except Exception:
            return "unknown"

    def capabilities(self) -> dict:
        from control.utils import get_squid_repo_state_description

        scope = self._microscope
        objective = scope.objective_store.current_objective
        stage_config = scope.stage.get_config()
        channels = scope.live_controller.get_channels(objective) or []
        return {
            "channels": [{"name": ch.name} for ch in channels],
            "objectives": [
                {"name": name, "magnification": info.get("magnification"), "na": info.get("NA")}
                for name, info in scope.objective_store.objectives_dict.items()
            ],
            "current_objective": objective,
            "stage": {
                "x_range_mm": [stage_config.X_AXIS.MIN_POSITION, stage_config.X_AXIS.MAX_POSITION],
                "y_range_mm": [stage_config.Y_AXIS.MIN_POSITION, stage_config.Y_AXIS.MAX_POSITION],
                "z_range_mm": [stage_config.Z_AXIS.MIN_POSITION, stage_config.Z_AXIS.MAX_POSITION],
            },
            "camera": {
                "model": type(scope.camera).__name__,
                "sensor_size_px": list(scope.camera.get_resolution()),
                "pixel_size_um": scope.camera.get_pixel_size_binned_um(),
            },
            "reflection_af_hardware": scope.addons.camera_focus is not None,
            "simulation": self._simulation,
            "api_version": API_VERSION,
            # URS API-DESC-002: surface the same version info as version().
            "software_version": get_squid_repo_state_description(),
            "firmware_version": self._firmware_version_str(),
        }

    def version(self) -> dict:
        from control.utils import get_squid_repo_state_description

        return {
            "software_version": get_squid_repo_state_description(),
            "api_version": API_VERSION,
            "firmware_version": self._firmware_version_str(),
        }

    def initialize(self, home: bool = False) -> dict:
        """Recover to INITIALIZED, verifying read-only subsystem access (URS API-LIFE-002).

        With `home=False` and the instrument already INITIALIZED, this is a
        pure no-op (no probes, no state transition). Otherwise it transitions
        through INITIALIZING, probes stage/camera/mcu, optionally homes, then
        returns to INITIALIZED. A probe failure moves the instrument to ERROR
        and raises a HARDWARE_FAULT naming the failed component.
        """
        started = time.monotonic()
        if self.state == InstrumentState.INITIALIZED and not home:
            return {
                "state": self.state.value,
                "no_op": True,
                "duration_s": time.monotonic() - started,
                "verified_components": [],
                "home_performed": False,
            }

        if not self._command_lock.acquire(blocking=False):
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_WRONG_STATE,
                    "Another state-changing command is in flight",
                    component="system",
                    detail={"current_state": self.state.value},
                )
            )
        try:
            if self.state not in (InstrumentState.INITIALIZED, InstrumentState.ERROR):
                self._fail(
                    F.make_fault(
                        F.FaultCategory.PROTOCOL,
                        F.PROTOCOL_WRONG_STATE,
                        f"initialize not allowed from {self.state.value}",
                        detail={"current_state": self.state.value},
                    )
                )
            self._state.transition(InstrumentState.INITIALIZING)

            verified_components = []
            probes = (
                ("stage", lambda: self._microscope.stage.get_pos()),
                ("camera", lambda: self._microscope.camera.get_resolution()),
                ("mcu", lambda: self._microscope.low_level_drivers.microcontroller.firmware_version),
            )
            for component, probe in probes:
                try:
                    probe()
                except Exception as e:
                    self._state.transition(InstrumentState.ERROR)
                    self._fail(
                        F.make_fault(
                            F.FaultCategory.HARDWARE_FAULT,
                            F.HARDWARE_FAULT_GENERIC,
                            f"{component} probe failed during initialize: {e}",
                            component=component,
                            detail={"verified_components": list(verified_components)},
                        )
                    )
                verified_components.append(component)

            home_performed = False
            if home:
                self._microscope.home_xyz()
                home_performed = True

            self._state.transition(InstrumentState.INITIALIZED)
            return {
                "state": self.state.value,
                "no_op": False,
                "duration_s": time.monotonic() - started,
                "verified_components": verified_components,
                "home_performed": home_performed,
            }
        finally:
            self._command_lock.release()

    def reset(self) -> dict:
        started = time.monotonic()
        if self.state == InstrumentState.INITIALIZED:
            return {"state": self.state.value, "no_op": True, "duration_s": time.monotonic() - started}
        if self.state == InstrumentState.ERROR:
            if self._mpc is not None and self._mpc.acquisition_in_progress():
                self._mpc.request_abort_aquisition()  # misspelling is the real API
            self._state.transition(InstrumentState.RECOVERING)
            self._state.transition(InstrumentState.INITIALIZED)
            return {"state": self.state.value, "no_op": False, "duration_s": time.monotonic() - started}
        self._fail(
            F.make_fault(
                F.FaultCategory.PROTOCOL,
                F.PROTOCOL_WRONG_STATE,
                f"reset not allowed from {self.state.value}",
                detail={"current_state": self.state.value},
            )
        )

    def faults_since(self, seq: int, limit: int = 100) -> dict:
        return {"faults": [f.model_dump() for f in self.fault_log.since(seq, limit)]}

    # ---- motion ----------------------------------------------------------

    def get_position(self) -> dict:
        pos = self._microscope.stage.get_pos()
        return {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}

    def _check_limits(self, axis: str, target: float) -> None:
        cfg = self._microscope.stage.get_config()
        axis_cfg = {"x": cfg.X_AXIS, "y": cfg.Y_AXIS, "z": cfg.Z_AXIS}[axis]
        if not (axis_cfg.MIN_POSITION <= target <= axis_cfg.MAX_POSITION):
            self._fail(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_OUT_OF_RANGE,
                    f"{axis} target {target:.3f} mm outside " f"[{axis_cfg.MIN_POSITION}, {axis_cfg.MAX_POSITION}]",
                    component=f"stage.{axis}",
                    detail={"axis": axis, "target_mm": target},
                )
            )

    def move(self, req: MoveRequest) -> dict:
        with self._exclusive("stage"):
            pos = self._microscope.stage.get_pos()
            if req.mode == "absolute":
                targets = {"x": req.x, "y": req.y, "z": req.z}
            else:
                targets = {
                    "x": pos.x_mm + req.x if req.x is not None else None,
                    "y": pos.y_mm + req.y if req.y is not None else None,
                    "z": pos.z_mm + req.z if req.z is not None else None,
                }
            for axis, target in targets.items():
                if target is not None:
                    self._check_limits(axis, target)
            blocking = req.block_until_complete
            if targets["x"] is not None:
                self._microscope.move_x_to(targets["x"], blocking=blocking)
            if targets["y"] is not None:
                self._microscope.move_y_to(targets["y"], blocking=blocking)
            if targets["z"] is not None:
                self._microscope.move_z_to(targets["z"], blocking=blocking)
            return {"position": self.get_position()}

    def home(self) -> dict:
        with self._exclusive("stage"):
            self._microscope.home_xyz()
            return {"homed": True, "position": self.get_position()}

    # ---- imaging ----------------------------------------------------------

    def _channel_or_fail(self, name: str):
        objective = self._microscope.objective_store.current_objective
        channel = self._microscope.live_controller.get_channel_by_name(objective, name)
        if channel is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.CONFIG,
                    F.CONFIG_UNKNOWN_CHANNEL,
                    f"Channel {name!r} not found for objective {objective!r}",
                    detail={"channel": name, "objective": objective},
                )
            )
        return channel

    def list_channels(self) -> dict:
        objective = self._microscope.objective_store.current_objective
        channels = self._microscope.live_controller.get_channels(objective) or []
        return {
            "objective": objective,
            "channels": [
                {
                    "name": ch.name,
                    "exposure_ms": ch.exposure_time,
                    "intensity": ch.illumination_intensity,
                }
                for ch in channels
            ],
        }

    def select_channel(self, name: str) -> dict:
        with self._exclusive("imaging"):
            channel = self._channel_or_fail(name)
            self._microscope.live_controller.set_microscope_mode(channel)
            return {"channel": name, "objective": self._microscope.objective_store.current_objective}

    def set_exposure(self, req: ExposureRequest) -> dict:
        with self._exclusive("imaging"):
            if req.channel is not None:
                self._channel_or_fail(req.channel)
                self._microscope.set_exposure_time(req.channel, req.exposure_ms)
            else:
                self._microscope.camera.set_exposure_time(req.exposure_ms)
            return {"exposure_ms": req.exposure_ms, "channel": req.channel}

    def set_intensity(self, req: IntensityRequest) -> dict:
        with self._exclusive("imaging"):
            self._channel_or_fail(req.channel)
            self._microscope.set_illumination_intensity(req.channel, req.intensity)
            return {"channel": req.channel, "intensity": req.intensity}

    def illumination(self, on: bool) -> dict:
        with self._exclusive("imaging"):
            if on:
                self._microscope.live_controller.turn_on_illumination()
            else:
                self._microscope.live_controller.turn_off_illumination()
            return {"illumination": "on" if on else "off"}

    def get_objectives(self) -> dict:
        store = self._microscope.objective_store
        return {"objectives": list(store.objectives_dict.keys()), "current": store.current_objective}

    def set_objective(self, name: str) -> dict:
        with self._exclusive("imaging"):
            if name not in self._microscope.objective_store.objectives_dict:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_UNKNOWN_OBJECTIVE,
                        f"Objective {name!r} not found",
                        detail={"objective": name},
                    )
                )
            self._microscope.set_objective(name)
            changer = self._microscope.addons.objective_changer
            if changer is not None:
                changer.move_to_objective(name)
            return {"objective": name}

    def acquire(self, req: AcquireRequest) -> dict:
        with self._exclusive("imaging"):
            if req.channel is not None:
                channel = self._channel_or_fail(req.channel)
                self._microscope.live_controller.set_microscope_mode(channel)
            try:
                image = self._microscope.acquire_image()
            except RuntimeError as e:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.HARDWARE_TRANSIENT,
                        F.HARDWARE_TRANSIENT_TIMEOUT,
                        f"Image acquisition failed: {e}",
                        recoverable=True,
                        scheduler_action=F.SchedulerAction.RETRY,
                        component="camera",
                    )
                )
            result = {"acquired": True, "shape": list(image.shape), "dtype": str(image.dtype)}
            if req.save_path:
                directory = os.path.dirname(req.save_path) or "."
                if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
                    self._fail(
                        F.make_fault(
                            F.FaultCategory.IO,
                            F.IO_PATH_NOT_WRITABLE,
                            f"Directory not writable: {directory}",
                            detail={"path": req.save_path},
                        )
                    )
                result["saved_to"] = self._microscope.save_image(image, req.save_path)
            return result

    def live(self, start: bool) -> dict:
        with self._exclusive("imaging"):
            if start:
                self._microscope.start_live()
            else:
                self._microscope.stop_live()
            return {"live": start}

    # ---- autofocus ---------------------------------------------------------

    def _require_af_hardware(self) -> None:
        if self._microscope.addons.camera_focus is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.CONFIG,
                    F.CONFIG_CAPABILITY_MISSING,
                    "No reflection-AF hardware on this instrument",
                    component="autofocus",
                )
            )

    def _af_controller_or_fail(self):
        """Guard used by the reference/correction ops (URS API-AF-002/003):
        hardware must be present and the controller must already be constructed
        and initialized (autofocus_run/perform_laser_af is what lazily builds it).
        """
        self._require_af_hardware()
        controller = self._microscope.laser_autofocus_controller
        if controller is None or not controller.is_initialized:
            self._fail(
                F.make_fault(
                    F.FaultCategory.AUTOFOCUS,
                    F.AUTOFOCUS_NOT_READY,
                    "Laser autofocus controller is not initialized",
                    recoverable=True,
                    scheduler_action=F.SchedulerAction.RETRY,
                    component="autofocus",
                )
            )
        return controller

    def autofocus_status(self) -> dict:
        """URS API-AF-001. `reference_set` mirrors `laser_af_properties.has_reference`,
        the flag `LaserAutofocusController.set_reference()` sets to True and which
        `move_to_target()` itself checks before running - i.e. the controller's own
        notion of "a reference is usable", as opposed to the raw `reference_crop`
        ndarray it also stores (which is an implementation detail used only for the
        cross-correlation alignment check).
        """
        controller = self._microscope.laser_autofocus_controller
        available = self._microscope.addons.camera_focus is not None
        initialized = bool(controller.is_initialized) if controller is not None else False
        reference_set = bool(controller.laser_af_properties.has_reference) if controller is not None else False

        if not available:
            readiness = "NO_HARDWARE"
        elif not initialized:
            readiness = "NOT_INITIALIZED"
        elif not reference_set:
            readiness = "NO_REFERENCE"
        else:
            readiness = "OK"

        return {
            "available": available,
            "initialized": initialized,
            "reference_set": reference_set,
            "readiness": readiness,
        }

    def autofocus_run(self, req: AutofocusRunRequest) -> dict:
        with self._exclusive("autofocus"):
            self._require_af_hardware()
            try:
                ok = self._microscope.perform_laser_af(req.target_um)
            except RuntimeError as e:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.AUTOFOCUS,
                        F.AUTOFOCUS_NOT_READY,
                        str(e),
                        recoverable=True,
                        scheduler_action=F.SchedulerAction.RETRY,
                        component="autofocus",
                    )
                )
            if not ok:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.AUTOFOCUS,
                        F.AUTOFOCUS_FAILURE,
                        "Reflection autofocus did not converge",
                        recoverable=True,
                        scheduler_action=F.SchedulerAction.RETRY,
                        component="autofocus",
                    )
                )
            return {"autofocus": "ok", "position": self.get_position()}

    def autofocus_store_reference(self) -> dict:
        """URS API-AF-002: capture the current laser spot as the new reference."""
        with self._exclusive("autofocus"):
            controller = self._af_controller_or_fail()
            reference_set = controller.set_reference()
            return {"reference_set": bool(reference_set)}

    def autofocus_correct(self, req: AutofocusCorrectRequest) -> dict:
        """URS API-AF-003: measure drift from the stored reference and correct it
        if it's within `threshold_um`; otherwise report it without moving so the
        caller can decide (e.g. re-run initialize_auto/set_reference).
        """
        with self._exclusive("autofocus"):
            controller = self._af_controller_or_fail()
            displacement_um = controller.measure_displacement()
            if math.isnan(displacement_um):
                self._fail(
                    F.make_fault(
                        F.FaultCategory.AUTOFOCUS,
                        F.AUTOFOCUS_FAILURE,
                        "Failed to measure laser AF displacement",
                        recoverable=True,
                        scheduler_action=F.SchedulerAction.RETRY,
                        component="autofocus",
                    )
                )
            if abs(displacement_um) > req.threshold_um:
                return {"corrected": False, "displacement_um": displacement_um}
            controller.move_to_target(0.0)
            controller.set_reference()
            return {"corrected": True, "displacement_um": displacement_um}

    # --- acquisitions (Task 9) ---
