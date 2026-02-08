from squid.ui.widgets.wellplate._common import (
    CAMERA_CONFIG,
    EventBusDialog,
    INVERTED_OBJECTIVE,
    Image,
    ImageDraw,
    ImageFont,
    List,
    Optional,
    QButtonGroup,
    QCheckBox,
    QColor,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPen,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
    TYPE_CHECKING,
    Tuple,
    Union,
    WELLPLATE_FORMAT_SETTINGS,
    handles,
    math,
    np,
    os,
    pg,
)
import squid.core.logging

from squid.core.events import (
    MoveStageRelativeCommand,
    StartLiveCommand,
    StopLiveCommand,
    LiveStateChanged,
    StagePositionChanged,
    SaveWellplateCalibrationCommand,
)

if TYPE_CHECKING:
    from squid.backend.io.stream_handler import StreamHandler
    from squid.ui.widgets.wellplate.format import WellplateFormatWidget
    from squid.ui.widgets.tracking.joystick import Joystick

_log = squid.core.logging.get_logger(__name__)


class WellplateCalibration(EventBusDialog):
    """Wellplate calibration dialog using UIEventBus.

    Publishes MoveStageCommand for stage movement.
    Publishes StartLiveCommand/StopLiveCommand for live view.
    Subscribes to LiveStateChanged and StagePositionChanged for state tracking.
    """

    def __init__(
        self,
        wellplateFormatWidget: "WellplateFormatWidget",
        streamHandler: "StreamHandler",
        event_bus: "UIEventBus",
        # Read-only config passed as params
        pixel_size_factor: float = 1.0,
        pixel_size_binned_um: float = 0.084665,
        was_live: bool = False,
        previous_format: str = "glass slide",
    ) -> None:
        super().__init__(event_bus)
        self.setWindowTitle("Well Plate Calibration")
        self.wellplateFormatWidget: "WellplateFormatWidget" = wellplateFormatWidget
        self.streamHandler: "StreamHandler" = streamHandler
        self._previous_format: str = previous_format

        # Read-only config from params (no direct camera/controller access)
        self._pixel_size_factor = pixel_size_factor
        self._pixel_size_binned_um = pixel_size_binned_um

        self._is_live: bool = False
        self.was_live: bool = was_live
        self.corners: List[Optional[Tuple[float, float]]] = [None, None, None]
        self.center_point: Optional[Tuple[float, float]] = None  # For center point calibration method
        self.show_virtual_joystick: bool = True  # FLAG

        # Cache current position for setCorner
        self._current_position: Optional[Tuple[float, float]] = None

        # UI elements
        self.mode_group: QButtonGroup
        self.new_format_radio: QRadioButton
        self.calibrate_format_radio: QRadioButton
        self.existing_format_combo: QComboBox
        self.new_format_widget: QWidget
        self.form_layout: QFormLayout
        self.nameInput: QLineEdit
        self.rowsInput: QSpinBox
        self.colsInput: QSpinBox
        self.plateWidthInput: QDoubleSpinBox
        self.plateHeightInput: QDoubleSpinBox
        self.wellSpacingInput: QDoubleSpinBox
        self.existing_params_group: QGroupBox
        self.existing_spacing_input: QDoubleSpinBox
        self.existing_well_size_input: QDoubleSpinBox
        self.update_params_button: QPushButton
        self.calibration_method_group: QGroupBox
        self.method_button_group: QButtonGroup
        self.edge_points_radio: QRadioButton
        self.center_point_radio: QRadioButton
        self.points_widget: QWidget
        self.edge_points_label: QLabel
        self.cornerLabels: List[QLabel] = []
        self.setPointButtons: List[QPushButton] = []
        self.center_point_widget: QWidget
        self.center_point_status_label: QLabel
        self.set_center_button: QPushButton
        self.center_well_size_label: QLabel
        self.center_well_size_input: QDoubleSpinBox
        self.clickToMoveCheckbox: QCheckBox
        self.showJoystickCheckbox: QCheckBox
        self.calibrateButton: QPushButton
        self.live_viewer: "CalibrationLiveViewer"
        self.right_layout: QVBoxLayout
        self.joystick: "Joystick"
        self.sensitivitySlider: QSlider

        self.initUI()
        # Initially allow click-to-move and hide the joystick controls
        self.clickToMoveCheckbox.setChecked(True)
        self.toggleVirtualJoystick(False)
        # Set minimum height to accommodate all UI configurations
        self.setMinimumHeight(580)

    def initUI(self) -> None:
        layout = QHBoxLayout(self)  # Change to QHBoxLayout to have two columns

        # Left column for existing controls
        left_layout = QVBoxLayout()

        # Add radio buttons for selecting mode
        self.mode_group = QButtonGroup(self)
        self.new_format_radio = QRadioButton("Add New Format")
        self.calibrate_format_radio = QRadioButton("Calibrate Existing Format")
        self.mode_group.addButton(self.new_format_radio)
        self.mode_group.addButton(self.calibrate_format_radio)
        self.new_format_radio.setChecked(True)

        left_layout.addWidget(self.new_format_radio)
        left_layout.addWidget(self.calibrate_format_radio)

        # Existing format selection (initially hidden)
        self.existing_format_combo = QComboBox(self)
        self.populate_existing_formats()
        self.existing_format_combo.hide()
        self.existing_format_combo.currentIndexChanged.connect(self.on_existing_format_changed)
        left_layout.addWidget(self.existing_format_combo)

        # Connect radio buttons to toggle visibility
        self.new_format_radio.toggled.connect(self.toggle_input_mode)
        self.calibrate_format_radio.toggled.connect(self.toggle_input_mode)

        # New format inputs container (hidden when calibrating existing format)
        self.new_format_widget = QWidget()
        self.form_layout = QFormLayout(self.new_format_widget)
        self.form_layout.setContentsMargins(0, 0, 0, 0)

        self.nameInput = QLineEdit(self)
        self.nameInput.setPlaceholderText("custom well plate")
        self.form_layout.addRow("Sample Name:", self.nameInput)

        self.rowsInput = QSpinBox(self)
        self.rowsInput.setKeyboardTracking(False)
        self.rowsInput.setRange(1, 100)
        self.rowsInput.setValue(8)
        self.form_layout.addRow("# Rows:", self.rowsInput)

        self.colsInput = QSpinBox(self)
        self.colsInput.setKeyboardTracking(False)
        self.colsInput.setRange(1, 100)
        self.colsInput.setValue(12)
        self.form_layout.addRow("# Columns:", self.colsInput)

        # Add new inputs for plate dimensions
        self.plateWidthInput = QDoubleSpinBox(self)
        self.plateWidthInput.setKeyboardTracking(False)
        self.plateWidthInput.setRange(10, 500)
        self.plateWidthInput.setValue(127.76)  # Default for standard 96-well plate
        self.plateWidthInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Width:", self.plateWidthInput)

        self.plateHeightInput = QDoubleSpinBox(self)
        self.plateHeightInput.setKeyboardTracking(False)
        self.plateHeightInput.setRange(10, 500)
        self.plateHeightInput.setValue(85.48)  # Default for standard 96-well plate
        self.plateHeightInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Height:", self.plateHeightInput)

        self.wellSpacingInput = QDoubleSpinBox(self)
        self.wellSpacingInput.setKeyboardTracking(False)
        self.wellSpacingInput.setRange(0.1, 100)
        self.wellSpacingInput.setValue(9)
        self.wellSpacingInput.setSingleStep(0.1)
        self.wellSpacingInput.setDecimals(2)
        self.wellSpacingInput.setSuffix(" mm")
        self.form_layout.addRow("Well Spacing:", self.wellSpacingInput)

        left_layout.addWidget(self.new_format_widget)

        # Existing format parameters section (initially hidden)
        self.existing_params_group = QGroupBox("Format Parameters")
        existing_params_layout = QFormLayout()

        self.existing_spacing_input = QDoubleSpinBox(self)
        self.existing_spacing_input.setKeyboardTracking(False)
        self.existing_spacing_input.setRange(0.1, 100)
        self.existing_spacing_input.setSingleStep(0.1)
        self.existing_spacing_input.setDecimals(3)
        self.existing_spacing_input.setSuffix(" mm")
        existing_params_layout.addRow("Well Spacing:", self.existing_spacing_input)

        self.existing_well_size_input = QDoubleSpinBox(self)
        self.existing_well_size_input.setKeyboardTracking(False)
        self.existing_well_size_input.setRange(0.1, 50)
        self.existing_well_size_input.setSingleStep(0.1)
        self.existing_well_size_input.setDecimals(3)
        self.existing_well_size_input.setSuffix(" mm")
        existing_params_layout.addRow("Well Size:", self.existing_well_size_input)

        self.existing_params_group.setLayout(existing_params_layout)

        self.update_params_button = QPushButton("Update Parameters")
        self.update_params_button.clicked.connect(self.update_existing_parameters)

        self.existing_params_group.hide()
        self.update_params_button.hide()
        left_layout.addWidget(self.existing_params_group)
        left_layout.addWidget(self.update_params_button)

        # Calibration method selection
        self.calibration_method_group = QGroupBox("Calibration Method")
        calibration_method_layout = QVBoxLayout()

        self.method_button_group = QButtonGroup(self)
        self.edge_points_radio = QRadioButton("3 Edge Points (recommended for large wells)")
        self.center_point_radio = QRadioButton("Center Point (recommended for small wells)")
        self.method_button_group.addButton(self.edge_points_radio)
        self.method_button_group.addButton(self.center_point_radio)
        self.edge_points_radio.setChecked(True)

        calibration_method_layout.addWidget(self.edge_points_radio)
        calibration_method_layout.addWidget(self.center_point_radio)
        self.calibration_method_group.setLayout(calibration_method_layout)
        left_layout.addWidget(self.calibration_method_group)

        # Only connect one radio button to avoid double-calls (both emit toggled when selection changes)
        self.edge_points_radio.toggled.connect(self.toggle_calibration_method)

        # 3 Edge Points UI
        self.points_widget = QWidget()
        points_layout = QGridLayout(self.points_widget)
        points_layout.setContentsMargins(0, 0, 0, 0)
        self.cornerLabels = []
        self.setPointButtons = []
        self.edge_points_label = QLabel("Navigate to and Select\n3 Points on the Edge of Well A1")
        self.edge_points_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        points_layout.addWidget(self.edge_points_label, 0, 0, 1, 2)
        for i in range(1, 4):
            label = QLabel(f"Point {i}: N/A")
            button = QPushButton("Set Point")
            button.setFixedWidth(button.sizeHint().width())
            button.clicked.connect(lambda checked, index=i - 1: self.setCorner(index))
            points_layout.addWidget(label, i, 0)
            points_layout.addWidget(button, i, 1)
            self.cornerLabels.append(label)
            self.setPointButtons.append(button)

        points_layout.setColumnStretch(0, 1)
        left_layout.addWidget(self.points_widget)

        # Center Point UI
        self.center_point_widget = QWidget()
        center_point_layout = QGridLayout(self.center_point_widget)
        center_point_layout.setContentsMargins(0, 0, 0, 0)

        center_point_label = QLabel("Navigate to the Center of Well A1")
        center_point_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_point_layout.addWidget(center_point_label, 0, 0, 1, 2)

        self.center_point_status_label = QLabel("Center: Not set")
        self.set_center_button = QPushButton("Set Center")
        self.set_center_button.setFixedWidth(self.set_center_button.sizeHint().width())
        self.set_center_button.clicked.connect(self.setCenterPoint)
        center_point_layout.addWidget(self.center_point_status_label, 1, 0)
        center_point_layout.addWidget(self.set_center_button, 1, 1)

        # Well size input for center point method (since we can't calculate it)
        # Hidden when calibrating existing formats (Format Parameters section has well size)
        self.center_well_size_label = QLabel("Well Size:")
        self.center_well_size_input = QDoubleSpinBox(self)
        self.center_well_size_input.setKeyboardTracking(False)
        self.center_well_size_input.setRange(0.1, 50)
        self.center_well_size_input.setSingleStep(0.1)
        self.center_well_size_input.setDecimals(3)
        self.center_well_size_input.setValue(3.0)  # Default for small wells
        self.center_well_size_input.setSuffix(" mm")
        center_point_layout.addWidget(self.center_well_size_label, 2, 0)
        center_point_layout.addWidget(self.center_well_size_input, 2, 1)

        center_point_layout.setColumnStretch(0, 1)
        self.center_point_widget.hide()  # Initially hidden
        left_layout.addWidget(self.center_point_widget)

        # Add 'Click to Move' checkbox
        self.clickToMoveCheckbox = QCheckBox("Click to Move")
        self.clickToMoveCheckbox.stateChanged.connect(self.toggleClickToMove)
        left_layout.addWidget(self.clickToMoveCheckbox)

        # Add 'Show Virtual Joystick' checkbox
        self.showJoystickCheckbox = QCheckBox("Virtual Joystick")
        self.showJoystickCheckbox.stateChanged.connect(self.toggleVirtualJoystick)
        left_layout.addWidget(self.showJoystickCheckbox)

        self.calibrateButton = QPushButton("Calibrate")
        self.calibrateButton.clicked.connect(self.calibrate)
        self.calibrateButton.setEnabled(False)
        left_layout.addWidget(self.calibrateButton)

        # Add left column to main layout
        layout.addLayout(left_layout)

        self.live_viewer = CalibrationLiveViewer()
        if hasattr(self.streamHandler, "image_to_display"):
            self.streamHandler.image_to_display.connect(self.live_viewer.display_image)  # type: ignore[attr-defined]

        if not self.was_live:
            self._publish(StartLiveCommand())

        # when the dialog closes i want to stop live if live was stopped before. . . if it was on before, leave it on
        layout.addWidget(self.live_viewer)

        # Right column for joystick and sensitivity controls
        self.right_layout = QVBoxLayout()
        self.right_layout.addStretch(1)

        # Import Joystick here to avoid circular imports
        from squid.ui.widgets.tracking.joystick import Joystick

        self.joystick = Joystick(self)
        self.joystick.joystickMoved.connect(self.moveStage)
        self.right_layout.addWidget(
            self.joystick,
            0,
            Qt.AlignmentFlag(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter),
        )

        self.right_layout.addStretch(1)

        # Create a container widget for sensitivity label and slider
        sensitivity_layout = QVBoxLayout()

        sensitivityLabel = QLabel("Joystick Sensitivity")
        sensitivityLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sensitivity_layout.addWidget(sensitivityLabel)

        self.sensitivitySlider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivitySlider.setMinimum(1)
        self.sensitivitySlider.setMaximum(100)
        self.sensitivitySlider.setValue(50)
        self.sensitivitySlider.setTickPosition(QSlider.TicksBelow)
        self.sensitivitySlider.setTickInterval(10)

        label_width = sensitivityLabel.sizeHint().width()
        self.sensitivitySlider.setFixedWidth(label_width)

        sensitivity_layout.addWidget(
            self.sensitivitySlider, 0, Qt.AlignmentFlag.AlignHCenter
        )

        self.right_layout.addLayout(sensitivity_layout)

        layout.addLayout(self.right_layout)

    def toggleVirtualJoystick(self, state: Union[bool, int]) -> None:
        if state:
            self.joystick.show()
            self.sensitivitySlider.show()
            joystick_item = self.right_layout.itemAt(
                self.right_layout.indexOf(self.joystick)
            )
            if joystick_item is not None:
                widget = joystick_item.widget()
                if widget is not None:
                    widget.show()
            last_item = self.right_layout.itemAt(self.right_layout.count() - 1)
            if last_item is not None:
                last_layout = last_item.layout()
                if last_layout is not None:
                    label_item = last_layout.itemAt(0)
                    slider_item = last_layout.itemAt(1)
                    if label_item is not None:
                        label_widget = label_item.widget()
                        if label_widget is not None:
                            label_widget.show()  # Show sensitivity label
                    if slider_item is not None:
                        slider_widget = slider_item.widget()
                        if slider_widget is not None:
                            slider_widget.show()  # Show sensitivity slider
        else:
            self.joystick.hide()
            self.sensitivitySlider.hide()
            joystick_item = self.right_layout.itemAt(
                self.right_layout.indexOf(self.joystick)
            )
            if joystick_item is not None:
                widget = joystick_item.widget()
                if widget is not None:
                    widget.hide()
            last_item = self.right_layout.itemAt(self.right_layout.count() - 1)
            if last_item is not None:
                last_layout = last_item.layout()
                if last_layout is not None:
                    label_item = last_layout.itemAt(0)
                    slider_item = last_layout.itemAt(1)
                    if label_item is not None:
                        label_widget = label_item.widget()
                        if label_widget is not None:
                            label_widget.hide()  # Hide sensitivity label
                    if slider_item is not None:
                        slider_widget = slider_item.widget()
                        if slider_widget is not None:
                            slider_widget.hide()  # Hide sensitivity slider

    def moveStage(self, x: float, y: float) -> None:
        sensitivity = self.sensitivitySlider.value() / 50.0  # Normalize to 0-2 range
        max_speed = 0.1 * sensitivity
        exponent = 2

        dx = math.copysign(max_speed * abs(x) ** exponent, x)
        dy = math.copysign(max_speed * abs(y) ** exponent, y)

        self._move_stage_relative(dx, dy)

    def toggleClickToMove(self, state: int) -> None:
        if state == Qt.CheckState.Checked:
            self.live_viewer.signal_calibration_viewer_click.connect(self.viewerClicked)
        else:
            self.live_viewer.signal_calibration_viewer_click.disconnect(
                self.viewerClicked
            )

    def viewerClicked(self, x: int, y: int, width: int, height: int) -> None:
        pixel_size_um = self._pixel_size_factor * self._pixel_size_binned_um

        pixel_sign_x = 1
        pixel_sign_y = 1 if INVERTED_OBJECTIVE else -1

        delta_x = pixel_sign_x * pixel_size_um * x / 1000.0
        delta_y = pixel_sign_y * pixel_size_um * y / 1000.0

        self._move_stage_relative(delta_x, delta_y)

    def _move_stage_relative(self, dx: float, dy: float) -> None:
        """Move stage by relative distance via EventBus."""
        self._publish(MoveStageRelativeCommand(x_mm=dx, y_mm=dy))

    @handles(StagePositionChanged)
    def _on_stage_position_changed(self, event: StagePositionChanged) -> None:
        """Cache current position from event."""
        self._current_position = (event.x_mm, event.y_mm)

    def setCorner(self, index: int) -> None:
        if self.corners[index] is None:
            # Get position from cached event position
            if self._current_position is None:
                QMessageBox.warning(
                    self,
                    "Position Unknown",
                    "Stage position not available. Please wait for the stage to report its position.",
                )
                return
            x, y = self._current_position

            # Check if the new point is different from existing points
            if any(
                corner is not None and np.allclose([x, y], corner)
                for corner in self.corners
            ):
                QMessageBox.warning(
                    self,
                    "Duplicate Point",
                    "This point is too close to an existing point. Please choose a different location.",
                )
                return

            self.corners[index] = (x, y)
            self.cornerLabels[index].setText(f"Point {index + 1}: ({x:.3f}, {y:.3f})")
            self.setPointButtons[index].setText("Clear Point")
        else:
            self.corners[index] = None
            self.cornerLabels[index].setText(f"Point {index + 1}: Not set")
            self.setPointButtons[index].setText("Set Point")

        self.update_calibrate_button_state()

    def _format_display_name(self, format_id) -> str:
        """Return a display name for a wellplate format, adding 'well plate' suffix if not present."""
        name = str(format_id)
        if "well plate" not in name.lower():
            return f"{format_id} well plate"
        return name

    def populate_existing_formats(self) -> None:
        self.existing_format_combo.clear()
        for format_ in WELLPLATE_FORMAT_SETTINGS:
            self.existing_format_combo.addItem(self._format_display_name(format_), format_)

    def toggle_input_mode(self) -> None:
        is_new_format = self.new_format_radio.isChecked()

        self.new_format_widget.setVisible(is_new_format)
        self.center_well_size_label.setVisible(is_new_format)
        self.center_well_size_input.setVisible(is_new_format)

        self.existing_format_combo.setVisible(not is_new_format)
        self.existing_params_group.setVisible(not is_new_format)
        self.update_params_button.setVisible(not is_new_format)

        if not is_new_format:
            self.load_existing_format_values()

    def load_existing_format_values(self) -> None:
        """Load current values from selected existing format into the parameter inputs."""
        selected_format = self.existing_format_combo.currentData()
        if selected_format is None:
            return

        settings = WELLPLATE_FORMAT_SETTINGS.get(selected_format, {})
        self.existing_spacing_input.setValue(settings.get("well_spacing_mm", 9.0))

        # Use consistent well size for both inputs
        well_size = settings.get("well_size_mm", 6.0)
        self.existing_well_size_input.setValue(well_size)
        self.center_well_size_input.setValue(well_size)

        # Auto-select center point method for 384 and 1536 well plates because their
        # small well diameters make it difficult to reliably set 3 distinct points
        # on the well edge under a microscope
        if selected_format in ("384 well plate", "1536 well plate"):
            self.center_point_radio.setChecked(True)
        else:
            self.edge_points_radio.setChecked(True)

    def on_existing_format_changed(self) -> None:
        """Handle existing format combo box selection change."""
        if self.calibrate_format_radio.isChecked():
            self.load_existing_format_values()
            # Reset calibration points when format changes
            self.reset_calibration_points()

    def reset_calibration_points(self) -> None:
        """Reset all calibration points to unset state."""
        # Reset edge points
        for i in range(3):
            self.corners[i] = None
            self.cornerLabels[i].setText(f"Point {i + 1}: Not set")
            self.setPointButtons[i].setText("Set Point")

        # Reset center point
        self.center_point = None
        self.center_point_status_label.setText("Center: Not set")
        self.set_center_button.setText("Set Center")

        self.update_calibrate_button_state()

    def toggle_calibration_method(self) -> None:
        """Toggle between 3 edge points and center point calibration methods."""
        if self.edge_points_radio.isChecked():
            self.points_widget.show()
            self.center_point_widget.hide()
        else:
            self.points_widget.hide()
            self.center_point_widget.show()
        self.update_calibrate_button_state()

    def setCenterPoint(self) -> None:
        """Set or clear the center point for center point calibration method."""
        if self.center_point is None:
            if self._current_position is None:
                QMessageBox.warning(
                    self,
                    "Position Unknown",
                    "Stage position not available. Please wait for the stage to report its position.",
                )
                return
            x, y = self._current_position
            self.center_point = (x, y)
            self.center_point_status_label.setText(f"Center: ({x:.3f}, {y:.3f})")
            self.set_center_button.setText("Clear Center")
        else:
            self.center_point = None
            self.center_point_status_label.setText("Center: Not set")
            self.set_center_button.setText("Set Center")
        self.update_calibrate_button_state()

    def update_calibrate_button_state(self) -> None:
        """Update the calibrate button enabled state based on current calibration method."""
        if self.center_point_radio.isChecked():
            self.calibrateButton.setEnabled(self.center_point is not None)
        else:
            self.calibrateButton.setEnabled(all(corner is not None for corner in self.corners))

    def _get_calibration_data(self) -> Optional[Tuple[float, float, float]]:
        """Extract calibration data based on current calibration method.

        Returns:
            tuple: (a1_x_mm, a1_y_mm, well_size_mm) or None if validation fails.
            Displays appropriate warning message if validation fails.
        """
        if self.center_point_radio.isChecked():
            if self.center_point is None:
                QMessageBox.warning(self, "Incomplete Information", "Please set the center point before calibrating.")
                return None
            a1_x_mm, a1_y_mm = self.center_point
            # Use appropriate well size input based on mode
            if self.calibrate_format_radio.isChecked():
                well_size_mm = self.existing_well_size_input.value()
            else:
                well_size_mm = self.center_well_size_input.value()
        else:
            if not all(self.corners):
                QMessageBox.warning(self, "Incomplete Information", "Please set 3 corner points before calibrating.")
                return None
            center, radius = self.calculate_circle(self.corners)
            well_size_mm = radius * 2
            a1_x_mm, a1_y_mm = center
        return a1_x_mm, a1_y_mm, well_size_mm

    def update_existing_parameters(self) -> None:
        """Update parameters for an existing format without recalibrating the position."""
        selected_format = self.existing_format_combo.currentData()
        if selected_format is None:
            QMessageBox.warning(self, "No Format Selected", "Please select a format to update.")
            return

        try:
            # Get the new values
            new_spacing = self.existing_spacing_input.value()
            new_well_size = self.existing_well_size_input.value()

            # Get existing settings
            existing_settings = WELLPLATE_FORMAT_SETTINGS.get(selected_format)
            if existing_settings is None:
                QMessageBox.critical(self, "Update Failed", f"Format '{selected_format}' not found in settings.")
                return

            display_name = self._format_display_name(selected_format)
            _log.info(f"Updating parameters for {display_name}")
            _log.info(
                f"OLD: spacing={existing_settings.get('well_spacing_mm')}, "
                f"well_size={existing_settings.get('well_size_mm')}"
            )
            _log.info(f"NEW: spacing={new_spacing}, well_size={new_well_size}")

            # Update the settings
            WELLPLATE_FORMAT_SETTINGS[selected_format].update(
                {
                    "well_spacing_mm": new_spacing,
                    "well_size_mm": new_well_size,
                }
            )

            # Save and refresh via event
            self._publish(
                SaveWellplateCalibrationCommand(
                    calibration={**WELLPLATE_FORMAT_SETTINGS[selected_format]},
                    name=selected_format,
                )
            )

            # Re-select the format
            self.wellplateFormatWidget.populate_combo_box()
            index = self.wellplateFormatWidget.comboBox.findData(selected_format)
            if index >= 0:
                self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

            QMessageBox.information(
                self,
                "Parameters Updated",
                f"Parameters for '{display_name}' have been updated successfully.",
            )

        except Exception as e:
            _log.exception("Failed to update existing format parameters")
            QMessageBox.critical(self, "Update Failed", f"An error occurred while updating parameters: {str(e)}")

    def calibrate(self) -> None:
        """Execute wellplate calibration based on current settings.

        Supports two modes:
        - New format: Creates a new custom wellplate format with all parameters
        - Existing format: Updates position calibration (a1_x_mm, a1_y_mm) and well_size_mm

        Supports two calibration methods:
        - 3 Edge Points: Calculates well center and diameter from 3 points on well edge
        - Center Point: Uses directly-specified center position with manual well size
        """
        try:
            if self.new_format_radio.isChecked():
                self._calibrate_new_format()
            else:
                self._calibrate_existing_format()
        except np.linalg.LinAlgError:
            _log.exception("Linear algebra error during calibration")
            QMessageBox.critical(
                self,
                "Calibration Error",
                "Unable to calculate well center from the provided points.\n"
                "The 3 points may be nearly collinear (in a straight line).\n"
                "Please choose points that are more spread out around the well edge.",
            )
        except Exception as e:
            _log.exception("Unexpected error during calibration")
            QMessageBox.critical(
                self,
                "Calibration Error",
                f"An error occurred during calibration: {str(e)}",
            )

    def _calibrate_new_format(self) -> None:
        """Create and calibrate a new wellplate format."""
        if not self.nameInput.text():
            QMessageBox.warning(self, "Incomplete Information", "Please enter a name for the format.")
            return

        calibration_data = self._get_calibration_data()
        if calibration_data is None:
            return
        a1_x_mm, a1_y_mm, well_size_mm = calibration_data

        name = self.nameInput.text()
        plate_width_mm = self.plateWidthInput.value()
        plate_height_mm = self.plateHeightInput.value()

        scale = 1 / 0.084665
        new_format = {
            "a1_x_mm": a1_x_mm,
            "a1_y_mm": a1_y_mm,
            "a1_x_pixel": round(a1_x_mm * scale),
            "a1_y_pixel": round(a1_y_mm * scale),
            "well_size_mm": well_size_mm,
            "well_spacing_mm": self.wellSpacingInput.value(),
            "number_of_skip": 0,
            "rows": self.rowsInput.value(),
            "cols": self.colsInput.value(),
        }

        self._publish(
            SaveWellplateCalibrationCommand(
                calibration=new_format,
                name=name,
                metadata={
                    "plate_width_mm": plate_width_mm,
                    "plate_height_mm": plate_height_mm,
                },
            )
        )
        self.create_wellplate_image(name, new_format, plate_width_mm, plate_height_mm)

        self._finish_calibration(name, f"New format '{name}' has been successfully created and calibrated.")

    def _calibrate_existing_format(self) -> None:
        """Recalibrate an existing wellplate format."""
        selected_format = self.existing_format_combo.currentData()

        calibration_data = self._get_calibration_data()
        if calibration_data is None:
            return
        a1_x_mm, a1_y_mm, well_size_mm = calibration_data

        existing_settings = WELLPLATE_FORMAT_SETTINGS[selected_format]
        display_name = self._format_display_name(selected_format)

        _log.info(f"Updating existing format {display_name}")
        _log.info(
            f"OLD: 'a1_x_mm': {existing_settings['a1_x_mm']}, 'a1_y_mm': {existing_settings['a1_y_mm']}, "
            f"'well_size_mm': {existing_settings['well_size_mm']}"
        )
        _log.info(f"NEW: 'a1_x_mm': {a1_x_mm}, 'a1_y_mm': {a1_y_mm}, 'well_size_mm': {well_size_mm}")

        updated_settings = {
            "a1_x_mm": a1_x_mm,
            "a1_y_mm": a1_y_mm,
            "well_size_mm": well_size_mm,
        }

        self._publish(
            SaveWellplateCalibrationCommand(
                calibration={
                    **WELLPLATE_FORMAT_SETTINGS[selected_format],
                    **updated_settings,
                },
                name=selected_format,
            )
        )

        self._finish_calibration(
            selected_format, f"Format '{display_name}' has been successfully recalibrated."
        )

    def _finish_calibration(self, format_id: str, success_message: str) -> None:
        """Complete calibration by updating UI and showing success message."""
        self.wellplateFormatWidget.populate_combo_box()
        index = self.wellplateFormatWidget.comboBox.findData(format_id)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

        QMessageBox.information(self, "Calibration Successful", success_message)
        self.accept()

    def create_wellplate_image(
        self, name: str, format_data: dict, plate_width_mm: float, plate_height_mm: float
    ) -> str:
        scale = 1 / 0.084665

        def mm_to_px(mm: float) -> int:
            return round(mm * scale)

        width = mm_to_px(plate_width_mm)
        height = mm_to_px(plate_height_mm)
        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        rows, cols = format_data["rows"], format_data["cols"]
        well_spacing_mm = format_data["well_spacing_mm"]
        well_size_mm = format_data["well_size_mm"]
        a1_x_mm, a1_y_mm = format_data["a1_x_mm"], format_data["a1_y_mm"]

        def draw_left_slanted_rectangle(
            draw, xy, slant, width=4, outline="black", fill=None
        ):
            x1, y1, x2, y2 = xy

            # Define the polygon points
            points = [
                (x1 + slant, y1),  # Top-left after slant
                (x2, y1),  # Top-right
                (x2, y2),  # Bottom-right
                (x1 + slant, y2),  # Bottom-left after slant
                (x1, y2 - slant),  # Bottom of left slant
                (x1, y1 + slant),  # Top of left slant
            ]

            # Draw the filled polygon with outline
            draw.polygon(points, fill=fill, outline=outline, width=width)

        # Draw the outer rectangle with rounded corners
        corner_radius = 20
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=corner_radius,
            outline="black",
            width=4,
            fill="grey",
        )

        # Draw the inner rectangle with left slanted corners
        margin = 20
        slant = 40
        draw_left_slanted_rectangle(
            draw,
            [margin, margin, width - margin, height - margin],
            slant,
            width=4,
            outline="black",
            fill="lightgrey",
        )

        # Function to draw a circle
        def draw_circle(x, y, diameter):
            radius = diameter / 2
            draw.ellipse(
                [x - radius, y - radius, x + radius, y + radius],
                outline="black",
                width=4,
                fill="white",
            )

        # Draw the wells
        for row in range(rows):
            for col in range(cols):
                x = mm_to_px(a1_x_mm + col * well_spacing_mm)
                y = mm_to_px(a1_y_mm + row * well_spacing_mm)
                draw_circle(x, y, mm_to_px(well_size_mm))

        # Load a default font
        font_size = 30
        font = ImageFont.load_default().font_variant(size=font_size)

        # Add column labels
        for col in range(cols):
            label = str(col + 1)
            x = mm_to_px(a1_x_mm + col * well_spacing_mm)
            y = mm_to_px((a1_y_mm - well_size_mm / 2) / 2)
            bbox = font.getbbox(label)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.text((x - text_width / 2, y), label, fill="black", font=font)

        # Add row labels
        for row in range(rows):
            label = (
                chr(65 + row)
                if row < 26
                else chr(65 + row // 26 - 1) + chr(65 + row % 26)
            )
            x = mm_to_px((a1_x_mm - well_size_mm / 2) / 2)
            y = mm_to_px(a1_y_mm + row * well_spacing_mm)
            bbox = font.getbbox(label)
            text_height = bbox[3] - bbox[1]
            text_width = bbox[2] - bbox[0]
            draw.text(
                (x + 20 - text_width / 2, y - text_height + 1),
                label,
                fill="black",
                font=font,
            )

        image_path = os.path.join("images", f"{name.replace(' ', '_')}.png")
        image.save(image_path)
        print(f"Wellplate image saved as {image_path}")
        return image_path

    @staticmethod
    def calculate_circle(points: List[Optional[Tuple[float, float]]]) -> Tuple[np.ndarray, float]:
        # Convert points to numpy array
        points_arr = np.array(points)

        # Calculate the center and radius of the circle
        A = np.array([points_arr[1] - points_arr[0], points_arr[2] - points_arr[0]])
        b = np.sum(A * (points_arr[1:3] + points_arr[0]) / 2, axis=1)
        center = np.linalg.solve(A, b)

        # Calculate the radius
        radius = np.mean(np.linalg.norm(points_arr - center, axis=1))

        return center, radius

    @handles(LiveStateChanged)
    def _on_live_state_changed(self, event: LiveStateChanged) -> None:
        """Track live state from the event bus."""
        if getattr(event, "camera", "main") != "main":
            return
        self._is_live = event.is_live

    def _stop_live_if_needed(self) -> None:
        """Stop live view if it wasn't initially on."""
        if not self.was_live and self._is_live:
            self._publish(StopLiveCommand())

    def closeEvent(self, event) -> None:
        # Stop live view if it wasn't initially on
        self._stop_live_if_needed()
        super().closeEvent(event)

    def accept(self) -> None:
        # Stop live view if it wasn't initially on
        self._stop_live_if_needed()
        super().accept()

    def reject(self) -> None:
        # This method is called when the dialog is closed without accepting
        self._stop_live_if_needed()
        # Restore the selection that was active when calibration opened.
        index = self.wellplateFormatWidget.comboBox.findData(self._previous_format)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.blockSignals(True)
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)
            self.wellplateFormatWidget.comboBox.blockSignals(False)

        # Update wellplate settings
        self.wellplateFormatWidget.setWellplateSettings(self._previous_format)

        super().reject()


