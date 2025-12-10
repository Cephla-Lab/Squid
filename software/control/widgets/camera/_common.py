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
from squid.events import (
    event_bus,
    ExposureTimeChanged,
    AnalogGainChanged,
    SetExposureTimeCommand,
    SetAnalogGainCommand,
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    SetTriggerModeCommand,
    SetTriggerFPSCommand,
    SetMicroscopeModeCommand,
    TriggerModeChanged,
    TriggerFPSChanged,
    MicroscopeModeChanged,
    # New camera settings commands
    SetROICommand,
    SetBinningCommand,
    SetPixelFormatCommand,
    SetCameraTemperatureCommand,
    SetBlackLevelCommand,
    SetAutoWhiteBalanceCommand,
    # State events
    ROIChanged,
    BinningChanged,
    PixelFormatChanged,
    CameraTemperatureChanged,
    BlackLevelChanged,
    AutoWhiteBalanceChanged,
)
from control.core.display import StreamHandler, LiveController, ImageSaver
from control.core.navigation import ObjectiveStore
from control.core.configuration import ChannelConfigurationManager
from control.utils_config import ChannelMode
from control.widgets.base import EventBusFrame

if TYPE_CHECKING:
    from squid.events import EventBus
    from squid.abc import CameraGainRange
