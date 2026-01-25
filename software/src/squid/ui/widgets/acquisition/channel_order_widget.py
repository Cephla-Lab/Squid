"""Channel selection and ordering widget with drag-and-drop support.

This widget allows users to:
1. Select which channels to image (via checkboxes)
2. Reorder channels via drag-and-drop to set imaging order
3. View the current imaging order at a glance

The imaging order matters for multi-channel acquisitions, particularly when
optimizing for speed or minimizing photobleaching.
"""

from typing import List, Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QFrame,
)


class ChannelOrderWidget(QWidget):
    """Widget for selecting and ordering imaging channels.

    Provides a drag-and-drop list where users can:
    - Check/uncheck channels to select them for imaging
    - Drag channels to reorder them (determines imaging order)
    - See the current imaging order displayed below the list

    Signals:
        selection_changed: Emitted when channels are selected/deselected or reordered.
            The signal carries the list of selected channel names in order.
    """

    selection_changed = Signal(list)  # List[str] of selected channel names in order

    def __init__(
        self,
        initial_channels: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ):
        """Initialize the channel order widget.

        Args:
            initial_channels: List of available channel names to display.
            parent: Parent widget.
        """
        super().__init__(parent)
        self._channels: List[str] = list(initial_channels or [])

        self._setup_ui()
        self._populate_channels()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Create and configure UI elements."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Channel list with drag-drop and checkboxes
        self.list_channels = QListWidget()
        self.list_channels.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_channels.setDefaultDropAction(Qt.MoveAction)
        self.list_channels.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_channels.setMinimumHeight(80)

        # Imaging order display
        self.order_frame = QFrame()
        order_layout = QHBoxLayout(self.order_frame)
        order_layout.setContentsMargins(2, 2, 2, 2)
        order_layout.setSpacing(4)

        self.order_label_prefix = QLabel("Order:")
        self.order_label_prefix.setStyleSheet("font-weight: bold; font-size: 10px;")
        self.order_label = QLabel("")
        self.order_label.setStyleSheet("font-size: 10px; color: #666;")
        self.order_label.setWordWrap(True)

        order_layout.addWidget(self.order_label_prefix)
        order_layout.addWidget(self.order_label, 1)

        layout.addWidget(self.list_channels)
        layout.addWidget(self.order_frame)

    def _populate_channels(self) -> None:
        """Populate the list with available channels."""
        self.list_channels.clear()
        for channel_name in self._channels:
            item = QListWidgetItem(channel_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Unchecked)
            self.list_channels.addItem(item)
        self._update_order_display()

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self.list_channels.itemChanged.connect(self._on_item_changed)
        self.list_channels.model().rowsMoved.connect(self._on_rows_moved)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Handle checkbox state changes."""
        self._update_order_display()
        self.selection_changed.emit(self.get_selected_channels_ordered())

    def _on_rows_moved(self) -> None:
        """Handle drag-and-drop reordering."""
        self._update_order_display()
        self.selection_changed.emit(self.get_selected_channels_ordered())

    def _update_order_display(self) -> None:
        """Update the imaging order label."""
        selected = self.get_selected_channels_ordered()
        if not selected:
            self.order_label.setText("(none selected)")
        elif len(selected) == 1:
            self.order_label.setText(selected[0])
        else:
            self.order_label.setText(" → ".join(selected))

    def get_selected_channels_ordered(self) -> List[str]:
        """Get the list of selected channel names in drag-drop order.

        Returns:
            List of channel names that are checked, in the order they
            appear in the list (which can be modified via drag-drop).
        """
        selected = []
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item and item.checkState() == Qt.Checked:
                selected.append(item.text())
        return selected

    def set_channels(self, channel_names: List[str]) -> None:
        """Update the available channels.

        Args:
            channel_names: New list of available channel names.
        """
        # Save current selections
        previously_selected = set(self.get_selected_channels_ordered())

        self._channels = list(channel_names)
        self._populate_channels()

        # Restore selections for channels that still exist
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item and item.text() in previously_selected:
                item.setCheckState(Qt.Checked)

        self._update_order_display()

    def set_selected_channels(self, channel_names: List[str]) -> None:
        """Select specific channels and optionally reorder them.

        Args:
            channel_names: List of channel names to select, in the desired order.
                          Channels not in this list will be deselected.
        """
        # Block signals during bulk update
        self.list_channels.blockSignals(True)

        # First, uncheck all and build a mapping
        item_map = {}
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item:
                item.setCheckState(Qt.Unchecked)
                item_map[item.text()] = i

        # Reorder items to match the requested order for selected channels
        # Move selected channels to the top in the specified order
        new_order = []
        for name in channel_names:
            if name in item_map:
                new_order.append(name)

        # Add remaining channels in their current order
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item and item.text() not in new_order:
                new_order.append(item.text())

        # Rebuild the list in the new order
        self.list_channels.clear()
        for name in new_order:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            if name in channel_names:
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.list_channels.addItem(item)

        self.list_channels.blockSignals(False)
        self._update_order_display()
        self.selection_changed.emit(self.get_selected_channels_ordered())

    def select_all(self) -> None:
        """Select all channels."""
        self.list_channels.blockSignals(True)
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item:
                item.setCheckState(Qt.Checked)
        self.list_channels.blockSignals(False)
        self._update_order_display()
        self.selection_changed.emit(self.get_selected_channels_ordered())

    def clear_selection(self) -> None:
        """Deselect all channels."""
        self.list_channels.blockSignals(True)
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item:
                item.setCheckState(Qt.Unchecked)
        self.list_channels.blockSignals(False)
        self._update_order_display()
        self.selection_changed.emit(self.get_selected_channels_ordered())

    def get_all_channels(self) -> List[str]:
        """Get all available channel names in current order.

        Returns:
            List of all channel names in their current drag-drop order.
        """
        channels = []
        for i in range(self.list_channels.count()):
            item = self.list_channels.item(i)
            if item:
                channels.append(item.text())
        return channels
