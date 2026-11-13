"""acquire_burst: a series of frames at the current position."""

import os
import threading
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
    srv._burst_lock = threading.Lock()  # as __init__ makes it
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
    # generous limits for a busy CI runner; the times are from frame to frame, the interval from start to start
    assert 0.08 <= result["frames"][2]["time_s"] <= 0.75


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
    """The take over comes while the burst waits for the time of its second frame (2 s after the first)."""
    session = server.microscope.agent_session
    session.begin()
    take_over = threading.Timer(0.2, lambda: session.request_stop(SessionState.HUMAN, "take over"))
    take_over.start()
    start = time.monotonic()
    try:
        result = server._cmd_acquire_burst(count=3, save_dir=str(tmp_path), interval_ms=2000, prefix="burst")
    finally:
        take_over.cancel()
    assert time.monotonic() - start < 1.0  # not the 2 s of the interval
    assert len(result["frames"]) == 1 and result["stopped_early"] is True


def test_a_late_frame_does_not_make_the_next_frames_come_at_once(server, tmp_path):
    """interval_ms is from the start of one frame to the start of the next. A frame that took longer (a light
    source that wakes up) must not be followed by frames with no time between them."""
    starts = []

    def capture():
        starts.append(time.monotonic())
        if len(starts) == 1:
            time.sleep(0.35)
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.acquire_image.side_effect = capture
    server._cmd_acquire_burst(count=4, save_dir=str(tmp_path), interval_ms=100, prefix="burst")
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps[0] >= 0.35 and all(gap >= 0.09 for gap in gaps[1:]), gaps


@pytest.mark.parametrize("interval_ms", [float("nan"), float("inf")])
def test_an_interval_that_is_not_a_finite_number_is_refused_and_the_burst_does_not_spin(server, tmp_path, interval_ms):
    outcome = []

    def call():
        try:
            server._cmd_acquire_burst(count=2, save_dir=str(tmp_path), interval_ms=interval_ms)
        except ValueError as error:
            outcome.append(str(error))

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(2)
    still_running = thread.is_alive()
    server.microscope.agent_session.begin()  # ends a burst that spins: its session is not the one of its start
    thread.join(2)
    assert not still_running and outcome and "interval_ms" in outcome[0]
    server.microscope.acquire_image.assert_not_called()


def test_a_second_burst_is_refused_while_one_runs(server, tmp_path):
    in_capture = threading.Event()
    release = threading.Event()

    def slow_capture():
        in_capture.set()
        release.wait(5)
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.acquire_image.side_effect = slow_capture
    first = []
    thread = threading.Thread(
        target=lambda: first.append(server._cmd_acquire_burst(count=1, save_dir=str(tmp_path / "a")))
    )
    thread.start()
    try:
        assert in_capture.wait(5)
        with pytest.raises(RuntimeError, match="^a burst is running$"):
            server._cmd_acquire_burst(count=1, save_dir=str(tmp_path / "b"))
    finally:
        release.set()
        thread.join(5)
    assert len(first[0]["frames"]) == 1 and not os.path.exists(tmp_path / "b")
    assert len(server._cmd_acquire_burst(count=1, save_dir=str(tmp_path / "c"))["frames"]) == 1  # free again


def test_a_burst_that_is_refused_or_fails_leaves_the_next_burst_free(server, tmp_path):
    server.microscope.live_controller.is_live = True
    with pytest.raises(RuntimeError, match="live view is on"):
        server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))
    server.microscope.live_controller.is_live = False
    server.microscope.acquire_image.side_effect = RuntimeError("no frame")
    with pytest.raises(RuntimeError, match="burst stopped after 0 frames"):
        server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))
    server.microscope.acquire_image.side_effect = lambda: np.zeros((4, 4), dtype=np.uint16)
    assert len(server._cmd_acquire_burst(count=1, save_dir=str(tmp_path))["frames"]) == 1


