from control.widgets.wellplate._common import *

class WellSelectionWidget(QTableWidget):
    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self, format_, wellplateFormatWidget, *args, **kwargs):
        super(WellSelectionWidget, self).__init__(*args, **kwargs)
        self.wellplateFormatWidget = wellplateFormatWidget
        self.cellDoubleClicked.connect(self.onDoubleClick)
        self.itemSelectionChanged.connect(self.onSelectionChanged)
        self.fixed_height = 400
        self.setFormat(format_)

    def setFormat(self, format_):
        self.format = format_
        settings = self.wellplateFormatWidget.getWellplateSettings(self.format)
        self.rows = settings["rows"]
        self.columns = settings["cols"]
        self.spacing_mm = settings["well_spacing_mm"]
        self.number_of_skip = settings["number_of_skip"]
        self.a1_x_mm = settings["a1_x_mm"]
        self.a1_y_mm = settings["a1_y_mm"]
        self.a1_x_pixel = settings["a1_x_pixel"]
        self.a1_y_pixel = settings["a1_y_pixel"]
        self.well_size_mm = settings["well_size_mm"]

        self.setRowCount(self.rows)
        self.setColumnCount(self.columns)
        self.initUI()
        self.setData()

    def initUI(self):
        # Disable editing, scrollbars, and other interactions
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setDisabled(True)
        self.horizontalScrollBar().setDisabled(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTabKeyNavigation(False)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropOverwriteMode(False)
        self.setMouseTracking(False)

        if self.format == "1536 well plate":
            font = QFont()
            font.setPointSize(6)  # You can adjust this value as needed
        else:
            font = QFont()
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)

        self.setLayout()

    def setLayout(self):
        # Calculate available space and cell size
        header_height = self.horizontalHeader().height()
        available_height = self.fixed_height - header_height  # Fixed height of 408 pixels

        # Calculate cell size based on the minimum of available height and width
        cell_size = available_height // self.rowCount()

        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(cell_size)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setDefaultSectionSize(cell_size)

        # Ensure sections do not resize
        self.verticalHeader().setMinimumSectionSize(cell_size)
        self.verticalHeader().setMaximumSectionSize(cell_size)
        self.horizontalHeader().setMinimumSectionSize(cell_size)
        self.horizontalHeader().setMaximumSectionSize(cell_size)

        row_header_width = self.verticalHeader().width()

        # Calculate total width and height
        total_height = (self.rowCount() * cell_size) + header_height
        total_width = (self.columnCount() * cell_size) + row_header_width

        # Set the widget's fixed size
        self.setFixedHeight(total_height)
        self.setFixedWidth(total_width)

        # Force the widget to update its layout
        self.updateGeometry()
        self.viewport().update()

    def onWellplateChanged(self):
        self.setFormat(self.wellplateFormatWidget.wellplate_format)

    def setData(self):
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                item = self.item(i, j)
                if not item:  # Create a new item if none exists
                    item = QTableWidgetItem()
                    self.setItem(i, j, item)
                # Reset to selectable by default
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        if self.number_of_skip > 0 and self.format != 0:
            for i in range(self.number_of_skip):
                for j in range(self.columns):  # Apply to rows
                    self.item(i, j).setFlags(self.item(i, j).flags() & ~Qt.ItemIsSelectable)
                    self.item(self.rows - 1 - i, j).setFlags(
                        self.item(self.rows - 1 - i, j).flags() & ~Qt.ItemIsSelectable
                    )
                for k in range(self.rows):  # Apply to columns
                    self.item(k, i).setFlags(self.item(k, i).flags() & ~Qt.ItemIsSelectable)
                    self.item(k, self.columns - 1 - i).setFlags(
                        self.item(k, self.columns - 1 - i).flags() & ~Qt.ItemIsSelectable
                    )

        # Update row headers
        row_headers = []
        for i in range(self.rows):
            if i < 26:
                label = chr(ord("A") + i)
            else:
                first_letter = chr(ord("A") + (i // 26) - 1)
                second_letter = chr(ord("A") + (i % 26))
                label = first_letter + second_letter
            row_headers.append(label)
        self.setVerticalHeaderLabels(row_headers)

        # Adjust vertical header width after setting labels
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def onDoubleClick(self, row, col):
        print("double click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)
            print("well location:", (x_mm, y_mm))
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSingleClick(self, row, col):
        print("single click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSelectionChanged(self):
        # Check if there are any selected indexes before proceeding
        if self.format != "glass slide":
            has_selection = bool(self.selectedIndexes())
            self.signal_wellSelected.emit(has_selection)

    def get_selected_cells(self):
        list_of_selected_cells = []
        print("getting selected cells...")
        if self.format == "glass slide":
            return list_of_selected_cells
        for index in self.selectedIndexes():
            row, col = index.row(), index.column()
            # Check if the cell is within the allowed bounds
            if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
                col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
            ):
                list_of_selected_cells.append((row, col))
        if list_of_selected_cells:
            print("cells:", list_of_selected_cells)
        else:
            print("no cells")
        return list_of_selected_cells

    def resizeEvent(self, event):
        self.initUI()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        # Ignore wheel events to prevent scrolling
        event.ignore()

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        pass

    def set_white_boundaries_style(self):
        style = """
        QTableWidget {
            gridline-color: white;
            border: 1px solid white;
        }
        QHeaderView::section {
            color: white;
        }
        """
        self.setStyleSheet(style)


