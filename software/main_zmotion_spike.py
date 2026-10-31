"""THROWAWAY SPIKE launcher: Squid on a Zmotion-controlled scanner with no Cephla microcontroller.

Usage (bench PC, configuration_Zmotion_spike.ini copied next to this file, zmotion.dll next to it
or at "drivers and libraries/zmotion/windows/x64/zmotion.dll", or ZMOTION_LIB set):
    python main_zmotion_spike.py                 # real controller at ZMOTION_IP (default 10.0.0.100)
    python main_zmotion_spike.py --simulation    # simulated camera and an in-memory controller
All other arguments are main_hcs.py's (--verbose, --live-only, --skip-init, --start-server, --qt-api).
"""

import os
import runpy
import sys

# The Qt binding must be chosen before anything imports qtpy (control.zmotion_spike pulls in
# control.microscope, which does). Same call main_hcs.py makes first: --qt-api, else SQUID_QT_API,
# else [GENERAL] qt_api in the configuration ini, else pyqt5.
from squid.qt_binding import select_qt_api  # noqa: E402  (imports no Qt)

select_qt_api(sys.argv)

import control.zmotion_spike as spike  # noqa: E402  (imports control._def, which reads the ini)

simulate = "--simulation" in sys.argv or os.environ.get("ZMOTION_SIMULATE") == "1"
spike.install(ip=os.environ.get("ZMOTION_IP", "10.0.0.100"), lib_path=os.environ.get("ZMOTION_LIB"), simulate=simulate)

sys.argv[0] = "main_hcs.py"
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_hcs.py"), run_name="__main__")
