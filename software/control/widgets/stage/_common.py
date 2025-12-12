# Common imports for stage widgets
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from qtpy.QtCore import Qt, Signal, QTimer
from qtpy.QtWidgets import (
    QFrame,
    QDialog,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QWidget,
    QCheckBox,
    QGroupBox,
    QMessageBox,
)

import squid.logging
from control._def import (
    ENABLE_CLICK_TO_MOVE_BY_DEFAULT,
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
)
from squid.abc import AbstractStage
from squid.services import StageService
from squid.events import StagePositionChanged
from control.core.display import LiveController
from control.core.autofocus import AutoFocusController
from control.peripherals.piezo import PiezoStage
from control.widgets.base import EventBusFrame, EventBusDialog

if TYPE_CHECKING:
    from squid.events import EventBus
