from squid.ui.widgets.tracking._common import *
from qtpy.QtWidgets import QListWidget, QAbstractItemView, QMessageBox
from qtpy.QtGui import QIcon
from typing import TYPE_CHECKING, List
from squid.core.events import (
    EventBus,
    SetPlateReaderParametersCommand,
    SetPlateReaderPathCommand,
    SetPlateReaderChannelsCommand,
    SetPlateReaderColumnsCommand,
    StartPlateReaderExperimentCommand,
    StartPlateReaderCommand,
    StopPlateReaderCommand,
    PlateReaderAcquisitionFinished,
    PlateReaderHomeCommand,
    PlateReaderMoveToCommand,
    PlateReaderHomingComplete,
    PlateReaderLocationChanged,
)


class PlateReaderAcquisitionWidget(QFrame):
    base_path_is_set: bool
    btn_setSavingDir: QPushButton
    lineEdit_savingDir: QLineEdit
    lineEdit_experimentID: QLineEdit
    list_columns: QListWidget
    list_configurations: QListWidget
    checkbox_withAutofocus: QCheckBox
    checkbox_withReflectionAutofocus: QCheckBox
    btn_startAcquisition: QPushButton
    grid: QGridLayout

    def __init__(
        self,
        event_bus: EventBus,
        initial_channel_configs: List[str],
        show_configurations: bool = True,
        main: Optional[QWidget] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self._channel_configs = list(initial_channel_configs)
        self.base_path_is_set = False
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to acquisition state events
        self._event_bus.subscribe(PlateReaderAcquisitionFinished, self._on_acquisition_finished)

    def add_components(self, show_configurations: bool) -> None:
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("assets/icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        # Publish default path via event
        self._event_bus.publish(SetPlateReaderPathCommand(base_path=DEFAULT_SAVING_PATH))
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        self.list_columns = QListWidget()
        for i in range(PLATE_READER.NUMBER_OF_COLUMNS):
            self.list_columns.addItems([str(i + 1)])
        self.list_columns.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.list_configurations = QListWidget()
        self.list_configurations.addItems(self._channel_configs)
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
        self.checkbox_withReflectionAutofocus = QCheckBox("With Reflection AF")
        self.btn_startAcquisition = QPushButton("Start Acquisition")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)

        self.btn_startAcquisition.setEnabled(False)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line1 = QGridLayout()
        tmp = QLabel("Sample ID")
        tmp.setFixedWidth(90)
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line2 = QGridLayout()
        tmp = QLabel("Columns")
        tmp.setFixedWidth(90)
        grid_line2.addWidget(tmp)
        grid_line2.addWidget(self.list_columns, 0, 1)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)
        # grid_line3.addWidget(self.checkbox_withAutofocus)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.grid.addLayout(grid_line2, 2, 0)
        if show_configurations:
            self.grid.addLayout(grid_line3, 3, 0)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addWidget(self.btn_startAcquisition, 4, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.checkbox_withAutofocus.stateChanged.connect(self._on_autofocus_changed)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)

    def set_saving_dir(self) -> None:
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self._event_bus.publish(SetPlateReaderPathCommand(base_path=save_dir_base))
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed: bool) -> None:
        if not self.base_path_is_set:
            self.btn_startAcquisition.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self._event_bus.publish(StartPlateReaderExperimentCommand(
                experiment_id=self.lineEdit_experimentID.text()
            ))
            self._event_bus.publish(SetPlateReaderChannelsCommand(
                channel_names=list(item.text() for item in self.list_configurations.selectedItems())
            ))
            self._event_bus.publish(SetPlateReaderColumnsCommand(
                columns=list(
                    map(
                        int, [item.text() for item in self.list_columns.selectedItems()]
                    )
                )
            ))
            self._event_bus.publish(StartPlateReaderCommand())
        else:
            self._event_bus.publish(StopPlateReaderCommand())

    def _on_autofocus_changed(self, state: int) -> None:
        """Handle autofocus checkbox change."""
        self._event_bus.publish(SetPlateReaderParametersCommand(
            use_autofocus=state == Qt.Checked
        ))

    def _on_acquisition_finished(self, event: PlateReaderAcquisitionFinished) -> None:
        """Handle acquisition finished event from controller."""
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)

    def setEnabled_all(
        self, enabled: bool, exclude_btn_startAcquisition: bool = False
    ) -> None:
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.list_columns.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def slot_homing_complete(self) -> None:
        self.btn_startAcquisition.setEnabled(True)


