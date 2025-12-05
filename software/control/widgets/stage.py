# Stage-related widgets
from typing import Optional, TYPE_CHECKING

import squid.logging
from squid.events import event_bus, StagePositionChanged
from qtpy.QtCore import Signal, Qt, QTimer

if TYPE_CHECKING:
    from squid.services import StageService
from qtpy.QtWidgets import (
    QDialog,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QPushButton,
    QCheckBox,
    QSlider,
    QMessageBox,
    QSizePolicy,
)

from control._def import (
    HOMING_ENABLED_X,
    HOMING_ENABLED_Y,
    HOMING_ENABLED_Z,
    ENABLE_CLICK_TO_MOVE_BY_DEFAULT,
)
from control.core.live_controller import LiveController
from control.peripherals.piezo import PiezoStage
from squid.abc import AbstractStage
from squid.stage.utils import move_to_loading_position, move_to_scanning_position, move_z_axis_to_safety_position


class StageUtils(QDialog):
    """Dialog containing microscope utility functions like homing, zeroing, and slide positioning."""

    signal_threaded_stage_move_started = Signal()
    signal_loading_position_reached = Signal()
    signal_scanning_position_reached = Signal()

    def __init__(
        self,
        stage: AbstractStage = None,  # Legacy - keep for backward compat
        live_controller: LiveController = None,
        is_wellplate: bool = False,
        stage_service: Optional["StageService"] = None,
        parent=None
    ):
        super().__init__(parent)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.live_controller = live_controller
        self.is_wellplate = is_wellplate
        self.slide_position = None

        # Use service if provided, otherwise create from legacy param
        if stage_service is not None:
            self._service = stage_service
            self.stage = stage  # Keep for utility functions that need it
        elif stage is not None:
            # Legacy mode - create service wrapper
            from squid.services import StageService
            self._service = StageService(stage, event_bus)
            self.stage = stage
        else:
            raise ValueError("Either stage_service or stage required")

        self.setWindowTitle("Stage Utils")
        self.setModal(False)  # Allow interaction with main window while dialog is open
        self.setup_ui()

    def setup_ui(self):
        """Setup the UI components."""
        # Create buttons
        self.btn_home_X = QPushButton("Home X")
        self.btn_home_X.setDefault(False)
        self.btn_home_X.setEnabled(HOMING_ENABLED_X)

        self.btn_home_Y = QPushButton("Home Y")
        self.btn_home_Y.setDefault(False)
        self.btn_home_Y.setEnabled(HOMING_ENABLED_Y)

        self.btn_home_Z = QPushButton("Home Z")
        self.btn_home_Z.setDefault(False)
        self.btn_home_Z.setEnabled(HOMING_ENABLED_Z)

        self.btn_zero_X = QPushButton("Zero X")
        self.btn_zero_X.setDefault(False)

        self.btn_zero_Y = QPushButton("Zero Y")
        self.btn_zero_Y.setDefault(False)

        self.btn_zero_Z = QPushButton("Zero Z")
        self.btn_zero_Z.setDefault(False)

        self.btn_load_slide = QPushButton("Move To Loading Position")
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")

        # Connect buttons to functions
        self.btn_home_X.clicked.connect(self.home_x)
        self.btn_home_Y.clicked.connect(self.home_y)
        self.btn_home_Z.clicked.connect(self.home_z)
        self.btn_zero_X.clicked.connect(self.zero_x)
        self.btn_zero_Y.clicked.connect(self.zero_y)
        self.btn_zero_Z.clicked.connect(self.zero_z)
        self.btn_load_slide.clicked.connect(self.switch_position)

        # Layout
        main_layout = QVBoxLayout()

        # Homing section
        homing_group = QGroupBox("Homing")
        homing_layout = QHBoxLayout()
        homing_layout.addWidget(self.btn_home_X)
        homing_layout.addWidget(self.btn_home_Y)
        homing_layout.addWidget(self.btn_home_Z)
        homing_group.setLayout(homing_layout)

        # Zero section
        zero_group = QGroupBox("Zero Position")
        zero_layout = QHBoxLayout()
        zero_layout.addWidget(self.btn_zero_X)
        zero_layout.addWidget(self.btn_zero_Y)
        zero_layout.addWidget(self.btn_zero_Z)
        zero_group.setLayout(zero_layout)

        # Slide positioning section
        slide_group = QGroupBox("Slide Positioning")
        slide_layout = QVBoxLayout()
        slide_layout.addWidget(self.btn_load_slide)
        slide_group.setLayout(slide_layout)

        # Add sections to main layout
        main_layout.addWidget(homing_group)
        main_layout.addWidget(zero_group)
        main_layout.addWidget(slide_group)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        main_layout.addWidget(close_button)

        self.setLayout(main_layout)

    def home_x(self):
        """Home X axis with confirmation dialog."""
        self._show_confirmation_dialog(x=True, y=False, z=False, theta=False)

    def home_y(self):
        """Home Y axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=True, z=False, theta=False)

    def home_z(self):
        """Home Z axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=False, z=True, theta=False)
        move_z_axis_to_safety_position(self.stage)

    def _show_confirmation_dialog(self, x: bool, y: bool, z: bool, theta: bool):
        """Display a confirmation dialog and home the specified axis if confirmed."""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self._service.home(x=x, y=y, z=z)

    def zero_x(self):
        """Zero X axis position."""
        self._service.zero(x=True, y=False, z=False)

    def zero_y(self):
        """Zero Y axis position."""
        self._service.zero(x=False, y=True, z=False)

    def zero_z(self):
        """Zero Z axis position."""
        self._service.zero(x=False, y=False, z=True)

    def switch_position(self):
        """Switch between loading and scanning positions."""
        self._was_live = self.live_controller.is_live
        if self._was_live:
            self.live_controller.stop_live()
        self.signal_threaded_stage_move_started.emit()
        if self.slide_position != "loading":
            move_to_loading_position(
                self.stage,
                blocking=False,
                callback=self._callback_loading_position_reached,
                is_wellplate=self.is_wellplate,
            )
        else:
            move_to_scanning_position(
                self.stage,
                blocking=False,
                callback=self._callback_scanning_position_reached,
                is_wellplate=self.is_wellplate,
            )
        self.btn_load_slide.setEnabled(False)

    def _callback_loading_position_reached(self, success: bool, error_message: Optional[str]):
        """Handle slide loading position reached signal."""
        self.slide_position = "loading"
        self.btn_load_slide.setStyleSheet("background-color: #C2FFC2")
        self.btn_load_slide.setText("Move to Scanning Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self.live_controller.start_live()
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self.signal_loading_position_reached.emit()

    def _callback_scanning_position_reached(self, success: bool, error_message: Optional[str]):
        """Handle slide scanning position reached signal."""
        self.slide_position = "scanning"
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")
        self.btn_load_slide.setText("Move to Loading Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self.live_controller.start_live()
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self.signal_scanning_position_reached.emit()


class PiezoWidget(QFrame):
    def __init__(self, piezo: PiezoStage, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.piezo = piezo
        self.piezo_displacement_um = 0.00
        self.add_components()

    def add_components(self):
        # Row 1: Slider and Double Spin Box for direct control
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setMinimum(0)
        self.slider.setMaximum(int(self.piezo.range_um * 100))  # Multiplied by 100 for 0.01 precision
        self.slider.setValue(int(self.piezo._home_position_um * 100))

        self.spinBox = QDoubleSpinBox(self)
        self.spinBox.setRange(0.0, self.piezo.range_um)
        self.spinBox.setDecimals(2)
        self.spinBox.setSingleStep(1)
        self.spinBox.setSuffix(" μm")
        self.spinBox.setKeyboardTracking(False)
        self.spinBox.setValue(self.piezo._home_position_um)

        # Row 3: Home Button
        self.home_btn = QPushButton(f" Set to {self.piezo._home_position_um} μm ", self)

        hbox1 = QHBoxLayout()
        hbox1.addWidget(self.home_btn)
        hbox1.addWidget(self.slider)
        hbox1.addWidget(self.spinBox)

        # Row 2: Increment Double Spin Box, Move Up and Move Down Buttons
        self.increment_spinBox = QDoubleSpinBox(self)
        self.increment_spinBox.setKeyboardTracking(False)
        self.increment_spinBox.setRange(0.0, 100.0)
        self.increment_spinBox.setDecimals(2)
        self.increment_spinBox.setSingleStep(1)
        self.increment_spinBox.setValue(1.00)
        self.increment_spinBox.setSuffix(" μm")
        self.move_up_btn = QPushButton("Move Up", self)
        self.move_down_btn = QPushButton("Move Down", self)

        hbox2 = QHBoxLayout()
        hbox2.addWidget(self.increment_spinBox)
        hbox2.addWidget(self.move_up_btn)
        hbox2.addWidget(self.move_down_btn)

        # Vertical Layout to include all HBoxes
        vbox = QVBoxLayout()
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)

        self.setLayout(vbox)

        # Connect signals and slots
        self.slider.valueChanged.connect(self.update_from_slider)
        self.spinBox.valueChanged.connect(self.update_from_spinBox)
        self.move_up_btn.clicked.connect(lambda: self.adjust_position(True))
        self.move_down_btn.clicked.connect(lambda: self.adjust_position(False))
        self.home_btn.clicked.connect(self.home)

    def update_from_slider(self, value):
        self.piezo_displacement_um = value / 100  # Convert back to float with two decimal places
        self.update_spinBox()
        self.update_piezo_position()

    def update_from_spinBox(self, value):
        self.piezo_displacement_um = value
        self.update_slider()
        self.update_piezo_position()

    def update_spinBox(self):
        self.spinBox.blockSignals(True)
        self.spinBox.setValue(self.piezo_displacement_um)
        self.spinBox.blockSignals(False)

    def update_slider(self):
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.piezo_displacement_um * 100))
        self.slider.blockSignals(False)

    def update_piezo_position(self):
        self.piezo.move_to(self.piezo_displacement_um)

    def adjust_position(self, up):
        increment = self.increment_spinBox.value()
        if up:
            self.piezo_displacement_um = min(self.piezo.range_um, self.spinBox.value() + increment)
        else:
            self.piezo_displacement_um = max(0, self.spinBox.value() - increment)
        self.update_spinBox()
        self.update_slider()
        self.update_piezo_position()

    def home(self):
        self.piezo.home()
        self.piezo_displacement_um = self.piezo._home_position_um
        self.update_spinBox()
        self.update_slider()

    def update_displacement_um_display(self, displacement=None):
        if displacement is None:
            displacement = self.piezo.position
        self.piezo_displacement_um = round(displacement, 2)
        self.update_spinBox()
        self.update_slider()


