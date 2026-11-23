"""An acquisition can start at a FOV in scan order (a resumed protocol imaging step) and reports where it stopped."""

import pytest

import control._def
import control.microscope
from control.core.multi_point_worker import MultiPointWorker

import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker as AcquisitionTracker, select_some_configs

TIMEOUT_S = 30


@pytest.fixture(autouse=True)
def _no_merge(monkeypatch):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)


def _controller():
    """A controller over two regions of three FOVs each (scan order A1:0-2, A2:0-2), recording each image's FOV."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = AcquisitionTracker()
    imaged = []

    def record(frame, info):
        imaged.append((info.region_id, info.fov))
        tt.receive_image(frame, info)

    callbacks = tt.get_callbacks()
    callbacks.signal_new_image = record
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
    config = mpc.stage.get_config()
    x0 = config.X_AXIS.MIN_POSITION + 1.0
    y0 = config.Y_AXIS.MIN_POSITION + 1.0
    mpc.scanCoordinates.region_fov_coordinates = {
        name: [(x0 + 0.5 * i, y0 + row) for i in range(3)] for row, name in enumerate(("A1", "A2"))
    }
    mpc.scanCoordinates.region_centers = {name: [x0 + 0.5, y0 + row] for row, name in enumerate(("A1", "A2"))}
    select_some_configs(mpc, scope.objective_store.current_objective)
    return scope, mpc, tt, imaged


def _run(mpc, tt):
    tt.started_event.clear()
    tt.finished_event.clear()
    assert mpc.run_acquisition()
    assert tt.started_event.wait(TIMEOUT_S)
    assert tt.finished_event.wait(TIMEOUT_S)


def _fovs(imaged):
    return sorted(set(imaged), key=lambda rf: (rf[0], rf[1]))


def test_a_run_from_start_fov_images_only_the_fovs_from_there_and_keeps_their_numbers():
    scope, mpc, tt, imaged = _controller()
    mpc.set_start_fov(4)  # A2 fov 1

    _run(mpc, tt)

    assert mpc.last_end_reason == "completed"
    assert _fovs(imaged) == [("A2", 1), ("A2", 2)]
    assert mpc.last_end_fov == 6
    assert mpc.start_fov == 0  # used by that run only


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_an_error_reports_the_first_fov_not_done_and_a_resume_images_the_rest():
    scope, mpc, tt, imaged = _controller()
    stage = scope.stage
    real_move_x_to = stage.move_x_to
    fifth_fov_x = mpc.scanCoordinates.region_fov_coordinates["A2"][1][0]
    a2_y = mpc.scanCoordinates.region_fov_coordinates["A2"][1][1]
    fail = {"on": True}

    def failing_move_x_to(x_mm, *args, **kwargs):
        # The move to A2 fov 1 (scan index 4) fails: the stage x is set before y, so check where y was left.
        if fail["on"] and abs(x_mm - fifth_fov_x) < 0.01 and abs(stage.get_pos().y_mm - a2_y) < 0.01:
            raise RuntimeError("stage gone")
        return real_move_x_to(x_mm, *args, **kwargs)

    stage.move_x_to = failing_move_x_to
    _run(mpc, tt)
    assert mpc.last_end_reason == "error"
    assert _fovs(imaged) == [("A1", 0), ("A1", 1), ("A1", 2), ("A2", 0)]
    assert mpc.last_end_fov == 4

    fail["on"] = False
    imaged.clear()
    mpc.start_new_experiment("unit test experiment attempt2")
    mpc.set_start_fov(mpc.last_end_fov)
    _run(mpc, tt)
    assert mpc.last_end_reason == "completed"
    assert _fovs(imaged) == [("A2", 1), ("A2", 2)]
    assert mpc.last_end_fov == 6


@pytest.mark.parametrize(
    "setup, problem",
    [
        pytest.param(lambda mpc: mpc.set_start_fov(6), "outside the 6 FOVs", id="past-the-last-fov"),
        pytest.param(lambda mpc: (mpc.set_Nt(2), mpc.set_start_fov(1)), "single time point", id="time-points"),
    ],
)
def test_a_start_fov_that_does_not_apply_refuses_the_start(setup, problem):
    scope, mpc, tt, imaged = _controller()
    setup(mpc)

    assert mpc.run_acquisition() is False

    assert mpc.last_end_reason == "failed_to_start"
    assert problem in mpc.last_start_refusal
    assert imaged == []
    assert mpc.start_fov == 0  # not carried into the next run


def _worker(**fields):
    # Bypass __init__; only _compute_end_fov()'s logic.
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.Nt = 1
    w._acquisition_error_count = 0
    w._images_lost = False
    w._next_fov = 5
    for name, value in fields.items():
        setattr(w, name, value)
    return w


def test_end_fov_is_the_next_fov_after_a_clean_single_time_point_run():
    assert _worker()._compute_end_fov() == 5


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"Nt": 2}, id="time-points"),
        pytest.param({"_acquisition_error_count": 1}, id="an-image-failed-to-save"),
        pytest.param({"_images_lost": True}, id="frames-or-jobs-given-up"),
    ],
)
def test_end_fov_is_unknown_when_fovs_counted_done_may_lack_images(fields):
    assert _worker(**fields)._compute_end_fov() is None
