"""Tests for Well1536SelectionWidget mouse selection (ports 6eb3427, 4e940f7)."""

import pytest
from unittest.mock import MagicMock, patch
from qtpy.QtCore import QPoint, Qt
from qtpy.QtWidgets import QApplication

from squid.core.events import MoveStageToCommand, SelectedWellsChanged


class MockEventBus:
    """Mock UIEventBus for testing."""

    def __init__(self):
        self.published_events = []
        self._subscriptions = {}

    def subscribe(self, event_type, callback):
        self._subscriptions[event_type] = callback

    def publish(self, event):
        self.published_events.append(event)


@pytest.fixture
def mock_event_bus():
    return MockEventBus()


@pytest.fixture
def widget(mock_event_bus, qtbot):
    """Create a Well1536SelectionWidget with mocked event bus."""
    from squid.ui.widgets.wellplate.well_1536 import Well1536SelectionWidget

    widget = Well1536SelectionWidget(mock_event_bus)
    qtbot.addWidget(widget)
    return widget


class TestCellFromLabelPos:
    """Tests for _cell_from_label_pos helper method."""

    def test_valid_position_top_left(self, widget):
        """Click at top-left corner should return (0, 0)."""
        pos = QPoint(0, 0)
        assert widget._cell_from_label_pos(pos) == (0, 0)

    def test_valid_position_middle(self, widget):
        """Click at middle of grid should return correct cell."""
        # self.a = 11 pixels per cell
        # Cell (5, 10) is at y=5*11=55, x=10*11=110
        pos = QPoint(115, 58)  # Inside cell (5, 10)
        assert widget._cell_from_label_pos(pos) == (5, 10)

    def test_position_out_of_bounds_right(self, widget):
        """Click outside right edge should return None."""
        pos = QPoint(48 * widget.a + 5, 0)  # Beyond column 47
        assert widget._cell_from_label_pos(pos) is None

    def test_position_out_of_bounds_bottom(self, widget):
        """Click outside bottom edge should return None."""
        pos = QPoint(0, 32 * widget.a + 5)  # Beyond row 31
        assert widget._cell_from_label_pos(pos) is None


class TestRowLabel:
    """Tests for _row_label helper method."""

    def test_single_letter_rows(self, widget):
        """Rows 0-25 should return A-Z."""
        assert widget._row_label(0) == "A"
        assert widget._row_label(25) == "Z"

    def test_double_letter_rows(self, widget):
        """Rows 26-31 should return AA-AF."""
        assert widget._row_label(26) == "AA"
        assert widget._row_label(27) == "AB"
        assert widget._row_label(31) == "AF"


class TestCellName:
    """Tests for _cell_name helper method."""

    def test_cell_name_a1(self, widget):
        """Cell (0, 0) should be A1."""
        assert widget._cell_name(0, 0) == "A1"

    def test_cell_name_z48(self, widget):
        """Cell (25, 47) should be Z48."""
        assert widget._cell_name(25, 47) == "Z48"

    def test_cell_name_aa1(self, widget):
        """Cell (26, 0) should be AA1."""
        assert widget._cell_name(26, 0) == "AA1"

    def test_cell_name_af48(self, widget):
        """Cell (31, 47) should be AF48 (bottom right)."""
        assert widget._cell_name(31, 47) == "AF48"


class TestToggleOrReplaceSelection:
    """Tests for _toggle_or_replace_selection helper method."""

    def test_replace_clears_previous_selection(self, widget):
        """Non-additive selection should clear previous."""
        widget.selected_cells = {(0, 0): "#1f77b4", (1, 1): "#1f77b4"}
        widget._toggle_or_replace_selection((5, 5), additive=False)
        assert widget.selected_cells == {(5, 5): "#1f77b4"}

    def test_additive_adds_new_cell(self, widget):
        """Additive selection should add to existing."""
        widget.selected_cells = {(0, 0): "#1f77b4"}
        widget._toggle_or_replace_selection((5, 5), additive=True)
        assert widget.selected_cells == {(0, 0): "#1f77b4", (5, 5): "#1f77b4"}

    def test_additive_removes_existing_cell(self, widget):
        """Additive selection should toggle off existing cell."""
        widget.selected_cells = {(0, 0): "#1f77b4", (5, 5): "#1f77b4"}
        widget._toggle_or_replace_selection((5, 5), additive=True)
        assert widget.selected_cells == {(0, 0): "#1f77b4"}


