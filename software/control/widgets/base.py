# Base utility widgets and functions
import logging
from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QMainWindow,
    QGroupBox,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QAbstractTableModel,
)
from qtpy.QtGui import QBrush, QColor

import pandas as pd

import control.utils as utils

if TYPE_CHECKING:
    from control.core.multi_point_controller import MultiPointController


def error_dialog(message: str, title: str = "Error"):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setText(message)
    msg.setWindowTitle(title)
    msg.setStandardButtons(QMessageBox.Ok)
    msg.setDefaultButton(QMessageBox.Ok)
    retval = msg.exec_()
    return


def check_space_available_with_error_dialog(
    multi_point_controller: "MultiPointController", logger: logging.Logger, factor_of_safecty: float = 1.03
) -> bool:
    # To check how much disk space is required, we need to have the MultiPointController all configured.  That is
    # a precondition of this function.
    save_directory = multi_point_controller.base_path
    available_disk_space = utils.get_available_disk_space(save_directory)
    space_required = factor_of_safecty * multi_point_controller.get_estimated_acquisition_disk_storage()
    image_count = multi_point_controller.get_acquisition_image_count()

    logger.info(
        f"Checking space available: {space_required=}, {available_disk_space=}, {image_count=}, {save_directory=}"
    )
    if space_required > available_disk_space:
        megabytes_required = int(space_required / 1024 / 1024)
        megabytes_available = int(available_disk_space / 1024 / 1024)
        error_message = (
            f"This acquisition will capture {image_count:,} images, which will"
            f" require {megabytes_required:,} [MB], but '{save_directory}' only has {megabytes_available:,} [MB] available."
        )
        logger.error(error_message)
        error_dialog(error_message, title="Not Enough Disk Space")
        return False
    return True


class WrapperWindow(QMainWindow):
    def __init__(self, content_widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCentralWidget(content_widget)
        self.hide()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def closeForReal(self, event):
        super().closeEvent(event)


class CollapsibleGroupBox(QGroupBox):
    def __init__(self, title):
        super(CollapsibleGroupBox, self).__init__(title)
        self.setCheckable(True)
        self.setChecked(True)
        self.higher_layout = QVBoxLayout()
        self.content = QVBoxLayout()
        # self.content.setAlignment(Qt.AlignTop)
        self.content_widget = QWidget()
        self.content_widget.setLayout(self.content)
        self.higher_layout.addWidget(self.content_widget)
        self.setLayout(self.higher_layout)
        self.toggled.connect(self.toggle_content)

    def toggle_content(self, state):
        self.content_widget.setVisible(state)


class PandasTableModel(QAbstractTableModel):
    """Model for displaying pandas DataFrame in a QTableView"""

    def __init__(self, data, port_names=None):
        super().__init__()
        self._data = data
        self._current_row = -1
        self._port_names = port_names or []
        self._column_name_map = {
            "sequence_name": "Sequence Name",
            "fluidic_port": "Fluidic Port",
            "fill_tubing_with": "Fill Tubing With",
            "flow_rate": "Flow Rate (µL/min)",
            "volume": "Volume (µL)",
            "incubation_time": "Incubation (min)",
            "repeat": "Repeat",
        }

    def rowCount(self, parent=None):
        return len(self._data)

    def columnCount(self, parent=None):
        return len(self._data.columns)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            value = self._data.iloc[index.row(), index.column()]
            if pd.isna(value):
                return ""

            # Map port numbers to names for specific columns
            column_name = self._data.columns[index.column()]
            if column_name in ["fluidic_port", "fill_tubing_with"] and self._port_names:
                try:
                    # Convert value to integer and get corresponding name
                    port_num = int(value)
                    if 1 <= port_num <= len(self._port_names):
                        return self._port_names[port_num - 1]
                except (ValueError, TypeError):
                    pass

            return str(value)

        elif role == Qt.BackgroundRole:
            # Highlight the current row
            if index.row() == self._current_row:
                return QBrush(QColor(173, 216, 230))  # Light blue
            else:
                return QBrush(QColor(255, 255, 255))  # White
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            original_name = str(self._data.columns[section])
            return self._column_name_map.get(original_name, original_name)
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None

    def set_current_row(self, row_index):
        self._current_row = row_index
        self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))
