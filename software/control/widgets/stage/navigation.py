from control.widgets.stage._common import *
from squid.events import MoveStageCommand


class NavigationWidget(QFrame):
    def __init__(
        self,
        stage_service: "StageService",
        main: Optional[Any] = None,
        widget_configuration: str = "full",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.widget_configuration: str = widget_configuration
        self.slide_position: Optional[str] = None

        self._service: "StageService" = stage_service

        # UI components
        self.label_Xpos: QLabel
        self.label_Ypos: QLabel
        self.label_Zpos: QLabel
        self.entry_dX: QDoubleSpinBox
        self.entry_dY: QDoubleSpinBox
        self.entry_dZ: QDoubleSpinBox
        self.btn_moveX_forward: QPushButton
        self.btn_moveX_backward: QPushButton
        self.btn_moveY_forward: QPushButton
        self.btn_moveY_backward: QPushButton
        self.btn_moveZ_forward: QPushButton
        self.btn_moveZ_backward: QPushButton
        self.checkbox_clickToMove: QCheckBox
        self.grid: QVBoxLayout

        # Subscribe to position updates
        event_bus.subscribe(StagePositionChanged, self._on_position_changed)

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.position_update_timer: QTimer = QTimer()
        self.position_update_timer.setInterval(100)
        self.position_update_timer.timeout.connect(self._update_position)
        self.position_update_timer.start()

    def _update_position(self) -> None:
        pos = self._service.get_position()
        self.label_Xpos.setNum(pos.x_mm)
        self.label_Ypos.setNum(pos.y_mm)
        # NOTE: The z label is in um
        self.label_Zpos.setNum(pos.z_mm * 1000)

    def _on_position_changed(self, event: StagePositionChanged) -> None:
        """Handle position changed event."""
        self.label_Xpos.setNum(event.x_mm)
        self.label_Ypos.setNum(event.y_mm)
        self.label_Zpos.setNum(event.z_mm * 1000)

    def add_components(self) -> None:
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
        self.checkbox_clickToMove.setSizePolicy(
            QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        )

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

    def set_click_to_move(self, enabled: bool) -> None:
        self.log.info(f"Click to move enabled={enabled}")
        self.setEnabled_all(enabled)
        self.checkbox_clickToMove.setChecked(enabled)

    def get_click_to_move_enabled(self) -> bool:
        return self.checkbox_clickToMove.isChecked()

    def setEnabled_all(self, enabled: bool) -> None:
        self.checkbox_clickToMove.setEnabled(enabled)
        self.btn_moveX_forward.setEnabled(enabled)
        self.btn_moveX_backward.setEnabled(enabled)
        self.btn_moveY_forward.setEnabled(enabled)
        self.btn_moveY_backward.setEnabled(enabled)
        self.btn_moveZ_forward.setEnabled(enabled)
        self.btn_moveZ_backward.setEnabled(enabled)

    def move_x_forward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="x", distance_mm=self.entry_dX.value())
        )

    def move_x_backward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="x", distance_mm=-self.entry_dX.value())
        )

    def move_y_forward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="y", distance_mm=self.entry_dY.value())
        )

    def move_y_backward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="y", distance_mm=-self.entry_dY.value())
        )

    def move_z_forward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="z", distance_mm=self.entry_dZ.value() / 1000)
        )

    def move_z_backward(self) -> None:
        event_bus.publish(
            MoveStageCommand(axis="z", distance_mm=-(self.entry_dZ.value() / 1000))
        )

    def set_deltaX(self, value: float) -> None:
        mm_per_ustep = self._service.get_x_mm_per_ustep()
        deltaX = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dX.setValue(deltaX)

    def set_deltaY(self, value: float) -> None:
        mm_per_ustep = self._service.get_y_mm_per_ustep()
        deltaY = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dY.setValue(deltaY)

    def set_deltaZ(self, value: float) -> None:
        mm_per_ustep = self._service.get_z_mm_per_ustep()
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_dZ.setValue(deltaZ)
