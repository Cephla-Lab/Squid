# set QT_API environment variable
import os
import sys
import tempfile

# qt libraries
os.environ["QT_API"] = "pyqt5"
import qtpy
import pyqtgraph as pg
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

from control._def import *
from control.core.acquisition import job_processing
from control.core.configuration import ChannelConfigurationManager
from control.core.configuration import ConfigurationManager
from control.core.configuration import ContrastManager
from control.core.autofocus import LaserAFSettingManager
from control.core.display import LiveController
from control.core.acquisition import MultiPointWorker
from control.core.navigation import ObjectiveStore
from control.core.navigation import ScanCoordinates
from control.core.display import (
    StreamHandlerFunctions,
    StreamHandler,
    QtStreamHandler,
    ImageSaver,
    ImageSaver_Tracking,
)
from control.core.display import (
    ImageDisplay,
    ImageDisplayWindow,
    ImageArrayDisplayWindow,
)
from control.core.tracking import (
    TrackingController,
    TrackingWorker,
)
from control.core.navigation import (
    FocusMap,
    NavigationViewer,
)
from control.microcontroller import Microcontroller
from control.peripherals.piezo import PiezoStage
from squid.abc import AbstractStage, AbstractCamera, CameraAcquisitionMode, CameraFrame
import control._def
import control.peripherals.lighting as serial_peripherals
import control.core.tracking.tracking_dasiamrpn as tracking
import control.utils as utils
import control.core.output.utils_acquisition as utils_acquisition
import control.core.utils.utils_channel as utils_channel
import control.utils_config as utils_config
import squid.logging


from typing import List, Tuple, Optional, Dict, Any, Callable, TypeVar
from queue import Queue
from threading import Thread, Lock
from pathlib import Path
from datetime import datetime
from enum import Enum
from control.utils_config import ChannelConfig, ChannelMode, LaserAFConfig
import time
import itertools
import json
import math
import numpy as np
import pandas as pd
import cv2
import imageio as iio
import squid.abc
import scipy.ndimage

if ENABLE_NL5:
    import control.peripherals.nl5 as NL5
else:
    NL5 = TypeVar("NL5")
