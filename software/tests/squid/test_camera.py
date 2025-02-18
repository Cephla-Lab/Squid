import squid.camera.utils
import squid.config


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
    (frame_width, frame_height, *_) = frame.shape
    (res_width, res_height) = sim_cam.get_resolution()

    assert frame_width == res_width
    assert frame_height == res_height
