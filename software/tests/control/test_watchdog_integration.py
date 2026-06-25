# tests/control/test_watchdog_integration.py
import os
import time

import squid.acquisition_state as ast
import control.microscope
import tests.control.gui_test_stubs as gts


def _wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_simulated_acquisition_writes_ended_breadcrumb(qtbot):
    state_dir = os.environ["SQUID_WATCHDOG_STATE_DIR"]
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)

    mpc.run_acquisition()
    assert _wait_for(lambda: ast.read_run(state_dir) is not None)
    assert ast.read_run(state_dir)["status"] == "running"

    # Abort and confirm the worker writes the end breadcrumb (deterministic path).
    mpc.request_abort_aquisition()
    assert _wait_for(lambda: (ast.read_run(state_dir) or {}).get("status") == "ended", timeout=30.0)

    rec = ast.read_run(state_dir)
    assert rec["status"] == "ended"
    assert rec["reason"] in {"completed", "completed_with_errors", "user_abort", "error"}
    assert rec["ended_at"] is not None
    scope.close()
