"""One owner of the stage at a time, whichever thread starts it: an acquisition, the loading-position move (and the
stay there), a stage move or home from the control server. Each takes the Microscope's stage-motion reservation
(squid.stage.reservation) before it moves anything, so the second one to start is refused with what holds the stage,
and every way out of each one gives the stage back.

Before, each side only read the other's flags: an acquisition started over TCP while the Stage Utils button was
stopping live went ahead and imaged while the stage drove to the loading position, and a TCP move_to went straight to
the stage during a loading move, at the loading position, or under an acquisition.
"""

import dataclasses
import threading
from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QMessageBox

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.core.multi_point_controller import NoOpCallbacks
from control.microscope_control_server import MicroscopeControlServer
from control.startup_homing import StageHomingRefused
from control.widgets import StageUtils
from squid.abc import Pos
from squid.stage.reservation import StageMotionRefused, StageMotionReservation

LOADING_MOVE = "a move to the loading position is running"
AT_LOADING = "the stage is at the loading position (return from it first)"
MOVING_BACK = "a move back from the loading position is running"
ACQUISITION = "an acquisition is running"
TCP_MOVE = "a stage move from the control server is running"


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


def _wait_until_done(mpc, finished):
    assert finished.wait(30)
    mpc.thread.join(10)


# --- an acquisition ---------------------------------------------------------------------------------------------------


def test_an_acquisition_is_refused_while_something_else_holds_the_stage(scope):
    mpc, _ = _acquisition(scope)
    loading = scope.stage_motion.take(LOADING_MOVE)
    start = scope.stage.get_pos()

    assert mpc.run_acquisition() is False

    assert mpc.last_end_reason == "failed_to_start"
    assert mpc.last_start_refusal == LOADING_MOVE
    assert scope.stage.get_pos() == start
    assert not loading.released  # the refused start left the loading move's hold alone


def test_an_acquisition_holds_the_stage_until_it_ends(scope):
    mpc, finished = _acquisition(scope)
    holders = []
    mpc.callbacks = dataclasses.replace(
        mpc.callbacks, signal_acquisition_start=lambda *_: holders.append(scope.stage_motion.holder)
    )

    assert mpc.run_acquisition() is True
    _wait_until_done(mpc, finished)

    assert holders == [ACQUISITION]
    assert scope.stage_motion.holder is None
    assert mpc.last_start_refusal is None


def test_a_refused_second_start_leaves_the_running_acquisition_holding_the_stage(scope):
    mpc, finished = _acquisition(scope)
    release_worker = threading.Event()
    with patch("control.core.multi_point_controller.MultiPointWorker.run", lambda worker: release_worker.wait(30)):
        assert mpc.run_acquisition() is True
        assert mpc.run_acquisition(acquire_current_fov=True) is False  # Snap Images during the run
        assert mpc.last_start_refusal == ACQUISITION
        assert scope.stage_motion.holder == ACQUISITION
        release_worker.set()
        mpc.thread.join(10)


def test_a_start_refused_by_the_start_blocker_gives_the_stage_back(scope):
    mpc, _ = _acquisition(scope)
    mpc.start_blocker = lambda: "the Z lift is moving"

    assert mpc.run_acquisition() is False

    assert mpc.last_start_refusal == "the Z lift is moving"
    assert scope.stage_motion.holder is None


def test_a_start_that_fails_gives_the_stage_back(scope, monkeypatch):
    import control.core.multi_point_controller as mpc_module

    class BrokenWorker:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(mpc_module, "MultiPointWorker", BrokenWorker)
    mpc, _ = _acquisition(scope)

    with pytest.raises(RuntimeError, match="boom"):
        mpc.run_acquisition()

    assert scope.stage_motion.holder is None


def test_a_start_that_raises_before_preparing_gives_the_stage_back(scope, monkeypatch):
    mpc, _ = _acquisition(scope)

    def unwritable_log():
        raise OSError("the log folder is not writable")

    monkeypatch.setattr(mpc, "_start_per_acquisition_log", unwritable_log)
    with pytest.raises(OSError):
        mpc.run_acquisition()

    assert scope.stage_motion.holder is None


# --- the control server -----------------------------------------------------------------------------------------------


def _server(scope, **kwargs):
    return MicroscopeControlServer(microscope=scope, port=0, **kwargs)


