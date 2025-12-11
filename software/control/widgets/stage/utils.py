from control.widgets.stage._common import *
from squid.events import (
    HomeStageCommand,
    MoveStageToCommand,
    MoveStageToLoadingPositionCommand,
    MoveStageToScanningPositionCommand,
    ZeroStageCommand,
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    LoadingPositionReached,
    ScanningPositionReached,
    ThreadedStageMoveBegan,
)
from control._def import Z_HOME_SAFETY_POINT


class StageUtils(EventBusDialog):
    """Dialog containing microscope utility functions like homing, zeroing, and slide positioning."""

    signal_threaded_stage_move_started: Signal = Signal()
    signal_loading_position_reached: Signal = Signal(bool, object)
    signal_scanning_position_reached: Signal = Signal(bool, object)

    def __init__(
        self,
        event_bus: "EventBus",
        is_wellplate: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(event_bus, parent=parent)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.is_wellplate: bool = is_wellplate
        self.slide_position: Optional[str] = None
        self._was_live: bool = False
        self._is_live: bool = False

        # UI components
        self.btn_home_X: QPushButton
        self.btn_home_Y: QPushButton
        self.btn_home_Z: QPushButton
        self.btn_zero_X: QPushButton
        self.btn_zero_Y: QPushButton
        self.btn_zero_Z: QPushButton
        self.btn_load_slide: QPushButton

        self.setWindowTitle("Stage Utils")
        self.setModal(False)  # Allow interaction with main window while dialog is open
        self.setup_ui()
        self._subscribe(LiveStateChanged, self._on_live_state_changed)
        # Ensure callbacks from worker threads marshal back to the Qt thread
        self.signal_loading_position_reached.connect(
            self._handle_loading_position_reached
        )
        self.signal_scanning_position_reached.connect(
            self._handle_scanning_position_reached
        )

    def setup_ui(self) -> None:
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

    def home_x(self) -> None:
        """Home X axis with confirmation dialog."""
        self._show_confirmation_dialog(x=True, y=False, z=False, theta=False)

    def home_y(self) -> None:
        """Home Y axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=True, z=False, theta=False)

    def home_z(self) -> None:
        """Home Z axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=False, z=True, theta=False)
        self._publish(MoveStageToCommand(z_mm=int(Z_HOME_SAFETY_POINT) / 1000.0))

    def _show_confirmation_dialog(self, x: bool, y: bool, z: bool, theta: bool) -> None:
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
            self._publish(HomeStageCommand(x=x, y=y, z=z, theta=theta))

    def zero_x(self) -> None:
        """Zero X axis position."""
        self._publish(ZeroStageCommand(x=True, y=False, z=False, theta=False))

    def zero_y(self) -> None:
        """Zero Y axis position."""
        self._publish(ZeroStageCommand(x=False, y=True, z=False, theta=False))

    def zero_z(self) -> None:
        """Zero Z axis position."""
        self._publish(ZeroStageCommand(x=False, y=False, z=True, theta=False))

    def switch_position(self) -> None:
        """Switch between loading and scanning positions."""
        self._was_live = self._is_live
        if self._was_live:
            self._publish(StopLiveCommand())
        self.signal_threaded_stage_move_started.emit()
        self._publish(ThreadedStageMoveBegan())
        if self.slide_position != "loading":
            self._publish(
                MoveStageToLoadingPositionCommand(
                    blocking=False,
                    callback=self._callback_loading_position_reached,
                    is_wellplate=self.is_wellplate,
                )
            )
        else:
            self._publish(
                MoveStageToScanningPositionCommand(
                    blocking=False,
                    callback=self._callback_scanning_position_reached,
                    is_wellplate=self.is_wellplate,
                )
            )
        self.btn_load_slide.setEnabled(False)

    def _callback_loading_position_reached(
        self, success: bool, error_message: Optional[str]
    ) -> None:
        """Receive callback from worker thread and forward to main thread handler."""
        self.signal_loading_position_reached.emit(success, error_message)

    def _handle_loading_position_reached(
        self, success: bool, error_message: Optional[str]
    ) -> None:
        """Handle slide loading position reached signal (Qt thread)."""
        self.slide_position = "loading"
        self.btn_load_slide.setStyleSheet("background-color: #C2FFC2")
        self.btn_load_slide.setText("Move to Scanning Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self._publish(StartLiveCommand())
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self._publish(LoadingPositionReached())

    def _callback_scanning_position_reached(
        self, success: bool, error_message: Optional[str]
    ) -> None:
        """Receive callback from worker thread and forward to main thread handler."""
        self.signal_scanning_position_reached.emit(success, error_message)

    def _handle_scanning_position_reached(
        self, success: bool, error_message: Optional[str]
    ) -> None:
        """Handle slide scanning position reached signal (Qt thread)."""
        self.slide_position = "scanning"
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")
        self.btn_load_slide.setText("Move to Loading Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self._publish(StartLiveCommand())
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self._publish(ScanningPositionReached())

    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Track live state from bus."""
        self._is_live = event.is_live
