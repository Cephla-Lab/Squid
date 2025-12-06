# Common imports for wellplate widgets
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Dict, List, Tuple, Union

from qtpy.QtCore import Qt, Signal, QModelIndex, QVariant
from qtpy.QtGui import QResizeEvent, QWheelEvent, QFont
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

import squid.logging
from squid.abc import AbstractStage
from squid.services import StageService
from control._def import WELLPLATE_FORMAT, WELLPLATE_FORMAT_SETTINGS, SAMPLE_FORMATS_CSV_PATH

if TYPE_CHECKING:
    pass
