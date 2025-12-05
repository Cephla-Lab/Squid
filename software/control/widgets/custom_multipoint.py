from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *
import pandas as pd
import numpy as np
import time

from control.widgets import FlexibleMultiPointWidget
import squid.logging


class TemplateMultiPointWidget(FlexibleMultiPointWidget):
    def __init__(self, multipointController, stage, main=None, *args, **kwargs):
        # Initialize templates dict
        self.templates = {}
        self._log = squid.logging.get_logger(self.__class__.__name__)

        # Call parent constructor
        super().__init__(multipointController, stage, main, *args, **kwargs)
        self.region_id = 0

    def add_components(self):
        # Call parent's add_components to set up base UI
        super().add_components()

        """
        # Remove add and remove buttons
        self.btn_add.hide()
        self.btn_remove.hide()

        # Remove Nx, Ny, and overlap components
        # Find and hide these in the layout
        for i in range(self.grid.count()):
            item = self.grid.itemAt(i)
            if item and item.layout():
                # Check if this is the layout containing Nx, Ny or overlap
                layout = item.layout()
                for j in range(layout.count()):
                    widget = layout.itemAt(j).widget()
                    if widget and isinstance(widget, QLabel):
                        if widget.text() in ["Nx", "Ny", "FOV Overlap"]:
                            # Hide this layout row
                            for k in range(layout.count()):
                                w = layout.itemAt(k).widget()
                                if w:
                                    w.hide()
        """

        # Add new template components
        self.btn_load_template = QPushButton("Load Template")
        self.btn_add_from_template = QPushButton("Add Using Template")
        self.dropdown_template = QComboBox()
        self.dropdown_template.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Create template controls layout
        temp = QHBoxLayout()
        temp.addWidget(QLabel("Template     "))
        temp.addWidget(self.dropdown_template)
        self.grid_template = QGridLayout()
        self.grid_template.addLayout(temp, 0, 0, 1, 6)  # Span across all columns except the last
        self.grid_template.addWidget(self.btn_load_template, 0, 6, 1, 2)  # Align with other buttons

        # a row with add using template, next, clear
        self.grid_add_next_clear = QGridLayout()
        self.grid_add_next_clear.addWidget(self.btn_add_from_template, 0, 0, 1, 4)
        self.grid_add_next_clear.addWidget(self.btn_next, 0, 4, 1, 2)
        self.grid_add_next_clear.addWidget(self.btn_clear, 0, 6, 1, 2)

        # adjust stretch factors
        for i in range(4):
            self.grid_template.setColumnStretch(i, 1)
            self.grid_template.setColumnStretch(i + 4, 1)
            self.grid_add_next_clear.setColumnStretch(i, 1)
            self.grid_add_next_clear.setColumnStretch(i + 4, 1)

    def setup_layout(self):
        self.grid = QVBoxLayout()
        self.grid.addLayout(self.grid_line0)
        self.grid.addLayout(self.grid_template)
        self.grid.addLayout(self.grid_location_list_line1)
        self.grid.addLayout(self.grid_add_next_clear)
        self.grid.addLayout(self.grid_location_list_line3)
        self.grid.addLayout(self.grid_acquisition)
        self.grid.addLayout(self.row_progress_layout)
        self.setLayout(self.grid)

        self.grid_location_list_line3.setEnabled(False)

    def setup_connections(self):
        super().setup_connections()
        self.btn_load_template.clicked.connect(self.load_template)
        self.btn_add_from_template.clicked.connect(self.add_from_template)

    def load_template(self):
        """Load a template CSV file with predefined positions"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Template", "", "CSV Files (*.csv);;All Files (*)")
        if not file_path:
            return

        try:
            template_df = pd.read_csv(file_path)
            template_name = QFileInfo(file_path).baseName()

            # Store template
            self.templates[template_name] = template_df

            # Update dropdown
            if template_name not in [self.dropdown_template.itemText(i) for i in range(self.dropdown_template.count())]:
                self.dropdown_template.addItem(template_name)
                self.dropdown_template.setCurrentText(template_name)

            self._log.info(f"Loaded template '{template_name}' with {len(template_df)} positions")

        except Exception as e:
            QMessageBox.warning(self, "Template Error", f"Failed to load template: {str(e)}")

    def add_from_template(self):
        """Add locations from the selected template"""
        self.region_id = self.region_id + 1

        template_name = self.dropdown_template.currentText()
        if not template_name or template_name not in self.templates:
            QMessageBox.warning(self, "Template Error", "No template selected")
            return

        template_df = self.templates[template_name]

        # Get current stage position to use as reference
        ref_x, ref_y, ref_z = self.stage.get_pos().x_mm, self.stage.get_pos().y_mm, self.stage.get_pos().z_mm

        # Check required columns
        if not all(col in template_df.columns for col in ["x_offset_mm", "y_offset_mm"]):
            QMessageBox.warning(
                self, "Template Error", "Template must contain 'x_offset_mm', and 'y_offset_mm' columns"
            )
            return

        self.table_location_list.blockSignals(True)
        self.dropdown_location_list.blockSignals(True)

        # Apply template positions to current position
        for _, row in template_df.iterrows():
            x = ref_x + row["x_offset_mm"]
            y = ref_y + row["y_offset_mm"]

            # Store actual values in location_list
            self.location_list = np.vstack((self.location_list, [[x, y, ref_z]]))
            self.location_ids = np.append(self.location_ids, f"R{len(self.location_ids)}")

            location_str = f"x:{round(x,3)} mm  y:{round(y,3)} mm  z:{round(ref_z*1000,1)} μm"
            self.dropdown_location_list.addItem(location_str)

            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(row, 2, QTableWidgetItem(str(round(ref_z * 1000, 1))))
            self.table_location_list.setItem(row, 3, QTableWidgetItem(str(self.region_id)))

        self.scanCoordinates.add_template_region(
            ref_x, ref_y, ref_z, template_df["x_offset_mm"], template_df["y_offset_mm"], str(self.region_id)
        )
        self._log.info(f"Added {len(template_df)} locations from template '{template_name}'")

        # Release signal blocks
        self.table_location_list.blockSignals(False)
        self.dropdown_location_list.blockSignals(False)

    def clear(self):
        super().clear()
        self.region_id = 0
