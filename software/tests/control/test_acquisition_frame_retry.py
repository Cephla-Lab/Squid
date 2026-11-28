"""Software trigger: a trigger whose frame never comes is not the end of the acquisition.

Before, the first frame that did not come within 5x the frame time + 2 s aborted the whole acquisition, with no second
trigger (bench, 2026-11-25: a 4-channel confocal stack ended at its third image). Now the worker triggers again, then
restarts the camera's stream, then reopens the camera (each followed by a new trigger), and aborts only when the
frame of the last trigger does not come either. A restart or reopen that hangs aborts within its time limit, with the
light off.
"""

import dataclasses
import logging
import threading
from unittest.mock import patch

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control._def import TriggerMode
from control.core.multi_point_controller import NoOpCallbacks
from control.core.multi_point_worker import MultiPointWorker


@pytest.fixture
def scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


def _acquisition(scope):
    """A software-triggered run of one FOV and one channel; finished is set when the run reports its end."""
    control._def.MERGE_CHANNELS = False
    finished = threading.Event()
    callbacks = dataclasses.replace(NoOpCallbacks, signal_acquisition_finished=finished.set)
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
    mpc.liveController.set_trigger_mode(TriggerMode.SOFTWARE)
    config = scope.stage.get_config()
    mpc.scanCoordinates.add_single_fov_region(
        "one",
        center_x=config.X_AXIS.MIN_POSITION + 1.0,
        center_y=config.Y_AXIS.MIN_POSITION + 1.0,
        center_z=config.Z_AXIS.MIN_POSITION + 1.0,
    )
    objective = scope.objective_store.current_objective
    mpc.set_selected_configurations([mpc.liveController.get_channels(objective)[0].name])
    return mpc, finished


class Camera:
    """Wraps the simulated camera: ignores the first `lost` triggers (no frame comes for them), records the steps."""

    def __init__(self, camera, lost, can_reopen=True, hang_in=None):
        self.real_send_trigger = camera.send_trigger
        self.real_restart_streaming = camera.restart_streaming
        self.lost, self.reopenable, self.hang_in = lost, can_reopen, hang_in
        self.steps = []
        self.light_on_at_recovery = []
        self.unhang = threading.Event()

    def send_trigger(self, *args, **kwargs):
        self.steps.append("trigger")
        if self.hang_in == "trigger again" and self.steps.count("trigger") == 2:
            self.unhang.wait(30)  # the driver does not return (bench: cap_firetrigger, 15 hours)
            raise RuntimeError("NOTRIGGER")
        if self.steps.count("trigger") > self.lost:
            self.real_send_trigger(*args, **kwargs)

    def _recovery(self, name, real):
        self.steps.append(name)
        self.light_on_at_recovery.append(self.live.illumination_on)
        if self.hang_in == name:
            self.unhang.wait(30)
        return real()

    def restart_streaming(self):
        return self._recovery("restart", self.real_restart_streaming)

    def reopen(self):
        return self._recovery("reopen", lambda: None)

    def can_reopen(self):
        return self.reopenable


def _run(scope, caplog, **camera_kwargs):
    mpc, finished = _acquisition(scope)
    cam = Camera(scope.camera, **camera_kwargs)
    cam.live = mpc.liveController
    with (
        caplog.at_level(logging.WARNING),
        patch.object(scope.camera, "send_trigger", side_effect=cam.send_trigger),
        patch.object(scope.camera, "restart_streaming", side_effect=cam.restart_streaming),
        patch.object(scope.camera, "reopen", side_effect=cam.reopen),
        patch.object(scope.camera, "can_reopen", side_effect=cam.can_reopen),
    ):
        assert mpc.run_acquisition() is True
        assert finished.wait(120)
        mpc.thread.join(10)
    cam.unhang.set()
    return mpc, cam


def test_a_frame_lost_once_is_triggered_again_and_the_run_completes(scope, caplog):
    mpc, cam = _run(scope, caplog, lost=1)

    assert cam.steps == ["trigger", "trigger"]
    assert (mpc.last_image_count, mpc.last_end_reason) == (1, "completed")
    assert "trigger again (1/3)" in caplog.text
    assert "The frame came after: trigger again." in caplog.text


def test_a_frame_lost_twice_restarts_the_stream_with_the_light_off(scope, caplog):
    mpc, cam = _run(scope, caplog, lost=2)

    assert cam.steps == ["trigger", "trigger", "restart", "trigger"]
    assert cam.light_on_at_recovery == [False]
    assert (mpc.last_image_count, mpc.last_end_reason) == (1, "completed")


def test_a_frame_lost_three_times_reopens_the_camera(scope, caplog):
    mpc, cam = _run(scope, caplog, lost=3)

    assert cam.steps == ["trigger", "trigger", "restart", "trigger", "reopen", "trigger"]
    assert cam.light_on_at_recovery == [False, False]
    assert (mpc.last_image_count, mpc.last_end_reason) == (1, "completed")
    assert "The frame came after: trigger again, restart streaming, reopen the camera." in caplog.text


def test_a_frame_lost_every_time_aborts_after_the_last_step(scope, caplog):
    mpc, cam = _run(scope, caplog, lost=99)

    assert cam.steps == ["trigger", "trigger", "restart", "trigger", "reopen", "trigger"]
    assert (mpc.last_image_count, mpc.last_end_reason) == (0, "error")
    assert "No frame after 4 trigger(s)" in caplog.text
    assert mpc.liveController.illumination_on is False


def test_a_camera_that_cannot_reopen_is_triggered_again_instead(scope, caplog):
    mpc, cam = _run(scope, caplog, lost=99, can_reopen=False)

    assert cam.steps == ["trigger", "trigger", "restart", "trigger", "trigger"]
    assert "cannot reopen the camera; triggering again instead" in caplog.text
    assert mpc.last_end_reason == "error"


def test_a_stream_restart_that_hangs_aborts_in_its_time_limit_with_the_light_off(scope, caplog):
    with patch.object(MultiPointWorker, "CAMERA_RECOVERY_TIMEOUT_S", 0.5):
        mpc, cam = _run(scope, caplog, lost=99, hang_in="restart")

    assert cam.steps == ["trigger", "trigger", "restart"]  # nothing more is sent to a camera that stopped answering
    assert mpc.last_end_reason == "error"
    assert "Camera recovery (restart streaming) did not finish in 0.5 s" in caplog.text
    assert "_recovery" in caplog.text  # where it is stuck
    assert mpc.liveController.illumination_on is False


def test_a_trigger_again_that_hangs_aborts_in_its_time_limit_with_the_light_off(scope, caplog):
    with patch.object(MultiPointWorker, "CAMERA_CALL_TIMEOUT_S", 0.5):
        mpc, cam = _run(scope, caplog, lost=99, hang_in="trigger again")

    assert cam.steps == ["trigger", "trigger"]  # nothing more is sent to a camera that stopped answering
    assert mpc.last_end_reason == "error"
    assert "Triggering again did not finish in" in caplog.text and "Restart Squid" in caplog.text
    assert "send_trigger" in caplog.text  # where it is stuck
    assert mpc.liveController.illumination_on is False
