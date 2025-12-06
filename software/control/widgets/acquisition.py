# Acquisition widgets for multi-point imaging
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


class FlexibleMultiPointWidget(QFrame):

    signal_acquisition_started = Signal(bool)  # true = started, false = finished
    signal_acquisition_channels = Signal(list)  # list channels
    signal_acquisition_shape = Signal(int, float)  # Nz, dz

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        focusMapWidget,
        stage_service: Optional["StageService"] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.acquisition_start_time = None
        self.last_used_locations = None
        self.last_used_location_ids = None
        self.stage = stage
        self._stage_service = stage_service
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        self.base_path_is_set = False
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.use_overlap = USE_OVERLAP_FOR_FLEXIBLE
        self.add_components()
        self.setup_layout()
        self.setup_connections()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.is_current_acquisition_widget = False
        self.acquisition_in_place = False

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()
        self.lineEdit_experimentID.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lineEdit_experimentID.setFixedWidth(96)

        self.dropdown_location_list = QComboBox()
        self.dropdown_location_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        self.btn_previous = QPushButton("Previous")
        self.btn_next = QPushButton("Next")
        self.btn_clear = QPushButton("Clear")

        self.btn_load_last_executed = QPushButton("Prev Used Locations")

        self.btn_export_locations = QPushButton("Export Location List")
        self.btn_import_locations = QPushButton("Import Location List")
        self.btn_show_table_location_list = QPushButton("Edit")  # Open / Edit

        # editable points table
        self.table_location_list = QTableWidget()
        self.table_location_list.setColumnCount(4)
        header_labels = ["x", "y", "z", "ID"]
        self.table_location_list.setHorizontalHeaderLabels(header_labels)
        self.btn_update_z = QPushButton("Update Z")

        self.entry_deltaX = QDoubleSpinBox()
        self.entry_deltaX.setMinimum(0)
        self.entry_deltaX.setMaximum(5)
        self.entry_deltaX.setSingleStep(0.1)
        self.entry_deltaX.setValue(Acquisition.DX)
        self.entry_deltaX.setDecimals(3)
        self.entry_deltaX.setSuffix(" mm")
        self.entry_deltaX.setKeyboardTracking(False)

        self.entry_NX = QSpinBox()
        self.entry_NX.setMinimum(1)
        self.entry_NX.setMaximum(1000)
        self.entry_NX.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NX.setMaximum(50)
        self.entry_NX.setSingleStep(1)
        self.entry_NX.setValue(1)
        self.entry_NX.setKeyboardTracking(False)
        # self.entry_NX.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_deltaY = QDoubleSpinBox()
        self.entry_deltaY.setMinimum(0)
        self.entry_deltaY.setMaximum(5)
        self.entry_deltaY.setSingleStep(0.1)
        self.entry_deltaY.setValue(Acquisition.DX)
        self.entry_deltaY.setDecimals(3)
        self.entry_deltaY.setSuffix(" mm")
        self.entry_deltaY.setKeyboardTracking(False)

        self.entry_NY = QSpinBox()
        self.entry_NY.setMinimum(1)
        self.entry_NY.setMaximum(1000)
        self.entry_NY.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NY.setMaximum(50)
        self.entry_NY.setSingleStep(1)
        self.entry_NY.setValue(1)
        self.entry_NY.setKeyboardTracking(False)
        # self.entry_NY.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setKeyboardTracking(False)
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setDecimals(1)
        self.entry_overlap.setSuffix(" %")
        self.entry_overlap.setValue(10)
        self.entry_overlap.setKeyboardTracking(False)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setKeyboardTracking(False)
        # self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setKeyboardTracking(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setKeyboardTracking(False)
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(12 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setKeyboardTracking(False)
        # self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(10000)  # @@@ to be changed
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)
        self.entry_Nt.setKeyboardTracking(False)

        # Calculate a consistent width
        max_delta_width = max(
            self.entry_deltaZ.sizeHint().width(),
            self.entry_dt.sizeHint().width(),
            self.entry_deltaX.sizeHint().width(),
            self.entry_deltaY.sizeHint().width(),
        )
        self.entry_deltaZ.setFixedWidth(max_delta_width)
        self.entry_dt.setFixedWidth(max_delta_width)
        self.entry_deltaX.setFixedWidth(max_delta_width)
        self.entry_deltaY.setFixedWidth(max_delta_width)

        max_num_width = max(
            self.entry_NX.sizeHint().width(),
            self.entry_NY.sizeHint().width(),
            self.entry_NZ.sizeHint().width(),
            self.entry_Nt.sizeHint().width(),
        )
        self.entry_NX.setFixedWidth(max_num_width)
        self.entry_NY.setFixedWidth(max_num_width)
        self.entry_NZ.setFixedWidth(max_num_width)
        self.entry_Nt.setFixedWidth(max_num_width)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.checkbox_set_z_range = QCheckBox("Set Z-range")
        self.checkbox_set_z_range.toggled.connect(self.toggle_z_range_controls)

        # Add new components for Z-range
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setKeyboardTracking(False)
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_minZ_button = QPushButton("Set")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setKeyboardTracking(False)
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_maxZ_button = QPushButton("Set")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # layout
        self.grid_line0 = QHBoxLayout()
        self.grid_line0.addWidget(QLabel("Saving Path"))
        self.grid_line0.addWidget(self.lineEdit_savingDir)
        self.grid_line0.addWidget(self.btn_setSavingDir)
        self.grid_line0.addWidget(QLabel("ID"))
        self.grid_line0.addWidget(self.lineEdit_experimentID)

        self.grid_location_list_line1 = QGridLayout()
        temp3 = QHBoxLayout()
        temp3.addWidget(QLabel("Location List"))
        temp3.addWidget(self.dropdown_location_list)
        self.grid_location_list_line1.addLayout(temp3, 0, 0, 1, 6)  # Span across all columns except the last
        self.grid_location_list_line1.addWidget(self.btn_update_z, 0, 6, 1, 2)  # Align with other buttons

        self.grid_location_list_line2 = QGridLayout()
        # Make all buttons span 2 columns for consistent width
        self.grid_location_list_line2.addWidget(self.btn_add, 1, 0, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_remove, 1, 2, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_next, 1, 4, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_clear, 1, 6, 1, 2)

        self.grid_location_list_line3 = QGridLayout()
        self.grid_location_list_line3.addWidget(self.btn_import_locations, 2, 0, 1, 3)
        self.grid_location_list_line3.addWidget(self.btn_export_locations, 2, 3, 1, 3)
        self.grid_location_list_line3.addWidget(self.btn_show_table_location_list, 2, 6, 1, 2)

        # Create spacer items
        EDGE_SPACING = 4  # Adjust this value as needed
        edge_spacer = QSpacerItem(EDGE_SPACING, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)

        # Create first row layouts
        if self.use_overlap:
            xy_half = QHBoxLayout()
            xy_half.addWidget(QLabel("Nx"))
            xy_half.addWidget(self.entry_NX)
            xy_half.addStretch(1)
            xy_half.addWidget(QLabel("Ny"))
            xy_half.addWidget(self.entry_NY)
            xy_half.addSpacerItem(edge_spacer)

            overlap_half = QHBoxLayout()
            overlap_half.addSpacerItem(edge_spacer)
            overlap_half.addWidget(QLabel("FOV Overlap"), alignment=Qt.AlignRight)
            overlap_half.addWidget(self.entry_overlap)
        else:
            # Create alternate first row layouts (dx, dy) instead of (overlap %)
            x_half = QHBoxLayout()
            x_half.addWidget(QLabel("dx"))
            x_half.addWidget(self.entry_deltaX)
            x_half.addStretch(1)
            x_half.addWidget(QLabel("Nx"))
            x_half.addWidget(self.entry_NX)
            x_half.addSpacerItem(edge_spacer)

            y_half = QHBoxLayout()
            y_half.addSpacerItem(edge_spacer)
            y_half.addWidget(QLabel("dy"))
            y_half.addWidget(self.entry_deltaY)
            y_half.addStretch(1)
            y_half.addWidget(QLabel("Ny"))
            y_half.addWidget(self.entry_NY)

        # Create second row layouts
        dz_half = QHBoxLayout()
        dz_half.addWidget(QLabel("dz"))
        dz_half.addWidget(self.entry_deltaZ)
        dz_half.addStretch(1)
        dz_half.addWidget(QLabel("Nz"))
        dz_half.addWidget(self.entry_NZ)
        dz_half.addSpacerItem(edge_spacer)

        dt_half = QHBoxLayout()
        dt_half.addSpacerItem(edge_spacer)
        dt_half.addWidget(QLabel("dt"))
        dt_half.addWidget(self.entry_dt)
        dt_half.addStretch(1)
        dt_half.addWidget(QLabel("Nt"))
        dt_half.addWidget(self.entry_Nt)

        self.grid_acquisition = QGridLayout()
        # Add the layouts to grid_line1
        if self.use_overlap:
            self.grid_acquisition.addLayout(xy_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(overlap_half, 3, 4, 1, 4)
        else:
            self.grid_acquisition.addLayout(x_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(y_half, 3, 4, 1, 4)
        self.grid_acquisition.addLayout(dz_half, 4, 0, 1, 4)
        self.grid_acquisition.addLayout(dt_half, 4, 4, 1, 4)

        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.set_minZ_button)
        self.z_min_layout.addWidget(QLabel("Z-min"), Qt.AlignRight)
        self.z_min_layout.addWidget(self.entry_minZ)
        self.z_min_layout.addSpacerItem(edge_spacer)

        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addSpacerItem(edge_spacer)
        self.z_max_layout.addWidget(self.set_maxZ_button)
        self.z_max_layout.addWidget(QLabel("Z-max"), Qt.AlignRight)
        self.z_max_layout.addWidget(self.entry_maxZ)

        self.grid_acquisition.addLayout(self.z_min_layout, 5, 0, 1, 4)  # hide this in toggle
        self.grid_acquisition.addLayout(self.z_max_layout, 5, 4, 1, 4)  # hide this in toggle

        grid_af = QVBoxLayout()
        grid_af.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            grid_af.addWidget(self.checkbox_withReflectionAutofocus)
        # grid_af.addWidget(self.checkbox_genAFMap)  # we are not using auto-focus map for now
        grid_af.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            grid_af.addWidget(self.checkbox_usePiezo)
        grid_af.addWidget(self.checkbox_set_z_range)

        grid_config = QHBoxLayout()
        grid_config.addWidget(self.list_configurations)
        grid_config.addSpacerItem(edge_spacer)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        grid_acquisition = QHBoxLayout()
        grid_acquisition.addSpacerItem(edge_spacer)
        grid_acquisition.addLayout(grid_af)
        grid_acquisition.addLayout(button_layout)

        self.grid_acquisition.addLayout(grid_config, 6, 0, 3, 4)
        self.grid_acquisition.addLayout(grid_acquisition, 6, 4, 3, 4)

        # Columns 0-3: Combined stretch factor = 4
        # Columns 4-7: Combined stretch factor = 4
        for i in range(4):
            self.grid_location_list_line1.setColumnStretch(i, 1)
            self.grid_location_list_line2.setColumnStretch(i, 1)
            self.grid_location_list_line3.setColumnStretch(i, 1)
            self.grid_acquisition.setColumnStretch(i, 1)

            self.grid_location_list_line1.setColumnStretch(i + 4, 1)
            self.grid_location_list_line2.setColumnStretch(i + 4, 1)
            self.grid_location_list_line3.setColumnStretch(i + 4, 1)
            self.grid_acquisition.setColumnStretch(i + 4, 1)

        self.grid_location_list_line1.setRowStretch(0, 0)  # Location list row
        self.grid_location_list_line2.setRowStretch(1, 0)  # Button row
        self.grid_location_list_line3.setRowStretch(2, 0)  # Import/Export buttons
        self.grid_acquisition.setRowStretch(0, 0)  # Nx/Ny and overlap row
        self.grid_acquisition.setRowStretch(1, 0)  # dz/Nz and dt/Nt row
        self.grid_acquisition.setRowStretch(2, 0)  # Z-range row
        self.grid_acquisition.setRowStretch(3, 1)  # Configuration/AF row - allow this to stretch
        self.grid_acquisition.setRowStretch(4, 0)  # Last row

        # Row : Progress Bar
        self.row_progress_layout = QHBoxLayout()
        self.row_progress_layout.addWidget(self.progress_label)
        self.row_progress_layout.addWidget(self.progress_bar)
        self.row_progress_layout.addWidget(self.eta_label)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

    def setup_connections(self):
        # connections
        if self.use_overlap:
            self.entry_overlap.valueChanged.connect(self.update_fov_positions)
        else:
            self.entry_deltaX.valueChanged.connect(self.update_fov_positions)
            self.entry_deltaY.valueChanged.connect(self.update_fov_positions)
        self.entry_NX.valueChanged.connect(self.update_fov_positions)
        self.entry_NY.valueChanged.connect(self.update_fov_positions)
        # self.btn_add.clicked.connect(self.update_fov_positions) #TODO: this is handled in the add_location method - to be removed
        # self.btn_remove.clicked.connect(self.update_fov_positions) #TODO: this is handled in the remove_location method - to be removed
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_NX.valueChanged.connect(self.multipointController.set_NX)
        self.entry_NY.valueChanged.connect(self.multipointController.set_NY)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        # self.combobox_z_stack.currentIndexChanged.connect(self.signal_z_stacking.emit)

        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

        self.btn_add.clicked.connect(self.add_location)
        self.btn_remove.clicked.connect(self.remove_location)
        self.btn_previous.clicked.connect(self.previous)
        self.btn_next.clicked.connect(self.next)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_load_last_executed.clicked.connect(self.load_last_used_locations)
        self.btn_export_locations.clicked.connect(self.export_location_list)
        self.btn_import_locations.clicked.connect(self.import_location_list)

        self.table_location_list.cellClicked.connect(self.cell_was_clicked)
        self.table_location_list.cellChanged.connect(self.cell_was_changed)
        self.btn_show_table_location_list.clicked.connect(self.table_location_list.show)
        self.btn_update_z.clicked.connect(self.update_z)
        self.dropdown_location_list.currentIndexChanged.connect(self.go_to)

        self.shortcut = QShortcut(QKeySequence(";"), self)
        self.shortcut.activated.connect(self.btn_add.click)

        self.toggle_z_range_controls(False)
        self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())

    def setup_layout(self):
        self.grid = QVBoxLayout()
        self.grid.addLayout(self.grid_line0)
        self.grid.addLayout(self.grid_location_list_line1)
        self.grid.addLayout(self.grid_location_list_line2)
        self.grid.addLayout(self.grid_location_list_line3)
        self.grid.addLayout(self.grid_acquisition)
        self.grid.addLayout(self.row_progress_layout)
        self.setLayout(self.grid)

    def toggle_z_range_controls(self, state):
        is_visible = bool(state)

        # Hide/show widgets in z_min_layout
        for i in range(self.z_min_layout.count()):
            widget = self.z_min_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)
            widget = self.z_max_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)

        # Disable reflection autofocus checkbox if Z-range is visible
        self.checkbox_withReflectionAutofocus.setEnabled(not is_visible)
        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)
        current_z = self.stage.get_pos().z_mm * 1000
        self.entry_minZ.setValue(current_z)
        if is_visible:
            self._reset_reflection_af_reference()
        self.entry_maxZ.setValue(current_z)

        if not is_visible:
            try:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
            except:
                pass
            # When Z-range is not specified, set Z-min and Z-max to current Z position
            current_z = self.stage.get_pos().z_mm * 1000
            self.entry_minZ.setValue(current_z)
            self.entry_maxZ.setValue(current_z)
        else:
            self.entry_minZ.valueChanged.connect(self.update_z_max)
            self.entry_maxZ.valueChanged.connect(self.update_z_min)
            self.entry_minZ.valueChanged.connect(self.update_Nz)
            self.entry_maxZ.valueChanged.connect(self.update_Nz)
            self.entry_deltaZ.valueChanged.connect(self.update_Nz)

        # Update the layout
        self.grid.update()
        self.updateGeometry()
        self.update()

    def init_z(self, z_pos_mm=None):
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        print("init z-level flexible:", self.entry_minZ.value())

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)
        self._reset_reflection_af_reference()

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)
            self._reset_reflection_af_reference()

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def _reset_reflection_af_reference(self):
        if (
            self.checkbox_withReflectionAutofocus.isChecked()
            and not self.multipointController.laserAutoFocusController.set_reference()
        ):
            error_dialog("Failed to set reference for reflection autofocus. Is the laser autofocus initialized?")

    def update_z(self):
        z_mm = self.stage.get_pos().z_mm
        index = self.dropdown_location_list.currentIndex()
        self.location_list[index, 2] = z_mm
        self.scanCoordinates.region_centers[self.location_ids[index]][2] = z_mm
        self.scanCoordinates.region_fov_coordinates[self.location_ids[index]] = [
            (coord[0], coord[1], z_mm)
            for coord in self.scanCoordinates.region_fov_coordinates[self.location_ids[index]]
        ]
        location_str = f"x:{round(self.location_list[index,0],3)} mm  y:{round(self.location_list[index,1],3)} mm  z:{round(z_mm * 1000.0,3)} μm"
        self.dropdown_location_list.setItemText(index, location_str)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def update_region_progress(self, current_fov, num_fovs):
        self._log.debug(f"Updating region progress for {current_fov=}, {num_fovs=}")
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self._log.debug(
            f"updating acquisition progress for {current_region=}, {num_regions=}, {current_time_point=}..."
        )
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")

        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def update_fov_positions(self):
        if not self.isVisible():
            return

        if self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

        for i, (x, y, z) in enumerate(self.location_list):
            region_id = self.location_ids[i]
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def toggle_acquisition(self, pressed):
        self._log.debug(f"FlexibleMultiPointWidget.toggle_acquisition, {pressed=}")
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please choose base saving directory first")
            return
        if not self.list_configurations.selectedItems():  # no channel selected
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please select at least one imaging channel first")
            return
        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            # add the current location to the location list if the list is empty
            if len(self.location_list) == 0:
                self.add_location()
                self.acquisition_in_place = True

            if self.checkbox_set_z_range.isChecked():
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000
                maxZ = self.entry_maxZ.value() / 1000
                self.multipointController.set_z_range(minZ, maxZ)
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz / 1000 * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
            else:
                self.multipointController.set_focus_map(None)

            # Set acquisition parameters
            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_base_path(self.lineEdit_savingDir.text())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if not check_space_available_with_error_dialog(self.multipointController, self._log):
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                self.btn_startAcquisition.setChecked(False)
                return

            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.is_current_acquisition_widget = True  # keep track of what widget started the acquisition
            self.btn_startAcquisition.setText("Stop\n Acquisition ")
            self.setEnabled_all(False)

            # emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start coordinate-based acquisition
            self.multipointController.run_acquisition()
        else:
            # This must eventually propagate through and call out acquisition_finished.
            self.multipointController.request_abort_aquisition()

    def load_last_used_locations(self):
        if self.last_used_locations is None or len(self.last_used_locations) == 0:
            return
        self.clear_only_location_list()

        for row, row_ind in zip(self.last_used_locations, self.last_used_location_ids):
            x = row[0]
            y = row[1]
            z = row[2]
            name = row_ind[0]
            if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                location_str = (
                    "x:" + str(round(x, 3)) + "mm  y:" + str(round(y, 3)) + "mm  z:" + str(round(1000 * z, 1)) + "μm"
                )
                self.dropdown_location_list.addItem(location_str)
                self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                self.location_ids = np.append(self.location_ids, name)
                self.table_location_list.insertRow(self.table_location_list.rowCount())
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(z * 1000, 1)))
                )
                self.table_location_list.setItem(self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(name))
                index = self.dropdown_location_list.count() - 1
                self.dropdown_location_list.setCurrentIndex(index)
                print(self.location_list)
            else:
                print("Duplicate values not added based on x and y.")
                # to-do: update z coordinate

    def add_location(self):
        # Get raw positions without rounding
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm
        z = pos.z_mm
        region_id = f"R{len(self.location_ids)}"

        # Check for duplicates using rounded values for comparison
        if not np.any(np.all(self.location_list[:, :2] == [round(x, 3), round(y, 3)], axis=1)):
            # Block signals to prevent triggering cell_was_changed
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Store actual values in location_list
            self.location_list = np.vstack((self.location_list, [[x, y, z]]))
            self.location_ids = np.append(self.location_ids, region_id)

            # Update both UI elements at the same time
            location_str = f"x:{round(x,3)} mm  y:{round(y,3)} mm  z:{round(z*1000,1)} μm"
            self.dropdown_location_list.addItem(location_str)
            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(row, 2, QTableWidgetItem(str(round(z * 1000, 1))))
            self.table_location_list.setItem(row, 3, QTableWidgetItem(region_id))

            # Store actual values in region coordinates
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

            # Set the current index to the newly added location
            self.dropdown_location_list.setCurrentIndex(len(self.location_ids) - 1)
            self.table_location_list.selectRow(row)

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            print(f"Added Region: {region_id} - x={x}, y={y}, z={z}")
        else:
            print("Invalid Region: Duplicate Location")

    def remove_location(self):
        index = self.dropdown_location_list.currentIndex()
        if index >= 0:
            # Remove region ID and associated data
            region_id = self.location_ids[index]
            print(f"Removing region: {region_id}")

            # Block signals to prevent unintended UI updates
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Remove from data structures
            self.location_list = np.delete(self.location_list, index, axis=0)
            self.location_ids = np.delete(self.location_ids, index)

            # Remove from both UI elements
            self.dropdown_location_list.removeItem(index)
            self.table_location_list.removeRow(index)

            # Remove scanCoordinates dictionaries and remove region overlay
            self.scanCoordinates.region_centers.pop(region_id, None)
            self.navigationViewer.deregister_fovs_from_image(
                self.scanCoordinates.region_fov_coordinates.pop(region_id, [])
            )

            """
            # Reindex remaining regions and update UI
            for i in range(index, len(self.location_ids)):
                old_id = self.location_ids[i]
                new_id = f"R{i}"
                self.location_ids[i] = new_id

                # Update dictionaries
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(old_id, None)
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    old_id, []
                )

                # Update UI with new ID and coordinates
                x, y, z = self.location_list[i]
                location_str = f"x:{round(x, 3)} mm  y:{round(y, 3)} mm  z:{round(z * 1000, 1)} μm"
                self.dropdown_location_list.setItemText(i, location_str)
                self.table_location_list.setItem(i, 3, QTableWidgetItem(new_id))
            """

            # Clear overlay if no locations remain
            if len(self.location_list) == 0:
                self.navigationViewer.clear_overlay()

            print(f"Remaining location IDs: {self.location_ids}")
            for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                self.navigationViewer.register_fovs_to_image(fov_coords)

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)

    def next(self):
        index = self.dropdown_location_list.currentIndex()
        # max_index = self.dropdown_location_list.count() - 1
        # index = min(index + 1, max_index)
        num_regions = self.dropdown_location_list.count()
        if num_regions <= 0:
            self._log.error("Cannot move to next location, because there are no locations in the list")
            return

        index = (index + 1) % num_regions
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self._move_stage_to(x, y, z)

    def previous(self):
        index = self.dropdown_location_list.currentIndex()
        index = max(index - 1, 0)
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self._move_stage_to(x, y, z)

    def clear(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.scanCoordinates.clear_regions()
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)
        self.navigationViewer.clear_overlay()

        self._log.info("Cleared all locations and overlays.")

    def clear_only_location_list(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)

    def go_to(self, index):
        if index != -1:
            if index < len(self.location_list):  # to avoid giving errors when adding new points
                x = self.location_list[index, 0]
                y = self.location_list[index, 1]
                z = self.location_list[index, 2]
                self._move_stage_to(x, y, z)
                self.table_location_list.selectRow(index)

    def _move_stage_to(self, x: float, y: float, z: float):
        """Move stage to position."""
        self._stage_service.move_to(x_mm=x, y_mm=y, z_mm=z)

    def cell_was_clicked(self, row, column):
        self.dropdown_location_list.setCurrentIndex(row)

    def cell_was_changed(self, row, column):
        # Get region ID
        region_id = self.location_ids[row]

        # Clear all FOVs for this region
        if region_id in self.scanCoordinates.region_fov_coordinates.keys():
            self.navigationViewer.deregister_fovs_from_image(self.scanCoordinates.region_fov_coordinates[region_id])

        # Handle the changed value
        val_edit = self.table_location_list.item(row, column).text()

        if column < 2:  # X or Y coordinate changed
            self.location_list[row, column] = float(val_edit)
            x, y, z = self.location_list[row]

            # Update region coordinates and FOVs for new position
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

        elif column == 2:  # Z coordinate changed
            z = float(val_edit) / 1000
            self.location_list[row, 2] = z
            self.scanCoordinates.region_centers[region_id][2] = z
        else:  # ID changed
            new_id = val_edit
            self.location_ids[row] = new_id
            # Update dictionary keys
            if region_id in self.scanCoordinates.region_centers:
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(region_id)
            if region_id in self.scanCoordinates.region_fov_coordinates:
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    region_id
                )

        # Update UI
        location_str = f"x:{round(self.location_list[row,0],3)} mm  y:{round(self.location_list[row,1],3)} mm  z:{round(1000*self.location_list[row,2],3)} μm"
        self.dropdown_location_list.setItemText(row, location_str)
        self.go_to(row)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_A and event.modifiers() == Qt.ControlModifier:
            self.add_location()
        else:
            super().keyPressEvent(event)

    def update_location_z_level(self, index, z_mm):
        self.table_location_list.blockSignals(True)
        self.dropdown_location_list.blockSignals(True)

        self.location_list[index, 2] = z_mm
        location_str = (
            "x:"
            + str(round(self.location_list[index, 0], 3))
            + "mm  y:"
            + str(round(self.location_list[index, 1], 3))
            + "mm  z:"
            + str(round(1000 * z_mm, 1))
            + "μm"
        )
        self.dropdown_location_list.setItemText(index, location_str)
        if self.table_location_list.rowCount() > index:
            self.table_location_list.setItem(index, 2, QTableWidgetItem(str(round(1000 * z_mm, 1))))

        self.table_location_list.blockSignals(False)
        self.dropdown_location_list.blockSignals(False)

    def export_location_list(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.DataFrame(self.location_list, columns=["x (mm)", "y (mm)", "z (mm)"])
            location_list_df["ID"] = self.location_ids
            location_list_df["i"] = 0
            location_list_df["j"] = 0
            location_list_df["k"] = 0
            location_list_df.to_csv(file_path, index=False, header=True)

    def import_location_list(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.read_csv(file_path)
            location_list_df_relevant = None
            try:
                location_list_df_relevant = location_list_df[["x (mm)", "y (mm)", "z (mm)"]]
            except KeyError:
                self._log.error("Improperly formatted location list being imported")
                return
            if "ID" in location_list_df.columns:
                location_list_df_relevant["ID"] = location_list_df["ID"].astype(str)
            else:
                location_list_df_relevant["ID"] = "None"
            self.clear_only_location_list()

            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)
            for index, row in location_list_df_relevant.iterrows():
                x = row["x (mm)"]
                y = row["y (mm)"]
                z = row["z (mm)"]
                region_id = row["ID"]
                if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                    location_str = (
                        "x:"
                        + str(round(x, 3))
                        + "mm  y:"
                        + str(round(y, 3))
                        + "mm  z:"
                        + str(round(1000.0 * z, 3))
                        + "μm"
                    )
                    self.dropdown_location_list.addItem(location_str)
                    index = self.dropdown_location_list.count() - 1
                    self.dropdown_location_list.setCurrentIndex(index)
                    self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                    self.location_ids = np.append(self.location_ids, region_id)
                    self.table_location_list.insertRow(self.table_location_list.rowCount())
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(1000 * z, 1)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(region_id)
                    )
                    if self.use_overlap:
                        self.scanCoordinates.add_flexible_region(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            overlap_percent=self.entry_overlap.value(),
                        )
                    else:
                        self.scanCoordinates.add_flexible_region_with_step_size(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            self.entry_deltaX.value(),
                            self.entry_deltaY.value(),
                        )
                else:
                    self._log.warning("Duplicate values not added based on x and y.")
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            self._log.debug(self.location_list)

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)

        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def acquisition_is_finished(self):
        self._log.debug(
            f"In FlexibleMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )

        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        if not self.acquisition_in_place:
            self.last_used_locations = self.location_list.copy()
            self.last_used_location_ids = self.location_ids.copy()
        else:
            self.clear_only_location_list()
            self.acquisition_in_place = False

        self.signal_acquisition_started.emit(False)
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        self.setEnabled_all(True)
        self.is_current_acquisition_widget = False

    def setEnabled_all(self, enabled, exclude_btn_startAcquisition=True):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.entry_NX.setEnabled(enabled)
        self.entry_NY.setEnabled(enabled)
        self.entry_deltaZ.setEnabled(enabled)
        self.entry_NZ.setEnabled(enabled)
        self.entry_dt.setEnabled(enabled)
        self.entry_Nt.setEnabled(enabled)
        if not self.use_overlap:
            self.entry_deltaX.setEnabled(enabled)
            self.entry_deltaY.setEnabled(enabled)
        else:
            self.entry_overlap.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_genAFMap.setEnabled(enabled)
        self.checkbox_useFocusMap.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        self.checkbox_stitchOutput.setEnabled(enabled)
        self.checkbox_set_z_range.setEnabled(enabled)

        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)


