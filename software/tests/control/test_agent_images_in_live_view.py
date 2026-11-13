"""In an agent session the images that the agent takes show in the live view of the window.

The display takes a camera frame only while live view runs or Snap works (LiveController.should_display_frames).
An image that a client takes over the control server did not open that gate, so a person who watched the window
saw nothing of what the agent imaged."""

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from control.core.agent_session import AgentSession


# --- LiveController.frames_reach_display ---


def _live(callbacks_enabled=False):
    from control.core.live_controller import LiveController

    callbacks = {}
    camera = MagicMock()
    camera.get_callbacks_enabled.return_value = callbacks_enabled

    def add(callback):
        callbacks[len(callbacks) + 1] = callback
        return len(callbacks)

    camera.add_frame_callback.side_effect = add
    camera.remove_frame_callback.side_effect = lambda callback_id: callbacks.pop(callback_id)
    return LiveController(SimpleNamespace(), camera), camera, callbacks


def test_frames_reach_the_display_inside_the_block_only():
    live, _, callbacks = _live()
    assert not live.should_display_frames()
    with live.frames_reach_display():
        assert live.should_display_frames()
        with live.frames_reach_display():  # a second capture at the same time
            for callback in list(callbacks.values()):
                callback(object())
        assert live.should_display_frames()  # the first block still holds the gate open
        for callback in list(callbacks.values()):
            callback(object())
    assert not live.should_display_frames()


def test_the_block_turns_the_frame_callbacks_on_and_puts_them_back():
    live, camera, callbacks = _live(callbacks_enabled=False)  # e.g. turned off by the laser autofocus
    with live.frames_reach_display():
        camera.enable_callbacks.assert_called_once_with(True)
        for callback in list(callbacks.values()):
            callback(object())
    assert camera.enable_callbacks.call_args_list[-1].args == (False,)
    assert callbacks == {}  # its own callback is removed


def test_the_block_ends_only_after_the_callbacks_of_its_frame_ran():
    """read_frame() can return just before the callbacks that put the frame on the screen run."""
    live, _, callbacks = _live()
    gate_at_callback = []

    def late_callbacks():
        time.sleep(0.15)
        gate_at_callback.append(live.should_display_frames())
        for callback in list(callbacks.values()):
            callback(object())

    began = time.monotonic()
    with live.frames_reach_display():
        threading.Thread(target=late_callbacks).start()
    assert 0.1 < time.monotonic() - began < 0.9
    assert gate_at_callback == [True]  # the frame found the gate open


def test_a_block_that_fails_does_not_wait_and_closes_the_gate():
    live, camera, callbacks = _live()
    began = time.monotonic()
    with pytest.raises(RuntimeError, match="no frame"):
        with live.frames_reach_display():
            raise RuntimeError("no frame")
    assert time.monotonic() - began < 0.5
    assert not live.should_display_frames() and callbacks == {}


# --- the control server: only in an agent session ---


@pytest.fixture
def server():
    from control.microscope_control_server import MicroscopeControlServer

    microscope = MagicMock()
    microscope.agent_session = AgentSession()
    microscope.stage.get_pos.return_value = MagicMock(x_mm=1.0, y_mm=2.0, z_mm=0.5)
    microscope.camera.get_exposure_time.return_value = 10.0
    microscope.live_controller.is_live = False
    microscope.live_controller.currentConfiguration.name = "Fluorescence 561 nm Ex"
    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        srv = MicroscopeControlServer()
    srv._log = MagicMock()
    srv._burst_lock = threading.Lock()
    srv.microscope = microscope
    srv.multipoint_controller = MagicMock()
    srv.multipoint_controller.acquisition_in_progress.return_value = False
    return srv


def _record_display(server, events):
    @contextmanager
    def shown():
        events.append("display on")
        try:
            yield
        finally:
            events.append("display off")

    server.microscope.live_controller.frames_reach_display.side_effect = shown
    server.microscope.acquire_image.side_effect = lambda: events.append("image") or np.zeros((4, 4), dtype=np.uint16)


def test_an_image_of_the_agent_goes_to_the_live_view(server):
    events = []
    _record_display(server, events)
    server.microscope.agent_session.begin()
    assert server._cmd_acquire_image()["acquired"] is True
    assert events == ["display on", "image", "display off"]


def test_with_no_agent_session_an_image_of_a_client_does_not_change_the_display(server):
    events = []
    _record_display(server, events)
    server._cmd_acquire_image()
    assert events == ["image"]
    server.microscope.live_controller.frames_reach_display.assert_not_called()


def test_a_burst_of_the_agent_goes_to_the_live_view_in_one_block(server, tmp_path):
    events = []
    _record_display(server, events)
    server.microscope.agent_session.begin()
    server._cmd_acquire_burst(count=3, save_dir=str(tmp_path))
    assert events == ["display on", "image", "image", "image", "display off"]


def test_with_no_agent_session_a_burst_does_not_change_the_display(server, tmp_path):
    events = []
    _record_display(server, events)
    server._cmd_acquire_burst(count=2, save_dir=str(tmp_path))
    assert events == ["image", "image"]


# --- the light-on burst starts the camera stream too ---


def test_the_light_on_series_starts_the_stream_of_a_camera_that_does_not_stream():
    from control._def import TriggerMode
    from control.microscope import Microscope

    events = []
    state = {"streaming": False}

    class Hold:
        def __enter__(self):
            events.append("hold")
            return self

        def __exit__(self, *exc):
            return False

        def wait_until_ready(self):
            return True

    def start():
        events.append("start stream")
        state["streaming"] = True

    def trigger():
        assert state["streaming"], "Camera is not streaming, cannot send trigger."
        events.append("trigger")

    camera = MagicMock()
    camera.get_is_streaming.side_effect = lambda: state["streaming"]
    camera.start_streaming.side_effect = start
    camera.get_ready_for_trigger.side_effect = lambda: state["streaming"]
    camera.send_trigger.side_effect = trigger
    camera.read_frame.side_effect = lambda: np.zeros((2, 2), dtype=np.uint16)
    live = MagicMock()
    live.trigger_mode = TriggerMode.SOFTWARE
    live.use_current_channel.side_effect = lambda name: Hold()
    live.turn_on_illumination.side_effect = lambda: events.append("light on")
    scope = SimpleNamespace(live_controller=live, camera=camera, _wait_for_microcontroller=lambda: None)
    with Microscope.illumination_kept_on(scope, "acquire_burst") as take_frame:
        take_frame()
    assert events == ["start stream", "hold", "light on", "trigger"]  # the stream first, then the light
    camera.stop_streaming.assert_not_called()  # left on, as after Live in the software trigger mode
