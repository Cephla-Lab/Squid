# Fluidics multi-point acquisition widget
import math
import time
from typing import Optional, List

import pandas as pd

import squid.logging
from squid.events import (
    EventBus,
    SetFluidicsRoundsCommand,
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
)

from qtpy.QtCore import Signal, QTimer
from qtpy.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QSizePolicy,
    QAbstractItemView,
    QWidget,
    QListWidget,
    QProgressBar,
)
from qtpy.QtGui import QIcon

from control._def import *


class MultiPointWithFluidicsWidget(QFrame):
    """A simplified version of WellplateMultiPointWidget for use with fluidics"""

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz

    def __init__(
        self,
        navigationViewer,
        scanCoordinates,
        event_bus: EventBus,
        initial_channel_configs: List[str],
        napariMosaicWidget=None,
        z_ustep_per_mm: Optional[float] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        # Z-axis conversion factor (usteps per mm), passed at init to avoid stage config access
        self._z_ustep_per_mm = z_ustep_per_mm
        self.navigationViewer = navigationViewer
        self.scanCoordinates = scanCoordinates
        # Initial channel configurations (passed from GUI, will be updated via events)
        self._channel_configs = list(initial_channel_configs)
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

        # Cached acquisition state from events
        self._acquisition_in_progress = False
        self._acquisition_is_aborting = False

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to acquisition state events
        self._event_bus.subscribe(AcquisitionStateChanged, self._on_acquisition_state_changed)
        self._event_bus.subscribe(AcquisitionProgress, self._on_acquisition_progress)
        self._event_bus.subscribe(AcquisitionRegionProgress, self._on_region_progress)
        self._event_bus.subscribe(LoadingPositionReached, self._on_loading_position_reached)
        self._event_bus.subscribe(ScanningPositionReached, self._on_scanning_position_reached)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("assets/icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        # Publish default path via event
        self._event_bus.publish(SetAcquisitionPathCommand(base_path=DEFAULT_SAVING_PATH))
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

        # Channel configurations (populated from initial_channel_configs)
        self.list_configurations = QListWidget()
        self.list_configurations.addItems(self._channel_configs)
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Reflection AF checkbox
        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(
            MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT
        )
        # Initial reflection AF flag set via event
        self._event_bus.publish(SetAcquisitionParametersCommand(
            use_reflection_af=MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT
        ))

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
        self.entry_NZ.valueChanged.connect(self._on_nz_changed)
        self.checkbox_withReflectionAutofocus.toggled.connect(self._on_reflection_af_toggled)
        self.checkbox_usePiezo.toggled.connect(self._on_use_piezo_toggled)
        self.list_configurations.itemSelectionChanged.connect(
            self.emit_selected_channels
        )
        # Note: acquisition_finished, signal_acquisition_progress, signal_region_progress
        # are now handled via EventBus subscriptions (see _on_acquisition_state_changed etc.)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

    # The following methods are copied from WellplateMultiPointWidget with minimal modifications
    def toggle_acquisition(self, pressed):
        rounds = self.get_rounds()
        if pressed:
            if not self.base_path_is_set:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(
                    self, "Warning", "Please choose base saving directory first"
                )
                return

            if not self.list_configurations.selectedItems():
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(
                    self, "Warning", "Please select at least one imaging channel"
                )
                return

            if self._acquisition_in_progress:
                self._log.warning(
                    "Acquisition in progress or aborting, cannot start another yet."
                )
                self.btn_startAcquisition.setChecked(False)
                return

            if not rounds:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(
                    self, "Warning", "Please enter valid round numbers (1-24)"
                )
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True
            self.btn_startAcquisition.setText("Stop\n Acquisition ")

            # Publish acquisition parameters via events
            self._event_bus.publish(SetAcquisitionParametersCommand(
                delta_z_um=self.entry_deltaZ.value(),
                n_z=self.entry_NZ.value(),
                use_piezo=self.checkbox_usePiezo.isChecked(),
                use_reflection_af=self.checkbox_withReflectionAutofocus.isChecked(),
                use_fluidics=True,  # may be set to False from other widgets
                n_t=len(rounds),
            ))
            self._event_bus.publish(SetAcquisitionChannelsCommand(
                channel_names=[item.text() for item in self.list_configurations.selectedItems()]
            ))
            self._event_bus.publish(SetFluidicsRoundsCommand(rounds=rounds))
            self._event_bus.publish(StartNewExperimentCommand(
                experiment_id=self.lineEdit_experimentID.text()
            ))

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(
                self.entry_NZ.value(), self.entry_deltaZ.value()
            )

            # Start acquisition via event
            self._event_bus.publish(StartAcquisitionCommand())
        else:
            self._event_bus.publish(StopAcquisitionCommand())

    def set_saving_dir(self):
        """Open dialog to set saving directory"""
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self._event_bus.publish(SetAcquisitionPathCommand(base_path=save_dir_base))
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
        elif self._z_ustep_per_mm is not None:
            # Use cached Z-axis config to quantize to valid step sizes
            mm_per_ustep = 1.0 / self._z_ustep_per_mm
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        else:
            # No Z config available, use value as-is
            deltaZ = value
        self.entry_deltaZ.setValue(deltaZ)
        self._event_bus.publish(SetAcquisitionParametersCommand(delta_z_um=deltaZ))

    def emit_selected_channels(self):
        """Emit signal with list of selected channel names"""
        selected_channels = [
            item.text() for item in self.list_configurations.selectedItems()
        ]
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

    def _on_loading_position_reached(self, event: LoadingPositionReached) -> None:
        """Handle loading position reached - disable acquisition button."""
        self.disable_the_start_aquisition_button()

    def _on_scanning_position_reached(self, event: ScanningPositionReached) -> None:
        """Handle scanning position reached - enable acquisition button."""
        self.enable_the_start_aquisition_button()

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
            self.eta_seconds = (
                remaining_fovs / fov_per_second if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(
        self, current_region, num_regions, current_time_point
    ):
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
                raise ValueError(
                    "CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns"
                )

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
            QMessageBox.warning(
                self,
                "Load Error",
                f"Failed to load coordinates from {file_path}\nError: {str(e)}",
            )

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

            rounds: list[int] = []

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
                        raise ValueError(
                            f"Invalid number {num}: Must be between 1 and 24"
                        )
                    rounds.append(num)

            self.nRound = len(rounds)

            return rounds

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Please enter valid round numbers (e.g., '1-3,5,7-10')",
            )
            return []

    # =========================================================================
    # EventBus Handlers
    # =========================================================================

    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle acquisition state changes from EventBus."""
        self._acquisition_in_progress = event.in_progress
        self._acquisition_is_aborting = event.is_aborting

        if not event.in_progress:
            # Acquisition finished
            self.acquisition_is_finished()

    def _on_acquisition_progress(self, event: AcquisitionProgress) -> None:
        """Handle acquisition progress updates from EventBus."""
        self.update_acquisition_progress(
            event.current_round, event.total_rounds, event.current_fov
        )

    def _on_region_progress(self, event: AcquisitionRegionProgress) -> None:
        """Handle region progress updates from EventBus."""
        self.update_region_progress(event.current_region, event.total_regions)

    # =========================================================================
    # UI Event Handlers (publish commands)
    # =========================================================================

    def _on_nz_changed(self, value: int) -> None:
        """Handle NZ spinbox change - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(n_z=value))

    def _on_reflection_af_toggled(self, checked: bool) -> None:
        """Handle reflection AF checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(use_reflection_af=checked))

    def _on_use_piezo_toggled(self, checked: bool) -> None:
        """Handle use piezo checkbox toggle - publish event."""
        self._event_bus.publish(SetAcquisitionParametersCommand(use_piezo=checked))
