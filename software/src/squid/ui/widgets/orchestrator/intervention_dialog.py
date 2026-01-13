"""
Intervention Dialog for operator acknowledgment.
"""

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
)
from PyQt5.QtGui import QFont

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class InterventionDialog(QDialog):
    """Modal dialog for operator intervention acknowledgment.

    Displays a message and requires the operator to acknowledge
    before the experiment can continue.
    """

    def __init__(
        self,
        round_name: str,
        message: str,
        parent: Optional[QDialog] = None,
    ):
        super().__init__(parent)
        self._round_name = round_name
        self._message = message

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

        # Acknowledge button
        acknowledge_btn = QPushButton("ACKNOWLEDGE AND CONTINUE")
        acknowledge_btn.setMinimumHeight(50)
        acknowledge_btn.setStyleSheet(
            "background-color: #FF9800; color: white; font-weight: bold;"
        )
        acknowledge_btn.clicked.connect(self.accept)
        layout.addWidget(acknowledge_btn)
