"""Tests for MultiPointWorker service routing and abort handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from squid.core.events import EventBus, AcquisitionWorkerFinished
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


def _make_worker(
    *,
    camera_service: MagicMock | None = None,
    stage_service: MagicMock | None = None,
    peripheral_service: MagicMock | None = None,
    event_bus: EventBus | None = None,
) -> MultiPointWorker:
    """Helper to build a worker with injectable services."""
    objective_store = SimpleNamespace(
        current_objective="10x", get_pixel_size_factor=lambda: None
    )
    channel_configuration_manager = MagicMock()
    if camera_service is None:
        camera_service = MagicMock()
        camera_service.get_pixel_size_binned_um.return_value = 1.0
        camera_service.get_total_frame_time.return_value = 10.0
    if stage_service is None:
        stage_service = MagicMock()
        stage_service.get_position.return_value = SimpleNamespace(
            x_mm=0.0, y_mm=0.0, z_mm=0.0
        )
    if peripheral_service is None:
        peripheral_service = MagicMock()
    if event_bus is None:
        event_bus = EventBus()
    return MultiPointWorker(
        auto_focus_controller=None,
        laser_auto_focus_controller=None,
        objective_store=objective_store,
        channel_configuration_mananger=channel_configuration_manager,
        acquisition_parameters=_make_params(),
        camera_service=camera_service,
        stage_service=stage_service,
        peripheral_service=peripheral_service,
        event_bus=event_bus,
    )


def test_camera_helpers_use_service_not_hardware():
    cam_service = MagicMock()
    cam_service.add_frame_callback.return_value = "svc_cb"
    cam_service.get_pixel_size_binned_um.return_value = 1.0
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


def test_abort_sets_finished_event_success_false():
    bus = EventBus()
    events: list[AcquisitionWorkerFinished] = []
    bus.subscribe(AcquisitionWorkerFinished, events.append)

    cam_service = MagicMock()
    cam_service.add_frame_callback.return_value = "cb"
    cam_service.get_total_frame_time.return_value = 10.0
    cam_service.get_pixel_size_binned_um.return_value = 1.0
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
    worker.request_abort()
    worker.run()
    bus.drain()

    assert events, "AcquisitionWorkerFinished should be published"
    assert events[-1].success is False


def test_acquire_rgb_image_emits_stream_handler_frame(tmp_path):
    cam_service = MagicMock()
    cam_service.get_pixel_size_binned_um.return_value = 1.0
    cam_service.get_total_frame_time.return_value = 10.0
    cam_service.get_exposure_time.return_value = 1.0
    cam_service.read_frame.side_effect = [
        # R/G/B monochrome captures
        np.ones((8, 12), dtype=np.uint16),
        np.ones((8, 12), dtype=np.uint16) * 2,
        np.ones((8, 12), dtype=np.uint16) * 3,
    ]

    stage_service = MagicMock()
    stage_service.get_position.return_value = SimpleNamespace(
        x_mm=1.0, y_mm=2.0, z_mm=3.0
    )

    worker = _make_worker(
        camera_service=cam_service,
        stage_service=stage_service,
        peripheral_service=MagicMock(),
        event_bus=EventBus(),
    )
    worker._select_config = MagicMock()

    worker.channelConfigurationManager.get_channel_configurations_for_objective.return_value = [
        SimpleNamespace(name="BF LED matrix full_R"),
        SimpleNamespace(name="BF LED matrix full_G"),
        SimpleNamespace(name="BF LED matrix full_B"),
    ]

    stream_handler = MagicMock()
    worker._stream_handler = stream_handler

    worker.acquire_rgb_image(
        SimpleNamespace(name="BF LED matrix full_RGB", id=0),
        "rgb_test",
        str(tmp_path),
        0,
        "region0",
        0,
    )

    assert stream_handler.on_new_image.call_count == 1
    args, kwargs = stream_handler.on_new_image.call_args
    assert args[0].shape == (8, 12, 3)
    assert kwargs["capture_info"].configuration.name == "BF LED matrix full_RGB"
