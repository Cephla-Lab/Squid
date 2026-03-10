"""
Protocol Validation Dialog for experiment orchestration.

Shows validation results before starting an experiment including:
- Time and disk estimates
- Per-round breakdown
- Errors and warnings
"""

from typing import Optional, TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
    QAbstractItemView,
    QDialogButtonBox,
    QSizePolicy,
    QWidget,
)
from PyQt5.QtGui import QFont, QColor

if TYPE_CHECKING:
    from squid.backend.controllers.orchestrator.validation import ValidationSummary

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class ValidationResultDialog(QDialog):
    """Dialog displaying protocol validation results.

    Shows:
    - Protocol name and summary
    - Total time estimate
    - Total disk usage estimate
    - Per-round breakdown table
    - Errors section (prevents starting)
    - Warnings section (allows starting)
    - OK button to close (user starts from main panel)
    """

    def __init__(
        self,
        summary: "ValidationSummary",
        parent: Optional["QDialog"] = None,
    ):
        super().__init__(parent)
        self._summary = summary

        self.setWindowTitle("Protocol Validation Results")
        self.setMinimumSize(600, 500)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Header
        header = self._create_header()
        layout.addWidget(header)

        # Summary statistics
        stats_group = self._create_stats_section()
        layout.addWidget(stats_group)

        # Per-round breakdown
        rounds_group = self._create_rounds_section()
        layout.addWidget(rounds_group)

        # Errors section (if any)
        if self._summary.has_errors:
            errors_group = self._create_errors_section()
            layout.addWidget(errors_group)

        # Warnings section (if any)
        if self._summary.has_warnings:
            warnings_group = self._create_warnings_section()
            layout.addWidget(warnings_group)

        # Button box
        button_box = self._create_buttons()
        layout.addWidget(button_box)

    def _create_header(self) -> QWidget:
        """Create the header section."""
        header = QLabel()

        if self._summary.valid:
            header.setText(f"Protocol: {self._summary.protocol_name}")
            header.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: #2196F3;"
            )
        else:
            header.setText(f"Protocol: {self._summary.protocol_name} - INVALID")
            header.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: #ff6b6b;"
            )

        return header

    def _create_stats_section(self) -> QGroupBox:
        """Create the summary statistics section."""
        group = QGroupBox("Summary")
        layout = QHBoxLayout(group)

        # Rounds
        rounds_label = QLabel(f"Total Rounds: {self._summary.total_rounds}")
        rounds_label.setFont(QFont("", -1, QFont.Bold))
        layout.addWidget(rounds_label)

        layout.addStretch()

        # Time estimate
        time_str = self._format_time(self._summary.total_estimated_seconds)
        time_label = QLabel(f"Est. Time: {time_str}")
        time_label.setStyleSheet("font-size: 14px; color: #2196F3;")
        layout.addWidget(time_label)

        layout.addSpacing(20)

        # Disk estimate
        disk_str = self._format_disk(self._summary.total_disk_bytes)
        disk_label = QLabel(f"Est. Disk: {disk_str}")
        disk_label.setStyleSheet("font-size: 14px; color: #4CAF50;")
        layout.addWidget(disk_label)

        return group

    def _create_rounds_section(self) -> QGroupBox:
        """Create the per-round breakdown section."""
        group = QGroupBox("Round Breakdown")
        layout = QVBoxLayout(group)

        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Round", "Operation", "Description", "Time", "Disk"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setMaximumHeight(200)

        # Populate table
        for estimate in self._summary.operation_estimates:
            row = table.rowCount()
            table.insertRow(row)

            # Round name
            round_item = QTableWidgetItem(estimate.round_name)
            table.setItem(row, 0, round_item)

            # Operation type
            op_item = QTableWidgetItem(estimate.operation_type.capitalize())
            table.setItem(row, 1, op_item)

            # Description
            desc_item = QTableWidgetItem(estimate.description)
            table.setItem(row, 2, desc_item)

            # Time
            time_str = self._format_time(estimate.estimated_seconds)
            time_item = QTableWidgetItem(time_str)
            time_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 3, time_item)

            # Disk
            disk_str = self._format_disk(estimate.estimated_disk_bytes) if estimate.estimated_disk_bytes > 0 else "-"
            disk_item = QTableWidgetItem(disk_str)
            disk_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 4, disk_item)

            # Color row if there are errors
            if estimate.has_errors:
                for col in range(5):
                    item = table.item(row, col)
                    if item:
                        item.setBackground(QColor("#2d1a1a"))

        layout.addWidget(table)
        return group

    def _create_errors_section(self) -> QGroupBox:
        """Create the errors section."""
        group = QGroupBox("Errors")
        group.setStyleSheet(
            "QGroupBox { color: #ff6b6b; font-weight: bold; }"
            "QGroupBox::title { color: #ff6b6b; }"
        )
        layout = QVBoxLayout(group)

        # Collect all errors
        all_errors = list(self._summary.errors)
        for estimate in self._summary.operation_estimates:
            for error in estimate.validation_errors:
                all_errors.append(f"[{estimate.round_name}] {error}")
        all_errors = self._dedupe_messages(all_errors)

        # Display in text area
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMaximumHeight(100)
        text.setStyleSheet("background-color: #2d1a1a; color: #ff6b6b;")
        text.setPlainText("\n".join(all_errors))
        layout.addWidget(text)

        return group

    def _create_warnings_section(self) -> QGroupBox:
        """Create the warnings section."""
        group = QGroupBox("Warnings")
        group.setStyleSheet(
            "QGroupBox { color: #FF9800; font-weight: bold; }"
            "QGroupBox::title { color: #FF9800; }"
        )
        layout = QVBoxLayout(group)

        # Collect all warnings
        all_warnings = list(self._summary.warnings)
        for estimate in self._summary.operation_estimates:
            for warning in estimate.validation_warnings:
                all_warnings.append(f"[{estimate.round_name}] {warning}")
        all_warnings = self._dedupe_messages(all_warnings)

        # Display in text area
        text = QTextEdit()
        text.setReadOnly(True)
        text.setMaximumHeight(100)
        text.setStyleSheet("background-color: #2d2213; color: #ffb74d;")
        text.setPlainText("\n".join(all_warnings))
        layout.addWidget(text)

        return group

    @staticmethod
    def _dedupe_messages(messages: list[str]) -> list[str]:
        """Deduplicate validation messages while preserving first-seen order."""
        seen: set[str] = set()
        unique: list[str] = []
        for message in messages:
            if message in seen:
                continue
            seen.add(message)
            unique.append(message)
        return unique

    def _create_buttons(self) -> QDialogButtonBox:
        """Create the button box."""
        button_box = QDialogButtonBox()

        # Close button - just closes the dialog, user can start from main panel
        close_btn = QPushButton("OK")
        if self._summary.valid:
            close_btn.setStyleSheet(
                "background-color: #4CAF50; color: white; "
                "font-weight: bold; padding: 8px 16px;"
            )
        else:
            close_btn.setStyleSheet(
                "background-color: #ff6b6b; color: white; "
                "font-weight: bold; padding: 8px 16px;"
            )
        close_btn.clicked.connect(self.accept)
        button_box.addButton(close_btn, QDialogButtonBox.AcceptRole)

        return button_box

    def _format_time(self, seconds: float) -> str:
        """Format seconds as human-readable time."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def _format_disk(self, bytes_: int) -> str:
        """Format bytes as human-readable disk size."""
        if bytes_ < 1024:
            return f"{bytes_} B"
        elif bytes_ < 1024 ** 2:
            return f"{bytes_ / 1024:.1f} KB"
        elif bytes_ < 1024 ** 3:
            return f"{bytes_ / (1024 ** 2):.1f} MB"
        else:
            return f"{bytes_ / (1024 ** 3):.1f} GB"

    @property
    def is_valid(self) -> bool:
        """Check if the validation passed."""
        return self._summary.valid
