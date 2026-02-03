"""
Warning Panel for orchestrator experiment monitoring.

Displays warnings accumulated during experiment execution with
filtering, navigation, and threshold status.
"""

from typing import Optional, TYPE_CHECKING

from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QGroupBox,
    QAbstractItemView,
)
from PyQt5.QtGui import QColor, QBrush, QFont

from squid.core.events import handles
from squid.backend.controllers.orchestrator import (
    WarningCategory,
    WarningSeverity,
    WarningRaised,
    WarningThresholdReached,
    WarningsCleared,
    ClearWarningsCommand,
)
from squid.ui.widgets.base import EventBusWidget

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.controllers.orchestrator import WarningManager

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


# Severity colors
SEVERITY_COLORS = {
    WarningSeverity.INFO: QColor("#2196F3"),  # Blue
    WarningSeverity.LOW: QColor("#4CAF50"),  # Green
    WarningSeverity.MEDIUM: QColor("#FF9800"),  # Orange
    WarningSeverity.HIGH: QColor("#f44336"),  # Red
    WarningSeverity.CRITICAL: QColor("#9C27B0"),  # Purple
}

SEVERITY_BG_COLORS = {
    WarningSeverity.INFO: QColor("#E3F2FD"),  # Light blue
    WarningSeverity.LOW: QColor("#E8F5E9"),  # Light green
    WarningSeverity.MEDIUM: QColor("#FFF3E0"),  # Light orange
    WarningSeverity.HIGH: QColor("#FFEBEE"),  # Light red
    WarningSeverity.CRITICAL: QColor("#F3E5F5"),  # Light purple
}