class TestSetSelectionInputFromSelectedCells:
    """Tests for _set_selection_input_from_selected_cells helper method."""

    def test_empty_selection(self, widget):
        """Empty selection should clear text."""
        widget.selected_cells = {}
        widget._set_selection_input_from_selected_cells()
        assert widget.selection_input.text() == ""

    def test_single_cell(self, widget):
        """Single cell should show as single name."""
        widget.selected_cells = {(0, 0): "#1f77b4"}
        widget._set_selection_input_from_selected_cells()
        assert widget.selection_input.text() == "A1"

    def test_consecutive_cells_in_row(self, widget):
        """Consecutive cells in a row should be compacted to range."""
        widget.selected_cells = {(0, 0): "#1f77b4", (0, 1): "#1f77b4", (0, 2): "#1f77b4"}
        widget._set_selection_input_from_selected_cells()
        assert widget.selection_input.text() == "A1:A3"

    def test_non_consecutive_cells_in_row(self, widget):
        """Non-consecutive cells should be listed separately."""
        widget.selected_cells = {(0, 0): "#1f77b4", (0, 5): "#1f77b4"}
        widget._set_selection_input_from_selected_cells()
        assert widget.selection_input.text() == "A1, A6"


class TestMouseDoubleClick:
    """Tests for double-click navigation behavior."""

    def test_double_click_selects_and_navigates(self, widget, mock_event_bus):
        """Double-click should select cell and trigger navigation."""
        # Create mock event
        mock_event = MagicMock()
        mock_event.button.return_value = Qt.LeftButton
        mock_event.pos.return_value = QPoint(55, 33)  # Cell (3, 5)
        mock_event.modifiers.return_value = Qt.NoModifier
        mock_event.accept = MagicMock()

        widget._on_label_mouse_double_click(mock_event)

        # Should be selected
        assert (3, 5) in widget.selected_cells
        # Should be current
        assert widget.current_cell == (3, 5)
        # Should have triggered navigation (MoveStageToCommand published)
        move_events = [e for e in mock_event_bus.published_events if isinstance(e, MoveStageToCommand)]
        assert len(move_events) == 1


class TestDragSelection:
    """Tests for drag selection behavior."""

    def test_apply_drag_rect_add(self, widget):
        """_apply_drag_rect with mode='add' should add cells."""
        widget.selected_cells = {}
        widget._apply_drag_rect((0, 2, 0, 2), "add")  # 3x3 rectangle
        assert len(widget.selected_cells) == 9
        assert (0, 0) in widget.selected_cells
        assert (2, 2) in widget.selected_cells

    def test_apply_drag_rect_remove(self, widget):
        """_apply_drag_rect with mode='remove' should remove cells."""
        # Pre-populate
        for r in range(5):
            for c in range(5):
                widget.selected_cells[(r, c)] = "#1f77b4"
        assert len(widget.selected_cells) == 25

        widget._apply_drag_rect((0, 2, 0, 2), "remove")  # Remove 3x3 corner
        assert len(widget.selected_cells) == 16
        assert (0, 0) not in widget.selected_cells


class TestAddCurrentWell:
    """Tests for add_current_well button behavior."""

    def test_add_current_well_toggles(self, widget):
        """add_current_well should toggle selection."""
        widget.current_cell = (5, 10)
        widget.selected_cells = {}

        # First call adds
        widget.add_current_well()
        assert (5, 10) in widget.selected_cells

        # Second call removes
        widget.add_current_well()
        assert (5, 10) not in widget.selected_cells


class TestGoToCell:
    """Tests for go_to_cell text input behavior."""

    def test_go_to_cell_navigates(self, widget, mock_event_bus):
        """go_to_cell should navigate to specified cell."""
        widget.cell_input.setText("C5")
        widget.go_to_cell()

        assert widget.current_cell == (2, 4)  # C=row 2, 5=col 4 (0-indexed)

        # Should trigger navigation
        move_events = [e for e in mock_event_bus.published_events if isinstance(e, MoveStageToCommand)]
        assert len(move_events) == 1

    def test_go_to_cell_double_letter_row(self, widget, mock_event_bus):
        """go_to_cell should handle double-letter rows like AA, AB."""
        widget.cell_input.setText("AA1")
        widget.go_to_cell()

        assert widget.current_cell == (26, 0)


class TestSelectCells:
    """Tests for select_cells text input parsing."""

    def test_select_cells_single(self, widget):
        """select_cells should parse single cell."""
        widget.selection_input.setText("B5")
        widget.select_cells()

        assert widget.selected_cells == {(1, 4): "#1f77b4"}

    def test_select_cells_range(self, widget):
        """select_cells should parse cell range."""
        widget.selection_input.setText("A1:A3")
        widget.select_cells()

        assert len(widget.selected_cells) == 3
        assert (0, 0) in widget.selected_cells
        assert (0, 1) in widget.selected_cells
        assert (0, 2) in widget.selected_cells

    def test_select_cells_multiple(self, widget):
        """select_cells should parse comma-separated cells."""
        widget.selection_input.setText("A1, B2, C3")
        widget.select_cells()

        assert len(widget.selected_cells) == 3
        assert (0, 0) in widget.selected_cells
        assert (1, 1) in widget.selected_cells
        assert (2, 2) in widget.selected_cells
