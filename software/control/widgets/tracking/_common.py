# Common imports for tracking widgets
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, List

from qtpy.QtCore import Qt, Signal, QTimer
from qtpy.QtGui import QPainter, QColor, QPen, QBrush
from qtpy.QtWidgets import (
    QFrame,
    QWidget,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QCheckBox,
    QGroupBox,
    QLineEdit,
    QFileDialog,
)

import squid.logging
from control._def import (
    DEFAULT_SAVING_PATH,
    TUBE_LENS_MM,
    CAMERA_PIXEL_SIZE_UM,
    CAMERA_SENSOR,
    TRACKERS,
    DEFAULT_TRACKER,
    PLATE_READER,
)
from control.core.navigation import ObjectiveStore
from control.core.configuration import ChannelConfigurationManager
from control.core.tracking import TrackingController

if TYPE_CHECKING:
    pass
