# stitcher_process_gui.py
import os
import sys
import time
from multiprocessing import Queue, Event
import napari
import numpy as np
from PyQt5.QtWidgets import (QApplication, QWidget, QGridLayout, QVBoxLayout, 
                            QPushButton, QLabel, QProgressBar, QComboBox, QMessageBox, 
                            QCheckBox, QSpinBox, QLineEdit, QFileDialog)
from PyQt5.QtCore import QTimer, Qt
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
from queue import Empty

from control.stitcher.parameters import StitchingParameters
from control.stitcher.stitcher_process import StitcherProcess

CHANNEL_COLORS_MAP = {
    "405": {"hex": 0x3300FF, "name": "blue"},
    "488": {"hex": 0x1FFF00, "name": "green"},
    "561": {"hex": 0xFFCF00, "name": "yellow"},
    "638": {"hex": 0xFF0000, "name": "red"},
    "730": {"hex": 0x770000, "name": "dark red"},
    "R": {"hex": 0xFF0000, "name": "red"},
    "G": {"hex": 0x1FFF00, "name": "green"},
    "B": {"hex": 0x3300FF, "name": "blue"},
}

"""
Cephla-Lab: Squid Microscopy Image Stitching GUI (soham mukherjee)

Usage: python3 -m control.stitcher.stitcher_process_gui

"""

