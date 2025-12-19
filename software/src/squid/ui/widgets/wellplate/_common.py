# Common imports for wellplate widgets
from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, Any, Optional, Dict, List, Tuple, Union

import numpy as np

from qtpy.QtCore import Qt, Signal, QModelIndex, QVariant
from qtpy.QtGui import QResizeEvent, QWheelEvent, QFont, QPen, QColor
from qtpy.QtWidgets import (
    QFrame,
    QWidget,
    QDialog,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QCheckBox,
    QGroupBox,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QLineEdit,
    QRadioButton,
    QButtonGroup,
    QSlider,
)

try:
    import pyqtgraph as pg
except ImportError:
    pg = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None  # type: ignore[misc, assignment]
    ImageDraw = None  # type: ignore[misc, assignment]
    ImageFont = None  # type: ignore[misc, assignment]

import squid.core.logging
from squid.core.abc import AbstractStage
from squid.backend.services import StageService
from _def import (
    CACHE_DIR,
    WELLPLATE_FORMAT,
    WELLPLATE_FORMAT_SETTINGS,
    SAMPLE_FORMATS_CSV_PATH,
    INVERTED_OBJECTIVE,
    CAMERA_CONFIG,
)
from squid.ui.widgets.base import EventBusDialog

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus
