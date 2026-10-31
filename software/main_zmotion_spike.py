"""THROWAWAY SPIKE launcher: Squid on a Zmotion-controlled scanner with no Cephla microcontroller.

Usage (bench PC, configuration_Zmotion_spike.ini copied next to this file, zmotion.dll next to it
or at "drivers and libraries/zmotion/windows/x64/zmotion.dll", or ZMOTION_LIB set):
    python main_zmotion_spike.py                 # real controller at ZMOTION_IP (default 10.0.0.100)
    python main_zmotion_spike.py --simulation    # simulated camera and an in-memory controller
All other arguments are main_hcs.py's (--verbose, --live-only, --skip-init, --start-server).
"""

import os
import runpy
import sys

import control.zmotion_spike as spike  # noqa: E402  (imports control._def, which reads the ini)

simulate = "--simulation" in sys.argv or os.environ.get("ZMOTION_SIMULATE") == "1"
spike.install(ip=os.environ.get("ZMOTION_IP", "10.0.0.100"), lib_path=os.environ.get("ZMOTION_LIB"), simulate=simulate)

sys.argv[0] = "main_hcs.py"
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_hcs.py"), run_name="__main__")
