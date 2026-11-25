"""A move to a FOV that fails because the stage's reply was lost or unreadable can be made again
(MULTIPOINT_FOV_MOVE_RETRIES, off by default) before the error ends the acquisition. A stage that is still busy is
never moved again."""

import logging
import time
from types import SimpleNamespace
from unittest import mock

import pytest

import control._def
import control.microscope
import squid.abc
import squid.stage.prior
from control.core.multi_point_worker import MultiPointWorker
from squid.exceptions import SquidReplyTimeout

import tests.control.test_stubs as ts
from tests.squid.test_prior_stage import FakeProScan
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


def _run_with_failing_second_fov(fail, failures, setup=None):
    """Run a 3-FOV acquisition whose stage fails (`fail(stage)` raises) `failures` times on its move to the second
    FOV. `setup(mpc, fovs)`, if given, runs before the stage is made to fail. Returns the controller, the tracker and
    the number of moves made to the second FOV."""
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

    if setup is not None:
        setup(mpc, fovs)

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


RETRY_DELAY_S = 0.0123  # a delay no other sleep of the worker uses, so the test can tell the retry delay apart


def _abort_at_the_failure(mpc, monkeypatch):
    def fail(stage):
        mpc.request_abort_aquisition()
        raise SquidReplyTimeout("Prior stage gave no status reply to '$,S' for 5 s")

    return fail


def _abort_during_the_retry_delay(mpc, monkeypatch):
    real_sleep = MultiPointWorker._sleep

    def sleep(worker, sec):
        if sec == RETRY_DELAY_S:
            mpc.request_abort_aquisition()
        real_sleep(worker, sec)

    monkeypatch.setattr(MultiPointWorker, "_sleep", sleep)
    return _raise(SquidReplyTimeout("Prior stage gave no status reply to '$,S' for 5 s"))


def _abort_during_the_stop_wait(mpc, monkeypatch):
    def reports_stopped():
        mpc.request_abort_aquisition()
        return False  # still moving when the user aborts

    monkeypatch.setattr(mpc.stage, "reports_stopped", reports_stopped)
    return _raise(ValueError("not enough values to unpack (expected 3, got 1)"))


@pytest.mark.parametrize(
    "abort_and_fail", [_abort_at_the_failure, _abort_during_the_retry_delay, _abort_during_the_stop_wait]
)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_user_abort_during_a_retry_ends_the_acquisition_as_a_user_abort(monkeypatch, abort_and_fail):
    """The user aborted while a failed FOV move waited to be made again: the run ends as the user's abort, with no
    images at that FOV and no further move, not as a stage error."""
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRIES", 1)
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRY_DELAY_S", RETRY_DELAY_S)
    moves_to_third_fov = []
    failing = {}

    def setup(mpc, fovs):
        failing["fail"] = abort_and_fail(mpc, monkeypatch)
        real_move_x_to = mpc.stage.move_x_to

        def move_x_to(x_mm, *args, **kwargs):
            if x_mm == pytest.approx(fovs[2][0]):
                moves_to_third_fov.append(x_mm)
            return real_move_x_to(x_mm, *args, **kwargs)

        mpc.stage.move_x_to = move_x_to

    mpc, tt, moves = _run_with_failing_second_fov(lambda stage: failing["fail"](stage), failures=10, setup=setup)

    assert mpc.last_end_reason == "user_abort"
    assert moves == 1  # not made again
    assert moves_to_third_fov == []
    assert tt.image_count == _images_per_fov(mpc)  # only the first FOV: nothing acquired at the failed one


# --- The real PriorStage, against a ProScan whose moves take time ---

MOVE_S = 0.8  # how long each fake move runs
SILENT_FROM_S, SILENT_UNTIL_S = 0.05, 0.5  # '$,S' gets no reply this far into the first move
STATUS_REPLY_TIMEOUT_S = 0.2  # so wait_for_stop gives up on the silent status inside the silent window