@pytest.mark.parametrize(
    "command, params",
    [
        ("move_to", {"x_mm": 30.0}),
        ("move_relative", {"dx_mm": 1.0}),
        ("home", {"objective_lowered": True}),
    ],
)
@pytest.mark.parametrize("holder", [LOADING_MOVE, AT_LOADING, ACQUISITION])
def test_a_tcp_stage_command_is_refused_while_something_else_holds_the_stage(scope, command, params, holder):
    server = _server(scope)
    scope.stage_motion.take(holder)
    start = scope.stage.get_pos()

    reply = server._execute(command, params)

    assert reply["success"] is False
    assert holder in reply["error"]
    assert scope.stage.get_pos() == start


def test_a_tcp_move_holds_the_stage_while_it_moves(scope):
    server = _server(scope)
    seen = []
    real_move_x_to = scope.stage.move_x_to

    def move_x_to(*args, **kwargs):
        try:
            scope.stage_motion.take(LOADING_MOVE)
        except StageMotionRefused as e:
            seen.append(e.reason)
        return real_move_x_to(*args, **kwargs)

    with patch.object(scope.stage, "move_x_to", side_effect=move_x_to):
        assert server._execute("move_to", {"x_mm": 30.0})["success"] is True

    assert seen == [TCP_MOVE]
    assert scope.stage_motion.holder is None


def test_a_tcp_move_that_fails_gives_the_stage_back(scope):
    server = _server(scope)
    with patch.object(scope.stage, "move_y", side_effect=TimeoutError("y stalled")):
        reply = server._execute("move_relative", {"dy_mm": 1.0})

    assert reply["success"] is False and "y stalled" in reply["error"]
    assert scope.stage_motion.holder is None


def test_a_non_blocking_tcp_move_holds_the_stage_until_it_reports_stopped(scope):
    server = _server(scope)
    stopped = threading.Event()
    with patch.object(scope, "_stage_reports_stopped", side_effect=stopped.is_set):
        assert server._execute("move_to", {"x_mm": 30.0, "blocking": False})["success"] is True
        assert scope.stage_motion.holder == TCP_MOVE  # the reply came back, the move is not done

        stopped.set()
        deadline = threading.Event()
        for _ in range(50):
            if scope.stage_motion.holder is None:
                break
            deadline.wait(0.1)

    assert scope.stage_motion.holder is None


def test_a_tcp_acquisition_refusal_says_what_holds_the_stage(scope):
    mpc, _ = _acquisition(scope)
    server = _server(scope, multipoint_controller=mpc, scan_coordinates=mpc.scanCoordinates)
    scope.stage_motion.take(AT_LOADING)
    channel = scope.live_controller.get_channels(scope.objective_store.current_objective)[0].name
    regions_before = dict(mpc.scanCoordinates.region_centers)

    reply = server._execute("run_acquisition", {"wells": "A1", "channels": [channel], "nx": 1, "ny": 1})

    assert reply["success"] is False
    assert reply["error"] == f"Refused: {AT_LOADING}"
    assert mpc.scanCoordinates.region_centers == regions_before  # refused before the GUI's regions were replaced


# --- homing -----------------------------------------------------------------------------------------------------------


def test_a_home_is_refused_while_something_else_holds_the_stage(scope):
    scope.stage_motion.take(AT_LOADING)

    with pytest.raises(StageHomingRefused) as refused:
        scope.claim_stage_homing("Home XYZ")

    assert refused.value.reason == AT_LOADING
    assert not scope.stage_homing_in_progress


def test_a_home_holds_the_stage_until_its_claim_is_released(scope):
    claim = scope.claim_stage_homing("Home XYZ")

    with pytest.raises(StageMotionRefused, match="the stage is homing"):
        scope.stage_motion.take(ACQUISITION)

    claim.release_when_stopped()
    assert claim.released
    assert scope.stage_motion.holder is None


# --- the loading position (Stage Utils) -------------------------------------------------------------------------------


def _stage_utils(qtbot, reservation=None, y_fails=False):
    stage = MagicMock(spec=["move_x_to", "move_y_to", "move_z_to", "get_pos"])
    stage.get_pos.return_value = Pos(x_mm=10.0, y_mm=10.0, z_mm=1.0, theta_rad=None)
    if y_fails:
        stage.move_y_to.side_effect = TimeoutError("y stalled")
    live = MagicMock()
    live.is_live = False
    dialog = StageUtils(stage, live, is_wellplate=False, stage_motion=reservation or StageMotionReservation())
    qtbot.addWidget(dialog)
    return dialog


