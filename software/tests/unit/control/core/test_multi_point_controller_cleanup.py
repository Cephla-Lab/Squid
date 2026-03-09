from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from squid.backend.controllers.multipoint import multi_point_controller
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController
from squid.core.events import AcquisitionWorkerFinished, EventBus


@dataclass
class _Pos:
    x_mm: float
    y_mm: float
    z_mm: float


class _FakeStageService:
    def __init__(self) -> None:
        self._pos = _Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0)

    def get_position(self) -> _Pos:
        return self._pos

    def move_x_to(self, x_mm: float) -> None:
        self._pos.x_mm = x_mm

    def move_y_to(self, y_mm: float) -> None:
        self._pos.y_mm = y_mm

    def move_z_to(self, z_mm: float) -> None:
        self._pos.z_mm = z_mm


class _FakeCameraService:
    def __init__(self) -> None:
        self._callbacks_enabled = False

    def get_callbacks_enabled(self) -> bool:
        return self._callbacks_enabled

    def enable_callbacks(self, enabled: bool) -> None:
        self._callbacks_enabled = enabled

    def stop_streaming(self) -> None:
        return None

    def get_pixel_size_binned_um(self) -> float:
        return 1.0

    def get_binning(self) -> tuple[int, int]:
        return (1, 1)


class _FakePeripheralService:
    pass


class _FakeLiveController:
    def __init__(self) -> None:
        self.is_live = False
        self.currentConfiguration = object()
        self.trigger_mode = None
        self.enable_channel_auto_filter_switching = False

    def stop_live(self) -> None:
        self.is_live = False

    def start_live(self) -> None:
        self.is_live = True

    def set_microscope_mode(self, _mode) -> None:
        return None


class _FakeAutoFocusController:
    def __init__(self) -> None:
        self.use_focus_map = False
        self.focus_map_coords = []

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []

    def set_focus_map_surface(self, _focus_map) -> None:
        return None

    def set_focus_map_use(self, enabled: bool) -> None:
        self.use_focus_map = enabled


class _FakeScanCoordinates:
    def __init__(self) -> None:
        self.region_fov_coordinates = {"region_1": [(0.0, 0.0, 0.0)]}
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()

    def has_regions(self) -> bool:
        return True

    def get_scan_bounds(self):
        return {"x": (0.0, 0.0), "y": (0.0, 0.0)}


class _FakeObjectiveStore:
    current_objective = "10x"
    objectives_dict = {}

    def get_pixel_size_factor(self) -> float:
        return 1.0


class _FakeChannelConfigurationManager:
    pass


class _FakeScanPositionInformation:
    def __init__(self) -> None:
        self.scan_region_names = ["region_1"]
        self.scan_region_coords_mm = [(0.0, 0.0, 0.0)]
        self.scan_region_fov_coords_mm = {"region_1": [(0.0, 0.0, 0.0)]}


class _ImmediateWorker:
    def __init__(self, *, acquisition_parameters, event_bus, **_kwargs) -> None:
        self._experiment_id = acquisition_parameters.experiment_ID
        self._event_bus = event_bus

    def set_current_round_index(self, _round_index: int) -> None:
        return None

    def set_start_fov_index(self, _start_fov_index: int) -> None:
        return None

    def run(self) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(
                AcquisitionWorkerFinished(
                    experiment_id=self._experiment_id,
                    success=True,
                    final_fov_count=1,
                )
            )


def test_run_acquisition_initializes_recording_start_time_for_orchestrator_path(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(multi_point_controller, "MultiPointWorker", _ImmediateWorker)
    monkeypatch.setattr(
        multi_point_controller.ScanPositionInformation,
        "from_scan_coordinates",
        lambda _scan: _FakeScanPositionInformation(),
    )
    monkeypatch.setattr(multi_point_controller, "save_acquisition_yaml", lambda **_kwargs: None)
    monkeypatch.setattr(
        multi_point_controller.AcquisitionDependencies,
        "create",
        staticmethod(lambda **_kwargs: object()),
    )

    bus = EventBus()
    bus.start()
    try:
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=_FakeScanCoordinates(),
        )
        controller.set_base_path(str(tmp_path))
        controller.experiment_ID = "round_000"
        (tmp_path / controller.experiment_ID).mkdir()

        with caplog.at_level(logging.ERROR):
            assert controller.run_acquisition() is True
            assert controller.recording_start_time is not None

            deadline = time.time() + 1.0
            while controller.state.name != "IDLE" and time.time() < deadline:
                bus.drain(timeout_s=0.05)
                threading.Event().wait(0.01)

        if controller.thread is not None:
            controller.thread.join(timeout=1.0)
        bus.drain(timeout_s=0.1)

        assert (tmp_path / controller.experiment_ID / ".done").exists()
        assert "Error during acquisition cleanup" not in caplog.text
    finally:
        bus.stop()
