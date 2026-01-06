import re

from squid.ui.widgets.wellplate._common import *
from qtpy.QtCore import QPoint, QTimer
from qtpy.QtGui import QPixmap, QPainter, QPen
from qtpy.QtWidgets import QApplication
from _def import WELLPLATE_OFFSET_X_mm, WELLPLATE_OFFSET_Y_mm

from squid.core.events import (
    ClickToMoveEnabledChanged,
    MoveStageToCommand,
    SelectedWellsChanged,
    WellplateFormatChanged,
)

from typing import Optional, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus


class Well1536SelectionWidget(QWidget):
    def __init__(self, event_bus: "UIEventBus"):
        super().__init__()
        self._bus = event_bus
        self._click_to_move_enabled: bool = True
        self.format = "1536 well plate"
        self.selected_cells = {}  # Dictionary to keep track of selected cells and their colors
        self.current_cell = None  # To track the current (green) cell

        # defaults
        self.rows = 32
        self.columns = 48
        self.spacing_mm = 2.25
        self.number_of_skip = 0
        self.well_size_mm = 1.5
        self.a1_x_mm = 11.0  # measured stage position - to update
        self.a1_y_mm = 7.86  # measured stage position - to update
        self.a1_x_pixel = 144  # coordinate on the png - to update
        self.a1_y_pixel = 108  # coordinate on the png - to update

        self._bus.subscribe(ClickToMoveEnabledChanged, self._on_click_to_move_enabled_changed)
        self._bus.subscribe(WellplateFormatChanged, self._on_wellplate_format_changed)
        self.initUI()

    def _on_click_to_move_enabled_changed(self, event: ClickToMoveEnabledChanged) -> None:
        self._click_to_move_enabled = event.enabled

    def _publish_selection(self) -> None:
        self._bus.publish(
            SelectedWellsChanged(
                format_name=self.format,
                selected_cells=tuple(self.get_selected_cells()),
            )
        )

    def _on_wellplate_format_changed(self, event: WellplateFormatChanged) -> None:
        if event.format_name != "1536 well plate":
            return
        self.spacing_mm = float(event.well_spacing_mm)
        self.number_of_skip = int(event.number_of_skip)
        self.well_size_mm = float(event.well_size_mm)
        self.a1_x_mm = float(event.a1_x_mm)
        self.a1_y_mm = float(event.a1_y_mm)
        self.a1_x_pixel = int(event.a1_x_pixel)
        self.a1_y_pixel = int(event.a1_y_pixel)

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

        # Mouse interaction is handled on the widget that *displays* the pixmap (QLabel),
        # not on the QPixmap itself. We delay the single-click handler so that it can be
        # cancelled when a double-click arrives.
        self._pending_click_cell: Optional[Tuple[int, int]] = None
        self._pending_click_modifiers = Qt.NoModifier
        self._click_token = 0
        self._press_pos: Optional[QPoint] = None
        self._press_button = None
        self._press_modifiers = Qt.NoModifier
        self._is_dragging = False
        self._drag_start_cell: Optional[Tuple[int, int]] = None
        self._last_drag_rect: Optional[Tuple[int, int, int, int]] = None  # (r0, r1, c0, c1)
        self._drag_mode: Optional[str] = None  # "replace" | "add" | "remove"
        app = QApplication.instance()
        self._double_click_ms = app.doubleClickInterval() if app is not None else 250
        self.label.mousePressEvent = self._on_label_mouse_press
        self.label.mouseDoubleClickEvent = self._on_label_mouse_double_click
        self.label.mouseMoveEvent = self._on_label_mouse_move
        self.label.mouseReleaseEvent = self._on_label_mouse_release

        self.cell_input = QLineEdit(self)
        self.cell_input.setPlaceholderText("e.g. AE12 or B4")
        go_button = QPushButton("Go to well", self)
        go_button.clicked.connect(self.go_to_cell)
        self.selection_input = QLineEdit(self)
        self.selection_input.setPlaceholderText("e.g. A1:E48, X1, AC24, Z2:AF6, ...")
        self.selection_input.editingFinished.connect(self.select_cells)
        self.selection_input.returnPressed.connect(self.select_cells)

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

    # -------------------------------------------------------------------------
    # Mouse interaction helper methods
    # -------------------------------------------------------------------------

    def _cell_from_label_pos(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        """Map a click position in label pixel coords -> (row, col) or None."""
        col = int(pos.x() // self.a)
        row = int(pos.y() // self.a)
        if 0 <= row < self.rows and 0 <= col < self.columns:
            return (row, col)
        return None

    def _row_label(self, row: int) -> str:
        """Row index to letter (A..Z, AA..AF for 32 rows)."""
        if row < 26:
            return chr(65 + row)
        return chr(64 + (row // 26)) + chr(65 + (row % 26))

    def _cell_name(self, row: int, col: int) -> str:
        return f"{self._row_label(row)}{col + 1}"

    def _emit_selection_changed(self) -> None:
        """Refresh UI elements that depend on selected_cells and notify listeners."""
        self.redraw_wells()
        self._set_selection_input_from_selected_cells()
        self._publish_selection()

    def _toggle_or_replace_selection(self, cell: Tuple[int, int], *, additive: bool) -> None:
        """
        Selection semantics to match the table-based well selector:
        - additive=False: replace selection with only this cell
        - additive=True: toggle this cell without clearing others
        """
        if additive:
            if cell in self.selected_cells:
                self.selected_cells.pop(cell, None)
            else:
                self.selected_cells[cell] = "#1f77b4"
        else:
            self.selected_cells = {cell: "#1f77b4"}

    def _set_selection_input_from_selected_cells(self) -> None:
        """Render current selection into the textbox, compacted into per-row ranges."""
        if not self.selected_cells:
            self.selection_input.setText("")
            return

        rows_to_cols: dict = {}
        for r, c in self.selected_cells.keys():
            rows_to_cols.setdefault(r, []).append(c)

        parts = []
        for r in sorted(rows_to_cols.keys()):
            cols = sorted(set(rows_to_cols[r]))
            start = prev = cols[0]
            for c in cols[1:]:
                if c == prev + 1:
                    prev = c
                    continue
                # flush run
                if start == prev:
                    parts.append(f"{self._row_label(r)}{start + 1}")
                else:
                    parts.append(f"{self._row_label(r)}{start + 1}:{self._row_label(r)}{prev + 1}")
                start = prev = c
            # flush last run
            if start == prev:
                parts.append(f"{self._row_label(r)}{start + 1}")
            else:
                parts.append(f"{self._row_label(r)}{start + 1}:{self._row_label(r)}{prev + 1}")

        self.selection_input.setText(", ".join(parts))

    def _commit_single_click(self, token: int) -> None:
        """Delayed single-click handler. Cancelled if a double-click arrives."""
        # If a double-click happened, the token will have changed -> ignore.
        if token != self._click_token:
            return
        if self._is_dragging:
            return
        cell = self._pending_click_cell
        mods = self._pending_click_modifiers
        self._pending_click_cell = None
        self._pending_click_modifiers = Qt.NoModifier
        if cell is None:
            return

        self.current_cell = cell
        self._toggle_or_replace_selection(cell, additive=bool(mods & Qt.ShiftModifier))

        # Update UI without navigating (no stage move here).
        row, col = cell
        self.cell_input.setText(self._cell_name(row, col))
        self._emit_selection_changed()

    def _on_label_mouse_press(self, event) -> None:
        if event.button() not in (Qt.LeftButton, Qt.RightButton):
            return

        cell = self._cell_from_label_pos(event.pos())
        if cell is None:
            return

        self._press_pos = QPoint(event.pos())
        self._press_button = event.button()
        self._press_modifiers = event.modifiers()
        self._is_dragging = False
        self._drag_start_cell = cell
        self._last_drag_rect = None
        self._drag_mode = None

        # Delay single-click action so we can cancel it if a double-click arrives.
        if event.button() == Qt.LeftButton:
            self._pending_click_cell = cell
            self._pending_click_modifiers = event.modifiers()
            self._click_token += 1
            token = self._click_token
            QTimer.singleShot(self._double_click_ms, lambda: self._commit_single_click(token))
        event.accept()

    def _apply_drag_rect(self, rect: Tuple[int, int, int, int], mode: str) -> None:
        r0, r1, c0, c1 = rect
        if mode == "add":
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    self.selected_cells[(r, c)] = "#1f77b4"
        elif mode == "remove":
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    self.selected_cells.pop((r, c), None)

    def _on_label_mouse_move(self, event) -> None:
        if self._press_pos is None or self._drag_start_cell is None:
            return

        # Start drag if we moved far enough.
        if not self._is_dragging:
            threshold = QApplication.startDragDistance()
            if (event.pos() - self._press_pos).manhattanLength() < threshold:
                return

            # Cancel any pending single-click action.
            self._click_token += 1
            self._pending_click_cell = None
            self._pending_click_modifiers = Qt.NoModifier
            self._is_dragging = True

            # Determine drag mode:
            # - Left-drag: replace selection (unless Shift is held, then add)
            # - Right-drag: remove
            if self._press_button == Qt.RightButton:
                self._drag_mode = "remove"
            elif self._press_modifiers & Qt.ShiftModifier:
                self._drag_mode = "add"
            else:
                self._drag_mode = "replace"

        current_cell = self._cell_from_label_pos(event.pos())
        if current_cell is None:
            return

        r0 = min(self._drag_start_cell[0], current_cell[0])
        r1 = max(self._drag_start_cell[0], current_cell[0])
        c0 = min(self._drag_start_cell[1], current_cell[1])
        c1 = max(self._drag_start_cell[1], current_cell[1])
        rect = (r0, r1, c0, c1)
        if rect == self._last_drag_rect:
            return
        self._last_drag_rect = rect

        if self._drag_mode == "replace":
            self.selected_cells = {}
            self._apply_drag_rect(rect, "add")
        else:
            # add/remove
            self._apply_drag_rect(rect, self._drag_mode)
        self.current_cell = current_cell  # keep outline tracking cursor
        self.redraw_wells()
        event.accept()

    def _on_label_mouse_release(self, event) -> None:
        if self._press_pos is None:
            return

        if self._is_dragging:
            # Finalize drag selection: sync textbox + notify listeners.
            self._set_selection_input_from_selected_cells()
            self._publish_selection()

        self._press_pos = None
        self._press_button = None
        self._press_modifiers = Qt.NoModifier
        self._is_dragging = False
        self._drag_start_cell = None
        self._last_drag_rect = None
        self._drag_mode = None
        event.accept()

    def _on_label_mouse_double_click(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return

        cell = self._cell_from_label_pos(event.pos())
        if cell is None:
            return

        # Cancel any pending single-click action.
        self._click_token += 1
        self._pending_click_cell = None
        self._is_dragging = False

        # Double-click navigates to the cell AND selects it.
        self._toggle_or_replace_selection(cell, additive=bool(event.modifiers() & Qt.ShiftModifier))
        self._set_selection_input_from_selected_cells()
        self._publish_selection()

        # Navigate to the cell (emits MoveStageToCommand via update_current_cell).
        self.current_cell = cell
        self.update_current_cell()
        event.accept()

    # -------------------------------------------------------------------------
    # Navigation methods
    # -------------------------------------------------------------------------

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
            cell = (row, col)
            cell_name = self._cell_name(row, col)
            if cell in self.selected_cells:
                self.selected_cells.pop(cell, None)
                print(f"Removed well {cell_name}")
            else:
                self.selected_cells[cell] = "#1f77b4"
                print(f"Added well {cell_name}")
            # Redraw only (do not navigate on select/toggle).
            self._emit_selection_changed()

    def update_current_cell(self):
        self.redraw_wells()
        row, col = self.current_cell
        # Update cell_input with the correct label (e.g., A1, B2, AA1, etc.)
        self.cell_input.setText(self._cell_name(row, col))

        x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
        y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
        if self._click_to_move_enabled:
            self._bus.publish(MoveStageToCommand(x_mm=x_mm, y_mm=y_mm))

    def redraw_wells(self):
        self.image.fill(QColor("white"))  # Clear the pixmap first
        painter = QPainter(self.image)
        painter.setPen(QColor("white"))
        # Draw selected cells (blue)
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
            self.update_current_cell()

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
        self._publish_selection()

    def row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def onSelectionChanged(self):
        self._publish_selection()

    def get_selected_cells(self):
        list_of_selected_cells = list(self.selected_cells.keys())
        return list_of_selected_cells