def _to_loading(qtbot, dialog):
    with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
        dialog.switch_position()


def _back_to_scanning(qtbot, dialog):
    with qtbot.waitSignal(dialog.signal_scanning_position_reached, timeout=10000):
        dialog.switch_position()


def test_the_loading_move_is_refused_while_an_acquisition_holds_the_stage(qtbot):
    reservation = StageMotionReservation()
    reservation.take(ACQUISITION)
    dialog = _stage_utils(qtbot, reservation)

    with patch.object(QMessageBox, "information") as shown:
        dialog.switch_position()

    assert ACQUISITION in shown.call_args.args[2]
    assert dialog.slide_position is None
    dialog.stage.move_x_to.assert_not_called()
    dialog.stage.move_y_to.assert_not_called()
    assert dialog.btn_load_slide.isEnabled()


def test_the_stage_is_held_from_the_move_to_loading_until_it_is_back_at_scanning(qtbot):
    reservation = StageMotionReservation()
    dialog = _stage_utils(qtbot, reservation)
    seen = []
    dialog.stage.move_y_to.side_effect = lambda *a, **k: seen.append(reservation.holder)

    _to_loading(qtbot, dialog)
    at_loading = reservation.holder
    _back_to_scanning(qtbot, dialog)

    assert seen == [LOADING_MOVE, MOVING_BACK]
    assert at_loading == AT_LOADING
    assert reservation.holder is None


def test_an_acquisition_started_while_the_loading_move_stops_live_is_refused(qtbot, scope):
    """The competing start: the button holds the stage before it stops live, so an acquisition starting meanwhile (from
    a control-server client, say) is refused instead of imaging while the stage drives to the loading position."""
    mpc, _ = _acquisition(scope)
    dialog = _stage_utils(qtbot, scope.stage_motion)
    dialog.live_controller.is_live = True
    started = []
    dialog.live_controller.stop_live.side_effect = lambda: started.append(mpc.run_acquisition())

    with patch.object(QMessageBox, "information") as refused:  # (a regression: the button refused, not the run)
        dialog.switch_position()

    assert started == [False]
    assert mpc.last_start_refusal == LOADING_MOVE
    refused.assert_not_called()
    qtbot.waitUntil(lambda: dialog.slide_position == "loading", timeout=10000)


def test_a_failed_move_back_keeps_the_stage_held_at_the_loading_position(qtbot):
    reservation = StageMotionReservation()
    dialog = _stage_utils(qtbot, reservation)
    _to_loading(qtbot, dialog)
    dialog.stage.move_y_to.side_effect = TimeoutError("y stalled")

    with patch.object(QMessageBox, "warning"):
        with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
            dialog.switch_position()

    assert reservation.holder == AT_LOADING
    dialog.stage.move_y_to.side_effect = None
    _back_to_scanning(qtbot, dialog)
    assert reservation.holder is None


def test_a_move_that_cannot_start_leaves_the_stage_held_at_the_loading_position(qtbot):
    """The move's thread did not start: reported as a failed move (the stage may be anywhere between), so the stage
    stays held and the move back is the way out."""
    reservation = StageMotionReservation()
    dialog = _stage_utils(qtbot, reservation)

    with patch("control.widgets.move_to_loading_position", side_effect=RuntimeError("can't start new thread")):
        with patch.object(QMessageBox, "warning") as warning:
            with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
                dialog.switch_position()

    assert "can't start new thread" in warning.call_args.args[2]
    assert reservation.holder == AT_LOADING
    _back_to_scanning(qtbot, dialog)
    assert reservation.holder is None


def test_squid_restarted_at_the_loading_position_holds_the_stage_there(qtbot):
    reservation = StageMotionReservation()
    dialog = _stage_utils(qtbot, reservation)

    with qtbot.waitSignal(dialog.signal_loading_position_reached, timeout=10000):
        dialog.enter_restored_loading_position()

    assert dialog.slide_position == "loading"
    assert reservation.holder == AT_LOADING
    _back_to_scanning(qtbot, dialog)
    assert reservation.holder is None
