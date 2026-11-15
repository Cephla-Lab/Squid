"""Microscope.acquire_image starts the camera stream when it is off.

A real camera refuses a trigger when it does not stream, and nothing starts the stream before the first live
view. The simulated camera takes the trigger anyway, so the fault showed only on a microscope: after a fresh
start, each image over the control server failed with "Camera is not streaming, cannot send trigger" until Live
was pressed one time in the window."""

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from control._def import TriggerMode
from control.microscope import Microscope


def _scope(trigger_mode, streaming, camera_starts=True, frame=np.zeros((2, 2), dtype=np.uint16)):
    """What acquire_image needs, with a camera that refuses a trigger when it does not stream."""
    events = []
    state = {"streaming": streaming}

    class Hold:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def wait_until_ready(self):
            return True

    def start():
        events.append("start stream")
        state["streaming"] = camera_starts

    def stop():
        events.append("stop stream")
        state["streaming"] = False

    def trigger(*args, **kwargs):
        if not state["streaming"]:
            raise RuntimeError("Camera is not streaming, cannot send trigger.")
        events.append("trigger")

    def read():
        events.append("frame")
        return frame

    camera = MagicMock()
    camera.get_is_streaming.side_effect = lambda: state["streaming"]
    camera.start_streaming.side_effect = start
    camera.stop_streaming.side_effect = stop
    camera.send_trigger.side_effect = trigger
    camera.read_frame.side_effect = read
    camera.get_exposure_time.return_value = 10.0
    live = MagicMock()
    live.trigger_mode = trigger_mode
    live.use_current_channel.side_effect = lambda name: Hold()
    controller = MagicMock()
    controller.send_hardware_trigger.side_effect = lambda **kwargs: events.append("hardware trigger")
    scope = SimpleNamespace(
        live_controller=live,
        camera=camera,
        low_level_drivers=SimpleNamespace(microcontroller=controller),
        _wait_for_microcontroller=lambda: None,
        _log=MagicMock(),
    )
    return scope, events


def test_an_image_starts_the_stream_of_a_camera_that_does_not_stream():
    scope, events = _scope(TriggerMode.SOFTWARE, streaming=False)
    assert Microscope.acquire_image(scope).shape == (2, 2)
    # The stream stays on, as after Live in this mode: the camera waits for the next trigger.
    assert events == ["start stream", "trigger", "frame"]


def test_an_image_does_not_start_a_stream_that_runs():
    scope, events = _scope(TriggerMode.SOFTWARE, streaming=True)
    Microscope.acquire_image(scope)
    assert events == ["trigger", "frame"]


def test_the_hardware_trigger_mode_gets_the_stream_too():
    scope, events = _scope(TriggerMode.HARDWARE, streaming=False)
    Microscope.acquire_image(scope)
    assert events == ["start stream", "hardware trigger", "frame"]


def test_in_the_continuous_mode_an_image_does_not_start_the_stream():
    """There the camera runs free, and only Live turns the light on. A stream started here gave a dark frame."""
    scope, events = _scope(TriggerMode.CONTINUOUS, streaming=False)
    with pytest.raises(RuntimeError, match="start Live first"):
        Microscope.acquire_image(scope)
    assert events == []  # no stream, no frame
    scope.live_controller.use_current_channel.assert_not_called()
    scope, events = _scope(TriggerMode.CONTINUOUS, streaming=True)  # live view is on: it gives the frame
    Microscope.acquire_image(scope)
    assert events == ["frame"]


@pytest.mark.parametrize("trigger_mode", [TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS])
def test_an_image_never_stops_the_stream(trigger_mode):
    """Live can start while the image waits for its frame, and then uses the same stream."""
    scope, _ = _scope(trigger_mode, streaming=False)
    with contextlib.suppress(RuntimeError):
        Microscope.acquire_image(scope)
    scope.camera.stop_streaming.assert_not_called()


def test_a_camera_that_does_not_start_its_stream_gives_a_clear_error():
    scope, events = _scope(TriggerMode.SOFTWARE, streaming=False, camera_starts=False)
    with pytest.raises(RuntimeError, match="did not start streaming"):
        Microscope.acquire_image(scope)
    assert events == ["start stream"]  # no light, no trigger
    scope.live_controller.use_current_channel.assert_not_called()


def test_the_light_goes_off_when_the_trigger_fails():
    """Found with the fault above: the light went on, the trigger was refused, and nothing turned the light off."""
    scope, _ = _scope(TriggerMode.SOFTWARE, streaming=True)
    scope.camera.send_trigger.side_effect = RuntimeError("Requested trigger too early")
    with pytest.raises(RuntimeError, match="too early"):
        Microscope.acquire_image(scope)
    scope.live_controller.turn_on_illumination.assert_called_once()
    scope.live_controller.turn_off_illumination.assert_called_once()
