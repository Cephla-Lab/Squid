# Common imports for display widgets
import numpy as np
from typing import Optional, TYPE_CHECKING

from qtpy.QtCore import Signal, Qt, QTimer
from qtpy.QtWidgets import (
    QFrame,
    QWidget,
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
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)

from squid.logging import get_logger

if TYPE_CHECKING:
    from squid.services import StageService, CameraService
