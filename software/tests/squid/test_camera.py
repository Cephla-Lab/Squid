import squid.camera.utils
import squid.config
from squid.abc import AbstractCamera


def test_create_simulated_camera():
    sim_cam = squid.camera.utils.get_camera(squid.config.get_camera_config(), simulated=True)


def test_simulated_camera():
    sim_cam = squid.camera.utils.get_camera(squid.config.get_camera_config(), simulated=True)

    # Really basic tests to make sure the simulated camera does what is expected.
    assert sim_cam.read_frame() is not None
    frame_id = sim_cam.get_frame_id()
    assert sim_cam.read_frame() is not None
    assert sim_cam.get_frame_id() != frame_id

    frame = sim_cam.read_frame()
    (frame_height, frame_width, *_) = frame.shape
    (res_width, res_height) = sim_cam.get_resolution()

    assert frame_width == res_width
    assert frame_height == res_height


def test_new_roi_for_resolution():
    old_resolution = (2000, 4000)
    old_roi_full = (0, 0, 2000, 4000)
    old_roi_partial = (20, 40, 200, 400)

    new_resolution_up = (4000, 6000)
    new_resolution_down = (1000, 3000)

    expected_up_roi_full = (0, 0, 4000, 6000)
    expected_up_roi_partial = (40, 60, 400, 600)
    expected_down_roi_full = (0, 0, 1000, 3000)
    expected_down_roi_partial = (10, 30, 100, 300)

    assert (
        AbstractCamera.calculate_new_roi_for_resolution(old_resolution, old_roi_full, new_resolution_up)
        == expected_up_roi_full
    )
    assert (
        AbstractCamera.calculate_new_roi_for_resolution(old_resolution, old_roi_partial, new_resolution_up)
        == expected_up_roi_partial
    )
    assert (
        AbstractCamera.calculate_new_roi_for_resolution(old_resolution, old_roi_full, new_resolution_down)
        == expected_down_roi_full
    )
    assert (
        AbstractCamera.calculate_new_roi_for_resolution(old_resolution, old_roi_partial, new_resolution_down)
        == expected_down_roi_partial
    )
