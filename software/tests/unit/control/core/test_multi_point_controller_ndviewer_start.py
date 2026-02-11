"""Regression tests for MultiPointController NDViewer start publishing."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import _def
from squid.backend.controllers.multipoint.multi_point_controller import MultiPointController
from squid.backend.controllers.multipoint.multi_point_utils import (
    AcquisitionParameters,
    ScanPositionInformation,
)
from squid.core.events import (
    AutofocusMode,
    EventBus,
    FocusLockSettings,
    NDViewerStartAcquisition,
)


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


class _FakeCameraService:
    def __init__(self) -> None:
        self._callbacks_enabled = False

    def get_callbacks_enabled(self) -> bool:
        return self._callbacks_enabled

    def enable_callbacks(self, enabled: bool) -> None:
        self._callbacks_enabled = enabled

    def stop_streaming(self) -> None:
        return None

    def get_crop_size(self) -> tuple[int, int]:
        return (640, 480)

    def get_resolution(self) -> tuple[int, int]:
        return (640, 480)


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
        self._focus_map_surface = None

    def sample_focus_map_points(self, coords) -> None:
        self.focus_map_coords = [(x, y, 0.0) for x, y in coords]

    def set_focus_map_use(self, enabled: bool) -> None:
        self.use_focus_map = enabled

    def set_focus_map_surface(self, focus_map) -> None:
        self._focus_map_surface = focus_map

    @property
    def focus_map_surface(self):
        return self._focus_map_surface

    def clear_focus_map(self) -> None:
        self.focus_map_coords = []


class _FakeScanCoordinates:
    def __init__(self) -> None:
        self.region_centers = {}
        self.region_fov_coordinates = {}
        self.objectiveStore = object()
        self.stage = object()
        self.camera = object()


class _FakeObjectiveStore:
    current_objective = "10x"
    objectives_dict = {}


class _FakeChannelConfigurationManager:
    def write_configuration_selected(self, *_args, **_kwargs) -> None:
        return None


@dataclass
class _FakeChannelConfig:
    name: str


def _create_controller(bus: EventBus) -> MultiPointController:
    return MultiPointController(
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


def _build_acquisition_params(experiment_id: str) -> AcquisitionParameters:
    scan_info = ScanPositionInformation(
        scan_region_coords_mm=[(0.0, 0.0), (1.0, 1.0)],
        scan_region_names=["1", "2"],
        scan_region_fov_coords_mm={
            "1": [(0.0, 0.0, 0.0), (0.1, 0.1, 0.0)],
            "2": [(1.0, 1.0, 0.0)],
        },
    )
    return AcquisitionParameters(
        experiment_ID=experiment_id,
        base_path="/tmp",
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=scan_info,
        NX=1,
        deltaX=0.5,
        NY=1,
        deltaY=0.5,
        NZ=1,
        deltaZ=1.0,
        Nt=1,
        deltat=0.0,
        autofocus_mode=AutofocusMode.NONE,
        autofocus_interval_fovs=1,
        focus_lock_settings=FocusLockSettings(),
        use_piezo=False,
        display_resolution_scaling=1.0,
        z_stacking_config="FROM CENTER",
        z_range=(0.0, 0.0),
        use_fluidics=False,
        skip_saving=True,
    )


def test_publish_ndviewer_start_skips_non_push_file_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    bus.start()
    try:
        published: list[NDViewerStartAcquisition] = []
        bus.subscribe(NDViewerStartAcquisition, published.append)

        controller = _create_controller(bus)
        controller.experiment_ID = "exp_ome"
        controller.selected_configurations = [_FakeChannelConfig("BF")]

        monkeypatch.setattr(_def, "FILE_SAVING_OPTION", _def.FileSavingOption.OME_TIFF)
        controller._publish_ndviewer_start(_build_acquisition_params("exp_ome"))
        bus.drain(timeout_s=1.0)

        assert published == []
        assert controller._ndviewer_mode == "inactive"
    finally:
        bus.stop()


def test_publish_ndviewer_start_emits_push_event_for_individual_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus()
    bus.start()
    try:
        published: list[NDViewerStartAcquisition] = []
        bus.subscribe(NDViewerStartAcquisition, published.append)

        controller = _create_controller(bus)
        controller.experiment_ID = "exp_push"
        controller.selected_configurations = [_FakeChannelConfig("BF"), _FakeChannelConfig("GFP")]

        monkeypatch.setattr(
            _def,
            "FILE_SAVING_OPTION",
            _def.FileSavingOption.INDIVIDUAL_IMAGES,
        )
        controller._publish_ndviewer_start(_build_acquisition_params("exp_push"))
        bus.drain(timeout_s=1.0)

        assert len(published) == 1
        event = published[0]
        assert event.channels == ["BF", "GFP"]
        assert event.num_z == 1
        assert event.height == 480
        assert event.width == 640
        assert event.fov_labels == ["1:0", "1:1", "2:0"]
        assert event.experiment_id == "exp_push"
        assert controller._ndviewer_mode == "tiff"
    finally:
        bus.stop()
