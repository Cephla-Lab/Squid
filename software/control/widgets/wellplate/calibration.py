from control.widgets.wellplate._common import *

if TYPE_CHECKING:
    from control.widgets.display.navigation import NavigationViewer
    from control.core.display import StreamHandler, LiveController
    from control.widgets.wellplate.format import WellplateFormatWidget
    from control.widgets.wellplate.joystick import Joystick


class WellplateCalibration(QDialog):
    def __init__(
        self,
        wellplateFormatWidget: "WellplateFormatWidget",
        stage: AbstractStage,
        navigationViewer: "NavigationViewer",
        streamHandler: "StreamHandler",
        liveController: "LiveController",
        stage_service: Optional["StageService"] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Well Plate Calibration")
        self.wellplateFormatWidget: "WellplateFormatWidget" = wellplateFormatWidget
        self.stage: AbstractStage = stage
        self._stage_service: Optional["StageService"] = stage_service
        self.navigationViewer: "NavigationViewer" = navigationViewer
        self.streamHandler: "StreamHandler" = streamHandler
        self.liveController: "LiveController" = liveController
        self.was_live: bool = self.liveController.is_live
        self.corners: List[Optional[Tuple[float, float]]] = [None, None, None]
        self.show_virtual_joystick: bool = True  # FLAG

        # UI elements
        self.mode_group: QButtonGroup
        self.new_format_radio: QRadioButton
        self.calibrate_format_radio: QRadioButton
        self.existing_format_combo: QComboBox
        self.form_layout: QFormLayout
        self.nameInput: QLineEdit
        self.rowsInput: QSpinBox
        self.colsInput: QSpinBox
        self.plateWidthInput: QDoubleSpinBox
        self.plateHeightInput: QDoubleSpinBox
        self.wellSpacingInput: QDoubleSpinBox
        self.cornerLabels: List[QLabel] = []
        self.setPointButtons: List[QPushButton] = []
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
        left_layout.addWidget(self.existing_format_combo)

        # Connect radio buttons to toggle visibility
        self.new_format_radio.toggled.connect(self.toggle_input_mode)
        self.calibrate_format_radio.toggled.connect(self.toggle_input_mode)

        self.form_layout = QFormLayout()

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
        self.plateWidthInput.setRange(10, 500)  # Adjust range as needed
        self.plateWidthInput.setValue(
            127.76
        )  # Default value for a standard 96-well plate
        self.plateWidthInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Width:", self.plateWidthInput)

        self.plateHeightInput = QDoubleSpinBox(self)
        self.plateHeightInput.setKeyboardTracking(False)
        self.plateHeightInput.setRange(10, 500)  # Adjust range as needed
        self.plateHeightInput.setValue(
            85.48
        )  # Default value for a standard 96-well plate
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

        left_layout.addLayout(self.form_layout)

        points_layout = QGridLayout()
        self.cornerLabels = []
        self.setPointButtons = []
        navigate_label = QLabel(
            "Navigate to and Select\n3 Points on the Edge of Well A1"
        )
        navigate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # navigate_label.setStyleSheet("font-weight: bold;")
        points_layout.addWidget(navigate_label, 0, 0, 1, 2)
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
        left_layout.addLayout(points_layout)

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
            self.liveController.start_live()

        # when the dialog closes i want to # self.liveController.stop_live() if live was stopped before. . . if it was on before, leave it on
        layout.addWidget(self.live_viewer)

        # Right column for joystick and sensitivity controls
        self.right_layout = QVBoxLayout()
        self.right_layout.addStretch(1)

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

        if not self.was_live:
            self.liveController.start_live()

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

    def viewerClicked(self, x, y, width, height):
        pixel_size_um = (
            self.navigationViewer.objectiveStore.get_pixel_size_factor()
            * self.liveController.microscope.camera.get_pixel_size_binned_um()
        )

        pixel_sign_x = 1
        pixel_sign_y = 1 if INVERTED_OBJECTIVE else -1

        delta_x = pixel_sign_x * pixel_size_um * x / 1000.0
        delta_y = pixel_sign_y * pixel_size_um * y / 1000.0

        self._move_stage_relative(delta_x, delta_y)

    def _move_stage_relative(self, dx: float, dy: float) -> None:
        """Move stage by relative distance."""
        if self._stage_service is not None:
            self._stage_service.move_x(dx)
            self._stage_service.move_y(dy)

    def setCorner(self, index):
        if self.corners[index] is None:
            pos = self._stage_service.get_position()
            x = pos.x_mm
            y = pos.y_mm

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
            self.cornerLabels[index].setText(f"Point {index + 1}: ({x:.2f}, {y:.2f})")
            self.setPointButtons[index].setText("Clear Point")
        else:
            self.corners[index] = None
            self.cornerLabels[index].setText(f"Point {index + 1}: Not set")
            self.setPointButtons[index].setText("Set Point")

        self.calibrateButton.setEnabled(
            all(corner is not None for corner in self.corners)
        )

    def populate_existing_formats(self):
        self.existing_format_combo.clear()
        for format_ in WELLPLATE_FORMAT_SETTINGS:
            self.existing_format_combo.addItem(f"{format_} well plate", format_)

    def toggle_input_mode(self):
        if self.new_format_radio.isChecked():
            self.existing_format_combo.hide()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().show()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().show()
        else:
            self.existing_format_combo.show()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().hide()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().hide()

    def calibrate(self):
        try:
            if self.new_format_radio.isChecked():
                if not self.nameInput.text() or not all(self.corners):
                    QMessageBox.warning(
                        self,
                        "Incomplete Information",
                        "Please fill in all fields and set 3 corner points before calibrating.",
                    )
                    return

                name = self.nameInput.text()
                rows = self.rowsInput.value()
                cols = self.colsInput.value()
                well_spacing_mm = self.wellSpacingInput.value()
                plate_width_mm = self.plateWidthInput.value()
                plate_height_mm = self.plateHeightInput.value()

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center
                scale = 1 / 0.084665
                a1_x_pixel = round(a1_x_mm * scale)
                a1_y_pixel = round(a1_y_mm * scale)

                new_format = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "a1_x_pixel": a1_x_pixel,
                    "a1_y_pixel": a1_y_pixel,
                    "well_size_mm": well_size_mm,
                    "well_spacing_mm": well_spacing_mm,
                    "number_of_skip": 0,
                    "rows": rows,
                    "cols": cols,
                }

                self.wellplateFormatWidget.add_custom_format(name, new_format)
                self.wellplateFormatWidget.save_formats_to_csv()
                self.create_wellplate_image(
                    name, new_format, plate_width_mm, plate_height_mm
                )
                self.wellplateFormatWidget.setWellplateSettings(name)
                success_message = (
                    f"New format '{name}' has been successfully created and calibrated."
                )

            else:
                selected_format = self.existing_format_combo.currentData()
                if not all(self.corners):
                    QMessageBox.warning(
                        self,
                        "Incomplete Information",
                        "Please set 3 corner points before calibrating.",
                    )
                    return

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center

                # Get the existing format settings
                existing_settings = WELLPLATE_FORMAT_SETTINGS[selected_format]

                print(f"Updating existing format {selected_format} well plate")
                print(
                    f"OLD: 'a1_x_mm': {existing_settings['a1_x_mm']}, 'a1_y_mm': {existing_settings['a1_y_mm']}, 'well_size_mm': {existing_settings['well_size_mm']}"
                )
                print(
                    f"NEW: 'a1_x_mm': {a1_x_mm}, 'a1_y_mm': {a1_y_mm}, 'well_size_mm': {well_size_mm}"
                )

                updated_settings = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "well_size_mm": well_size_mm,
                }

                WELLPLATE_FORMAT_SETTINGS[selected_format].update(updated_settings)

                self.wellplateFormatWidget.save_formats_to_csv()
                self.wellplateFormatWidget.setWellplateSettings(selected_format)
                success_message = f"Format '{selected_format} well plate' has been successfully recalibrated."

            # Update the WellplateFormatWidget's combo box to reflect the newly calibrated format
            self.wellplateFormatWidget.populate_combo_box()
            index = self.wellplateFormatWidget.comboBox.findData(
                selected_format if self.calibrate_format_radio.isChecked() else name
            )
            if index >= 0:
                self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

            # Display success message
            QMessageBox.information(self, "Calibration Successful", success_message)
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Calibration Error",
                f"An error occurred during calibration: {str(e)}",
            )

    def create_wellplate_image(
        self, name, format_data, plate_width_mm, plate_height_mm
    ):
        scale = 1 / 0.084665

        def mm_to_px(mm):
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
    def calculate_circle(points):
        # Convert points to numpy array
        points = np.array(points)

        # Calculate the center and radius of the circle
        A = np.array([points[1] - points[0], points[2] - points[0]])
        b = np.sum(A * (points[1:3] + points[0]) / 2, axis=1)
        center = np.linalg.solve(A, b)

        # Calculate the radius
        radius = np.mean(np.linalg.norm(points - center, axis=1))

        return center, radius

    def closeEvent(self, event):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().closeEvent(event)

    def accept(self):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().accept()

    def reject(self):
        # This method is called when the dialog is closed without accepting
        if not self.was_live:
            self.liveController.stop_live()
        sample = self.navigationViewer.sample

        # Convert sample string to format int
        if "glass slide" in sample:
            sample_format = "glass slide"
        else:
            try:
                sample_format = int(sample.split()[0])
            except (ValueError, IndexError):
                print(
                    f"Unable to parse sample format from '{sample}'. Defaulting to 0."
                )
                sample_format = "glass slide"

        # Set dropdown to the current sample format
        index = self.wellplateFormatWidget.comboBox.findData(sample_format)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

        # Update wellplate settings
        self.wellplateFormatWidget.setWellplateSettings(sample_format)

        super().reject()


class CalibrationLiveViewer(QWidget):
    signal_calibration_viewer_click = Signal(int, int, int, int)
    signal_mouse_moved = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.initial_zoom_set = False
        self.initUI()

    def initUI(self):
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

    def setCrosshairPosition(self):
        center = self.viewbox.viewRect().center()
        self.crosshair_h.setPos(center.y())
        self.crosshair_v.setPos(center.x())

    def display_image(self, image):
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

    # def mouseMoveEvent(self, event):
    #     self.signal_mouse_moved.emit(event.x(), event.y())

    def onMouseClicked(self, event):
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

    def wheelEvent(self, event):
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setCrosshairPosition()
