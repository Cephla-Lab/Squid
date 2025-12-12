"""Tests for MultiPointWorker service routing and abort handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from squid.core.events import EventBus, AcquisitionFinished
import _def as _def

# Avoid spawning JobRunner processes in tests
_def.Acquisition.USE_MULTIPROCESSING = False

from squid.ops.acquisition.multi_point_worker import MultiPointWorker
from squid.ops.acquisition.multi_point_utils import (
    AcquisitionParameters,
    ScanPositionInformation,
)


def _make_params() -> AcquisitionParameters:
    """Minimal acquisition parameters for worker construction."""
    scan_info = ScanPositionInformation(
        scan_region_coords_mm=[],
        scan_region_names=[],
        scan_region_fov_coords_mm={},
    )
    return AcquisitionParameters(
        experiment_ID="test-exp-001",
        base_path=None,
        selected_configurations=[],
        acquisition_start_time=0.0,
        scan_position_information=scan_info,
        NX=1,
        deltaX=0.0,
        NY=1,
        deltaY=0.0,
        NZ=1,
        deltaZ=0.0,
        Nt=1,
        deltat=0.0,
        do_autofocus=False,
        do_reflection_autofocus=False,
        use_piezo=False,
        display_resolution_scaling=1.0,
        z_stacking_config="FROM BOTTOM",
        z_range=(0.0, 0.0),
        use_fluidics=False,
    )


class DummyMicroscope:
    """Lightweight microscope stub."""

    def __init__(self):
        self.camera = MagicMock()
        # provide pixel size so __init__ pre-computation succeeds
        self.camera.get_pixel_size_binned_um.return_value = 1.0
        self.stage = MagicMock()
        self.stage.get_pos.return_value = SimpleNamespace(x_mm=0.0, y_mm=0.0, z_mm=0.0)
        self.low_level_drivers = SimpleNamespace(microcontroller=MagicMock())
        self.addons = SimpleNamespace(piezo_stage=None, fluidics=None)
        self.objective_store = SimpleNamespace(
            current_objective="10x", get_pixel_size_factor=lambda: None
        )
        self.channel_configuration_manager = MagicMock()


def _make_worker(
    *,
    camera_service: MagicMock | None = None,
    stage_service: MagicMock | None = None,
    peripheral_service: MagicMock | None = None,
    event_bus: EventBus | None = None,
) -> MultiPointWorker:
    """Helper to build a worker with injectable services."""
    mic = DummyMicroscope()
    return MultiPointWorker(
        scope=mic,
        live_controller=MagicMock(),
        auto_focus_controller=None,
        laser_auto_focus_controller=None,
        objective_store=mic.objective_store,
        channel_configuration_mananger=mic.channel_configuration_manager,
        acquisition_parameters=_make_params(),
        abort_requested_fn=lambda: False,
        request_abort_fn=lambda: None,
        camera_service=camera_service,
        stage_service=stage_service,
        peripheral_service=peripheral_service,
        piezo_service=None,
        microscope_mode_controller=None,
        event_bus=event_bus,
    )


def test_camera_helpers_use_service_not_hardware():
    cam_service = MagicMock()
    cam_service.add_frame_callback.return_value = "svc_cb"
    stage_service = MagicMock()
    stage_service.get_position.return_value = SimpleNamespace(
        x_mm=0.0, y_mm=0.0, z_mm=0.0
    )
    worker = _make_worker(
        camera_service=cam_service, stage_service=stage_service, peripheral_service=MagicMock()
    )

    cb_id = worker._camera_add_frame_callback(lambda *_: None)
    worker._camera_start_streaming()

    cam_service.add_frame_callback.assert_called_once()
    cam_service.start_streaming.assert_called_once()
    worker.microscope.camera.add_frame_callback.assert_not_called()
    worker.microscope.camera.start_streaming.assert_not_called()
    assert cb_id == "svc_cb"


def test_stage_helpers_wait_and_use_service():
    stage_service = MagicMock()
    stage_service.get_position.return_value = SimpleNamespace(
        x_mm=0.0, y_mm=0.0, z_mm=0.0
    )
    worker = _make_worker(stage_service=stage_service)

    worker._stage_move_x_to(1.0)
    stage_service.move_x_to.assert_called_once_with(1.0)
    stage_service.wait_for_idle.assert_called()
    worker.microscope.stage.move_x_to.assert_not_called()


def test_abort_sets_finished_event_success_false():
    bus = EventBus()
    events: list[AcquisitionFinished] = []
    bus.subscribe(AcquisitionFinished, events.append)

    cam_service = MagicMock()
    cam_service.add_frame_callback.return_value = "cb"
    cam_service.get_total_frame_time.return_value = 10.0
    stage_service = MagicMock()
    stage_service.get_position.return_value = SimpleNamespace(
        x_mm=0.0, y_mm=0.0, z_mm=0.0
    )
    worker = _make_worker(
        camera_service=cam_service,
        stage_service=stage_service,
        peripheral_service=MagicMock(),
        event_bus=bus,
    )
    # Force an immediate abort
    worker.abort_requested_fn = lambda: True
    worker.run()
    bus.drain()

    assert events, "AcquisitionFinished should be published"
    assert events[-1].success is False
