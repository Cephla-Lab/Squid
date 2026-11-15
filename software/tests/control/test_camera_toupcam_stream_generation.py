"""A ToupCam frame carries the run of the stream it was pulled in (CameraFrame.stream_generation).

The run number is read at the top of the frame callback, before the pull, so a restart that races the
callback cannot relabel a frame of the run before as one of the run after.
"""

import numpy as np

from squid.abc import CameraPixelFormat
from tests.control.toupcam_test_utils import make_callback_camera, skeleton_toupcam

_W, _H = 4, 2


def _callback_camera(stream_generation: int):
    pixels = np.zeros((_H, _W), dtype=np.uint16)
    cam, _ = make_callback_camera(
        CameraPixelFormat.MONO16, pixels.tobytes(), _W, _H, log_name="test_toupcam_stream_generation"
    )
    cam._stream_generation = stream_generation
    delivered = []
    cam._propogate_frame = delivered.append
    return cam, delivered


def test_a_pulled_frame_carries_the_run_of_the_stream_it_was_pulled_in():
    cam, delivered = _callback_camera(stream_generation=2)

    cam._on_frame_callback()

    [frame] = delivered
    assert frame.stream_generation == 2 == cam.stream_generation()


def test_starting_the_raw_stream_begins_a_new_run():
    cam = skeleton_toupcam("test_toupcam_stream_generation", _raw_camera_stream_started=False)
    assert cam.stream_generation() == 0  # never started: the number unstamped frames carry

    cam._start_raw_camera_stream()

    cam._camera.StartPullModeWithCallback.assert_called_once()
    assert cam.stream_generation() == 1
