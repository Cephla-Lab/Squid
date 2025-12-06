# Common imports for tracking widgets
import numpy as np
import math

import squid.logging
from qtpy.QtCore import Signal, Qt, QTimer
from qtpy.QtWidgets import (
    QWidget,
    QFrame,
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
    QSizePolicy,
    QGroupBox,
    QFileDialog,
    QMessageBox,
)
from qtpy.QtGui import QPainter, QBrush, QPen, QColor

from control._def import *
from control.core.tracking import TrackingController
