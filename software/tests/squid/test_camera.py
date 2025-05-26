from typing import Optional, Sequence

import squid.camera.utils
import squid.config
from squid.abc import AbstractCamera, CameraFrame
from squid.camera.utils import SimulatedCamera
from squid.config import CameraConfig


def test_create_simulated_camera():
    sim_cam = squid.camera.utils.get_camera(squid.config.get_camera_config(), simulated=True)


def test_simulated_camera():
    sim_cam_config: CameraConfig = squid.config.get_camera_config().model_copy(
        update={"rotate_image_angle": None, "flip": None}
    )
    sim_cam = squid.camera.utils.get_camera(sim_cam_config, simulated=True)

    # Really basic tests to make sure the simulated camera does what is expected.
    sim_cam.send_trigger()
    assert sim_cam.read_frame() is not None
    frame_id = sim_cam.get_frame_id()
    sim_cam.send_trigger()
    assert sim_cam.read_frame() is not None
    assert sim_cam.get_frame_id() != frame_id

    sim_cam.send_trigger()
    frame = sim_cam.read_frame()
    (frame_height, frame_width, *_) = frame.shape
    (res_width, res_height) = sim_cam.get_resolution()

    assert frame_width == res_width
    assert frame_height == res_height


def test_new_roi_for_binning():
    old_binning = (2, 2)  # Base binning
    old_roi_full = (0, 0, 1500, 3000)
    old_roi_partial = (30, 60, 300, 600)

    new_binning_up = (1, 1)  # Decreasing binning (increasing resolution)
    new_binning_down = (3, 3)  # Increasing binning (decreasing resolution)

    expected_up_roi_full = (0, 0, 3000, 6000)
    expected_up_roi_partial = (60, 120, 600, 1200)
    expected_down_roi_full = (0, 0, 1000, 2000)
    expected_down_roi_partial = (20, 40, 200, 400)

    assert (
        AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi_full, new_binning_up) == expected_up_roi_full
    )
    assert (
        AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi_partial, new_binning_up)
        == expected_up_roi_partial
    )
    assert (
        AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi_full, new_binning_down)
        == expected_down_roi_full
    )
    assert (
        AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi_partial, new_binning_down)
        == expected_down_roi_partial
    )


class SimulatedWithTimeouts(SimulatedCamera):
    def __init__(self, timeout_ids: Sequence[int], *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._timeout_ids = list(timeout_ids)

    def read_camera_frame(self) -> Optional[CameraFrame]:
        frame = super().read_camera_frame()

        if frame.frame_id in self._timeout_ids:
            return None
        return frame


def test_read_frame_on_timeout():
    sim_cam = SimulatedWithTimeouts(
        timeout_ids=[3, 5, 8],
        camera_config=squid.config.get_camera_config(),
        hw_trigger_fn=None,
        hw_set_strobe_delay_ms_fn=None,
    )

    def do_frame():
        sim_cam.send_trigger()
        return sim_cam.read_frame()

    frames = [do_frame() for _ in range(10)]

    def frame_to_idx(frame_id):
        return frame_id - 1

    assert frames[frame_to_idx(1)] is not None
    assert frames[frame_to_idx(2)] is not None
    assert frames[frame_to_idx(3)] is None
    assert frames[frame_to_idx(4)] is not None
    assert frames[frame_to_idx(5)] is None
    assert frames[frame_to_idx(6)] is not None
    assert frames[frame_to_idx(7)] is not None
    assert frames[frame_to_idx(8)] is None
    assert frames[frame_to_idx(9)] is not None
    assert frames[frame_to_idx(10)] is not None
