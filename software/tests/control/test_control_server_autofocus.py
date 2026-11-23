"""autofocus over the control server: the contrast autofocus of Squid, on frames in memory.

A client that focuses with move + acquire_image for each plane needs about 3 s for each plane (a file for each
frame). The window's Autofocus button does the same sweep on frames in memory in about the time of the images."""

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
import tifffile

import control._def
from control._def import FocusMeasureOperator, TriggerMode
from control.core.auto_focus_controller import AutoFocusController
from squid.stage.reservation import StageMotionRefused, StageMotionReservation

FOCUS_Z_MM = 1.2000


class FakeStage:
    def __init__(self, z_mm):
        self.z_mm = z_mm

    def move_z(self, delta_mm, blocking=True):
        self.z_mm += delta_mm

    def move_z_to(self, z_mm, blocking=True):
        self.z_mm = z_mm

    def get_pos(self):
        return SimpleNamespace(x_mm=10.0, y_mm=20.0, z_mm=self.z_mm)

    def wait_for_idle(self, timeout_s):
        pass


class SteppedStage(FakeStage):
    """Rounds each move to its motor steps, as the real stage does."""

    STEP_MM = 0.000136

    def move_z(self, delta_mm, blocking=True):
        self.z_mm += round(delta_mm / self.STEP_MM) * self.STEP_MM


class FakeCamera:
    """A fine texture that blurs with the distance from the focus plane: 1 pixel of blur for each 2 um."""

    def __init__(self, stage, frames=True):
        self._stage = stage
        self._frames = frames
        rng = np.random.default_rng(1)
        self._scene = rng.integers(200, 4000, (400, 400)).astype(np.float32)
        self.callbacks_enabled = True
        self.frames_read = 0
        self.streaming = True

    def get_is_streaming(self):
        return self.streaming

    def start_streaming(self):
        self.streaming = True

    def send_trigger(self, illumination_time=None):
        pass

    def read_frame(self):
        self.frames_read += 1
        if not self._frames or not self.streaming:  # a real camera refuses a trigger when it does not stream
            return None
        blur = abs(self._stage.z_mm - FOCUS_Z_MM) * 1000.0 / 2.0
        image = cv2.GaussianBlur(self._scene, (0, 0), blur) if blur > 0.05 else self._scene
        return image.astype(np.uint16)

    def get_callbacks_enabled(self):
        return self.callbacks_enabled

    def enable_callbacks(self, enabled):
        self.callbacks_enabled = enabled

    def get_exposure_time(self):
        return 10.0


class PausedCamera(FakeCamera):
    """Its frames wait until the test lets them go."""

    def __init__(self, stage, frames=True):
        super().__init__(stage, frames)
        self.reading = threading.Event()
        self.go = threading.Event()

    def read_frame(self):
        self.reading.set()
        assert self.go.wait(20)
        return super().read_frame()


class Hold:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait_until_ready(self, cancel_fn=None):
        return True


def make_server(start_um_from_focus=4.0, frames=True, gui=False, stage_class=FakeStage, camera_class=FakeCamera):
    from control.microscope_control_server import MicroscopeControlServer

    stage = stage_class(FOCUS_Z_MM + start_um_from_focus / 1000.0)
    camera = camera_class(stage, frames)
    live = MagicMock()
    live.is_live = False
    live.trigger_mode = TriggerMode.SOFTWARE
    live.use_current_channel.side_effect = lambda name: Hold()
    controller_board = MagicMock()
    controller_board.is_busy.return_value = False
    microscope = SimpleNamespace(
        camera=camera,
        stage=stage,
        live_controller=live,
        low_level_drivers=SimpleNamespace(microcontroller=controller_board),
        addons=SimpleNamespace(nl5=None),
        stage_motion=StageMotionReservation(),  # who drives the stage: one owner at a time
    )
    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        server = MicroscopeControlServer()
    server._log = MagicMock()
    server.microscope = microscope
    server._own_autofocus_controller = None  # as __init__ makes it
    server.gui = None
    if gui:  # the window has its own controller, with the values of its Autofocus panel
        shown = []
        window_controller = AutoFocusController(camera, stage, live, controller_board, lambda: None, shown.append, None)
        server.gui = SimpleNamespace(autofocusController=window_controller, shown=shown)
    return server, stage, camera


def test_autofocus_moves_to_the_sharpest_plane_and_reports_the_curve():
    server, stage, camera = make_server(start_um_from_focus=4.0)
    start_z = stage.z_mm
    result = server._cmd_autofocus(n_planes=11, step_um=1.5)
    assert abs(stage.z_mm - FOCUS_Z_MM) * 1000.0 <= 1.5  # within one step of the focus plane
    assert result["z_mm"] == pytest.approx(stage.z_mm)
    assert result["moved_um"] == pytest.approx((stage.z_mm - start_z) * 1000.0)
    planes = result["planes"]
    assert 3 <= len(planes) <= 11 and all(set(plane) == {"z_mm", "focus_measure"} for plane in planes)
    measures = [plane["focus_measure"] for plane in planes]
    assert result["best_index"] == measures.index(max(measures))
    assert planes[result["best_index"]]["z_mm"] == pytest.approx(stage.z_mm, abs=1e-6)
    steps = np.diff([plane["z_mm"] for plane in planes]) * 1000.0
    assert steps == pytest.approx([1.5] * (len(planes) - 1))  # from below, one step each
    assert result["measure"] == control._def.FOCUS_MEASURE_OPERATOR.name and result["seconds"] >= 0
    assert result["stopped_early"] == (len(planes) < 11)
    assert camera.frames_read == len(planes)  # frames in memory: one for each plane, no file


