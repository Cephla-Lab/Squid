"""Run the Z motion self-test from the command line, through the same path the GUI uses.

Connects to the controller the way the GUI does (reset, initialise, configure every axis from the ini via
CephlaStage), then runs squid.motion_selftest.ZMotionSelfTest and prints the report. Run it from the software/
directory so the machine's configuration ini is found.

usage: python tools/motion_selftest.py [--depth-mm 2.5] [--stack-n 20] [--hold-s 5] [--gui]
       --gui shows the same dialog the GUI opens from its Utils menu, against the real controller.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import control._def as _def  # noqa: E402  (loads the ini)
import control.microscope  # noqa: E402
import squid.config  # noqa: E402
from squid.stage.cephla import CephlaStage  # noqa: E402
from squid.motion_selftest import ZMotionSelfTest  # noqa: E402


def connect():
    drivers = control.microscope.LowLevelDrivers.build_from_global_config(simulated=False)
    drivers.prepare_for_use()
    cfg = squid.config.get_stage_config()
    stage = CephlaStage(drivers.microcontroller, cfg)  # configures the axes exactly as the GUI does
    return drivers.microcontroller, stage, cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth-mm", type=float, default=None, help="working depth (default 2.5 mm, clipped to the travel)")
    ap.add_argument("--stack-n", type=int, default=20)
    ap.add_argument("--hold-s", type=float, default=5.0)
    ap.add_argument("--gui", action="store_true", help="show the dialog instead of printing")
    ap.add_argument("--autostart", action="store_true", help="with --gui: start without the confirmation and exit when done (bench check)")
    a = ap.parse_args()

    mcu, stage, cfg = connect()
    if a.gui:
        from qtpy.QtWidgets import QApplication
        from control.widgets_motion_selftest import MotionSelfTestDialog

        from qtpy.QtCore import QTimer

        app = QApplication(sys.argv)
        dlg = MotionSelfTestDialog(mcu, cfg.Z_AXIS)
        dlg.show()
        if a.autostart:
            dlg.signal_finished.connect(lambda ok: (print(dlg.log_view.toPlainText()), print('DIALOG DONE:', 'PASS' if ok else 'FAIL'), QTimer.singleShot(1500, app.quit)))
            QTimer.singleShot(500, lambda: dlg.start(confirm=False))
        rc = app.exec_()
        mcu.close()
        sys.exit(rc)

    test = ZMotionSelfTest(mcu, cfg.Z_AXIS, working_depth_mm=a.depth_mm, stack_n=a.stack_n, hold_s=a.hold_s)
    report = test.run()
    mcu.close()
    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