class NavigationWidget(QFrame):
    def __init__(
        self,
        stage: AbstractStage = None,  # Legacy - keep for backward compat
        stage_service: Optional["StageService"] = None,
        main=None,
        widget_configuration="full",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.widget_configuration = widget_configuration
        self.slide_position = None

        # Use service if provided, otherwise create from legacy param
        if stage_service is not None:
            self._service = stage_service
            self.stage = None  # Don't need direct access
        elif stage is not None:
            # Legacy mode - create service wrapper
            from squid.services import StageService
            self._service = StageService(stage, event_bus)
            self.stage = stage  # Keep for backwards compat (e.g., set_deltaX)
        else:
            raise ValueError("Either stage_service or stage required")

        # Subscribe to position updates
        event_bus.subscribe(StagePositionChanged, self._on_position_changed)

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.position_update_timer = QTimer()
        self.position_update_timer.setInterval(100)
        self.position_update_timer.timeout.connect(self._update_position)
        self.position_update_timer.start()

    def _update_position(self):
        pos = self._service.get_position()
        self.label_Xpos.setNum(pos.x_mm)
        self.label_Ypos.setNum(pos.y_mm)
        # NOTE: The z label is in um
        self.label_Zpos.setNum(pos.z_mm * 1000)

    def _on_position_changed(self, event: StagePositionChanged):
        """Handle position changed event."""
        self.label_Xpos.setNum(event.x_mm)
        self.label_Ypos.setNum(event.y_mm)
        self.label_Zpos.setNum(event.z_mm * 1000)

    def add_components(self):
        x_label = QLabel("X :")
        x_label.setFixedWidth(20)
        self.label_Xpos = QLabel()
        self.label_Xpos.setNum(0)
        self.label_Xpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dX = QDoubleSpinBox()
        self.entry_dX.setMinimum(0)
        self.entry_dX.setMaximum(25)
        self.entry_dX.setSingleStep(0.2)
        self.entry_dX.setValue(0)
        self.entry_dX.setDecimals(3)
        self.entry_dX.setSuffix(" mm")
        self.entry_dX.setKeyboardTracking(False)
        self.btn_moveX_forward = QPushButton("Forward")
        self.btn_moveX_forward.setDefault(False)
        self.btn_moveX_backward = QPushButton("Backward")
        self.btn_moveX_backward.setDefault(False)

        self.checkbox_clickToMove = QCheckBox("Click to Move")
        self.checkbox_clickToMove.setChecked(False)
        self.checkbox_clickToMove.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))

        y_label = QLabel("Y :")
        y_label.setFixedWidth(20)
        self.label_Ypos = QLabel()
        self.label_Ypos.setNum(0)
        self.label_Ypos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dY = QDoubleSpinBox()
        self.entry_dY.setMinimum(0)
        self.entry_dY.setMaximum(25)
        self.entry_dY.setSingleStep(0.2)
        self.entry_dY.setValue(0)
        self.entry_dY.setDecimals(3)
        self.entry_dY.setSuffix(" mm")

        self.entry_dY.setKeyboardTracking(False)
        self.btn_moveY_forward = QPushButton("Forward")
        self.btn_moveY_forward.setDefault(False)
        self.btn_moveY_backward = QPushButton("Backward")
        self.btn_moveY_backward.setDefault(False)

        z_label = QLabel("Z :")
        z_label.setFixedWidth(20)
        self.label_Zpos = QLabel()
        self.label_Zpos.setNum(0)
        self.label_Zpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dZ = QDoubleSpinBox()
        self.entry_dZ.setMinimum(0)
        self.entry_dZ.setMaximum(1000)
        self.entry_dZ.setSingleStep(0.2)
        self.entry_dZ.setValue(0)
        self.entry_dZ.setDecimals(3)
        self.entry_dZ.setSuffix(" μm")
        self.entry_dZ.setKeyboardTracking(False)
        self.btn_moveZ_forward = QPushButton("Forward")
        self.btn_moveZ_forward.setDefault(False)
        self.btn_moveZ_backward = QPushButton("Backward")
        self.btn_moveZ_backward.setDefault(False)

        grid_line0 = QGridLayout()
        grid_line0.addWidget(x_label, 0, 0)
        grid_line0.addWidget(self.label_Xpos, 0, 1)
        grid_line0.addWidget(self.entry_dX, 0, 2)
        grid_line0.addWidget(self.btn_moveX_forward, 0, 3)
        grid_line0.addWidget(self.btn_moveX_backward, 0, 4)

        grid_line0.addWidget(y_label, 1, 0)
        grid_line0.addWidget(self.label_Ypos, 1, 1)
        grid_line0.addWidget(self.entry_dY, 1, 2)
        grid_line0.addWidget(self.btn_moveY_forward, 1, 3)
        grid_line0.addWidget(self.btn_moveY_backward, 1, 4)

        grid_line0.addWidget(z_label, 2, 0)
        grid_line0.addWidget(self.label_Zpos, 2, 1)
        grid_line0.addWidget(self.entry_dZ, 2, 2)
        grid_line0.addWidget(self.btn_moveZ_forward, 2, 3)
        grid_line0.addWidget(self.btn_moveZ_backward, 2, 4)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        self.set_click_to_move(ENABLE_CLICK_TO_MOVE_BY_DEFAULT)
        if not ENABLE_CLICK_TO_MOVE_BY_DEFAULT:
            grid_line3 = QHBoxLayout()
            grid_line3.addWidget(self.checkbox_clickToMove, 1)
            self.grid.addLayout(grid_line3)
        self.setLayout(self.grid)

        self.entry_dX.valueChanged.connect(self.set_deltaX)
        self.entry_dY.valueChanged.connect(self.set_deltaY)
        self.entry_dZ.valueChanged.connect(self.set_deltaZ)

        self.btn_moveX_forward.clicked.connect(self.move_x_forward)
        self.btn_moveX_backward.clicked.connect(self.move_x_backward)
        self.btn_moveY_forward.clicked.connect(self.move_y_forward)
        self.btn_moveY_backward.clicked.connect(self.move_y_backward)
        self.btn_moveZ_forward.clicked.connect(self.move_z_forward)
        self.btn_moveZ_backward.clicked.connect(self.move_z_backward)

    def set_click_to_move(self, enabled):
        self.log.info(f"Click to move enabled={enabled}")
        self.setEnabled_all(enabled)
        self.checkbox_clickToMove.setChecked(enabled)

    def get_click_to_move_enabled(self):
        return self.checkbox_clickToMove.isChecked()

    def setEnabled_all(self, enabled):
        self.checkbox_clickToMove.setEnabled(enabled)
        self.btn_moveX_forward.setEnabled(enabled)
        self.btn_moveX_backward.setEnabled(enabled)
        self.btn_moveY_forward.setEnabled(enabled)
        self.btn_moveY_backward.setEnabled(enabled)
        self.btn_moveZ_forward.setEnabled(enabled)
        self.btn_moveZ_backward.setEnabled(enabled)

    def move_x_forward(self):
        self._service.move_x(self.entry_dX.value())

    def move_x_backward(self):
        self._service.move_x(-self.entry_dX.value())

    def move_y_forward(self):
        self._service.move_y(self.entry_dY.value())

    def move_y_backward(self):
        self._service.move_y(-self.entry_dY.value())

    def move_z_forward(self):
        self._service.move_z(self.entry_dZ.value() / 1000)

    def move_z_backward(self):
        self._service.move_z(-self.entry_dZ.value() / 1000)

    def set_deltaX(self, value):
        mm_per_ustep = 1.0 / self.stage.x_mm_to_usteps(1.0)
        deltaX = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dX.setValue(deltaX)

    def set_deltaY(self, value):
        mm_per_ustep = 1.0 / self.stage.y_mm_to_usteps(1.0)
        deltaY = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dY.setValue(deltaY)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.z_mm_to_usteps(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_dZ.setValue(deltaZ)


class AutoFocusWidget(QFrame):
    signal_autoLevelSetting = Signal(bool)

    def __init__(self, autofocusController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autofocusController = autofocusController
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.stage = self.autofocusController.stage

    def add_components(self):
        self.entry_delta = QDoubleSpinBox()
        self.entry_delta.setMinimum(0)
        self.entry_delta.setMaximum(20)
        self.entry_delta.setSingleStep(0.2)
        self.entry_delta.setDecimals(3)
        self.entry_delta.setSuffix(" μm")
        self.entry_delta.setValue(1.524)
        self.entry_delta.setKeyboardTracking(False)
        self.entry_delta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_deltaZ(1.524)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(3)
        self.entry_N.setMaximum(10000)
        self.entry_N.setFixedWidth(self.entry_N.sizeHint().width())
        self.entry_N.setMaximum(20)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(10)
        self.entry_N.setKeyboardTracking(False)
        self.entry_N.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_N(10)

        self.btn_autofocus = QPushButton("Autofocus")
        self.btn_autofocus.setDefault(False)
        self.btn_autofocus.setCheckable(True)
        self.btn_autofocus.setChecked(False)

        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(False)
        self.btn_autolevel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # layout
        self.grid = QVBoxLayout()
        grid_line0 = QHBoxLayout()
        grid_line0.addWidget(QLabel("\u0394 Z"))
        grid_line0.addWidget(self.entry_delta)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(QLabel("# of Z-Planes"))
        grid_line0.addWidget(self.entry_N)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(self.btn_autolevel)

        self.grid.addLayout(grid_line0)
        self.grid.addWidget(self.btn_autofocus)
        self.setLayout(self.grid)

        # connections
        self.btn_autofocus.toggled.connect(lambda: self.autofocusController.autofocus(False))
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.entry_delta.valueChanged.connect(self.set_deltaZ)
        self.entry_N.valueChanged.connect(self.autofocusController.set_N)
        self.autofocusController.autofocusFinished.connect(self.autofocus_is_finished)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.log.debug(f"{deltaZ=}")

        self.entry_delta.setValue(deltaZ)
        self.autofocusController.set_deltaZ(deltaZ)

    def autofocus_is_finished(self):
        self.btn_autofocus.setChecked(False)