def test_the_light_source_is_held_one_time_for_the_whole_burst(server, tmp_path):
    """Each acquire_image() takes and releases the light source by itself. On a laser engine that opens and closes
    a mechanical shutter for each frame; one hold around the burst keeps it open."""
    events = []

    class Hold:
        def __enter__(self):
            events.append("hold")
            return self

        def __exit__(self, *exc):
            events.append("release")
            return False

    def capture():
        events.append("frame")
        if events.count("frame") == 3:
            raise RuntimeError("no frame")
        return np.zeros((4, 4), dtype=np.uint16)

    server.microscope.live_controller.use_current_channel.side_effect = lambda name: Hold()
    server.microscope.acquire_image.side_effect = capture
    with pytest.raises(RuntimeError, match="burst stopped after 2 frames"):
        server._cmd_acquire_burst(count=5, save_dir=str(tmp_path))
    assert events == ["hold", "frame", "frame", "frame", "release"]  # released also when a frame fails
    server.microscope.live_controller.use_current_channel.assert_called_once_with("acquire_burst")


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
        ({"count": True}, "count"),
        ({"count": 2.0}, "count"),
        ({"count": 1, "interval_ms": -1}, "interval_ms"),
        ({"count": 1, "interval_ms": 60001}, "interval_ms"),
        ({"count": 1, "interval_ms": "100"}, "interval_ms"),
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


def test_the_schema_tells_the_limits_and_has_a_complete_first_sentence(server):
    schema = server._cmd_acquire_burst._schema
    assert schema["description"].endswith(".") and "series of frames" in schema["description"]
    parameters = schema["parameters"]
    assert (parameters["count"]["minimum"], parameters["count"]["maximum"]) == (1, 1000)
    assert (parameters["interval_ms"]["minimum"], parameters["interval_ms"]["maximum"]) == (0, 60000)


# --- keep_light_on: the light stays on for the whole burst; the files are written in a second thread ---


def _light_block(server, events, take=None):
    """Make microscope.illumination_kept_on a block that records when the light goes on and off."""
    from contextlib import contextmanager

    def take_frame():
        events.append("frame")
        return np.full((4, 4), len(events), dtype=np.uint16)

    @contextmanager
    def kept_on(name):
        events.append(f"light on for {name}")
        try:
            yield take or take_frame
        finally:
            events.append("light off")

    server.microscope.illumination_kept_on.side_effect = kept_on


def test_keep_light_on_takes_all_frames_inside_one_light_block(server, tmp_path):
    events = []
    _light_block(server, events)
    result = server._cmd_acquire_burst(count=3, save_dir=str(tmp_path), keep_light_on=True)
    assert events == ["light on for acquire_burst", "frame", "frame", "frame", "light off"]
    server.microscope.acquire_image.assert_not_called()  # that turns the light on and off for each frame
    assert result["light_kept_on"] is True and len(result["frames"]) == 3
    assert [int(tifffile.imread(frame["path"])[0, 0]) for frame in result["frames"]] == [2, 3, 4]


def test_the_light_is_not_kept_on_by_default(server, tmp_path):
    result = server._cmd_acquire_burst(count=2, save_dir=str(tmp_path))
    assert result["light_kept_on"] is False
    assert server.microscope.acquire_image.call_count == 2
    server.microscope.illumination_kept_on.assert_not_called()


@pytest.mark.parametrize("value", [1, "true", None])
def test_keep_light_on_is_true_or_false(server, tmp_path, value):
    with pytest.raises(ValueError, match="keep_light_on"):
        server._cmd_acquire_burst(count=1, save_dir=str(tmp_path), keep_light_on=value)
    server.microscope.illumination_kept_on.assert_not_called()


def test_the_light_goes_off_when_a_frame_fails_and_the_frames_before_it_are_saved(server, tmp_path):
    events = []

    def take_frame():
        events.append("frame")
        if events.count("frame") == 3:
            raise RuntimeError("camera.read_frame() returned None")
        return np.zeros((4, 4), dtype=np.uint16)

    _light_block(server, events, take_frame)
    with pytest.raises(RuntimeError, match="burst stopped after 2 frames: camera.read_frame"):
        server._cmd_acquire_burst(count=5, save_dir=str(tmp_path), keep_light_on=True)
    assert events[-1] == "light off"
    assert sorted(os.listdir(tmp_path)) == ["burst_0000.tiff", "burst_0001.tiff"]


