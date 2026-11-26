"""An acquisition that saves nothing leaves no job runner process behind.

The controller warms a job runner subprocess for the next acquisition every time one starts. With skip_saving
the acquisition has no jobs, so it never took that runner, and nothing shut it down: one process more per run.
On the bench (2026-11-26) a soak test of 23 runs without saving left 24 of them, 1.2-1.9 GB each, until the
memory and the swap were full.
"""

import dataclasses
import multiprocessing
import threading
import time

import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.job_processing import JobRunner
from control.core.multi_point_controller import NoOpCallbacks


@pytest.fixture
def scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


def _job_runners():
    return [p for p in multiprocessing.active_children() if isinstance(p, JobRunner)]


def _wait_for(condition, timeout_s=15):
    deadline = time.time() + timeout_s
    while not condition() and time.time() < deadline:
        time.sleep(0.05)
    return condition()


def test_acquisitions_without_saving_leave_only_the_runner_warmed_for_the_next(scope, monkeypatch, tmp_path):
    monkeypatch.setattr(control._def.Acquisition, "USE_MULTIPROCESSING", True)
    control._def.MERGE_CHANNELS = False
    finished = threading.Event()
    callbacks = dataclasses.replace(NoOpCallbacks, signal_acquisition_finished=finished.set)
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
    mpc.set_base_path(str(tmp_path))
    mpc.set_skip_saving(True)
    config = scope.stage.get_config()
    mpc.scanCoordinates.add_single_fov_region(
        "one",
        center_x=config.X_AXIS.MIN_POSITION + 1.0,
        center_y=config.Y_AXIS.MIN_POSITION + 1.0,
        center_z=config.Z_AXIS.MIN_POSITION + 1.0,
    )
    objective = scope.objective_store.current_objective
    mpc.set_selected_configurations([mpc.liveController.get_channels(objective)[0].name])

    try:
        for run in range(3):
            finished.clear()
            mpc.start_new_experiment(f"run_{run}")
            assert mpc.run_acquisition() is True
            assert finished.wait(60)
            mpc.thread.join(10)

        # The one warmed for the next acquisition, and no other.
        assert _wait_for(lambda: len(_job_runners()) <= 1), f"{len(_job_runners())} job runner processes are alive"
    finally:
        mpc._cleanup_prewarmed_runner(mpc._prewarmed_job_runner)
