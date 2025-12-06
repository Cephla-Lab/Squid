# set QT_API environment variable
import os

# qt libraries
os.environ["QT_API"] = "pyqt5"
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *


from typing import TypeVar

# Re-export classes that are accessed via `core.ClassName` pattern
from control.core.display.stream_handler import QtStreamHandler, ImageSaver
from control.core.display.image_display import (
    ImageDisplay,
    ImageDisplayWindow,
    ImageArrayDisplayWindow,
)
from control.core.navigation.focus_map import NavigationViewer, FocusMap
from control.core.tracking.tracking import TrackingController

if ENABLE_NL5:
    import control.peripherals.nl5 as NL5
else:
    NL5 = TypeVar("NL5")
