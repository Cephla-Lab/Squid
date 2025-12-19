from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from squid.core.abc import CameraFrame, CameraFrameFormat, Pos
from squid.core.config import CameraPixelFormat
from squid.core.events import AcquisitionCoordinates, EventBus
from squid.backend.controllers.multipoint.job_processing import CaptureInfo
from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.backend.controllers.multipoint.multi_point_utils import AcquisitionParameters, ScanPositionInformation


def _make_params() -> AcquisitionParameters:
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


def test_multipoint_worker_fans_out_frame_via_stream_handler_and_publishes_coordinates():
    bus = EventBus()
    coords_events: list[AcquisitionCoordinates] = []
    bus.subscribe(AcquisitionCoordinates, coords_events.append)

    on_new_image = MagicMock()
    stream_handler = SimpleNamespace(on_new_image=on_new_image)

    camera_service = MagicMock()
    camera_service.get_pixel_size_binned_um.return_value = 1.0
    stage_service = MagicMock()
    peripheral_service = MagicMock()

    objective_store = SimpleNamespace(current_objective="10x", get_pixel_size_factor=lambda: None)
    channel_configuration_manager = MagicMock()

    worker = MultiPointWorker(
        auto_focus_controller=None,
        laser_auto_focus_controller=None,
        objective_store=objective_store,
        channel_configuration_mananger=channel_configuration_manager,
        acquisition_parameters=_make_params(),
        camera_service=camera_service,
        stage_service=stage_service,
        peripheral_service=peripheral_service,
        event_bus=bus,
        stream_handler=stream_handler,
    )
    worker._job_runners = []

    info = CaptureInfo(
        position=Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None),
        z_index=0,
        capture_time=123.0,
        configuration=SimpleNamespace(name="ch1"),
        save_directory="",
        file_id="file",
        region_id=7,
        fov=9,
        configuration_idx=0,
    )
    worker._current_capture_info.set(info)

    frame = CameraFrame(
        frame_id=123,
        timestamp=456.0,
        frame=np.zeros((8, 8), dtype=np.uint8),
        frame_format=CameraFrameFormat.RAW,
        frame_pixel_format=CameraPixelFormat.MONO8,
    )

    worker._process_camera_frame(frame)
    bus.drain()

    on_new_image.assert_called_once()
    _, kwargs = on_new_image.call_args
    assert kwargs["frame_id"] == 123
    assert kwargs["timestamp"] == 456.0
    assert kwargs["capture_info"] is info
    assert coords_events and coords_events[-1].x_mm == 1.0 and coords_events[-1].region_id == 7