class WarningPanel(EventBusWidget):
    """Panel displaying acquisition warnings with filtering and navigation.

    Features:
    - Warning table with timestamp, category, severity, message
    - Category filter dropdown
    - Warning count badge with severity breakdown
    - Click-to-navigate to FOV
    - Clear button
    """

    # Signal emitted when user clicks a warning to navigate to its FOV
    navigate_to_fov = pyqtSignal(str)  # fov_id

    # Signals for thread-safe UI updates
    warning_added = pyqtSignal(dict)  # warning data
    warnings_cleared = pyqtSignal(int)  # count cleared
    threshold_reached = pyqtSignal(str, int)  # threshold_type, count

    def __init__(
        self,
        event_bus: "EventBus",
        warning_manager: Optional["WarningManager"] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(event_bus, parent)
        self._warning_manager = warning_manager
        self._current_filter: Optional[WarningCategory] = None
        self._experiment_id: str = ""

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Header with count and controls
        header_layout = QHBoxLayout()

        # Warning count label
        self._count_label = QLabel("Warnings: 0")
        self._count_label.setFont(QFont("", -1, QFont.Bold))
        header_layout.addWidget(self._count_label)

        # Severity badges (compact counts)
        self._severity_labels = {}
        for severity in [WarningSeverity.CRITICAL, WarningSeverity.HIGH, WarningSeverity.MEDIUM]:
            label = QLabel("0")
            label.setFixedWidth(24)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"background-color: {SEVERITY_COLORS[severity].name()}; "
                f"color: white; border-radius: 4px; font-size: 11px;"
            )
            self._severity_labels[severity] = label
            header_layout.addWidget(label)

        header_layout.addStretch()

        # Category filter
        self._category_combo = QComboBox()
        self._category_combo.addItem("All Categories", None)
        for category in WarningCategory:
            self._category_combo.addItem(category.name, category)
        self._category_combo.currentIndexChanged.connect(self._on_filter_changed)
        header_layout.addWidget(self._category_combo)

        # Clear button
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        header_layout.addWidget(self._clear_btn)

        layout.addLayout(header_layout)

        # Warning table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Time", "Round", "Category", "Severity", "Message"])
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self._table)

        # Status bar
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._status_label)

    def _connect_signals(self) -> None:
        """Connect internal signals to slots."""
        self.warning_added.connect(self._handle_warning_added)
        self.warnings_cleared.connect(self._handle_warnings_cleared)
        self.threshold_reached.connect(self._handle_threshold_reached)

    # ========================================================================
    # Event Handlers
    # ========================================================================

    @handles(WarningRaised)
    def _on_warning_raised(self, event: WarningRaised) -> None:
        """Handle warning raised event."""
        # Thread-safe: emit signal to update UI on main thread
        self.warning_added.emit({
            "experiment_id": event.experiment_id,
            "category": event.category,
            "severity": event.severity,
            "message": event.message,
            "round_index": event.round_index,
            "round_name": event.round_name,
            "time_point": event.time_point,
            "fov_id": event.fov_id,
            "fov_index": event.fov_index,
            "total_warnings": event.total_warnings,
            "warnings_in_category": event.warnings_in_category,
        })

    @handles(WarningThresholdReached)
    def _on_threshold_reached(self, event: WarningThresholdReached) -> None:
        """Handle threshold reached event."""
        self.threshold_reached.emit(event.threshold_type, event.current_count)

    @handles(WarningsCleared)
    def _on_warnings_cleared(self, event: WarningsCleared) -> None:
        """Handle warnings cleared event."""
        self.warnings_cleared.emit(event.cleared_count)

    # ========================================================================
    # UI Slots
    # ========================================================================

    @pyqtSlot(dict)
    def _handle_warning_added(self, data: dict) -> None:
        """Add a warning to the table (main thread)."""
        # Check filter
        if self._current_filter is not None:
            category = WarningCategory[data["category"]]
            if category != self._current_filter:
                # Still update counts but don't add to table
                self._update_counts(data["total_warnings"])
                return

        # Add row to table
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Time (just time portion)
        from datetime import datetime
        time_str = datetime.now().strftime("%H:%M:%S")
        self._table.setItem(row, 0, QTableWidgetItem(time_str))

        # Round
        round_text = data.get("round_name", f"Round {data.get('round_index', 0) + 1}")
        self._table.setItem(row, 1, QTableWidgetItem(round_text))

        # Category
        self._table.setItem(row, 2, QTableWidgetItem(data["category"]))

        # Severity (with color)
        severity_item = QTableWidgetItem(data["severity"])
        try:
            severity = WarningSeverity[data["severity"]]
            severity_item.setForeground(QBrush(SEVERITY_COLORS.get(severity, QColor("#000"))))
            severity_item.setBackground(QBrush(SEVERITY_BG_COLORS.get(severity, QColor("#FFF"))))
        except (KeyError, ValueError):
            pass
        self._table.setItem(row, 3, severity_item)

        # Message
        msg_item = QTableWidgetItem(data["message"])
        self._table.setItem(row, 4, msg_item)

        # Store fov_id in row data
        fov_id = data.get("fov_id")
        if fov_id:
            self._table.item(row, 0).setData(Qt.ItemDataRole.UserRole, fov_id)

        # Auto-scroll to bottom
        self._table.scrollToBottom()

        # Update counts
        self._update_counts(data["total_warnings"])

    @pyqtSlot(int)
    def _handle_warnings_cleared(self, count: int) -> None:
        """Handle warnings cleared (main thread)."""
        self._table.setRowCount(0)
        self._update_counts(0)
        self._status_label.setText(f"Cleared {count} warning(s)")
        # Clear status after 3 seconds
        QTimer.singleShot(3000, lambda: self._status_label.setText(""))

    @pyqtSlot(str, int)
    def _handle_threshold_reached(self, threshold_type: str, count: int) -> None:
        """Handle threshold reached (main thread)."""
        self._status_label.setText(
            f"Warning threshold reached: {threshold_type} ({count})"
        )
        self._status_label.setStyleSheet("color: #f44336; font-weight: bold; font-size: 11px;")

    def _on_filter_changed(self, index: int) -> None:
        """Handle category filter change."""
        self._current_filter = self._category_combo.currentData()
        self._refresh_table()

    def _on_clear_clicked(self) -> None:
        """Handle clear button click."""
        categories = None
        if self._current_filter is not None:
            categories = (self._current_filter,)
        self._publish(
            ClearWarningsCommand(
                experiment_id=self._experiment_id,
                categories=categories,
            )
        )

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """Handle double-click on a warning row."""
        item = self._table.item(row, 0)
        if item:
            fov_id = item.data(Qt.ItemDataRole.UserRole)
            if fov_id:
                self.navigate_to_fov.emit(fov_id)
                self._status_label.setText(f"Navigating to FOV: {fov_id}")

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _update_counts(self, total: int) -> None:
        """Update warning count display."""
        self._count_label.setText(f"Warnings: {total}")

        # Update severity badges from warning manager
        if self._warning_manager is not None:
            stats = self._warning_manager.get_stats()
            for severity, label in self._severity_labels.items():
                count = stats.by_severity.get(severity, 0)
                label.setText(str(count))

    def _refresh_table(self) -> None:
        """Refresh table from warning manager."""
        self._table.setRowCount(0)

        if self._warning_manager is None:
            return

        warnings = self._warning_manager.get_warnings(category=self._current_filter)
        for warning in warnings:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # Time
            time_str = warning.timestamp.strftime("%H:%M:%S")
            time_item = QTableWidgetItem(time_str)
            time_item.setData(Qt.ItemDataRole.UserRole, warning.fov_id)
            self._table.setItem(row, 0, time_item)

            # Round
            round_text = warning.round_name or f"Round {warning.round_index + 1}"
            self._table.setItem(row, 1, QTableWidgetItem(round_text))

            # Category
            self._table.setItem(row, 2, QTableWidgetItem(warning.category.name))

            # Severity
            severity_item = QTableWidgetItem(warning.severity.name)
            severity_item.setForeground(QBrush(SEVERITY_COLORS.get(warning.severity, QColor("#000"))))
            severity_item.setBackground(QBrush(SEVERITY_BG_COLORS.get(warning.severity, QColor("#FFF"))))
            self._table.setItem(row, 3, severity_item)

            # Message
            self._table.setItem(row, 4, QTableWidgetItem(warning.message))

        self._update_counts(len(self._warning_manager))

    def set_warning_manager(self, manager: "WarningManager") -> None:
        """Set the warning manager reference."""
        self._warning_manager = manager
        self._refresh_table()

    def set_experiment_id(self, experiment_id: str) -> None:
        """Set the current experiment ID."""
        self._experiment_id = experiment_id

    def clear(self) -> None:
        """Clear all warnings from display."""
        self._table.setRowCount(0)
        self._update_counts(0)
        self._status_label.setText("")
