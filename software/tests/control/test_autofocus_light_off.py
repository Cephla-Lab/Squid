"""The sweep of the contrast autofocus turns its light off after each plane, in the software trigger mode.

Also when the frame of a plane does not come, and when the camera raises."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control._def import TriggerMode
from control.core.auto_focus_worker import AutofocusWorker

PLANES = 5


class Light:
    """The illumination as the live controller switches it. `on` is its state now."""

    trigger_mode = TriggerMode.SOFTWARE

    def __init__(self):
        self.on = False

    def turn_on_illumination(self):
        self.on = True

    def turn_off_illumination(self):
        self.on = False


def make_worker(read_frame):
    light = Light()
    board = MagicMock()
    board.is_busy.return_value = False
    camera = SimpleNamespace(send_trigger=lambda: None, read_frame=read_frame)
    controller = SimpleNamespace(
        camera=camera,
        microcontroller=board,
        stage=MagicMock(),
        liveController=light,
        nl5=None,
        N=PLANES,
        deltaZ=0.001,
        crop_width=64,
        crop_height=64,
    )
    keep_running = threading.Event()
    keep_running.set()
    return AutofocusWorker(controller, lambda: None, lambda image: None, keep_running), light


def frames(missing):
    """A camera whose frames at the plane numbers in `missing` do not come."""
    count = iter(range(PLANES))
    rng = np.random.default_rng(1)

    def read_frame():
        return None if next(count) in missing else rng.integers(0, 4000, (64, 64)).astype(np.uint16)

    return read_frame


def test_the_light_is_off_after_a_sweep_with_all_its_frames():
    worker, light = make_worker(frames(missing=()))
    worker.run_autofocus()
    assert light.on is False


def test_the_light_is_off_when_the_last_frame_does_not_come():
    worker, light = make_worker(frames(missing=(PLANES - 1,)))
    worker.run_autofocus()
    assert light.on is False  # before: the light stayed on after the sweep


def test_the_light_is_off_when_no_frame_comes():
    worker, light = make_worker(frames(missing=range(PLANES)))
    worker.run_autofocus()
    assert light.on is False


def test_the_light_is_off_when_the_camera_raises():
    def read_frame():
        raise RuntimeError("the camera lost its connection")

    worker, light = make_worker(read_frame)
    with pytest.raises(RuntimeError, match="lost its connection"):
        worker.run_autofocus()
    assert light.on is False
