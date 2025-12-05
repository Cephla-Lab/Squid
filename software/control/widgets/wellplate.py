# Wellplate and calibration widgets
import numpy as np
import json
import math
import time

import squid.logging
from qtpy.QtCore import Signal, Qt, QTimer
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
from qtpy.QtGui import QColor, QBrush

from control._def import *
from squid.abc import AbstractStage


class WellSelectionWidget(QTableWidget):
    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self, format_, wellplateFormatWidget, *args, **kwargs):
        super(WellSelectionWidget, self).__init__(*args, **kwargs)
        self.wellplateFormatWidget = wellplateFormatWidget
        self.cellDoubleClicked.connect(self.onDoubleClick)
        self.itemSelectionChanged.connect(self.onSelectionChanged)
        self.fixed_height = 400
        self.setFormat(format_)

    def setFormat(self, format_):
        self.format = format_
        settings = self.wellplateFormatWidget.getWellplateSettings(self.format)
        self.rows = settings["rows"]
        self.columns = settings["cols"]
        self.spacing_mm = settings["well_spacing_mm"]
        self.number_of_skip = settings["number_of_skip"]
        self.a1_x_mm = settings["a1_x_mm"]
        self.a1_y_mm = settings["a1_y_mm"]
        self.a1_x_pixel = settings["a1_x_pixel"]
        self.a1_y_pixel = settings["a1_y_pixel"]
        self.well_size_mm = settings["well_size_mm"]

        self.setRowCount(self.rows)
        self.setColumnCount(self.columns)
        self.initUI()
        self.setData()

    def initUI(self):
        # Disable editing, scrollbars, and other interactions
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setDisabled(True)
        self.horizontalScrollBar().setDisabled(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTabKeyNavigation(False)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropOverwriteMode(False)
        self.setMouseTracking(False)

        if self.format == "1536 well plate":
            font = QFont()
            font.setPointSize(6)  # You can adjust this value as needed
        else:
            font = QFont()
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)

        self.setLayout()

    def setLayout(self):
        # Calculate available space and cell size
        header_height = self.horizontalHeader().height()
        available_height = self.fixed_height - header_height  # Fixed height of 408 pixels

        # Calculate cell size based on the minimum of available height and width
        cell_size = available_height // self.rowCount()

        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(cell_size)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setDefaultSectionSize(cell_size)

        # Ensure sections do not resize
        self.verticalHeader().setMinimumSectionSize(cell_size)
        self.verticalHeader().setMaximumSectionSize(cell_size)
        self.horizontalHeader().setMinimumSectionSize(cell_size)
        self.horizontalHeader().setMaximumSectionSize(cell_size)

        row_header_width = self.verticalHeader().width()

        # Calculate total width and height
        total_height = (self.rowCount() * cell_size) + header_height
        total_width = (self.columnCount() * cell_size) + row_header_width

        # Set the widget's fixed size
        self.setFixedHeight(total_height)
        self.setFixedWidth(total_width)

        # Force the widget to update its layout
        self.updateGeometry()
        self.viewport().update()

    def onWellplateChanged(self):
        self.setFormat(self.wellplateFormatWidget.wellplate_format)

    def setData(self):
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                item = self.item(i, j)
                if not item:  # Create a new item if none exists
                    item = QTableWidgetItem()
                    self.setItem(i, j, item)
                # Reset to selectable by default
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        if self.number_of_skip > 0 and self.format != 0:
            for i in range(self.number_of_skip):
                for j in range(self.columns):  # Apply to rows
                    self.item(i, j).setFlags(self.item(i, j).flags() & ~Qt.ItemIsSelectable)
                    self.item(self.rows - 1 - i, j).setFlags(
                        self.item(self.rows - 1 - i, j).flags() & ~Qt.ItemIsSelectable
                    )
                for k in range(self.rows):  # Apply to columns
                    self.item(k, i).setFlags(self.item(k, i).flags() & ~Qt.ItemIsSelectable)
                    self.item(k, self.columns - 1 - i).setFlags(
                        self.item(k, self.columns - 1 - i).flags() & ~Qt.ItemIsSelectable
                    )

        # Update row headers
        row_headers = []
        for i in range(self.rows):
            if i < 26:
                label = chr(ord("A") + i)
            else:
                first_letter = chr(ord("A") + (i // 26) - 1)
                second_letter = chr(ord("A") + (i % 26))
                label = first_letter + second_letter
            row_headers.append(label)
        self.setVerticalHeaderLabels(row_headers)

        # Adjust vertical header width after setting labels
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def onDoubleClick(self, row, col):
        print("double click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)
            print("well location:", (x_mm, y_mm))
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSingleClick(self, row, col):
        print("single click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSelectionChanged(self):
        # Check if there are any selected indexes before proceeding
        if self.format != "glass slide":
            has_selection = bool(self.selectedIndexes())
            self.signal_wellSelected.emit(has_selection)

    def get_selected_cells(self):
        list_of_selected_cells = []
        print("getting selected cells...")
        if self.format == "glass slide":
            return list_of_selected_cells
        for index in self.selectedIndexes():
            row, col = index.row(), index.column()
            # Check if the cell is within the allowed bounds
            if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
                col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
            ):
                list_of_selected_cells.append((row, col))
        if list_of_selected_cells:
            print("cells:", list_of_selected_cells)
        else:
            print("no cells")
        return list_of_selected_cells

    def resizeEvent(self, event):
        self.initUI()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        # Ignore wheel events to prevent scrolling
        event.ignore()

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        pass

    def set_white_boundaries_style(self):
        style = """
        QTableWidget {
            gridline-color: white;
            border: 1px solid white;
        }
        QHeaderView::section {
            color: white;
        }
        """
        self.setStyleSheet(style)


class WellplateFormatWidget(QWidget):

    signalWellplateSettings = Signal(QVariant, float, float, int, int, float, float, int, int, int)

    def __init__(self, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.wellplate_format = WELLPLATE_FORMAT
        self.csv_path = SAMPLE_FORMATS_CSV_PATH  # 'sample_formats.csv'
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        self.label = QLabel("Sample Format", self)
        self.comboBox = QComboBox(self)
        self.populate_combo_box()
        self.comboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.label)
        layout.addWidget(self.comboBox)
        self.comboBox.currentIndexChanged.connect(self.wellplateChanged)
        index = self.comboBox.findData(self.wellplate_format)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)

    def populate_combo_box(self):
        self.comboBox.clear()
        for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
            self.comboBox.addItem(format_, format_)

        # Add custom item and set its font to italic
        self.comboBox.addItem("calibrate format...", "custom")
        index = self.comboBox.count() - 1  # Get the index of the last item
        font = QFont()
        font.setItalic(True)
        self.comboBox.setItemData(index, font, Qt.FontRole)

    def wellplateChanged(self, index):
        self.wellplate_format = self.comboBox.itemData(index)
        if self.wellplate_format == "custom":
            calibration_dialog = WellplateCalibration(
                self, self.stage, self.navigationViewer, self.streamHandler, self.liveController
            )
            result = calibration_dialog.exec_()
            if result == QDialog.Rejected:
                # If the dialog was closed without adding a new format, revert to the previous selection
                prev_index = self.comboBox.findData(self.wellplate_format)
                self.comboBox.setCurrentIndex(prev_index)
        else:
            self.setWellplateSettings(self.wellplate_format)

    def setWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            self.signalWellplateSettings.emit(QVariant("glass slide"), 0, 0, 0, 0, 0, 0, 0, 1, 1)
            return
        else:
            print(f"Wellplate format {wellplate_format} not recognized")
            return

        self.signalWellplateSettings.emit(
            QVariant(wellplate_format),
            settings["a1_x_mm"],
            settings["a1_y_mm"],
            settings["a1_x_pixel"],
            settings["a1_y_pixel"],
            settings["well_size_mm"],
            settings["well_spacing_mm"],
            settings["number_of_skip"],
            settings["rows"],
            settings["cols"],
        )

    def getWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            settings = {
                "format": "glass slide",
                "a1_x_mm": 0,
                "a1_y_mm": 0,
                "a1_x_pixel": 0,
                "a1_y_pixel": 0,
                "well_size_mm": 0,
                "well_spacing_mm": 0,
                "number_of_skip": 0,
                "rows": 1,
                "cols": 1,
            }
        else:
            return None
        return settings

    def add_custom_format(self, name, settings):
        WELLPLATE_FORMAT_SETTINGS[name] = settings
        self.populate_combo_box()
        index = self.comboBox.findData(name)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)
        self.wellplateChanged(index)

    def save_formats_to_csv(self):
        cache_path = os.path.join("cache", self.csv_path)
        os.makedirs("cache", exist_ok=True)

        fieldnames = [
            "format",
            "a1_x_mm",
            "a1_y_mm",
            "a1_x_pixel",
            "a1_y_pixel",
            "well_size_mm",
            "well_spacing_mm",
            "number_of_skip",
            "rows",
            "cols",
        ]
        with open(cache_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
                writer.writerow({**{"format": format_}, **settings})

    @staticmethod
    def parse_csv_row(row):
        return {
            "a1_x_mm": float(row["a1_x_mm"]),
            "a1_y_mm": float(row["a1_y_mm"]),
            "a1_x_pixel": int(row["a1_x_pixel"]),
            "a1_y_pixel": int(row["a1_y_pixel"]),
            "well_size_mm": float(row["well_size_mm"]),
            "well_spacing_mm": float(row["well_spacing_mm"]),
            "number_of_skip": int(row["number_of_skip"]),
            "rows": int(row["rows"]),
            "cols": int(row["cols"]),
        }


class WellplateCalibration(QDialog):

    def __init__(self, wellplateFormatWidget, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.setWindowTitle("Well Plate Calibration")
        self.wellplateFormatWidget = wellplateFormatWidget
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.was_live = self.liveController.is_live
        self.corners = [None, None, None]
        self.show_virtual_joystick = True  # FLAG
        self.initUI()
        # Initially allow click-to-move and hide the joystick controls
        self.clickToMoveCheckbox.setChecked(True)
        self.toggleVirtualJoystick(False)

    def initUI(self):
        layout = QHBoxLayout(self)  # Change to QHBoxLayout to have two columns

        # Left column for existing controls
        left_layout = QVBoxLayout()

        # Add radio buttons for selecting mode
        self.mode_group = QButtonGroup(self)
        self.new_format_radio = QRadioButton("Add New Format")
        self.calibrate_format_radio = QRadioButton("Calibrate Existing Format")
        self.mode_group.addButton(self.new_format_radio)
        self.mode_group.addButton(self.calibrate_format_radio)
        self.new_format_radio.setChecked(True)

        left_layout.addWidget(self.new_format_radio)
        left_layout.addWidget(self.calibrate_format_radio)

        # Existing format selection (initially hidden)
        self.existing_format_combo = QComboBox(self)
        self.populate_existing_formats()
        self.existing_format_combo.hide()
        left_layout.addWidget(self.existing_format_combo)

        # Connect radio buttons to toggle visibility
        self.new_format_radio.toggled.connect(self.toggle_input_mode)
        self.calibrate_format_radio.toggled.connect(self.toggle_input_mode)

        self.form_layout = QFormLayout()

        self.nameInput = QLineEdit(self)
        self.nameInput.setPlaceholderText("custom well plate")
        self.form_layout.addRow("Sample Name:", self.nameInput)

        self.rowsInput = QSpinBox(self)
        self.rowsInput.setKeyboardTracking(False)
        self.rowsInput.setRange(1, 100)
        self.rowsInput.setValue(8)
        self.form_layout.addRow("# Rows:", self.rowsInput)

        self.colsInput = QSpinBox(self)
        self.colsInput.setKeyboardTracking(False)
        self.colsInput.setRange(1, 100)
        self.colsInput.setValue(12)
        self.form_layout.addRow("# Columns:", self.colsInput)

        # Add new inputs for plate dimensions
        self.plateWidthInput = QDoubleSpinBox(self)
        self.plateWidthInput.setKeyboardTracking(False)
        self.plateWidthInput.setRange(10, 500)  # Adjust range as needed
        self.plateWidthInput.setValue(127.76)  # Default value for a standard 96-well plate
        self.plateWidthInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Width:", self.plateWidthInput)

        self.plateHeightInput = QDoubleSpinBox(self)
        self.plateHeightInput.setKeyboardTracking(False)
        self.plateHeightInput.setRange(10, 500)  # Adjust range as needed
        self.plateHeightInput.setValue(85.48)  # Default value for a standard 96-well plate
        self.plateHeightInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Height:", self.plateHeightInput)

        self.wellSpacingInput = QDoubleSpinBox(self)
        self.wellSpacingInput.setKeyboardTracking(False)
        self.wellSpacingInput.setRange(0.1, 100)
        self.wellSpacingInput.setValue(9)
        self.wellSpacingInput.setSingleStep(0.1)
        self.wellSpacingInput.setDecimals(2)
        self.wellSpacingInput.setSuffix(" mm")
        self.form_layout.addRow("Well Spacing:", self.wellSpacingInput)

        left_layout.addLayout(self.form_layout)

        points_layout = QGridLayout()
        self.cornerLabels = []
        self.setPointButtons = []
        navigate_label = QLabel("Navigate to and Select\n3 Points on the Edge of Well A1")
        navigate_label.setAlignment(Qt.AlignCenter)
        # navigate_label.setStyleSheet("font-weight: bold;")
        points_layout.addWidget(navigate_label, 0, 0, 1, 2)
        for i in range(1, 4):
            label = QLabel(f"Point {i}: N/A")
            button = QPushButton("Set Point")
            button.setFixedWidth(button.sizeHint().width())
            button.clicked.connect(lambda checked, index=i - 1: self.setCorner(index))
            points_layout.addWidget(label, i, 0)
            points_layout.addWidget(button, i, 1)
            self.cornerLabels.append(label)
            self.setPointButtons.append(button)

        points_layout.setColumnStretch(0, 1)
        left_layout.addLayout(points_layout)

        # Add 'Click to Move' checkbox
        self.clickToMoveCheckbox = QCheckBox("Click to Move")
        self.clickToMoveCheckbox.stateChanged.connect(self.toggleClickToMove)
        left_layout.addWidget(self.clickToMoveCheckbox)

        # Add 'Show Virtual Joystick' checkbox
        self.showJoystickCheckbox = QCheckBox("Virtual Joystick")
        self.showJoystickCheckbox.stateChanged.connect(self.toggleVirtualJoystick)
        left_layout.addWidget(self.showJoystickCheckbox)

        self.calibrateButton = QPushButton("Calibrate")
        self.calibrateButton.clicked.connect(self.calibrate)
        self.calibrateButton.setEnabled(False)
        left_layout.addWidget(self.calibrateButton)

        # Add left column to main layout
        layout.addLayout(left_layout)

        self.live_viewer = CalibrationLiveViewer()
        self.streamHandler.image_to_display.connect(self.live_viewer.display_image)

        if not self.was_live:
            self.liveController.start_live()

        # when the dialog closes i want to # self.liveController.stop_live() if live was stopped before. . . if it was on before, leave it on
        layout.addWidget(self.live_viewer)

        # Right column for joystick and sensitivity controls
        self.right_layout = QVBoxLayout()
        self.right_layout.addStretch(1)

        self.joystick = Joystick(self)
        self.joystick.joystickMoved.connect(self.moveStage)
        self.right_layout.addWidget(self.joystick, 0, Qt.AlignTop | Qt.AlignHCenter)

        self.right_layout.addStretch(1)

        # Create a container widget for sensitivity label and slider
        sensitivity_layout = QVBoxLayout()

        sensitivityLabel = QLabel("Joystick Sensitivity")
        sensitivityLabel.setAlignment(Qt.AlignCenter)
        sensitivity_layout.addWidget(sensitivityLabel)

        self.sensitivitySlider = QSlider(Qt.Horizontal)
        self.sensitivitySlider.setMinimum(1)
        self.sensitivitySlider.setMaximum(100)
        self.sensitivitySlider.setValue(50)
        self.sensitivitySlider.setTickPosition(QSlider.TicksBelow)
        self.sensitivitySlider.setTickInterval(10)

        label_width = sensitivityLabel.sizeHint().width()
        self.sensitivitySlider.setFixedWidth(label_width)

        sensitivity_layout.addWidget(self.sensitivitySlider, 0, Qt.AlignHCenter)

        self.right_layout.addLayout(sensitivity_layout)

        layout.addLayout(self.right_layout)

        if not self.was_live:
            self.liveController.start_live()

    def toggleVirtualJoystick(self, state):
        if state:
            self.joystick.show()
            self.sensitivitySlider.show()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().show()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().show()  # Show sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().show()  # Show sensitivity slider
        else:
            self.joystick.hide()
            self.sensitivitySlider.hide()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().hide()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().hide()  # Hide sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().hide()  # Hide sensitivity slider

    def moveStage(self, x, y):
        sensitivity = self.sensitivitySlider.value() / 50.0  # Normalize to 0-2 range
        max_speed = 0.1 * sensitivity
        exponent = 2

        dx = math.copysign(max_speed * abs(x) ** exponent, x)
        dy = math.copysign(max_speed * abs(y) ** exponent, y)

        self.stage.move_x(dx)
        self.stage.move_y(dy)

    def toggleClickToMove(self, state):
        if state == Qt.Checked:
            self.live_viewer.signal_calibration_viewer_click.connect(self.viewerClicked)
        else:
            self.live_viewer.signal_calibration_viewer_click.disconnect(self.viewerClicked)

    def viewerClicked(self, x, y, width, height):
        pixel_size_um = (
            self.navigationViewer.objectiveStore.get_pixel_size_factor()
            * self.liveController.microscope.camera.get_pixel_size_binned_um()
        )

        pixel_sign_x = 1
        pixel_sign_y = 1 if INVERTED_OBJECTIVE else -1

        delta_x = pixel_sign_x * pixel_size_um * x / 1000.0
        delta_y = pixel_sign_y * pixel_size_um * y / 1000.0

        self.stage.move_x(delta_x)
        self.stage.move_y(delta_y)

    def setCorner(self, index):
        if self.corners[index] is None:
            pos = self.stage.get_pos()
            x = pos.x_mm
            y = pos.y_mm

            # Check if the new point is different from existing points
            if any(corner is not None and np.allclose([x, y], corner) for corner in self.corners):
                QMessageBox.warning(
                    self,
                    "Duplicate Point",
                    "This point is too close to an existing point. Please choose a different location.",
                )
                return

            self.corners[index] = (x, y)
            self.cornerLabels[index].setText(f"Point {index+1}: ({x:.2f}, {y:.2f})")
            self.setPointButtons[index].setText("Clear Point")
        else:
            self.corners[index] = None
            self.cornerLabels[index].setText(f"Point {index+1}: Not set")
            self.setPointButtons[index].setText("Set Point")

        self.calibrateButton.setEnabled(all(corner is not None for corner in self.corners))

    def populate_existing_formats(self):
        self.existing_format_combo.clear()
        for format_ in WELLPLATE_FORMAT_SETTINGS:
            self.existing_format_combo.addItem(f"{format_} well plate", format_)

    def toggle_input_mode(self):
        if self.new_format_radio.isChecked():
            self.existing_format_combo.hide()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().show()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().show()
        else:
            self.existing_format_combo.show()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().hide()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().hide()

    def calibrate(self):
        try:
            if self.new_format_radio.isChecked():
                if not self.nameInput.text() or not all(self.corners):
                    QMessageBox.warning(
                        self,
                        "Incomplete Information",
                        "Please fill in all fields and set 3 corner points before calibrating.",
                    )
                    return

                name = self.nameInput.text()
                rows = self.rowsInput.value()
                cols = self.colsInput.value()
                well_spacing_mm = self.wellSpacingInput.value()
                plate_width_mm = self.plateWidthInput.value()
                plate_height_mm = self.plateHeightInput.value()

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center
                scale = 1 / 0.084665
                a1_x_pixel = round(a1_x_mm * scale)
                a1_y_pixel = round(a1_y_mm * scale)

                new_format = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "a1_x_pixel": a1_x_pixel,
                    "a1_y_pixel": a1_y_pixel,
                    "well_size_mm": well_size_mm,
                    "well_spacing_mm": well_spacing_mm,
                    "number_of_skip": 0,
                    "rows": rows,
                    "cols": cols,
                }

                self.wellplateFormatWidget.add_custom_format(name, new_format)
                self.wellplateFormatWidget.save_formats_to_csv()
                self.create_wellplate_image(name, new_format, plate_width_mm, plate_height_mm)
                self.wellplateFormatWidget.setWellplateSettings(name)
                success_message = f"New format '{name}' has been successfully created and calibrated."

            else:
                selected_format = self.existing_format_combo.currentData()
                if not all(self.corners):
                    QMessageBox.warning(
                        self, "Incomplete Information", "Please set 3 corner points before calibrating."
                    )
                    return

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center

                # Get the existing format settings
                existing_settings = WELLPLATE_FORMAT_SETTINGS[selected_format]

                print(f"Updating existing format {selected_format} well plate")
                print(
                    f"OLD: 'a1_x_mm': {existing_settings['a1_x_mm']}, 'a1_y_mm': {existing_settings['a1_y_mm']}, 'well_size_mm': {existing_settings['well_size_mm']}"
                )
                print(f"NEW: 'a1_x_mm': {a1_x_mm}, 'a1_y_mm': {a1_y_mm}, 'well_size_mm': {well_size_mm}")

                updated_settings = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "well_size_mm": well_size_mm,
                }

                WELLPLATE_FORMAT_SETTINGS[selected_format].update(updated_settings)

                self.wellplateFormatWidget.save_formats_to_csv()
                self.wellplateFormatWidget.setWellplateSettings(selected_format)
                success_message = f"Format '{selected_format} well plate' has been successfully recalibrated."

            # Update the WellplateFormatWidget's combo box to reflect the newly calibrated format
            self.wellplateFormatWidget.populate_combo_box()
            index = self.wellplateFormatWidget.comboBox.findData(
                selected_format if self.calibrate_format_radio.isChecked() else name
            )
            if index >= 0:
                self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

            # Display success message
            QMessageBox.information(self, "Calibration Successful", success_message)
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Calibration Error", f"An error occurred during calibration: {str(e)}")

    def create_wellplate_image(self, name, format_data, plate_width_mm, plate_height_mm):

        scale = 1 / 0.084665

        def mm_to_px(mm):
            return round(mm * scale)

        width = mm_to_px(plate_width_mm)
        height = mm_to_px(plate_height_mm)
        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        rows, cols = format_data["rows"], format_data["cols"]
        well_spacing_mm = format_data["well_spacing_mm"]
        well_size_mm = format_data["well_size_mm"]
        a1_x_mm, a1_y_mm = format_data["a1_x_mm"], format_data["a1_y_mm"]

        def draw_left_slanted_rectangle(draw, xy, slant, width=4, outline="black", fill=None):
            x1, y1, x2, y2 = xy

            # Define the polygon points
            points = [
                (x1 + slant, y1),  # Top-left after slant
                (x2, y1),  # Top-right
                (x2, y2),  # Bottom-right
                (x1 + slant, y2),  # Bottom-left after slant
                (x1, y2 - slant),  # Bottom of left slant
                (x1, y1 + slant),  # Top of left slant
            ]

            # Draw the filled polygon with outline
            draw.polygon(points, fill=fill, outline=outline, width=width)

        # Draw the outer rectangle with rounded corners
        corner_radius = 20
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=corner_radius, outline="black", width=4, fill="grey"
        )

        # Draw the inner rectangle with left slanted corners
        margin = 20
        slant = 40
        draw_left_slanted_rectangle(
            draw, [margin, margin, width - margin, height - margin], slant, width=4, outline="black", fill="lightgrey"
        )

        # Function to draw a circle
        def draw_circle(x, y, diameter):
            radius = diameter / 2
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline="black", width=4, fill="white")

        # Draw the wells
        for row in range(rows):
            for col in range(cols):
                x = mm_to_px(a1_x_mm + col * well_spacing_mm)
                y = mm_to_px(a1_y_mm + row * well_spacing_mm)
                draw_circle(x, y, mm_to_px(well_size_mm))

        # Load a default font
        font_size = 30
        font = ImageFont.load_default().font_variant(size=font_size)

        # Add column labels
        for col in range(cols):
            label = str(col + 1)
            x = mm_to_px(a1_x_mm + col * well_spacing_mm)
            y = mm_to_px((a1_y_mm - well_size_mm / 2) / 2)
            bbox = font.getbbox(label)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.text((x - text_width / 2, y), label, fill="black", font=font)

        # Add row labels
        for row in range(rows):
            label = chr(65 + row) if row < 26 else chr(65 + row // 26 - 1) + chr(65 + row % 26)
            x = mm_to_px((a1_x_mm - well_size_mm / 2) / 2)
            y = mm_to_px(a1_y_mm + row * well_spacing_mm)
            bbox = font.getbbox(label)
            text_height = bbox[3] - bbox[1]
            text_width = bbox[2] - bbox[0]
            draw.text((x + 20 - text_width / 2, y - text_height + 1), label, fill="black", font=font)

        image_path = os.path.join("images", f'{name.replace(" ", "_")}.png')
        image.save(image_path)
        print(f"Wellplate image saved as {image_path}")
        return image_path

    @staticmethod
    def calculate_circle(points):
        # Convert points to numpy array
        points = np.array(points)

        # Calculate the center and radius of the circle
        A = np.array([points[1] - points[0], points[2] - points[0]])
        b = np.sum(A * (points[1:3] + points[0]) / 2, axis=1)
        center = np.linalg.solve(A, b)

        # Calculate the radius
        radius = np.mean(np.linalg.norm(points - center, axis=1))

        return center, radius

    def closeEvent(self, event):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().closeEvent(event)

    def accept(self):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().accept()

    def reject(self):
        # This method is called when the dialog is closed without accepting
        if not self.was_live:
            self.liveController.stop_live()
        sample = self.navigationViewer.sample

        # Convert sample string to format int
        if "glass slide" in sample:
            sample_format = "glass slide"
        else:
            try:
                sample_format = int(sample.split()[0])
            except (ValueError, IndexError):
                print(f"Unable to parse sample format from '{sample}'. Defaulting to 0.")
                sample_format = "glass slide"

        # Set dropdown to the current sample format
        index = self.wellplateFormatWidget.comboBox.findData(sample_format)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

        # Update wellplate settings
        self.wellplateFormatWidget.setWellplateSettings(sample_format)

        super().reject()


class CalibrationLiveViewer(QWidget):

    signal_calibration_viewer_click = Signal(int, int, int, int)
    signal_mouse_moved = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.initial_zoom_set = False
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = pg.GraphicsLayoutWidget()
        self.viewbox = self.view.addViewBox()
        self.viewbox.setAspectLocked(True)
        self.viewbox.invertY(True)

        self.viewbox.setMouseEnabled(x=False, y=False)  # Disable panning
        self.viewbox.setMenuEnabled(False)

        # Set appropriate panning limits based on the acquisition image or plate size
        xmax = int(CAMERA_CONFIG.CROP_WIDTH_UNBINNED)
        ymax = int(CAMERA_CONFIG.CROP_HEIGHT_UNBINNED)
        self.viewbox.setLimits(xMin=0, xMax=xmax, yMin=0, yMax=ymax)

        self.img_item = pg.ImageItem()
        self.viewbox.addItem(self.img_item)

        # Add fixed crosshair
        pen = QPen(QColor(255, 0, 0))  # Red color
        pen.setWidth(4)

        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.viewbox.addItem(self.crosshair_h)
        self.viewbox.addItem(self.crosshair_v)

        layout.addWidget(self.view)

        # Connect double-click event
        self.view.scene().sigMouseClicked.connect(self.onMouseClicked)

        # Set fixed size for the viewer
        self.setFixedSize(500, 500)

    def setCrosshairPosition(self):
        center = self.viewbox.viewRect().center()
        self.crosshair_h.setPos(center.y())
        self.crosshair_v.setPos(center.x())

    def display_image(self, image):
        # Step 1: Update the image
        self.img_item.setImage(image)

        # Step 2: Get the image dimensions
        image_width = image.shape[1]
        image_height = image.shape[0]

        # Step 3: Calculate the center of the image
        image_center_x = image_width / 2
        image_center_y = image_height / 2

        # Step 4: Calculate the current view range
        current_view_range = self.viewbox.viewRect()

        # Step 5: If it's the first image or initial zoom hasn't been set, center the image
        if not self.initial_zoom_set:
            self.viewbox.setRange(xRange=(0, image_width), yRange=(0, image_height), padding=0)
            self.initial_zoom_set = True  # Mark initial zoom as set

        # Step 6: Always center the view around the image center (for seamless transitions)
        else:
            self.viewbox.setRange(
                xRange=(
                    image_center_x - current_view_range.width() / 2,
                    image_center_x + current_view_range.width() / 2,
                ),
                yRange=(
                    image_center_y - current_view_range.height() / 2,
                    image_center_y + current_view_range.height() / 2,
                ),
                padding=0,
            )

        # Step 7: Ensure the crosshair is updated
        self.setCrosshairPosition()

    # def mouseMoveEvent(self, event):
    #     self.signal_mouse_moved.emit(event.x(), event.y())

    def onMouseClicked(self, event):
        # Map the scene position to view position
        if event.double():  # double click to move
            pos = event.pos()
            scene_pos = self.viewbox.mapSceneToView(pos)

            # Get the x, y coordinates
            x, y = int(scene_pos.x()), int(scene_pos.y())
            # Ensure the coordinates are within the image boundaries
            image_shape = self.img_item.image.shape
            if 0 <= x < image_shape[1] and 0 <= y < image_shape[0]:
                # Adjust the coordinates to be relative to the center of the image
                x_centered = x - image_shape[1] // 2
                y_centered = y - image_shape[0] // 2
                # Emit the signal with the clicked coordinates and image size
                self.signal_calibration_viewer_click.emit(x_centered, y_centered, image_shape[1], image_shape[0])
            else:
                print("click was outside the image bounds.")
        else:
            print("single click only detected")

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            scale_factor = 0.9
        else:
            scale_factor = 1.1

        # Get the center of the viewbox
        center = self.viewbox.viewRect().center()

        # Scale the view
        self.viewbox.scaleBy((scale_factor, scale_factor), center)

        # Update crosshair position after scaling
        self.setCrosshairPosition()

        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setCrosshairPosition()


class Well1536SelectionWidget(QWidget):

    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.format = "1536 well plate"
        self.selected_cells = {}  # Dictionary to keep track of selected cells and their colors
        self.current_cell = None  # To track the current (green) cell
        self.rows = 32
        self.columns = 48
        self.spacing_mm = 2.25
        self.number_of_skip = 0
        self.well_size_mm = 1.5
        self.a1_x_mm = 11.0  # measured stage position - to update
        self.a1_y_mm = 7.86  # measured stage position - to update
        self.a1_x_pixel = 144  # coordinate on the png - to update
        self.a1_y_pixel = 108  # coordinate on the png - to update
        self.initUI()

    def initUI(self):
        self.setWindowTitle("1536 Well Plate")
        self.setGeometry(100, 100, 750, 400)  # Increased width to accommodate controls

        self.a = 11
        image_width = 48 * self.a
        image_height = 32 * self.a

        self.image = QPixmap(image_width, image_height)
        self.image.fill(QColor("white"))
        self.label = QLabel()
        self.label.setPixmap(self.image)
        self.label.setFixedSize(image_width, image_height)
        self.label.setAlignment(Qt.AlignCenter)

        self.cell_input = QLineEdit(self)
        self.cell_input.setPlaceholderText("e.g. AE12 or B4")
        go_button = QPushButton("Go to well", self)
        go_button.clicked.connect(self.go_to_cell)
        self.selection_input = QLineEdit(self)
        self.selection_input.setPlaceholderText("e.g. A1:E48, X1, AC24, Z2:AF6, ...")
        self.selection_input.editingFinished.connect(self.select_cells)

        # Create navigation buttons
        up_button = QPushButton("↑", self)
        left_button = QPushButton("←", self)
        right_button = QPushButton("→", self)
        down_button = QPushButton("↓", self)
        add_button = QPushButton("Select", self)

        # Connect navigation buttons to their respective functions
        up_button.clicked.connect(self.move_up)
        left_button.clicked.connect(self.move_left)
        right_button.clicked.connect(self.move_right)
        down_button.clicked.connect(self.move_down)
        add_button.clicked.connect(self.add_current_well)

        layout = QHBoxLayout()
        layout.addWidget(self.label)

        layout_controls = QVBoxLayout()
        layout_controls.addStretch(2)

        # Add navigation buttons in a + sign layout
        layout_move = QGridLayout()
        layout_move.addWidget(up_button, 0, 2)
        layout_move.addWidget(left_button, 1, 1)
        layout_move.addWidget(add_button, 1, 2)
        layout_move.addWidget(right_button, 1, 3)
        layout_move.addWidget(down_button, 2, 2)
        layout_move.setColumnStretch(0, 1)
        layout_move.setColumnStretch(4, 1)
        layout_controls.addLayout(layout_move)

        layout_controls.addStretch(1)

        layout_input = QGridLayout()
        layout_input.addWidget(QLabel("Well Navigation"), 0, 0)
        layout_input.addWidget(self.cell_input, 0, 1)
        layout_input.addWidget(go_button, 0, 2)
        layout_input.addWidget(QLabel("Well Selection"), 1, 0)
        layout_input.addWidget(self.selection_input, 1, 1, 1, 2)
        layout_controls.addLayout(layout_input)

        control_widget = QWidget()
        control_widget.setLayout(layout_controls)
        control_widget.setFixedHeight(image_height)  # Set the height of controls to match the image

        layout.addWidget(control_widget)
        self.setLayout(layout)

    def move_up(self):
        if self.current_cell:
            row, col = self.current_cell
            if row > 0:
                self.current_cell = (row - 1, col)
                self.update_current_cell()

    def move_left(self):
        if self.current_cell:
            row, col = self.current_cell
            if col > 0:
                self.current_cell = (row, col - 1)
                self.update_current_cell()

    def move_right(self):
        if self.current_cell:
            row, col = self.current_cell
            if col < self.columns - 1:
                self.current_cell = (row, col + 1)
                self.update_current_cell()

    def move_down(self):
        if self.current_cell:
            row, col = self.current_cell
            if row < self.rows - 1:
                self.current_cell = (row + 1, col)
                self.update_current_cell()

    def add_current_well(self):
        if self.current_cell:
            row, col = self.current_cell
            cell_name = f"{chr(65 + row)}{col + 1}"

            if (row, col) in self.selected_cells:
                # If the well is already selected, remove it
                del self.selected_cells[(row, col)]
                self.remove_well_from_selection_input(cell_name)
                print(f"Removed well {cell_name}")
            else:
                # If the well is not selected, add it
                self.selected_cells[(row, col)] = "#1f77b4"  # Add to selected cells with blue color
                self.add_well_to_selection_input(cell_name)
                print(f"Added well {cell_name}")

            self.redraw_wells()
            self.signal_wellSelected.emit(bool(self.selected_cells))

    def add_well_to_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        if current_selection:
            self.selection_input.setText(f"{current_selection}, {cell_name}")
        else:
            self.selection_input.setText(cell_name)

    def remove_well_from_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        cells = [cell.strip() for cell in current_selection.split(",")]
        if cell_name in cells:
            cells.remove(cell_name)
            self.selection_input.setText(", ".join(cells))

    def update_current_cell(self):
        self.redraw_wells()
        row, col = self.current_cell
        if row < 26:
            row_label = chr(65 + row)
        else:
            row_label = chr(64 + (row // 26)) + chr(65 + (row % 26))
        # Update cell_input with the correct label (e.g., A1, B2, AA1, etc.)
        self.cell_input.setText(f"{row_label}{col + 1}")

        x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
        y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
        self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def redraw_wells(self):
        self.image.fill(QColor("white"))  # Clear the pixmap first
        painter = QPainter(self.image)
        painter.setPen(QColor("white"))
        # Draw selected cells in red
        for (row, col), color in self.selected_cells.items():
            painter.setBrush(QColor(color))
            painter.drawRect(col * self.a, row * self.a, self.a, self.a)
        # Draw current cell in green
        if self.current_cell:
            painter.setBrush(Qt.NoBrush)  # No fill
            painter.setPen(QPen(QColor("red"), 2))  # Red outline, 2 pixels wide
            row, col = self.current_cell
            painter.drawRect(col * self.a + 2, row * self.a + 2, self.a - 3, self.a - 3)
        painter.end()
        self.label.setPixmap(self.image)

    def go_to_cell(self):
        cell_desc = self.cell_input.text().strip()
        match = re.match(r"([A-Za-z]+)(\d+)", cell_desc)
        if match:
            row_part, col_part = match.groups()
            row_index = self.row_to_index(row_part)
            col_index = int(col_part) - 1
            self.current_cell = (row_index, col_index)  # Update the current cell
            self.redraw_wells()  # Redraw with the new current cell
            x_mm = col_index * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row_index * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def select_cells(self):
        # first clear selection
        self.selected_cells = {}

        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        cell_descriptions = self.selection_input.text().split(",")
        for desc in cell_descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = self.row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = self.row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(min(start_row_index, end_row_index), max(start_row_index, end_row_index) + 1):
                        for col in range(min(start_col_index, end_col_index), max(start_col_index, end_col_index) + 1):
                            self.selected_cells[(row, col)] = "#1f77b4"
                else:  # It's a single cell
                    self.selected_cells[(start_row_index, start_col_index)] = "#1f77b4"
        self.redraw_wells()
        if self.selected_cells:
            self.signal_wellSelected.emit(True)

    def row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def onSelectionChanged(self):
        self.get_selected_cells()

    def onWellplateChanged(self):
        """A placeholder to match the method in WellSelectionWidget"""
        pass

    def get_selected_cells(self):
        list_of_selected_cells = list(self.selected_cells.keys())
        return list_of_selected_cells


class SampleSettingsWidget(QFrame):
    def __init__(self, ObjectivesWidget, WellplateFormatWidget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objectivesWidget = ObjectivesWidget
        self.wellplateFormatWidget = WellplateFormatWidget

        # Set up the layout
        top_row_layout = QGridLayout()
        top_row_layout.setSpacing(2)
        top_row_layout.setContentsMargins(0, 2, 0, 2)
        top_row_layout.addWidget(self.objectivesWidget, 0, 0)
        top_row_layout.addWidget(self.wellplateFormatWidget, 0, 1)
        self.setLayout(top_row_layout)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Connect signals for saving settings
        self.objectivesWidget.signal_objective_changed.connect(self.save_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(lambda *args: self.save_settings())

    def save_settings(self):
        """Save current objective and wellplate format to cache"""
        os.makedirs("cache", exist_ok=True)
        data = {
            "objective": self.objectivesWidget.dropdown.currentText(),
            "wellplate_format": self.wellplateFormatWidget.wellplate_format,
        }

        with open("cache/objective_and_sample_format.txt", "w") as f:
            json.dump(data, f)


from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
from scipy.interpolate import griddata


