# tests/control/test_watchdog_breadcrumbs.py
import os

import squid.acquisition_state as ast
import control.microscope
import tests.control.gui_test_stubs as gts


def test_run_acquisition_writes_running_breadcrumb(qtbot):
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = gts.get_test_qt_multi_point_controller(microscope=scope)
    mpc.run_acquisition()
    rec = ast.read_run(os.environ["SQUID_WATCHDOG_STATE_DIR"])
    assert rec is not None
    assert rec["status"] == "running"
    assert rec["pid"] == os.getpid()
    assert rec["expected"]["timepoints"] >= 1
    mpc.request_abort_aquisition()
    scope.close()
