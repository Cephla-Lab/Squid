"""Unified Acquisition Setup tab.

Compact vertically-stacked panel: colored-border tab row, XY controls,
Z/Focus compact rows, channel list, options row, acquisition controls.
"""

import csv
import os
import tempfile
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
from qtpy.QtCore import Qt, QEventLoop, QTimer
from qtpy.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from _def import (
    Acquisition,
    SOFTWARE_POS_LIMIT,
    squid,
)
from squid.core.config.feature_flags import get_feature_flags
from squid.core.events import (
    AutofocusMode,
    AcquisitionProgress,
    AcquisitionStateChanged,
    ActiveAcquisitionTabChanged,
    AddFlexibleRegionCommand,
    BinningChanged,
    ChannelConfigurationsChanged,
    ClearManualShapesCommand,
    ClearScanCoordinatesCommand,
    LoadScanCoordinatesCommand,
    ManualShapeDrawingEnabledChanged,
    ManualShapesChanged,
    MosaicLayersCleared,
    MosaicLayersInitialized,
    ObjectiveChanged,
    RequestScanCoordinatesSnapshotCommand,
    ScanCoordinatesSnapshot,
    ScanCoordinatesUpdated,
    SetAcquisitionChannelsCommand,
    FocusLockSettings,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetManualScanCoordinatesCommand,
    SetWellSelectionScanCoordinatesCommand,
    StagePositionChanged,
    StartAcquisitionCommand,
    StartNewExperimentCommand,
    StopAcquisitionCommand,
    handles,
)
from squid.core.protocol.imaging_protocol import (
    FocusConfig,
    FocusLockConfig,
    ImagingProtocol,
    ZStackConfig,
)
from squid.core.utils.geometry_utils import calculate_scan_size_from_coverage, calculate_well_coverage
from squid.ui.widgets.acquisition.channel_order_widget import ChannelOrderWidget
from squid.ui.widgets.base import CollapsibleGroupBox, EventBusWidget

_FEATURE_FLAGS = get_feature_flags()
_log = squid.core.logging.get_logger(__name__)

_Z_DIRECTION_MAP = {0: "from_bottom", 1: "from_center", 2: "from_top"}
_Z_DIRECTION_REVERSE = {"from_bottom": 0, "from_center": 1, "from_top": 2}

_MODE_MULTIWELL = 0
_MODE_ROI_TILING = 1
_MODE_MULTIPOINT = 2
_MODE_LOAD_CSV = 3

_INACTIVE_TAB = "QFrame { border: 1px solid palette(mid); border-radius: 2px; }"
_XY_TAB_ACTIVE = "QFrame { border: 1px solid #FF8C00; border-radius: 2px; }"
_Z_TAB_ACTIVE = "QFrame { border: 1px solid palette(highlight); border-radius: 2px; }"
_FOCUS_TAB_ACTIVE = "QFrame { border: 1px solid #A020F0; border-radius: 2px; }"

_Z_CONTROLS_BG = """
    QFrame { background-color: rgba(0, 120, 215, 0.15); }
    QFrame QLabel { background-color: transparent; }
"""
_FOCUS_CONTROLS_BG = """
    QFrame { background-color: rgba(160, 32, 240, 0.15); }
    QFrame QLabel { background-color: transparent; }
"""


