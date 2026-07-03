"""Transport-agnostic core service facade over the Microscope stack."""

import dataclasses
import json
import math
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import yaml as _yaml

import squid.logging
from squid_service import faults as F
from squid_service.events import EventBus
from squid_service.gui_bridge import GuiBridge
from squid_service.jobs import JobOutcome, JobResult, JobState, JobStore
from squid_service.methods import MethodRegistry
from squid_service.models import (
    AcquireRequest,
    AcquisitionRequest,
    AutofocusCorrectRequest,
    AutofocusRunRequest,
    DebugSettingsRequest,
    ExposureRequest,
    IntensityRequest,
    MoveRequest,
)
from squid_service.state import BUSY_STATES, InstrumentState, StateMachine
from squid_service.timeutil import utc_now_iso
from squid_service.wells import parse_well_names, well_center_mm

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
        methods_dir: Optional[Path] = None,
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
        self.methods = MethodRegistry(methods_dir) if methods_dir is not None else None
        self._state = StateMachine(initial_state, on_transition=self._on_state_changed)
        self._command_lock = threading.Lock()
        self._python_exec_enabled = False
        self._acq_stats = None  # last AcquisitionStats from the worker
        # Per-acquisition observer state (set on start, updated on the worker thread).
        self._api_yaml_data = None
        self._acq_t0 = time.monotonic()
        self._images_seen = 0
        self._last_progress_pub = 0.0
        if self._mpc is not None:
            self._wrap_controller_callbacks()

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

    def sample_formats(self) -> dict:
        """URS API-LAB-001: list every known sample/wellplate format and its layout.

        Mirrors ``control._def.WELLPLATE_FORMAT_SETTINGS`` (populated by
        ``read_sample_formats_csv``/``load_formats``, ~control/_def.py:1128-1170),
        accessed via the module (not a top-level import) so MCP-driven cache
        reloads are reflected without restarting the service.
        """
        import control._def

        return {
            "formats": [
                {
                    "name": name,
                    "rows": settings["rows"],
                    "cols": settings["cols"],
                    "well_spacing_mm": settings["well_spacing_mm"],
                    "well_size_mm": settings["well_size_mm"],
                    "a1_x_mm": settings["a1_x_mm"],
                    "a1_y_mm": settings["a1_y_mm"],
                }
                for name, settings in control._def.WELLPLATE_FORMAT_SETTINGS.items()
            ]
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

    # ---- acquisitions ------------------------------------------------------

    _REASON_TO_OUTCOME = {
        "completed": JobOutcome.SUCCESS,
        "user_abort": JobOutcome.ABORTED,
        "error": JobOutcome.FAILURE,
        "completed_with_errors": JobOutcome.PARTIAL,
    }

    def _wrap_controller_callbacks(self) -> None:
        """Chain our observers onto the controller's callbacks.

        Originals always run first (the GUI keeps working); our handlers never
        propagate exceptions into the acquisition worker. Wrapped ONCE at
        construction: the controller later does its own ``dataclasses.replace``
        of ``signal_acquisition_finished`` at run time, but that copies (and thus
        preserves) our already-chained callbacks.
        """
        original = self._mpc.callbacks

        def chain(first, second):
            def call(*args, **kwargs):
                try:
                    first(*args, **kwargs)
                finally:
                    try:
                        second(*args, **kwargs)
                    except Exception:
                        self._log.exception("core-service acquisition observer failed")

            return call

        self._mpc.callbacks = dataclasses.replace(
            original,
            signal_acquisition_start=chain(original.signal_acquisition_start, self._on_acq_start),
            signal_acquisition_finished=chain(original.signal_acquisition_finished, self._on_acq_finished),
            signal_new_image=chain(original.signal_new_image, self._on_new_image),
            signal_overall_progress=chain(original.signal_overall_progress, self._on_overall_progress),
            signal_slack_timepoint_notification=chain(
                original.signal_slack_timepoint_notification, self._on_timepoint_stats
            ),
            signal_slack_acquisition_finished=chain(original.signal_slack_acquisition_finished, self._on_acq_stats),
        )

    # -- observers (run on the acquisition worker thread) --

    def _on_acq_start(self, params) -> None:
        if self.state != InstrumentState.ACQUIRING:
            self._state.transition(InstrumentState.ACQUIRING)
        if self.jobs.active is None:
            # GUI-started acquisition: track it so API clients see truthful state/jobs
            self.jobs.create(experiment_id=getattr(params, "experiment_ID", None), origin="gui")
        job = self.jobs.active
        self._acq_t0 = time.monotonic()
        self._images_seen = 0
        self._last_progress_pub = 0.0
        self.jobs.mark_running(job.job_id)

    def _on_new_image(self, frame, info) -> None:
        job = self.jobs.active
        if job is None:
            return
        self._images_seen += 1
        elapsed = time.monotonic() - self._acq_t0
        self.jobs.update_progress(job.job_id, images_acquired=self._images_seen, elapsed_s=elapsed)
        now = time.monotonic()
        if now - self._last_progress_pub >= 0.5:
            self._last_progress_pub = now
            progress = self.jobs.get(job.job_id).progress
            self.events.publish("progress", {"job_id": job.job_id, **progress.model_dump()})

    def _on_overall_progress(self, update) -> None:
        job = self.jobs.active
        if job is None:
            return
        self.jobs.update_progress(
            job.job_id,
            current_region=update.current_region,
            total_regions=update.total_regions,
            current_timepoint=update.current_timepoint,
            total_timepoints=update.total_timepoints,
        )

    def _on_timepoint_stats(self, stats) -> None:
        """Per-timepoint granularity: accumulate laser-AF failures across the run
        (URS ERR-RES-001/003). Fires once per completed timepoint via Slack callback."""
        job = self.jobs.active
        if job is None:
            return
        failures = getattr(stats, "laser_af_failures", 0) or 0
        if failures:
            self.jobs.update_progress(job.job_id, af_failures=job.progress.af_failures + failures)

    def _on_acq_stats(self, stats) -> None:
        self._acq_stats = stats

    def _derive_end_reason(self) -> str:
        """Fallback used by _on_acq_finished when AcquisitionStats never arrived.

        multi_point_worker.py only calls ``signal_slack_acquisition_finished``
        (and ``signal_slack_timepoint_notification``) inside
        ``if self._slack_notifier is not None:`` blocks. With no Slack notifier
        configured -- the default -- ``self._acq_stats`` stays None and the real
        end reason (e.g. "user_abort", "error") never reaches us, so every
        acquisition would otherwise be reported as outcome SUCCESS.

        ``MultiPointWorker._compute_end_reason()`` (control/core/multi_point_worker.py:450)
        is the worker's own authoritative classification: it reads
        ``self._run_state_fatal``, ``self.abort_requested_fn()``, ``self._abort_cause``,
        and ``self._acquisition_error_count`` and returns a string -- no mutation of
        any state, so calling it again here is side-effect-free. It is invoked from
        the ``finally`` block of ``MultiPointWorker.run()`` immediately before that
        same block calls ``self.callbacks.signal_acquisition_finished()``, which is
        what (via the chaining in ``_wrap_controller_callbacks``) eventually calls
        this service's ``_on_acq_finished``. So by the time we get here the worker's
        state is already final, and ``self._mpc.multiPointWorker`` has not yet been
        cleared -- that only happens in ``MultiPointController.close()``, a separate
        shutdown path -- so the reference is still valid.
        """
        worker = getattr(self._mpc, "multiPointWorker", None)
        if worker is not None:
            try:
                return worker._compute_end_reason()
            except Exception:
                self._log.exception("worker._compute_end_reason() failed; falling back")
        return "user_abort" if getattr(self._mpc, "abort_acqusition_requested", False) else "completed"

    def _on_acq_finished(self) -> None:
        job = self.jobs.active
        stats = self._acq_stats
        self._acq_stats = None
        yaml_data = getattr(self, "_api_yaml_data", None)
        self._api_yaml_data = None

        # A run_acquisition() validation failure fires finished WITHOUT start,
        # so the job never reached RUNNING (started_at is None).
        validation_failure = job is not None and job.started_at is None
        # See _derive_end_reason: without a Slack notifier, `stats` stays None
        # even for real runs, so fall back to asking the worker directly. Skip
        # that lookup for a validation failure -- runtime_reason is unused
        # there (that branch hardcodes end_reason="error" below), and
        # multiPointWorker could still reference a *previous* completed run.
        worker = None
        if stats is not None:
            runtime_reason = getattr(stats, "reason", "completed")
        elif validation_failure:
            runtime_reason = "completed"
        else:
            worker = getattr(self._mpc, "multiPointWorker", None)
            runtime_reason = self._derive_end_reason()
        # A runtime "error" drives the instrument to ERROR (URS ERR-STATE-001/002);
        # a pre-start validation failure is recoverable and returns to INITIALIZED.
        go_error = (not validation_failure) and runtime_reason == "error"

        try:
            if self.state == InstrumentState.ACQUIRING:
                self._state.transition(InstrumentState.PROCESSING)
            if self.state == InstrumentState.PROCESSING:
                self._state.transition(InstrumentState.ERROR if go_error else InstrumentState.INITIALIZED)
        except Exception:
            self._log.exception("state transition on acquisition finish failed")

        if job is None:
            return

        if validation_failure:
            fault = self._record_fault(
                F.make_fault(
                    F.FaultCategory.ACQUISITION,
                    F.ACQUISITION_START_FAILED,
                    "Acquisition failed controller validation before starting",
                    terminal=True,
                    component="acquisition",
                )
            )
            completed = self.jobs.complete(job.job_id, JobOutcome.FAILURE, JobResult(end_reason="error"), fault=fault)
        else:
            outcome = self._REASON_TO_OUTCOME.get(runtime_reason, JobOutcome.SUCCESS)
            output_dir = None
            if self._mpc.base_path and self._mpc.experiment_ID:
                output_dir = os.path.join(self._mpc.base_path, self._mpc.experiment_ID)
            if stats is not None:
                errors_encountered = getattr(stats, "errors_encountered", 0)
                image_count_written = getattr(stats, "total_images", self._images_seen)
            else:
                # No stats (no Slack notifier): fall back to the worker's own
                # error counter and the service's own per-image counted total.
                errors_encountered = getattr(worker, "_acquisition_error_count", 0)
                image_count_written = self._images_seen
            result = JobResult(
                output_dir=output_dir,
                image_count_written=image_count_written,
                partial_write=outcome is not JobOutcome.SUCCESS,
                errors_encountered=errors_encountered,
                end_reason=runtime_reason,
            )
            # save_failures mirrors the terminal error count (URS API-POLL-001).
            progress_update = {"save_failures": errors_encountered}
            if stats is None:
                # signal_slack_timepoint_notification (which normally accumulates
                # af_failures across the run via _on_timepoint_stats) is gated the
                # same way, so without a notifier job.progress.af_failures never
                # moves off 0. Merge the worker's own running total in now.
                af_failures = getattr(worker, "_laser_af_failures", 0) or 0
                if af_failures > job.progress.af_failures:
                    progress_update["af_failures"] = af_failures
            self.jobs.update_progress(job.job_id, **progress_update)
            fault = None
            if go_error:
                fault = self._record_fault(
                    F.make_fault(
                        F.FaultCategory.ACQUISITION,
                        F.ACQUISITION_RUNTIME,
                        "Acquisition failed during the run",
                        terminal=True,
                        component="acquisition",
                    )
                )
            completed = self.jobs.complete(job.job_id, outcome, result, fault=fault)

        if yaml_data is not None:
            self._gui_bridge.set_acquisition_state(yaml_data, running=False)
        self.events.publish(
            "job_completed",
            {
                "job_id": completed.job_id,
                "outcome": completed.outcome.value if completed.outcome else None,
                "completed_at": completed.completed_at,
            },
        )

    # -- source resolution & checks --

    def _load_yaml_or_fault(self, yaml_path: str):
        from control.acquisition_yaml_loader import parse_acquisition_yaml

        if not yaml_path or not os.path.exists(yaml_path):
            raise F.FaultError(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_BAD_VALUE,
                    f"YAML file not found: {yaml_path}",
                    detail={"yaml_path": yaml_path},
                )
            )
        try:
            yaml_data = parse_acquisition_yaml(yaml_path)
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = _yaml.safe_load(f) or {}
        except F.FaultError:
            raise
        except Exception as e:
            raise F.FaultError(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_BAD_VALUE,
                    f"Failed to parse YAML: {e}",
                    detail={"yaml_path": yaml_path},
                )
            )
        return yaml_data, raw

    def _resolve_yaml_path(self, req: AcquisitionRequest) -> str:
        """Resolve a method name to its server-side YAML; a raw yaml_path passes through."""
        if req.method is not None:
            if self.methods is None:
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_CAPABILITY_MISSING,
                        "Method registry not attached; cannot run by method name",
                        detail={"method": req.method},
                    )
                )
            return str(self.methods.path_for(req.method))
        return req.yaml_path

    def _output_path_check(self, req: AcquisitionRequest, ctx: dict):
        import control._def

        def check_output_path():
            base = req.overrides.output_path or getattr(control._def, "DEFAULT_SAVING_PATH", None)
            if not base:
                raise F.FaultError(
                    F.make_fault(F.FaultCategory.IO, F.IO_GENERIC, "No output path and no DEFAULT_SAVING_PATH")
                )
            ctx["base_path"] = base
            probe = base
            while probe and not os.path.isdir(probe):
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
            if not probe or not os.access(probe, os.W_OK):
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.IO,
                        F.IO_PATH_NOT_WRITABLE,
                        f"Output path not writable: {base}",
                        detail={"output_path": base},
                    )
                )
            ctx["free_bytes"] = shutil.disk_usage(probe).free

        return check_output_path

    def _yaml_checks(self, req: AcquisitionRequest):
        """Ordered (name, callable) checks for a yaml_path/method acquisition.

        Each callable raises FaultError on failure and threads context to later
        checks via the shared ``ctx`` dict."""
        import control._def
        from control.acquisition_yaml_loader import validate_hardware

        ctx = {}

        def check_yaml():
            yaml_path = self._resolve_yaml_path(req)
            ctx["yaml_path"] = yaml_path
            ctx["yaml_data"], ctx["raw"] = self._load_yaml_or_fault(yaml_path)

        def check_widget_type():
            if ctx["yaml_data"].widget_type != "wellplate":
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.INVALID_PARAM,
                        F.INVALID_PARAM_BAD_VALUE,
                        "Only wellplate-mode YAMLs are supported by the API "
                        f"(got widget_type={ctx['yaml_data'].widget_type!r})",
                    )
                )

        def check_hardware():
            try:
                binning = tuple(self._microscope.camera.get_binning())
            except Exception:
                binning = (1, 1)
            validation = validate_hardware(
                ctx["yaml_data"], self._microscope.objective_store.current_objective, binning
            )
            if not validation.is_valid:
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_HARDWARE_MISMATCH,
                        f"Hardware configuration mismatch: {validation.message}",
                    )
                )

        def check_channels():
            objective = self._microscope.objective_store.current_objective
            available = {ch.name for ch in (self._microscope.live_controller.get_channels(objective) or [])}
            if not ctx["yaml_data"].channel_names:
                raise F.FaultError(
                    F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_BAD_VALUE, "YAML has no channels")
                )
            invalid = [ch for ch in ctx["yaml_data"].channel_names if ch not in available]
            if invalid:
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_UNKNOWN_CHANNEL,
                        f"Invalid channels: {invalid}. Available: {sorted(available)}",
                        detail={"invalid": invalid},
                    )
                )

        def check_regions():
            # sample_format override (URS API-LAB-002): validate before any hardware call.
            if req.overrides.sample_format:
                try:
                    control._def.get_wellplate_settings(req.overrides.sample_format)
                except ValueError as e:
                    raise F.FaultError(F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_BAD_VALUE, str(e)))
            if req.overrides.wells:
                fmt = req.overrides.sample_format or ctx["raw"].get("sample", {}).get(
                    "wellplate_format", "96 well plate"
                )
                try:
                    settings = control._def.get_wellplate_settings(fmt)
                    for name in parse_well_names(req.overrides.wells):
                        well_center_mm(name, settings)
                except ValueError as e:
                    raise F.FaultError(F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_BAD_VALUE, str(e)))
            elif not ctx["yaml_data"].wellplate_regions:
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.INVALID_PARAM,
                        F.INVALID_PARAM_BAD_VALUE,
                        "No regions in YAML and no wells override provided",
                    )
                )

        checks = [
            ("yaml", check_yaml),
            ("widget_type", check_widget_type),
            ("hardware", check_hardware),
            ("channels", check_channels),
            ("regions", check_regions),
            ("output_path", self._output_path_check(req, ctx)),
        ]
        return checks, ctx

    def _grid_checks(self, req: AcquisitionRequest):
        """Ordered checks for a grid acquisition (URS API-COMPAT-002 parity)."""
        import control._def

        grid = req.grid
        ctx = {}

        def check_channels():
            objective = self._microscope.objective_store.current_objective
            available = {ch.name for ch in (self._microscope.live_controller.get_channels(objective) or [])}
            invalid = [c for c in grid.channels if c not in available]
            if invalid:
                raise F.FaultError(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_UNKNOWN_CHANNEL,
                        f"Invalid channels: {invalid}. Available: {sorted(available)}",
                        detail={"invalid": invalid},
                    )
                )

        def check_wellplate_format():
            try:
                ctx["settings"] = control._def.get_wellplate_settings(grid.wellplate_format)
            except ValueError as e:
                raise F.FaultError(F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_BAD_VALUE, str(e)))

        def check_regions():
            settings = ctx.get("settings") or control._def.get_wellplate_settings(grid.wellplate_format)
            try:
                for name in parse_well_names(grid.wells):
                    well_center_mm(name, settings)
            except ValueError as e:
                raise F.FaultError(F.make_fault(F.FaultCategory.INVALID_PARAM, F.INVALID_PARAM_BAD_VALUE, str(e)))

        checks = [
            ("channels", check_channels),
            ("wellplate_format", check_wellplate_format),
            ("regions", check_regions),
            ("output_path", self._output_path_check(req, ctx)),
        ]
        return checks, ctx

    def _acquisition_checks(self, req: AcquisitionRequest):
        if req.grid is not None:
            return self._grid_checks(req)
        return self._yaml_checks(req)

    def _run_checks_report(self, checks, ctx, skip_names=()) -> dict:
        """Run checks, never raising for a check failure; report each as ok/failed/skipped.

        Once the ``yaml`` check fails there is no parsed YAML for the later checks to
        read, so they are reported "skipped" rather than crashing on missing context.
        """
        results = []
        ok = True
        yaml_failed = False
        for name, fn in checks:
            if name in skip_names:
                continue
            if yaml_failed:
                results.append({"name": name, "ok": False, "message": "skipped (yaml check failed)"})
                continue
            try:
                fn()
                results.append({"name": name, "ok": True, "message": ""})
            except F.FaultError as e:
                ok = False
                yaml_failed = yaml_failed or name == "yaml"
                results.append({"name": name, "ok": False, "message": e.fault.message})
            except Exception as e:
                ok = False
                yaml_failed = yaml_failed or name == "yaml"
                results.append({"name": name, "ok": False, "message": str(e)})
        return {"ok": ok, "checks": results, "free_bytes": ctx.get("free_bytes")}

    def preflight(self, req: AcquisitionRequest) -> dict:
        checks, ctx = self._acquisition_checks(req)
        return self._run_checks_report(checks, ctx)

    # -- controller configuration --

    def _configure_regions(self, yaml_data, raw: dict, wells_override, sample_format_override) -> None:
        import control._def

        sc = self._scan_coordinates
        sc.clear_regions()
        current_z = self._microscope.stage.get_pos().z_mm
        scan_size = yaml_data.scan_size_mm or 2.0
        shape = yaml_data.scan_shape or "Square"
        if wells_override:
            fmt = sample_format_override or raw.get("sample", {}).get("wellplate_format", "96 well plate")
            settings = control._def.get_wellplate_settings(fmt)
            for name in parse_well_names(wells_override):
                x, y = well_center_mm(name, settings)
                sc.add_region(
                    well_id=name,
                    center_x=x,
                    center_y=y,
                    scan_size_mm=scan_size,
                    overlap_percent=yaml_data.overlap_percent,
                    shape=shape,
                )
                if name in sc.region_centers:
                    sc.region_centers[name][2] = current_z
        else:
            for region in yaml_data.wellplate_regions:
                name = region.get("name", "region")
                center = region.get("center_mm", [0, 0, 0])
                sc.add_region(
                    well_id=name,
                    center_x=center[0],
                    center_y=center[1],
                    scan_size_mm=scan_size,
                    overlap_percent=yaml_data.overlap_percent,
                    shape=region.get("shape", shape),
                )
                if name in sc.region_centers:
                    sc.region_centers[name][2] = center[2] if len(center) > 2 else current_z
        sc.sort_coordinates()

    def _configure_grid_regions(self, grid) -> None:
        import control._def

        sc = self._scan_coordinates
        sc.clear_regions()
        current_z = self._microscope.stage.get_pos().z_mm
        settings = control._def.get_wellplate_settings(grid.wellplate_format)
        for name in parse_well_names(grid.wells):
            x, y = well_center_mm(name, settings)
            sc.add_flexible_region(
                region_id=name,
                center_x=x,
                center_y=y,
                center_z=current_z,
                Nx=grid.nx,
                Ny=grid.ny,
                overlap_percent=grid.overlap_percent,
            )
        sc.sort_coordinates()

    def _configure_controller(self, yaml_data) -> None:
        self._mpc.set_NX(1)
        self._mpc.set_NY(1)
        self._mpc.set_NZ(yaml_data.nz)
        self._mpc.set_deltaZ(yaml_data.delta_z_um)
        self._mpc.set_Nt(yaml_data.nt)
        self._mpc.set_deltat(yaml_data.delta_t_s)
        self._mpc.do_autofocus = yaml_data.contrast_af
        self._mpc.do_reflection_af = yaml_data.laser_af
        self._mpc.use_piezo = yaml_data.use_piezo
        self._mpc.set_selected_configurations(yaml_data.channel_names)

    def _configure_grid_controller(self, grid) -> None:
        self._mpc.set_NX(1)
        self._mpc.set_NY(1)
        self._mpc.set_NZ(1)
        self._mpc.set_deltaZ(1.0)
        self._mpc.set_Nt(1)
        self._mpc.set_deltat(0.0)
        self._mpc.do_autofocus = False
        self._mpc.do_reflection_af = False
        self._mpc.use_piezo = False
        self._mpc.set_selected_configurations(grid.channels)

    def _apply_autofocus_override(self, req: AcquisitionRequest) -> None:
        """URS API-ACQ-003: request overrides beat both YAML and grid defaults."""
        if req.autofocus:
            if req.autofocus.reflection is not None:
                self._mpc.do_reflection_af = req.autofocus.reflection
            if req.autofocus.contrast is not None:
                self._mpc.do_autofocus = req.autofocus.contrast

    def _write_api_request_json(self, req: AcquisitionRequest, output_dir: str, source: str) -> None:
        """URS ERR-OBS-003/005: persist the originating request alongside the data.
        Log-and-continue on failure so a write hiccup never blocks acquisition."""
        try:
            payload = {
                "operator": req.operator,
                "scheduler_job_id": req.scheduler_job_id,
                "experiment_id": self._mpc.experiment_ID,
                "source": source,
                "api_version": API_VERSION,
                "software_version": self.version().get("software_version"),
                "firmware_version": self._firmware_version_str(),
                "accepted_at": utc_now_iso(),
            }
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "api_request.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            self._log.warning(f"Could not write api_request.json: {e}")

    # -- start / jobs --

    def start_acquisition(self, req: AcquisitionRequest) -> dict:
        if self._mpc is None or self._scan_coordinates is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.CONFIG,
                    F.CONFIG_CAPABILITY_MISSING,
                    "Acquisition controller not attached to the core service",
                )
            )
        with self._exclusive("acquisition"):
            if self._mpc.acquisition_in_progress():
                self._fail(
                    F.make_fault(
                        F.FaultCategory.PROTOCOL,
                        F.PROTOCOL_WRONG_STATE,
                        "Acquisition already in progress",
                        detail={"current_state": self.state.value},
                    )
                )
            checks, ctx = self._acquisition_checks(req)
            try:
                for _, fn in checks:
                    fn()
            except F.FaultError as e:
                raise F.FaultError(self._record_fault(e.fault))
            base_path = ctx["base_path"]

            if req.grid is not None:
                grid = req.grid
                self._configure_grid_regions(grid)
                self._configure_grid_controller(grid)
                channel_count = len(grid.channels)
                nz, nt = 1, 1
                source = "grid"
                yaml_data = None
            else:
                yaml_data, raw = ctx["yaml_data"], ctx["raw"]
                self._configure_regions(yaml_data, raw, req.overrides.wells, req.overrides.sample_format)
                self._configure_controller(yaml_data)
                channel_count = len(yaml_data.channel_names)
                nz, nt = yaml_data.nz, yaml_data.nt
                source = req.method if req.method is not None else req.yaml_path

            self._apply_autofocus_override(req)
            self._mpc.set_base_path(base_path)
            self._mpc.start_new_experiment(req.experiment_id or "api_acquisition")
            output_dir = os.path.join(base_path, self._mpc.experiment_ID)
            self._write_api_request_json(req, output_dir, source)

            total_fovs = sum(len(v) for v in self._scan_coordinates.region_fov_coordinates.values())
            total_images = total_fovs * channel_count * nz * nt
            job = self.jobs.create(
                experiment_id=self._mpc.experiment_ID,
                origin="api",
                expected_total_images=total_images,
                expected_total_regions=len(self._scan_coordinates.region_fov_coordinates),
                expected_total_timepoints=nt,
                operator=req.operator,
                scheduler_job_id=req.scheduler_job_id,
            )
            self._api_yaml_data = yaml_data
            if yaml_data is not None:
                self._gui_bridge.sync_yaml_to_widgets(yaml_data, ctx.get("yaml_path"))
                self._gui_bridge.set_acquisition_state(yaml_data, running=True)
            self._state.transition(InstrumentState.ACQUIRING)
            try:
                self._mpc.run_acquisition()
            except Exception as e:
                if self.state == InstrumentState.ACQUIRING:
                    self._state.transition(InstrumentState.INITIALIZED)
                fault = self._record_fault(
                    F.make_fault(
                        F.FaultCategory.ACQUISITION,
                        F.ACQUISITION_START_FAILED,
                        f"Failed to start acquisition: {e}",
                        terminal=True,
                        component="acquisition",
                    )
                )
                self.jobs.complete(job.job_id, JobOutcome.FAILURE, JobResult(end_reason="error"), fault=fault)
                raise F.FaultError(fault)
            return {
                "job_id": job.job_id,
                "kind": "acquisition",
                "experiment_id": self._mpc.experiment_ID,
                "expected_fov_count": total_fovs,
                "expected_image_count": total_images,
                "output_dir": output_dir,
                "accepted_at": job.accepted_at,
            }

    def get_job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_UNKNOWN_RESOURCE,
                    f"Unknown job: {job_id}",
                    detail={"job_id": job_id},
                )
            )
        return job.model_dump()

    def last_job(self) -> dict:
        job = self.jobs.last
        if job is None:
            self._fail(F.make_fault(F.FaultCategory.PROTOCOL, F.PROTOCOL_UNKNOWN_RESOURCE, "No completed job yet"))
        return job.model_dump()

    def abort_job(self, job_id: str, timeout_s: float = 60.0) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_UNKNOWN_RESOURCE,
                    f"Unknown job: {job_id}",
                    detail={"job_id": job_id},
                )
            )
        if job.state == JobState.COMPLETED:
            return {"clean": job.outcome == JobOutcome.ABORTED, "timed_out": False, "job": job.model_dump()}
        self._mpc.request_abort_aquisition()  # controller API is misspelled; do not "fix"
        finished = self.jobs.wait(job_id, timeout_s=timeout_s)
        final = self.jobs.get(job_id)
        return {
            "clean": bool(finished and final.outcome == JobOutcome.ABORTED),
            "timed_out": not finished,
            "job": final.model_dump(),
        }

    # -- named method registry (URS API-METH-001..005) --

    def _require_methods(self) -> None:
        if self.methods is None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.CONFIG,
                    F.CONFIG_CAPABILITY_MISSING,
                    "Method registry not attached to the core service",
                )
            )

    def list_methods(self) -> dict:
        self._require_methods()
        return {"methods": self.methods.list()}

    def get_method(self, name: str) -> dict:
        self._require_methods()
        try:
            return self.methods.get(name)
        except F.FaultError as e:
            raise F.FaultError(self._record_fault(e.fault))

    def create_method(self, name: str, config: dict) -> dict:
        self._require_methods()
        try:
            self.methods.save(name, config, overwrite=False)
        except F.FaultError as e:
            raise F.FaultError(self._record_fault(e.fault))
        return {"name": name, "created": True}

    def update_method(self, name: str, config: dict) -> dict:
        self._require_methods()
        try:
            self.methods.save(name, config, overwrite=True)
        except F.FaultError as e:
            raise F.FaultError(self._record_fault(e.fault))
        return {"name": name, "updated": True}

    def delete_method(self, name: str) -> dict:
        self._require_methods()
        if self.jobs.active is not None:
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_WRONG_STATE,
                    "Cannot delete a method while an acquisition is active",
                    detail={"method": name},
                )
            )
        try:
            self.methods.delete(name)
        except F.FaultError as e:
            raise F.FaultError(self._record_fault(e.fault))
        return {"name": name, "deleted": True}

    def validate_method(self, name: str) -> dict:
        """URS API-METH-004: run the yaml/widget/hardware/channels/regions checks
        (not output_path) against a stored method and return the preflight-style list."""
        self._require_methods()
        try:
            path = str(self.methods.path_for(name))
        except F.FaultError as e:
            raise F.FaultError(self._record_fault(e.fault))
        checks, ctx = self._yaml_checks(AcquisitionRequest(yaml_path=path))
        return self._run_checks_report(checks, ctx, skip_names={"output_path"})

    # ---- debug ---------------------------------------------------------------

    def set_python_exec_enabled(self, enabled: bool) -> None:
        self._python_exec_enabled = enabled
        (self._log.warning if enabled else self._log.info)(f"python_exec {'ENABLED' if enabled else 'disabled'}")

    def python_exec_status(self) -> dict:
        return {"enabled": self._python_exec_enabled}

    def python_exec(self, code: str) -> dict:
        """Execute arbitrary Python with the microscope objects in scope.

        NOT SANDBOXED. Gated by the GUI opt-in toggle; the service refuses when
        disabled. Only expose this endpoint on loopback binds.
        """
        import tempfile

        import numpy as np

        if not self._python_exec_enabled:
            self._fail(
                F.make_fault(
                    F.FaultCategory.PROTOCOL,
                    F.PROTOCOL_FORBIDDEN,
                    "python_exec is disabled; enable it via Settings in the GUI",
                )
            )
        namespace = {
            "microscope": self._microscope,
            "stage": self._microscope.stage,
            "camera": self._microscope.camera,
            "live_controller": self._microscope.live_controller,
            "objective_store": self._microscope.objective_store,
            "multipoint_controller": self._mpc,
            "scan_coordinates": self._scan_coordinates,
            "np": np,
            "result": None,
            "image": None,
        }
        try:
            exec(code, namespace)  # noqa: S102 - intentionally unsandboxed, opt-in debug tool
        except Exception as e:
            self._fail(
                F.make_fault(
                    F.FaultCategory.INVALID_PARAM,
                    F.INVALID_PARAM_BAD_VALUE,
                    f"python_exec failed: {e}",
                    detail={"exception": type(e).__name__},
                )
            )
        response = {}
        result = namespace.get("result")
        if result is not None:
            try:
                json.dumps(result)
                response["result"] = result
            except (TypeError, ValueError):
                response["result"] = str(result)
        image = namespace.get("image")
        if image is not None and isinstance(image, np.ndarray):
            path = os.path.join(tempfile.gettempdir(), "squid_python_exec_image.tiff")
            try:
                import tifffile

                tifffile.imwrite(path, image)
            except ImportError:
                path = path.replace(".tiff", ".npy")
                np.save(path, image)
            response["image_path"] = path
            response["image_shape"] = list(image.shape)
            response["image_dtype"] = str(image.dtype)
        return response

    def debug_settings(self) -> dict:
        """URS API-COMPAT-002 delta: REST parity for the legacy TCP view/performance
        debug commands (_cmd_get_view_settings / _cmd_get_performance_mode).
        `performance_mode` is None when no GUI is attached (headless service).

        Note: the legacy `display_plate_view` field is intentionally not
        reproduced here -- `control._def.DISPLAY_PLATE_VIEW` no longer exists in
        this codebase; plate view was unified into the mosaic view
        (UnifiedMosaicWidget), governed solely by `display_mosaic_view`.
        """
        import control._def

        return {
            "performance_mode": self._gui_bridge.get_performance_mode(),
            "save_downsampled_well_images": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
            "display_mosaic_view": control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY,
        }

    def set_debug_settings(self, req: DebugSettingsRequest) -> dict:
        """URS API-COMPAT-002 delta: REST parity for the legacy TCP
        _cmd_set_view_settings / _cmd_set_performance_mode commands. View settings
        are applied directly to `control._def` (module import, so MCP-driven
        reloads and other readers see the change immediately); `performance_mode`
        is dispatched fire-and-forget to the GUI thread (see GuiBridge.set_performance_mode)
        and so may not be reflected in the returned snapshot yet.
        """
        import control._def

        if req.performance_mode is not None:
            if not self._gui_bridge.has_gui:
                self._fail(
                    F.make_fault(
                        F.FaultCategory.CONFIG,
                        F.CONFIG_CAPABILITY_MISSING,
                        "No GUI attached; cannot set performance_mode",
                        component="debug",
                    )
                )
            self._gui_bridge.set_performance_mode(req.performance_mode)

        if req.save_downsampled_well_images is not None:
            control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = req.save_downsampled_well_images

        if req.display_mosaic_view is not None:
            control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = req.display_mosaic_view

        return self.debug_settings()
