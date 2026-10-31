"""THROWAWAY SPIKE launcher: Squid on a Zmotion-controlled scanner with no Cephla microcontroller.

Usage (bench PC, configuration_Zmotion_spike.ini copied next to this file; the vendor library is
vendored at "drivers and libraries/zmotion/windows/x64/zmotion.dll" (Windows x64) and
"drivers and libraries/zmotion/linux/x64/libzmotion.so" (Linux x86-64); ZMOTION_LIB overrides it.
Ubuntu notes: docs/zmotion-ubuntu.md):
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

# Squid's settings restart re-runs [sys.executable] + sys.argv (gui_hcs.py), so argv[0] must stay this
# launcher: a restart through main_hcs.py alone would come up without the shim (bench run 6).
sys.argv[0] = os.path.abspath(__file__)
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_hcs.py"), run_name="__main__")