def test_the_z_of_each_plane_is_the_position_that_the_stage_reported_for_its_frame():
    # A real stage rounds each move to its motor steps: 1.524 um becomes 11 steps of 0.136 um = 1.496 um.
    server, stage, camera = make_server(start_um_from_focus=-3.0, stage_class=SteppedStage)
    z_of_frames = []
    read_frame = camera.read_frame

    def read_frame_and_note_z():
        z_of_frames.append(stage.z_mm)
        return read_frame()

    camera.read_frame = read_frame_and_note_z
    result = server._cmd_autofocus(n_planes=10, step_um=1.524)
    assert [plane["z_mm"] for plane in result["planes"]] == pytest.approx(z_of_frames, abs=1e-9)


def test_autofocus_uses_the_focus_measure_of_the_call_and_then_the_setting_again(monkeypatch):
    import control.utils

    server, _, _ = make_server()
    used = []
    measure = control.utils.calculate_focus_measure
    monkeypatch.setattr(
        control.utils, "calculate_focus_measure", lambda image, method: used.append(method) or measure(image, method)
    )
    assert server._cmd_autofocus(n_planes=5, step_um=2.0, measure="tenengrad")["measure"] == "TENENGRAD"
    assert set(used) == {FocusMeasureOperator.TENENGRAD}
    used.clear()
    assert server._cmd_autofocus(n_planes=5, step_um=2.0)["measure"] == control._def.FOCUS_MEASURE_OPERATOR.name
    assert set(used) == {control._def.FOCUS_MEASURE_OPERATOR}
    with pytest.raises(ValueError, match="LAPE, GLVA, TENENGRAD"):
        server._cmd_autofocus(measure="sharpness")


def test_autofocus_saves_the_centre_of_each_plane_when_a_folder_is_given(tmp_path):
    server, _, _ = make_server()
    result = server._cmd_autofocus(n_planes=7, step_um=2.0, save_dir=str(tmp_path / "planes"))
    paths = [plane["path"] for plane in result["planes"]]
    assert [os.path.basename(path) for path in paths] == [f"plane_{i:02d}.tiff" for i in range(len(paths))]
    assert all(tifffile.imread(path).shape == (400, 400) for path in paths)  # the crop that the measure used
    server2, _, _ = make_server()
    assert all("path" not in plane for plane in server2._cmd_autofocus(n_planes=7, step_um=2.0)["planes"])


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"n_planes": 2}, "n_planes"),
        ({"n_planes": 51}, "n_planes"),
        ({"n_planes": 5.0}, "n_planes"),
        ({"step_um": 0}, "step_um"),
        ({"step_um": 51}, "step_um"),
        ({"step_um": float("nan")}, "step_um"),
        ({"save_dir": "relative/folder"}, "save_dir"),
    ],
)
def test_autofocus_checks_its_inputs(kwargs, message):
    server, stage, camera = make_server()
    start_z = stage.z_mm
    with pytest.raises(ValueError, match=message):
        server._cmd_autofocus(**kwargs)
    assert stage.z_mm == start_z and camera.frames_read == 0


@pytest.mark.parametrize(
    "holder",
    ["an acquisition is running", "the stage is at the loading position", "the stage is homing"],
)
def test_autofocus_is_refused_while_something_else_holds_the_stage(holder):
    server, stage, camera = make_server()
    start_z = stage.z_mm
    hold = server.microscope.stage_motion.take(holder)  # as the acquisition, the loading position and the homing do
    with pytest.raises(StageMotionRefused, match=holder):
        server._cmd_autofocus()
    assert stage.z_mm == start_z and camera.frames_read == 0 and server._own_autofocus_controller is None
    hold.release()
    assert server._cmd_autofocus(n_planes=5, step_um=2.0)["planes"]  # the stage is free: the autofocus runs


def test_autofocus_is_refused_while_the_autofocus_of_the_window_runs():
    server, stage, camera = make_server(gui=True)
    server.gui.autofocusController.autofocus_in_progress = True  # the Autofocus button of the window
    with pytest.raises(RuntimeError, match="an autofocus is running"):
        server._cmd_autofocus()
    assert camera.frames_read == 0 and server.microscope.stage_motion.holder is None


