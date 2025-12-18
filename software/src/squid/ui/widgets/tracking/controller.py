from squid.ui.widgets.tracking._common import *
from squid.backend.services import PeripheralService
from typing import Callable, Tuple
import numpy as np
from qtpy.QtWidgets import QListWidget, QAbstractItemView
from qtpy.QtGui import QIcon
from qtpy.QtCore import QMetaObject
from squid.ui.widgets.hardware.objectives import ObjectivesWidget
from squid.core.events import (
    EventBus,
    ObjectiveChanged,
    SetTrackingParametersCommand,
    SetTrackingPathCommand,
    SetTrackingChannelsCommand,
    StartTrackingExperimentCommand,
    StartTrackingCommand,
    StopTrackingCommand,
    TrackingStateChanged,
)


class TrackingControllerWidget(QFrame):
    base_path_is_set: bool
    btn_setSavingDir: QPushButton
    lineEdit_savingDir: QLineEdit
    lineEdit_experimentID: QLineEdit
    objectivesWidget: ObjectivesWidget
    dropdown_objective: QComboBox
    dropdown_tracker: QComboBox
    entry_tracking_interval: QDoubleSpinBox
    list_configurations: QListWidget
    checkbox_saveImages: QCheckBox
    btn_track: QPushButton
    checkbox_enable_stage_tracking: QCheckBox
    grid: QVBoxLayout

    def __init__(
        self,
        event_bus: EventBus,
        initial_channel_configs: List[str],
        peripheral_service: PeripheralService,
        objectivesWidget: ObjectivesWidget,
        initial_objective: str,
        initial_pixel_size_um: float,
        roi_bbox_provider: Callable[[], object],
        show_configurations: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self._channel_configs = list(initial_channel_configs)
        self.peripheral_service = peripheral_service
        self.objectivesWidget = objectivesWidget
        self._roi_bbox_provider = roi_bbox_provider
        self._current_objective = initial_objective
        self._pixel_size_um = initial_pixel_size_um
        self.base_path_is_set = False
        self._is_tracking = False

        if self.peripheral_service is None:
            raise ValueError("PeripheralService is required for tracking controller.")
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to tracking state events
        self._event_bus.subscribe(TrackingStateChanged, self._on_tracking_state_changed)
        self._event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

        self.peripheral_service.add_joystick_button_listener(
            lambda button_pressed: self.handle_button_pressed(button_pressed)
        )

    def add_components(self, show_configurations: bool) -> None:
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("assets/icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        # Publish default path via event
        self._event_bus.publish(SetTrackingPathCommand(base_path=DEFAULT_SAVING_PATH))
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # ObjectivesWidget passed in from parent

        self.dropdown_tracker = QComboBox()
        self.dropdown_tracker.addItems(TRACKERS)
        self.dropdown_tracker.setCurrentText(DEFAULT_TRACKER)

        self.entry_tracking_interval = QDoubleSpinBox()
        self.entry_tracking_interval.setKeyboardTracking(False)
        self.entry_tracking_interval.setMinimum(0)
        self.entry_tracking_interval.setMaximum(30)
        self.entry_tracking_interval.setSingleStep(0.5)
        self.entry_tracking_interval.setValue(0)

        # Channel configurations (populated from initial_channel_configs)
        self.list_configurations = QListWidget()
        self.list_configurations.addItems(self._channel_configs)
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_saveImages = QCheckBox("Save Images")
        self.btn_track = QPushButton("Start Tracking")
        self.btn_track.setCheckable(True)
        self.btn_track.setChecked(False)

        self.checkbox_enable_stage_tracking = QCheckBox(" Enable Stage Tracking")
        self.checkbox_enable_stage_tracking.setChecked(True)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 0, 0)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1, 1, 2)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 3)
        tmp = QLabel("Experiment ID")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 1, 0)
        grid_line0.addWidget(self.lineEdit_experimentID, 1, 1, 1, 1)
        tmp = QLabel("Objective")
        tmp.setFixedWidth(90)
        # grid_line0.addWidget(tmp,1,2)
        # grid_line0.addWidget(self.dropdown_objective, 1,3)
        grid_line0.addWidget(tmp, 1, 2)
        grid_line0.addWidget(self.objectivesWidget, 1, 3)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)

        grid_line1 = QHBoxLayout()
        tmp = QLabel("Tracker")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.dropdown_tracker)
        tmp = QLabel("Tracking Interval (s)")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.entry_tracking_interval)
        grid_line1.addWidget(self.checkbox_saveImages)

        grid_line4 = QGridLayout()
        grid_line4.addWidget(self.btn_track, 0, 0, 1, 3)
        grid_line4.addWidget(self.checkbox_enable_stage_tracking, 0, 4)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        if show_configurations:
            self.grid.addLayout(grid_line3)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line4)
        self.grid.addStretch()
        self.setLayout(self.grid)

        # connections - buttons, checkboxes, entries
        self.checkbox_enable_stage_tracking.stateChanged.connect(
            self._on_enable_stage_tracking_changed
        )
        self.checkbox_saveImages.stateChanged.connect(
            self._on_save_images_changed
        )
        self.entry_tracking_interval.valueChanged.connect(
            self._on_tracking_interval_changed
        )
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_track.clicked.connect(self.toggle_acquisition)
        # connections - selections and entries
        self.dropdown_tracker.currentIndexChanged.connect(self.update_tracker)
        # self.dropdown_objective.currentIndexChanged.connect(self.update_pixel_size)
        self.objectivesWidget.dropdown.currentIndexChanged.connect(
            self.update_pixel_size
        )
        # Note: tracking state is now handled via TrackingStateChanged event subscription

        # run initialization functions
        self.update_pixel_size()
        self._event_bus.publish(SetTrackingParametersCommand(
            image_resizing_factor=1
        ))  # to add: image resizing slider

    # TODO(imo): This needs testing!
    def handle_button_pressed(self, button_state: bool) -> None:
        QMetaObject.invokeMethod(
            self,
            "slot_joystick_button_pressed",
            Qt.ConnectionType.AutoConnection,
            button_state,
        )  # type: ignore[call-overload]

    def slot_joystick_button_pressed(self, button_state: bool) -> None:
        self.btn_track.setChecked(button_state)
        if self.btn_track.isChecked():
            if not self.base_path_is_set:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            self.setEnabled_all(False)
            self._event_bus.publish(StartTrackingExperimentCommand(
                experiment_id=self.lineEdit_experimentID.text()
            ))
            self._event_bus.publish(SetTrackingChannelsCommand(
                channel_names=list(item.text() for item in self.list_configurations.selectedItems())
            ))
            bbox = tuple(int(x) for x in np.array(self._roi_bbox_provider()).tolist())
            self._event_bus.publish(StartTrackingCommand(roi_bbox=bbox))  # type: ignore[arg-type]
        else:
            self._event_bus.publish(StopTrackingCommand())

    def set_saving_dir(self) -> None:
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self._event_bus.publish(SetTrackingPathCommand(base_path=save_dir_base))
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed: bool) -> None:
        if pressed:
            if not self.base_path_is_set:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self._event_bus.publish(StartTrackingExperimentCommand(
                experiment_id=self.lineEdit_experimentID.text()
            ))
            self._event_bus.publish(SetTrackingChannelsCommand(
                channel_names=list(item.text() for item in self.list_configurations.selectedItems())
            ))
            bbox = tuple(int(x) for x in np.array(self._roi_bbox_provider()).tolist())
            self._event_bus.publish(StartTrackingCommand(roi_bbox=bbox))  # type: ignore[arg-type]
        else:
            self._event_bus.publish(StopTrackingCommand())

    def setEnabled_all(self, enabled: bool) -> None:
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        # self.dropdown_tracker
        # self.dropdown_objective
        self.list_configurations.setEnabled(enabled)

    def update_tracker(self, index: int) -> None:
        """Publish tracker type change via event."""
        self._event_bus.publish(SetTrackingParametersCommand(
            tracker_type=self.dropdown_tracker.currentText()
        ))

    def update_pixel_size(self) -> None:
        """Calculate and publish pixel size based on current objective."""
        pixel_size_xy = self._pixel_size_um
        objective = self._current_objective
        # Publish via event instead of direct controller call
        self._event_bus.publish(SetTrackingParametersCommand(
            pixel_size_um=pixel_size_xy,
            objective=objective
        ))
        print(f"pixel size is {pixel_size_xy:.2f} μm")

    # Event handlers for checkbox/spinbox changes
    def _on_enable_stage_tracking_changed(self, state: int) -> None:
        """Handle stage tracking checkbox change."""
        self._event_bus.publish(SetTrackingParametersCommand(
            enable_stage_tracking=state == Qt.Checked
        ))

    def _on_save_images_changed(self, state: int) -> None:
        """Handle save images checkbox change."""
        self._event_bus.publish(SetTrackingParametersCommand(
            save_images=state == Qt.Checked
        ))

    def _on_tracking_interval_changed(self, value: float) -> None:
        """Handle tracking interval spinbox change."""
        self._event_bus.publish(SetTrackingParametersCommand(
            time_interval_s=value
        ))

    def _on_tracking_state_changed(self, event: TrackingStateChanged) -> None:
        """Handle tracking state change from controller."""
        self._is_tracking = event.is_tracking
        if not event.is_tracking:
            # Tracking stopped - update UI
            self.btn_track.setChecked(False)
            self.setEnabled_all(True)
            print("tracking stopped")

    def _on_objective_changed(self, event: ObjectiveChanged) -> None:
        """Cache objective and pixel size from events."""
        if event.objective_name is not None:
            self._current_objective = event.objective_name
        if event.pixel_size_um is not None:
            self._pixel_size_um = event.pixel_size_um
