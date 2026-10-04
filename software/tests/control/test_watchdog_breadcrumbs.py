# tests/control/test_watchdog_breadcrumbs.py
import os

import pytest

import squid.acquisition_state as ast
import control.microscope
import tests.control.gui_test_stubs as gts


def _writer(tmp_path):
    return ast.RunStateWriter.start(
        experiment_id="e",
        pid=os.getpid(),
        config_path=None,
        output_path=str(tmp_path),
        expected={"timepoints": 1},
        state_dir=tmp_path,
    )


def test_run_acquisition_writes_running_breadcrumb(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)
    mpc.run_acquisition()
    rec = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])
    assert rec is not None
    assert rec["status"] == "running"
    assert rec["pid"] == os.getpid()
    assert rec["expected"]["timepoints"] >= 1
    # close() aborts the acquisition and joins its thread; that must happen
    # before closing the scope, otherwise the thread keeps running against a
    # closed microcontroller and dies with a TimeoutError.
    mpc.close()
    scope.close()


def test_set_status_paused_marks_record_and_refreshes_heartbeat(tmp_path):
    writer = _writer(tmp_path)
    before = ast.read_run(tmp_path)
    assert before["status"] == "running"

    writer.set_status("paused")
    rec = ast.read_run(tmp_path)
    assert rec["status"] == "paused"
    assert rec["heartbeat_at"] >= before["heartbeat_at"]

    # A paused writer keeps beating, so the watchdog still sees a live run.
    writer.beat(force=True)
    assert ast.read_run(tmp_path)["status"] == "paused"


def test_set_status_running_restores_and_is_idempotent(tmp_path):
    writer = _writer(tmp_path)
    writer.set_status("paused")
    writer.set_status("running")
    assert ast.read_run(tmp_path)["status"] == "running"

    # Setting the status it already has is a no-op (no status change, no error).
    writer.set_status("running")
    assert ast.read_run(tmp_path)["status"] == "running"


def test_set_status_rejects_unknown_status(tmp_path):
    writer = _writer(tmp_path)
    for bad in ("ended", "stopped", "", None):
        with pytest.raises(ValueError):
            writer.set_status(bad)
    assert ast.read_run(tmp_path)["status"] == "running"


def test_set_status_after_end_leaves_record_ended(tmp_path):
    writer = _writer(tmp_path)
    writer.end("completed")
    writer.set_status("paused")
    rec = ast.read_run(tmp_path)
    assert rec["status"] == "ended"
    assert rec["reason"] == "completed"


def test_null_writer_set_status_is_noop():
    ast.NullRunStateWriter().set_status("paused")
