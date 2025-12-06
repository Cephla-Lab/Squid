# Common imports for acquisition widgets
import os
import json
import re
import math
import time
import logging
import yaml
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import numpy as np

import squid.logging

if TYPE_CHECKING:
    from squid.services import StageService

from qtpy.QtCore import Signal, Qt, QTimer
from qtpy.QtWidgets import (
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
    QRadioButton,
    QButtonGroup,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QHeaderView,
    QAbstractItemView,
    QGroupBox,
    QScrollArea,
    QWidget,
    QDialog,
    QListWidget,
    QListWidgetItem,
    QApplication,
    QProgressBar,
    QSpacerItem,
    QShortcut,
)
from qtpy.QtGui import QIcon, QColor, QBrush, QKeySequence

from control._def import *
import control.utils as utils
from control.widgets.base import error_dialog, check_space_available_with_error_dialog
from control.widgets.wellplate import WellSelectionWidget
from squid.abc import AbstractStage
