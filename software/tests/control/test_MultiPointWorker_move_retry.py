"""A move to a FOV that fails because the stage's reply was lost or unreadable can be made again
(MULTIPOINT_FOV_MOVE_RETRIES, off by default) before the error ends the acquisition. A stage that is still busy is
never moved again."""

from unittest import mock

import pytest

import control._def
import control.microscope
import squid.abc
from squid.exceptions import SquidReplyTimeout

import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker as AcquisitionTracker, select_some_configs

N_FOVS = 3
TIMEOUT_S = 30


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRY_DELAY_S", 0.01)


@pytest.fixture
def retries_on(monkeypatch):
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRIES", 2)


def _raise(error):
    def fail(stage):
        raise error

    return fail


def _stage_still_busy(stage):
    """What a move that waits for a stage that never stops raises: AbstractStage.wait_for_idle's SquidTimeout."""
    with mock.patch.object(stage, "get_state", return_value=squid.abc.StageStage(busy=True)):
        stage.wait_for_idle(0.01)


def _run_with_failing_second_fov(fail, failures):
    """Run a 3-FOV acquisition whose stage fails (`fail(stage)` raises) `failures` times on its move to the second
    FOV. Returns the controller, the tracker and the number of moves made to the second FOV."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    tt = AcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tt.get_callbacks())

    config = mpc.stage.get_config()
    x0 = config.X_AXIS.MIN_POSITION + 1.0
    y0 = config.Y_AXIS.MIN_POSITION + 1.0
    fovs = [(x0 + 0.5 * i, y0) for i in range(N_FOVS)]
    mpc.scanCoordinates.region_fov_coordinates = {"A1": fovs}
    mpc.scanCoordinates.region_centers = {"A1": [fovs[1][0], y0]}
    select_some_configs(mpc, scope.objective_store.current_objective)

    stage = scope.stage
    real_move_x_to = stage.move_x_to
    moves_to_second_fov = []

    def flaky_move_x_to(x_mm, *args, **kwargs):
        if x_mm == pytest.approx(fovs[1][0]):
            moves_to_second_fov.append(x_mm)
            if len(moves_to_second_fov) <= failures:
                fail(stage)
        return real_move_x_to(x_mm, *args, **kwargs)

    stage.move_x_to = flaky_move_x_to

    mpc.run_acquisition()
    assert tt.started_event.wait(TIMEOUT_S)
    assert tt.finished_event.wait(TIMEOUT_S)
    return mpc, tt, len(moves_to_second_fov)


def _images_per_fov(mpc):
    return mpc.get_acquisition_image_count() // N_FOVS


LOST_REPLY_ERRORS = [
    # A reply that could not be parsed.
    pytest.param(_raise(ValueError("not enough values to unpack (expected 3, got 1)")), id="ValueError"),
    # No readable reply within the driver's timeout.
    pytest.param(
        _raise(SquidReplyTimeout("Prior stage gave no status reply to '$,S' for 5 s")), id="SquidReplyTimeout"
    ),
]


@pytest.mark.parametrize("fail", LOST_REPLY_ERRORS)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_fov_move_that_lost_its_reply_is_made_again_when_retries_are_on(retries_on, fail):
    mpc, tt, moves = _run_with_failing_second_fov(fail, failures=2)

    assert moves == 3  # two failures, then the move that got there
    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_the_acquisition_ends_when_the_fov_move_keeps_failing(retries_on):
    mpc, tt, moves = _run_with_failing_second_fov(_raise(ValueError("garbled")), failures=10)

    assert moves == 1 + control._def.MULTIPOINT_FOV_MOVE_RETRIES
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)  # only the first FOV


@pytest.mark.parametrize("fail", LOST_REPLY_ERRORS)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_no_retry_by_default(fail):
    assert control._def.MULTIPOINT_FOV_MOVE_RETRIES == 0

    mpc, tt, moves = _run_with_failing_second_fov(fail, failures=1)

    assert moves == 1
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)


@pytest.mark.parametrize(
    "fail",
    [
        # The stage is stuck or still moving: moving it again could drive a blocked stage.
        pytest.param(_stage_still_busy, id="wait_for_idle-still-busy"),
        # The microcontroller's move may still be running.
        pytest.param(_raise(TimeoutError("Current mcu operation timed out after 3 [s].")), id="mcu-TimeoutError"),
        # An error the controller reported, e.g. a limit.
        pytest.param(_raise(Exception("Error from controller: E,8")), id="controller-error"),
        pytest.param(_raise(RuntimeError("Refused: something else holds the stage")), id="RuntimeError"),
    ],
)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_other_errors_end_the_acquisition_at_once_even_with_retries_on(retries_on, fail):
    mpc, tt, moves = _run_with_failing_second_fov(fail, failures=1)

    assert moves == 1
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)