class WellplateMultiPointWidget(QFrame):

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz
    signal_manual_shape_mode = Signal(bool)  # enable manual shape layer on mosaic display
    signal_toggle_live_scan_grid = Signal(bool)  # enable/disable live scan grid

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        liveController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        focusMapWidget=None,
        napariMosaicWidget=None,
        tab_widget: Optional[QTabWidget] = None,
        well_selection_widget: Optional[WellSelectionWidget] = None,
        stage_service: Optional["StageService"] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self._stage_service = stage_service
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.liveController = liveController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        if napariMosaicWidget is None:
            self.performance_mode = True
        else:
            self.napariMosaicWidget = napariMosaicWidget
            self.performance_mode = False
        self.tab_widget: Optional[QTabWidget] = tab_widget
        self.well_selection_widget: Optional[WellSelectionWidget] = well_selection_widget
        self.base_path_is_set = False
        self.well_selected = False
        self.num_regions = 0
        self.acquisition_start_time = None
        self.manual_shape = None
        self.eta_seconds = 0
        self.is_current_acquisition_widget = False

        self.shapes_mm = None

        # TODO (hl): these along with update_live_coordinates need to move out of this class
        self._last_update_time = 0
        self._last_x_mm = None
        self._last_y_mm = None

        # Add state tracking for coordinates
        self.has_loaded_coordinates = False

        # Cache for loaded coordinates dataframe (restored when switching back to Load Coordinates mode)
        self.cached_loaded_coordinates_df = None
        self.cached_loaded_file_path = None

        # Add state tracking for Z parameters
        self.stored_z_params = {"dz": None, "nz": None, "z_min": None, "z_max": None, "z_mode": "From Bottom"}

        # Add state tracking for Time parameters
        self.stored_time_params = {"dt": None, "nt": None}

        # Add state tracking for XY mode parameters
        self.stored_xy_params = {
            "Current Position": {"scan_size": None, "coverage": None, "scan_shape": None},
            "Select Wells": {"scan_size": None, "coverage": None, "scan_shape": None},
        }

        # Track previous XY mode for parameter storage
        self._previous_xy_mode = None

        # Track XY mode before unchecking, for restoration when re-checking
        self._xy_mode_before_uncheck = None

        # Track loading from cache
        self._loading_from_cache = False

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.set_default_scan_size()

    def add_components(self):
        self.entry_well_coverage = QDoubleSpinBox()
        self.entry_well_coverage.setKeyboardTracking(False)
        self.entry_well_coverage.setRange(1, 999.99)
        self.entry_well_coverage.setValue(100)
        self.entry_well_coverage.setSuffix("%")
        self.entry_well_coverage.setDecimals(0)
        btn_width = self.entry_well_coverage.sizeHint().width()

        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.btn_setSavingDir.setFixedWidth(btn_width)

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Update scan size entry
        self.entry_scan_size = QDoubleSpinBox()
        self.entry_scan_size.setKeyboardTracking(False)
        self.entry_scan_size.setRange(0.1, 100)
        self.entry_scan_size.setValue(0.1)
        self.entry_scan_size.setSuffix(" mm")

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setKeyboardTracking(False)
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setValue(10)
        self.entry_overlap.setSuffix("%")
        self.entry_overlap.setFixedWidth(btn_width)

        # Add z-min and z-max entries
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setKeyboardTracking(False)
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to minimum
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_minZ_button = QPushButton("Set Z-min")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.goto_minZ_button = QPushButton("Go To")
        self.goto_minZ_button.clicked.connect(self.goto_z_min)
        self.goto_minZ_button.setFixedWidth(50)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setKeyboardTracking(False)
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to maximum
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_maxZ_button = QPushButton("Set Z-max")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.goto_maxZ_button = QPushButton("Go To")
        self.goto_maxZ_button.clicked.connect(self.goto_z_max)
        self.goto_maxZ_button.setFixedWidth(50)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        # self.entry_deltaZ.setEnabled(False)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setEnabled(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setKeyboardTracking(False)
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(24 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(5000)
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])
        self.combobox_z_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Add a combo box for shape selection
        self.combobox_shape = QComboBox()
        self.combobox_shape.addItems(["Square", "Circle", "Rectangle"])
        self.combobox_shape.setFixedWidth(btn_width)
        # self.combobox_shape.currentTextChanged.connect(self.on_shape_changed)

        self.btn_save_scan_coordinates = QPushButton("Save Coordinates")
        self.btn_load_scan_coordinates = QPushButton("Load New Coords")

        # Add text area for showing loaded file path
        self.text_loaded_coordinates = QLineEdit()
        self.text_loaded_coordinates.setReadOnly(True)
        self.text_loaded_coordinates.setPlaceholderText("No file loaded")

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Laser AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        # Add acquisition tabs with checkboxes and frames
        # XY Tab
        self.xy_frame = QFrame()

        self.checkbox_xy = QCheckBox("XY")
        self.checkbox_xy.setChecked(True)

        self.combobox_xy_mode = QComboBox()
        self.combobox_xy_mode.addItems(["Current Position", "Select Wells", "Manual", "Load Coordinates"])
        self.combobox_xy_mode.setEnabled(True)  # Initially enabled since XY is checked
        # disable manual mode on init (before mosaic is loaded) - identify the index of the manual mode by name
        _manual_index = self.combobox_xy_mode.findText("Manual")
        self.combobox_xy_mode.model().item(_manual_index).setEnabled(False)

        xy_layout = QHBoxLayout()
        xy_layout.setContentsMargins(8, 4, 8, 4)
        xy_layout.addWidget(self.checkbox_xy)
        xy_layout.addWidget(self.combobox_xy_mode)
        self.xy_frame.setLayout(xy_layout)

        # Z Tab
        self.z_frame = QFrame()

        self.checkbox_z = QCheckBox("Z")
        self.checkbox_z.setChecked(False)

        self.combobox_z_mode = QComboBox()
        self.combobox_z_mode.addItems(["From Bottom", "Set Range"])
        self.combobox_z_mode.setEnabled(False)  # Initially disabled since Z is unchecked

        z_layout = QHBoxLayout()
        z_layout.setContentsMargins(8, 4, 8, 4)
        z_layout.addWidget(self.checkbox_z)
        z_layout.addWidget(self.combobox_z_mode)
        self.z_frame.setLayout(z_layout)

        # Time Tab
        self.time_frame = QFrame()

        self.checkbox_time = QCheckBox("Time")
        self.checkbox_time.setChecked(False)

        time_layout = QHBoxLayout()
        time_layout.setContentsMargins(8, 4, 8, 4)
        time_layout.addWidget(self.checkbox_time)
        time_layout.addStretch()  # Fill horizontal space
        self.time_frame.setLayout(time_layout)

        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        #  Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        row_1_layout = QHBoxLayout()
        row_1_layout.addWidget(QLabel("Experiment ID"))
        row_1_layout.addWidget(self.lineEdit_experimentID)
        main_layout.addLayout(row_1_layout)

        # Acquisition tabs row
        tabs_layout = QHBoxLayout()
        tabs_layout.setSpacing(4)  # Small spacing between frames
        tabs_layout.addWidget(self.xy_frame, 2)  # Give XY frame more space (weight 2)
        tabs_layout.addWidget(self.z_frame, 1)  # Z frame gets weight 1
        tabs_layout.addWidget(self.time_frame, 1)  # Time frame gets weight 1
        main_layout.addLayout(tabs_layout)

        # Scan Shape, FOV overlap, and Save / Load Scan Coordinates
        # Frame for orange background
        self.xy_controls_frame = QFrame()

        self.row_2_layout = QGridLayout()
        self.row_2_layout.setContentsMargins(4, 2, 4, 2)
        self.scan_shape_label = QLabel("Scan Shape")
        self.scan_size_label = QLabel("Scan Size")
        self.coverage_label = QLabel("Coverage")
        self.fov_overlap_label = QLabel("FOV Overlap")

        self.row_2_layout.addWidget(self.scan_shape_label, 0, 0)
        self.row_2_layout.addWidget(self.combobox_shape, 0, 1)
        self.row_2_layout.addWidget(self.scan_size_label, 0, 2)
        self.row_2_layout.addWidget(self.entry_scan_size, 0, 3)
        self.row_2_layout.addWidget(self.coverage_label, 0, 4)
        self.row_2_layout.addWidget(self.entry_well_coverage, 0, 5)
        self.row_2_layout.addWidget(self.fov_overlap_label, 1, 0)
        self.row_2_layout.addWidget(self.entry_overlap, 1, 1)
        self.row_2_layout.addWidget(self.btn_save_scan_coordinates, 1, 2, 1, 4)

        self.xy_controls_frame.setLayout(self.row_2_layout)
        main_layout.addWidget(self.xy_controls_frame)

        # Frame for Load Coordinates UI (initially hidden)
        self.load_coordinates_frame = QFrame()
        load_coords_layout = QHBoxLayout()
        load_coords_layout.setContentsMargins(4, 2, 4, 2)
        load_coords_layout.addWidget(self.btn_load_scan_coordinates)
        load_coords_layout.addWidget(self.text_loaded_coordinates)
        self.load_coordinates_frame.setLayout(load_coords_layout)
        self.load_coordinates_frame.setVisible(False)  # Initially hidden
        main_layout.addWidget(self.load_coordinates_frame)

        grid = QGridLayout()

        # Z controls frame for dz/Nz (left half of row 1) with blue background
        self.z_controls_dz_frame = QFrame()

        self.dz_layout = QHBoxLayout()
        self.dz_layout.setContentsMargins(4, 2, 4, 2)
        self.dz_layout.addWidget(QLabel("dz"))
        self.dz_layout.addWidget(self.entry_deltaZ)
        self.dz_layout.addWidget(QLabel("Nz"))
        self.dz_layout.addWidget(self.entry_NZ)

        self.z_controls_dz_frame.setLayout(self.dz_layout)
        grid.addWidget(self.z_controls_dz_frame, 0, 0)

        # Time controls frame with green background
        self.time_controls_frame = QFrame()

        # dt and Nt
        self.dt_layout = QHBoxLayout()
        self.dt_layout.setContentsMargins(4, 2, 4, 2)
        self.dt_layout.addWidget(QLabel("dt"))
        self.dt_layout.addWidget(self.entry_dt)
        self.dt_layout.addWidget(QLabel("Nt"))
        self.dt_layout.addWidget(self.entry_Nt)

        self.time_controls_frame.setLayout(self.dt_layout)
        grid.addWidget(self.time_controls_frame, 0, 2)

        # Create informational labels for when modes are not selected
        self.z_not_selected_label = QLabel("Z stack not selected")
        self.z_not_selected_label.setAlignment(Qt.AlignCenter)
        self.z_not_selected_label.setStyleSheet(
            """
            QLabel {
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 0px;
                color: palette(text);
            }
        """
        )
        self.z_not_selected_label.setVisible(False)

        self.time_not_selected_label = QLabel("Time lapse not selected")
        self.time_not_selected_label.setAlignment(Qt.AlignCenter)
        self.time_not_selected_label.setStyleSheet(
            """
            QLabel {
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 0px;
                color: palette(text);
            }
        """
        )
        self.time_not_selected_label.setVisible(False)

        # Z controls frame for Z-min and Z-max (full row 2) with blue background
        self.z_controls_range_frame = QFrame()
        z_range_layout = QHBoxLayout()
        z_range_layout.setContentsMargins(4, 2, 4, 2)

        # Z-min
        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.entry_minZ)
        self.z_min_layout.addWidget(self.set_minZ_button)
        self.z_min_layout.addWidget(self.goto_minZ_button)
        z_range_layout.addLayout(self.z_min_layout)

        # Spacer to maintain original spacing between Z-min and Z-max
        z_range_layout.addStretch()

        # Z-max
        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addWidget(self.entry_maxZ)
        self.z_max_layout.addWidget(self.set_maxZ_button)
        self.z_max_layout.addWidget(self.goto_maxZ_button)
        z_range_layout.addLayout(self.z_max_layout)

        self.z_controls_range_frame.setLayout(z_range_layout)
        self.z_controls_range_frame.setVisible(False)  # Initially hidden (shown when "Set Range" mode)
        grid.addWidget(self.z_controls_range_frame, 1, 0, 1, 3)  # Span full row (columns 0, 1, 2)

        # Configuration list
        grid.addWidget(self.list_configurations, 2, 0)

        # Options and Start button
        options_layout = QVBoxLayout()
        options_layout.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        # options_layout.addWidget(self.checkbox_genAFMap)  # We are not using AF map now
        options_layout.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        bottom_right = QHBoxLayout()
        bottom_right.addLayout(options_layout)
        bottom_right.addSpacing(2)
        bottom_right.addLayout(button_layout)

        grid.addLayout(bottom_right, 2, 2)
        spacer_widget = QWidget()
        spacer_widget.setFixedWidth(2)
        grid.addWidget(spacer_widget, 0, 1)

        # Add informational labels to grid (initially hidden)
        grid.addWidget(self.z_not_selected_label, 0, 0)
        grid.addWidget(self.time_not_selected_label, 0, 2)

        # Set column stretches
        grid.setColumnStretch(0, 1)  # Middle spacer
        grid.setColumnStretch(1, 0)  # Middle spacer
        grid.setColumnStretch(2, 1)  # Middle spacer

        main_layout.addLayout(grid)
        # Row 5: Progress Bar
        row_progress_layout = QHBoxLayout()
        row_progress_layout.addWidget(self.progress_label)
        row_progress_layout.addWidget(self.progress_bar)
        row_progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(row_progress_layout)
        self.toggle_z_range_controls(False)  # Initially hide Z-range controls

        # Initialize Z and Time controls visibility based on checkbox states
        if not self.checkbox_z.isChecked():
            self.hide_z_controls()
        if not self.checkbox_time.isChecked():
            self.hide_time_controls()

        # Update control visibility based on both states
        self.update_control_visibility()

        # Initialize scan controls visibility based on XY checkbox state
        self.update_scan_control_ui()

        # Update tab styles now that all frames are created
        self.update_tab_styles()

        # Initialize previous XY mode tracking
        self._previous_xy_mode = self.combobox_xy_mode.currentText()

        # Connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.entry_overlap.valueChanged.connect(self.update_coordinates)
        self.entry_scan_size.valueChanged.connect(self.update_coordinates)
        self.entry_scan_size.valueChanged.connect(self.update_coverage_from_scan_size)
        self.entry_well_coverage.valueChanged.connect(self.update_scan_size_from_coverage)
        self.combobox_shape.currentTextChanged.connect(self.reset_coordinates)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_useFocusMap.toggled.connect(self.multipointController.set_manual_focus_map_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)
        if not self.performance_mode:
            self.napariMosaicWidget.signal_layers_initialized.connect(self.enable_manual_ROI)

        # Connect save/clear coordinates button
        self.btn_save_scan_coordinates.clicked.connect(self.on_save_or_clear_coordinates_clicked)
        self.btn_load_scan_coordinates.clicked.connect(self.on_load_coordinates_clicked)

        # Connect acquisition tabs
        self.checkbox_xy.toggled.connect(self.on_xy_toggled)
        self.combobox_xy_mode.currentTextChanged.connect(self.on_xy_mode_changed)
        self.checkbox_z.toggled.connect(self.on_z_toggled)
        self.combobox_z_mode.currentTextChanged.connect(self.on_z_mode_changed)
        self.checkbox_time.toggled.connect(self.on_time_toggled)

        # Load cached acquisition settings
        self.load_multipoint_widget_config_from_cache()

        # Connect settings saving to relevant value changes
        self.checkbox_xy.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.combobox_xy_mode.currentTextChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_z.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.combobox_z_mode.currentTextChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_time.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_overlap.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_dt.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_Nt.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_deltaZ.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_NZ.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.list_configurations.itemSelectionChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_withAutofocus.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.save_multipoint_widget_config_to_cache)

    def enable_manual_ROI(self):
        _manual_index = self.combobox_xy_mode.findText("Manual")
        self.combobox_xy_mode.model().item(_manual_index).setEnabled(True)

    def initialize_live_scan_grid_state(self):
        """Initialize live scan grid state - call this after all external connections are made"""
        enable_live_scan_grid = (
            self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position"
        )
        self.signal_toggle_live_scan_grid.emit(enable_live_scan_grid)

    def save_multipoint_widget_config_to_cache(self):
        """Save current acquisition settings to cache"""
        try:
            os.makedirs("cache", exist_ok=True)

            settings = {
                "xy_enabled": self.checkbox_xy.isChecked(),
                "xy_mode": self.combobox_xy_mode.currentText(),
                "z_enabled": self.checkbox_z.isChecked(),
                "z_mode": self.combobox_z_mode.currentText(),
                "time_enabled": self.checkbox_time.isChecked(),
                "fov_overlap": self.entry_overlap.value(),
                "dt": self.entry_dt.value(),
                "nt": self.entry_Nt.value(),
                "dz": self.entry_deltaZ.value(),
                "nz": self.entry_NZ.value(),
                "selected_channels": [item.text() for item in self.list_configurations.selectedItems()],
                "contrast_af": self.checkbox_withAutofocus.isChecked(),
                "laser_af": self.checkbox_withReflectionAutofocus.isChecked(),
            }

            with open("cache/multipoint_widget_config.yaml", "w") as f:
                yaml.dump(settings, f, default_flow_style=False, sort_keys=False)

        except Exception as e:
            self._log.warning(f"Failed to save acquisition settings to cache: {e}")

    def load_multipoint_widget_config_from_cache(self):
        """Load acquisition settings from cache if it exists"""
        try:
            cache_file = "cache/multipoint_widget_config.yaml"
            if not os.path.exists(cache_file):
                return

            with open(cache_file, "r") as f:
                settings = yaml.safe_load(f)

            # Block signals to prevent triggering save during load
            self.checkbox_xy.blockSignals(True)
            self.combobox_xy_mode.blockSignals(True)
            self.checkbox_z.blockSignals(True)
            self.combobox_z_mode.blockSignals(True)
            self.checkbox_time.blockSignals(True)
            self.entry_overlap.blockSignals(True)
            self.entry_dt.blockSignals(True)
            self.entry_Nt.blockSignals(True)
            self.entry_deltaZ.blockSignals(True)
            self.entry_NZ.blockSignals(True)
            self.list_configurations.blockSignals(True)
            self.checkbox_withAutofocus.blockSignals(True)
            self.checkbox_withReflectionAutofocus.blockSignals(True)

            # Set flag to prevent automatic file dialog when loading "Load Coordinates" mode from cache
            self._loading_from_cache = True

            # Load settings
            self.checkbox_xy.setChecked(settings.get("xy_enabled", True))

            xy_mode = settings.get("xy_mode", "Current Position")
            if xy_mode in ["Current Position", "Select Wells", "Manual", "Load Coordinates"]:
                self.combobox_xy_mode.setCurrentText(xy_mode)

            # If XY is checked and mode is Manual at startup, uncheck XY and change mode to Current Position
            if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Manual":
                self.checkbox_xy.setChecked(False)
                self.combobox_xy_mode.setCurrentText("Current Position")
                # Set the "before uncheck" mode to Current Position, so re-checking XY stays at Current Position
                self._xy_mode_before_uncheck = "Current Position"
                self._log.info(
                    "XY was checked with Manual mode at startup - unchecked XY and changed mode to Current Position"
                )

            self.checkbox_z.setChecked(settings.get("z_enabled", False))

            z_mode = settings.get("z_mode", "From Bottom")
            if z_mode in ["From Bottom", "Set Range"]:
                self.combobox_z_mode.setCurrentText(z_mode)

            self.checkbox_time.setChecked(settings.get("time_enabled", False))
            self.entry_overlap.setValue(settings.get("fov_overlap", 10))
            self.entry_dt.setValue(settings.get("dt", 0))
            self.entry_Nt.setValue(settings.get("nt", 1))
            self.entry_deltaZ.setValue(settings.get("dz", 1.0))
            self.entry_NZ.setValue(settings.get("nz", 1))

            # Restore selected channels
            selected_channels = settings.get("selected_channels", [])
            if selected_channels:
                self.list_configurations.clearSelection()
                for i in range(self.list_configurations.count()):
                    item = self.list_configurations.item(i)
                    if item.text() in selected_channels:
                        item.setSelected(True)

            # Restore autofocus settings
            self.checkbox_withAutofocus.setChecked(settings.get("contrast_af", False))
            self.checkbox_withReflectionAutofocus.setChecked(settings.get("laser_af", False))

            # Unblock signals
            self.checkbox_xy.blockSignals(False)
            self.combobox_xy_mode.blockSignals(False)
            self.checkbox_z.blockSignals(False)
            self.combobox_z_mode.blockSignals(False)
            self.checkbox_time.blockSignals(False)
            self.entry_overlap.blockSignals(False)
            self.entry_dt.blockSignals(False)
            self.entry_Nt.blockSignals(False)
            self.entry_deltaZ.blockSignals(False)
            self.entry_NZ.blockSignals(False)
            self.list_configurations.blockSignals(False)
            self.checkbox_withAutofocus.blockSignals(False)
            self.checkbox_withReflectionAutofocus.blockSignals(False)

            # Update UI state based on loaded settings
            self.update_scan_control_ui()
            self.update_control_visibility()
            self.update_tab_styles()  # Update tab visual styles based on checkbox states

            # Ensure XY mode combobox is properly enabled based on loaded XY state
            self.combobox_xy_mode.setEnabled(self.checkbox_xy.isChecked())

            # Ensure Z controls and Z mode combobox are properly enabled based on loaded Z state
            self.combobox_z_mode.setEnabled(self.checkbox_z.isChecked())
            if self.checkbox_z.isChecked():
                self.show_z_controls(True)
                # Also ensure Z range controls are properly toggled based on loaded Z mode
                if self.combobox_z_mode.currentText() == "Set Range":
                    self.toggle_z_range_controls(True)

            # Ensure Time controls are properly shown based on loaded Time state
            if self.checkbox_time.isChecked():
                self.show_time_controls(True)

            # Clear the cache loading flag
            self._loading_from_cache = False

            self._log.info("Loaded acquisition settings from cache")

        except Exception as e:
            self._log.warning(f"Failed to load acquisition settings from cache: {e}")
            # Clear the flag even on error
            self._loading_from_cache = False

    def update_tab_styles(self):
        """Update tab frame styles based on checkbox states"""
        # Active tab styles (checked) - custom colors for each tab
        xy_active_style = """
            QFrame {
                border: 1px solid #FF8C00;
                border-radius: 2px;
            }
        """

        # Orange background with opaque widget backgrounds to prevent color bleed
        xy_controls_style = """
            QFrame {
                background-color: rgba(255, 140, 0, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
                color: black;
            }
            QFrame QComboBox:disabled, QFrame QSpinBox:disabled, QFrame QDoubleSpinBox:disabled {
                background-color: palette(button);
                color: palette(disabled-text);
            }
            QFrame QComboBox QAbstractItemView {
                background-color: white;
                color: black;
                selection-background-color: palette(highlight);
                selection-color: palette(highlighted-text);
            }
            QFrame QPushButton {
                background-color: #FFD9B3;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        z_active_style = """
            QFrame {
                border: 1px solid palette(highlight);
                border-radius: 2px;
            }
        """

        # Blue background for Z controls with opaque widget backgrounds
        z_controls_style = """
            QFrame {
                background-color: rgba(0, 120, 215, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
            }
            QFrame QPushButton {
                background-color: #C2D9FF;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        time_active_style = """
            QFrame {
                border: 1px solid #00A000;
                border-radius: 2px;
            }
        """

        # Green background for Time controls with opaque widget backgrounds
        time_controls_style = """
            QFrame {
                background-color: rgba(0, 160, 0, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
            }
            QFrame QPushButton {
                background-color: #C2FFC2;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        # Inactive tab style (unchecked) - uses default Qt inactive tab colors
        inactive_style = """
            QFrame {
                border: 1px solid palette(mid);
                border-radius: 2px;
            }
        """

        # Apply styles based on checkbox states
        self.xy_frame.setStyleSheet(xy_active_style if self.checkbox_xy.isChecked() else inactive_style)
        if hasattr(self, "xy_controls_frame"):
            self.xy_controls_frame.setStyleSheet(xy_controls_style if self.checkbox_xy.isChecked() else "")
        if hasattr(self, "load_coordinates_frame"):
            self.load_coordinates_frame.setStyleSheet(xy_controls_style if self.checkbox_xy.isChecked() else "")

        self.z_frame.setStyleSheet(z_active_style if self.checkbox_z.isChecked() else inactive_style)
        if hasattr(self, "z_controls_dz_frame"):
            self.z_controls_dz_frame.setStyleSheet(z_controls_style if self.checkbox_z.isChecked() else "")
        if hasattr(self, "z_controls_range_frame"):
            self.z_controls_range_frame.setStyleSheet(z_controls_style if self.checkbox_z.isChecked() else "")

        self.time_frame.setStyleSheet(time_active_style if self.checkbox_time.isChecked() else inactive_style)
        if hasattr(self, "time_controls_frame"):
            self.time_controls_frame.setStyleSheet(time_controls_style if self.checkbox_time.isChecked() else "")

    def on_xy_toggled(self, checked):
        """Handle XY checkbox toggle"""
        self.combobox_xy_mode.setEnabled(checked)

        if not checked:
            # Store the current mode before unchecking
            self._xy_mode_before_uncheck = self.combobox_xy_mode.currentText()

            # Switch mode to "Current Position" when unchecking
            self.combobox_xy_mode.setCurrentText("Current Position")
        else:
            # When checking XY, restore previous mode if it exists
            if self._xy_mode_before_uncheck is not None:
                # Check if previous mode was Manual
                if self._xy_mode_before_uncheck == "Manual":
                    # If mosaic view has been cleared (no shapes), stay at "Current Position"
                    if self.shapes_mm is None or len(self.shapes_mm) == 0:
                        self.combobox_xy_mode.setCurrentText("Current Position")
                        self._log.info("Manual mode had no shapes, staying at Current Position")
                    else:
                        # Shapes exist, restore Manual mode
                        self.combobox_xy_mode.setCurrentText("Manual")
                else:
                    # For non-Manual modes, always restore
                    self.combobox_xy_mode.setCurrentText(self._xy_mode_before_uncheck)

        self.update_tab_styles()

        # Show/hide scan shape and coordinate controls
        self.update_scan_control_ui()

        if checked:
            self.update_coordinates()  # to-do: what does this do? is it needed?
            if self.combobox_xy_mode.currentText() == "Current Position":
                self.signal_toggle_live_scan_grid.emit(True)
        else:
            self.signal_toggle_live_scan_grid.emit(False)  # disable live scan grid regardless of XY mode

        self._log.debug(f"XY acquisition {'enabled' if checked else 'disabled'}")

    def on_xy_mode_changed(self, mode):
        """Handle XY mode dropdown change"""
        self._log.debug(f"XY mode changed to: {mode}")

        # Store current mode's parameters before switching (if we know the previous mode)
        # We need to track the previous mode to store its parameters
        if hasattr(self, "_previous_xy_mode") and self._previous_xy_mode in ["Current Position", "Select Wells"]:
            self.store_xy_mode_parameters(self._previous_xy_mode)

        # Restore parameters for the new mode
        if mode in ["Current Position", "Select Wells"]:
            self.restore_xy_mode_parameters(mode)

        # Update UI based on the new mode
        self.update_scan_control_ui()

        # Handle coordinate restoration/clearing based on mode
        if mode == "Load Coordinates":
            # If no file has been loaded previously, open file dialog immediately
            # But skip if we're loading from cache
            if self.cached_loaded_coordinates_df is None and not getattr(self, "_loading_from_cache", False):
                QTimer.singleShot(100, self.on_load_coordinates_clicked)
            else:
                # Restore cached coordinates when switching to Load Coordinates mode
                self.restore_cached_coordinates()
        else:
            # When switching away from Load Coordinates, clear coordinates and update based on new mode
            if hasattr(self, "_previous_xy_mode") and self._previous_xy_mode == "Load Coordinates":
                self.scanCoordinates.clear_regions()

        # Store the current mode as previous for next time
        self._previous_xy_mode = mode

        if mode == "Manual":
            self.signal_manual_shape_mode.emit(True)
        elif mode == "Load Coordinates":
            # Don't update coordinates or emit signals for Load Coordinates mode
            pass
        else:
            self.update_coordinates()  # to-do: what does this do? is it needed?

        if mode == "Current Position":
            self.signal_toggle_live_scan_grid.emit(True)  # enable live scan grid
        else:
            self.signal_toggle_live_scan_grid.emit(False)  # disable live scan grid

    def update_scan_control_ui(self):
        """Update scan control UI based on XY checkbox and mode selection"""
        xy_checked = self.checkbox_xy.isChecked()
        xy_mode = self.combobox_xy_mode.currentText()

        # Handle Load Coordinates mode separately
        if xy_checked and xy_mode == "Load Coordinates":
            # Hide the two-line xy_controls_frame
            self.xy_controls_frame.setVisible(False)
            # Show the Load Coordinates frame
            self.load_coordinates_frame.setVisible(True)
            return

        # Show/hide the entire XY controls frame based on XY checkbox
        self.xy_controls_frame.setVisible(xy_checked)
        # Hide the Load Coordinates frame for all other modes
        self.load_coordinates_frame.setVisible(False)

        # Handle coverage field based on XY mode
        if xy_checked:
            if xy_mode in ["Current Position", "Manual"]:
                # For Current Position and Manual modes, coverage should be N/A and disabled
                self.entry_well_coverage.blockSignals(True)
                self.entry_well_coverage.setRange(0, 0)  # Allow 0 for N/A mode
                self.entry_well_coverage.setValue(0)  # Set to 0 for N/A indicator
                self.entry_well_coverage.setEnabled(False)
                self.entry_well_coverage.setSuffix(" (N/A)")
                self.entry_well_coverage.blockSignals(False)
                if xy_mode == "Manual":
                    # hide the row of scan shape, scan size and coverage
                    self.scan_shape_label.setVisible(False)
                    self.combobox_shape.setVisible(False)
                    self.scan_size_label.setVisible(False)
                    self.entry_scan_size.setVisible(False)
                    self.coverage_label.setVisible(False)
                    self.entry_well_coverage.setVisible(False)
                elif xy_mode == "Current Position":
                    # show the row of scan shape, scan size and coverage
                    self.scan_shape_label.setVisible(True)
                    self.combobox_shape.setVisible(True)
                    self.scan_size_label.setVisible(True)
                    self.entry_scan_size.setVisible(True)
                    self.coverage_label.setVisible(True)
                    self.entry_well_coverage.setVisible(True)
            elif xy_mode == "Select Wells":
                # For Select Wells mode, coverage should be enabled
                self.entry_well_coverage.blockSignals(True)
                self.entry_well_coverage.setRange(1, 999.99)  # Restore normal range
                self.entry_well_coverage.setSuffix("%")

                # Restore stored coverage value for Select Wells mode
                if self.stored_xy_params["Select Wells"]["coverage"] is not None:
                    self.entry_well_coverage.setValue(self.stored_xy_params["Select Wells"]["coverage"])
                else:
                    self.entry_well_coverage.setValue(100)  # Set to default if no stored value

                self.entry_well_coverage.blockSignals(False)

                # Enable coverage unless it's glass slide mode
                if "glass slide" not in self.navigationViewer.sample:
                    self.entry_well_coverage.setEnabled(True)
                else:
                    self.entry_well_coverage.setEnabled(False)

                # show the row of scan shape, scan size and coverage
                self.scan_shape_label.setVisible(True)
                self.combobox_shape.setVisible(True)
                self.scan_size_label.setVisible(True)
                self.entry_scan_size.setVisible(True)
                self.coverage_label.setVisible(True)
                self.entry_well_coverage.setVisible(True)

    def set_coordinates_to_current_position(self):
        """Set scan coordinates to current stage position (single FOV)"""
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # Clear existing regions
        if self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

        # Get current position and add it as a single region
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm

        # Add current position as a single FOV with minimal scan size
        scan_size_mm = 0.01  # Very small scan size for single FOV
        overlap_percent = 0  # No overlap needed for single FOV
        shape = "Square"  # Default shape

        self.scanCoordinates.add_region("current", x, y, scan_size_mm, overlap_percent, shape)

    def on_z_toggled(self, checked):
        """Handle Z checkbox toggle"""
        self.update_tab_styles()

        # Enable/disable the Z mode dropdown
        self.combobox_z_mode.setEnabled(checked)

        if checked:
            # Z Stack enabled - restore stored parameters and show controls
            self.restore_z_parameters()
            self.show_z_controls(True)
        else:
            # Z Stack disabled - store current parameters and hide controls
            self.store_z_parameters()
            self.hide_z_controls()

        # Update visibility based on both Z and Time states
        self.update_control_visibility()

        self._log.debug(f"Z acquisition {'enabled' if checked else 'disabled'}")

    def on_z_mode_changed(self, mode):
        """Handle Z mode dropdown change"""
        # Show/hide Z-min/Z-max controls based on mode
        self.toggle_z_range_controls(mode == "Set Range")
        self._log.debug(f"Z mode changed to: {mode}")

    def on_time_toggled(self, checked):
        """Handle Time checkbox toggle"""
        self.update_tab_styles()

        if checked:
            # Time lapse enabled - restore stored parameters and show controls
            self.restore_time_parameters()
            self.show_time_controls(True)
        else:
            # Time lapse disabled - store current parameters and hide controls
            self.store_time_parameters()
            self.hide_time_controls()

        # Update visibility based on both Z and Time states
        self.update_control_visibility()

        self._log.debug(f"Time acquisition {'enabled' if checked else 'disabled'}")

    def store_xy_mode_parameters(self, mode):
        """Store current scan size, coverage, and shape parameters for the given XY mode"""
        if mode in self.stored_xy_params:
            # Always store scan size and scan shape
            self.stored_xy_params[mode]["scan_size"] = self.entry_scan_size.value()
            self.stored_xy_params[mode]["scan_shape"] = self.combobox_shape.currentText()

            # Only store coverage for Select Wells mode (Current Position uses N/A)
            if mode == "Select Wells":
                self.stored_xy_params[mode]["coverage"] = self.entry_well_coverage.value()

    def restore_xy_mode_parameters(self, mode):
        """Restore stored scan size, coverage, and shape parameters for the given XY mode"""
        if mode in self.stored_xy_params:
            # Restore scan size for both Current Position and Select Wells modes
            if self.stored_xy_params[mode]["scan_size"] is not None:
                self.entry_scan_size.blockSignals(True)
                self.entry_scan_size.setValue(self.stored_xy_params[mode]["scan_size"])
                self.entry_scan_size.blockSignals(False)
            else:
                # Set default values if no stored value exists
                if mode == "Current Position":
                    # For current position, use a small default scan size
                    self.entry_scan_size.blockSignals(True)
                    self.entry_scan_size.setValue(0.1)  # Small default for single FOV
                    self.entry_scan_size.blockSignals(False)
                elif mode == "Select Wells":
                    # For select wells, use a larger default scan size
                    self.entry_scan_size.blockSignals(True)
                    self.entry_scan_size.setValue(1.0)  # Larger default for well coverage
                    self.entry_scan_size.blockSignals(False)

            # Restore scan shape for both modes
            if self.stored_xy_params[mode]["scan_shape"] is not None:
                self.combobox_shape.blockSignals(True)
                self.combobox_shape.setCurrentText(self.stored_xy_params[mode]["scan_shape"])
                self.combobox_shape.blockSignals(False)
            else:
                # Set default shape if no stored value exists
                self.combobox_shape.blockSignals(True)
                if mode == "Current Position":
                    # For current position, default to Square (simple single FOV)
                    self.combobox_shape.setCurrentText("Square")
                elif mode == "Select Wells":
                    # For select wells, use the format-based default from set_default_shape
                    self.set_default_shape()
                self.combobox_shape.blockSignals(False)

            # Coverage restoration for Select Wells mode is handled in update_scan_control_ui()
            # to avoid conflicts with range setting and UI state management

    def store_z_parameters(self):
        """Store current Z parameters before hiding controls"""
        self.stored_z_params["dz"] = self.entry_deltaZ.value()
        self.stored_z_params["nz"] = self.entry_NZ.value()
        self.stored_z_params["z_min"] = self.entry_minZ.value()
        self.stored_z_params["z_max"] = self.entry_maxZ.value()
        self.stored_z_params["z_mode"] = self.combobox_z_mode.currentText()

    def restore_z_parameters(self):
        """Restore stored Z parameters when showing controls"""
        if self.stored_z_params["dz"] is not None:
            self.entry_deltaZ.setValue(self.stored_z_params["dz"])
        if self.stored_z_params["nz"] is not None:
            self.entry_NZ.setValue(self.stored_z_params["nz"])
        if self.stored_z_params["z_min"] is not None:
            self.entry_minZ.setValue(self.stored_z_params["z_min"])
        if self.stored_z_params["z_max"] is not None:
            self.entry_maxZ.setValue(self.stored_z_params["z_max"])
        self.combobox_z_mode.setCurrentText(self.stored_z_params["z_mode"])

    def hide_z_controls(self):
        """Hide Z-related controls and set single-slice parameters"""
        # Hide dz/Nz widgets
        for i in range(self.dz_layout.count()):
            widget = self.dz_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(False)

        # Hide Z-min/Z-max controls
        for layout in (self.z_min_layout, self.z_max_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)

        # Set single-slice parameters
        current_z = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_NZ.setValue(1)
        self.entry_minZ.setValue(current_z)
        self.entry_maxZ.setValue(current_z)
        self.combobox_z_mode.blockSignals(True)
        self.combobox_z_mode.setCurrentText("From Bottom")
        self.combobox_z_mode.blockSignals(False)

    def show_z_controls(self, visible):
        """Show Z-related controls"""
        # Show dz/Nz widgets
        for i in range(self.dz_layout.count()):
            widget = self.dz_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)

        # Show/hide Z-min/Z-max based on dropdown selection
        self.toggle_z_range_controls(self.combobox_z_mode.currentText() == "Set Range")

    def store_time_parameters(self):
        """Store current Time parameters before hiding controls"""
        self.stored_time_params["dt"] = self.entry_dt.value()
        self.stored_time_params["nt"] = self.entry_Nt.value()

    def restore_time_parameters(self):
        """Restore stored Time parameters when showing controls"""
        if self.stored_time_params["dt"] is not None:
            self.entry_dt.setValue(self.stored_time_params["dt"])
        if self.stored_time_params["nt"] is not None:
            self.entry_Nt.setValue(self.stored_time_params["nt"])

    def hide_time_controls(self):
        """Hide Time-related controls and set single-timepoint parameters"""
        # Hide dt/Nt widgets
        for i in range(self.dt_layout.count()):
            widget = self.dt_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(False)

        # Set single-timepoint parameters
        self.entry_dt.setValue(0)
        self.entry_Nt.setValue(1)

    def show_time_controls(self, visible):
        """Show Time-related controls"""
        # Show dt/Nt widgets
        for i in range(self.dt_layout.count()):
            widget = self.dt_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)

    def update_control_visibility(self):
        """Update visibility of controls and informational labels based on Z and Time states"""
        z_checked = self.checkbox_z.isChecked()
        time_checked = self.checkbox_time.isChecked()

        if time_checked and not z_checked:
            # Time lapse selected but Z stack not - show "Z stack not selected" message
            self.z_not_selected_label.setVisible(True)
            # Hide actual Z controls
            for i in range(self.dz_layout.count()):
                widget = self.dz_layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)
        elif z_checked and not time_checked:
            # Z stack selected but Time lapse not - show "Time lapse not selected" message
            self.time_not_selected_label.setVisible(True)
            # Hide actual Time controls
            for i in range(self.dt_layout.count()):
                widget = self.dt_layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)
        else:
            # Both selected or both unselected - hide informational labels
            self.z_not_selected_label.setVisible(False)
            self.time_not_selected_label.setVisible(False)

            # Show/hide actual controls based on individual states
            if z_checked:
                self.show_z_controls(True)
            if time_checked:
                self.show_time_controls(True)

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def toggle_z_range_controls(self, is_visible):
        # Show/hide the entire range frame (Z-min and Z-max)
        if hasattr(self, "z_controls_range_frame"):
            self.z_controls_range_frame.setVisible(is_visible)

        # Also control individual widgets for compatibility
        for layout in (self.z_min_layout, self.z_max_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(is_visible)

        # Disable and uncheck reflection autofocus checkbox if Z-range is visible
        if is_visible:
            self.checkbox_withReflectionAutofocus.setChecked(False)
        self.checkbox_withReflectionAutofocus.setEnabled(not is_visible)
        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)
        current_z = self.stage.get_pos().z_mm * 1000
        self.entry_minZ.setValue(current_z)
        if is_visible:
            self._reset_reflection_af_reference()
        self.entry_maxZ.setValue(current_z)

        # Safely connect or disconnect signals
        try:
            if is_visible:
                self.entry_minZ.valueChanged.connect(self.update_z_max)
                self.entry_maxZ.valueChanged.connect(self.update_z_min)
                self.entry_minZ.valueChanged.connect(self.update_Nz)
                self.entry_maxZ.valueChanged.connect(self.update_Nz)
                self.entry_deltaZ.valueChanged.connect(self.update_Nz)
                self.update_Nz()
            else:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
        except TypeError:
            # Handle case where signals might not be connected/disconnected
            pass

        # Update the layout
        self.updateGeometry()
        self.update()

    def set_default_scan_size(self):
        if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Select Wells":
            self._log.debug(f"Sample Format: {self.navigationViewer.sample}")
            self.combobox_shape.blockSignals(True)
            self.entry_well_coverage.blockSignals(True)
            self.entry_scan_size.blockSignals(True)

            self.set_default_shape()

            if "glass slide" in self.navigationViewer.sample:
                self.entry_scan_size.setValue(
                    0.1
                )  # init to 0.1mm when switching to 'glass slide' (for imaging a single FOV by default)
                self.entry_scan_size.setEnabled(True)
                self.entry_well_coverage.setEnabled(False)
            else:
                self.entry_well_coverage.setEnabled(True)
                # entry_well_coverage.valueChanged signal will not emit coverage = 100 already
                self.entry_well_coverage.setValue(100)
                self.update_scan_size_from_coverage()

            self.update_coordinates()

            self.combobox_shape.blockSignals(False)
            self.entry_well_coverage.blockSignals(False)
            self.entry_scan_size.blockSignals(False)
        else:
            # update stored settings for "Select Wells" mode for use later
            coverage = 100
            self.stored_xy_params["Select Wells"]["coverage"] = coverage

            # Calculate scan size from well size and coverage
            if "glass slide" not in self.navigationViewer.sample:
                effective_well_size = self.get_effective_well_size()
                scan_size = round((coverage / 100) * effective_well_size, 3)
                self.stored_xy_params["Select Wells"]["scan_size"] = scan_size
            else:
                # For glass slide, use default scan size
                self.stored_xy_params["Select Wells"]["scan_size"] = 0.1

            self.stored_xy_params["Select Wells"]["scan_shape"] = (
                "Square" if self.scanCoordinates.format in ["384 well plate", "1536 well plate"] else "Circle"
            )

        # change scan size to single FOV if XY is checked and mode is "Current Position"
        if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position":
            self.entry_scan_size.setValue(0.1)

    def set_default_shape(self):
        if self.scanCoordinates.format in ["384 well plate", "1536 well plate"]:
            self.combobox_shape.setCurrentText("Square")
        # elif self.scanCoordinates.format in ["4 slide"]:
        #     self.combobox_shape.setCurrentText("Rectangle")
        elif self.scanCoordinates.format != 0:
            self.combobox_shape.setCurrentText("Circle")

    def get_effective_well_size(self):
        well_size = self.scanCoordinates.well_size_mm
        if self.combobox_shape.currentText() == "Circle":
            fov_size_mm = self.navigationViewer.camera.get_fov_size_mm() * self.objectiveStore.get_pixel_size_factor()
            return well_size + fov_size_mm * (1 + math.sqrt(2))
        return well_size

    def reset_coordinates(self):
        if self.combobox_xy_mode.currentText() == "Select Wells":
            self.update_scan_size_from_coverage()
        self.update_coordinates()

    def update_manual_shape(self, shapes_data_mm):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        if shapes_data_mm and len(shapes_data_mm) > 0:
            self.shapes_mm = shapes_data_mm
            self._log.debug(f"Manual ROIs updated with {len(self.shapes_mm)} shapes")
        else:
            self.shapes_mm = None
            self._log.debug("No valid shapes found, cleared manual ROIs")
        self.update_coordinates()

    def convert_pixel_to_mm(self, pixel_coords):
        # Convert pixel coordinates to millimeter coordinates
        mm_coords = pixel_coords * self.napariMosaicWidget.viewer_pixel_size_mm
        mm_coords += np.array(
            [self.napariMosaicWidget.top_left_coordinate[1], self.napariMosaicWidget.top_left_coordinate[0]]
        )
        return mm_coords

    def update_coverage_from_scan_size(self):
        if "glass slide" not in self.navigationViewer.sample:
            effective_well_size = self.get_effective_well_size()
            scan_size = self.entry_scan_size.value()
            coverage = round((scan_size / effective_well_size) * 100, 2)
            self.entry_well_coverage.blockSignals(True)
            self.entry_well_coverage.setValue(coverage)
            self.entry_well_coverage.blockSignals(False)
            self._log.debug(f"Coverage: {coverage}")

    def update_scan_size_from_coverage(self):
        effective_well_size = self.get_effective_well_size()
        coverage = self.entry_well_coverage.value()
        scan_size = round((coverage / 100) * effective_well_size, 3)
        self.entry_scan_size.setValue(scan_size)
        self._log.debug(f"Scan size: {scan_size}")

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)
        self._reset_reflection_af_reference()

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def goto_z_min(self):
        z_value_mm = self.entry_minZ.value() / 1000  # Convert from μm to mm
        self._move_z_to(z_value_mm)

    def goto_z_max(self):
        z_value_mm = self.entry_maxZ.value() / 1000  # Convert from μm to mm
        self._move_z_to(z_value_mm)

    def _move_z_to(self, z_mm: float):
        """Move Z axis."""
        self._stage_service.move_to(z_mm=z_mm)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)
            self._reset_reflection_af_reference()

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def _reset_reflection_af_reference(self):
        if self.checkbox_withReflectionAutofocus.isChecked():
            was_live = self.liveController.is_live
            if was_live:
                self.liveController.stop_live()
            if not self.multipointController.laserAutoFocusController.set_reference():
                error_dialog("Failed to set reference for reflection autofocus. Is the laser autofocus initialized?")
            if was_live:
                self.liveController.start_live()

    def init_z(self, z_pos_mm=None):
        # sets initial z range form the current z position used after startup of the GUI
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        self._log.debug(f"Init z-level wellplate: {self.entry_minZ.value()}")

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def update_coordinates(self):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # If XY is not checked, use current position instead of scan coordinates
        if not self.checkbox_xy.isChecked():
            self.set_coordinates_to_current_position()
            return

        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()

        if self.combobox_xy_mode.currentText() == "Manual":
            self.scanCoordinates.set_manual_coordinates(self.shapes_mm, overlap_percent)

        elif self.combobox_xy_mode.currentText() == "Current Position":
            pos = self.stage.get_pos()
            self.scanCoordinates.set_live_scan_coordinates(pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape)
        else:
            if self.scanCoordinates.has_regions():
                self.scanCoordinates.clear_regions()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)

    def update_well_coordinates(self, selected):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # If XY is not checked, use current position instead
        if not self.checkbox_xy.isChecked():
            self.set_coordinates_to_current_position()  # to-do: is it needed?
            return

        if self.combobox_xy_mode.currentText() != "Select Wells":
            return

        if selected:
            scan_size_mm = self.entry_scan_size.value()
            overlap_percent = self.entry_overlap.value()
            shape = self.combobox_shape.currentText()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)
        elif self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

    def update_live_coordinates(self, pos: squid.abc.Pos):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return
        # Don't update scan coordinates if we're navigating focus points. A temporary fix for focus map with glass slide.
        # This disables updating scanning grid when focus map is checked
        if self.focusMapWidget is not None and self.focusMapWidget.enabled:
            return
        # Don't update live coordinates if XY is not checked - coordinates should stay at current position
        if not self.checkbox_xy.isChecked():
            return

        x_mm = pos.x_mm
        y_mm = pos.y_mm
        # Check if x_mm or y_mm has changed
        position_changed = (x_mm != self._last_x_mm) or (y_mm != self._last_y_mm)
        if not position_changed or time.time() - self._last_update_time < 0.5:
            return
        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()
        self.scanCoordinates.set_live_scan_coordinates(x_mm, y_mm, scan_size_mm, overlap_percent, shape)
        self._last_update_time = time.time()
        self._last_x_mm = x_mm
        self._last_y_mm = y_mm

    def toggle_acquisition(self, pressed):
        self._log.debug(f"WellplateMultiPointWidget.toggle_acquisition, {pressed=}")
        if not self.base_path_is_set:
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
            return

        if not self.list_configurations.selectedItems():
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            # if XY is not checked, use current position
            if not self.checkbox_xy.isChecked():
                self.set_coordinates_to_current_position()

            self.scanCoordinates.sort_coordinates()

            if self.combobox_z_mode.currentText() == "Set Range":
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000  # Convert from μm to mm
                maxZ = self.entry_maxZ.value() / 1000  # Convert from μm to mm
                self.multipointController.set_z_range(minZ, maxZ)
                self._log.debug(f"Set z-range: ({minZ}, {maxZ})")
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                # Try to fit the surface
                if self.focusMapWidget.fit_surface():
                    # If fit successful, set the surface fitter in controller
                    self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                else:
                    QMessageBox.warning(self, "Warning", "Failed to fit focus surface")
                    self.btn_startAcquisition.setChecked(False)
                    return
            else:
                # If checkbox not checked, set surface fitter to None
                self.multipointController.set_focus_map(None)

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if not check_space_available_with_error_dialog(self.multipointController, self._log):
                self.btn_startAcquisition.setChecked(False)
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True
            self.btn_startAcquisition.setText("Stop\n Acquisition ")

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()

        else:
            # This must eventually propagate through and call our aquisition_is_finished, or else we'll be left
            # in an odd state.
            self.multipointController.request_abort_aquisition()

    def acquisition_is_finished(self):
        self._log.debug(
            f"In WellMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.disable_updating_focus_points_on_signal()
        self.reset_coordinates()
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.update_focus_point_display()
            self.focusMapWidget.enable_updating_focus_points_on_signal()
        self.setEnabled_all(True)
        self.toggle_coordinate_controls(self.has_loaded_coordinates)

    def setEnabled_all(self, enabled):
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

            if self.scanCoordinates.format == "glass slide":
                self.entry_well_coverage.setEnabled(False)

        # Restore scan controls visibility based on XY checkbox state
        if enabled:
            self.update_scan_control_ui()

            # Restore mode dropdown states based on their respective checkboxes
            self.combobox_xy_mode.setEnabled(self.checkbox_xy.isChecked())
            self.combobox_z_mode.setEnabled(self.checkbox_z.isChecked())

            # Restore Z controls based on Z mode
            if self.checkbox_z.isChecked() and self.combobox_z_mode.currentText() == "Set Range":
                # In Set Range mode, Nz should be disabled
                self.entry_NZ.setEnabled(False)

            # Restore coverage based on XY mode
            if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position":
                # In Current Position mode, coverage should be disabled (N/A)
                self.entry_well_coverage.setEnabled(False)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)
        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def toggle_coordinate_controls(self, has_coordinates: bool):
        """Toggle button text and control states based on whether coordinates are loaded"""
        if has_coordinates:
            self.btn_save_scan_coordinates.setText("Clear Coordinates")
            # Disable scan controls when coordinates are loaded
            self.combobox_shape.setEnabled(False)
            self.entry_scan_size.setEnabled(False)
            self.entry_well_coverage.setEnabled(False)
            self.entry_overlap.setEnabled(False)
            # Disable well selector
            if self.well_selection_widget is not None:
                self.well_selection_widget.setEnabled(False)
        else:
            self.btn_save_scan_coordinates.setText("Save Coordinates")
            # Re-enable scan controls when coordinates are cleared - use update_scan_control_ui for proper logic
            self.update_scan_control_ui()

        self.has_loaded_coordinates = has_coordinates

    def on_save_or_clear_coordinates_clicked(self):
        """Handle save/clear coordinates button click"""
        if self.has_loaded_coordinates:
            # Clear coordinates
            self.scanCoordinates.clear_regions()
            self.toggle_coordinate_controls(has_coordinates=False)
            # Update display/coordinates as needed
            self.update_coordinates()
        else:
            # Save coordinates (existing save functionality)
            self.save_coordinates()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"  # Default directory
        )

        if file_path:
            self._log.info(f"Loading coordinates from {file_path}")
            self.load_coordinates(file_path)

    def restore_cached_coordinates(self):
        """Restore previously loaded coordinates from cached dataframe"""
        if self.cached_loaded_coordinates_df is None:
            return

        df = self.cached_loaded_coordinates_df

        # Clear existing coordinates
        self.scanCoordinates.clear_regions()

        # Load coordinates into scanCoordinates from cached dataframe
        for region_id in df["region"].unique():
            region_points = df[df["region"] == region_id]
            coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
            self.scanCoordinates.region_fov_coordinates[region_id] = coords

            # Calculate and store region center (average of points)
            center_x = region_points["x (mm)"].mean()
            center_y = region_points["y (mm)"].mean()
            self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

            # Register FOVs with navigation viewer
            for x, y in coords:
                self.navigationViewer.register_fov_to_image(x, y)

        # Update text area to show loaded file path
        if self.cached_loaded_file_path:
            self.text_loaded_coordinates.setText(f"Loaded: {self.cached_loaded_file_path}")

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV
            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["region", "x (mm)", "y (mm)"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns")

            # Cache the dataframe and file path
            self.cached_loaded_coordinates_df = df.copy()
            self.cached_loaded_file_path = file_path

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["region"].unique():
                region_points = df[df["region"] == region_id]
                coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["x (mm)"].mean()
                center_y = region_points["y (mm)"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                self.navigationViewer.register_fovs_to_image(coords)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

            # Update text area to show loaded file path
            self.text_loaded_coordinates.setText(f"Loaded: {file_path}")

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def save_coordinates(self):
        """Save scan coordinates to a CSV file.

        Opens a file dialog for the user to specify a folder name and location.
        Coordinates are saved in CSV format with headers for each objective.
        """
        # Open file dialog for user to specify folder name and location
        folder_path, _ = QFileDialog.getSaveFileName(
            self, "Create Folder for Scan Coordinates", "", "Folder"  # Default directory
        )

        if folder_path:
            # Create the folder if it doesn't exist
            os.makedirs(folder_path, exist_ok=True)

            folder_name = os.path.basename(folder_path)

            current_objective = self.objectiveStore.current_objective

            def _helper_save_coordinates(self, file_path: str):
                # Get coordinates from scanCoordinates
                coordinates = []
                for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                    for x, y in fov_coords:
                        coordinates.append([region_id, x, y])

                # Save to CSV with headers

                df = pd.DataFrame(coordinates, columns=["region", "x (mm)", "y (mm)"])
                df.to_csv(file_path, index=False)

                self._log.info(f"Saved scan coordinates to {file_path}")

            try:
                for objective_name in self.objectiveStore.objectives_dict.keys():
                    if objective_name == current_objective:
                        continue
                    else:
                        self.objectiveStore.set_current_objective(objective_name)
                        self.update_coordinates()
                        obj_file_path = os.path.join(folder_path, f"{folder_name}_{objective_name}.csv")
                        _helper_save_coordinates(self, obj_file_path)

                self.objectiveStore.set_current_objective(current_objective)
                self.update_coordinates()
                obj_file_path = os.path.join(folder_path, f"{folder_name}_{current_objective}.csv")
                _helper_save_coordinates(self, obj_file_path)

            except Exception as e:
                self._log.error(f"Failed to save coordinates: {str(e)}")
                QMessageBox.warning(self, "Save Error", f"Failed to save coordinates to {folder_path}\nError: {str(e)}")


class MultiPointWithFluidicsWidget(QFrame):
    """A simplified version of WellplateMultiPointWidget for use with fluidics"""

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        channelConfigurationManager,
        scanCoordinates,
        napariMosaicWidget=None,
        stage_service: Optional["StageService"] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self._stage_service = stage_service
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.scanCoordinates = scanCoordinates
        if napariMosaicWidget is None:
            self.performance_mode = True
        else:
            self.napariMosaicWidget = napariMosaicWidget
            self.performance_mode = False

        self.base_path_is_set = False
        self.acquisition_start_time = None
        self.eta_seconds = 0
        self.nRound = 0
        self.is_current_acquisition_widget = False

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Z-stack controls
        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)

        # Channel configurations
        self.list_configurations = QListWidget()
        for microscope_configuration in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Reflection AF checkbox
        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        # Piezo checkbox
        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        # Start acquisition button
        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setEnabled(False)

        # Progress indicators
        self.progress_label = QLabel("Round -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Layout setup
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        exp_id_layout = QHBoxLayout()
        exp_id_layout.addWidget(QLabel("Experiment ID"))
        exp_id_layout.addWidget(self.lineEdit_experimentID)

        self.btn_load_coordinates = QPushButton("Load Coordinates")
        exp_id_layout.addWidget(self.btn_load_coordinates)

        self.btn_init_fluidics = QPushButton("Init Fluidics")
        # exp_id_layout.addWidget(self.btn_init_fluidics)

        main_layout.addLayout(exp_id_layout)

        # Z-stack controls
        z_stack_layout = QHBoxLayout()
        z_stack_layout.addWidget(QLabel("dz"))
        z_stack_layout.addWidget(self.entry_deltaZ)
        z_stack_layout.addWidget(QLabel("Nz"))
        z_stack_layout.addWidget(self.entry_NZ)

        # Rounds input
        z_stack_layout.addWidget(QLabel("Fluidics Rounds:"))
        self.entry_rounds = QLineEdit()
        z_stack_layout.addWidget(self.entry_rounds)

        main_layout.addLayout(z_stack_layout)

        # Grid layout for channel list and options
        grid = QGridLayout()

        # Channel configurations on left
        grid.addWidget(self.list_configurations, 0, 0)

        # Options layout
        options_layout = QVBoxLayout()
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)

        grid.addLayout(options_layout, 0, 2)

        # Start button on far right
        grid.addWidget(self.btn_startAcquisition, 0, 4)

        # Add spacers between columns
        spacer_widget1 = QWidget()
        spacer_widget1.setFixedWidth(2)
        grid.addWidget(spacer_widget1, 0, 1)

        spacer_widget2 = QWidget()
        spacer_widget2.setFixedWidth(2)
        grid.addWidget(spacer_widget2, 0, 3)

        # Set column stretches
        grid.setColumnStretch(0, 2)  # Channel list - half width
        grid.setColumnStretch(1, 0)  # First spacer
        grid.setColumnStretch(2, 1)  # Options
        grid.setColumnStretch(3, 0)  # Second spacer
        grid.setColumnStretch(4, 1)  # Start button

        main_layout.addLayout(grid)

        # Progress bar layout
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(progress_layout)

        # Connect signals
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.btn_load_coordinates.clicked.connect(self.on_load_coordinates_clicked)
        # self.btn_init_fluidics.clicked.connect(self.init_fluidics)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

    # The following methods are copied from WellplateMultiPointWidget with minimal modifications
    def toggle_acquisition(self, pressed):
        rounds = self.get_rounds()
        if pressed:
            if not self.base_path_is_set:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
                return

            if not self.list_configurations.selectedItems():
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
                return

            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            if not rounds:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please enter valid round numbers (1-24)")
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True
            self.btn_startAcquisition.setText("Stop\n Acquisition ")

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_use_fluidics(True)  # may be set to False from other widgets
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.set_Nt(len(rounds))
            self.multipointController.fluidics.set_rounds(rounds)
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()
        else:
            self.multipointController.request_abort_aquisition()

    def set_saving_dir(self):
        """Open dialog to set saving directory"""
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_deltaZ(self, value):
        """Set Z-stack step size, adjusting for piezo if needed"""
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        """Emit signal with list of selected channel names"""
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def acquisition_is_finished(self):
        """Handle acquisition completion"""
        self._log.debug(
            f"In MultiPointWithFluidicsWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled):
        """Enable/disable all widget controls"""
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.nRound

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = remaining_fovs / fov_per_second if fov_per_second > 0 else 0
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.nRound > 1:
            progress_parts.append(f"Round {current_time_point + 1}/{self.nRound}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        """Update the estimated time remaining display"""
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        """Show/hide progress tracking widgets"""
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Round 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self._log.info(f"Loading coordinates from {file_path}")
            self.load_coordinates(file_path)

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV
            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["region", "x (mm)", "y (mm)"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns")

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["region"].unique():
                region_points = df[df["region"] == region_id]
                coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["x (mm)"].mean()
                center_y = region_points["y (mm)"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                self.navigationViewer.register_fovs_to_image(coords)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def init_fluidics(self):
        """Initialize the fluidics system"""
        # self.multipointController.fluidics.initialize()
        self.btn_startAcquisition.setEnabled(True)

    def get_rounds(self) -> list:
        """Parse rounds input string into a list of round numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            rounds_str = self.entry_rounds.text().strip()
            if not rounds_str:
                return []

            rounds = []

            # Split by comma and process each part
            for part in rounds_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 24 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 24, and start must be <= end"
                        )
                    rounds.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 24:
                        raise ValueError(f"Invalid number {num}: Must be between 1 and 24")
                    rounds.append(num)

            self.nRound = len(rounds)

            return rounds

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception as e:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid round numbers (e.g., '1-3,5,7-10')")
            return []


