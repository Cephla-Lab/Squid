from squid.ui.widgets.wellplate._common import *
from _def import WELLPLATE_OFFSET_X_mm, WELLPLATE_OFFSET_Y_mm


class Well1536SelectionWidget(QWidget):
    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.format = "1536 well plate"
        self.selected_cells = {}  # Dictionary to keep track of selected cells and their colors
        self.current_cell = None  # To track the current (green) cell
        self.rows = 32
        self.columns = 48
        self.spacing_mm = 2.25
        self.number_of_skip = 0
        self.well_size_mm = 1.5
        self.a1_x_mm = 11.0  # measured stage position - to update
        self.a1_y_mm = 7.86  # measured stage position - to update
        self.a1_x_pixel = 144  # coordinate on the png - to update
        self.a1_y_pixel = 108  # coordinate on the png - to update
        self.initUI()

    def initUI(self):
        self.setWindowTitle("1536 Well Plate")
        self.setGeometry(100, 100, 750, 400)  # Increased width to accommodate controls

        self.a = 11
        image_width = 48 * self.a
        image_height = 32 * self.a

        self.image = QPixmap(image_width, image_height)
        self.image.fill(QColor("white"))
        self.label = QLabel()
        self.label.setPixmap(self.image)
        self.label.setFixedSize(image_width, image_height)
        self.label.setAlignment(Qt.AlignCenter)

        self.cell_input = QLineEdit(self)
        self.cell_input.setPlaceholderText("e.g. AE12 or B4")
        go_button = QPushButton("Go to well", self)
        go_button.clicked.connect(self.go_to_cell)
        self.selection_input = QLineEdit(self)
        self.selection_input.setPlaceholderText("e.g. A1:E48, X1, AC24, Z2:AF6, ...")
        self.selection_input.editingFinished.connect(self.select_cells)

        # Create navigation buttons
        up_button = QPushButton("↑", self)
        left_button = QPushButton("←", self)
        right_button = QPushButton("→", self)
        down_button = QPushButton("↓", self)
        add_button = QPushButton("Select", self)

        # Connect navigation buttons to their respective functions
        up_button.clicked.connect(self.move_up)
        left_button.clicked.connect(self.move_left)
        right_button.clicked.connect(self.move_right)
        down_button.clicked.connect(self.move_down)
        add_button.clicked.connect(self.add_current_well)

        layout = QHBoxLayout()
        layout.addWidget(self.label)

        layout_controls = QVBoxLayout()
        layout_controls.addStretch(2)

        # Add navigation buttons in a + sign layout
        layout_move = QGridLayout()
        layout_move.addWidget(up_button, 0, 2)
        layout_move.addWidget(left_button, 1, 1)
        layout_move.addWidget(add_button, 1, 2)
        layout_move.addWidget(right_button, 1, 3)
        layout_move.addWidget(down_button, 2, 2)
        layout_move.setColumnStretch(0, 1)
        layout_move.setColumnStretch(4, 1)
        layout_controls.addLayout(layout_move)

        layout_controls.addStretch(1)

        layout_input = QGridLayout()
        layout_input.addWidget(QLabel("Well Navigation"), 0, 0)
        layout_input.addWidget(self.cell_input, 0, 1)
        layout_input.addWidget(go_button, 0, 2)
        layout_input.addWidget(QLabel("Well Selection"), 1, 0)
        layout_input.addWidget(self.selection_input, 1, 1, 1, 2)
        layout_controls.addLayout(layout_input)

        control_widget = QWidget()
        control_widget.setLayout(layout_controls)
        control_widget.setFixedHeight(
            image_height
        )  # Set the height of controls to match the image

        layout.addWidget(control_widget)
        self.setLayout(layout)

    def move_up(self):
        if self.current_cell:
            row, col = self.current_cell
            if row > 0:
                self.current_cell = (row - 1, col)
                self.update_current_cell()

    def move_left(self):
        if self.current_cell:
            row, col = self.current_cell
            if col > 0:
                self.current_cell = (row, col - 1)
                self.update_current_cell()

    def move_right(self):
        if self.current_cell:
            row, col = self.current_cell
            if col < self.columns - 1:
                self.current_cell = (row, col + 1)
                self.update_current_cell()

    def move_down(self):
        if self.current_cell:
            row, col = self.current_cell
            if row < self.rows - 1:
                self.current_cell = (row + 1, col)
                self.update_current_cell()

    def add_current_well(self):
        if self.current_cell:
            row, col = self.current_cell
            cell_name = f"{chr(65 + row)}{col + 1}"

            if (row, col) in self.selected_cells:
                # If the well is already selected, remove it
                del self.selected_cells[(row, col)]
                self.remove_well_from_selection_input(cell_name)
                print(f"Removed well {cell_name}")
            else:
                # If the well is not selected, add it
                self.selected_cells[(row, col)] = (
                    "#1f77b4"  # Add to selected cells with blue color
                )
                self.add_well_to_selection_input(cell_name)
                print(f"Added well {cell_name}")

            self.redraw_wells()
            self.signal_wellSelected.emit(bool(self.selected_cells))

    def add_well_to_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        if current_selection:
            self.selection_input.setText(f"{current_selection}, {cell_name}")
        else:
            self.selection_input.setText(cell_name)

    def remove_well_from_selection_input(self, cell_name):
        current_selection = self.selection_input.text()
        cells = [cell.strip() for cell in current_selection.split(",")]
        if cell_name in cells:
            cells.remove(cell_name)
            self.selection_input.setText(", ".join(cells))

    def update_current_cell(self):
        self.redraw_wells()
        row, col = self.current_cell
        if row < 26:
            row_label = chr(65 + row)
        else:
            row_label = chr(64 + (row // 26)) + chr(65 + (row % 26))
        # Update cell_input with the correct label (e.g., A1, B2, AA1, etc.)
        self.cell_input.setText(f"{row_label}{col + 1}")

        x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
        y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
        self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def redraw_wells(self):
        self.image.fill(QColor("white"))  # Clear the pixmap first
        painter = QPainter(self.image)
        painter.setPen(QColor("white"))
        # Draw selected cells in red
        for (row, col), color in self.selected_cells.items():
            painter.setBrush(QColor(color))
            painter.drawRect(col * self.a, row * self.a, self.a, self.a)
        # Draw current cell in green
        if self.current_cell:
            painter.setBrush(Qt.NoBrush)  # No fill
            painter.setPen(QPen(QColor("red"), 2))  # Red outline, 2 pixels wide
            row, col = self.current_cell
            painter.drawRect(col * self.a + 2, row * self.a + 2, self.a - 3, self.a - 3)
        painter.end()
        self.label.setPixmap(self.image)

    def go_to_cell(self):
        cell_desc = self.cell_input.text().strip()
        match = re.match(r"([A-Za-z]+)(\d+)", cell_desc)
        if match:
            row_part, col_part = match.groups()
            row_index = self.row_to_index(row_part)
            col_index = int(col_part) - 1
            self.current_cell = (row_index, col_index)  # Update the current cell
            self.redraw_wells()  # Redraw with the new current cell
            x_mm = col_index * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row_index * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def select_cells(self):
        # first clear selection
        self.selected_cells = {}

        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        cell_descriptions = self.selection_input.text().split(",")
        for desc in cell_descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = self.row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = self.row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(
                        min(start_row_index, end_row_index),
                        max(start_row_index, end_row_index) + 1,
                    ):
                        for col in range(
                            min(start_col_index, end_col_index),
                            max(start_col_index, end_col_index) + 1,
                        ):
                            self.selected_cells[(row, col)] = "#1f77b4"
                else:  # It's a single cell
                    self.selected_cells[(start_row_index, start_col_index)] = "#1f77b4"
        self.redraw_wells()
        if self.selected_cells:
            self.signal_wellSelected.emit(True)

    def row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def onSelectionChanged(self):
        self.get_selected_cells()

    def onWellplateChanged(self):
        """A placeholder to match the method in WellSelectionWidget"""
        pass

    def get_selected_cells(self):
        list_of_selected_cells = list(self.selected_cells.keys())
        return list_of_selected_cells
