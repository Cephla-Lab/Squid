"""Bench GUI for the Cephla laser engine v2: test the engine through the Squid driver without a microscope.

python3 tools/laser_engine_v2_bench.py      (from software/ or anywhere; Qt binding from QT_API, e.g. pyqt5)
"""

import os
import sys

software_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(software_dir)
os.chdir(software_dir)  # the machine .ini (control._def) and cache/ are read from the working directory

from control.laser_engine_v2_bench import main

if __name__ == "__main__":
    main()
