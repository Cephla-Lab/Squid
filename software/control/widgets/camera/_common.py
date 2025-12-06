# Common imports for camera widgets
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import squid.logging
from squid.events import event_bus, ExposureTimeChanged, AnalogGainChanged

if TYPE_CHECKING:
    from squid.services import CameraService

from qtpy.QtCore import Signal, Qt
from qtpy.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
)
from qtpy.QtGui import QIcon

from control._def import (
    TriggerMode,
    CAMERA_CONFIG,
    DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS,
    DEFAULT_SAVING_PATH,
)
import control.utils as utils
from squid.abc import AbstractCamera
from squid.config import CameraPixelFormat
