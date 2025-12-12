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

import squid.core.logging
from _def import (
    ENABLE_CLICK_TO_MOVE_BY_DEFAULT,
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
)
from squid.core.abc import AbstractStage
from squid.mcs.services import StageService
from squid.core.events import StagePositionChanged
from squid.mcs.controllers.live_controller import LiveController
from squid.mcs.controllers.autofocus import AutoFocusController
from squid.mcs.drivers.peripherals.piezo import PiezoStage
from squid.ui.widgets.base import EventBusFrame, EventBusDialog

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus
