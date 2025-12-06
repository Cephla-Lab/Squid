# Common imports for hardware widgets
import numpy as np
from typing import TYPE_CHECKING, Optional

from qtpy.QtCore import Signal, Qt
from qtpy.QtWidgets import (
    QWidget,
    QFrame,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QSizePolicy,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QDialogButtonBox,
)
from qtpy.QtGui import QColor

from squid.logging import get_logger

if TYPE_CHECKING:
    from squid.services import PeripheralService
