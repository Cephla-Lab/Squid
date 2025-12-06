# Common imports for stage widgets
from typing import Optional, TYPE_CHECKING

import squid.logging
from squid.events import event_bus, StagePositionChanged
from qtpy.QtCore import Signal, Qt, QTimer

if TYPE_CHECKING:
    from squid.services import StageService

from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QMessageBox,
    QSizePolicy,
)

from control._def import (
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
    ENABLE_CLICK_TO_MOVE_BY_DEFAULT,
)
from control.core.live_controller import LiveController
from control.peripherals.piezo import PiezoStage
from squid.abc import AbstractStage