def test_a_take_over_stops_a_burst_that_keeps_the_light_on(server, tmp_path):
    session = server.microscope.agent_session
    session.begin()
    events = []

    def take_frame():
        events.append("frame")
        if events.count("frame") == 2:
            session.request_stop(SessionState.HUMAN, cause="take_over")
        return np.zeros((4, 4), dtype=np.uint16)

    _light_block(server, events, take_frame)
    result = server._cmd_acquire_burst(count=100, save_dir=str(tmp_path), keep_light_on=True)
    assert result["stopped_early"] is True and len(result["frames"]) == 2
    assert events == ["light on for acquire_burst", "frame", "frame", "light off"]


def test_the_files_are_written_by_a_second_thread_and_are_there_when_the_command_returns(server, tmp_path):
    writers = set()
    imwrite = tifffile.imwrite

    def slow_write(path, image):
        writers.add(threading.current_thread().name)
        time.sleep(0.02)
        imwrite(path, image)

    with patch.object(tifffile, "imwrite", slow_write):
        result = server._cmd_acquire_burst(count=5, save_dir=str(tmp_path))
    assert threading.current_thread().name not in writers and len(writers) == 1
    assert all(os.path.exists(frame["path"]) for frame in result["frames"]) and len(result["frames"]) == 5
    # The time between two frames does not have the 20 ms of a file write in it.
    assert result["frames"][-1]["time_s"] < 0.06


def test_a_file_that_can_not_be_written_stops_the_burst(server, tmp_path):
    imwrite = tifffile.imwrite
    taken = []

    def capture():
        taken.append(1)
        time.sleep(0.005)
        return np.zeros((4, 4), dtype=np.uint16)

    def write(path, image):
        if path.endswith("0001.tiff"):
            raise OSError("no space left on device")
        imwrite(path, image)

    server.microscope.acquire_image.side_effect = capture
    with patch.object(tifffile, "imwrite", write):
        with pytest.raises(RuntimeError, match="burst stopped after 1 frames: no space left on device"):
            server._cmd_acquire_burst(count=200, save_dir=str(tmp_path))
    assert len(taken) < 200  # it did not go on to the end
    assert os.listdir(tmp_path) == ["burst_0000.tiff"]


def test_the_camera_waits_when_the_disk_is_slower_than_the_camera(server, tmp_path, monkeypatch):
    import control.microscope_control_server as control_server

    frame = np.zeros((4, 4), dtype=np.uint16)
    monkeypatch.setattr(control_server, "_BURST_QUEUE_BYTES", 2 * frame.nbytes)  # room for 2 frames
    disk_is_free = threading.Event()
    imwrite = tifffile.imwrite
    taken = []

    def capture():
        taken.append(1)
        return frame

    def blocked_write(path, image):
        disk_is_free.wait(10)
        imwrite(path, image)

    server.microscope.acquire_image.side_effect = capture
    results = []
    with patch.object(tifffile, "imwrite", blocked_write):
        burst = threading.Thread(
            target=lambda: results.append(server._cmd_acquire_burst(count=6, save_dir=str(tmp_path)))
        )
        burst.start()
        time.sleep(0.4)
        assert len(taken) <= 3  # 2 frames wait for the disk; the third waits for room; no frame is dropped
        disk_is_free.set()
        burst.join(10)
    assert len(results[0]["frames"]) == 6 and len(os.listdir(tmp_path)) == 6


def _scope(trigger_mode, ready=True, frames=None, camera_ready=None):
    """What Microscope.illumination_kept_on needs, with a list of what happened."""
    from types import SimpleNamespace

    events = []

    class Hold:
        def __enter__(self):
            events.append("hold")
            return self

        def __exit__(self, *exc):
            events.append("release")
            return False

        def wait_until_ready(self):
            return ready

    live = MagicMock()
    live.trigger_mode = trigger_mode
    live.use_current_channel.side_effect = lambda name: Hold()
    live.turn_on_illumination.side_effect = lambda: events.append("light on")
    live.turn_off_illumination.side_effect = lambda: events.append("light off")
    camera = MagicMock()
    camera.get_ready_for_trigger.side_effect = camera_ready or (lambda: True)
    camera.send_trigger.side_effect = lambda: events.append("trigger")
    camera.read_frame.side_effect = frames or (lambda: np.zeros((2, 2), dtype=np.uint16))
    scope = SimpleNamespace(
        live_controller=live, camera=camera, _wait_for_microcontroller=lambda: events.append("controller done")
    )
    return scope, events


def test_the_microscope_keeps_the_light_on_for_a_series_of_frames():
    from control._def import TriggerMode
    from control.microscope import Microscope

    scope, events = _scope(TriggerMode.SOFTWARE)
    with Microscope.illumination_kept_on(scope, "acquire_burst") as take_frame:
        assert take_frame().shape == (2, 2)
        take_frame()
    assert events == ["hold", "light on", "controller done", "trigger", "trigger", "light off", "release"]
    scope.live_controller.use_current_channel.assert_called_once_with("acquire_burst")


def test_the_light_goes_off_and_the_source_is_released_after_an_error_in_the_series():
    from control._def import TriggerMode
    from control.microscope import Microscope

    scope, events = _scope(TriggerMode.SOFTWARE, frames=lambda: None)  # the camera gives no frame
    with pytest.raises(RuntimeError, match="read_frame"):
        with Microscope.illumination_kept_on(scope, "acquire_burst") as take_frame:
            take_frame()
    assert events[-2:] == ["light off", "release"]


def test_the_light_is_kept_on_in_the_software_trigger_mode_only():
    from control._def import TriggerMode
    from control.microscope import Microscope

    scope, events = _scope(TriggerMode.HARDWARE)
    with pytest.raises(RuntimeError, match="software trigger"):
        with Microscope.illumination_kept_on(scope, "acquire_burst"):
            pass
    assert events == []  # no light, no hold of the source


def test_the_light_does_not_go_on_when_the_source_does_not_become_ready():
    from control._def import TriggerMode
    from control.microscope import Microscope

    scope, events = _scope(TriggerMode.SOFTWARE, ready=False)
    with pytest.raises(RuntimeError, match="did not become ready"):
        with Microscope.illumination_kept_on(scope, "acquire_burst"):
            pass
    assert events == ["hold", "release"]


def test_a_frame_waits_until_the_camera_takes_a_trigger():
    from control._def import TriggerMode
    from control.microscope import Microscope

    answers = iter([False, False, True])
    scope, events = _scope(TriggerMode.SOFTWARE, camera_ready=lambda: next(answers))
    with Microscope.illumination_kept_on(scope, "acquire_burst") as take_frame:
        take_frame()
    assert scope.camera.get_ready_for_trigger.call_count == 3 and events.count("trigger") == 1


def test_the_light_goes_off_before_the_last_files_are_written(server, tmp_path):
    """The sample must not get light while the disk works: also after a frame that fails."""
    events = []
    light_is_off = threading.Event()
    imwrite = tifffile.imwrite

    def take_frame():
        if events.count("frame") == 2:
            raise RuntimeError("no frame")
        events.append("frame")
        return np.zeros((4, 4), dtype=np.uint16)

    from contextlib import contextmanager

    @contextmanager
    def kept_on(name):
        try:
            yield take_frame
        finally:
            events.append("light off")
            light_is_off.set()

    def slow_write(path, image):
        light_is_off.wait(2)  # the disk is slow: it writes nothing before the light is off
        events.append("file")
        imwrite(path, image)

    server.microscope.illumination_kept_on.side_effect = kept_on
    with patch.object(tifffile, "imwrite", slow_write):
        with pytest.raises(RuntimeError, match="burst stopped after 2 frames: no frame"):
            server._cmd_acquire_burst(count=5, save_dir=str(tmp_path), keep_light_on=True)
    assert events == ["frame", "frame", "light off", "file", "file"]
