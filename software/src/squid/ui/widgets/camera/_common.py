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
    QMessageBox,
)
from qtpy.QtGui import QIcon

import squid.core.logging
from _def import (
    DEFAULT_SAVING_PATH,
    DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS,
    CAMERA_CONFIG,
    TriggerMode,
)
from squid.core.abc import CameraPixelFormat
from squid.core.events import (
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
    AutoLevelCommand,
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
    # Objective and channel config events
    ObjectiveChanged,
    ChannelConfigurationsChanged,
    UpdateChannelConfigurationCommand,
    ProfileChanged,
)
from squid.backend.io.stream_handler import StreamHandler
from squid.ui.image_saver import ImageSaver
from squid.backend.managers import ObjectiveStore
from squid.backend.managers import ChannelConfigurationManager
from squid.core.utils.config_utils import ChannelMode
from squid.ui.widgets.base import EventBusFrame

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus
    from squid.core.abc import CameraGainRange
