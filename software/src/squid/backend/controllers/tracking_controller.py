from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING, Tuple, List

import numpy as np

import squid.core.logging
from squid.core.mode_gate import GlobalMode, GlobalModeGate
from squid.core.events import (
    EventBus,
    ObjectiveChanged,
    SetTrackingParametersCommand,
    SetTrackingPathCommand,
    SetTrackingChannelsCommand,
    StartTrackingExperimentCommand,
    StartTrackingCommand,
    StopTrackingCommand,
    TrackingStateChanged,
    TrackingWorkerFinished,
)
from squid.backend.processing.tracking_dasiamrpn import Tracker_Image
from squid.backend.io.utils_acquisition import save_image

if TYPE_CHECKING:
    from squid.core.utils.config_utils import ChannelMode
    from squid.backend.controllers.live_controller import LiveController
    from squid.backend.services import CameraService, StageService, PeripheralService
    from squid.backend.managers.channel_configuration_manager import (
        ChannelConfigurationManager,
    )
    from squid.backend.managers.objective_store import ObjectiveStore


@dataclass(frozen=True)
class _TrackingRunConfig:
    experiment_id: str
    base_path: str
    configuration: "ChannelMode"
    roi_bbox: Tuple[int, int, int, int]
    time_interval_s: float
    enable_stage_tracking: bool
    save_images: bool
    tracker_type: str
    pixel_size_um: float
    image_resizing_factor: float


