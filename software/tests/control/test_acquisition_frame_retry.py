"""Software trigger: a trigger whose frame never comes is sent again before the acquisition aborts.

Before, the first frame that did not come in 5x the frame time + 2 s aborted the whole acquisition, with no second
trigger (bench, 2026-11-25: a 4-channel confocal stack ended at its third image).
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


def _run_losing_frames(scope, lost):
    """Run, with the camera ignoring the first `lost` triggers (no frame comes for them). Returns the controller and
    the number of triggers sent."""
    mpc, finished = _acquisition(scope)
    real_send_trigger = scope.camera.send_trigger
    sent = []

    def send_trigger(*args, **kwargs):
        sent.append(1)
        if len(sent) > lost:
            real_send_trigger(*args, **kwargs)

    with patch.object(scope.camera, "send_trigger", side_effect=send_trigger):
        assert mpc.run_acquisition() is True
        assert finished.wait(60)
        mpc.thread.join(10)
    return mpc, len(sent)


def test_a_lost_frame_is_triggered_again_and_the_run_completes(scope, caplog):
    with caplog.at_level(logging.WARNING):
        mpc, triggers = _run_losing_frames(scope, lost=1)

    assert triggers == 2
    assert mpc.last_image_count == 1
    assert mpc.last_end_reason == "completed"
    assert "triggering again (1/1)" in caplog.text
    assert "The frame came after 1 re-trigger(s)" in caplog.text


def test_a_frame_lost_again_on_the_retry_aborts_the_run(scope, caplog):
    with caplog.at_level(logging.WARNING):
        mpc, triggers = _run_losing_frames(scope, lost=2)

    assert triggers == 2  # one retry, then the abort: no trigger for any other image
    assert mpc.last_image_count == 0
    assert mpc.last_end_reason == "error"
    assert "No frame after 2 trigger(s)" in caplog.text
