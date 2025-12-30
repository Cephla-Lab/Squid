# Flexible multi-point acquisition widget
import math
import time
from typing import Optional, List

import numpy as np
import pandas as pd

from squid.core.events import (
    EventBus,
    StagePositionChanged,
    MoveStageCommand,
    ObjectiveChanged,
    SetLaserAFReferenceCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetAcquisitionChannelsCommand,
    StartNewExperimentCommand,
    StartAcquisitionCommand,
    StopAcquisitionCommand,
    AcquisitionStateChanged,
    AcquisitionProgress,
    AcquisitionRegionProgress,
    LoadingPositionReached,
    ScanningPositionReached,
    ScanCoordinatesUpdated,
    ActiveAcquisitionTabChanged,
    ClearScanCoordinatesCommand,
    AddFlexibleRegionCommand,
    AddFlexibleRegionWithStepSizeCommand,
    RemoveScanCoordinateRegionCommand,
    RenameScanCoordinateRegionCommand,
    UpdateScanCoordinateRegionZCommand,
)

from qtpy.QtCore import Qt, QTimer
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
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QListWidget,
    QProgressBar,
    QSpacerItem,
    QShortcut,
)
from qtpy.QtGui import QIcon, QKeySequence

from _def import *
from squid.ui.widgets.base import error_dialog, check_space_available_with_error_dialog