class TrackingControllerCore:
    """Backend-only tracking controller (no Qt, services-only hardware access)."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        camera_service: "CameraService",
        stage_service: "StageService",
        live_controller: "LiveController",
        channel_config_manager: "ChannelConfigurationManager",
        objective_store: "ObjectiveStore",
        peripheral_service: Optional["PeripheralService"] = None,
        mode_gate: Optional[GlobalModeGate] = None,
    ) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._bus = event_bus
        self._camera = camera_service
        self._stage = stage_service
        self._live = live_controller
        self._configs = channel_config_manager
        self._objective_store = objective_store
        self._peripheral = peripheral_service
        self._mode_gate = mode_gate

        self._lock = threading.RLock()

        self._base_path: Optional[str] = None
        self._experiment_id_base: Optional[str] = None
        self._selected_configuration_names: List[str] = []

        self._time_interval_s: float = 0.0
        self._enable_stage_tracking: bool = True
        self._save_images: bool = False
        self._tracker_type: str = "csrt"
        self._pixel_size_um: Optional[float] = None
        self._image_resizing_factor: float = 1.0

        self._objective_name: Optional[str] = getattr(
            self._objective_store, "current_objective", None
        )
        self._is_tracking: bool = False

        self._keep_running = threading.Event()
        self._worker: Optional[_TrackingWorker] = None

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        self._bus.subscribe(SetTrackingParametersCommand, self._on_set_parameters)
        self._bus.subscribe(SetTrackingPathCommand, self._on_set_path)
        self._bus.subscribe(SetTrackingChannelsCommand, self._on_set_channels)
        self._bus.subscribe(StartTrackingExperimentCommand, self._on_start_experiment)
        self._bus.subscribe(StartTrackingCommand, self._on_start_tracking)
        self._bus.subscribe(StopTrackingCommand, self._on_stop_tracking)
        self._bus.subscribe(ObjectiveChanged, self._on_objective_changed)
        self._bus.subscribe(TrackingWorkerFinished, self._on_worker_finished)

    def _publish_tracking_state(self, is_tracking: bool) -> None:
        self._bus.publish(TrackingStateChanged(is_tracking=is_tracking))

    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        if event.objective_name is None:
            return
        with self._lock:
            self._objective_name = event.objective_name
            if event.pixel_size_um is not None:
                self._pixel_size_um = event.pixel_size_um

    def _on_set_parameters(self, cmd: SetTrackingParametersCommand) -> None:
        with self._lock:
            if cmd.time_interval_s is not None:
                self._time_interval_s = float(cmd.time_interval_s)
            if cmd.enable_stage_tracking is not None:
                self._enable_stage_tracking = bool(cmd.enable_stage_tracking)
            if cmd.save_images is not None:
                self._save_images = bool(cmd.save_images)
            if cmd.tracker_type is not None:
                self._tracker_type = str(cmd.tracker_type)
            if cmd.pixel_size_um is not None:
                self._pixel_size_um = float(cmd.pixel_size_um)
            if cmd.objective is not None:
                self._objective_name = str(cmd.objective)
            if cmd.image_resizing_factor is not None:
                self._image_resizing_factor = float(cmd.image_resizing_factor)

    def _on_set_path(self, cmd: SetTrackingPathCommand) -> None:
        with self._lock:
            self._base_path = cmd.base_path

    def _on_set_channels(self, cmd: SetTrackingChannelsCommand) -> None:
        with self._lock:
            self._selected_configuration_names = list(cmd.channel_names)

    def _on_start_experiment(self, cmd: StartTrackingExperimentCommand) -> None:
        with self._lock:
            self._experiment_id_base = cmd.experiment_id

    def _build_run_config(self, *, roi_bbox: Tuple[int, int, int, int]) -> _TrackingRunConfig:
        with self._lock:
            if self._is_tracking:
                raise RuntimeError("Tracking already running")
            if not self._base_path:
                raise RuntimeError("Tracking base path not set")
            if not self._experiment_id_base:
                raise RuntimeError("Tracking experiment id not set")
            if self._pixel_size_um is None:
                raise RuntimeError("Tracking pixel size not set")

            objective = self._objective_name or getattr(
                self._objective_store, "current_objective", None
            )
            if not objective:
                raise RuntimeError("Objective not available for tracking")

            config_name = (
                self._selected_configuration_names[0]
                if self._selected_configuration_names
                else getattr(getattr(self._live, "currentConfiguration", None), "name", None)
            )
            if not config_name:
                raise RuntimeError("No tracking configuration selected")

            config = self._configs.get_channel_configuration_by_name(objective, config_name)
            if config is None:
                raise RuntimeError(f"Unknown tracking configuration: {config_name}")

            unique_experiment_id = (
                f"{self._experiment_id_base}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S.%f')}"
            )

            return _TrackingRunConfig(
                experiment_id=unique_experiment_id,
                base_path=self._base_path,
                configuration=config,
                roi_bbox=tuple(int(x) for x in roi_bbox),
                time_interval_s=self._time_interval_s,
                enable_stage_tracking=self._enable_stage_tracking,
                save_images=self._save_images,
                tracker_type=self._tracker_type,
                pixel_size_um=float(self._pixel_size_um),
                image_resizing_factor=float(self._image_resizing_factor or 1.0),
            )

    def _on_start_tracking(self, cmd: StartTrackingCommand) -> None:
        run_cfg = self._build_run_config(roi_bbox=cmd.roi_bbox)

        with self._lock:
            self._is_tracking = True
            self._keep_running.set()

        previous_mode = self._mode_gate.get_mode() if self._mode_gate else None

        was_live = bool(getattr(self._live, "is_live", False))
        prev_config = getattr(self._live, "currentConfiguration", None)

        try:
            os.makedirs(
                os.path.join(run_cfg.base_path, run_cfg.experiment_id), exist_ok=True
            )
            objective = self._objective_name or getattr(
                self._objective_store, "current_objective", None
            )
            if objective:
                self._configs.save_current_configuration_to_path(
                    objective,
                    Path(os.path.join(run_cfg.base_path, run_cfg.experiment_id, "configurations.xml")),
                )
            if was_live:
                self._live.stop_live()
            self._live.set_microscope_mode(run_cfg.configuration)
        except Exception:
            self._log.exception("Failed to prepare microscope for tracking")
            if self._mode_gate and previous_mode is not None:
                self._mode_gate.restore_mode(previous_mode, reason="tracking start failed")
            with self._lock:
                self._is_tracking = False
                self._keep_running.clear()
            self._publish_tracking_state(is_tracking=False)
            raise

        if self._mode_gate:
            self._mode_gate.set_mode(GlobalMode.ACQUIRING, reason="tracking start")

        self._publish_tracking_state(is_tracking=True)

        worker = _TrackingWorker(
            bus=self._bus,
            camera_service=self._camera,
            stage_service=self._stage,
            live_controller=self._live,
            peripheral_service=self._peripheral,
            keep_running=self._keep_running,
            run_cfg=run_cfg,
            previous_live_state=was_live,
            previous_configuration=prev_config,
            previous_mode=previous_mode,
            mode_gate=self._mode_gate,
        )
        with self._lock:
            self._worker = worker
        worker.start()

    def _on_stop_tracking(self, cmd: StopTrackingCommand) -> None:
        with self._lock:
            if not self._is_tracking:
                return
            self._keep_running.clear()

    def _on_worker_finished(self, event: TrackingWorkerFinished) -> None:
        with self._lock:
            worker = self._worker
            self._worker = None
            self._is_tracking = False
            self._keep_running.clear()

        if worker is not None:
            worker.restore_after_run()
        self._publish_tracking_state(is_tracking=False)


class _TrackingWorker(threading.Thread):
    def __init__(
        self,
        *,
        bus: EventBus,
        camera_service: "CameraService",
        stage_service: "StageService",
        live_controller: "LiveController",
        peripheral_service: Optional["PeripheralService"],
        keep_running: threading.Event,
        run_cfg: _TrackingRunConfig,
        previous_live_state: bool,
        previous_configuration: Optional["ChannelMode"],
        previous_mode: Optional[GlobalMode],
        mode_gate: Optional[GlobalModeGate],
    ) -> None:
        super().__init__(daemon=True)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._bus = bus
        self._camera = camera_service
        self._stage = stage_service
        self._live = live_controller
        self._peripheral = peripheral_service
        self._keep_running = keep_running
        self._cfg = run_cfg
        self._was_live = previous_live_state
        self._prev_config = previous_configuration
        self._prev_mode = previous_mode
        self._mode_gate = mode_gate

        self._experiment_dir = os.path.join(self._cfg.base_path, self._cfg.experiment_id)
        self._csv_path = os.path.join(self._experiment_dir, "tracking.csv")
        self._csv_file = None
        self._frame_counter = 0

    def restore_after_run(self) -> None:
        try:
            if self._prev_config is not None:
                self._live.set_microscope_mode(self._prev_config)
        except Exception:
            self._log.exception("Failed to restore microscope configuration after tracking")

        try:
            if self._was_live:
                self._live.start_live()
        except Exception:
            self._log.exception("Failed to restart live after tracking")

        if self._mode_gate:
            try:
                if self._was_live:
                    return
                if self._prev_mode is not None:
                    self._mode_gate.restore_mode(self._prev_mode, reason="tracking complete")
                else:
                    self._mode_gate.set_mode(GlobalMode.IDLE, reason="tracking complete")
            except Exception:
                pass

    def run(self) -> None:
        try:
            os.makedirs(self._experiment_dir, exist_ok=True)
            with open(self._csv_path, "w", encoding="utf-8") as f:
                self._csv_file = f
                self._csv_file.write(
                    "timestamp_s,x_mm,y_mm,z_mm,x_error_mm,y_error_mm,frame_idx\n"
                )
                self._csv_file.flush()
                self._run_loop()
            self._bus.publish(
                TrackingWorkerFinished(
                    success=True,
                    aborted=not self._keep_running.is_set(),
                )
            )
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            self._log.exception("Tracking worker failed")
            self._bus.publish(
                TrackingWorkerFinished(
                    success=False,
                    aborted=not self._keep_running.is_set(),
                    error=msg,
                )
            )
        finally:
            self._csv_file = None

    def _capture_frame(self) -> Tuple[np.ndarray, float, bool]:
        if self._live.control_illumination:
            self._live.turn_on_illumination()
            if self._peripheral is not None:
                self._peripheral.wait_till_operation_is_completed()
        self._camera.send_trigger()
        frame = self._camera.read_camera_frame()
        image = np.squeeze(frame.frame)
        timestamp = float(getattr(frame, "timestamp", time.time()))
        if self._live.control_illumination:
            self._live.turn_off_illumination()
        is_color = False
        try:
            is_color = bool(frame.is_color())
        except Exception:
            pass
        return image, timestamp, is_color

    def _run_loop(self) -> None:
        tracker = Tracker_Image()
        tracker.update_tracker_type(self._cfg.tracker_type)
        tracker.set_roi_bbox(self._cfg.roi_bbox)
        tracker.reset()

        if not self._camera.get_is_streaming():
            self._camera.start_streaming()

        try:
            while self._keep_running.is_set():
                loop_start = time.time()
                image, t, is_color = self._capture_frame()
                image_shape = image.shape
                image_center = np.array([image_shape[1] * 0.5, image_shape[0] * 0.5])

                is_first = self._frame_counter == 0
                object_found, centroid, _ = tracker.track(image, None, is_first_frame=is_first)
                if not object_found or centroid is None:
                    raise RuntimeError("Tracker failed to find object")

                error_px = image_center - centroid
                pixel_size_um_scaled = self._cfg.pixel_size_um / max(self._cfg.image_resizing_factor, 1e-9)
                error_mm = (error_px * pixel_size_um_scaled) / 1000.0
                x_error_mm = float(error_mm[0])
                y_error_mm = float(error_mm[1])

                pos = self._stage.get_position()
                if self._cfg.enable_stage_tracking:
                    self._stage.move_x(x_error_mm)
                    self._stage.move_y(y_error_mm)

                if self._cfg.save_images:
                    save_image(
                        image=image.copy(),
                        file_id=f"{self._frame_counter:06d}",
                        save_directory=self._experiment_dir,
                        config=self._cfg.configuration,
                        is_color=is_color,
                    )

                if self._csv_file is not None:
                    self._csv_file.write(
                        f"{t},{pos.x_mm},{pos.y_mm},{pos.z_mm},{x_error_mm},{y_error_mm},{self._frame_counter}\n"
                    )
                    if self._frame_counter % 25 == 0:
                        self._csv_file.flush()

                self._frame_counter += 1

                if self._cfg.time_interval_s > 0:
                    elapsed = time.time() - loop_start
                    remaining = self._cfg.time_interval_s - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
        finally:
            try:
                self._camera.stop_streaming()
            except Exception:
                self._log.exception("Failed to stop camera streaming after tracking")
