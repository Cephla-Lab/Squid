from control.widgets.wellplate._common import *
from control._def import WELLPLATE_OFFSET_X_mm, WELLPLATE_OFFSET_Y_mm

if TYPE_CHECKING:
    from control.widgets.wellplate.format import WellplateFormatWidget


class WellSelectionWidget(QTableWidget):
    signal_wellSelected: Signal = Signal(bool)
    signal_wellSelectedPos: Signal = Signal(float, float)

    def __init__(
        self,
        format_: str,
        wellplateFormatWidget: "WellplateFormatWidget",
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super(WellSelectionWidget, self).__init__(*args, **kwargs)
        self.wellplateFormatWidget: "WellplateFormatWidget" = wellplateFormatWidget
        self.cellDoubleClicked.connect(self.onDoubleClick)
        self.itemSelectionChanged.connect(self.onSelectionChanged)
        self.fixed_height: int = 400
        self.format: str = ""
        self.rows: int = 0
        self.columns: int = 0
        self.spacing_mm: float = 0.0
        self.number_of_skip: int = 0
        self.a1_x_mm: float = 0.0
        self.a1_y_mm: float = 0.0
        self.a1_x_pixel: int = 0
        self.a1_y_pixel: int = 0
        self.well_size_mm: float = 0.0
        self.setFormat(format_)

    def setFormat(self, format_: str) -> None:
        self.format = format_
        settings = self.wellplateFormatWidget.getWellplateSettings(self.format)
        if settings is None:
            return
        self.rows = int(settings["rows"])
        self.columns = int(settings["cols"])
        self.spacing_mm = float(settings["well_spacing_mm"])
        self.number_of_skip = int(settings["number_of_skip"])
        self.a1_x_mm = float(settings["a1_x_mm"])
        self.a1_y_mm = float(settings["a1_y_mm"])
        self.a1_x_pixel = int(settings["a1_x_pixel"])
        self.a1_y_pixel = int(settings["a1_y_pixel"])
        self.well_size_mm = float(settings["well_size_mm"])

        self.setRowCount(self.rows)
        self.setColumnCount(self.columns)
        self.initUI()
        self.setData()

    def initUI(self) -> None:
        # Disable editing, scrollbars, and other interactions
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v_scroll = self.verticalScrollBar()
        h_scroll = self.horizontalScrollBar()
        if v_scroll is not None:
            v_scroll.setDisabled(True)
        if h_scroll is not None:
            h_scroll.setDisabled(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        h_header = self.horizontalHeader()
        v_header = self.verticalHeader()
        if h_header is not None:
            h_header.setFont(font)
        if v_header is not None:
            v_header.setFont(font)

        self._setLayout()

    def _setLayout(self) -> None:
        # Calculate available space and cell size
        h_header = self.horizontalHeader()
        v_header = self.verticalHeader()
        header_height = h_header.height() if h_header is not None else 0
        available_height = (
            self.fixed_height - header_height
        )  # Fixed height of 408 pixels

        # Calculate cell size based on the minimum of available height and width
        cell_size = available_height // self.rowCount()

        if v_header is not None:
            v_header.setSectionResizeMode(QHeaderView.Fixed)
            v_header.setDefaultSectionSize(cell_size)
            v_header.setMinimumSectionSize(cell_size)
            v_header.setMaximumSectionSize(cell_size)
        if h_header is not None:
            h_header.setSectionResizeMode(QHeaderView.Fixed)
            h_header.setDefaultSectionSize(cell_size)
            h_header.setMinimumSectionSize(cell_size)
            h_header.setMaximumSectionSize(cell_size)

        row_header_width = v_header.width() if v_header is not None else 0

        # Calculate total width and height
        total_height = (self.rowCount() * cell_size) + header_height
        total_width = (self.columnCount() * cell_size) + row_header_width

        # Set the widget's fixed size
        self.setFixedHeight(total_height)
        self.setFixedWidth(total_width)

        # Force the widget to update its layout
        self.updateGeometry()
        viewport = self.viewport()
        if viewport is not None:
            viewport.update()

    def onWellplateChanged(self) -> None:
        self.setFormat(self.wellplateFormatWidget.wellplate_format)

    def setData(self) -> None:
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                item = self.item(i, j)
                if not item:  # Create a new item if none exists
                    item = QTableWidgetItem()
                    self.setItem(i, j, item)
                # Reset to selectable by default
                item.setFlags(
                    Qt.ItemFlag(
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    )
                )

        if self.number_of_skip > 0 and self.format != 0:
            for i in range(self.number_of_skip):
                for j in range(self.columns):  # Apply to rows
                    item_ij = self.item(i, j)
                    if item_ij is not None:
                        item_ij.setFlags(
                            Qt.ItemFlag(item_ij.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                        )
                    item_bot = self.item(self.rows - 1 - i, j)
                    if item_bot is not None:
                        item_bot.setFlags(
                            Qt.ItemFlag(
                                item_bot.flags() & ~Qt.ItemFlag.ItemIsSelectable
                            )
                        )
                for k in range(self.rows):  # Apply to columns
                    item_ki = self.item(k, i)
                    if item_ki is not None:
                        item_ki.setFlags(
                            Qt.ItemFlag(item_ki.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                        )
                    item_right = self.item(k, self.columns - 1 - i)
                    if item_right is not None:
                        item_right.setFlags(
                            Qt.ItemFlag(
                                item_right.flags() & ~Qt.ItemFlag.ItemIsSelectable
                            )
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
        v_header = self.verticalHeader()
        if v_header is not None:
            v_header.setSectionResizeMode(QHeaderView.ResizeToContents)

    def onDoubleClick(self, row: int, col: int) -> None:
        print("double click well", row, col)
        if (
            row >= 0 + self.number_of_skip
            and row <= self.rows - 1 - self.number_of_skip
        ) and (
            col >= 0 + self.number_of_skip
            and col <= self.columns - 1 - self.number_of_skip
        ):
            x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)
            print("well location:", (x_mm, y_mm))
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSingleClick(self, row: int, col: int) -> None:
        print("single click well", row, col)
        if (
            row >= 0 + self.number_of_skip
            and row <= self.rows - 1 - self.number_of_skip
        ) and (
            col >= 0 + self.number_of_skip
            and col <= self.columns - 1 - self.number_of_skip
        ):
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSelectionChanged(self) -> None:
        # Check if there are any selected indexes before proceeding
        if self.format != "glass slide":
            has_selection = bool(self.selectedIndexes())
            self.signal_wellSelected.emit(has_selection)

    def get_selected_cells(self) -> List[Tuple[int, int]]:
        list_of_selected_cells: List[Tuple[int, int]] = []
        print("getting selected cells...")
        if self.format == "glass slide":
            return list_of_selected_cells
        for index in self.selectedIndexes():
            row, col = index.row(), index.column()
            # Check if the cell is within the allowed bounds
            if (
                row >= 0 + self.number_of_skip
                and row <= self.rows - 1 - self.number_of_skip
            ) and (
                col >= 0 + self.number_of_skip
                and col <= self.columns - 1 - self.number_of_skip
            ):
                list_of_selected_cells.append((row, col))
        if list_of_selected_cells:
            print("cells:", list_of_selected_cells)
        else:
            print("no cells")
        return list_of_selected_cells

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        self.initUI()
        super().resizeEvent(event)

    def wheelEvent(self, event: Optional[QWheelEvent]) -> None:
        # Ignore wheel events to prevent scrolling
        if event is not None:
            event.ignore()

    def scrollTo(
        self,
        index: QModelIndex,
        hint: QAbstractItemView.ScrollHint = QAbstractItemView.EnsureVisible,
    ) -> None:
        pass

    def set_white_boundaries_style(self) -> None:
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
