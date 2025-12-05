# Display widgets for visualization
import numpy as np

import squid.logging
import pyqtgraph as pg
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
from qtpy.QtCore import Signal, Qt, QTimer
from qtpy.QtWidgets import (
    QFrame,
    QWidget,
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
    QHeaderView,
    QAbstractItemView,
)

from control._def import *
from squid.abc import AbstractStage


class StatsDisplayWidget(QFrame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initUI()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def initUI(self):
        self.layout = QVBoxLayout()
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(2)
        self.table_widget.verticalHeader().hide()
        self.table_widget.horizontalHeader().hide()
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.layout.addWidget(self.table_widget)
        self.setLayout(self.layout)

    def display_stats(self, stats):
        print("displaying parasite stats")
        locale.setlocale(locale.LC_ALL, "")
        self.table_widget.setRowCount(len(stats))
        row = 0
        for key, value in stats.items():
            key_item = QTableWidgetItem(str(key))
            value_item = None
            try:
                value_item = QTableWidgetItem(f"{value:n}")
            except:
                value_item = QTableWidgetItem(str(value))
            self.table_widget.setItem(row, 0, key_item)
            self.table_widget.setItem(row, 1, value_item)
            row += 1


class FocusMapWidget(QFrame):
    """Widget for managing focus map points and surface fitting"""

    def __init__(self, stage: AbstractStage, navigationViewer, scanCoordinates, focusMap):
        super().__init__()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self._allow_updating_focus_points_on_signal = True

        # Store controllers
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.scanCoordinates = scanCoordinates
        self.focusMap = focusMap

        # Store focus points in widget
        self.focus_points = []  # list of (region_id, x, y, z) tuples
        self.enabled = False  # toggled when focus map enabled for next acquisition

        self.setup_ui()
        self.make_connections()
        self.setEnabled(False)
        self.add_margin = True  # margin for focus grid makes it smaller, but will avoid points at the borders

    def setup_ui(self):
        """Create and arrange UI components"""
        self.layout = QVBoxLayout(self)

        # Point combo and Z control
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Focus Point:"))
        self.point_combo = QComboBox()
        controls_layout.addWidget(self.point_combo, stretch=1)
        self.update_z_btn = QPushButton("Update Z")
        controls_layout.addWidget(self.update_z_btn)
        self.layout.addLayout(controls_layout)

        # Point control buttons - line 1
        point_controls = QHBoxLayout()
        self.add_point_btn = QPushButton("Add")
        self.remove_point_btn = QPushButton("Remove")
        self.next_point_btn = QPushButton("Next")
        self.edit_point_btn = QPushButton("Edit")
        point_controls.addWidget(self.add_point_btn)
        point_controls.addWidget(self.remove_point_btn)
        point_controls.addWidget(self.next_point_btn)
        point_controls.addWidget(self.edit_point_btn)
        self.layout.addLayout(point_controls)

        # Point control buttons - line 2
        point_controls_2 = QHBoxLayout()
        point_controls_2.addWidget(QLabel("Focus Grid:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setKeyboardTracking(False)
        self.rows_spin.setRange(1, 10)
        self.rows_spin.setValue(4)
        point_controls_2.addWidget(self.rows_spin)
        x_label = QLabel("×")
        x_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        point_controls_2.addWidget(x_label)
        self.cols_spin = QSpinBox()
        self.cols_spin.setKeyboardTracking(False)
        self.cols_spin.setRange(1, 10)
        self.cols_spin.setValue(4)
        point_controls_2.addWidget(self.cols_spin)
        self.export_btn = QPushButton("Export")
        self.export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_btn = QPushButton("Import")
        self.import_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        point_controls_2.addWidget(self.export_btn)
        point_controls_2.addWidget(self.import_btn)
        self.layout.addLayout(point_controls_2)

        # Surface fitting controls
        settings_layout = QHBoxLayout()
        settings_layout.addWidget(QLabel("Fitting Method:"))
        self.fit_method_combo = QComboBox()
        self.fit_method_combo.addItems(["spline", "rbf", "constant"])
        settings_layout.addWidget(self.fit_method_combo)
        settings_layout.addWidget(QLabel("Smoothing:"))
        self.smoothing_spin = QDoubleSpinBox()
        self.smoothing_spin.setKeyboardTracking(False)
        self.smoothing_spin.setRange(0.01, 1.0)
        self.smoothing_spin.setValue(0.1)
        self.smoothing_spin.setSingleStep(0.05)
        settings_layout.addWidget(self.smoothing_spin)
        self.by_region_checkbox = QCheckBox("Fit by Region")
        self.by_region_checkbox.setChecked(False)
        settings_layout.addWidget(self.by_region_checkbox)
        self.layout.addLayout(settings_layout)

        # Status label - reserve space even when hidden
        self.status_label = QLabel()
        self.status_label.setText(" ")  # Empty text to keep space
        self.layout.addWidget(self.status_label)

    def make_connections(self):
        # Auto-navigate when point selection changes
        self.point_combo.currentIndexChanged.connect(self.goto_selected_point)

        # Update Z for current point
        self.update_z_btn.clicked.connect(self.update_current_z)

        # Connect grid size changes
        self.rows_spin.valueChanged.connect(self.regenerate_grid)
        self.cols_spin.valueChanged.connect(self.regenerate_grid)

        # Connect point control buttons
        self.add_point_btn.clicked.connect(self.add_current_point)
        self.remove_point_btn.clicked.connect(self.remove_current_point)
        self.next_point_btn.clicked.connect(self.goto_next_point)
        self.edit_point_btn.clicked.connect(self.edit_current_point)
        self.export_btn.clicked.connect(self.export_focus_points)
        self.import_btn.clicked.connect(self.import_focus_points)

        # Connect fitting method change
        self.fit_method_combo.currentTextChanged.connect(self._match_by_region_box)

    def update_point_list(self):
        """Update point selection combo showing grid coordinates for points"""
        self.point_combo.blockSignals(True)
        curr_focus_point = self.point_combo.currentIndex()
        self.point_combo.clear()
        for idx, (region_id, x, y, z) in enumerate(self.focus_points):
            point_text = (
                f"{region_id}: "
                + "x:"
                + str(round(x, 3))
                + "mm  y:"
                + str(round(y, 3))
                + "mm  z:"
                + str(round(1000 * z, 2))
                + "μm"
            )
            self.point_combo.addItem(point_text)
        self.point_combo.setCurrentIndex(max(0, min(curr_focus_point, len(self.focus_points) - 1)))
        self.point_combo.blockSignals(False)

    def edit_current_point(self):
        """Edit coordinates of current point in a popup dialog"""
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            region_id, x, y, z = self.focus_points[index]

            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Edit Focus Point")
            layout = QFormLayout()

            # Add coordinate spinboxes with good precision
            x_spin = QDoubleSpinBox()
            x_spin.setKeyboardTracking(False)
            x_spin.setRange(SOFTWARE_POS_LIMIT.X_NEGATIVE, SOFTWARE_POS_LIMIT.X_POSITIVE)
            x_spin.setDecimals(3)
            x_spin.setValue(x)
            x_spin.setSuffix(" mm")

            y_spin = QDoubleSpinBox()
            y_spin.setKeyboardTracking(False)
            y_spin.setRange(SOFTWARE_POS_LIMIT.Y_NEGATIVE, SOFTWARE_POS_LIMIT.Y_POSITIVE)
            y_spin.setDecimals(3)
            y_spin.setValue(y)
            y_spin.setSuffix(" mm")

            z_spin = QDoubleSpinBox()
            z_spin.setKeyboardTracking(False)
            z_spin.setRange(
                SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000, SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000
            )  # Convert mm limits to μm
            z_spin.setDecimals(2)
            z_spin.setValue(z * 1000)  # Convert mm to μm
            z_spin.setSuffix(" μm")

            layout.addRow("X:", x_spin)
            layout.addRow("Y:", y_spin)
            layout.addRow("Z:", z_spin)

            # Add OK/Cancel buttons
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)
            dialog.setLayout(layout)

            # Show dialog and handle result
            if dialog.exec_() == QDialog.Accepted:
                new_x = x_spin.value()
                new_y = y_spin.value()
                new_z = z_spin.value() / 1000  # Convert μm back to mm for storage
                self.focus_points[index] = (region_id, new_x, new_y, new_z)
                self.update_point_list()
                self.update_focus_point_display()

    def update_focus_point_display(self):
        """Update all focus points on navigation viewer"""
        self.navigationViewer.clear_focus_points()
        for _, x, y, _ in self.focus_points:
            self.navigationViewer.register_focus_point(x, y)

    def generate_grid(self, rows=4, cols=4):
        """Generate focus point grid that spans scan bounds"""
        if self.enabled:
            self.point_combo.blockSignals(True)
            self.focus_points.clear()
            self.navigationViewer.clear_focus_points()
            self.status_label.setText(" ")
            current_z = self.stage.get_pos().z_mm

            # Use FocusMap to generate coordinates
            coordinates = self.focusMap.generate_grid_coordinates(
                self.scanCoordinates, rows=rows, cols=cols, add_margin=self.add_margin
            )

            # Add points with current z coordinate
            for region_id, coords_list in coordinates.items():
                for coords in coords_list:
                    self.focus_points.append((region_id, coords[0], coords[1], current_z))
                    self.navigationViewer.register_focus_point(coords[0], coords[1])

            self.update_point_list()
            self.point_combo.blockSignals(False)

    def regenerate_grid(self):
        """Generate focus point grid given updated dims"""
        self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def add_current_point(self):
        # Check if any scan regions exist
        if not self.scanCoordinates.has_regions():
            QMessageBox.warning(self, "No Regions Defined", "Please define scan regions before adding focus points.")
            return

        pos = self.stage.get_pos()
        region_id = None

        # If by_region checkbox is checked, ask for region ID
        if self.by_region_checkbox.isChecked():
            region_ids = list(self.scanCoordinates.region_centers.keys())
            if not region_ids:
                QMessageBox.warning(
                    self, "No Regions Defined", "Please define scan regions before adding focus points."
                )
                return

            region_id, ok = QInputDialog.getItem(
                self, "Select Region", "Choose a region:", [str(r) for r in region_ids], 0, False
            )
            if not ok or not region_id:
                return
            region_id = str(region_id)  # Ensure string format
        else:
            # Find the closest region to current position
            closest_region = None
            min_distance = float("inf")
            for rid, center in self.scanCoordinates.region_centers.items():
                dx = center[0] - pos.x_mm
                dy = center[1] - pos.y_mm
                distance = dx * dx + dy * dy
                if distance < min_distance:
                    min_distance = distance
                    closest_region = rid
            region_id = closest_region

        if region_id is not None:
            self.focus_points.append((region_id, pos.x_mm, pos.y_mm, pos.z_mm))
            self.update_point_list()
            self.navigationViewer.register_focus_point(pos.x_mm, pos.y_mm)
        else:
            QMessageBox.warning(self, "Region Error", "Could not determine a valid region for this focus point.")

    def remove_current_point(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            self.focus_points.pop(index)
            self.update_point_list()
            self.update_focus_point_display()

    def goto_next_point(self):
        if not self.focus_points:
            return
        current = self.point_combo.currentIndex()
        next_index = (current + 1) % len(self.focus_points)
        self.point_combo.setCurrentIndex(next_index)
        self.goto_selected_point()

    def goto_selected_point(self):
        if self.enabled:
            index = self.point_combo.currentIndex()
            if 0 <= index < len(self.focus_points):
                _, x, y, z = self.focus_points[index]
                self.stage.move_x_to(x)
                self.stage.move_y_to(y)
                self.stage.move_z_to(z)

    def update_current_z(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            new_z = self.stage.get_pos().z_mm
            region_id, x, y, _ = self.focus_points[index]
            self.focus_points[index] = (region_id, x, y, new_z)
            self.update_point_list()

    def get_region_points_dict(self):
        points_dict = {}
        for region_id, x, y, z in self.focus_points:
            if region_id not in points_dict:
                points_dict[region_id] = []
            points_dict[region_id].append((x, y, z))
        return points_dict

    def fit_surface(self):
        try:
            method = self.fit_method_combo.currentText()
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            by_region = self.by_region_checkbox.isChecked()

            # Validate settings
            if by_region:
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in self.focus_points)
                if focus_regions != scan_regions:
                    QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        "The focus points region IDs do not match the scan regions. Please uncheck 'By Region' or select the correct regions.",
                    )
                    return False

            if method == "constant" and (rows != 1 or cols != 1):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For 'constant' method, grid size should be 1×1.\nUse 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            if method != "constant" and (rows < 2 or cols < 2):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For surface fitting methods ('spline' or 'rbf'), a grid size of at least 2×2 is recommended.\nAlternatively, use 1x1 grid and 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            self.focusMap.set_method(method)
            self.focusMap.set_fit_by_region(by_region)
            self.focusMap.smoothing_factor = self.smoothing_spin.value()

            mean_error, std_error = self.focusMap.fit(self.get_region_points_dict())

            self.status_label.setText(f"Surface fit: {mean_error:.3f} mm mean error")
            return True

        except Exception as e:
            self.status_label.setText(f"Fitting failed: {str(e)}")
            return False

    def _match_by_region_box(self):
        if self.fit_method_combo.currentText() == "constant":
            self.by_region_checkbox.setChecked(True)

    def export_focus_points(self):
        """Export focus points to a CSV file"""
        if not self.focus_points:
            QMessageBox.warning(self, "No Focus Points", "There are no focus points to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Focus Points", "", "CSV Files (*.csv);;All Files (*)")
        if not file_path:
            return
        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        try:
            with open(file_path, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                # Write header
                writer.writerow(["Region_ID", "X_mm", "Y_mm", "Z_um"])

                # Write data
                for region_id, x, y, z in self.focus_points:
                    writer.writerow([region_id, x, y, z])

            self.status_label.setText(f"Exported {len(self.focus_points)} points to {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export focus points: {str(e)}")

    def import_focus_points(self):
        """Import focus points from a CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Focus Points", "", "CSV Files (*.csv);;All Files (*)")

        if not file_path:
            return

        try:
            # Read the CSV file
            imported_points = []
            with open(file_path, "r", newline="") as csvfile:
                reader = csv.reader(csvfile)
                header = next(reader)  # Skip header row

                # Validate header
                required_columns = ["Region_ID", "X_mm", "Y_mm", "Z_um"]
                if not all(col in header for col in required_columns):
                    QMessageBox.warning(
                        self, "Invalid Format", f"CSV file must contain columns: {', '.join(required_columns)}"
                    )
                    return

                # Get column indices
                region_idx = header.index("Region_ID")
                x_idx = header.index("X_mm")
                y_idx = header.index("Y_mm")
                z_idx = header.index("Z_um")

                # Read data
                for row in reader:
                    if len(row) >= 4:
                        try:
                            region_id = str(row[region_idx])
                            x = float(row[x_idx])
                            y = float(row[y_idx])
                            z = float(row[z_idx])
                            imported_points.append((region_id, x, y, z))
                        except (ValueError, IndexError):
                            continue

            # If by_region is checked, validate regions
            if self.by_region_checkbox.isChecked():
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in imported_points)

                if not focus_regions == scan_regions:
                    response = QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        f"The imported focus points have regions: {', '.join(sorted(focus_regions))}\n\n"
                        f"Current scan has regions: {', '.join(sorted(scan_regions))}\n\n"
                        "Import anyway (disable 'By Region') or cancel?",
                        QMessageBox.Ok | QMessageBox.Cancel,
                        QMessageBox.Cancel,
                    )

                    if response == QMessageBox.Cancel:
                        return
                    else:
                        # User chose to continue, uncheck by_region
                        self.by_region_checkbox.setChecked(False)

            # Clear existing points and add imported ones
            self.focus_points = imported_points
            self.update_point_list()
            self.update_focus_point_display()

            self.status_label.setText(f"Imported {len(imported_points)} focus points")

        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import focus points: {str(e)}")

    def on_regions_updated(self):
        if not self._allow_updating_focus_points_on_signal:
            return
        if self.scanCoordinates.has_regions():
            self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def disable_updating_focus_points_on_signal(self):
        self._allow_updating_focus_points_on_signal = False

    def enable_updating_focus_points_on_signal(self):
        self._allow_updating_focus_points_on_signal = True

    def setEnabled(self, enabled):
        self.enabled = enabled
        super().setEnabled(enabled)
        self.navigationViewer.focus_point_overlay_item.setVisible(enabled)
        self.on_regions_updated()

    def resizeEvent(self, event):
        """Handle resize events to maintain button sizing"""
        super().resizeEvent(event)
        self.update_z_btn.setFixedWidth(self.edit_point_btn.width())


class NapariLiveWidget(QWidget):
    signal_coordinates_clicked = Signal(int, int, int, int)
    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)

    def __init__(
        self,
        streamHandler,
        liveController,
        stage: AbstractStage,
        objectiveStore,
        channelConfigurationManager,
        contrastManager,
        wellSelectionWidget=None,
        show_trigger_options=True,
        show_display_options=True,
        show_autolevel=False,
        autolevel=False,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.stage = stage
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.wellSelectionWidget = wellSelectionWidget
        self.live_configuration = self.liveController.currentConfiguration
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.init_live = False
        self.init_live_rgb = False
        self.init_scale = False
        self.previous_scale = None
        self.previous_center = None
        self.last_was_autofocus = False
        self.fps_trigger = 10
        self.fps_display = 10
        self.contrastManager = contrastManager

        self.initNapariViewer()
        self.addNapariGrayclipColormap()
        self.initControlWidgets(show_trigger_options, show_display_options, show_autolevel, autolevel)
        self.update_ui_for_mode(self.live_configuration)

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        self.viewerWidget = self.viewer.window._qt_window
        self.viewer.dims.axis_labels = ["Y-axis", "X-axis"]
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def updateHistogram(self, layer):
        if self.histogram_widget is not None and layer.data is not None:
            self.pg_image_item.setImage(layer.data, autoLevels=False)
            self.histogram_widget.setLevels(*layer.contrast_limits)
            self.histogram_widget.setHistogramRange(layer.data.min(), layer.data.max())

            # Set the histogram widget's region to match the layer's contrast limits
            self.histogram_widget.region.setRegion(layer.contrast_limits)

            # Update colormap only if it has changed
            if hasattr(self, "last_colormap") and self.last_colormap != layer.colormap.name:
                self.histogram_widget.gradient.setColorMap(self.createColorMap(layer.colormap))
            self.last_colormap = layer.colormap.name

    def createColorMap(self, colormap):
        colors = colormap.colors
        positions = np.linspace(0, 1, len(colors))
        return pg.ColorMap(positions, colors)

    def initControlWidgets(self, show_trigger_options, show_display_options, show_autolevel, autolevel):
        # Initialize histogram widget
        self.pg_image_item = pg.ImageItem()
        self.histogram_widget = pg.HistogramLUTWidget(image=self.pg_image_item)
        self.histogram_widget.setFixedWidth(100)
        self.histogram_dock = self.viewer.window.add_dock_widget(self.histogram_widget, area="right", name="hist")
        self.histogram_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.histogram_dock.setTitleBarWidget(QWidget())
        self.histogram_widget.region.sigRegionChanged.connect(self.on_histogram_region_changed)
        self.histogram_widget.region.sigRegionChangeFinished.connect(self.on_histogram_region_changed)

        # Microscope Configuration
        self.dropdown_modeSelection = QComboBox()
        for config in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.dropdown_modeSelection.addItem(config.name)
        self.dropdown_modeSelection.setCurrentText(self.live_configuration.name)
        self.dropdown_modeSelection.activated(self.select_new_microscope_mode_by_name)

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        gradient_style = """
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #D6D6FF, stop:1 #C2C2FF);
                border-radius: 5px;
                color: black;
                border: 1px solid #A0A0A0;
            }
            QPushButton:checked {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #FFD6D6, stop:1 #FFC2C2);
                border: 1px solid #A0A0A0;
            }
            QPushButton:hover {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #E0E0FF, stop:1 #D0D0FF);
            }
            QPushButton:pressed {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #9090C0, stop:1 #8080B0);
            }
        """
        self.btn_live.setStyleSheet(gradient_style)
        # self.btn_live.setStyleSheet("font-weight: bold; background-color: #7676F7") #6666D3
        current_height = self.btn_live.sizeHint().height()
        self.btn_live.setFixedHeight(int(current_height * 1.5))
        self.btn_live.clicked.connect(self.toggle_live)

        # Exposure Time
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(*self.camera.get_exposure_limits())
        self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)

        # Analog Gain
        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setRange(0, 24)
        self.entry_analogGain.setSingleStep(0.1)
        self.entry_analogGain.setValue(self.live_configuration.analog_gain)
        # self.entry_analogGain.setSuffix('x')
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)

        # Illumination Intensity
        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setRange(0, 100)
        self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setTickInterval(10)
        self.slider_illuminationIntensity.valueChanged.connect(self.update_config_illumination_intensity)
        self.label_illuminationIntensity = QLabel(str(self.slider_illuminationIntensity.value()) + "%")
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda v: self.label_illuminationIntensity.setText(str(v) + "%")
        )

        # Trigger mode
        self.dropdown_triggerMode = QComboBox()
        trigger_modes = [
            ("Software", TriggerMode.SOFTWARE),
            ("Hardware", TriggerMode.HARDWARE),
            ("Continuous", TriggerMode.CONTINUOUS),
        ]
        for display_name, mode in trigger_modes:
            self.dropdown_triggerMode.addItem(display_name, mode)
        self.dropdown_triggerMode.currentIndexChanged.connect(self.on_trigger_mode_changed)

        # Trigger FPS
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setRange(0.02, 1000)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        # self.entry_triggerFPS.setSuffix(" fps")
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)

        # Display FPS
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setRange(1, 240)
        self.entry_displayFPS.setValue(self.fps_display)
        # self.entry_displayFPS.setSuffix(" fps")
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)

        # Resolution Scaling
        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setRange(10, 100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setTickInterval(10)
        self.slider_resolutionScaling.valueChanged.connect(self.update_resolution_scaling)
        self.label_resolutionScaling = QLabel(str(self.slider_resolutionScaling.value()) + "%")
        self.slider_resolutionScaling.valueChanged.connect(lambda v: self.label_resolutionScaling.setText(str(v) + "%"))

        # Autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)
        self.btn_autolevel.clicked.connect(self.signal_autoLevelSetting.emit)

        def make_row(label_widget, entry_widget, value_label=None):
            row = QHBoxLayout()
            row.addWidget(label_widget)
            row.addWidget(entry_widget)
            if value_label:
                row.addWidget(value_label)
            return row

        control_layout = QVBoxLayout()

        # Add widgets to layout
        control_layout.addWidget(self.dropdown_modeSelection)
        control_layout.addWidget(self.btn_live)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        row1 = make_row(QLabel("Exposure Time"), self.entry_exposureTime)
        control_layout.addLayout(row1)

        row2 = make_row(QLabel("Illumination"), self.slider_illuminationIntensity, self.label_illuminationIntensity)
        control_layout.addLayout(row2)

        row3 = make_row((QLabel("Analog Gain")), self.entry_analogGain)
        control_layout.addLayout(row3)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_trigger_options:
            row0 = make_row(QLabel("Trigger Mode"), self.dropdown_triggerMode)
            control_layout.addLayout(row0)
            row00 = make_row(QLabel("Trigger FPS"), self.entry_triggerFPS)
            control_layout.addLayout(row00)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_display_options:
            row4 = make_row((QLabel("Display FPS")), self.entry_displayFPS)
            control_layout.addLayout(row4)
            row5 = make_row(QLabel("Display Resolution"), self.slider_resolutionScaling, self.label_resolutionScaling)
            control_layout.addLayout(row5)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_autolevel:
            control_layout.addWidget(self.btn_autolevel)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        control_layout.addStretch(1)

        add_live_controls = False
        if USE_NAPARI_FOR_LIVE_CONTROL or add_live_controls:
            live_controls_widget = QWidget()
            live_controls_widget.setLayout(control_layout)
            # layer_list_widget.setFixedWidth(270)

            layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
            layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

            self.viewer.window._qt_viewer.layerButtons.hide()
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)

            # Add the actual dock widgets
            self.dock_layer_controls = self.viewer.window.add_dock_widget(
                layer_controls_widget, area="left", name="layer controls", tabify=True
            )
            self.dock_layer_list = self.viewer.window.add_dock_widget(
                layer_list_widget, area="left", name="layer list", tabify=True
            )
            self.dock_live_controls = self.viewer.window.add_dock_widget(
                live_controls_widget, area="left", name="live controls", tabify=True
            )

            self.viewer.window.window_menu.addAction(self.dock_live_controls.toggleViewAction())

        if USE_NAPARI_WELL_SELECTION:
            well_selector_layout = QVBoxLayout()
            # title_label = QLabel("Well Selector")
            # title_label.setAlignment(Qt.AlignCenter)  # Center the title
            # title_label.setStyleSheet("font-weight: bold;")  # Optional: style the title
            # well_selector_layout.addWidget(title_label)

            well_selector_row = QHBoxLayout()
            well_selector_row.addStretch(1)
            well_selector_row.addWidget(self.wellSelectionWidget)
            well_selector_row.addStretch(1)
            well_selector_layout.addLayout(well_selector_row)
            well_selector_layout.addStretch()

            well_selector_dock_widget = QWidget()
            well_selector_dock_widget.setLayout(well_selector_layout)
            self.dock_well_selector = self.viewer.window.add_dock_widget(
                well_selector_dock_widget, area="bottom", name="well selector"
            )
            self.dock_well_selector.setFixedHeight(self.dock_well_selector.minimumSizeHint().height())

        layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
        layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

        self.viewer.window._qt_viewer.layerButtons.hide()
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)
        self.print_window_menu_items()

    def print_window_menu_items(self):
        print("Items in window_menu:")
        for action in self.viewer.window.window_menu.actions():
            print(action.text())

    def on_histogram_region_changed(self):
        if self.live_configuration.name:
            min_val, max_val = self.histogram_widget.region.getRegion()
            self.updateContrastLimits(self.live_configuration.name, min_val, max_val)

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def toggle_live_controls(self, show):
        if show:
            self.dock_live_controls.show()
        else:
            self.dock_live_controls.hide()

    def toggle_well_selector(self, show):
        if show:
            self.dock_well_selector.show()
        else:
            self.dock_well_selector.hide()

    def replace_well_selector(self, wellSelector):
        self.viewer.window.remove_dock_widget(self.dock_well_selector)
        self.wellSelectionWidget = wellSelector
        well_selector_layout = QHBoxLayout()
        well_selector_layout.addStretch(1)  # Add stretch on the left
        well_selector_layout.addWidget(self.wellSelectionWidget)
        well_selector_layout.addStretch(1)  # Add stretch on the right
        well_selector_dock_widget = QWidget()
        well_selector_dock_widget.setLayout(well_selector_layout)
        self.dock_well_selector = self.viewer.window.add_dock_widget(
            well_selector_dock_widget, area="bottom", name="well selector", tabify=True
        )

    def select_new_microscope_mode_by_name(self, config_index):
        config_name = self.dropdown_modeSelection.itemText(config_index)
        maybe_new_config = self.channelConfigurationManager.get_channel_configuration_by_name(
            self.objectiveStore.current_objective, config_name
        )

        if not maybe_new_config:
            self._log.error(f"User attempted to select config named '{config_name}' but it does not exist!")
            return

        self.liveController.set_microscope_mode(maybe_new_config)
        self.update_ui_for_mode(maybe_new_config)

    def update_ui_for_mode(self, config):
        self.live_configuration = config
        self.dropdown_modeSelection.setCurrentText(config.name if config else "Unknown")
        if self.live_configuration:
            self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
            self.entry_analogGain.setValue(self.live_configuration.analog_gain)
            self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))

    def update_config_exposure_time(self, new_value):
        self.live_configuration.exposure_time = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "ExposureTime", new_value
        )
        self.signal_newExposureTime.emit(new_value)

    def update_config_analog_gain(self, new_value):
        self.live_configuration.analog_gain = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "AnalogGain", new_value
        )
        self.signal_newAnalogGain.emit(new_value)

    def update_config_illumination_intensity(self, new_value):
        self.live_configuration.illumination_intensity = new_value
        self.channelConfigurationManager.update_configuration(
            self.objectiveStore.current_objective, self.live_configuration.id, "IlluminationIntensity", new_value
        )
        self.liveController.update_illumination()

    def update_resolution_scaling(self, value):
        self.streamHandler.set_display_resolution_scaling(value)
        self.liveController.set_display_resolution_scaling(value)

    def on_trigger_mode_changed(self, index):
        # Get the actual value using user data
        actual_value = self.dropdown_triggerMode.itemData(index)
        print(f"Selected: {self.dropdown_triggerMode.currentText()} (actual value: {actual_value})")

    def addNapariGrayclipColormap(self):
        if hasattr(napari.utils.colormaps.AVAILABLE_COLORMAPS, "grayclip"):
            return
        grayclip = []
        for i in range(255):
            grayclip.append([i / 255, i / 255, i / 255])
        grayclip.append([1, 0, 0])
        napari.utils.colormaps.AVAILABLE_COLORMAPS["grayclip"] = napari.utils.Colormap(name="grayclip", colors=grayclip)

    def initLiveLayer(self, channel, image_height, image_width, image_dtype, rgb=False):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        self.viewer.layers.clear()
        self.image_width = image_width
        self.image_height = image_height
        if self.dtype != np.dtype(image_dtype):

            self.contrastManager.scale_contrast_limits(
                np.dtype(image_dtype)
            )  # Fix This to scale existing contrast limits to new dtype range
            self.dtype = image_dtype

        self.channels.add(channel)
        self.live_configuration.name = channel

        if rgb:
            canvas = np.zeros((image_height, image_width, 3), dtype=self.dtype)
        else:
            canvas = np.zeros((image_height, image_width), dtype=self.dtype)
        limits = self.getContrastLimits(self.dtype)
        layer = self.viewer.add_image(
            canvas,
            name="Live View",
            visible=True,
            rgb=rgb,
            colormap="grayclip",
            contrast_limits=limits,
            blending="additive",
        )
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name, self.dtype)
        layer.mouse_double_click_callbacks.append(self.onDoubleClick)
        layer.events.contrast_limits.connect(self.signalContrastLimits)
        self.updateHistogram(layer)

        if not self.init_scale:
            self.resetView()
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
        else:
            self.viewer.camera.zoom = self.previous_scale
            self.viewer.camera.center = self.previous_center

    def updateLiveLayer(self, image, from_autofocus=False):
        """Updates the canvas with the new image data."""
        if self.dtype != np.dtype(image.dtype):
            self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            self.dtype = np.dtype(image.dtype)
            self.init_live = False
            self.init_live_rgb = False

        if not self.live_configuration.name:
            self.live_configuration.name = self.liveController.currentConfiguration.name
        rgb = len(image.shape) >= 3

        if not rgb and not self.init_live or "Live View" not in self.viewer.layers:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live = True
            self.init_live_rgb = False
            print("init live")
        elif rgb and not self.init_live_rgb:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live_rgb = True
            self.init_live = False
            print("init live rgb")

        layer = self.viewer.layers["Live View"]
        layer.data = image
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name)
        self.updateHistogram(layer)

        if from_autofocus:
            # save viewer scale
            if not self.last_was_autofocus:
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
            # resize to cropped view
            self.resetView()
            self.last_was_autofocus = True
        else:
            if not self.init_scale:
                # init viewer scale
                self.resetView()
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
                self.init_scale = True
            elif self.last_was_autofocus:
                # return to to original view
                self.viewer.camera.zoom = self.previous_scale
                self.viewer.camera.center = self.previous_center
            # save viewer scale
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
            self.last_was_autofocus = False
        layer.refresh()

    def onDoubleClick(self, layer, event):
        """Handle double-click events and emit centered coordinates if within the data range."""
        coords = layer.world_to_data(event.position)
        layer_shape = layer.data.shape[0:2] if len(layer.data.shape) >= 3 else layer.data.shape

        if coords is not None and (0 <= int(coords[-1]) < layer_shape[-1] and (0 <= int(coords[-2]) < layer_shape[-2])):
            x_centered = int(coords[-1] - layer_shape[-1] / 2)
            y_centered = int(coords[-2] - layer_shape[-2] / 2)
            # Emit the centered coordinates and dimensions of the layer's data array
            self.signal_coordinates_clicked.emit(x_centered, y_centered, layer_shape[-1], layer_shape[-2])

    def set_live_configuration(self, live_configuration):
        self.live_configuration = live_configuration

    def updateContrastLimits(self, channel, min_val, max_val):
        self.contrastManager.update_limits(channel, min_val, max_val)
        if "Live View" in self.viewer.layers:
            self.viewer.layers["Live View"].contrast_limits = (min_val, max_val)

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(self.live_configuration.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()

    def activate(self):
        print("ACTIVATING NAPARI LIVE WIDGET")
        self.viewer.window.activate()


class NapariMultiChannelWidget(QWidget):

    def __init__(self, objectiveStore, camera, contrastManager, grid_enabled=False, parent=None):
        super().__init__(parent)
        # Initialize placeholders for the acquisition parameters
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.pixel_size_um = 1
        self.dz_um = 1
        self.Nz = 1
        self.layers_initialized = False
        self.acquisition_initialized = False
        self.viewer_scale_initialized = False
        self.update_layer_count = 0
        self.grid_enabled = grid_enabled

        # Initialize a napari Viewer without showing its standalone window.
        self.initNapariViewer()

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        if self.grid_enabled:
            self.viewer.grid.enabled = True
        self.viewer.dims.axis_labels = ["Z-axis", "Y-axis", "X-axis"]
        self.viewerWidget = self.viewer.window._qt_window
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def initLayersShape(self, Nz, dz):
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        if self.Nz != Nz or self.dz_um != dz or self.pixel_size_um != pixel_size_um:
            self.acquisition_initialized = False
            self.Nz = Nz
            self.dz_um = dz if Nz > 1 and dz != 0 else 1.0
            self.pixel_size_um = pixel_size_um

    def initChannels(self, channels):
        self.channels = set(channels)

    def extractWavelength(self, name):
        # Split the string and find the wavelength number immediately after "Fluorescence"
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]  # Assuming '488 nm Ex' and taking '488'
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Convert a HEX value to a normalized RGB tuple."""
        positions = [0, 1]
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,  # Normalize the Red component
            ((channel_info["hex"] >> 8) & 0xFF) / 255,  # Normalize the Green component
            (channel_info["hex"] & 0xFF) / 255,
        )  # Normalize the Blue component
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def initLayers(self, image_height, image_width, image_dtype):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        if self.acquisition_initialized:
            for layer in list(self.viewer.layers):
                if layer.name not in self.channels:
                    self.viewer.layers.remove(layer)
        else:
            self.viewer.layers.clear()
            self.acquisition_initialized = True
            if self.dtype != np.dtype(image_dtype) and not USE_NAPARI_FOR_LIVE_VIEW:
                self.contrastManager.scale_contrast_limits(image_dtype)

        self.image_width = image_width
        self.image_height = image_height
        self.dtype = np.dtype(image_dtype)
        self.layers_initialized = True
        self.update_layer_count = 0

    def updateLayers(self, image, x, y, k, channel_name):
        """Updates the appropriate slice of the canvas with the new image data."""
        rgb = len(image.shape) == 3

        # Check if the layer exists and has a different dtype
        if self.dtype != np.dtype(image.dtype):  # or self.viewer.layers[channel_name].data.dtype != image.dtype:
            # Remove the existing layer
            self.layers_initialized = False
            self.acquisition_initialized = False

        if not self.layers_initialized:
            self.initLayers(image.shape[0], image.shape[1], image.dtype)

        if channel_name not in self.viewer.layers:
            self.channels.add(channel_name)
            if rgb:
                color = None  # RGB images do not need a colormap
                canvas = np.zeros((self.Nz, self.image_height, self.image_width, 3), dtype=self.dtype)
            else:
                channel_info = CHANNEL_COLORS_MAP.get(
                    self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
                )
                if channel_info["name"] in AVAILABLE_COLORMAPS:
                    color = AVAILABLE_COLORMAPS[channel_info["name"]]
                else:
                    color = self.generateColormap(channel_info)
                canvas = np.zeros((self.Nz, self.image_height, self.image_width), dtype=self.dtype)

            limits = self.getContrastLimits(self.dtype)
            layer = self.viewer.add_image(
                canvas,
                name=channel_name,
                visible=True,
                rgb=rgb,
                colormap=color,
                contrast_limits=limits,
                blending="additive",
                scale=(self.dz_um, self.pixel_size_um, self.pixel_size_um),
            )

            # print(f"multi channel - dz_um:{self.dz_um}, pixel_y_um:{self.pixel_size_um}, pixel_x_um:{self.pixel_size_um}")
            layer.contrast_limits = self.contrastManager.get_limits(channel_name)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

            if not self.viewer_scale_initialized:
                self.resetView()
                self.viewer_scale_initialized = True
            else:
                layer.refresh()

        layer = self.viewer.layers[channel_name]
        layer.data[k] = image
        layer.contrast_limits = self.contrastManager.get_limits(channel_name)
        self.update_layer_count += 1
        if self.update_layer_count % len(self.channels) == 0:
            if self.Nz > 1:
                self.viewer.dims.set_point(0, k * self.dz_um)
            for layer in self.viewer.layers:
                layer.refresh()

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def activate(self):
        self.viewer.window.activate()


class NapariMosaicDisplayWidget(QWidget):

    signal_coordinates_clicked = Signal(float, float)  # x, y in mm
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal()
    signal_shape_drawn = Signal(list)

    def __init__(self, objectiveStore, camera, contrastManager, parent=None):
        super().__init__(parent)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewer.window._qt_window)
        self.layers_initialized = False
        self.shape_layer = None
        self.shapes_mm = []
        self.is_drawing_shape = False

        # add clear button
        self.clear_button = QPushButton("Clear Mosaic View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        self.layout.addWidget(self.clear_button)

        self.setLayout(self.layout)
        self.customizeViewer()
        self.viewer_pixel_size_mm = 1
        self.dz_um = None
        self.Nz = None
        self.channels = set()
        self.viewer_extents = []  # [min_y, max_y, min_x, max_x]
        self.top_left_coordinate = None  # [y, x] in mm
        self.mosaic_dtype = None

    def customizeViewer(self):
        # # hide status bar
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()
        self.viewer.bind_key("D", self.toggle_draw_mode)

    def toggle_draw_mode(self, viewer):
        self.is_drawing_shape = not self.is_drawing_shape

        if "Manual ROI" not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name="Manual ROI", edge_width=40, edge_color="red", face_color="transparent"
            )
            self.shape_layer.events.data.connect(self.on_shape_change)
        else:
            self.shape_layer = self.viewer.layers["Manual ROI"]

        if self.is_drawing_shape:
            # if there are existing shapes, switch to vertex select mode
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                # if no shapes exist, switch to add polygon mode
                # start drawing a new polygon on click, add vertices with additional clicks, finish/close polygon with double-click
                self.shape_layer.mode = "add_polygon"
        else:
            # if no shapes exist, switch to pan/zoom mode
            self.shape_layer.mode = "pan_zoom"

        self.on_shape_change()

    def enable_shape_drawing(self, enable):
        if enable:
            self.toggle_draw_mode(self.viewer)
        else:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.mode = "pan_zoom"

    def on_shape_change(self, event=None):
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # convert shapes to mm coordinates
            self.shapes_mm = [self.convert_shape_to_mm(shape) for shape in self.shape_layer.data]
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

    def convert_shape_to_mm(self, shape_data):
        shape_data_mm = []
        for point in shape_data:
            coords = self.viewer.layers[0].world_to_data(point)
            x_mm = self.top_left_coordinate[1] + coords[1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[0] * self.viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        return np.array(shape_data_mm)

    def convert_mm_to_viewer_shapes(self, shapes_mm):
        viewer_shapes = []
        for shape_mm in shapes_mm:
            viewer_shape = []
            for point_mm in shape_mm:
                x_data = (point_mm[0] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm
                y_data = (point_mm[1] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm
                world_coords = self.viewer.layers[0].data_to_world([y_data, x_data])
                viewer_shape.append(world_coords)
            viewer_shapes.append(viewer_shape)
        return viewer_shapes

    def update_shape_layer_position(self, prev_top_left, new_top_left):
        if self.shape_layer is None or len(self.shapes_mm) == 0:
            return
        try:
            # update top_left_coordinate
            self.top_left_coordinate = new_top_left

            # convert mm coordinates to viewer coordinates
            new_shapes = self.convert_mm_to_viewer_shapes(self.shapes_mm)

            # update shape layer data
            self.shape_layer.data = new_shapes
        except Exception as e:
            print(f"Error updating shape layer position: {e}")
            import traceback

            traceback.print_exc()

    def initChannels(self, channels):
        self.channels = set(channels)

    def initLayersShape(self, Nz, dz):
        self.Nz = 1
        self.dz_um = dz

    def extractWavelength(self, name):
        # extract wavelength from channel name
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        # generate colormap from hex value
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updateMosaic(self, image, x_mm, y_mm, k, channel_name):
        # calculate pixel size
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        downsample_factor = max(1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        image_pixel_size_um = pixel_size_um * downsample_factor
        image_pixel_size_mm = image_pixel_size_um / 1000
        image_dtype = image.dtype

        # downsample image
        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (image.shape[1] // downsample_factor, image.shape[0] // downsample_factor),
                interpolation=cv2.INTER_AREA,
            )

        # adjust image position
        x_mm -= (image.shape[1] * image_pixel_size_mm) / 2
        y_mm -= (image.shape[0] * image_pixel_size_mm) / 2

        if not self.viewer.layers:
            # initialize first layer
            self.layers_initialized = True
            self.signal_layers_initialized.emit()
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.viewer_extents = [
                y_mm,
                y_mm + image.shape[0] * image_pixel_size_mm,
                x_mm,
                x_mm + image.shape[1] * image_pixel_size_mm,
            ]
            self.top_left_coordinate = [y_mm, x_mm]
            self.mosaic_dtype = image_dtype
        else:
            # convert image dtype and scale if necessary
            image = self.convertImageDtype(image, self.mosaic_dtype)
            if image_pixel_size_mm != self.viewer_pixel_size_mm:
                scale_factor = image_pixel_size_mm / self.viewer_pixel_size_mm
                image = cv2.resize(
                    image,
                    (int(image.shape[1] * scale_factor), int(image.shape[0] * scale_factor)),
                    interpolation=cv2.INTER_LINEAR,
                )

        if channel_name not in self.viewer.layers:
            # create new layer for channel
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            layer = self.viewer.add_image(
                np.zeros_like(image),
                name=channel_name,
                rgb=len(image.shape) == 3,
                colormap=color,
                visible=True,
                blending="additive",
                scale=(self.viewer_pixel_size_mm * 1000, self.viewer_pixel_size_mm * 1000),
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

        # get layer for channel
        layer = self.viewer.layers[channel_name]

        # update extents
        self.viewer_extents[0] = min(self.viewer_extents[0], y_mm)
        self.viewer_extents[1] = max(self.viewer_extents[1], y_mm + image.shape[0] * self.viewer_pixel_size_mm)
        self.viewer_extents[2] = min(self.viewer_extents[2], x_mm)
        self.viewer_extents[3] = max(self.viewer_extents[3], x_mm + image.shape[1] * self.viewer_pixel_size_mm)

        # store previous top-left coordinate
        prev_top_left = self.top_left_coordinate.copy() if self.top_left_coordinate else None
        self.top_left_coordinate = [self.viewer_extents[0], self.viewer_extents[2]]

        # update layer
        self.updateLayer(layer, image, x_mm, y_mm, k, prev_top_left)

        # update contrast limits
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        scaled_min = self.convertValue(min_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        scaled_max = self.convertValue(max_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        layer.contrast_limits = (scaled_min, scaled_max)
        layer.refresh()

    def updateLayer(self, layer, image, x_mm, y_mm, k, prev_top_left):
        # calculate new mosaic size and position
        mosaic_height = int(math.ceil((self.viewer_extents[1] - self.viewer_extents[0]) / self.viewer_pixel_size_mm))
        mosaic_width = int(math.ceil((self.viewer_extents[3] - self.viewer_extents[2]) / self.viewer_pixel_size_mm))

        is_rgb = len(image.shape) == 3 and image.shape[2] == 3
        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            # calculate offsets for existing data
            y_offset = int(math.floor((prev_top_left[0] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
            x_offset = int(math.floor((prev_top_left[1] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

            for mosaic in self.viewer.layers:
                if mosaic.name != "Manual ROI":
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data = np.zeros((mosaic_height, mosaic_width, 3), dtype=mosaic.data.dtype)
                    else:
                        new_data = np.zeros((mosaic_height, mosaic_width), dtype=mosaic.data.dtype)

                    # ensure offsets don't exceed bounds
                    y_end = min(y_offset + mosaic.data.shape[0], new_data.shape[0])
                    x_end = min(x_offset + mosaic.data.shape[1], new_data.shape[1])

                    # shift existing data
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data[y_offset:y_end, x_offset:x_end, :] = mosaic.data[
                            : y_end - y_offset, : x_end - x_offset, :
                        ]
                    else:
                        new_data[y_offset:y_end, x_offset:x_end] = mosaic.data[: y_end - y_offset, : x_end - x_offset]
                    mosaic.data = new_data

            if "Manual ROI" in self.viewer.layers:
                self.update_shape_layer_position(prev_top_left, self.top_left_coordinate)

            self.resetView()

        # insert new image
        y_pos = int(math.floor((y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
        x_pos = int(math.floor((x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

        # ensure indices are within bounds
        y_end = min(y_pos + image.shape[0], layer.data.shape[0])
        x_end = min(x_pos + image.shape[1], layer.data.shape[1])

        # insert image data
        if is_rgb:
            layer.data[y_pos:y_end, x_pos:x_end, :] = image[: y_end - y_pos, : x_end - x_pos, :]
        else:
            layer.data[y_pos:y_end, x_pos:x_end] = image[: y_end - y_pos, : x_end - x_pos]
        layer.refresh()

    def convertImageDtype(self, image, target_dtype):
        # convert image to target dtype
        if image.dtype == target_dtype:
            return image

        # get full range of values for both dtypes
        if np.issubdtype(image.dtype, np.integer):
            input_info = np.iinfo(image.dtype)
            input_min, input_max = input_info.min, input_info.max
        else:
            input_min, input_max = np.min(image), np.max(image)

        if np.issubdtype(target_dtype, np.integer):
            output_info = np.iinfo(target_dtype)
            output_min, output_max = output_info.min, output_info.max
        else:
            output_min, output_max = 0.0, 1.0

        # normalize and scale image
        image_normalized = (image.astype(np.float64) - input_min) / (input_max - input_min)
        image_scaled = image_normalized * (output_max - output_min) + output_min

        return image_scaled.astype(target_dtype)

    def convertValue(self, value, from_dtype, to_dtype):
        # Convert value from one dtype range to another
        from_info = np.iinfo(from_dtype)
        to_info = np.iinfo(to_dtype)

        # Normalize the value to [0, 1] range
        normalized = (value - from_info.min) / (from_info.max - from_info.min)

        # Scale to the target dtype range
        return normalized * (to_info.max - to_info.min) + to_info.min

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)

        # Convert the new limits from mosaic_dtype to acquisition_dtype
        acquisition_min = self.convertValue(min_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)
        acquisition_max = self.convertValue(max_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)

        # Update the ContrastManager with the new limits
        self.contrastManager.update_limits(layer.name, acquisition_min, acquisition_max)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def onDoubleClick(self, layer, event):
        coords = layer.world_to_data(event.position)
        if coords is not None:
            x_mm = self.top_left_coordinate[1] + coords[-1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[-2] * self.viewer_pixel_size_mm
            print(f"move from click: ({x_mm:.6f}, {y_mm:.6f})")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clear_shape(self):
        if self.shape_layer is not None:
            self.viewer.layers.remove(self.shape_layer)
            self.shape_layer = None
            self.is_drawing_shape = False
            self.signal_shape_drawn.emit([])

    def clearAllLayers(self):
        # Keep the Manual ROI layer and clear the content of all other layers
        for layer in self.viewer.layers:
            if layer.name == "Manual ROI":
                continue

            if hasattr(layer, "data") and hasattr(layer.data, "shape"):
                # Create an empty array matching the layer's dimensions
                if len(layer.data.shape) == 3 and layer.data.shape[2] == 3:  # RGB
                    empty_data = np.zeros((layer.data.shape[0], layer.data.shape[1], 3), dtype=layer.data.dtype)
                else:  # Grayscale
                    empty_data = np.zeros((layer.data.shape[0], layer.data.shape[1]), dtype=layer.data.dtype)

                layer.data = empty_data

        self.channels = set()

        for layer in self.viewer.layers:
            layer.refresh()

        self.signal_clear_viewer.emit()

    def activate(self):
        self.viewer.window.activate()


class WaveformDisplay(QFrame):

    def __init__(self, N=1000, include_x=True, include_y=True, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.N = N
        self.include_x = include_x
        self.include_y = include_y
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.plotWidget = {}
        self.plotWidget["X"] = PlotWidget("X", N=self.N, add_legend=True)
        self.plotWidget["Y"] = PlotWidget("X", N=self.N, add_legend=True)

        layout = QGridLayout()  # layout = QStackedLayout()
        if self.include_x:
            layout.addWidget(self.plotWidget["X"], 0, 0)
        if self.include_y:
            layout.addWidget(self.plotWidget["Y"], 1, 0)
        self.setLayout(layout)

    def plot(self, time, data):
        if self.include_x:
            self.plotWidget["X"].plot(time, data[0, :], "X", color=(255, 255, 255), clear=True)
        if self.include_y:
            self.plotWidget["Y"].plot(time, data[1, :], "Y", color=(255, 255, 255), clear=True)

    def update_N(self, N):
        self.N = N
        self.plotWidget["X"].update_N(N)
        self.plotWidget["Y"].update_N(N)


class PlotWidget(pg.GraphicsLayoutWidget):

    def __init__(self, title="", N=1000, parent=None, add_legend=False):
        super().__init__(parent)
        self.plotWidget = self.addPlot(title="", axisItems={"bottom": pg.DateAxisItem()})
        if add_legend:
            self.plotWidget.addLegend()
        self.N = N

    def plot(self, x, y, label, color, clear=False):
        self.plotWidget.plot(x[-self.N :], y[-self.N :], pen=pg.mkPen(color=color, width=4), name=label, clear=clear)

    def update_N(self, N):
        self.N = N


class SurfacePlotWidget(QWidget):
    """
    A widget that displays a 3D surface plot of the coordinates.
    """

    signal_point_clicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)

        # Setup canvas and figure
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")

        layout = QVBoxLayout()
        layout.addWidget(self.canvas)
        self.setLayout(layout)

        self.selected_index = None
        self.plot_populated = False

        # Connect events
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("button_press_event", self.on_click)

        self.x = list()
        self.y = list()
        self.z = list()
        self.regions = list()

    def clear(self):
        self.x.clear()
        self.y.clear()
        self.z.clear()
        self.regions.clear()

    def add_point(self, x: float, y: float, z: float, region: int):
        self.x.append(x)
        self.y.append(y)
        self.z.append(z)
        self.regions.append(region)

    def plot(self) -> None:
        """
        Plot both surface and scatter points in 3D.

        Args:
            x (np.array): X coordinates (1D array)
            y (np.array): Y coordinates (1D array)
            z (np.array): Z coordinates (1D array)
        """
        try:
            # Clear previous plot
            self.ax.clear()

            x = np.array(self.x).astype(float)
            y = np.array(self.y).astype(float)
            z = np.array(self.z).astype(float)
            regions = np.array(self.regions)

            # plot surface by region
            for r in np.unique(regions):
                try:
                    mask = regions == r
                    num_points = np.sum(mask)
                    if num_points >= 4:
                        grid_x, grid_y = np.mgrid[min(x[mask]) : max(x[mask]) : 10j, min(y[mask]) : max(y[mask]) : 10j]
                        grid_z = griddata((x[mask], y[mask]), z[mask], (grid_x, grid_y), method="cubic")
                        self.ax.plot_surface(grid_x, grid_y, grid_z, cmap="viridis", edgecolor="none")
                    else:
                        self._log.debug(f"Region {r} has only {num_points} point(s), skipping surface interpolation")
                except Exception as e:
                    raise Exception(f"Cannot plot region {r}: {e}")

            # Create scatter plot using original coordinates
            self.colors = ["r"] * len(x)
            self.scatter = self.ax.scatter(x, y, z, c=self.colors, s=30)

            # Set labels
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_zlabel("Z (um)")
            self.ax.set_title("Double-click a point to go to that position")

            # Force x and y to have same scale
            max_range = max(np.ptp(x), np.ptp(y))
            center_x = np.mean(x)
            center_y = np.mean(y)

            self.ax.set_xlim(center_x - max_range / 2, center_x + max_range / 2)
            self.ax.set_ylim(center_y - max_range / 2, center_y + max_range / 2)

            self.canvas.draw()
            self.plot_populated = True
        except Exception as e:
            self._log.error(f"Error plotting surface: {e}")

    def on_scroll(self, event):
        scale = 1.1 if event.button == "up" else 0.9

        def zoom(lim):
            center = (lim[0] + lim[1]) / 2
            half_range = (lim[1] - lim[0]) / 2 * scale
            return center - half_range, center + half_range

        self.ax.set_xlim(zoom(self.ax.get_xlim()))
        self.ax.set_ylim(zoom(self.ax.get_ylim()))
        self.ax.set_zlim(zoom(self.ax.get_zlim()))
        self.canvas.draw()

    def on_click(self, event):
        if not self.plot_populated:
            return
        if not event.dblclick or event.inaxes != self.ax:
            return

        # Cancel drag mode after double-click
        self.canvas.button_pressed = None  # FIX: Avoids AttributeError

        # Project 3D points to 2D screen space
        x2d, y2d, _ = proj3d.proj_transform(self.x, self.y, self.z, self.ax.get_proj())
        dists = np.hypot(x2d - event.xdata, y2d - event.ydata)
        idx = np.argmin(dists)

        # Threshold in data coordinates
        display_thresh = 0.05 * max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0], self.ax.get_ylim()[1] - self.ax.get_ylim()[0]
        )
        if dists[idx] > display_thresh:
            return

        # Change point color
        self.colors = ["r"] * len(self.x)
        self.colors[idx] = "g"
        self.scatter.remove()
        self.scatter = self.ax.scatter(self.x, self.y, self.z, c=self.colors, s=30)

        print(f"Clicked Point: x={self.x[idx]:.3f}, y={self.y[idx]:.3f}, z={self.z[idx]:.3f}")
        self.canvas.draw()
        self.signal_point_clicked.emit(self.x[idx], self.y[idx])
