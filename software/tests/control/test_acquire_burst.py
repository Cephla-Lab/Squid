"""acquire_burst: a series of frames at the current position."""

import os
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import tifffile

from control.core.agent_session import AgentSession, SessionState


@pytest.fixture
def server():
    from control.microscope_control_server import MicroscopeControlServer

    microscope = MagicMock()
    microscope.agent_session = AgentSession()
    microscope.stage.get_pos.return_value = MagicMock(x_mm=1.0, y_mm=2.0, z_mm=0.5)
    microscope.camera.get_exposure_time.return_value = 10.0
    microscope.live_controller.is_live = False
    microscope.live_controller.currentConfiguration.name = "Fluorescence 638 nm Ex"
    microscope.acquire_image.side_effect = lambda: np.full((4, 4), 7, dtype=np.uint16)
    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        srv = MicroscopeControlServer()
    srv._log = MagicMock()
    srv.microscope = microscope
    srv.multipoint_controller = MagicMock()
    srv.multipoint_controller.acquisition_in_progress.return_value = False
    return srv


def test_burst_saves_the_frames_and_reports_them(server, tmp_path):
    result = server._cmd_acquire_burst(count=3, save_dir=str(tmp_path / "b"), interval_ms=0, prefix="org7")
    paths = [f["path"] for f in result["frames"]]
    assert [os.path.basename(p) for p in paths] == ["org7_0000.tiff", "org7_0001.tiff", "org7_0002.tiff"]
    assert all(tifffile.imread(p).shape == (4, 4) for p in paths)
    times = [f["time_s"] for f in result["frames"]]
    assert times[0] == 0.0 and times == sorted(times) and result["stopped_early"] is False
    assert result["position"] == {"x_mm": 1.0, "y_mm": 2.0, "z_mm": 0.5}
    assert result["channel"] == "Fluorescence 638 nm Ex" and result["exposure_ms"] == 10.0


def test_burst_has_defaults_for_interval_and_prefix(server, tmp_path):
    result = server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))
    assert os.path.basename(result["frames"][0]["path"]) == "burst_0000.tiff"


def test_burst_keeps_the_interval(server, tmp_path):
    result = server._cmd_acquire_burst(count=3, save_dir=str(tmp_path), interval_ms=50, prefix="burst")
    assert 0.09 <= result["frames"][2]["time_s"] <= 0.75  # generous upper limit for a busy CI runner


def test_burst_stops_at_the_next_frame_after_a_take_over(server, tmp_path):
    session = server.microscope.agent_session
    session.begin()
    calls = []

    def capture():
        calls.append(1)
        if len(calls) == 2:
            session.request_stop(SessionState.HUMAN, "take over")
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.acquire_image.side_effect = capture
    result = server._cmd_acquire_burst(count=100, save_dir=str(tmp_path), interval_ms=0, prefix="burst")
    assert len(result["frames"]) == 2 and result["stopped_early"] is True


def test_a_take_over_ends_the_wait_between_frames(server, tmp_path):
    session = server.microscope.agent_session
    session.begin()

    def capture():
        session.request_stop(SessionState.HUMAN, "take over")
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.acquire_image.side_effect = capture
    start = time.monotonic()
    result = server._cmd_acquire_burst(count=3, save_dir=str(tmp_path), interval_ms=2000, prefix="burst")
    assert time.monotonic() - start < 0.5 and len(result["frames"]) == 1 and result["stopped_early"] is True


def test_burst_does_not_continue_after_a_handover(server, tmp_path):
    session = server.microscope.agent_session
    session.begin()
    calls = []

    def capture():
        calls.append(1)
        if len(calls) == 1:  # the human takes over, unlocks, and hands back before the next frame
            session.request_stop(SessionState.HUMAN, "take over")
            session.finish_stop({})
            session.hand_back({})
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.acquire_image.side_effect = capture
    result = server._cmd_acquire_burst(count=5, save_dir=str(tmp_path), interval_ms=0, prefix="burst")
    assert session.state is SessionState.AGENT and len(result["frames"]) == 1 and result["stopped_early"] is True


def test_burst_is_refused_during_an_acquisition_and_with_live_view(server, tmp_path):
    server.multipoint_controller.acquisition_in_progress.return_value = True
    with pytest.raises(RuntimeError, match="^an acquisition is running$"):
        server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))
    server.multipoint_controller.acquisition_in_progress.return_value = False
    server.microscope.live_controller.is_live = True
    with pytest.raises(RuntimeError, match="^live view is on$"):
        server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))
    server.microscope.acquire_image.assert_not_called()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"count": 0}, "count"),
        ({"count": 1001}, "count"),
        ({"count": 1, "interval_ms": -1}, "interval_ms"),
        ({"count": 1, "prefix": "../x"}, "prefix"),
        ({"count": 1, "prefix": ""}, "prefix"),
    ],
)
def test_burst_checks_its_inputs(server, tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        server._cmd_acquire_burst(save_dir=str(tmp_path), **kwargs)
    server.microscope.acquire_image.assert_not_called()


def test_burst_refuses_a_relative_folder(server):
    with pytest.raises(ValueError, match="save_dir must be an absolute path"):
        server._cmd_acquire_burst(count=1, save_dir="relative/folder")


def test_capture_error_says_how_many_frames_were_saved(server, tmp_path):
    frames = iter([np.zeros((4, 4), dtype=np.uint16), RuntimeError("camera.read_frame() returned None")])

    def capture():
        item = next(frames)
        if isinstance(item, Exception):
            raise item
        return item

    server.microscope.acquire_image.side_effect = capture
    with pytest.raises(RuntimeError, match="burst stopped after 1 frames: camera.read_frame"):
        server._cmd_acquire_burst(count=5, save_dir=str(tmp_path), interval_ms=0, prefix="burst")
    assert os.path.exists(tmp_path / "burst_0000.tiff")