class CalibrationLiveViewer(QWidget):
    signal_calibration_viewer_click = Signal(int, int, int, int)
    signal_mouse_moved = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.initial_zoom_set = False
        self.initUI()

    def initUI(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = pg.GraphicsLayoutWidget()
        self.viewbox = self.view.addViewBox()
        self.viewbox.setAspectLocked(True)
        self.viewbox.invertY(True)

        self.viewbox.setMouseEnabled(x=False, y=False)  # Disable panning
        self.viewbox.setMenuEnabled(False)

        # Set appropriate panning limits based on the acquisition image or plate size
        xmax = int(CAMERA_CONFIG.CROP_WIDTH_UNBINNED)
        ymax = int(CAMERA_CONFIG.CROP_HEIGHT_UNBINNED)
        self.viewbox.setLimits(xMin=0, xMax=xmax, yMin=0, yMax=ymax)

        self.img_item = pg.ImageItem()
        self.viewbox.addItem(self.img_item)

        # Add fixed crosshair
        pen = QPen(QColor(255, 0, 0))  # Red color
        pen.setWidth(4)

        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.viewbox.addItem(self.crosshair_h)
        self.viewbox.addItem(self.crosshair_v)

        layout.addWidget(self.view)

        # Connect double-click event
        self.view.scene().sigMouseClicked.connect(self.onMouseClicked)

        # Set fixed size for the viewer
        self.setFixedSize(500, 500)

    def setCrosshairPosition(self) -> None:
        center = self.viewbox.viewRect().center()
        self.crosshair_h.setPos(center.y())
        self.crosshair_v.setPos(center.x())

    def display_image(self, image) -> None:
        # Step 1: Update the image
        self.img_item.setImage(image)

        # Step 2: Get the image dimensions
        image_width = image.shape[1]
        image_height = image.shape[0]

        # Step 3: Calculate the center of the image
        image_center_x = image_width / 2
        image_center_y = image_height / 2

        # Step 4: Calculate the current view range
        current_view_range = self.viewbox.viewRect()

        # Step 5: If it's the first image or initial zoom hasn't been set, center the image
        if not self.initial_zoom_set:
            self.viewbox.setRange(
                xRange=(0, image_width), yRange=(0, image_height), padding=0
            )
            self.initial_zoom_set = True  # Mark initial zoom as set

        # Step 6: Always center the view around the image center (for seamless transitions)
        else:
            self.viewbox.setRange(
                xRange=(
                    image_center_x - current_view_range.width() / 2,
                    image_center_x + current_view_range.width() / 2,
                ),
                yRange=(
                    image_center_y - current_view_range.height() / 2,
                    image_center_y + current_view_range.height() / 2,
                ),
                padding=0,
            )

        # Step 7: Ensure the crosshair is updated
        self.setCrosshairPosition()

    def onMouseClicked(self, event) -> None:
        # Map the scene position to view position
        if event.double():  # double click to move
            pos = event.pos()
            scene_pos = self.viewbox.mapSceneToView(pos)

            # Get the x, y coordinates
            x, y = int(scene_pos.x()), int(scene_pos.y())
            # Ensure the coordinates are within the image boundaries
            image_shape = self.img_item.image.shape
            if 0 <= x < image_shape[1] and 0 <= y < image_shape[0]:
                # Adjust the coordinates to be relative to the center of the image
                x_centered = x - image_shape[1] // 2
                y_centered = y - image_shape[0] // 2
                # Emit the signal with the clicked coordinates and image size
                self.signal_calibration_viewer_click.emit(
                    x_centered, y_centered, image_shape[1], image_shape[0]
                )
            else:
                print("click was outside the image bounds.")
        else:
            print("single click only detected")

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() > 0:
            scale_factor = 0.9
        else:
            scale_factor = 1.1

        # Get the center of the viewbox
        center = self.viewbox.viewRect().center()

        # Scale the view
        self.viewbox.scaleBy((scale_factor, scale_factor), center)

        # Update crosshair position after scaling
        self.setCrosshairPosition()

        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setCrosshairPosition()