class StitchingGUI(QWidget):
    def __init__(self):
        super().__init__()
        # Initialize process management
        self.stitcher_process = None
        self.progress_queue = Queue()
        self.status_queue = Queue()
        self.complete_queue = Queue()
        self.stop_event = Event()
        
        # Create timer for checking queues
        self.queue_timer = QTimer()
        self.queue_timer.timeout.connect(self.check_queues)
        self.queue_timer.start(100)  # Check every 100ms
        
        # State variables
        self.input_directory = None
        self.output_path = ""
        self.dtype = None
        self.contrast_limits = None
        self.init_ui()

    def check_queues(self):
        """Check for updates from the stitching process"""
        # Check progress updates
        try:
            while True:
                msg_type, data = self.progress_queue.get_nowait()
                if self.start_btn.isChecked() and msg_type == 'progress':
                    current, total = data
                    self.progress_bar.setVisible(True)
                    self.progress_bar.setRange(0, total)
                    self.progress_bar.setValue(current)
        except Empty:
            pass

        # Check status updates
        try:
            while True:
                msg_type, data = self.status_queue.get_nowait()
                if self.start_btn.isChecked() and msg_type == 'status':
                    status, is_saving = data
                    self.status_label.setText(f"Status: {status}")
                    if is_saving:
                        self.progress_bar.setRange(0, 0)  # Indeterminate mode
                elif msg_type == 'error':
                    QMessageBox.critical(self, "Error", str(data))
                    self.stop_stitching()
        except Empty:
            pass

        # Check completion updates
        try:
            msg_type, data = self.complete_queue.get_nowait()
            if msg_type == 'complete':
                output_path, dtype = data
                self.saving_complete(output_path, dtype)
        except Empty:
            pass

    def init_ui(self):
        """Initialize the user interface."""
        self.layout = QVBoxLayout(self)

        # Input Directory Selection
        self.input_directory_btn = QPushButton('Select Input Directory', self)
        self.input_directory_btn.clicked.connect(self.select_input_directory)
        self.layout.addWidget(self.input_directory_btn)

        # Grid for checkboxes and registration inputs
        grid = QGridLayout()

        # Checkboxes
        self.apply_flatfield_checkbox = QCheckBox("Flatfield Correction", self)
        grid.addWidget(self.apply_flatfield_checkbox, 0, 0)

        self.use_registration_checkbox = QCheckBox('Cross-Correlation Registration', self)
        self.use_registration_checkbox.toggled.connect(self.use_registration_checked)
        grid.addWidget(self.use_registration_checkbox, 1, 0)

        self.merge_timepoints_checkbox = QCheckBox('Merge Timepoints', self)
        self.merge_timepoints_checkbox.setChecked(False)
        grid.addWidget(self.merge_timepoints_checkbox, 2, 0)

        self.merge_regions_checkbox = QCheckBox('Merge HCS Regions', self)
        self.merge_regions_checkbox.setChecked(False)
        grid.addWidget(self.merge_regions_checkbox, 3, 0)

        # Registration inputs
        self.channel_label = QLabel('Registration Channel', self)
        self.channel_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.channel_combo = QComboBox(self)
        grid.addWidget(self.channel_label, 0, 2)
        grid.addWidget(self.channel_combo, 0, 3)

        self.z_level_label = QLabel('Registration Z-Level', self)
        self.z_level_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.z_level_input = QSpinBox(self)
        self.z_level_input.setMinimum(0)
        self.z_level_input.setMaximum(100)
        grid.addWidget(self.z_level_label, 1, 2)
        grid.addWidget(self.z_level_input, 1, 3)

        self.layout.addLayout(grid)

        # Output format combo box
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(['OME-ZARR', 'OME-TIFF'])
        self.output_format_combo.currentTextChanged.connect(self.output_format_changed)
        self.layout.addWidget(self.output_format_combo)

        # Status label
        self.status_label = QLabel('Status: Ready', self)
        self.layout.addWidget(self.status_label)

        # Start/Stop button
        self.start_btn = QPushButton('Start Stitching', self)
        self.start_btn.setCheckable(True)
        self.start_btn.clicked.connect(self.toggle_stitching)
        self.layout.addWidget(self.start_btn)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.layout.addWidget(self.progress_bar)

        # Output path edit
        self.output_path_edit = QLineEdit(self)
        self.output_path_edit.setPlaceholderText("Enter Filepath To Visualize (No Stitching Required)")
        self.output_path_edit.textChanged.connect(self.output_path_changed)
        self.layout.addWidget(self.output_path_edit)

        # View button
        self.view_btn = QPushButton('View Output in Napari', self)
        self.view_btn.clicked.connect(self.view_output_napari)
        self.view_btn.setEnabled(False)
        self.layout.addWidget(self.view_btn)

        # Initialize UI state
        self.setWindowTitle('Image Stitcher')
        self.setGeometry(300, 300, 500, 300)
        
        # Initially hide registration inputs
        self.z_level_label.hide()
        self.z_level_input.hide()
        self.channel_label.hide()
        self.channel_combo.hide()
        
        self.show()

    def select_input_directory(self):
        """Handle input directory selection."""
        self.input_directory = QFileDialog.getExistingDirectory(self, "Select Input Image Folder")
        if self.input_directory:
            self.input_directory_btn.setText(f'Selected: {self.input_directory}')
            self.use_registration_checked(self.use_registration_checkbox.isChecked())

    def use_registration_checked(self, checked):
        """Handle registration checkbox state change."""
        self.z_level_label.setVisible(checked)
        self.z_level_input.setVisible(checked)
        self.channel_label.setVisible(checked)
        self.channel_combo.setVisible(checked)

        if checked:
            if not self.input_directory:
                QMessageBox.warning(self, "Input Error", "Please Select an Input Image Folder First")
                self.use_registration_checkbox.setChecked(False)
                return

            try:
                # Create temporary params with minimal required parameters
                temp_params = StitchingParameters(
                    input_folder=self.input_directory
                )
                
                # Create temporary stitcher to parse filenames
                temp_stitcher = StitcherProcess(
                    params=temp_params,
                    progress_queue=Queue(),
                    status_queue=Queue(),
                    complete_queue=Queue(),
                    stop_event=Event()
                )
                
                # Get initial metadata
                temp_stitcher.get_timepoints()
                temp_stitcher.extract_acquisition_parameters()
                temp_stitcher.parse_acquisition_metadata()
                
                # Setup Z-Level
                self.z_level_input.setMinimum(0)
                self.z_level_input.setMaximum(temp_stitcher.num_z - 1)

                # Setup channel dropdown
                self.channel_combo.clear()
                self.channel_combo.addItems(temp_stitcher.channel_names)
                
            except Exception as e:
                QMessageBox.critical(self, "Parsing Error", f"An error occurred during data processing: {e}")
                self.use_registration_checkbox.setChecked(False)
                self.z_level_label.hide()
                self.z_level_input.hide()
                self.channel_label.hide()
                self.channel_combo.hide()

    def output_format_changed(self, format_text):
        """Handle output format changes."""
        is_zarr = format_text == 'OME-ZARR'
        self.merge_timepoints_checkbox.setEnabled(is_zarr)
        self.merge_regions_checkbox.setEnabled(is_zarr)
        if not is_zarr:
            self.merge_timepoints_checkbox.setChecked(False)
            self.merge_regions_checkbox.setChecked(False)

    def output_path_changed(self, text):
        """Enable/disable view button based on output path content."""
        self.view_btn.setEnabled(bool(text.strip()))

    def toggle_stitching(self):
        """Handle start/stop button toggle."""
        if self.start_btn.isChecked():
            self.start_stitching()
        else:
            self.stop_stitching()

    def start_stitching(self):
        """Start the stitching process."""
        if not self.input_directory:
            self.start_btn.setChecked(False)
            QMessageBox.warning(self, "Input Error", "Please select input directory")
            return

        try:
            # Reset state for new process
            self.stop_event = Event()
            
            # Disable controls during processing
            self.input_directory_btn.setEnabled(False)
            self.output_format_combo.setEnabled(False)
            self.apply_flatfield_checkbox.setEnabled(False)
            self.use_registration_checkbox.setEnabled(False)
            self.merge_timepoints_checkbox.setEnabled(False)
            self.merge_regions_checkbox.setEnabled(False)
            self.channel_combo.setEnabled(False)
            self.z_level_input.setEnabled(False)
            
            params = StitchingParameters(
                input_folder=self.input_directory,
                output_format='.' + self.output_format_combo.currentText().lower().replace('-', '.'),
                apply_flatfield=self.apply_flatfield_checkbox.isChecked(),
                use_registration=self.use_registration_checkbox.isChecked(),
                registration_channel=self.channel_combo.currentText() if self.use_registration_checkbox.isChecked() else '',
                registration_z_level=self.z_level_input.value() if self.use_registration_checkbox.isChecked() else 0,
                scan_pattern='Unidirectional',
                merge_timepoints=self.merge_timepoints_checkbox.isChecked(),
                merge_hcs_regions=self.merge_regions_checkbox.isChecked()
            )

            self.stitcher = StitcherProcess(
                params=params,
                progress_queue=self.progress_queue, 
                status_queue=self.status_queue,
                complete_queue=self.complete_queue,
                stop_event=self.stop_event
            )
            
            self.stitcher.start()
            self.start_btn.setText('Stop Stitching')
            self.status_label.setText('Status: Initializing Stitching...')
            
        except Exception as e:
            self.start_btn.setChecked(False)
            QMessageBox.critical(self, "Stitching Error", str(e))
            self.status_label.setText('Status: Error Encountered')
            self.reset_gui_state()

    def stop_stitching(self):
        """Stop the stitching process and reset GUI state."""
        try:
            if hasattr(self, 'stitcher') and self.stitcher and self.stitcher.is_alive():
                # Set stop event first
                self.stop_event.set()
                
                # Give process a chance to exit cleanly
                self.stitcher.join(timeout=2)
                
                # If still alive after timeout, force terminate
                if self.stitcher.is_alive():
                    try:
                        self.stitcher.terminate()
                        self.stitcher.join(timeout=1)
                    except:
                        print("Error terminating process")
            
            # Now safe to clear reference
            self.stitcher = None
            
            # Reset UI state after process is fully cleaned up
            self.reset_gui_state()
            self.status_label.setText('Status: Process Stopped... Ready.')
                
        except Exception as e:
            print(f"Error stopping stitcher: {e}")
            # Still try to reset UI even if error
            self.reset_gui_state()

    def reset_gui_state(self):
        """Reset all GUI elements to initial state."""
        # Reset buttons
        self.start_btn.setChecked(False)
        self.start_btn.setText('Start Stitching')
        self.start_btn.setEnabled(True)

        # Reset status and progress
        self.status_label.setText('Status: Ready')
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        
        # Re-enable all controls
        self.input_directory_btn.setEnabled(True)
        self.output_format_combo.setEnabled(True)
        self.apply_flatfield_checkbox.setEnabled(True)
        self.use_registration_checkbox.setEnabled(True)
        self.merge_timepoints_checkbox.setEnabled(True)
        self.merge_regions_checkbox.setEnabled(True)
        
        # Reset registration controls if needed
        if self.use_registration_checkbox.isChecked():
            self.channel_combo.setEnabled(True)
            self.z_level_input.setEnabled(True)
        
        # Update view button state
        self.view_btn.setEnabled(bool(self.output_path_edit.text().strip()))
        QApplication.processEvents()

    def saving_complete(self, path, dtype):
        """Handle completion of the stitching process."""
        # Set data attributes
        self.output_path = path
        self.output_path_edit.setText(path)
        self.dtype = np.dtype(dtype)
        if dtype == np.uint16:
            self.contrast_limits = [0, 65535]
        elif dtype == np.uint8:
            self.contrast_limits = [0, 255]
        else:
            self.contrast_limits = None
            
        # Reset UI through common method
        self.reset_gui_state()
        self.status_label.setText("Saving Completed. Ready to View.")

    def view_output_napari(self):
        """Open the output in Napari viewer."""
        output_path = self.output_path_edit.text()
        try:
            viewer = napari.Viewer()
            if ".ome.zarr" in output_path:
                viewer.open(output_path, plugin='napari-ome-zarr')
            else:
                viewer.open(output_path)

            for layer in viewer.layers:
                wavelength = self.extractWavelength(layer.name)
                channel_info = CHANNEL_COLORS_MAP.get(wavelength, {'hex': 0xFFFFFF, 'name': 'gray'})

                # Set colormap
                if channel_info['name'] in AVAILABLE_COLORMAPS:
                    layer.colormap = AVAILABLE_COLORMAPS[channel_info['name']]
                else:
                    layer.colormap = self.generateColormap(channel_info)

                # Set contrast limits based on dtype
                if np.issubdtype(layer.data.dtype, np.integer):
                    info = np.iinfo(layer.data.dtype)
                    layer.contrast_limits = (info.min, info.max)
                elif np.issubdtype(layer.data.dtype, np.floating):
                    layer.contrast_limits = (0.0, 1.0)

            napari.run()
        except Exception as e:
            QMessageBox.critical(self, "Error Opening in Napari", str(e))
            print(f"An error occurred while opening output in Napari: {e}")

    def extractWavelength(self, name):
        """Extract wavelength information from channel name."""
        parts = name.split()
        if 'Fluorescence' in parts:
            index = parts.index('Fluorescence') + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ['R', 'G', 'B']:
            if color in parts or "full_" + color in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Generate a colormap from channel information."""
        c0 = (0, 0, 0)
        c1 = (((channel_info['hex'] >> 16) & 0xFF) / 255,
              ((channel_info['hex'] >> 8) & 0xFF) / 255,
              (channel_info['hex'] & 0xFF) / 255)
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info['name'])

    def closeEvent(self, event):
        """Handle application closure."""
        self.stop_stitching()
        super().closeEvent(event)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = StitchingGUI()
    gui.show()
    sys.exit(app.exec_())
