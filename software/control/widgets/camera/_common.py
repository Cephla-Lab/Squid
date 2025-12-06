# Common imports for camera widgets
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Dict, List

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QFrame,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QGridLayout,
    QWidget,
    QCheckBox,
    QSlider,
    QLineEdit,
    QFileDialog,
)
from qtpy.QtGui import QIcon

import squid.logging
from control._def import (
    DEFAULT_SAVING_PATH,
    DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS,
    CAMERA_CONFIG,
    TriggerMode,
)
from squid.abc import CameraPixelFormat
from squid.services import CameraService
from squid.events import event_bus, ExposureTimeChanged, AnalogGainChanged
from control.core.display import StreamHandler, LiveController, ImageSaver
from control.core.navigation import ObjectiveStore
from control.core.configuration import ChannelConfigurationManager
from control.utils_config import ChannelMode

if TYPE_CHECKING:
    pass