class AcquisitionSetupWidget(EventBusWidget):
    """Unified acquisition setup — vertically stacked, compact layout."""

    _ACQUISITION_START_WATCHDOG_MS = 6000

    def __init__(
        self,
        event_bus,
        initial_channel_configs: List[str],
        well_selection_widget=None,
        config_repo=None,
        initial_z_mm: float = 0.0,
        z_ustep_per_mm: Optional[float] = None,
        camera_fov_size_mm: float = 0.0,
        objective_pixel_size_factors: Optional[Dict[str, float]] = None,
    ):
        super().__init__(event_bus)
        self._config_repo = config_repo
        self._z_ustep_per_mm = z_ustep_per_mm
        self._camera_fov_size_mm = camera_fov_size_mm
        self._objective_pixel_size_factors = dict(objective_pixel_size_factors or {})
        self._channel_configs = list(initial_channel_configs)
        self._well_selection_widget = well_selection_widget
        # Start True — this is tab index 0, onTabChanged won't fire for initial tab
        self._is_active_tab = True

        self._cached_x_mm = 0.0
        self._cached_y_mm = 0.0
        self._cached_z_mm = initial_z_mm
        self._total_fovs = 0
        self._mosaic_initialized = False

        self._multipoint_positions: List[Tuple[str, float, float, float]] = []
        self._next_region_id = 1
        self._manual_shapes_mm = None

        self._is_acquiring = False
        self._active_experiment_id: Optional[str] = None
        self._start_pending_experiment_id: Optional[str] = None
        self._snapshot_request_id: Optional[str] = None
        self._snapshot_loop = None
        self._snapshot_result = None
        self._acquisition_start_watchdog = QTimer(self)
        self._acquisition_start_watchdog.setSingleShot(True)
        self._acquisition_start_watchdog.timeout.connect(self._on_acquisition_start_timeout)

        self._setup_ui()
        self._connect_signals()
        self._update_tab_styles()

    @staticmethod
    def _normalize_experiment_id(experiment_id: Optional[str]) -> Optional[str]:
        """Normalize IDs to match backend canonicalization (spaces -> underscores)."""
        if experiment_id is None:
            return None
        return experiment_id.strip().replace(" ", "_")

    # =========================================================================
    # UI — everything stacked vertically, no grid
    # =========================================================================

    def _setup_ui(self) -> None:
        from qtpy.QtWidgets import QScrollArea

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)
        scroll.setWidget(container)

        # 1. Tab row
        self._build_tab_row(vbox)
        # 2. XY controls (show/hide panels, no QStackedWidget)
        self._build_xy_controls(vbox)
        # 3. FOV count
        self._build_fov_row(vbox)
        # 4. Z dz/Nz row (or "not selected" placeholder)
        self._build_z_dz_row(vbox)
        # 5. Z range row (hidden by default)
        self._build_z_range_row(vbox)
        # 6. Focus controls row (hidden by default)
        self._build_focus_controls(vbox)
        # 7. Channel list + Acquisition controls (side by side)
        self._build_channel_and_acquisition(vbox)

        vbox.addStretch()

    def _build_tab_row(self, vbox: QVBoxLayout) -> None:
        tabs = QHBoxLayout()
        tabs.setSpacing(4)

        # XY tab
        self._xy_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(8, 4, 8, 4)
        self._xy_checkbox = QCheckBox("XY")
        self._xy_checkbox.setChecked(True)
        self._xy_mode_combo = QComboBox()
        self._xy_mode_combo.addItems(["Multiwell Tiled", "ROI Tiling", "Multipoint + Scan", "Load Coordinates"])
        # Disable ROI Tiling until mosaic has coordinate system
        self._xy_mode_combo.model().item(_MODE_ROI_TILING).setEnabled(False)
        self._xy_mode_combo.setItemData(
            _MODE_ROI_TILING, "Requires tile scan for coordinate reference", Qt.ToolTipRole
        )
        lay.addWidget(self._xy_checkbox)
        lay.addWidget(self._xy_mode_combo)
        self._xy_frame.setLayout(lay)
        tabs.addWidget(self._xy_frame, 2)

        # Z tab
        self._z_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(8, 4, 8, 4)
        self._z_checkbox = QCheckBox("Z")
        self._z_checkbox.setChecked(False)
        self._z_direction = QComboBox()
        self._z_direction.addItems(["From Bottom", "From Center", "From Top"])
        self._z_direction.setCurrentIndex(1)
        self._z_direction.setEnabled(False)
        lay.addWidget(self._z_checkbox)
        lay.addWidget(self._z_direction)
        self._z_frame.setLayout(lay)
        tabs.addWidget(self._z_frame, 1)

        # Focus tab
        self._focus_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(8, 4, 8, 4)
        self._focus_checkbox = QCheckBox("Focus")
        self._focus_checkbox.setChecked(False)
        self._focus_method = QComboBox()
        methods = ["Contrast AF", "Laser AF"]
        if _FEATURE_FLAGS.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            methods.append("Focus Lock")
        self._focus_method.addItems(methods)
        self._focus_method.setEnabled(False)
        lay.addWidget(self._focus_checkbox)
        lay.addWidget(self._focus_method)
        self._focus_frame.setLayout(lay)
        tabs.addWidget(self._focus_frame, 1)

        vbox.addLayout(tabs)

    def _build_xy_controls(self, vbox: QVBoxLayout) -> None:
        # Use show/hide panels instead of QStackedWidget so each panel
        # takes only its natural height (no reserving space for tallest page)
        self._xy_panels: List[QWidget] = []
        self._build_multiwell_panel(vbox)
        self._build_roi_panel(vbox)
        self._build_multipoint_panel(vbox)
        self._build_csv_panel(vbox)
        # Show only the first panel initially
        for i, p in enumerate(self._xy_panels):
            p.setVisible(i == 0)

    def _build_multiwell_panel(self, vbox: QVBoxLayout) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if self._well_selection_widget is not None:
            v.addWidget(self._well_selection_widget)
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("Shape"), 0, 0)
        self._well_scan_shape = QComboBox()
        self._well_scan_shape.addItems(["Square", "Circle", "Rectangle"])
        g.addWidget(self._well_scan_shape, 0, 1)
        g.addWidget(QLabel("Size"), 0, 2)
        self._well_scan_size = QDoubleSpinBox()
        self._well_scan_size.setRange(0.01, 50.0)
        self._well_scan_size.setDecimals(3)
        self._well_scan_size.setSingleStep(0.1)
        self._well_scan_size.setValue(1.0)
        self._well_scan_size.setSuffix(" mm")
        self._well_scan_size.setKeyboardTracking(False)
        g.addWidget(self._well_scan_size, 0, 3)
        g.addWidget(QLabel("Coverage"), 0, 4)
        self._well_coverage = QDoubleSpinBox()
        self._well_coverage.setRange(0.1, 100.0)
        self._well_coverage.setDecimals(1)
        self._well_coverage.setSingleStep(5.0)
        self._well_coverage.setValue(50.0)
        self._well_coverage.setSuffix(" %")
        self._well_coverage.setKeyboardTracking(False)
        g.addWidget(self._well_coverage, 0, 5)
        g.addWidget(QLabel("Overlap"), 1, 0)
        self._well_overlap = QDoubleSpinBox()
        self._well_overlap.setRange(0, 99)
        self._well_overlap.setDecimals(1)
        self._well_overlap.setSingleStep(5.0)
        self._well_overlap.setValue(10.0)
        self._well_overlap.setSuffix(" %")
        self._well_overlap.setKeyboardTracking(False)
        g.addWidget(self._well_overlap, 1, 1)
        v.addLayout(g)
        self._xy_panels.append(w)
        vbox.addWidget(w)

    def _build_roi_panel(self, vbox: QVBoxLayout) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        h1 = QHBoxLayout()
        self._btn_draw_rois = QPushButton("Draw ROIs in Mosaic")
        self._btn_draw_rois.setCheckable(True)
        h1.addWidget(self._btn_draw_rois)
        self._btn_clear_rois = QPushButton("Clear ROIs")
        h1.addWidget(self._btn_clear_rois)
        self._btn_generate_fovs = QPushButton("Generate FOVs")
        h1.addWidget(self._btn_generate_fovs)
        v.addLayout(h1)
        h2 = QHBoxLayout()
        self._roi_status_label = QLabel("0 ROIs, 0 FOVs")
        h2.addWidget(self._roi_status_label)
        h2.addStretch()
        h2.addWidget(QLabel("Overlap"))
        self._roi_overlap = QDoubleSpinBox()
        self._roi_overlap.setRange(0, 99)
        self._roi_overlap.setDecimals(1)
        self._roi_overlap.setSingleStep(5.0)
        self._roi_overlap.setValue(10.0)
        self._roi_overlap.setSuffix(" %")
        self._roi_overlap.setKeyboardTracking(False)
        h2.addWidget(self._roi_overlap)
        v.addLayout(h2)
        self._xy_panels.append(w)
        vbox.addWidget(w)

    def _build_multipoint_panel(self, vbox: QVBoxLayout) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self._mp_table = QTableWidget(0, 4)
        self._mp_table.setHorizontalHeaderLabels(["Region", "X (mm)", "Y (mm)", "Z (mm)"])
        self._mp_table.setMaximumHeight(120)
        v.addWidget(self._mp_table)
        h = QHBoxLayout()
        self._btn_mp_add = QPushButton("Add from Stage")
        h.addWidget(self._btn_mp_add)
        self._btn_mp_remove = QPushButton("Remove")
        h.addWidget(self._btn_mp_remove)
        self._btn_mp_clear = QPushButton("Clear")
        h.addWidget(self._btn_mp_clear)
        h.addWidget(QLabel("Nx"))
        self._mp_nx = QSpinBox()
        self._mp_nx.setRange(1, 50)
        self._mp_nx.setValue(1)
        self._mp_nx.setKeyboardTracking(False)
        h.addWidget(self._mp_nx)
        h.addWidget(QLabel("Ny"))
        self._mp_ny = QSpinBox()
        self._mp_ny.setRange(1, 50)
        self._mp_ny.setValue(1)
        self._mp_ny.setKeyboardTracking(False)
        h.addWidget(self._mp_ny)
        h.addWidget(QLabel("Overlap"))
        self._mp_overlap = QDoubleSpinBox()
        self._mp_overlap.setRange(0, 99)
        self._mp_overlap.setDecimals(1)
        self._mp_overlap.setSingleStep(5.0)
        self._mp_overlap.setValue(10.0)
        self._mp_overlap.setSuffix(" %")
        self._mp_overlap.setKeyboardTracking(False)
        h.addWidget(self._mp_overlap)
        v.addLayout(h)
        self._xy_panels.append(w)
        vbox.addWidget(w)

    def _build_csv_panel(self, vbox: QVBoxLayout) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        h = QHBoxLayout()
        self._btn_load_csv = QPushButton("Load CSV")
        h.addWidget(self._btn_load_csv)
        self._csv_filename_label = QLabel("(no file loaded)")
        self._csv_filename_label.setStyleSheet("color: palette(disabled-text);")
        h.addWidget(self._csv_filename_label, 1)
        v.addLayout(h)
        self._csv_status_label = QLabel("")
        v.addWidget(self._csv_status_label)
        self._xy_panels.append(w)
        vbox.addWidget(w)

    def _build_fov_row(self, vbox: QVBoxLayout) -> None:
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        self._fov_count_label = QLabel("0 FOVs")
        self._fov_count_label.setStyleSheet("font-weight: bold;")
        h.addWidget(self._fov_count_label)
        h.addStretch()
        self._btn_clear_fovs = QPushButton("Clear FOVs")
        h.addWidget(self._btn_clear_fovs)
        self._btn_save_coords = QPushButton("Save Coordinates CSV")
        h.addWidget(self._btn_save_coords)
        vbox.addLayout(h)

    def _build_z_dz_row(self, vbox: QVBoxLayout) -> None:
        # dz/Nz frame — compact single row, blue bg when Z checked
        self._z_dz_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(QLabel("dz"))
        self._z_delta = QDoubleSpinBox()
        self._z_delta.setRange(0.01, 1000)
        self._z_delta.setSingleStep(0.1)
        self._z_delta.setValue(Acquisition.DZ)
        self._z_delta.setDecimals(3)
        self._z_delta.setSuffix(" \u03bcm")
        self._z_delta.setKeyboardTracking(False)
        self._z_delta.setEnabled(False)
        lay.addWidget(self._z_delta)
        lay.addWidget(QLabel("Nz"))
        self._z_nz = QSpinBox()
        self._z_nz.setRange(1, 2000)
        self._z_nz.setValue(1)
        self._z_nz.setKeyboardTracking(False)
        self._z_nz.setEnabled(False)
        lay.addWidget(self._z_nz)
        self._z_dz_frame.setLayout(lay)
        self._z_dz_frame.setVisible(False)  # Z starts unchecked
        vbox.addWidget(self._z_dz_frame)

        # "Z-stack not selected" placeholder (same spot, shown when Z unchecked)
        self._z_not_selected_label = QLabel("Z-stack not selected")
        self._z_not_selected_label.setAlignment(Qt.AlignCenter)
        self._z_not_selected_label.setStyleSheet(
            "QLabel { background-color: palette(button); border: 1px solid palette(mid);"
            " border-radius: 4px; padding: 2px; color: palette(text); }"
        )
        self._z_not_selected_label.setFixedHeight(30)
        vbox.addWidget(self._z_not_selected_label)

    def _build_z_range_row(self, vbox: QVBoxLayout) -> None:
        self._z_range_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(4, 2, 4, 2)
        self._z_range_enable = QCheckBox("Z-range")
        self._z_range_enable.setEnabled(False)
        lay.addWidget(self._z_range_enable)
        self._btn_set_zmin = QPushButton("Set Z-min")
        lay.addWidget(self._btn_set_zmin)
        self._z_min = QDoubleSpinBox()
        self._z_min.setRange(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000, SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)
        self._z_min.setSingleStep(1)
        self._z_min.setValue(self._cached_z_mm * 1000)
        self._z_min.setSuffix(" \u03bcm")
        self._z_min.setKeyboardTracking(False)
        lay.addWidget(self._z_min)
        self._btn_set_zmax = QPushButton("Set Z-max")
        lay.addWidget(self._btn_set_zmax)
        self._z_max = QDoubleSpinBox()
        self._z_max.setRange(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000, SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)
        self._z_max.setSingleStep(1)
        self._z_max.setValue(self._cached_z_mm * 1000)
        self._z_max.setSuffix(" \u03bcm")
        self._z_max.setKeyboardTracking(False)
        lay.addWidget(self._z_max)
        self._z_range_frame.setLayout(lay)
        self._z_range_frame.setVisible(False)
        vbox.addWidget(self._z_range_frame)

    def _build_focus_controls(self, vbox: QVBoxLayout) -> None:
        self._focus_controls_frame = QFrame()
        lay = QHBoxLayout()
        lay.setContentsMargins(4, 2, 4, 2)

        self._focus_stack = QStackedWidget()
        self._focus_stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        # 0: Contrast AF
        p = QWidget()
        h = QHBoxLayout(p)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Contrast AF every"))
        self._contrast_af_interval = QSpinBox()
        self._contrast_af_interval.setRange(1, 1000)
        self._contrast_af_interval.setValue(3)
        self._contrast_af_interval.setSuffix(" FOVs")
        h.addWidget(self._contrast_af_interval)
        h.addStretch()
        self._focus_stack.addWidget(p)

        # 1: Laser AF
        p = QWidget()
        h = QHBoxLayout(p)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Laser AF every"))
        self._laser_af_interval = QSpinBox()
        self._laser_af_interval.setRange(1, 1000)
        self._laser_af_interval.setValue(3)
        self._laser_af_interval.setSuffix(" FOVs")
        h.addWidget(self._laser_af_interval)
        h.addStretch()
        self._focus_stack.addWidget(p)

        # 2: Focus Lock
        if _FEATURE_FLAGS.is_enabled("SUPPORT_LASER_AUTOFOCUS"):
            p = QWidget()
            h = QHBoxLayout(p)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel("Tolerance"))
            self._fl_tolerance = QDoubleSpinBox()
            self._fl_tolerance.setRange(0.01, 5.0)
            self._fl_tolerance.setValue(0.25)
            self._fl_tolerance.setDecimals(2)
            self._fl_tolerance.setSuffix(" \u03bcm")
            h.addWidget(self._fl_tolerance)
            h.addWidget(QLabel("Buffer"))
            self._fl_buffer = QSpinBox()
            self._fl_buffer.setRange(1, 20)
            self._fl_buffer.setValue(5)
            h.addWidget(self._fl_buffer)
            h.addWidget(QLabel("Retries"))
            self._fl_retries = QSpinBox()
            self._fl_retries.setRange(1, 10)
            self._fl_retries.setValue(3)
            h.addWidget(self._fl_retries)
            self._fl_auto_recover = QCheckBox("Auto-recover")
            self._fl_auto_recover.setChecked(True)
            h.addWidget(self._fl_auto_recover)
            h.addStretch()
            self._focus_stack.addWidget(p)

        lay.addWidget(self._focus_stack)
        self._focus_controls_frame.setLayout(lay)
        self._focus_controls_frame.setVisible(False)
        vbox.addWidget(self._focus_controls_frame)

    def _build_channel_and_acquisition(self, vbox: QVBoxLayout) -> None:
        # --- Horizontal split: channels (left) | acquisition (right) ---
        hbox = QHBoxLayout()
        hbox.setSpacing(8)

        # Left column: channel list
        left = QVBoxLayout()
        left.setSpacing(4)
        self._channel_order_widget = ChannelOrderWidget(initial_channels=self._channel_configs)
        left.addWidget(self._channel_order_widget)
        hbox.addLayout(left, 1)

        # Right column: acquisition controls
        right = QVBoxLayout()
        right.setSpacing(4)

        # Quick Scan collapsible section
        self._quick_scan_group = CollapsibleGroupBox("Quick Scan", collapsed=True)
        qs = self._quick_scan_group.content

        grid_row = QHBoxLayout()
        grid_row.setContentsMargins(0, 4, 0, 4)
        grid_row.addWidget(QLabel("Nx"))
        self._qs_nx = QSpinBox()
        self._qs_nx.setRange(1, 50)
        self._qs_nx.setValue(1)
        self._qs_nx.setKeyboardTracking(False)
        grid_row.addWidget(self._qs_nx)
        grid_row.addWidget(QLabel("Ny"))
        self._qs_ny = QSpinBox()
        self._qs_ny.setRange(1, 50)
        self._qs_ny.setValue(1)
        self._qs_ny.setKeyboardTracking(False)
        grid_row.addWidget(self._qs_ny)
        grid_row.addWidget(QLabel("Overlap"))
        self._qs_overlap = QDoubleSpinBox()
        self._qs_overlap.setRange(0, 99)
        self._qs_overlap.setDecimals(1)
        self._qs_overlap.setSingleStep(5.0)
        self._qs_overlap.setValue(10.0)
        self._qs_overlap.setSuffix(" %")
        self._qs_overlap.setKeyboardTracking(False)
        grid_row.addWidget(self._qs_overlap)
        qs.addLayout(grid_row)

        self._btn_quick_scan = QPushButton("Quick Scan")
        self._btn_quick_scan.setMinimumHeight(32)
        self._btn_quick_scan.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:disabled { background-color: #a5d6a7; color: #e0e0e0; }"
        )
        self._btn_quick_scan.setToolTip(
            "Run a mosaic quick scan (Nx by Ny) at current position with selected channels. Single z plane. No files saved."
        )
        qs.addWidget(self._btn_quick_scan)
        right.addWidget(self._quick_scan_group)

        # Save path row
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        self._save_path_edit = QLineEdit()
        self._save_path_edit.setPlaceholderText("Experiment folder...")
        h.addWidget(self._save_path_edit, 1)
        self._btn_browse_path = QPushButton("Browse")
        h.addWidget(self._btn_browse_path)
        right.addLayout(h)

        # Experiment ID
        from datetime import datetime

        self._experiment_id_edit = QLineEdit()
        self._experiment_id_edit.setPlaceholderText("Experiment ID")
        self._experiment_id_edit.setText(datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
        right.addWidget(self._experiment_id_edit)

        # Options row: format, skip saving
        opts = QHBoxLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        opts.addWidget(QLabel("Format:"))
        self._save_format = QComboBox()
        self._save_format.addItems(["OME-TIFF", "TIFF", "Zarr V3"])
        opts.addWidget(self._save_format)
        self._skip_saving = QCheckBox("Skip Saving")
        opts.addWidget(self._skip_saving)
        opts.addStretch()
        right.addLayout(opts)

        # Start/Stop button
        self._btn_start_stop = QPushButton("Start Acquisition")
        self._btn_start_stop.setCheckable(True)
        self._btn_start_stop.setMinimumHeight(32)
        right.addWidget(self._btn_start_stop)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("")
        self._progress_bar.setTextVisible(True)
        right.addWidget(self._progress_bar)

        # Progress label
        self._progress_label = QLabel("Ready")
        self._progress_label.setStyleSheet("color: gray; font-size: 11px;")
        right.addWidget(self._progress_label)

        right.addStretch()
        hbox.addLayout(right, 1)
        vbox.addLayout(hbox)

        # Protocol save/load row (full width below)
        proto = QHBoxLayout()
        proto.setContentsMargins(0, 0, 0, 0)
        self._protocol_name = QLineEdit()
        self._protocol_name.setPlaceholderText("Protocol name")
        proto.addWidget(self._protocol_name, 1)
        self._btn_save_protocol = QPushButton("Save Proto")
        proto.addWidget(self._btn_save_protocol)
        self._btn_load_protocol = QPushButton("Load Proto")
        proto.addWidget(self._btn_load_protocol)
        vbox.addLayout(proto)


    # =========================================================================
    # Styles
    # =========================================================================

    def _update_tab_styles(self) -> None:
        xy = self._xy_checkbox.isChecked()
        self._xy_frame.setStyleSheet(_XY_TAB_ACTIVE if xy else _INACTIVE_TAB)

        z = self._z_checkbox.isChecked()
        self._z_frame.setStyleSheet(_Z_TAB_ACTIVE if z else _INACTIVE_TAB)
        self._z_dz_frame.setStyleSheet(_Z_CONTROLS_BG if z else "")
        self._z_range_frame.setStyleSheet(_Z_CONTROLS_BG if z else "")

        f = self._focus_checkbox.isChecked()
        self._focus_frame.setStyleSheet(_FOCUS_TAB_ACTIVE if f else _INACTIVE_TAB)
        self._focus_controls_frame.setStyleSheet(_FOCUS_CONTROLS_BG if f else "")

    # =========================================================================
    # Connections
    # =========================================================================

    def _connect_signals(self) -> None:
        self._xy_checkbox.toggled.connect(self._on_xy_toggled)
        self._z_checkbox.toggled.connect(self._on_z_toggled)
        self._focus_checkbox.toggled.connect(self._on_focus_toggled)
        self._xy_mode_combo.currentIndexChanged.connect(self._on_xy_mode_changed)
        self._well_scan_size.valueChanged.connect(self._on_well_scan_size_changed)
        self._well_coverage.valueChanged.connect(self._on_well_coverage_changed)
        self._well_scan_shape.currentIndexChanged.connect(self._on_well_params_changed)
        self._well_overlap.valueChanged.connect(self._on_well_params_changed)
        self._btn_draw_rois.toggled.connect(self._on_draw_rois_toggled)
        self._btn_clear_rois.clicked.connect(self._on_clear_rois)
        self._btn_generate_fovs.clicked.connect(self._on_generate_fovs)
        self._btn_mp_add.clicked.connect(self._on_mp_add)
        self._btn_mp_remove.clicked.connect(self._on_mp_remove)
        self._btn_mp_clear.clicked.connect(self._on_mp_clear)
        self._mp_nx.valueChanged.connect(self._on_mp_params_changed)
        self._mp_ny.valueChanged.connect(self._on_mp_params_changed)
        self._mp_overlap.valueChanged.connect(self._on_mp_params_changed)
        self._btn_load_csv.clicked.connect(self._on_load_csv)
        self._btn_save_coords.clicked.connect(self._on_save_coords)
        self._z_range_enable.toggled.connect(self._on_z_range_toggled)
        self._btn_set_zmin.clicked.connect(lambda: self._z_min.setValue(self._cached_z_mm * 1000))
        self._btn_set_zmax.clicked.connect(lambda: self._z_max.setValue(self._cached_z_mm * 1000))
        self._z_min.valueChanged.connect(self._on_z_range_value_changed)
        self._z_max.valueChanged.connect(self._on_z_range_value_changed)
        self._focus_method.currentIndexChanged.connect(self._focus_stack.setCurrentIndex)
        self._skip_saving.toggled.connect(self._on_skip_saving_toggled)
        self._btn_save_protocol.clicked.connect(self._on_save_protocol)
        self._btn_load_protocol.clicked.connect(self._on_load_protocol)
        self._btn_clear_fovs.clicked.connect(self._on_clear_fovs)
        self._btn_browse_path.clicked.connect(self._on_browse_save_path)
        self._btn_start_stop.clicked.connect(self._on_start_stop_acquisition)
        self._btn_quick_scan.clicked.connect(self._quick_scan)

    # =========================================================================
    # Tab toggles
    # =========================================================================

    def _on_xy_toggled(self, checked: bool) -> None:
        self._xy_mode_combo.setEnabled(checked)
        idx = self._xy_mode_combo.currentIndex()
        for i, p in enumerate(self._xy_panels):
            p.setVisible(checked and i == idx)
        if not checked:
            self._publish(ClearScanCoordinatesCommand())
            if self._btn_draw_rois.isChecked():
                self._btn_draw_rois.setChecked(False)
        self._update_tab_styles()

    def _on_z_toggled(self, checked: bool) -> None:
        self._z_delta.setEnabled(checked)
        self._z_nz.setEnabled(checked)
        self._z_direction.setEnabled(checked)
        self._z_range_enable.setEnabled(checked)
        if not checked:
            self._z_nz.setValue(1)
            self._z_range_enable.setChecked(False)
        self._z_dz_frame.setVisible(checked)
        self._z_not_selected_label.setVisible(not checked)
        if not checked:
            self._z_range_frame.setVisible(False)
        self._update_tab_styles()

    def _on_focus_toggled(self, checked: bool) -> None:
        self._focus_method.setEnabled(checked)
        self._focus_controls_frame.setVisible(checked)
        self._update_tab_styles()

    # =========================================================================
    # XY mode handlers
    # =========================================================================

    def _on_xy_mode_changed(self, index: int) -> None:
        old_index = next((i for i, p in enumerate(self._xy_panels) if not p.isHidden()), 0)
        for i, p in enumerate(self._xy_panels):
            p.setVisible(i == index)
        self._publish(ClearScanCoordinatesCommand())

        if old_index == _MODE_ROI_TILING and index != _MODE_ROI_TILING:
            self._btn_draw_rois.setChecked(False)
            self._publish(ManualShapeDrawingEnabledChanged(enabled=False))

        if index == _MODE_ROI_TILING and self._xy_checkbox.isChecked():
            self._publish(ManualShapeDrawingEnabledChanged(enabled=True))

    # Well scan
    def _on_well_scan_size_changed(self, value: float) -> None:
        well_size = self._get_well_size_mm()
        if well_size > 0 and self._camera_fov_size_mm > 0:
            cov = calculate_well_coverage(
                value, self._camera_fov_size_mm, self._well_overlap.value(),
                self._well_scan_shape.currentText(), well_size,
            )
            self._well_coverage.blockSignals(True)
            self._well_coverage.setValue(cov)
            self._well_coverage.blockSignals(False)
        self._publish_well_scan_command()

    def _on_well_coverage_changed(self, value: float) -> None:
        well_size = self._get_well_size_mm()
        if well_size > 0 and self._camera_fov_size_mm > 0:
            size = calculate_scan_size_from_coverage(
                value, self._camera_fov_size_mm, self._well_overlap.value(),
                self._well_scan_shape.currentText(), well_size,
            )
            self._well_scan_size.blockSignals(True)
            self._well_scan_size.setValue(size)
            self._well_scan_size.blockSignals(False)
        self._publish_well_scan_command()

    def _on_well_params_changed(self) -> None:
        self._on_well_scan_size_changed(self._well_scan_size.value())

    def _publish_well_scan_command(self) -> None:
        if self._xy_mode_combo.currentIndex() != _MODE_MULTIWELL:
            return
        self._publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=self._well_scan_size.value(),
            overlap_percent=self._well_overlap.value(),
            shape=self._well_scan_shape.currentText(),
        ))

    def _get_well_size_mm(self) -> float:
        if self._well_selection_widget is not None:
            return getattr(self._well_selection_widget, "well_size_mm", 0.0)
        return 0.0

    # ROI Tiling
    def _on_draw_rois_toggled(self, checked: bool) -> None:
        self._publish(ManualShapeDrawingEnabledChanged(enabled=checked))

    def _on_clear_rois(self) -> None:
        self._manual_shapes_mm = None
        self._roi_status_label.setText("0 ROIs, 0 FOVs")
        self._publish(ClearManualShapesCommand())
        self._publish(ClearScanCoordinatesCommand())

    def _on_generate_fovs(self) -> None:
        if self._manual_shapes_mm is None:
            _log.warning("Generate FOVs: no shapes stored (_manual_shapes_mm is None)")
            return
        if len(self._manual_shapes_mm) == 0:
            _log.warning("Generate FOVs: shapes list is empty")
            return
        shapes_tuples = tuple(
            tuple(tuple(map(float, xy)) for xy in shape)
            for shape in self._manual_shapes_mm
        )
        _log.info(
            f"Generate FOVs: publishing {len(shapes_tuples)} shapes, "
            f"overlap={self._roi_overlap.value()}%"
        )
        # Clear any displayed FOVs first (including completed quick-scan boxes),
        # then regenerate from current manual ROI shapes.
        self._publish(ClearScanCoordinatesCommand(clear_displayed_fovs=True))
        self._publish(SetManualScanCoordinatesCommand(
            manual_shapes_mm=shapes_tuples,
            overlap_percent=self._roi_overlap.value(),
        ))

    # Multipoint
    def _on_mp_add(self) -> None:
        rid = str(self._next_region_id)
        self._next_region_id += 1
        self._multipoint_positions.append((rid, self._cached_x_mm, self._cached_y_mm, self._cached_z_mm))
        self._update_mp_table()
        self._publish_multipoint_regions()

    def _on_mp_remove(self) -> None:
        row = self._mp_table.currentRow()
        if 0 <= row < len(self._multipoint_positions):
            self._multipoint_positions.pop(row)
            self._update_mp_table()
            self._publish_multipoint_regions()

    def _on_mp_clear(self) -> None:
        self._multipoint_positions.clear()
        self._update_mp_table()
        self._publish(ClearScanCoordinatesCommand())

    def _on_mp_params_changed(self) -> None:
        self._publish_multipoint_regions()

    def _update_mp_table(self) -> None:
        self._mp_table.setRowCount(len(self._multipoint_positions))
        for i, (rid, x, y, z) in enumerate(self._multipoint_positions):
            self._mp_table.setItem(i, 0, QTableWidgetItem(rid))
            self._mp_table.setItem(i, 1, QTableWidgetItem(f"{x:.3f}"))
            self._mp_table.setItem(i, 2, QTableWidgetItem(f"{y:.3f}"))
            self._mp_table.setItem(i, 3, QTableWidgetItem(f"{z:.4f}"))

    def _publish_multipoint_regions(self) -> None:
        if self._xy_mode_combo.currentIndex() != _MODE_MULTIPOINT:
            return
        self._publish(ClearScanCoordinatesCommand())
        for rid, x, y, z in self._multipoint_positions:
            self._publish(AddFlexibleRegionCommand(
                region_id=rid, center_x_mm=x, center_y_mm=y, center_z_mm=z,
                n_x=self._mp_nx.value(), n_y=self._mp_ny.value(),
                overlap_percent=self._mp_overlap.value(),
            ))

    # Load CSV
    def _on_load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Coordinates CSV", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        self._csv_filename_label.setText(os.path.basename(path))
        region_fov_coordinates: Dict[str, list] = {}
        try:
            with open(path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    region = row.get("region", row.get("Region", "0"))
                    x = float(row.get("x", row.get("X", row.get("x_mm", 0))))
                    y = float(row.get("y", row.get("Y", row.get("y_mm", 0))))
                    z = float(row.get("z", row.get("Z", row.get("z_mm", 0))))
                    if region not in region_fov_coordinates:
                        region_fov_coordinates[region] = []
                    region_fov_coordinates[region].append((x, y, z))
            coord_tuples = {rid: tuple(tuple(pt) for pt in pts) for rid, pts in region_fov_coordinates.items()}
            total = sum(len(pts) for pts in region_fov_coordinates.values())
            self._csv_status_label.setText(f"{len(region_fov_coordinates)} regions, {total} FOVs loaded")
            self._publish(LoadScanCoordinatesCommand(region_fov_coordinates=coord_tuples))
        except Exception as e:
            _log.exception("Failed to load coordinates CSV")
            QMessageBox.warning(self, "Load Error", f"Failed to load CSV: {e}")

    def _on_save_coords(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Coordinates CSV", "", "CSV files (*.csv)")
        if not path:
            return
        snapshot = self._request_scan_coordinates_snapshot()
        if snapshot is None:
            QMessageBox.warning(self, "Snapshot Failed", "Could not retrieve scan coordinates. Is a scan configured?")
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["region", "x_mm", "y_mm", "z_mm"])
                for region_id, fovs in snapshot.region_fov_coordinates.items():
                    for fov in fovs:
                        x, y = fov[0], fov[1]
                        z = fov[2] if len(fov) > 2 else self._cached_z_mm
                        writer.writerow([region_id, f"{x:.6f}", f"{y:.6f}", f"{z:.6f}"])
            _log.info(f"Saved coordinates to {path}")
        except Exception as e:
            _log.exception("Failed to save coordinates CSV")
            QMessageBox.warning(self, "Save Error", f"Failed to save CSV: {e}")

    # =========================================================================
    # Acquisition controls
    # =========================================================================

    def _on_browse_save_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Experiment Folder")
        if path:
            self._save_path_edit.setText(path)

    def _on_start_stop_acquisition(self) -> None:
        if not self._is_acquiring and self._start_pending_experiment_id is None:
            self._start_acquisition()
        else:
            self._btn_start_stop.setText("Stopping...")
            self._btn_start_stop.setChecked(True)
            self._publish(StopAcquisitionCommand())

    def _start_acquisition(self) -> None:
        # Validate
        selected = self._channel_order_widget.get_selected_channels_ordered()
        if not selected:
            QMessageBox.warning(self, "No Channels", "Select at least one channel.")
            self._btn_start_stop.setChecked(False)
            return
        save_path = self._save_path_edit.text().strip()
        if not save_path and not self._skip_saving.isChecked():
            QMessageBox.warning(self, "No Save Path", "Set a save path or enable 'Skip Saving'.")
            self._btn_start_stop.setChecked(False)
            return

        experiment_id = self._experiment_id_edit.text().strip()
        if not experiment_id:
            from datetime import datetime

            experiment_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._experiment_id_edit.setText(experiment_id)

        # Map XY mode to MultiPointController's expected string
        mode_map = {
            _MODE_MULTIWELL: "Select Wells",
            _MODE_ROI_TILING: "Manual",
            _MODE_MULTIPOINT: "Manual",
            _MODE_LOAD_CSV: "Load Coordinates",
        }
        xy_mode = mode_map.get(self._xy_mode_combo.currentIndex(), "Manual")

        # Z-stacking config
        z_stacking_config = self._z_direction.currentIndex() if self._z_checkbox.isChecked() else 1

        # Focus settings
        autofocus_mode = AutofocusMode.NONE
        autofocus_interval_fovs = 1
        focus_lock_settings = None
        if self._focus_checkbox.isChecked():
            focus_method = self._focus_method.currentIndex()
            if focus_method == 0:
                autofocus_mode = AutofocusMode.CONTRAST
                autofocus_interval_fovs = self._contrast_af_interval.value()
            elif focus_method == 1:
                autofocus_mode = AutofocusMode.LASER_REFLECTION
                autofocus_interval_fovs = self._laser_af_interval.value()
            else:
                tolerance_um = self._fl_tolerance.value()
                autofocus_mode = AutofocusMode.FOCUS_LOCK
                focus_lock_settings = FocusLockSettings(
                    buffer_length=self._fl_buffer.value(),
                    recovery_attempts=self._fl_retries.value(),
                    acquire_threshold_um=tolerance_um,
                    maintain_threshold_um=max(0.5, tolerance_um),
                    auto_search_enabled=self._fl_auto_recover.isChecked(),
                )

        # Publish command sequence
        self._publish(SetAcquisitionParametersCommand(
            delta_z_um=self._z_delta.value(),
            n_z=self._z_nz.value() if self._z_checkbox.isChecked() else 1,
            autofocus_mode=autofocus_mode,
            autofocus_interval_fovs=autofocus_interval_fovs,
            focus_lock_settings=focus_lock_settings,
            skip_saving=self._skip_saving.isChecked(),
            z_stacking_config=z_stacking_config,
            widget_type="setup",
        ))
        self._publish(SetAcquisitionChannelsCommand(channel_names=selected))
        if save_path:
            self._publish(SetAcquisitionPathCommand(base_path=save_path))
        self._publish(StartNewExperimentCommand(experiment_id=experiment_id))

        canonical_id = self._normalize_experiment_id(experiment_id)
        self._active_experiment_id = canonical_id
        self._start_pending_experiment_id = canonical_id
        self._acquisition_start_watchdog.start(self._ACQUISITION_START_WATCHDOG_MS)
        self._btn_start_stop.setText("Stop Acquisition")
        self._btn_start_stop.setChecked(True)
        self._btn_start_stop.setEnabled(True)
        self._set_controls_enabled(False)
        self._publish(StartAcquisitionCommand(xy_mode=xy_mode))
        _log.info(f"Started acquisition: id={experiment_id}, xy_mode={xy_mode}")

    def _quick_scan(self) -> None:
        """One-click tiled scan at current stage position, no saving."""
        if self._is_acquiring or self._active_experiment_id is not None:
            QMessageBox.warning(self, "Acquisition Running", "Stop the current acquisition before starting Quick Scan.")
            return

        selected = self._channel_order_widget.get_selected_channels_ordered()
        if not selected:
            QMessageBox.warning(self, "No Channels", "Select at least one channel.")
            return

        from datetime import datetime

        experiment_id = f"quick_scan_{datetime.now().strftime('%H-%M-%S-%f')}"
        nx = self._qs_nx.value()
        ny = self._qs_ny.value()
        overlap = self._qs_overlap.value()
        mode_map = {
            _MODE_MULTIWELL: "Select Wells",
            _MODE_ROI_TILING: "Manual",
            _MODE_MULTIPOINT: "Manual",
            _MODE_LOAD_CSV: "Load Coordinates",
        }
        xy_mode = mode_map.get(self._xy_mode_combo.currentIndex(), "Manual")

        self._publish(SetAcquisitionParametersCommand(
            skip_saving=True, n_z=1,
            autofocus_mode=AutofocusMode.NONE,
            autofocus_interval_fovs=1,
            widget_type="setup",
        ))
        self._publish(SetAcquisitionChannelsCommand(channel_names=selected))
        self._publish(SetAcquisitionPathCommand(base_path=tempfile.gettempdir()))
        self._publish(StartNewExperimentCommand(experiment_id=experiment_id))
        canonical_id = self._normalize_experiment_id(experiment_id)
        self._active_experiment_id = canonical_id
        self._start_pending_experiment_id = canonical_id
        self._acquisition_start_watchdog.start(self._ACQUISITION_START_WATCHDOG_MS)
        self._btn_start_stop.setText("Stop Acquisition")
        self._btn_start_stop.setChecked(True)
        self._btn_start_stop.setEnabled(True)
        self._set_controls_enabled(False)
        # Pass grid params directly to controller — bypasses global ScanCoordinates
        # so no FOVs appear in NavigationViewer
        self._publish(StartAcquisitionCommand(
            xy_mode=xy_mode,
            quick_scan_center=(self._cached_x_mm, self._cached_y_mm, self._cached_z_mm),
            quick_scan_nx=nx,
            quick_scan_ny=ny,
            quick_scan_overlap=overlap,
        ))
        _log.info(
            f"Quick scan started: id={experiment_id}, {nx}x{ny} grid at "
            f"({self._cached_x_mm:.3f}, {self._cached_y_mm:.3f}), channels={selected}"
        )

    def _on_acquisition_start_timeout(self) -> None:
        """Recover UI if start was requested but no matching state/progress arrived."""
        pending_id = self._start_pending_experiment_id
        if pending_id is None or self._is_acquiring:
            return
        if self._active_experiment_id is None:
            return
        if not self._experiment_id_matches(self._active_experiment_id, pending_id):
            return

        _log.warning(
            "Acquisition start watchdog timed out for experiment '%s'; resetting controls.",
            pending_id,
        )
        self._start_pending_experiment_id = None
        self._is_acquiring = False
        self._active_experiment_id = None
        self._btn_start_stop.setText("Start Acquisition")
        self._btn_start_stop.setChecked(False)
        self._btn_start_stop.setEnabled(True)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("")
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)
        self._progress_label.setStyleSheet("color: gray; font-size: 11px;")
        self._progress_label.setText("")
        self._set_controls_enabled(True)

    def _on_clear_fovs(self) -> None:
        self._publish(ClearScanCoordinatesCommand())
        self._multipoint_positions.clear()
        self._update_mp_table()

    # =========================================================================
    # Scan coordinate snapshot
    # =========================================================================

    def _request_scan_coordinates_snapshot(self, timeout_ms: int = 2000) -> Optional[ScanCoordinatesSnapshot]:
        request_id = uuid4().hex
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)

        self._snapshot_request_id = request_id
        self._snapshot_loop = loop
        self._snapshot_result = None

        self._publish(RequestScanCoordinatesSnapshotCommand(request_id=request_id))
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        loop.exec_()

        if self._snapshot_request_id == request_id:
            self._snapshot_request_id = None
        self._snapshot_loop = None
        result = self._snapshot_result
        self._snapshot_result = None
        return result

    @handles(ScanCoordinatesSnapshot)
    def _on_scan_coordinates_snapshot(self, event: ScanCoordinatesSnapshot) -> None:
        if self._snapshot_request_id is None:
            return
        if event.request_id != self._snapshot_request_id:
            return
        self._snapshot_result = event
        if self._snapshot_loop is not None:
            self._snapshot_loop.quit()

    # =========================================================================
    # Acquisition state / progress
    # =========================================================================

    def _experiment_id_matches(
        self,
        active_experiment_id: Optional[str],
        event_experiment_id: Optional[str],
    ) -> bool:
        """Return True when IDs are equal or one is the timestamp-suffixed form of the other."""
        active_id = self._normalize_experiment_id(active_experiment_id)
        event_id = self._normalize_experiment_id(event_experiment_id)
        if active_id is None or event_id is None:
            return False
        if active_id == event_id:
            return True
        return (
            event_id.startswith(f"{active_id}_")
            or active_id.startswith(f"{event_id}_")
        )

    @handles(AcquisitionStateChanged)
    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        if (
            self._active_experiment_id is not None
            and not self._experiment_id_matches(
                self._active_experiment_id, event.experiment_id
            )
        ):
            return

        if (
            self._start_pending_experiment_id is not None
            and self._experiment_id_matches(
                self._start_pending_experiment_id, event.experiment_id
            )
        ):
            self._start_pending_experiment_id = None

        self._is_acquiring = event.in_progress
        if event.in_progress:
            # Use backend-published experiment id as canonical id for later filtering.
            if event.experiment_id is not None:
                self._active_experiment_id = self._normalize_experiment_id(
                    event.experiment_id
                )
            self._btn_start_stop.setChecked(True)
            if event.is_aborting:
                self._btn_start_stop.setText("Stopping...")
                self._btn_start_stop.setEnabled(False)
            else:
                self._btn_start_stop.setText("Stop Acquisition")
                self._btn_start_stop.setEnabled(True)
            self._progress_bar.setVisible(True)
            self._progress_label.setVisible(True)
            self._progress_bar.setValue(0)
            self._progress_bar.setFormat("Starting...")
            self._progress_label.setStyleSheet("font-size: 11px;")
            self._progress_label.setText("")
            self._set_controls_enabled(False)
        else:
            self._btn_start_stop.setText("Start Acquisition")
            self._btn_start_stop.setChecked(False)
            self._btn_start_stop.setEnabled(True)
            self._progress_bar.setValue(0)
            self._progress_bar.setFormat("")
            self._progress_bar.setVisible(False)
            self._progress_label.setVisible(False)
            self._progress_label.setStyleSheet("color: gray; font-size: 11px;")
            self._progress_label.setText("")
            self._active_experiment_id = None
            self._set_controls_enabled(True)
            # Generate fresh experiment ID for next run
            from datetime import datetime

            self._experiment_id_edit.setText(datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))

    @handles(AcquisitionProgress)
    def _on_acquisition_progress(self, event: AcquisitionProgress) -> None:
        if (
            self._active_experiment_id is not None
            and not self._experiment_id_matches(
                self._active_experiment_id, event.experiment_id
            )
        ):
            return

        if (
            self._start_pending_experiment_id is not None
            and self._experiment_id_matches(
                self._start_pending_experiment_id, event.experiment_id
            )
        ):
            self._start_pending_experiment_id = None

        self._progress_bar.setValue(int(event.progress_percent))
        self._progress_bar.setFormat(f"FOV {event.current_fov}/{event.total_fovs}")
        self._progress_bar.setVisible(True)
        self._progress_label.setVisible(True)
        eta = f"ETA: {int(event.eta_seconds)}s" if event.eta_seconds else ""
        self._progress_label.setStyleSheet("font-size: 11px;")
        self._progress_label.setText(
            f"FOV {event.current_fov}/{event.total_fovs} | "
            f"{event.current_channel} | {event.progress_percent:.0f}%"
            + (f" | {eta}" if eta else "")
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._xy_checkbox.setEnabled(enabled)
        self._xy_mode_combo.setEnabled(enabled and self._xy_checkbox.isChecked())
        self._z_checkbox.setEnabled(enabled)
        self._focus_checkbox.setEnabled(enabled)
        self._channel_order_widget.setEnabled(enabled)
        self._save_path_edit.setEnabled(enabled)
        self._btn_browse_path.setEnabled(enabled)
        self._experiment_id_edit.setEnabled(enabled)
        self._btn_save_coords.setEnabled(enabled)
        self._btn_clear_fovs.setEnabled(enabled)
        self._btn_save_protocol.setEnabled(enabled)
        self._btn_load_protocol.setEnabled(enabled)
        self._skip_saving.setEnabled(enabled)
        self._save_format.setEnabled(enabled)
        self._btn_quick_scan.setEnabled(enabled)
        for panel in self._xy_panels:
            panel.setEnabled(enabled)

    # =========================================================================
    # Z handlers
    # =========================================================================

    def _on_z_range_toggled(self, checked: bool) -> None:
        self._z_range_frame.setVisible(checked and self._z_checkbox.isChecked())
        self._z_nz.setEnabled(not checked and self._z_checkbox.isChecked())
        if checked:
            z_um = self._cached_z_mm * 1000
            self._z_min.setValue(z_um)
            self._z_max.setValue(z_um)

    def _on_z_range_value_changed(self) -> None:
        if not self._z_range_enable.isChecked():
            return
        z_min, z_max, dz = self._z_min.value(), self._z_max.value(), self._z_delta.value()
        if dz > 0 and z_max >= z_min:
            import math
            self._z_nz.blockSignals(True)
            self._z_nz.setValue(max(1, math.ceil((z_max - z_min) / dz) + 1))
            self._z_nz.blockSignals(False)

    # =========================================================================
    # Saving
    # =========================================================================

    def _on_skip_saving_toggled(self, checked: bool) -> None:
        if checked:
            reply = QMessageBox.warning(
                self, "Skip Saving",
                "Are you sure you want to skip saving images?\nNo image data will be written to disk.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._skip_saving.blockSignals(True)
                self._skip_saving.setChecked(False)
                self._skip_saving.blockSignals(False)

    # =========================================================================
    # Protocol build / apply / save / load
    # =========================================================================

    def build_imaging_protocol(self) -> ImagingProtocol:
        selected = self._channel_order_widget.get_selected_channels_ordered()
        if not selected:
            raise ValueError("Select at least one channel")
        z_dir = _Z_DIRECTION_MAP.get(self._z_direction.currentIndex(), "from_center")
        focus_mode = AutofocusMode.NONE
        focus_interval = 1
        focus_lock = FocusLockConfig()
        if self._focus_checkbox.isChecked():
            mi = self._focus_method.currentIndex()
            if mi == 0:
                focus_mode = AutofocusMode.CONTRAST
                focus_interval = self._contrast_af_interval.value()
            elif mi == 1:
                focus_mode = AutofocusMode.LASER_REFLECTION
                focus_interval = self._laser_af_interval.value()
            else:
                tolerance_um = self._fl_tolerance.value()
                focus_mode = AutofocusMode.FOCUS_LOCK
                focus_lock = FocusLockConfig(
                    buffer_length=self._fl_buffer.value(),
                    recovery_attempts=self._fl_retries.value(),
                    acquire_threshold_um=tolerance_um,
                    maintain_threshold_um=max(0.5, tolerance_um),
                    auto_search_enabled=self._fl_auto_recover.isChecked(),
                )
        fmt_map = {0: "ome-tiff", 1: "tiff", 2: "zarr-v3"}
        return ImagingProtocol(
            channels=selected,
            z_stack=ZStackConfig(
                planes=self._z_nz.value() if self._z_checkbox.isChecked() else 1,
                step_um=self._z_delta.value(), direction=z_dir,
            ),
            acquisition_order="channel_first",
            focus=FocusConfig(
                mode=focus_mode,
                interval_fovs=focus_interval,
                focus_lock=focus_lock,
            ),
            skip_saving=self._skip_saving.isChecked(),
            save_format=fmt_map.get(self._save_format.currentIndex(), "ome-tiff"),
        )

    def apply_imaging_protocol(self, protocol: ImagingProtocol) -> None:
        self._channel_order_widget.set_selected_channels(protocol.get_channel_names())
        has_z = protocol.z_stack.planes > 1
        self._z_checkbox.setChecked(has_z)
        self._z_nz.blockSignals(True)
        self._z_nz.setValue(protocol.z_stack.planes)
        self._z_nz.blockSignals(False)
        self._z_delta.setValue(protocol.z_stack.step_um)
        self._z_direction.setCurrentIndex(_Z_DIRECTION_REVERSE.get(protocol.z_stack.direction, 1))
        self._focus_checkbox.setChecked(protocol.focus.mode != AutofocusMode.NONE)
        if protocol.focus.mode == AutofocusMode.CONTRAST:
            self._focus_method.setCurrentIndex(0)
            self._contrast_af_interval.setValue(protocol.focus.interval_fovs)
        elif protocol.focus.mode == AutofocusMode.LASER_REFLECTION:
            self._focus_method.setCurrentIndex(1)
            self._laser_af_interval.setValue(protocol.focus.interval_fovs)
        elif protocol.focus.mode == AutofocusMode.FOCUS_LOCK and self._focus_method.count() > 2:
            self._focus_method.setCurrentIndex(2)
            self._fl_buffer.setValue(protocol.focus.focus_lock.buffer_length)
            self._fl_retries.setValue(protocol.focus.focus_lock.recovery_attempts)
            self._fl_tolerance.setValue(protocol.focus.focus_lock.acquire_threshold_um)
            self._fl_auto_recover.setChecked(protocol.focus.focus_lock.auto_search_enabled)
        self._skip_saving.blockSignals(True)
        self._skip_saving.setChecked(protocol.skip_saving)
        self._skip_saving.blockSignals(False)
        if protocol.save_format:
            self._save_format.setCurrentIndex({"ome-tiff": 0, "tiff": 1, "zarr-v3": 2}.get(protocol.save_format, 0))
        self._update_tab_styles()

    def _on_save_protocol(self) -> None:
        if self._config_repo is None:
            QMessageBox.warning(self, "No Profile", "No configuration profile is set.")
            return
        name = self._protocol_name.text().strip()
        if not name:
            QMessageBox.warning(self, "No Name", "Please enter a protocol name.")
            return
        try:
            protocol = self.build_imaging_protocol()
        except ValueError as e:
            QMessageBox.warning(self, "Validation Error", str(e))
            return
        profile = self._config_repo.current_profile
        if not profile:
            QMessageBox.warning(self, "No Profile", "No active profile.")
            return
        existing = self._config_repo.get_available_imaging_protocols(profile)
        if name in existing:
            reply = QMessageBox.question(
                self, "Overwrite?", f"Protocol '{name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._config_repo.save_imaging_protocol(profile, name, protocol)
        _log.info(f"Saved imaging protocol '{name}' to profile '{profile}'")
        QMessageBox.information(self, "Saved", f"Protocol '{name}' saved.")

    def _on_load_protocol(self) -> None:
        if self._config_repo is None:
            QMessageBox.warning(self, "No Profile", "No configuration profile is set.")
            return
        profile = self._config_repo.current_profile
        if not profile:
            QMessageBox.warning(self, "No Profile", "No active profile.")
            return
        available = self._config_repo.get_available_imaging_protocols(profile)
        if not available:
            QMessageBox.information(self, "Load Protocol", "No saved protocols found.")
            return
        from qtpy.QtWidgets import QInputDialog
        name, ok = QInputDialog.getItem(self, "Load Protocol", "Select protocol:", available, 0, False)
        if not ok or not name:
            return
        protocol = self._config_repo.get_imaging_protocol(name, profile)
        if protocol is None:
            QMessageBox.warning(self, "Load Failed", f"Failed to load protocol '{name}'.")
            return
        self.apply_imaging_protocol(protocol)
        self._protocol_name.setText(name)
        _log.info(f"Loaded imaging protocol '{name}' from profile '{profile}'")

    # =========================================================================
    # EventBus handlers
    # =========================================================================

    @handles(ActiveAcquisitionTabChanged)
    def _on_active_tab_changed(self, event: ActiveAcquisitionTabChanged) -> None:
        was_active = self._is_active_tab
        self._is_active_tab = event.active_tab == "setup"

        if not self._is_active_tab:
            if was_active:
                self._publish(ManualShapeDrawingEnabledChanged(enabled=False))
            return

        # Re-enable ROI drawing if in ROI mode
        in_roi = self._xy_checkbox.isChecked() and self._xy_mode_combo.currentIndex() == _MODE_ROI_TILING
        self._publish(ManualShapeDrawingEnabledChanged(enabled=in_roi))
        if in_roi and self._manual_shapes_mm:
            self._on_generate_fovs()
        else:
            self._update_xy_coordinates()

    @handles(MosaicLayersInitialized)
    def _on_mosaic_initialized(self, event: MosaicLayersInitialized) -> None:
        self._mosaic_initialized = True
        self._xy_mode_combo.model().item(_MODE_ROI_TILING).setEnabled(True)
        self._xy_mode_combo.setItemData(_MODE_ROI_TILING, "", Qt.ToolTipRole)

    @handles(MosaicLayersCleared)
    def _on_mosaic_cleared(self, event: MosaicLayersCleared) -> None:
        self._mosaic_initialized = False
        self._xy_mode_combo.model().item(_MODE_ROI_TILING).setEnabled(False)
        self._xy_mode_combo.setItemData(
            _MODE_ROI_TILING, "Requires tile scan for coordinate reference", Qt.ToolTipRole
        )
        # If user was in ROI mode, switch to multiwell (the default)
        if self._xy_mode_combo.currentIndex() == _MODE_ROI_TILING:
            self._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)

    @handles(StagePositionChanged)
    def _on_stage_position_changed(self, event: StagePositionChanged) -> None:
        self._cached_x_mm = event.x_mm
        self._cached_y_mm = event.y_mm
        self._cached_z_mm = event.z_mm

    @handles(ObjectiveChanged)
    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        if self._is_active_tab:
            self._update_xy_coordinates()

    @handles(BinningChanged)
    def _on_binning_changed(self, event: BinningChanged) -> None:
        if self._is_active_tab:
            self._update_xy_coordinates()

    @handles(ScanCoordinatesUpdated)
    def _on_scan_coordinates_updated(self, event: ScanCoordinatesUpdated) -> None:
        self._total_fovs = event.total_fovs
        self._fov_count_label.setText(f"{event.total_fovs} FOVs ({event.total_regions} regions)")

    @handles(ChannelConfigurationsChanged)
    def _on_channel_configs_changed(self, event: ChannelConfigurationsChanged) -> None:
        self._channel_configs = list(event.configuration_names)
        self._channel_order_widget.set_channels(event.configuration_names)

    @handles(ManualShapesChanged)
    def _on_manual_shapes_changed(self, event: ManualShapesChanged) -> None:
        # Always store shapes (don't gate on _is_active_tab)
        if event.shapes_mm is None:
            self._manual_shapes_mm = None
        else:
            self._manual_shapes_mm = [np.array(shape, dtype=float) for shape in event.shapes_mm]

        # Always update the status label when in ROI mode
        if self._xy_mode_combo.currentIndex() == _MODE_ROI_TILING:
            n = len(self._manual_shapes_mm) if self._manual_shapes_mm else 0
            self._roi_status_label.setText(f"{n} ROIs")

    def _update_xy_coordinates(self) -> None:
        if not self._xy_checkbox.isChecked():
            return
        mode = self._xy_mode_combo.currentIndex()
        if mode == _MODE_MULTIWELL:
            self._publish_well_scan_command()
        elif mode == _MODE_MULTIPOINT:
            self._publish_multipoint_regions()
        elif mode == _MODE_ROI_TILING and self._manual_shapes_mm is not None:
            self._on_generate_fovs()
