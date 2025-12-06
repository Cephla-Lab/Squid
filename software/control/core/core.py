# set QT_API environment variable
import os

# qt libraries
os.environ["QT_API"] = "pyqt5"
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *


from typing import TypeVar

if ENABLE_NL5:
    import control.peripherals.nl5 as NL5
else:
    NL5 = TypeVar("NL5")
