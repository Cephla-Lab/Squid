from control.widgets.tracking._common import *
from squid.services import PeripheralService
from qtpy.QtWidgets import QListWidget, QAbstractItemView
from qtpy.QtGui import QIcon
from qtpy.QtCore import QMetaObject
from control.widgets.hardware.objectives import ObjectivesWidget


class TrackingControllerWidget(QFrame):
    trackingController: TrackingController
    objectiveStore: ObjectiveStore
    channelConfigurationManager: ChannelConfigurationManager
    base_path_is_set: bool
    btn_setSavingDir: QPushButton
    lineEdit_savingDir: QLineEdit
    lineEdit_experimentID: QLineEdit
    objectivesWidget: ObjectivesWidget
    dropdown_objective: QComboBox
    dropdown_tracker: QComboBox
    entry_tracking_interval: QDoubleSpinBox
    list_configurations: QListWidget
    checkbox_withAutofocus: QCheckBox
    checkbox_saveImages: QCheckBox
    btn_track: QPushButton
    checkbox_enable_stage_tracking: QCheckBox
    grid: QVBoxLayout

    def __init__(
        self,
        trackingController: TrackingController,
        objectiveStore: ObjectiveStore,
        channelConfigurationManager: ChannelConfigurationManager,
        peripheral_service: PeripheralService,
        show_configurations: bool = True,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.trackingController = trackingController
        self.objectiveStore = objectiveStore
        self.channelConfigurationManager = channelConfigurationManager
        self.peripheral_service = peripheral_service
        self.base_path_is_set = False
        if self.peripheral_service is None:
            raise ValueError("PeripheralService is required for tracking controller.")
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

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
        self.trackingController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # self.dropdown_objective = QComboBox()
        # self.dropdown_objective.addItems(list(OBJECTIVES.keys()))
        # self.dropdown_objective.setCurrentText(DEFAULT_OBJECTIVE)
        self.objectivesWidget = ObjectivesWidget(self.objectiveStore)

        self.dropdown_tracker = QComboBox()
        self.dropdown_tracker.addItems(TRACKERS)
        self.dropdown_tracker.setCurrentText(DEFAULT_TRACKER)

        self.entry_tracking_interval = QDoubleSpinBox()
        self.entry_tracking_interval.setKeyboardTracking(False)
        self.entry_tracking_interval.setMinimum(0)
        self.entry_tracking_interval.setMaximum(30)
        self.entry_tracking_interval.setSingleStep(0.5)
        self.entry_tracking_interval.setValue(0)

        self.list_configurations = QListWidget()
        for (
            microscope_configuration
        ) in self.channelConfigurationManager.get_channel_configurations_for_objective(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
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
        grid_line1.addWidget(self.checkbox_withAutofocus)
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
            self.trackingController.toggle_stage_tracking
        )
        self.checkbox_withAutofocus.stateChanged.connect(
            self.trackingController.toggel_enable_af
        )
        self.checkbox_saveImages.stateChanged.connect(
            self.trackingController.toggel_save_images
        )
        self.entry_tracking_interval.valueChanged.connect(
            self.trackingController.set_tracking_time_interval
        )
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_track.clicked.connect(self.toggle_acquisition)
        # connections - selections and entries
        self.dropdown_tracker.currentIndexChanged.connect(self.update_tracker)
        # self.dropdown_objective.currentIndexChanged.connect(self.update_pixel_size)
        self.objectivesWidget.dropdown.currentIndexChanged.connect(
            self.update_pixel_size
        )
        # controller to widget
        self.trackingController.signal_tracking_stopped.connect(
            self.slot_tracking_stopped
        )

        # run initialization functions
        self.update_pixel_size()
        self.trackingController.update_image_resizing_factor(
            1
        )  # to add: image resizing slider

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
            self.trackingController.start_new_experiment(
                self.lineEdit_experimentID.text()
            )
            self.trackingController.set_selected_configurations(
                list(item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def slot_tracking_stopped(self) -> None:
        self.btn_track.setChecked(False)
        self.setEnabled_all(True)
        print("tracking stopped")

    def set_saving_dir(self) -> None:
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.trackingController.set_base_path(save_dir_base)
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
            self.trackingController.start_new_experiment(
                self.lineEdit_experimentID.text()
            )
            self.trackingController.set_selected_configurations(
                list(item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def setEnabled_all(self, enabled: bool) -> None:
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        # self.dropdown_tracker
        # self.dropdown_objective
        self.list_configurations.setEnabled(enabled)

    def update_tracker(self, index: int) -> None:
        self.trackingController.update_tracker_selection(
            self.dropdown_tracker.currentText()
        )

    def update_pixel_size(self) -> None:
        objective = self.objectiveStore.current_objective
        self.trackingController.objective = objective
        objective_info = self.objectiveStore.objectives_dict[objective]
        magnification = objective_info["magnification"]
        objective_tube_lens_mm = objective_info["tube_lens_f_mm"]
        tube_lens_mm = TUBE_LENS_MM
        # TODO: these pixel size code needs to be updated.
        pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR]
        pixel_size_xy = pixel_size_um / (
            magnification / (objective_tube_lens_mm / tube_lens_mm)
        )
        self.trackingController.update_pixel_size(pixel_size_xy)
        print(f"pixel size is {pixel_size_xy:.2f} μm")