class FlexibleMultiPointWidget(QFrame):
    def __init__(
        self,
        focusMapWidget,
        event_bus: EventBus,
        initial_channel_configs: List[str],
        z_ustep_per_mm: Optional[float] = None,
        initial_z_mm: float = 0.0,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.acquisition_start_time = None
        self.last_used_locations = None
        self.last_used_location_ids = None
        self._event_bus = event_bus
        self._z_ustep_per_mm = z_ustep_per_mm
        # Cache current position (updated via StagePositionChanged events)
        self._cached_x_mm = 0.0
        self._cached_y_mm = 0.0
        self._cached_z_mm = initial_z_mm
        self.focusMapWidget = focusMapWidget
        # Initial channel configurations (passed from GUI, will be updated via events)
        self._channel_configs = list(initial_channel_configs)
        self.base_path_is_set = False
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.use_overlap = USE_OVERLAP_FOR_FLEXIBLE

        # Cached acquisition state from events
        self._acquisition_in_progress = False
        self._acquisition_is_aborting = False
        self._active_experiment_id: Optional[str] = None

        self.add_components()
        self.setup_layout()
        self.setup_connections()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.is_current_acquisition_widget = False
        self.acquisition_in_place = False
        self._is_active_tab = False

        # Subscribe to EventBus for position updates and acquisition state
        self._event_bus.subscribe(StagePositionChanged, self._on_stage_position_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)
        self._event_bus.subscribe(AcquisitionStateChanged, self._on_acquisition_state_changed)
        self._event_bus.subscribe(AcquisitionProgress, self._on_acquisition_progress)
        self._event_bus.subscribe(AcquisitionRegionProgress, self._on_region_progress)
        self._event_bus.subscribe(LoadingPositionReached, self._on_loading_position_reached)
        self._event_bus.subscribe(ScanningPositionReached, self._on_scanning_position_reached)
        self._event_bus.subscribe(ScanCoordinatesUpdated, self._on_scan_coordinates_updated)
        self._event_bus.subscribe(ActiveAcquisitionTabChanged, self._on_active_tab_changed)

    def _on_active_tab_changed(self, event: ActiveAcquisitionTabChanged) -> None:
        self._is_active_tab = event.active_tab == "flexible"
        if not self._is_active_tab:
            return
        self.emit_selected_channels()
        try:
            self.update_fov_positions()
        except Exception:
            self._log.exception("Failed to update flexible regions on tab activation")

    def _on_scan_coordinates_updated(self, event: ScanCoordinatesUpdated) -> None:
        """Handle updates to scan coordinates (regions added/removed/cleared)."""
        # Log for debugging - can be extended to update UI elements
        self._log.debug(f"ScanCoordinates updated: {event.total_regions} regions, {event.total_fovs} FOVs")

    def _on_stage_position_changed(self, event: StagePositionChanged) -> None:
        """Cache stage position from EventBus."""
        self._cached_x_mm = event.x_mm
        self._cached_y_mm = event.y_mm
        self._cached_z_mm = event.z_mm

    def _on_objective_changed(self, _event: ObjectiveChanged) -> None:
        # Recompute FOV grid spacing for stored locations when objective changes.
        try:
            self.update_fov_positions()
        except Exception:
            self._log.exception("Failed to update flexible multipoint FOV positions on objective change")

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("assets/icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        # Publish default path via event
        self._event_bus.publish(SetAcquisitionPathCommand(base_path=DEFAULT_SAVING_PATH))
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()
        self.lineEdit_experimentID.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.lineEdit_experimentID.setFixedWidth(96)

        self.dropdown_location_list = QComboBox()
        self.dropdown_location_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
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

        # Channel configurations (populated from initial_channel_configs)
        self.list_configurations = QListWidget()
        self.list_configurations.addItems(self._channel_configs)
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(
            MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT
        )
        # Set initial autofocus flag via event
        self._event_bus.publish(SetAcquisitionParametersCommand(
            use_autofocus=MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT
        ))

        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(
            MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT
        )
        # Set initial reflection AF flag via event
        self._event_bus.publish(SetAcquisitionParametersCommand(
            use_reflection_af=MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT
        ))

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
        self.entry_minZ.setMinimum(
            SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000
        )  # Convert to μm
        self.entry_minZ.setMaximum(
            SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000
        )  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(
            self._cached_z_mm * 1000
        )  # Set to current position
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_minZ_button = QPushButton("Set")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setKeyboardTracking(False)
        self.entry_maxZ.setMinimum(
            SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000
        )  # Convert to μm
        self.entry_maxZ.setMaximum(
            SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000
        )  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(
            self._cached_z_mm * 1000
        )  # Set to current position
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_maxZ_button = QPushButton("Set")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(
            ["From Bottom (Z-min)", "From Center", "From Top (Z-max)"]
        )

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
        self.grid_location_list_line1.addLayout(
            temp3, 0, 0, 1, 6
        )  # Span across all columns except the last
        self.grid_location_list_line1.addWidget(
            self.btn_update_z, 0, 6, 1, 2
        )  # Align with other buttons

        self.grid_location_list_line2 = QGridLayout()
        # Make all buttons span 2 columns for consistent width
        self.grid_location_list_line2.addWidget(self.btn_add, 1, 0, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_remove, 1, 2, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_next, 1, 4, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_clear, 1, 6, 1, 2)

        self.grid_location_list_line3 = QGridLayout()
        self.grid_location_list_line3.addWidget(self.btn_import_locations, 2, 0, 1, 3)
        self.grid_location_list_line3.addWidget(self.btn_export_locations, 2, 3, 1, 3)
        self.grid_location_list_line3.addWidget(
            self.btn_show_table_location_list, 2, 6, 1, 2
        )

        # Create spacer items
        EDGE_SPACING = 4  # Adjust this value as needed
        edge_spacer = QSpacerItem(
            EDGE_SPACING, 0, QSizePolicy.Fixed, QSizePolicy.Minimum
        )

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

        self.grid_acquisition.addLayout(
            self.z_min_layout, 5, 0, 1, 4
        )  # hide this in toggle
        self.grid_acquisition.addLayout(
            self.z_max_layout, 5, 4, 1, 4
        )  # hide this in toggle

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
        self.grid_acquisition.setRowStretch(
            3, 1
        )  # Configuration/AF row - allow this to stretch
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
        self.entry_dt.valueChanged.connect(self._on_dt_changed)
        self.entry_NX.valueChanged.connect(self._on_nx_changed)
        self.entry_NY.valueChanged.connect(self._on_ny_changed)
        self.entry_NZ.valueChanged.connect(self._on_nz_changed)
        self.entry_Nt.valueChanged.connect(self._on_nt_changed)
        self.checkbox_genAFMap.toggled.connect(self._on_gen_af_map_toggled)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_withAutofocus.toggled.connect(self._on_autofocus_toggled)
        self.checkbox_withReflectionAutofocus.toggled.connect(self._on_reflection_af_toggled)
        self.checkbox_usePiezo.toggled.connect(self._on_use_piezo_toggled)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        # Note: acquisition_finished, signal_acquisition_progress, signal_region_progress
        # are now handled via EventBus subscriptions (see _on_acquisition_state_changed etc.)
        self.list_configurations.itemSelectionChanged.connect(
            self.emit_selected_channels
        )
        # self.combobox_z_stack.currentIndexChanged.connect(self.signal_z_stacking.emit)

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
        # Set initial piezo flag via event
        self._event_bus.publish(SetAcquisitionParametersCommand(
            use_piezo=self.checkbox_usePiezo.isChecked()
        ))

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
        current_z = self._cached_z_mm * 1000
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
            except Exception:
                pass
            # When Z-range is not specified, set Z-min and Z-max to current Z position
            current_z = self._cached_z_mm * 1000
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
            z_pos_mm = self._cached_z_mm

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
        z_value = self._cached_z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)
        self._reset_reflection_af_reference()

    def set_z_max(self):
        z_value = self._cached_z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)
            self._reset_reflection_af_reference()

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def _reset_reflection_af_reference(self):
        if self.checkbox_withReflectionAutofocus.isChecked():
            # Publish command - the result will come back via LaserAFReferenceSet event
            # For now we just publish and let the controller handle errors
            self._event_bus.publish(SetLaserAFReferenceCommand())

    def update_z(self):
        z_mm = self._cached_z_mm
        index = self.dropdown_location_list.currentIndex()
        self.location_list[index, 2] = z_mm
        self._event_bus.publish(
            UpdateScanCoordinateRegionZCommand(region_id=str(self.location_ids[index]), z_mm=z_mm)
        )
        location_str = f"x:{round(self.location_list[index, 0], 3)} mm  y:{round(self.location_list[index, 1], 3)} mm  z:{round(z_mm * 1000.0, 3)} μm"
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
                remaining_fovs / fov_per_second
                + (Nt - 1 - self.current_time_point) * dt
                if fov_per_second > 0
                else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(
        self, current_region, num_regions, current_time_point
    ):
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
            progress_parts.append(
                f"Time {current_time_point + 1}/{self.entry_Nt.value()}"
            )

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

        self._event_bus.publish(ClearScanCoordinatesCommand())

        for i, (x, y, z) in enumerate(self.location_list):
            region_id = self.location_ids[i]
            if self.use_overlap:
                self._event_bus.publish(
                    AddFlexibleRegionCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        overlap_percent=float(self.entry_overlap.value()),
                    )
                )
            else:
                self._event_bus.publish(
                    AddFlexibleRegionWithStepSizeCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        delta_x_mm=float(self.entry_deltaX.value()),
                        delta_y_mm=float(self.entry_deltaY.value()),
                    )
                )

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        elif self._z_ustep_per_mm is not None:
            # Use cached Z-axis config to quantize to valid step sizes
            mm_per_ustep = 1.0 / self._z_ustep_per_mm
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        else:
            # No Z config available, use value as-is
            deltaZ = value
        self.entry_deltaZ.setValue(deltaZ)
        self._event_bus.publish(SetAcquisitionParametersCommand(delta_z_um=deltaZ))

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self._event_bus.publish(SetAcquisitionPathCommand(base_path=save_dir_base))
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def emit_selected_channels(self):
        selected_channels = [
            item.text() for item in self.list_configurations.selectedItems()
        ]
        self._event_bus.publish(
            SetAcquisitionChannelsCommand(channel_names=list(selected_channels))
        )

    def toggle_acquisition(self, pressed: bool) -> None:
        self._log.debug(f"FlexibleMultiPointWidget.toggle_acquisition, {pressed=}")
        if not self.base_path_is_set:
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please choose base saving directory first")
            return
        if not self.list_configurations.selectedItems():  # no channel selected
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please select at least one imaging channel first")
            return
        if pressed:
            if self._acquisition_in_progress:
                self._log.warning(
                    "Acquisition in progress or aborting, cannot start another yet."
                )
                self.btn_startAcquisition.setChecked(False)
                return

            # add the current location to the location list if the list is empty
            if len(self.location_list) == 0:
                self.add_location()
                self.acquisition_in_place = True

            # Calculate z_range
            if self.checkbox_set_z_range.isChecked():
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000
                maxZ = self.entry_maxZ.value() / 1000
                z_range = (minZ, maxZ)
            else:
                z = self._cached_z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                z_range = (z, z + dz / 1000 * (Nz - 1))

            # Get focus map if needed
            focus_map = None
            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                focus_map = self.focusMapWidget.focusMap

            # Publish acquisition parameters via events
            self._event_bus.publish(SetAcquisitionParametersCommand(
                delta_z_um=self.entry_deltaZ.value(),
                n_z=self.entry_NZ.value(),
                delta_t_s=self.entry_dt.value(),
                n_t=self.entry_Nt.value(),
                use_piezo=self.checkbox_usePiezo.isChecked(),
                use_autofocus=self.checkbox_withAutofocus.isChecked(),
                use_reflection_af=self.checkbox_withReflectionAutofocus.isChecked(),
                use_fluidics=False,
                z_range=z_range,
                focus_map=focus_map,
            ))
            self._event_bus.publish(SetAcquisitionPathCommand(
                base_path=self.lineEdit_savingDir.text()
            ))
            self._event_bus.publish(SetAcquisitionChannelsCommand(
                channel_names=[item.text() for item in self.list_configurations.selectedItems()]
            ))
            self._event_bus.publish(StartNewExperimentCommand(
                experiment_id=self.lineEdit_experimentID.text()
            ))
            requested_id = self.lineEdit_experimentID.text().strip()
            self._active_experiment_id = requested_id or None

            # TODO: check_space_available_with_error_dialog needs to be refactored
            # to not require multipointController reference

            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.is_current_acquisition_widget = (
                True  # keep track of what widget started the acquisition
            )
            self.btn_startAcquisition.setText("Stop\n Acquisition ")
            self.setEnabled_all(False)

            # Start coordinate-based acquisition via event
            self._event_bus.publish(StartAcquisitionCommand(xy_mode="Current Position"))
        else:
            # This must eventually propagate through and call out acquisition_finished.
            self._event_bus.publish(StopAcquisitionCommand())

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
                    "x:"
                    + str(round(x, 3))
                    + "mm  y:"
                    + str(round(y, 3))
                    + "mm  z:"
                    + str(round(1000 * z, 1))
                    + "μm"
                )
                self.dropdown_location_list.addItem(location_str)
                self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                self.location_ids = np.append(self.location_ids, name)
                self.table_location_list.insertRow(self.table_location_list.rowCount())
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1,
                    0,
                    QTableWidgetItem(str(round(x, 3))),
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1,
                    1,
                    QTableWidgetItem(str(round(y, 3))),
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1,
                    2,
                    QTableWidgetItem(str(round(z * 1000, 1))),
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(name)
                )
                index = self.dropdown_location_list.count() - 1
                self.dropdown_location_list.setCurrentIndex(index)
                print(self.location_list)
            else:
                print("Duplicate values not added based on x and y.")
                # to-do: update z coordinate

    def add_location(self):
        # Get raw positions from cached values (updated via StagePositionChanged events)
        x = self._cached_x_mm
        y = self._cached_y_mm
        z = self._cached_z_mm
        region_id = f"R{len(self.location_ids)}"

        # Check for duplicates using rounded values for comparison
        if not np.any(
            np.all(self.location_list[:, :2] == [round(x, 3), round(y, 3)], axis=1)
        ):
            # Block signals to prevent triggering cell_was_changed
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Store actual values in location_list
            self.location_list = np.vstack((self.location_list, [[x, y, z]]))
            self.location_ids = np.append(self.location_ids, region_id)

            # Update both UI elements at the same time
            location_str = (
                f"x:{round(x, 3)} mm  y:{round(y, 3)} mm  z:{round(z * 1000, 1)} μm"
            )
            self.dropdown_location_list.addItem(location_str)
            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(
                row, 2, QTableWidgetItem(str(round(z * 1000, 1)))
            )
            self.table_location_list.setItem(row, 3, QTableWidgetItem(region_id))

            # Store actual values in region coordinates
            if self.use_overlap:
                self._event_bus.publish(
                    AddFlexibleRegionCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        overlap_percent=float(self.entry_overlap.value()),
                    )
                )
            else:
                self._event_bus.publish(
                    AddFlexibleRegionWithStepSizeCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        delta_x_mm=float(self.entry_deltaX.value()),
                        delta_y_mm=float(self.entry_deltaY.value()),
                    )
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

            self._event_bus.publish(RemoveScanCoordinateRegionCommand(region_id=str(region_id)))

            # Note: region reindexing must be done via RenameScanCoordinateRegionCommand.

            print(f"Remaining location IDs: {self.location_ids}")

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)

    def next(self):
        index = self.dropdown_location_list.currentIndex()
        # max_index = self.dropdown_location_list.count() - 1
        # index = min(index + 1, max_index)
        num_regions = self.dropdown_location_list.count()
        if num_regions <= 0:
            self._log.error(
                "Cannot move to next location, because there are no locations in the list"
            )
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
        self._event_bus.publish(ClearScanCoordinatesCommand())
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)

        self._log.info("Cleared all locations and overlays.")

    def clear_only_location_list(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)

    def go_to(self, index):
        if index != -1:
            if index < len(
                self.location_list
            ):  # to avoid giving errors when adding new points
                x = self.location_list[index, 0]
                y = self.location_list[index, 1]
                z = self.location_list[index, 2]
                self._move_stage_to(x, y, z)
                self.table_location_list.selectRow(index)

    def _move_stage_to(self, x: float, y: float, z: float) -> None:
        """Move stage to position via EventBus."""
        self._event_bus.publish(MoveStageCommand(x_mm=x, y_mm=y, z_mm=z))

    def cell_was_clicked(self, row, column):
        self.dropdown_location_list.setCurrentIndex(row)

    def cell_was_changed(self, row, column):
        # Get region ID
        region_id = self.location_ids[row]

        # Handle the changed value
        val_edit = self.table_location_list.item(row, column).text()

        if column < 2:  # X or Y coordinate changed
            self.location_list[row, column] = float(val_edit)
            x, y, z = self.location_list[row]

            # Update region coordinates and FOVs for new position
            if self.use_overlap:
                self._event_bus.publish(RemoveScanCoordinateRegionCommand(region_id=str(region_id)))
                self._event_bus.publish(
                    AddFlexibleRegionCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        overlap_percent=float(self.entry_overlap.value()),
                    )
                )
            else:
                self._event_bus.publish(RemoveScanCoordinateRegionCommand(region_id=str(region_id)))
                self._event_bus.publish(
                    AddFlexibleRegionWithStepSizeCommand(
                        region_id=str(region_id),
                        center_x_mm=float(x),
                        center_y_mm=float(y),
                        center_z_mm=float(z),
                        n_x=int(self.entry_NX.value()),
                        n_y=int(self.entry_NY.value()),
                        delta_x_mm=float(self.entry_deltaX.value()),
                        delta_y_mm=float(self.entry_deltaY.value()),
                    )
                )

        elif column == 2:  # Z coordinate changed
            z = float(val_edit) / 1000
            self.location_list[row, 2] = z
            self._event_bus.publish(UpdateScanCoordinateRegionZCommand(region_id=str(region_id), z_mm=float(z)))
        else:  # ID changed
            new_id = val_edit
            self.location_ids[row] = new_id
            self._event_bus.publish(
                RenameScanCoordinateRegionCommand(old_region_id=str(region_id), new_region_id=str(new_id))
            )

        # Update UI
        location_str = f"x:{round(self.location_list[row, 0], 3)} mm  y:{round(self.location_list[row, 1], 3)} mm  z:{round(1000 * self.location_list[row, 2], 3)} μm"
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
            self.table_location_list.setItem(
                index, 2, QTableWidgetItem(str(round(1000 * z_mm, 1)))
            )

        self.table_location_list.blockSignals(False)
        self.dropdown_location_list.blockSignals(False)

    def export_location_list(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Location List", "", "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            location_list_df = pd.DataFrame(
                self.location_list, columns=["x (mm)", "y (mm)", "z (mm)"]
            )
            location_list_df["ID"] = self.location_ids
            location_list_df["i"] = 0
            location_list_df["j"] = 0
            location_list_df["k"] = 0
            location_list_df.to_csv(file_path, index=False, header=True)

    def import_location_list(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Location List", "", "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            location_list_df = pd.read_csv(file_path)
            location_list_df_relevant = None
            try:
                location_list_df_relevant = location_list_df[
                    ["x (mm)", "y (mm)", "z (mm)"]
                ]
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
                    self.table_location_list.insertRow(
                        self.table_location_list.rowCount()
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1,
                        0,
                        QTableWidgetItem(str(round(x, 3))),
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1,
                        1,
                        QTableWidgetItem(str(round(y, 3))),
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1,
                        2,
                        QTableWidgetItem(str(round(1000 * z, 1))),
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1,
                        3,
                        QTableWidgetItem(region_id),
                    )
                    if self.use_overlap:
                        self._event_bus.publish(
                            AddFlexibleRegionCommand(
                                region_id=str(region_id),
                                center_x_mm=float(x),
                                center_y_mm=float(y),
                                center_z_mm=float(z),
                                n_x=int(self.entry_NX.value()),
                                n_y=int(self.entry_NY.value()),
                                overlap_percent=float(self.entry_overlap.value()),
                            )
                        )
                    else:
                        self._event_bus.publish(
                            AddFlexibleRegionWithStepSizeCommand(
                                region_id=str(region_id),
                                center_x_mm=float(x),
                                center_y_mm=float(y),
                                center_z_mm=float(z),
                                n_x=int(self.entry_NX.value()),
                                n_y=int(self.entry_NY.value()),
                                delta_x_mm=float(self.entry_deltaX.value()),
                                delta_y_mm=float(self.entry_deltaY.value()),
                            )
                        )
                else:
                    self._log.warning("Duplicate values not added based on x and y.")
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            self._log.debug(self.location_list)

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(
                self, "Warning", "Please select at least one imaging channel"
            )
            return

        # Set the selected channels and acquisition parameters via events
        self._event_bus.publish(SetAcquisitionChannelsCommand(
            channel_names=[item.text() for item in self.list_configurations.selectedItems()]
        ))

        z = self._cached_z_mm
        self._event_bus.publish(SetAcquisitionParametersCommand(
            delta_z_um=0,
            n_z=1,
            delta_t_s=0,
            n_t=1,
            use_piezo=False,
            use_autofocus=False,
            use_reflection_af=False,
            use_fluidics=False,
            z_range=(z, z),
        ))

        # Start the acquisition process for the single FOV
        experiment_id = "snapped images" + self.lineEdit_experimentID.text()
        self._active_experiment_id = experiment_id.strip() or None
        self._event_bus.publish(StartNewExperimentCommand(experiment_id=experiment_id))
        self._event_bus.publish(StartAcquisitionCommand(acquire_current_fov=True, xy_mode="Current Position"))

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

        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        self.setEnabled_all(True)
        self.is_current_acquisition_widget = False
        self._active_experiment_id = None

    def setEnabled_all(self, enabled: bool, exclude_btn_startAcquisition: bool = True):
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

    # =========================================================================
    # EventBus Handlers
    # =========================================================================

    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle acquisition state changes from EventBus."""
        if self._active_experiment_id and event.experiment_id != self._active_experiment_id:
            return
        self._acquisition_in_progress = event.in_progress
        self._acquisition_is_aborting = event.is_aborting

        if self.is_current_acquisition_widget:
            self.display_progress_bar(event.in_progress)

        if not event.in_progress:
            # Acquisition finished
            self.acquisition_is_finished()

    def _on_acquisition_progress(self, event: AcquisitionProgress) -> None:
        """Handle acquisition progress updates from EventBus."""
        if self._active_experiment_id and event.experiment_id != self._active_experiment_id:
            return
        if not self.is_current_acquisition_widget:
            return

        total = max(1, int(event.total_fovs))
        current = max(0, min(int(event.current_fov), total))
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

        parts = []
        if event.total_rounds > 1:
            parts.append(f"Region {event.current_round}/{event.total_rounds}")
        parts.append(f"Image {event.current_fov}/{event.total_fovs}")
        if event.current_channel:
            parts.append(str(event.current_channel))
        self.progress_label.setText("  ".join(parts))

        if event.eta_seconds is None or event.eta_seconds <= 0:
            self.eta_label.setText("--:--")
            return

        eta = int(event.eta_seconds)
        hours, remainder = divmod(eta, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            eta_str = f"{minutes:02d}:{seconds:02d}"
        self.eta_label.setText(eta_str)

    def _on_region_progress(self, event: AcquisitionRegionProgress) -> None:
        """Handle region progress updates from EventBus."""
        if self._active_experiment_id and event.experiment_id != self._active_experiment_id:
            return
        self.update_region_progress(event.current_region, event.total_regions)

    def _on_loading_position_reached(self, event: LoadingPositionReached) -> None:
        """Handle loading position reached - disable acquisition button."""
        self.disable_the_start_aquisition_button()

    def _on_scanning_position_reached(self, event: ScanningPositionReached) -> None:
        """Handle scanning position reached - enable acquisition button."""
        self.enable_the_start_aquisition_button()

    # =========================================================================
    # UI Event Handlers (publish commands)
    # =========================================================================

    def _on_dt_changed(self, value: float) -> None:
        """Handle dt spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(delta_t_s=value))

    def _on_nx_changed(self, value: int) -> None:
        """Handle NX spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(n_x=value))

    def _on_ny_changed(self, value: int) -> None:
        """Handle NY spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(n_y=value))

    def _on_nz_changed(self, value: int) -> None:
        """Handle NZ spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(n_z=value))

    def _on_nt_changed(self, value: int) -> None:
        """Handle Nt spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(n_t=value))

    def _on_gen_af_map_toggled(self, checked: bool) -> None:
        """Handle generate AF map checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(gen_focus_map=checked))

    def _on_autofocus_toggled(self, checked: bool) -> None:
        """Handle autofocus checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(use_autofocus=checked))

    def _on_reflection_af_toggled(self, checked: bool) -> None:
        """Handle reflection AF checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(use_reflection_af=checked))

    def _on_use_piezo_toggled(self, checked: bool) -> None:
        """Handle use piezo checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(use_piezo=checked))
