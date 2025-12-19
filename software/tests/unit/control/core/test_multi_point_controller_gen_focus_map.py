from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Optional

import pytest

from squid.core.events import (
    EventBus,
    AcquisitionWorkerFinished,
)
from squid.core.mode_gate import GlobalModeGate, GlobalMode
from squid.backend.controllers.multipoint import multi_point_controller
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController


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

    def move_x_to(self, _x_mm: float) -> None:
        return None

    def move_y_to(self, _y_mm: float) -> None:
        return None

    def move_z_to(self, _z_mm: float) -> None:
        return None


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
        self._gen_focus_map_calls = 0

    def gen_focus_map(self, _coord1, _coord2, _coord3) -> None:
        self._gen_focus_map_calls += 1

    def set_focus_map_use(self, enabled: bool) -> None:
        self.use_focus_map = enabled

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []


class _FakeScanCoordinates:
    def __init__(self) -> None:
        self.region_centers = {}
        self.region_fov_coordinates = {}
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()

    def get_scan_bounds(self):
        return None


class _FakeObjectiveStore:
    current_objective = "10x"
    objectives_dict = {}


class _FakeChannelConfigurationManager:
    def write_configuration_selected(self, *_args, **_kwargs) -> None:
        return None


class _FakeGlobalScanCoordinates:
    def __init__(self) -> None:
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()
        self.update_calls: list[tuple[str, int, float]] = []

    def update_fov_z_level(self, region_id: str, index: int, z_mm: float) -> None:
        self.update_calls.append((region_id, index, z_mm))


class _FakeFocusMap:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float, str]] = []

    def interpolate(self, x_mm: float, y_mm: float, region_id: str) -> float:
        self.calls.append((x_mm, y_mm, region_id))
        return 1.23


def test_gen_focus_map_invalid_bounds_publishes_failure(tmp_path: Path) -> None:
    bus = EventBus()
    bus.start()
    try:
        finished: list[AcquisitionWorkerFinished] = []
        finished_ready = threading.Event()

        def _on_finished(e: AcquisitionWorkerFinished) -> None:
            finished.append(e)
            finished_ready.set()

        bus.subscribe(AcquisitionWorkerFinished, _on_finished)

        live = _FakeLiveController()
        af = _FakeAutoFocusController()
        stage_service = _FakeStageService()
        camera_service = _FakeCameraService()
        peripheral_service = _FakePeripheralService()
        mode_gate = GlobalModeGate(bus)

        controller = MultiPointController(
            live_controller=live,
            autofocus_controller=af,
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=camera_service,
            stage_service=stage_service,
            peripheral_service=peripheral_service,
            event_bus=bus,
            scan_coordinates=_FakeScanCoordinates(),
            mode_gate=mode_gate,
        )

        controller.set_base_path(str(tmp_path))
        controller.start_new_experiment("unit_test")
        controller.set_gen_focus_map_flag(True)
        controller.set_reflection_af_flag(False)

        controller.run_acquisition()

        assert finished_ready.wait(timeout=2.0), "Timed out waiting for AcquisitionWorkerFinished"
        assert len(finished) == 1
        assert finished[0].experiment_id == controller.experiment_ID
        assert finished[0].experiment_id.startswith("unit_test_")
        assert finished[0].success is False
        assert finished[0].error == "Invalid scan bounds"
        assert af._gen_focus_map_calls == 0
        assert controller.state.name == "IDLE"
        assert mode_gate.get_mode() == GlobalMode.IDLE
    finally:
        bus.stop()


def test_reflection_af_requires_laser_controller() -> None:
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
            laser_autofocus_controller=None,
        )
        controller.set_reflection_af_flag(True)

        assert controller.validate_acquisition_settings() is False
    finally:
        bus.stop()


def test_acquire_current_fov_focus_map_does_not_mutate_global_scan_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _LocalScanCoordinates:
        last_instance: Optional["_LocalScanCoordinates"] = None

        def __init__(self, objectiveStore, stage, camera, event_bus=None) -> None:
            self.objectiveStore = objectiveStore
            self.stage = stage
            self.camera = camera
            self.update_calls: list[tuple[str, int, float]] = []
            type(self).last_instance = self

        def add_single_fov_region(self, *_args, **_kwargs) -> None:
            return None

        def update_fov_z_level(self, region_id: str, index: int, z_mm: float) -> None:
            self.update_calls.append((region_id, index, z_mm))

    class _FakeScanPositionInformation:
        def __init__(self) -> None:
            self.scan_region_names = ["region"]
            self.scan_region_coords_mm = [(0.0, 0.0, 0.0)]
            self.scan_region_fov_coords_mm = {"region": [(1.0, 2.0, 0.0)]}

    class _NoOpWorker:
        def __init__(self, *, acquisition_parameters, event_bus, **_kwargs) -> None:
            self._event_bus = event_bus
            self._experiment_id = acquisition_parameters.experiment_ID

        def run(self) -> None:
            if self._event_bus:
                self._event_bus.publish(
                    AcquisitionWorkerFinished(
                        experiment_id=self._experiment_id,
                        success=True,
                        final_fov_count=0,
                    )
                )

    monkeypatch.setattr(multi_point_controller, "ScanCoordinates", _LocalScanCoordinates)
    monkeypatch.setattr(multi_point_controller, "MultiPointWorker", _NoOpWorker)
    monkeypatch.setattr(
        multi_point_controller.ScanPositionInformation,
        "from_scan_coordinates",
        lambda _scan: _FakeScanPositionInformation(),
    )

    bus = EventBus()
    bus.start()
    try:
        global_scan = _FakeGlobalScanCoordinates()
        controller = MultiPointController(
            live_controller=_FakeLiveController(),
            autofocus_controller=_FakeAutoFocusController(),
            objective_store=_FakeObjectiveStore(),
            channel_configuration_manager=_FakeChannelConfigurationManager(),
            camera_service=_FakeCameraService(),
            stage_service=_FakeStageService(),
            peripheral_service=_FakePeripheralService(),
            event_bus=bus,
            scan_coordinates=global_scan,
        )
        controller.set_base_path(str(tmp_path))
        controller.start_new_experiment("unit_test")
        controller.set_focus_map(_FakeFocusMap())

        controller.run_acquisition(acquire_current_fov=True)
        bus.drain(timeout_s=1.0)
        if controller.thread:
            controller.thread.join(timeout=1.0)

        assert global_scan.update_calls == []
        local_scan = _LocalScanCoordinates.last_instance
        assert local_scan is not None
        assert local_scan.update_calls != []
    finally:
        bus.stop()
