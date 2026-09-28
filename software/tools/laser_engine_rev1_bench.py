"""Bench GUI for the Cephla laser engine, carrier rev 1: test the engine through the Squid driver without a microscope.

    python3 tools/laser_engine_rev1_bench.py      (from software/ or anywhere; Qt binding from QT_API, e.g. pyqt5)
"""

import os
import sys

software_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(software_dir)
os.chdir(software_dir)  # control._def reads the machine .ini from the working directory

from control.laser_engine_rev1_bench import main

if __name__ == "__main__":
    main()
