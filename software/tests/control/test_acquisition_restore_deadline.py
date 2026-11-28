"""An acquisition reports its end even when restoring the microscope after it hangs.

On the bench (2026-11-25, ORCA camera, software trigger) a frame never came, the worker aborted on the frame timeout,
and the restore after the run (setting the channel back) went into a device call that never returned. The run never
reported its end: the GUI stayed in "acquiring" with Live greyed out, every new start was refused as "in progress",
and nothing said why. Squid had to be killed.
"""

import dataclasses
import logging
import threading
from unittest.mock import patch

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.multi_point_controller import NoOpCallbacks


@pytest.fixture
def scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


def _acquisition(scope):
    """A multipoint controller on scope with one FOV and one channel; finished is set when the run reports its end."""
    control._def.MERGE_CHANNELS = False
    finished = threading.Event()
    callbacks = dataclasses.replace(NoOpCallbacks, signal_acquisition_finished=finished.set)
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
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


def test_a_restore_that_hangs_still_ends_the_run_and_blocks_the_next_until_it_returns(scope, caplog):
    mpc, finished = _acquisition(scope)
    device_answers = threading.Event()
    restoring = threading.Event()
    real_set_microscope_mode = mpc.liveController.set_microscope_mode
    real_restore_software = mpc._restore_software_state_after_acquisition

    def restore_software():  # runs right before the hardware is restored
        restoring.set()
        real_restore_software()

    def set_microscope_mode(configuration):  # the device stops answering once the images are taken
        if restoring.is_set():
            device_answers.wait(30)
        else:
            real_set_microscope_mode(configuration)

    mpc.RESTORE_HARDWARE_TIMEOUT_S = 0.5
    with caplog.at_level(logging.WARNING):
        with (
            patch.object(mpc.liveController, "set_microscope_mode", side_effect=set_microscope_mode),
            patch.object(mpc, "_restore_software_state_after_acquisition", side_effect=restore_software),
        ):
            assert mpc.run_acquisition() is True
            assert finished.wait(30)  # the run reported its end: the GUI leaves "acquiring"
            mpc.thread.join(10)
            assert not mpc.thread.is_alive()

            # the log says what is stuck, and where
            assert "did not finish in 0.5 s" in caplog.text
            assert "set_microscope_mode" in caplog.text
            # it was stuck before the move back: the stage did not move and is free, with no "a move failed"
            assert scope.stage_motion.holder is None
            assert "a move failed" not in caplog.text

            # the next start would hang on the same device: refused, with the reason
            finished.clear()
            assert mpc.run_acquisition() is False
            assert "restart Squid" in mpc.last_start_refusal
            assert finished.is_set()  # the refused start re-enabled the UI

            with patch.object(scope.stage, "move_x_to") as move_x:
                device_answers.set()
                mpc._hung_restore.join(10)
            # back after its time limit: it does not move the stage, which was given back meanwhile
            move_x.assert_not_called()
            assert "came back after its time limit: not moving the stage back" in caplog.text

    # the device answered after all: acquisitions start again
    finished.clear()
    assert mpc.run_acquisition() is True
    assert finished.wait(30)
    mpc.thread.join(10)
    assert mpc._hung_restore is None


def test_a_restore_that_returns_in_time_changes_nothing(scope, caplog):
    mpc, finished = _acquisition(scope)
    with caplog.at_level(logging.ERROR):
        assert mpc.run_acquisition() is True
        assert finished.wait(30)
        mpc.thread.join(10)
    assert "did not finish" not in caplog.text
    assert mpc._hung_restore is None
    assert scope.stage_motion.holder is None
