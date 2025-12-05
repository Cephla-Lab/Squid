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
from control.core import job_processing
from control.core.channel_configuration_mananger import ChannelConfigurationManager
from control.core.configuration_mananger import ConfigurationManager
from control.core.contrast_manager import ContrastManager
from control.core.laser_af_settings_manager import LaserAFSettingManager
from control.core.live_controller import LiveController
from control.core.multi_point_worker import MultiPointWorker
from control.core.objective_store import ObjectiveStore
from control.core.scan_coordinates import ScanCoordinates
from control.core.stream_handler import (
    StreamHandlerFunctions,
    StreamHandler,
    QtStreamHandler,
    ImageSaver,
    ImageSaver_Tracking,
)
from control.core.image_display import (
    ImageDisplay,
    ImageDisplayWindow,
    ImageArrayDisplayWindow,
)
from control.core.tracking import (
    TrackingController,
    TrackingWorker,
)
from control.core.focus_map import (
    FocusMap,
    NavigationViewer,
)
from control.microcontroller import Microcontroller
from control.peripherals.piezo import PiezoStage
from squid.abc import AbstractStage, AbstractCamera, CameraAcquisitionMode, CameraFrame
import control._def
import control.peripherals.lighting as serial_peripherals
import control.core.tracking_dasiamrpn as tracking
import control.utils as utils
import control.utils_acquisition as utils_acquisition
import control.utils_channel as utils_channel
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
