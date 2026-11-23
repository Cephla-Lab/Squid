"""A move to a FOV that fails with a stage reply error is made again before the error ends the acquisition."""

import pytest

import control._def
import control.microscope
from squid.exceptions import SquidTimeout

import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker as AcquisitionTracker, select_some_configs

N_FOVS = 3
TIMEOUT_S = 30


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRY_DELAY_S", 0.01)


def _run_with_failing_second_fov(error_factory, failures):
    """Run a 3-FOV acquisition whose stage fails `failures` times on its move to the second FOV. Returns the
    controller, the tracker and the number of moves made to the second FOV."""
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
                raise error_factory()
        return real_move_x_to(x_mm, *args, **kwargs)

    stage.move_x_to = flaky_move_x_to

    mpc.run_acquisition()
    assert tt.started_event.wait(TIMEOUT_S)
    assert tt.finished_event.wait(TIMEOUT_S)
    return mpc, tt, len(moves_to_second_fov)


def _images_per_fov(mpc):
    return mpc.get_acquisition_image_count() // N_FOVS


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(lambda: ValueError("not enough values to unpack (expected 3, got 1)"), id="ValueError"),
        pytest.param(lambda: SquidTimeout("Prior stage gave no readable reply to 'P'"), id="SquidTimeout"),
    ],
)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_fov_move_that_fails_with_a_stage_reply_error_is_made_again(error_factory):
    mpc, tt, moves = _run_with_failing_second_fov(error_factory, failures=2)

    assert moves == 3  # two failures, then the move that got there
    assert mpc.last_end_reason == "completed"
    assert tt.image_count == mpc.get_acquisition_image_count()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_the_acquisition_ends_when_the_fov_move_keeps_failing():
    mpc, tt, moves = _run_with_failing_second_fov(lambda: ValueError("garbled"), failures=10)

    assert moves == 1 + control._def.MULTIPOINT_FOV_MOVE_RETRIES
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)  # only the first FOV


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_no_retry_when_retries_are_off(monkeypatch):
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRIES", 0)

    mpc, tt, moves = _run_with_failing_second_fov(lambda: ValueError("garbled"), failures=1)

    assert moves == 1
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)


@pytest.mark.parametrize(
    "error_factory",
    [
        # The microcontroller's move may still be running: moving again could drive a stuck stage.
        pytest.param(lambda: TimeoutError("Current mcu operation timed out after 3 [s]."), id="mcu-TimeoutError"),
        # An error the controller reported, e.g. a limit.
        pytest.param(lambda: Exception("Error from controller: E,8"), id="controller-error"),
        pytest.param(lambda: RuntimeError("Refused: something else holds the stage"), id="RuntimeError"),
    ],
)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_other_errors_still_end_the_acquisition_at_once(error_factory):
    mpc, tt, moves = _run_with_failing_second_fov(error_factory, failures=1)

    assert moves == 1
    assert mpc.last_end_reason == "error"
    assert tt.image_count == _images_per_fov(mpc)