class PlateReaderNavigationWidget(QFrame):
    dropdown_column: QComboBox
    dropdown_row: QComboBox
    btn_moveto: QPushButton
    btn_home: QPushButton
    label_current_location: QLabel
    grid: QGridLayout

    def __init__(
        self,
        event_bus: EventBus,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._event_bus = event_bus
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Subscribe to events
        self._event_bus.subscribe(PlateReaderHomingComplete, self._on_homing_complete)
        self._event_bus.subscribe(PlateReaderLocationChanged, self._on_location_changed)

    def add_components(self) -> None:
        self.dropdown_column = QComboBox()
        self.dropdown_column.addItems([""])
        self.dropdown_column.addItems(
            [str(i + 1) for i in range(PLATE_READER.NUMBER_OF_COLUMNS)]
        )
        self.dropdown_row = QComboBox()
        self.dropdown_row.addItems([""])
        self.dropdown_row.addItems(
            [chr(i) for i in range(ord("A"), ord("A") + PLATE_READER.NUMBER_OF_ROWS)]
        )
        self.btn_moveto = QPushButton("Move To")
        self.btn_home = QPushButton("Home")
        self.label_current_location = QLabel()
        self.label_current_location.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.label_current_location.setFixedWidth(50)

        self.dropdown_column.setEnabled(False)
        self.dropdown_row.setEnabled(False)
        self.btn_moveto.setEnabled(False)

        # layout
        grid_line0 = QHBoxLayout()
        # tmp = QLabel('Saving Path')
        # tmp.setFixedWidth(90)
        grid_line0.addWidget(self.btn_home)
        grid_line0.addWidget(QLabel("Column"))
        grid_line0.addWidget(self.dropdown_column)
        grid_line0.addWidget(QLabel("Row"))
        grid_line0.addWidget(self.dropdown_row)
        grid_line0.addWidget(self.btn_moveto)
        grid_line0.addStretch()
        grid_line0.addWidget(self.label_current_location)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.setLayout(self.grid)

        self.btn_home.clicked.connect(self.home)
        self.btn_moveto.clicked.connect(self.move)

    def home(self) -> None:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self._event_bus.publish(PlateReaderHomeCommand())

    def move(self) -> None:  # type: ignore[override]
        self._event_bus.publish(PlateReaderMoveToCommand(
            column=self.dropdown_column.currentText(),
            row=self.dropdown_row.currentText()
        ))

    def _on_homing_complete(self, event: PlateReaderHomingComplete) -> None:
        """Handle homing complete event."""
        self.dropdown_column.setEnabled(True)
        self.dropdown_row.setEnabled(True)
        self.btn_moveto.setEnabled(True)

    def _on_location_changed(self, event: PlateReaderLocationChanged) -> None:
        """Handle location changed event."""
        self.label_current_location.setText(event.location_str)
        row = event.location_str[0]
        column = event.location_str[1:]
        self.dropdown_row.setCurrentText(row)
        self.dropdown_column.setCurrentText(column)

    # Keep legacy slot for backwards compatibility during transition
    def slot_homing_complete(self) -> None:
        self.dropdown_column.setEnabled(True)
        self.dropdown_row.setEnabled(True)
        self.btn_moveto.setEnabled(True)

    def update_current_location(self, location_str: str) -> None:
        self.label_current_location.setText(location_str)
        row = location_str[0]
        column = location_str[1:]
        self.dropdown_row.setCurrentText(row)
        self.dropdown_column.setCurrentText(column)