def test_autofocus_holds_the_stage_for_the_whole_sweep_and_a_second_client_is_refused():
    server, _, camera = make_server(camera_class=PausedCamera)
    results, errors = [], []

    def client():
        try:
            results.append(server._cmd_autofocus(n_planes=5, step_um=2.0))
        except RuntimeError as error:
            errors.append(error)

    first = threading.Thread(target=client)
    first.start()
    assert camera.reading.wait(10)  # the sweep of the first client waits for its first frame
    assert server.microscope.stage_motion.holder == "an autofocus from the control server is running"
    client()  # the second client, at the same time
    camera.go.set()
    first.join(20)
    assert len(results) == 1 and len(errors) == 1  # exactly one autofocus, and one refusal
    assert isinstance(errors[0], StageMotionRefused) and "an autofocus from the control server" in str(errors[0])
    assert server.microscope.stage_motion.holder is None


# The thread of the sweep raises the error of the camera, as it did before: the uncaught-exception log has it.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_sweep_that_fails_after_some_planes_is_an_error_and_the_stage_goes_back():
    server, stage, camera = make_server()
    start_z = stage.z_mm
    read_frame = camera.read_frame

    def fails_at_the_third_frame():
        if camera.frames_read == 2:
            raise RuntimeError("the camera lost its connection")
        return read_frame()

    camera.read_frame = fails_at_the_third_frame
    with pytest.raises(RuntimeError, match="autofocus failed: the camera lost its connection"):
        server._cmd_autofocus(n_planes=9, step_um=2.0)
    assert stage.z_mm == pytest.approx(start_z)  # not left in the middle of the sweep
    assert server.microscope.stage_motion.holder is None


def test_an_autofocus_that_runs_too_long_is_stopped_and_the_stage_is_free_again(monkeypatch):
    server, _, camera = make_server()
    read_frame = camera.read_frame
    camera.read_frame = lambda: time.sleep(0.05) or read_frame()
    monkeypatch.setattr(type(server), "_AUTOFOCUS_TIMEOUT_S", 0.12)
    with pytest.raises(RuntimeError, match="did not end in .* and was stopped"):
        server._cmd_autofocus(n_planes=50, step_um=0.1)
    assert server._own_autofocus_controller.autofocus_in_progress is False and camera.frames_read < 50
    assert server.microscope.stage_motion.holder is None


def test_an_autofocus_that_does_not_stop_keeps_the_stage_until_it_ends(monkeypatch):
    server, _, camera = make_server(camera_class=PausedCamera)  # a camera that does not answer
    monkeypatch.setattr(type(server), "_AUTOFOCUS_TIMEOUT_S", 0.1)
    monkeypatch.setattr(type(server), "_AUTOFOCUS_STOP_S", 0.1)
    with pytest.raises(RuntimeError, match="does not stop"):
        server._cmd_autofocus(n_planes=5, step_um=2.0)
    assert server.microscope.stage_motion.holder == "an autofocus that did not stop still runs"
    with pytest.raises(StageMotionRefused, match="did not stop"):
        server._cmd_autofocus(n_planes=5, step_um=2.0)  # nothing else moves the stage under the sweep
    camera.go.set()  # the camera answers at last: the sweep ends
    deadline = time.monotonic() + 20
    while server.microscope.stage_motion.holder is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.microscope.stage_motion.holder is None


def test_autofocus_starts_the_camera_stream_when_it_is_off():
    server, _, camera = make_server()
    camera.streaming = False  # as after the stop_live of a client
    result = server._cmd_autofocus(n_planes=5, step_um=2.0)
    assert camera.streaming is True and result["planes"]  # the frames came


def test_the_sweep_of_the_window_reads_no_position_for_each_plane_and_keeps_no_planes():
    # The Autofocus button and the autofocus of an acquisition work as before: the planes are for the command only.
    server, stage, _ = make_server(gui=True)
    controller = server.gui.autofocusController
    reads = []
    get_pos = stage.get_pos
    stage.get_pos = lambda: reads.append(1) or get_pos()
    controller.set_N(5)
    controller.set_deltaZ(2.0)
    controller.autofocus(focus_map_override=True)
    controller.wait_till_autofocus_has_completed()
    assert reads == [] and controller.last_planes == []
    assert server._cmd_autofocus(n_planes=5, step_um=2.0)["planes"]  # the command still gets its planes
    assert controller.report_planes is False  # and the window's sweep is as before again


def test_a_camera_that_gives_no_frame_is_an_error_and_the_stage_goes_back():
    server, stage, _ = make_server(frames=False)
    start_z = stage.z_mm
    with pytest.raises(RuntimeError, match="no frame"):
        server._cmd_autofocus(n_planes=5, step_um=2.0)
    assert stage.z_mm == pytest.approx(start_z)  # not left at the bottom of the range


def test_with_a_window_its_controller_is_used_and_its_panel_values_stay():
    server, stage, _ = make_server(gui=True)
    controller = server.gui.autofocusController
    controller.set_N(10)
    controller.set_deltaZ(1.524)
    result = server._cmd_autofocus(n_planes=7, step_um=2.0, measure="TENENGRAD")
    assert len(server.gui.shown) == len(result["planes"])  # each plane went to the display of the window
    assert (controller.N, controller.deltaZ) == (10, pytest.approx(0.001524))
    assert controller.focus_measure_operator is None and controller.keep_plane_images is False
    assert abs(stage.z_mm - FOCUS_Z_MM) * 1000.0 <= 2.0
