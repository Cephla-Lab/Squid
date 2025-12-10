# Focus map widget for managing focus points and surface fitting
from __future__ import annotations

import csv
from typing import Optional, TYPE_CHECKING, List, Tuple, Dict

from qtpy.QtGui import QResizeEvent
from qtpy.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
)

from control._def import SOFTWARE_POS_LIMIT
from squid.events import EventBus, StagePositionChanged, MoveStageToCommand

if TYPE_CHECKING:
    from control.core.navigation import NavigationViewer, ScanCoordinates, FocusMap


class FocusMapWidget(QFrame):
    """Widget for managing focus map points and surface fitting"""

    _allow_updating_focus_points_on_signal: bool
    _event_bus: EventBus
    _cached_x_mm: float
    _cached_y_mm: float
    _cached_z_mm: float
    navigationViewer: NavigationViewer
    scanCoordinates: ScanCoordinates
    focusMap: FocusMap
    focus_points: List[Tuple[str, float, float, float]]
    enabled: bool
    add_margin: bool

    # UI components
    _layout: QVBoxLayout
    point_combo: QComboBox
    update_z_btn: QPushButton
    add_point_btn: QPushButton
    remove_point_btn: QPushButton
    next_point_btn: QPushButton
    edit_point_btn: QPushButton
    rows_spin: QSpinBox
    cols_spin: QSpinBox
    export_btn: QPushButton
    import_btn: QPushButton
    fit_method_combo: QComboBox
    smoothing_spin: QDoubleSpinBox
    by_region_checkbox: QCheckBox
    status_label: QLabel

    def __init__(
        self,
        navigationViewer: "NavigationViewer",
        scanCoordinates: "ScanCoordinates",
        focusMap: "FocusMap",
        event_bus: EventBus,
        initial_z_mm: float = 0.0,
    ) -> None:
        super().__init__()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self._allow_updating_focus_points_on_signal = True

        # Store event bus and cached position
        self._event_bus = event_bus
        self._cached_x_mm = 0.0
        self._cached_y_mm = 0.0
        self._cached_z_mm = initial_z_mm

        # Store references
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

        # Subscribe to stage position events
        self._event_bus.subscribe(StagePositionChanged, self._on_stage_position_changed)

    def _on_stage_position_changed(self, event: StagePositionChanged) -> None:
        """Cache stage position from EventBus."""
        self._cached_x_mm = event.x_mm
        self._cached_y_mm = event.y_mm
        self._cached_z_mm = event.z_mm

    def setup_ui(self) -> None:
        """Create and arrange UI components"""
        self._layout = QVBoxLayout(self)

        # Point combo and Z control
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Focus Point:"))
        self.point_combo = QComboBox()
        controls_layout.addWidget(self.point_combo, stretch=1)
        self.update_z_btn = QPushButton("Update Z")
        controls_layout.addWidget(self.update_z_btn)
        self._layout.addLayout(controls_layout)

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
        self._layout.addLayout(point_controls)

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
        self._layout.addLayout(point_controls_2)

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
        self._layout.addLayout(settings_layout)

        # Status label - reserve space even when hidden
        self.status_label = QLabel()
        self.status_label.setText(" ")  # Empty text to keep space
        self._layout.addWidget(self.status_label)

    def make_connections(self) -> None:
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

    def update_point_list(self) -> None:
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
        self.point_combo.setCurrentIndex(
            max(0, min(curr_focus_point, len(self.focus_points) - 1))
        )
        self.point_combo.blockSignals(False)

    def edit_current_point(self) -> None:
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
            x_spin.setRange(
                SOFTWARE_POS_LIMIT.X_NEGATIVE, SOFTWARE_POS_LIMIT.X_POSITIVE
            )
            x_spin.setDecimals(3)
            x_spin.setValue(x)
            x_spin.setSuffix(" mm")

            y_spin = QDoubleSpinBox()
            y_spin.setKeyboardTracking(False)
            y_spin.setRange(
                SOFTWARE_POS_LIMIT.Y_NEGATIVE, SOFTWARE_POS_LIMIT.Y_POSITIVE
            )
            y_spin.setDecimals(3)
            y_spin.setValue(y)
            y_spin.setSuffix(" mm")

            z_spin = QDoubleSpinBox()
            z_spin.setKeyboardTracking(False)
            z_spin.setRange(
                SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000,
                SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000,
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

    def update_focus_point_display(self) -> None:
        """Update all focus points on navigation viewer"""
        self.navigationViewer.clear_focus_points()
        for _, x, y, _ in self.focus_points:
            self.navigationViewer.register_focus_point(x, y)

    def generate_grid(self, rows: int = 4, cols: int = 4) -> None:
        """Generate focus point grid that spans scan bounds"""
        if self.enabled:
            self.point_combo.blockSignals(True)
            self.focus_points.clear()
            self.navigationViewer.clear_focus_points()
            self.status_label.setText(" ")
            current_z = self._cached_z_mm

            # Use FocusMap to generate coordinates
            coordinates = self.focusMap.generate_grid_coordinates(
                self.scanCoordinates, rows=rows, cols=cols, add_margin=self.add_margin
            )

            # Add points with current z coordinate
            for region_id, coords_list in coordinates.items():
                for coords in coords_list:
                    self.focus_points.append(
                        (region_id, coords[0], coords[1], current_z)
                    )
                    self.navigationViewer.register_focus_point(coords[0], coords[1])

            self.update_point_list()
            self.point_combo.blockSignals(False)

    def regenerate_grid(self) -> None:
        """Generate focus point grid given updated dims"""
        self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def add_current_point(self) -> None:
        # Check if any scan regions exist
        if not self.scanCoordinates.has_regions():
            QMessageBox.warning(
                self,
                "No Regions Defined",
                "Please define scan regions before adding focus points.",
            )
            return

        # Use cached position from events
        x_mm = self._cached_x_mm
        y_mm = self._cached_y_mm
        z_mm = self._cached_z_mm
        region_id = None

        # If by_region checkbox is checked, ask for region ID
        if self.by_region_checkbox.isChecked():
            region_ids = list(self.scanCoordinates.region_centers.keys())
            if not region_ids:
                QMessageBox.warning(
                    self,
                    "No Regions Defined",
                    "Please define scan regions before adding focus points.",
                )
                return

            region_id, ok = QInputDialog.getItem(
                self,
                "Select Region",
                "Choose a region:",
                [str(r) for r in region_ids],
                0,
                False,
            )
            if not ok or not region_id:
                return
            region_id = str(region_id)  # Ensure string format
        else:
            # Find the closest region to current position
            closest_region = None
            min_distance = float("inf")
            for rid, center in self.scanCoordinates.region_centers.items():
                dx = center[0] - x_mm
                dy = center[1] - y_mm
                distance = dx * dx + dy * dy
                if distance < min_distance:
                    min_distance = distance
                    closest_region = rid
            region_id = closest_region

        if region_id is not None:
            self.focus_points.append((region_id, x_mm, y_mm, z_mm))
            self.update_point_list()
            self.navigationViewer.register_focus_point(x_mm, y_mm)
        else:
            QMessageBox.warning(
                self,
                "Region Error",
                "Could not determine a valid region for this focus point.",
            )

    def remove_current_point(self) -> None:
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            self.focus_points.pop(index)
            self.update_point_list()
            self.update_focus_point_display()

    def goto_next_point(self) -> None:
        if not self.focus_points:
            return
        current = self.point_combo.currentIndex()
        next_index = (current + 1) % len(self.focus_points)
        self.point_combo.setCurrentIndex(next_index)
        self.goto_selected_point()

    def goto_selected_point(self) -> None:
        if self.enabled:
            index = self.point_combo.currentIndex()
            if 0 <= index < len(self.focus_points):
                _, x, y, z = self.focus_points[index]
                self._move_stage_to(x, y, z)

    def _move_stage_to(self, x: float, y: float, z: float) -> None:
        """Move stage to position via EventBus."""
        self._event_bus.publish(MoveStageToCommand(x_mm=x, y_mm=y, z_mm=z))

    def update_current_z(self) -> None:
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            new_z = self._cached_z_mm
            region_id, x, y, _ = self.focus_points[index]
            self.focus_points[index] = (region_id, x, y, new_z)
            self.update_point_list()

    def get_region_points_dict(self) -> Dict[str, List[Tuple[float, float, float]]]:
        points_dict: Dict[str, List[Tuple[float, float, float]]] = {}
        for region_id, x, y, z in self.focus_points:
            if region_id not in points_dict:
                points_dict[region_id] = []
            points_dict[region_id].append((x, y, z))
        return points_dict

    def fit_surface(self) -> bool:
        try:
            method = self.fit_method_combo.currentText()
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            by_region = self.by_region_checkbox.isChecked()

            # Validate settings
            if by_region:
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(
                    region_id for region_id, _, _, _ in self.focus_points
                )
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

    def _match_by_region_box(self) -> None:
        if self.fit_method_combo.currentText() == "constant":
            self.by_region_checkbox.setChecked(True)

    def export_focus_points(self) -> None:
        """Export focus points to a CSV file"""
        if not self.focus_points:
            QMessageBox.warning(
                self, "No Focus Points", "There are no focus points to export."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Focus Points", "", "CSV Files (*.csv);;All Files (*)"
        )
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

            self.status_label.setText(
                f"Exported {len(self.focus_points)} points to {file_path}"
            )

        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export focus points: {str(e)}"
            )

    def import_focus_points(self) -> None:
        """Import focus points from a CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Focus Points", "", "CSV Files (*.csv);;All Files (*)"
        )

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
                        self,
                        "Invalid Format",
                        f"CSV file must contain columns: {', '.join(required_columns)}",
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
            QMessageBox.critical(
                self, "Import Error", f"Failed to import focus points: {str(e)}"
            )

    def on_regions_updated(self) -> None:
        if not self._allow_updating_focus_points_on_signal:
            return
        if self.scanCoordinates.has_regions():
            self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def disable_updating_focus_points_on_signal(self) -> None:
        self._allow_updating_focus_points_on_signal = False

    def enable_updating_focus_points_on_signal(self) -> None:
        self._allow_updating_focus_points_on_signal = True

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled
        super().setEnabled(enabled)
        self.navigationViewer.focus_point_overlay_item.setVisible(enabled)
        self.on_regions_updated()

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        """Handle resize events to maintain button sizing"""
        super().resizeEvent(event)  # type: ignore[arg-type]
        self.update_z_btn.setFixedWidth(self.edit_point_btn.width())
