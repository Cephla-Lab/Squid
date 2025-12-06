# Common imports for wellplate widgets
import numpy as np
import json
import math
import time
from typing import Optional, TYPE_CHECKING

import squid.logging

if TYPE_CHECKING:
    from squid.services import StageService

from qtpy.QtCore import Signal, Qt, QTimer, QVariant
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
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QSizePolicy,
    QGroupBox,
    QMessageBox,
    QFileDialog,
)
from qtpy.QtGui import QColor, QBrush, QFont

from control._def import *
from squid.abc import AbstractStage
