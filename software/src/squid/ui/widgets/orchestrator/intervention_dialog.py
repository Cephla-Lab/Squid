"""Intervention dialog with a small fixed action set."""

from typing import Optional, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
)
from PyQt5.QtGui import QFont

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class InterventionDialog(QDialog):
    """Modal dialog for operator intervention resolution."""

    def __init__(
        self,
        round_name: str,
        message: str,
        allowed_actions: Sequence[str] = ("acknowledge",),
        parent: Optional[QDialog] = None,
    ):
        super().__init__(parent)
        self._round_name = round_name
        self._message = message
        self._allowed_actions = tuple(allowed_actions)
        self.selected_action = "acknowledge"

        self.setWindowTitle("Operator Intervention Required")
        self.setModal(True)
        self.setMinimumWidth(400)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # Warning header
        header = QLabel("INTERVENTION REQUIRED")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSize(16)
        header.setFont(font)
        header.setStyleSheet("color: #FF9800;")
        layout.addWidget(header)

        # Round name
        round_label = QLabel(f"Round: {self._round_name}")
        round_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(round_label)

        # Message
        message_label = QLabel(self._message)
        message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        for action, label, style in (
            ("acknowledge", "Continue", "background-color: #FF9800; color: white; font-weight: bold;"),
            ("retry", "Retry", "background-color: #1976D2; color: white; font-weight: bold;"),
            ("skip", "Skip", "background-color: #424242; color: white; font-weight: bold;"),
            ("abort", "Abort", "background-color: #C62828; color: white; font-weight: bold;"),
        ):
            if action not in self._allowed_actions:
                continue
            button = QPushButton(label)
            button.setMinimumHeight(46)
            button.setStyleSheet(style)
            button.clicked.connect(lambda _checked=False, chosen=action: self._accept_action(chosen))
            button_row.addWidget(button)
        layout.addLayout(button_row)

    def _accept_action(self, action: str) -> None:
        self.selected_action = action
        self.accept()