class TimedProScan(FakeProScan):
    """A ProScan whose moves run MOVE_S. During the first move its '$,S' status replies go missing from SILENT_FROM_S
    to `silent_until_s` into it: wait_for_stop raises SquidReplyTimeout with the stage still moving."""

    silent_until_s = SILENT_UNTIL_S

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.move_start = None
        self.move_end = 0.0
        self.moves = 0
        self.motion_log = []  # (command, sent while the stage was still moving)

    def _reply(self, cmd):
        now = time.monotonic()
        if cmd.startswith(("GX ", "GY ")):
            moving = now < self.move_end
            self.motion_log.append((cmd, moving))
            if not moving:
                self.move_start, self.move_end = now, now + MOVE_S
                self.moves += 1
            return super()._reply(cmd)
        if cmd == "$,S":
            if self.moves == 1 and SILENT_FROM_S < now - self.move_start < self.silent_until_s:
                return b""
            return b"3\r" if now < self.move_end else b"0\r"
        return super()._reply(cmd)


@pytest.fixture
def prior_stage(monkeypatch):
    monkeypatch.setattr(squid.stage.prior.PriorStage, "STATUS_REPLY_TIMEOUT_S", STATUS_REPLY_TIMEOUT_S)
    made = {}

    def make_serial(*args, **kwargs):
        made["serial"] = TimedProScan(*args, **kwargs)
        return made["serial"]

    monkeypatch.setattr(squid.stage.prior.serial, "Serial", make_serial)
    monkeypatch.setattr(
        squid.stage.prior.serial.tools.list_ports,
        "comports",
        lambda: [SimpleNamespace(device="COM_FAKE", serial_number="FAKE_SN")],
    )
    stage = squid.stage.prior.PriorStage(sn="FAKE_SN")
    stage.serial.timeout = 0.01  # an unanswered read costs 10 ms, not 100
    yield stage, made["serial"]
    stage.close()


def _worker_on(stage):
    """Just enough of a MultiPointWorker for move_to_coordinate, with its real methods."""
    worker = MultiPointWorker.__new__(MultiPointWorker)
    worker.stage = stage
    worker._log = logging.getLogger("test_move_retry")
    worker.abort_requested_fn = lambda: False
    worker._alignment_widget = None
    worker.do_reflection_af = False
    worker.do_autofocus = False
    worker.time_point = 0
    return worker


def test_a_retry_waits_for_the_stage_to_report_stopped_before_moving_it_again(retries_on, prior_stage, monkeypatch):
    """The status went silent mid-move: the move is made again only once a fresh '$,S' says the stage stopped."""
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRY_STOP_WAIT_S", 5.0)
    stage, ser = prior_stage

    assert _worker_on(stage).move_to_coordinate((1.0, 1.0), "A1", 0) is True

    commands = [cmd.split()[0] for cmd, _ in ser.motion_log]
    assert commands == ["GX", "GX", "GY"]  # the lost-reply move made again, then on to Y
    assert not [cmd for cmd, moving in ser.motion_log if moving], "a motion command went to a stage still moving"


@pytest.mark.parametrize(
    "silent_until_s",
    [
        pytest.param(SILENT_UNTIL_S, id="still-moving"),  # the status comes back, but says it is still moving
        pytest.param(float("inf"), id="status-stays-silent"),  # the stage's state cannot be read at all
    ],
)
def test_a_retry_ends_the_move_when_the_stage_does_not_report_stopped_in_time(
    retries_on, prior_stage, monkeypatch, silent_until_s
):
    monkeypatch.setattr(control._def, "MULTIPOINT_FOV_MOVE_RETRY_STOP_WAIT_S", 0.1)  # shorter than the move
    monkeypatch.setattr(TimedProScan, "silent_until_s", silent_until_s)
    stage, ser = prior_stage

    with pytest.raises(SquidReplyTimeout):
        _worker_on(stage).move_to_coordinate((1.0, 1.0), "A1", 0)

    assert [cmd.split()[0] for cmd, _ in ser.motion_log] == ["GX"]  # never commanded again
